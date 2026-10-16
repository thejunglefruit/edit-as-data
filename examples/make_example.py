# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Generate the neutral media of the example with ffmpeg alone: four synthetic sources
(test patterns, a fractal, a vertical gradient, a 24 fps counter), a 125 BPM metronome
with an accented downbeat after half a second of silence, and a noise whoosh.

    python examples/make_example.py [folder]      # default: examples/media
"""

import os
import subprocess
import sys

BPM, LEAD_S, MUSIC_S = 125, 0.5, 10.0
BEAT = 60.0 / BPM


def ff(*args, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-map_metadata", "-1", "-fflags", "+bitexact", out],
                   check=True)


def video(src, seconds, out):
    ff("-f", "lavfi", "-i", src, "-t", f"{seconds}", "-c:v", "libx264", "-preset", "ultrafast",
       "-crf", "23", "-pix_fmt", "yuv420p", out=out)


def click_expr(bpm=BPM, lead=LEAD_S, beats_per_bar=4, start=None):
    """Metronome: 1760 Hz loud click on the downbeat, 880 Hz softer on the other beats.
    `lead` is where a downbeat falls; `start` (default: lead) is where the clicks begin."""
    beat = 60.0 / bpm
    u = f"(t-{lead})"
    idx = f"mod(floor({u}/{beat}+0.001),{beats_per_bar})"
    return (f"gte(t,{lead if start is None else start})*if(eq({idx},0),0.9,0.45)*sin(2*PI*if(eq({idx},0),1760,880)*t)"
            f"*exp(-60*mod({u},{beat}))")


def make(folder):
    os.makedirs(folder, exist_ok=True)
    p = lambda n: os.path.join(folder, n)
    video("testsrc2=s=1280x720:r=25", 8, p("bars.mp4"))
    video("mandelbrot=s=1280x720:r=25", 8, p("fractal.mp4"))
    video("life=s=180x320:r=25:ratio=0.35:seed=7:mold=8:life_color=#f2c14e:death_color=#1d3557:mold_color=#e63946,"
          "scale=720:1280:flags=neighbor", 8, p("portrait.mp4"))
    video("testsrc=s=1280x720:r=24", 8, p("film24.mp4"))
    ff("-f", "lavfi", "-i", f"aevalsrc='{click_expr()}':s=48000:d={MUSIC_S}", "-ac", "2",
       "-c:a", "pcm_s16le", out=p("click_125bpm.wav"))
    ff("-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:a=0.6:seed=3",
       "-af", "highpass=f=400,afade=t=in:d=0.35,afade=t=out:st=0.35:d=0.25", "-ac", "2",
       "-c:a", "pcm_s16le", out=p("whoosh.wav"))
    return folder


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print("-> " + make(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "media")))

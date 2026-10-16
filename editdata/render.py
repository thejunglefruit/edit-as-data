# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Render a resolved edit to an H.264 + AAC mp4 with ffmpeg.

Two passes, on purpose, not one giant filter_complex:

  1. Every shot is conformed on its own to the sequence format (size, fps,
     speed, fit) at near-lossless quality and cached under a hash of its
     parameters. Changing one shot re-renders one shot.
  2. The conformed shots are concatenated without re-encoding, then titles,
     fades, music and sound effects go on in a single final encode.

Every ffmpeg call runs with cwd = the folder of edit.json and gets RELATIVE
paths. On Windows the filter parser splits option values on ':' so a drive
letter inside drawtext's fontfile (or any path inside a filter) breaks it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess

CACHE = ".render-cache"


class RenderError(RuntimeError):
    pass


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RenderError(" ".join(cmd[:10]) + " ...\n" + (r.stderr or "")[-2500:])
    return r


def ffmpeg_major():
    r = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True)
    m = re.search(r"version n?(\d+)", r.stdout or "")
    return int(m.group(1)) if m else 0


def _rel(edit, p):
    """Path as ffmpeg should see it from the edit folder: relative when possible, forward slashes."""
    try:
        p = os.path.relpath(p, edit["root"])
    except ValueError:            # another drive on Windows: absolute is the only option (inputs only)
        pass
    return p.replace("\\", "/")


def fit_chain(fit, w, h):
    if fit == "fit":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    if fit == "blur":
        # a frame of another shape (say 9:16 in 16:9) shown whole, over a blurred, darker copy of itself
        return (f"split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"gblur=sigma=40,eq=brightness=-0.2[bgb];"
                f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def shot_key(edit, sh):
    src = edit["sources"].get(sh["src"] or "", {})
    raw = json.dumps([src.get("path"), sh["in_s"], sh["frames"], sh["speed_pct"], sh["fit"],
                      edit["fps"], edit["width"], edit["height"]], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def conform(edit, sh, out, log=print):
    fps, w, h, n = edit["fps"], edit["width"], edit["height"], sh["frames"]
    root = edit["root"]
    if not sh["src"]:
        _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
              "-i", f"color=c=black:s={w}x{h}:r={fps:g}:d={n / fps + 1:.4f}",
              "-frames:v", str(n), "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
              "-pix_fmt", "yuv420p", out], root)
        return
    src = edit["sources"][sh["src"]]
    if not os.path.exists(src["abspath"]):
        raise RenderError(f"shot {sh['i']}: missing source file {src['path']}")
    k = sh["speed_pct"] / 100.0
    chain = [fit_chain(sh["fit"], w, h), "setsar=1", "format=yuv420p"]
    if abs(k - 1.0) > 1e-6:
        chain.append(f"setpts=PTS/{k:.9f}")
    # tpad clones the last frame if the source runs out: the shot keeps its length and the cut stays put
    chain += [f"fps={fps:g}", f"tpad=stop_mode=clone:stop_duration={n / fps + 1:.4f}"]
    # -ss and -t BEFORE -i: they bound what is read from the source. After -i, -t would cap the
    # output instead, and a slowed-down shot would come out short.
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{sh['in_s']:.4f}", "-t", f"{sh['span_s'] + 0.5:.4f}",
          "-i", _rel(edit, src["abspath"]), "-an", "-filter_complex", ",".join(chain),
          "-frames:v", str(n), "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
          "-pix_fmt", "yuv420p", "-r", f"{fps:g}", out], root)


def title_filters(edit):
    """drawtext per title. The text goes through a UTF-8 file (textfile + expansion=none):
    no escaping of ':' ',' '%' or quotes, which is where inline text= breaks."""
    fps, parts = edit["fps"], []
    cache = os.path.join(edit["root"], CACHE)
    os.makedirs(cache, exist_ok=True)
    for j, t in enumerate(edit["titles"], 1):
        tf = os.path.join(cache, f"title_{j:02d}.txt")
        with open(tf, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(t["text"])
        t0, t1 = t["start"] / fps, (t["start"] + t["frames"]) / fps
        rise = min(0.3, (t1 - t0) / 3)
        alpha = f"min(1,max(0,(t-{t0:.3f})/{rise:.3f}))*min(1,max(0,({t1:.3f}-t)/{rise:.3f}))"
        parts.append(
            f"drawtext=fontfile='{t['font']}':textfile='{_rel(edit, tf)}':expansion=none:fontsize={t['size']}"
            f":fontcolor={t['color']}:x={t['x']}:y={t['y']}:alpha='{alpha}'"
            f":enable='between(t,{t0:.3f},{t1:.3f})'")
    return parts


def build_graph(edit, n_sfx):
    """Final filter graph: video [vout], and audio [aout] when there is any audio."""
    total = edit["total_seconds"]
    v = title_filters(edit)
    if edit["fade_in_s"] > 0:
        v.append(f"fade=t=in:st=0:d={edit['fade_in_s']:.3f}")
    if edit["fade_out_s"] > 0:
        v.append(f"fade=t=out:st={total - edit['fade_out_s']:.3f}:d={edit['fade_out_s']:.3f}")
    parts = ["[0:v]" + (",".join(v) if v else "null") + "[vout]"]

    labels, i = [], 1
    m = edit["music"]
    if m:
        off = float(m.get("offset_s", 0.0))
        parts.append(f"[{i}:a]atrim=start={off:.4f},asetpts=PTS-STARTPTS,"
                     f"volume={float(m.get('gain_db', 0.0)):.2f}dB,apad,atrim=0:{total:.4f}[mus]")
        labels.append("[mus]")
        i += 1
    for j, s in enumerate(edit["sfx"][:n_sfx]):
        ms = int(round(s["rec_in"] / edit["fps"] * 1000))
        parts.append(f"[{i}:a]adelay={ms}:all=1,volume={s['gain_db']:.2f}dB,apad,atrim=0:{total:.4f}[s{j}]")
        labels.append(f"[s{j}]")
        i += 1
    if not labels:
        return ";".join(parts), False
    # normalize=0: by default amix divides by the number of inputs and every sfx would duck the music
    mix = "".join(labels) + (f"amix=inputs={len(labels)}:duration=first:normalize=0" if len(labels) > 1 else "anull")
    tail = []
    if edit["audio_fade_out_s"] > 0:
        d = edit["audio_fade_out_s"]
        tail.append(f"afade=t=out:st={total - d:.3f}:d={d:.3f}")
    if edit["loudness_lufs"] is not None:
        tail.append(f"loudnorm=I={float(edit['loudness_lufs']):g}:TP=-1.5:LRA=11")
    tail.append("aresample=48000")
    parts.append(mix + "," + ",".join(tail) + "[aout]")
    return ";".join(parts), True


def render(edit, out, crf=18, log=print, keep_cache=True):
    """Conform, concatenate and mix. Returns the output path."""
    if not shutil.which("ffmpeg"):
        raise RenderError("ffmpeg is not in the PATH")
    root = edit["root"]
    cache = os.path.join(root, CACHE)
    os.makedirs(cache, exist_ok=True)
    names = []
    for sh in edit["shots"]:
        name = f"{sh['i']:03d}_{shot_key(edit, sh)}.mp4"
        names.append(name)
        dst = os.path.join(cache, name)
        if os.path.exists(dst):
            continue
        log(f"  conform {sh['i']:3d}/{len(edit['shots'])}  {sh['frames']:4d}f  {sh['src'] or '(gap)'}")
        conform(edit, sh, os.path.relpath(dst, root).replace("\\", "/"))

    # concat demuxer: entries are resolved relative to the list file, so bare names are enough
    lst = os.path.join(cache, "concat.txt")
    with open(lst, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(f"file '{n}'\n" for n in names)
    base = os.path.join(cache, "base.mp4")
    _run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", _rel(edit, lst),
          "-c", "copy", _rel(edit, base)], root)

    inputs = ["-i", _rel(edit, base)]
    if edit["music"]:
        if not os.path.exists(edit["music"]["abspath"]):
            raise RenderError(f"missing music file {edit['music']['path']}")
        inputs += ["-i", _rel(edit, edit["music"]["abspath"])]
    n_sfx = 0
    for s in edit["sfx"]:
        if not os.path.exists(s["abspath"]):
            raise RenderError(f"missing sfx file {s['path']}")
        inputs += ["-i", _rel(edit, s["abspath"])]
        n_sfx += 1
    for t in edit["titles"]:
        if not os.path.exists(os.path.join(root, t["font"])):
            raise RenderError(f"missing font {t['font']}")

    graph, has_audio = build_graph(edit, n_sfx)
    gfile = os.path.join(cache, "graph.txt")       # quotes, commas and UTF-8 survive a file better than argv
    with open(gfile, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(graph)
    flag = "-/filter_complex" if ffmpeg_major() >= 7 else "-filter_complex_script"

    out_abs = os.path.abspath(out)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", *inputs, flag, _rel(edit, gfile), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", f"{edit['fps']:g}", "-frames:v", str(edit["total_frames"]),
            "-t", f"{edit['total_seconds']:.4f}", "-movflags", "+faststart",
            "-map_metadata", "-1", "-fflags", "+bitexact", out_abs]
    log(f"  final encode: {edit['total_frames']} frames, {edit['total_seconds']:.2f} s")
    _run(cmd, root)
    if not keep_cache:
        shutil.rmtree(cache, ignore_errors=True)
    return out_abs


def contact_sheet(edit, video, out, cols=4, width=320):
    """One frame from the middle of every shot, tiled. For people and for agents that read images."""
    mids = [sh["rec_in"] + sh["frames"] // 2 for sh in edit["shots"]]
    rows = -(-len(mids) // cols)
    sel = "+".join(f"eq(n,{f})" for f in mids)       # inside quotes: commas need no escaping
    vf = f"select='{sel}',scale={width}:-2,tile={cols}x{rows}:padding=6:margin=6:color=0x111111"
    _run(["ffmpeg", "-v", "error", "-y", "-i", os.path.abspath(video), "-vf", vf,
          "-frames:v", "1", "-map_metadata", "-1", os.path.abspath(out)], edit["root"])
    return os.path.abspath(out)

# edit-as-data

**Write the edit as data, cut it against the music, render it with ffmpeg and open it in Premiere.** A small Python engine: an `edit.json` describes every shot in frames, seconds, beats or bars, and it becomes an mp4, an FCP7 XML timeline, a CMX3600 EDL and a shot list.

## Why

An edit made by hand in an NLE is hard to diff, review or regenerate. An edit written as data can be produced by a script or an agent, checked by tests, re-rendered after one change, and still handed to an editor as a real timeline to finish by hand. The music sets the grid: a cut written as `"to_bar": 5` lands on bar 5 whatever the frame rate, and the beat grid can be measured from the track itself.

## Quickstart

Needs Python 3.9+, `numpy` and `ffmpeg`/`ffprobe` in the PATH.

```bash
pip install -r requirements.txt
python examples/make_example.py                       # synthetic media into examples/media
python -m editdata check  examples/edit.json          # where every cut sits against the beat
python -m editdata render examples/edit.json -o out/example.mp4
python -m editdata export examples/edit.json -o out   # out/example.xml, .edl, .csv
python -m editdata sheet  examples/edit.json out/example.mp4 -o out/sheet.png
python -m editdata measure some_track.wav --json grid.json
```

`check` moves nothing. It reports each cut's distance to the nearest beat and bar, and warns when a shot asks for more footage than its source has:

```
example: 6 shots, 192 frames (7.68 s at 25 fps)
grid (edit.json): 125 BPM, beat = 12.000 f, bar = 48.000 f, first downbeat at frame 0.00
shot  frame     timecode  to beat  beat  to bar  bar  source
   1      0  00:00:00:00    +0.00     0   +0.00    1  bars
   2     48  00:00:01:23    +0.00     4   +0.00    2  fractal
   3     72  00:00:02:22    +0.00     6  -24.00    3  portrait
   4     96  00:00:03:21    +0.00     8   +0.00    3  film24
   5    144  00:00:05:19    +0.00    12   +0.00    4  (gap)
   6    156  00:00:06:06    +0.00    13  +12.00    4  bars
0 of 6 cuts more than 2 frames off the beat
```

## The `edit.json` format

All paths are relative to the folder of `edit.json`.

```json
{
  "name": "example",
  "fps": 25, "width": 1280, "height": 720,
  "music": {"path": "media/click_125bpm.wav", "bpm": 125, "beats_per_bar": 4,
            "first_downbeat_s": 0.5, "offset_s": 0.5},
  "sources": {"bars": {"path": "media/bars.mp4"}, "film24": {"path": "media/film24.mp4"}},
  "shots": [
    {"src": "bars",   "in_s": 1.0, "bars": 1, "note": "opens on the downbeat"},
    {"src": "film24", "in_s": 0.5, "beats": 4, "speed_pct": 50},
    {"gap": true, "beats": 1},
    {"src": "bars",   "in_s": 4.0, "to_bar": 4, "fit": "fill"}
  ],
  "sfx":    [{"path": "media/whoosh.wav", "at_bar": 3, "gain_db": -6, "note": "whoosh"}],
  "titles": [{"text": "edit-as-data", "font": "fonts/Merriweather-Bold.ttf", "at_bar": 1, "bars": 1, "size": 72}],
  "markers": {"0": "bar 1", "96": "bar 3"},
  "video": {"fade_in_s": 0, "fade_out_s": 0.4},
  "audio": {"fade_out_s": 0.4, "loudness_lufs": -14}
}
```

| Key | Meaning |
|---|---|
| `fps`, `width`, `height` | The sequence. Defaults 25, 1920, 1080. |
| `music.path` | The track. Optional, but musical units need it. |
| `music.bpm`, `first_downbeat_s` | The beat grid. **Leave `bpm` out and it is measured from the track** (tempo, first downbeat). |
| `music.offset_s` | Where in the file the edit starts. The grid moves with it. |
| `sources.<name>.path` | A video file. `fps`, `width`, `height`, `duration` are read with ffprobe unless given. |
| shot length | Exactly one of `frames`, `seconds`, `beats`, `bars` (a duration), or `to_beat` (0-based from the first downbeat) / `to_bar` (1-based, like a score) for an absolute end. |
| `in_s` | Source in point, in seconds. |
| `speed_pct` | 50 is half speed. The shot keeps its length on the timeline; it uses half as much footage. |
| `fit` | `fill` (scale and crop, default), `fit` (letterbox), `blur` (whole frame over a blurred, darker copy, for verticals). |
| `gap: true` | Black. |
| `sfx[]` | `path`, a position (`at` frames, `at_s`, `at_beat` or `at_bar`) and `gain_db`. |
| `titles[]` | `text`, `font` (relative path, see below), a position and a length, `size`, `color`, `x`, `y` (drawtext expressions). |
| `markers` | `{"frame": "name"}`, exported to the XML and the EDL. |

Shots run end to end on one track. The cursor that places them is a float and every cut is rounded only when it is placed, so 120 BPM at 25 fps (12.5 frames per beat) alternates 12- and 13-frame beats and never drifts.

## Render

Two passes, not one giant filter graph. Each shot is conformed on its own (size, frame rate, speed, fit) at near-lossless quality and cached under a hash of its parameters, so changing one shot re-renders one shot. The shots are then concatenated without re-encoding, and titles, fades, music and sound effects go on in one final encode. The output has exactly `total_frames` frames: if a source runs out, its last frame holds, so the cuts after it stay where they were written (and `check` tells you).

## Export to Premiere (and Resolve)

`export` writes:

- **`.xml`, FCP7 XML (xmeml v5).** This is the one to use: *File > Import* in Premiere Pro, or import it into DaVinci Resolve. Shots on V1 linked to the original files, in/out points in each file's own timebase (a 24 fps source lands in place on a 25 fps sequence), speed changes as Time Remap, music on A1, sound effects on A2 with their gain, markers on the sequence.
- **`.edl`, CMX3600.** Plan B for moving cuts to another room. Gaps are `BL` events so the record side is continuous; speed goes as an `M2` record; audio, which an EDL cannot carry here, is listed as comments.
- **`.csv`.** One row per shot with timecodes, to check by eye.

## Tests

```bash
python -m pytest
```

29 tests, no media in the repository: the suite generates the example with ffmpeg in a temporary folder, then checks that

- musical units resolve to the right frames, fractional beats do not drift, and invalid edits fail with a message that says where;
- the beat grid of synthetic metronomes comes out within 0.05 BPM and 5 ms, including a silent lead-in, a hit on the very first sample, a track that starts mid-bar, and an `edit.json` without `bpm`;
- the render has exactly 192 frames at 25 fps and 7.68 s, black exactly where the gap is, reuses its cache, and keeps its length when a source runs out;
- the XML parses as an xmeml sequence whose clips, file references, timebases, Time Remap, audio levels and markers are right, and whose file URLs point to files that exist;
- the EDL is CRLF CMX3600 with numbered events, a continuous record side and the `M2` speed record.

## Lessons learnt

- On Windows, ffmpeg's filter parser splits option values on `:`, so a drive letter inside `drawtext=fontfile=...` breaks the graph. Every ffmpeg call here runs from the folder of `edit.json` with relative paths, and title fonts must be relative.
- Title text goes to drawtext through a UTF-8 file (`textfile`, `expansion=none`): nothing to escape.
- `-t` before `-i` bounds what is read; after `-i` it caps the output. Put it after and a slowed-down shot comes out short.
- `amix` divides by the number of inputs unless `normalize=0`: every sound effect would duck the music.
- Autocorrelation alone gives the tempo to the nearest analysis hop (11.6 ms), which drifts a frame within a minute. Fitting a line through the onset peaks fixes it.

## Built with AI-assisted workflows

Made by a motion and creative technologist working with AI agents. The engine came out of editing a showreel this way: an agent wrote and revised the `edit.json`, the tests caught what it got wrong, and the timeline went to Premiere for the final touches.

Licence: GPL-3.0-or-later (see `LICENSE`) · font in `examples/fonts` under OFL 1.1

**Commercial licence.** If you want to use it in a closed product without the GPL obligations, a commercial licence is available: thejunglefruit@proton.me

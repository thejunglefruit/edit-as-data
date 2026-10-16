# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Read an edit.json and resolve it into a flat timeline of frames.

Every length in the file can be written in the unit the editor thinks in:
`frames`, `seconds`, `beats`, `bars`, or an absolute grid position with
`to_beat` / `to_bar`. Musical units need a beat grid: either written in the
`music` block (`bpm`, `first_downbeat_s`) or measured from the track itself
(see beatgrid.py).

The cursor that walks the timeline is a float, and every cut is rounded to
the nearest frame only when it is placed. So a 120 BPM track at 25 fps
(12.5 frames per beat) alternates 12- and 13-frame beats and never drifts.
"""

import json
import math
import os
import shutil
import subprocess

LENGTH_KEYS = ("frames", "seconds", "beats", "bars", "to_beat", "to_bar")
FITS = ("fill", "fit", "blur")


class EditError(ValueError):
    """The edit.json is not valid. The message says where."""


def tc(frames, fps):
    """Non-drop-frame timecode HH:MM:SS:FF."""
    fps = int(round(fps))
    frames = int(round(frames))
    h, r = divmod(frames, 3600 * fps)
    m, r = divmod(r, 60 * fps)
    s, f = divmod(r, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def probe(path):
    """fps, width, height and duration of a media file, or {} if unknown."""
    if not shutil.which("ffprobe") or not os.path.exists(path):
        return {}
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate:format=duration",
         "-of", "json", path], capture_output=True, text=True)
    if r.returncode:
        return {}
    d = json.loads(r.stdout or "{}")
    out = {}
    dur = d.get("format", {}).get("duration")
    if dur not in (None, "N/A"):
        out["duration"] = float(dur)
    for st in d.get("streams", []):
        if st.get("codec_type") == "video":
            num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
            if float(den or 1):
                out["fps"] = float(num) / float(den or 1)
            out["width"], out["height"] = st.get("width"), st.get("height")
            break
    return out


def _grid(music, fps, root, needed):
    """Beat grid in timeline frames, or None when the edit does not need one."""
    if not music:
        if needed:
            raise EditError("shots use beats/bars but there is no `music` block")
        return None
    bpm = music.get("bpm")
    first = music.get("first_downbeat_s")
    source = "edit.json"
    if bpm is None and needed:
        from . import beatgrid                          # numpy only when measuring
        m = beatgrid.analyse(os.path.join(root, music["path"]),
                             beats_per_bar=music.get("beats_per_bar", 4))
        bpm, source = m["bpm"], "measured"
        if first is None:
            first = m["first_downbeat"]
    if bpm is None:
        return None
    bpb = int(music.get("beats_per_bar", 4))
    beat_f = fps * 60.0 / float(bpm)
    # the music starts `offset_s` into the file, so the grid moves back by that much
    down_f = (float(first or 0.0) - float(music.get("offset_s", 0.0))) * fps
    return {"bpm": float(bpm), "beats_per_bar": bpb, "beat_frames": beat_f,
            "bar_frames": beat_f * bpb, "downbeat_frame": down_f, "source": source}


def _frame(x):
    """Round half up (Python's round() is half-to-even, which makes 12.5-frame beats uneven)."""
    return int(math.floor(x + 0.5))


def _advance(cursor, item, fps, grid, where):
    keys = [k for k in LENGTH_KEYS if k in item]
    if len(keys) != 1:
        raise EditError(f"{where}: give exactly one of {', '.join(LENGTH_KEYS)} (got {keys or 'none'})")
    k, v = keys[0], float(item[keys[0]])
    if k == "frames":
        return cursor + v
    if k == "seconds":
        return cursor + v * fps
    if grid is None:
        raise EditError(f"{where}: `{k}` needs a beat grid (music.bpm or a track to measure)")
    if k == "beats":
        return cursor + v * grid["beat_frames"]
    if k == "bars":
        return cursor + v * grid["bar_frames"]
    if k == "to_beat":
        return grid["downbeat_frame"] + v * grid["beat_frames"]
    return grid["downbeat_frame"] + (v - 1) * grid["bar_frames"]   # to_bar is 1-based, like a score


def load(path):
    """Parse and validate an edit.json. Returns a plain dict with every cut in frames."""
    path = os.path.abspath(path)
    root = os.path.dirname(path)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    fps = float(raw.get("fps", 25))
    if fps <= 0:
        raise EditError("fps must be positive")
    width, height = int(raw.get("width", 1920)), int(raw.get("height", 1080))
    if width % 2 or height % 2:
        raise EditError("width and height must be even (yuv420p)")

    sources = {}
    for name, s in (raw.get("sources") or {}).items():
        if "path" not in s:
            raise EditError(f"source `{name}` has no path")
        sources[name] = dict(s, abspath=os.path.normpath(os.path.join(root, s["path"])))

    shots_raw = raw.get("shots") or []
    if not shots_raw:
        raise EditError("the edit has no shots")
    musical = any(k in sh for sh in shots_raw for k in ("beats", "bars", "to_beat", "to_bar"))
    music = raw.get("music")
    if music:
        if "path" not in music:
            raise EditError("music has no path")
        music = dict(music, abspath=os.path.normpath(os.path.join(root, music["path"])))
    grid = _grid(music, fps, root, musical)

    shots, cursor, prev = [], 0.0, 0
    for i, sh in enumerate(shots_raw, 1):
        where = f"shot {i}"
        gap = bool(sh.get("gap")) or not sh.get("src")
        if not gap and sh["src"] not in sources:
            raise EditError(f"{where}: unknown source `{sh['src']}`")
        cursor = _advance(cursor, sh, fps, grid, where)
        rec_out = _frame(cursor)
        if rec_out <= prev:
            raise EditError(f"{where}: ends at frame {rec_out}, not after its start ({prev})")
        fit = sh.get("fit", "fill")
        if fit not in FITS:
            raise EditError(f"{where}: fit must be one of {FITS}")
        speed = float(sh.get("speed_pct", 100.0))
        if speed <= 0:
            raise EditError(f"{where}: speed_pct must be positive")
        frames = rec_out - prev
        shots.append({
            "i": i, "src": None if gap else sh["src"], "rec_in": prev, "rec_out": rec_out,
            "frames": frames, "in_s": float(sh.get("in_s", 0.0)), "speed_pct": speed,
            "span_s": frames / fps * speed / 100.0, "fit": fit, "note": sh.get("note", ""),
        })
        prev = rec_out
    total = prev

    def position(item, where):
        """Start frame of an sfx or title: `at` (frames), `at_s`, `at_beat` (0-based) or `at_bar` (1-based)."""
        keys = [k for k in ("at", "at_s", "at_beat", "at_bar") if k in item]
        if len(keys) > 1:
            raise EditError(f"{where}: give only one of at, at_s, at_beat, at_bar")
        if not keys:
            return 0
        k, v = keys[0], float(item[keys[0]])
        if k == "at":
            return int(round(v))
        if k == "at_s":
            return int(round(v * fps))
        return _frame(_advance(0.0, {"to_beat" if k == "at_beat" else "to_bar": v}, fps, grid, where))

    sfx = []
    for j, s in enumerate(raw.get("sfx") or [], 1):
        if "path" not in s:
            raise EditError(f"sfx {j} has no path")
        sfx.append({"path": s["path"], "abspath": os.path.normpath(os.path.join(root, s["path"])),
                    "rec_in": position(s, f"sfx {j}"),
                    "gain_db": float(s.get("gain_db", 0.0)), "note": s.get("note", "")})

    titles = []
    for j, t in enumerate(raw.get("titles") or [], 1):
        font = t.get("font")
        if not font:
            raise EditError(f"title {j}: `font` is required (a .ttf/.otf path relative to edit.json)")
        if os.path.isabs(font) or font.startswith(("/", "\\")) or ":" in font:
            # ffmpeg splits filter options on ':' so a drive letter breaks drawtext
            raise EditError(f"title {j}: font path must be relative to edit.json, not `{font}`")
        has_len = any(k in t for k in ("frames", "seconds", "beats", "bars"))
        length = int(round(_advance(0.0, {k: t[k] for k in ("frames", "seconds", "beats", "bars") if k in t},
                                    fps, grid, f"title {j}"))) if has_len else int(round(fps * 2))
        titles.append({"text": str(t.get("text", "")), "font": font.replace("\\", "/"),
                       "start": position(t, f"title {j}"), "frames": length,
                       "size": int(t.get("size", round(height / 14))), "color": t.get("color", "white"),
                       "x": str(t.get("x", "(w-text_w)/2")), "y": str(t.get("y", "h-text_h*2.2"))})

    markers = sorted((int(f), str(n)) for f, n in (raw.get("markers") or {}).items())
    video = raw.get("video") or {}
    audio = raw.get("audio") or {}
    edit = {
        "path": path, "root": root, "name": raw.get("name") or os.path.splitext(os.path.basename(path))[0],
        "fps": fps, "width": width, "height": height, "total_frames": total, "total_seconds": total / fps,
        "grid": grid, "music": music, "sources": sources, "shots": shots, "sfx": sfx, "titles": titles,
        "markers": markers,
        "fade_in_s": float(video.get("fade_in_s", 0.0)), "fade_out_s": float(video.get("fade_out_s", 0.0)),
        "audio_fade_out_s": float(audio.get("fade_out_s", 0.0)),
        "loudness_lufs": audio.get("loudness_lufs"),
    }
    for f, _ in markers:
        if not 0 <= f <= total:
            raise EditError(f"marker at frame {f} is outside the edit (0-{total})")
    return edit


def source_info(edit, name):
    """fps/size/duration of a source: what edit.json says, else what ffprobe says."""
    s = edit["sources"][name]
    if "_probe" not in s:
        s["_probe"] = probe(s["abspath"])
    info = dict(s["_probe"])
    for k in ("fps", "width", "height", "duration"):
        if s.get(k) is not None:
            info[k] = s[k]
    return info


def footage_short(edit, shot):
    """Seconds of footage the shot asks for beyond the end of its source (0 if it fits or is unknown)."""
    if not shot["src"]:
        return 0.0
    dur = source_info(edit, shot["src"]).get("duration")
    if dur is None:
        return 0.0
    return max(0.0, shot["in_s"] + shot["span_s"] - dur)


def nearest(frame, origin, unit):
    """Signed distance in frames from `frame` to the nearest grid line, and that line's index."""
    k = round((frame - origin) / unit)
    return frame - (origin + k * unit), int(k)

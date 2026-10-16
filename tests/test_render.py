# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
import os

import pytest

from conftest import ffprobe, needs_ffmpeg


@needs_ffmpeg
def test_render_has_the_exact_length(rendered):
    edit, out = rendered
    d = ffprobe(out, "-count_frames", "-show_entries",
                "stream=codec_type,nb_read_frames,width,height,r_frame_rate,sample_rate:format=duration")
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    assert int(v["nb_read_frames"]) == edit["total_frames"] == 192
    assert (v["width"], v["height"], v["r_frame_rate"]) == (1280, 720, "25/1")
    assert float(d["format"]["duration"]) == pytest.approx(7.68, abs=0.05)
    assert a["sample_rate"] == "48000"


@needs_ffmpeg
def test_cuts_land_on_their_frames(rendered):
    # shot 5 is a gap: black from frame 144 to 155, picture again at 156
    edit, out = rendered
    import subprocess
    def luma(frame):
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vf",
                            f"select='eq(n,{frame})',scale=8:8,format=gray", "-frames:v", "1",
                            "-f", "rawvideo", "-"], capture_output=True, check=True)
        return sum(r.stdout) / len(r.stdout)
    assert luma(143) > 20
    assert luma(144) < 4 and luma(155) < 4
    assert luma(156) > 20


@needs_ffmpeg
def test_cache_rerenders_only_what_changed(example_dir, rendered):
    from editdata import load, render
    logs = []
    edit = load(str(example_dir / "edit.json"))
    render.render(edit, str(example_dir / "out" / "again.mp4"), log=logs.append)
    assert not [l for l in logs if "conform" in l]


@needs_ffmpeg
def test_contact_sheet(rendered, tmp_path):
    from editdata import render
    edit, out = rendered
    sheet = render.contact_sheet(edit, out, str(tmp_path / "sheet.png"), cols=3, width=200)
    d = ffprobe(sheet, "-show_entries", "stream=width,height")["streams"][0]
    assert d["width"] == 3 * 200 + 2 * 6 + 2 * 6 and d["height"] > 2 * 112


@needs_ffmpeg
def test_check_reports_every_cut_on_the_beat(rendered):
    from editdata import check
    edit, _ = rendered
    rows, warnings = check.report(edit)
    assert len(rows) == 6 and all(r["on_beat"] for r in rows)
    assert [r["bar"] for r in rows if r["off_bar"] == 0] == [1, 2, 3, 4]
    assert warnings == []


@needs_ffmpeg
def test_check_warns_when_footage_runs_out(example_dir):
    import json
    from editdata import check, load
    data = json.loads((example_dir / "edit.json").read_text(encoding="utf-8"))
    data["shots"][0]["in_s"] = 7.5                    # the source is 8 s long; the shot needs 1.92 s
    p = example_dir / "short.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    _, warnings = check.report(load(str(p)))
    assert len(warnings) == 1 and warnings[0].startswith("shot 1 (bars) asks for 1.42 s more")


@needs_ffmpeg
def test_short_footage_still_keeps_the_shot_length(example_dir):
    # the last frame holds (tpad) so the cuts after it do not move
    import json
    from editdata import load, render
    from conftest import ffprobe
    data = json.loads((example_dir / "edit.json").read_text(encoding="utf-8"))
    data["shots"][0]["in_s"] = 7.5
    p = example_dir / "short.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    out = render.render(load(str(p)), str(example_dir / "out" / "short.mp4"), log=lambda *a: None)
    d = ffprobe(out, "-count_frames", "-select_streams", "v", "-show_entries", "stream=nb_read_frames")
    assert int(d["streams"][0]["nb_read_frames"]) == 192

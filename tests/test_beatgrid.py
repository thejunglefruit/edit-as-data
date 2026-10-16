# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
import json
import os
import shutil
import subprocess

import pytest

from conftest import needs_ffmpeg, write_edit


def click(tmp_path, name, bpm, lead, start=None, seconds=16):
    from make_example import click_expr
    p = str(tmp_path / name)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='{click_expr(bpm, lead, start=start)}':s=44100:d={seconds}", p], check=True)
    return p


@needs_ffmpeg
@pytest.mark.parametrize("bpm, lead", [(125, 0.5), (96, 1.1), (140, 0.0)])
def test_tempo_and_downbeat_of_a_click_track(tmp_path, bpm, lead):
    from editdata import beatgrid
    r = beatgrid.analyse(click(tmp_path, f"c{bpm}.wav", bpm, lead))
    assert r["bpm"] == pytest.approx(bpm, abs=0.05)
    assert r["first_beat"] == pytest.approx(lead, abs=0.005)
    assert r["first_downbeat"] == pytest.approx(lead, abs=0.005)


@needs_ffmpeg
def test_downbeat_when_the_track_starts_mid_bar(tmp_path):
    # clicks start on beat 3 of a bar: the first beat heard is not the 'one'
    from editdata import beatgrid
    beat = 60 / 120
    r = beatgrid.analyse(click(tmp_path, "mid.wav", 120, 0.25, start=0.25 + 2 * beat - 0.01))
    assert r["first_beat"] == pytest.approx(0.25 + 2 * beat, abs=0.005)
    assert r["first_downbeat"] == pytest.approx(0.25 + 4 * beat, abs=0.005)


@needs_ffmpeg
def test_edit_without_bpm_measures_the_music(tmp_path):
    from editdata import load
    shutil.copy(click(tmp_path, "m.wav", 125, 0.5), tmp_path / "music.wav")
    p = write_edit(tmp_path, {"fps": 25, "music": {"path": "music.wav", "offset_s": 0.5},
                              "sources": {"a": {"path": "a.mp4"}},
                              "shots": [{"src": "a", "bars": 1}, {"src": "a", "beats": 2}, {"src": "a", "to_bar": 3}]})
    e = load(p)
    assert e["grid"]["source"] == "measured"
    assert e["grid"]["bpm"] == pytest.approx(125, abs=0.05)
    assert [s["rec_out"] for s in e["shots"]] == [48, 72, 96]


@needs_ffmpeg
def test_measure_cli_writes_json(tmp_path):
    from editdata.__main__ import main
    out = tmp_path / "grid.json"
    assert main(["measure", click(tmp_path, "c.wav", 100, 0.3), "--json", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["bpm"] == pytest.approx(100, abs=0.05) and len(d["rms_db_per_second"]) == 16
    assert os.path.basename(d["audio"]) == "c.wav"

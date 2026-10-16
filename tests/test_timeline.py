# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
import os

import pytest

from conftest import EXAMPLES, write_edit
from editdata import EditError, load, tc


def test_example_resolves_against_the_grid():
    e = load(os.path.join(EXAMPLES, "edit.json"))
    assert e["grid"]["beat_frames"] == 12.0          # 125 BPM at 25 fps
    assert [s["rec_in"] for s in e["shots"]] == [0, 48, 72, 96, 144, 156]
    assert e["total_frames"] == 192 and e["total_seconds"] == pytest.approx(7.68)
    assert e["shots"][4]["src"] is None               # the gap
    assert e["shots"][3]["span_s"] == pytest.approx(0.96)   # 48 frames at half speed
    assert e["sfx"][0]["rec_in"] == 96                # at_bar 3
    assert e["titles"][0]["start"] == 0 and e["titles"][0]["frames"] == 48


def test_fractional_beats_do_not_drift(tmp_path):
    # 120 BPM at 25 fps is 12.5 frames per beat: beats alternate 12 and 13, and 8 beats are exactly 100
    p = write_edit(tmp_path, {"fps": 25, "music": {"path": "m.wav", "bpm": 120},
                              "sources": {"a": {"path": "a.mp4"}},
                              "shots": [{"src": "a", "beats": 1} for _ in range(8)]})
    e = load(p)
    assert [s["frames"] for s in e["shots"]] == [13, 12, 13, 12, 13, 12, 13, 12]
    assert e["total_frames"] == 100


def test_offset_moves_the_grid(tmp_path):
    # the downbeat is 1 s into the file and the music starts 0.6 s in: the grid's bar 1 is at 0.4 s
    p = write_edit(tmp_path, {"fps": 25, "music": {"path": "m.wav", "bpm": 125, "first_downbeat_s": 1.0,
                                                   "offset_s": 0.6},
                              "sources": {"a": {"path": "a.mp4"}},
                              "shots": [{"src": "a", "to_bar": 1}, {"src": "a", "to_bar": 2}]})
    e = load(p)
    assert [s["rec_out"] for s in e["shots"]] == [10, 58]


@pytest.mark.parametrize("shots, msg", [
    ([{"src": "nope", "frames": 10}], "unknown source"),
    ([{"src": "a", "frames": 10, "seconds": 1}], "exactly one"),
    ([{"src": "a", "beats": 2}], "no `music` block"),
    ([{"src": "a", "frames": 10}, {"src": "a", "frames": 0}], "not after its start"),
    ([{"src": "a", "frames": 10, "fit": "stretch"}], "fit must be"),
])
def test_invalid_edits_say_where(tmp_path, shots, msg):
    p = write_edit(tmp_path, {"sources": {"a": {"path": "a.mp4"}}, "shots": shots})
    with pytest.raises(EditError, match=msg):
        load(p)


@pytest.mark.parametrize("font", ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/x.ttf"])
def test_title_font_must_be_relative(tmp_path, font):
    # a drive letter inside a filter option breaks ffmpeg's parser on ':'
    p = write_edit(tmp_path, {"sources": {"a": {"path": "a.mp4"}}, "shots": [{"src": "a", "frames": 10}],
                              "titles": [{"text": "x", "font": font}]})
    with pytest.raises(EditError, match="relative"):
        load(p)


def test_timecode():
    assert tc(0, 25) == "00:00:00:00"
    assert tc(25 * 3661 + 7, 25) == "01:01:01:07"
    assert tc(48, 24) == "00:00:02:00"

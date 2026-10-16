# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
import csv
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import pytest

from conftest import needs_ffmpeg

EVENT = re.compile(r"^(\d{3})  (\S+)\s+V\s+C\s+(\d\d:\d\d:\d\d:\d\d) (\d\d:\d\d:\d\d:\d\d) "
                   r"(\d\d:\d\d:\d\d:\d\d) (\d\d:\d\d:\d\d:\d\d)$")


def frames(tc, fps=25):
    h, m, s, f = map(int, tc.split(":"))
    return ((h * 60 + m) * 60 + s) * fps + f


@pytest.fixture(scope="module")
def exported(example_dir, tmp_path_factory):
    from editdata import export, load
    edit = load(str(example_dir / "edit.json"))
    return edit, export.export_all(edit, str(tmp_path_factory.mktemp("export")))


@needs_ffmpeg
def test_xml_is_a_valid_xmeml_sequence(exported):
    edit, paths = exported
    root = ET.parse(paths["xml"]).getroot()
    assert root.tag == "xmeml" and root.get("version") == "5"
    seq = root.find("sequence")
    assert int(seq.findtext("duration")) == 192 and seq.findtext("rate/timebase") == "25"
    vtrack = seq.find("media/video/track")
    clips = vtrack.findall("clipitem")
    shots = [s for s in edit["shots"] if s["src"]]
    assert len(clips) == len(shots) == 5                   # the gap is empty track
    for c, s in zip(clips, shots):
        assert (int(c.findtext("start")), int(c.findtext("end"))) == (s["rec_in"], s["rec_out"])
    # every <file> reference resolves to a full definition with a path that exists
    defined = {f.get("id"): f for f in seq.iter("file") if f.find("pathurl") is not None}
    for f in seq.iter("file"):
        assert f.get("id") in defined
    for f in defined.values():
        url = urllib.parse.urlparse(f.findtext("pathurl"))
        assert url.scheme == "file" and url.netloc == "localhost"
        p = urllib.request.url2pathname(url.path.lstrip("/") if os.name == "nt" else url.path)
        assert os.path.exists(p), p


@needs_ffmpeg
def test_xml_speed_and_source_timebase(exported):
    _, paths = exported
    seq = ET.parse(paths["xml"]).getroot().find("sequence")
    film = seq.findall("media/video/track/clipitem")[3]
    assert film.findtext("rate/timebase") == "24"           # in/out in the FILE's timebase
    assert (int(film.findtext("in")), int(film.findtext("out"))) == (12, 35)   # 0.5 s in, 0.96 s of source
    assert film.findtext("filter/effect/effectid") == "timeremap"
    assert float(film.findtext("filter/effect/parameter/value")) == 50.0


@needs_ffmpeg
def test_xml_audio_and_markers(exported):
    _, paths = exported
    seq = ET.parse(paths["xml"]).getroot().find("sequence")
    music, sfx = seq.findall("media/audio/track")
    m = music.find("clipitem")
    assert (m.findtext("in"), m.findtext("out")) == ("12", "204")      # offset 0.5 s at 25 fps
    s = sfx.find("clipitem")
    assert s.findtext("start") == "96"
    assert float(s.findtext("filter/effect/parameter/value")) == pytest.approx(10 ** (-6 / 20), abs=1e-4)
    assert [(k.findtext("name"), k.findtext("in")) for k in seq.findall("marker")] == [("bar 1", "0"), ("bar 3", "96")]


@needs_ffmpeg
def test_edl_is_well_formed_cmx3600(exported):
    edit, paths = exported
    raw = open(paths["edl"], "rb").read()
    assert b"\r\n" in raw and raw.replace(b"\r\n", b"").count(b"\n") == 0     # CRLF throughout
    text = raw.decode("ascii")
    lines = text.splitlines()
    assert lines[0] == "TITLE: EXAMPLE" and lines[1] == "FCM: NON-DROP FRAME"
    events = [EVENT.match(l) for l in lines if re.match(r"^\d{3} ", l)]
    assert all(events) and len(events) == len(edit["shots"]) == 6
    rec = 0
    for n, (ev, sh) in enumerate(zip(events, edit["shots"]), 1):
        num, reel, sin, sout, rin, rout = ev.groups()
        assert int(num) == n
        assert reel == ("AX" if sh["src"] else "BL")
        assert frames(rin) == rec == sh["rec_in"]                   # record side is continuous
        assert frames(rout) - frames(rin) == sh["frames"]
        rec = frames(rout)
    assert rec == 192
    m2 = [l for l in lines if l.startswith("M2")]
    assert len(m2) == 1 and "012.5" in m2[0]                        # 25 fps at 50 %


@needs_ffmpeg
def test_csv_shot_list(exported):
    _, paths = exported
    with open(paths["csv"], encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["tc_in"] for r in rows] == ["00:00:00:00", "00:00:01:23", "00:00:02:22",
                                          "00:00:03:21", "00:00:05:19", "00:00:06:06"]
    assert rows[4]["source"] == "(gap)"

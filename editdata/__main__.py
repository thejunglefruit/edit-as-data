# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Command line: python -m editdata {measure,check,render,export,sheet} ..."""

import argparse
import json
import os
import sys

from . import beatgrid, check, export, render
from .timeline import EditError, load


def main(argv=None):
    ap = argparse.ArgumentParser(prog="editdata", description="An edit as data: edit.json -> mp4, FCP7 XML, EDL.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("measure", help="tempo, first downbeat and loudness of a music track")
    m.add_argument("audio")
    m.add_argument("--beats-per-bar", type=int, default=4)
    m.add_argument("--fps", type=float, default=25.0)
    m.add_argument("--json", help="also write the measurement to this file")

    c = sub.add_parser("check", help="how far each cut sits from the beat, and footage that runs out")
    c.add_argument("edit")
    c.add_argument("--tol", type=float, default=2.0, help="frames off the beat that still count as on it")

    r = sub.add_parser("render", help="render the edit to an mp4")
    r.add_argument("edit")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--crf", type=int, default=18)
    r.add_argument("--clear-cache", action="store_true", help="delete the conformed shots afterwards")

    e = sub.add_parser("export", help="write FCP7 XML, CMX3600 EDL and a CSV shot list")
    e.add_argument("edit")
    e.add_argument("-o", "--outdir", required=True)

    s = sub.add_parser("sheet", help="contact sheet: the middle frame of every shot of a render")
    s.add_argument("edit")
    s.add_argument("video")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--cols", type=int, default=4)
    s.add_argument("--width", type=int, default=320)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "measure":
            res = beatgrid.analyse(a.audio, a.beats_per_bar, a.fps)
            if a.json:
                with open(a.json, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, indent=2)
            print(f"{os.path.basename(a.audio)}: {res['duration']} s")
            print(f"  bpm            {res['bpm']}  (beat = {res['beat_seconds']} s = {res['beat_frames']} f at {a.fps:g} fps)")
            print(f"  first beat     {res['first_beat']} s")
            print(f"  first downbeat {res['first_downbeat']} s   (bar = {res['bar_seconds']} s)")
            prof = res["rms_db_per_second"]
            for i in range(0, len(prof), 16):
                print(f"  {i:4d}s  " + " ".join(f"{v:6.1f}" for v in prof[i:i + 16]))
            return 0
        edit = load(a.edit)
        if a.cmd == "check":
            _, warnings = check.print_report(edit, a.tol)
            return 0
        if a.cmd == "render":
            out = render.render(edit, a.out, crf=a.crf, keep_cache=not a.clear_cache)
            print(f"-> {out}")
            return 0
        if a.cmd == "export":
            for kind, p in export.export_all(edit, a.outdir).items():
                print(f"-> {kind:3s} {p}")
            return 0
        if a.cmd == "sheet":
            print("-> " + render.contact_sheet(edit, a.video, a.out, a.cols, a.width))
            return 0
    except (EditError, render.RenderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())

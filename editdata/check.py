# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Report how far every cut sits from the musical grid, and which shots run out of footage.

It moves nothing. Deciding which cut moves is an editing decision, not a script's:
an edit cut entirely on the beat turns monotonous.
"""

from .timeline import footage_short, nearest, tc


def report(edit, tol=2.0):
    """Rows (one per cut) plus a list of warnings."""
    g, fps = edit["grid"], edit["fps"]
    rows, warnings = [], []
    for sh in edit["shots"]:
        row = {"shot": sh["i"], "frame": sh["rec_in"], "tc": tc(sh["rec_in"], fps),
               "source": sh["src"] or "(gap)"}
        if g:
            db, kb = nearest(sh["rec_in"], g["downbeat_frame"], g["beat_frames"])
            dc, kc = nearest(sh["rec_in"], g["downbeat_frame"], g["bar_frames"])
            row.update(off_beat=round(db, 2), beat=kb, off_bar=round(dc, 2), bar=kc + 1,
                       on_beat=abs(db) <= tol)
        short = footage_short(edit, sh)
        if short > 1e-3:
            warnings.append(f"shot {sh['i']} ({sh['src']}) asks for {short:.2f} s more footage than "
                            f"the source has: the last frame will hold. Shorten it, move its in "
                            f"point back or slow it down.")
        rows.append(row)
    return rows, warnings


def print_report(edit, tol=2.0, out=print):
    rows, warnings = report(edit, tol)
    g = edit["grid"]
    out(f"{edit['name']}: {len(edit['shots'])} shots, {edit['total_frames']} frames "
        f"({edit['total_seconds']:.2f} s at {edit['fps']:g} fps)")
    if g:
        out(f"grid ({g['source']}): {g['bpm']:g} BPM, beat = {g['beat_frames']:.3f} f, "
            f"bar = {g['bar_frames']:.3f} f, first downbeat at frame {g['downbeat_frame']:.2f}")
        out(f"{'shot':>4} {'frame':>6} {'timecode':>12} {'to beat':>8} {'beat':>5} {'to bar':>7} {'bar':>4}  source")
        for r in rows:
            flag = "" if r["on_beat"] else "   << off the beat"
            out(f"{r['shot']:>4} {r['frame']:>6} {r['tc']:>12} {r['off_beat']:>+8.2f} {r['beat']:>5} "
                f"{r['off_bar']:>+7.2f} {r['bar']:>4}  {r['source']}{flag}")
        off = sum(1 for r in rows if not r["on_beat"])
        out(f"{off} of {len(rows)} cuts more than {tol:g} frames off the beat")
    else:
        out("no beat grid (no music bpm and no musical units): cut positions only")
        for r in rows:
            out(f"{r['shot']:>4} {r['frame']:>6} {r['tc']:>12}  {r['source']}")
    for w in warnings:
        out("! " + w)
    return rows, warnings

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Export a resolved edit to formats an NLE opens.

  FCP7 XML (xmeml v5)  The one to use. Premiere (File > Import), Resolve and Final Cut
                       Pro 7 read it: shots on V1 linked to the original files, speed
                       changes as a Time Remap filter, music on A1, sound effects on A2
                       at their frame and gain, markers on the sequence.
  CMX3600 EDL          Plan B. Cuts only: an EDL cannot carry speed (it goes as an M2
                       record), overlays or multitrack audio (music and sfx go as
                       comments). Good to move the cuts to another room, not to rebuild
                       the piece.
  CSV                  One row per shot, to check by eye or in a spreadsheet.
"""

import csv
import os
import urllib.parse
from xml.sax.saxutils import escape

from .timeline import source_info, tc


def pathurl(p):
    """file:// URL as Premiere writes it: file://localhost/<absolute path with forward slashes>."""
    p = os.path.abspath(p).replace("\\", "/")
    return "file://localhost" + ("" if p.startswith("/") else "/") + urllib.parse.quote(p, safe="/:")


def _rate(fps, ind):
    ntsc = "TRUE" if abs(fps - round(fps)) > 1e-6 else "FALSE"
    return f"{ind}<rate>\n{ind}  <timebase>{int(round(fps))}</timebase>\n{ind}  <ntsc>{ntsc}</ntsc>\n{ind}</rate>\n"


def _audio_len(edit, path, default):
    from .timeline import probe
    d = probe(path).get("duration")
    return int(round(d * edit["fps"])) if d else default


def build_xml(edit):
    fps, total, w, h = edit["fps"], edit["total_frames"], edit["width"], edit["height"]
    o = []
    a = o.append
    a('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">\n'
      '  <sequence id="sequence-1">\n')
    a(f"    <name>{escape(edit['name'])}</name>\n    <duration>{total}</duration>\n")
    a(_rate(fps, "    "))
    a("    <timecode>\n")
    a(_rate(fps, "      "))
    a("      <string>00:00:00:00</string>\n      <frame>0</frame>\n"
      "      <displayformat>NDF</displayformat>\n    </timecode>\n")
    a("    <media>\n      <video>\n        <format>\n          <samplecharacteristics>\n")
    a(_rate(fps, "            "))
    a(f"            <width>{w}</width>\n            <height>{h}</height>\n"
      "            <pixelaspectratio>square</pixelaspectratio>\n"
      "          </samplecharacteristics>\n        </format>\n        <track>\n")

    seen = {}
    for sh in edit["shots"]:
        if not sh["src"]:
            continue                                    # a gap is just empty track
        info = source_info(edit, sh["src"])
        src = edit["sources"][sh["src"]]
        sfps = info.get("fps") or fps
        first = sh["src"] not in seen
        fid = seen.setdefault(sh["src"], f"file-{len(seen) + 1}")
        # in/out in the FILE's timebase: that is what the xmeml spec says, and what makes
        # 24 fps sources land in place on a 25 fps sequence
        sin = int(round(sh["in_s"] * sfps))
        sout = sin + max(1, int(round(sh["span_s"] * sfps)))
        a(f'          <clipitem id="clipitem-{sh["i"]}">\n'
          f"            <name>{escape(sh['src'])}</name>\n"
          f"            <enabled>TRUE</enabled>\n"
          f"            <duration>{sout}</duration>\n")
        a(_rate(sfps, "            "))
        a(f"            <start>{sh['rec_in']}</start>\n            <end>{sh['rec_out']}</end>\n"
          f"            <in>{sin}</in>\n            <out>{sout}</out>\n")
        if first:
            a(f'            <file id="{fid}">\n'
              f"              <name>{escape(os.path.basename(src['abspath']))}</name>\n"
              f"              <pathurl>{escape(pathurl(src['abspath']))}</pathurl>\n")
            a(_rate(sfps, "              "))
            if info.get("duration"):
                a(f"              <duration>{int(round(info['duration'] * sfps))}</duration>\n")
            a("              <media>\n                <video>\n                  <samplecharacteristics>\n"
              f"                    <width>{info.get('width') or w}</width>\n"
              f"                    <height>{info.get('height') or h}</height>\n"
              "                  </samplecharacteristics>\n                </video>\n"
              "              </media>\n            </file>\n")
        else:
            a(f'            <file id="{fid}"/>\n')
        if abs(sh["speed_pct"] - 100.0) > 0.01:
            a("            <filter>\n              <effect>\n"
              "                <name>Time Remap</name>\n                <effectid>timeremap</effectid>\n"
              "                <effectcategory>motion</effectcategory>\n"
              "                <effecttype>motion</effecttype>\n                <mediatype>video</mediatype>\n"
              "                <parameter>\n                  <parameterid>speed</parameterid>\n"
              "                  <name>speed</name>\n"
              f"                  <value>{sh['speed_pct']:.4f}</value>\n"
              "                </parameter>\n              </effect>\n            </filter>\n")
        note = " | ".join(x for x in (sh["fit"] if sh["fit"] != "fill" else "", sh["note"]) if x)
        if note:
            a(f"            <comments><mastercomment1>{escape(note)}</mastercomment1></comments>\n")
        a("          </clipitem>\n")
    a("        </track>\n      </video>\n      <audio>\n")

    m = edit["music"]
    if m:
        off = int(round(float(m.get("offset_s", 0.0)) * fps))
        a('        <track>\n          <clipitem id="clipitem-music">\n'
          f"            <name>{escape(os.path.basename(m['abspath']))}</name>\n"
          f"            <duration>{_audio_len(edit, m['abspath'], off + total)}</duration>\n")
        a(_rate(fps, "            "))
        a(f"            <start>0</start>\n            <end>{total}</end>\n"
          f"            <in>{off}</in>\n            <out>{off + total}</out>\n"
          '            <file id="file-music">\n'
          f"              <name>{escape(os.path.basename(m['abspath']))}</name>\n"
          f"              <pathurl>{escape(pathurl(m['abspath']))}</pathurl>\n")
        a(_rate(fps, "              "))
        a("              <media><audio><channelcount>2</channelcount></audio></media>\n"
          "            </file>\n          </clipitem>\n        </track>\n")
    if edit["sfx"]:
        a("        <track>\n")
        for j, s in enumerate(edit["sfx"], 1):
            n = _audio_len(edit, s["abspath"], int(round(3 * fps)))
            end = min(s["rec_in"] + n, total)
            a(f'          <clipitem id="clipitem-sfx-{j}">\n'
              f"            <name>{escape(os.path.basename(s['abspath']))}</name>\n"
              f"            <duration>{n}</duration>\n")
            a(_rate(fps, "            "))
            a(f"            <start>{s['rec_in']}</start>\n            <end>{end}</end>\n"
              f"            <in>0</in>\n            <out>{end - s['rec_in']}</out>\n"
              f'            <file id="file-sfx-{j}">\n'
              f"              <name>{escape(os.path.basename(s['abspath']))}</name>\n"
              f"              <pathurl>{escape(pathurl(s['abspath']))}</pathurl>\n")
            a(_rate(fps, "              "))
            a("              <media><audio><channelcount>2</channelcount></audio></media>\n"
              "            </file>\n"
              "            <filter>\n              <effect>\n"
              "                <name>Audio Levels</name>\n                <effectid>audiolevels</effectid>\n"
              "                <effectcategory>audiolevels</effectcategory>\n"
              "                <effecttype>audiolevels</effecttype>\n                <mediatype>audio</mediatype>\n"
              "                <parameter>\n                  <parameterid>level</parameterid>\n"
              "                  <name>Level</name>\n"
              f"                  <value>{10 ** (s['gain_db'] / 20):.5f}</value>\n"
              "                </parameter>\n              </effect>\n            </filter>\n")
            if s["note"]:
                a(f"            <comments><mastercomment1>{escape(s['note'])} ({s['gain_db']:+.1f} dB)"
                  "</mastercomment1></comments>\n")
            a("          </clipitem>\n")
        a("        </track>\n")
    a("      </audio>\n    </media>\n")
    for f, name in edit["markers"]:
        a(f"    <marker>\n      <name>{escape(name)}</name>\n      <comment></comment>\n"
          f"      <in>{f}</in>\n      <out>-1</out>\n    </marker>\n")
    a("  </sequence>\n</xmeml>\n")
    return "".join(o)


def build_edl(edit):
    """CMX3600, every timecode at the sequence rate. Gaps become BL (black) events,
    so the record side is continuous from the first frame to the last."""
    fps = edit["fps"]
    title = "".join(c for c in edit["name"].upper() if c.isalnum() or c in " _-")[:60] or "EDIT"
    o = [f"TITLE: {title}\nFCM: NON-DROP FRAME\n\n"]
    for n, sh in enumerate(edit["shots"], 1):
        reel = "AX" if sh["src"] else "BL"
        sin = int(round(sh["in_s"] * fps)) if sh["src"] else 0
        sout = sin + (int(round(sh["span_s"] * fps)) if sh["src"] else sh["frames"])
        o.append(f"{n:03d}  {reel:<8} V     C        {tc(sin, fps)} {tc(sout, fps)} "
                 f"{tc(sh['rec_in'], fps)} {tc(sh['rec_out'], fps)}\n")
        if sh["src"]:
            if abs(sh["speed_pct"] - 100.0) > 0.01:
                o.append(f"M2   {reel:<8} {fps * sh['speed_pct'] / 100.0:05.1f}"
                         f"                {tc(sin, fps)}\n")
            o.append(f"* FROM CLIP NAME: {os.path.basename(edit['sources'][sh['src']]['abspath'])}\n")
        o.append("\n")
    o.append("* AUDIO IS NOT EVENTS HERE (THIS EDL CARRIES V ONLY). USE THE XML TO REBUILD IT.\n")
    if edit["music"]:
        o.append(f"* A1 MUSIC {os.path.basename(edit['music']['abspath'])} "
                 f"{tc(0, fps)} {tc(edit['total_frames'], fps)}\n")
    for s in edit["sfx"]:
        o.append(f"* A2 SFX {tc(s['rec_in'], fps)} {s['gain_db']:+.1f} DB {os.path.basename(s['abspath'])}\n")
    for f, name in edit["markers"]:
        o.append(f"* MARKER {tc(f, fps)} {name}\n")
    return "".join(o)


def write_csv(edit, path):
    fps = edit["fps"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "tc_in", "tc_out", "frames", "seconds", "source", "source_in_s",
                    "speed_pct", "fit", "note"])
        for sh in edit["shots"]:
            w.writerow([sh["i"], tc(sh["rec_in"], fps), tc(sh["rec_out"], fps), sh["frames"],
                        f"{sh['frames'] / fps:.3f}", sh["src"] or "(gap)", f"{sh['in_s']:.3f}",
                        f"{sh['speed_pct']:g}", sh["fit"], sh["note"]])


def export_all(edit, outdir, stem=None):
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, stem or edit["name"])
    paths = {"xml": base + ".xml", "edl": base + ".edl", "csv": base + ".csv"}
    with open(paths["xml"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_xml(edit))
    with open(paths["edl"], "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write(build_edl(edit))
    write_csv(edit, paths["csv"])
    return paths

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
sys.path.insert(0, ROOT)
sys.path.insert(0, EXAMPLES)

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not in the PATH")


@pytest.fixture(scope="session")
def example_dir(tmp_path_factory):
    """A copy of examples/ with its media generated, in a temporary folder."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not in the PATH")
    import make_example
    d = tmp_path_factory.mktemp("example")
    shutil.copy(os.path.join(EXAMPLES, "edit.json"), d / "edit.json")
    shutil.copytree(os.path.join(EXAMPLES, "fonts"), d / "fonts")
    make_example.make(str(d / "media"))
    return d


@pytest.fixture(scope="session")
def rendered(example_dir):
    from editdata import load, render
    edit = load(str(example_dir / "edit.json"))
    out = render.render(edit, str(example_dir / "out" / "example.mp4"), log=lambda *a: None)
    return edit, out


def write_edit(folder, data):
    p = os.path.join(str(folder), "edit.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return p


def ffprobe(path, *entries):
    r = subprocess.run(["ffprobe", "-v", "error", *entries, "-of", "json", path],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)

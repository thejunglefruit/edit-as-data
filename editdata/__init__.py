# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""edit-as-data: an edit described as JSON, cut against the music, rendered with ffmpeg
and exported to FCP7 XML (Premiere, Resolve) and CMX3600 EDL."""

from .timeline import EditError, load, tc

__all__ = ["EditError", "load", "tc"]
__version__ = "0.1.0"

from __future__ import annotations

import json
import subprocess
from typing import Optional

from core.ffmpeg import which_ffprobe


def get_duration_seconds(path: str) -> float:
    """
    Длительность файла через ffprobe.
    """
    ffprobe = which_ffprobe()
    if not ffprobe:
        raise RuntimeError("ffprobe не найден в PATH")

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        path
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr[:300]}")
    data = json.loads(p.stdout)
    dur = float(data["format"]["duration"])
    return dur

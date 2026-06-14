from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from core.ffmpeg import which_ffmpeg, run_cmd, file_size_bytes, human_file_size

CanvasIntensity = Literal["low", "medium", "high"]
CanvasVideoMode = Literal["crop", "blur"]

_INTENSITY_TO_AMPLITUDE = {
    "low": 0.02,
    "medium": 0.03,
    "high": 0.04,
}


def _ensure_input_exists(input_path: str) -> Path:
    p = Path(str(input_path))
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Файл не найден: {input_path}")
    return p



def _ensure_ffmpeg() -> str:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")
    return ffmpeg



def _safe_seconds(value: float, *, default: float, min_value: float, max_value: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = float(default)
    return max(float(min_value), min(float(max_value), v))



def _safe_fps(value: int, *, default: int = 30) -> int:
    try:
        v = int(value)
    except Exception:
        v = int(default)
    return max(1, min(120, v))



def make_canvas_from_image(
    input_path: str,
    out_path: str,
    seconds: float = 5,
    fps: int = 30,
    intensity: CanvasIntensity = "low",
) -> str:
    """Создаёт Spotify Canvas 9:16 из статичной картинки.

    Делает бесшовный breathing zoom: первый и последний кадр совпадают по фазе.
    Выход: H.264 MP4, yuv420p, 1080x1920.
    """
    ffmpeg = _ensure_ffmpeg()
    in_p = _ensure_input_exists(input_path)
    out_p = Path(str(out_path))
    out_p.parent.mkdir(parents=True, exist_ok=True)

    sec = _safe_seconds(seconds, default=5.0, min_value=4.0, max_value=8.0)
    fps_i = _safe_fps(fps, default=30)
    frames = max(2, int(round(sec * fps_i)))
    frames_denom = max(1, frames - 1)
    amp = _INTENSITY_TO_AMPLITUDE.get(str(intensity).lower(), 0.02)
    z0 = 1.04

    # Берём крупный вертикальный холст с запасом, чтобы zoompan не «упёрся» в границы.
    pre_w = 2600
    pre_h = 4622  # ~9:16

    vf = (
        f"scale={pre_w}:{pre_h}:force_original_aspect_ratio=increase,"
        f"crop={pre_w}:{pre_h},"
        f"zoompan="
        f"z='if(eq(on,0),{z0:.5f},{z0:.5f}+{amp:.5f}*sin(2*PI*on/{frames_denom}))':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={frames}:"
        f"s=1080x1920:"
        f"fps={fps_i},"
        f"format=yuv420p"
    )

    cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        str(in_p),
        "-vf",
        vf,
        "-frames:v",
        str(frames),
        "-r",
        str(fps_i),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_p),
    ]
    run_cmd(cmd, check=True)
    return str(out_p)



def make_vertical_from_video(
    input_path: str,
    out_path: str,
    seconds: Optional[float] = 6,
    fps: int = 30,
    mode: CanvasVideoMode = "crop",
) -> str:
    """Приводит стороннее видео к 9:16 1080x1920.

    mode='crop' -> Fill + center crop.
    mode='blur' -> Fit + blurred background.
    """
    ffmpeg = _ensure_ffmpeg()
    in_p = _ensure_input_exists(input_path)
    out_p = Path(str(out_path))
    out_p.parent.mkdir(parents=True, exist_ok=True)

    fps_i = _safe_fps(fps, default=30)

    mode_norm = str(mode).lower().strip()
    if mode_norm not in {"crop", "blur"}:
        mode_norm = "crop"

    if mode_norm == "blur":
        vf = (
            "split[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=20:10[bg2];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"
            "[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
            f"fps={fps_i},format=yuv420p"
        )
    else:
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            f"fps={fps_i},format=yuv420p"
        )

    cmd = [ffmpeg, "-y"]
    if seconds is not None:
        sec = _safe_seconds(seconds, default=6.0, min_value=4.0, max_value=10.0)
        cmd += ["-t", f"{sec:.3f}"]

    cmd += [
        "-i",
        str(in_p),
        "-vf",
        vf,
        "-r",
        str(fps_i),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_p),
    ]
    run_cmd(cmd, check=True)
    return str(out_p)



def output_info(path: str) -> str:
    size = file_size_bytes(path)
    return human_file_size(size)

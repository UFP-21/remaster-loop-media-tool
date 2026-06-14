from __future__ import annotations

from core.ffmpeg import which_ffmpeg, run
from core.presets import PRESETS


def apply_mastering_preset(
    input_path: str,
    output_path: str,
    preset_name: str,
    sample_rate: int = 48000,
    mp3_bitrate: str | None = None,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в PATH")

    if preset_name not in PRESETS:
        raise RuntimeError(f"Неизвестный пресет: {preset_name}")

    af = PRESETS[preset_name]

    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-af", af,
        "-ar", str(sample_rate),
    ]

    if mp3_bitrate:
        cmd += ["-c:a", "libmp3lame", "-b:a", mp3_bitrate, output_path]
    else:
        cmd += ["-c:a", "pcm_s16le", output_path]

    run(cmd)

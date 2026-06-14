# core/mastering.py
# ----------------------------------------------------
# Применение пресета к аудио через ffmpeg.
# ----------------------------------------------------

from __future__ import annotations

from typing import Optional

from core.ffmpeg import run_cmd, which_ffmpeg
from core.presets import PRESETS


def apply_mastering_preset(
    input_path: str,
    output_path: str,
    preset_name: str,
    sample_rate: int = 48000,
    mp3_bitrate: Optional[str] = None,  # "320k" для MP3
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH.")

    if preset_name not in PRESETS:
        raise RuntimeError(f"Неизвестный пресет: {preset_name}")

    preset_filter = PRESETS[preset_name]

    args = [ffmpeg, "-y", "-i", input_path]

    # Если пресет None -> без фильтра
    if preset_filter:
        args += ["-filter:a", preset_filter]

    args += ["-ac", "2", "-ar", str(sample_rate)]

    if mp3_bitrate:
        args += ["-c:a", "libmp3lame", "-b:a", mp3_bitrate]
    else:
        args += ["-c:a", "pcm_s16le"]

    args += [output_path]

    # CompletedProcess (subprocess) не имеет поля .ok — это было в requests.
    # run_cmd(..., check=True) сам бросит исключение, если ffmpeg завершился с ошибкой.
    run_cmd(args, check=True)

from __future__ import annotations

from pathlib import Path

from core.ffmpeg import which_ffmpeg, run
from core.audio.analysis import get_duration_seconds


def make_loopable_wav(
    input_path: str,
    output_wav_path: str,
    crossfade_sec: float,
    sample_rate: int = 48000,
) -> None:
    """
    Делает “бесшовный цикл” простым и надёжным способом:
    - берём хвост (последние crossfade_sec)
    - берём начало (первые crossfade_sec)
    - склеиваем хвост+начало через acrossfade
    - затем concat: (середина без крайних кусков) + (сшитый сегмент)
    """
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в PATH")

    dur = get_duration_seconds(input_path)
    d = float(crossfade_sec)
    if dur <= (d * 2 + 0.25):
        raise RuntimeError("Файл слишком короткий для такого crossfade. Уменьши секунды кроссфейда.")

    # Важно: приводим к 48kHz stereo на лету
    # seg1 = head (0..d), seg2 = tail (dur-d..dur), mid = (d..dur-d)
    # seam = acrossfade(tail, head)
    # out = concat(mid, seam)
    cmd = [
        ffmpeg,
        "-y",
        "-i", input_path,
        "-filter_complex",
        (
            f"[0:a]aformat=sample_rates={sample_rate}:channel_layouts=stereo,asplit=3[a0][a1][a2];"
            f"[a0]atrim=0:{d},asetpts=PTS-STARTPTS[head];"
            f"[a1]atrim={dur-d}:{dur},asetpts=PTS-STARTPTS[tail];"
            f"[a2]atrim={d}:{dur-d},asetpts=PTS-STARTPTS[mid];"
            f"[tail][head]acrossfade=d={d}:c1=tri:c2=tri[seam];"
            f"[mid][seam]concat=n=2:v=0:a=1[outa]"
        ),
        "-map", "[outa]",
        "-ar", str(sample_rate),
        output_wav_path,
    ]
    run(cmd)


def render_loop_to_duration(
    loopable_wav_path: str,
    output_path: str,
    target_seconds: float,
    sample_rate: int = 48000,
    mp3_bitrate: str | None = None,
) -> None:
    """
    Рендерим луп до целевой длительности.
    Используем stream_loop=-1 и ограничиваем -t target_seconds.
    """
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в PATH")

    cmd = [ffmpeg, "-y", "-stream_loop", "-1", "-i", loopable_wav_path, "-t", str(float(target_seconds))]

    if mp3_bitrate:
        cmd += ["-ar", str(sample_rate), "-c:a", "libmp3lame", "-b:a", mp3_bitrate, output_path]
    else:
        cmd += ["-ar", str(sample_rate), "-c:a", "pcm_s16le", output_path]

    run(cmd)


def render_preview(
    input_path: str,
    output_preview_path: str,
    preview_seconds: float = 45.0,
    sample_rate: int = 48000,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в PATH")

    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-t", str(float(preview_seconds)),
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        output_preview_path
    ]
    run(cmd)

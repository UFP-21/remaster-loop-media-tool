from __future__ import annotations

from typing import Optional

from core.ffmpeg import run_cmd, which_ffmpeg, probe_duration_seconds


def _sec(x: float) -> str:
    return f"{x:.6f}"


def make_loopable_wav(
    input_path: str,
    output_wav_path: str,
    crossfade_sec: float = 4.0,
    sample_rate: int = 48000,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH.")

    dur = probe_duration_seconds(input_path)
    if dur <= 0.2:
        raise RuntimeError("Файл слишком короткий.")

    cf = max(0.2, min(crossfade_sec, dur / 3.0))
    dur_minus_cf = max(0.0, dur - cf)

    filter_complex = (
        f"[0:a]atrim=0:{_sec(dur_minus_cf)},asetpts=PTS-STARTPTS[mid];"
        f"[0:a]atrim={_sec(dur_minus_cf)}:{_sec(dur)},asetpts=PTS-STARTPTS[tail];"
        f"[0:a]atrim=0:{_sec(cf)},asetpts=PTS-STARTPTS[head];"
        f"[tail][head]acrossfade=d={_sec(cf)}:c1=tri:c2=tri[cross];"
        f"[mid][cross]concat=n=2:v=0:a=1[out]"
    )

    args = [
        ffmpeg, "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ac", "2",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        output_wav_path,
    ]

    run_cmd(args, check=True)


def render_loop_to_duration(
    loopable_wav_path: str,
    output_path: str,
    target_seconds: float,
    sample_rate: int = 48000,
    mp3_bitrate: Optional[str] = None,
    fade_out_sec: float = 0.0,  # ✅ НОВОЕ: плавное затухание в конце
) -> None:
    """
    Рендерит длинный файл нужной длительности из loopable WAV.
    Если fade_out_sec > 0 — добавляет fade-out в конце на fade_out_sec секунд.
    """
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH.")

    if target_seconds <= 1:
        raise RuntimeError("Целевая длительность слишком маленькая.")

    # Защита: fade не может быть >= длины
    fo = max(0.0, float(fade_out_sec))
    if fo >= target_seconds:
        fo = max(0.0, target_seconds * 0.2)

    args = [
        ffmpeg, "-y",
        "-stream_loop", "-1",
        "-i", loopable_wav_path,
        "-t", f"{target_seconds:.3f}",
        "-ac", "2",
        "-ar", str(sample_rate),
    ]

    if fo > 0.0:
        # start time fade-out: target_seconds - fo
        st_time = max(0.0, target_seconds - fo)
        args += ["-af", f"afade=t=out:st={st_time:.3f}:d={fo:.3f}"]

    if mp3_bitrate:
        args += ["-c:a", "libmp3lame", "-b:a", mp3_bitrate]
    else:
        args += ["-c:a", "pcm_s16le"]

    args += [output_path]
    run_cmd(args, check=True)


def render_preview(
    input_path: str,
    output_preview_path: str,
    preview_seconds: float = 45.0,
    sample_rate: int = 48000,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH.")

    args = [
        ffmpeg, "-y",
        "-i", input_path,
        "-t", f"{preview_seconds:.3f}",
        "-ac", "2",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        output_preview_path,
    ]
    run_cmd(args, check=True)

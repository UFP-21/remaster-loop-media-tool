from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, List, Optional


# ────────────────────────────────────────────────────────────────
# FFmpeg / FFprobe discovery
# ────────────────────────────────────────────────────────────────
def which_ffmpeg() -> Optional[str]:
    """Ищем ffmpeg через env или PATH."""
    p = os.environ.get("FFMPEG_PATH")
    if p and Path(p).exists():
        return str(p)

    from shutil import which

    w = which("ffmpeg")
    return w



def which_ffprobe() -> Optional[str]:
    """Ищем ffprobe через env или PATH."""
    p = os.environ.get("FFPROBE_PATH")
    if p and Path(p).exists():
        return str(p)

    from shutil import which

    w = which("ffprobe")
    return w


# ────────────────────────────────────────────────────────────────
# Probing
# ────────────────────────────────────────────────────────────────
def probe_duration_seconds(media_path: str) -> Optional[float]:
    """Длительность через ffprobe. Возвращает float или None."""
    ffprobe = which_ffprobe()
    if not ffprobe:
        return None

    p = str(media_path)
    if not Path(p).exists():
        return None

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        p,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            return None
        s = (r.stdout or "").strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None



def file_size_bytes(path: str) -> Optional[int]:
    """Размер файла в байтах. None, если файла нет."""
    try:
        p = Path(str(path))
        if not p.exists() or not p.is_file():
            return None
        return int(p.stat().st_size)
    except Exception:
        return None



def human_file_size(num_bytes: Optional[int]) -> str:
    """Человеко-читаемый размер файла."""
    if num_bytes is None:
        return "—"

    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    while value >= 1024.0 and unit_idx < len(units) - 1:
        value /= 1024.0
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(value)} {units[unit_idx]}"
    return f"{value:.2f} {units[unit_idx]}"



def is_over_distrokid_limit(path: str, limit_gb: float = 1.0) -> bool:
    """Проверка лимита DistroKid по размеру файла."""
    size_b = file_size_bytes(path)
    if size_b is None:
        return False
    limit_b = int(float(limit_gb) * 1024 * 1024 * 1024)
    return size_b > limit_b


# ────────────────────────────────────────────────────────────────
# Running commands
# ────────────────────────────────────────────────────────────────
def run_cmd(cmd: List[str], check: bool = True, work_dir: Optional[str] = None) -> subprocess.CompletedProcess:
    """Обычный запуск."""
    try:
        r = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as ex:
        raise RuntimeError(f"Не удалось запустить команду: {' '.join(cmd)}\n{ex}") from ex

    if check and r.returncode != 0:
        stdout = r.stdout or ""
        stderr = r.stderr or ""
        msg = (
            "Команда завершилась с ошибкой:\n"
            + " ".join(cmd)
            + "\n\n--- STDOUT (first 4000 chars) ---\n"
            + stdout[:4000]
            + "\n\n--- STDERR (first 4000 chars) ---\n"
            + stderr[:4000]
        )
        raise RuntimeError(msg)

    return r



def run_cmd_stream(
    cmd: List[str],
    *,
    on_stdout_line: Optional[Callable[[str], None]] = None,
    check: bool = True,
    work_dir: Optional[str] = None,
) -> int:
    """Стриминговый запуск (для прогресса ffmpeg -progress pipe:1)."""
    try:
        p = subprocess.Popen(
            cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
    except Exception as ex:
        raise RuntimeError(f"Не удалось запустить команду: {' '.join(cmd)}\n{ex}") from ex

    collected: List[str] = []
    assert p.stdout is not None

    for line in p.stdout:
        line = line.rstrip("\n")
        collected.append(line)
        if on_stdout_line:
            try:
                on_stdout_line(line)
            except Exception:
                pass

    rc = p.wait()

    if check and rc != 0:
        tail = "\n".join(collected[-200:])
        raise RuntimeError(
            "Команда завершилась с ошибкой:\n"
            + " ".join(cmd)
            + "\n\n--- OUTPUT (last 200 lines) ---\n"
            + tail
        )

    return rc


# ────────────────────────────────────────────────────────────────
# Audio helpers: trim / silence-trim / post-normalize / export
# ────────────────────────────────────────────────────────────────
def trim_audio_to_wav(
    *,
    input_path: str,
    output_path: str,
    start_sec: float,
    end_sec: float,
    mode: str = "sharp",  # "sharp" | "fade"
    fade_in_sec: float = 0.0,
    fade_out_sec: float = 2.0,
    sample_rate: int = 48000,
) -> None:
    """Обрезка аудио в WAV (pcm_s16le). start/end — абсолютные секунды (что оставляем)."""
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")

    in_p = Path(str(input_path))
    if not in_p.exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    start = max(0.0, float(start_sec))
    end = max(start, float(end_sec))
    dur = end - start
    if dur <= 0.0:
        raise ValueError("Обрезка невозможна: end_sec должен быть больше start_sec.")

    af = None
    if mode == "fade":
        fi = max(0.0, float(fade_in_sec))
        fo = max(0.0, float(fade_out_sec))

        if fi > dur:
            fi = max(0.0, dur * 0.3)
        if fo > dur:
            fo = max(0.0, dur * 0.3)

        filters: List[str] = []
        if fi > 0:
            filters.append(f"afade=t=in:st=0:d={fi:.3f}")
        if fo > 0:
            st_out = max(0.0, dur - fo)
            filters.append(f"afade=t=out:st={st_out:.3f}:d={fo:.3f}")
        if filters:
            af = ",".join(filters)

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(in_p),
        "-vn",
    ]
    if af:
        cmd += ["-af", af]

    cmd += [
        "-ar",
        str(int(sample_rate)),
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    run_cmd(cmd, check=True)



def trim_silence_audio_to_wav(
    *,
    input_path: str,
    output_path: str,
    threshold_db: float = -35.0,
    min_silence_sec: float = 0.20,
    keep_sec: float = 0.05,
    sample_rate: int = 48000,
) -> None:
    """Авто-срез тишины (начало+конец) через silenceremove."""
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")

    in_p = Path(str(input_path))
    if not in_p.exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    thr = float(threshold_db)
    ms = max(0.01, float(min_silence_sec))
    keep = max(0.0, float(keep_sec))

    af = (
        "silenceremove="
        f"start_periods=1:start_duration={ms:.3f}:start_threshold={thr:.1f}dB:start_silence={keep:.3f}:"
        f"stop_periods=1:stop_duration={ms:.3f}:stop_threshold={thr:.1f}dB:stop_silence={keep:.3f}"
    )

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(in_p),
        "-vn",
        "-af",
        af,
        "-ar",
        str(int(sample_rate)),
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    run_cmd(cmd, check=True)



def normalize_audio_to_wav(
    *,
    input_path: str,
    output_path: str,
    target_i_lufs: int = -14,
    true_peak_db: float = -1.0,
    lra: int = 11,
    limiter: bool = False,
    sample_rate: int = 48000,
) -> None:
    """Пост-нормализация готового WAV/аудио: loudnorm на весь файл + опционально alimiter.

    ВАЖНО:
    Параметр loudnorm TP задаётся в dBFS (например, -1.0), а alimiter=limit ожидает
    уже ЛИНЕЙНОЕ значение амплитуды в диапазоне 0.0625..1.0.
    Поэтому нельзя передавать в alimiter отрицательное значение напрямую.
    """
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")
    in_p = Path(str(input_path))
    if not in_p.exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    af_parts = [
        f"loudnorm=I={int(target_i_lufs)}:TP={float(true_peak_db)}:LRA={int(lra)}:print_format=summary"
    ]

    if limiter:
        limiter_linear = 10 ** (float(true_peak_db) / 20.0)
        limiter_linear = max(0.0625, min(1.0, limiter_linear))
        af_parts.append(f"alimiter=limit={limiter_linear:.6f}")

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(in_p),
        "-vn",
        "-af",
        ",".join(af_parts),
        "-ar",
        str(int(sample_rate)),
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    run_cmd(cmd, check=True)



def encode_mp3(
    *,
    input_path: str,
    output_path: str,
    bitrate_k: int = 320,
) -> None:
    """Экспорт MP3 (для удобства)."""
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")
    in_p = Path(str(input_path))
    if not in_p.exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(in_p),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{int(bitrate_k)}k",
        str(output_path),
    ]
    run_cmd(cmd, check=True)



def encode_flac(
    *,
    input_path: str,
    output_path: str,
    sample_rate: int = 44100,
    compression_level: int = 8,
) -> None:
    """Lossless FLAC для DistroKid: тот же звук без потерь, но файл обычно сильно меньше WAV."""
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")

    in_p = Path(str(input_path))
    if not in_p.exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    level = max(0, min(12, int(compression_level)))

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(in_p),
        "-vn",
        "-ar",
        str(int(sample_rate)),
        "-ac",
        "2",
        "-c:a",
        "flac",
        "-compression_level",
        str(level),
        str(output_path),
    ]
    run_cmd(cmd, check=True)



def convert_audio_to_wav_16_44100(
    *,
    input_path: str,
    output_path: str,
) -> None:
    """Подготовка более лёгкого WAV для DistroKid: 16-bit / 44.1 kHz / stereo."""
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")

    in_p = Path(str(input_path))
    if not in_p.exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(in_p),
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    run_cmd(cmd, check=True)

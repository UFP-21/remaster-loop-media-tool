from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core.ffmpeg import probe_duration_seconds, run_cmd, run_cmd_stream, which_ffmpeg, which_ffprobe


SIZES = {
    "16:9 (1920×1080)": (1920, 1080),
    "1:1 (1080×1080)": (1080, 1080),
    "9:16 (1080×1920)": (1080, 1920),
}

MERGE_OUTPUT_SIZES = SIZES  # backward compat
AUDIO_SR = 48000
AUDIO_CHANNELS = 2
AUDIO_BITRATE = "192k"


def _ensure_ffmpeg() -> None:
    if not which_ffmpeg():
        raise RuntimeError("Не найден ffmpeg. Установи ffmpeg и добавь в PATH/FFMPEG_PATH.")



def _has_audio_stream(media_path: str) -> bool:
    ffprobe = which_ffprobe()
    if not ffprobe:
        return False

    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    r = run_cmd(cmd, check=False)
    out = (r.stdout or "").strip().lower()
    return r.returncode == 0 and "audio" in out



def _scale_crop_filter(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"



def _write_concat_list(paths: List[str]) -> str:
    lines = []
    for p in paths:
        pp = str(Path(p).resolve()).replace("\\", "/")
        pp = pp.replace("'", "''")
        lines.append(f"file '{pp}'")

    txt = "\n".join(lines) + "\n"
    fd, list_path = tempfile.mkstemp(prefix="ffconcat_", suffix=".txt")
    os.close(fd)
    Path(list_path).write_text(txt, encoding="utf-8")
    return list_path



def merge_video_clips_to_single_bg(
    input_video_paths: List[str],
    output_bg_path: str,
    fps: int = 30,
) -> str:
    """
    Склеивает несколько видео в единый фон, предварительно нормализуя FPS/формат.
    Здесь фон используется как визуальный слой, поэтому звук специально не сохраняем.
    """
    _ensure_ffmpeg()
    if not input_video_paths:
        raise RuntimeError("Не переданы входные видеофайлы.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="bg_norm_"))
    norm_paths: List[str] = []

    for i, p in enumerate(input_video_paths):
        outp = tmp_dir / f"clip_{i:03d}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            p,
            "-an",
            "-vf",
            f"fps={fps},format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            str(outp),
        ]
        run_cmd(cmd)
        norm_paths.append(str(outp))

    list_path = _write_concat_list(norm_paths)
    cmd2 = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        str(output_bg_path),
    ]
    run_cmd(cmd2)
    return str(output_bg_path)



def _ffmpeg_progress_parser(total_sec: float, progress_cb: Callable[[float, float], None]):
    total_sec = max(0.001, float(total_sec))
    out_time_sec = 0.0

    def on_line(line: str) -> None:
        nonlocal out_time_sec
        if line.startswith("out_time_ms="):
            try:
                ms = int(line.split("=", 1)[1].strip())
                out_time_sec = max(0.0, ms / 1_000_000.0)
                frac = min(1.0, out_time_sec / total_sec)
                progress_cb(frac, out_time_sec)
            except Exception:
                pass
        elif line.startswith("progress=") and "end" in line:
            progress_cb(1.0, total_sec)

    return on_line



def make_mp4_from_image(
    audio_path: str,
    image_path: str,
    output_path: str,
    size: Tuple[int, int],
    fps: int = 30,
    crf: int = 20,
    preset: str = "veryfast",
    *,
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> str:
    _ensure_ffmpeg()

    if not audio_path:
        raise RuntimeError("Для клипа с картинкой нужно выбрать аудио.")

    w, h = size
    vf = _scale_crop_filter(w, h)

    total = float(probe_duration_seconds(audio_path) or 0.0)
    if total <= 0:
        raise RuntimeError("Не удалось определить длительность аудио.")

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        image_path,
        "-i",
        audio_path,
        "-t",
        f"{total:.3f}",
        "-vf",
        vf,
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(int(crf)),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        "-movflags",
        "+faststart",
    ]

    if progress_cb:
        cmd += ["-progress", "pipe:1", "-nostats", output_path]
        run_cmd_stream(cmd, on_stdout_line=_ffmpeg_progress_parser(total, progress_cb))
    else:
        cmd += [output_path]
        run_cmd(cmd)

    return output_path



def make_mp4_from_looping_video_bg(
    audio_path: Optional[str],
    bg_video_path: str,
    output_path: str,
    size: Tuple[int, int],
    fps: int = 30,
    crf: int = 20,
    preset: str = "veryfast",
    *,
    keep_bg_audio: bool = False,
    bg_audio_volume: float = 1.0,
    music_volume: float = 0.25,
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> str:
    """
    Рендер клипа на основе видео-фона.

    Варианты:
      1) Только музыка (старое поведение): keep_bg_audio=False, audio_path задан.
      2) Только оригинальный звук видео: keep_bg_audio=True, audio_path=None.
      3) Голос/звук видео + музыкальный фон: keep_bg_audio=True, audio_path задан.

    Длительность:
      - если сохраняем оригинальный звук видео, итог идёт по длительности видео;
      - если используем только музыку, итог идёт по длительности музыки.
    """
    _ensure_ffmpeg()

    w, h = size
    vf = _scale_crop_filter(w, h)

    bg_dur = float(probe_duration_seconds(bg_video_path) or 0.0)
    if bg_dur <= 0:
        raise RuntimeError("Не удалось определить длительность фонового видео.")

    has_bg_audio = _has_audio_stream(bg_video_path)
    if keep_bg_audio and not has_bg_audio:
        raise RuntimeError("У выбранного фонового видео нет аудиодорожки. Сохранять оригинальный звук нечего.")

    music_dur = float(probe_duration_seconds(audio_path) or 0.0) if audio_path else 0.0
    if audio_path and music_dur <= 0:
        raise RuntimeError("Не удалось определить длительность музыкального файла.")

    if keep_bg_audio:
        total = bg_dur
    else:
        total = music_dur

    if total <= 0:
        raise RuntimeError("Не удалось определить длительность итогового клипа.")

    ffmpeg = which_ffmpeg()
    cmd: List[str] = [ffmpeg, "-y"]

    if (bg_dur + 0.05) < total:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", bg_video_path]

    music_input_idx: Optional[int] = None
    if audio_path:
        if (music_dur + 0.05) < total:
            cmd += ["-stream_loop", "-1"]
        music_input_idx = 1
        cmd += ["-i", audio_path]

    filter_parts: List[str] = []
    map_audio: str

    if keep_bg_audio and music_input_idx is not None:
        filter_parts.append(
            f"[0:a]volume={max(0.0, float(bg_audio_volume)):.3f},"
            f"aresample={AUDIO_SR},aformat=sample_fmts=fltp:channel_layouts=stereo[bgmix]"
        )
        filter_parts.append(
            f"[{music_input_idx}:a]volume={max(0.0, float(music_volume)):.3f},"
            f"aresample={AUDIO_SR},aformat=sample_fmts=fltp:channel_layouts=stereo[musmix]"
        )
        filter_parts.append("[bgmix][musmix]amix=inputs=2:duration=first:dropout_transition=2[aout]")
        map_audio = "[aout]"
    elif keep_bg_audio:
        filter_parts.append(
            f"[0:a]volume={max(0.0, float(bg_audio_volume)):.3f},"
            f"aresample={AUDIO_SR},aformat=sample_fmts=fltp:channel_layouts=stereo[aout]"
        )
        map_audio = "[aout]"
    elif music_input_idx is not None:
        filter_parts.append(
            f"[{music_input_idx}:a]volume={max(0.0, float(music_volume)):.3f},"
            f"aresample={AUDIO_SR},aformat=sample_fmts=fltp:channel_layouts=stereo[aout]"
        )
        map_audio = "[aout]"
    else:
        raise RuntimeError("Не выбрано ни аудио, ни оригинальный звук видео.")

    cmd += [
        "-t", f"{total:.3f}",
        "-vf", vf,
        "-r", str(fps),
    ]

    if filter_parts:
        cmd += ["-filter_complex", ";".join(filter_parts)]

    cmd += [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SR),
        "-ac", str(AUDIO_CHANNELS),
        "-map", "0:v:0",
        "-map", map_audio,
        "-shortest",
        "-movflags", "+faststart",
    ]

    if progress_cb:
        cmd += ["-progress", "pipe:1", "-nostats", output_path]
        run_cmd_stream(cmd, on_stdout_line=_ffmpeg_progress_parser(total, progress_cb))
    else:
        cmd += [output_path]
        run_cmd(cmd)

    return output_path



def make_mp4_from_color(
    audio_path: str,
    output_path: str,
    size: Tuple[int, int],
    fps: int = 30,
    crf: int = 20,
    color: str = "black",
    *,
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> str:
    ffmpeg = which_ffmpeg()
    w, h = size

    total = float(probe_duration_seconds(audio_path) or 0.0)
    if total <= 0:
        raise RuntimeError("Не удалось определить длительность аудио.")

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={w}x{h}:r={fps}",
        "-i",
        str(audio_path),
        "-t",
        f"{total:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        str(int(crf)),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        "-movflags",
        "+faststart",
    ]

    if progress_cb:
        cmd += ["-progress", "pipe:1", "-nostats", str(output_path)]
        run_cmd_stream(cmd, on_stdout_line=_ffmpeg_progress_parser(total, progress_cb))
    else:
        cmd += [str(output_path)]
        run_cmd(cmd)

    return str(output_path)



def make_final_mp4(
    *,
    audio_path: Optional[str],
    output_dir: str,
    size: Tuple[int, int],
    bg_image_path: Optional[str] = None,
    bg_video_path: Optional[str] = None,
    bg_video_list: Optional[List[str]] = None,
    fps: int = 30,
    crf: int = 20,
    preset: str = "veryfast",
    prefix: str = "VIDEO",
    tag: str = "custom",
    keep_bg_audio: bool = False,
    bg_audio_volume: float = 1.0,
    music_volume: float = 0.25,
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> str:
    _ensure_ffmpeg()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    final_bg_video = None
    if bg_video_list:
        final_bg_video = str(out_dir / "BG_MERGED.mp4")
        merge_video_clips_to_single_bg(bg_video_list, final_bg_video, fps=fps)
    elif bg_video_path:
        final_bg_video = bg_video_path

    w, h = size
    out_path = str(out_dir / f"{prefix}_{tag}_{w}x{h}.mp4")

    if final_bg_video:
        return make_mp4_from_looping_video_bg(
            audio_path=audio_path,
            bg_video_path=final_bg_video,
            output_path=out_path,
            size=(w, h),
            fps=fps,
            crf=crf,
            preset=preset,
            keep_bg_audio=keep_bg_audio,
            bg_audio_volume=bg_audio_volume,
            music_volume=music_volume,
            progress_cb=progress_cb,
        )

    if bg_image_path:
        if not audio_path:
            raise RuntimeError("Для клипа с картинкой нужно выбрать аудио.")
        return make_mp4_from_image(
            audio_path=audio_path,
            image_path=bg_image_path,
            output_path=out_path,
            size=(w, h),
            fps=fps,
            crf=crf,
            preset=preset,
            progress_cb=progress_cb,
        )

    if not audio_path:
        raise RuntimeError("Нужно выбрать аудио или видео с оригинальной дорожкой.")

    return make_mp4_from_color(
        audio_path=audio_path,
        output_path=out_path,
        size=(w, h),
        fps=fps,
        crf=crf,
        color="black",
        progress_cb=progress_cb,
    )



def make_three_final_mp4(
    *,
    audio_path: Optional[str],
    output_dir: str,
    bg_image_path: Optional[str] = None,
    bg_video_path: Optional[str] = None,
    bg_video_list: Optional[List[str]] = None,
    fps: int = 30,
    crf: int = 20,
    preset: str = "veryfast",
    prefix: str = "VIDEO",
    keep_bg_audio: bool = False,
    bg_audio_volume: float = 1.0,
    music_volume: float = 0.25,
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> List[str]:
    outs: List[str] = []
    for label, size in SIZES.items():
        tag = "16x9" if "16:9" in label else ("1x1" if "1:1" in label else "9x16")
        outp = make_final_mp4(
            audio_path=audio_path,
            output_dir=output_dir,
            size=size,
            bg_image_path=bg_image_path,
            bg_video_path=bg_video_path,
            bg_video_list=bg_video_list,
            fps=fps,
            crf=crf,
            preset=preset,
            prefix=prefix,
            tag=tag,
            keep_bg_audio=keep_bg_audio,
            bg_audio_volume=bg_audio_volume,
            music_volume=music_volume,
            progress_cb=progress_cb,
        )
        outs.append(outp)
    return outs

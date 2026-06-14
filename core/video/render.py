from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core.ffmpeg import probe_duration_seconds, run_cmd, run_cmd_stream, which_ffmpeg


SIZES = {
    "16:9 (1920×1080)": (1920, 1080),
    "1:1 (1080×1080)": (1080, 1080),
    "9:16 (1080×1920)": (1080, 1920),
}

MERGE_OUTPUT_SIZES = SIZES  # backward compat


def _ensure_ffmpeg() -> None:
    if not which_ffmpeg():
        raise RuntimeError("Не найден ffmpeg. Установи ffmpeg и добавь в PATH/FFMPEG_PATH.")


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
    """
    ffmpeg -progress pipe:1 печатает:
      out_time_ms=...
      progress=continue/end
    Мы превращаем это в (frac, out_sec).
    """
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
        "192k",
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
    audio_path: str,
    bg_video_path: str,
    output_path: str,
    size: Tuple[int, int],
    fps: int = 30,
    crf: int = 20,
    preset: str = "veryfast",
    *,
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> str:
    _ensure_ffmpeg()

    w, h = size
    vf = _scale_crop_filter(w, h)

    audio_dur = float(probe_duration_seconds(audio_path) or 0.0)
    if audio_dur <= 0:
        raise RuntimeError("Не удалось определить длительность аудио.")

    bg_dur = float(probe_duration_seconds(bg_video_path) or 0.0)

    cmd = ["ffmpeg", "-y"]

    # ✅ зацикливаем фон только если он короче аудио
    if bg_dur > 0 and (bg_dur + 0.05) < audio_dur:
        cmd += ["-stream_loop", "-1"]

    cmd += [
        "-i",
        bg_video_path,
        "-i",
        audio_path,
        "-t",
        f"{audio_dur:.3f}",
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
        "192k",
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
        run_cmd_stream(cmd, on_stdout_line=_ffmpeg_progress_parser(audio_dur, progress_cb))
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
        "192k",
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
    audio_path: str,
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
    progress_cb: Optional[Callable[[float, float], None]] = None,
) -> str:
    """
    ✅ Рендер одного MP4 под один размер (самое быстрое).
    """
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
            progress_cb=progress_cb,
        )

    if bg_image_path:
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
    audio_path: str,
    output_dir: str,
    *,
    bg_image_path: Optional[str] = None,
    bg_video_path: Optional[str] = None,
    bg_video_list: Optional[List[str]] = None,
    fps: int = 30,
    crf: int = 20,
    preset: str = "veryfast",  # ✅ ВАЖНО: чтобы preset=preset ниже был определён
    prefix: str = "VIDEO",
) -> List[str]:
    """
    Совместимость: рендер 3 размеров.
    """
    _ensure_ffmpeg()
    outs: List[str] = []

    for label, (w, h) in SIZES.items():
        tag = "16x9" if "16:9" in label else ("1x1" if "1:1" in label else "9x16")

        outp = make_final_mp4(
            audio_path=audio_path,
            output_dir=output_dir,
            size=(w, h),
            bg_image_path=bg_image_path,
            bg_video_path=bg_video_path,
            bg_video_list=bg_video_list,
            fps=fps,
            crf=crf,
            preset=preset,
            prefix=prefix,
            tag=tag,
            progress_cb=None,  # прогресс на 3 размерах показываем снаружи (UI)
        )
        outs.append(outp)

    return outs


def render_three_sizes_from_audio(
    *,
    audio_path: str,
    out_dir: str,
    bg_path: str | None,
    bg_kind: str = "image",
    crf: int = 20,
    basename: str | None = None,
) -> list[str]:
    """
    Старый API-адаптер (встречается в старом UI).
    """
    if basename is None:
        stem = Path(audio_path).stem
        basename = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", stem)[:80].strip("._") or "output"

    bg_image_path: Optional[str] = None
    bg_video_path: Optional[str] = None

    if bg_kind == "image":
        bg_image_path = bg_path
    elif bg_kind == "video":
        bg_video_path = bg_path

    return make_three_final_mp4(
        audio_path=audio_path,
        output_dir=out_dir,
        bg_image_path=bg_image_path,
        bg_video_path=bg_video_path,
        fps=30,
        crf=crf,
        preset="veryfast",
        prefix=basename,
    )
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

from core.ffmpeg import probe_duration_seconds, run_cmd, which_ffmpeg, which_ffprobe


AUDIO_SR = 48000
AUDIO_CHANNELS = 2
AUDIO_BITRATE = "192k"


def _as_clip(x: Any) -> Dict[str, Any]:
    """
    Принимаем:
      - str -> {"path": str, "trim_start": 0.0, "trim_end": 0.0}
      - dict -> берём path, trim_start, trim_end

    trim_start/trim_end — секунды, которые нужно ОТРЕЗАТЬ от начала/конца.
    """
    if isinstance(x, dict):
        p = str(x.get("path", "")).strip()
        ts = float(x.get("trim_start", 0.0) or 0.0)
        te = float(x.get("trim_end", 0.0) or 0.0)
        return {"path": p, "trim_start": max(0.0, ts), "trim_end": max(0.0, te)}
    return {"path": str(x).strip(), "trim_start": 0.0, "trim_end": 0.0}



def _probe_video_wh(media_path: str) -> Tuple[int, int]:
    ffprobe = which_ffprobe()
    if not ffprobe:
        raise RuntimeError("ffprobe не найден. Установи FFmpeg и добавь в PATH/FFPROBE_PATH.")

    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(Path(media_path)),
    ]
    proc = run_cmd(cmd)
    out = (proc.stdout or "").strip()
    if "x" not in out:
        raise RuntimeError(f"Не удалось получить width/height через ffprobe: '{out}'")
    w_str, h_str = out.split("x", 1)
    return int(w_str), int(h_str)



def _has_audio_stream(media_path: str) -> bool:
    ffprobe = which_ffprobe()
    if not ffprobe:
        raise RuntimeError("ffprobe не найден. Установи FFmpeg и добавь в PATH/FFPROBE_PATH.")

    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(Path(media_path)),
    ]
    proc = run_cmd(cmd, check=False)
    out = (proc.stdout or "").strip().lower()
    return proc.returncode == 0 and "audio" in out



def _trim_and_normalize_video(
    *,
    src_path: str,
    dst_path: str,
    start_sec: float,
    end_sec: float,
    target_w: int,
    target_h: int,
    fps: int,
    crf: int = 20,
    preset: str = "veryfast",
) -> str:
    """
    Обрезаем и нормализуем клип так, чтобы он был пригоден к стабильной склейке.

    Что приводим к общему виду:
      - fps
      - размер (scale+pad)
      - SAR / pixel format
      - аудио: всегда stereo AAC 48k

    Если у исходника нет аудио, добавляем тихую пустую дорожку.
    Это важно: так все клипы становятся одинаковыми по структуре,
    а итоговая склейка не теряет звук.
    """
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH/FFMPEG_PATH.")

    clip_len = max(0.001, float(end_sec) - float(start_sec))
    has_audio = _has_audio_stream(src_path)

    vf = (
        f"fps={int(fps)},"
        f"scale={int(target_w)}:{int(target_h)}:force_original_aspect_ratio=decrease,"
        f"pad={int(target_w)}:{int(target_h)}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,"
        f"format=yuv420p"
    )

    cmd: List[str] = [
        ffmpeg,
        "-y",
        "-i", str(Path(src_path)),
        "-ss", f"{max(0.0, float(start_sec)):.3f}",
        "-to", f"{max(0.0, float(end_sec)):.3f}",
    ]

    if not has_audio:
        cmd += [
            "-f", "lavfi",
            "-t", f"{clip_len:.3f}",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SR}",
        ]

    cmd += [
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
    ]

    if has_audio:
        cmd += [
            "-af", f"aresample={AUDIO_SR},aformat=sample_fmts=fltp:channel_layouts=stereo",
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-ar", str(AUDIO_SR),
            "-ac", str(AUDIO_CHANNELS),
            "-map", "0:v:0",
            "-map", "0:a:0",
        ]
    else:
        cmd += [
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-ar", str(AUDIO_SR),
            "-ac", str(AUDIO_CHANNELS),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
        ]

    cmd += [
        "-movflags", "+faststart",
        str(Path(dst_path)),
    ]

    run_cmd(cmd)
    return str(dst_path)



def _run_plain_concat(norm_paths: List[str], out: str, ffmpeg: str) -> str:
    """
    Стабильная склейка без визуальных переходов, но С сохранением аудио.
    """
    concat_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(norm_paths)))
    filter_complex = f"{concat_inputs}concat=n={len(norm_paths)}:v=1:a=1[vout][aout]"

    cmd: List[str] = [ffmpeg, "-y"]
    for p in norm_paths:
        cmd += ["-i", str(Path(p))]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SR),
        "-ac", str(AUDIO_CHANNELS),
        "-movflags", "+faststart",
        out,
    ]
    run_cmd(cmd)
    return out



def concat_videos(
    input_paths: List[Any],
    output_path: str,
    *,
    transition: Optional[str] = None,
    transition_sec: float = 0.0,
    fps: int = 30,
) -> str:
    """
    Склейка видео с поддержкой trim и сохранением аудио.

    input_paths может быть:
      - List[str]
      - List[dict] где dict содержит:
          {"path": "...", "trim_start": 0.5, "trim_end": 1.2}

    trim_start/trim_end — секунды, которые отрезаем от начала/конца клипа.

    Реализация:
      1) Для каждого клипа делаем tmp "trim+normalize"
         (H.264 + AAC, одинаковые размер/fps/audio params)
      2) Далее:
         - без переходов: concat filter по видео + аудио
         - с переходами: пробуем xfade + acrossfade
           если ffmpeg отклонит xfade на конкретных исходниках,
           автоматически откатываемся к обычной склейке без потери звука.

    Главный фикс текущей проблемы:
      - раньше tmp-клипы создавались с -an, и звук пропадал ещё до склейки;
      - теперь звук сохраняется, а если аудио нет — подставляется тишина.
    """
    clips: List[Dict[str, Any]] = []
    for x in input_paths or []:
        c = _as_clip(x)
        if c["path"]:
            clips.append(c)

    if not clips:
        raise ValueError("Нет входных видеофайлов")

    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH (или FFMPEG_PATH).")

    out = str(Path(output_path))
    out_p = Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    target_w, target_h = _probe_video_wh(clips[0]["path"])

    tmp_dir = out_p.parent / "_tmp_trim"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]

    norm_paths: List[str] = []
    durs: List[float] = []

    for i, c in enumerate(clips):
        src = c["path"]
        dur = float(probe_duration_seconds(src) or 0.0)
        if dur <= 0:
            raise RuntimeError(f"Не удалось определить длительность клипа: {Path(src).name}")

        trim_start = float(c.get("trim_start", 0.0) or 0.0)
        trim_end = float(c.get("trim_end", 0.0) or 0.0)

        start = max(0.0, trim_start)
        end = max(0.0, dur - trim_end)

        if end - start < 0.2:
            raise RuntimeError(
                f"Клип после обрезки слишком короткий (<0.2с): {Path(src).name}\n"
                f"Длительность={dur:.2f}s, start={start:.2f}s, end={end:.2f}s"
            )

        dst = tmp_dir / f"trim_{run_id}_{i:03d}.mp4"
        _trim_and_normalize_video(
            src_path=src,
            dst_path=str(dst),
            start_sec=start,
            end_sec=end,
            target_w=target_w,
            target_h=target_h,
            fps=fps,
            crf=20,
            preset="veryfast",
        )

        norm_paths.append(str(dst))

        nd = float(probe_duration_seconds(str(dst)) or 0.0)
        if nd <= 0:
            raise RuntimeError(f"Не удалось определить длительность временного клипа: {dst.name}")
        durs.append(nd)

    if len(norm_paths) == 1:
        cmd = [ffmpeg, "-y", "-i", norm_paths[0], "-c", "copy", out]
        run_cmd(cmd)
        return out

    use_transitions = bool(transition) and float(transition_sec) > 0.0 and len(norm_paths) > 1
    if not use_transitions:
        return _run_plain_concat(norm_paths, out, ffmpeg)

    min_d = min(durs)
    if transition_sec >= min_d:
        transition_sec = max(0.2, min_d * 0.3)
    if transition_sec <= 0:
        transition_sec = 0.5

    offsets: List[float] = []
    acc = durs[0]
    for i in range(1, len(durs)):
        offsets.append(max(0.0, acc - transition_sec))
        acc += durs[i] - transition_sec

    cmd: List[str] = [ffmpeg, "-y"]
    for p in norm_paths:
        cmd += ["-i", str(Path(p))]

    norm_parts: List[str] = []
    for i in range(len(norm_paths)):
        norm_parts.append(f"[{i}:v:0]fps={int(fps)},setpts=PTS-STARTPTS,format=rgba[nv{i}]")
        norm_parts.append(f"[{i}:a:0]asetpts=PTS-STARTPTS[a{i}]")

    xfade_parts: List[str] = []
    last_v = "[nv0]"
    for i in range(1, len(norm_paths)):
        v_out = f"[v{i}]"
        off = offsets[i - 1]
        xfade_parts.append(
            f"{last_v}[nv{i}]"
            f"xfade=transition={transition}:duration={transition_sec}:offset={off}"
            f"{v_out}"
        )
        last_v = v_out

    across_parts: List[str] = []
    last_a = "[a0]"
    for i in range(1, len(norm_paths)):
        a_out = f"[ax{i}]"
        across_parts.append(
            f"{last_a}[a{i}]"
            f"acrossfade=d={transition_sec}:c1=tri:c2=tri"
            f"{a_out}"
        )
        last_a = a_out

    finalize = [f"{last_v}format=yuv420p[vout]"]
    if last_a != "[aout]":
        finalize.append(f"{last_a}anull[aout]")

    filter_complex = "; ".join(norm_parts + xfade_parts + across_parts + finalize)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SR),
        "-ac", str(AUDIO_CHANNELS),
        "-movflags", "+faststart",
        out,
    ]

    try:
        run_cmd(cmd)
        return out
    except Exception:
        # Безопасный откат: пусть видео соберётся без визуального перехода,
        # но пользователь не потеряет звук и не упрётся в ошибку ffmpeg.
        return _run_plain_concat(norm_paths, out, ffmpeg)

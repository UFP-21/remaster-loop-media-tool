from __future__ import annotations

from typing import List, Optional

from pathlib import Path
import uuid

from core.ffmpeg import run_cmd, which_ffmpeg


def _fmt_sec(x: float) -> str:
    return f"{float(x):.6f}".rstrip("0").rstrip(".")


def concat_tracks_with_crossfade(
    input_paths: List[str],
    output_path: str,
    crossfade_sec: float = 1.5,
    sample_rate: int = 48000,
    mp3_bitrate: Optional[str] = None,
    apply_loudnorm: bool = False,
    normalize_each: bool = False,
    target_i_lufs: int = -14,
    true_peak_db: float = -1.0,
    lra: int = 11,
    protect_peaks: bool = False,
    xfade_mode: str = "soft",  # "soft" | "no_dip"
) -> None:
    """
    Последовательная склейка с кроссфейдом.

    xfade_mode:
      - "soft"   : tri/tri
      - "no_dip" : qsin/qsin (equal-power, но это ВСЁ РАВНО fade)
    """
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH/FFMPEG_PATH.")

    if not input_paths:
        raise RuntimeError("Не переданы входные файлы.")
    if crossfade_sec < 0:
        raise RuntimeError("crossfade_sec не может быть отрицательным.")

    mode = str(xfade_mode).strip().lower()
    if mode == "no_dip":
        c1, c2 = "qsin", "qsin"
    else:
        c1, c2 = "tri", "tri"

    prep_chains: List[str] = []
    for i in range(len(input_paths)):
        chain = (
            f"[{i}:a]"
            f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo,"
            f"aresample={sample_rate},"
            f"asetpts=PTS-STARTPTS"
        )
        if normalize_each:
            chain += f",loudnorm=I={int(target_i_lufs)}:TP={float(true_peak_db)}:LRA={int(lra)}:linear=true"
        prep_chains.append(chain + f"[a{i}]")

    if len(input_paths) == 1:
        mix_chain = "[a0]anull[out]"
    elif crossfade_sec == 0:
        inputs_concat = "".join([f"[a{i}]" for i in range(len(input_paths))])
        mix_chain = f"{inputs_concat}concat=n={len(input_paths)}:v=0:a=1[out]"
    else:
        cf = float(crossfade_sec)
        chains = [f"[a0][a1]acrossfade=d={cf}:c1={c1}:c2={c2}[x1]"]
        last = "x1"
        for i in range(2, len(input_paths)):
            nxt = f"x{i}"
            chains.append(f"[{last}][a{i}]acrossfade=d={cf}:c1={c1}:c2={c2}[{nxt}]")
            last = nxt
        mix_chain = ";".join(chains) + f";[{last}]anull[out]"

    post_filters: List[str] = []
    if apply_loudnorm:
        post_filters.append(f"loudnorm=I=-14:TP={float(true_peak_db)}:LRA={int(lra)}:linear=true")
    if protect_peaks:
        post_filters.append("alimiter=limit=0.98")

    if post_filters:
        post_chain = "[out]" + ",".join(post_filters) + "[out_final]"
        final_map = "[out_final]"
        filter_complex = ";".join(prep_chains) + ";" + mix_chain + ";" + post_chain
    else:
        final_map = "[out]"
        filter_complex = ";".join(prep_chains) + ";" + mix_chain

    args = [ffmpeg, "-y"]
    for p in input_paths:
        args += ["-i", p]

    args += [
        "-filter_complex", filter_complex,
        "-map", final_map,
        "-ac", "2",
        "-ar", str(sample_rate),
    ]

    if mp3_bitrate:
        args += ["-c:a", "libmp3lame", "-b:a", mp3_bitrate]
    else:
        args += ["-c:a", "pcm_s16le"]

    args += [output_path]
    run_cmd(args, check=True)


def concat_audio_timeline_wav(
    input_paths: List[str],
    output_path: str,
    offsets_sec: List[float],
    *,
    mode: str,  # "mix" | "replace"
    sample_rate: int = 48000,
    normalize_each: bool = False,
    target_i_lufs: int = -14,
    true_peak_db: float = -1.0,
    lra: int = 11,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH/FFMPEG_PATH.")

    if not input_paths:
        raise RuntimeError("Не переданы входные файлы.")

    if len(offsets_sec) != len(input_paths):
        raise RuntimeError("offsets_sec должен быть той же длины, что и input_paths.")

    m = str(mode).strip().lower()
    if m not in ("mix", "replace"):
        raise RuntimeError("mode должен быть 'mix' или 'replace'.")

    prep_chains: List[str] = []
    for i in range(len(input_paths)):
        chain = (
            f"[{i}:a]"
            f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo,"
            f"aresample={sample_rate},"
            f"asetpts=PTS-STARTPTS"
        )
        if normalize_each:
            chain += f",loudnorm=I={int(target_i_lufs)}:TP={float(true_peak_db)}:LRA={int(lra)}:linear=true"
        prep_chains.append(chain + f"[a{i}]")

    extra: List[str] = []

    if m == "mix":
        delayed_labels: List[str] = []
        for i, off in enumerate(offsets_sec):
            off = max(0.0, float(off))
            ms = int(round(off * 1000.0))
            out = f"d{i}"
            extra.append(f"[a{i}]adelay={ms}|{ms}[{out}]")
            delayed_labels.append(f"[{out}]")

        mix_in = "".join(delayed_labels)
        extra.append(f"{mix_in}amix=inputs={len(delayed_labels)}:duration=longest:dropout_transition=0[out]")

    else:
        extra.append("[a0]anull[c0]")
        for i in range(1, len(input_paths)):
            off = max(0.0, float(offsets_sec[i]))
            prev = f"c{i-1}"
            head = f"h{i}"
            cur = f"c{i}"
            extra.append(f"[{prev}]atrim=end={_fmt_sec(off)},asetpts=PTS-STARTPTS[{head}]")
            extra.append(f"[{head}][a{i}]concat=n=2:v=0:a=1[{cur}]")
        extra.append(f"[c{len(input_paths)-1}]anull[out]")

    filter_complex = ";".join(prep_chains + extra)

    args = [ffmpeg, "-y"]
    for p in input_paths:
        args += ["-i", p]

    args += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ac", "2",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        output_path,
    ]

    run_cmd(args, check=True)


def _chunked(lst: List[str], n: int) -> List[List[str]]:
    n = max(1, int(n))
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def concat_audio_files_wav(
    input_paths: List[str],
    output_path: str,
    crossfade_sec: float = 1.5,
    sample_rate: int = 48000,
    apply_loudnorm: bool = False,
    normalize_each: bool = False,
    target_i_lufs: int = -14,
    xfade_mode: str = "soft",
    *,
    batched: bool = False,
    batch_size: int = 12,
    tmp_dir: Optional[str] = None,
) -> None:
    """
    Wrapper для UI.

    batched=True:
      - если треков много, склеиваем "пачками" по batch_size (например 12),
        затем склеиваем результаты пачек в финал.
      - Это сильно уменьшает размер filter_complex и убирает падения на больших списках.

    ВАЖНО про loudnorm:
      - если normalize_each=True, то loudnorm применяется в ПЕРВОМ проходе (внутри пачек),
        а при финальной склейке пачек normalize_each=False (чтобы не применить loudnorm второй раз).
    """
    if not batched or len(input_paths) <= max(2, int(batch_size)):
        # обычный режим
        concat_tracks_with_crossfade(
            input_paths=input_paths,
            output_path=output_path,
            crossfade_sec=crossfade_sec,
            sample_rate=sample_rate,
            mp3_bitrate=None,
            apply_loudnorm=apply_loudnorm,
            normalize_each=normalize_each,
            target_i_lufs=target_i_lufs,
            true_peak_db=-1.0,
            lra=11,
            protect_peaks=False,
            xfade_mode=xfade_mode,
        )
        return

    # ── батч режим ───────────────────────────────
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if tmp_dir:
        tmp_root = Path(tmp_dir)
    else:
        tmp_root = out_p.parent / "_tmp_batches"

    tmp_root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]

    chunks = _chunked(list(input_paths), int(batch_size))
    batch_outputs: List[str] = []

    # 1) склеиваем внутри пачек (с loudnorm на каждом треке — если включено)
    for bi, chunk in enumerate(chunks, start=1):
        tmp_out = tmp_root / f"batch_{run_id}_{bi:02d}.wav"
        concat_tracks_with_crossfade(
            input_paths=chunk,
            output_path=str(tmp_out),
            crossfade_sec=crossfade_sec,
            sample_rate=sample_rate,
            mp3_bitrate=None,
            apply_loudnorm=False,                 # пост-луднорм не делаем
            normalize_each=normalize_each,        # loudnorm на каждом входе (по желанию)
            target_i_lufs=target_i_lufs,
            true_peak_db=-1.0,
            lra=11,
            protect_peaks=False,
            xfade_mode=xfade_mode,
        )
        batch_outputs.append(str(tmp_out))

    # 2) склеиваем пачки в финал
    #    ВАЖНО: normalize_each=False, чтобы не делать loudnorm второй раз.
    concat_tracks_with_crossfade(
        input_paths=batch_outputs,
        output_path=str(out_p),
        crossfade_sec=crossfade_sec,
        sample_rate=sample_rate,
        mp3_bitrate=None,
        apply_loudnorm=apply_loudnorm,   # если хочешь — можно применить ОДИН раз на финале
        normalize_each=False,            # НЕ повторяем loudnorm
        target_i_lufs=target_i_lufs,
        true_peak_db=-1.0,
        lra=11,
        protect_peaks=False,
        xfade_mode=xfade_mode,
    )
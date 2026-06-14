from __future__ import annotations

from pathlib import Path
import uuid

import streamlit as st

from core.audio_loop import render_loop_to_duration, render_preview
from core.ffmpeg import (
    probe_duration_seconds,
    which_ffmpeg,
    run_cmd,
    normalize_audio_to_wav,
    encode_mp3,
    encode_flac,
    convert_audio_to_wav_16_44100,
    file_size_bytes,
    human_file_size,
    is_over_distrokid_limit,
)
from core.paths import safe_filename, build_output_name, ensure_unique_path, pretty_filename_from_path
from ui.state import AppState

PENDING_NAV_KEY = "__pending_nav_choice"
NAV_CLIP_LABEL = "🎬 Создать видеоклип"


def _sec_from_hms(h: int, m: int, s: int) -> float:
    return float(h) * 3600.0 + float(m) * 60.0 + float(s)


def _fmt_hms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _apply_fade_out(in_wav: str, out_wav: str, fade_out_sec: float) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH.")

    dur = probe_duration_seconds(in_wav) or 0.0
    if dur <= 0.5:
        raise RuntimeError("Файл слишком короткий для fade-out.")

    fo = max(0.0, float(fade_out_sec))
    if fo <= 0:
        Path(out_wav).write_bytes(Path(in_wav).read_bytes())
        return

    if fo >= dur:
        fo = max(0.1, dur * 0.2)

    st_time = max(0.0, dur - fo)

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        in_wav,
        "-af",
        f"afade=t=out:st={st_time:.3f}:d={fo:.3f}",
        "-c:a",
        "pcm_s16le",
        out_wav,
    ]
    run_cmd(cmd, check=True)


def _probe_video_duration_seconds(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    proc = run_cmd(cmd, check=True)
    out = (proc.stdout or "").strip()
    try:
        return float(out)
    except Exception:
        return 0.0


def _repeat_n_times_with_crossfade(
    input_audio_path: str,
    output_wav_path: str,
    *,
    loops: int,
    crossfade_sec: float,
    sample_rate: int = 48000,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Установи FFmpeg и добавь в PATH.")

    loops = int(loops)
    if loops <= 0:
        raise RuntimeError("loops должен быть >= 1")

    if loops == 1 or crossfade_sec <= 0:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            input_audio_path,
            "-ac",
            "2",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            output_wav_path,
        ]
        run_cmd(cmd, check=True)
        return

    if loops > 80:
        raise RuntimeError("Слишком много повторов (loops > 80). Для очень длинных треков используй режим по времени.")

    cmd = [ffmpeg, "-y"]
    for _ in range(loops):
        cmd += ["-i", input_audio_path]

    prep = []
    for i in range(loops):
        prep.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo,"
            f"aresample={sample_rate},asetpts=PTS-STARTPTS[a{i}]"
        )

    cf = float(crossfade_sec)
    chains = [f"[a0][a1]acrossfade=d={cf}:c1=tri:c2=tri[x1]"]
    last = "x1"
    for i in range(2, loops):
        nxt = f"x{i}"
        chains.append(f"[{last}][a{i}]acrossfade=d={cf}:c1=tri:c2=tri[{nxt}]")
        last = nxt

    filter_complex = ";".join(prep + chains)

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{last}]",
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        output_wav_path,
    ]
    run_cmd(cmd, check=True)


def _push_to_video_page_state(state: AppState, *, audio_path: str, video_path: str | None) -> None:
    """Кладём подготовленные файлы в 'Создать видеоклип' (state.video.*)."""
    if audio_path and Path(audio_path).exists():
        # state.video.audio_items канонично list[dict], но проект уже переживал list[str]
        try:
            existing = []
            for x in state.video.audio_items:
                if isinstance(x, dict):
                    existing.append(str(x.get("path", "")))
                else:
                    existing.append(str(x))
            if audio_path not in existing:
                if isinstance(state.video.audio_items, list) and state.video.audio_items and isinstance(state.video.audio_items[0], dict):
                    state.video.audio_items.insert(0, {"path": audio_path, "name": Path(audio_path).name, "kind": "audio"})
                else:
                    state.video.audio_items.insert(0, audio_path)
        except Exception:
            pass

    if video_path and Path(video_path).exists():
        try:
            existing = []
            for x in state.video.bg_video_items:
                if isinstance(x, dict):
                    existing.append(str(x.get("path", "")))
                else:
                    existing.append(str(x))
            if video_path not in existing:
                if isinstance(state.video.bg_video_items, list) and state.video.bg_video_items and isinstance(state.video.bg_video_items[0], dict):
                    state.video.bg_video_items.insert(0, {"path": video_path, "name": Path(video_path).name, "kind": "video"})
                else:
                    state.video.bg_video_items.insert(0, video_path)
        except Exception:
            pass


def _sig_of_uploaded(f) -> str:
    size = getattr(f, "size", None)
    if size is None:
        try:
            size = len(f.getbuffer())
        except Exception:
            size = 0
    return f"{f.name}::{int(size)}"


def _render_distrokid_tools(
    *,
    source_path: str,
    out_dir: Path,
    base_name: str,
    tag_prefix: str,
    key_prefix: str,
) -> None:
    src = Path(str(source_path))
    if not src.exists():
        return

    size_b = file_size_bytes(str(src))
    st.markdown("### 📦 Подготовка для DistroKid")
    st.caption(f"Текущий файл: {src.name}")
    st.write(f"Размер файла: **{human_file_size(size_b)}**")

    if size_b is not None:
        if is_over_distrokid_limit(str(src)):
            st.error("⚠️ Файл больше 1 GB — лучше сделать FLAC или WAV 16-bit / 44.1 kHz.")
        elif size_b >= int(0.9 * 1024 * 1024 * 1024):
            st.warning("Файл близко к лимиту 1 GB. Лучше заранее подготовить облегчённую версию.")
        else:
            st.success("Размер файла укладывается в лимит 1 GB.")

    flac_out = ensure_unique_path(out_dir / build_output_name(base_name=base_name, tag=f"{tag_prefix} (FLAC)", ext=".flac"))
    wav44_out = ensure_unique_path(out_dir / build_output_name(base_name=base_name, tag=f"{tag_prefix} (44.1k 16bit)", ext=".wav"))

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🗜️ Экспорт FLAC (lossless)", key=f"{key_prefix}_to_flac"):
            encode_flac(input_path=str(src), output_path=str(flac_out), sample_rate=44100, compression_level=8)
            st.success(f"✅ FLAC готов: {flac_out.name}")
            st.caption(f"Размер: {human_file_size(file_size_bytes(str(flac_out)))}")
    with c2:
        if st.button("🎚️ Экспорт WAV 16-bit / 44.1 kHz", key=f"{key_prefix}_to_wav441"):
            convert_audio_to_wav_16_44100(input_path=str(src), output_path=str(wav44_out))
            st.success(f"✅ WAV 16/44.1 готов: {wav44_out.name}")
            st.caption(f"Размер: {human_file_size(file_size_bytes(str(wav44_out)))}")


def page_loop(state: AppState) -> None:
    st.subheader("🔁 Зацикливание")
    st.caption("Режимы: по повторам, точная длительность, или умная подгонка под видео.")

    session_dir = Path(state.paths.work_dir) / "loop"
    session_dir.mkdir(parents=True, exist_ok=True)

    SS_AUDIO = "loop__saved_audio_path"
    SS_VIDEO = "loop__saved_video_path"
    PROC_AUDIO = "loop__processed_audio"
    PROC_VIDEO = "loop__processed_video"

    if PROC_AUDIO not in st.session_state:
        st.session_state[PROC_AUDIO] = set()
    if PROC_VIDEO not in st.session_state:
        st.session_state[PROC_VIDEO] = set()

    # ─────────────────────────────────────────────
    # Очистка (входные / выходные)
    # ─────────────────────────────────────────────
    c0, c1, c2 = st.columns([1, 1, 2])
    with c0:
        if st.button("🧹 Очистить входные", key="loop_clear_inputs"):
            st.session_state.pop(SS_AUDIO, None)
            st.session_state.pop(SS_VIDEO, None)
            st.session_state[PROC_AUDIO] = set()
            st.session_state[PROC_VIDEO] = set()
            st.session_state.pop("loop_last_out_wav", None)
            st.session_state.pop("loop_last_run_dir", None)
            st.rerun()
    with c1:
        if st.button("🗑️ Очистить выходные", key="loop_clear_outputs"):
            try:
                for p in session_dir.glob("run_*/*"):
                    if p.is_file():
                        p.unlink()
            except Exception:
                pass
            st.session_state.pop("loop_last_out_wav", None)
            st.session_state.pop("loop_last_run_dir", None)
            st.success("Выходные (loop) удалены.")
            st.rerun()
    with c2:
        st.caption("Очистка входных удаляет ссылки в session_state (файлы в uploads_* остаются). Очистка выходных удаляет файлы loop/run_*/...")

    st.divider()

    # ─────────────────────────────────────────────
    # Upload audio (дедуп на rerun)
    # ─────────────────────────────────────────────
    up = st.file_uploader(
        "Загрузите аудиофайл (MP3/WAV/FLAC/OGG/M4A)",
        type=["mp3", "wav", "flac", "ogg", "m4a"],
        accept_multiple_files=False,
        key="loop_uploader",
    )

    if up:
        sig = _sig_of_uploaded(up)
        if sig not in st.session_state[PROC_AUDIO]:
            st.session_state[PROC_AUDIO].add(sig)
            dst = Path(state.paths.uploads_audio) / f"{uuid.uuid4().hex[:8]}__{safe_filename(up.name)}"
            dst.write_bytes(up.getbuffer())
            st.session_state[SS_AUDIO] = str(dst)

    in_path = st.session_state.get(SS_AUDIO)
    if not in_path or not Path(in_path).exists():
        st.info("Перетащите 1 аудиофайл. Для склейки нескольких треков используйте вкладку 🧩 Склейка треков.")
        return

    st.markdown("### Входной файл")
    st.write(f"Файл: **{pretty_filename_from_path(in_path)}**")
    st.audio(str(in_path))

    src_dur = probe_duration_seconds(str(in_path)) or 0.0
    if src_dur > 0:
        st.caption(f"Длительность исходника: **{_fmt_hms(src_dur)}**")

    st.divider()

    mode = st.radio(
        "Режим зацикливания",
        options=[
            "1) По количеству повторов (без куска начала в конце)",
            "2) Точная длительность (под видео) + плавное затухание в конце",
            "3) Умный режим: подогнать под видео (авто-длительность)",
        ],
        index=0,
        horizontal=False,
        key="loop_mode",
    )

    colA, colB = st.columns([1, 1])
    with colA:
        crossfade = st.slider(
            "Crossfade между повторами (сек)",
            min_value=0.0,
            max_value=12.0,
            value=4.0,
            step=0.1,
            key="loop_crossfade",
            help="В режиме 1 кроссфейд применяется ТОЛЬКО между повторами. В конце трека кроссфейда нет.",
        )
    with colB:
        fmt = st.radio(
            "Формат экспорта",
            ["WAV (48kHz)", "MP3 (320 kbps)"],
            index=0,
            horizontal=True,
            key="loop_fmt",
        )

    target_seconds: float | None = None
    fade_out_sec = 0.0
    smart_video_path: str | None = None

    if mode.startswith("1)"):
        st.markdown("### Настройки (по повторам)")
        loops_count = st.number_input(
            "Сколько повторов сделать (loops)",
            min_value=1,
            max_value=80,
            value=2,
            step=1,
            key="loop_loops_count",
        )

        fade_out_sec = st.slider(
            "Плавное затухание в конце (сек)",
            min_value=0.0,
            max_value=20.0,
            value=0.0,
            step=0.5,
            key="loop_fade_out_mode1",
        )

        if src_dur > 0:
            approx = float(src_dur) * float(loops_count) - float(crossfade) * (int(loops_count) - 1)
            approx = max(0.0, approx)
            target_seconds = approx
            st.info(f"Ожидаемая длительность (примерно): **{_fmt_hms(approx)}** (≈ {approx:.1f} сек)")
        else:
            st.error("Не удалось определить длительность исходника.")
            st.stop()

    elif mode.startswith("2)"):
        st.markdown("### Настройки (точная длительность)")
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            h = st.number_input("Часы", min_value=0, max_value=24, value=0, step=1, key="loop_h")
        with c2:
            m = st.number_input("Минуты", min_value=0, max_value=59, value=10, step=1, key="loop_m")
        with c3:
            s = st.number_input("Секунды", min_value=0, max_value=59, value=0, step=1, key="loop_s")

        target_seconds = _sec_from_hms(int(h), int(m), int(s))
        st.info(f"Итоговая длительность: **{_fmt_hms(target_seconds)}** (≈ {target_seconds:.1f} сек)")

        fade_out_sec = st.slider(
            "Плавное затухание в конце (сек)",
            min_value=0.0,
            max_value=20.0,
            value=6.0,
            step=0.5,
            key="loop_fade_out_sec",
        )

    else:
        st.markdown("### Настройки (умная подгонка под видео)")

        vid = st.file_uploader(
            "Загрузите видео-файл, под который нужно подогнать длину аудио",
            type=["mp4", "mov", "mkv", "webm", "m4v"],
            accept_multiple_files=False,
            key="loop_video_uploader",
        )

        if vid:
            sigv = _sig_of_uploaded(vid)
            if sigv not in st.session_state[PROC_VIDEO]:
                st.session_state[PROC_VIDEO].add(sigv)
                dstv = Path(state.paths.uploads_video) / f"{uuid.uuid4().hex[:8]}__{safe_filename(vid.name)}"
                dstv.write_bytes(vid.getbuffer())
                st.session_state[SS_VIDEO] = str(dstv)

        smart_video_path = st.session_state.get(SS_VIDEO)
        if not smart_video_path or not Path(smart_video_path).exists():
            st.info("Загрузите видео — я возьму его длительность автоматически.")
            st.stop()

        vdur = _probe_video_duration_seconds(str(smart_video_path))
        if vdur <= 0:
            st.error("Не удалось определить длительность видео через ffprobe. Проверь, что ffprobe доступен в PATH.")
            st.stop()

        st.success(f"Длительность видео: **{_fmt_hms(vdur)}** (≈ {vdur:.3f} сек)")
        try:
            st.video(Path(smart_video_path).read_bytes())
        except Exception:
            st.video(str(smart_video_path))

        safety_pad = st.slider(
            "Сделать аудио короче видео на (сек)",
            min_value=0.0,
            max_value=10.0,
            value=0.0,
            step=0.5,
            key="loop_video_pad",
        )

        fade_out_sec = st.slider(
            "Плавное затухание в конце (сек)",
            min_value=0.0,
            max_value=20.0,
            value=6.0,
            step=0.5,
            key="loop_video_fade_out_sec",
        )

        target_seconds = max(1.0, float(vdur) - float(safety_pad))
        st.info(f"Целевая длительность аудио: **{_fmt_hms(target_seconds)}** (≈ {target_seconds:.3f} сек)")

    do_preview = st.checkbox("Сделать превью (45 сек) после генерации", value=True, key="loop_preview")
    st.divider()

    if st.button("⚡ Создать зацикленный трек", type="primary", key="loop_run"):
        run_dir = session_dir / f"run_{uuid.uuid4().hex[:10]}"
        run_dir.mkdir(parents=True, exist_ok=True)

        with st.status("Генерирую луп…", expanded=True) as s:
            if mode.startswith("1)"):
                loops_count = int(st.session_state.get("loop_loops_count", 2))
                out_wav_raw = run_dir / "loop_raw.wav"
                _repeat_n_times_with_crossfade(
                    str(in_path),
                    str(out_wav_raw),
                    loops=loops_count,
                    crossfade_sec=float(crossfade),
                    sample_rate=48000,
                )
                                
                base_name = Path(pretty_filename_from_path(in_path)).stem or "Loop"
                tag = f"Loop ({loops_count}x)"
                out_name = build_output_name(base_name=base_name, tag=tag, ext=".wav")
                out_wav = ensure_unique_path(run_dir / out_name)

                if float(fade_out_sec) > 0:
                    _apply_fade_out(str(out_wav_raw), str(out_wav), float(fade_out_sec))
                else:
                    out_wav.write_bytes(out_wav_raw.read_bytes())

            else:
                if not target_seconds or float(target_seconds) <= 1:
                    st.error("Целевая длительность слишком маленькая.")
                    st.stop()

                out_wav_raw = run_dir / "loop_raw.wav"
                render_loop_to_duration(
                    str(in_path),
                    str(out_wav_raw),
                    float(target_seconds),
                    sample_rate=48000,
                    mp3_bitrate=None,
                )

                base_name = Path(pretty_filename_from_path(in_path)).stem or "Loop"
                tag = f"Loop ({_fmt_hms(float(target_seconds))})"
                out_name = build_output_name(base_name=base_name, tag=tag, ext=".wav")
                out_wav = ensure_unique_path(run_dir / out_name)

                if float(fade_out_sec) > 0:
                    _apply_fade_out(str(out_wav_raw), str(out_wav), float(fade_out_sec))
                else:
                    out_wav.write_bytes(out_wav_raw.read_bytes())

            state.last_audio_result = str(out_wav)
            state.last_audio_label = "Зацикливание (результат)"
            st.session_state["loop_last_out_wav"] = str(out_wav)
            st.session_state["loop_last_run_dir"] = str(run_dir)
            s.update(label="✅ Луп готов", state="complete")

        st.success("✅ Готово")
        st.audio(str(out_wav))
        st.caption(f"Размер текущего WAV: {human_file_size(file_size_bytes(str(out_wav)))}")

        st.download_button(
            "⬇️ Скачать WAV",
            data=out_wav.read_bytes(),
            file_name=out_wav.name,
            mime="audio/wav",
            key=f"loop_dl_wav_{out_wav.name}",
        )

        if fmt.startswith("MP3"):
            mp3_path = out_wav.with_suffix(".mp3")
            encode_mp3(input_path=str(out_wav), output_path=str(mp3_path), bitrate_k=320)

            st.download_button(
                "⬇️ Скачать MP3 (320kbps)",
                data=mp3_path.read_bytes(),
                file_name=mp3_path.name,
                mime="audio/mpeg",
                key=f"loop_dl_mp3_{mp3_path.name}",
            )

        if do_preview:
            prev = run_dir / "preview_45s.wav"
            render_preview(str(out_wav), str(prev), preview_seconds=45.0, sample_rate=48000)
            st.markdown("### Превью 45 сек")
            st.audio(str(prev))

        if mode.startswith("3)") and smart_video_path:
            _push_to_video_page_state(state, audio_path=str(out_wav), video_path=str(smart_video_path))
            st.info("Подготовил аудио+видео для вкладки 🎬 Создать видеоклип.")

        if st.button("➡️ Перейти в 🎬 Создать видеоклип", key="loop_goto_clip"):
            _push_to_video_page_state(
                state,
                audio_path=str(out_wav),
                video_path=str(smart_video_path) if smart_video_path else None,
            )
            st.session_state[PENDING_NAV_KEY] = NAV_CLIP_LABEL
            st.rerun()

    # ─────────────────────────────────────────────
    # Пост-нормализация результата лупа
    # ─────────────────────────────────────────────
    last_out = st.session_state.get("loop_last_out_wav")
    last_run_dir = st.session_state.get("loop_last_run_dir")

    if last_out and Path(last_out).exists():
        st.divider()
        st.subheader("🔊 Пост-нормализация результата лупа")
        st.caption("Нормализуем уже готовый луп целиком — это безопаснее, чем поднимать громкость отдельных кусков.")

        cN1, cN2, cN3 = st.columns([1, 1, 1])
        with cN1:
            norm_i = st.selectbox("Цель (LUFS)", options=[-16, -14, -12], index=1, key="loop_norm_i")
        with cN2:
            norm_tp = st.selectbox("Пик (dB)", options=[-2.0, -1.0, -0.8, -0.5], index=1, key="loop_norm_tp")
        with cN3:
            norm_limiter = st.checkbox("Лимитер (страховка от клипов)", value=True, key="loop_norm_limiter")

        norm_src = Path(last_out)
        norm_dir = Path(last_run_dir) if last_run_dir else norm_src.parent
        norm_name = build_output_name(base_name=norm_src.stem, tag=f"Norm {int(norm_i)}", ext=".wav")
        norm_out = ensure_unique_path(norm_dir / norm_name)
        st.caption(f"Нормализованный WAV: {norm_out}")

        cB1, cB2 = st.columns([1, 1])
        with cB1:
            if st.button("🔊 Нормализовать луп", key="loop_do_post_norm"):
                normalize_audio_to_wav(
                    input_path=str(norm_src),
                    output_path=str(norm_out),
                    target_i_lufs=int(norm_i),
                    true_peak_db=float(norm_tp),
                    lra=11,
                    limiter=bool(norm_limiter),
                    sample_rate=48000,
                )
                st.success("✅ Пост-нормализация готова.")
                st.audio(str(norm_out), format="audio/wav")

        with cB2:
            if st.button("🎵 Экспорт MP3 320k из лупа", key="loop_export_mp3_post"):
                src = str(norm_out) if norm_out.exists() else str(norm_src)
                mp3_path = Path(src).with_suffix(".mp3")
                encode_mp3(input_path=src, output_path=str(mp3_path), bitrate_k=320)
                st.success("✅ MP3 готов.")
                st.caption(str(mp3_path))


render = page_loop
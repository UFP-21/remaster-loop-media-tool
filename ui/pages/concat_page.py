from __future__ import annotations

from pathlib import Path
import uuid
import os

import streamlit as st

from core.concat_tracks import concat_audio_files_wav, concat_audio_timeline_wav
from core.ffmpeg import (
    probe_duration_seconds,
    normalize_audio_to_wav,
    encode_mp3,
    encode_flac,
    convert_audio_to_wav_16_44100,
    file_size_bytes,
    human_file_size,
    is_over_distrokid_limit,
)
from core.paths import (
    build_output_name,
    ensure_unique_path,
    pretty_filename_from_path,
    safe_filename,
)
from ui.components.lists import render_sortable_paths_list
from ui.state import AppState


def _sig_of_uploaded(f) -> str:
    size = getattr(f, "size", None)
    if size is None:
        try:
            size = len(f.getbuffer())
        except Exception:
            size = 0
    return f"{f.name}::{int(size)}"


def _save_upload_unique(uploaded_file, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    base_name = safe_filename(uploaded_file.name)
    dst = folder / f"{uuid.uuid4().hex}__{base_name}"
    dst.write_bytes(uploaded_file.getbuffer())
    return str(dst)


def _normalize_concat_items_to_paths(items) -> list[str]:
    out: list[str] = []
    if not isinstance(items, list):
        return out

    for it in items:
        if isinstance(it, str) and it.strip():
            out.append(it.strip())
        elif isinstance(it, dict):
            p = str(it.get("path", "")).strip()
            if p:
                out.append(p)

    seen = set()
    deduped: list[str] = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    return deduped


def _default_base_name_from_paths(paths: list[str]) -> str:
    if not paths:
        return "Mix"
    first = pretty_filename_from_path(paths[0])
    stem = Path(first).stem.strip()
    return stem or "Mix"


def _open_folder(path: Path) -> None:
    """Открыть папку в проводнике (Windows)."""
    try:
        if path.exists():
            os.startfile(str(path))  # noqa
    except Exception as e:
        st.warning(f"Не удалось открыть папку: {e}")


def _render_distrokid_tools(
    *,
    source_path: str,
    out_dir: Path,
    base_name: str,
    tag_prefix: str,
    key_prefix: str,
) -> None:
    """Показывает размер файла и экспорт для DistroKid (FLAC / WAV 16-44.1)."""
    src = Path(str(source_path))
    if not src.exists():
        return

    size_b = file_size_bytes(str(src))
    size_h = human_file_size(size_b)

    st.markdown("### 📦 Подготовка для DistroKid")
    st.caption(f"Текущий файл: {src.name}")
    st.write(f"Размер файла: **{size_h}**")

    if size_b is not None:
        if is_over_distrokid_limit(str(src)):
            st.error("⚠️ Файл больше 1 GB — DistroKid может не принять его. Рекомендуется FLAC или WAV 16-bit / 44.1 kHz.")
        elif size_b >= int(0.9 * 1024 * 1024 * 1024):
            st.warning("Файл уже близко к 1 GB. Лучше заранее подготовить облегчённую версию для DistroKid.")
        else:
            st.success("Размер файла укладывается в лимит 1 GB.")

    flac_out = ensure_unique_path(out_dir / build_output_name(base_name=base_name, tag=f"{tag_prefix} (FLAC)", ext=".flac"))
    wav44_out = ensure_unique_path(out_dir / build_output_name(base_name=base_name, tag=f"{tag_prefix} (44.1k 16bit)", ext=".wav"))

    st.caption(f"FLAC: {flac_out}")
    st.caption(f"WAV 16/44.1: {wav44_out}")

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🗜️ Экспорт FLAC (lossless)", key=f"{key_prefix}_to_flac"):
            encode_flac(
                input_path=str(src),
                output_path=str(flac_out),
                sample_rate=44100,
                compression_level=8,
            )
            st.success(f"✅ FLAC готов: {flac_out.name}")
            st.caption(f"Размер: {human_file_size(file_size_bytes(str(flac_out)))}")

    with c2:
        if st.button("🎚️ Экспорт WAV 16-bit / 44.1 kHz", key=f"{key_prefix}_to_wav441"):
            convert_audio_to_wav_16_44100(
                input_path=str(src),
                output_path=str(wav44_out),
            )
            st.success(f"✅ WAV 16/44.1 готов: {wav44_out.name}")
            st.caption(f"Размер: {human_file_size(file_size_bytes(str(wav44_out)))}")


def page_concat(state: AppState) -> None:
    st.subheader("🧩 Склейка треков")
    st.caption("Загрузите треки, отсортируйте (↑↓), нажмите ▶️ и склейте.")

    state.concat.items = _normalize_concat_items_to_paths(getattr(state.concat, "items", []))

    list_id = "concat_audio_paths"
    store_key = f"__paths__{list_id}"
    preview_key = f"__preview_path__{list_id}"

    if "concat_uploader_nonce" not in st.session_state:
        st.session_state["concat_uploader_nonce"] = 1
    uploader_key = f"concat_uploader__{st.session_state['concat_uploader_nonce']}"

    cA, cB = st.columns([1, 1])
    with cA:
        if st.button("🧹 Очистить список", help="Полная очистка: список + превью + uploader"):
            state.concat.items = []
            state.concat.result_path = None
            st.session_state.pop("concat__processed", None)
            st.session_state.pop(store_key, None)
            st.session_state.pop(preview_key, None)
            st.session_state.pop("timeline_offsets", None)
            st.session_state["concat_uploader_nonce"] += 1
            st.rerun()

    with cB:
        out_dir_btn = Path(state.paths.outputs_dir) / "concat"
        if st.button("📂 Открыть папку concat", help=str(out_dir_btn)):
            _open_folder(out_dir_btn)

    if "concat__processed" not in st.session_state:
        st.session_state["concat__processed"] = set()

    uploads = st.file_uploader(
        "Добавить аудиофайлы",
        type=["mp3", "wav", "flac", "ogg", "m4a"],
        accept_multiple_files=True,
        key=uploader_key,
    )

    if uploads:
        upload_dir = Path(state.paths.uploads_audio)
        upload_dir.mkdir(parents=True, exist_ok=True)

        existing = set(state.concat.items)
        added = 0

        for f in uploads:
            sig = _sig_of_uploaded(f)
            if sig in st.session_state["concat__processed"]:
                continue
            st.session_state["concat__processed"].add(sig)

            sp = _save_upload_unique(f, upload_dir)
            if sp not in existing:
                state.concat.items.append(sp)
                existing.add(sp)
                added += 1

        if added:
            st.success(f"Добавлено файлов: {added}")

    paths = render_sortable_paths_list(
        kind="audio",
        paths=list(state.concat.items),
        list_id=list_id,
        get_duration=lambda p: probe_duration_seconds(p) or 0.0,
        allow_preview=True,
        show_shuffle=True,
        preview_mode="panel",
    )
    state.concat.items = paths

    if len(paths) < 2:
        st.info("Загрузите минимум 2 трека для склейки.")
        return

    st.divider()

    st.subheader("📝 Имя результата")
    default_base = _default_base_name_from_paths(paths)
    base_name = st.text_input(
        "Базовое имя (без расширения)",
        value=st.session_state.get("concat_base_name", default_base),
        help="Используется для имени итогового файла в outputs/concat.",
    ).strip()
    if not base_name:
        base_name = default_base
    st.session_state["concat_base_name"] = base_name

    add_details = st.checkbox(
        "Добавлять тех. параметры в имя файла (кол-во треков, режим, кроссфейд)",
        value=False,
    )

    st.divider()

    st.subheader("Режим склейки")
    mode = st.radio(
        "Выбери режим",
        options=[
            "Кроссфейд (последовательно)",
            "Таймлайн MIX (наложение со сдвигом — как дорожки)",
            "Таймлайн REPLACE (замена с позиции — как вставка/обрезка)",
        ],
        index=0,
    )

    st.markdown("### Громкость (важно)")
    st.caption("Если слышишь «ступеньку» на старте следующего трека — выключи нормализацию ДО склейки и нормализуй финал (кнопка ниже).")
    normalize_each = st.checkbox(
        "🔊 Выровнять громкость МЕЖДУ треками (нормализация каждого трека перед склейкой) — может дать «ступеньку»",
        value=False,  # безопасный дефолт
    )
    target_i = st.selectbox("Целевая громкость (LUFS)", options=[-16, -14, -12], index=1)

    out_dir = Path(state.paths.outputs_dir) / "concat"
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode.startswith("Кроссфейд"):
        st.subheader("Переход (кроссфейд)")

        mode_label = st.radio(
            "Форма кроссфейда",
            options=[
                "Мягкий (fade-out + fade-in)",
                "Equal-power (ровнее по энергии)",
            ],
            index=0,
        )
        xfade_mode = "soft" if mode_label.startswith("Мягкий") else "no_dip"

        crossfade = st.slider(
            "Длительность кроссфейда (сек)",
            min_value=0.0,
            max_value=12.0,
            value=float(getattr(state.concat, "crossfade_sec", 2.0)),
            step=0.1,
        )
        state.concat.crossfade_sec = float(crossfade)

        default_batched = True if len(paths) >= 16 else False
        batched = st.checkbox(
            "⚡ Стабильная склейка (батчами) — рекомендуется при 16+ треках",
            value=default_batched,
        )
        batch_size = st.slider("Размер батча", 6, 15, 12, 1, disabled=not batched)

        n = len(paths)
        tag = "Concat"
        if add_details:
            tag = f"Concat ({n} tracks, xfade {state.concat.crossfade_sec:.1f}s)"

        out_name = build_output_name(base_name=base_name, tag=tag, ext=".wav")
        out_path = ensure_unique_path(out_dir / out_name)
        st.caption(f"Файл результата: {out_path}")

        if st.button("🧩 Склеить (кроссфейд)", type="primary"):
            concat_audio_files_wav(
                input_paths=paths,
                output_path=str(out_path),
                crossfade_sec=float(state.concat.crossfade_sec),
                sample_rate=48000,
                normalize_each=bool(normalize_each),
                target_i_lufs=int(target_i),
                xfade_mode=xfade_mode,
                batched=bool(batched),
                batch_size=int(batch_size),
                tmp_dir=str(out_dir / "_tmp_batches"),
            )
            state.concat.result_path = str(out_path)
            state.last_audio_result = str(out_path)

    else:
        timeline_mode = "mix" if "MIX" in mode else "replace"
        st.subheader("Таймлайн: сдвиг треков (offset)")

        if "timeline_offsets" not in st.session_state:
            st.session_state["timeline_offsets"] = {}

        offsets_map = dict(st.session_state["timeline_offsets"])

        if st.button("⚡ Авто-расставить подряд (по длительности)"):
            acc = 0.0
            new_map = {}
            for i, p in enumerate(paths):
                new_map[p] = 0.0 if i == 0 else acc
                d = float(probe_duration_seconds(p) or 0.0)
                acc += max(0.0, d)
            st.session_state["timeline_offsets"] = new_map
            st.rerun()

        for i, p in enumerate(paths):
            name = pretty_filename_from_path(p)
            default = 0.0 if i == 0 else float(offsets_map.get(p, 0.0))
            val = st.number_input(
                f"{i+1}) {name}",
                min_value=0.0,
                max_value=999999.0,
                value=float(default),
                step=0.1,
                disabled=(i == 0),
            )
            offsets_map[p] = 0.0 if i == 0 else float(val)

        st.session_state["timeline_offsets"] = offsets_map
        offsets_sec = [float(offsets_map.get(p, 0.0)) for p in paths]

        n = len(paths)
        tag = "Concat"
        if add_details:
            tag = f"Concat ({n} tracks, timeline {timeline_mode})"

        out_name = build_output_name(base_name=base_name, tag=tag, ext=".wav")
        out_path = ensure_unique_path(out_dir / out_name)
        st.caption(f"Файл результата: {out_path}")

        if st.button("🎬 Собрать (таймлайн)", type="primary"):
            concat_audio_timeline_wav(
                input_paths=paths,
                output_path=str(out_path),
                offsets_sec=offsets_sec,
                mode=timeline_mode,
                sample_rate=48000,
                normalize_each=bool(normalize_each),
                target_i_lufs=int(target_i),
            )
            state.concat.result_path = str(out_path)
            state.last_audio_result = str(out_path)

    rp = getattr(state.concat, "result_path", None)
    if rp and Path(rp).exists():
        st.divider()
        st.subheader("✅ Результат")
        st.audio(rp)
        st.caption(f"Размер текущего WAV: {human_file_size(file_size_bytes(rp))}")

        st.divider()
        st.subheader("🔊 Пост-нормализация результата (по желанию)")
        st.caption("Нормализуем УЖЕ ГОТОВЫЙ файл целиком — так нет «ступенек» на стыках.")
        cN1, cN2, cN3 = st.columns([1, 1, 1])
        with cN1:
            norm_i = st.selectbox("Цель (LUFS)", options=[-16, -14, -12], index=1, key="concat_norm_i")
        with cN2:
            norm_tp = st.selectbox("Пик (dB)", options=[-2.0, -1.0, -0.8, -0.5], index=1, key="concat_norm_tp")
        with cN3:
            norm_limiter = st.checkbox("Лимитер (страховка от клипов)", value=True, key="concat_norm_limiter")

        norm_tag = f"Concat (Norm {int(norm_i)})"
        norm_out_name = build_output_name(base_name=base_name, tag=norm_tag, ext=".wav")
        norm_out_path = ensure_unique_path(Path(state.paths.outputs_dir) / "concat" / norm_out_name)
        st.caption(f"Нормализованный WAV: {norm_out_path}")

        cB1, cB2 = st.columns([1, 1])
        with cB1:
            if st.button("🔊 Нормализовать WAV", key="concat_do_post_norm"):
                normalize_audio_to_wav(
                    input_path=rp,
                    output_path=str(norm_out_path),
                    target_i_lufs=int(norm_i),
                    true_peak_db=float(norm_tp),
                    lra=11,
                    limiter=bool(norm_limiter),
                    sample_rate=48000,
                )
                st.success("✅ Нормализация готова.")
                st.audio(str(norm_out_path), format="audio/wav")

        with cB2:
            if st.button("🎵 Экспорт MP3 320k", key="concat_export_mp3"):
                mp3_path = norm_out_path.with_suffix(".mp3")
                src = str(norm_out_path) if norm_out_path.exists() else rp
                encode_mp3(input_path=src, output_path=str(mp3_path), bitrate_k=320)
                st.success("✅ MP3 готов.")
                st.caption(str(mp3_path))


render = page_concat
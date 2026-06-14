from __future__ import annotations

from pathlib import Path
import hashlib
import uuid
import os
import re
import time

import streamlit as st

from core.paths import (
    build_output_name,
    ensure_unique_path,
    pretty_filename_from_path,
    safe_filename,
)
from core.ffmpeg import (
    which_ffmpeg,
    run_cmd,
    probe_duration_seconds,
    trim_audio_to_wav,
    trim_silence_audio_to_wav,
    normalize_audio_to_wav,
    encode_mp3,
    encode_flac,
    convert_audio_to_wav_16_44100,
    file_size_bytes,
    human_file_size,
    is_over_distrokid_limit,
)
from ui.state import AppState


# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────
def _sig_of_uploaded(f) -> str:
    size = getattr(f, "size", None)
    if size is None:
        try:
            size = len(f.getbuffer())
        except Exception:
            size = 0
    return f"{f.name}::{int(size)}"


def _save_upload(uploaded_file, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{uuid.uuid4().hex}__{safe_filename(uploaded_file.name)}"
    dst.write_bytes(uploaded_file.getbuffer())
    return str(dst)


def _normalize_items_to_paths(items) -> list[str]:
    """Поддерживаем list[str] и list[dict]. Возвращаем list[str] без дублей."""
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
    res: list[str] = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        res.append(p)
    return res


def _preset_label_for_filename(preset_name: str) -> str:
    """Чистим пресет для суффикса в имени файла (без эмодзи)."""
    s = (preset_name or "").strip()
    s = re.sub(r"^[^0-9A-Za-zА-Яа-я]+\s*", "", s)
    return safe_filename(s) or "Preset"


def _open_folder(path: Path) -> None:
    """Открыть папку в проводнике (Windows)."""
    try:
        if path.exists():
            os.startfile(str(path))  # noqa
    except Exception as e:
        st.warning(f"Не удалось открыть папку: {e}")


def _apply_pending_selected_before_widgets() -> None:
    """Применяем отложенную смену выбранного трека ДО создания selectbox."""
    pending = st.session_state.pop("__pending_mast_selected_path", None)
    if isinstance(pending, str) and pending.strip():
        st.session_state["mast_selected_path"] = pending


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


# ────────────────────────────────────────────────
# Presets
# ────────────────────────────────────────────────
PRESETS = {
    "🎵 Универсальный": (-14, -1.0, 11, 0.0, 0.0, 0.0, None),
    "🔎 Чёткость": (-14, -1.0, 11, 0.0, 1.5, 2.0, None),
    "🔥 Тёплый (мягче верх)": (-14, -1.0, 11, 0.5, -0.5, -1.5, None),
    "🌬️ Яркие верха (воздух)": (-14, -1.0, 11, 0.0, 0.5, 2.5, None),
    "🛡️ Мягкий и безопасный": (-16, -1.0, 12, 0.0, 0.0, 0.5, None),
    "🟣 Глубокий бас + яркий верх": (-14, -1.0, 11, 4.0, 0.0, 3.0, None),
    "🎬 Кино (глубже, спокойнее)": (-16, -1.0, 12, 2.0, 0.0, 1.0, None),
    "🎧 Шире стерео": (-14, -1.0, 11, 0.0, 0.0, 1.0, 140.0),
    "🎧 Шире стерео + воздух": (-14, -1.0, 11, 0.0, 0.5, 2.5, 145.0),
    "⚙️ Custom (ручной)": (-14, -1.0, 11, 0.0, 0.0, 0.0, 120.0),
}

TILES = list(PRESETS.keys())

HELP = {
    "🎵 Универсальный": "Ровный базовый вариант.",
    "🔎 Чёткость": "Больше читаемости/атаки (середина+верх).",
    "🔥 Тёплый (мягче верх)": "Мягче верх, чуть теплее.",
    "🌬️ Яркие верха (воздух)": "Добавляет «воздуха»/яркости наверху.",
    "🛡️ Мягкий и безопасный": "Тише по LUFS — меньше риска перегруза.",
    "🟣 Глубокий бас + яркий верх": "V-образная форма: низ + верх.",
    "🎬 Кино (глубже, спокойнее)": "Чуть спокойнее под «киношный» вайб.",
    "🎧 Шире стерео": "Расширяет стерео-картину.",
    "🎧 Шире стерео + воздух": "Шире + чуть «воздуха» сверху.",
    "⚙️ Custom (ручной)": "Ручные параметры LUFS/TP/LRA/EQ + ширина.",
}


def _build_af(
    i_lufs: int,
    tp_db: float,
    lra: int,
    low_db: float,
    mid_db: float,
    high_db: float,
    stereo_pct: float | None,
    apply_loudnorm: bool = True,
) -> str:
    """Собирает audio filtergraph.

    Если apply_loudnorm=False -> loudnorm не применяется (только EQ + ширина).
    """
    parts: list[str] = []

    if apply_loudnorm:
        parts.append(
            f"loudnorm=I={int(i_lufs)}:TP={float(tp_db)}:LRA={int(lra)}:print_format=summary"
        )

    parts.append(f"equalizer=f=80:t=h:w=1.0:g={float(low_db)}")
    parts.append(f"equalizer=f=1000:t=h:w=1.0:g={float(mid_db)}")
    parts.append(f"equalizer=f=8000:t=h:w=1.0:g={float(high_db)}")

    if stereo_pct is not None and float(stereo_pct) > 0:
        w = float(stereo_pct)
        parts.append(f"stereotools=mlev={w/100.0:.3f}")

    return ",".join(parts)


def _preview_cache_key(input_path: str, preset_name: str, start_sec: float, dur_sec: float, af: str) -> str:
    p = Path(input_path)
    try:
        stt = p.stat()
        stamp = f"{stt.st_size}-{int(stt.st_mtime)}"
    except Exception:
        stamp = "0-0"
    raw = f"{p.resolve()}|{stamp}|{preset_name}|{start_sec:.3f}|{dur_sec:.3f}|{af}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _render_preview(
    *,
    input_path: str,
    out_dir: Path,
    preset_name: str,
    start_sec: float,
    dur_sec: float,
    i_lufs: int,
    tp_db: float,
    lra: int,
    low_db: float,
    mid_db: float,
    high_db: float,
    stereo_pct: float | None,
    apply_loudnorm: bool = True,
) -> str:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")

    out_dir.mkdir(parents=True, exist_ok=True)
    af = _build_af(
        i_lufs,
        tp_db,
        lra,
        low_db,
        mid_db,
        high_db,
        stereo_pct,
        apply_loudnorm=apply_loudnorm,
    )

    key = _preview_cache_key(input_path, preset_name, start_sec, dur_sec, af)
    out_path = out_dir / f"PREVIEW__{key}__{safe_filename(preset_name)}.wav"

    if out_path.exists() and out_path.stat().st_size > 10_000:
        return str(out_path)

    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0.0, float(start_sec)):.3f}",
        "-t",
        f"{max(0.5, float(dur_sec)):.3f}",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        af,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(out_path),
    ]
    run_cmd(cmd, check=True)
    return str(out_path)


def _run_mastering_full(
    *,
    input_path: str,
    output_path: str,
    i_lufs: int,
    tp_db: float,
    lra: int,
    low_db: float,
    mid_db: float,
    high_db: float,
    stereo_pct: float | None,
    sample_rate: int = 48000,
    apply_loudnorm: bool = True,
) -> None:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")

    af = _build_af(
        i_lufs,
        tp_db,
        lra,
        low_db,
        mid_db,
        high_db,
        stereo_pct,
        apply_loudnorm=apply_loudnorm,
    )

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        af,
        "-ar",
        str(sample_rate),
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    run_cmd(cmd, check=True)


def page_mastering(state: AppState) -> None:
    st.header("🎧 Мастеринг")

    _apply_pending_selected_before_widgets()

    # Нормализуем список входных треков (dedup)
    state.mastering.items = _normalize_items_to_paths(getattr(state.mastering, "items", []))

    # nonce для uploader (стабильная очистка)
    if "mast_uploader_nonce" not in st.session_state:
        st.session_state["mast_uploader_nonce"] = 1
    uploader_key = f"mast_uploader__{st.session_state['mast_uploader_nonce']}"

    # ── Очистки
    c0, c1, c2 = st.columns([1, 1, 1])

    with c0:
        if st.button("🧹 Очистить входные", key="mast_clear_inputs"):
            state.mastering.items = []
            state.mastering.last_output_path = None
            st.session_state.pop("mast__processed", None)
            st.session_state.pop("mast_selected_path", None)
            st.session_state.pop("mast_preview_path", None)
            st.session_state.pop("mast_trim_preview", None)
            st.session_state["mast_uploader_nonce"] += 1
            st.rerun()

    with c1:
        if st.button("🗑️ Очистить выходные", key="mast_clear_outputs"):
            out_dir = Path(state.paths.outputs_dir) / "mastering"
            prev_dir = Path(state.paths.outputs_dir) / "mastering_previews"
            trim_dir = Path(state.paths.outputs_dir) / "trim"
            for d in [out_dir, prev_dir, trim_dir]:
                d.mkdir(parents=True, exist_ok=True)
                for p in d.glob("*"):
                    try:
                        if p.is_file():
                            p.unlink()
                    except Exception:
                        pass
            state.mastering.last_output_path = None
            st.session_state.pop("mast_preview_path", None)
            st.session_state.pop("mast_trim_preview", None)
            st.success("Выходные удалены.")
            st.rerun()

    with c2:
        out_dir = Path(state.paths.outputs_dir) / "mastering"
        if st.button("📂 Открыть папку мастеринга", help=str(out_dir)):
            _open_folder(out_dir)

    st.divider()

    # ── Upload (dedup)
    if "mast__processed" not in st.session_state:
        st.session_state["mast__processed"] = set()

    uploads = st.file_uploader(
        "Добавить аудиофайлы",
        type=["mp3", "wav", "flac", "ogg", "m4a"],
        accept_multiple_files=True,
        key=uploader_key,
    )

    if uploads:
        added = 0
        for f in uploads:
            sig = _sig_of_uploaded(f)
            if sig in st.session_state["mast__processed"]:
                continue
            st.session_state["mast__processed"].add(sig)

            p = _save_upload(f, Path(state.paths.uploads_audio))
            state.mastering.items.append(p)
            added += 1

        if added:
            st.success(f"Добавлено: {added}")

    state.mastering.items = _normalize_items_to_paths(state.mastering.items)

    if not state.mastering.items:
        st.info("Загрузи хотя бы один трек.")
        return

    # ── Выбор трека + оригинал
    st.subheader("✅ Выбор трека (для обрезки / превью / сравнения)")

    paths = list(state.mastering.items)
    if "mast_selected_path" not in st.session_state or st.session_state["mast_selected_path"] not in paths:
        st.session_state["mast_selected_path"] = paths[0]

    selected_path = st.selectbox(
        "Выбери трек",
        options=paths,
        format_func=lambda p: pretty_filename_from_path(p),
        index=paths.index(st.session_state["mast_selected_path"]),
        key="mast_selected_path",
    )

    st.markdown("### 🎧 Оригинал (без обработки)")
    st.audio(selected_path)

    # ────────────────────────────────────────────────
    # ✂️ Обрезка
    # ────────────────────────────────────────────────
    st.divider()
    st.subheader("✂️ Обрезка трека")

    dur = probe_duration_seconds(selected_path)
    if dur is None:
        st.error("Не удалось определить длительность трека (ffprobe). Для обрезки нужен ffprobe.")
        st.stop()
    dur = float(dur)
    st.caption(f"Длительность: ~ {dur:.2f} сек")

    trim_dir = Path(state.paths.outputs_dir) / "trim"
    trim_dir.mkdir(parents=True, exist_ok=True)

    mode = st.radio(
        "Режим обрезки",
        options=["Ручной: срезать в начале/конце", "Авто: срезать тишину (начало+конец)"],
        index=0,
        horizontal=True,
        key="mast_trim_main_mode",
    )

    cA, cB, _ = st.columns([1, 1, 1])
    with cA:
        replace_in_list = st.checkbox(
            "Заменить трек в списке на обрезанный",
            value=False,
            help="Если включить, дальше пакетный мастеринг пойдёт по обрезанной версии.",
            key="mast_trim_replace",
        )
    with cB:
        open_folder_after = st.checkbox("Открыть папку после сохранения", value=True, key="mast_trim_open")

    if mode.startswith("Ручной"):
        if "mast_cut_start" not in st.session_state:
            st.session_state["mast_cut_start"] = 0.0
        if "mast_cut_end" not in st.session_state:
            st.session_state["mast_cut_end"] = 0.0

        c1, c2 = st.columns(2)
        with c1:
            cut_start = st.number_input(
                "Срезать в начале (сек)",
                min_value=0.0,
                max_value=max(0.0, dur),
                value=float(st.session_state["mast_cut_start"]),
                step=0.1,
                key="mast_cut_start",
            )
        with c2:
            cut_end = st.number_input(
                "Срезать в конце (сек)",
                min_value=0.0,
                max_value=max(0.0, dur),
                value=float(st.session_state["mast_cut_end"]),
                step=0.1,
                key="mast_cut_end",
            )

        keep_len = dur - float(cut_start) - float(cut_end)
        if keep_len <= 0.2:
            st.error("Слишком много срезано: итоговый трек станет слишком коротким. Уменьши срезы.")
        else:
            start_sec = max(0.0, float(cut_start))
            end_sec = max(start_sec, dur - float(cut_end))
            st.info(f"Оставляем: **{start_sec:.2f} .. {end_sec:.2f} сек** (итого ~ {keep_len:.2f} сек)")

            trim_mode = st.radio(
                "Тип края",
                options=["Резко (cut)", "Плавно (fade-out в конце)"],
                index=0,
                horizontal=True,
                key="mast_trim_mode",
            )

            fade_in = 0.0
            fade_out = 0.0
            if "Плавно" in trim_mode:
                cF1, cF2 = st.columns(2)
                with cF1:
                    fade_in = st.slider(
                        "Fade-in (сек, опционально)",
                        0.0,
                        5.0,
                        0.0,
                        0.1,
                        key="mast_trim_fade_in",
                    )
                with cF2:
                    fade_out = st.slider("Fade-out (сек)", 0.1, 10.0, 2.0, 0.1, key="mast_trim_fade_out")

            tag_t = "Trim" if "Резко" in trim_mode else "Trim (Fade)"
            out_name_trim = build_output_name(source_path=selected_path, tag=tag_t, ext=".wav")
            out_path_trim = ensure_unique_path(trim_dir / out_name_trim)
            st.caption(f"Файл результата: {out_path_trim}")

            if st.button("✂️ Обрезать", type="primary", key="mast_trim_do"):
                mode_ff = "sharp" if "Резко" in trim_mode else "fade"
                trim_audio_to_wav(
                    input_path=selected_path,
                    output_path=str(out_path_trim),
                    start_sec=float(start_sec),
                    end_sec=float(end_sec),
                    mode=mode_ff,
                    fade_in_sec=float(fade_in),
                    fade_out_sec=float(fade_out),
                    sample_rate=48000,
                )

                st.session_state["mast_trim_preview"] = str(out_path_trim)
                st.success("✅ Обрезка готова.")
                st.audio(str(out_path_trim), format="audio/wav")

                if replace_in_list:
                    new_items = []
                    for p in state.mastering.items:
                        new_items.append(str(out_path_trim) if str(p) == str(selected_path) else p)
                    state.mastering.items = _normalize_items_to_paths(new_items)

                    st.session_state["__pending_mast_selected_path"] = str(out_path_trim)
                    st.success("✅ Трек в списке заменён на обрезанный.")

                if open_folder_after:
                    _open_folder(trim_dir)

                st.rerun()

    else:
        st.caption("Авто-режим пытается сам определить тишину в начале/конце и удалить её.")
        thr = st.slider("Порог тишины (dB)", -60.0, -10.0, -35.0, 1.0, key="sil_thr_db")
        min_sil = st.slider("Минимальная тишина (сек)", 0.05, 2.0, 0.20, 0.05, key="sil_min_sec")
        keep = st.slider("Оставить запас (сек)", 0.0, 0.5, 0.05, 0.01, key="sil_keep_sec")

        out_name_trim = build_output_name(source_path=selected_path, tag="Trim (Silence)", ext=".wav")
        out_path_trim = ensure_unique_path(trim_dir / out_name_trim)
        st.caption(f"Файл результата: {out_path_trim}")

        if st.button("🤖 Срезать тишину", type="primary", key="mast_trim_silence_do"):
            trim_silence_audio_to_wav(
                input_path=selected_path,
                output_path=str(out_path_trim),
                threshold_db=float(thr),
                min_silence_sec=float(min_sil),
                keep_sec=float(keep),
                sample_rate=48000,
            )

            st.session_state["mast_trim_preview"] = str(out_path_trim)
            st.success("✅ Авто-срез тишины готов.")
            st.audio(str(out_path_trim), format="audio/wav")

            if replace_in_list:
                new_items = []
                for p in state.mastering.items:
                    new_items.append(str(out_path_trim) if str(p) == str(selected_path) else p)
                state.mastering.items = _normalize_items_to_paths(new_items)

                st.session_state["__pending_mast_selected_path"] = str(out_path_trim)
                st.success("✅ Трек в списке заменён на авто-обрезанный.")

            if open_folder_after:
                _open_folder(trim_dir)

            st.rerun()

    last_trim = st.session_state.get("mast_trim_preview")
    if last_trim and Path(last_trim).exists():
        with st.expander("🎧 Последняя обрезка (прослушать снова)", expanded=False):
            st.audio(last_trim, format="audio/wav")
            st.caption(last_trim)

    # ────────────────────────────────────────────────
    # Пресеты
    # ────────────────────────────────────────────────
    st.divider()
    st.subheader("🎛️ Пресеты мастеринга (плитки)")

    cur = getattr(state.mastering, "preset", None) or "🎵 Универсальный"
    if cur not in PRESETS:
        cur = "🎵 Универсальный"
    state.mastering.preset = cur

    cols = st.columns(4)
    for i, name in enumerate(TILES):
        with cols[i % 4]:
            if st.button(name, key=f"mast_tile_{name}"):
                state.mastering.preset = name
                st.rerun()
            st.caption(HELP.get(name, ""))

    preset = state.mastering.preset
    st.caption(f"Текущий пресет: **{preset}**")

    # ── Параметры пресета
    if preset == "⚙️ Custom (ручной)":
        st.markdown("### ⚙️ Ручные настройки (Custom)")
        cA, cB, cC = st.columns(3)
        with cA:
            i_lufs = st.slider("Target LUFS (I)", -24, -6, -14, 1, key="mast_i_lufs")
        with cB:
            tp_db = st.slider("True Peak (TP)", -3.0, -0.1, -1.0, 0.1, key="mast_tp")
        with cC:
            lra = st.slider("LRA", 5, 20, 11, 1, key="mast_lra")

        st.markdown("### 🎚️ Эквалайзер")
        cE1, cE2, cE3 = st.columns(3)
        with cE1:
            low_db = st.slider("Низ (bass) dB", -8.0, 8.0, 0.0, 0.5, key="mast_low")
        with cE2:
            mid_db = st.slider("Середина (mid) dB", -8.0, 8.0, 0.0, 0.5, key="mast_mid")
        with cE3:
            high_db = st.slider("Верх (treble) dB", -8.0, 8.0, 0.0, 0.5, key="mast_high")

        st.markdown("### 🎧 Стерео-ширина")
        stereo_pct = st.slider("Stereo widen (%)", 0, 160, 120, 5, key="mast_stereo_pct")
        stereo_pct = None if stereo_pct <= 0 else float(stereo_pct)
    else:
        i_lufs, tp_db, lra, low_db, mid_db, high_db, stereo_pct = PRESETS[preset]
        with st.expander("ℹ️ Параметры пресета"):
            st.write(f"LUFS(I): {i_lufs} | TP: {tp_db} | LRA: {lra}")
            st.write(f"EQ: low {low_db:+.1f} dB | mid {mid_db:+.1f} dB | high {high_db:+.1f} dB")
            st.write(f"Stereo widen: {stereo_pct:.0f}%" if stereo_pct is not None else "Stereo widen: нет")

    # ────────────────────────────────────────────────
    # Loudnorm toggle + hint
    # ────────────────────────────────────────────────
    st.divider()
    st.subheader("⚙️ Loudnorm (выравнивание громкости)")
    apply_loudnorm = st.checkbox(
        "Применять Loudnorm (выравнивание по LUFS)",
        value=True,
        help="Если выключить, громкость не выравнивается — применяется только EQ/ширина.",
        key="mast_apply_loudnorm",
    )
    st.caption("Если интро стало слишком громким — выключи Loudnorm.")
    if not apply_loudnorm:
        st.info("Loudnorm выключен: громкость трека не выравнивается (только EQ/ширина).")

    st.divider()

    # ── Превью
    st.subheader("🎧 Превью пресета (кусок 5–10 сек)")

    cP1, cP2 = st.columns([1, 2])
    with cP1:
        preview_len = st.slider("Длина превью (сек)", 3, 15, 8, 1, key="mast_preview_len")
    with cP2:
        preview_start = st.slider("Старт (сек)", 0.0, 180.0, 0.0, 0.5, key="mast_preview_start")

    prev_dir = Path(state.paths.outputs_dir) / "mastering_previews"

    if st.button("⚡ Сделать превью текущего пресета", type="primary", key="mast_make_preview"):
        prev_path = _render_preview(
            input_path=selected_path,
            out_dir=prev_dir,
            preset_name=preset,
            start_sec=float(preview_start),
            dur_sec=float(preview_len),
            i_lufs=int(i_lufs),
            tp_db=float(tp_db),
            lra=int(lra),
            low_db=float(low_db),
            mid_db=float(mid_db),
            high_db=float(high_db),
            stereo_pct=(None if stereo_pct is None else float(stereo_pct)),
            apply_loudnorm=bool(apply_loudnorm),
        )
        st.session_state["mast_preview_path"] = prev_path

    prev_path = st.session_state.get("mast_preview_path")
    if prev_path and Path(prev_path).exists():
        st.markdown("#### ▶️ Превью результата")
        st.audio(prev_path, format="audio/wav")

    st.divider()

    # ── Экспорт
    st.subheader("📦 Экспорт полного мастеринга (весь трек)")

    out_dir = Path(state.paths.outputs_dir) / "mastering"
    out_dir.mkdir(parents=True, exist_ok=True)

    include_preset = st.checkbox(
        "Добавить название пресета в имя файла",
        value=False,
        help="Если включить, получится: 'Трек — Master (Cinema).wav'",
        key="mast_include_preset_in_name",
    )

    tag = "Master"
    if include_preset:
        tag = f"Master ({_preset_label_for_filename(preset)})"

    out_name = build_output_name(source_path=selected_path, tag=tag, ext=".wav")
    out_path = ensure_unique_path(out_dir / out_name)

    st.caption(f"Файл результата: {out_path}")

    if st.button("🎚️ Сделать мастеринг (весь трек)", key="mast_do_full"):
        with st.status("Выполняю мастеринг…", expanded=True):
            _run_mastering_full(
                input_path=selected_path,
                output_path=str(out_path),
                i_lufs=int(i_lufs),
                tp_db=float(tp_db),
                lra=int(lra),
                low_db=float(low_db),
                mid_db=float(mid_db),
                high_db=float(high_db),
                stereo_pct=(None if stereo_pct is None else float(stereo_pct)),
                sample_rate=48000,
                apply_loudnorm=bool(apply_loudnorm),
            )
        state.mastering.last_output_path = str(out_path)
        st.success("✅ Готово: мастеринг выполнен.")
        st.audio(str(out_path))
        st.caption(f"Размер текущего WAV: {human_file_size(file_size_bytes(str(out_path)))}")

    # ── Пост-нормализация результата мастеринга
    mr = getattr(state.mastering, "last_output_path", None)
    if mr and Path(mr).exists():
        st.divider()
        st.subheader("🔊 Пост-нормализация мастера (по желанию)")
        st.caption("Полезно, если мастер делался без Loudnorm, а итоговую громкость нужно выровнять уже на готовом файле.")

        cN1, cN2, cN3 = st.columns([1, 1, 1])
        with cN1:
            norm_i = st.selectbox("Цель (LUFS)", options=[-16, -14, -12], index=1, key="mast_norm_i")
        with cN2:
            norm_tp = st.selectbox("Пик (dB)", options=[-2.0, -1.0, -0.8, -0.5], index=1, key="mast_norm_tp")
        with cN3:
            norm_limiter = st.checkbox("Лимитер (страховка от клипов)", value=True, key="mast_norm_limiter")

        norm_tag = f"Master (Norm {int(norm_i)})"
        norm_out_name = build_output_name(source_path=selected_path, tag=norm_tag, ext=".wav")
        norm_out_path = ensure_unique_path(out_dir / norm_out_name)
        st.caption(f"Нормализованный WAV: {norm_out_path}")

        cB1, cB2 = st.columns([1, 1])
        with cB1:
            if st.button("🔊 Нормализовать мастер", key="mast_do_post_norm"):
                normalize_audio_to_wav(
                    input_path=mr,
                    output_path=str(norm_out_path),
                    target_i_lufs=int(norm_i),
                    true_peak_db=float(norm_tp),
                    lra=11,
                    limiter=bool(norm_limiter),
                    sample_rate=48000,
                )
                st.success("✅ Пост-нормализация готова.")
                st.audio(str(norm_out_path), format="audio/wav")

        with cB2:
            if st.button("🎵 Экспорт MP3 320k", key="mast_export_mp3"):
                src = str(norm_out_path) if norm_out_path.exists() else str(mr)
                mp3_path = Path(src).with_suffix(".mp3")
                encode_mp3(input_path=src, output_path=str(mp3_path), bitrate_k=320)
                st.success("✅ MP3 готов.")
                st.caption(str(mp3_path))

        distro_src = str(norm_out_path) if norm_out_path.exists() else str(mr)
        base_name_dk = Path(distro_src).stem
        _render_distrokid_tools(
            source_path=distro_src,
            out_dir=out_dir,
            base_name=base_name_dk,
            tag_prefix="Master",
            key_prefix="master_distrokid",
        )

    # ── Пакетный мастеринг (однопоточный, безопасный)
    st.divider()
    st.subheader("📦 Пакетный мастеринг (все треки из списка)")

    only_missing = st.checkbox(
        "Пропускать треки, если результат уже существует",
        value=False,
        help="Полезно, если мастеринг прервался — можно продолжить без пересчёта уже готовых.",
        key="mast_batch_only_missing",
    )

    if "mast_batch_last_results" not in st.session_state:
        st.session_state["mast_batch_last_results"] = []

    if st.button("🚀 Сделать мастеринг ВСЕХ треков", type="primary", key="mast_do_batch"):
        if not which_ffmpeg():
            st.error("ffmpeg не найден. Проверь PATH/FFMPEG_PATH.")
            return

        progress = st.progress(0)
        status = st.empty()

        results: list[str] = []
        started = time.time()

        total = len(state.mastering.items)
        done = 0

        for idx, p in enumerate(state.mastering.items, start=1):
            out_name_b = build_output_name(source_path=p, tag=tag, ext=".wav")
            out_path_b = ensure_unique_path(out_dir / out_name_b)

            if only_missing:
                raw = out_dir / out_name_b
                if raw.exists() and raw.stat().st_size > 10_000:
                    results.append(str(raw))
                    done += 1
                    progress.progress(int(done / total * 100))
                    continue

            status.info(f"[{idx}/{total}] {pretty_filename_from_path(p)} → {out_path_b.name}")

            try:
                _run_mastering_full(
                    input_path=p,
                    output_path=str(out_path_b),
                    i_lufs=int(i_lufs),
                    tp_db=float(tp_db),
                    lra=int(lra),
                    low_db=float(low_db),
                    mid_db=float(mid_db),
                    high_db=float(high_db),
                    stereo_pct=(None if stereo_pct is None else float(stereo_pct)),
                    sample_rate=48000,
                    apply_loudnorm=bool(apply_loudnorm),
                )
                results.append(str(out_path_b))
            except Exception as e:
                status.error(f"Ошибка на файле: {pretty_filename_from_path(p)}\n{e}")
                results.append(f"ERROR::{p}::{e}")
            finally:
                done += 1
                progress.progress(int(done / total * 100))

        dt = time.time() - started
        st.session_state["mast_batch_last_results"] = results

        status.success(f"✅ Пакетный мастеринг завершён. Готово: {done}/{total}. Время: {dt:.1f} сек.")
        _open_folder(out_dir)

    batch_results = st.session_state.get("mast_batch_last_results", [])
    if batch_results:
        with st.expander("📄 Результаты последнего пакетного мастеринга", expanded=False):
            ok = [x for x in batch_results if not str(x).startswith("ERROR::")]
            bad = [x for x in batch_results if str(x).startswith("ERROR::")]
            st.write(f"✅ Успешно: {len(ok)}")
            st.write(f"❌ Ошибок: {len(bad)}")
            if bad:
                for x in bad:
                    st.code(x)

    if getattr(state.mastering, "last_output_path", None) and Path(state.mastering.last_output_path).exists():
        st.divider()
        st.subheader("✅ Результат мастеринга")
        st.audio(state.mastering.last_output_path)
        st.caption(state.mastering.last_output_path)


render = page_mastering
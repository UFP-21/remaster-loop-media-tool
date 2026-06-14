from __future__ import annotations

from pathlib import Path
import uuid
import shutil

import streamlit as st

from core.ffmpeg import probe_duration_seconds
from core.paths import safe_filename
from core.concat_videos import concat_videos
from ui.components.lists import render_sortable_media_list
from ui.state import AppState

PENDING_NAV_KEY = "__pending_nav_choice"
NAV_CLIP_LABEL = "🎬 Создать видеоклип"


def _sig_of_uploaded(f) -> str:
    size = getattr(f, "size", None)
    if size is None:
        try:
            size = len(f.getbuffer())
        except Exception:
            size = 0
    return f"{f.name}::{int(size)}"


def _try_delete_tmp_trim(tmp_trim_dir: Path) -> bool:
    try:
        if tmp_trim_dir.exists():
            shutil.rmtree(tmp_trim_dir, ignore_errors=True)
        return True
    except Exception:
        return False


def page_video_concat(state: AppState) -> None:
    st.header("🎞️ Склейка видео")
    st.caption("Загрузите клипы, при желании обрежьте начало/конец, отсортируйте (↑↓) и склейте в один MP4.")

    list_id = "video_concat_items"
    store_key = f"__items__{list_id}"
    preview_key = f"__preview_path__{list_id}"

    in_dir = Path(state.paths.uploads_video)
    out_dir = Path(state.paths.outputs_dir) / "video_concat"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # где копятся временные обрезки
    tmp_trim_dir = out_dir / "_tmp_trim"

    # nonce uploader
    if "vconcat_uploader_nonce" not in st.session_state:
        st.session_state["vconcat_uploader_nonce"] = 1
    uploader_key = f"video_concat_uploader__{st.session_state['vconcat_uploader_nonce']}"

    # map обрезок
    st.session_state.setdefault("vconcat_trim_map", {})

    # ✅ автоудаление tmp_trim
    auto_delete_tmp = st.checkbox(
        "✅ Авто-удалять временные обрезки (_tmp_trim) после успешной склейки",
        value=True,
        help="Удалит временные обрезанные клипы сразу после создания итогового MP4.",
    )

    c0, c1, c2 = st.columns([1, 1, 1])

    with c0:
        if st.button("🧹 Очистить список входных", key="vconcat_clear_inputs"):
            state.video.concat_items = []
            st.session_state.pop("vconcat__processed", None)
            st.session_state.pop(store_key, None)
            st.session_state.pop(preview_key, None)
            st.session_state.pop("vconcat_trim_map", None)

            st.session_state["vconcat_uploader_nonce"] += 1
            st.rerun()

    with c1:
        if st.button("🗑️ Очистить выходные", key="vconcat_clear_outputs"):
            try:
                for p in out_dir.glob("*.mp4"):
                    p.unlink()
            except Exception:
                pass
            state.video.bg_video_merged = None
            st.success("Выходные (video_concat) удалены.")
            st.rerun()

    with c2:
        if st.button("🧹 Очистить временные обрезки (_tmp_trim)", key="vconcat_clear_tmp_trim"):
            ok = _try_delete_tmp_trim(tmp_trim_dir)
            if ok:
                st.success("Временные обрезки удалены (_tmp_trim).")
            else:
                st.warning("Не удалось удалить _tmp_trim (возможно файл занят).")
            st.rerun()

    st.divider()

    if "vconcat__processed" not in st.session_state:
        st.session_state["vconcat__processed"] = set()

    uploaded = st.file_uploader(
        "Добавьте видео-файлы (MP4/MOV/MKV/WEBM)",
        type=["mp4", "mov", "mkv", "webm", "m4v"],
        accept_multiple_files=True,
        key=uploader_key,
    )

    if uploaded:
        added = 0
        for uf in uploaded:
            sig = _sig_of_uploaded(uf)
            if sig in st.session_state["vconcat__processed"]:
                continue
            st.session_state["vconcat__processed"].add(sig)

            p = in_dir / f"{uuid.uuid4().hex[:8]}__{safe_filename(uf.name)}"
            p.write_bytes(uf.getbuffer())
            sp = str(p)

            existing_paths = set()
            for it in (state.video.concat_items or []):
                if isinstance(it, dict):
                    existing_paths.add(str(it.get("path", "")).strip())
                else:
                    existing_paths.add(str(it).strip())

            if sp not in existing_paths:
                state.video.concat_items.append(sp)
                added += 1

        if added:
            st.success(f"Добавлено клипов: {added}")

    st.divider()

    # Список + сортировка (list[dict])
    state.video.concat_items = render_sortable_media_list(
        kind="video",
        items=state.video.concat_items,
        list_id=list_id,
        get_duration=lambda p: float(probe_duration_seconds(p) or 0.0),
        allow_preview=True,
        show_shuffle=True,
        preview_mode="panel",
    )

    if not state.video.concat_items:
        st.info("Добавьте хотя бы один клип выше 👆")
        return

    # Реальные пути
    input_paths = []
    for it in state.video.concat_items:
        if isinstance(it, dict) and it.get("path"):
            input_paths.append(str(it["path"]))
        elif isinstance(it, str) and it.strip():
            input_paths.append(it.strip())

    # ── ✂️ Обрезка клипов ─────────────────────────
    st.divider()
    st.subheader("✂️ Обрезка клипов (по секундам)")
    st.caption("Обрезка применяется ДО склейки. Trim start/end = сколько секунд отрезать от начала/конца каждого клипа.")

    trim_map = dict(st.session_state.get("vconcat_trim_map", {}))

    with st.expander("Настроить обрезку для каждого клипа", expanded=True):
        if st.button("↩️ Сбросить обрезку у всех", key="vconcat_trim_reset"):
            trim_map = {}
            st.session_state["vconcat_trim_map"] = trim_map
            st.rerun()

        for idx, p in enumerate(input_paths):
            dur = float(probe_duration_seconds(p) or 0.0)
            prev = trim_map.get(p, {"trim_start": 0.0, "trim_end": 0.0})
            ts0 = float(prev.get("trim_start", 0.0) or 0.0)
            te0 = float(prev.get("trim_end", 0.0) or 0.0)

            st.markdown(f"**{idx+1}) {Path(p).name}**  — длительность ~ {dur:.2f} сек")

            cA, cB = st.columns([1, 1])
            with cA:
                ts = st.number_input(
                    "Trim start (сек)",
                    min_value=0.0,
                    max_value=max(0.0, dur),
                    value=float(ts0),
                    step=0.1,
                    key=f"trim_start__{p}",
                )
            with cB:
                te = st.number_input(
                    "Trim end (сек)",
                    min_value=0.0,
                    max_value=max(0.0, dur),
                    value=float(te0),
                    step=0.1,
                    key=f"trim_end__{p}",
                )

            trim_map[p] = {"trim_start": float(ts), "trim_end": float(te)}

            if dur > 0 and (dur - float(te) - float(ts)) < 0.2:
                st.warning("⚠️ После обрезки клип станет слишком коротким (<0.2 сек). Уменьши обрезку.")

            st.write("")

    st.session_state["vconcat_trim_map"] = trim_map

    # ── Переходы ─────────────────────────────────
    st.divider()
    st.markdown("## 🌫️ Переходы между клипами")
    use_transitions = st.checkbox("Добавить мягкие переходы (xfade)", value=True)
    transition_type = st.selectbox(
        "Тип перехода",
        options=["fade", "wipeleft", "wiperight", "wipeup", "wipedown", "circleopen", "circleclose"],
        index=0,
        disabled=not use_transitions,
    )
    transition_sec = st.slider(
        "Длительность перехода (сек)",
        min_value=0.2,
        max_value=3.0,
        value=1.5,
        step=0.1,
        disabled=not use_transitions,
    )

    out_name = st.text_input("Имя итогового файла", value="merged_video")
    out_path = out_dir / f"{safe_filename(out_name)}.mp4"

    concat_inputs = []
    for p in input_paths:
        t = trim_map.get(p, {"trim_start": 0.0, "trim_end": 0.0})
        concat_inputs.append(
            {"path": p, "trim_start": float(t.get("trim_start", 0.0) or 0.0), "trim_end": float(t.get("trim_end", 0.0) or 0.0)}
        )

    if st.button("🎬 Склеить видео", type="primary", key="vconcat_run"):
        with st.spinner("Обрезаем + нормализуем клипы, затем склеиваем..."):
            result = concat_videos(
                concat_inputs,
                str(out_path),
                transition=transition_type if use_transitions else None,
                transition_sec=float(transition_sec) if use_transitions else 0.0,
            )

        state.video.bg_video_merged = str(Path(result))
        st.success("✅ Готово! Результат ниже в блоке «Результат (склеенный фон)».")

        # ✅ автоудаление tmp_trim после успеха
        if auto_delete_tmp:
            ok = _try_delete_tmp_trim(tmp_trim_dir)
            if ok:
                st.caption("🧹 Временные обрезки (_tmp_trim) удалены автоматически.")
            else:
                st.warning("Не удалось удалить _tmp_trim автоматически (возможно файл занят).")

    if state.video.bg_video_merged and Path(state.video.bg_video_merged).exists():
        st.divider()
        st.subheader("✅ Результат (склеенный фон)")
        st.video(Path(state.video.bg_video_merged).read_bytes())

        st.download_button(
            "⬇️ Скачать MP4",
            data=Path(state.video.bg_video_merged).read_bytes(),
            file_name=Path(state.video.bg_video_merged).name,
            mime="video/mp4",
            key="vconcat_dl",
        )

        if st.button("🎬 Сделать MP4 видео-клип из результата", use_container_width=True, key="vconcat_to_clip"):
            state.video.transferred_bg_video = state.video.bg_video_merged
            st.session_state[PENDING_NAV_KEY] = NAV_CLIP_LABEL
            st.rerun()


render = page_video_concat
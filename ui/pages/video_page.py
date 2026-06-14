from __future__ import annotations

from pathlib import Path
import hashlib
import time
import uuid

import streamlit as st

from core.ffmpeg import probe_duration_seconds, run_cmd, which_ffmpeg
from core.paths import safe_filename
from ui.components.lists import render_sortable_media_list
from ui.state import AppState


PENDING_NAV_KEY = "__pending_nav_choice"



def _sig_of_uploaded(f) -> str:
    size = getattr(f, "size", None)
    if size is None:
        try:
            size = len(f.getbuffer())
        except Exception:
            size = 0
    return f"{f.name}::{int(size)}"



def _save_upload_to_folder(uploaded_file, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_filename(uploaded_file.name)
    dst = folder / f"{uuid.uuid4().hex[:8]}__{name}"
    dst.write_bytes(uploaded_file.getbuffer())
    return str(dst)



def _has_path(items, path: str) -> bool:
    for it in items or []:
        if isinstance(it, dict) and str(it.get("path", "")).strip() == path:
            return True
    return False



def _make_video_preview(*, src_path: str, previews_dir: Path, seconds: float = 7.0, width: int = 320) -> str:
    p = Path(src_path)
    if not p.exists():
        raise FileNotFoundError(src_path)

    try:
        stt = p.stat()
        stamp = f"{stt.st_size}-{int(stt.st_mtime)}"
    except Exception:
        stamp = "0-0"

    key = hashlib.md5(f"{p.resolve()}|{stamp}|{seconds}|{width}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    previews_dir.mkdir(parents=True, exist_ok=True)
    out = previews_dir / f"prev_{key}.mp4"
    if out.exists():
        return str(out)

    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден. Добавь FFmpeg в PATH или задай FFMPEG_PATH.")

    cmd = [
        ffmpeg, "-y",
        "-ss", "0",
        "-t", str(float(seconds)),
        "-i", str(p),
        "-an",
        "-vf", f"scale={int(width)}:-2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "30",
        "-movflags", "+faststart",
        str(out),
    ]
    run_cmd(cmd)
    return str(out)



def _resolve_video_render_api():
    import core.video_render as vr

    fn_three = getattr(vr, "make_three_final_mp4", None)
    sizes = getattr(vr, "SIZES", None)
    fn_one = getattr(vr, "make_final_mp4", None)

    if not callable(fn_three):
        raise RuntimeError("В core/video_render.py нет функции make_three_final_mp4()")
    if not callable(fn_one):
        raise RuntimeError("В core/video_render.py нет функции make_final_mp4()")
    if not isinstance(sizes, dict) or not sizes:
        raise RuntimeError("В core/video_render.py нет словаря SIZES")

    return vr, fn_three, fn_one, sizes



def _reset_list_component(list_id: str) -> None:
    store_key = f"__items__{list_id}"
    preview_key = f"__preview_path__{list_id}"
    st.session_state.pop(store_key, None)
    st.session_state.pop(preview_key, None)



def page_video(state: AppState) -> None:
    st.header("🎬 Создать видеоклип")

    uploads_audio = Path(state.paths.uploads_audio)
    uploads_video = Path(state.paths.uploads_video)
    uploads_images = Path(state.paths.uploads_images)
    previews_dir = Path(state.paths.previews) / "video_previews"
    outputs_mp4 = Path(state.paths.outputs_dir) / "mp4"
    outputs_mp4.mkdir(parents=True, exist_ok=True)

    st.session_state.setdefault("clip_audio_uploader_nonce", 1)
    st.session_state.setdefault("clip_video_uploader_nonce", 1)
    st.session_state.setdefault("clip_img_uploader_nonce", 1)

    st.markdown("### 🧹 Очистка (быстро)")

    cA, cB, cC = st.columns([1, 1, 1])

    with cA:
        if st.button("🧹 Очистить АУДИО", use_container_width=True):
            state.video.audio_items = []
            st.session_state.pop("clip_audio_pick", None)
            st.session_state.pop("clip_proc_audio", None)
            _reset_list_component("clip_audio")
            st.session_state["clip_audio_uploader_nonce"] += 1
            st.rerun()

    with cB:
        if st.button("🧹 Очистить ФОН-ВИДЕО", use_container_width=True):
            state.video.bg_video_items = []
            st.session_state.pop("clip_bg_pick", None)
            st.session_state.pop("clip_proc_video", None)
            state.video.transferred_bg_video = None
            _reset_list_component("clip_bgvideo")
            st.session_state["clip_video_uploader_nonce"] += 1
            st.rerun()

    with cC:
        if st.button("🧹 Очистить КАРТИНКУ-ФОН", use_container_width=True):
            state.video.bg_image_path = None
            st.session_state.pop("clip_proc_img", None)
            st.session_state["clip_img_uploader_nonce"] += 1
            st.rerun()

    st.divider()

    # ─────────────────────────────────────────────
    # Аудио / музыка
    # ─────────────────────────────────────────────
    st.subheader("🎧 Музыка / отдельное аудио")
    st.caption("Этот блок теперь можно использовать и как основной звук, и как музыкальный фон поверх видео с речью.")

    proc_audio = st.session_state.setdefault("clip_proc_audio", set())
    up_audio_key = f"clip_audio_uploader__{st.session_state['clip_audio_uploader_nonce']}"

    up_audio = st.file_uploader(
        "Добавить аудио (можно несколько)",
        type=["mp3", "wav", "flac", "ogg", "m4a"],
        accept_multiple_files=True,
        key=up_audio_key,
    )

    if up_audio:
        for f in up_audio:
            sig = _sig_of_uploaded(f)
            if sig in proc_audio:
                continue
            proc_audio.add(sig)
            p = _save_upload_to_folder(f, uploads_audio)
            if not _has_path(state.video.audio_items, p):
                state.video.audio_items.append({"id": uuid.uuid4().hex[:10], "path": p, "name": Path(p).name, "kind": "audio"})

    audio_paths = [it["path"] for it in state.video.audio_items if isinstance(it, dict) and it.get("path")]
    music_audio_path = None

    if state.video.audio_items:
        state.video.audio_items = render_sortable_media_list(
            kind="audio",
            items=state.video.audio_items,
            list_id="clip_audio",
            allow_preview=True,
            preview_mode="panel",
            allow_move=True,
            allow_delete=True,
            show_shuffle=False,
            get_duration=lambda p: float(probe_duration_seconds(p) or 0.0),
            pick_path_state_key="clip_audio_pick",
        )

        audio_paths = [it["path"] for it in state.video.audio_items if isinstance(it, dict) and it.get("path")]
        if audio_paths:
            if "clip_audio_pick" not in st.session_state or st.session_state["clip_audio_pick"] not in audio_paths:
                st.session_state["clip_audio_pick"] = audio_paths[0]
            music_audio_path = st.session_state["clip_audio_pick"]
    else:
        st.info("Музыку можно не загружать, если ты хочешь оставить только оригинальный звук видео.")

    st.divider()

    # ─────────────────────────────────────────────
    # Фон
    # ─────────────────────────────────────────────
    st.subheader("🧩 Фон")

    bg_kind = st.radio(
        "Выберите тип фона",
        options=["video", "image"],
        format_func=lambda x: "🎞️ Видео" if x == "video" else "🖼️ Картинка",
        horizontal=True,
    )

    bg_path = None

    if bg_kind == "video":
        st.subheader("🎞️ Видео (фон)")

        proc_video = st.session_state.setdefault("clip_proc_video", set())
        up_video_key = f"clip_video_uploader__{st.session_state['clip_video_uploader_nonce']}"

        up_video = st.file_uploader(
            "Добавить видео (можно несколько)",
            type=["mp4", "mov", "mkv", "webm", "m4v"],
            accept_multiple_files=True,
            key=up_video_key,
        )

        uploaded_any_video_now = False
        if up_video:
            added = 0
            for f in up_video:
                sig = _sig_of_uploaded(f)
                if sig in proc_video:
                    continue
                proc_video.add(sig)
                p = _save_upload_to_folder(f, uploads_video)
                if not _has_path(state.video.bg_video_items, p):
                    state.video.bg_video_items.append({"id": uuid.uuid4().hex[:10], "path": p, "name": Path(p).name, "kind": "video"})
                    added += 1
            if added:
                uploaded_any_video_now = True

        if state.video.transferred_bg_video and Path(state.video.transferred_bg_video).exists():
            if not _has_path(state.video.bg_video_items, state.video.transferred_bg_video):
                state.video.bg_video_items.insert(
                    0,
                    {"id": "transfer", "path": state.video.transferred_bg_video, "name": Path(state.video.transferred_bg_video).name, "kind": "video"},
                )

        if not state.video.bg_video_items:
            st.info("Добавь хотя бы одно видео (или передай результат из «Склейка видео»).")
            return

        state.video.bg_video_items = render_sortable_media_list(
            kind="video",
            items=state.video.bg_video_items,
            list_id="clip_bgvideo",
            allow_preview=True,
            preview_mode="panel",
            allow_move=True,
            allow_delete=True,
            show_shuffle=False,
            get_duration=lambda p: float(probe_duration_seconds(p) or 0.0),
            pick_path_state_key="clip_bg_pick",
        )

        video_paths = [it["path"] for it in state.video.bg_video_items if isinstance(it, dict) and it.get("path")]
        if not video_paths:
            st.info("Список видео пуст. Добавь видео заново.")
            return

        if "clip_bg_pick" not in st.session_state or st.session_state["clip_bg_pick"] not in video_paths:
            st.session_state["clip_bg_pick"] = video_paths[0]
        bg_path = st.session_state["clip_bg_pick"]

        if uploaded_any_video_now and state.video.transferred_bg_video:
            state.video.transferred_bg_video = None

        with st.expander("👀 Мини-превью видео (первые ~7 сек)", expanded=True):
            cols = st.columns(3)
            for i, p in enumerate(video_paths):
                with cols[i % 3]:
                    st.caption(Path(p).name)
                    try:
                        prev = _make_video_preview(src_path=p, previews_dir=previews_dir, seconds=7.0, width=320)
                        st.video(Path(prev).read_bytes())
                    except Exception as e:
                        st.warning(f"Не удалось сделать превью: {e}")

    else:
        st.subheader("🖼️ Картинка-фон")

        proc_img = st.session_state.setdefault("clip_proc_img", set())
        up_img_key = f"clip_img_uploader__{st.session_state['clip_img_uploader_nonce']}"

        up_img = st.file_uploader(
            "Загрузить картинку (jpg/png/webp)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=False,
            key=up_img_key,
        )
        if up_img:
            sig = _sig_of_uploaded(up_img)
            if sig not in proc_img:
                proc_img.add(sig)
                p = _save_upload_to_folder(up_img, uploads_images)
                state.video.bg_image_path = p

        if not state.video.bg_image_path or not Path(state.video.bg_image_path).exists():
            st.info("Загрузи картинку-фон (или очисть и выбери другую).")
            return

        bg_path = state.video.bg_image_path
        st.image(Path(bg_path).read_bytes(), caption=Path(bg_path).name, use_container_width=True)

    st.divider()

    # ─────────────────────────────────────────────
    # Смешивание звука
    # ─────────────────────────────────────────────
    st.subheader("🔊 Звук")

    if bg_kind == "video":
        keep_original_video_audio = st.checkbox(
            "Сохранить оригинальный звук видео",
            value=True,
            help="Полезно для роликов с речью: голос и исходный звук видео останутся в итоговом MP4.",
        )
    else:
        keep_original_video_audio = False
        st.caption("Для картинки оригинального звука нет — используется только выбранное аудио.")

    if bg_kind == "video":
        add_music_overlay = st.checkbox(
            "Добавить музыкальный фон",
            value=bool(music_audio_path),
            help="Наложит выбранный аудиофайл поверх оригинального звука видео.",
        )
    else:
        add_music_overlay = True

    bg_audio_volume_pct = 100
    music_volume_pct = 25 if bg_kind == "video" else 100

    if keep_original_video_audio:
        bg_audio_volume_pct = st.slider(
            "Громкость голоса / оригинального звука видео (%)",
            min_value=0,
            max_value=200,
            value=100,
            step=5,
        )

    if add_music_overlay:
        music_volume_pct = st.slider(
            "Громкость музыки (%)",
            min_value=0,
            max_value=200,
            value=25 if bg_kind == "video" else 100,
            step=5,
        )

    selected_audio_path = music_audio_path if add_music_overlay else None

    if add_music_overlay and not selected_audio_path:
        st.warning("Включено добавление музыки, но аудиофайл не выбран. Загрузите музыку или выключите эту галку.")
        return

    if bg_kind == "image" and not selected_audio_path:
        st.warning("Для клипа с картинкой обязательно нужно выбрать аудио.")
        return

    if bg_kind == "video" and not keep_original_video_audio and not selected_audio_path:
        st.warning("Сейчас выключены и оригинальный звук видео, и музыка. Итоговый клип получится без звука.")
        return

    if bg_kind == "video" and keep_original_video_audio and add_music_overlay:
        st.info("Итог: сохранится оригинальный звук видео и поверх него добавится музыкальный фон.")
    elif bg_kind == "video" and keep_original_video_audio:
        st.info("Итог: сохранится только оригинальный звук видео.")
    elif bg_kind == "video" and add_music_overlay:
        st.info("Итог: в клипе будет только выбранная музыка, без исходного звука видео.")

    st.divider()

    # ─────────────────────────────────────────────
    # Рендер
    # ─────────────────────────────────────────────
    st.subheader("🎛️ Рендер")

    vr, make_three_final_mp4, make_final_mp4, SIZES = _resolve_video_render_api()
    sizes_list = list(SIZES.items())

    cR1, cR2, cR3 = st.columns([2, 1, 1])

    with cR1:
        size_label = st.selectbox(
            "Размер (сгенерировать 1 файл)",
            options=[k for k, _ in sizes_list],
            index=0,
        )
        size_wh = dict(sizes_list)[size_label]

    with cR2:
        fps = st.selectbox("FPS", options=[24, 25, 30, 60], index=2)
    with cR3:
        crf = st.slider("Качество (CRF, меньше = лучше)", 16, 28, 20, 1)

    out_prefix = st.text_input("Префикс имени файла", value="VIDEO")

    prog = st.progress(0)
    prog_txt = st.empty()

    if bg_kind == "video" and keep_original_video_audio:
        total_sec = float(probe_duration_seconds(bg_path) or 0.0)
    else:
        total_sec = float(probe_duration_seconds(selected_audio_path) or 0.0) if selected_audio_path else 0.0

    last_emit_t = time.time()
    last_pct = -1

    def progress_cb(frac: float, out_sec: float):
        nonlocal last_emit_t, last_pct
        now = time.time()

        if now - last_emit_t < 0.25:
            return

        frac2 = float(max(0.0, min(1.0, frac)))
        pct = int(frac2 * 100)

        if pct == last_pct:
            return

        last_pct = pct
        last_emit_t = now

        prog.progress(pct)
        if total_sec > 0:
            prog_txt.write(f"Прогресс: {pct}%  •  {out_sec:.1f} / {total_sec:.1f} сек")
        else:
            prog_txt.write(f"Прогресс: {pct}%")

    if st.button("🎬 Сгенерировать 1 размер (с прогрессом)", type="primary"):
        out_dir = str(outputs_mp4)
        tag = "16x9" if "16:9" in size_label else ("1x1" if "1:1" in size_label else "9x16")

        bg_image_path = bg_path if bg_kind == "image" else None
        bg_video_path = bg_path if bg_kind == "video" else None

        prog.progress(0)
        prog_txt.write("Прогресс: 0%")
        last_emit_t = time.time()
        last_pct = -1

        with st.status("Рендерю видео…", expanded=True):
            outp = make_final_mp4(
                audio_path=selected_audio_path,
                output_dir=out_dir,
                size=size_wh,
                bg_image_path=bg_image_path,
                bg_video_path=bg_video_path,
                bg_video_list=None,
                fps=int(fps),
                crf=int(crf),
                preset="veryfast",
                prefix=safe_filename(out_prefix) or "VIDEO",
                tag=tag,
                keep_bg_audio=bool(keep_original_video_audio),
                bg_audio_volume=float(bg_audio_volume_pct) / 100.0,
                music_volume=float(music_volume_pct) / 100.0,
                progress_cb=progress_cb,
            )

        prog.progress(100)
        prog_txt.write("Готово ✅")

        state.video.last_rendered = str(outp)
        state.video.last_mp4_path = str(outp)
        st.success("✅ Видео готово!")
        st.video(Path(outp).read_bytes())

        st.download_button(
            "⬇️ Скачать MP4",
            data=Path(outp).read_bytes(),
            file_name=Path(outp).name,
            mime="video/mp4",
            key="clip_dl_last",
        )


render = page_video

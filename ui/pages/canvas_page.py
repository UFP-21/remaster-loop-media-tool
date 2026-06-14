from __future__ import annotations

from pathlib import Path
import os
import uuid

import streamlit as st

from core.canvas_tools import make_canvas_from_image, make_vertical_from_video, output_info
from core.paths import safe_filename
from ui.state import AppState

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm"}



def _open_folder(path: Path) -> None:
    try:
        if path.exists():
            os.startfile(str(path))  # noqa
    except Exception as e:
        st.warning(f"Не удалось открыть папку: {e}")



def _save_upload(uploaded_file, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{uuid.uuid4().hex[:10]}__{safe_filename(uploaded_file.name)}"
    dst.write_bytes(uploaded_file.getbuffer())
    return str(dst)



def _default_stem(path_str: str, fallback: str) -> str:
    p = Path(str(path_str))
    stem = p.stem.strip()
    return stem or fallback



def _render_output_result(out_path: str, *, label: str) -> None:
    p = Path(str(out_path))
    if not p.exists():
        return

    st.success("✅ Готово")
    st.video(str(p))
    st.caption(f"{label}: {p}")
    st.write(f"Размер файла: **{output_info(str(p))}**")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button(
            "⬇️ Скачать MP4",
            data=p.read_bytes(),
            file_name=p.name,
            mime="video/mp4",
            key=f"dl_{p.name}",
        )
    with c2:
        if st.button("📂 Открыть папку outputs/canvas", key=f"open_{p.name}"):
            _open_folder(p.parent)



def _render_image_to_canvas(state: AppState, out_dir: Path) -> None:
    st.markdown("## A) Из картинки (обложки)")
    st.caption("JPG / JPEG / PNG / WEBP → бесшовный 9:16 Spotify Canvas")

    up = st.file_uploader(
        "Выберите картинку",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=False,
        key="canvas_image_uploader",
    )

    src_path = None
    if up:
        src_path = _save_upload(up, Path(state.paths.uploads_images))
        st.image(str(src_path), caption=Path(src_path).name, use_container_width=True)

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        seconds = st.slider("Длительность (сек)", 4, 8, 5, 1, key="canvas_img_sec")
    with c2:
        intensity_ru = st.selectbox(
            "Интенсивность zoom",
            options=["Low", "Medium", "High"],
            index=0,
            key="canvas_img_intensity",
        )
    with c3:
        out_name_custom = st.text_input(
            "Имя файла (необязательно, без .mp4)",
            value="",
            key="canvas_img_out_name",
        ).strip()

    intensity = {
        "Low": "low",
        "Medium": "medium",
        "High": "high",
    }[intensity_ru]

    if st.button("🎞️ Сделать Canvas из обложки", type="primary", key="canvas_img_run"):
        if not src_path:
            st.error("Файл не найден: сначала выберите картинку.")
            return

        try:
            stem = out_name_custom or _default_stem(src_path, "cover")
            out_path = out_dir / f"{safe_filename(stem)}_canvas_9x16_{int(seconds)}s.mp4"
            make_canvas_from_image(
                input_path=str(src_path),
                out_path=str(out_path),
                seconds=float(seconds),
                fps=30,
                intensity=intensity,
            )
            _render_output_result(str(out_path), label="Canvas MP4")
        except FileNotFoundError as e:
            st.error(str(e))
        except RuntimeError as e:
            st.error(f"Не удалось конвертировать: {e}")
        except Exception as e:
            st.error(f"Не удалось конвертировать: {e}")



def _render_video_to_vertical(state: AppState, out_dir: Path) -> None:
    st.markdown("## B) Из видео (привести к 9:16)")
    st.caption("MP4 / MOV / WEBM → 1080×1920, 30 fps, yuv420p")

    up = st.file_uploader(
        "Выберите видео",
        type=["mp4", "mov", "webm"],
        accept_multiple_files=False,
        key="canvas_video_uploader",
    )

    src_path = None
    if up:
        src_path = _save_upload(up, Path(state.paths.uploads_video))
        st.video(str(src_path))

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        trim_enabled = st.checkbox("Обрезать по длительности", value=True, key="canvas_vid_trim_enabled")
    with c2:
        seconds = st.slider("Длительность (сек)", 4, 10, 6, 1, key="canvas_vid_sec", disabled=not trim_enabled)
    with c3:
        mode_ru = st.selectbox(
            "Режим кадрирования",
            options=["Fill + center crop", "Fit + blur background"],
            index=0,
            key="canvas_vid_mode",
        )

    out_name_custom = st.text_input(
        "Имя файла (необязательно, без .mp4)",
        value="",
        key="canvas_vid_out_name",
    ).strip()

    mode = "crop" if mode_ru.startswith("Fill") else "blur"

    if st.button("📐 Сделать 9:16 версию", type="primary", key="canvas_vid_run"):
        if not src_path:
            st.error("Файл не найден: сначала выберите видео.")
            return

        try:
            stem = out_name_custom or _default_stem(src_path, "video")
            out_path = out_dir / f"{safe_filename(stem)}_9x16.mp4"
            make_vertical_from_video(
                input_path=str(src_path),
                out_path=str(out_path),
                seconds=float(seconds) if trim_enabled else None,
                fps=30,
                mode=mode,
            )
            _render_output_result(str(out_path), label="9:16 MP4")
        except FileNotFoundError as e:
            st.error(str(e))
        except RuntimeError as e:
            st.error(f"Не удалось конвертировать: {e}")
        except Exception as e:
            st.error(f"Не удалось конвертировать: {e}")



def _collect_batch_inputs(folder_path: str, do_images: bool, do_videos: bool) -> tuple[list[Path], list[Path]]:
    root = Path(folder_path)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Папка не найдена: {folder_path}")

    images: list[Path] = []
    videos: list[Path] = []

    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if do_images and ext in _IMAGE_EXTS:
            images.append(p)
        if do_videos and ext in _VIDEO_EXTS:
            videos.append(p)
    return images, videos



def _render_batch_tools(out_dir: Path) -> None:
    st.markdown("## C) Пакетная обработка папки (опционально)")
    st.caption("В Streamlit надёжнее указывать путь к папке вручную, чем пытаться открыть системный выбор папки.")

    folder_path = st.text_input(
        "Путь к папке",
        value=r"D:\Covers",
        key="canvas_batch_folder",
    ).strip()

    c1, c2 = st.columns([1, 1])
    with c1:
        do_images = st.checkbox("Обработать все картинки → Canvas", value=True, key="canvas_batch_do_images")
    with c2:
        do_videos = st.checkbox("Обработать все видео → 9:16", value=False, key="canvas_batch_do_videos")

    c3, c4, c5 = st.columns([1, 1, 1])
    with c3:
        img_seconds = st.slider("Canvas сек", 4, 8, 5, 1, key="canvas_batch_img_sec")
    with c4:
        img_intensity_ru = st.selectbox("Zoom", ["Low", "Medium", "High"], index=0, key="canvas_batch_img_int")
    with c5:
        vid_seconds = st.slider("Видео сек", 4, 10, 6, 1, key="canvas_batch_vid_sec")

    mode_ru = st.selectbox(
        "Видео режим",
        ["Fill + center crop", "Fit + blur background"],
        index=0,
        key="canvas_batch_vid_mode",
    )

    if st.button("🚀 Запустить пакетную обработку", key="canvas_batch_run"):
        try:
            images, videos = _collect_batch_inputs(folder_path, do_images=do_images, do_videos=do_videos)
            total = len(images) + len(videos)
            if total == 0:
                st.warning("В указанной папке не найдено подходящих файлов.")
                return

            progress = st.progress(0)
            log_box = st.empty()
            done = 0
            errors: list[str] = []

            img_intensity = {"Low": "low", "Medium": "medium", "High": "high"}[img_intensity_ru]
            vid_mode = "crop" if mode_ru.startswith("Fill") else "blur"

            for p in images:
                try:
                    out_path = out_dir / f"{safe_filename(p.stem)}_canvas_9x16_{int(img_seconds)}s.mp4"
                    make_canvas_from_image(str(p), str(out_path), seconds=float(img_seconds), fps=30, intensity=img_intensity)
                    log_box.info(f"Готово (image): {p.name} → {out_path.name}")
                except Exception as e:
                    errors.append(f"{p.name}: {e}")
                done += 1
                progress.progress(int(done / total * 100))

            for p in videos:
                try:
                    out_path = out_dir / f"{safe_filename(p.stem)}_9x16.mp4"
                    make_vertical_from_video(str(p), str(out_path), seconds=float(vid_seconds), fps=30, mode=vid_mode)
                    log_box.info(f"Готово (video): {p.name} → {out_path.name}")
                except Exception as e:
                    errors.append(f"{p.name}: {e}")
                done += 1
                progress.progress(int(done / total * 100))

            if errors:
                st.warning("Пакетная обработка завершена с ошибками.")
                for err in errors:
                    st.code(err)
            else:
                st.success("✅ Пакетная обработка завершена без ошибок.")

            if st.button("📂 Открыть папку outputs/canvas", key="canvas_batch_open"):
                _open_folder(out_dir)

        except FileNotFoundError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Не удалось конвертировать: {e}")



def page_canvas(state: AppState) -> None:
    st.header("🎞️ Spotify Canvas (9:16)")
    st.caption("Создание бесшовного Canvas из обложки и приведение сторонних видео к вертикальному формату 9:16.")

    out_dir = Path(state.paths.outputs_dir) / "canvas"
    out_dir.mkdir(parents=True, exist_ok=True)

    top1, top2 = st.columns([1, 1])
    with top1:
        if st.button("📂 Открыть папку canvas", key="canvas_open_out_dir"):
            _open_folder(out_dir)
    with top2:
        st.caption(f"Папка результатов: {out_dir}")

    tab1, tab2, tab3 = st.tabs([
        "A) Из картинки",
        "B) Из видео",
        "C) Пакетно",
    ])

    with tab1:
        _render_image_to_canvas(state, out_dir)

    with tab2:
        _render_video_to_vertical(state, out_dir)

    with tab3:
        _render_batch_tools(out_dir)


render = page_canvas

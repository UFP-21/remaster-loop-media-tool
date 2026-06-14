from __future__ import annotations

from pathlib import Path
from typing import List, Tuple
import time
import shutil

import streamlit as st

from ui.state import AppState


# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────
def _dir_size_bytes(folder: Path) -> int:
    try:
        if not folder.exists():
            return 0
        total = 0
        for p in folder.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except Exception:
                pass
        return total
    except Exception:
        return 0


def _count_files(folder: Path) -> int:
    try:
        if not folder.exists():
            return 0
        c = 0
        for p in folder.rglob("*"):
            try:
                if p.is_file():
                    c += 1
            except Exception:
                pass
        return c
    except Exception:
        return 0


def _fmt_bytes(n: int) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    kb = n / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.2f} MB"
    gb = mb / 1024
    return f"{gb:.2f} GB"


def _safe_list_dirs(base: Path) -> List[Path]:
    if not base.exists():
        return []
    out = []
    try:
        for p in base.iterdir():
            if p.is_dir():
                out.append(p)
    except Exception:
        pass
    return out


def _delete_contents(
    folder: Path,
    *,
    older_than_days: int | None = None,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """
    Удаляет содержимое папки (только файлы).
    Возвращает (files_deleted, bytes_deleted).
    """
    if not folder.exists():
        return (0, 0)

    cutoff_ts = None
    if older_than_days is not None and older_than_days > 0:
        cutoff_ts = time.time() - (older_than_days * 86400)

    files_deleted = 0
    bytes_deleted = 0

    for p in folder.rglob("*"):
        try:
            if not p.is_file():
                continue

            if cutoff_ts is not None:
                try:
                    if p.stat().st_mtime >= cutoff_ts:
                        continue
                except Exception:
                    pass

            try:
                sz = p.stat().st_size
            except Exception:
                sz = 0

            if not dry_run:
                p.unlink(missing_ok=True)

            files_deleted += 1
            bytes_deleted += int(sz or 0)
        except Exception:
            pass

    return (files_deleted, bytes_deleted)


def _delete_dir_tree(folder: Path, *, dry_run: bool = False) -> Tuple[int, int]:
    """
    Удаляет папку целиком (session_*).
    Возвращает (files_deleted, bytes_deleted) оценочно.
    """
    if not folder.exists() or not folder.is_dir():
        return (0, 0)

    files = _count_files(folder)
    bytes_ = _dir_size_bytes(folder)

    if not dry_run:
        try:
            shutil.rmtree(folder, ignore_errors=False)
        except Exception:
            # если что-то удерживается — пробуем более мягко
            shutil.rmtree(folder, ignore_errors=True)

    return (files, bytes_)


def _work_root_from_state(state: AppState) -> Path:
    """
    Корневая рабочая папка (RemasterLoop_Work) из путей state.
    Обычно uploads_audio = D:\\RemasterLoop_Work\\uploads_audio → parent = RemasterLoop_Work
    """
    candidates = []
    try:
        candidates.append(Path(state.paths.uploads_audio))
    except Exception:
        pass
    try:
        candidates.append(Path(state.paths.uploads_video))
    except Exception:
        pass
    try:
        candidates.append(Path(state.paths.outputs_dir))
    except Exception:
        pass

    for c in candidates:
        if c and c.exists():
            return c.parent if c.name.lower().startswith(("uploads", "outputs", "previews")) else c

    try:
        c = Path(state.paths.uploads_audio)
        return c.parent
    except Exception:
        return Path.cwd()


def _reset_app_lists(state: AppState) -> None:
    """Сбрасываем списки в state, чтобы UI не ссылался на удалённые файлы."""
    try:
        state.mastering.items = []
        state.mastering.last_output_path = None
    except Exception:
        pass
    try:
        state.concat.items = []
        state.concat.result_path = None
    except Exception:
        pass
    try:
        state.video.concat_items = []
    except Exception:
        pass
    try:
        state.video.bg_video_merged = None
    except Exception:
        pass
    try:
        state.video.audio_items = []
    except Exception:
        pass
    try:
        state.loop.items = []
    except Exception:
        pass


def page_cleanup(state: AppState) -> None:
    st.header("🧹 Глубокая очистка Work-папки")

    work_root = _work_root_from_state(state)
    uploads_audio = Path(state.paths.uploads_audio)
    uploads_video = Path(state.paths.uploads_video)
    outputs = Path(state.paths.outputs_dir)

    st.caption(f"Рабочая папка: **{work_root}**")
    st.caption("Очищает только рабочие файлы (uploads/outputs/previews + session_*). Код проекта не трогает.")

    st.divider()

    # ── Сводка размеров ──────────────────────────
    st.subheader("📦 Что сейчас занимает место")

    rows: List[Tuple[str, Path, int]] = []
    rows.append(("uploads_audio", uploads_audio, _dir_size_bytes(uploads_audio)))
    rows.append(("uploads_video", uploads_video, _dir_size_bytes(uploads_video)))
    rows.append(("outputs", outputs, _dir_size_bytes(outputs)))

    out_children = _safe_list_dirs(outputs)
    for d in sorted(out_children, key=lambda x: x.name.lower()):
        rows.append((f"outputs/{d.name}", d, _dir_size_bytes(d)))

    # session_* (суммарно)
    session_dirs = [p for p in _safe_list_dirs(work_root) if p.name.startswith("session_")]
    session_total_bytes = sum(_dir_size_bytes(p) for p in session_dirs)
    rows.append((f"session_* (папок: {len(session_dirs)})", work_root / "session_*", session_total_bytes))

    for label, path, sz in rows:
        c1, c2, c3 = st.columns([3, 7, 2])
        with c1:
            st.write(label)
        with c2:
            st.code(str(path), language="text")
        with c3:
            st.write(_fmt_bytes(sz))

    if session_dirs:
        with st.expander("Показать список session_*"):
            for p in sorted(session_dirs, key=lambda x: x.name.lower()):
                st.write(f"- {p} — {_fmt_bytes(_dir_size_bytes(p))}")

    st.divider()

    # ── Настройки ────────────────────────────────
    st.subheader("⚙️ Настройки очистки")

    cA, cB = st.columns([1, 1])
    with cA:
        older_mode = st.checkbox("Удалять только старые файлы", value=False)
    with cB:
        dry_run = st.checkbox("Только посчитать (без удаления)", value=False)

    older_days = None
    if older_mode:
        older_days = st.slider("Удалить файлы старше (дней)", 1, 60, 7, 1)

    st.divider()

    # ── Что чистим ───────────────────────────────
    st.subheader("🧹 Что очистить")

    clear_uploads_audio = st.checkbox("✅ uploads_audio (загруженные аудиофайлы)", value=True)
    clear_uploads_video = st.checkbox("✅ uploads_video (загруженные видео)", value=False)

    st.markdown("**outputs:**")
    clear_outputs_all = st.checkbox("✅ outputs целиком (все результаты)", value=False)
    clear_outputs_previews = st.checkbox("✅ outputs/mastering_previews (превью мастеринга)", value=True)
    clear_outputs_mastering = st.checkbox("✅ outputs/mastering (результаты мастеринга)", value=False)
    clear_outputs_concat = st.checkbox("✅ outputs/concat (склейка треков)", value=False)
    clear_outputs_video = st.checkbox("✅ outputs/video_concat + clip (видео результаты)", value=False)

    # ✅ По твоему требованию: удаляем ВСЕ session_* без вариантов
    st.markdown("**session_***:")
    st.info("🧨 По умолчанию будет удалено ВСЁ: все папки session_* целиком (без вариантов).")
    delete_all_sessions = True  # фиксировано

    reset_lists = st.checkbox("Сбросить списки в приложении (после очистки)", value=True)

    st.divider()

    # ── Подтверждение ────────────────────────────
    st.subheader("🔒 Подтверждение")
    st.warning("Удаление необратимо. Чтобы выполнить, введи слово: DELETE")

    confirm = st.text_input("Подтверждение", value="", placeholder="DELETE")
    can_run = confirm.strip().upper() == "DELETE"

    # ── Выполнить ────────────────────────────────
    if st.button("🧨 Выполнить очистку", type="primary", disabled=not can_run):
        plan: List[Path] = []

        if clear_uploads_audio:
            plan.append(uploads_audio)
        if clear_uploads_video:
            plan.append(uploads_video)

        if clear_outputs_all:
            plan.append(outputs)
        else:
            if clear_outputs_previews:
                plan.append(outputs / "mastering_previews")
            if clear_outputs_mastering:
                plan.append(outputs / "mastering")
            if clear_outputs_concat:
                plan.append(outputs / "concat")
            if clear_outputs_video:
                plan.append(outputs / "video_concat")
                plan.append(outputs / "clip")
                plan.append(outputs / "video")

        # dedup
        uniq: List[Path] = []
        seen = set()
        for p in plan:
            ps = str(p)
            if ps in seen:
                continue
            seen.add(ps)
            uniq.append(p)

        total_files = 0
        total_bytes = 0

        with st.status("Очищаю…", expanded=True) as status:
            # 1) чистим выбранные папки (только файлы)
            for folder in uniq:
                folder.mkdir(parents=True, exist_ok=True)
                f, b = _delete_contents(folder, older_than_days=older_days, dry_run=dry_run)
                total_files += f
                total_bytes += b
                st.write(f"• {folder} → удалено файлов: {f}, освобождено: {_fmt_bytes(b)}")

            # 2) удаляем session_* целиком
            if delete_all_sessions and session_dirs:
                st.write("—")
                st.write("🧨 Удаляю все session_* …")
                for sdir in sorted(session_dirs, key=lambda x: x.name.lower()):
                    f, b = _delete_dir_tree(sdir, dry_run=dry_run)
                    total_files += f
                    total_bytes += b
                    st.write(f"• {sdir} → удалено файлов: {f}, освобождено: {_fmt_bytes(b)}")

            if reset_lists and not dry_run:
                _reset_app_lists(state)

            if dry_run:
                status.update(label="Готово (только расчёт).", state="complete")
                st.success(f"План: удалилось бы файлов: {total_files}, освободилось бы: {_fmt_bytes(total_bytes)}")
            else:
                status.update(label="Очистка выполнена.", state="complete")
                st.success(f"Удалено файлов: {total_files}. Освобождено: {_fmt_bytes(total_bytes)}")
                st.info("Если ты удалил session_*, а они использовались текущей сессией — просто перезапусти Streamlit.")

        if not dry_run:
            st.rerun()

    st.divider()
    with st.expander("ℹ️ Что это удаляет и что НЕ удаляет"):
        st.write(
            "- Удаляет **файлы** внутри выбранных папок (uploads/outputs).\n"
            "- Удаляет **все папки session_*** целиком (по твоему требованию).\n"
            "- **Не удаляет код проекта** (папка D:\\PROJECT\\...).\n"
            "- 'Только посчитать' — ничего не удаляет, просто показывает объёмы."
        )


render = page_cleanup
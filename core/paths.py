# core/paths.py
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


# ────────────────────────────────────────────────────────────────
# Naming helpers (единое правило имён)
# ────────────────────────────────────────────────────────────────

_UUID_PREFIX_RE = re.compile(r"^(?P<prefix>[0-9a-fA-F]{6,64})__(?P<rest>.+)$")


def strip_uuid_prefix(filename: str) -> str:
    """Убирает технический префикс вида `8d8109...__ИмяФайла.ext`.

    В проекте мы часто сохраняем uploads как:
        <uuid/hex>__<original_name>

    Для UI и итоговых имён (outputs) пользователь должен видеть
    «человеческое» имя без префикса.
    """
    name = (filename or "").strip()
    if not name:
        return ""

    m = _UUID_PREFIX_RE.match(name)
    if not m:
        return name
    return m.group("rest")


def pretty_filename_from_path(path: str | Path) -> str:
    """Возвращает «человеческое» имя файла из пути (без uuid__ префикса)."""
    p = Path(str(path))
    return strip_uuid_prefix(p.name)


def ensure_unique_path(path: Path) -> Path:
    """Если файл уже существует — добавляет суффикс (2), (3)… перед расширением."""
    path = Path(path)
    if not path.exists():
        return path

    parent = path.parent
    stem = path.stem
    suf = path.suffix

    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suf}"
        if not cand.exists():
            return cand
        i += 1


def build_output_name(
    *,
    source_path: str | Path | None = None,
    base_name: str | None = None,
    tag: str,
    ext: str = ".wav",
) -> str:
    """Строит предсказуемое имя результата.

    Правило:
        <BASE> — <TAG><EXT>

    где BASE берётся либо из base_name (если задано), либо из имени source_path.
    """
    if base_name and str(base_name).strip():
        base = str(base_name).strip()
    else:
        if source_path is None:
            base = "Result"
        else:
            human = pretty_filename_from_path(source_path)
            base = Path(human).stem

    base = safe_filename(base) or "Result"
    return f"{base} — {safe_filename(tag)}{ext}"


def safe_filename(name: str, max_len: int = 160) -> str:
    """
    Делает имя файла безопасным для Windows/FS:
    - убирает запрещённые символы <>:"/\\|?*
    - сводит пробелы
    - режет длину
    """
    name = (name or "").strip()
    if not name:
        return "file"

    # запретные символы Windows
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()

    # если вдруг имя стало пустым
    if not name:
        return "file"

    if len(name) > max_len:
        base = name[:max_len].rstrip()
        name = base

    return name


def get_work_root() -> Path:
    """
    Единая корневая папка для временных/выходных файлов.
    По требованию: стараемся работать на диске D:, чтобы не забивать системный TEMP.
    """
    candidates = [
        Path(r"D:\RemasterLoop_Work"),
        Path(r"D:\Temp\RemasterLoop_Work"),
        Path(tempfile.gettempdir()) / "RemasterLoop_Work",
    ]

    for p in candidates:
        try:
            p.mkdir(parents=True, exist_ok=True)
            # проверка записи
            test = p / "_write_test.tmp"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return p
        except Exception:
            continue

    # крайний случай — текущая папка проекта
    p = Path.cwd() / "RemasterLoop_Work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_old_runs(base_dir: Path, *, keep_days: int = 7, keep_last: int = 50) -> None:
    """Удаляет старые временные папки (run_... / job_...).

    Это защищает диск от засорения, если пользователь много раз
    загружает файлы и запускает рендер.

    keep_days:
        удаляем папки старше N дней
    keep_last:
        дополнительно ограничиваем количество последних папок
    """
    try:
        base_dir = Path(base_dir)
        if not base_dir.exists():
            return

        # Берём только папки-"запуски"
        dirs = [p for p in base_dir.iterdir() if p.is_dir() and (p.name.startswith('run_') or p.name.startswith('job_'))]
        if not dirs:
            return

        # Сортируем по времени изменения (новые в конце)
        dirs.sort(key=lambda p: p.stat().st_mtime)

        import time
        now = time.time()
        cutoff = now - (keep_days * 86400)

        # 1) Сначала удаляем старше keep_days
        to_delete = [p for p in dirs if p.stat().st_mtime < cutoff]

        # 2) Затем удаляем "лишние" сверх keep_last (из самых старых)
        remain = [p for p in dirs if p not in to_delete]
        if len(remain) > keep_last:
            to_delete.extend(remain[: max(0, len(remain) - keep_last)])

        for p in to_delete:
            try:
                import shutil
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        # Никогда не ломаем приложение из-за уборки
        pass


def new_job_dir(prefix: str = "job") -> Path:
    """
    Создаём отдельную папку под один запуск/операцию.
    """
    root = get_work_root()
    # делаем уникальную подпапку
    d = Path(tempfile.mkdtemp(prefix=f"{prefix}_", dir=str(root)))
    return d


# --- совместимость с более ранними версиями кода/UI ---
def new_session_dir(prefix: str = "session") -> Path:
    """Создать папку для текущей сессии работы.

    В UI (Streamlit) удобнее оперировать термином "session".
    В ядре мы используем `new_job_dir()`, но оставляем этот алиас,
    чтобы импорты не ломались при обновлениях.
    """
    return new_job_dir(prefix=prefix)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload_to_workdir(uploaded_file, work_dir: Path) -> Path:
    """Сохраняет объект Streamlit UploadedFile в рабочую папку.

    Args:
        uploaded_file: объект, который возвращает st.file_uploader (имеет .name и .getbuffer()).
        work_dir: папка, куда сохранять.

    Returns:
        Path до сохранённого файла.
    """
    work_dir = ensure_dir(Path(work_dir))

    # Streamlit UploadedFile обычно имеет .name и .getbuffer()
    name = getattr(uploaded_file, "name", None) or "upload"
    safe = safe_filename(str(name))

    dst = work_dir / safe

    # если уже существует — делаем уникальным
    if dst.exists():
        stem = dst.stem
        suf = dst.suffix
        i = 1
        while True:
            cand = work_dir / f"{stem}__{i}{suf}"
            if not cand.exists():
                dst = cand
                break
            i += 1

    # пишем байты
    data = None
    if hasattr(uploaded_file, "getbuffer"):
        data = uploaded_file.getbuffer()
    elif hasattr(uploaded_file, "read"):
        data = uploaded_file.read()
    else:
        raise TypeError("uploaded_file должен быть Streamlit UploadedFile (getbuffer/read)")

    with open(dst, "wb") as f:
        f.write(data)

    return dst
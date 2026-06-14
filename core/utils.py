from __future__ import annotations

from pathlib import Path
import zipfile


def zip_files(zip_path: str, file_paths: list[str]) -> str:
    """
    Упаковка списка файлов в zip. Возвращает путь к zip.
    """
    zp = Path(zip_path)
    zp.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            p = Path(fp)
            if p.exists():
                zf.write(p, arcname=p.name)
    return str(zp)

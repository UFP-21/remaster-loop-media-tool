


### 2) `ui/state.py` (ГЛАВНЫЙ фикс дублей: везде `list[dict]`, никаких `list[str]`)

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

import streamlit as st

from core.paths import get_work_root, cleanup_old_runs


# ──────────────────────────────────────────────────────────────
# ВАЖНО (главный фикс багов rerun):
#  - Внутри state списки медиа храним ЕДИНООБРАЗНО как list[dict]
#    формат элемента:
#      {"id": str, "path": str, "name": str, "kind": "audio"|"video"|"image"}
#  - НИГДЕ не храним list[str] как «основной» формат,
#    иначе при миграциях/рендерах начинается дублирование и залипания.
# ──────────────────────────────────────────────────────────────

def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _ensure_dirs(work_dir: Path) -> Dict[str, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    d = {
        "uploads_audio": work_dir / "uploads_audio",
        "uploads_video": work_dir / "uploads_video",
        "uploads_images": work_dir / "uploads_images",
        "previews": work_dir / "previews",
        "outputs": work_dir / "outputs",
    }
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


# -----------------------------
# Канонизаторы (идемпотентные)
# -----------------------------
def _norm_path(x: Any) -> str:
    """Достаём путь из любого «мусорного» формата."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, dict):
        return str(x.get("path") or x.get("filepath") or x.get("file") or "").strip()
    if hasattr(x, "path"):
        try:
            return str(getattr(x, "path")).strip()
        except Exception:
            return ""
    return ""


def _as_item_dict(x: Any, *, kind: str) -> Optional[Dict[str, Any]]:
    """Приводит x к каноничному dict-элементу списка."""
    p = _norm_path(x)
    if not p:
        return None
    name = ""
    try:
        name = Path(p).name
    except Exception:
        name = p
    if isinstance(x, dict):
        it = dict(x)
        it["path"] = p
        it.setdefault("id", _new_id())
        it.setdefault("name", it.get("name") or name)
        it.setdefault("kind", kind)
        return it
    return {"id": _new_id(), "path": p, "name": name, "kind": kind}


def _dedup_items_keep_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        p = _norm_path(it)
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(it)
    return out


def _norm_items(seq: Any, *, kind: str) -> List[Dict[str, Any]]:
    if not seq:
        return []
    out: List[Dict[str, Any]] = []
    for x in seq:
        it = _as_item_dict(x, kind=kind)
        if it:
            out.append(it)
    return _dedup_items_keep_order(out)


def _migrate_state(state: "AppState") -> None:
    """Идемпотентная миграция state (без append-эффекта на rerun)."""
    # аудио
    state.mastering.items = _norm_items(state.mastering.items, kind="audio")
    state.concat.items = _norm_items(state.concat.items, kind="audio")
    state.video.audio_items = _norm_items(state.video.audio_items, kind="audio")

    # видео/фон
    state.video.bg_video_items = _norm_items(state.video.bg_video_items, kind="video")
    state.video.concat_items = _norm_items(state.video.concat_items, kind="video")

    # пути/результаты
    state.loop.input_path = _norm_path(state.loop.input_path)
    state.loop.output_path = _norm_path(state.loop.output_path)

    state.video.bg_video_merged = _norm_path(state.video.bg_video_merged)
    state.video.bg_image_path = _norm_path(state.video.bg_image_path)
    state.video.last_rendered = _norm_path(state.video.last_rendered)

    state.last_audio_result = _norm_path(state.last_audio_result)

    # дефолт пресета
    if not state.mastering.preset:
        state.mastering.preset = "🎵 Универсальный"


@dataclass
class PathsState:
    work_dir: Path
    uploads_audio: Path
    uploads_video: Path
    uploads_images: Path
    previews: Path
    outputs_dir: Path

    @property
    def out_dir(self) -> Path:
        return self.outputs_dir


@dataclass
class MasteringState:
    items: List[Dict[str, Any]] = field(default_factory=list)
    preset: str = "🎵 Универсальный"
    last_output_path: Optional[str] = None


@dataclass
class ConcatState:
    items: List[Dict[str, Any]] = field(default_factory=list)
    crossfade_sec: float = 2.0
    result_path: Optional[str] = None


@dataclass
class LoopState:
    input_path: Optional[str] = None
    target_seconds: int = 3600
    fade_seconds: float = 2.0
    output_path: Optional[str] = None


@dataclass
class VideoState:
    audio_items: List[Dict[str, Any]] = field(default_factory=list)
    bg_video_items: List[Dict[str, Any]] = field(default_factory=list)

    # результат «Склейка видео»
    bg_video_merged: Optional[str] = None

    # префилл (передача результата в «Создать видеоклип», но НЕ залипает навсегда)
    transferred_bg_video: Optional[str] = None

    bg_image_path: Optional[str] = None

    # список входных видео для «Склейка видео»
    concat_items: List[Dict[str, Any]] = field(default_factory=list)

    last_rendered: Optional[str] = None


@dataclass
class AppState:
    paths: PathsState
    mastering: MasteringState = field(default_factory=MasteringState)
    concat: ConcatState = field(default_factory=ConcatState)
    loop: LoopState = field(default_factory=LoopState)
    video: VideoState = field(default_factory=VideoState)

    # общий “последний аудио результат”
    last_audio_result: Optional[str] = None


def get_state() -> AppState:
    """Единственная точка получения state."""
    if "APP_STATE" not in st.session_state:
        work_dir = get_work_root()
        cleanup_old_runs(work_dir)
        dirs = _ensure_dirs(work_dir)

        st.session_state["APP_STATE"] = AppState(
            paths=PathsState(
                work_dir=work_dir,
                uploads_audio=dirs["uploads_audio"],
                uploads_video=dirs["uploads_video"],
                uploads_images=dirs["uploads_images"],
                previews=dirs["previews"],
                outputs_dir=dirs["outputs"],
            )
        )

    state: AppState = st.session_state["APP_STATE"]
    _migrate_state(state)
    return state

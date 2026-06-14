from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
import hashlib
import uuid
import random

import streamlit as st


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _safe_stat_size(p: str) -> int:
    try:
        return int(Path(p).stat().st_size)
    except Exception:
        return 0


def _stable_key_from_path(path: str) -> str:
    return hashlib.md5(path.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _as_path(x: Any) -> str:
    if isinstance(x, dict):
        return str(x.get("path", "")).strip()
    return str(x).strip()


# ────────────────────────────────────────────────
# ✅ PATHS-ONLY LIST (для Склейки треков)
# ────────────────────────────────────────────────
def render_sortable_paths_list(
    *,
    kind: str,
    paths: List[str],
    list_id: str,
    title: Optional[str] = None,
    allow_preview: bool = True,
    preview_mode: str = "panel",
    allow_move: bool = True,
    show_shuffle: bool = False,
    allow_delete: bool = True,
    get_duration: Optional[Callable[[str], float]] = None,
) -> List[str]:
    lid = list_id
    store_key = f"__paths__{lid}"
    preview_key = f"__preview_path__{lid}"

    if store_key not in st.session_state:
        st.session_state[store_key] = []

    cur: List[str] = list(st.session_state[store_key])
    cur_set = {p for p in cur if p}

    incoming = [str(p).strip() for p in (paths or []) if str(p).strip()]
    for p in incoming:
        if p not in cur_set:
            cur.append(p)
            cur_set.add(p)

    st.session_state[store_key] = cur

    if preview_key not in st.session_state:
        st.session_state[preview_key] = None

    items = list(st.session_state[store_key])

    if title:
        st.markdown(title)

    # ✅ ВАЖНО: НИЧЕГО НЕ РИСУЕМ, если список пуст (чтобы не было лишнего блока)
    if not items:
        return items

    if show_shuffle:
        if st.button("🔀 Перемешать", key=f"shuffle_{lid}"):
            random.shuffle(items)
            st.session_state[store_key] = items
            st.rerun()

    def _move(lst: List[str], idx: int, new_idx: int) -> List[str]:
        if not (0 <= idx < len(lst)):
            return lst
        new_idx = max(0, min(len(lst) - 1, new_idx))
        if idx == new_idx:
            return lst
        x = lst.pop(idx)
        lst.insert(new_idx, x)
        return lst

    for idx, path in enumerate(items):
        p = str(path).strip()
        name = Path(p).name if p else "—"
        stable = _stable_key_from_path(p) if p else f"no_{idx}"

        dur_str = ""
        if get_duration and p:
            try:
                ds = float(get_duration(p))
                if ds > 0:
                    m = int(ds // 60)
                    s = int(ds % 60)
                    dur_str = f"{m:02d}:{s:02d}"
            except Exception:
                pass

        size_bytes = _safe_stat_size(p)
        size_mb = size_bytes / (1024 * 1024) if size_bytes else 0.0
        size_str = f"{size_mb:.1f} MB" if size_mb else ""

        meta_text = " • ".join([x for x in [dur_str, size_str] if x]).strip()

        c1, c2, c3, c4 = st.columns([6, 2, 5, 2])

        with c1:
            st.text_input(
                " ",
                value=name,
                disabled=True,
                label_visibility="collapsed",
                key=f"lbl_{lid}_{stable}",
            )

        with c2:
            st.text_input(
                " ",
                value=meta_text,
                disabled=True,
                label_visibility="collapsed",
                key=f"meta_{lid}_{stable}",
            )

        with c3:
            btns = []
            if allow_preview:
                btns.append("▶️")
            if allow_move:
                btns.extend(["↑", "↓", "⤒", "⤓"])
            if allow_delete:
                btns.append("🗑️")

            cols = st.columns(len(btns)) if btns else []
            bi = 0

            if allow_preview:
                with cols[bi]:
                    if st.button("▶️", key=f"pv_{lid}_{stable}"):
                        st.session_state[preview_key] = p
                        st.rerun()
                bi += 1

            if allow_move:
                with cols[bi]:
                    if st.button("↑", key=f"up_{lid}_{stable}"):
                        items = _move(items, idx, idx - 1)
                        st.session_state[store_key] = items
                        st.rerun()
                bi += 1

                with cols[bi]:
                    if st.button("↓", key=f"dn_{lid}_{stable}"):
                        items = _move(items, idx, idx + 1)
                        st.session_state[store_key] = items
                        st.rerun()
                bi += 1

                with cols[bi]:
                    if st.button("⤒", key=f"top_{lid}_{stable}"):
                        items = _move(items, idx, 0)
                        st.session_state[store_key] = items
                        st.rerun()
                bi += 1

                with cols[bi]:
                    if st.button("⤓", key=f"bot_{lid}_{stable}"):
                        items = _move(items, idx, len(items) - 1)
                        st.session_state[store_key] = items
                        st.rerun()
                bi += 1

            if allow_delete:
                with cols[bi]:
                    if st.button("🗑️", key=f"del_{lid}_{stable}"):
                        items.pop(idx)
                        if st.session_state.get(preview_key) == p:
                            st.session_state[preview_key] = None
                        st.session_state[store_key] = items
                        st.rerun()

        with c4:
            st.caption(Path(p).suffix.lower() if p else "")

    if allow_preview and preview_mode == "panel":
        prev_path = st.session_state.get(preview_key)
        if prev_path:
            st.divider()
            st.markdown("### ▶️ Прослушивание / Просмотр")
            st.caption(Path(prev_path).name)

            if Path(prev_path).exists():
                if kind == "audio":
                    st.audio(prev_path)
                else:
                    try:
                        st.video(Path(prev_path).read_bytes())
                    except Exception:
                        st.video(prev_path)

    return list(st.session_state[store_key])


# ────────────────────────────────────────────────
# Dict-based list оставляем как есть (для других страниц)
# ────────────────────────────────────────────────
def _ensure_item_dict(path: str, kind: str) -> Dict[str, Any]:
    p = str(path).strip()
    return {
        "id": _new_id(),
        "path": p,
        "name": Path(p).name,
        "kind": kind,
        "dur_sec": None,
        "size_bytes": _safe_stat_size(p),
    }


def _normalize_items(
    *,
    kind: str,
    items: Optional[Sequence[Any]] = None,
    paths: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    if items is not None:
        for it in items:
            p = _as_path(it)
            if not p:
                continue
            if isinstance(it, dict):
                it2 = dict(it)
                it2["path"] = p
                it2.setdefault("id", _new_id())
                it2["name"] = Path(p).name
                it2["kind"] = kind
                it2.setdefault("dur_sec", None)
                it2["size_bytes"] = _safe_stat_size(p)
                out.append(it2)
            else:
                out.append(_ensure_item_dict(p, kind=kind))
        return out

    if paths is not None:
        for p in paths:
            pp = _as_path(p)
            if pp:
                out.append(_ensure_item_dict(pp, kind=kind))
        return out

    return []


def _dedup_by_path_keep_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        p = str(it.get("path", "")).strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(it)
    return out


def _move_dict(lst: List[Dict[str, Any]], idx: int, new_idx: int) -> List[Dict[str, Any]]:
    if not (0 <= idx < len(lst)):
        return lst
    new_idx = max(0, min(len(lst) - 1, new_idx))
    if idx == new_idx:
        return lst
    x = lst.pop(idx)
    lst.insert(new_idx, x)
    return lst


def render_sortable_media_list(
    *,
    kind: str,
    items: Optional[List[Dict[str, Any]]] = None,
    paths: Optional[List[str]] = None,
    list_id: Optional[str] = None,
    title: Optional[str] = None,
    allow_preview: bool = True,
    preview_mode: str = "panel",
    allow_move: bool = True,
    show_shuffle: bool = False,
    allow_delete: bool = True,
    pick_path_state_key: Optional[str] = None,
    get_duration: Optional[Callable[[str], float]] = None,
) -> List[Dict[str, Any]]:
    lid = list_id or f"{kind}_list"
    store_key = f"__items__{lid}"
    preview_key = f"__preview_path__{lid}"

    incoming = _dedup_by_path_keep_order(_normalize_items(kind=kind, items=items, paths=paths))

    if store_key not in st.session_state:
        st.session_state[store_key] = incoming

    cur: List[Dict[str, Any]] = list(st.session_state[store_key])
    cur_paths = {str(x.get("path", "")).strip() for x in cur if x.get("path")}
    for inc in incoming:
        p = str(inc.get("path", "")).strip()
        if p and p not in cur_paths:
            cur.append(inc)
            cur_paths.add(p)

    st.session_state[store_key] = _dedup_by_path_keep_order(cur)

    if preview_key not in st.session_state:
        st.session_state[preview_key] = None

    items_norm: List[Dict[str, Any]] = list(st.session_state[store_key])

    if title:
        st.markdown(title)

    if not items_norm:
        return items_norm

    if show_shuffle:
        if st.button("🔀 Перемешать", key=f"shuffle_{lid}"):
            random.shuffle(items_norm)
            st.session_state[store_key] = items_norm
            st.rerun()

    for idx, it in enumerate(items_norm):
        path = str(it.get("path", "")).strip()
        name = Path(path).name if path else "—"
        stable = str(it.get("id") or _stable_key_from_path(path) or f"no_{idx}")

        dur_str = ""
        if get_duration and path:
            try:
                ds = float(get_duration(path))
                if ds > 0:
                    m = int(ds // 60)
                    s = int(ds % 60)
                    dur_str = f"{m:02d}:{s:02d}"
            except Exception:
                pass

        size_bytes = int(it.get("size_bytes") or _safe_stat_size(path))
        size_mb = size_bytes / (1024 * 1024) if size_bytes else 0.0
        size_str = f"{size_mb:.1f} MB" if size_mb else ""

        c1, c2, c3, c4 = st.columns([6, 2, 5, 2])
        with c1:
            st.write(f"**{name}**")
        with c2:
            if dur_str or size_str:
                st.caption(" • ".join([x for x in [dur_str, size_str] if x]))

        # кнопки/превью оставлены как было (не трогаем)
        with c3:
            btns = []
            if allow_preview:
                btns.append("▶️")
            if allow_move:
                btns.extend(["↑", "↓", "⤒", "⤓"])
            if allow_delete:
                btns.append("🗑️")
            cols = st.columns(len(btns)) if btns else []
            bi = 0

            if allow_preview:
                with cols[bi]:
                    if st.button("▶️", key=f"pv_{lid}_{stable}"):
                        if pick_path_state_key:
                            st.session_state[pick_path_state_key] = path
                        st.session_state[preview_key] = path
                        st.rerun()
                bi += 1

            if allow_move:
                with cols[bi]:
                    if st.button("↑", key=f"up_{lid}_{stable}"):
                        items_norm = _move_dict(items_norm, idx, idx - 1)
                        st.session_state[store_key] = items_norm
                        st.rerun()
                bi += 1
                with cols[bi]:
                    if st.button("↓", key=f"dn_{lid}_{stable}"):
                        items_norm = _move_dict(items_norm, idx, idx + 1)
                        st.session_state[store_key] = items_norm
                        st.rerun()
                bi += 1
                with cols[bi]:
                    if st.button("⤒", key=f"top_{lid}_{stable}"):
                        items_norm = _move_dict(items_norm, idx, 0)
                        st.session_state[store_key] = items_norm
                        st.rerun()
                bi += 1
                with cols[bi]:
                    if st.button("⤓", key=f"bot_{lid}_{stable}"):
                        items_norm = _move_dict(items_norm, idx, len(items_norm) - 1)
                        st.session_state[store_key] = items_norm
                        st.rerun()
                bi += 1

            if allow_delete:
                with cols[bi]:
                    if st.button("🗑️", key=f"del_{lid}_{stable}"):
                        items_norm.pop(idx)
                        st.session_state[store_key] = items_norm
                        st.rerun()

        with c4:
            st.caption(Path(path).suffix.lower() if path else "")

    if allow_preview and preview_mode == "panel":
        prev_path = st.session_state.get(preview_key)
        if prev_path and Path(prev_path).exists():
            st.divider()
            st.markdown("### ▶️ Прослушивание / Просмотр")
            st.caption(Path(prev_path).name)
            if kind == "audio":
                st.audio(prev_path)
            else:
                try:
                    st.video(Path(prev_path).read_bytes())
                except Exception:
                    st.video(prev_path)

    return items_norm
from __future__ import annotations

import streamlit as st


def render_simple_timeline(durations: list[float], labels: list[str], title: str = "Мини-таймлайн") -> None:
    if not durations:
        return
    st.markdown(f"**{title}:**")
    total = sum(durations)
    st.write(f"Суммарно: {total:.1f} сек")

    # Простейшая визуализация “полосками” текстом (без графиков)
    for lab, d in zip(labels, durations):
        bar = "█" * max(1, int((d / max(total, 1e-9)) * 40))
        st.write(f"{lab}: {d:.1f}s  {bar}")

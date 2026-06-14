# ui/layout.py
from __future__ import annotations
import streamlit as st


def render_header():
    st.markdown(
        """
        <style>
        .rl-title {
            font-size: 40px;
            font-weight: 800;
            color: #0b2a6f; /* темно-синий */
            margin-bottom: 4px;
        }
        .rl-subtitle {
            font-size: 15px;
            color: #334155;
            margin-top: 0px;
            margin-bottom: 18px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="rl-title">🎧 Remaster+Loop</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="rl-subtitle">Локальная программа для зацикливания, склейки, мастеринга аудио и сборки MP4 под YouTube/Shorts/Reels.</div>',
        unsafe_allow_html=True,
    )

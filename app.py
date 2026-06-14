# app.py — Streamlit entrypoint (Remaster+Loop)
from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

from ui.state import get_state


NAV_DEFAULT = "🎧 Мастеринг"


def _apply_pending_nav_before_sidebar() -> None:
    """
    pending_nav применяется ДО создания sidebar-виджета.
    Иначе переходы могут быть недетерминированными.
    """
    pending = st.session_state.pop("__pending_nav_choice", None)
    if isinstance(pending, str) and pending.strip():
        st.session_state["nav_choice"] = pending



def _optional_home_page():
    """
    Home (опционально): подключаем только если файл реально существует.
    ВАЖНО: не глотаем любые ошибки внутри home.py — они должны быть видны,
    иначе потом "тихо" ломается логика, и мы не понимаем почему.
    """
    try:
        from ui.pages.home import page_home  # import inside is OK for Streamlit rerun
        return page_home
    except ModuleNotFoundError:
        return None
    except ImportError:
        # На всякий: если модуль есть, но импорт невозможен из-за путей/пакета
        return None



def main() -> None:
    # Подхватывает .env из корня проекта
    load_dotenv(override=False)

    st.set_page_config(
        page_title="Remaster+Loop",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # 1) применяем pending_nav ДО sidebar
    _apply_pending_nav_before_sidebar()

    # 2) состояние приложения
    state = get_state()

    # 3) страницы (импорты внутри main — безопаснее для rerun)
    from ui.pages.mastering_page import page_mastering
    from ui.pages.concat_page import page_concat
    from ui.pages.video_concat_page import page_video_concat
    from ui.pages.video_page import page_video
    from ui.pages.loop_page import page_loop
    from ui.pages.canvas_page import page_canvas
    from ui.pages.cleanup_page import page_cleanup
    from ui.pages.help_page import page_help

    pages: dict[str, callable] = {}

    home = _optional_home_page()
    if home is not None:
        pages["🏠 Главная"] = home

    pages.update(
        {
            "🎧 Мастеринг": page_mastering,
            "🧩 Склейка треков": page_concat,
            "🎞️ Склейка видео": page_video_concat,
            "🎬 Создать видеоклип": page_video,
            "🔁 Зацикливание": page_loop,
            "🎞️ Spotify Canvas (9:16)": page_canvas,
            "🧹 Очистка Work-папки": page_cleanup,
            "📘 Руководство": page_help,
        }
    )

    st.sidebar.title("Навигация")

    # Текущий выбор + защита от "битого" значения
    current = st.session_state.get("nav_choice", NAV_DEFAULT)
    if current not in pages:
        current = NAV_DEFAULT

    options = list(pages)
    choice = st.sidebar.radio(
        "Перейти к разделу",
        options=options,
        index=options.index(current),
        key="nav_choice",
    )

    pages[choice](state)


if __name__ == "__main__":
    main()

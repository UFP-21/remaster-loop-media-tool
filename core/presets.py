from __future__ import annotations

# Пресеты — это НЕ “магия AI”. Это готовые DSP-цепочки (EQ/компрессия/лимитер/нормализация),
# которые ffmpeg выполняет через аудио-фильтры. Это “быстрый мастеринг” в один клик.
# Позже можно расширить: multi-band compression, dynamic EQ, true-peak лимитер, LUFS-таргеты под платформы и т.д.

# Справочник фильтров ffmpeg: https://ffmpeg.org/ffmpeg-filters.html
# Формат: "filter1=..., filter2=..., filter3=..."
# Важно: значения подобраны “мягко”, чтобы не разваливать микс и не клипповать.

PRESETS: dict[str, str] = {
    # ─────────────────────────────────────────
    # БАЗОВЫЕ
    # ─────────────────────────────────────────
    "Чистый звук": "highpass=f=25, lowpass=f=19500",

    "Тёплое звучание": (
        "equalizer=f=120:t=q:w=1.0:g=2, "
        "equalizer=f=2500:t=q:w=1.0:g=-1, "
        "highpass=f=25, lowpass=f=19000"
    ),

    "Глубокий бас": (
        "equalizer=f=80:t=q:w=1.0:g=4, "
        "equalizer=f=200:t=q:w=1.0:g=2, "
        "highpass=f=25"
    ),

    "Яркие верха": (
        "equalizer=f=8000:t=q:w=0.8:g=3, "
        "equalizer=f=12000:t=q:w=0.7:g=2, "
        "lowpass=f=19500"
    ),

    # ─────────────────────────────────────────
    # НОВЫЕ (по твоему запросу)
    # ─────────────────────────────────────────
    "Яркие верха + глубокий бас": (
        "equalizer=f=80:t=q:w=1.0:g=4, "
        "equalizer=f=200:t=q:w=1.0:g=2, "
        "equalizer=f=8000:t=q:w=0.8:g=3, "
        "equalizer=f=12000:t=q:w=0.7:g=2, "
        "highpass=f=25, lowpass=f=19500"
    ),

    "Стерео шире (лёгкий объём)": (
        "stereotools=mlev=0.90:slev=1.10, "
        "highpass=f=25, lowpass=f=19500"
    ),
        "Объём + ВЕРХА + БАС (Wide)": (
        # 1) Стерео-объём (шире)
        "stereotools=mlev=0.90:slev=1.10, "
        # 2) Бас + верха (EQ)
        "equalizer=f=80:t=q:w=1.0:g=4, "
        "equalizer=f=200:t=q:w=1.0:g=2, "
        "equalizer=f=8000:t=q:w=0.8:g=3, "
        "equalizer=f=12000:t=q:w=0.7:g=2, "
        # 3) Лёгкая “санитарка”
        "highpass=f=25, lowpass=f=19500"
    ),

    # ─────────────────────────────────────────
    # ДИНАМИКА / ГРОМКОСТЬ
    # ─────────────────────────────────────────
    "Мягкий микс": (
        "acompressor=threshold=-16dB:ratio=2:attack=30:release=250, "
        "alimiter=limit=0.96"
    ),

    "Студийный": (
        "highpass=f=30, "
        "acompressor=threshold=-18dB:ratio=3:attack=20:release=200, "
        "alimiter=limit=0.95"
    ),

    "Плотнее (loud)": (
        "acompressor=threshold=-22dB:ratio=6:attack=8:release=120, "
        "alimiter=limit=0.93"
    ),

    "Радио-формат": (
        "acompressor=threshold=-20dB:ratio=4:attack=10:release=150, "
        "loudnorm=I=-14:TP=-1.5:LRA=11"
    ),
}

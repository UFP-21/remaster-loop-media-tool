@echo off
setlocal

cd /d "%~dp0"

call ".venv\Scripts\activate.bat"

REM Work folder рядом с проектом:
set REM_LOOP_WORK=%~dp0RemasterLoop_Work

REM Если ffmpeg лежит в проекте: tools\ffmpeg\bin\ffmpeg.exe
REM (если нет - закомментируй 2 строки ниже и используй системный PATH)
set FFMPEG_PATH=%~dp0tools\ffmpeg\bin\ffmpeg.exe
set FFPROBE_PATH=%~dp0tools\ffmpeg\bin\ffprobe.exe

streamlit run app.py

pause
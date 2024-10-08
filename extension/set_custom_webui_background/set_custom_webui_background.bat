@echo off
echo Loading extension, please stand by.
echo.

cd /d %~dp0
for /F "tokens=* USEBACKQ" %%A in (`python -m poetry env info --path`) do call "%%A\Scripts\activate.bat"
chcp 65001
cls
cd ..
cd ..

:: extension code below
title Custom webui background - windrecorder
streamlit run "extension\set_custom_webui_background\_webui.py"
pause
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
title Windrecorder
python "extension\manually_convert_screenshot_cache_into_video\_main.py"
pause
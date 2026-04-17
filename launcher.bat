@echo off
title JV Measurements
cd /d "%~dp0"

if exist "python\python.exe" (
    "python\python.exe" main.py
) else (
    python main.py
)

if errorlevel 1 (
    echo.
    echo An error occurred. Press any key to close.
    pause > nul
)

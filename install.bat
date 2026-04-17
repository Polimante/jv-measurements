@echo off
title JV Measurements — First-time Setup
echo ================================================
echo   JV Measurements — First-time Setup
echo ================================================
echo.
echo This will install all required Python libraries.
echo This may take a few minutes. Please wait.
echo.

cd /d "%~dp0"

if exist "python\python.exe" (
    set PYTHON="python\python.exe"
) else (
    set PYTHON=python
)

echo Installing dependencies...
%PYTHON% -m pip install --upgrade pip --quiet
%PYTHON% -m pip install -r requirements.txt --quiet

if errorlevel 1 (
    echo.
    echo ERROR: Installation failed.
    echo Please contact the software administrator.
    pause
    exit /b 1
)

echo.
echo ================================================
echo   Setup complete! You can now run launcher.bat
echo   or use the desktop shortcut if installed.
echo ================================================
echo.
pause

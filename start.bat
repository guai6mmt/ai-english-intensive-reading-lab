@echo off
title AI English Lab
cd /d "%~dp0"

echo.
echo  ============================================
echo    AI English Intensive Reading Lab
echo    Port: 8010  ^|  http://127.0.0.1:8010
echo  ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo.
    echo  Download Python 3.10+ from: https://www.python.org/downloads/
    echo  During install, check [x] Add Python to PATH
    echo.
    pause
    exit /b 1
)

:: Create venv if missing
if not exist ".venv" (
    echo  Setting up virtual environment for the first time...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate venv
call ".venv\Scripts\activate.bat"

:: Install / update dependencies
echo  Checking dependencies...
pip install -r requirements.txt --no-cache-dir --disable-pip-version-check -q
if errorlevel 1 (
    echo  [ERROR] Failed to install dependencies.
    echo  Try running:  pip install -r requirements.txt --no-cache-dir
    pause
    exit /b 1
)

:: Load .env if present
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

:: Ensure data directory
if not exist "data" mkdir data

:: Set defaults
if not defined PORT set PORT=8010
if not defined HOST set HOST=127.0.0.1

echo.
echo  ============================================
echo.
echo    Server started!  Open your browser at:
echo.
echo      http://%HOST%:%PORT%
echo.
echo    Press Ctrl+C to stop.
echo.
echo  ============================================
echo.

:: Auto-open browser after ~2 seconds
start "" cmd /c "ping -n 3 127.0.0.1 >nul 2>&1 && start http://%HOST%:%PORT%"

:: Start server
python -m uvicorn app:app --reload --host %HOST% --port %PORT%

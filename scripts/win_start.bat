@echo off
cd /d "%~dp0\.."

set PORT=8010
set HOST=127.0.0.1

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

if not exist "data" mkdir data

echo.
echo  AI English Lab starting at http://%HOST%:%PORT%
echo  Press Ctrl+C to stop.
echo.

python -m uvicorn app:app --reload --host %HOST% --port %PORT%

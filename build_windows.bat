@echo off
REM Build the CCTV Tailgate Windows app. Run from the project root on Windows
REM with Python 3.11 or 3.12 installed.

setlocal

if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

pyinstaller cctv-tailgate.spec --noconfirm

echo.
echo Build complete.
echo Run: dist\CCTV-Tailgate\CCTV-Tailgate.exe
endlocal

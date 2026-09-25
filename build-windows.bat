@echo off
setlocal EnableExtensions

title CLINTOY HUB Boot Creator Builder

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python was not found. Install Python and enable Add Python to PATH.
  pause
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 1

if not exist assets\logo.svg (
  echo ERROR: assets\logo.svg is missing.
  pause
  exit /b 1
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --add-data "assets;assets" --name "CLINTOY-HUB-Boot-Creator" app.py
if errorlevel 1 (
  echo BUILD FAILED.
  pause
  exit /b 1
)

echo BUILD COMPLETE: dist\CLINTOY-HUB-Boot-Creator.exe
pause

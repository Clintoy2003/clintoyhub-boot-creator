@echo off
setlocal EnableExtensions

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python and enable Add Python to PATH.
  pause
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 1

python -m PyInstaller --noconfirm --clean --onefile --windowed --name "CLINTOY-HUB-Boot-Creator" app.py
if errorlevel 1 (
  echo BUILD FAILED.
  pause
  exit /b 1
)

echo BUILD COMPLETE: dist\CLINTOY-HUB-Boot-Creator.exe
pause

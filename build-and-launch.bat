@echo off
setlocal EnableExtensions

title CLINTOY HUB Boot Creator v4 - Ventoy Edition Builder

echo ==========================================
echo   CLINTOY HUB Boot Creator v4
echo   Ventoy Multi-Boot Edition
echo ==========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found.
    echo.
    echo Install Python from:
    echo https://www.python.org/downloads/windows/
    echo.
    echo Enable "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

echo Python detected:
python --version
echo.

echo Installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: pip upgrade failed.
    pause
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    pause
    exit /b 1
)

python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo ERROR: PyInstaller installation failed.
    pause
    exit /b 1
)

if not exist assets mkdir assets

if exist "assets\logo.svg.svg" (
    echo Fixing logo filename...
    ren "assets\logo.svg.svg" "logo.svg" >nul 2>&1
)

if not exist "assets\logo.svg" (
    echo Downloading CLINTOY HUB logo...
    powershell -Command "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; (New-Object System.Net.WebClient).DownloadFile('https://raw.githubusercontent.com/Clintoy2003/clintoyhub-boot-creator/main/assets/logo.svg', 'assets\\logo.svg')"
    if errorlevel 1 (
        echo WARNING: Could not download logo, continuing without it.
    )
)

echo.
echo Building CLINTOY HUB Boot Creator v4 - Ventoy Edition...

python -m PyInstaller --noconfirm --clean --onefile --windowed --add-data "assets;assets" --name "CLINTOY-HUB-Boot-Creator-v4" app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo BUILD COMPLETED SUCCESSFULLY
echo ==========================================
echo.
echo Executable:
echo %CD%\dist\CLINTOY-HUB-Boot-Creator-v4.exe
echo.
echo Launching application...

start "" "%CD%\dist\CLINTOY-HUB-Boot-Creator-v4.exe"

exit /b 0

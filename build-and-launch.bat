@echo off
setlocal EnableExtensions

title CLINTOY HUB Boot Creator v4 - Ventoy Edition
cd /d "%~dp0"

echo ==========================================
echo   CLINTOY HUB Boot Creator v4
echo   Ventoy Multi-Boot Edition
echo ==========================================
echo.

if not exist "app.py" (
    echo ERROR: app.py was not found.
    echo Put this BAT file in the same folder as app.py.
    pause
    exit /b 1
)

if not exist "assets" mkdir "assets"

if exist "assets\logo.svg.svg" (
    echo Renaming logo.svg.svg to logo.svg...
    ren "assets\logo.svg.svg" "logo.svg" >nul 2>&1
)

if not exist "assets\logo.svg" (
    echo Downloading CLINTOY HUB logo...
    powershell -Command "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; (New-Object System.Net.WebClient).DownloadFile('https://raw.githubusercontent.com/Clintoy2003/clintoyhub-boot-creator/main/assets/logo.svg', 'assets\\logo.svg')"
)

if not exist "assets\logo.svg" (
    echo ERROR: Logo could not be found or downloaded.
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found.
    echo Install Python and enable "Add Python to PATH".
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

python -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 1

echo.
echo Building CLINTOY HUB v4 - Ventoy Edition...

python -m PyInstaller ^^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --add-data "assets;assets" ^
    --name "CLINTOY-HUB-Boot-Creator-v4" ^
    app.py

if errorlevel 1 (
    echo BUILD FAILED.
    pause
    exit /b 1
)

if not exist "dist\CLINTOY-HUB-Boot-Creator-v4.exe" (
    echo ERROR: Executable was not created.
    pause
    exit /b 1
)

echo.
echo BUILD SUCCESSFUL.
echo Launching CLINTOY HUB v4...

start "" "dist\CLINTOY-HUB-Boot-Creator-v4.exe"

exit /b 0

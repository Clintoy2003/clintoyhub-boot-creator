@echo off
setlocal

echo Installing dependencies...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install pyinstaller

echo Building CLINTOY HUB Boot Creator...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "CLINTOY-HUB-Boot-Creator" ^
  app.py

echo.
echo Build complete.
echo Executable:
echo dist\CLINTOY-HUB-Boot-Creator.exe
pause
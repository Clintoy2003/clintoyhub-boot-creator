# CLINTOY HUB Boot Creator

A desktop utility for creating bootable USB drives from ISO and IMG files.

## Features

- Windows, Linux, and macOS support
- ISO and IMG image support
- USB drive detection
- Raw image writing
- BIOS/UEFI-compatible image support
- SHA-256 verification
- Administrator/root permission handling
- Safety confirmation before erasing a drive

## Requirements

- Python 3.10 or newer
- PySide6
- Administrator/root privileges
- A removable USB drive
- An ISO or IMG file

## Install

```bash
python -m pip install -r requirements.txt
```

## Run on Windows

Open Command Prompt as Administrator:

```bat
python app.py
```

Or double-click:

```text
build-windows.bat
```

The executable will be created at:

```text
dist\CLINTOY-HUB-Boot-Creator.exe
```

## Run on Linux

```bash
sudo python3 app.py
```

Required system commands:

```text
lsblk
umount
wipefs
```

Ubuntu/Debian users can install them with:

```bash
sudo apt install util-linux
```

## Run on macOS

```bash
sudo python3 app.py
```

The application uses:

```text
diskutil
```

## Important warning

This application writes directly to the selected physical USB device. Selecting the wrong device can permanently destroy data.

Always check:

- USB model
- USB capacity
- Device path
- Selected ISO file

## Current scope

This release implements single-image ISO/IMG creation.

A true Ventoy-style multi-ISO system requires installing a bootloader and creating a special partition layout. Simply copying multiple ISO files to a normal USB drive does not make the drive multi-boot.

## License

MIT License
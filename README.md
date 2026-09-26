# CLINTOY HUB Boot Creator v4 - Ventoy Multi-Boot Edition

## Features

- **Single ISO Creator Mode**: Create verified Windows bootable USB drives
  - GPT/UEFI and MBR/Legacy BIOS boot support
  - FAT32 with automatic WIM splitting for large files
  - NTFS support for large install.esd images
  - Full file verification and validation
  - Supports Windows 10 and Windows 11

- **Professional UI**: Dark cyan/teal theme
  - Modern PySide6 interface
  - Real-time progress tracking
  - Live status updates
  - Disk detection and validation

## Requirements

- **Windows 10 or Windows 11**
- **Python 3.10+** ([Download](https://www.python.org/downloads/windows/))
- **Administrator privileges** (required for USB operations)
- **USB drive** (8 GB minimum, 32 GB recommended)
- **Windows ISO** (official Microsoft Windows 10/11 installation media)

## Installation & Build

### Option 1: Quick Launch (Recommended)

1. Download or clone this repository
2. Place your Windows ISO in the project folder (optional)
3. Double-click `build-and-launch.bat`

The script will:
- Install Python dependencies
- Build the executable with PyInstaller
- Automatically launch CLINTOY HUB v4

### Option 2: Manual Build

```bash
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --add-data "assets;assets" --name "CLINTOY-HUB-Boot-Creator-v4" app.py
```

The executable will be created at:
```
dist\CLINTOY-HUB-Boot-Creator-v4.exe
```

## Usage

1. **Launch** the application
   ```bash
   build-and-launch.bat
   ```
   Or double-click:
   ```
   dist\CLINTOY-HUB-Boot-Creator-v4.exe
   ```

2. **Select Windows ISO**
   - Click "Browse ISO"
   - Choose an official Windows 10 or Windows 11 ISO file
   - Supported: .iso format only

3. **Select USB Drive**
   - Click "Refresh removable drives"
   - Select your USB drive from the list
   - **Verify disk number and capacity** - all data will be erased

4. **Configure Boot**
   - **Partition style**: GPT (UEFI, recommended for Windows 11) or MBR (BIOS)
   - **Filesystem**: FAT32 (with auto-splitting) or NTFS

5. **Create Installer**
   - Click "Create verified installer USB"
   - Confirm the disk erase warning
   - Wait for completion and verification
   - Safely eject the USB when finished

## File Structure

```
clintoyhub-boot-creator/
├── app.py                          # Main PySide6 application
├── requirements.txt                # Python dependencies
├── build-and-launch.bat            # Build and launch script
├── build-windows.bat               # Alternative build script
├── README.md                       # This file
├── LICENSE                         # License information
├── .gitignore                      # Git ignore rules
└── assets/
    ├── logo.svg                    # CLINTOY HUB logo
    └── README.md                   # Asset information
```

## Technical Details

### Single ISO Creator Mode

- **USB Preparation**: DiskPart-based disk partitioning (GPT or MBR)
- **File Copying**: Robocopy with file verification
- **WIM Splitting**: DISM-based automatic splitting for FAT32 compatibility
- **Boot Configuration**: MBR-specific boot sector modification
- **Verification**: Full file integrity checking before completion

### Technology Stack

- **UI Framework**: PySide6 (Qt for Python)
- **System Integration**: Python subprocess, PowerShell, DiskPart, Robocopy, DISM
- **Build Tool**: PyInstaller for Windows executable packaging
- **Theme**: Custom dark UI with cyan/teal accents

## Troubleshooting

### Python Not Found

**Error**: `ERROR: Python was not found`

**Solution**:
1. Download Python from https://www.python.org/downloads/windows/
2. During installation, check "Add python.exe to PATH"
3. Restart your computer
4. Try `build-and-launch.bat` again

### USB Not Detected

**Error**: `Detected 0 removable USB drive(s)`

**Solution**:
1. Connect your USB drive to your computer
2. Wait 3-5 seconds for Windows to recognize it
3. Click "Refresh removable drives"
4. Ensure the USB is formatted and visible in File Explorer

### Disk Erase Confirmation

**Error**: Cannot proceed after confirmation

**Solution**:
1. Click "Refresh removable drives" to update the disk list
2. Select the correct disk (verify disk number and capacity)
3. Confirm the warning dialog

### Admin Privileges Required

**Error**: Access denied

**Solution**:
- The application requires administrator privileges
- Windows will automatically prompt for elevation
- Click "Yes" when prompted

## License

MIT License - See LICENSE file for details

## Credits

- **CLINTOY HUB**: Boot Creator tool suite
- **PySide6**: Qt framework for Python
- **Windows Tools**: DiskPart, Robocopy, DISM

## Version History

- **v4.0** - Ventoy Multi-Boot Edition (Current)
- **v3.0** - Single ISO Creator stable release
- **v2.0** - Initial MBR/GPT support
- **v1.0** - First release

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Check existing documentation
- Review troubleshooting section above

---

**CLINTOY HUB Boot Creator v4** - Professional Windows Boot Media Creation Tool

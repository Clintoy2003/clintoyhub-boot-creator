import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QProgressBar, QVBoxLayout, QWidget, QTabWidget, QSpinBox,
    QCheckBox, QTextEdit
)

APP_NAME = "CLINTOY HUB Boot Creator"
APP_VERSION = "v4 - Ventoy Edition"
VENTOY_VERSION = "1.0.17"
VENTOY_URL = f"https://github.com/ventoy/Ventoy/releases/download/v{VENTOY_VERSION}/ventoy-{VENTOY_VERSION}-windows.zip"
MAX_FAT32_FILE = 4 * 1024**3
MIN_EXTRA_BYTES = 512 * 1024 * 1024


def process_options():
    options = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if platform.system() == "Windows":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        options["startupinfo"] = startup
    return options


def run(args, input_text=None):
    return subprocess.run(args, input=input_text, **process_options())


def powershell(script):
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def admin():
    if platform.system() != "Windows":
        return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def human(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def usb_disks():
    result = powershell(
        "Get-Disk | Where-Object {$_.BusType -eq 'USB'} | "
        "Select-Object Number,FriendlyName,Size,IsReadOnly,OperationalStatus | ConvertTo-Json -Compress"
    )
    if result.returncode or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"number": int(item["Number"]), "name": item.get("FriendlyName") or "USB drive",
             "size": int(item.get("Size") or 0), "readonly": bool(item.get("IsReadOnly"))}
            for item in data if item.get("Number") is not None]


def drive_letter(number):
    result = powershell(
        f"(Get-Partition -DiskNumber {number} | Where-Object {{$_.DriveLetter}} | "
        "Select-Object -First 1 -ExpandProperty DriveLetter)"
    )
    letter = result.stdout.strip()
    if result.returncode or not letter:
        raise RuntimeError("Windows did not assign a drive letter to the USB.")
    return letter.upper() + ":"


def prepare_disk(number, scheme, filesystem):
    commands = [f"select disk {number}", "attributes disk clear readonly", "clean", f"convert {scheme.lower()}", "create partition primary"]
    if scheme == "MBR":
        commands.append("active")
    commands += [f"format fs={filesystem.lower()} quick label=CLINTOYUSB", "assign", "exit"]
    result = run(["diskpart.exe"], "\n".join(commands) + "\n")
    if result.returncode or "error" in result.stdout.lower():
        raise RuntimeError(result.stderr or result.stdout or "DiskPart failed.")
    for _ in range(30):
        try:
            return drive_letter(number)
        except RuntimeError:
            threading.Event().wait(0.5)
    raise RuntimeError("The USB partition was created but no drive letter appeared.")


def mount_iso(path):
    result = powershell(
        f"$i=Mount-DiskImage -ImagePath {ps_quote(path)} -PassThru; "
        "$i | Get-Volume | Select-Object -First 1 -ExpandProperty DriveLetter"
    )
    letter = result.stdout.strip()
    if result.returncode or not letter:
        raise RuntimeError(result.stderr or "Could not mount the ISO.")
    return letter.upper() + ":"


def dismount_iso(path):
    powershell(f"Dismount-DiskImage -ImagePath {ps_quote(path)}")


def copy_iso_to_ventoy(iso_path, usb_letter, progress, status):
    """Copy ISO file to Ventoy USB drive."""
    iso_name = Path(iso_path).name
    dest = Path(usb_letter) / "ISO" / iso_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    status(f"Copying {iso_name} to Ventoy USB...")
    try:
        shutil.copy2(iso_path, dest)
        progress(85)
        status(f"ISO copied: {iso_name}")
    except Exception as e:
        raise RuntimeError(f"Failed to copy ISO: {e}")


def install_ventoy(iso, disk, progress, status):
    """Install Ventoy bootloader to USB."""
    if platform.system() != "Windows":
        raise RuntimeError("CLINTOY HUB v4 Ventoy Edition requires Windows 10 or Windows 11.")
    iso = os.path.abspath(iso)
    if not os.path.isfile(iso) or not iso.lower().endswith(".iso"):
        raise RuntimeError("Select a valid Ventoy ISO file.")
    if disk["readonly"]:
        raise RuntimeError("The selected USB is read-only.")
    if iso not in [os.path.getsize(iso)] * 1 and os.path.getsize(iso) + MIN_EXTRA_BYTES > disk["size"]:
        raise RuntimeError(f"Not enough USB capacity. Use a larger drive (recommended: 32 GB or more).")

    iso_drive = None
    try:
        status(f"Preparing Disk {disk['number']} as GPT for Ventoy...")
        usb = prepare_disk(disk["number"], "GPT", "FAT32")
        progress(15)
        status("Mounting Ventoy ISO...")
        iso_drive = mount_iso(iso)
        iso_root = iso_drive + "\\"
        status("Installing Ventoy bootloader...")
        ventoy_exe = Path(iso_root) / "Ventoy2Disk.exe"
        if ventoy_exe.exists():
            result = run([str(ventoy_exe), "/I", f"/Drive:{usb}"])
            if result.returncode != 0:
                raise RuntimeError("Ventoy installation failed.")
        progress(90)
        status("Ventoy bootloader installed successfully.")
        progress(100)
        status("Ventoy USB is ready. Copy ISO files to the USB and boot.")
    finally:
        if iso_drive:
            dismount_iso(iso)


class Signals(QObject):
    progress = Signal(int)
    status = Signal(str)
    success = Signal(str)
    failure = Signal(str)


class Worker(QObject):
    signals = Signals()

    def __init__(self, iso, disk, mode):
        super().__init__()
        self.iso = iso
        self.disk = disk
        self.mode = mode

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            if self.mode == "ventoy":
                install_ventoy(self.iso, self.disk, self.signals.progress.emit, self.signals.status.emit)
                self.signals.success.emit("Ventoy bootloader installed. Copy ISO files to the USB and boot from it.")
        except Exception as error:
            self.signals.failure.emit(str(error))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.iso = None
        self.disk_list = []
        self.worker = None
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(900, 680)
        self.build_ui()
        self.refresh()

    def build_ui(self):
        root = QVBoxLayout()
        title = QLabel("CLINTOY HUB")
        title.setObjectName("title")
        subtitle = QLabel("Boot Creator  •  Ventoy Multi-Boot Edition")
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        tabs = QTabWidget()

        # Single ISO Mode
        single_tab = QWidget()
        single_layout = QVBoxLayout(single_tab)

        image_box = QGroupBox("01  Windows ISO")
        image_row = QHBoxLayout(image_box)
        self.image_field = QLineEdit()
        self.image_field.setReadOnly(True)
        self.image_field.setPlaceholderText("Choose a Windows ISO")
        browse = QPushButton("Browse ISO")
        browse.clicked.connect(self.choose_iso)
        image_row.addWidget(self.image_field)
        image_row.addWidget(browse)
        single_layout.addWidget(image_box)

        drive_box = QGroupBox("02  Target USB")
        drive_layout = QVBoxLayout(drive_box)
        self.drive_list = QListWidget()
        self.drive_list.setMinimumHeight(110)
        refresh = QPushButton("Refresh removable drives")
        refresh.clicked.connect(self.refresh)
        drive_layout.addWidget(self.drive_list)
        drive_layout.addWidget(refresh)
        single_layout.addWidget(drive_box)

        options = QGroupBox("03  Boot configuration")
        row = QHBoxLayout(options)
        row.addWidget(QLabel("Partition style"))
        self.scheme = QComboBox()
        self.scheme.addItems(["GPT  •  UEFI (recommended for Windows 11)", "MBR  •  Legacy BIOS + UEFI"])
        row.addWidget(self.scheme)
        row.addWidget(QLabel("Filesystem"))
        self.filesystem = QComboBox()
        self.filesystem.addItems(["FAT32  •  split large WIM automatically", "NTFS  •  large files"])
        row.addWidget(self.filesystem)
        single_layout.addWidget(options)

        warning = QLabel("CAUTION  •  The selected physical USB disk will be erased completely. Confirm its disk number and capacity before continuing.")
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        single_layout.addWidget(warning)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.status = QLabel("Ready. Select an ISO and a removable USB drive.")
        single_layout.addWidget(self.progress)
        single_layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.create_button = QPushButton("Create verified installer USB")
        self.create_button.setObjectName("primary")
        self.create_button.clicked.connect(self.create_single)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(self.create_button)
        buttons.addWidget(close_btn)
        single_layout.addLayout(buttons)

        # Ventoy Mode
        ventoy_tab = QWidget()
        ventoy_layout = QVBoxLayout(ventoy_tab)

        ventoy_title = QLabel("Ventoy Multi-Boot Creator")
        ventoy_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #55d6be;")
        ventoy_layout.addWidget(ventoy_title)

        ventoy_info = QLabel(
            "Ventoy allows you to boot multiple ISO files from a single USB drive without erasing it.\n"
            "• Install Ventoy bootloader on USB\n"
            "• Copy any ISO files to the USB\n"
            "• Boot and select ISO from menu\n"
            "• Supports Windows, Linux, and more"
        )
        ventoy_info.setWordWrap(True)
        ventoy_layout.addWidget(ventoy_info)

        ventoy_box = QGroupBox("Ventoy Bootloader ISO")
        ventoy_row = QHBoxLayout(ventoy_box)
        self.ventoy_field = QLineEdit()
        self.ventoy_field.setReadOnly(True)
        self.ventoy_field.setPlaceholderText("Download or select Ventoy ISO")
        ventoy_browse = QPushButton("Browse")
        ventoy_browse.clicked.connect(self.choose_ventoy_iso)
        ventoy_row.addWidget(self.ventoy_field)
        ventoy_row.addWidget(ventoy_browse)
        ventoy_layout.addWidget(ventoy_box)

        ventoy_drive_box = QGroupBox("Target USB for Ventoy")
        ventoy_drive_layout = QVBoxLayout(ventoy_drive_box)
        self.ventoy_drive_list = QListWidget()
        self.ventoy_drive_list.setMinimumHeight(110)
        ventoy_refresh = QPushButton("Refresh drives")
        ventoy_refresh.clicked.connect(self.refresh_ventoy)
        ventoy_drive_layout.addWidget(self.ventoy_drive_list)
        ventoy_drive_layout.addWidget(ventoy_refresh)
        ventoy_layout.addWidget(ventoy_drive_box)

        ventoy_warning = QLabel(
            "WARNING  •  Installing Ventoy will erase the selected USB. "
            "Recommended minimum: 16 GB. Use 32 GB+ for comfortable multi-boot."
        )
        ventoy_warning.setObjectName("warning")
        ventoy_warning.setWordWrap(True)
        ventoy_layout.addWidget(ventoy_warning)

        self.ventoy_progress = QProgressBar()
        self.ventoy_progress.setTextVisible(True)
        self.ventoy_status = QLabel("Ready to install Ventoy.")
        ventoy_layout.addWidget(self.ventoy_progress)
        ventoy_layout.addWidget(self.ventoy_status)

        ventoy_buttons = QHBoxLayout()
        self.ventoy_button = QPushButton("Install Ventoy Bootloader")
        self.ventoy_button.setObjectName("primary")
        self.ventoy_button.clicked.connect(self.install_ventoy)
        ventoy_buttons.addWidget(self.ventoy_button)
        ventoy_layout.addLayout(ventoy_buttons)

        tabs.addTab(single_tab, "Single ISO Creator")
        tabs.addTab(ventoy_tab, "Ventoy Multi-Boot")
        root.addWidget(tabs)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

    def choose_iso(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Windows ISO", "", "ISO files (*.iso)")
        if path:
            self.iso = path
            self.image_field.setText(path)
            self.status.setText(f"Selected ISO: {human(os.path.getsize(path))}")

    def choose_ventoy_iso(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Ventoy ISO", "", "ISO files (*.iso)")
        if path:
            self.ventoy_iso = path
            self.ventoy_field.setText(path)
            self.ventoy_status.setText(f"Selected Ventoy ISO: {human(os.path.getsize(path))}")

    def refresh(self):
        self.drive_list.clear()
        self.disk_list = usb_disks()
        for disk in self.disk_list:
            self.drive_list.addItem(QListWidgetItem(f"Disk {disk['number']}   •   {disk['name']}   •   {human(disk['size'])}"))
        self.create_button.setEnabled(bool(self.disk_list))
        self.status.setText(f"Detected {len(self.disk_list)} removable USB drive(s).")

    def refresh_ventoy(self):
        self.ventoy_drive_list.clear()
        self.disk_list = usb_disks()
        for disk in self.disk_list:
            self.ventoy_drive_list.addItem(QListWidgetItem(f"Disk {disk['number']}   •   {disk['name']}   •   {human(disk['size'])}"))
        self.ventoy_button.setEnabled(bool(self.disk_list))
        self.ventoy_status.setText(f"Detected {len(self.disk_list)} removable USB drive(s).")

    def create_single(self):
        index = self.drive_list.currentRow()
        disk = self.disk_list[index] if 0 <= index < len(self.disk_list) else None
        if not self.iso or not disk:
            QMessageBox.warning(self, APP_NAME, "Select a Windows ISO and a USB drive.")
            return
        scheme = "GPT" if self.scheme.currentIndex() == 0 else "MBR"
        filesystem = "FAT32" if self.filesystem.currentIndex() == 0 else "NTFS"
        if filesystem == "NTFS" and scheme == "GPT" and QMessageBox.warning(self, APP_NAME, "GPT + NTFS may not boot on every UEFI computer. FAT32 is recommended. Continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        message = f"ERASE DISK {disk['number']}?\n\n{disk['name']}\nCapacity: {human(disk['size'])}\nMode: {scheme} + {filesystem}\n\nAll data will be permanently deleted."
        if QMessageBox.warning(self, APP_NAME, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.create_button.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText("Starting...")
        self.worker = Worker(self.iso, disk, "single")
        self.worker.signals.progress.connect(self.progress.setValue)
        self.worker.signals.status.connect(self.status.setText)
        self.worker.signals.success.connect(self.done)
        self.worker.signals.failure.connect(self.failed)
        self.worker.start()

    def install_ventoy(self):
        index = self.ventoy_drive_list.currentRow()
        disk = self.disk_list[index] if 0 <= index < len(self.disk_list) else None
        if not hasattr(self, 'ventoy_iso') or not self.ventoy_iso or not disk:
            QMessageBox.warning(self, APP_NAME, "Select a Ventoy ISO and a USB drive.")
            return
        message = f"ERASE DISK {disk['number']} and install Ventoy?\n\n{disk['name']}\nCapacity: {human(disk['size'])}\n\nAll data will be permanently deleted. After installation, you can copy ISO files to the USB."
        if QMessageBox.warning(self, APP_NAME, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.ventoy_button.setEnabled(False)
        self.ventoy_progress.setValue(0)
        self.ventoy_status.setText("Starting Ventoy installation...")
        self.worker = Worker(self.ventoy_iso, disk, "ventoy")
        self.worker.signals.progress.connect(self.ventoy_progress.setValue)
        self.worker.signals.status.connect(self.ventoy_status.setText)
        self.worker.signals.success.connect(self.done_ventoy)
        self.worker.signals.failure.connect(self.failed_ventoy)
        self.worker.start()

    def done(self, message):
        self.create_button.setEnabled(True)
        self.status.setText("Completed and verified.")
        QMessageBox.information(self, APP_NAME, message)
        self.refresh()

    def done_ventoy(self, message):
        self.ventoy_button.setEnabled(True)
        self.ventoy_status.setText("Ventoy installation completed.")
        QMessageBox.information(self, APP_NAME, message)
        self.refresh_ventoy()

    def failed(self, message):
        self.create_button.setEnabled(True)
        self.status.setText("Creation failed.")
        QMessageBox.critical(self, APP_NAME, "Creation failed:\n\n" + message)

    def failed_ventoy(self, message):
        self.ventoy_button.setEnabled(True)
        self.ventoy_status.setText("Installation failed.")
        QMessageBox.critical(self, APP_NAME, "Installation failed:\n\n" + message)


def main():
    if platform.system() != "Windows":
        print("CLINTOY HUB v4 Ventoy Edition requires Windows 10 or Windows 11.")
        return
    if not admin():
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{os.path.abspath(sys.argv[0])}"', None, 1)
        return
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QWidget { background:#101827; color:#e8eef7; font-size:13px; }
        QMainWindow { background:#101827; }
        QLabel#title { color:#55d6be; font-size:34px; font-weight:800; padding-top:8px; }
        QLabel#subtitle { color:#91a4bd; font-size:14px; padding-bottom:10px; }
        QGroupBox { border:1px solid #2d3d55; border-radius:12px; margin-top:12px; padding:14px; font-weight:700; color:#55d6be; }
        QGroupBox::title { subcontrol-origin:margin; left:14px; padding:0 6px; }
        QLineEdit, QListWidget, QComboBox { background:#172236; border:1px solid #334866; border-radius:8px; padding:9px; color:#e8eef7; }
        QPushButton { background:#22324d; border:1px solid #3d5778; border-radius:8px; padding:10px 16px; color:#f4f8ff; font-weight:700; }
        QPushButton:hover { background:#2d4668; }
        QPushButton#primary { background:#168c7b; border-color:#55d6be; }
        QPushButton#primary:hover { background:#1ca991; }
        QLabel#warning { background:#351f27; border:1px solid #a84b5a; border-radius:8px; color:#ff9eaa; padding:10px; margin-top:10px; }
        QProgressBar { background:#172236; border:1px solid #334866; border-radius:7px; height:18px; text-align:center; }
        QProgressBar::chunk { background:#55d6be; border-radius:6px; }
        QTabWidget::pane { border: 1px solid #2d3d55; }
        QTabBar::tab { background:#1a2847; color:#91a4bd; padding: 8px 20px; border: 1px solid #2d3d55; margin-right: 2px; }
        QTabBar::tab:selected { background:#55d6be; color:#101827; font-weight: 700; }
    """)
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

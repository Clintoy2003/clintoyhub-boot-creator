import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QProgressBar, QVBoxLayout, QWidget,
)

APP_NAME = "CLINTOY HUB Boot Creator"
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


def total_files(root, excluded):
    total = 0
    for directory, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower() in excluded:
                continue
            try:
                total += os.path.getsize(os.path.join(directory, filename))
            except OSError:
                pass
    return total


def copy_files(source, target, excluded, progress, status):
    args = ["robocopy", source, target, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/NP", "/NFL", "/NDL"]
    for filename in excluded:
        args += ["/XF", filename]
    result = run(args)
    if result.returncode >= 8:
        raise RuntimeError(result.stdout or result.stderr or "Robocopy failed.")
    progress(78)
    status("Files copied. Verifying the installer contents...")


def verify_files(source, target, excluded, progress, status):
    checked = 0
    expected = total_files(source, excluded)
    for directory, _, filenames in os.walk(source):
        for filename in filenames:
            if filename.lower() in excluded:
                continue
            source_file = Path(directory) / filename
            relative = source_file.relative_to(source)
            target_file = Path(target) / relative
            if not target_file.exists():
                raise RuntimeError(f"Missing copied file: {relative}")
            if source_file.stat().st_size != target_file.stat().st_size:
                raise RuntimeError(f"File size mismatch: {relative}")
            checked += source_file.stat().st_size
            progress(78 + int(15 * checked / expected) if expected else 93)
    status("File verification passed.")


def split_wim(source, destination, progress, status):
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    status("Splitting install.wim into FAT32-compatible files...")
    result = run(["dism.exe", "/English", "/Split-Image", f"/ImageFile:{source}", f"/SWMFile:{destination}", "/FileSize:3800"])
    if result.returncode != 0:
        raise RuntimeError(result.stdout or result.stderr or "DISM could not split install.wim.")
    if not Path(destination).exists():
        raise RuntimeError("DISM completed without creating install.swm.")
    progress(95)


def create_installer(iso, disk, scheme, filesystem, progress, status):
    if platform.system() != "Windows":
        raise RuntimeError("CLINTOY HUB v3 currently supports Windows 10 and Windows 11.")
    iso = os.path.abspath(iso)
    if not os.path.isfile(iso) or not iso.lower().endswith(".iso"):
        raise RuntimeError("Select a valid Windows ISO file.")
    if disk["readonly"]:
        raise RuntimeError("The selected USB is read-only.")
    iso_size = os.path.getsize(iso)
    if iso_size + MIN_EXTRA_BYTES > disk["size"]:
        raise RuntimeError(f"Not enough USB capacity. Use a larger drive (recommended: 32 GB or more).")

    iso_drive = None
    try:
        status(f"Preparing Disk {disk['number']} as {scheme}/{filesystem}...")
        usb = prepare_disk(disk["number"], scheme, filesystem)
        progress(8)
        iso_drive = mount_iso(iso)
        iso_root = iso_drive + "\\"
        sources = Path(iso_root) / "sources"
        wim = sources / "install.wim"
        esd = sources / "install.esd"
        excluded = set()
        split_required = filesystem == "FAT32" and wim.exists() and wim.stat().st_size > MAX_FAT32_FILE
        if split_required:
            excluded.add("install.wim")
        if filesystem == "FAT32" and esd.exists() and esd.stat().st_size > MAX_FAT32_FILE:
            raise RuntimeError("install.esd is larger than 4 GB. Choose NTFS or use an install.wim ISO.")
        status("Copying Windows installation files...")
        copy_files(iso_root, usb + "\\", excluded, progress, status)
        verify_files(iso_root, usb + "\\", excluded, progress, status)
        if split_required:
            split_wim(wim, Path(usb + "\\sources\\install.swm"), progress, status)
        bootsect = Path(iso_root) / "boot" / "bootsect.exe"
        if scheme == "MBR" and bootsect.exists():
            run([str(bootsect), "/nt60", usb, "/force", "/mbr"])
        progress(100)
        status("Installer USB is ready and verified.")
    finally:
        if iso_drive:
            dismount_iso(iso)


class Signals(QObject):
    progress = Signal(int)
    status = Signal(str)
    success = Signal(str)
    failure = Signal(str)


class Worker:
    def __init__(self, iso, disk, scheme, filesystem):
        self.iso, self.disk, self.scheme, self.filesystem = iso, disk, scheme, filesystem
        self.signals = Signals()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            create_installer(self.iso, self.disk, self.scheme, self.filesystem, self.signals.progress.emit, self.signals.status.emit)
            self.signals.success.emit("USB creation and verification completed. Safely eject the USB before removing it.")
        except Exception as error:
            self.signals.failure.emit(str(error))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.iso = None
        self.disk_list = []
        self.worker = None
        self.setWindowTitle(APP_NAME + " v3")
        self.resize(820, 620)
        self.build_ui()
        self.refresh()

    def build_ui(self):
        root = QVBoxLayout()
        title = QLabel("CLINTOY HUB")
        title.setObjectName("title")
        subtitle = QLabel("Boot Creator  •  Verified Windows installer media")
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        image_box = QGroupBox("01  Windows ISO")
        image_row = QHBoxLayout(image_box)
        self.image_field = QLineEdit(); self.image_field.setReadOnly(True); self.image_field.setPlaceholderText("Choose an official Windows ISO")
        browse = QPushButton("Browse ISO"); browse.clicked.connect(self.choose_iso)
        image_row.addWidget(self.image_field); image_row.addWidget(browse)
        root.addWidget(image_box)

        drive_box = QGroupBox("02  Target USB")
        drive_layout = QVBoxLayout(drive_box)
        self.drive_list = QListWidget(); self.drive_list.setMinimumHeight(110)
        refresh = QPushButton("Refresh removable drives"); refresh.clicked.connect(self.refresh)
        drive_layout.addWidget(self.drive_list); drive_layout.addWidget(refresh)
        root.addWidget(drive_box)

        options = QGroupBox("03  Boot configuration")
        row = QHBoxLayout(options)
        row.addWidget(QLabel("Partition style"))
        self.scheme = QComboBox(); self.scheme.addItems(["GPT  •  UEFI (recommended for Windows 11)", "MBR  •  Legacy BIOS + UEFI"])
        row.addWidget(self.scheme)
        row.addWidget(QLabel("Filesystem"))
        self.filesystem = QComboBox(); self.filesystem.addItems(["FAT32  •  split large WIM automatically", "NTFS  •  large files"])
        row.addWidget(self.filesystem)
        root.addWidget(options)

        warning = QLabel("CAUTION  •  The selected physical USB disk will be erased completely. Confirm its disk number and capacity before continuing.")
        warning.setObjectName("warning"); warning.setWordWrap(True); root.addWidget(warning)
        self.progress = QProgressBar(); self.progress.setTextVisible(True)
        self.status = QLabel("Ready. Select an ISO and a removable USB drive.")
        root.addWidget(self.progress); root.addWidget(self.status)

        buttons = QHBoxLayout()
        self.create_button = QPushButton("Create verified installer USB"); self.create_button.setObjectName("primary"); self.create_button.clicked.connect(self.create)
        close = QPushButton("Close"); close.clicked.connect(self.close)
        buttons.addWidget(self.create_button); buttons.addWidget(close); root.addLayout(buttons)
        container = QWidget(); container.setLayout(root); self.setCentralWidget(container)

    def choose_iso(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Windows ISO", "", "ISO files (*.iso)")
        if path:
            self.iso = path; self.image_field.setText(path); self.status.setText(f"Selected ISO: {human(os.path.getsize(path))}")

    def refresh(self):
        self.drive_list.clear(); self.disk_list = usb_disks()
        for disk in self.disk_list:
            self.drive_list.addItem(QListWidgetItem(f"Disk {disk['number']}   •   {disk['name']}   •   {human(disk['size'])}"))
        self.create_button.setEnabled(bool(self.disk_list))
        self.status.setText(f"Detected {len(self.disk_list)} removable USB drive(s).")

    def create(self):
        index = self.drive_list.currentRow()
        disk = self.disk_list[index] if 0 <= index < len(self.disk_list) else None
        if not self.iso or not disk:
            QMessageBox.warning(self, APP_NAME, "Select a Windows ISO and a USB drive."); return
        scheme = "GPT" if self.scheme.currentIndex() == 0 else "MBR"
        filesystem = "FAT32" if self.filesystem.currentIndex() == 0 else "NTFS"
        if filesystem == "NTFS" and scheme == "GPT" and QMessageBox.warning(self, APP_NAME, "GPT + NTFS may not boot on every UEFI computer. FAT32 is recommended. Continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        message = f"ERASE DISK {disk['number']}?\n\n{disk['name']}\nCapacity: {human(disk['size'])}\nMode: {scheme} + {filesystem}\n\nAll data will be permanently deleted."
        if QMessageBox.warning(self, APP_NAME, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.create_button.setEnabled(False); self.progress.setValue(0); self.status.setText("Starting...")
        self.worker = Worker(self.iso, disk, scheme, filesystem)
        self.worker.signals.progress.connect(self.progress.setValue); self.worker.signals.status.connect(self.status.setText)
        self.worker.signals.success.connect(self.done); self.worker.signals.failure.connect(self.failed); self.worker.start()

    def done(self, message):
        self.create_button.setEnabled(True); self.status.setText("Completed and verified."); QMessageBox.information(self, APP_NAME, message); self.refresh()

    def failed(self, message):
        self.create_button.setEnabled(True); self.status.setText("Creation failed."); QMessageBox.critical(self, APP_NAME, "Creation failed:\n\n" + message)


def main():
    if platform.system() != "Windows":
        print("CLINTOY HUB v3 requires Windows 10 or Windows 11."); return
    if not admin():
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{os.path.abspath(sys.argv[0])}"', None, 1); return
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
    """)
    window = Window(); window.show(); sys.exit(app.exec())


if __name__ == "__main__":
    main()

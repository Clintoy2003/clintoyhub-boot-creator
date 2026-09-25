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
    QPushButton, QProgressBar, QVBoxLayout, QWidget
)

APP_NAME = "CLINTOY HUB Boot Creator"
MAX_FAT32_FILE = 4 * 1024**3


def command(args, input_text=None):
    return subprocess.run(args, input=input_text, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def ps(script):
    return command(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])


def admin():
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin()) if platform.system() == "Windows" else False


def quote_ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def human(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def drives():
    result = ps("Get-Disk | Where-Object {$_.BusType -eq 'USB'} | "
                "Select-Object Number,FriendlyName,Size,IsReadOnly,OperationalStatus | "
                "ConvertTo-Json -Compress")
    if result.returncode or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"number": int(x["Number"]), "name": x.get("FriendlyName") or "USB drive",
             "size": int(x.get("Size") or 0), "readonly": bool(x.get("IsReadOnly"))}
            for x in data if x.get("Number") is not None]


def drive_letter(number):
    result = ps(f"(Get-Partition -DiskNumber {number} | Where-Object {{$_.DriveLetter}} | "
                "Select-Object -First 1 -ExpandProperty DriveLetter)")
    letter = result.stdout.strip()
    if result.returncode or not letter:
        raise RuntimeError("Windows did not assign a drive letter to the USB.")
    return letter.upper() + ":"


def prepare_disk(number, scheme, filesystem):
    lines = [f"select disk {number}", "attributes disk clear readonly", "clean"]
    if scheme == "GPT":
        lines.append("convert gpt")
    else:
        lines += ["convert mbr"]
    lines += ["create partition primary"]
    if scheme == "MBR":
        lines.append("active")
    lines += [f"format fs={filesystem.lower()} quick label=CLINTOYUSB", "assign", "exit"]
    result = command(["diskpart.exe"], "\n".join(lines) + "\n")
    if result.returncode != 0 or "error" in result.stdout.lower():
        raise RuntimeError(result.stderr or result.stdout or "DiskPart failed.")
    # Give Windows a moment to mount the newly-created volume.
    for _ in range(20):
        try:
            return drive_letter(number)
        except RuntimeError:
            threading.Event().wait(0.5)
    raise RuntimeError("The USB partition was created but no drive letter appeared.")


def mount_iso(path):
    result = ps(f"$i=Mount-DiskImage -ImagePath {quote_ps(path)} -PassThru; "
                "$i | Get-Volume | Select-Object -First 1 -ExpandProperty DriveLetter")
    letter = result.stdout.strip()
    if result.returncode or not letter:
        raise RuntimeError(result.stderr or "Could not mount the ISO.")
    return letter.upper() + ":"


def dismount_iso(path):
    ps(f"Dismount-DiskImage -ImagePath {quote_ps(path)}")


def image_file(iso_root):
    sources = Path(iso_root) / "sources"
    wim = sources / "install.wim"
    esd = sources / "install.esd"
    if wim.exists():
        return wim
    if esd.exists():
        return esd
    return None


def copy_files(source, target, exclude, progress, status):
    source = str(Path(source))
    target = str(Path(target))
    # robocopy is substantially safer and faster than Python copying on Windows.
    args = ["robocopy", source, target, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/NP", "/NFL", "/NDL"]
    for item in exclude:
        args += ["/XF", item]
    result = command(args)
    if result.returncode >= 8:
        raise RuntimeError(result.stdout or result.stderr or "Robocopy failed.")
    progress(80)
    status("ISO files copied. Finalizing installation image...")


def split_wim(wim, destination, progress, status):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    status(f"Splitting {wim.name} into FAT32-compatible files...")
    result = command(["dism.exe", "/English", "/Split-Image", f"/ImageFile:{wim}",
                      f"/SWMFile:{destination}", "/FileSize:3800"])
    if result.returncode != 0:
        raise RuntimeError(result.stdout or result.stderr or "DISM could not split the Windows image.")
    progress(96)


def create_installer(iso, disk, scheme, filesystem, progress, status):
    if platform.system() != "Windows":
        raise RuntimeError("This release supports Windows 10/11 only.")
    iso = os.path.abspath(iso)
    if not os.path.isfile(iso) or not iso.lower().endswith(".iso"):
        raise RuntimeError("Select a valid Windows ISO file.")
    if os.path.getsize(iso) > disk["size"]:
        raise RuntimeError("The ISO is larger than the USB capacity. Use a larger USB drive.")

    iso_drive = None
    try:
        status(f"Erasing Disk {disk['number']} and creating {scheme}/{filesystem} media...")
        usb = prepare_disk(disk["number"], scheme, filesystem)
        progress(10)
        iso_drive = mount_iso(iso)
        root = iso_drive + "\\"
        special = image_file(root)
        excludes = []
        if filesystem == "FAT32" and special and special.stat().st_size > MAX_FAT32_FILE:
            if special.name.lower() != "install.wim":
                raise RuntimeError("This ISO has an install.esd larger than 4 GB. Choose NTFS or an ISO with install.wim.")
            excludes = [special.name]
        status("Copying Windows installation files...")
        copy_files(root, usb + "\\", excludes, progress, status)
        if excludes:
            split_wim(special, Path(usb + "\\sources\\install.swm"), progress, status)
        # Use bootsect when present; it is not required for UEFI but helps MBR/BIOS media.
        bootsect = Path(root) / "boot" / "bootsect.exe"
        if scheme == "MBR" and bootsect.exists():
            command([str(bootsect), "/nt60", usb, "/force", "/mbr"])
        command(["cmd.exe", "/c", "sync"])
        progress(100)
        status("Installer USB is ready.")
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
            create_installer(self.iso, self.disk, self.scheme, self.filesystem,
                             self.signals.progress.emit, self.signals.status.emit)
            self.signals.success.emit("CLINTOY HUB finished successfully. Safely eject the USB before removing it.")
        except Exception as error:
            self.signals.failure.emit(str(error))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.iso = None
        self.disk_list = []
        self.worker = None
        self.setWindowTitle(APP_NAME + " v2")
        self.resize(760, 570)
        self.ui()
        self.refresh()

    def ui(self):
        root = QVBoxLayout()
        title = QLabel(APP_NAME + " v2")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        root.addWidget(title)
        root.addWidget(QLabel("Windows installer media with GPT/MBR and large-WIM handling."))

        image_box = QGroupBox("1. Windows ISO")
        image_row = QHBoxLayout(image_box)
        self.image_field = QLineEdit(); self.image_field.setReadOnly(True)
        browse = QPushButton("Browse"); browse.clicked.connect(self.choose_iso)
        image_row.addWidget(self.image_field); image_row.addWidget(browse)
        root.addWidget(image_box)

        drive_box = QGroupBox("2. Target USB drive")
        drive_row = QVBoxLayout(drive_box)
        self.drive_list = QListWidget()
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.refresh)
        drive_row.addWidget(self.drive_list); drive_row.addWidget(refresh)
        root.addWidget(drive_box)

        options = QGroupBox("3. Boot options")
        row = QHBoxLayout(options)
        row.addWidget(QLabel("Partition style:"))
        self.scheme = QComboBox(); self.scheme.addItems(["GPT (UEFI - Windows 11 recommended)", "MBR (Legacy BIOS + UEFI)"])
        row.addWidget(self.scheme)
        row.addWidget(QLabel("Filesystem:"))
        self.filesystem = QComboBox(); self.filesystem.addItems(["FAT32 (split install.wim)", "NTFS (large files)"])
        row.addWidget(self.filesystem)
        root.addWidget(options)

        warning = QLabel("WARNING: The selected USB disk will be completely erased. Verify the disk number and capacity.")
        warning.setStyleSheet("color:#b00020;font-weight:bold;padding:6px;")
        warning.setWordWrap(True); root.addWidget(warning)
        self.progress = QProgressBar(); self.status = QLabel("Ready.")
        root.addWidget(self.progress); root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.create_button = QPushButton("Create Install USB"); self.create_button.clicked.connect(self.create)
        close = QPushButton("Close"); close.clicked.connect(self.close)
        buttons.addWidget(self.create_button); buttons.addWidget(close); root.addLayout(buttons)
        container = QWidget(); container.setLayout(root); self.setCentralWidget(container)

    def choose_iso(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Windows ISO", "", "ISO files (*.iso)")
        if path:
            self.iso = path; self.image_field.setText(path)

    def refresh(self):
        self.drive_list.clear(); self.disk_list = drives()
        for disk in self.disk_list:
            self.drive_list.addItem(QListWidgetItem(f"Disk {disk['number']} — {disk['name']} — {human(disk['size'])}"))
        self.create_button.setEnabled(bool(self.disk_list))
        self.status.setText(f"Detected {len(self.disk_list)} removable USB drive(s).")

    def create(self):
        index = self.drive_list.currentRow()
        disk = self.disk_list[index] if 0 <= index < len(self.disk_list) else None
        if not self.iso or not disk:
            QMessageBox.warning(self, APP_NAME, "Select a Windows ISO and a USB drive."); return
        scheme = "GPT" if self.scheme.currentIndex() == 0 else "MBR"
        filesystem = "FAT32" if self.filesystem.currentIndex() == 0 else "NTFS"
        if filesystem == "NTFS" and scheme == "GPT":
            text = "GPT/NTFS may not boot on every UEFI computer. FAT32 is recommended for maximum compatibility. Continue?"
            if QMessageBox.warning(self, APP_NAME, text, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        confirmation = (f"ERASE DISK {disk['number']}?\n\n{disk['name']}\n{human(disk['size'])}\n\n"
                        f"Mode: {scheme} + {filesystem}\n\nAll data will be permanently deleted.")
        if QMessageBox.warning(self, APP_NAME, confirmation, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.create_button.setEnabled(False); self.progress.setValue(0)
        self.worker = Worker(self.iso, disk, scheme, filesystem)
        self.worker.signals.progress.connect(self.progress.setValue)
        self.worker.signals.status.connect(self.status.setText)
        self.worker.signals.success.connect(self.done)
        self.worker.signals.failure.connect(self.failed)
        self.worker.start()

    def done(self, message):
        self.create_button.setEnabled(True); self.status.setText("Completed.")
        QMessageBox.information(self, APP_NAME, message); self.refresh()

    def failed(self, message):
        self.create_button.setEnabled(True); self.status.setText("Failed.")
        QMessageBox.critical(self, APP_NAME, "Creation failed:\n\n" + message)


def main():
    if platform.system() != "Windows":
        print("CLINTOY HUB v2 requires Windows 10 or Windows 11."); return
    if not admin():
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{os.path.abspath(sys.argv[0])}"', None, 1); return
    app = QApplication(sys.argv); window = Window(); window.show(); sys.exit(app.exec())


if __name__ == "__main__":
    main()

import json
import os
import platform
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "CLINTOY HUB Boot Creator"


def run_command(command, input_text=None):
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def is_admin():
    if platform.system() == "Windows":
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return hasattr(os, "geteuid") and os.geteuid() == 0


def format_bytes(value):
    value = int(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "Unknown"


def detect_windows_usb_drives():
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "Get-Disk | Where-Object {$_.BusType -eq 'USB'} | "
        "Select-Object Number,FriendlyName,Size | ConvertTo-Json -Compress",
    ]
    result = run_command(command)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "number": int(d["Number"]),
            "name": d.get("FriendlyName") or "USB drive",
            "size": int(d.get("Size") or 0),
            "path": f"PhysicalDrive{int(d['Number'])}",
        }
        for d in data if d.get("Number") is not None
    ]


def detect_drives():
    return detect_windows_usb_drives() if platform.system() == "Windows" else []


def get_drive_letter(drive_number):
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        f"(Get-Partition -DiskNumber {drive_number} | Where-Object {{$_.DriveLetter}} | Select-Object -First 1 -ExpandProperty DriveLetter)",
    ]
    result = run_command(command)
    letter = result.stdout.strip()
    if not letter:
        raise RuntimeError("Windows did not assign a drive letter to the USB.")
    return f"{letter}:"


def create_partitioned_usb(drive_number):
    script = (
        f"select disk {drive_number}\n"
        "attributes disk clear readonly\n"
        "clean\n"
        "convert gpt\n"
        "create partition primary\n"
        "format fs=fat32 quick label=CLINTOYUSB\n"
        "assign\n"
        "exit\n"
    )
    result = run_command(["diskpart"], script)
    if result.returncode != 0 or "DiskPart successfully" not in result.stdout:
        raise RuntimeError(result.stderr or result.stdout or "DiskPart failed.")
    return get_drive_letter(drive_number)


def mount_iso(iso_path):
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        f"Mount-DiskImage -ImagePath '{iso_path}' -PassThru | Get-Volume | Select-Object -First 1 -ExpandProperty DriveLetter",
    ]
    result = run_command(command)
    letter = result.stdout.strip()
    if result.returncode != 0 or not letter:
        raise RuntimeError(result.stderr or "Could not mount ISO.")
    return f"{letter}:"


def dismount_iso(iso_path):
    run_command([
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        f"Dismount-DiskImage -ImagePath '{iso_path}'",
    ])


def copy_tree(source, target, progress, status):
    total = 0
    for root, _, files in os.walk(source):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                pass

    copied = 0
    for root, _, files in os.walk(source):
        relative = os.path.relpath(root, source)
        destination_root = target if relative == "." else os.path.join(target, relative)
        os.makedirs(destination_root, exist_ok=True)
        for filename in files:
            src = os.path.join(root, filename)
            dst = os.path.join(destination_root, filename)
            with open(src, "rb") as source_file, open(dst, "wb") as target_file:
                while True:
                    block = source_file.read(1024 * 1024)
                    if not block:
                        break
                    target_file.write(block)
                    copied += len(block)
                    progress(int(copied * 100 / total) if total else 100)
            status(f"Copying installation files: {copied:,} / {total:,} bytes")


def create_windows_installer(iso_path, drive, progress, status):
    if platform.system() != "Windows":
        raise RuntimeError("Windows installer mode is currently supported on Windows only.")

    status("Creating GPT/FAT32 installer partition...")
    usb_letter = create_partitioned_usb(drive["number"])
    iso_letter = mount_iso(iso_path)
    try:
        status("Copying Windows installer files...")
        copy_tree(iso_letter + "\\", usb_letter + "\\", progress, status)
        # bootsect is optional and may not be present in PATH.
        bootsect = os.path.join(iso_letter + "\\", "boot", "bootsect.exe")
        if os.path.exists(bootsect):
            run_command([bootsect, "/nt60", usb_letter, "/force", "/mbr"])
    finally:
        dismount_iso(iso_path)
    progress(100)
    status("Installer USB created successfully.")


class Signals(QObject):
    progress = Signal(int)
    status = Signal(str)
    success = Signal(str)
    failure = Signal(str)


class Worker:
    def __init__(self, iso, drive):
        self.iso = iso
        self.drive = drive
        self.signals = Signals()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            create_windows_installer(
                self.iso, self.drive,
                self.signals.progress.emit,
                self.signals.status.emit,
            )
            self.signals.success.emit("The installer USB is ready. Restart the target PC and boot from USB.")
        except Exception as error:
            self.signals.failure.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.iso = None
        self.drives = []
        self.worker = None
        self.setWindowTitle(APP_NAME)
        self.resize(700, 500)
        self.build_ui()
        self.refresh()

    def build_ui(self):
        root = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        root.addWidget(title)
        root.addWidget(QLabel("Create a partitioned Windows installer USB."))

        image_box = QGroupBox("1. Windows ISO")
        image_row = QHBoxLayout(image_box)
        self.image_field = QLineEdit()
        self.image_field.setReadOnly(True)
        browse = QPushButton("Browse")
        browse.clicked.connect(self.choose_iso)
        image_row.addWidget(self.image_field)
        image_row.addWidget(browse)
        root.addWidget(image_box)

        drive_box = QGroupBox("2. Target USB drive")
        drive_layout = QVBoxLayout(drive_box)
        self.drive_list = QListWidget()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        drive_layout.addWidget(self.drive_list)
        drive_layout.addWidget(refresh)
        root.addWidget(drive_box)

        warning = QLabel("WARNING: The selected USB drive will be completely erased.")
        warning.setStyleSheet("color: #b00020; font-weight: bold;")
        root.addWidget(warning)

        self.progress = QProgressBar()
        self.status = QLabel("Ready.")
        root.addWidget(self.progress)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        self.create_button = QPushButton("Create Install USB")
        self.create_button.clicked.connect(self.create)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(self.create_button)
        buttons.addWidget(close)
        root.addLayout(buttons)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

    def choose_iso(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Windows ISO", "", "ISO files (*.iso)")
        if path:
            self.iso = path
            self.image_field.setText(path)

    def refresh(self):
        self.drive_list.clear()
        self.drives = detect_drives()
        for drive in self.drives:
            self.drive_list.addItem(QListWidgetItem(
                f"{drive['name']} — {format_bytes(drive['size'])} — Disk {drive['number']}"
            ))
        self.create_button.setEnabled(bool(self.drives))
        self.status.setText(f"Detected {len(self.drives)} USB drive(s).")

    def selected_drive(self):
        row = self.drive_list.currentRow()
        return self.drives[row] if 0 <= row < len(self.drives) else None

    def create(self):
        drive = self.selected_drive()
        if not self.iso or not drive:
            QMessageBox.warning(self, APP_NAME, "Select a Windows ISO and USB drive.")
            return
        answer = QMessageBox.warning(
            self, APP_NAME,
            f"ERASE DISK {drive['number']} ({drive['name']}, {format_bytes(drive['size'])})?\n\nAll data will be lost.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.create_button.setEnabled(False)
        self.progress.setValue(0)
        self.worker = Worker(self.iso, drive)
        self.worker.signals.progress.connect(self.progress.setValue)
        self.worker.signals.status.connect(self.status.setText)
        self.worker.signals.success.connect(self.success)
        self.worker.signals.failure.connect(self.failure)
        self.worker.start()

    def success(self, message):
        self.create_button.setEnabled(True)
        QMessageBox.information(self, APP_NAME, message)
        self.refresh()

    def failure(self, message):
        self.create_button.setEnabled(True)
        QMessageBox.critical(self, APP_NAME, "Creation failed:\n\n" + message)


def main():
    if platform.system() != "Windows":
        print("This Windows installer mode must run on Windows.")
        return
    if not is_admin():
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{os.path.abspath(sys.argv[0])}"', None, 1)
        return
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

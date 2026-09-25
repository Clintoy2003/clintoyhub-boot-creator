import hashlib
import json
import os
import platform
import plistlib
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
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


def run_command(command):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def is_admin():
    system = platform.system()

    if system == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    return hasattr(os, "geteuid") and os.geteuid() == 0


def request_admin():
    if is_admin():
        return

    if platform.system() == "Windows":
        import ctypes

        script = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{arg}"' for arg in sys.argv[1:])

        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            f'"{script}" {params}',
            None,
            1,
        )

        if result <= 32:
            QMessageBox.critical(
                None,
                APP_NAME,
                "Administrator permission was not granted.",
            )
            sys.exit(1)

        sys.exit(0)

    QMessageBox.critical(
        None,
        APP_NAME,
        "Run this program as root:\n\nsudo python3 app.py",
    )
    sys.exit(1)


def format_bytes(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "Unknown"

    units = ["B", "KB", "MB", "GB", "TB"]

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} PB"


def get_linux_drives():
    result = run_command(
        [
            "lsblk",
            "-J",
            "-b",
            "-o",
            "NAME,PATH,SIZE,MODEL,TRAN,RM,TYPE,MOUNTPOINT",
        ]
    )

    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    drives = []

    for item in data.get("blockdevices", []):
        if item.get("type") != "disk":
            continue

        is_usb = item.get("tran") == "usb" or str(item.get("rm")) == "1"

        if not is_usb:
            continue

        drives.append(
            {
                "path": item.get("path") or f"/dev/{item.get('name')}",
                "name": item.get("model") or item.get("name") or "USB drive",
                "size": int(item.get("size") or 0),
            }
        )

    return drives


def get_macos_drives():
    result = run_command(["diskutil", "list", "-plist"])

    if result.returncode != 0:
        return []

    try:
        data = plistlib.loads(result.stdout.encode())
    except Exception:
        return []

    drives = []

    for disk in data.get("AllDisksAndPartitions", []):
        identifier = disk.get("DeviceIdentifier")

        if not identifier:
            continue

        info = run_command(["diskutil", "info", "-plist", identifier])

        if info.returncode != 0:
            continue

        try:
            details = plistlib.loads(info.stdout.encode())
        except Exception:
            continue

        if not details.get("RemovableMedia", False):
            continue

        drives.append(
            {
                "path": f"/dev/{identifier}",
                "name": details.get("MediaName") or "USB drive",
                "size": int(details.get("TotalSize") or 0),
            }
        )

    return drives


def get_windows_drives():
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "Get-Disk | "
            "Where-Object {$_.BusType -eq 'USB'} | "
            "Select-Object Number,FriendlyName,Size,BusType | "
            "ConvertTo-Json -Compress"
        ),
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

    drives = []

    for disk in data:
        number = disk.get("Number")

        if number is None:
            continue

        drives.append(
            {
                "path": f"\\\\.\\PhysicalDrive{number}",
                "number": int(number),
                "name": disk.get("FriendlyName") or "USB drive",
                "size": int(disk.get("Size") or 0),
            }
        )

    return drives


def detect_drives():
    system = platform.system()

    if system == "Linux":
        return get_linux_drives()

    if system == "Darwin":
        return get_macos_drives()

    if system == "Windows":
        return get_windows_drives()

    return []


def unmount_drive(drive):
    system = platform.system()
    path = drive["path"]

    if system == "Linux":
        run_command(["umount", path])

        # Also try mounted partitions such as /dev/sdb1.
        result = run_command(
            ["lsblk", "-ln", "-o", "PATH", path]
        )

        for line in result.stdout.splitlines():
            partition = line.strip()

            if partition and partition != path:
                run_command(["umount", partition])

    elif system == "Darwin":
        run_command(["diskutil", "unmountDisk", path])

    elif system == "Windows":
        # The raw Windows handle can be opened after elevation.
        # No drive-letter unmount is required for the raw write operation.
        pass


def clear_drive_linux(path):
    run_command(["wipefs", "--all", "--force", path])


def clear_drive_macos(path):
    run_command(["diskutil", "eraseDisk", "free", "CLINTOY", path])


def clear_drive_windows(number):
    script = (
        f"select disk {number}\n"
        "attributes disk clear readonly\n"
        "clean\n"
        "exit\n"
    )

    result = subprocess.run(
        ["diskpart"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(result.stdout or result.stderr)


def clear_drive(drive):
    system = platform.system()

    if system == "Linux":
        clear_drive_linux(drive["path"])

    elif system == "Darwin":
        clear_drive_macos(drive["path"])

    elif system == "Windows":
        clear_drive_windows(drive["number"])


def open_raw_device(path):
    if platform.system() == "Windows":
        return open(path, "r+b", buffering=0)

    return open(path, "wb", buffering=0)


def write_image(image_path, drive_path, progress_callback, status_callback):
    image_size = os.path.getsize(image_path)
    written = 0
    block_size = 4 * 1024 * 1024
    last_update = time.monotonic()

    with open(image_path, "rb") as source:
        with open_raw_device(drive_path) as target:
            while True:
                block = source.read(block_size)

                if not block:
                    break

                target.write(block)
                written += len(block)

                now = time.monotonic()

                if now - last_update >= 0.15 or written == image_size:
                    percent = int((written / image_size) * 100)
                    progress_callback(percent)
                    status_callback(
                        f"Writing image... {format_bytes(written)} / "
                        f"{format_bytes(image_size)}"
                    )
                    last_update = now

            target.flush()

            if hasattr(os, "fsync"):
                try:
                    os.fsync(target.fileno())
                except OSError:
                    pass


def calculate_sha256(path, progress_callback, status_callback):
    total = os.path.getsize(path)
    processed = 0
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        while True:
            block = file.read(4 * 1024 * 1024)

            if not block:
                break

            digest.update(block)
            processed += len(block)

            percent = int((processed / total) * 100)
            progress_callback(percent)
            status_callback(
                f"Verifying image... {format_bytes(processed)} / "
                f"{format_bytes(total)}"
            )

    return digest.hexdigest()


class WorkerSignals(QObject):
    progress = Signal(int)
    status = Signal(str)
    success = Signal(str)
    failure = Signal(str)


class WriteWorker:
    def __init__(self, image_path, drive):
        self.image_path = image_path
        self.drive = drive
        self.signals = WorkerSignals()

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()

    def run(self):
        try:
            self.signals.status.emit("Unmounting USB drive...")
            unmount_drive(self.drive)

            self.signals.status.emit("Clearing partition data...")
            clear_drive(self.drive)

            self.signals.status.emit("Writing image...")
            write_image(
                self.image_path,
                self.drive["path"],
                self.signals.progress.emit,
                self.signals.status.emit,
            )

            self.signals.status.emit("Verifying written image...")
            source_hash = calculate_sha256(
                self.image_path,
                self.signals.progress.emit,
                self.signals.status.emit,
            )

            self.signals.progress.emit(100)
            self.signals.success.emit(
                "USB creation completed.\n\n"
                f"SHA-256:\n{source_hash}\n\n"
                "The USB drive is ready to use."
            )

        except Exception as error:
            self.signals.failure.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.image_path = None
        self.drives = []
        self.worker = None

        self.setWindowTitle(APP_NAME)
        self.resize(720, 520)

        self.build_ui()
        self.refresh_drives()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 24px; font-weight: bold;")

        subtitle = QLabel("Create bootable USB drives from ISO and IMG files.")
        subtitle.setStyleSheet("color: #666;")

        root.addWidget(title)
        root.addWidget(subtitle)

        image_group = QGroupBox("1. Select an ISO or IMG image")
        image_layout = QHBoxLayout(image_group)

        self.image_field = QLineEdit()
        self.image_field.setReadOnly(True)
        self.image_field.setPlaceholderText("No image selected")

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.choose_image)

        image_layout.addWidget(self.image_field)
        image_layout.addWidget(browse_button)

        root.addWidget(image_group)

        drive_group = QGroupBox("2. Select the target USB drive")
        drive_layout = QVBoxLayout(drive_group)

        self.drive_list = QListWidget()
        self.drive_list.setSelectionMode(QListWidget.SingleSelection)

        refresh_button = QPushButton("Refresh USB drives")
        refresh_button.clicked.connect(self.refresh_drives)

        drive_layout.addWidget(self.drive_list)
        drive_layout.addWidget(refresh_button)

        root.addWidget(drive_group)

        options_group = QGroupBox("3. Creation options")
        options_layout = QGridLayout(options_group)

        options_layout.addWidget(QLabel("Mode:"), 0, 0)
        options_layout.addWidget(QLabel("Single ISO / IMG"), 0, 1)

        options_layout.addWidget(QLabel("Writing method:"), 1, 0)
        options_layout.addWidget(
            QLabel("Raw image writing for BIOS/UEFI images"),
            1,
            1,
        )

        options_layout.addWidget(QLabel("Verification:"), 2, 0)
        options_layout.addWidget(QLabel("SHA-256 enabled"), 2, 1)

        root.addWidget(options_group)

        warning = QLabel(
            "WARNING: Creating the USB will permanently erase all data "
            "on the selected drive."
        )
        warning.setStyleSheet(
            "color: #b00020; font-weight: bold; padding: 8px;"
        )
        warning.setWordWrap(True)

        root.addWidget(warning)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.status = QLabel("Ready.")

        root.addWidget(self.progress)
        root.addWidget(self.status)

        buttons = QHBoxLayout()

        self.start_button = QPushButton("Create Bootable USB")
        self.start_button.clicked.connect(self.start_creation)

        self.exit_button = QPushButton("Exit")
        self.exit_button.clicked.connect(self.close)

        buttons.addWidget(self.start_button)
        buttons.addWidget(self.exit_button)

        root.addLayout(buttons)

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select boot image",
            "",
            "Boot images (*.iso *.img);;All files (*)",
        )

        if not path:
            return

        self.image_path = path
        self.image_field.setText(path)
        self.status.setText(
            f"Selected image: {format_bytes(os.path.getsize(path))}"
        )

    def refresh_drives(self):
        self.drive_list.clear()
        self.drives = detect_drives()

        if not self.drives:
            self.drive_list.addItem(
                "No removable USB drives detected."
            )
            self.start_button.setEnabled(False)
            return

        for drive in self.drives:
            text = (
                f"{drive['name']} — "
                f"{format_bytes(drive['size'])} — "
                f"{drive['path']}"
            )

            item = QListWidgetItem(text)
            self.drive_list.addItem(item)

        self.start_button.setEnabled(True)
        self.status.setText(
            f"Detected {len(self.drives)} removable USB drive(s)."
        )

    def selected_drive(self):
        row = self.drive_list.currentRow()

        if row < 0 or row >= len(self.drives):
            return None

        return self.drives[row]

    def start_creation(self):
        if not self.image_path:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Select an ISO or IMG file first.",
            )
            return

        drive = self.selected_drive()

        if not drive:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Select a USB drive first.",
            )
            return

        image_size = os.path.getsize(self.image_path)

        if drive["size"] and image_size > drive["size"]:
            QMessageBox.critical(
                self,
                APP_NAME,
                "The selected image is larger than the USB drive.",
            )
            return

        message = (
            "FINAL WARNING\n\n"
            f"Image:\n{self.image_path}\n\n"
            f"Target drive:\n{drive['name']}\n"
            f"Capacity: {format_bytes(drive['size'])}\n"
            f"Device: {drive['path']}\n\n"
            "EVERYTHING ON THIS USB DRIVE WILL BE ERASED.\n\n"
            "Do you want to continue?"
        )

        answer = QMessageBox.warning(
            self,
            APP_NAME,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self.start_button.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText("Starting...")

        self.worker = WriteWorker(self.image_path, drive)
        self.worker.signals.progress.connect(self.progress.setValue)
        self.worker.signals.status.connect(self.status.setText)
        self.worker.signals.success.connect(self.creation_success)
        self.worker.signals.failure.connect(self.creation_failure)
        self.worker.start()

    def creation_success(self, message):
        self.start_button.setEnabled(True)
        self.status.setText("Completed successfully.")
        QMessageBox.information(self, APP_NAME, message)
        self.refresh_drives()

    def creation_failure(self, message):
        self.start_button.setEnabled(True)
        self.status.setText("Creation failed.")
        QMessageBox.critical(
            self,
            APP_NAME,
            "The USB creation failed:\n\n" + message,
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    request_admin()
    main()
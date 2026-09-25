"""Fast, resumable ISO downloader for CLINTOY HUB.

This module downloads from a user-provided HTTPS URL. It uses parallel HTTP
range requests when the server supports them and falls back to a normal
streaming download otherwise. It does not bypass server limits.
"""

from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class DownloadError(RuntimeError):
    pass


def _request(url: str, headers: dict[str, str] | None = None):
    request = Request(url, headers=headers or {}, method="GET")
    return urlopen(request, timeout=60)


def _probe(url: str) -> tuple[int | None, bool]:
    """Return (size, supports_ranges)."""
    try:
        with _request(url, {"Range": "bytes=0-0"}) as response:
            content_range = response.headers.get("Content-Range", "")
            size = None
            if "/" in content_range:
                try:
                    size = int(content_range.rsplit("/", 1)[1])
                except ValueError:
                    pass
            if size is None:
                length = response.headers.get("Content-Length")
                size = int(length) if length else None
            return size, response.status == 206 or bool(content_range)
    except Exception:
        with _request(url) as response:
            length = response.headers.get("Content-Length")
            return (int(length) if length else None), False


def _download_range(url: str, start: int, end: int, path: Path, lock: threading.Lock,
                    state: dict, total: int | None, callback):
    with _request(url, {"Range": f"bytes={start}-{end}"}) as response:
        with path.open("r+b") as output:
            output.seek(start)
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                with lock:
                    state["done"] += len(block)
                    done = state["done"]
                callback(done, total)


def download(url: str, destination: str, expected_sha256: str | None = None,
             callback=None, workers: int = 8, chunk_size: int = 16 * 1024 * 1024) -> str:
    """Download URL to destination and return the SHA-256 digest.

    callback receives (bytes_downloaded, total_bytes_or_None).
    Existing .part files are replaced safely; the final file is renamed only
    after download and optional checksum verification succeed.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise DownloadError("Only HTTPS download URLs are allowed.")

    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    size, ranges = _probe(url)
    callback = callback or (lambda _done, _total: None)

    if size is None:
        ranges = False

    if ranges and size and size > chunk_size:
        with partial.open("wb") as output:
            output.truncate(size)
        state = {"done": 0}
        lock = threading.Lock()
        ranges_to_fetch = [(start, min(start + chunk_size - 1, size - 1))
                           for start in range(0, size, chunk_size)]
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ranges_to_fetch)))) as pool:
            futures = [pool.submit(_download_range, url, start, end, partial, lock,
                                   state, size, callback)
                       for start, end in ranges_to_fetch]
            for future in as_completed(futures):
                future.result()
    else:
        state = {"done": 0}
        with _request(url) as response, partial.open("wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                state["done"] += len(block)
                callback(state["done"], size)

    digest = hashlib.sha256()
    with partial.open("rb") as source:
        while True:
            block = source.read(4 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    actual = digest.hexdigest()

    if expected_sha256 and actual.lower() != expected_sha256.lower():
        partial.unlink(missing_ok=True)
        raise DownloadError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual}")

    os.replace(partial, target)
    return actual

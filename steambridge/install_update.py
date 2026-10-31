"""Detached, checksum-verified ZIP replacement. Built as SteamBridgeUpdater.exe."""
from __future__ import annotations

import argparse
import ctypes
from hashlib import sha256
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


def install(archive: Path, expected_sha: str, destination: Path,
            wait_pid: int, executable: str) -> None:
    if sha256(archive.read_bytes()).hexdigest().lower() != expected_sha.lower():
        raise ValueError("SHA-256 check failed.")
    if not destination.is_dir() or not (destination / executable).is_file():
        raise ValueError("Install target doesn't contain the running application.")
    destination = destination.resolve()
    if destination == Path(destination.anchor):
        raise ValueError("Unsafe installation location.")
    if not executable.lower().endswith(".exe"):
        raise ValueError("Invalid executable name.")
    # Wait for main app to exit before replacing Windows-locked binaries.
    if wait_pid > 0 and os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, 0, wait_pid)
        if handle:
            try:
                result = kernel32.WaitForSingleObject(handle, 120_000)
                if result == 0x102:
                    raise TimeoutError("Application did not close.")
            finally:
                kernel32.CloseHandle(handle)
    with tempfile.TemporaryDirectory(prefix="SteamBridgeUpdate-") as tmp:
        stage = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            if len(members) > 10000:
                raise ValueError("Too many files in update.")
            total_size = sum(m.file_size for m in members)
            if total_size > 650 * 1024 * 1024:
                raise ValueError("Uncompressed update too large.")
            for member in members:
                path = Path(member.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise ValueError("Unsafe archive path.")
                if len(path.parts) > 18:
                    raise ValueError("Archive nesting is too deep.")
                # Skip links and other special files.
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Symlink in update.")
                target = stage / path
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        if not (stage / executable).is_file() or not (stage / "SteamBridgeUpdater.exe").is_file():
            raise ValueError("Release archive is missing expected executables.")
        for source in stage.rglob("*"):
            if not source.is_file():
                continue
            target = destination / source.relative_to(stage)
            target.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(20):
                try:
                    os.replace(source, target)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.5)
    subprocess.Popen([str(destination / executable)], cwd=str(destination),
                     close_fds=True, creationflags=0x00000008 if os.name == "nt" else 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--wait-pid", required=True, type=int)
    parser.add_argument("--exe", required=True)
    args = parser.parse_args()
    try:
        install(args.archive, args.sha256, args.install_dir, args.wait_pid, args.exe)
    except Exception as exc:
        # Separate updater has no GUI; log errors to user AppData.
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SteamBridgeWin"
        root.mkdir(parents=True, exist_ok=True)
        (root / "update-error.txt").write_text(str(exc), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()

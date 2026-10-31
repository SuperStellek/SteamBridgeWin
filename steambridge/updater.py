"""GitHub Releases automatic updates for packaged Windows installations.

No token is used; only public stable releases from the fixed project repo.
Downloads are bounded and validated by a SHA-256 asset. Installation is
delegated to a separate process *after the GUI exits*.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

from . import __version__
from .model import Store

REPO = "SuperStellek/SteamBridgeWin"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ARCHIVE_NAME = "SteamBridgeWin-windows-x64.zip"
CHECKSUM_NAME = ARCHIVE_NAME + ".sha256"
MAX_DOWNLOAD = 250 * 1024 * 1024
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def version_tuple(tag: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(tag)
    if match is None:
        raise ValueError("Not a stable semantic version: " + tag)
    return tuple(map(int, match.groups()))


def get_url(url: str, *, limit: int = MAX_DOWNLOAD) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "SteamBridgeWin/" + __version__,
                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=18) as response:
        if not response.geturl().startswith("https://"):
            raise ValueError("Insecure download redirect was refused.")
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > limit:
            raise ValueError("Download exceeds the size limit.")
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Download exceeds the size limit.")
    return payload


def parse_checksum(text: str) -> str:
    for line in text.splitlines():
        match = re.fullmatch(
            r"([a-fA-F0-9]{64})(?:\s+\*?SteamBridgeWin-windows-x64\.zip)?\s*",
            line.strip())
        if match:
            return match.group(1).lower()
    raise ValueError("The release is missing a valid package checksum.")


@dataclass(frozen=True)
class ReleaseUpdate:
    tag: str
    archive_url: str
    checksum_url: str

    @property
    def version(self) -> str:
        return self.tag.removeprefix("v")


class UpdateManager:
    def __init__(self, store: Store):
        self.root = store.root / "updates"
        self.installed_version = __version__

    def check(self) -> ReleaseUpdate | None:
        payload = json.loads(get_url(API, limit=1024 * 1024))
        if payload.get("draft") or payload.get("prerelease"):
            return None
        tag = str(payload["tag_name"])
        if version_tuple(tag) <= version_tuple(self.installed_version):
            return None
        assets = {a["name"]: a for a in payload["assets"] if isinstance(a, dict)}
        if ARCHIVE_NAME not in assets or CHECKSUM_NAME not in assets:
            raise ValueError("Latest release lacks the Windows ZIP and SHA-256 checksum.")
        if assets[ARCHIVE_NAME].get("size", MAX_DOWNLOAD + 1) > MAX_DOWNLOAD:
            raise ValueError("Release package is too large.")
        urls = (assets[ARCHIVE_NAME]["browser_download_url"],
                assets[CHECKSUM_NAME]["browser_download_url"])
        expected_prefix = f"https://github.com/{REPO}/releases/download/"
        if not all(url.startswith(expected_prefix) for url in urls):
            raise ValueError("Unrecognized release download URL.")
        return ReleaseUpdate(tag, *urls)

    def download(self, update: ReleaseUpdate) -> tuple[Path, str]:
        digest = parse_checksum(get_url(update.checksum_url, limit=4096).decode("ascii"))
        package = self.root / update.tag / ARCHIVE_NAME
        package.parent.mkdir(parents=True, exist_ok=True)
        if package.is_file() and self._digest(package) == digest:
            return package, digest
        package.unlink(missing_ok=True)
        # Atomic staging: never use a partial or checksum-mismatched archive.
        temp = package.with_suffix(".partial")
        try:
            content = get_url(update.archive_url)
            if sha256(content).hexdigest() != digest:
                raise ValueError("Downloaded update checksum mismatch; package refused.")
            temp.write_bytes(content)
            temp.replace(package)
        finally:
            temp.unlink(missing_ok=True)
        return package, digest

    @staticmethod
    def _digest(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def can_install() -> bool:
        return bool(getattr(sys, "frozen", False) and os.name == "nt")

    def install_on_exit(self, package: Path, digest: str) -> None:
        """Spawn a copy of updater OUTSIDE of the replaceable install folder."""
        if not self.can_install():
            raise RuntimeError("Auto-install works with the packaged Windows app only.")
        install_dir = Path(sys.executable).resolve().parent
        updater = install_dir / "SteamBridgeUpdater.exe"
        if not updater.is_file():
            raise FileNotFoundError("SteamBridgeUpdater.exe is missing; reinstall the full package.")
        runner = self.root.parent / "updater-runner.exe"
        runner.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(updater, runner)
        # Separate process is not in install_dir; it can replace everything,
        # including SteamBridgeUpdater.exe, after this PID exits.
        subprocess.Popen([
            str(runner), "--archive", str(package.resolve()),
            "--sha256", digest, "--install-dir", str(install_dir),
            "--wait-pid", str(os.getpid()), "--exe", Path(sys.executable).name,
        ], close_fds=True, creationflags=subprocess.DETACHED_PROCESS)

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .environment import get_update_asset_name, get_update_repo


class UpdateError(RuntimeError):
    """Raised when an update cannot be prepared."""


@dataclass(slots=True)
class AvailableUpdate:
    version: str
    notes: str
    asset_url: str
    asset_name: str
    release_name: str


def should_check_for_updates() -> bool:
    return bool(get_update_repo()) and _is_packaged_executable() and sys.platform.startswith("win")


def check_for_update(current_version: str) -> Optional[AvailableUpdate]:
    repo = get_update_repo()
    if not repo or not _is_packaged_executable():
        return None
    try:
        data = _fetch_latest_release(repo)
    except Exception:
        return None

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None
    latest_version = tag.lstrip("v")
    if not _is_remote_newer(latest_version, current_version):
        return None

    asset_name = get_update_asset_name()
    asset_url = _find_asset_url(data, asset_name)
    if not asset_url:
        raise UpdateError(f"Latest release is missing an asset named {asset_name!r}.")
    return AvailableUpdate(
        version=latest_version,
        notes=str(data.get("body") or ""),
        asset_url=asset_url,
        asset_name=asset_name,
        release_name=str(data.get("name") or tag),
    )


ProgressCallback = Callable[[int, int], None]


def prepare_and_schedule_restart(update: AvailableUpdate, progress: Optional[ProgressCallback] = None) -> None:
    executable = _current_executable()
    if executable is None:
        raise UpdateError("Updates are only supported in packaged builds.")
    download_path = _download_asset(update.asset_url, update.asset_name, progress)
    _schedule_replace_and_restart(executable, download_path)


# --------------------------------------------------------------------------- helpers

def _is_packaged_executable() -> bool:
    return bool(getattr(sys, "frozen", False))


def _current_executable() -> Optional[Path]:
    if not _is_packaged_executable():
        return None
    return Path(sys.executable).resolve()


def _fetch_latest_release(repo: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PersonalAssistantUpdater/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload)


def _is_remote_newer(remote: str, current: str) -> bool:
    return _normalize_version(remote) > _normalize_version(current)


def _normalize_version(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    tokens = []
    for chunk in cleaned.replace("-", ".").split("."):
        if not chunk:
            continue
        numeric = "".join(ch for ch in chunk if ch.isdigit())
        if numeric:
            tokens.append(int(numeric))
        else:
            tokens.append(0)
    while tokens and tokens[-1] == 0:
        tokens.pop()
    return tuple(tokens or [0])


def _find_asset_url(release_data: dict, asset_name: str) -> Optional[str]:
    for asset in release_data.get("assets") or []:
        if str(asset.get("name")) == asset_name:
            return str(asset.get("browser_download_url"))
    return None


def _download_asset(url: str, asset_name: str, progress: Optional[ProgressCallback]) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pa-update-"))
    target = tmp_dir / asset_name
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as handle:
            total_header = response.info().get("Content-Length")
            total_size = int(total_header) if total_header and total_header.isdigit() else -1
            downloaded = 0
            if progress:
                progress(downloaded, total_size)
            chunk_size = 1024 * 64
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total_size)
            if progress:
                progress(downloaded, total_size)
    except urllib.error.URLError as exc:
        raise UpdateError(f"Failed to download update: {exc.reason}") from exc
    return target


def _schedule_replace_and_restart(executable: Path, downloaded: Path) -> None:
    quoted_exe = str(executable).replace('"', '""')
    quoted_new = str(downloaded).replace('"', '""')
    script_content = """@echo off
setlocal
set "EXE=%s"
set "NEW=%s"
set "PID=%d"
:wait
tasklist /FI "PID eq %PID%" | find "%PID%" >nul
if %ERRORLEVEL%==0 (
    timeout /T 1 /NOBREAK >nul
    goto wait
)
copy /Y "%NEW%" "%EXE%"
if exist "%NEW%" del /F /Q "%NEW%"
start "" "%EXE%"
exit /B 0
""" % (quoted_exe, quoted_new, os.getpid())
    script_path = Path(tempfile.mkdtemp(prefix="pa-update-script-")) / "apply-update.bat"
    script_path.write_text(script_content, encoding="utf-8")
    try:
        subprocess.Popen(
            ["cmd", "/c", str(script_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS if hasattr(subprocess, "DETACHED_PROCESS") else 0,
        )
    except FileNotFoundError as exc:
        raise UpdateError("cmd.exe is required to apply updates on Windows.") from exc

from __future__ import annotations

import json
from datetime import datetime
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

from .environment import ensure_user_data_dir, get_update_asset_name, get_update_repo


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
    repo = get_update_repo()
    if not repo:
        _python_log("Update check skipped: no repository configured.")
        return False
    if not _is_packaged_executable():
        _python_log("Update check skipped: not a packaged executable.")
        return False
    if not sys.platform.startswith("win"):
        _python_log(f"Update check skipped: unsupported platform {sys.platform}.")
        return False
    return True


def check_for_update(current_version: str) -> Optional[AvailableUpdate]:
    repo = get_update_repo()
    if not repo or not _is_packaged_executable():
        if not repo:
            _python_log("Update check aborted: repository not configured.")
        if not _is_packaged_executable():
            _python_log("Update check aborted: not running packaged executable.")
        return None
    _python_log(f"Checking for updates against {repo} (current version {current_version}).")
    try:
        data = _fetch_latest_release(repo)
    except Exception as exc:  # pragma: no cover - network failure
        _python_log(f"Failed to fetch latest release: {exc}")
        return None

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        _python_log("Latest release did not include a tag name.")
        return None
    latest_version = tag.lstrip("v")
    if not _is_remote_newer(latest_version, current_version):
        _python_log(f"No update available. Remote={latest_version}, current={current_version}.")
        return None

    asset_name = get_update_asset_name()
    asset_url = _find_asset_url(data, asset_name)
    if not asset_url:
        raise UpdateError(f"Latest release is missing an asset named {asset_name!r}.")
    _python_log(f"Update available: version {latest_version}, asset {asset_name}.")
    return AvailableUpdate(
        version=latest_version,
        notes=str(data.get("body") or ""),
        asset_url=asset_url,
        asset_name=asset_name,
        release_name=str(data.get("name") or tag),
    )


ProgressCallback = Callable[[int, int], None]


# --------------------------------------------------------------------------- logging helpers

_LOG_DIR = ensure_user_data_dir() / "Logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_PYTHON_LOG_PATH = _LOG_DIR / f"pa-update-python-{datetime.now():%Y%m%d-%H%M%S-%f}.log"


def _python_log(message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    with _PYTHON_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def prepare_and_schedule_restart(update: AvailableUpdate, progress: Optional[ProgressCallback] = None) -> None:
    executable = _current_executable()
    if executable is None:
        raise UpdateError("Updates are only supported in packaged builds.")
    _python_log(f"Preparing update {update.version}; current executable {executable}.")
    download_path = _download_asset(update.asset_url, update.asset_name, progress)
    _python_log(f"Scheduling replacement using payload {download_path}.")
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
    _python_log(f"Downloading {asset_name} from {url} to {target}.")
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
            _python_log(f"Download complete: {downloaded} bytes.")
    except urllib.error.URLError as exc:
        _python_log(f"Download failed: {exc}")
        raise UpdateError(f"Failed to download update: {exc.reason}") from exc
    return target


def _schedule_replace_and_restart(executable: Path, downloaded: Path) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    primary_log = _LOG_DIR / f"pa-update-ps-{timestamp}.log"
    secondary_log = Path(tempfile.gettempdir()) / f"pa-update-ps-{timestamp}.log"
    try:
        primary_log.parent.mkdir(parents=True, exist_ok=True)
        secondary_log.parent.mkdir(parents=True, exist_ok=True)
        primary_log.touch(exist_ok=True)
        secondary_log.touch(exist_ok=True)
    except OSError as exc:
        _python_log(f"Failed to prime PowerShell log files: {exc}")
    _python_log(
        f"Preparing PowerShell helper; primary log {primary_log}, secondary log {secondary_log}."
    )
    script_lines = [
        "param(",
        "    [Parameter(Mandatory = $true)][string]$TargetPath,",
        "    [Parameter(Mandatory = $true)][string]$SourcePath,",
        "    [Parameter(Mandatory = $true)][int]$ParentPid,",
        "    [Parameter(Mandatory = $true)][string]$PrimaryLogPath,",
        "    [Parameter(Mandatory = $true)][string]$SecondaryLogPath,",
        "    [Parameter(Mandatory = $true)][string]$ScriptPath,",
        "    [string]$ArgumentsJson = '[]'",
        ")",
        "$ErrorActionPreference = 'SilentlyContinue'",
        "$maxRetries = 10",
        "$retryDelayMs = 1500",
        "$logPath = $PrimaryLogPath",
        "$secondaryLogPath = $SecondaryLogPath",
        "$logDir = Split-Path -LiteralPath $logPath -Parent",
        "if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {",
        "    try { New-Item -ItemType Directory -Path $logDir -Force | Out-Null } catch {}",
        "}",
        "$secondaryDir = Split-Path -LiteralPath $secondaryLogPath -Parent",
        "if ($secondaryDir -and -not (Test-Path -LiteralPath $secondaryDir)) {",
        "    try { New-Item -ItemType Directory -Path $secondaryDir -Force | Out-Null } catch {}",
        "}",
        "function Write-Log([string]$Message) {",
        "    $timestamped = \"{0:o} {1}\" -f (Get-Date), $Message",
        "    Write-Host $timestamped",
        "    try {",
        "        Add-Content -LiteralPath $logPath -Value $timestamped -Force",
        "    } catch {",
        "        try { [System.IO.File]::AppendAllText($logPath, $timestamped + [Environment]::NewLine) } catch {}",
        "    }",
        "    if ($secondaryLogPath) {",
        "        try {",
        "            Add-Content -LiteralPath $secondaryLogPath -Value $timestamped -Force",
        "        } catch {",
        "            try { [System.IO.File]::AppendAllText($secondaryLogPath, $timestamped + [Environment]::NewLine) } catch {}",
        "        }",
        "    }",
        "}",
        "Write-Log ('Logging to {0}' -f $logPath)",
        "Write-Log ('Updater started. Target={0} Source={1} ParentPid={2}' -f $TargetPath, $SourcePath, $ParentPid)",
        "Write-Log ('Primary log: {0}' -f $logPath)",
        "Write-Log ('Secondary log: {0}' -f $secondaryLogPath)",
        "try {",
        "    $sourceInfo = Get-Item -LiteralPath $SourcePath -ErrorAction Stop",
        "    Write-Log ('Source size: {0} bytes' -f $sourceInfo.Length)",
        "} catch {",
        "    Write-Log ('Failed to stat source file: {0}' -f $_.Exception.Message)",
        "}",
        "Write-Log 'Waiting for parent process to exit.'",
        "for ($i = 0; $i -lt 120; $i++) {",
        "    if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }",
        "    Start-Sleep -Milliseconds 500",
        "    if ($i -eq 0 -or ($i % 10) -eq 0) { Write-Log ('Still waiting for parent PID {0}.' -f $ParentPid) }",
        "}",
        "Write-Log 'Parent process exited. Proceeding with update.'",
        "if (-not (Test-Path -LiteralPath $SourcePath)) { Write-Log 'Source file missing.'; exit 1 }",
        "$sourceHash = Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256",
        "$backupPath = \"$TargetPath.bak\"",
        "$tempPath = \"$TargetPath.new\"",
        "$argumentList = @()",
        "if ($ArgumentsJson -and $ArgumentsJson.Trim().Length -gt 0) {",
        "    try {",
        "        $parsed = ConvertFrom-Json -InputObject $ArgumentsJson",
        "        if ($parsed -is [System.Collections.IEnumerable]) { $argumentList = @($parsed) }",
        "        Write-Log ('Restored argument list: {0}' -f ($argumentList -join ' '))",
        "    } catch {",
        "        Write-Log ('Failed to parse ArgumentsJson: {0}' -f $_.Exception.Message)",
        "        $argumentList = @()",
        "    }",
        "}",
        "$success = $false",
        "for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {",
        "    Write-Log (\"Attempt {0} beginning.\" -f $attempt)",
        "    $restoreNeeded = $false",
        "    try {",
        "        if (Test-Path -LiteralPath $backupPath) {",
        "            Write-Log 'Removing stale backup before attempting update.'",
        "            try { Remove-Item -LiteralPath $backupPath -Force } catch { Write-Log ('Failed to remove stale backup: {0}' -f $_.Exception.Message) }",
        "        }",
        "        if (Test-Path -LiteralPath $tempPath) {",
        "            try { Remove-Item -LiteralPath $tempPath -Force } catch { Write-Log ('Failed to remove stale temp file: {0}' -f $_.Exception.Message) }",
        "        }",
        "        Copy-Item -LiteralPath $SourcePath -Destination $tempPath -Force -ErrorAction Stop",
        "        Write-Log 'Copied update payload to temporary file.'",
        "        try {",
        "            Unblock-File -LiteralPath $tempPath -ErrorAction Stop",
        "            Write-Log 'Removed Zone.Identifier from temporary executable.'",
        "        } catch {",
        "            Write-Log ('Unblock-File failed: {0}' -f $_.Exception.Message)",
        "        }",
        "        $targetHash = Get-FileHash -LiteralPath $tempPath -Algorithm SHA256",
        "        if ($targetHash.Hash -eq $sourceHash.Hash) {",
        "            if (Test-Path -LiteralPath $TargetPath) {",
        "                try {",
        "                    [System.IO.File]::Replace($tempPath, $TargetPath, $backupPath, $false)",
        "                    Write-Log 'Existing target replaced atomically with File.Replace.'",
        "                } catch {",
        "                    Write-Log ('File.Replace failed ({0}); falling back to Move-Item.' -f $_.Exception.Message)",
        "                    try { Move-Item -LiteralPath $TargetPath -Destination $backupPath -Force -ErrorAction Stop } catch {}",
        "                    Move-Item -LiteralPath $tempPath -Destination $TargetPath -Force -ErrorAction Stop",
        "                }",
        "            } else {",
        "                Move-Item -LiteralPath $tempPath -Destination $TargetPath -Force -ErrorAction Stop",
        "            }",
        "            if (-not (Test-Path -LiteralPath $TargetPath)) {",
        "                throw 'Target executable missing after promotion.'",
        "            }",
        "            Write-Log 'New executable promoted into place.'",
        "            Write-Log 'Hash match. Update successful.'",
        "            $success = $true",
        "            break",
        "        } else {",
        "            Write-Log ('Hash mismatch (target {0} vs source {1}). Retrying.' -f $targetHash.Hash, $sourceHash.Hash)",
        "            $restoreNeeded = $true",
        "        }",
        "    } catch {",
        "        Write-Log ('Error during copy: {0}' -f $_.Exception.Message)",
        "        $restoreNeeded = $true",
        "    }",
        "    if ($restoreNeeded) {",
        "        try {",
        "            if (Test-Path -LiteralPath $backupPath) {",
        "                Move-Item -LiteralPath $backupPath -Destination $TargetPath -Force -ErrorAction Stop",
        "                Write-Log 'Backup restored after failed attempt.'",
        "            }",
        "        } catch {",
        "            Write-Log ('Failed to restore backup: {0}' -f $_.Exception.Message)",
        "        }",
        "    }",
        "    if (-not $success -and (Test-Path -LiteralPath $tempPath)) {",
        "        try { Remove-Item -LiteralPath $tempPath -Force } catch {}",
        "    }",
        "    Start-Sleep -Milliseconds $retryDelayMs",
        "}",
        "if (-not $success) {",
        "    Write-Log 'Failed to copy update after maximum retries.'",
        "    try {",
        "        if (Test-Path -LiteralPath $backupPath) {",
        "            Move-Item -LiteralPath $backupPath -Destination $TargetPath -Force -ErrorAction Stop",
        "            Write-Log 'Backup restored after exhausting retries.'",
        "        }",
        "    } catch {",
        "        Write-Log ('Failed to restore backup after retries: {0}' -f $_.Exception.Message)",
        "    }",
        "    exit 2",
        "}",
        "try { Remove-Item -LiteralPath $SourcePath -Force } catch {}",
        "Write-Log 'Update payload applied. Automatic relaunch skipped by design.'",
        "if ($success -and (Test-Path -LiteralPath $backupPath)) {",
        "    try { Remove-Item -LiteralPath $backupPath -Force } catch {}",
        "}",
        "if (Test-Path -LiteralPath $tempPath) {",
        "    try { Remove-Item -LiteralPath $tempPath -Force } catch {}",
        "}",
        "try { Add-Type -AssemblyName PresentationFramework } catch {}",
        "if ($success) {",
        "    Write-Log 'Update installed successfully. Prompting user to reopen application manually.'",
        "    try {",
        "        [System.Windows.MessageBox]::Show(",
        "            'Update installed successfully. Please reopen Personal Assistant manually to finish applying the update.',",
        "            'Update Complete',",
        "            [System.Windows.MessageBoxButton]::OK,",
        "            [System.Windows.MessageBoxImage]::Information",
        "        ) | Out-Null",
        "    } catch {}",
        "} else {",
        "    Write-Log 'Update encountered an error during installation.'",
        "    try {",
        "        [System.Windows.MessageBox]::Show(",
        "            'The update could not be installed. Please reopen Personal Assistant and try again.',",
        "            'Update Failed',",
        "            [System.Windows.MessageBoxButton]::OK,",
        "            [System.Windows.MessageBoxImage]::Warning",
        "        ) | Out-Null",
        "    } catch {}",
        "}",
        "Start-Sleep -Milliseconds 2000",
        "try { Remove-Item -LiteralPath $ScriptPath -Force } catch {}",
        "Write-Log 'Update script completed.'",
        "Start-Sleep -Milliseconds 1000",
    ]
    script_content = "\r\n".join(script_lines)
    script_path = Path(tempfile.mkdtemp(prefix="pa-update-script-")) / "apply-update.ps1"
    script_path.write_text(script_content, encoding="utf-8")
    _python_log(f"Update script written to {script_path}.")
    powershell = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh.exe")
        or shutil.which("pwsh")
    )
    if not powershell:
        system_root = os.environ.get("SystemRoot")
        if system_root:
            candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            if candidate.exists():
                powershell = str(candidate)
    if not powershell:
        _python_log("Unable to locate PowerShell executable; aborting update.")
        raise UpdateError("PowerShell is required to apply updates on Windows.")
    arguments_json = json.dumps(sys.argv[1:])
    creation_flags = 0
    startupinfo = None
    _python_log(f"Launching update script via {powershell}.")
    try:
        proc = subprocess.Popen(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-TargetPath",
                str(executable),
                "-SourcePath",
                str(downloaded),
                "-ParentPid",
                str(os.getpid()),
                "-PrimaryLogPath",
                str(primary_log),
                "-SecondaryLogPath",
                str(secondary_log),
                "-ScriptPath",
                str(script_path),
                "-ArgumentsJson",
                arguments_json,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            startupinfo=startupinfo,
        )
        _python_log(f"Update script launched (PID {proc.pid}).")
    except FileNotFoundError as exc:
        _python_log(f'Failed to spawn update script: {exc}')
        raise UpdateError("PowerShell is required to apply updates on Windows.") from exc

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
    script_lines = [
        "param(",
        "    [Parameter(Mandatory = $true)][string]$TargetPath,",
        "    [Parameter(Mandatory = $true)][string]$SourcePath,",
        "    [Parameter(Mandatory = $true)][int]$ParentPid,",
        "    [Parameter(Mandatory = $true)][string]$ScriptPath,",
        "    [string]$ArgumentsJson = '[]'",
        ")",
        "$ErrorActionPreference = 'SilentlyContinue'",
        "$maxRetries = 10",
        "$retryDelayMs = 1500",
        "$logPath = Join-Path ([System.IO.Path]::GetTempPath()) 'pa-update.log'",
        "function Write-Log([string]$Message) {",
        "    Add-Content -LiteralPath $logPath -Value (\"{0:o} {1}\" -f (Get-Date), $Message)",
        "}",
        "Write-Log ('Updater started. Target={0} Source={1} ParentPid={2}' -f $TargetPath, $SourcePath, $ParentPid)",
        "for ($i = 0; $i -lt 120; $i++) {",
        "    if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }",
        "    Start-Sleep -Milliseconds 500",
        "}",
        "Write-Log 'Parent process exited. Proceeding with update.'",
        "if (-not (Test-Path -LiteralPath $SourcePath)) { Write-Log 'Source file missing.'; exit 1 }",
        "$sourceHash = Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256",
        "$backupPath = \"$TargetPath.bak\"",
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
        "        if (Test-Path -LiteralPath $TargetPath) {",
        "            try { Remove-Item -LiteralPath $backupPath -Force } catch {}",
        "            Move-Item -LiteralPath $TargetPath -Destination $backupPath -Force",
        "            Write-Log 'Existing target renamed to .bak.'",
        "        }",
        "        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Force",
        "        $targetHash = Get-FileHash -LiteralPath $TargetPath -Algorithm SHA256",
        "        if ($targetHash.Hash -eq $sourceHash.Hash) {",
        "            Write-Log 'Hash match. Update successful.'",
        "            try { Remove-Item -LiteralPath $backupPath -Force } catch {}",
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
        "                Move-Item -LiteralPath $backupPath -Destination $TargetPath -Force",
        "                Write-Log 'Backup restored after failed attempt.'",
        "            }",
        "        } catch {",
        "            Write-Log ('Failed to restore backup: {0}' -f $_.Exception.Message)",
        "        }",
        "    }",
        "    Start-Sleep -Milliseconds $retryDelayMs",
        "}",
        "if (-not $success) {",
        "    Write-Log 'Failed to copy update after maximum retries.'",
        "    try {",
        "        if (Test-Path -LiteralPath $backupPath) {",
        "            Move-Item -LiteralPath $backupPath -Destination $TargetPath -Force",
        "            Write-Log 'Backup restored after exhausting retries.'",
        "        }",
        "    } catch {",
        "        Write-Log ('Failed to restore backup after retries: {0}' -f $_.Exception.Message)",
        "    }",
        "    exit 2",
        "}",
        "try { Remove-Item -LiteralPath $SourcePath -Force } catch {}",
        "Write-Log 'Launching updated executable.'",
        "Start-Sleep -Milliseconds 2000",
        "$workingDirectory = Split-Path -LiteralPath $TargetPath",
        "if (-not $workingDirectory) { $workingDirectory = [System.IO.Path]::GetDirectoryName($TargetPath) }",
        "try {",
        "    if ($argumentList.Count -gt 0) {",
        "        $proc = Start-Process -FilePath $TargetPath -ArgumentList $argumentList -WorkingDirectory $workingDirectory -WindowStyle Normal -PassThru -ErrorAction Stop",
        "    } else {",
        "        $proc = Start-Process -FilePath $TargetPath -WorkingDirectory $workingDirectory -WindowStyle Normal -PassThru -ErrorAction Stop",
        "    }",
        "    if ($null -ne $proc) {",
        "        Write-Log ('Launched updated executable (PID {0}).' -f $proc.Id)",
        "        try { $proc.Dispose() } catch {}",
        "    } else {",
        "        Write-Log 'Launched updated executable.'",
        "    }",
        "} catch {",
        "    Write-Log ('Failed to launch updated executable: {0}' -f $_.Exception.Message)",
        "}",
        "Start-Sleep -Milliseconds 2000",
        "try { Remove-Item -LiteralPath $ScriptPath -Force } catch {}",
        "Write-Log 'Update script completed.'",
    ]
    script_content = "\r\n".join(script_lines)
    script_path = Path(tempfile.mkdtemp(prefix="pa-update-script-")) / "apply-update.ps1"
    script_path.write_text(script_content, encoding="utf-8")
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
        raise UpdateError("PowerShell is required to apply updates on Windows.")
    arguments_json = json.dumps(sys.argv[1:])
    creation_flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creation_flags |= subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    startupinfo = None
    if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        if hasattr(subprocess, "STARTF_USESHOWWINDOW"):
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
        if hasattr(subprocess, "SW_HIDE"):
            startupinfo.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    try:
        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script_path),
                "-TargetPath",
                str(executable),
                "-SourcePath",
                str(downloaded),
                "-ParentPid",
                str(os.getpid()),
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
    except FileNotFoundError as exc:
        raise UpdateError("PowerShell is required to apply updates on Windows.") from exc

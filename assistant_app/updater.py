#!/usr/bin/env python3
"""
Robust Windows updater & relauncher (full rewrite)
--------------------------------------------------
Purpose:
  - Reliably replace an installed EXE (PyInstaller or any EXE) with an updated payload.
  - Then relaunch the updated app with a CLEAN environment to avoid the "cannot find python DLL" error.
  - Provide logs, retries, and optional backup/rollback.

How it works:
  - Your app should spawn this updater (preferably from a temporary location) and then EXIT.
  - The updater writes and executes a PowerShell script that:
      * Waits for the target EXE to be unlocked
      * Backs it up (optional)
      * Replaces it atomically
      * Waits a short settle delay
      * Launches the updated EXE using .NET ProcessStartInfo with a minimal environment
      * Retries a few times if the first launch fails
  - The PowerShell script writes detailed logs.

Example (from your app, before exiting):
  updater.exe --target "C:\Program Files\MyApp\MyApp.exe" --payload "C:\Path\To\downloaded\MyApp.new" --args "--minimized" --backup --appdata-name "MyApp"

Notes:
  - This script is Windows-only. It requires PowerShell (present by default on supported Windows versions).
  - If you need hash verification, pass --sha256 "<hex>" and the updater will validate the payload before replacing.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ---------------------------
# Helpers
# ---------------------------

def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def default_log_dir(app_name: str) -> Path:
    # Use %APPDATA%\<AppName>\Logs by default
    appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    log_dir = Path(appdata) / app_name / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

def write_ps_script(*, ps_path: Path, target: Path, payload: Path, working_dir: Path,
                    log_file: Path, backup: bool, launch_args: str, settle_seconds: int):
    """Writes the robust PowerShell script that does the file swap and clean-env relaunch."""
    ps = f"""Param(
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [Parameter(Mandatory=$true)][string]$PayloadPath,
    [Parameter(Mandatory=$true)][string]$WorkingDirectory,
    [Parameter(Mandatory=$true)][string]$LogFile,
    [Parameter(Mandatory=$false)][switch]$DoBackup,
    [Parameter(Mandatory=$false)][string]$LaunchArgs = "",
    [Parameter(Mandatory=$false)][int]$SettleSeconds = {settle_seconds}
)

$ErrorActionPreference = 'Stop'

function Write-Log($msg) {{
    $timestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    $line = "[$timestamp] $msg"
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}}

function Wait-For-UnlockedFile($path, $timeoutSec) {{
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {{
        try {{
            $fs = [System.IO.File]::Open($path, 'Open', 'ReadWrite', 'None')
            $fs.Close()
            return $true
        }} catch {{
            if ($sw.Elapsed.TotalSeconds -ge $timeoutSec) {{ return $false }}
            Start-Sleep -Milliseconds 200
        }}
    }}
}}

function Safe-Copy-Replace($src, $dst, $doBackup) {{
    # Ensure dest directory exists
    $dstDir = Split-Path -LiteralPath $dst -Parent
    if (!(Test-Path -LiteralPath $dstDir)) {{
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }}

    $tmpDst = Join-Path -Path $dstDir -ChildPath ("." + [IO.Path]::GetFileName($dst) + ".tmp_" + [Guid]::NewGuid().ToString("N"))

    Write-Log "Copying payload to temporary file: $tmpDst"
    Copy-Item -LiteralPath $src -Destination $tmpDst -Force

    if ($doBackup -and (Test-Path -LiteralPath $dst)) {{
        $bak = $dst + ".bak"
        try {{
            Write-Log "Creating backup: $bak"
            Copy-Item -LiteralPath $dst -Destination $bak -Force
        }} catch {{
            Write-Log "WARNING: Backup failed: $($_.Exception.Message)"
        }}
    }}

    Write-Log "Replacing: $dst"
    Move-Item -LiteralPath $tmpDst -Destination $dst -Force
}}

function Launch-With-CleanEnv($exePath, $args, $workDir) {{
    # Build a minimal, stable environment
    $cleanEnv = @{{}}
    $cleanEnv["SystemRoot"] = $env:SystemRoot
    $cleanEnv["WINDIR"]     = $env:WINDIR
    $cleanEnv["COMSPEC"]    = $env:Comspec
    $cleanEnv["TEMP"]       = $env:TEMP
    $cleanEnv["TMP"]        = $env:TMP
    $cleanEnv["PYI_SAFE_MODE"] = "1"
    # Ensure app dir first in PATH, then core system dirs
    $appDir = Split-Path -LiteralPath $exePath -Parent
    $cleanEnv["Path"] = "$appDir;$([Environment]::GetFolderPath('Windows'))\System32;$([Environment]::GetFolderPath('Windows'))"

    # Use .NET ProcessStartInfo for deterministic env
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exePath
    $psi.WorkingDirectory = $workDir
    $psi.UseShellExecute = $false  # required to set Environment
    if ($args -ne $null -and $args.Trim() -ne "") {{ $psi.Arguments = $args }}
    foreach ($k in $cleanEnv.Keys) {{ $psi.Environment[$k] = $cleanEnv[$k] }}

    $proc = [System.Diagnostics.Process]::Start($psi)
    if ($null -eq $proc) {{ throw "Failed to start process via ProcessStartInfo." }}
    return $proc.Id
}}

try {{
    Write-Log "Updater started."
    Write-Log "TargetPath: $TargetPath"
    Write-Log "PayloadPath: $PayloadPath"
    Write-Log "WorkingDirectory: $WorkingDirectory"
    Write-Log "Backup: $($DoBackup.IsPresent)"
    Write-Log "SettleSeconds: $SettleSeconds"
    if ($LaunchArgs) {{ Write-Log "LaunchArgs: $LaunchArgs" }}

    if (!(Test-Path -LiteralPath $PayloadPath)) {{ throw "Payload not found: $PayloadPath" }}

    # Wait for target to unlock (in case parent hasn't exited yet)
    if (Test-Path -LiteralPath $TargetPath) {{
        if (-not (Wait-For-UnlockedFile -path $TargetPath -timeoutSec 30)) {{
            Write-Log "Target still locked after 30s; proceeding anyway."
        }}
    }}

    Safe-Copy-Replace -src $PayloadPath -dst $TargetPath -doBackup:$DoBackup.IsPresent

    # Small settle delay to dodge AV/SmartScreen timing
    if ($SettleSeconds -gt 0) {{
        Write-Log "Sleeping $SettleSeconds second(s) before relaunch."
        Start-Sleep -Seconds $SettleSeconds
    }}

    # Advisory check: DLL presence next to EXE (useful for PyInstaller)
    try {{
        $appDir = Split-Path -LiteralPath $TargetPath -Parent
        $pyDlls = Get-ChildItem -LiteralPath $appDir -Filter 'python*.dll' -ErrorAction SilentlyContinue
        if ($pyDlls) {{ Write-Log ("Found python DLL(s): " + ($pyDlls | ForEach-Object {{ $_.Name }}) -join ', ') }}
        else {{ Write-Log "NOTE: No local python*.dll found; may be embedded or not required." }}
    }} catch {{ Write-Log "DLL check failed: $($_.Exception.Message)" }}

    $maxAttempts = 3
    $delays = @(0, 2, 5) # extra waits between attempts
    for ($i = 0; $i -lt $maxAttempts; $i++) {{
        if ($delays[$i] -gt 0) {{
            Write-Log ("Extra delay before attempt {0}: {1}s" -f ($i+1), $delays[$i])
            Start-Sleep -Seconds $delays[$i]
        }}
        try {{
            Write-Log ("Launching updated app (attempt {0})..." -f ($i+1))
            $pid = Launch-With-CleanEnv -exePath $TargetPath -args $LaunchArgs -workDir $WorkingDirectory
            Write-Log ("Launched. PID={0}" -f $pid)
            exit 0
        }} catch {{
            Write-Log ("Launch attempt {0} failed: {1}" -f ($i+1), $_.Exception.Message)
        }}
    }}

    throw "Failed to launch the updated app after $maxAttempts attempts."
}} catch {{
    Write-Log ("FATAL: {0}" -f $_.Exception.Message)
    exit 1
}}
"""
    ps_path.write_text(ps, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="Robust Windows updater & relauncher (full rewrite)")
    parser.add_argument("--target", required=True, help="Path to the installed target executable to replace (e.g., C:\\Program Files\\MyApp\\MyApp.exe)")
    parser.add_argument("--payload", required=True, help="Path to the downloaded new executable to install (temporary file)")
    parser.add_argument("--args", default="", help="Arguments to pass when launching the updated app (single string)")
    parser.add_argument("--backup", action="store_true", help="Keep a .bak of the previous executable")
    parser.add_argument("--sha256", default=None, help="If provided, verify payload SHA-256 before replacing")
    parser.add_argument("--settle-seconds", type=int, default=3, help="Seconds to sleep after replace before launching")
    parser.add_argument("--appdata-name", default=None, help="Name for %APPDATA%\\<name>\\Logs; default = stem of target exe")
    parser.add_argument("--log-dir", default=None, help="Override log directory path (if not using appdata-name)")

    args = parser.parse_args()

    target = Path(args.target).resolve()
    payload = Path(args.payload).resolve()
    if not payload.exists():
        print(f"[Updater] Payload does not exist: {payload}", file=sys.stderr)
        sys.exit(2)

    app_name = args.appdata_name or target.stem
    if args.log_dir:
        log_dir = Path(args.log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
    else:
        log_dir = default_log_dir(app_name)

    log_file = log_dir / f"updater-ps-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    # Optional SHA-256 verify
    if args.sha256:
        actual = compute_sha256(payload)
        if actual.lower() != args.sha256.lower():
            print(f"[Updater] SHA-256 mismatch. Expected {args.sha256}, got {actual}", file=sys.stderr)
            sys.exit(3)

    # Prepare PowerShell script
    tmp_dir = Path(tempfile.mkdtemp(prefix="app_update_"))
    ps_path = tmp_dir / "do_update.ps1"
    working_dir = target.parent

    write_ps_script(
        ps_path=ps_path,
        target=target,
        payload=payload,
        working_dir=working_dir,
        log_file=log_file,
        backup=args.backup,
        launch_args=args.args,
        settle_seconds=args.settle_seconds,
    )

    # Build PowerShell command
    ps_cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(ps_path),
        "-TargetPath", str(target),
        "-PayloadPath", str(payload),
        "-WorkingDirectory", str(working_dir),
        "-LogFile", str(log_file),
    ]
    if args.backup:
        ps_cmd.append("-DoBackup")
    if args.args:
        ps_cmd.extend(["-LaunchArgs", args.args])
    if args.settle_seconds is not None:
        ps_cmd.extend(["-SettleSeconds", str(args.settle_seconds)])

    # Run PowerShell helper
    try:
        completed = subprocess.run(ps_cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("[Updater] PowerShell not found. This updater requires PowerShell on Windows.", file=sys.stderr)
        sys.exit(4)

    # Bubble up useful stdout/stderr for diagnostics
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)

    # Exit code 0 => success (launched), 1 => fatal error in PS
    if completed.returncode == 0:
        sys.exit(0)
    else:
        print(f"[Updater] PowerShell updater failed with exit code {completed.returncode}. See log: {log_file}", file=sys.stderr)
        sys.exit(5)

if __name__ == "__main__":
    if os.name != "nt":
        print("This updater is intended for Windows (nt) only.", file=sys.stderr)
        sys.exit(9)
    main()

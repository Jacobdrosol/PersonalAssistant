Param(
    [string]$Python = "python",
    [string]$Version = $null
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-VersionFromModule {
    param([string]$Python)
    $cmd = @(
        $Python,
        "-c",
        "from assistant_app.version import __version__; print(__version__)",
        ""
    )
    $output = & $cmd[0] $cmd[1] $cmd[2]
    return $output.Trim()
}

if (-not $Version) {
    $Version = Get-VersionFromModule -Python $Python
}

Write-Host "Building Personal Assistant version $Version" -ForegroundColor Cyan

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt pyinstaller

$distDir = Join-Path -Path (Get-Location) -ChildPath "dist"
if (Test-Path $distDir) {
    Remove-Item $distDir -Recurse -Force
}

$pyInstallerArgs = @(
    "assistant_app/__main__.py",
    "--name", "PersonalAssistant",
    "--noconsole",
    "--onefile",
    "--clean"
)

& $Python -m PyInstaller @pyInstallerArgs

$exePath = Join-Path $distDir "PersonalAssistant.exe"
if (-not (Test-Path $exePath)) {
    throw "PyInstaller did not produce dist\PersonalAssistant.exe"
}

$packageDir = Join-Path $distDir "package"
if (Test-Path $packageDir) {
    Remove-Item $packageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $packageDir | Out-Null

Copy-Item $exePath -Destination (Join-Path $packageDir "PersonalAssistant.exe") -Force
Copy-Item "packaging/Install-PersonalAssistant.ps1" -Destination (Join-Path $packageDir "Install-PersonalAssistant.ps1") -Force
Copy-Item "packaging/Install-PersonalAssistant.bat" -Destination (Join-Path $packageDir "Install-PersonalAssistant.bat") -Force
Copy-Item "packaging/README.txt" -Destination (Join-Path $packageDir "README.txt") -Force

$zipPath = Join-Path $distDir "PersonalAssistant-package.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $packageDir '*') -DestinationPath $zipPath -Force

Write-Host "Build complete. Outputs:" -ForegroundColor Green
Write-Host " - $exePath" -ForegroundColor Green
Write-Host " - $zipPath" -ForegroundColor Green

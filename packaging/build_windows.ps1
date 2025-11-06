Param(
    [string]$Python = "python",
    [string]$Version = $null
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;

public static class PAIconUtilities
{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool DestroyIcon(System.IntPtr hIcon);
}
"@

function New-PersonalAssistantIcon {
    param([string]$Path)

    $bitmap = New-Object System.Drawing.Bitmap 128, 128
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $background = [System.Drawing.Color]::FromArgb(0x20, 0x28, 0x3A)
    $graphics.Clear($background)

    $silhouetteColor = [System.Drawing.Color]::FromArgb(0xFF, 0xF4, 0xF6, 0xFA)
    $brush = New-Object System.Drawing.SolidBrush($silhouetteColor)

    # Head
    $graphics.FillEllipse($brush, 44, 18, 40, 40)
    # Shoulders / torso
    $graphics.FillEllipse($brush, 24, 52, 80, 60)
    $graphics.FillRectangle($brush, 34, 70, 60, 46)

    $brush.Dispose()
    $graphics.Dispose()

    $iconHandle = $bitmap.GetHicon()
    $icon = [System.Drawing.Icon]::FromHandle($iconHandle)
    $stream = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Create)
    $icon.Save($stream)
    $stream.Close()
    $icon.Dispose()
    $bitmap.Dispose()
    [PAIconUtilities]::DestroyIcon($iconHandle) | Out-Null
}

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

$assetsDir = Join-Path (Get-Location) "assets"
if (-not (Test-Path $assetsDir)) {
    New-Item -ItemType Directory -Path $assetsDir | Out-Null
}
$iconPath = Join-Path $assetsDir "personal_assistant.ico"
if (-not (Test-Path $iconPath)) {
    New-PersonalAssistantIcon -Path $iconPath
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt pyinstaller
# Optional-heavy dependencies required for the bundled summarizer stack
$packagingExtras = @('transformers', 'torch', 'sentencepiece', 'safetensors')
& $Python -m pip install @packagingExtras

$distDir = Join-Path -Path (Get-Location) -ChildPath "dist"
if (Test-Path $distDir) {
    Remove-Item $distDir -Recurse -Force
}

$pyInstallerArgs = @(
    "assistant_app/__main__.py",
    "--name", "PersonalAssistant",
    "--noconsole",
    "--onefile",
    "--clean",
    "--noconfirm",
    "--icon", $iconPath,
    "--add-data", "$iconPath;."
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
Copy-Item $iconPath -Destination (Join-Path $packageDir "personal_assistant.ico") -Force
Copy-Item "packaging/Install-PersonalAssistant.ps1" -Destination (Join-Path $packageDir "Install-PersonalAssistant.ps1") -Force
Copy-Item "packaging/Install-PersonalAssistant.bat" -Destination (Join-Path $packageDir "Install-PersonalAssistant.bat") -Force
Copy-Item "packaging/README.txt" -Destination (Join-Path $packageDir "README.txt") -Force
Copy-Item $iconPath -Destination (Join-Path $distDir "personal_assistant.ico") -Force

$zipPath = Join-Path $distDir "PersonalAssistant-package.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $packageDir '*') -DestinationPath $zipPath -Force

Write-Host "Build complete. Outputs:" -ForegroundColor Green
Write-Host " - $exePath" -ForegroundColor Green
Write-Host " - $zipPath" -ForegroundColor Green



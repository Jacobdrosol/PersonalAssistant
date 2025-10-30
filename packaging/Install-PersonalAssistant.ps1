Param(
    [string]$Repo = "Jacobdrosol/PersonalAssistant",
    [string]$AssetName = "PersonalAssistant.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

$installRoot = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'PersonalAssistant'
if (-not (Test-Path $installRoot)) {
    New-Item -ItemType Directory -Path $installRoot | Out-Null
}

$packageDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$localExe = Join-Path $packageDir $AssetName
$targetExe = Join-Path $installRoot $AssetName
$iconSource = Join-Path $packageDir 'personal_assistant.ico'
$iconTarget = Join-Path $installRoot 'personal_assistant.ico'
$downloadRequired = -not (Test-Path $localExe)

if (-not $downloadRequired) {
    Write-Info "Copying bundled executable to $targetExe"
    Copy-Item $localExe $targetExe -Force
} else {
    Write-Info "Downloading latest release from GitHub..."
    $headers = @{ 'User-Agent' = 'PersonalAssistantBootstrap/1.0'; 'Accept' = 'application/vnd.github+json' }
    $releaseUrl = "https://api.github.com/repos/$Repo/releases/latest"
    $response = Invoke-WebRequest -Uri $releaseUrl -Headers $headers -UseBasicParsing
    $data = $response.Content | ConvertFrom-Json
    $asset = $data.assets | Where-Object { $_.name -eq $AssetName }
    if (-not $asset) {
        throw "Release is missing asset named $AssetName"
    }
    $downloadUrl = $asset.browser_download_url
    Write-Info "Downloading $AssetName..."
    Invoke-WebRequest -Uri $downloadUrl -Headers @{ 'User-Agent' = 'PersonalAssistantBootstrap/1.0' } -OutFile $targetExe -UseBasicParsing
}

if (Test-Path $iconSource) {
    Copy-Item $iconSource $iconTarget -Force
}

Write-Info "Creating desktop shortcut..."
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Personal Assistant.lnk'
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $targetExe
$shortcut.WorkingDirectory = $installRoot
$shortcut.WindowStyle = 1
if (Test-Path $iconTarget) {
    $shortcut.IconLocation = "$iconTarget,0"
} else {
    $shortcut.IconLocation = "$targetExe,0"
}
$shortcut.Save()

Write-Info "Launching Personal Assistant"
Start-Process $targetExe

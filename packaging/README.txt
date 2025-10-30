Personal Assistant Portable Package
===================================

Files:
  - PersonalAssistant.exe
  - personal_assistant.ico
  - Install-PersonalAssistant.ps1
  - Install-PersonalAssistant.bat
  - README.txt

Usage:
 1. Extract the zip into any folder.
 2. Run `Install-PersonalAssistant.bat`. This copies the exe and icon to %APPDATA%\PersonalAssistant,
    creates a desktop shortcut, and starts the app. The PowerShell script will download a fresh exe
    from GitHub if one is not included.
 3. Future launches should use the desktop shortcut. The packaged app checks GitHub for
    updates on startup and applies them automatically (keeping your data in %APPDATA%).

The ps1 script can be run directly if your execution policy permits; otherwise use the batch file
which runs it with `-ExecutionPolicy Bypass`.

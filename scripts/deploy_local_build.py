from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil

from assistant_app.shortcuts import create_startup_shortcut, startup_shortcut_path
from assistant_app.version import __version__


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path.home() / "AppData/Roaming/PersonalAssistant"


def deploy() -> None:
    source_executable = ROOT / "dist/PersonalAssistant.exe"
    target_executable = DATA_ROOT / "PersonalAssistant.exe"
    icon = DATA_ROOT / "personal_assistant.ico"
    settings_path = DATA_ROOT / "settings.json"
    if not source_executable.exists():
        raise SystemExit(f"Build output not found: {source_executable}")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if target_executable.exists():
        shutil.copy2(target_executable, DATA_ROOT / f"PersonalAssistant.pre_ru_{stamp}.exe")
    if settings_path.exists():
        shutil.copy2(settings_path, DATA_ROOT / f"settings.pre_ru_{stamp}.json")
    shutil.copy2(source_executable, target_executable)

    settings: dict[str, object] = {}
    if settings_path.exists():
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings = loaded
    settings["launch_at_startup"] = True
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    (DATA_ROOT / "app_version.txt").write_text(__version__, encoding="utf-8")
    if not create_startup_shortcut(target_executable, icon if icon.exists() else None):
        raise SystemExit("The executable was deployed, but the Windows Startup shortcut could not be created.")
    print(f"Deployed Personal Assistant {__version__} to {target_executable}")
    print(f"Startup shortcut: {startup_shortcut_path()}")


if __name__ == "__main__":
    deploy()

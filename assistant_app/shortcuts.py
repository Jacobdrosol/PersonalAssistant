from __future__ import annotations

from pathlib import Path
from typing import Optional

from .environment import get_desktop_path, get_start_menu_programs_path

_SHORTCUT_NAME = "Personal Assistant.lnk"


def desktop_shortcut_path() -> Path:
    return get_desktop_path() / _SHORTCUT_NAME


def start_menu_shortcut_path() -> Path:
    return get_start_menu_programs_path() / _SHORTCUT_NAME


def shortcut_exists(path: Path) -> bool:
    return path.exists()


def create_shortcut(path: Path, target: Path, icon: Optional[Path]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32com.client  # type: ignore
    except Exception:
        return False
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(path))
        shortcut.TargetPath = str(target)
        shortcut.WorkingDirectory = str(target.parent)
        shortcut.WindowStyle = 1
        if icon is not None and icon.exists():
            shortcut.IconLocation = f"{icon},0"
        else:
            shortcut.IconLocation = f"{target},0"
        shortcut.Save()
        return True
    except Exception:
        return False


def remove_shortcut(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        path.unlink()
        return True
    except Exception:
        return False


def create_desktop_shortcut(target: Path, icon: Optional[Path]) -> bool:
    return create_shortcut(desktop_shortcut_path(), target, icon)


def remove_desktop_shortcut() -> bool:
    return remove_shortcut(desktop_shortcut_path())


def desktop_shortcut_exists() -> bool:
    return shortcut_exists(desktop_shortcut_path())


def create_start_menu_shortcut(target: Path, icon: Optional[Path]) -> bool:
    return create_shortcut(start_menu_shortcut_path(), target, icon)


def remove_start_menu_shortcut() -> bool:
    return remove_shortcut(start_menu_shortcut_path())


def start_menu_shortcut_exists() -> bool:
    return shortcut_exists(start_menu_shortcut_path())

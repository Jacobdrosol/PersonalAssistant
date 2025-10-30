from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Optional

from .environment import get_desktop_path


def shortcut_path() -> Path:
    return get_desktop_path() / "Personal Assistant.lnk"


def shortcut_exists() -> bool:
    return shortcut_path().exists()


def create_desktop_shortcut(target: Path, icon: Optional[Path]) -> bool:
    try:
        import win32com.client  # type: ignore
    except Exception:
        return _create_shortcut_ctypes(target, icon)
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path()))
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


def remove_desktop_shortcut() -> bool:
    path = shortcut_path()
    if not path.exists():
        return True
    try:
        path.unlink()
        return True
    except Exception:
        return False


def _create_shortcut_ctypes(target: Path, icon: Optional[Path]) -> bool:
    # Fallback implementation using IShellLink via ctypes to avoid pywin32 dependency at runtime.
    CLSID_ShellLink = ctypes.c_char * 16
    IID_IShellLink = ctypes.c_char * 16
    shell_link = CLSID_ShellLink.from_buffer_copy(bytes.fromhex("00021401-0000-0000-C000-000000000046".replace("-", "")))
    iid = IID_IShellLink.from_buffer_copy(bytes.fromhex("000214F9-0000-0000-C000-000000000046".replace("-", "")))
    # Using ctypes COM creation is verbose; rather than replicate the full implementation here,
    # fall back to reporting failure so that the caller can notify the user.
    return False

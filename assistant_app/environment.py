from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Basic metadata used across the application and tooling.
APP_NAME = "Personal Assistant"
APP_ID = "PersonalAssistant"
# Enable auto-update against your public GitHub repo.
DEFAULT_UPDATE_REPO: Optional[str] = "Jacobdrosol/PersonalAssistant"


def _default_base_dir() -> Path:
    """
    Resolve the base directory where user-specific data should be stored.
    Follows platform conventions unless PA_USER_DATA_DIR is set.
    """
    forced = os.getenv("PA_USER_DATA_DIR")
    if forced:
        return Path(forced).expanduser()

    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA") or str(Path.home())
        return Path(base) / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def ensure_user_data_dir() -> Path:
    """
    Return the user data directory, creating it when necessary.
    """
    path = _default_base_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_project_root() -> Path:
    """
    Legacy location that stored bundled data beside the source tree.
    Used for one-time migrations to the user data directory.
    """
    return Path(__file__).resolve().parent.parent


def get_update_repo() -> Optional[str]:
    """
    Return the GitHub repository (owner/name) used for release checks.
    Set via PA_UPDATE_REPO environment variable to avoid hard-coding.
    """
    repo = os.getenv("PA_UPDATE_REPO")
    if repo:
        return repo.strip()
    return DEFAULT_UPDATE_REPO


def get_update_asset_name() -> str:
    """
    Name of the packaged executable asset that should be downloaded on update.
    Can be overridden with PA_UPDATE_ASSET_NAME.
    """
    return os.getenv("PA_UPDATE_ASSET_NAME", f"{APP_ID}.exe")


def get_desktop_path() -> Path:
    if sys.platform.startswith("win"):
        desktop = os.getenv("USERPROFILE")
        if desktop:
            return Path(desktop) / "Desktop"
        return Path.home() / "Desktop"
    return Path.home() / "Desktop"

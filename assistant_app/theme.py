from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    name: str
    window_bg: str
    surface_bg: str
    surface_alt_bg: str
    card_bg: str
    card_alt_bg: str
    border: str
    accent: str
    accent_hover: str
    text_primary: str
    text_secondary: str
    text_muted: str
    entry_bg: str
    entry_fg: str
    entry_border: str
    list_bg: str
    list_alt_bg: str
    list_selected_bg: str
    list_selected_fg: str
    calendar_cell_bg: str
    calendar_cell_selected_bg: str
    calendar_outside_text: str
    notification_bg: str
    notification_body: str
    danger_bg: str
    danger_fg: str


DARK_THEME = ThemePalette(
    name="dark",
    window_bg="#111219",
    surface_bg="#171821",
    surface_alt_bg="#1d1f2f",
    card_bg="#1d1e2c",
    card_alt_bg="#1c1d2b",
    border="#2c2f45",
    accent="#4F75FF",
    accent_hover="#6c8cff",
    text_primary="#E8EAF6",
    text_secondary="#9FA8DA",
    text_muted="#B0B4C7",
    entry_bg="#1c1d2b",
    entry_fg="#f5f5ff",
    entry_border="#2c2f45",
    list_bg="#1c1d2b",
    list_alt_bg="#222338",
    list_selected_bg="#3a3d55",
    list_selected_fg="#ffffff",
    calendar_cell_bg="#232337",
    calendar_cell_selected_bg="#31314a",
    calendar_outside_text="#61647a",
    notification_bg="#1f2030",
    notification_body="#B0B4C7",
    danger_bg="#ba1a1a",
    danger_fg="#ffffff",
)

LIGHT_THEME = ThemePalette(
    name="light",
    window_bg="#f6f7fb",
    surface_bg="#ffffff",
    surface_alt_bg="#f0f3ff",
    card_bg="#ffffff",
    card_alt_bg="#f6f8ff",
    border="#d1d6e6",
    accent="#4F75FF",
    accent_hover="#345de0",
    text_primary="#161829",
    text_secondary="#4b4f62",
    text_muted="#6e7285",
    entry_bg="#ffffff",
    entry_fg="#161829",
    entry_border="#c6cbde",
    list_bg="#ffffff",
    list_alt_bg="#f1f4ff",
    list_selected_bg="#d7e2ff",
    list_selected_fg="#161829",
    calendar_cell_bg="#edf1ff",
    calendar_cell_selected_bg="#d3ddff",
    calendar_outside_text="#8a90a8",
    notification_bg="#ffffff",
    notification_body="#4b4f62",
    danger_bg="#c62828",
    danger_fg="#ffffff",
)


THEMES = {
    "dark": DARK_THEME,
    "light": LIGHT_THEME,
}


def get_theme(name: str) -> ThemePalette:
    return THEMES.get(name.lower(), DARK_THEME)

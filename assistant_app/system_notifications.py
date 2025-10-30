from __future__ import annotations

import platform
import subprocess


class SystemNotifier:
    def __init__(self) -> None:
        self._is_windows = platform.system().lower().startswith("win")

    def notify(self, title: str, message: str) -> None:
        if not self._is_windows:
            return
        self._show_windows_toast(title or "Notification", message or "")

    def _show_windows_toast(self, title: str, message: str) -> None:
        script = self._build_powershell_script(title, message)
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception:
            pass

    def _build_powershell_script(self, title: str, message: str) -> str:
        clean_title = self._ps_quote(title.strip())
        normalized_message = message.replace("\r", " ").replace("\n", " ").strip()
        if not normalized_message:
            normalized_message = "Time for a quick check-in."
        clean_message = self._ps_quote(normalized_message)
        return (
            "$appId = 'PersonalAssistant';"
            " try { Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop } catch { } ;"
            " [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            " $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
            " $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template);"
            " $nodes = $xml.GetElementsByTagName('text');"
            " $nodes.Item(0).AppendChild($xml.CreateTextNode(%s)) | Out-Null;"
            " $nodes.Item(1).AppendChild($xml.CreateTextNode(%s)) | Out-Null;"
            " $toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
            " $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId);"
            " $notifier.Show($toast);"
        ) % (clean_title, clean_message)

    @staticmethod
    def _ps_quote(value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"


__all__ = ["SystemNotifier"]


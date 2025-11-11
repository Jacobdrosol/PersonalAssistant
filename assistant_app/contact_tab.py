from __future__ import annotations

import json
import mimetypes
import os
import smtplib
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .environment import ensure_user_data_dir
from .version import __version__


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    from_address: str


@dataclass(frozen=True)
class HttpEndpointConfig:
    url: str
    api_key: Optional[str]
    timeout: int = 30


class ContactTab(ttk.Frame):
    """
    Contact form that allows end-users to send feedback directly to the maintainer.
    Attachments are supported and the full submission flow is logged so issues can be audited.
    """

    SUPPORT_RECIPIENT = "jacobdrosol@hotmail.com"
    SMTP_CONFIG_FILENAME = "contact_support.smtp.json"
    HTTP_CONFIG_FILENAME = "contact_support.endpoint.json"

    def __init__(self, master: tk.Misc, data_root: Path, *, app_version: Optional[str] = None) -> None:
        super().__init__(master, padding=(16, 16, 16, 12))
        self.data_root = data_root
        self.log_path = ensure_user_data_dir() / "contact-submissions.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.app_version = app_version or __version__

        self.name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Fill out the form and click Send to submit your feedback.")
        self.delivery_status_var = tk.StringVar(value="Detecting delivery options...")

        self.attachments: List[Path] = []
        self._attachment_items: Dict[str, Path] = {}
        self._sending = False
        self._log_lock = threading.Lock()
        self.smtp_config_path = self.data_root / self.SMTP_CONFIG_FILENAME
        self._smtp_source: Optional[str] = None
        self._smtp_source_error: Optional[str] = None
        self._smtp_available = False
        self.http_config_path = self.data_root / self.HTTP_CONFIG_FILENAME
        self._http_source: Optional[str] = None
        self._http_source_error: Optional[str] = None
        self._http_available = False

        self._build_ui()
        self._update_delivery_status()

    # ------------------------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X, pady=(0, 16))

        heading_row = ttk.Frame(header)
        heading_row.pack(fill=tk.X)
        ttk.Label(heading_row, text="Contact & Feedback", style="SidebarHeading.TLabel").pack(side=tk.LEFT)
        actions = ttk.Frame(heading_row)
        actions.pack(side=tk.RIGHT)
        ttk.Button(actions, text="Import SMTP Config...", command=self._import_smtp_config).pack(side=tk.LEFT)
        ttk.Button(actions, text="Refresh Status", command=self._update_delivery_status).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(
            header,
            text="Submit feature ideas, issues, or other notes. Files such as screenshots or documents are welcome.",
            wraplength=640,
        ).pack(anchor="w", pady=(6, 0))

        form = ttk.Frame(self)
        form.pack(fill=tk.X, pady=(0, 16))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Name").grid(row=0, column=0, sticky="w")
        name_entry = ttk.Entry(form, textvariable=self.name_var)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(form, text="Email").grid(row=1, column=0, sticky="w", pady=(8, 0))
        email_entry = ttk.Entry(form, textvariable=self.email_var)
        email_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(form, text="Description").grid(row=2, column=0, sticky="nw", pady=(8, 0))
        self.description_text = tk.Text(form, height=10, wrap="word")
        self.description_text.grid(row=2, column=1, sticky="nsew", padx=(8, 0), pady=(8, 0))
        form.rowconfigure(2, weight=1)

        attachment_frame = ttk.Frame(self)
        attachment_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 16))
        attachment_frame.columnconfigure(0, weight=1)

        heading = ttk.Frame(attachment_frame)
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)

        ttk.Label(heading, text="Attachments").grid(row=0, column=0, sticky="w")
        btns = ttk.Frame(heading)
        btns.grid(row=0, column=1, sticky="e")
        ttk.Button(btns, text="Add Files…", command=self._add_attachments).pack(side=tk.LEFT)
        self.remove_btn = ttk.Button(btns, text="Remove Selected", command=self._remove_selected, state=tk.DISABLED)
        self.remove_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.attachment_tree = ttk.Treeview(
            attachment_frame,
            columns=("name", "size"),
            show="headings",
            height=6,
            selectmode="extended",
        )
        self.attachment_tree.heading("name", text="File name")
        self.attachment_tree.heading("size", text="Size")
        self.attachment_tree.column("name", anchor="w", width=420)
        self.attachment_tree.column("size", anchor="center", width=120)
        self.attachment_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        attachment_frame.rowconfigure(1, weight=1)

        scrollbar = ttk.Scrollbar(attachment_frame, orient=tk.VERTICAL, command=self.attachment_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.attachment_tree.configure(yscrollcommand=scrollbar.set)
        self.attachment_tree.bind("<<TreeviewSelect>>", lambda _: self._update_attachment_controls())

        footer = ttk.Frame(self)
        footer.pack(fill=tk.X)

        status_column = ttk.Frame(footer)
        status_column.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(status_column, textvariable=self.status_var, foreground="#9FA8DA").pack(anchor="w")
        ttk.Label(status_column, textvariable=self.delivery_status_var, foreground="#9FA8DA").pack(anchor="w")

        self.send_button = ttk.Button(footer, text="Send", command=self._on_send)
        self.send_button.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ Attachment helpers
    def _add_attachments(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self,
            title="Select files to attach",
            filetypes=[("All files", "*.*")],
        )
        if not files:
            return
        added = 0
        for file_path in files:
            path = Path(file_path).resolve()
            if not path.exists() or path in self.attachments:
                continue
            self.attachments.append(path)
            self._insert_attachment(path)
            added += 1
            self._log(f"Attachment queued: {path} ({path.stat().st_size} bytes)")
        if added:
            self.status_var.set(f"Added {added} attachment(s).")
        self._update_attachment_controls()

    def _insert_attachment(self, path: Path) -> None:
        size = self._format_size(path.stat().st_size)
        item_id = f"att::{len(self._attachment_items)}::{path.name}"
        self._attachment_items[item_id] = path
        self.attachment_tree.insert("", tk.END, iid=item_id, values=(path.name, size))

    def _remove_selected(self) -> None:
        selected = list(self.attachment_tree.selection())
        if not selected:
            return
        for item in selected:
            path = self._attachment_items.pop(item, None)
            if path and path in self.attachments:
                self.attachments.remove(path)
                self._log(f"Attachment removed: {path}")
            self.attachment_tree.delete(item)
        self.status_var.set(f"Removed {len(selected)} attachment(s).")
        self._update_attachment_controls()

    def _update_attachment_controls(self) -> None:
        if self._sending:
            self.remove_btn.configure(state=tk.DISABLED)
            self.send_button.configure(state=tk.DISABLED)
            return
        has_selection = bool(self.attachment_tree.selection())
        self.remove_btn.configure(state=tk.NORMAL if has_selection else tk.DISABLED)
        self.send_button.configure(state=tk.NORMAL)

    def _update_delivery_status(self) -> None:
        self._smtp_available = False
        self._http_available = False
        settings = self._load_smtp_settings(require=False)
        if settings:
            source = self._smtp_source or "SMTP configuration"
            self.delivery_status_var.set(f"Delivery via SMTP ({source}).")
            self._smtp_available = True
            return
        endpoint = self._load_http_endpoint(require=False)
        if endpoint:
            source = self._http_source or "HTTP configuration"
            self.delivery_status_var.set(f"Delivery via support API ({source}).")
            self._http_available = True
            return
        if self._smtp_source_error:
            self.delivery_status_var.set(f"Delivery configuration error: {self._smtp_source_error}")
        elif self._http_source_error:
            self.delivery_status_var.set(f"API configuration error: {self._http_source_error}")
        else:
            self.delivery_status_var.set(
                f"Delivery via local Outlook. Add {self.smtp_config_path.name} or {self.http_config_path.name} to enable remote delivery."
            )

    # ------------------------------------------------------------------ Sending logic
    def _on_send(self) -> None:
        if self._sending:
            return
        name = self.name_var.get().strip()
        email = self.email_var.get().strip()
        description = self.description_text.get("1.0", tk.END).strip()

        if not name:
            messagebox.showerror("Contact", "Please provide your name.", parent=self)
            return
        if not self._is_valid_email(email):
            messagebox.showerror("Contact", "Enter a valid email address so we can follow up.", parent=self)
            return
        if not description:
            messagebox.showerror("Contact", "Add a short description so we know how to help.", parent=self)
            return
        if not self._smtp_available and not self._http_available:
            messagebox.showinfo(
                "Contact",
                "No SMTP configuration has been imported. Please click 'Import SMTP Config...' and provide the file from Jacob.",
                parent=self,
            )
            return

        self._sending = True
        self.status_var.set("Sending message...")
        self._update_attachment_controls()
        self._log(f"Submission initiated by {name} <{email}> with {len(self.attachments)} attachment(s).")

        attachments = list(self.attachments)
        thread = threading.Thread(
            target=self._send_in_background,
            args=(name, email, description, attachments),
            daemon=True,
        )
        thread.start()

    def _send_in_background(
        self,
        name: str,
        email: str,
        description: str,
        attachments: Iterable[Path],
    ) -> None:
        sent_at = datetime.now().astimezone()
        try:
            delivered = False
            settings = self._load_smtp_settings(require=False)
            if settings:
                message = self._build_message(settings, name, email, description, attachments, sent_at)
                self._deliver_via_smtp(settings, message)
                delivered = True
            else:
                endpoint = self._load_http_endpoint(require=False)
                if endpoint:
                    self._send_via_http_endpoint(endpoint, name, email, description, attachments, sent_at)
                    delivered = True
            if not delivered:
                if self._send_via_outlook(name, email, description, attachments, sent_at):
                    self._log("Submission sent through Outlook automation.")
                    delivered = True
                else:
                    raise RuntimeError(
                        "Support email is not configured and Outlook automation is unavailable on this system."
                    )
        except Exception as exc:  # pragma: no cover - UI code
            self._log(f"Submission failed: {exc}\n{traceback.format_exc()}")
            self.after(
                0,
                lambda: self._finalize_send(
                    success=False,
                    user_message=f"Failed to send your submission:\n{exc}",
                ),
            )
            return
        self._log("Submission sent successfully.")
        self.after(
            0,
            lambda: self._finalize_send(
                success=True,
                user_message="Thank you! Your submission has been sent.",
            ),
        )

    def _finalize_send(self, success: bool, user_message: str) -> None:
        self._sending = False
        self._update_attachment_controls()
        if success:
            messagebox.showinfo("Contact", user_message, parent=self)
            self._reset_form()
            self.status_var.set("Submission sent. You can send another message if needed.")
        else:
            messagebox.showerror("Contact", user_message, parent=self)
            self.status_var.set("Sending failed. Review the message and try again.")

    def _reset_form(self) -> None:
        self.name_var.set("")
        self.email_var.set("")
        self.description_text.delete("1.0", tk.END)
        self.attachments.clear()
        for item_id in list(self._attachment_items.keys()):
            self.attachment_tree.delete(item_id)
        self._attachment_items.clear()

    # ------------------------------------------------------------------ Message helpers
    def _build_message(
        self,
        settings: SmtpSettings,
        name: str,
        email: str,
        description: str,
        attachments: Iterable[Path],
        sent_at: datetime,
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = "PERSONAL ASSISTANT USER SUBMISSION"
        message["From"] = settings.from_address
        message["To"] = self.SUPPORT_RECIPIENT
        message["Reply-To"] = email
        message["X-Priority"] = "1"
        message["X-MSMail-Priority"] = "High"
        message["Importance"] = "High"

        body = self._compose_body(name, email, description, sent_at)
        message.set_content(body)

        for path in attachments:
            try:
                data = path.read_bytes()
            except Exception as exc:  # pragma: no cover - filesystem issues
                raise RuntimeError(f"Failed to read attachment '{path.name}': {exc}") from exc
            maintype, subtype = self._guess_mime_types(path)
            message.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
            self._log(f"Attachment added to email: {path} ({len(data)} bytes, {maintype}/{subtype})")

        return message

    def _deliver_via_smtp(self, settings: SmtpSettings, message: EmailMessage) -> None:
        self._log(
            f"Connecting to SMTP server {settings.host}:{settings.port} "
            f"(TLS={'yes' if settings.use_tls else 'no'}) as {settings.username}"
        )
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as server:
            server.ehlo()
            if settings.use_tls:
                server.starttls()
                server.ehlo()
                self._log("TLS negotiation completed.")
            if settings.username and settings.password:
                server.login(settings.username, settings.password)
                self._log("SMTP authentication succeeded.")
            server.send_message(message)
        source = self._smtp_source or "SMTP configuration"
        self._log(f"SMTP transaction completed without error via {source}.")

    def _send_via_http_endpoint(
        self,
        endpoint: HttpEndpointConfig,
        name: str,
        email: str,
        description: str,
        attachments: Iterable[Path],
        sent_at: datetime,
    ) -> None:
        headers = {
            "User-Agent": f"PersonalAssistant/{self.app_version}",
        }
        if endpoint.api_key:
            headers["x-functions-key"] = endpoint.api_key
        data = {
            "name": name,
            "email": email,
            "description": description,
            "app_version": self.app_version,
            "sent_at": sent_at.isoformat(),
        }
        files = []
        for path in attachments:
            maintype, subtype = self._guess_mime_types(path)
            try:
                payload = path.read_bytes()
            except Exception as exc:
                raise RuntimeError(f"Unable to read attachment '{path.name}': {exc}") from exc
            files.append(("attachments", (path.name, payload, f"{maintype}/{subtype}")))
            self._log(f"Attachment prepared for HTTP upload: {path} ({len(payload)} bytes)")
        response = requests.post(
            endpoint.url,
            data=data,
            files=files or None,
            headers=headers,
            timeout=endpoint.timeout,
        )
        if response.status_code >= 400:
            snippet = response.text.strip()
            if len(snippet) > 240:
                snippet = snippet[:240] + "..."
            raise RuntimeError(f"Support API error {response.status_code}: {snippet or 'Unknown error'}")
        source = self._http_source or "HTTP configuration"
        self._log(f"Submission relayed via HTTP endpoint ({source}). Status: {response.status_code}")

    # ------------------------------------------------------------------ Utility helpers
    def _load_smtp_settings(self, *, require: bool = True) -> Optional[SmtpSettings]:
        self._smtp_source_error = None
        env_settings = self._smtp_from_env()
        if env_settings:
            self._smtp_source = "environment variables"
            return env_settings
        file_settings, file_error = self._smtp_from_file()
        if file_settings:
            self._smtp_source = f"{self.smtp_config_path.name}"
            return file_settings
        self._smtp_source = None
        if file_error:
            self._smtp_source_error = file_error
        if require:
            raise RuntimeError(file_error or "Support email is not configured.")
        return None

    def _smtp_from_env(self) -> Optional[SmtpSettings]:
        host = os.getenv("PA_SUPPORT_SMTP_HOST", "").strip()
        username = os.getenv("PA_SUPPORT_SMTP_USERNAME", "").strip()
        password = os.getenv("PA_SUPPORT_SMTP_PASSWORD", "").strip()
        if not host or not username or not password:
            return None
        port = int(os.getenv("PA_SUPPORT_SMTP_PORT", "587"))
        use_tls_env = os.getenv("PA_SUPPORT_SMTP_USE_TLS", "1").lower()
        use_tls = use_tls_env not in {"0", "false", "no"}
        from_address = os.getenv("PA_SUPPORT_FROM_ADDRESS", username).strip() or username
        return SmtpSettings(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=use_tls,
            from_address=from_address,
        )

    def _smtp_from_file(self) -> tuple[Optional[SmtpSettings], Optional[str]]:
        path = self.smtp_config_path
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, f"Invalid SMTP configuration file ({path.name}): {exc}"
        host = str(data.get("host", "")).strip()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()
        required_values = {"host": host, "username": username, "password": password}
        missing = [field for field, value in required_values.items() if not value]
        if missing:
            return None, f"Missing required fields {', '.join(missing)} in {path.name}"
        port_value = data.get("port", 587)
        try:
            port = int(port_value)
        except (TypeError, ValueError):
            return None, f"Invalid port value '{port_value}' in {path.name}"
        use_tls_raw = data.get("use_tls", True)
        if isinstance(use_tls_raw, bool):
            use_tls = use_tls_raw
        else:
            use_tls = str(use_tls_raw).strip().lower() not in {"0", "false", "no"}
        from_address = str(data.get("from_address", username)).strip() or username
        return (
            SmtpSettings(
                host=host,
                port=port,
                username=username,
                password=password,
                use_tls=use_tls,
                from_address=from_address,
            ),
            None,
        )

    def _load_http_endpoint(self, *, require: bool = True) -> Optional[HttpEndpointConfig]:
        self._http_source_error = None
        env_settings = self._http_from_env()
        if env_settings:
            self._http_source = "environment variables"
            return env_settings
        file_settings, file_error = self._http_from_file()
        if file_settings:
            self._http_source = f"{self.http_config_path.name}"
            return file_settings
        self._http_source = None
        if file_error:
            self._http_source_error = file_error
        if require:
            raise RuntimeError(file_error or "Support API endpoint is not configured.")
        return None

    def _http_from_env(self) -> Optional[HttpEndpointConfig]:
        url = os.getenv("PA_SUPPORT_HTTP_ENDPOINT", "").strip()
        if not url:
            return None
        api_key = os.getenv("PA_SUPPORT_HTTP_KEY", "").strip() or None
        timeout_value = os.getenv("PA_SUPPORT_HTTP_TIMEOUT", "30")
        try:
            timeout = max(5, int(timeout_value))
        except (TypeError, ValueError):
            timeout = 30
        return HttpEndpointConfig(url=url, api_key=api_key, timeout=timeout)

    def _http_from_file(self) -> tuple[Optional[HttpEndpointConfig], Optional[str]]:
        path = self.http_config_path
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, f"Invalid endpoint configuration file ({path.name}): {exc}"
        url = str(data.get("url", "")).strip()
        if not url:
            return None, f"Missing 'url' in {path.name}"
        api_key_raw = data.get("api_key", "")
        api_key = str(api_key_raw).strip() or None
        timeout_raw = data.get("timeout", 30)
        try:
            timeout = max(5, int(timeout_raw))
        except (TypeError, ValueError):
            return None, f"Invalid timeout value '{timeout_raw}' in {path.name}"
        return HttpEndpointConfig(url=url, api_key=api_key, timeout=timeout), None

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    @staticmethod
    def _guess_mime_types(path: Path) -> tuple[str, str]:
        mimetype, _ = mimetypes.guess_type(path.name)
        if mimetype:
            maintype, subtype = mimetype.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        return maintype, subtype

    @staticmethod
    def _is_valid_email(value: str) -> bool:
        return "@" in value and "." in value.split("@")[-1]

    def _log(self, message: str) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
        with self._log_lock:
            try:
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + os.linesep)
            except Exception:
                # Best-effort logging; ignore filesystem errors.
                pass

    def _compose_body(self, name: str, email: str, description: str, sent_at: datetime) -> str:
        timestamp = sent_at.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        if not timestamp:
            timestamp = sent_at.strftime("%Y-%m-%d %H:%M:%S")
        return (
            "New submission from Personal Assistant:\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Sent at: {timestamp}\n"
            f"App version: {self.app_version}\n\n"
            f"Description:\n{description}\n"
        )

    def _send_via_outlook(
        self,
        name: str,
        email: str,
        description: str,
        attachments: Iterable[Path],
        sent_at: datetime,
    ) -> bool:
        try:
            import win32com.client  # type: ignore[import]
        except Exception:
            self._log("Outlook automation unavailable; falling back to SMTP configuration.")
            return False

        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)
        except Exception as exc:
            self._log(f"Unable to access Outlook: {exc}")
            return False

        mail.To = self.SUPPORT_RECIPIENT
        mail.Subject = "PERSONAL ASSISTANT USER SUBMISSION"
        mail.Body = self._compose_body(name, email, description, sent_at)
        for path in attachments:
            try:
                mail.Attachments.Add(str(path))
                self._log(f"Outlook attachment added: {path}")
            except Exception as exc:  # pragma: no cover - COM automation issues
                raise RuntimeError(f"Failed to add attachment '{path.name}' via Outlook: {exc}") from exc
        try:
            mail.Send()
        except Exception as exc:
            raise RuntimeError(f"Outlook was unable to send the message: {exc}") from exc
        return True

    def _open_smtp_folder(self) -> None:
        template = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "support@example.com",
            "password": "CHANGE_ME",
            "use_tls": True,
            "from_address": "support@example.com",
        }
        self._open_config_folder(self.smtp_config_path, template, "SMTP")

    def _open_http_folder(self) -> None:
        template = {
            "url": "https://your-function.azurewebsites.net/api/support",
            "api_key": "PASTE_FUNCTION_KEY_HERE",
            "timeout": 30,
        }
        self._open_config_folder(self.http_config_path, template, "Endpoint")

    def _import_smtp_config(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Select SMTP configuration file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return
        try:
            data = Path(selected).read_text(encoding="utf-8")
            json.loads(data)
        except Exception as exc:
            messagebox.showerror("Import SMTP Config", f"Could not read file:\n{exc}", parent=self)
            return
        try:
            self.smtp_config_path.parent.mkdir(parents=True, exist_ok=True)
            self.smtp_config_path.write_text(data, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Import SMTP Config", f"Could not save configuration:\n{exc}", parent=self)
            return
        self.status_var.set("SMTP configuration imported successfully.")
        self._update_delivery_status()

    def _open_config_folder(self, path: Path, template: dict, label: str) -> None:
        folder = path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Contact", f"Could not prepare the config folder:\n{exc}", parent=self)
            return
        if not path.exists():
            try:
                path.write_text(json.dumps(template, indent=2), encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("Contact", f"Unable to create template file:\n{exc}", parent=self)
                return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            messagebox.showerror("Contact", f"Unable to open the config folder:\n{exc}", parent=self)
            return
        self.status_var.set(f"{label} configuration folder opened at {folder}")
        self._update_delivery_status()

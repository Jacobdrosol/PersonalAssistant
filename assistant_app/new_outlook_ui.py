from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
import re
import tempfile
import time
from typing import Callable, Optional
from uuid import uuid4

try:
    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover - reported at runtime
    win32api = None
    win32clipboard = None
    win32con = None
    win32gui = None
    win32process = None

try:
    from comtypes.client import CreateObject, GetModule

    GetModule("UIAutomationCore.dll")
    from comtypes.gen.UIAutomationClient import (  # type: ignore[import-not-found]
        CUIAutomation,
        IUIAutomation,
        IUIAutomationInvokePattern,
        IUIAutomationSelectionItemPattern,
        IUIAutomationTogglePattern,
    )
except Exception:  # pragma: no cover - reported at runtime
    CreateObject = None
    CUIAutomation = None
    IUIAutomation = None
    IUIAutomationInvokePattern = None
    IUIAutomationSelectionItemPattern = None
    IUIAutomationTogglePattern = None

from .production_log_engine import OutlookAttachment, ProductionLogError, _naive_datetime


TREE_SCOPE_CHILDREN = 2
TREE_SCOPE_DESCENDANTS = 4
UIA_PROCESS_ID_PROPERTY = 30002
UIA_CONTROL_TYPE_PROPERTY = 30003
UIA_NAME_PROPERTY = 30005
UIA_AUTOMATION_ID_PROPERTY = 30011
UIA_TREE_ITEM = 50024
UIA_LIST_ITEM = 50007
UIA_BUTTON = 50000
UIA_CHECK_BOX = 50002
UIA_TEXT = 50020
UIA_DOCUMENT = 50030
UIA_INVOKE_PATTERN = 10000
UIA_SELECTION_ITEM_PATTERN = 10010
UIA_TOGGLE_PATTERN = 10015
TOGGLE_OFF = 0
TOGGLE_ON = 1


@dataclass(slots=True)
class NewOutlookMessagePreview:
    message_id: str
    received_time: Optional[datetime]
    subject_summary: str
    sender: str
    categories: list[str]
    unread: bool
    has_attachments: bool


class NewOutlookUiSource:
    """Read New Outlook through its Windows accessibility surface.

    New Outlook does not expose the Classic Outlook COM object model. Its
    WebView2 accessibility tree does expose folders, message metadata,
    attachments, and category controls. This adapter resolves those elements at
    runtime and only uses their live bounds for actions that Chromium requires
    to originate from a genuine pointer gesture (not fixed screen coordinates).

    LIMITATION: New Outlook must be open in an unlocked interactive Windows
    session. New Outlook does not provide the Classic Outlook COM API, and this
    project intentionally does not require users to register an Entra/Graph
    application. The adapter can restore a minimized Outlook window as needed,
    but Windows UI Automation cannot operate through the lock screen. A user
    acknowledgement is displayed before the adapter sends trusted pointer input.
    """

    def __init__(self) -> None:
        # One source instance is used for attachment retrieval and the later
        # category/read update, so one acknowledgement covers the complete run.
        self._control_notice_shown = False

    @classmethod
    def is_available(cls) -> bool:
        return CreateObject is not None and win32gui is not None and cls._find_outlook_window() is not None

    def preview_pending(
        self,
        *,
        folder_path: str,
        subject_contains: str = "",
        subject_exact: bool = False,
        sender_contains: str = "",
        body_contains: str = "",
        required_category: str = "",
        received_since: Optional[datetime] = None,
        received_until: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[NewOutlookMessagePreview]:
        runtime = self._runtime()
        try:
            self._select_folder(runtime, folder_path)
            rows = self._matching_rows(
                runtime,
                subject_contains=subject_contains,
                subject_exact=subject_exact,
                sender_contains=sender_contains,
                body_contains=body_contains,
                required_category=required_category,
                received_since=received_since,
                received_until=received_until,
            )
            return [self._preview(row) for row in rows[:limit]]
        finally:
            self._finish_runtime(runtime)

    def fetch_pending(
        self,
        *,
        folder_path: str,
        subject_contains: str = "",
        subject_exact: bool = False,
        sender_contains: str = "",
        body_contains: str = "",
        attachment_pattern: str = "*.csv",
        required_category: str = "",
        already_processed: Optional[Callable[[str, str], bool]] = None,
        received_since: Optional[datetime] = None,
        received_until: Optional[datetime] = None,
        limit: int = 30,
    ) -> list[OutlookAttachment]:
        runtime = self._runtime()
        try:
            self._select_folder(runtime, folder_path)
            rows = self._matching_rows(
                runtime,
                subject_contains=subject_contains,
                subject_exact=subject_exact,
                sender_contains=sender_contains,
                body_contains=body_contains,
                required_category=required_category,
                received_since=received_since,
                received_until=received_until,
            )
            attachments: list[OutlookAttachment] = []
            with tempfile.TemporaryDirectory(prefix="personal-assistant-new-outlook-") as temp_dir:
                for row in rows:
                    preview = self._preview(row)
                    self._select_message(row)
                    try:
                        details = self._selected_message_details(runtime)
                        subject = details["subject"]
                        sender = details["sender"] or preview.sender
                        body = details["body"]
                        if subject_contains:
                            if subject_exact and subject.casefold().strip() != subject_contains.casefold().strip():
                                continue
                            if not subject_exact and subject_contains.casefold() not in subject.casefold():
                                continue
                        if sender_contains and sender_contains.casefold() not in sender.casefold():
                            continue
                        if body_contains and body_contains.casefold() not in body.casefold():
                            continue
                        for name in details["attachments"]:
                            if not fnmatch(name.casefold(), (attachment_pattern or "*.csv").casefold()):
                                continue
                            if already_processed and already_processed(preview.message_id, name):
                                continue
                            destination = Path(temp_dir) / f"{len(attachments):03d}-{Path(name).name}"
                            self._save_selected_attachment(runtime, name, destination)
                            attachments.append(
                                OutlookAttachment(
                                    message_id=preview.message_id,
                                    attachment_name=name,
                                    received_time=preview.received_time,
                                    content=destination.read_bytes(),
                                    categories=preview.categories,
                                )
                            )
                            if len(attachments) >= limit:
                                return attachments
                    finally:
                        self._restore_unread(runtime, preview.unread)
            # Outlook attachments are returned as bytes. TemporaryDirectory removes
            # every downloaded CSV here (including on exceptions or early returns),
            # so the automation never retains a mailbox attachment cache on disk.
            return attachments
        finally:
            self._finish_runtime(runtime)

    def mark_message_updated(
        self,
        message_id: str,
        *,
        folder_path: str,
        remove_category: str,
        add_category: str,
    ) -> None:
        runtime = self._runtime()
        try:
            self._select_folder(runtime, folder_path)
            row = self._find_message_row(runtime, message_id)
            if row is None:
                raise ProductionLogError("New Outlook message is no longer visible in the configured folder.")
            self._select_message(row)
            if remove_category:
                self._set_category(runtime, remove_category, enabled=False)
            if add_category:
                self._set_category(runtime, add_category, enabled=True)
            # A successfully processed production-log email is deliberately marked
            # read. Fetch/preview restores the original unread state, so this state
            # change happens only after the workbook update has succeeded.
            self._mark_read(runtime)
        finally:
            self._finish_runtime(runtime)

    def _runtime(self):
        if CreateObject is None or win32gui is None:
            raise ProductionLogError(
                "New Outlook automation requires the comtypes and pywin32 Windows packages."
            )
        outlook_window = self._find_outlook_window()
        if outlook_window is None:
            raise ProductionLogError(
                "New Outlook is not open. Open New Outlook, leave it signed in, and keep the Windows session unlocked."
            )
        was_minimized = bool(win32gui.IsIconic(outlook_window))
        if was_minimized:
            # Chromium exposes zero-sized/stale accessibility bounds while New
            # Outlook is minimized. Restore before building the UIA tree, then
            # return the window to its prior minimized state after the operation.
            win32gui.ShowWindow(outlook_window, win32con.SW_RESTORE)
            time.sleep(0.8)
        process_id = win32process_id(outlook_window)
        try:
            automation = CreateObject(CUIAutomation, interface=IUIAutomation)
            root = automation.GetRootElement()
            condition = automation.CreatePropertyCondition(UIA_PROCESS_ID_PROPERTY, process_id)
            window = root.FindFirst(TREE_SCOPE_CHILDREN, condition)
        except Exception as exc:
            raise ProductionLogError(f"Unable to connect to New Outlook accessibility: {exc}") from exc
        if window is None:
            raise ProductionLogError("The New Outlook accessibility window could not be found.")
        return automation, window, outlook_window, was_minimized

    @staticmethod
    def _finish_runtime(runtime) -> None:
        _automation, _window, outlook_window, was_minimized = runtime
        if was_minimized and win32gui.IsWindow(outlook_window):
            win32gui.ShowWindow(outlook_window, win32con.SW_MINIMIZE)

    @staticmethod
    def _find_outlook_window() -> Optional[int]:
        candidates: list[int] = []

        def collect(hwnd, _extra):
            try:
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Outlook Host":
                    candidates.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(collect, None)
        return candidates[0] if candidates else None

    def _select_folder(self, runtime, folder_path: str) -> None:
        automation, window, _hwnd, _was_minimized = runtime
        target = next(
            (part.strip() for part in reversed(folder_path.replace("\\", "/").split("/")) if part.strip()),
            "",
        )
        if not target:
            raise ProductionLogError("Configure a New Outlook folder path first.")
        candidates = []
        for element in self._elements(window):
            if element.CurrentControlType != UIA_TREE_ITEM:
                continue
            if _folder_display_name(element.CurrentName, element.CurrentHelpText).casefold() == target.casefold():
                candidates.append(element)
        if not candidates:
            raise ProductionLogError(
                f"New Outlook folder '{target}' is not visible. Expand the configured folder tree once and try again."
            )
        selected = next((item for item in candidates if " selected" in (item.CurrentName or "").casefold()), None)
        if selected is not None:
            return
        if len(candidates) > 1:
            raise ProductionLogError(
                f"More than one visible New Outlook folder is named '{target}'. Select the intended folder manually once."
            )
        try:
            candidates[0].GetCurrentPattern(UIA_SELECTION_ITEM_PATTERN).QueryInterface(
                IUIAutomationSelectionItemPattern
            ).Select()
        except Exception as exc:
            raise ProductionLogError(f"New Outlook folder '{target}' could not be selected: {exc}") from exc
        self._wait_until(lambda: self._folder_is_selected(window, target), 5.0, "New Outlook folder selection")

    def _matching_rows(
        self,
        runtime,
        *,
        subject_contains: str,
        subject_exact: bool,
        sender_contains: str,
        body_contains: str,
        required_category: str,
        received_since: Optional[datetime],
        received_until: Optional[datetime],
    ):
        _automation, window, _hwnd, _was_minimized = runtime
        rows = []
        for row in self._message_rows(window):
            preview = self._preview(row)
            summary = preview.subject_summary.casefold()
            if subject_contains and subject_contains.casefold() not in summary:
                continue
            if sender_contains and sender_contains.casefold() not in (preview.sender or preview.subject_summary).casefold():
                continue
            if body_contains and body_contains.casefold() not in summary:
                continue
            if required_category and required_category.casefold() not in {
                item.casefold() for item in preview.categories
            }:
                continue
            comparable = _naive_datetime(preview.received_time)
            if received_since and comparable and comparable < _naive_datetime(received_since):
                continue
            if received_until and comparable and comparable > _naive_datetime(received_until):
                continue
            # Exact subjects are verified after selection, when the reading pane
            # exposes a dedicated subject element.
            rows.append(row)
        rows.sort(key=lambda item: self._preview(item).received_time or datetime.min)
        return rows

    def _message_rows(self, window):
        result = []
        for element in self._elements(window):
            name = element.CurrentName or ""
            if (
                element.CurrentControlType == UIA_LIST_ITEM
                and element.CurrentAutomationId
                and ("Has attachments" in name or "Unread" in name)
            ):
                result.append(element)
        return result

    def _find_message_row(self, runtime, message_id: str):
        _automation, window, _hwnd, _was_minimized = runtime
        for row in self._message_rows(window):
            if (row.CurrentAutomationId or "") == message_id:
                return row
        return None

    def _preview(self, row) -> NewOutlookMessagePreview:
        name = row.CurrentName or ""
        sender = ""
        categories: list[str] = []
        received = None
        for child in self._elements(row):
            child_name = child.CurrentName or ""
            help_text = child.CurrentHelpText or ""
            if "@" in child_name and child.CurrentControlType == 50006:
                sender = child_name
            category_match = re.search(r"category\s+(.+)$", help_text, flags=re.IGNORECASE)
            if category_match:
                value = category_match.group(1).strip()
                if value and value not in categories:
                    categories.append(value)
            if received is None and help_text:
                received = _parse_outlook_display_datetime(help_text)
        if not sender:
            address = re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", name)
            if address:
                sender = address.group(0)
        return NewOutlookMessagePreview(
            message_id=row.CurrentAutomationId or "",
            received_time=received,
            subject_summary=name,
            sender=sender,
            categories=categories,
            unread=name.startswith("Unread "),
            has_attachments="Has attachments" in name,
        )

    def _select_message(self, row) -> None:
        try:
            row.GetCurrentPattern(UIA_SELECTION_ITEM_PATTERN).QueryInterface(
                IUIAutomationSelectionItemPattern
            ).Select()
        except Exception as exc:
            raise ProductionLogError(f"New Outlook message could not be selected: {exc}") from exc
        time.sleep(0.6)

    def _selected_message_details(self, runtime) -> dict[str, object]:
        _automation, window, _hwnd, _was_minimized = runtime
        subject = ""
        sender = ""
        body_parts: list[str] = []
        attachments: list[str] = []
        for element in self._elements(window):
            automation_id = element.CurrentAutomationId or ""
            name = element.CurrentName or ""
            if automation_id.startswith("MSG_") and automation_id.endswith("_SUBJECT"):
                subject = name
            elif automation_id.startswith("MSG_") and automation_id.endswith("_FROM") and name.startswith("From:"):
                sender = name.partition(":")[2].strip()
            elif automation_id.startswith("UniqueMessageBody") and element.CurrentControlType == UIA_DOCUMENT:
                for child in self._elements(element):
                    if child.CurrentControlType == UIA_TEXT and child.CurrentName:
                        body_parts.append(child.CurrentName)
            elif element.CurrentControlType == UIA_LIST_ITEM:
                match = re.match(r"(.+?\.[A-Za-z0-9]{1,8})\s+Open(?:\s|$)", name)
                if match and match.group(1) not in attachments:
                    attachments.append(match.group(1))
        if not subject:
            raise ProductionLogError("New Outlook did not expose the selected message subject.")
        return {
            "subject": subject,
            "sender": sender,
            "body": "\n".join(body_parts),
            "attachments": attachments,
        }

    def _save_selected_attachment(self, runtime, attachment_name: str, destination: Path) -> None:
        automation, window, outlook_hwnd, _was_minimized = runtime
        self._dismiss_stale_save_dialogs()
        self._warn_before_control(outlook_hwnd)
        # New Outlook's WebView2 menu does not reliably honor UIA Expand/Invoke;
        # it may report success or an event-subscriber error without opening the
        # menu. Pointer-click the live accessibility bounds. Do not press Escape
        # here: when no popup is open, New Outlook treats Escape as a request to
        # clear the selected message and removes the attachment controls.
        desktop = automation.GetRootElement()
        menu_button = self._wait_for_attachment_menu_button(window, attachment_name, 2.0)
        self._click_element(menu_button, outlook_hwnd)
        save_as = self._wait_for_named_control(automation, desktop, 50011, "Save as", 3.0)
        if save_as is None:
            raise ProductionLogError("New Outlook did not open the attachment action menu.")
        self._click_element(save_as, outlook_hwnd)
        dialog = self._wait_for_save_dialog(5.0)
        if dialog is None:
            raise ProductionLogError("New Outlook did not open the attachment Save As dialog.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        # Chromium intentionally ignores directory components typed into New
        # Outlook's Save As filename field. Give the download a collision-proof
        # temporary name in the user's Downloads folder, copy its bytes into our
        # private TemporaryDirectory, and immediately remove only that uniquely
        # named download. Existing user files are never overwritten or deleted.
        download_name = f"PersonalAssistant-{uuid4().hex}-{Path(attachment_name).name}"
        downloaded_path = Path.home() / "Downloads" / download_name
        downloaded_path.unlink(missing_ok=True)
        try:
            filename_input = self._wait_for_filename_input(dialog, 5.0)
            if filename_input is None:
                raise ProductionLogError("The Save As filename field was not available.")
            self._replace_text(filename_input, download_name)
            # Post rather than Send: the shell may synchronously open a Confirm
            # Save As dialog, which would deadlock the automation thread if the
            # click were sent synchronously.
            self._click_window(win32gui.GetDlgItem(dialog, 1))
        except Exception as exc:
            win32gui.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
            raise ProductionLogError(f"The New Outlook attachment path could not be entered: {exc}") from exc
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if downloaded_path.exists():
                destination.write_bytes(downloaded_path.read_bytes())
                downloaded_path.unlink(missing_ok=True)
                return
            confirmation = self._find_dialog("Confirm Save As")
            if confirmation is not None:
                yes_button = self._find_child_button(confirmation, "&Yes")
                if yes_button is not None:
                    self._click_window(yes_button)
            time.sleep(0.1)
        downloaded_path.unlink(missing_ok=True)
        raise ProductionLogError(f"Timed out while waiting for saving {attachment_name}.")

    def _attachment_menu_button(self, window, attachment_name: str):
        attachment = self._find_attachment(window, attachment_name)
        if attachment is None:
            raise ProductionLogError(f"New Outlook attachment is no longer visible: {attachment_name}")
        attachment_rect = attachment.CurrentBoundingRectangle
        menu_buttons = []
        for element in self._elements(window):
            if element.CurrentControlType != UIA_BUTTON or (element.CurrentName or "") != "More actions":
                continue
            rectangle = element.CurrentBoundingRectangle
            if rectangle.right > rectangle.left and rectangle.bottom > rectangle.top:
                menu_buttons.append(element)
        if not menu_buttons:
            raise ProductionLogError("New Outlook attachment actions are unavailable.")
        return min(
            menu_buttons,
            key=lambda element: abs(
                _rect_center_y(element.CurrentBoundingRectangle) - _rect_center_y(attachment_rect)
            ),
        )

    def _wait_for_attachment_menu_button(self, window, attachment_name: str, timeout: float):
        deadline = time.monotonic() + timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                return self._attachment_menu_button(window, attachment_name)
            except ProductionLogError as exc:
                last_error = exc
                time.sleep(0.1)
        if last_error is not None:
            raise last_error
        raise ProductionLogError("New Outlook attachment actions are unavailable.")

    def _set_category(self, runtime, category: str, *, enabled: bool) -> None:
        _automation, window, outlook_hwnd, _was_minimized = runtime
        self._warn_before_control(outlook_hwnd)
        categorize = self._find_element(
            window,
            lambda item: item.CurrentControlType == UIA_BUTTON
            and (item.CurrentAutomationId or "") == "509"
            and (item.CurrentName or "") == "Categorize",
        )
        if categorize is None:
            raise ProductionLogError("New Outlook Categorize control is unavailable.")
        self._click_element(categorize, outlook_hwnd)
        checkbox = self._wait_for_element(
            window,
            lambda item: item.CurrentControlType == UIA_CHECK_BOX
            and (item.CurrentName or "").casefold() == category.casefold(),
            3.0,
        )
        if checkbox is None:
            self._press_escape()
            raise ProductionLogError(f"New Outlook category was not found: {category}")
        try:
            toggle = checkbox.GetCurrentPattern(UIA_TOGGLE_PATTERN).QueryInterface(IUIAutomationTogglePattern)
            state = int(toggle.CurrentToggleState)
            if (enabled and state != TOGGLE_ON) or (not enabled and state != TOGGLE_OFF):
                toggle.Toggle()
                time.sleep(0.4)
        finally:
            self._press_escape()

    def _restore_unread(self, runtime, was_unread: bool) -> None:
        if not was_unread:
            return
        _automation, window, _outlook_hwnd, _was_minimized = runtime
        button = self._find_element(
            window,
            lambda item: item.CurrentControlType == UIA_BUTTON and (item.CurrentName or "") == "Mark as unread",
        )
        if button is None:
            return
        try:
            button.GetCurrentPattern(UIA_INVOKE_PATTERN).QueryInterface(IUIAutomationInvokePattern).Invoke()
            time.sleep(0.2)
        except Exception:
            return

    def _mark_read(self, runtime) -> None:
        _automation, window, _outlook_hwnd, _was_minimized = runtime
        button = self._find_element(
            window,
            lambda item: item.CurrentControlType == UIA_BUTTON and (item.CurrentName or "") == "Mark as read",
        )
        if button is None:
            return
        try:
            button.GetCurrentPattern(UIA_INVOKE_PATTERN).QueryInterface(IUIAutomationInvokePattern).Invoke()
            time.sleep(0.3)
        except Exception as exc:
            raise ProductionLogError(f"The processed New Outlook message could not be marked read: {exc}") from exc

    def _find_attachment(self, window, attachment_name: str):
        target = attachment_name.casefold()
        return self._find_element(
            window,
            lambda item: item.CurrentControlType == UIA_LIST_ITEM
            and (item.CurrentName or "").casefold().startswith(target + " "),
        )

    @staticmethod
    def _elements(element):
        collection = element.FindAll(TREE_SCOPE_DESCENDANTS, _true_condition())
        return [collection.GetElement(index) for index in range(collection.Length)]

    @staticmethod
    def _find_element(window, predicate):
        for element in NewOutlookUiSource._elements(window):
            try:
                if predicate(element):
                    return element
            except Exception:
                continue
        return None

    @staticmethod
    def _wait_for_element(window, predicate, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = NewOutlookUiSource._find_element(window, predicate)
            if found is not None:
                return found
            time.sleep(0.1)
        return None

    @staticmethod
    def _wait_for_named_control(automation, root, control_type: int, name: str, timeout: float):
        control_condition = automation.CreatePropertyCondition(UIA_CONTROL_TYPE_PROPERTY, control_type)
        name_condition = automation.CreatePropertyCondition(UIA_NAME_PROPERTY, name)
        condition = automation.CreateAndCondition(control_condition, name_condition)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = root.FindFirst(TREE_SCOPE_DESCENDANTS, condition)
            if found is not None:
                return found
            time.sleep(0.1)
        return None

    @staticmethod
    def _folder_is_selected(window, target: str) -> bool:
        for element in NewOutlookUiSource._elements(window):
            if element.CurrentControlType != UIA_TREE_ITEM:
                continue
            if " selected" not in (element.CurrentName or "").casefold():
                continue
            if _folder_display_name(element.CurrentName, element.CurrentHelpText).casefold() == target.casefold():
                return True
        return False

    @staticmethod
    def _click_element(element, outlook_hwnd: int) -> None:
        if win32gui.IsIconic(outlook_hwnd):
            win32gui.ShowWindow(outlook_hwnd, win32con.SW_RESTORE)
            time.sleep(0.6)
        rectangle = element.CurrentBoundingRectangle
        if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
            raise ProductionLogError("A required New Outlook control is not visible on screen.")
        try:
            win32gui.SetForegroundWindow(outlook_hwnd)
        except Exception:
            pass
        original_position = win32gui.GetCursorPos()
        try:
            win32api.SetCursorPos(
                (int((rectangle.left + rectangle.right) / 2), int((rectangle.top + rectangle.bottom) / 2))
            )
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.2)
        finally:
            win32api.SetCursorPos(original_position)

    def _warn_before_control(self, outlook_hwnd: int) -> None:
        if self._control_notice_shown:
            return
        message = (
            "Personal Assistant found a production-log email and is ready to process it.\n\n"
            "New Outlook will temporarily come to the front and the mouse pointer may move while "
            "the CSV is downloaded and the email is updated. Outlook may come to the front more "
            "than once before the run finishes.\n\n"
            "Click OK when you are ready."
        )
        win32gui.MessageBox(
            outlook_hwnd,
            message,
            "Production Log Automation",
            win32con.MB_OK | win32con.MB_ICONINFORMATION | win32con.MB_SETFOREGROUND,
        )
        self._control_notice_shown = True

    @staticmethod
    def _wait_for_save_dialog(timeout: float) -> Optional[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            dialogs: list[int] = []

            def collect(hwnd, _extra):
                try:
                    if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "#32770":
                        win32gui.GetDlgItem(hwnd, 1)
                        dialogs.append(hwnd)
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(collect, None)
            if dialogs:
                return dialogs[0]
            time.sleep(0.1)
        return None

    @staticmethod
    def _find_dialog(title: str) -> Optional[int]:
        matches: list[int] = []

        def collect(hwnd, _extra):
            try:
                if (
                    win32gui.IsWindowVisible(hwnd)
                    and win32gui.GetClassName(hwnd) == "#32770"
                    and win32gui.GetWindowText(hwnd) == title
                ):
                    matches.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(collect, None)
        return matches[0] if matches else None

    @staticmethod
    def _find_child_button(dialog: int, text: str) -> Optional[int]:
        matches: list[int] = []

        def collect(hwnd, _extra):
            try:
                if win32gui.GetClassName(hwnd) == "Button" and win32gui.GetWindowText(hwnd) == text:
                    matches.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumChildWindows(dialog, collect, None)
        return matches[0] if matches else None

    @staticmethod
    def _dismiss_stale_save_dialogs() -> None:
        for title, button_text in (
            ("Confirm Save As", "&No"),
            ("Warning: this site can see edits you make", "Cancel"),
        ):
            dialog = NewOutlookUiSource._find_dialog(title)
            if dialog is None:
                continue
            button = NewOutlookUiSource._find_child_button(dialog, button_text)
            if button is not None:
                NewOutlookUiSource._click_window(button)
                time.sleep(0.2)

    @staticmethod
    def _click_window(hwnd: int) -> None:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        original_position = win32gui.GetCursorPos()
        try:
            win32api.SetCursorPos((int((left + right) / 2), int((top + bottom) / 2)))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.2)
        finally:
            win32api.SetCursorPos(original_position)

    @staticmethod
    def _replace_text(hwnd: int, value: str) -> None:
        """Enter text as keyboard input so the shell dialog commits the value."""
        previous_clipboard: Optional[str] = None
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    previous_clipboard = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(value, win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            NewOutlookUiSource._click_window(hwnd)
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord("A"), 0, 0, 0)
            win32api.keybd_event(ord("A"), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord("V"), 0, 0, 0)
            win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.2)
        finally:
            if previous_clipboard is not None:
                try:
                    win32clipboard.OpenClipboard()
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(previous_clipboard, win32con.CF_UNICODETEXT)
                    win32clipboard.CloseClipboard()
                except Exception:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass

    @staticmethod
    def _wait_for_filename_input(dialog: int, timeout: float) -> Optional[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            edits: list[tuple[int, int]] = []

            def collect(hwnd, _extra):
                try:
                    if win32gui.GetClassName(hwnd) == "Edit":
                        edits.append((hwnd, int(win32gui.GetDlgCtrlID(hwnd))))
                except Exception:
                    pass
                return True

            win32gui.EnumChildWindows(dialog, collect, None)
            preferred = next((hwnd for hwnd, control_id in edits if control_id == 1001), None)
            if preferred is not None:
                return preferred
            fallback = next((hwnd for hwnd, control_id in edits if control_id != 41477), None)
            if fallback is not None:
                return fallback
            time.sleep(0.1)
        return None

    @staticmethod
    def _wait_until(predicate, timeout: float, operation: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise ProductionLogError(f"Timed out while waiting for {operation}.")

    @staticmethod
    def _press_escape() -> None:
        win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
        win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)


_TRUE_CONDITION = None


def _true_condition():
    global _TRUE_CONDITION
    if _TRUE_CONDITION is None:
        automation = CreateObject(CUIAutomation, interface=IUIAutomation)
        _TRUE_CONDITION = automation.CreateTrueCondition()
    return _TRUE_CONDITION


def win32process_id(hwnd: int) -> int:
    _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
    return int(process_id)


def _folder_display_name(name: str, help_text: str) -> str:
    value = (help_text or name or "").strip()
    value = re.sub(r"\s+-\s+\d+\s+items?.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+selected(?:\s+\d+\s+unread)?$", "", value, flags=re.IGNORECASE)
    return value.strip()


def _parse_outlook_display_datetime(value: str) -> Optional[datetime]:
    text = re.sub(r"^[A-Za-z]{3}\s+", "", (value or "").strip())
    for pattern in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _rect_center_y(rectangle) -> float:
    return (rectangle.top + rectangle.bottom) / 2

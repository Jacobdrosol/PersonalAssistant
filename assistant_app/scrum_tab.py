from __future__ import annotations

from datetime import date, datetime
import calendar as cal
import re
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from .database import Database, SCRUM_PRIORITIES
from .models import ScrumNote, ScrumTask


class ScrumTab(ttk.Frame):
    STATUSES: Sequence[tuple[str, str]] = (
        ("todo", "To Do"),
        ("doing", "Doing"),
        ("review", "Review"),
        ("done", "Done"),
    )

    def __init__(self, master: tk.Misc, db: Database) -> None:
        super().__init__(master, padding=(16, 16))
        self.db = db
        self.status_title_map: Dict[str, str] = {status: title for status, title in self.STATUSES}
        self.status_key_map: Dict[str, str] = {title: status for status, title in self.STATUSES}
        self.status_frames: Dict[str, ttk.Frame] = {}
        self.status_columns_meta: Dict[str, Dict[str, tk.Widget]] = {}
        self.tasks: Dict[int, ScrumTask] = {}
        self._note_cache: Dict[int, List[ScrumNote]] = {}
        self._modal_overlay: Optional[tk.Frame] = None
        self._modal_panel: Optional[tk.Frame] = None
        self._active_canvas: Optional[tk.Canvas] = None

        self._configure_styles()
        self.base_bg = ttk.Style(self).lookup("TFrame", "background") or "#171821"
        self._drag_preview: Optional[tk.Toplevel] = None
        self._drag_data: Dict[str, object] = {"card": None, "task": None, "moved": False, "start": (0, 0)}
        self._build_ui()
        self._bind_mousewheel_support()
        self._board_canvas.bind("<Shift-MouseWheel>", self._on_board_mousewheel, add="+")  # type: ignore[attr-defined]
        self.refresh()

    def _on_board_mousewheel(self, event: tk.Event) -> str:
        direction = -1 if event.delta > 0 else 1
        self._board_canvas.xview_scroll(direction, "units")  # type: ignore[attr-defined]
        return "break"

    def _on_columns_container_configure(self, event: tk.Event) -> None:
        if not hasattr(self, "_board_canvas"):
            return
        container = self._columns_container  # type: ignore[attr-defined]
        container.update_idletasks()
        children = container.winfo_children()
        required = 0
        gap = 12
        for idx, child in enumerate(children):
            width = max(child.winfo_reqwidth(), self._column_min_width)  # type: ignore[attr-defined]
            required += width
            if idx < len(children) - 1:
                required += gap
        required += 24
        canvas = self._board_canvas  # type: ignore[attr-defined]
        canvas.configure(scrollregion=canvas.bbox("all"))
        current = canvas.winfo_width()
        canvas.itemconfigure(self._board_window, width=max(required, event.width, current))  # type: ignore[attr-defined]

    def _on_board_canvas_configure(self, _event: tk.Event) -> None:
        if not hasattr(self, "_board_canvas"):
            return
        self._board_canvas.configure(scrollregion=self._board_canvas.bbox("all"))  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ UI
    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("ScrumCardNormal.TFrame", background="#1c1d2b", relief="ridge", borderwidth=1)
        style.configure("ScrumCardNormal.TLabel", background="#1c1d2b", foreground="#E8EAF6", font=("Segoe UI", 10, "bold"))
        style.configure("ScrumCardMeta.TLabel", background="#1c1d2b", foreground="#9FA8DA", font=("Segoe UI", 9))
        style.configure("ScrumCardSoon.TFrame", background="#554822", relief="ridge", borderwidth=1)
        style.configure("ScrumCardSoon.TLabel", background="#554822", foreground="#f8f4d7", font=("Segoe UI", 10, "bold"))
        style.configure("ScrumCardSoonMeta.TLabel", background="#554822", foreground="#f8f4d7", font=("Segoe UI", 9))
        style.configure("ScrumCardOverdue.TFrame", background="#4c1f1f", relief="ridge", borderwidth=1)
        style.configure("ScrumCardOverdue.TLabel", background="#4c1f1f", foreground="#f5f5f5", font=("Segoe UI", 10, "bold"))
        style.configure("ScrumCardOverdueMeta.TLabel", background="#4c1f1f", foreground="#f5f5f5", font=("Segoe UI", 9))
        style.configure("ScrumCardDone.TFrame", background="#2a2b33", relief="ridge", borderwidth=1)
        style.configure("ScrumCardDone.TLabel", background="#2a2b33", foreground="#9fa8da", font=("Segoe UI", 10, "bold"))
        style.configure("ScrumCardDoneMeta.TLabel", background="#2a2b33", foreground="#c0c4d6", font=("Segoe UI", 9))
        style.configure("ScrumCardDragging.TFrame", background="#394055", relief="ridge", borderwidth=1)
        style.configure("ScrumCardDragging.TLabel", background="#394055", foreground="#E8EAF6", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="Scrum Dashboard", style="SidebarHeading.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="New Item", command=self._open_new_task).pack(side=tk.RIGHT)

        self._column_min_width = 320

        board_wrapper = ttk.Frame(self)
        board_wrapper.pack(fill=tk.BOTH, expand=True)
        board_wrapper.rowconfigure(0, weight=1)
        board_wrapper.columnconfigure(0, weight=1)

        self._board_canvas = tk.Canvas(board_wrapper, highlightthickness=0, bg=self.base_bg, bd=0)
        self._board_canvas.grid(row=0, column=0, sticky="nsew")
        h_scroll = ttk.Scrollbar(board_wrapper, orient=tk.HORIZONTAL, command=self._board_canvas.xview)
        h_scroll.grid(row=1, column=0, sticky="ew")
        self._board_canvas.configure(xscrollcommand=h_scroll.set)

        self._columns_container = ttk.Frame(self._board_canvas)
        self._board_window = self._board_canvas.create_window((0, 0), window=self._columns_container, anchor="nw")
        self._columns_container.bind("<Configure>", self._on_columns_container_configure)
        self._board_canvas.bind("<Configure>", self._on_board_canvas_configure)

        self._columns_container.rowconfigure(0, weight=1)
        for idx, (status, title) in enumerate(self.STATUSES):
            column = ttk.Frame(self._columns_container, padding=(0, 0))
            column.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 12, 0))
            self._columns_container.columnconfigure(idx, weight=1, uniform="board")

            ttk.Label(column, text=title, style="SidebarHeading.TLabel").grid(row=0, column=0, sticky="w")
            column.columnconfigure(0, weight=1)

            canvas = tk.Canvas(column, highlightthickness=0, bg=self.base_bg, bd=0)
            canvas.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
            column.rowconfigure(1, weight=1)
            scrollbar = ttk.Scrollbar(column, orient=tk.VERTICAL, command=canvas.yview)
            scrollbar.grid(row=1, column=1, sticky="ns", pady=(6, 0))
            canvas.configure(yscrollcommand=scrollbar.set)

            frame = ttk.Frame(canvas)
            frame.columnconfigure(0, weight=1)
            window_id = canvas.create_window((0, 0), window=frame, anchor="nw")

            def _configure(event: tk.Event, cv=canvas, win=window_id, frm=frame) -> None:
                cv.configure(scrollregion=cv.bbox("all"))
                cv.itemconfigure(win, width=cv.winfo_width())

            frame.bind("<Configure>", _configure)
            canvas.bind("<Configure>", lambda e, frm=frame: frm.configure(width=canvas.winfo_width()))
            self._register_scroll_region(canvas, frame)
            self.status_frames[status] = frame
            self.status_columns_meta[status] = {"frame": frame, "canvas": canvas, "column": column}

    def _register_scroll_region(self, canvas: tk.Canvas, frame: tk.Widget) -> None:
        def handle_enter(_: tk.Event, cv: tk.Canvas = canvas) -> None:
            self._set_active_canvas(cv)

        def handle_leave(_: tk.Event) -> None:
            self._set_active_canvas(None)

        for widget in (canvas, frame):
            widget.bind("<Enter>", handle_enter, add="+")
            widget.bind("<Leave>", handle_leave, add="+")

    def _register_card_scroll(self, card: "ScrumCard", canvas: tk.Canvas) -> None:
        def handle_enter(_: tk.Event, cv: tk.Canvas = canvas) -> None:
            self._set_active_canvas(cv)

        card.bind("<Enter>", handle_enter, add="+")
        for child in card.winfo_children():
            child.bind("<Enter>", handle_enter, add="+")

    def _bind_mousewheel_support(self) -> None:
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_mousewheel_up, add="+")
        self.bind_all("<Button-5>", self._on_mousewheel_down, add="+")

    def _set_active_canvas(self, canvas: Optional[tk.Canvas]) -> None:
        self._active_canvas = canvas

    def _scroll_active_canvas(self, steps: int) -> None:
        canvas = self._active_canvas
        if canvas is None:
            return
        canvas.yview_scroll(steps, "units")

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        if event.delta == 0:
            return None
        steps = -1 if event.delta > 0 else 1
        magnitude = max(1, abs(event.delta) // 120)
        self._scroll_active_canvas(steps * magnitude)
        return "break"

    def _on_mousewheel_up(self, _: tk.Event) -> str | None:
        self._scroll_active_canvas(-1)
        return "break"

    def _on_mousewheel_down(self, _: tk.Event) -> str | None:
        self._scroll_active_canvas(1)
        return "break"

    # ------------------------------------------------------------------ Data handling
    def refresh(self) -> None:
        tasks = self.db.get_scrum_tasks()
        self.tasks: Dict[int, ScrumTask] = {task.id: task for task in tasks}
        self._note_cache.clear()
        for frame in self.status_frames.values():
            for widget in frame.winfo_children():
                widget.destroy()
        for task in tasks:
            meta = self.status_columns_meta.get(task.status)
            if not meta:
                continue
            container = meta["frame"]
            canvas = meta["canvas"]
            card = ScrumCard(self, container, task)
            card.apply_severity(self._task_severity(task))
            card.pack(fill=tk.X, pady=6)
            self._register_card_scroll(card, canvas)

    def _task_severity(self, task: ScrumTask) -> Optional[str]:
        if task.status == "done":
            return "done"
        if not task.target_date:
            return None
        today = date.today()
        delta = (task.target_date - today).days
        if delta < 0:
            return "overdue"
        if delta <= 1:
            return "due_soon"
        return None

    # ------------------------------------------------------------------ Actions
    def _open_new_task(self) -> None:
        self._open_task_editor(None)

    def _open_task_editor(self, task: Optional[ScrumTask]) -> None:
        self._close_modal()
        overlay = tk.Frame(self, bg="#000000", bd=0)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()

        canvas = tk.Canvas(overlay, bg="#000000", bd=0, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y = ttk.Scrollbar(overlay, orient=tk.VERTICAL, command=canvas.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scroll_y.set)

        holder = ttk.Frame(canvas, padding=(0, 20))
        holder.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=holder, anchor="n")

        def _resize_holder(_event: tk.Event | None = None) -> None:
            overlay.update_idletasks()
            width_available = overlay.winfo_width() - scroll_y.winfo_width() - 40
            canvas.itemconfigure(window_id, width=max(width_available, 420))

        overlay.bind("<Configure>", _resize_holder)
        self.after(10, _resize_holder)

        dialog = ScrumTaskDialog(
            holder,
            task=task,
            statuses=[title for _, title in self.STATUSES],
            status_title_map=self.status_title_map,
            status_key_map=self.status_key_map,
            on_submit=self._handle_task_submit,
            on_delete=self._handle_task_delete,
            on_add_note=self._handle_add_note,
            on_update_note=self._handle_update_note,
            on_delete_note=self._handle_delete_note,
            load_notes=self._load_notes,
            on_cancel=self._close_modal,
        )
        self._modal_overlay = overlay
        self._modal_panel = dialog

    def _handle_task_submit(self, payload: Dict[str, object], task_id: Optional[int]) -> None:
        try:
            if task_id is None:
                self.db.create_scrum_task(**payload)
            else:
                self.db.update_scrum_task(task_id, **payload)
        except ValueError as exc:
            messagebox.showerror("Save Failed", str(exc), parent=self)
            return
        self._close_modal()
        self.refresh()

    def _handle_task_delete(self, task_id: int) -> None:
        if not messagebox.askyesno("Delete Item", "Delete this task and its notes?", parent=self):
            return
        self.db.delete_scrum_task(task_id)
        self._close_modal()
        self.refresh()

    def _handle_add_note(self, task_id: int, content: str) -> None:
        self.db.create_scrum_note(task_id, content)
        self._note_cache.pop(task_id, None)

    def _handle_update_note(self, task_id: int, note_id: int, content: str) -> None:
        self.db.update_scrum_note(note_id, content)
        self._note_cache.pop(task_id, None)

    def _handle_delete_note(self, task_id: int, note_id: int) -> None:
        self.db.delete_scrum_note(note_id)
        self._note_cache.pop(task_id, None)

    def _load_notes(self, task_id: int) -> List[ScrumNote]:
        notes = self._note_cache.get(task_id)
        if notes is None:
            notes = self.db.get_scrum_notes(task_id)
            self._note_cache[task_id] = notes
        return notes

    def _close_modal(self) -> None:
        if self._modal_panel is not None and hasattr(self._modal_panel, '_close_date_picker'):
            self._modal_panel._close_date_picker()
        if self._modal_panel is not None:
            self._modal_panel.destroy()
            self._modal_panel = None
        if self._modal_overlay is not None:
            self._modal_overlay.destroy()
            self._modal_overlay = None
        self._close_drag_preview()
        self._drag_data = {"card": None, "moved": False, "task": None, "start": (0, 0)}

    def begin_drag(self, card: "ScrumCard", event: tk.Event) -> None:
        self._close_drag_preview()
        prev_style = getattr(card, "_drag_prev_style", card.cget("style") or "ScrumCardNormal.TFrame")
        card._drag_prev_style = prev_style  # type: ignore[attr-defined]
        card.configure(style="ScrumCardDragging.TFrame")
        for child in card.winfo_children():
            if isinstance(child, ttk.Label):
                child.configure(style="ScrumCardDragging.TLabel")
        self._drag_data = {
            "card": card,
            "task": card.task,
            "start": (event.x_root, event.y_root),
            "moved": False,
        }
        preview = tk.Toplevel(self)
        preview.overrideredirect(True)
        preview.attributes("-topmost", True)
        ttk.Label(preview, text=card.task.title, padding=6, style="SidebarHeading.TLabel").pack()
        preview.geometry(f"+{event.x_root}+{event.y_root}")
        self._drag_preview = preview

    def drag_motion(self, event: tk.Event) -> None:
        if not self._drag_data.get("card") or not self._drag_preview:
            return
        start_x, start_y = self._drag_data["start"]
        if abs(event.x_root - start_x) > 4 or abs(event.y_root - start_y) > 4:
            self._drag_data["moved"] = True
        self._drag_preview.geometry(f"+{event.x_root+16}+{event.y_root+16}")

    def end_drag(self, event: tk.Event) -> None:
        card: Optional[ScrumCard] = self._drag_data.get("card")  # type: ignore[assignment]
        moved = bool(self._drag_data.get("moved"))
        self._close_drag_preview()
        if card is not None:
            card.restore_style()
        if card is None:
            return
        new_status = self._status_at(event.x_root, event.y_root)
        if not moved:
            self._open_task_editor(card.task)
            return
        if new_status and new_status != card.task.status:
            try:
                self.db.update_scrum_task(card.task.id, status=new_status)
            except ValueError as exc:
                messagebox.showerror("Move Failed", str(exc), parent=self)
                return
        self.refresh()

    def _status_at(self, x_root: int, y_root: int) -> Optional[str]:
        for status, meta in self.status_columns_meta.items():
            column = meta["column"]
            x1 = column.winfo_rootx()
            y1 = column.winfo_rooty()
            x2 = x1 + column.winfo_width()
            y2 = y1 + column.winfo_height()
            if x1 <= x_root <= x2 and y1 <= y_root <= y2:
                return status
        return None

    def _close_drag_preview(self) -> None:
        if self._drag_preview is not None:
            self._drag_preview.destroy()
        self._drag_preview = None
        self._drag_data = {"card": None, "task": None, "moved": False, "start": (0, 0)}


class ScrumCard(ttk.Frame):
    def __init__(self, tab: ScrumTab, parent: tk.Misc, task: ScrumTask) -> None:
        super().__init__(parent, style="ScrumCardNormal.TFrame", padding=(12, 10), cursor="hand2")
        self.tab = tab
        self.task = task
        self._default_styles = ("ScrumCardNormal.TFrame", "ScrumCardNormal.TLabel", "ScrumCardMeta.TLabel")

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)

        title = ttk.Label(self, text=task.title, style="ScrumCardNormal.TLabel")
        title.pack(anchor="w")
        if task.target_date:
            due_text = task.target_date.isoformat()
            if task.require_time:
                due_text = f"{due_text} {task.require_time}"
        else:
            due_text = "No target date"
        ttk.Label(self, text=due_text, style="ScrumCardMeta.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(self, text=f"Priority: {task.priority}", style="ScrumCardMeta.TLabel").pack(anchor="w")
        for child in self.winfo_children():
            child.bind("<ButtonPress-1>", self._on_press)
            child.bind("<B1-Motion>", self._on_motion)
            child.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event: tk.Event) -> None:
        self.tab.begin_drag(self, event)

    def _on_motion(self, event: tk.Event) -> None:
        self.tab.drag_motion(event)

    def _on_release(self, event: tk.Event) -> None:
        self.tab.end_drag(event)

    def restore_style(self) -> None:
        prev_frame = getattr(self, "_drag_prev_style", self._default_styles[0])
        self.configure(style=prev_frame)
        labels = list(self.winfo_children())
        if labels:
            labels[0].configure(style=self._default_styles[1])
            for child in labels[1:]:
                child.configure(style=self._default_styles[2])

    def apply_severity(self, severity: Optional[str]) -> None:
        frame_style = "ScrumCardNormal.TFrame"
        title_style = "ScrumCardNormal.TLabel"
        meta_style = "ScrumCardMeta.TLabel"
        if severity == "overdue":
            frame_style = "ScrumCardOverdue.TFrame"
            title_style = "ScrumCardOverdue.TLabel"
            meta_style = "ScrumCardOverdueMeta.TLabel"
        elif severity == "due_soon":
            frame_style = "ScrumCardSoon.TFrame"
            title_style = "ScrumCardSoon.TLabel"
            meta_style = "ScrumCardSoonMeta.TLabel"
        elif self.task.status == "done":
            frame_style = "ScrumCardDone.TFrame"
            title_style = "ScrumCardDone.TLabel"
            meta_style = "ScrumCardDoneMeta.TLabel"
        self._default_styles = (frame_style, title_style, meta_style)
        self.configure(style=frame_style)
        children = list(self.winfo_children())
        if children:
            children[0].configure(style=title_style)
            for child in children[1:]:
                child.configure(style=meta_style)


class ScrumTaskDialog(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        task: Optional[ScrumTask],
        statuses: Iterable[str],
        status_title_map: Dict[str, str],
        status_key_map: Dict[str, str],
        on_submit: Callable[[Dict[str, object], Optional[int]], None],
        on_delete: Callable[[int], None],
        on_add_note: Callable[[int, str], None],
        on_update_note: Callable[[int, int, str], None],
        on_delete_note: Callable[[int, int], None],
        load_notes: Callable[[int], List[ScrumNote]],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self.task = task
        self.status_title_map = status_title_map
        self.status_key_map = status_key_map
        self._on_submit = on_submit
        self._on_delete = on_delete
        self._on_add_note = on_add_note
        self._on_update_note = on_update_note
        self._on_delete_note = on_delete_note
        self._load_notes_fn = load_notes
        self._on_cancel = on_cancel
        self.statuses = list(statuses)
        self._date_picker: Optional[DatePickerPopup] = None
        self._notes_map: Dict[int, ScrumNote] = {}

        self.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)
        container = self._build_form()

        ttk.Label(container, text="Title").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.title_entry = tk.Entry(container, width=50, bg="#ffffff", fg="#1c1d2b")
        self.title_entry.insert(0, self.task.title if self.task else "")
        self.title_entry.grid(row=1, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Status").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.status_var = tk.StringVar()
        status_title = self.status_title_map.get(self.task.status, self.statuses[0]) if self.task else self.statuses[0]
        self.status_var.set(status_title)
        self.status_combo = ttk.Combobox(container, values=self.statuses, state="readonly", textvariable=self.status_var)
        self.status_combo.grid(row=2, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Priority").grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.priority_var = tk.StringVar()
        priority_default = self.task.priority if self.task else "Unknown"
        if priority_default not in SCRUM_PRIORITIES:
            priority_default = "Unknown"
        self.priority_var.set(priority_default)
        self.priority_combo = ttk.Combobox(container, values=list(SCRUM_PRIORITIES), state="readonly", textvariable=self.priority_var)
        self.priority_combo.grid(row=3, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Target Date").grid(row=4, column=0, sticky="w", pady=(12, 0))
        target_row = ttk.Frame(container)
        target_row.grid(row=4, column=1, sticky="ew", pady=(12, 0))
        target_row.columnconfigure(0, weight=1)
        self.target_entry = tk.Entry(target_row, bg="#ffffff", fg="#1c1d2b")
        self.target_entry.insert(0, self.task.target_date.isoformat() if self.task and self.task.target_date else "")
        self.target_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(target_row, text="Pick", width=4, command=self._open_date_picker).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(target_row, text="Format: YYYY-MM-DD", foreground="#9FA8DA").grid(row=0, column=2, padx=(8, 0))

        ttk.Label(container, text="Require Time (optional)").grid(row=5, column=0, sticky="w", pady=(12, 0))
        self.require_time_entry = tk.Entry(container, bg="#ffffff", fg="#1c1d2b")
        default_require_time = self.task.require_time if self.task and self.task.require_time else ""
        self.require_time_entry.insert(0, default_require_time)
        self.require_time_entry.grid(row=5, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Tags (comma separated)").grid(row=6, column=0, sticky="w", pady=(12, 0))
        self.tags_entry = tk.Entry(container, bg="#ffffff", fg="#1c1d2b")
        self.tags_entry.insert(0, ", ".join(self.task.tags) if self.task else "")
        self.tags_entry.grid(row=6, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Collaborators (comma separated)").grid(row=7, column=0, sticky="w", pady=(12, 0))
        self.collaborators_entry = tk.Entry(container, bg="#ffffff", fg="#1c1d2b")
        self.collaborators_entry.insert(0, ", ".join(self.task.collaborators) if self.task else "")
        self.collaborators_entry.grid(row=7, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Description").grid(row=8, column=0, sticky="nw", pady=(12, 0))
        self.description_text = tk.Text(container, height=6, wrap="word", bg="#ffffff", fg="#1c1d2b")
        if self.task and self.task.description:
            self.description_text.insert("1.0", self.task.description)
        self.description_text.grid(row=8, column=1, sticky="ew")

        ttk.Label(container, text="Created").grid(row=9, column=0, sticky="w", pady=(12, 0))
        created_value = self.task.created_at.strftime("%Y-%m-%d %H:%M") if self.task else datetime.now().strftime("%Y-%m-%d %H:%M")
        ttk.Label(container, text=created_value).grid(row=9, column=1, sticky="w", pady=(12, 0))

        notes_frame = ttk.Frame(container)
        notes_frame.grid(row=10, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(1, weight=1)
        ttk.Label(notes_frame, text="Notes").grid(row=0, column=0, sticky="w")

        tree_container = ttk.Frame(notes_frame)
        tree_container.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)
        self.notes_tree = ttk.Treeview(
            tree_container,
            columns=("created", "content"),
            show="headings",
            selectmode="browse",
            height=6,
        )
        self.notes_tree.heading("created", text="Created")
        self.notes_tree.heading("content", text="Content")
        self.notes_tree.column("created", width=150, anchor="w")
        self.notes_tree.column("content", width=420, anchor="w")
        self.notes_tree.grid(row=0, column=0, sticky="nsew")
        notes_scroll = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.notes_tree.yview)
        notes_scroll.grid(row=0, column=1, sticky="ns")
        self.notes_tree.configure(yscrollcommand=notes_scroll.set)
        self.notes_tree.bind("<<TreeviewSelect>>", lambda _: self._update_note_actions())

        actions_row = ttk.Frame(notes_frame)
        actions_row.grid(row=2, column=0, sticky="e", pady=(6, 0))
        self.edit_note_button = ttk.Button(actions_row, text="Edit", command=self._edit_selected_note, state=tk.DISABLED)
        self.edit_note_button.pack(side=tk.LEFT)
        self.delete_note_button = ttk.Button(actions_row, text="Delete", command=self._delete_selected_note, state=tk.DISABLED)
        self.delete_note_button.pack(side=tk.LEFT, padx=(6, 0))

        new_note_frame = ttk.Frame(notes_frame)
        new_note_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        new_note_frame.columnconfigure(0, weight=1)
        ttk.Label(new_note_frame, text="Add Note").grid(row=0, column=0, sticky="w")
        self.new_note_text = tk.Text(new_note_frame, height=3, wrap="word", bg="#ffffff", fg="#1c1d2b")
        self.new_note_text.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.add_note_button = ttk.Button(new_note_frame, text="Add Note", command=self._add_note)
        self.add_note_button.grid(row=2, column=0, sticky="e", pady=(6, 0))

        button_row = ttk.Frame(container)
        button_row.grid(row=11, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT)
        if self.task is not None:
            ttk.Button(button_row, text="Delete", command=self._delete).pack(side=tk.LEFT)

        if self.task is None:
            self.notes_tree.configure(selectmode="none")
            self.edit_note_button.state(["disabled"])
            self.delete_note_button.state(["disabled"])
            self.new_note_text.configure(state="disabled")
            self.add_note_button.state(["disabled"])
        else:
            self.notes_tree.configure(selectmode="browse")
            self._refresh_notes()
            self._update_note_actions()
        self.title_entry.focus_set()

    def _build_form(self) -> ttk.Frame:
        self.columnconfigure(0, weight=1)
        container = ttk.Frame(self, padding=24)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(1, weight=1)
        container.rowconfigure(10, weight=1)
        ttk.Label(container, text="Task Details", style="SidebarHeading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        return container

    def _parse_list(self, raw: str) -> List[str]:
        results: List[str] = []
        for chunk in raw.replace("\n", ",").split(","):
            value = chunk.strip()
            if value:
                results.append(value)
        return results

    def _normalize_require_time(self, raw: str) -> str:
        text = raw.strip()
        if not text:
            raise ValueError("Require Time must be left blank or contain a time value (e.g. 07:00 or 7:30am).")
        text = text.replace(".", ":")
        period_match = re.search(r"(?i)(am|pm)\s*$", text)
        period: Optional[str] = None
        if period_match:
            period = period_match.group(1).lower()
            text = text[:period_match.start()].strip()
        core = re.sub(r"\s+", "", text)
        if not core:
            raise ValueError("Require Time must include digits for the hour.")
        try:
            if ":" in core:
                hour_text, minute_text = core.split(":", 1)
                hour = int(hour_text)
                minute = int(minute_text)
            elif core.isdigit():
                if len(core) in (1, 2):
                    hour = int(core)
                    minute = 0
                elif len(core) == 3:
                    hour = int(core[0])
                    minute = int(core[1:])
                elif len(core) == 4:
                    hour = int(core[:2])
                    minute = int(core[2:])
                else:
                    raise ValueError
            else:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Require Time must be a valid time like 07:00 or 7:30am.") from exc
        if period:
            if not (1 <= hour <= 12):
                raise ValueError("When using AM/PM, hours must be between 1 and 12.")
            hour = hour % 12
            if period == "pm":
                hour += 12
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Require Time must be a valid time like 07:00 or 7:30am.")
        return f"{hour:02d}:{minute:02d}"

    def _save(self) -> None:
        title = self.title_entry.get().strip()
        if not title:
            messagebox.showerror("Invalid Data", "Title is required.", parent=self)
            return
        status = self.status_key_map.get(self.status_var.get(), "todo")
        priority = self.priority_var.get() or "Unknown"
        target_raw = self.target_entry.get().strip()
        target_date = None
        if target_raw:
            try:
                target_date = datetime.strptime(target_raw, "%Y-%m-%d").date()
            except ValueError:
                messagebox.showerror("Invalid Data", "Target date must be in YYYY-MM-DD format.", parent=self)
                return
        require_time_raw = self.require_time_entry.get().strip()
        try:
            require_time_value = self._normalize_require_time(require_time_raw) if require_time_raw else None
        except ValueError as exc:
            messagebox.showerror("Invalid Data", str(exc), parent=self)
            return
        payload = {
            "title": title,
            "description": self.description_text.get("1.0", tk.END).strip(),
            "status": status,
            "priority": priority,
            "target_date": target_date,
            "require_time": require_time_value,
            "tags": self._parse_list(self.tags_entry.get()),
            "collaborators": self._parse_list(self.collaborators_entry.get()),
        }
        task_id = self.task.id if self.task else None
        self._on_submit(payload, task_id)

    def _delete(self) -> None:
        if self.task is None:
            return
        self._on_delete(self.task.id)

    def _cancel(self) -> None:
        self._on_cancel()

    def _refresh_notes(self) -> None:
        if self.task is None:
            return
        notes = self._load_notes_fn(self.task.id)
        self._notes_map = {note.id: note for note in notes}
        for item in self.notes_tree.get_children():
            self.notes_tree.delete(item)
        for note in notes:
            timestamp = note.created_at.strftime("%Y-%m-%d %H:%M")
            preview = self._note_preview(note.content)
            self.notes_tree.insert("", "end", iid=str(note.id), values=(timestamp, preview))
        if notes and not self.notes_tree.selection():
            first_iid = str(notes[0].id)
            self.notes_tree.selection_set(first_iid)
            self.notes_tree.focus(first_iid)
        self._update_note_actions()

    def _add_note(self) -> None:
        if self.task is None:
            return
        content = self.new_note_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("Notes", "Enter note content.", parent=self)
            return
        self._on_add_note(self.task.id, content)
        self.new_note_text.delete("1.0", tk.END)
        self._refresh_notes()
        self._update_note_actions()

    def _selected_note_id(self) -> Optional[int]:
        selection = self.notes_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _update_note_actions(self) -> None:
        if self.task is None:
            self.edit_note_button.state(["disabled"])
            self.delete_note_button.state(["disabled"])
            return
        has_selection = bool(self.notes_tree.selection())
        if has_selection:
            self.edit_note_button.state(["!disabled"])
            self.delete_note_button.state(["!disabled"])
        else:
            self.edit_note_button.state(["disabled"])
            self.delete_note_button.state(["disabled"])

    def _edit_selected_note(self) -> None:
        if self.task is None:
            return
        note_id = self._selected_note_id()
        if note_id is None:
            return
        note = self._notes_map.get(note_id)
        if note is None:
            return
        updated = self._prompt_note_edit(note.content)
        if updated is None:
            return
        cleaned = updated.strip()
        if not cleaned:
            messagebox.showinfo("Notes", "Note content cannot be empty.", parent=self)
            return
        self._on_update_note(self.task.id, note_id, cleaned)
        self._refresh_notes()

    def _delete_selected_note(self) -> None:
        if self.task is None:
            return
        note_id = self._selected_note_id()
        if note_id is None:
            return
        if not messagebox.askyesno("Delete Note", "Delete this note?", parent=self):
            return
        self._on_delete_note(self.task.id, note_id)
        self._refresh_notes()

    def _note_preview(self, content: str) -> str:
        cleaned = " ".join(content.split())
        if not cleaned:
            return "(empty)"
        if len(cleaned) > 140:
            return f"{cleaned[:137]}..."
        return cleaned

    def _prompt_note_edit(self, content: str) -> Optional[str]:
        dialog = tk.Toplevel(self)
        dialog.title("Edit Note")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.configure(bg="#1d1e2c")
        dialog.resizable(False, False)
        dialog.columnconfigure(0, weight=1)

        ttk.Label(dialog, text="Note Content").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        text_widget = tk.Text(dialog, width=60, height=8, wrap="word", bg="#ffffff", fg="#1c1d2b")
        text_widget.grid(row=1, column=0, padx=12, pady=(0, 12))
        text_widget.insert("1.0", content)
        text_widget.focus_set()

        result: List[Optional[str]] = [None]

        def confirm() -> None:
            result[0] = text_widget.get("1.0", tk.END)
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        text_widget.bind("<Control-Return>", lambda _: confirm())
        dialog.bind("<Escape>", lambda _: cancel())

        button_row = ttk.Frame(dialog)
        button_row.grid(row=2, column=0, sticky="e", padx=12, pady=(0, 12))
        ttk.Button(button_row, text="Cancel", command=cancel).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(button_row, text="Save", command=confirm).pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.wait_window()
        return result[0]

    def _open_date_picker(self) -> None:
        current = None
        raw = self.target_entry.get().strip()
        if raw:
            try:
                current = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                current = None
        self._close_date_picker()
        self._date_picker = DatePickerPopup(self, current, on_select=self._set_target_date, on_close=self._close_date_picker)

    def _set_target_date(self, value: Optional[date]) -> None:
        self.target_entry.delete(0, tk.END)
        if value:
            self.target_entry.insert(0, value.isoformat())
        self._close_date_picker()

    def _close_date_picker(self) -> None:
        if self._date_picker is not None:
            self._date_picker.destroy()
            self._date_picker = None


class DatePickerPopup(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        current: Optional[date],
        *,
        on_select: Callable[[Optional[date]], None],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#000000", bd=0)
        self._on_select = on_select
        self._on_close = on_close
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()

        today = date.today()
        if current is None:
            current = today
        self.current_month = date(current.year, current.month, 1)

        frame = ttk.Frame(self, padding=12)
        frame.place(relx=0.5, rely=0.5, anchor="center")
        frame.columnconfigure(0, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Button(header, text="◀", width=3, command=lambda: self._shift_month(-1)).pack(side=tk.LEFT)
        self.month_label = ttk.Label(header, text="")
        self.month_label.pack(side=tk.LEFT, expand=True)
        ttk.Button(header, text="▶", width=3, command=lambda: self._shift_month(1)).pack(side=tk.RIGHT)

        self.calendar_frame = ttk.Frame(frame)
        self.calendar_frame.grid(row=1, column=0, pady=(8, 0))

        action_row = ttk.Frame(frame)
        action_row.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(action_row, text="Clear", command=lambda: self._finish(None)).pack(side=tk.LEFT)
        ttk.Button(action_row, text="Today", command=lambda: self._finish(today)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(action_row, text="Close", command=self._close).pack(side=tk.RIGHT)

        self._render_calendar()

    def _shift_month(self, delta: int) -> None:
        month = self.current_month.month - 1 + delta
        year = self.current_month.year + month // 12
        month = month % 12 + 1
        self.current_month = date(year, month, 1)
        self._render_calendar()

    def _render_calendar(self) -> None:
        for child in self.calendar_frame.winfo_children():
            child.destroy()
        self.month_label.configure(text=self.current_month.strftime("%B %Y"))
        headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for idx, title in enumerate(headers):
            ttk.Label(self.calendar_frame, text=title, width=4, anchor="center").grid(row=0, column=idx)
        for row_index, week in enumerate(cal.Calendar(firstweekday=0).monthdatescalendar(self.current_month.year, self.current_month.month), start=1):
            for col_index, day in enumerate(week):
                btn = ttk.Button(
                    self.calendar_frame,
                    text=str(day.day),
                    width=4,
                    command=lambda d=day: self._finish(d),
                )
                if day.month != self.current_month.month:
                    btn.state(["disabled"])
                btn.grid(row=row_index, column=col_index, padx=1, pady=1)

    def _finish(self, value: Optional[date]) -> None:
        self._on_select(value)

    def _close(self) -> None:
        self._on_close()

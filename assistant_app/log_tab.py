from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Set, Tuple

from .database import Database
from .models import LogEntry


class LogTab(ttk.Frame):
    def __init__(self, master: tk.Misc, db: Database):
        super().__init__(master, padding=(16, 16))
        self.db = db
        self.entries: List[LogEntry] = []
        self.tree_items: Dict[int, str] = {}
        self._tree_style = ttk.Style(self)
        self._tree_font: tkfont.Font | None = None
        self._single_line_height = 0
        self._extra_line_height = 0
        self._current_row_height = 0
        self._column_pixel_width = 680
        self._entries_with_wrap: Set[int] = set()
        self._pending_resize_refresh = False
        self._editor_panel: TextEditorPanel | None = None

        self._configure_styles()
        self._build_ui()
        self._initialize_tree_metrics()
        self.refresh()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure(
            "Danger.TButton",
            background="#ba1a1a",
            foreground="#ffffff",
            padding=(12, 6),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#d32f2f"), ("pressed", "#8c1c1c")],
        )

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X, pady=(0, 12))

        title = ttk.Label(header, text="Daily Update Log", style="SidebarHeading.TLabel")
        title.pack(side=tk.LEFT)

        self.clear_btn = ttk.Button(header, text="Clear", style="Danger.TButton", command=self.clear_entries)
        self.clear_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self.copy_btn = ttk.Button(header, text="Copy All", command=self.copy_to_clipboard)
        self.copy_btn.pack(side=tk.RIGHT)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.tree.heading("#0", text="Entries")
        self.tree.column("#0", width=680, minwidth=480, stretch=True)
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.tree.bind("<Double-1>", lambda e: self.edit_entry())
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Configure>", self._on_tree_resize)
        self.after_idle(lambda: self._on_tree_resize())

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, pady=(12, 0))

        self.add_entry_btn = ttk.Button(buttons, text="Add Entry", command=self.add_entry)
        self.add_entry_btn.pack(side=tk.LEFT)
        self.add_sub_entry_btn = ttk.Button(buttons, text="Add Sub-entry", command=self.add_sub_entry)
        self.add_sub_entry_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.edit_entry_btn = ttk.Button(buttons, text="Edit", command=self.edit_entry)
        self.edit_entry_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.delete_entry_btn = ttk.Button(buttons, text="Delete", command=self.delete_entry)
        self.delete_entry_btn.pack(side=tk.LEFT, padx=(6, 0))
        self._action_buttons = [
            self.copy_btn,
            self.add_entry_btn,
            self.add_sub_entry_btn,
            self.edit_entry_btn,
            self.delete_entry_btn,
            self.clear_btn,
        ]

    def _initialize_tree_metrics(self) -> None:
        if self._tree_font is not None:
            return
        self.update_idletasks()
        style = self._tree_style
        font_spec = style.lookup("Treeview", "font") or "TkDefaultFont"
        try:
            self._tree_font = tkfont.nametofont(font_spec)
        except (tk.TclError, TypeError):
            self._tree_font = tkfont.Font(root=self, font=font_spec)
        linespace = self._tree_font.metrics("linespace") or 16
        self._single_line_height = linespace + 10
        self._extra_line_height = linespace + 6
        try:
            self._column_pixel_width = int(self.tree.column("#0", option="width"))
        except Exception:
            self._column_pixel_width = 680
        self.tree.configure(style="Log.Treeview")
        style.configure("Log.Treeview", font=self._tree_font)
        self._update_row_height(1)

    def _row_height_for_lines(self, lines: int) -> int:
        return self._single_line_height + (lines - 1) * self._extra_line_height

    def _update_row_height(self, line_count: int) -> None:
        line_count = max(1, line_count)
        target = self._row_height_for_lines(line_count)
        if target == self._current_row_height:
            return
        self._tree_style.configure("Log.Treeview", rowheight=target)
        self._current_row_height = target

    def _schedule_tree_refresh(self) -> None:
        if self._pending_resize_refresh:
            return
        self._pending_resize_refresh = True
        self.after_idle(self._perform_resize_refresh)

    def _perform_resize_refresh(self) -> None:
        if not self._pending_resize_refresh:
            return
        self._pending_resize_refresh = False
        if self.entries:
            self._rebuild_tree(preserve_state=True)

    def _wrap_entry_content(self, content: str) -> List[str]:
        max_width = max(80, self._column_pixel_width - 24)
        paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
        if not paragraphs:
            return [""]
        lines: List[str] = []
        for paragraph in paragraphs:
            lines.extend(self._wrap_paragraph(paragraph, max_width))
        return lines or [""]

    def _wrap_paragraph(self, paragraph: str, max_width: int) -> List[str]:
        if not paragraph:
            return [""]
        lines: List[str] = []
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip() if current else word
            if candidate and self._tree_font.measure(candidate) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if self._tree_font.measure(word) <= max_width:
                current = word
                continue
            segments = self._split_long_word(word, max_width)
            if segments:
                lines.extend(segments[:-1])
                current = segments[-1]
        if current:
            lines.append(current)
        return lines or [""]

    def _split_long_word(self, word: str, max_width: int) -> List[str]:
        if not word:
            return [""]
        segments: List[str] = []
        current = ""
        for char in word:
            candidate = current + char
            if not current or self._tree_font.measure(candidate) <= max_width:
                current = candidate
            else:
                segments.append(current)
                current = char
        if current:
            segments.append(current)
        return segments or [""]

    def _collect_tree_state(self) -> Tuple[Set[int], Optional[int]]:
        selected_entry = self._selected_entry_id()
        open_entries: Set[int] = set()
        for entry_id, iid in self.tree_items.items():
            try:
                if self.tree.item(iid, "open"):
                    open_entries.add(entry_id)
            except tk.TclError:
                continue
        return open_entries, selected_entry

    def _restore_tree_state(self, open_entries: Set[int], selected_entry: Optional[int]) -> None:
        for entry_id in self._entries_with_wrap | open_entries:
            iid = self.tree_items.get(entry_id)
            if iid:
                try:
                    self.tree.item(iid, open=True)
                except tk.TclError:
                    pass
        if selected_entry is not None:
            iid = self.tree_items.get(selected_entry)
            if iid:
                try:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                except tk.TclError:
                    pass

    def refresh(self) -> None:
        self.entries = self.db.get_log_entries()
        self._rebuild_tree(preserve_state=False)

    def _rebuild_tree(self, preserve_state: bool) -> None:
        if self._tree_font is None:
            self._initialize_tree_metrics()
        open_entries: Set[int] = set()
        selected_entry: Optional[int] = None
        if preserve_state:
            open_entries, selected_entry = self._collect_tree_state()
        self.tree.delete(*self.tree.get_children())
        self.tree_items.clear()
        self._entries_with_wrap.clear()
        children: Dict[Optional[int], List[LogEntry]] = {}
        for entry in self.entries:
            children.setdefault(entry.parent_id, []).append(entry)
        max_lines = 1

        def insert_children(parent_id: Optional[int], tree_parent: str) -> None:
            nonlocal max_lines
            for entry in children.get(parent_id, []):
                iid = str(entry.id)
                wrapped_lines = self._wrap_entry_content(entry.content)
                bullet_lines = [f"- {wrapped_lines[0]}"] + [f"  {line}" for line in wrapped_lines[1:]]
                max_lines = max(max_lines, len(bullet_lines))
                self.tree.insert(tree_parent, tk.END, iid=iid, text=bullet_lines[0])
                self.tree_items[entry.id] = iid
                for index, continuation in enumerate(bullet_lines[1:], start=1):
                    wrap_iid = f"wrap:{entry.id}:{index}"
                    self.tree.insert(tree_parent, tk.END, iid=wrap_iid, text=continuation, tags=("wrapline",))
                if len(bullet_lines) > 1:
                    self._entries_with_wrap.add(entry.id)
                insert_children(entry.id, iid)

        insert_children(None, "")
        self._update_row_height(max_lines)
        self._restore_tree_state(open_entries, selected_entry)

    def _resolve_entry_id(self, item_id: str) -> Optional[int]:
        if item_id.isdigit():
            return int(item_id)
        if item_id.startswith("wrap:"):
            parts = item_id.split(":", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        return None

    def _selected_entry_id(self) -> Optional[int]:
        selected = self.tree.selection()
        if not selected:
            return None
        return self._resolve_entry_id(selected[0])

    def _on_tree_select(self, _: tk.Event) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            return
        canonical = self.tree_items.get(entry_id)
        if canonical and self.tree.selection() != (canonical,):
            self.tree.selection_set(canonical)
            self.tree.see(canonical)

    def _on_tree_resize(self, event: tk.Event | None = None) -> None:
        raw_width = event.width if event is not None else self.tree.winfo_width()
        if raw_width <= 1:
            return
        width = max(520, raw_width - 24)
        try:
            self.tree.column("#0", width=width)
        except Exception:
            pass
        self._column_pixel_width = width
        self._schedule_tree_refresh()

    def add_entry(self) -> None:
        def on_save(value: str) -> None:
            self.db.create_log_entry(content=value, parent_id=None)
            self.refresh()

        self._open_text_editor("New Entry", "", on_save)

    def add_sub_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            messagebox.showinfo("Select Entry", "Pick an entry to add a sub-entry.")
            return
        def on_save(value: str) -> None:
            self.db.create_log_entry(content=value, parent_id=entry_id)
            self.refresh()
            iid = self.tree_items.get(entry_id)
            if iid:
                self.tree.item(iid, open=True)

        self._open_text_editor("New Sub-entry", "", on_save)

    def edit_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            return
        entry = next((e for e in self.entries if e.id == entry_id), None)
        if not entry:
            return
        def on_save(value: str) -> None:
            self.db.update_log_entry(entry_id, value)
            self.refresh()
            iid = self.tree_items.get(entry_id)
            if iid:
                self.tree.selection_set(iid)

        self._open_text_editor("Edit Entry", entry.content, on_save)

    def delete_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            return
        entry = next((e for e in self.entries if e.id == entry_id), None)
        if not entry:
            return
        if messagebox.askyesno("Delete Entry", "Delete this entry and its sub-entries?"):
            self.db.delete_log_entry(entry_id)
            self.refresh()

    def clear_entries(self) -> None:
        if not self.entries:
            messagebox.showinfo("Clear Log", "There are no entries to clear.")
            return
        confirm = messagebox.askyesno(
            "Clear Daily Update Log",
            "This will permanently delete every entry in the daily update log. Continue?",
            parent=self,
        )
        if not confirm:
            return
        self.db.clear_log_entries()
        self.refresh()
        messagebox.showinfo("Clear Log", "All daily update log entries have been cleared.", parent=self)

    def _open_text_editor(self, title: str, initial: str, on_save: Callable[[str], None]) -> None:
        if self._editor_panel is not None:
            self._editor_panel.destroy()
        self._set_controls_enabled(False)
        self._editor_panel = TextEditorPanel(
            self,
            title=title,
            initial=initial,
            on_save=lambda value: self._close_editor(on_save, value),
            on_cancel=lambda: self._close_editor(None, None),
        )
        self._editor_panel.place(relx=0.5, rely=0.5, anchor="center")
        self._editor_panel.lift()

    def _close_editor(self, callback: Callable[[str], None] | None, value: Optional[str]) -> None:
        if self._editor_panel is not None:
            self._editor_panel.destroy()
            self._editor_panel = None
        self._set_controls_enabled(True)
        if callback and value is not None:
            callback(value)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self._action_buttons:
            widget.configure(state=state)
        self.tree.configure(selectmode="browse" if enabled else "none")

    def copy_to_clipboard(self) -> None:
        if not self.entries:
            messagebox.showinfo("Copy", "No entries to copy.")
            return
        children: Dict[Optional[int], List[LogEntry]] = {}
        for entry in self.entries:
            children.setdefault(entry.parent_id, []).append(entry)

        lines: List[str] = []

        def build_lines(parent_id: Optional[int], depth: int) -> None:
            for entry in children.get(parent_id, []):
                prefix = "  " * depth + "- "
                entry_lines = entry.content.splitlines() or [""]
                lines.append(prefix + entry_lines[0])
                for extra_line in entry_lines[1:]:
                    lines.append("  " * (depth + 1) + extra_line)
                build_lines(entry.id, depth + 1)

        build_lines(None, 0)
        payload = "\n".join(lines)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            messagebox.showinfo("Copy", "Entries copied to clipboard. Ready to paste.")
        except Exception as exc:
            messagebox.showerror("Copy Failed", str(exc))


class TextEditorPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        initial: str,
        on_save: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self._on_save = on_save
        self._on_cancel = on_cancel

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        ttk.Label(container, text=title, style="SidebarHeading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 12))

        self.text_widget = tk.Text(container, width=60, height=8, wrap="word")
        self.text_widget.grid(row=1, column=0, sticky="nsew")
        self.text_widget.insert("1.0", initial)
        self.text_widget.focus_set()

        button_row = ttk.Frame(container)
        button_row.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT)

    def _save(self) -> None:
        value = self.text_widget.get("1.0", tk.END).strip()
        if not value:
            if not messagebox.askyesno("Empty Entry", "Save empty entry?", parent=self):
                return
        self._on_save(value)

    def _cancel(self) -> None:
        self._on_cancel()

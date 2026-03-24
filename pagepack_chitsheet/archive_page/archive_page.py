from __future__ import annotations

import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

_SHARED_DIR = Path(__file__).resolve().parents[1] / "shared_bypages"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from projectpack_loader import LoadedRecord, find_projectpack_root, scan_page_index
from scroll_utils import bind_canvas_recursive, bind_y_scroll


class ArchivePage(ttk.Frame):
    PAGE_TITLE = "Archive"
    INDEX_NAME = "archive_index"
    INDEX_ALIASES = {"archive_index": ["archive_transitional"]}
    SUMMARY_KEYS = [
        "title",
        "name",
        "summary",
        "snapshot",
        "week",
        "chunk",
        "pass",
        "date",
        "archive_group",
        "source_kind",
    ]
    GROUP_KEYS = ["week", "chunk", "pass", "snapshot", "date", "archive_group", "source_kind"]
    REVIEW_FLAG_DEFS = [
        ("outdated_restrictions", "Outdated restrictions"),
        ("old_framework_assumptions", "Old framework assumptions"),
        ("migration_leftovers", "Migration leftovers"),
        ("duplicate_or_hanging_notes", "Duplicate / hanging notes"),
    ]
    META_SKIP_KEYS = {
        "record_key",
        "rel_stem",
        "source_json",
        "source_md",
        "path",
        "json_path",
        "md_path",
        "schema",
        "schema_version",
        "bucket_index",
        "uid",
        "id",
        "review_flags",
    }

    def __init__(self, parent=None, app=None, page_key=None, page_folder=None, *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)
        super().__init__(parent)
        self.app = app
        self.page_key = page_key
        self.page_folder = page_folder
        self.page_dir = Path(__file__).resolve().parent

        state_root = self._resolve_page_state_root()
        self._root_choice_file = state_root / "projectpack_root.txt"
        self._review_state_file = state_root / "archive_review_state.json"
        self._chosen_root: Path | None = self._read_saved_root_choice()
        self._review_state = self._read_review_state()

        self._scan_result = None
        self._records: list[LoadedRecord] = []
        self._record_lookup: dict[str, LoadedRecord] = {}
        self._display_lookup: dict[str, str] = {}
        self._item_display_names: list[str] = []
        self._current_record: LoadedRecord | None = None
        self._search_job = None

        self.status_var = tk.StringVar(value="Ready.")
        self.root_display_var = tk.StringVar(value="")
        self.index_display_var = tk.StringVar(value="")
        self.scan_status_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="All")

        self._listboxes: list[tk.Listbox] = []
        self._result_labels: list[tk.StringVar] = []

        self._build_styles()
        self._build_ui()
        self.after_idle(self._safe_initial_refresh)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _safe_initial_refresh(self) -> None:
        try:
            self._refresh_records()
        except Exception as exc:
            try:
                self.status_var.set(f"Startup warning: {exc}")
            except Exception:
                pass

    def build(self, parent=None):
        if parent is not None:
            self.pack(fill="both", expand=True)
        return self

    def create_widgets(self, parent=None):
        return self

    def mount(self, parent=None):
        return self

    def render(self, parent=None):
        return self

    def _resolve_page_state_root(self) -> Path:
        if self.page_folder:
            try:
                pf = Path(self.page_folder)
                if pf.is_dir():
                    return pf / "_page_state"
            except Exception:
                pass
        return self.page_dir / "_page_state"

    def _read_saved_root_choice(self) -> Path | None:
        if self._root_choice_file.exists():
            try:
                text = self._root_choice_file.read_text(encoding="utf-8").strip()
                if text:
                    path = Path(text).expanduser()
                    if path.exists():
                        return path
            except Exception:
                return None
        return None

    def _save_root_choice(self, root_path: Path | None) -> None:
        if root_path is None:
            return
        self._root_choice_file.parent.mkdir(parents=True, exist_ok=True)
        self._root_choice_file.write_text(str(root_path), encoding="utf-8")

    def _read_review_state(self) -> dict:
        if self._review_state_file.exists():
            try:
                data = json.loads(self._review_state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                return {}
        return {}

    def _write_review_state(self) -> None:
        self._review_state_file.parent.mkdir(parents=True, exist_ok=True)
        self._review_state_file.write_text(json.dumps(self._review_state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _choose_projectpack_root(self) -> None:
        initial = self._chosen_root or Path.home()
        chosen = filedialog.askdirectory(title="Choose project pack root", initialdir=str(initial))
        if chosen:
            self._chosen_root = Path(chosen)
            self._save_root_choice(self._chosen_root)
            self.after_idle(self._safe_initial_refresh)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_styles(self) -> None:
        s = ttk.Style()
        try:
            s.configure("ArchiveSection.TLabelframe", padding=8)
            s.configure("ArchiveSection.TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
            s.configure("ArchiveKey.TLabel", font=("TkDefaultFont", 9, "bold"))
            s.configure("ArchiveHint.TLabel", foreground="#555555")
            s.configure("ArchiveHeader.TLabel", font=("TkDefaultFont", 10, "bold"))
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(outer, text="Archive", style="ArchiveSection.TLabelframe")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=0)
        top.columnconfigure(5, weight=1)

        ttk.Label(top, text="Search:", style="ArchiveKey.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        search = ttk.Entry(top, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky="ew")
        search.bind("<KeyRelease>", self._on_search_changed)

        ttk.Label(top, text="Filter:", style="ArchiveKey.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 6))
        filter_box = ttk.Combobox(top, textvariable=self.filter_var, state="readonly", values=["All", "Paired", "Markdown Only", "JSON Only"], width=14)
        filter_box.grid(row=0, column=3, sticky="w")
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._render_all_tabs())

        ttk.Button(top, text="Choose Root", command=self._choose_projectpack_root).grid(row=0, column=4, sticky="e", padx=(10, 0))
        ttk.Label(top, textvariable=self.root_display_var, anchor="w", style="ArchiveHint.TLabel").grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        ttk.Label(top, textvariable=self.index_display_var, anchor="w", style="ArchiveHint.TLabel").grid(row=2, column=0, columnspan=6, sticky="ew", pady=(2, 0))
        ttk.Label(top, textvariable=self.scan_status_var, anchor="w", style="ArchiveHint.TLabel").grid(row=3, column=0, columnspan=6, sticky="ew", pady=(2, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self._tab_user = ttk.Frame(self.notebook, padding=6)
        self._tab_pro = ttk.Frame(self.notebook, padding=6)
        self._tab_machine = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self._tab_user, text="User Friendly")
        self.notebook.add(self._tab_pro, text="Pro View")
        self.notebook.add(self._tab_machine, text="Machine View")

        self._user_widgets = self._build_archive_tab(self._tab_user, role="user")
        self._pro_widgets = self._build_archive_tab(self._tab_pro, role="pro")
        self._machine_widgets = self._build_machine_tab(self._tab_machine)

        self._listboxes = [self._user_widgets["listbox"], self._pro_widgets["listbox"], self._machine_widgets["listbox"]]
        self._result_labels = [self._user_widgets["results_var"], self._pro_widgets["results_var"], self._machine_widgets["results_var"]]

        status_row = ttk.Frame(outer)
        status_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="w")

    def _build_archive_tab(self, tab: ttk.Frame, role: str) -> dict:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        body = ttk.Frame(tab)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=190)
        body.columnconfigure(1, weight=3, minsize=360)
        body.columnconfigure(2, weight=2, minsize=250)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Archive Items", style="ArchiveSection.TLabelframe")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        results_var = tk.StringVar(value="0 items")
        ttk.Label(left, textvariable=results_var, style="ArchiveHint.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        listbox = tk.Listbox(left, exportselection=False)
        listbox.grid(row=1, column=0, sticky="nsew")
        listbox.bind("<<ListboxSelect>>", self._on_item_selected)
        bind_y_scroll(listbox, listbox)

        center_title = "Archive Summary" if role == "user" else "Archive Review"
        center = ttk.LabelFrame(body, text=center_title, style="ArchiveSection.TLabelframe")
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)

        center_canvas = tk.Canvas(center, highlightthickness=0)
        center_scroll = ttk.Scrollbar(center, orient="vertical", command=center_canvas.yview)
        center_canvas.configure(yscrollcommand=center_scroll.set)
        center_canvas.grid(row=0, column=0, sticky="nsew")
        center_scroll.grid(row=0, column=1, sticky="ns")

        center_inner = ttk.Frame(center_canvas)
        center_window = center_canvas.create_window((0, 0), window=center_inner, anchor="nw")
        center_canvas._shared_window_id = center_window
        bind_canvas_recursive(center_canvas, center_inner)

        right_title = "Quick Details" if role == "user" else "Review / Cleanup"
        right = ttk.LabelFrame(body, text=right_title, style="ArchiveSection.TLabelframe")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        right_canvas = tk.Canvas(right, highlightthickness=0)
        right_scroll = ttk.Scrollbar(right, orient="vertical", command=right_canvas.yview)
        right_canvas.configure(yscrollcommand=right_scroll.set)
        right_canvas.grid(row=0, column=0, sticky="nsew")
        right_scroll.grid(row=0, column=1, sticky="ns")

        right_inner = ttk.Frame(right_canvas)
        right_window = right_canvas.create_window((0, 0), window=right_inner, anchor="nw")
        right_canvas._shared_window_id = right_window
        bind_canvas_recursive(right_canvas, right_inner)

        if role == "pro":
            vars_by_key = {key: tk.BooleanVar(value=False) for key, _ in self.REVIEW_FLAG_DEFS}
            for row, (key, label) in enumerate(self.REVIEW_FLAG_DEFS):
                ttk.Checkbutton(right_inner, text=label, variable=vars_by_key[key]).grid(row=row, column=0, sticky="w", pady=(0, 4))
            ttk.Label(right_inner, text="Review note:", style="ArchiveKey.TLabel").grid(row=len(self.REVIEW_FLAG_DEFS), column=0, sticky="w", pady=(8, 4))
            note_text = tk.Text(right_inner, height=10, wrap="word")
            note_text.grid(row=len(self.REVIEW_FLAG_DEFS) + 1, column=0, sticky="nsew")
            ttk.Button(right_inner, text="Save Review Notes", command=self._save_current_review_state).grid(row=len(self.REVIEW_FLAG_DEFS) + 2, column=0, sticky="w", pady=(8, 0))
            bind_y_scroll(note_text, note_text)
        else:
            vars_by_key = {}
            note_text = None

        note_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=note_var, style="ArchiveHint.TLabel", anchor="w").grid(row=1, column=0, sticky="ew", pady=(6, 0))

        return {
            "listbox": listbox,
            "results_var": results_var,
            "center_inner": center_inner,
            "right_inner": right_inner,
            "note_var": note_var,
            "review_vars": vars_by_key,
            "review_text": note_text,
            "center_canvas": center_canvas,
            "right_canvas": right_canvas,
        }

    def _build_machine_tab(self, tab: ttk.Frame) -> dict:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        body = ttk.Frame(tab)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=190)
        body.columnconfigure(1, weight=3, minsize=360)
        body.columnconfigure(2, weight=2, minsize=250)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Archive Items", style="ArchiveSection.TLabelframe")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        results_var = tk.StringVar(value="0 items")
        ttk.Label(left, textvariable=results_var, style="ArchiveHint.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        listbox = tk.Listbox(left, exportselection=False)
        listbox.grid(row=1, column=0, sticky="nsew")
        listbox.bind("<<ListboxSelect>>", self._on_item_selected)
        bind_y_scroll(listbox, listbox)

        center = ttk.LabelFrame(body, text="Machine View", style="ArchiveSection.TLabelframe")
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)
        machine_text = tk.Text(center, wrap="word", state="disabled")
        machine_text.grid(row=0, column=0, sticky="nsew")
        machine_scroll = ttk.Scrollbar(center, orient="vertical", command=machine_text.yview)
        machine_text.configure(yscrollcommand=machine_scroll.set)
        machine_scroll.grid(row=0, column=1, sticky="ns")
        bind_y_scroll(machine_text, machine_text)

        right = ttk.LabelFrame(body, text="Quick Details", style="ArchiveSection.TLabelframe")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right_text = tk.Text(right, wrap="word", state="disabled")
        right_text.grid(row=0, column=0, sticky="nsew")
        right_scroll = ttk.Scrollbar(right, orient="vertical", command=right_text.yview)
        right_text.configure(yscrollcommand=right_scroll.set)
        right_scroll.grid(row=0, column=1, sticky="ns")
        bind_y_scroll(right_text, right_text)

        note_var = tk.StringVar(value="Read-only machine/reference view.")
        ttk.Label(tab, textvariable=note_var, style="ArchiveHint.TLabel", anchor="w").grid(row=1, column=0, sticky="ew", pady=(6, 0))

        return {
            "listbox": listbox,
            "results_var": results_var,
            "machine_text": machine_text,
            "right_text": right_text,
            "note_var": note_var,
        }

    # ------------------------------------------------------------------
    # Refresh / selection
    # ------------------------------------------------------------------

    def _refresh_records(self) -> None:
        root, shape, warnings = find_projectpack_root(__file__, self.page_folder, self._chosen_root)
        self._scan_result = scan_page_index(root, self.INDEX_NAME, alias_map=self.INDEX_ALIASES)
        self._records = list(self._scan_result.records)
        self._record_lookup = {r.record_key: r for r in self._records}
        self._display_lookup = {}
        self._item_display_names = []
        for record in self._records:
            name = record.display_name
            if name in self._display_lookup:
                name = f"{name} [{record.rel_stem}]"
            self._display_lookup[name] = record.record_key
            self._item_display_names.append(name)

        root_text = str(self._scan_result.root_path) if self._scan_result.root_path else "(missing)"
        self.root_display_var.set(f"Root: {root_text}  |  shape: {self._scan_result.root_shape}")
        active = self._scan_result.active_index_name or "(missing)"
        self.index_display_var.set(f"Index: {self.INDEX_NAME}  |  active: {active}")
        alias_mode = self._scan_result.active_index_name not in (None, self.INDEX_NAME)
        pair_count = sum(1 for r in self._records if r.json_path and r.md_path)
        self.scan_status_var.set(
            f"records: {len(self._records)}  |  pairs: {pair_count}  |  alias mode: {'on' if alias_mode else 'off'}"
        )
        status_bits = warnings + list(self._scan_result.warnings)
        self.status_var.set(" | ".join(status_bits) if status_bits else "Archive loaded.")
        self._render_all_tabs(select_first=True)

    def _render_all_tabs(self, select_first: bool = False) -> None:
        names = self._filtered_display_names()
        for listbox, label_var in zip(self._listboxes, self._result_labels):
            current_name = None
            sel = listbox.curselection()
            if sel:
                try:
                    current_name = listbox.get(sel[0])
                except Exception:
                    current_name = None
            listbox.delete(0, tk.END)
            for name in names:
                listbox.insert(tk.END, name)
            label_var.set(f"{len(names)} items")
            if names:
                target_name = current_name if current_name in names else (names[0] if select_first else current_name)
                if target_name in names:
                    idx = names.index(target_name)
                    listbox.selection_clear(0, tk.END)
                    listbox.selection_set(idx)
                    listbox.activate(idx)
        selected = self._selected_display_name()
        if not selected and names:
            selected = names[0]
        self._set_current_record_from_display_name(selected)

    def _selected_display_name(self) -> str | None:
        for listbox in self._listboxes:
            sel = listbox.curselection()
            if sel:
                try:
                    return listbox.get(sel[0])
                except Exception:
                    continue
        return None

    def _set_current_record_from_display_name(self, display_name: str | None) -> None:
        if not display_name:
            self._current_record = None
            self._render_user_view(None)
            self._render_pro_view(None)
            self._render_machine_view(None)
            return
        record_key = self._display_lookup.get(display_name)
        record = self._record_lookup.get(record_key) if record_key else None
        self._current_record = record
        self._render_user_view(record)
        self._render_pro_view(record)
        self._render_machine_view(record)

    def _on_item_selected(self, _event=None) -> None:
        display_name = self._selected_display_name()
        self._sync_listboxes(display_name)
        self._set_current_record_from_display_name(display_name)

    def _sync_listboxes(self, display_name: str | None) -> None:
        if not display_name:
            return
        for listbox in self._listboxes:
            values = listbox.get(0, tk.END)
            if display_name in values:
                idx = values.index(display_name)
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(idx)
                listbox.activate(idx)
                listbox.see(idx)

    def _on_search_changed(self, _event=None) -> None:
        if self._search_job:
            try:
                self.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.after(120, self._render_all_tabs)

    def _filtered_display_names(self) -> list[str]:
        search = self.search_var.get().strip().lower()
        mode = self.filter_var.get().strip().lower()
        names: list[str] = []
        for display_name in self._item_display_names:
            record_key = self._display_lookup.get(display_name)
            record = self._record_lookup.get(record_key) if record_key else None
            if not record:
                continue
            if mode == "paired" and record.source_state != "paired":
                continue
            if mode == "markdown only" and record.source_state != "md_only":
                continue
            if mode == "json only" and record.source_state != "json_only":
                continue
            if search and not self._record_matches_search(record, search, display_name):
                continue
            names.append(display_name)
        return names

    def _record_matches_search(self, record: LoadedRecord, search: str, display_name: str) -> bool:
        haystacks = [display_name.lower(), record.record_key.lower(), record.rel_stem.lower(), record.md_raw.lower()]
        try:
            haystacks.append(json.dumps(record.json_data, ensure_ascii=False).lower())
        except Exception:
            pass
        return any(search in h for h in haystacks)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _clear_children(self, parent) -> None:
        for child in list(parent.winfo_children()):
            child.destroy()

    def _render_user_view(self, record: LoadedRecord | None) -> None:
        widgets = self._user_widgets
        self._clear_children(widgets["center_inner"])
        self._clear_children(widgets["right_inner"])
        widgets["note_var"].set("")
        if not record:
            ttk.Label(widgets["center_inner"], text="No archive record selected.").grid(row=0, column=0, sticky="w")
            return

        row = 0
        ttk.Label(widgets["center_inner"], text=record.display_name, style="ArchiveHeader.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        for key, value in self._archive_summary_pairs(record):
            self._add_key_value(widgets["center_inner"], row, key, value)
            row += 1

        user_body = self._user_friendly_body(record)
        if user_body:
            ttk.Separator(widgets["center_inner"], orient="horizontal").grid(row=row, column=0, sticky="ew", pady=6)
            row += 1
            text = tk.Text(widgets["center_inner"], height=18, wrap="word")
            text.insert("1.0", user_body)
            text.configure(state="disabled")
            text.grid(row=row, column=0, sticky="nsew")
            bind_y_scroll(text, text)
            row += 1

        self._render_right_summary(record, widgets["right_inner"])
        widgets["note_var"].set("Summary first archive reader. Detail stays secondary.")

    def _render_pro_view(self, record: LoadedRecord | None) -> None:
        widgets = self._pro_widgets
        self._clear_children(widgets["center_inner"])
        if not record:
            self._clear_children(widgets["right_inner"])
            ttk.Label(widgets["center_inner"], text="No archive record selected.").grid(row=0, column=0, sticky="w")
            return

        clean_fields, dirty_lines = self._split_md_clean_dirty(record.md_raw)
        row = 0
        ttk.Label(widgets["center_inner"], text=record.display_name, style="ArchiveHeader.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        ttk.Label(widgets["center_inner"], text=f"State: {record.source_state}  |  Group: {self._group_label(record)}", style="ArchiveHint.TLabel").grid(row=row, column=0, sticky="w", pady=(0, 6))
        row += 1

        if clean_fields:
            section = ttk.LabelFrame(widgets["center_inner"], text="Structured / Keepable", style="ArchiveSection.TLabelframe")
            section.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            section.columnconfigure(1, weight=1)
            for idx, (key, value) in enumerate(clean_fields):
                ttk.Label(section, text=f"{key}:", style="ArchiveKey.TLabel").grid(row=idx, column=0, sticky="nw", padx=(0, 6), pady=1)
                ttk.Label(section, text=value, wraplength=520, justify="left").grid(row=idx, column=1, sticky="w", pady=1)
            row += 1

        if dirty_lines:
            dirty = ttk.LabelFrame(widgets["center_inner"], text="Historical / Dirty / Support", style="ArchiveSection.TLabelframe")
            dirty.grid(row=row, column=0, sticky="nsew", pady=(0, 6))
            dirty.columnconfigure(0, weight=1)
            dirty.rowconfigure(0, weight=1)
            text = tk.Text(dirty, height=18, wrap="word")
            text.insert("1.0", "\n".join(dirty_lines).strip())
            text.configure(state="disabled")
            text.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(dirty, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.grid(row=0, column=1, sticky="ns")
            bind_y_scroll(text, text)
            row += 1

        self._render_review_panel(record)
        widgets["note_var"].set("Local review/cleanup marks only. No destructive source edits.")

    def _render_machine_view(self, record: LoadedRecord | None) -> None:
        widgets = self._machine_widgets
        machine_text = widgets["machine_text"]
        right_text = widgets["right_text"]
        for target in (machine_text, right_text):
            target.configure(state="normal")
            target.delete("1.0", tk.END)

        if not record:
            machine_text.insert("1.0", "No archive record selected.")
            right_text.insert("1.0", "")
        else:
            machine_text.insert("1.0", self._machine_view_text(record))
            right_text.insert("1.0", self._quick_detail_text(record))
        for target in (machine_text, right_text):
            target.configure(state="disabled")
        widgets["note_var"].set("Read-only machine/reference view.")

    def _add_key_value(self, parent, row: int, key: str, value: str) -> None:
        wrap = 520 if parent is self._user_widgets["center_inner"] else 360
        ttk.Label(parent, text=f"{key}:", style="ArchiveKey.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 6), pady=1)
        ttk.Label(parent, text=value, wraplength=wrap, justify="left").grid(row=row, column=1, sticky="w", pady=1)

    def _render_right_summary(self, record: LoadedRecord, parent) -> None:
        row = 0
        for key, label in self.REVIEW_FLAG_DEFS:
            value = self._review_state.get(record.record_key, {}).get(key, False)
            ttk.Label(parent, text=f"{label}: {'yes' if value else 'no'}", wraplength=280, justify="left").grid(row=row, column=0, sticky="w", pady=2)
            row += 1
        note = self._review_state.get(record.record_key, {}).get("review_note", "").strip()
        if note:
            ttk.Separator(parent, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=6)
            row += 1
            ttk.Label(parent, text="Review note:", style="ArchiveKey.TLabel").grid(row=row, column=0, sticky="w")
            row += 1
            ttk.Label(parent, text=note, wraplength=280, justify="left").grid(row=row, column=0, sticky="w")
            row += 1
        ttk.Separator(parent, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=6)
        row += 1
        ttk.Label(parent, text=self._group_label(record), wraplength=280, justify="left", style="ArchiveHint.TLabel").grid(row=row, column=0, sticky="w")

    def _render_review_panel(self, record: LoadedRecord) -> None:
        widgets = self._pro_widgets
        state = self._review_state.get(record.record_key, {})
        for key, _label in self.REVIEW_FLAG_DEFS:
            if key in widgets["review_vars"]:
                widgets["review_vars"][key].set(bool(state.get(key, False)))
        text_widget = widgets["review_text"]
        if text_widget is not None:
            text_widget.delete("1.0", tk.END)
            text_widget.insert("1.0", state.get("review_note", ""))

    def _archive_summary_pairs(self, record: LoadedRecord) -> list[tuple[str, str]]:
        fields = self._combined_fields(record)
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key in self.SUMMARY_KEYS + self.GROUP_KEYS:
            if key in seen:
                continue
            value = fields.get(key)
            if value:
                pairs.append((self._labelize(key), self._stringify_value(value)))
                seen.add(key)
        if not pairs:
            pairs.append(("Record", record.rel_stem))
        return pairs

    def _user_friendly_body(self, record: LoadedRecord) -> str:
        if record.md_body.strip():
            return record.md_body.strip()
        clean_fields, dirty_lines = self._split_md_clean_dirty(record.md_raw)
        if clean_fields:
            return "\n".join(f"{k}: {v}" for k, v in clean_fields)
        if dirty_lines:
            return "\n".join(dirty_lines).strip()
        return ""

    def _group_label(self, record: LoadedRecord) -> str:
        fields = self._combined_fields(record)
        bits: list[str] = []
        for key in self.GROUP_KEYS:
            value = fields.get(key)
            if value:
                bits.append(f"{self._labelize(key)}: {self._stringify_value(value)}")
        if not bits:
            path = record.rel_stem.replace("\\", "/")
            if "/" in path:
                return f"Path group: {path.rsplit('/', 1)[0]}"
            return "Path group: root"
        return " | ".join(bits)

    def _split_md_clean_dirty(self, text: str) -> tuple[list[tuple[str, str]], list[str]]:
        clean: list[tuple[str, str]] = []
        dirty: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<<") and stripped.endswith(">>"):
                clean.append(("marker", stripped))
                continue
            if ":" in stripped and not stripped.startswith("#"):
                key, value = stripped.split(":", 1)
                key = key.strip()
                value = value.strip()
                if key and value:
                    clean.append((key, value))
                    continue
            dirty.append(line)
        return clean, dirty

    def _machine_view_text(self, record: LoadedRecord) -> str:
        data = record.json_data or {}
        if not isinstance(data, dict) or not data:
            return "No JSON machine record available."
        lines = [f"Record: {record.display_name}", ""]
        for key in sorted(data.keys()):
            lines.append(f"[{self._labelize(key)}]")
            lines.append(self._stringify_value(data.get(key)))
            lines.append("")
        return "\n".join(lines).strip()

    def _quick_detail_text(self, record: LoadedRecord) -> str:
        lines = [f"State: {record.source_state}", f"Record key: {record.record_key}", f"Group: {self._group_label(record)}"]
        state = self._review_state.get(record.record_key, {})
        active = [label for key, label in self.REVIEW_FLAG_DEFS if state.get(key)]
        if active:
            lines.append("")
            lines.append("Review flags:")
            lines.extend(f"- {item}" for item in active)
        note = state.get("review_note", "").strip()
        if note:
            lines.append("")
            lines.append("Review note:")
            lines.append(note)
        return "\n".join(lines)

    def _combined_fields(self, record: LoadedRecord) -> dict:
        fields = {}
        if isinstance(record.json_data, dict):
            fields.update(record.json_data)
        if isinstance(record.md_meta, dict):
            fields.update({k: v for k, v in record.md_meta.items() if k not in fields})
        return fields

    def _stringify_value(self, value) -> str:
        if isinstance(value, list):
            return ", ".join(self._stringify_value(v) for v in value)
        if isinstance(value, dict):
            try:
                return json.dumps(value, indent=2, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    def _labelize(self, text: str) -> str:
        return str(text).replace("_", " ").strip().title()

    # ------------------------------------------------------------------
    # Review state
    # ------------------------------------------------------------------

    def _save_current_review_state(self) -> None:
        record = self._current_record
        if not record:
            self.status_var.set("No archive record selected.")
            return
        widgets = self._pro_widgets
        state = self._review_state.setdefault(record.record_key, {})
        for key, _label in self.REVIEW_FLAG_DEFS:
            state[key] = bool(widgets["review_vars"][key].get())
        note_widget = widgets["review_text"]
        state["review_note"] = note_widget.get("1.0", tk.END).strip() if note_widget is not None else ""
        self._write_review_state()
        self._render_user_view(record)
        self._render_machine_view(record)
        self.status_var.set(f"Saved local review state for {record.display_name}.")


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Archive Page")
    page = ArchivePage(root)
    page.pack(fill="both", expand=True)
    root.geometry("1360x840")
    root.mainloop()

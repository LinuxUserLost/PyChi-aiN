from __future__ import annotations

import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

_SHARED_DIR = Path(__file__).resolve().parents[1] / "shared_bypages"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from easy_visibility import EasyVisibilityStore
from projectpack_loader import LoadedRecord, find_projectpack_root, parse_md_record, scan_page_index
from scroll_utils import bind_canvas_recursive, bind_y_scroll


_UF_SECTION_START = "## User Friendly Fields"
_UF_SECTION_END = "## /User Friendly Fields"


class GlossaryPage(ttk.Frame):
    PAGE_TITLE = "Glossary"
    INDEX_NAME = "glossary_index"
    INDEX_ALIASES = {"glossary_index": ["glossary_bridge"]}
    DEFAULT_USER_FRIENDLY_KEYS = ["title", "term", "name", "topic", "rule_name", "summary", "definition", "notes"]

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
        self._visibility_store = EasyVisibilityStore(state_root / "easy_visibility.json", self.DEFAULT_USER_FRIENDLY_KEYS)

        self._chosen_root: Path | None = self._read_saved_root_choice()
        self._scan_result = None
        self._records: list[LoadedRecord] = []
        self._record_lookup: dict[str, LoadedRecord] = {}
        self._display_lookup: dict[str, str] = {}
        self._current_record: LoadedRecord | None = None
        self._current_pro_items: list[dict] = []
        self._current_dirty_blocks: list[str] = []
        self._current_uf_map: dict[str, str] = {}
        self._current_machine_lines: list[str] = []
        self._current_machine_sections: list[tuple[str, str]] = []

        self.term_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")
        self.root_display_var = tk.StringVar(value="")
        self.index_display_var = tk.StringVar(value="")
        self.machine_filter_var = tk.StringVar(value="")

        self._uf_vars: dict[str, tk.StringVar] = {}
        self._uf_edit_widgets: dict[str, tk.Widget] = {}
        self._pro_item_vars: list[tuple[dict, tk.BooleanVar]] = []

        self._build_styles()
        self._build_ui()
        self.after_idle(self._safe_initial_refresh)

    # ------------------------------------------------------------------
    # Root / state
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
            s.configure("GlossarySection.TLabelframe", padding=8)
            s.configure("GlossarySection.TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
            s.configure("GlossaryKey.TLabel", font=("TkDefaultFont", 9, "bold"))
            s.configure("GlossaryHint.TLabel", foreground="#555555")
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        self._build_root_row(outer)
        self._build_index_row(outer)
        self._build_selector_row(outer)

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=3, column=0, sticky="nsew", pady=(8, 0))

        self._tab_user = ttk.Frame(self.notebook, padding=6)
        self._tab_pro = ttk.Frame(self.notebook, padding=6)
        self._tab_machine = ttk.Frame(self.notebook, padding=6)

        self.notebook.add(self._tab_user, text="User Friendly")
        self.notebook.add(self._tab_pro, text="Pro View")
        self.notebook.add(self._tab_machine, text="Machine View")

        self._build_user_tab()
        self._build_pro_tab()
        self._build_machine_tab()

        status_row = ttk.Frame(outer)
        status_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="w")

    def _build_root_row(self, parent) -> None:
        row = ttk.LabelFrame(parent, text="Project Pack Root", style="GlossarySection.TLabelframe")
        row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, textvariable=self.root_display_var, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Button(row, text="Choose Root", command=self._choose_projectpack_root).grid(row=0, column=1, sticky="e")

    def _build_index_row(self, parent) -> None:
        row = ttk.LabelFrame(parent, text="Resolved Page Index", style="GlossarySection.TLabelframe")
        row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, textvariable=self.index_display_var, anchor="w", justify="left").grid(row=0, column=0, sticky="ew")

    def _build_selector_row(self, parent) -> None:
        row = ttk.LabelFrame(parent, text="Record", style="GlossarySection.TLabelframe")
        row.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text="Item:", style="GlossaryKey.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.term_combo = ttk.Combobox(row, textvariable=self.term_var, state="readonly", height=22)
        self.term_combo.grid(row=0, column=1, sticky="ew")
        self.term_combo.bind("<<ComboboxSelected>>", self._on_term_selected)

    def _build_user_tab(self) -> None:
        tab = self._tab_user
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        action = ttk.Frame(tab)
        action.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(action, text="Save User Friendly", command=self._save_user_friendly).pack(side="right")
        ttk.Button(action, text="Return to Default", command=self._reset_user_friendly_to_default).pack(side="right", padx=(0, 6))
        ttk.Label(action, text="Editable .md working layer.", style="GlossaryHint.TLabel").pack(side="left")

        canvas = tk.Canvas(tab, borderwidth=0, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        vsb.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vsb.set)

        self._uf_inner = ttk.Frame(canvas)
        self._uf_inner.columnconfigure(0, weight=1)
        self._uf_window_id = canvas.create_window((0, 0), window=self._uf_inner, anchor="nw")
        canvas._shared_window_id = self._uf_window_id
        bind_canvas_recursive(canvas, self._uf_inner)
        self._uf_canvas = canvas

    def _build_pro_tab(self) -> None:
        tab = self._tab_pro
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        action = ttk.Frame(tab)
        action.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(action, text="Update User Friendly Fields", command=self._update_user_friendly_from_pro).pack(side="right")
        ttk.Button(action, text="Reset User Friendly to Default", command=self._reset_user_friendly_to_default).pack(side="right", padx=(0, 6))
        ttk.Label(action, text=".md-only structured working surface.", style="GlossaryHint.TLabel").pack(side="left")

        canvas = tk.Canvas(tab, borderwidth=0, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        vsb.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vsb.set)

        self._pro_inner = ttk.Frame(canvas)
        self._pro_inner.columnconfigure(0, weight=1)
        self._pro_window_id = canvas.create_window((0, 0), window=self._pro_inner, anchor="nw")
        canvas._shared_window_id = self._pro_window_id
        bind_canvas_recursive(canvas, self._pro_inner)
        self._pro_canvas = canvas

    def _build_machine_tab(self) -> None:
        tab = self._tab_machine
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Filter:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        entry = ttk.Entry(top, textvariable=self.machine_filter_var)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<KeyRelease>", lambda _e: self._render_machine_view())
        ttk.Label(top, text=".json-only reference layer.", style="GlossaryHint.TLabel").grid(row=0, column=2, sticky="e", padx=(10, 0))

        frame = ttk.LabelFrame(tab, text="Machine View", style="GlossarySection.TLabelframe")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self._machine_text = tk.Text(frame, wrap="word", relief="flat", padx=8, pady=8, state="disabled")
        self._machine_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self._machine_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._machine_text.configure(yscrollcommand=sb.set)
        bind_y_scroll(self._machine_text)

    # ------------------------------------------------------------------
    # Loading / selection
    # ------------------------------------------------------------------

    def _refresh_records(self) -> None:
        root_path, root_shape, warnings = find_projectpack_root(
            page_file=__file__,
            page_folder=self.page_folder,
            chosen_root=self._chosen_root,
        )
        if root_path is not None:
            self._chosen_root = root_path
            self._save_root_choice(root_path)

        self._scan_result = scan_page_index(
            root_path=root_path,
            index_name=self.INDEX_NAME,
            alias_map=self.INDEX_ALIASES,
        )
        all_warnings = list(warnings) + list(self._scan_result.warnings)
        self._records = list(self._scan_result.records)
        self._record_lookup = {rec.record_key: rec for rec in self._records}
        self._refresh_root_labels(root_path, root_shape)
        self._reload_selector()
        if not self._records:
            self._clear_views()
            self.status_var.set(all_warnings[0] if all_warnings else "No records found.")
        else:
            self.status_var.set(all_warnings[0] if all_warnings else f"Loaded {len(self._records)} record(s).")

    def _refresh_root_labels(self, root_path: Path | None, root_shape: str | None) -> None:
        if root_path is None:
            self.root_display_var.set("Not found")
        else:
            self.root_display_var.set(f"{root_path} ({root_shape or 'unknown'})")
        if self._scan_result and self._scan_result.active_index_name:
            note = ""
            if self._scan_result.active_index_name != self.INDEX_NAME:
                note = f"  [alias fallback: {self._scan_result.active_index_name}]"
            self.index_display_var.set(f"{self.INDEX_NAME}{note}")
        else:
            self.index_display_var.set(self.INDEX_NAME)

    def _reload_selector(self) -> None:
        self._display_lookup = {}
        display_values: list[str] = []
        name_counts: dict[str, int] = {}
        for rec in self._records:
            name = rec.display_name or Path(rec.rel_stem).name
            name_counts[name] = name_counts.get(name, 0) + 1
        for rec in self._records:
            name = rec.display_name or Path(rec.rel_stem).name
            if name_counts.get(name, 0) > 1:
                label = f"{name} [{rec.rel_stem}]"
            else:
                label = name
            display_values.append(label)
            self._display_lookup[label] = rec.record_key
        self.term_combo["values"] = display_values
        if display_values:
            self.term_var.set(display_values[0])
            self._select_record(self._record_lookup[self._display_lookup[display_values[0]]])
        else:
            self.term_var.set("")

    def _on_term_selected(self, _event=None) -> None:
        label = self.term_var.get().strip()
        key = self._display_lookup.get(label)
        rec = self._record_lookup.get(key or "")
        if rec:
            self._select_record(rec)

    def _select_record(self, rec: LoadedRecord) -> None:
        self._current_record = rec
        self._current_uf_map = self._load_user_friendly_map(rec)
        self._current_pro_items, self._current_dirty_blocks = self._build_pro_view_model(rec)
        self._current_machine_sections = self._build_machine_sections(rec)
        self._current_machine_lines = self._build_machine_promote_lines(rec)
        self._render_user_friendly(rec)
        self._render_pro_view(rec)
        self._render_machine_view()
        self.status_var.set(f"Loaded: {rec.display_name} ({rec.source_state})")

    # ------------------------------------------------------------------
    # User Friendly
    # ------------------------------------------------------------------

    def _render_user_friendly(self, rec: LoadedRecord) -> None:
        self._clear_container(self._uf_inner)
        self._uf_vars = {}
        self._uf_edit_widgets = {}

        info = ttk.LabelFrame(self._uf_inner, text="Record", style="GlossarySection.TLabelframe")
        info.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        info.columnconfigure(1, weight=1)
        ttk.Label(info, text="Title:", style="GlossaryKey.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 6))
        ttk.Label(info, text=rec.display_name, wraplength=800, justify="left").grid(row=0, column=1, sticky="w")
        ttk.Label(info, text="Source:", style="GlossaryKey.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, 6))
        ttk.Label(info, text=rec.rel_stem, wraplength=800, justify="left").grid(row=1, column=1, sticky="w")

        if not rec.md_path:
            warn = ttk.LabelFrame(self._uf_inner, text="User Friendly", style="GlossarySection.TLabelframe")
            warn.grid(row=1, column=0, sticky="ew")
            ttk.Label(warn, text="No .md file exists for this record yet. Use Pro View or Machine View actions first.", wraplength=900, justify="left").grid(row=0, column=0, sticky="w")
            return

        uf_map = self._current_uf_map or self._default_user_friendly_map(rec)
        ordered_keys = list(uf_map.keys())
        if not ordered_keys:
            ordered_keys = list(self._default_user_friendly_map(rec).keys())
            uf_map = self._default_user_friendly_map(rec)

        for idx, key in enumerate(ordered_keys, start=1):
            block = ttk.LabelFrame(self._uf_inner, text=self._labelize_key(key), style="GlossarySection.TLabelframe")
            block.grid(row=idx, column=0, sticky="ew", pady=(0, 6))
            block.columnconfigure(1, weight=1)

            var = tk.BooleanVar(value=True)
            self._uf_vars[key] = var
            ttk.Checkbutton(block, text="Show", variable=var).grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Button(block, text="Hide", command=lambda k=key: self._hide_user_friendly_field(k)).grid(row=0, column=2, sticky="e")

            value = uf_map.get(key, "")
            if self._is_multiline_value(value):
                text = tk.Text(block, height=max(4, min(12, value.count("\n") + 3)), wrap="word", undo=True, padx=6, pady=6)
                text.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
                text.insert("1.0", value)
                block.rowconfigure(1, weight=1)
                bind_y_scroll(text)
                self._uf_edit_widgets[key] = text
            else:
                entry = ttk.Entry(block)
                entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
                entry.insert(0, value)
                self._uf_edit_widgets[key] = entry

    def _hide_user_friendly_field(self, key: str) -> None:
        widget = self._uf_edit_widgets.get(key)
        var = self._uf_vars.get(key)
        if var:
            var.set(False)
        if widget:
            try:
                widget.configure(state="disabled")
            except Exception:
                pass

    def _save_user_friendly(self) -> None:
        rec = self._current_record
        if not rec or not rec.md_path:
            self.status_var.set("Nothing editable to save.")
            return

        uf_map = self._collect_user_friendly_from_widgets()
        try:
            raw = rec.md_path.read_text(encoding="utf-8")
        except Exception:
            raw = rec.md_raw or ""
        updated = self._replace_user_friendly_section(raw, uf_map, rec)
        rec.md_path.parent.mkdir(parents=True, exist_ok=True)
        rec.md_path.write_text(updated, encoding="utf-8")

        rec.md_raw = updated
        rec.md_meta, rec.md_body = parse_md_record(updated)
        self._current_uf_map = uf_map
        self.status_var.set(f"Saved User Friendly: {rec.md_path.name}")

    def _collect_user_friendly_from_widgets(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, widget in self._uf_edit_widgets.items():
            if not self._uf_vars.get(key, tk.BooleanVar(value=True)).get():
                continue
            value = self._get_widget_value(widget).strip()
            if value:
                result[key] = value
        return result

    def _reset_user_friendly_to_default(self) -> None:
        rec = self._current_record
        if not rec or not rec.md_path:
            self.status_var.set("No .md target to reset.")
            return
        default_map = self._default_user_friendly_map(rec)
        try:
            raw = rec.md_path.read_text(encoding="utf-8")
        except Exception:
            raw = rec.md_raw or ""
        updated = self._replace_user_friendly_section(raw, default_map, rec)
        rec.md_path.parent.mkdir(parents=True, exist_ok=True)
        rec.md_path.write_text(updated, encoding="utf-8")
        rec.md_raw = updated
        rec.md_meta, rec.md_body = parse_md_record(updated)
        self._current_uf_map = default_map
        self._render_user_friendly(rec)
        self.status_var.set("User Friendly reset to default.")

    # ------------------------------------------------------------------
    # Pro View
    # ------------------------------------------------------------------

    def _render_pro_view(self, rec: LoadedRecord) -> None:
        self._clear_container(self._pro_inner)
        self._pro_item_vars = []

        rec_frame = ttk.LabelFrame(self._pro_inner, text="Record", style="GlossarySection.TLabelframe")
        rec_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        rec_frame.columnconfigure(1, weight=1)
        ttk.Label(rec_frame, text="Title:", style="GlossaryKey.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 6))
        ttk.Label(rec_frame, text=rec.display_name, wraplength=900, justify="left").grid(row=0, column=1, sticky="w")
        ttk.Label(rec_frame, text="Path:", style="GlossaryKey.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, 6))
        ttk.Label(rec_frame, text=str(rec.md_path or rec.rel_stem), wraplength=900, justify="left").grid(row=1, column=1, sticky="w")

        clean = ttk.LabelFrame(self._pro_inner, text="Clean Fields", style="GlossarySection.TLabelframe")
        clean.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        clean.columnconfigure(2, weight=1)

        if not self._current_pro_items:
            ttk.Label(clean, text="No clean fields detected from .md.", style="GlossaryHint.TLabel").grid(row=0, column=0, sticky="w")
        else:
            for row_idx, item in enumerate(self._current_pro_items):
                var = tk.BooleanVar(value=item.get("selected", False))
                self._pro_item_vars.append((item, var))
                ttk.Checkbutton(clean, variable=var).grid(row=row_idx, column=0, sticky="nw", padx=(0, 6))
                ttk.Label(clean, text=self._labelize_key(item["key"]), style="GlossaryKey.TLabel").grid(row=row_idx, column=1, sticky="nw", padx=(0, 8))
                value_widget = self._make_pro_value_widget(clean, row_idx, item["value"])
                value_widget.grid(row=row_idx, column=2, sticky="ew", pady=(0, 4))

        dirty = ttk.LabelFrame(self._pro_inner, text="Dirty Group", style="GlossarySection.TLabelframe")
        dirty.grid(row=2, column=0, sticky="nsew")
        dirty.columnconfigure(0, weight=1)
        dirty.rowconfigure(0, weight=1)
        dirty_text = tk.Text(dirty, height=16, wrap="word", relief="flat", padx=8, pady=8)
        dirty_text.grid(row=0, column=0, sticky="nsew")
        dirty_text.insert("1.0", "\n\n".join(self._current_dirty_blocks).strip() or "No dirty grouped content.")
        sb = ttk.Scrollbar(dirty, orient="vertical", command=dirty_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        dirty_text.configure(yscrollcommand=sb.set)
        bind_y_scroll(dirty_text)

    def _make_pro_value_widget(self, parent, row_idx: int, value: str):
        if self._is_multiline_value(value):
            text = tk.Text(parent, height=max(3, min(10, value.count("\n") + 2)), wrap="word", relief="solid", bd=1, padx=4, pady=4)
            text.insert("1.0", value)
            bind_y_scroll(text)
            return text
        entry = ttk.Entry(parent)
        entry.insert(0, value)
        return entry

    def _update_user_friendly_from_pro(self) -> None:
        rec = self._current_record
        if not rec or not rec.md_path:
            self.status_var.set("No .md target available for User Friendly update.")
            return

        selected_map: dict[str, str] = {}
        for item, var in self._pro_item_vars:
            if var.get() and str(item.get("value", "")).strip():
                selected_map[item["key"]] = str(item["value"]).strip()

        if not selected_map:
            self.status_var.set("No Pro View fields selected.")
            return

        try:
            raw = rec.md_path.read_text(encoding="utf-8")
        except Exception:
            raw = rec.md_raw or ""
        updated = self._replace_user_friendly_section(raw, selected_map, rec)
        rec.md_path.parent.mkdir(parents=True, exist_ok=True)
        rec.md_path.write_text(updated, encoding="utf-8")
        rec.md_raw = updated
        rec.md_meta, rec.md_body = parse_md_record(updated)
        self._current_uf_map = selected_map
        self._render_user_friendly(rec)
        self.status_var.set("Updated User Friendly fields from Pro View.")

    def _build_pro_view_model(self, rec: LoadedRecord) -> tuple[list[dict], list[str]]:
        text = _remove_named_section(rec.md_raw or "", _UF_SECTION_START, _UF_SECTION_END)
        if not text:
            return [], []

        clean_items: list[dict] = []
        dirty_blocks: list[str] = []
        clean_keys: set[str] = set()
        current_dirty: list[str] = []

        def flush_dirty() -> None:
            nonlocal current_dirty
            chunk = "\n".join(line for line in current_dirty if line is not None).strip()
            if chunk:
                dirty_blocks.append(chunk)
            current_dirty = []

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if current_dirty and current_dirty[-1] != "":
                    current_dirty.append("")
                continue
            if stripped == _UF_SECTION_START or stripped == _UF_SECTION_END:
                continue
            if _looks_clean_marker(stripped):
                flush_dirty()
                key = stripped.strip("<>").strip()
                clean_items.append({"key": key or "marker", "value": stripped, "selected": True})
                continue
            clean = _split_clean_field(stripped)
            if clean is not None:
                flush_dirty()
                key, value = clean
                norm = self._normalize_key(key)
                if norm in clean_keys:
                    norm = self._dedupe_key(norm, clean_keys)
                clean_keys.add(norm)
                clean_items.append({"key": norm, "value": value, "selected": norm in self._default_user_friendly_map(rec)})
                continue
            current_dirty.append(line)
        flush_dirty()
        return clean_items, dirty_blocks

    # ------------------------------------------------------------------
    # Machine View
    # ------------------------------------------------------------------

    def _build_machine_sections(self, rec: LoadedRecord) -> list[tuple[str, str]]:
        data = rec.json_data or {}
        if not data:
            return [("Info", "No JSON data available.")]
        sections: list[tuple[str, str]] = []
        for key in sorted(data.keys(), key=lambda x: str(x).lower()):
            value = data.get(key)
            sections.append((self._labelize_key(str(key)), _stringify_machine_value(value)))
        return sections

    def _build_machine_promote_lines(self, rec: LoadedRecord) -> list[str]:
        lines = []
        for title, body in self._build_machine_sections(rec):
            if body.strip():
                lines.append(f"{self._normalize_key(title)}: {body.strip()}")
        return lines

    def _render_machine_view(self) -> None:
        rec = self._current_record
        if rec is None:
            _set_text(self._machine_text, "")
            return
        needle = self.machine_filter_var.get().strip().lower()
        parts: list[str] = []
        for title, body in self._current_machine_sections:
            block = f"{title}\n{'-' * len(title)}\n{body}".strip()
            if needle and needle not in block.lower():
                continue
            parts.append(block)
        if not parts:
            if needle:
                parts = ["No machine-view sections match the current filter."]
            else:
                parts = ["No JSON data available."]
        _set_text(self._machine_text, "\n\n".join(parts))

    # ------------------------------------------------------------------
    # Promote from support tabs
    # ------------------------------------------------------------------

    def _promote_from_machine(self) -> None:
        rec = self._current_record
        if not rec:
            self.status_var.set("Nothing selected.")
            return
        target_md = rec.md_path or self._default_md_target_path(rec)
        header = {
            "title": rec.display_name,
            "record_key": rec.record_key,
        }
        if rec.json_path:
            header["source_json"] = str(rec.json_path)
        block = self._format_promoted_md_block(header, self._current_machine_lines)
        if target_md.exists():
            existing = target_md.read_text(encoding="utf-8")
            updated = existing.rstrip() + "\n\n" + block + "\n"
        else:
            updated = block + "\n"
        target_md.parent.mkdir(parents=True, exist_ok=True)
        target_md.write_text(updated, encoding="utf-8")
        self.after_idle(self._safe_initial_refresh)
        self.status_var.set(f"Promoted machine view to {target_md.name}")

    def _promote_from_full(self) -> None:
        rec = self._current_record
        if not rec:
            self.status_var.set("Nothing selected.")
            return
        if not rec.md_raw.strip():
            self.status_var.set("No .md content exists to promote.")
            return
        target_md = rec.md_path or self._default_md_target_path(rec)
        target_md.parent.mkdir(parents=True, exist_ok=True)
        if target_md.exists():
            self.status_var.set(".md already exists; record restored for User Friendly display.")
        else:
            target_md.write_text(rec.md_raw, encoding="utf-8")
            self.status_var.set(f"Created .md from full view: {target_md.name}")
        self.after_idle(self._safe_initial_refresh)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_user_friendly_map(self, rec: LoadedRecord) -> dict[str, str]:
        if not rec.md_raw:
            return {}
        return _extract_named_section_map(rec.md_raw, _UF_SECTION_START, _UF_SECTION_END)

    def _default_user_friendly_map(self, rec: LoadedRecord) -> dict[str, str]:
        source = rec.md_meta or {}
        result: dict[str, str] = {}
        visible_defaults = self._visibility_store.visible_keys_for(rec.record_key, source.keys())
        keys = visible_defaults or list(source.keys())
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            text = _stringify_md_value(value).strip()
            if text:
                result[self._normalize_key(key)] = text
        if not result and rec.md_body.strip():
            result["body"] = rec.md_body.strip()
        return result

    def _replace_user_friendly_section(self, raw: str, uf_map: dict[str, str], rec: LoadedRecord) -> str:
        block = self._render_user_friendly_section(uf_map, rec)
        if not raw.strip():
            return block
        start = raw.find(_UF_SECTION_START)
        end = raw.find(_UF_SECTION_END)
        if start != -1 and end != -1 and end > start:
            end += len(_UF_SECTION_END)
            prefix = raw[:start].rstrip()
            suffix = raw[end:].lstrip("\n")
            parts = [p for p in [prefix, block, suffix] if p]
            return "\n\n".join(parts).rstrip() + "\n"
        return raw.rstrip() + "\n\n" + block + "\n"

    def _render_user_friendly_section(self, uf_map: dict[str, str], rec: LoadedRecord) -> str:
        lines = [_UF_SECTION_START, f"record_key: {rec.record_key}"]
        if rec.json_path:
            lines.append(f"source_json: {rec.json_path}")
        for key, value in uf_map.items():
            if "\n" in value:
                lines.append(f"{key}: |")
                lines.extend([f"  {part}" if part else "" for part in value.splitlines()])
            else:
                lines.append(f"{key}: {value}")
        lines.append(_UF_SECTION_END)
        return "\n".join(lines)

    def _default_md_target_path(self, rec: LoadedRecord) -> Path:
        base = None
        if self._scan_result and self._scan_result.md_dir:
            base = self._scan_result.md_dir
        elif self._chosen_root:
            base = self._chosen_root / "md" / self._scan_result.active_index_name if self._scan_result and self._scan_result.active_index_name else self._chosen_root / self.INDEX_NAME / "md"
        else:
            base = self.page_dir / "_generated_md"
        return Path(base) / f"{rec.rel_stem}.record.md"

    def _clear_views(self) -> None:
        self._clear_container(self._uf_inner)
        self._clear_container(self._pro_inner)
        _set_text(self._machine_text, "")

    def _clear_container(self, widget) -> None:
        for child in widget.winfo_children():
            child.destroy()

    def _get_widget_value(self, widget) -> str:
        if isinstance(widget, tk.Text):
            return widget.get("1.0", "end-1c")
        return widget.get()

    def _labelize_key(self, key: str) -> str:
        text = str(key).strip().replace("_", " ").replace("-", " ")
        return " ".join(part.capitalize() if part else "" for part in text.split()) or "Field"

    def _normalize_key(self, key: str) -> str:
        return str(key).strip().lower().replace(" ", "_").replace("/", "_")

    def _dedupe_key(self, key: str, existing: set[str]) -> str:
        idx = 2
        new_key = f"{key}_{idx}"
        while new_key in existing:
            idx += 1
            new_key = f"{key}_{idx}"
        return new_key

    def _is_multiline_value(self, value: str) -> bool:
        return "\n" in str(value) or len(str(value)) > 120

    def _format_promoted_md_block(self, header: dict[str, str], machine_lines: list[str]) -> str:
        out: list[str] = []
        for key, value in header.items():
            out.append(f"{key}: {value}")
        out.append("")
        out.append("# Promoted Machine View")
        out.extend(machine_lines)
        return "\n".join(out).rstrip()



# ----------------------------------------------------------------------
# Standalone helpers
# ----------------------------------------------------------------------


def _set_text(widget: tk.Text, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


def _split_clean_field(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key or not value:
        return None
    if key.startswith("#"):
        return None
    return key, value


def _looks_clean_marker(text: str) -> bool:
    return text.startswith("<<") and text.endswith(">>") and len(text) >= 4


def _stringify_md_value(value) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def _stringify_machine_value(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key in sorted(value.keys(), key=lambda x: str(x).lower()):
            parts.append(f"{key}: {_stringify_machine_value(value[key])}")
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(f"- {_stringify_machine_value(item)}" for item in value)
    return str(value)


def _extract_named_section_map(text: str, start_marker: str, end_marker: str) -> dict[str, str]:
    lines = text.splitlines()
    inside = False
    section: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == start_marker:
            inside = True
            continue
        if stripped == end_marker:
            break
        if inside:
            section.append(line)
    if not section:
        return {}
    data = parse_md_record("\n".join(section))[0]
    result: dict[str, str] = {}
    for key, value in data.items():
        if key in {"record_key", "source_json"}:
            continue
        text_value = _stringify_md_value(value).strip()
        if text_value:
            result[str(key).strip()] = text_value
    return result


def _remove_named_section(text: str, start_marker: str, end_marker: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == start_marker:
            inside = True
            continue
        if inside and stripped == end_marker:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "\n".join(out).strip()

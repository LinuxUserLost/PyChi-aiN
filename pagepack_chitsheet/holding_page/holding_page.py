from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

_SHARED_DIR = Path(__file__).resolve().parents[1] / "shared_bypages"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from projectpack_loader import LoadedRecord, find_projectpack_root, scan_page_index
from scroll_utils import bind_canvas_recursive, bind_y_scroll


class HoldingPage(ttk.Frame):
    PAGE_TITLE = "Holding"
    INDEX_NAME = "holding_index"
    INDEX_ALIASES = {}
    TAG_KEYS = {
        "tags", "tag", "labels", "label", "sorting_tags", "holding_tags",
        "possible_sort", "possible_home", "candidate_home", "candidate_bucket",
        "placement_hint", "placement_hints", "subcategories", "subcategory",
        "bucket", "family", "type",
    }
    SIMPLE_SKIP_KEYS = {
        "record_key", "rel_stem", "source_json", "source_md", "path", "json_path",
        "md_path", "schema", "schema_version", "bucket_index", "uid", "id",
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
        self._note_dir = state_root / "holding_notes"
        self._chosen_root: Path | None = self._read_saved_root_choice()

        self._scan_result = None
        self._records: list[LoadedRecord] = []
        self._record_lookup: dict[str, LoadedRecord] = {}
        self._display_lookup: dict[str, str] = {}
        self._current_record: LoadedRecord | None = None

        self.status_var = tk.StringVar(value="Ready.")
        self.root_display_var = tk.StringVar(value="")
        self.index_display_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="All")
        self._search_job = None

        self._listboxes: list[tk.Listbox] = []
        self._result_labels: list[tk.StringVar] = []
        self._item_display_names: list[str] = []

        self._build_styles()
        self._build_ui()
        self.after_idle(self._safe_initial_refresh)

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

    def _build_styles(self) -> None:
        s = ttk.Style()
        try:
            s.configure("HoldingSection.TLabelframe", padding=8)
            s.configure("HoldingSection.TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
            s.configure("HoldingKey.TLabel", font=("TkDefaultFont", 9, "bold"))
            s.configure("HoldingHint.TLabel", foreground="#555555")
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(outer, text="Holding", style="HoldingSection.TLabelframe")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        top.columnconfigure(5, weight=1)

        ttk.Label(top, text="Search:", style="HoldingKey.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        search = ttk.Entry(top, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky="ew")
        search.bind("<KeyRelease>", self._on_search_changed)

        ttk.Label(top, text="Filter:", style="HoldingKey.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 6))
        filter_box = ttk.Combobox(
            top,
            textvariable=self.filter_var,
            state="readonly",
            values=["All", "Paired", "Markdown Only", "JSON Only"],
            width=14,
        )
        filter_box.grid(row=0, column=3, sticky="w")
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._render_all_tabs())

        ttk.Button(top, text="Choose Root", command=self._choose_projectpack_root).grid(row=0, column=4, sticky="e", padx=(10, 0))
        ttk.Label(top, textvariable=self.root_display_var, anchor="w", style="HoldingHint.TLabel").grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        ttk.Label(top, textvariable=self.index_display_var, anchor="w", style="HoldingHint.TLabel").grid(row=2, column=0, columnspan=6, sticky="ew", pady=(2, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self._tab_user = ttk.Frame(self.notebook, padding=6)
        self._tab_pro = ttk.Frame(self.notebook, padding=6)
        self._tab_machine = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self._tab_user, text="User Friendly")
        self.notebook.add(self._tab_pro, text="Pro View")
        self.notebook.add(self._tab_machine, text="Machine View")

        self._user_widgets = self._build_holding_tab(self._tab_user, role="user")
        self._pro_widgets = self._build_holding_tab(self._tab_pro, role="pro")
        self._machine_widgets = self._build_machine_tab(self._tab_machine)

        self._listboxes = [self._user_widgets["listbox"], self._pro_widgets["listbox"], self._machine_widgets["listbox"]]
        self._result_labels = [self._user_widgets["results_var"], self._pro_widgets["results_var"], self._machine_widgets["results_var"]]

        status_row = ttk.Frame(outer)
        status_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="w")

    def _build_holding_tab(self, tab: ttk.Frame, role: str) -> dict:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        body = ttk.Frame(tab)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=200)
        body.columnconfigure(1, weight=3, minsize=340)
        body.columnconfigure(2, weight=2, minsize=240)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Held Items", style="HoldingSection.TLabelframe")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        results_var = tk.StringVar(value="0 items")
        ttk.Label(left, textvariable=results_var, style="HoldingHint.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))

        listbox = tk.Listbox(left, exportselection=False)
        listbox.grid(row=1, column=0, sticky="nsew")
        listbox.bind("<<ListboxSelect>>", self._on_item_selected)
        lsb = ttk.Scrollbar(left, orient="vertical", command=listbox.yview)
        lsb.grid(row=1, column=1, sticky="ns")
        listbox.configure(yscrollcommand=lsb.set)
        bind_y_scroll(listbox)

        center_title = "Holding Overview" if role == "user" else "Holding Review"
        center = ttk.LabelFrame(body, text=center_title, style="HoldingSection.TLabelframe")
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)

        center_canvas = tk.Canvas(center, borderwidth=0, highlightthickness=0)
        center_canvas.grid(row=0, column=0, sticky="nsew")
        center_vsb = ttk.Scrollbar(center, orient="vertical", command=center_canvas.yview)
        center_vsb.grid(row=0, column=1, sticky="ns")
        center_canvas.configure(yscrollcommand=center_vsb.set)
        center_inner = ttk.Frame(center_canvas)
        center_inner.columnconfigure(0, weight=1)
        center_window = center_canvas.create_window((0, 0), window=center_inner, anchor="nw")
        center_canvas._shared_window_id = center_window
        bind_canvas_recursive(center_canvas, center_inner)

        right = ttk.LabelFrame(body, text="Sort Hints / Tags", style="HoldingSection.TLabelframe")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        right_notebook = ttk.Notebook(right)
        right_notebook.grid(row=0, column=0, sticky="nsew")
        tags_frame = ttk.Frame(right_notebook, padding=4)
        paths_frame = ttk.Frame(right_notebook, padding=4)
        right_notebook.add(tags_frame, text="Tags")
        right_notebook.add(paths_frame, text="Possible Paths")

        tags_text = self._build_readonly_text(tags_frame)
        paths_text = self._build_readonly_text(paths_frame)

        notes_wrap = ttk.LabelFrame(tab, text="Possible Sorting Paths", style="HoldingSection.TLabelframe")
        notes_wrap.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        notes_wrap.columnconfigure(0, weight=1)
        notes_wrap.rowconfigure(0, weight=1)
        note_text = tk.Text(notes_wrap, wrap="word", height=6)
        note_text.grid(row=0, column=0, sticky="nsew")
        note_sb = ttk.Scrollbar(notes_wrap, orient="vertical", command=note_text.yview)
        note_sb.grid(row=0, column=1, sticky="ns")
        note_text.configure(yscrollcommand=note_sb.set)
        bind_y_scroll(note_text)

        btns = ttk.Frame(notes_wrap)
        btns.grid(row=1, column=0, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="Save Notes", command=lambda r=role: self._save_local_note(r)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text="Clear Notes", command=lambda r=role: self._clear_local_note(r)).grid(row=0, column=1)

        return {
            "role": role,
            "listbox": listbox,
            "results_var": results_var,
            "center_inner": center_inner,
            "tags_text": tags_text,
            "paths_text": paths_text,
            "note_text": note_text,
        }

    def _build_machine_tab(self, tab: ttk.Frame) -> dict:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        body = ttk.Frame(tab)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=200)
        body.columnconfigure(1, weight=3, minsize=360)
        body.columnconfigure(2, weight=2, minsize=240)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Held Items", style="HoldingSection.TLabelframe")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        results_var = tk.StringVar(value="0 items")
        ttk.Label(left, textvariable=results_var, style="HoldingHint.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))

        listbox = tk.Listbox(left, exportselection=False)
        listbox.grid(row=1, column=0, sticky="nsew")
        listbox.bind("<<ListboxSelect>>", self._on_item_selected)
        lsb = ttk.Scrollbar(left, orient="vertical", command=listbox.yview)
        lsb.grid(row=1, column=1, sticky="ns")
        listbox.configure(yscrollcommand=lsb.set)
        bind_y_scroll(listbox)

        center = ttk.LabelFrame(body, text="Machine View", style="HoldingSection.TLabelframe")
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)
        machine_text = self._build_readonly_text(center)

        right = ttk.LabelFrame(body, text="Machine Tags / Paths", style="HoldingSection.TLabelframe")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right_text = self._build_readonly_text(right)

        return {
            "listbox": listbox,
            "results_var": results_var,
            "machine_text": machine_text,
            "right_text": right_text,
        }

    def _build_readonly_text(self, parent) -> tk.Text:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        text = tk.Text(parent, wrap="word", relief="flat", padx=8, pady=8, state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=sb.set)
        text.tag_configure("heading", font=("TkDefaultFont", 10, "bold"))
        text.tag_configure("key", font=("TkDefaultFont", 9, "bold"))
        text.tag_configure("dim", foreground="#666666")
        bind_y_scroll(text)
        return text

    def _refresh_records(self) -> None:
        root_path, root_shape, warnings = find_projectpack_root(
            page_file=__file__,
            page_folder=self.page_folder,
            chosen_root=self._chosen_root,
        )
        self._scan_result = scan_page_index(root_path, self.INDEX_NAME, alias_map=self.INDEX_ALIASES)
        self._records = list(self._scan_result.records)
        self._record_lookup = {r.record_key: r for r in self._records}

        self.root_display_var.set(
            f"Root: {self._scan_result.root_path or 'not found'}"
        )
        active_name = self._scan_result.active_index_name or self.INDEX_NAME
        self.index_display_var.set(
            f"Index: {active_name} | Records: {len(self._records)} | Pairs: {sum(1 for r in self._records if r.source_state == 'paired')}"
        )

        combined_warnings = list(warnings) + list(self._scan_result.warnings)
        self.status_var.set(" | ".join(combined_warnings) if combined_warnings else f"Loaded {len(self._records)} holding records.")
        self._render_all_tabs()

    def _on_search_changed(self, _event=None) -> None:
        if self._search_job is not None:
            try:
                self.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.after(120, self._render_all_tabs)

    def _render_all_tabs(self) -> None:
        filtered = self._filtered_records()
        self._item_display_names = []
        self._display_lookup = {}
        counts = Counter(self._subcategory_for_record(r) for r in filtered)
        for rec in filtered:
            sub = self._subcategory_for_record(rec)
            label = f"[{sub}] {self._display_name_for_record(rec)}"
            if label in self._display_lookup:
                label = f"{label} [{rec.record_key}]"
            self._display_lookup[label] = rec.record_key
            self._item_display_names.append(label)

        grouped_note = ", ".join(f"{k}:{counts[k]}" for k in sorted(counts)[:6]) or "0 groups"
        for listbox, var in zip(self._listboxes, self._result_labels):
            listbox.delete(0, "end")
            for name in self._item_display_names:
                listbox.insert("end", name)
            var.set(f"{len(filtered)} items | {grouped_note}")

        if filtered:
            if self._current_record and self._current_record.record_key in self._record_lookup:
                target_key = self._current_record.record_key
            else:
                target_key = filtered[0].record_key
            self._select_record_by_key(target_key)
        else:
            self._current_record = None
            self._clear_views()

    def _filtered_records(self) -> list[LoadedRecord]:
        text = self.search_var.get().strip().lower()
        mode = self.filter_var.get()
        out: list[LoadedRecord] = []
        for rec in self._records:
            if mode == "Paired" and rec.source_state != "paired":
                continue
            if mode == "Markdown Only" and rec.source_state != "md_only":
                continue
            if mode == "JSON Only" and rec.source_state != "json_only":
                continue
            if text:
                blob = self._search_blob(rec)
                if text not in blob:
                    continue
            out.append(rec)
        return out

    def _search_blob(self, rec: LoadedRecord) -> str:
        parts = [
            rec.record_key,
            rec.rel_stem,
            self._subcategory_for_record(rec),
            self._display_name_for_record(rec),
            " ".join(self._tags_for_record(rec)),
            rec.md_body[:2500],
        ]
        if rec.md_raw:
            parts.append(rec.md_raw[:2500])
        if rec.json_data:
            try:
                parts.append(json.dumps(rec.json_data, ensure_ascii=False)[:4000])
            except Exception:
                parts.append(str(rec.json_data)[:4000])
        return "\n".join(str(p) for p in parts if p).lower()

    def _subcategory_for_record(self, rec: LoadedRecord) -> str:
        parent = Path(rec.rel_stem).parent
        if str(parent) and str(parent) != ".":
            return str(parent).replace("\\", "/")
        tags = self._tags_for_record(rec)
        if tags:
            return tags[0]
        return "root"

    def _display_name_for_record(self, rec: LoadedRecord) -> str:
        meta = rec.md_meta or {}
        data = rec.json_data or {}
        for key in ("title", "name", "topic", "label", "summary"):
            val = meta.get(key) or data.get(key)
            if isinstance(val, (str, int, float)) and str(val).strip():
                return str(val).strip()
        return rec.display_name or Path(rec.rel_stem).name

    def _tags_for_record(self, rec: LoadedRecord) -> list[str]:
        values: list[str] = []
        for src in (rec.md_meta or {}, rec.json_data or {}):
            for key, val in src.items():
                if key not in self.TAG_KEYS and not any(x in key.lower() for x in ("tag", "label", "sort", "place", "bucket", "family")):
                    continue
                if isinstance(val, list):
                    values.extend(str(v).strip() for v in val if str(v).strip())
                elif isinstance(val, dict):
                    values.extend(f"{k}: {v}" for k, v in val.items() if str(v).strip())
                elif val is not None and str(val).strip():
                    values.extend(p.strip() for p in str(val).replace("|", ",").split(",") if p.strip())
        seen = []
        for item in values:
            if item and item not in seen:
                seen.append(item)
        return seen[:24]

    def _on_item_selected(self, _event=None) -> None:
        widget = self.focus_get()
        if not isinstance(widget, tk.Listbox):
            return
        sel = widget.curselection()
        if not sel:
            return
        try:
            label = widget.get(sel[0])
        except Exception:
            return
        record_key = self._display_lookup.get(label)
        if record_key:
            self._select_record_by_key(record_key)

    def _select_record_by_key(self, record_key: str) -> None:
        rec = self._record_lookup.get(record_key)
        if rec is None:
            return
        self._current_record = rec
        for listbox in self._listboxes:
            listbox.selection_clear(0, "end")
            for i, item in enumerate(self._item_display_names):
                if self._display_lookup.get(item) == record_key:
                    listbox.selection_set(i)
                    listbox.see(i)
                    break
        self._render_record(rec)

    def _render_record(self, rec: LoadedRecord) -> None:
        self._render_user_tab(rec)
        self._render_pro_tab(rec)
        self._render_machine_tab(rec)
        self.status_var.set(f"Loaded holding item: {self._display_name_for_record(rec)}")

    def _render_user_tab(self, rec: LoadedRecord) -> None:
        for child in self._user_widgets["center_inner"].winfo_children():
            child.destroy()
        self._fill_user_center(self._user_widgets["center_inner"], rec)
        self._set_text(self._user_widgets["tags_text"], self._format_tags_panel(rec))
        self._set_text(self._user_widgets["paths_text"], self._format_paths_panel(rec))
        self._load_note_into_widget(self._user_widgets["note_text"], rec)

    def _render_pro_tab(self, rec: LoadedRecord) -> None:
        for child in self._pro_widgets["center_inner"].winfo_children():
            child.destroy()
        self._fill_pro_center(self._pro_widgets["center_inner"], rec)
        self._set_text(self._pro_widgets["tags_text"], self._format_tags_panel(rec, include_extra=True))
        self._set_text(self._pro_widgets["paths_text"], self._format_paths_panel(rec, include_extra=True))
        self._load_note_into_widget(self._pro_widgets["note_text"], rec)

    def _render_machine_tab(self, rec: LoadedRecord) -> None:
        self._set_text(self._machine_widgets["machine_text"], self._format_machine_text(rec))
        self._set_text(self._machine_widgets["right_text"], self._format_machine_side(rec))

    def _fill_user_center(self, parent, rec: LoadedRecord) -> None:
        row = 0
        summary = self._summary_lines(rec)
        if summary:
            lf = ttk.LabelFrame(parent, text="Simple Summary", style="HoldingSection.TLabelframe")
            lf.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            lf.columnconfigure(0, weight=1)
            self._add_bullets(lf, summary)
            row += 1

        held = self._held_group_lines(rec)
        if held:
            lf = ttk.LabelFrame(parent, text="Held Material", style="HoldingSection.TLabelframe")
            lf.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            lf.columnconfigure(0, weight=1)
            self._add_bullets(lf, held)
            row += 1

        body = rec.md_body.strip()
        if body:
            lf = ttk.LabelFrame(parent, text="Readable Notes", style="HoldingSection.TLabelframe")
            lf.grid(row=row, column=0, sticky="nsew", pady=(0, 6))
            lf.columnconfigure(0, weight=1)
            text = self._build_readonly_text(lf)
            self._set_text(text, body[:5000])

    def _fill_pro_center(self, parent, rec: LoadedRecord) -> None:
        clean, rough = self._split_md_content(rec)
        row = 0

        overview = [
            f"Subcategory: {self._subcategory_for_record(rec)}",
            f"Record Key: {rec.record_key}",
            f"State: {rec.source_state}",
        ]
        ov = ttk.LabelFrame(parent, text="Holding Overview", style="HoldingSection.TLabelframe")
        ov.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        ov.columnconfigure(0, weight=1)
        self._add_bullets(ov, overview)
        row += 1

        if clean:
            lf = ttk.LabelFrame(parent, text="Clean Structured Items", style="HoldingSection.TLabelframe")
            lf.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            lf.columnconfigure(0, weight=1)
            self._add_bullets(lf, clean)
            row += 1

        if rough:
            lf = ttk.LabelFrame(parent, text="Rough / Support Holding Content", style="HoldingSection.TLabelframe")
            lf.grid(row=row, column=0, sticky="nsew", pady=(0, 6))
            lf.columnconfigure(0, weight=1)
            text = self._build_readonly_text(lf)
            self._set_text(text, "\n".join(rough[:400]))

    def _split_md_content(self, rec: LoadedRecord) -> tuple[list[str], list[str]]:
        clean: list[str] = []
        rough: list[str] = []
        for line in rec.md_raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<<") and stripped.endswith(">>"):
                clean.append(stripped)
            elif ":" in stripped and not stripped.startswith(("#", "-", "*", "  ")):
                clean.append(stripped)
            else:
                rough.append(stripped)
        return clean, rough

    def _summary_lines(self, rec: LoadedRecord) -> list[str]:
        lines: list[str] = []
        meta = {**(rec.json_data or {}), **(rec.md_meta or {})}
        for key in ("title", "name", "summary", "what_it_is", "why_held", "reason", "status"):
            val = meta.get(key)
            if val is not None and str(val).strip():
                lines.append(f"{key}: {self._fmt_value(val)}")
        if not lines:
            lines.append(f"Stored under: {self._subcategory_for_record(rec)}")
        return lines[:8]

    def _held_group_lines(self, rec: LoadedRecord) -> list[str]:
        meta = {**(rec.json_data or {}), **(rec.md_meta or {})}
        lines: list[str] = []
        for key in ("bucket", "family", "possible_home", "candidate_home", "placement_hint", "next_place"):
            val = meta.get(key)
            if val is not None and str(val).strip():
                lines.append(f"{key}: {self._fmt_value(val)}")
        tags = self._tags_for_record(rec)
        if tags:
            lines.append("tags: " + ", ".join(tags[:10]))
        return lines

    def _format_tags_panel(self, rec: LoadedRecord, include_extra: bool = False) -> str:
        tags = self._tags_for_record(rec)
        lines = [f"Subcategory: {self._subcategory_for_record(rec)}", "", "Tags / tag-like labels:"]
        if tags:
            lines.extend(f"- {tag}" for tag in tags)
        else:
            lines.append("- none found")
        if include_extra:
            lines.extend(["", f"Source State: {rec.source_state}", f"Display Name: {self._display_name_for_record(rec)}"])
        return "\n".join(lines)

    def _format_paths_panel(self, rec: LoadedRecord, include_extra: bool = False) -> str:
        meta = {**(rec.json_data or {}), **(rec.md_meta or {})}
        lines = ["Possible sorting paths:"]
        found = False
        for key in ("possible_home", "candidate_home", "placement_hint", "next_place", "bucket", "family"):
            val = meta.get(key)
            if val is not None and str(val).strip():
                lines.append(f"- {key}: {self._fmt_value(val)}")
                found = True
        if not found:
            lines.append("- no explicit placement hints found")
        lines.extend(["", f"Relative Stem: {rec.rel_stem}"])
        if include_extra:
            if rec.md_path:
                lines.append(f"MD: {rec.md_path}")
            if rec.json_path:
                lines.append(f"JSON: {rec.json_path}")
        return "\n".join(lines)

    def _format_machine_text(self, rec: LoadedRecord) -> str:
        data = rec.json_data or {}
        if not data:
            return "No JSON record found for this holding item."
        lines = []
        for key in sorted(data.keys()):
            lines.append(f"{key}:\n{self._fmt_value(data[key])}\n")
        return "\n".join(lines).strip()

    def _format_machine_side(self, rec: LoadedRecord) -> str:
        lines = [
            f"Record Key: {rec.record_key}",
            f"Subcategory: {self._subcategory_for_record(rec)}",
            f"Source State: {rec.source_state}",
            "",
            "Tags:",
        ]
        tags = self._tags_for_record(rec)
        if tags:
            lines.extend(f"- {tag}" for tag in tags)
        else:
            lines.append("- none found")
        return "\n".join(lines)

    def _load_note_into_widget(self, widget: tk.Text, rec: LoadedRecord) -> None:
        widget.delete("1.0", "end")
        path = self._note_path_for_record(rec)
        if path.exists():
            try:
                widget.insert("1.0", path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save_local_note(self, role: str) -> None:
        if not self._current_record:
            self.status_var.set("Nothing selected.")
            return
        widget = self._user_widgets["note_text"] if role == "user" else self._pro_widgets["note_text"]
        text = widget.get("1.0", "end-1c").rstrip()
        path = self._note_path_for_record(self._current_record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.status_var.set(f"Saved holding note: {path.name}")

    def _clear_local_note(self, role: str) -> None:
        widget = self._user_widgets["note_text"] if role == "user" else self._pro_widgets["note_text"]
        widget.delete("1.0", "end")

    def _note_path_for_record(self, rec: LoadedRecord) -> Path:
        safe = rec.record_key.replace("/", "__").replace("\\", "__")
        return self._note_dir / f"{safe}.md"

    def _clear_views(self) -> None:
        for inner in (self._user_widgets["center_inner"], self._pro_widgets["center_inner"]):
            for child in inner.winfo_children():
                child.destroy()
        for key in ("tags_text", "paths_text"):
            self._set_text(self._user_widgets[key], "")
            self._set_text(self._pro_widgets[key], "")
        self._set_text(self._machine_widgets["machine_text"], "")
        self._set_text(self._machine_widgets["right_text"], "")
        self._user_widgets["note_text"].delete("1.0", "end")
        self._pro_widgets["note_text"].delete("1.0", "end")

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _add_bullets(self, parent, lines: list[str]) -> None:
        for i, line in enumerate(lines):
            ttk.Label(parent, text=f"• {line}", wraplength=620, justify="left").grid(row=i, column=0, sticky="w", pady=1)

    def _fmt_value(self, value) -> str:
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return "; ".join(f"{k}: {v}" for k, v in value.items())
        return str(value)

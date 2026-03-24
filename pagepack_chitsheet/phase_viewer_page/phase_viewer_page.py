from __future__ import annotations

import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

_SHARED_DIR = Path(__file__).resolve().parents[1] / "shared_bypages"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from projectpack_loader import LoadedRecord, find_projectpack_root, parse_md_record, scan_page_index
from scroll_utils import bind_canvas_recursive, bind_y_scroll


class PhaseViewerPage(ttk.Frame):
    PAGE_TITLE = "Phase Viewer"
    INDEX_NAME = "phase_viewer_index"
    INDEX_ALIASES = {"phase_viewer_index": []}
    SUMMARY_KEYS = [
        "title",
        "name",
        "phase",
        "stage",
        "belongs_to",
        "parent",
        "summary",
        "what_it_is",
        "notes",
    ]
    NEIGHBOR_KEYS = [
        "belongs_to",
        "parent",
        "children",
        "nearby_related",
        "related_to",
        "supports",
        "supported_by",
        "used_with",
        "cross_use",
        "future_use",
    ]
    SEQUENCE_KEYS = [
        "before",
        "after",
        "previous",
        "next",
        "depends_on",
        "unlocks",
        "comes_before",
        "comes_after",
    ]
    GAP_KEYS = [
        "missing_links",
        "missing",
        "gaps",
        "unknowns",
        "open_questions",
        "weak_links",
        "conflicted_links",
        "blockers",
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
        self._chosen_root: Path | None = self._read_saved_root_choice()

        self._scan_result = None
        self._records: list[LoadedRecord] = []
        self._record_lookup: dict[str, LoadedRecord] = {}
        self._display_lookup: dict[str, str] = {}
        self._current_record: LoadedRecord | None = None
        self._pair_count = 0
        self._record_count = 0
        self._alias_mode = False

        self.status_var = tk.StringVar(value="Ready.")
        self.root_display_var = tk.StringVar(value="")
        self.index_display_var = tk.StringVar(value="")
        self.scan_status_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="All")
        self._search_job = None

        self._listboxes: list[tk.Listbox] = []
        self._result_labels: list[tk.StringVar] = []
        self._item_display_names: list[str] = []

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
            s.configure("PhaseSection.TLabelframe", padding=8)
            s.configure("PhaseSection.TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
            s.configure("PhaseKey.TLabel", font=("TkDefaultFont", 9, "bold"))
            s.configure("PhaseHint.TLabel", foreground="#555555")
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(outer, text="Phase Viewer", style="PhaseSection.TLabelframe")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=0)
        top.columnconfigure(5, weight=1)

        ttk.Label(top, text="Search:", style="PhaseKey.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        search = ttk.Entry(top, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky="ew")
        search.bind("<KeyRelease>", self._on_search_changed)

        ttk.Label(top, text="Filter:", style="PhaseKey.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 6))
        filter_box = ttk.Combobox(top, textvariable=self.filter_var, state="readonly", values=["All", "Paired", "Markdown Only", "JSON Only"], width=14)
        filter_box.grid(row=0, column=3, sticky="w")
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._render_all_tabs())

        ttk.Button(top, text="Choose Root", command=self._choose_projectpack_root).grid(row=0, column=4, sticky="e", padx=(10, 0))
        ttk.Label(top, textvariable=self.root_display_var, anchor="w", style="PhaseHint.TLabel").grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        ttk.Label(top, textvariable=self.index_display_var, anchor="w", style="PhaseHint.TLabel").grid(row=2, column=0, columnspan=6, sticky="ew", pady=(2, 0))
        ttk.Label(top, textvariable=self.scan_status_var, anchor="w", style="PhaseHint.TLabel").grid(row=3, column=0, columnspan=6, sticky="ew", pady=(2, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self._tab_user = ttk.Frame(self.notebook, padding=6)
        self._tab_pro = ttk.Frame(self.notebook, padding=6)
        self._tab_machine = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self._tab_user, text="User Friendly")
        self.notebook.add(self._tab_pro, text="Pro View")
        self.notebook.add(self._tab_machine, text="Machine View")

        self._user_widgets = self._build_phase_tab(self._tab_user, role="user")
        self._pro_widgets = self._build_phase_tab(self._tab_pro, role="pro")
        self._machine_widgets = self._build_machine_tab(self._tab_machine)

        self._listboxes = [self._user_widgets["listbox"], self._pro_widgets["listbox"], self._machine_widgets["listbox"]]
        self._result_labels = [self._user_widgets["results_var"], self._pro_widgets["results_var"], self._machine_widgets["results_var"]]

        status_row = ttk.Frame(outer)
        status_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="w")

    def _build_phase_tab(self, tab: ttk.Frame, role: str) -> dict:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        body = ttk.Frame(tab)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=180)
        body.columnconfigure(1, weight=3, minsize=340)
        body.columnconfigure(2, weight=2, minsize=220)
        body.rowconfigure(0, weight=1)
        if role != "machine":
            body.rowconfigure(1, weight=0)

        left = ttk.LabelFrame(body, text="Items", style="PhaseSection.TLabelframe")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        results_var = tk.StringVar(value="0 items")
        ttk.Label(left, textvariable=results_var, style="PhaseHint.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))

        listbox = tk.Listbox(left, exportselection=False)
        listbox.grid(row=1, column=0, sticky="nsew")
        listbox.bind("<<ListboxSelect>>", self._on_item_selected)
        lsb = ttk.Scrollbar(left, orient="vertical", command=listbox.yview)
        lsb.grid(row=1, column=1, sticky="ns")
        listbox.configure(yscrollcommand=lsb.set)
        bind_y_scroll(listbox)

        center_title = "Phase Structure" if role == "user" else "Phase Structure / Progression"
        center = ttk.LabelFrame(body, text=center_title, style="PhaseSection.TLabelframe")
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)

        center_canvas = tk.Canvas(center, borderwidth=0, highlightthickness=0)
        center_canvas.grid(row=0, column=0, sticky="nsew")
        center_vsb = ttk.Scrollbar(center, orient="vertical", command=center_canvas.yview)
        center_vsb.grid(row=0, column=1, sticky="ns")
        center_canvas.configure(yscrollcommand=center_vsb.set)
        center_inner = ttk.Frame(center_canvas)
        center_window = center_canvas.create_window((0, 0), window=center_inner, anchor="nw")
        center_canvas._shared_window_id = center_window
        bind_canvas_recursive(center_canvas, center_inner)

        right = ttk.LabelFrame(body, text="Nearby / Gaps", style="PhaseSection.TLabelframe")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        right_book = ttk.Notebook(right)
        right_book.grid(row=0, column=0, sticky="nsew")
        neighbors_tab = ttk.Frame(right_book, padding=4)
        gaps_tab = ttk.Frame(right_book, padding=4)
        right_book.add(neighbors_tab, text="Neighbors")
        right_book.add(gaps_tab, text="Gaps")

        neighbors_text = self._make_readonly_text(neighbors_tab)
        neighbors_text.grid(row=0, column=0, sticky="nsew")
        gaps_text = self._make_readonly_text(gaps_tab)
        gaps_text.grid(row=0, column=0, sticky="nsew")
        neighbors_tab.columnconfigure(0, weight=1)
        neighbors_tab.rowconfigure(0, weight=1)
        gaps_tab.columnconfigure(0, weight=1)
        gaps_tab.rowconfigure(0, weight=1)

        notes_text = None
        if role != "machine":
            notes = ttk.LabelFrame(body, text="Light Notes", style="PhaseSection.TLabelframe")
            notes.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))
            notes.columnconfigure(0, weight=1)
            notes.rowconfigure(0, weight=1)
            notes_text = self._make_readonly_text(notes, height=6)
            notes_text.grid(row=0, column=0, sticky="nsew")

        return {
            "listbox": listbox,
            "results_var": results_var,
            "center_inner": center_inner,
            "neighbors_text": neighbors_text,
            "gaps_text": gaps_text,
            "notes_text": notes_text,
        }

    def _build_machine_tab(self, tab: ttk.Frame) -> dict:
        widgets = self._build_phase_tab(tab, role="machine")
        return widgets

    def _make_readonly_text(self, parent, height: int = 18) -> tk.Text:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        box = tk.Text(parent, wrap="word", height=height)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        bind_y_scroll(box)
        return box

    # ------------------------------------------------------------------
    # Data load / selection
    # ------------------------------------------------------------------

    def _refresh_records(self) -> None:
        root_path, root_shape, warnings = find_projectpack_root(__file__, page_folder=self.page_folder, chosen_root=self._chosen_root)
        self._scan_result = scan_page_index(root_path, self.INDEX_NAME, alias_map=self.INDEX_ALIASES)
        records = list(self._scan_result.records)
        for rec in records:
            rec.display_name = self._derive_display_name(rec)
        records.sort(key=lambda r: (r.display_name or "").lower())
        self._records = records
        self._record_lookup = {rec.record_key: rec for rec in records}
        self._pair_count = sum(1 for rec in records if rec.source_state == "paired")
        self._record_count = len(records)
        self._alias_mode = bool(self._scan_result.active_index_name and self._scan_result.active_index_name != self.INDEX_NAME)

        root_label = str(root_path) if root_path else "(not found)"
        self.root_display_var.set(f"Project pack root: {root_label}")
        active_index = self._scan_result.active_index_name or self.INDEX_NAME
        json_dir = str(self._scan_result.json_dir) if self._scan_result.json_dir else "(missing)"
        md_dir = str(self._scan_result.md_dir) if self._scan_result.md_dir else "(missing)"
        self.index_display_var.set(f"Index: {active_index} | md: {md_dir} | json: {json_dir}")
        self.scan_status_var.set(
            f"root: {'found' if root_path else 'missing'} | index: {'found' if self._scan_result.active_index_name else 'missing'} | records: {self._record_count} | pairs: {self._pair_count} | alias mode: {'on' if self._alias_mode else 'off'}"
        )

        problems = list(warnings) + list(self._scan_result.warnings)
        if problems:
            self.status_var.set(" | ".join(problems))
        elif records:
            self.status_var.set(f"Loaded {len(records)} phase record(s).")
        else:
            self.status_var.set("No phase records found yet.")

        self._render_all_tabs()

    def _build_filtered_records(self) -> list[LoadedRecord]:
        needle = self.search_var.get().strip().lower()
        mode = self.filter_var.get().strip() or "All"
        out: list[LoadedRecord] = []
        for rec in self._records:
            if mode == "Paired" and rec.source_state != "paired":
                continue
            if mode == "Markdown Only" and rec.source_state != "md_only":
                continue
            if mode == "JSON Only" and rec.source_state != "json_only":
                continue
            blob = "\n".join([
                rec.display_name or "",
                rec.rel_stem or "",
                json.dumps(rec.md_meta or {}, ensure_ascii=False),
                rec.md_body or "",
                json.dumps(rec.json_data or {}, ensure_ascii=False),
            ]).lower()
            if needle and needle not in blob:
                continue
            out.append(rec)
        return out

    def _render_all_tabs(self) -> None:
        records = self._build_filtered_records()
        self._item_display_names = []
        self._display_lookup = {}
        for idx, rec in enumerate(records):
            display = rec.display_name or f"Item {idx + 1}"
            if display in self._display_lookup:
                display = f"{display} [{rec.rel_stem}]"
            self._display_lookup[display] = rec.record_key
            self._item_display_names.append(display)

        for listbox in self._listboxes:
            listbox.delete(0, "end")
            for display in self._item_display_names:
                listbox.insert("end", display)
        for label in self._result_labels:
            label.set(f"{len(self._item_display_names)} items")

        if not records:
            self._current_record = None
            self._clear_rendered_views()
            return

        if self._current_record and self._current_record.record_key in {r.record_key for r in records}:
            target_key = self._current_record.record_key
        else:
            target_key = records[0].record_key
        self._select_record_by_key(target_key)

    def _on_search_changed(self, _event=None) -> None:
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(120, self._render_all_tabs)

    def _on_item_selected(self, event=None) -> None:
        widget = event.widget if event is not None else None
        if widget is None:
            return
        selection = widget.curselection()
        if not selection:
            return
        idx = int(selection[0])
        if idx >= len(self._item_display_names):
            return
        display = self._item_display_names[idx]
        record_key = self._display_lookup.get(display)
        if record_key:
            self._select_record_by_key(record_key)

    def _select_record_by_key(self, record_key: str) -> None:
        rec = self._record_lookup.get(record_key)
        if not rec:
            return
        self._current_record = rec
        for listbox in self._listboxes:
            try:
                idx = self._item_display_names.index(next(name for name, key in self._display_lookup.items() if key == record_key))
            except Exception:
                continue
            listbox.selection_clear(0, "end")
            listbox.selection_set(idx)
            listbox.see(idx)
        self._render_record(rec)

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_record(self, rec: LoadedRecord) -> None:
        user_sections = self._build_user_sections(rec)
        pro_sections = self._build_pro_sections(rec)
        machine_sections = self._build_machine_sections(rec)
        neighbor_text = self._build_neighbors_text(rec)
        gap_text = self._build_gaps_text(rec)
        note_text = self._build_notes_text(rec)

        self._render_section_cards(self._user_widgets["center_inner"], user_sections)
        self._render_section_cards(self._pro_widgets["center_inner"], pro_sections)
        self._render_machine_sections(self._machine_widgets["center_inner"], machine_sections)

        self._set_text(self._user_widgets["neighbors_text"], neighbor_text)
        self._set_text(self._pro_widgets["neighbors_text"], neighbor_text)
        self._set_text(self._machine_widgets["neighbors_text"], neighbor_text)
        self._set_text(self._user_widgets["gaps_text"], gap_text)
        self._set_text(self._pro_widgets["gaps_text"], gap_text)
        self._set_text(self._machine_widgets["gaps_text"], gap_text)

        if self._user_widgets["notes_text"] is not None:
            self._set_text(self._user_widgets["notes_text"], note_text)
        if self._pro_widgets["notes_text"] is not None:
            self._set_text(self._pro_widgets["notes_text"], note_text)

    def _clear_rendered_views(self) -> None:
        for widgets in (self._user_widgets, self._pro_widgets, self._machine_widgets):
            self._clear_container(widgets["center_inner"])
            self._set_text(widgets["neighbors_text"], "")
            self._set_text(widgets["gaps_text"], "")
            if widgets.get("notes_text") is not None:
                self._set_text(widgets["notes_text"], "")

    def _render_section_cards(self, parent, sections: list[tuple[str, str]]) -> None:
        self._clear_container(parent)
        if not sections:
            ttk.Label(parent, text="No structured content found.", style="PhaseHint.TLabel").grid(row=0, column=0, sticky="w")
            return
        parent.columnconfigure(0, weight=1)
        for row, (title, body) in enumerate(sections):
            frame = ttk.LabelFrame(parent, text=title, style="PhaseSection.TLabelframe")
            frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            frame.columnconfigure(0, weight=1)
            text = tk.Text(frame, wrap="word", height=_best_height(body), relief="flat")
            text.grid(row=0, column=0, sticky="ew")
            bind_y_scroll(text)
            self._set_text(text, body)

    def _render_machine_sections(self, parent, sections: list[tuple[str, str]]) -> None:
        self._clear_container(parent)
        if not sections:
            ttk.Label(parent, text="No JSON data available.", style="PhaseHint.TLabel").grid(row=0, column=0, sticky="w")
            return
        parent.columnconfigure(0, weight=1)
        for row, (title, body) in enumerate(sections):
            frame = ttk.LabelFrame(parent, text=title, style="PhaseSection.TLabelframe")
            frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            frame.columnconfigure(0, weight=1)
            text = tk.Text(frame, wrap="word", height=_best_height(body), relief="flat")
            text.grid(row=0, column=0, sticky="ew")
            bind_y_scroll(text)
            self._set_text(text, body)

    # ------------------------------------------------------------------
    # Page-specific content builders
    # ------------------------------------------------------------------

    def _build_user_sections(self, rec: LoadedRecord) -> list[tuple[str, str]]:
        fields = self._combined_fields(rec)
        sections: list[tuple[str, str]] = []
        summary_parts = []
        for key in self.SUMMARY_KEYS:
            if key in fields and _texty(fields[key]):
                summary_parts.append(f"{self._labelize_key(key)}: {_stringify(fields[key])}")
        if summary_parts:
            sections.append(("Summary", "\n".join(summary_parts)))

        hierarchy = self._collect_group(fields, ["belongs_to", "parent", "children", "phase", "stage", "bucket", "area"])
        if hierarchy:
            sections.append(("Belonging", hierarchy))

        sequence = self._collect_group(fields, self.SEQUENCE_KEYS)
        if sequence:
            sections.append(("Before / After", sequence))

        related = self._collect_group(fields, ["nearby_related", "related_to", "supports", "used_with", "cross_use", "future_use"])
        if related:
            sections.append(("Nearby / Future Use", related))

        if not sections:
            body = rec.md_body.strip() or "No markdown summary available yet."
            sections.append(("Readable View", body))
        return sections

    def _build_pro_sections(self, rec: LoadedRecord) -> list[tuple[str, str]]:
        fields = self._combined_fields(rec)
        lines = [ln.rstrip() for ln in (rec.md_raw or "").splitlines()]
        clean_fields: list[str] = []
        dirty_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if _looks_clean_marker(stripped):
                clean_fields.append(stripped)
                continue
            pair = _split_clean_field(stripped)
            if pair is not None:
                clean_fields.append(f"{pair[0]}: {pair[1]}")
            else:
                dirty_lines.append(line)

        sections: list[tuple[str, str]] = []
        hierarchy = self._collect_group(fields, ["belongs_to", "parent", "children", "phase", "stage", "bucket", "area"])
        if hierarchy:
            sections.append(("Hierarchy / Belonging", hierarchy))
        sequence = self._collect_group(fields, self.SEQUENCE_KEYS)
        if sequence:
            sections.append(("Before / After", sequence))
        nearby = self._collect_group(fields, self.NEIGHBOR_KEYS)
        if nearby:
            sections.append(("Nearby / Cross Use", nearby))
        if clean_fields:
            sections.append(("Clean Structured Items", "\n".join(clean_fields)))
        if dirty_lines:
            sections.append(("Dirty / Support Content", "\n".join(dirty_lines).strip()))
        if not sections:
            sections.append(("Readable View", rec.md_body.strip() or "No markdown content found."))
        return sections

    def _build_machine_sections(self, rec: LoadedRecord) -> list[tuple[str, str]]:
        data = rec.json_data or {}
        if not data:
            return [("Info", "No JSON data available.")]
        sections: list[tuple[str, str]] = []
        hierarchy = self._collect_group(data, ["belongs_to", "parent", "children", "phase", "stage", "bucket", "area"])
        if hierarchy:
            sections.append(("Hierarchy", hierarchy))
        sequence = self._collect_group(data, self.SEQUENCE_KEYS)
        if sequence:
            sections.append(("Before / After", sequence))
        for key in sorted(data.keys(), key=lambda x: str(x).lower()):
            if key in self.META_SKIP_KEYS or key in {*("belongs_to", "parent", "children", "phase", "stage", "bucket", "area"), *self.SEQUENCE_KEYS}:
                continue
            sections.append((self._labelize_key(str(key)), _stringify(data.get(key))))
        return sections

    def _build_neighbors_text(self, rec: LoadedRecord) -> str:
        fields = self._combined_fields(rec)
        parts = []
        blocks = [
            ("Belongs To", ["belongs_to", "parent", "phase", "stage"]),
            ("Nearby Related", ["nearby_related", "related_to", "children"]),
            ("Cross Use / Support", ["supports", "supported_by", "used_with", "cross_use", "future_use"]),
        ]
        for title, keys in blocks:
            body = self._collect_group(fields, keys)
            if body:
                parts.append(f"{title}\n{'-' * len(title)}\n{body}")
        return "\n\n".join(parts) if parts else "No nearby/relationship links found yet."

    def _build_gaps_text(self, rec: LoadedRecord) -> str:
        fields = self._combined_fields(rec)
        gap = self._collect_group(fields, self.GAP_KEYS)
        if gap:
            return gap
        sequence = self._collect_group(fields, ["missing_predecessor", "missing_successor", "open_questions"])
        if sequence:
            return sequence
        return "No explicit gaps recorded yet."

    def _build_notes_text(self, rec: LoadedRecord) -> str:
        fields = self._combined_fields(rec)
        note_parts = []
        for key in ("notes", "summary", "what_it_is", "description"):
            if key in fields and _texty(fields[key]):
                note_parts.append(f"{self._labelize_key(key)}: {_stringify(fields[key])}")
        if rec.md_body.strip():
            body = rec.md_body.strip()
            if len(body) > 1600:
                body = body[:1600].rstrip() + "\n..."
            note_parts.append(body)
        return "\n\n".join(note_parts) if note_parts else "No notes yet."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _combined_fields(self, rec: LoadedRecord) -> dict[str, object]:
        out: dict[str, object] = {}
        for source in (rec.json_data or {}, rec.md_meta or {}):
            if isinstance(source, dict):
                for key, value in source.items():
                    norm = self._normalize_key(key)
                    if norm not in out and value not in (None, "", [], {}):
                        out[norm] = value
        return out

    def _collect_group(self, fields: dict[str, object], keys: list[str]) -> str:
        parts = []
        seen = set()
        for key in keys:
            norm = self._normalize_key(key)
            if norm in seen:
                continue
            seen.add(norm)
            if norm not in fields:
                continue
            val = fields[norm]
            text = _stringify(val).strip()
            if text:
                parts.append(f"{self._labelize_key(norm)}: {text}")
        return "\n".join(parts)

    def _derive_display_name(self, rec: LoadedRecord) -> str:
        for source in (rec.md_meta or {}, rec.json_data or {}):
            if not isinstance(source, dict):
                continue
            title = source.get("title") or source.get("name") or source.get("phase") or source.get("stage")
            text = _texty(title)
            if text:
                return text
            for key, value in source.items():
                key_text = str(key).lower()
                if any(word in key_text for word in ("phase", "stage", "name", "title", "summary", "topic")):
                    text = _texty(value)
                    if text:
                        return text
        return Path(rec.rel_stem).name

    def _clear_container(self, widget) -> None:
        for child in widget.winfo_children():
            child.destroy()

    def _labelize_key(self, key: str) -> str:
        text = str(key).strip().replace("_", " ").replace("-", " ")
        return " ".join(part.capitalize() if part else "" for part in text.split()) or "Field"

    def _normalize_key(self, key: str) -> str:
        return str(key).strip().lower().replace(" ", "_").replace("/", "_")

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")


def _best_height(text: str) -> int:
    lines = max(4, min(18, len(str(text).splitlines()) + 1))
    return lines


def _texty(value) -> str:
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text
    return ""


def _stringify(value) -> str:
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_stringify(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(f"- {_stringify(v)}" for v in value)
    return str(value)


def _split_clean_field(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key or not value or key.startswith("#"):
        return None
    return key, value


def _looks_clean_marker(text: str) -> bool:
    return text.startswith("<<") and text.endswith(">>") and len(text) >= 4

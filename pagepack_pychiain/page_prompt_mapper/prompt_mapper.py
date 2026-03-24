"""
page_prompt_mapper / prompt_mapper.py
────────────────────────────────────────────────────────────────────────────────
Prompt Mapper page for pychiain.

Purpose:
  1. Build map templates — reusable slot structures, often half-filled with
     defaults or <<placeholder>> markers for future hotkey/widget/macro use.
  2. Build mapped prompt records — usable filled structures saved from templates.

Status semantics (this phase):
  Status controls trust/preference, NOT basic usability.
  - active   = verified, current, preferred
  - archived = older but still valid/readable
  - draft / review / copied / tuning = work-in-progress, still usable if
    minimum extractable fields exist

  Status file routing:
  - draft / review / copied / tuning → primary source is .md
  - active / archived                → primary source is .json

Slot safety:
  Slots ONLY control the map structure and output ordering.
  Slots NEVER write to, edit, or damage the source prompt records they pull from.
  Resolution is read-only: open source prompt .json, read one field, close.

Storage roots:
  Templates : promptlibrary/promptmapper/json/maps/templates/
              promptlibrary/promptmapper/md/maps/templates/
  Active    : promptlibrary/promptmapper/json/maps/active/
              promptlibrary/promptmapper/md/maps/active/

Tabs:
  1. Easy View    — slot list + simple slot editor
  2. Pro View     — slot list + full slot controls + assembled preview
  3. Machine View — field-targeted JSON editing (adaptive editor, not raw dump)

Scroll handling is Linux/Debian/Wayland-safe: each scrollable widget binds
its own Button-4/Button-5/MouseWheel and returns "break".
"""

import os
import json
import uuid
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ─────────────────────────────────────────────────────────────────────────────
# Field definitions for map records
# ─────────────────────────────────────────────────────────────────────────────

# (key, display_label, widget_type, is_multiline)
_MAP_FIELD_DEFS = [
    ("file_name",         "File Name",         "entry",      False),
    ("status",            "Status",            "combo",      False),
    ("map_type",          "Map Type",          "combo_type", False),
    ("tags",              "Tags",              "entry",      False),
    ("notes",             "Notes",             "text",       True),
    ("source_template",   "Source Template",   "entry",      False),
    ("created_on",        "Created On",        "entry_ro",   False),
    ("last_modified",     "Last Modified",     "entry_ro",   False),
    ("internal_id",       "Internal ID",       "entry_ro",   False),
]

MAP_FIELD_BY_KEY = {d[0]: d for d in _MAP_FIELD_DEFS}

STATUS_VALUES    = ["", "draft", "review", "copied", "tuning", "active", "archived"]
MAP_TYPE_VALUES  = ["template", "active"]

_KNOWN_PLACEHOLDERS = ["<<user_input>>", "<<user_upload>>", "<<adjustable>>"]


def _bind_scroll(widget):
    def _handler(event):
        if event.num == 4:
            widget.yview_scroll(-1, "units")
        elif event.num == 5:
            widget.yview_scroll(1, "units")
        elif event.delta:
            widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"
    widget.bind("<MouseWheel>", _handler, add=False)
    widget.bind("<Button-4>",   _handler, add=False)
    widget.bind("<Button-5>",   _handler, add=False)


def _make_listbox_panel(parent, title, height=7):
    outer = ttk.LabelFrame(parent, text=title, padding=(2, 2))
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)
    lb = tk.Listbox(outer, height=height, selectmode="single",
                    activestyle="dotbox", exportselection=False)
    sb = ttk.Scrollbar(outer, orient="vertical", command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    _bind_scroll(lb)
    return outer, lb


def _new_slot(slot_type="prompt"):
    return {
        "slot_id":          str(uuid.uuid4()),
        "slot_type":        slot_type,
        "prompt_ref":       "",
        "pull_field":       "prompt_body",
        "header_text":      "",
        "resolved_content": "",
    }


PULL_FIELD_OPTIONS = [
    "prompt_body", "notes", "detailed_intent", "observed_behavior",
    "tags", "full_name_description", "marker_header_notes",
]


class PagePromptMapper:
    """
    Prompt Mapper page for pychiain.

    Shell contract (matches prompt editor pattern):
        page = PagePromptMapper(parent_widget)
        page.build(parent)   # or .mount() / .create_widgets() / .render()
    """

    PAGE_NAME = "prompt_mapper"

    def __init__(self, parent, app=None, page_key="", page_folder="", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)
        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        self.pack_root      = ""
        self.mapper_root    = ""
        self.json_templates = ""
        self.json_active    = ""
        self.md_templates   = ""
        self.md_active      = ""
        self.prompt_json_root = ""

        self.current_json_path = ""
        self.current_md_path   = ""
        self.field_data        = self._empty_record()
        self.slots             = []

        self._c1_sel = ""
        self._c2_sel = ""
        self._active_tab = "easy"

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)

        self._build_top_bar()
        self._build_chooser_area()
        self._build_notebook()
        self._build_bottom_bar()

        self.frame.after(250, self._auto_find_root)

    # ── Shell mount methods ──────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy()
                self.parent = container
                self.frame = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1)
                self.frame.rowconfigure(2, weight=1)
                self._build_top_bar()
                self._build_chooser_area()
                self._build_notebook()
                self._build_bottom_bar()
                self.frame.after(50, self._auto_find_root)
        except Exception:
            pass
        try:
            self.frame.pack(fill="both", expand=True)
        except Exception:
            try:
                self.frame.grid(row=0, column=0, sticky="nsew")
            except Exception:
                pass
        return self.frame

    def build(self, parent=None):
        return self._embed_into_parent(parent)
    def create_widgets(self, parent=None):
        return self._embed_into_parent(parent)
    def mount(self, parent=None):
        return self._embed_into_parent(parent)
    def render(self, parent=None):
        return self._embed_into_parent(parent)

    def _empty_record(self):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        return {
            "file_name": "", "status": "draft", "map_type": "template",
            "tags": "", "notes": "", "source_template": "", "slots": [],
            "created_on": now, "last_modified": now,
            "internal_id": str(uuid.uuid4()),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # TOP BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_top_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 4))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(99, weight=1)

        def _btn(col, text, cmd, width=None):
            w = width or (len(text) + 2)
            ttk.Button(bar, text=text, command=cmd, width=w).grid(
                row=0, column=col, padx=2, pady=2, sticky="w")

        _btn(0, "Auto-Find Root",      self._auto_find_root,  width=16)
        _btn(1, "Choose Root\u2026",   self._choose_root,     width=14)
        _btn(2, "Reload",              self._reload,          width=9)
        _btn(3, "New Map",             self._new_record,      width=10)
        _btn(4, "Save Template",       self._save_template,   width=14)
        _btn(5, "Save Mapped Record",  self._save_active,     width=18)
        _btn(6, "Search\u2026",        self._search_prompts,  width=10)

        ttk.Separator(bar, orient="vertical").grid(
            row=0, column=7, sticky="ns", padx=6)

        self._path_var = tk.StringVar(
            value="No root set  \u2014  use Auto-Find Root or Choose Root\u2026")
        ttk.Label(bar, textvariable=self._path_var,
                  anchor="w", foreground="#555",
                  font=("", 9)).grid(row=0, column=99, sticky="ew", padx=4)

    # ═════════════════════════════════════════════════════════════════════════
    # CHOOSER
    # ═════════════════════════════════════════════════════════════════════════

    def _build_chooser_area(self):
        cf = ttk.LabelFrame(self.frame, text="Browse Maps", padding=(4, 4))
        cf.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 2))
        cf.columnconfigure(0, weight=1)
        cf.columnconfigure(1, weight=1)
        cf.columnconfigure(2, weight=1)
        self._chooser_lbs  = []
        self._chooser_data = [[], [], []]
        for i, label in enumerate(["\u2460 Maps Root", "\u2461 Subcategory", "\u2462 Items"]):
            panel, lb = _make_listbox_panel(cf, "  " + label, height=5)
            panel.grid(row=0, column=i, sticky="nsew", padx=3, pady=2)
            self._chooser_lbs.append(lb)
        self._chooser_lbs[0].bind("<<ListboxSelect>>", self._on_c1_select)
        self._chooser_lbs[1].bind("<<ListboxSelect>>", self._on_c2_select)
        self._chooser_lbs[2].bind("<<ListboxSelect>>", self._on_c3_select)
        self._rec_label_var = tk.StringVar(value="No map loaded.")
        ttk.Label(cf, textvariable=self._rec_label_var,
                  anchor="w", foreground="#226",
                  font=("", 9)).grid(row=1, column=0, columnspan=3,
                                     sticky="ew", padx=6, pady=(2, 1))

    def _chooser_populate(self, idx, items):
        lb = self._chooser_lbs[idx]
        lb.delete(0, "end")
        for item in items:
            lb.insert("end", item)
        self._chooser_data[idx] = list(items)
        for j in range(idx + 1, 3):
            self._chooser_lbs[j].delete(0, "end")
            self._chooser_data[j] = []

    def _get_browse_base(self):
        return os.path.join(self.mapper_root, "json", "maps") if self.mapper_root else ""

    def _on_c1_select(self, _event=None):
        sel = self._chooser_lbs[0].curselection()
        if not sel: return
        name = self._chooser_data[0][sel[0]]
        self._c1_sel = name; self._c2_sel = ""
        path = os.path.join(self._get_browse_base(), name)
        if os.path.isdir(path):
            self._chooser_populate(1, sorted(os.listdir(path)))
        elif name.endswith(".json") and os.path.isfile(path):
            self._chooser_populate(1, [])
            self._load_json_file(path)

    def _on_c2_select(self, _event=None):
        sel = self._chooser_lbs[1].curselection()
        if not sel: return
        name = self._chooser_data[1][sel[0]]
        self._c2_sel = name
        path = os.path.join(self._get_browse_base(), self._c1_sel, name)
        if os.path.isdir(path):
            self._chooser_populate(2, sorted(os.listdir(path)))
        elif name.endswith(".json") and os.path.isfile(path):
            self._chooser_populate(2, [])
            self._load_json_file(path)

    def _on_c3_select(self, _event=None):
        sel = self._chooser_lbs[2].curselection()
        if not sel: return
        name = self._chooser_data[2][sel[0]]
        path = os.path.join(self._get_browse_base(), self._c1_sel, self._c2_sel, name)
        if name.endswith(".json") and os.path.isfile(path):
            self._load_json_file(path)

    # ═════════════════════════════════════════════════════════════════════════
    # NOTEBOOK
    # ═════════════════════════════════════════════════════════════════════════

    def _build_notebook(self):
        self._nb = ttk.Notebook(self.frame)
        self._nb.grid(row=2, column=0, sticky="nsew", padx=6, pady=2)
        self._tab_frames = {}
        for key, label in [("easy","  Easy View  "),("pro","  Pro View  "),("machine","  Machine View  ")]:
            f = ttk.Frame(self._nb); f.columnconfigure(0, weight=1); f.rowconfigure(0, weight=1)
            self._nb.add(f, text=label); self._tab_frames[key] = f
        self._build_easy_view()
        self._build_pro_view()
        self._build_machine_view()
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, _event=None):
        tab_keys = ["easy", "pro", "machine"]
        idx = self._nb.index("current")
        if 0 <= idx < len(tab_keys):
            old = self._active_tab; new = tab_keys[idx]
            if old != new:
                self._sync_from_active_tab(old)
                self._active_tab = new
                self._populate_active_tab(new)

    # ═════════════════════════════════════════════════════════════════════════
    # EASY VIEW
    # ═════════════════════════════════════════════════════════════════════════

    def _build_easy_view(self):
        parent = self._tab_frames["easy"]
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(pw, width=260); left.columnconfigure(0, weight=1); left.rowconfigure(1, weight=1)
        pw.add(left, weight=0)
        ttk.Label(left, text="Slots", font=("", 9, "bold"), padding=(4,4,0,0)).grid(row=0, column=0, sticky="w")
        self._easy_slot_lb = tk.Listbox(left, selectmode="single", activestyle="dotbox",
            exportselection=False, width=30, relief="flat", borderwidth=1)
        esb = ttk.Scrollbar(left, orient="vertical", command=self._easy_slot_lb.yview)
        self._easy_slot_lb.configure(yscrollcommand=esb.set)
        self._easy_slot_lb.grid(row=1, column=0, sticky="nsew", padx=(4,0), pady=(2,2))
        esb.grid(row=1, column=1, sticky="ns", pady=(2,2))
        _bind_scroll(self._easy_slot_lb)
        self._easy_slot_lb.bind("<<ListboxSelect>>", self._on_easy_slot_select)
        ctrl = ttk.Frame(left, padding=(4,2))
        ctrl.grid(row=2, column=0, columnspan=2, sticky="ew")
        for text, cmd in [
            ("+ Prompt Slot", lambda: self._add_slot("prompt")),
            ("+ Header Slot", lambda: self._add_slot("header")),
            ("Move Up", self._move_slot_up), ("Move Down", self._move_slot_down),
            ("Remove Slot", self._remove_slot),
        ]:
            ttk.Button(ctrl, text=text, command=cmd).pack(fill="x", pady=1)

        right = ttk.Frame(pw, padding=(8,4)); right.columnconfigure(0, weight=1)
        pw.add(right, weight=1)
        self._easy_slot_idx = -1
        self._easy_type_var = tk.StringVar(value="Select a slot on the left")
        ttk.Label(right, textvariable=self._easy_type_var, font=("",10,"bold")).grid(row=0, column=0, sticky="w", pady=(0,4))
        ttk.Separator(right, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=(0,6))

        prf = ttk.Frame(right); prf.grid(row=2, column=0, sticky="ew", pady=2); prf.columnconfigure(1, weight=1)
        ttk.Label(prf, text="Prompt:").grid(row=0, column=0, sticky="w", padx=(0,6))
        self._easy_prompt_var = tk.StringVar()
        ttk.Entry(prf, textvariable=self._easy_prompt_var, width=40).grid(row=0, column=1, sticky="ew")
        ttk.Button(prf, text="Browse\u2026", width=9, command=self._browse_prompt_for_slot).grid(row=0, column=2, padx=(4,0))

        pff = ttk.Frame(right); pff.grid(row=3, column=0, sticky="ew", pady=2); pff.columnconfigure(1, weight=1)
        ttk.Label(pff, text="Pull Field:").grid(row=0, column=0, sticky="w", padx=(0,6))
        self._easy_field_var = tk.StringVar(value="prompt_body")
        ttk.Combobox(pff, textvariable=self._easy_field_var, values=PULL_FIELD_OPTIONS, width=28, state="readonly").grid(row=0, column=1, sticky="w")

        hhf = ttk.Frame(right); hhf.grid(row=4, column=0, sticky="ew", pady=2); hhf.columnconfigure(1, weight=1)
        ttk.Label(hhf, text="Header:").grid(row=0, column=0, sticky="w", padx=(0,6))
        self._easy_header_var = tk.StringVar()
        ttk.Entry(hhf, textvariable=self._easy_header_var, width=50).grid(row=0, column=1, sticky="ew")

        ttk.Separator(right, orient="horizontal").grid(row=5, column=0, sticky="ew", pady=(8,4))

        ttk.Label(right, text="Resolved Content (read-only preview):", font=("",9), foreground="#555").grid(row=6, column=0, sticky="nw")
        prev_f = ttk.Frame(right); prev_f.grid(row=7, column=0, sticky="nsew", pady=(2,4))
        prev_f.columnconfigure(0, weight=1); prev_f.rowconfigure(0, weight=1)
        right.rowconfigure(7, weight=1)
        self._easy_preview = tk.Text(prev_f, wrap="word", height=8, state="disabled",
            font=("Monospace",9), background="#f5f5f0", relief="flat", borderwidth=1)
        epsb = ttk.Scrollbar(prev_f, orient="vertical", command=self._easy_preview.yview)
        self._easy_preview.configure(yscrollcommand=epsb.set)
        self._easy_preview.grid(row=0, column=0, sticky="nsew"); epsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._easy_preview)

        btn_row = ttk.Frame(right); btn_row.grid(row=8, column=0, sticky="w", pady=(4,2))
        ttk.Button(btn_row, text="Apply to Slot", command=self._apply_easy_slot).pack(side="left", padx=(0,6))
        ttk.Button(btn_row, text="Resolve", command=self._resolve_current_slot).pack(side="left")

    def _on_easy_slot_select(self, _event=None):
        sel = self._easy_slot_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx >= len(self.slots): return
        self._apply_easy_slot()
        self._easy_slot_idx = idx
        self._load_slot_into_easy(idx)

    def _load_slot_into_easy(self, idx):
        if idx < 0 or idx >= len(self.slots): return
        slot = self.slots[idx]
        stype = slot.get("slot_type", "prompt")
        self._easy_type_var.set(f"Slot {idx+1}  \u2014  {'Prompt' if stype=='prompt' else 'Header'}")
        self._easy_prompt_var.set(slot.get("prompt_ref", ""))
        self._easy_field_var.set(slot.get("pull_field", "prompt_body"))
        self._easy_header_var.set(slot.get("header_text", ""))
        self._easy_preview.configure(state="normal")
        self._easy_preview.delete("1.0", "end")
        self._easy_preview.insert("1.0", slot.get("resolved_content", ""))
        self._easy_preview.configure(state="disabled")

    def _apply_easy_slot(self):
        idx = self._easy_slot_idx
        if idx < 0 or idx >= len(self.slots): return
        self.slots[idx]["prompt_ref"]  = self._easy_prompt_var.get()
        self.slots[idx]["pull_field"]  = self._easy_field_var.get()
        self.slots[idx]["header_text"] = self._easy_header_var.get()
        self._refresh_slot_lists()

    # ═════════════════════════════════════════════════════════════════════════
    # PRO VIEW
    # ═════════════════════════════════════════════════════════════════════════

    def _build_pro_view(self):
        parent = self._tab_frames["pro"]
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(pw, width=260); left.columnconfigure(0, weight=1); left.rowconfigure(1, weight=1)
        pw.add(left, weight=0)
        ttk.Label(left, text="Slots (Pro)", font=("",9,"bold"), padding=(4,4,0,0)).grid(row=0, column=0, sticky="w")
        self._pro_slot_lb = tk.Listbox(left, selectmode="single", activestyle="dotbox",
            exportselection=False, width=30, relief="flat", borderwidth=1)
        psb = ttk.Scrollbar(left, orient="vertical", command=self._pro_slot_lb.yview)
        self._pro_slot_lb.configure(yscrollcommand=psb.set)
        self._pro_slot_lb.grid(row=1, column=0, sticky="nsew", padx=(4,0), pady=(2,2))
        psb.grid(row=1, column=1, sticky="ns", pady=(2,2))
        _bind_scroll(self._pro_slot_lb)
        self._pro_slot_lb.bind("<<ListboxSelect>>", self._on_pro_slot_select)
        ctrl = ttk.Frame(left, padding=(4,2))
        ctrl.grid(row=2, column=0, columnspan=2, sticky="ew")
        for text, cmd in [
            ("+ Prompt Slot", lambda: self._add_slot("prompt")),
            ("+ Header Slot", lambda: self._add_slot("header")),
            ("Move Up", self._move_slot_up), ("Move Down", self._move_slot_down),
            ("Remove Slot", self._remove_slot), ("Resolve All", self._resolve_all_slots),
        ]:
            ttk.Button(ctrl, text=text, command=cmd).pack(fill="x", pady=1)

        right = ttk.Frame(pw, padding=(8,4)); right.columnconfigure(0, weight=1); right.rowconfigure(5, weight=1)
        pw.add(right, weight=1)
        self._pro_slot_idx = -1

        meta_f = ttk.LabelFrame(right, text="Map Metadata", padding=(6,4))
        meta_f.grid(row=0, column=0, sticky="ew", pady=(0,4)); meta_f.columnconfigure(1, weight=1); meta_f.columnconfigure(3, weight=1)
        ttk.Label(meta_f, text="Name:").grid(row=0, column=0, sticky="w")
        self._pro_name_var = tk.StringVar()
        ttk.Entry(meta_f, textvariable=self._pro_name_var, width=24).grid(row=0, column=1, sticky="ew", padx=(4,12))
        ttk.Label(meta_f, text="Status:").grid(row=0, column=2, sticky="w")
        self._pro_status_var = tk.StringVar(value="draft")
        ttk.Combobox(meta_f, textvariable=self._pro_status_var, values=STATUS_VALUES, width=12, state="normal").grid(row=0, column=3, sticky="w", padx=(4,12))
        ttk.Label(meta_f, text="Type:").grid(row=0, column=4, sticky="w")
        self._pro_type_var = tk.StringVar(value="template")
        ttk.Combobox(meta_f, textvariable=self._pro_type_var, values=MAP_TYPE_VALUES, width=10, state="readonly").grid(row=0, column=5, sticky="w", padx=(4,0))
        ttk.Label(meta_f, text="Tags:").grid(row=1, column=0, sticky="w", pady=(4,0))
        self._pro_tags_var = tk.StringVar()
        ttk.Entry(meta_f, textvariable=self._pro_tags_var, width=40).grid(row=1, column=1, columnspan=5, sticky="ew", padx=(4,0), pady=(4,0))

        detail = ttk.LabelFrame(right, text="Selected Slot", padding=(6,4))
        detail.grid(row=1, column=0, sticky="ew", pady=(4,4)); detail.columnconfigure(1, weight=1)
        self._pro_stype_var = tk.StringVar(value="")
        ttk.Label(detail, textvariable=self._pro_stype_var, font=("",9,"bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,4))
        ttk.Label(detail, text="Prompt:").grid(row=1, column=0, sticky="w")
        self._pro_prompt_var = tk.StringVar()
        ttk.Entry(detail, textvariable=self._pro_prompt_var, width=30).grid(row=1, column=1, sticky="ew", padx=(4,4))
        ttk.Button(detail, text="Browse\u2026", width=9, command=self._browse_prompt_for_slot).grid(row=1, column=2, padx=(0,4))
        ttk.Button(detail, text="Resolve", width=8, command=self._resolve_current_slot).grid(row=1, column=3)
        ttk.Label(detail, text="Pull Field:").grid(row=2, column=0, sticky="w", pady=(4,0))
        self._pro_field_var = tk.StringVar(value="prompt_body")
        ttk.Combobox(detail, textvariable=self._pro_field_var, values=PULL_FIELD_OPTIONS, width=24, state="readonly").grid(row=2, column=1, sticky="w", padx=(4,0), pady=(4,0))
        ttk.Label(detail, text="Header:").grid(row=3, column=0, sticky="w", pady=(4,0))
        self._pro_header_var = tk.StringVar()
        ttk.Entry(detail, textvariable=self._pro_header_var, width=50).grid(row=3, column=1, columnspan=3, sticky="ew", padx=(4,0), pady=(4,0))
        ttk.Button(detail, text="Apply to Slot", command=self._apply_pro_slot).grid(row=4, column=0, sticky="w", pady=(6,0))

        ttk.Label(right, text="Notes:", font=("",9)).grid(row=2, column=0, sticky="w", pady=(4,0))
        nf = ttk.Frame(right); nf.grid(row=3, column=0, sticky="ew", pady=(2,4)); nf.columnconfigure(0, weight=1)
        self._pro_notes = tk.Text(nf, wrap="word", height=3, undo=True, font=("",9))
        nsb = ttk.Scrollbar(nf, orient="vertical", command=self._pro_notes.yview)
        self._pro_notes.configure(yscrollcommand=nsb.set)
        self._pro_notes.grid(row=0, column=0, sticky="ew"); nsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._pro_notes)

        ttk.Label(right, text="Assembled Output Preview:", font=("",9,"bold"), foreground="#335").grid(row=4, column=0, sticky="w", pady=(4,0))
        apf = ttk.Frame(right); apf.grid(row=5, column=0, sticky="nsew", pady=(2,4))
        apf.columnconfigure(0, weight=1); apf.rowconfigure(0, weight=1)
        self._pro_assembled = tk.Text(apf, wrap="word", state="disabled",
            font=("Monospace",9), background="#f5f5f0", relief="flat", borderwidth=1)
        asb = ttk.Scrollbar(apf, orient="vertical", command=self._pro_assembled.yview)
        self._pro_assembled.configure(yscrollcommand=asb.set)
        self._pro_assembled.grid(row=0, column=0, sticky="nsew"); asb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._pro_assembled)

    def _on_pro_slot_select(self, _event=None):
        sel = self._pro_slot_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx >= len(self.slots): return
        self._apply_pro_slot(); self._pro_slot_idx = idx; self._load_slot_into_pro(idx)

    def _load_slot_into_pro(self, idx):
        if idx < 0 or idx >= len(self.slots): return
        slot = self.slots[idx]; stype = slot.get("slot_type", "prompt")
        self._pro_stype_var.set(f"Slot {idx+1}  \u2014  {'Prompt' if stype=='prompt' else 'Header'}")
        self._pro_prompt_var.set(slot.get("prompt_ref", ""))
        self._pro_field_var.set(slot.get("pull_field", "prompt_body"))
        self._pro_header_var.set(slot.get("header_text", ""))

    def _apply_pro_slot(self):
        idx = self._pro_slot_idx
        if idx < 0 or idx >= len(self.slots): return
        self.slots[idx]["prompt_ref"]  = self._pro_prompt_var.get()
        self.slots[idx]["pull_field"]  = self._pro_field_var.get()
        self.slots[idx]["header_text"] = self._pro_header_var.get()
        self._refresh_slot_lists()

    # ═════════════════════════════════════════════════════════════════════════
    # MACHINE VIEW — field-targeted, adaptive editor
    # ═════════════════════════════════════════════════════════════════════════

    def _build_machine_view(self):
        parent = self._tab_frames["machine"]
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        # Left: field list + actions
        left = ttk.Frame(pw, width=200); left.columnconfigure(0, weight=1); left.rowconfigure(1, weight=1)
        pw.add(left, weight=0)
        ttk.Label(left, text="Fields", font=("",9,"bold"), padding=(4,4,0,0)).grid(row=0, column=0, sticky="w")
        self._mv_field_lb = tk.Listbox(left, selectmode="single", activestyle="dotbox",
            exportselection=False, width=22, relief="flat", borderwidth=1)
        mfsb = ttk.Scrollbar(left, orient="vertical", command=self._mv_field_lb.yview)
        self._mv_field_lb.configure(yscrollcommand=mfsb.set)
        self._mv_field_lb.grid(row=1, column=0, sticky="nsew", padx=(4,0), pady=(2,4))
        mfsb.grid(row=1, column=1, sticky="ns", pady=(2,4))
        _bind_scroll(self._mv_field_lb)

        self._mv_field_keys = [d[0] for d in _MAP_FIELD_DEFS] + ["slots"]
        for d in _MAP_FIELD_DEFS:
            self._mv_field_lb.insert("end", "  " + d[1])
        self._mv_field_lb.insert("end", "  Slots (JSON)")
        self._mv_field_lb.bind("<<ListboxSelect>>", self._on_mv_field_select)

        mv_ctrl = ttk.Frame(left, padding=(4,2))
        mv_ctrl.grid(row=2, column=0, columnspan=2, sticky="ew")
        for text, cmd in [("Save", self._mv_save), ("Sync to JSON", self._mv_sync_to_json), ("Regenerate MD", self._mv_regenerate_md)]:
            ttk.Button(mv_ctrl, text=text, command=cmd).pack(fill="x", pady=1)

        # Right: adaptive editor + JSON display
        right = ttk.Frame(pw, padding=(8,4)); right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=0); right.rowconfigure(4, weight=1)
        pw.add(right, weight=1)

        # Header
        hdr = ttk.Frame(right); hdr.grid(row=0, column=0, sticky="ew", pady=(0,2)); hdr.columnconfigure(1, weight=1)
        self._mv_field_label = tk.StringVar(value="Select a field on the left")
        ttk.Label(hdr, textvariable=self._mv_field_label, font=("",10,"bold")).grid(row=0, column=0, sticky="w")
        self._mv_path_var = tk.StringVar(value="")
        ttk.Label(hdr, textvariable=self._mv_path_var, foreground="#558", font=("Monospace",8), anchor="e").grid(row=0, column=1, sticky="e", padx=(12,0))

        ttk.Separator(right, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=(2,4))

        # Adaptive editor area
        editor_f = ttk.Frame(right); editor_f.grid(row=2, column=0, sticky="nsew", pady=(0,4))
        editor_f.columnconfigure(0, weight=1); editor_f.rowconfigure(1, weight=1)

        # Single-line mode
        sl_row = ttk.Frame(editor_f); sl_row.columnconfigure(0, weight=1)
        self._mv_single_var = tk.StringVar()
        self._mv_single_entry = ttk.Entry(sl_row, textvariable=self._mv_single_var, width=60, font=("Monospace",9))
        self._mv_single_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(sl_row, text="Apply", width=8, command=self._mv_apply_field).grid(row=0, column=1, padx=(6,0))
        self._mv_single_row = sl_row

        # Multi-line mode
        ml_frame = ttk.Frame(editor_f); ml_frame.columnconfigure(0, weight=1); ml_frame.rowconfigure(0, weight=1)
        self._mv_multi_txt = tk.Text(ml_frame, wrap="word", height=6, undo=True, font=("Monospace",9), relief="flat", borderwidth=1)
        mmsb = ttk.Scrollbar(ml_frame, orient="vertical", command=self._mv_multi_txt.yview)
        self._mv_multi_txt.configure(yscrollcommand=mmsb.set)
        self._mv_multi_txt.grid(row=0, column=0, sticky="nsew"); mmsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._mv_multi_txt)
        ml_btn = ttk.Frame(ml_frame); ml_btn.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2,0))
        ttk.Button(ml_btn, text="Apply", width=8, command=self._mv_apply_field).pack(side="left")
        ttk.Label(ml_btn, text="  edits apply to this record only \u2014 source prompts are never modified",
                  foreground="#888", font=("",8)).pack(side="left")
        self._mv_multi_frame = ml_frame

        sl_row.grid(row=0, column=0, sticky="ew")
        ml_frame.grid(row=1, column=0, sticky="nsew"); ml_frame.grid_remove()
        self._mv_editor_mode = "single"

        ttk.Separator(right, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=(4,4))

        # JSON display
        jf = ttk.Frame(right); jf.grid(row=4, column=0, sticky="nsew")
        jf.columnconfigure(0, weight=1); jf.rowconfigure(0, weight=1)
        self._mv_json_txt = tk.Text(jf, wrap="none", state="disabled",
            font=("Monospace",9), background="#f5f5f0", relief="flat", borderwidth=1)
        mjy = ttk.Scrollbar(jf, orient="vertical", command=self._mv_json_txt.yview)
        mjx = ttk.Scrollbar(jf, orient="horizontal", command=self._mv_json_txt.xview)
        self._mv_json_txt.configure(yscrollcommand=mjy.set, xscrollcommand=mjx.set)
        self._mv_json_txt.grid(row=0, column=0, sticky="nsew"); mjy.grid(row=0, column=1, sticky="ns"); mjx.grid(row=1, column=0, sticky="ew")
        _bind_scroll(self._mv_json_txt)
        self._mv_active_field = None

    def _mv_show_editor(self, mode):
        if mode == "multi" and self._mv_editor_mode != "multi":
            self._mv_single_row.grid_remove(); self._mv_multi_frame.grid(); self._mv_editor_mode = "multi"
        elif mode == "single" and self._mv_editor_mode != "single":
            self._mv_multi_frame.grid_remove(); self._mv_single_row.grid(); self._mv_editor_mode = "single"

    def _on_mv_field_select(self, _event=None):
        sel = self._mv_field_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx >= len(self._mv_field_keys): return
        fkey = self._mv_field_keys[idx]
        self._mv_active_field = fkey
        fdef = MAP_FIELD_BY_KEY.get(fkey)
        label = fdef[1] if fdef else ("Slots (JSON)" if fkey == "slots" else fkey)
        self._mv_field_label.set(label)

        if fkey == "slots":
            val = json.dumps(self.slots, indent=2, ensure_ascii=False)
        else:
            val = self.field_data.get(fkey, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, indent=2, ensure_ascii=False)
            val = str(val) if val is not None else ""

        is_multi = fkey in ("notes", "slots") or (fdef and fdef[3]) or "\n" in val
        if is_multi:
            self._mv_show_editor("multi")
            self._mv_multi_txt.delete("1.0", "end"); self._mv_multi_txt.insert("1.0", val)
        else:
            self._mv_show_editor("single")
            self._mv_single_var.set(val)
        self._mv_refresh_json()

    def _mv_apply_field(self):
        fkey = self._mv_active_field
        if not fkey: return
        raw = self._mv_multi_txt.get("1.0", "end-1c") if self._mv_editor_mode == "multi" else self._mv_single_var.get()
        if fkey == "slots":
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    self.slots = parsed; self._refresh_slot_lists(); self._set_status("Slots updated from editor.")
                else:
                    messagebox.showwarning("Apply", "Slots must be a JSON array."); return
            except json.JSONDecodeError as e:
                messagebox.showerror("JSON Error", f"Invalid JSON:\n{e}"); return
        else:
            self.field_data[fkey] = raw
        self._mv_refresh_json(); self._set_status(f"Applied: {fkey}")

    def _mv_refresh_json(self):
        data = dict(self.field_data); data["slots"] = self.slots
        txt = self._mv_json_txt; txt.configure(state="normal"); txt.delete("1.0", "end")
        try:
            txt.insert("1.0", json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            txt.insert("1.0", f"(render error: {e})")
        txt.configure(state="disabled")
        txt.tag_remove("highlight", "1.0", "end")
        fkey = self._mv_active_field
        if fkey:
            txt.tag_configure("highlight", background="#fff3c0")
            pos = txt.search(f'"{fkey}"', "1.0", "end")
            if pos:
                line_num = int(pos.split(".")[0])
                txt.tag_add("highlight", f"{line_num}.0", f"{line_num}.end"); txt.see(pos)
        self._mv_path_var.set(self.current_json_path or "(unsaved)")

    def _mv_save(self):
        self._sync_from_active_tab("machine"); self._save_record_to("auto")

    def _mv_sync_to_json(self):
        if not self.current_md_path or not os.path.isfile(self.current_md_path):
            messagebox.showinfo("Sync to JSON", "No .md file found to sync from."); return
        if not messagebox.askyesno("Sync to JSON", "Current .json fields will be replaced with fields from the .md.\nContinue?"):
            return
        if self.current_json_path and os.path.isfile(self.current_json_path):
            self._load_json_file(self.current_json_path)
        self._set_status("Synced from disk.")

    def _mv_regenerate_md(self):
        if not self.current_json_path:
            messagebox.showwarning("Regenerate MD", "Save the record first to establish a file path."); return
        if not messagebox.askyesno("Regenerate MD", "Any existing .md file with the same name will be replaced.\nContinue?"):
            return
        self._sync_from_all_tabs(); self._write_md_mirror(); self._set_status("MD mirror regenerated.")

    # ═════════════════════════════════════════════════════════════════════════
    # BOTTOM BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.frame, padding=(6,2)); bar.grid(row=3, column=0, sticky="ew"); bar.columnconfigure(2, weight=1)
        ttk.Button(bar, text="Save Template", command=self._save_template, width=14).grid(row=0, column=0, padx=(0,4))
        ttk.Button(bar, text="Save Mapped Record", command=self._save_active, width=18).grid(row=0, column=1, padx=(0,10))
        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self._status_var, anchor="w", foreground="#555", font=("",9)).grid(row=0, column=2, sticky="ew")

    def _set_status(self, msg):
        self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # SLOT MANAGEMENT — slots control map structure ONLY, never source prompts
    # ═════════════════════════════════════════════════════════════════════════

    def _add_slot(self, slot_type="prompt"):
        self.slots.append(_new_slot(slot_type)); self._refresh_slot_lists()
        idx = len(self.slots) - 1
        for lb in (self._easy_slot_lb, self._pro_slot_lb):
            lb.selection_clear(0, "end")
            if idx < lb.size(): lb.selection_set(idx); lb.see(idx)
        if self._active_tab == "easy": self._easy_slot_idx = idx; self._load_slot_into_easy(idx)
        elif self._active_tab == "pro": self._pro_slot_idx = idx; self._load_slot_into_pro(idx)
        self._set_status(f"Added {slot_type} slot.")

    def _remove_slot(self):
        idx = self._get_active_slot_idx()
        if idx < 0 or idx >= len(self.slots): messagebox.showinfo("Remove", "Select a slot first."); return
        self.slots.pop(idx); self._easy_slot_idx = -1; self._pro_slot_idx = -1
        self._refresh_slot_lists(); self._set_status("Slot removed.")

    def _move_slot_up(self):
        idx = self._get_active_slot_idx()
        if idx <= 0 or idx >= len(self.slots): return
        self.slots[idx], self.slots[idx-1] = self.slots[idx-1], self.slots[idx]
        self._refresh_slot_lists(); self._select_slot(idx-1)

    def _move_slot_down(self):
        idx = self._get_active_slot_idx()
        if idx < 0 or idx >= len(self.slots)-1: return
        self.slots[idx], self.slots[idx+1] = self.slots[idx+1], self.slots[idx]
        self._refresh_slot_lists(); self._select_slot(idx+1)

    def _get_active_slot_idx(self):
        if self._active_tab == "easy":
            sel = self._easy_slot_lb.curselection(); return sel[0] if sel else -1
        elif self._active_tab == "pro":
            sel = self._pro_slot_lb.curselection(); return sel[0] if sel else -1
        return -1

    def _select_slot(self, idx):
        for lb in (self._easy_slot_lb, self._pro_slot_lb):
            lb.selection_clear(0, "end")
            if idx < lb.size(): lb.selection_set(idx); lb.see(idx)
        if self._active_tab == "easy": self._easy_slot_idx = idx; self._load_slot_into_easy(idx)
        elif self._active_tab == "pro": self._pro_slot_idx = idx; self._load_slot_into_pro(idx)

    def _refresh_slot_lists(self):
        for lb in (self._easy_slot_lb, self._pro_slot_lb):
            prev = lb.curselection(); lb.delete(0, "end")
            for i, slot in enumerate(self.slots):
                stype = slot.get("slot_type", "prompt")
                if stype == "header":
                    htxt = slot.get("header_text", "")
                    if any(p in htxt for p in _KNOWN_PLACEHOLDERS):
                        label = f"  H  {htxt[:28]}  \u27e8\u2026\u27e9"
                    else:
                        label = f"  H  {htxt[:30] or '(header)'}"
                else:
                    ref = slot.get("prompt_ref", "") or "(unassigned)"
                    field = slot.get("pull_field", "prompt_body")
                    resolved = slot.get("resolved_content", "")
                    mark = "\u2713" if resolved else "\u00b7"
                    label = f"  {mark}  {ref}  \u2192  {field}"
                lb.insert("end", label)
            if prev and prev[0] < lb.size(): lb.selection_set(prev[0])
        self._refresh_assembled_preview()

    def _refresh_assembled_preview(self):
        parts = []
        for slot in self.slots:
            stype = slot.get("slot_type", "prompt")
            if stype == "header":
                parts.append(slot.get("header_text", ""))
            else:
                content = slot.get("resolved_content", "")
                if content: parts.append(content)
                else:
                    ref = slot.get("prompt_ref", ""); field = slot.get("pull_field", "prompt_body")
                    parts.append(f"<<{ref}.{field}>>" if ref else "<<unassigned>>")
        self._pro_assembled.configure(state="normal"); self._pro_assembled.delete("1.0", "end")
        self._pro_assembled.insert("1.0", "\n\n".join(parts)); self._pro_assembled.configure(state="disabled")

    # ═════════════════════════════════════════════════════════════════════════
    # PROMPT RESOLUTION — read-only access, never writes to source files
    # ═════════════════════════════════════════════════════════════════════════

    def _resolve_prompt_field(self, prompt_ref, pull_field):
        if not prompt_ref or not self.prompt_json_root: return ""
        prompts_dir = os.path.join(self.prompt_json_root, "prompts")
        if not os.path.isdir(prompts_dir): return ""
        target = prompt_ref.strip()
        target_fname = target if target.endswith(".json") else target + ".json"
        for dirpath, _dirs, files in os.walk(prompts_dir):
            for fname in files:
                if fname == target_fname:
                    try:
                        with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as fh:
                            return str(json.load(fh).get(pull_field, ""))
                    except Exception: return ""
        for dirpath, _dirs, files in os.walk(prompts_dir):
            for fname in files:
                if not fname.endswith(".json"): continue
                try:
                    with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if data.get("file_name", "").strip() == prompt_ref.strip():
                        return str(data.get(pull_field, ""))
                except Exception: pass
        return ""

    def _resolve_current_slot(self):
        idx = self._get_active_slot_idx()
        if idx < 0 or idx >= len(self.slots): messagebox.showinfo("Resolve", "Select a slot first."); return
        if self._active_tab == "pro": self._apply_pro_slot()
        elif self._active_tab == "easy": self._apply_easy_slot()
        slot = self.slots[idx]
        if slot.get("slot_type") == "header": self._set_status("Headers don't need resolution."); return
        ref = slot.get("prompt_ref", ""); field = slot.get("pull_field", "prompt_body")
        content = self._resolve_prompt_field(ref, field); slot["resolved_content"] = content
        self._set_status(f"Resolved: {ref} \u2192 {field} ({len(content)} chars)" if content else f"Could not resolve: {ref}")
        self._refresh_slot_lists()
        if self._active_tab == "easy": self._load_slot_into_easy(idx)
        elif self._active_tab == "pro": self._load_slot_into_pro(idx)

    def _resolve_all_slots(self):
        count = 0
        for slot in self.slots:
            if slot.get("slot_type") == "header": continue
            ref = slot.get("prompt_ref", ""); field = slot.get("pull_field", "prompt_body")
            if ref:
                content = self._resolve_prompt_field(ref, field); slot["resolved_content"] = content
                if content: count += 1
        self._refresh_slot_lists(); self._set_status(f"Resolved {count} slot(s).")

    # ═════════════════════════════════════════════════════════════════════════
    # PROMPT BROWSER — read-only search, never modifies source prompts
    # ═════════════════════════════════════════════════════════════════════════

    def _browse_prompt_for_slot(self):
        if not self.prompt_json_root: messagebox.showwarning("Browse", "Set root first."); return
        prompts_dir = os.path.join(self.prompt_json_root, "prompts")
        if not os.path.isdir(prompts_dir): messagebox.showinfo("Browse", f"No prompts directory:\n{prompts_dir}"); return

        dlg = tk.Toplevel(self.frame); dlg.title("Browse Prompts \u2014 read-only search")
        dlg.geometry("580x440"); dlg.grab_set(); dlg.columnconfigure(0, weight=1); dlg.rowconfigure(2, weight=1)

        qf = ttk.Frame(dlg, padding=(8,8,8,4)); qf.grid(row=0, column=0, sticky="ew"); qf.columnconfigure(1, weight=1)
        ttk.Label(qf, text="Search:").grid(row=0, column=0, sticky="w")
        sq_var = tk.StringVar(); sq_e = ttk.Entry(qf, textvariable=sq_var)
        sq_e.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(qf, text="Go", width=6, command=lambda: _do_search()).grid(row=0, column=2)

        ttk.Label(dlg, text="Double-click or Select to assign \u2014 this does NOT edit the source prompt:",
                  padding=(8,2), foreground="#555", font=("",8)).grid(row=1, column=0, sticky="w")

        lf = ttk.Frame(dlg, padding=(8,0,8,4)); lf.grid(row=2, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)
        res_lb = tk.Listbox(lf, selectmode="single", activestyle="dotbox", exportselection=False, font=("Monospace",9))
        res_sb = ttk.Scrollbar(lf, orient="vertical", command=res_lb.yview)
        res_lb.configure(yscrollcommand=res_sb.set)
        res_lb.grid(row=0, column=0, sticky="nsew"); res_sb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(res_lb)
        result_names = []

        def _do_search():
            q = sq_var.get().strip().lower(); res_lb.delete(0, "end"); result_names.clear()
            for dirpath, _dirs, files in os.walk(prompts_dir):
                for fname in sorted(files):
                    if not fname.endswith(".json"): continue
                    try:
                        with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as fh: data = json.load(fh)
                        name = data.get("file_name", fname.replace(".json",""))
                        tags = data.get("tags",""); status = data.get("status","")
                        if not q or q in f"{name} {tags} {fname} {status}".lower():
                            parts = [f"  {name}"]
                            if status: parts.append(f"[{status}]")
                            if tags: parts.append(f"({tags})")
                            res_lb.insert("end", "  ".join(parts)); result_names.append(name)
                    except Exception: pass
            res_lb.insert("end", f"  \u2500\u2500\u2500 {len(result_names)} prompt(s) \u2500\u2500\u2500")

        def _select(event=None):
            sel = res_lb.curselection()
            if not sel or sel[0] >= len(result_names): return
            chosen = result_names[sel[0]]; dlg.destroy()
            if self._active_tab == "easy": self._easy_prompt_var.set(chosen)
            elif self._active_tab == "pro": self._pro_prompt_var.set(chosen)

        res_lb.bind("<Double-Button-1>", _select)
        bf = ttk.Frame(dlg, padding=(8,4)); bf.grid(row=3, column=0, sticky="ew")
        ttk.Button(bf, text="Select", command=_select).pack(side="left")
        ttk.Button(bf, text="Close", command=dlg.destroy).pack(side="right")
        sq_e.bind("<Return>", lambda _e: _do_search()); sq_e.focus_set(); _do_search()

    def _search_prompts(self):
        self._browse_prompt_for_slot()

    # ═════════════════════════════════════════════════════════════════════════
    # ROOT FINDING
    # ═════════════════════════════════════════════════════════════════════════

    def _auto_find_root(self):
        from pathlib import Path
        page_file = Path(__file__).resolve()
        for candidate in [page_file.parent, *page_file.parents]:
            if (candidate / "promptlibrary").is_dir() or (candidate / "index_pychiain").is_dir() or candidate.name == "pagepack_pychiain":
                self._set_root(str(candidate)); self._set_status(f"Auto-found root: {candidate}"); return
        cwd = os.getcwd(); parts = [p for p in cwd.split(os.sep) if p]
        for i in range(len(parts), 0, -1):
            probe = os.sep + os.path.join(*parts[:i])
            if os.path.isdir(os.path.join(probe, "pagepack_pychiain")):
                self._set_root(os.path.join(probe, "pagepack_pychiain")); return
            if os.path.basename(probe) == "pagepack_pychiain":
                self._set_root(probe); return
        self._set_status("Root not found \u2014 use Choose Root to locate pagepack_pychiain.")

    def _choose_root(self):
        d = filedialog.askdirectory(title="Select pagepack_pychiain directory")
        if d: self._set_root(d)

    def _set_root(self, pack_path):
        self.pack_root = pack_path
        self.mapper_root = os.path.join(pack_path, "promptlibrary", "promptmapper")
        self.json_templates = os.path.join(self.mapper_root, "json", "maps", "templates")
        self.json_active    = os.path.join(self.mapper_root, "json", "maps", "active")
        self.md_templates   = os.path.join(self.mapper_root, "md", "maps", "templates")
        self.md_active      = os.path.join(self.mapper_root, "md", "maps", "active")
        for subpath in [
            os.path.join(pack_path, "promptlibrary", "prompteditor", "json"),
            os.path.join(pack_path, "index_pychiain", "prompteditor", "json"),
            os.path.join(pack_path, "index_pychiain", "prompt_editor", "json"),
        ]:
            if os.path.isdir(subpath): self.prompt_json_root = subpath; break
        else:
            self.prompt_json_root = os.path.join(pack_path, "promptlibrary", "prompteditor", "json")
        short = pack_path if len(pack_path) <= 55 else "\u2026" + pack_path[-52:]
        self._path_var.set(f"Root: {short}"); self._refresh_chooser()

    def _refresh_chooser(self):
        base = self._get_browse_base()
        if not base or not os.path.isdir(base):
            for d in (self.json_templates, self.json_active, self.md_templates, self.md_active):
                os.makedirs(d, exist_ok=True)
        if os.path.isdir(base):
            items = sorted(os.listdir(base)); self._chooser_populate(0, items)
            self._set_status(f"Browsing: {base}  ({len(items)} items)")
        else:
            self._chooser_populate(0, []); self._set_status(f"Maps dir: {base}")

    def _reload(self):
        self._refresh_chooser()
        if self.current_json_path and os.path.isfile(self.current_json_path): self._load_json_file(self.current_json_path)
        self._set_status("Reloaded.")

    # ═════════════════════════════════════════════════════════════════════════
    # RECORD LOAD / SAVE
    # ═════════════════════════════════════════════════════════════════════════

    def _load_json_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh: data = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not load:\n{path}\n\n{exc}"); return
        self.current_json_path = path
        base = self._get_browse_base()
        if base and path.startswith(base):
            rel = os.path.relpath(path, base); stem = os.path.splitext(rel)[0]
            self.current_md_path = os.path.join(self.mapper_root, "md", "maps", stem + ".md")
        else: self.current_md_path = ""
        self.field_data = self._empty_record()
        for k, v in data.items():
            if k == "slots": self.slots = v if isinstance(v, list) else []
            else: self.field_data[k] = v
        self._populate_all_tabs()
        fname = os.path.basename(path)
        self._rec_label_var.set(f"Loaded:  {fname}   |   {path}"); self._set_status(f"Loaded: {fname}")

    def _new_record(self):
        self.field_data = self._empty_record(); self.slots = []
        self.current_json_path = ""; self.current_md_path = ""
        self._populate_all_tabs(); self._rec_label_var.set("New map (unsaved)"); self._set_status("New map ready.")

    def _save_template(self):
        self._sync_from_all_tabs(); self.field_data["map_type"] = "template"; self._save_record_to("template")

    def _save_active(self):
        self._sync_from_all_tabs(); self.field_data["map_type"] = "active"
        self._resolve_all_slots(); self._save_record_to("active")

    def _save_record_to(self, target="auto"):
        self._sync_from_all_tabs()
        if not self.field_data.get("file_name", "").strip():
            messagebox.showwarning("Save", "File Name is required before saving."); return
        if not self.mapper_root: messagebox.showwarning("Save", "No root set."); return
        map_type = target if target != "auto" else self.field_data.get("map_type", "template")
        json_dir = self.json_templates if map_type == "template" else self.json_active
        md_dir   = self.md_templates   if map_type == "template" else self.md_active
        if not self.current_json_path or target != "auto":
            fname = self.field_data["file_name"].strip().replace(" ","_").replace("/","-")
            if not fname.endswith(".json"): fname += ".json"
            os.makedirs(json_dir, exist_ok=True)
            path = filedialog.asksaveasfilename(initialdir=json_dir, initialfile=fname,
                title=f"Save {map_type.title()} \u2014 {fname}", defaultextension=".json",
                filetypes=[("JSON files","*.json"),("All files","*.*")])
            if not path: return
            self.current_json_path = path
            rel = os.path.relpath(path, json_dir); stem = os.path.splitext(rel)[0]
            self.current_md_path = os.path.join(md_dir, stem + ".md")
        self.field_data["last_modified"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_data = dict(self.field_data); save_data["slots"] = self.slots
        try:
            os.makedirs(os.path.dirname(self.current_json_path), exist_ok=True)
            with open(self.current_json_path, "w", encoding="utf-8") as fh:
                json.dump(save_data, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to write JSON:\n{exc}"); return
        self._write_md_mirror()
        fname = os.path.basename(self.current_json_path)
        self._rec_label_var.set(f"Saved:  {fname}   |   {self.current_json_path}")
        self._set_status(f"Saved {map_type}: {fname}"); self._refresh_chooser(); self._mv_refresh_json()

    def _write_md_mirror(self):
        if not self.current_md_path: return
        try:
            os.makedirs(os.path.dirname(self.current_md_path), exist_ok=True)
            with open(self.current_md_path, "w", encoding="utf-8") as fh: fh.write(self._render_md())
        except Exception as exc: self._set_status(f"MD write warning: {exc}")

    def _render_md(self):
        d = self.field_data; lines = []
        lines.append(f"# {d.get('file_name', 'Untitled Map')}\n")
        for label, val in [("Status",d.get("status","")),("Map Type",d.get("map_type","")),
            ("Tags",d.get("tags","")),("Source Template",d.get("source_template","")),
            ("Internal ID",d.get("internal_id","")),("Created On",d.get("created_on","")),
            ("Last Modified",d.get("last_modified",""))]:
            if val: lines.append(f"**{label}:** {val}")
        lines.append("")
        notes = d.get("notes","").strip()
        if notes: lines.append("## Notes\n"); lines.append(notes); lines.append("")
        lines.append("## Slots\n")
        for i, slot in enumerate(self.slots):
            stype = slot.get("slot_type","prompt")
            if stype == "header":
                lines.append(f"### Slot {i+1} \u2014 Header"); lines.append(f"  {slot.get('header_text','')}")
            else:
                lines.append(f"### Slot {i+1} \u2014 Prompt")
                lines.append(f"  **Prompt:** {slot.get('prompt_ref','')}")
                lines.append(f"  **Pull Field:** {slot.get('pull_field','prompt_body')}")
                content = slot.get("resolved_content","")
                if content: lines.append(f"\n{content}")
            lines.append("")
        lines.append("---"); lines.append("*pychiain prompt_mapper record*")
        return "\n".join(lines) + "\n"

    # ═════════════════════════════════════════════════════════════════════════
    # TAB SYNC
    # ═════════════════════════════════════════════════════════════════════════

    def _sync_from_active_tab(self, tab_name):
        if tab_name == "easy": self._apply_easy_slot()
        elif tab_name == "pro":
            self._apply_pro_slot()
            self.field_data["file_name"] = self._pro_name_var.get()
            self.field_data["status"]    = self._pro_status_var.get()
            self.field_data["map_type"]  = self._pro_type_var.get()
            self.field_data["tags"]      = self._pro_tags_var.get()
            self.field_data["notes"]     = self._pro_notes.get("1.0", "end-1c")

    def _sync_from_all_tabs(self):
        for tn in ("easy", "pro", "machine"): self._sync_from_active_tab(tn)

    def _populate_active_tab(self, tab_name):
        if tab_name == "machine":
            self._mv_refresh_json()
            if self._mv_active_field: self._on_mv_field_select()
        elif tab_name == "pro": self._populate_pro_from_data()
        self._refresh_slot_lists()

    def _populate_pro_from_data(self):
        self._pro_name_var.set(self.field_data.get("file_name",""))
        self._pro_status_var.set(self.field_data.get("status","draft"))
        self._pro_type_var.set(self.field_data.get("map_type","template"))
        self._pro_tags_var.set(self.field_data.get("tags",""))
        self._pro_notes.delete("1.0","end"); self._pro_notes.insert("1.0", self.field_data.get("notes",""))

    def _populate_all_tabs(self):
        self._refresh_slot_lists(); self._populate_pro_from_data(); self._mv_refresh_json()
        self._easy_slot_idx = -1; self._pro_slot_idx = -1
        self._easy_type_var.set("Select a slot on the left"); self._pro_stype_var.set("")

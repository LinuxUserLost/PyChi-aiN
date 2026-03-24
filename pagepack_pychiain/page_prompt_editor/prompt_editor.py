"""
page_prompt_editor / prompt_editor.py
────────────────────────────────────────────────────────────────────────────────
Prompt Editor page for pychiain.

Index root  : /guichi/index_pychiain/prompteditor/
MD branch   : .../prompteditor/md/
JSON branch : .../prompteditor/json/

Tabs
  1. Skimmer Designed  – practical body-first working view
  2. Easy View         – fast common fields only
  3. Full View         – all fields + per-field Easy View toggle
  4. Machine View      – read-only raw JSON display

Layout
  [Top action bar]
  [3-window chooser]
  [Tab strip]
  [Lower 2-pane: narrow selector | focused field editor]
  [Bottom status bar]

Scroll handling is Linux/Debian/Wayland-safe: each scrollable widget binds
its own Button-4/Button-5/MouseWheel and returns "break" to stop propagation.
No broad global capture is used.
"""

import os
import json
import uuid
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ─────────────────────────────────────────────────────────────────────────────
# Field definitions
# ─────────────────────────────────────────────────────────────────────────────

# (key, display_label, widget_type)
# widget_type: entry | entry_ro | combo | text | text_ro | check | internal
_FIELD_DEFS_MASTER = [
    ("file_name",                "File Name",                          "entry"),
    ("status",                   "Status",                             "combo"),
    ("tags",                     "Tags",                               "entry"),
    ("notes",                    "Notes",                              "text"),
    ("prompt_body",              "Prompt Body",                        "text"),
    ("full_name_description",    "Full Name / Description",            "entry"),
    ("detailed_intent",          "Detailed Intent / Comparison Notes", "text"),
    ("observed_behavior",        "Observed Behavior / Results",        "text"),
    ("created_on",               "Created On",                         "entry_ro"),
    ("last_modified",            "Last Modified",                      "entry_ro"),
    ("pinned",                   "Pinned",                             "check"),
    ("usage_count",              "Usage Count",                        "entry"),
    ("internal_id",              "Internal ID",                        "entry_ro"),
    ("marker_header_notes",      "Marker / Header Notes",              "text"),
    ("source_parent_group",      "Source / Parent Group",              "entry"),
    ("easy_view_enabled_fields", "Easy View Enabled Fields",           "internal"),
    ("machine_metadata",         "Machine Metadata",                   "text_ro"),
]

# These are mutable so _add_custom_field can extend them at runtime.
FIELD_DEFS: list   = list(_FIELD_DEFS_MASTER)
FIELD_BY_KEY: dict = {d[0]: d for d in FIELD_DEFS}

STATUS_VALUES  = ["", "draft", "active", "archived", "review"]
EASY_DEFAULT   = ["file_name", "tags", "notes", "prompt_body"]
SKIMMER_FIELDS = ["file_name", "status", "tags", "notes", "prompt_body"]

def _all_edit_keys() -> list:
    """Return all field keys that get an editor widget (excludes 'internal')."""
    return [d[0] for d in FIELD_DEFS if d[2] != "internal"]


# ─────────────────────────────────────────────────────────────────────────────
# Scroll helper — Wayland/Linux-safe
# ─────────────────────────────────────────────────────────────────────────────

def _bind_scroll(widget: tk.Widget) -> None:
    """
    Attach scroll events directly to a widget.
    Returns "break" so the event stops here and does NOT bubble to a parent
    scroll capture — avoids the chooser-vs-editor scroll fight on Wayland.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Small helper: labelled Listbox panel
# ─────────────────────────────────────────────────────────────────────────────

def _make_listbox_panel(parent, title: str, height: int = 7):
    """
    Return (outer_frame, listbox).
    outer_frame is a LabelFrame ready to grid/pack.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Main page class
# ─────────────────────────────────────────────────────────────────────────────

class PagePromptEditor:
    """
    Prompt Editor page for pychiain.

    Shell contract  (Guichi loader)
    ───────────────────────────────
    Loader instantiates with:
        page = PagePromptEditor(parent, app, page_key, page_folder)

    ``parent``      -- Tk parent widget
    ``app``         -- live Guichi/pychiain application reference
    ``page_key``    -- string key this page is registered under
    ``page_folder`` -- folder name ("page_prompt_editor")

    The page's root widget is ``page.frame`` -- a ttk.Frame that fills its
    parent.  The loader is responsible for placing/packing/gridding it.
    """

    PAGE_NAME        = "prompt_editor"
    GUICHI_DIRNAME   = "guichi"
    INDEX_SUBPATH    = os.path.join("index_pychiain", "prompteditor")

    # ── Init ─────────────────────────────────────────────────────────────────

    def __init__(self, parent: tk.Widget, app=None, page_key: str="", page_folder: str="", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)
        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # Root paths
        self.guichi_root: str = ""
        self.index_root:  str = ""   # .../index_pychiain/prompteditor/
        self.md_root:     str = ""   # .../prompteditor/md/
        self.json_root:   str = ""   # .../prompteditor/json/

        # Current record state
        self.current_json_path: str = ""
        self.current_md_path:   str = ""
        self.field_data: dict       = self._empty_record()
        self.easy_fields: list      = list(EASY_DEFAULT)

        # Per-tab editor registry
        # tab_name -> {"sel_lb", "editor_frames", "field_get_set", "field_keys", "active_field"}
        self._tab_reg: dict  = {}
        self._active_tab: str = "skimmer"

        # Chooser state
        self._win1_sel: str = ""
        self._win2_sel: str = ""

        # Build
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)   # notebook row expands

        self._build_top_bar()
        self._build_chooser_area()
        self._build_notebook()
        self._build_bottom_bar()

        self.frame.after(250, self._auto_find_root)

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

    # ── Empty record scaffold ────────────────────────────────────────────────

    def _empty_record(self) -> dict:
        now = datetime.datetime.now().isoformat(timespec="seconds")
        return {
            "file_name":                "",
            "status":                   "draft",
            "tags":                     "",
            "notes":                    "",
            "prompt_body":              "",
            "full_name_description":    "",
            "detailed_intent":          "",
            "observed_behavior":        "",
            "created_on":               now,
            "last_modified":            now,
            "pinned":                   False,
            "usage_count":              0,
            "internal_id":              str(uuid.uuid4()),
            "marker_header_notes":      "",
            "source_parent_group":      "",
            "easy_view_enabled_fields": list(EASY_DEFAULT),
            "machine_metadata":         {},
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Top action bar
    # ─────────────────────────────────────────────────────────────────────────

    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self.frame, padding=(4, 4))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(99, weight=1)   # path label column

        def _btn(col, text, cmd, width=None):
            w = width or (len(text) + 2)
            ttk.Button(bar, text=text, command=cmd, width=w).grid(
                row=0, column=col, padx=2, pady=2, sticky="w")

        _btn(0,  "Auto-Find Root",  self._auto_find_root,  width=16)
        _btn(1,  "Choose Root…",    self._choose_root,     width=14)
        _btn(2,  "Reload",          self._reload,          width=9)
        _btn(3,  "New Record",      self._new_record,      width=12)
        _btn(4,  "Load Record",     self._load_record_dlg, width=13)
        _btn(5,  "Save",            self._save_record,     width=7)
        _btn(6,  "Sync MD↔JSON",   self._sync_md_json,    width=13)
        _btn(7,  "Search…",         self._search_records,  width=10)

        ttk.Separator(bar, orient="vertical").grid(
            row=0, column=8, sticky="ns", padx=6)

        self._path_var = tk.StringVar(value="No root set  —  use Auto-Find Root or Choose Root…")
        ttk.Label(bar, textvariable=self._path_var,
                  anchor="w", foreground="#555",
                  font=("", 9)).grid(row=0, column=99, sticky="ew", padx=4)

    # ─────────────────────────────────────────────────────────────────────────
    # 3-window chooser area
    # ─────────────────────────────────────────────────────────────────────────

    def _build_chooser_area(self) -> None:
        cf = ttk.LabelFrame(self.frame, text="Browse Records", padding=(4, 4))
        cf.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 2))
        cf.columnconfigure(0, weight=1)
        cf.columnconfigure(1, weight=1)
        cf.columnconfigure(2, weight=1)

        self._chooser_lbs: list  = []
        self._chooser_data: list = [[], [], []]

        for i, label in enumerate(["  ① Level 1", "  ② Level 2", "  ③ Files"]):
            panel, lb = _make_listbox_panel(cf, label, height=6)
            panel.grid(row=0, column=i, sticky="nsew", padx=3, pady=2)
            self._chooser_lbs.append(lb)

        self._chooser_lbs[0].bind("<<ListboxSelect>>", self._on_c1_select)
        self._chooser_lbs[1].bind("<<ListboxSelect>>", self._on_c2_select)
        self._chooser_lbs[2].bind("<<ListboxSelect>>", self._on_c3_select)

        self._rec_label_var = tk.StringVar(value="No record selected.")
        ttk.Label(cf, textvariable=self._rec_label_var,
                  anchor="w", foreground="#226",
                  font=("", 9)).grid(row=1, column=0, columnspan=3,
                                     sticky="ew", padx=6, pady=(2, 1))

    # Chooser helpers ─────────────────────────────────────────────────────────

    def _chooser_populate(self, idx: int, items: list) -> None:
        lb = self._chooser_lbs[idx]
        lb.delete(0, "end")
        for item in items:
            lb.insert("end", item)
        self._chooser_data[idx] = list(items)
        # Clear downstream windows
        for j in range(idx + 1, 3):
            self._chooser_lbs[j].delete(0, "end")
            self._chooser_data[j] = []

    def _on_c1_select(self, _event=None) -> None:
        sel = self._chooser_lbs[0].curselection()
        if not sel:
            return
        name = self._chooser_data[0][sel[0]]
        self._win1_sel = name
        self._win2_sel = ""
        path = os.path.join(self.json_root, "prompts", name)
        if os.path.isdir(path):
            items = sorted(os.listdir(path))
            self._chooser_populate(1, items)
        elif name.endswith(".json") and os.path.isfile(path):
            self._chooser_populate(1, [])
            self._load_json_file(path)

    def _on_c2_select(self, _event=None) -> None:
        sel = self._chooser_lbs[1].curselection()
        if not sel:
            return
        name = self._chooser_data[1][sel[0]]
        self._win2_sel = name
        path = os.path.join(self.json_root, "prompts", self._win1_sel, name)
        if os.path.isdir(path):
            items = sorted(os.listdir(path))
            self._chooser_populate(2, items)
        elif name.endswith(".json") and os.path.isfile(path):
            self._chooser_populate(2, [])
            self._load_json_file(path)

    def _on_c3_select(self, _event=None) -> None:
        sel = self._chooser_lbs[2].curselection()
        if not sel:
            return
        name = self._chooser_data[2][sel[0]]
        path = os.path.join(
            self.json_root, "prompts",
            self._win1_sel, self._win2_sel, name)
        if name.endswith(".json") and os.path.isfile(path):
            self._load_json_file(path)

    # ─────────────────────────────────────────────────────────────────────────
    # Notebook + tabs
    # ─────────────────────────────────────────────────────────────────────────

    def _build_notebook(self) -> None:
        self._nb = ttk.Notebook(self.frame)
        self._nb.grid(row=2, column=0, sticky="nsew", padx=6, pady=2)

        self._tab_frames: dict = {}
        for key, label in [
            ("skimmer", "  Skimmer Designed  "),
            ("easy",    "  Easy View  "),
            ("full",    "  Full View  "),
            ("machine", "  Machine View  "),
        ]:
            f = ttk.Frame(self._nb)
            f.columnconfigure(0, weight=1)
            f.rowconfigure(0, weight=1)
            self._nb.add(f, text=label)
            self._tab_frames[key] = f

        # Build each tab's interior
        self._build_edit_tab("skimmer", SKIMMER_FIELDS)
        self._build_edit_tab("easy",    EASY_DEFAULT)
        self._build_full_view_tab()
        self._build_machine_view_tab()

        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, _event=None) -> None:
        tab_keys = ["skimmer", "easy", "full", "machine"]
        idx = self._nb.index("current")
        if idx < 0 or idx >= len(tab_keys):
            return
        old = self._active_tab
        new = tab_keys[idx]
        if old != new:
            self._sync_tab_to_data(old)
            self._active_tab = new
            self._populate_tab_from_data(new)

    # ─────────────────────────────────────────────────────────────────────────
    # Generic edit tab builder (Skimmer + Easy)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_edit_tab(self, tab_name: str, field_keys: list) -> None:
        """
        Build a left-selector + right-editor pane inside tab_name's frame.
        field_keys defines which fields are shown and in what order.
        """
        parent = self._tab_frames[tab_name]
        reg: dict = {}
        self._tab_reg[tab_name] = reg

        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        # ── Left selector pane ────────────────────────────────────────────
        left = ttk.Frame(pw, width=190)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        pw.add(left, weight=0)

        ttk.Label(left, text="Fields", font=("", 9, "bold"),
                  padding=(4, 4, 0, 0)).grid(row=0, column=0, sticky="w")

        sel_lb = tk.Listbox(left, selectmode="single",
                            activestyle="dotbox", exportselection=False,
                            width=24, relief="flat", borderwidth=1)
        sel_sb = ttk.Scrollbar(left, orient="vertical", command=sel_lb.yview)
        sel_lb.configure(yscrollcommand=sel_sb.set)
        sel_lb.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=(2, 4))
        sel_sb.grid(row=1, column=1, sticky="ns",   pady=(2, 4))
        _bind_scroll(sel_lb)

        # ── Right editor pane ─────────────────────────────────────────────
        right = ttk.Frame(pw)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        pw.add(right, weight=1)

        editor_frames: dict = {}
        field_get_set: dict = {}
        display_keys  = []

        for fkey in field_keys:
            fdef = FIELD_BY_KEY.get(fkey)
            if not fdef or fdef[2] == "internal":
                continue
            _, flabel, ftype = fdef
            ef, get_fn, set_fn = self._make_field_editor(
                right, fkey, flabel, ftype)
            ef.grid(row=0, column=0, sticky="nsew")
            ef.grid_remove()
            editor_frames[fkey]  = ef
            field_get_set[fkey]  = (get_fn, set_fn)
            display_keys.append(fkey)
            sel_lb.insert("end", "  " + flabel)

        reg.update({
            "sel_lb":        sel_lb,
            "editor_frames": editor_frames,
            "field_get_set": field_get_set,
            "field_keys":    display_keys,
            "active_field":  None,
        })

        # Select first field automatically
        if display_keys:
            sel_lb.selection_set(0)
            editor_frames[display_keys[0]].grid()
            reg["active_field"] = display_keys[0]

        def _on_sel(event, tn=tab_name, fks=display_keys,
                    lb=sel_lb, efr=editor_frames, r=reg):
            s = lb.curselection()
            if not s:
                return
            i   = s[0]
            fk  = fks[i] if i < len(fks) else None
            if not fk:
                return
            for f in efr.values():
                f.grid_remove()
            if fk in efr:
                efr[fk].grid()
            r["active_field"] = fk

        sel_lb.bind("<<ListboxSelect>>", _on_sel)

    # ─────────────────────────────────────────────────────────────────────────
    # Full View tab (all fields + Easy toggle per field)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_full_view_tab(self) -> None:
        tab_name = "full"
        parent   = self._tab_frames[tab_name]
        reg: dict = {}
        self._tab_reg[tab_name] = reg

        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        # ── Left selector pane ────────────────────────────────────────────
        left = ttk.Frame(pw, width=220)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        left.rowconfigure(2, weight=0)
        pw.add(left, weight=0)

        hf = ttk.Frame(left, padding=(4, 4, 0, 0))
        hf.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(hf, text="All Fields", font=("", 9, "bold")).pack(side="left")
        ttk.Label(hf, text="  ● = in Easy View",
                  foreground="#338", font=("", 8)).pack(side="left")

        sel_lb = tk.Listbox(left, selectmode="single",
                            activestyle="dotbox", exportselection=False,
                            width=26, relief="flat", borderwidth=1)
        sel_sb = ttk.Scrollbar(left, orient="vertical", command=sel_lb.yview)
        sel_lb.configure(yscrollcommand=sel_sb.set)
        sel_lb.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=(2, 2))
        sel_sb.grid(row=1, column=1, sticky="ns",   pady=(2, 2))
        _bind_scroll(sel_lb)
        self._full_view_lb = sel_lb

        # Full View controls
        ctrl = ttk.Frame(left, padding=(4, 2))
        ctrl.grid(row=2, column=0, columnspan=2, sticky="ew")
        for text, cmd in [
            ("Regenerate MD",      self._regenerate_md),
            ("+ Add Field",        self._add_custom_field),
            ("Toggle Easy Field",  lambda: self._toggle_easy_for_selected()),
        ]:
            ttk.Button(ctrl, text=text, command=cmd).pack(
                fill="x", pady=2)

        # ── Right editor pane ─────────────────────────────────────────────
        right = ttk.Frame(pw)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        pw.add(right, weight=1)

        editor_frames: dict = {}
        field_get_set: dict = {}
        all_keys = _all_edit_keys()

        for fkey in all_keys:
            fdef = FIELD_BY_KEY.get(fkey)
            if not fdef:
                continue
            _, flabel, ftype = fdef
            ef, get_fn, set_fn = self._make_field_editor(
                right, fkey, flabel, ftype)
            ef.grid(row=0, column=0, sticky="nsew")
            ef.grid_remove()
            editor_frames[fkey]  = ef
            field_get_set[fkey]  = (get_fn, set_fn)

        reg.update({
            "sel_lb":        sel_lb,
            "editor_frames": editor_frames,
            "field_get_set": field_get_set,
            "field_keys":    all_keys,
            "active_field":  None,
        })

        self._full_refresh_selector()

        # Show first field
        if all_keys:
            sel_lb.selection_set(0)
            first = all_keys[0]
            if first in editor_frames:
                editor_frames[first].grid()
            reg["active_field"] = first

        def _on_sel(event, tn=tab_name, fks=all_keys,
                    lb=sel_lb, efr=editor_frames, r=reg):
            s = lb.curselection()
            if not s:
                return
            i  = s[0]
            fk = fks[i] if i < len(fks) else None
            if not fk:
                return
            for f in efr.values():
                f.grid_remove()
            if fk in efr:
                efr[fk].grid()
            r["active_field"] = fk

        sel_lb.bind("<<ListboxSelect>>", _on_sel)

    def _full_refresh_selector(self) -> None:
        """Repopulate the Full View listbox, highlighting Easy View fields."""
        lb = getattr(self, "_full_view_lb", None)
        if lb is None:
            return
        prev_sel = lb.curselection()
        lb.delete(0, "end")
        for fkey in _all_edit_keys():
            fdef = FIELD_BY_KEY.get(fkey)
            if not fdef:
                continue
            label = fdef[1]
            marker = "● " if fkey in self.easy_fields else "  "
            lb.insert("end", f"  {marker}{label}")
        # Apply highlight background for easy fields
        for i, fkey in enumerate(_all_edit_keys()):
            if fkey in self.easy_fields:
                lb.itemconfigure(i, background="#dde8ff", foreground="#112266")
            else:
                lb.itemconfigure(i, background="", foreground="")
        # Restore selection if possible
        if prev_sel:
            lb.selection_set(prev_sel[0])

    def _toggle_easy_for_selected(self) -> None:
        reg = self._tab_reg.get("full", {})
        lb  = reg.get("sel_lb")
        fks = reg.get("field_keys", [])
        if lb is None:
            return
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("Toggle Easy View",
                                "Select a field in the list first.")
            return
        fkey = fks[sel[0]] if sel[0] < len(fks) else None
        if not fkey:
            return
        if fkey in self.easy_fields:
            self.easy_fields.remove(fkey)
        else:
            self.easy_fields.append(fkey)
        self.field_data["easy_view_enabled_fields"] = list(self.easy_fields)
        self._full_refresh_selector()

    # ─────────────────────────────────────────────────────────────────────────
    # Machine View tab
    # ─────────────────────────────────────────────────────────────────────────

    def _build_machine_view_tab(self) -> None:
        parent = self._tab_frames["machine"]
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        hdr = ttk.Frame(parent, padding=(8, 6, 8, 2))
        hdr.grid(row=0, column=0, sticky="ew")
        ttk.Label(hdr, text="Machine View",
                  font=("", 10, "bold")).pack(side="left")
        ttk.Label(hdr, text="  —  read-only raw JSON",
                  foreground="#888", font=("", 9)).pack(side="left")
        ttk.Button(hdr, text="Refresh",
                   command=self._machine_view_refresh).pack(side="right")
        ttk.Label(hdr, text="path:", foreground="#aaa",
                  font=("", 8)).pack(side="right", padx=(0, 4))
        self._machine_path_var = tk.StringVar(value="")
        ttk.Label(hdr, textvariable=self._machine_path_var,
                  foreground="#558", font=("Monospace", 8)).pack(
                  side="right", padx=(0, 8))

        txt_frame = ttk.Frame(parent, padding=(8, 2, 8, 4))
        txt_frame.grid(row=1, column=0, sticky="nsew")
        txt_frame.columnconfigure(0, weight=1)
        txt_frame.rowconfigure(0, weight=1)

        self._machine_txt = tk.Text(
            txt_frame, wrap="none", state="disabled",
            font=("Monospace", 9), background="#f5f5f0",
            relief="flat", borderwidth=1)
        mv_sb_y = ttk.Scrollbar(txt_frame, orient="vertical",
                                command=self._machine_txt.yview)
        mv_sb_x = ttk.Scrollbar(txt_frame, orient="horizontal",
                                command=self._machine_txt.xview)
        self._machine_txt.configure(
            yscrollcommand=mv_sb_y.set,
            xscrollcommand=mv_sb_x.set)
        self._machine_txt.grid(row=0, column=0, sticky="nsew")
        mv_sb_y.grid(row=0, column=1, sticky="ns")
        mv_sb_x.grid(row=1, column=0, sticky="ew")
        _bind_scroll(self._machine_txt)

    def _machine_view_refresh(self) -> None:
        txt = self._machine_txt
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        if not self.current_json_path:
            txt.insert("1.0", "(No record loaded.)")
            self._machine_path_var.set("")
        elif not os.path.isfile(self.current_json_path):
            txt.insert("1.0",
                "No paired json file found for this record.\n\n"
                f"Expected path:\n  {self.current_json_path}")
            self._machine_path_var.set(self.current_json_path)
        else:
            try:
                with open(self.current_json_path, "r", encoding="utf-8") as fh:
                    raw = fh.read()
                txt.insert("1.0", raw)
                self._machine_path_var.set(self.current_json_path)
            except Exception as exc:
                txt.insert("1.0", f"Error reading file:\n{exc}")
        txt.configure(state="disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # Field editor factory
    # ─────────────────────────────────────────────────────────────────────────

    def _make_field_editor(self, parent: tk.Widget,
                           fkey: str, flabel: str, ftype: str):
        """
        Create a field editor frame inside parent.
        Returns (frame, get_fn, set_fn).
        frame is placed at (row=0, col=0) with sticky=nsew but NOT shown (grid_remove).
        """
        ef = ttk.Frame(parent, padding=(12, 8, 12, 8))
        ef.columnconfigure(0, weight=1)

        # Header
        hdr = ttk.Frame(ef)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        hdr.columnconfigure(0, weight=1)
        ttk.Label(hdr, text=flabel,
                  font=("", 11, "bold")).grid(row=0, column=0, sticky="w")

        ttk.Separator(ef, orient="horizontal").grid(
            row=1, column=0, sticky="ew", pady=(0, 8))

        # Widget
        if ftype in ("entry", "entry_ro"):
            ef.rowconfigure(2, weight=0)
            var = tk.StringVar()
            ent = ttk.Entry(ef, textvariable=var, width=64)
            if ftype == "entry_ro":
                ent.configure(state="readonly")
            ent.grid(row=2, column=0, sticky="ew")
            get_fn = var.get
            set_fn = lambda v, _v=var: _v.set("" if v is None else str(v))

        elif ftype == "combo":
            ef.rowconfigure(2, weight=0)
            var = tk.StringVar()
            cb  = ttk.Combobox(ef, textvariable=var,
                               values=STATUS_VALUES, width=32,
                               state="normal")
            cb.grid(row=2, column=0, sticky="w")
            get_fn = var.get
            set_fn = lambda v, _v=var: _v.set("" if v is None else str(v))

        elif ftype in ("text", "text_ro"):
            ef.rowconfigure(2, weight=1)
            tf = ttk.Frame(ef)
            tf.grid(row=2, column=0, sticky="nsew")
            tf.columnconfigure(0, weight=1)
            tf.rowconfigure(0, weight=1)

            mono = "Monospace" if fkey in (
                "prompt_body", "marker_header_notes", "machine_metadata"
            ) else "TkDefaultFont"
            txt = tk.Text(tf, wrap="word", width=72, height=16,
                          undo=(ftype == "text"),
                          font=(mono, 10))
            if ftype == "text_ro":
                txt.configure(state="disabled", background="#f5f5f0")
            tsb = ttk.Scrollbar(tf, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=tsb.set)
            txt.grid(row=0, column=0, sticky="nsew")
            tsb.grid(row=0, column=1, sticky="ns")
            _bind_scroll(txt)

            def _get_text(_t=txt, _ft=ftype):
                if _ft == "text_ro":
                    _t.configure(state="normal")
                    v = _t.get("1.0", "end-1c")
                    _t.configure(state="disabled")
                    return v
                return _t.get("1.0", "end-1c")

            def _set_text(v, _t=txt, _ft=ftype):
                enabled = (_ft != "text_ro")
                _t.configure(state="normal")
                _t.delete("1.0", "end")
                _t.insert("1.0", "" if v is None else str(v))
                if not enabled:
                    _t.configure(state="disabled")

            get_fn = _get_text
            set_fn = _set_text

        elif ftype == "check":
            ef.rowconfigure(2, weight=0)
            bvar = tk.BooleanVar()
            ttk.Checkbutton(ef, text=flabel,
                            variable=bvar).grid(row=2, column=0, sticky="w")
            get_fn = bvar.get
            set_fn = lambda v, _b=bvar: _b.set(bool(v))

        else:
            # internal / unknown placeholder
            ef.rowconfigure(2, weight=0)
            ttk.Label(ef, text="(internal — not directly editable here)",
                      foreground="#aaa").grid(row=2, column=0, sticky="w")
            get_fn = lambda: None
            set_fn = lambda v: None

        # Save button row
        sbf = ttk.Frame(ef, padding=(0, 10, 0, 0))
        sbf.grid(row=3, column=0, sticky="w")
        ttk.Button(sbf, text="Save Record",
                   command=self._save_record).pack(side="left")
        ttk.Label(sbf, text="  saves all fields to JSON + MD",
                  foreground="#999", font=("", 8)).pack(side="left")

        return ef, get_fn, set_fn

    # ─────────────────────────────────────────────────────────────────────────
    # Bottom status bar
    # ─────────────────────────────────────────────────────────────────────────

    def _build_bottom_bar(self) -> None:
        bar = ttk.Frame(self.frame, padding=(6, 2))
        bar.grid(row=3, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        ttk.Button(bar, text="Save", command=self._save_record,
                   width=8).grid(row=0, column=0, padx=(0, 10))

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self._status_var,
                  anchor="w", foreground="#555",
                  font=("", 9)).grid(row=0, column=1, sticky="ew")

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    # ─────────────────────────────────────────────────────────────────────────
    # Root finding
    # ─────────────────────────────────────────────────────────────────────────

    def _auto_find_root(self) -> None:
        """Locate pagepack-local root first, then fall back to old guichi search."""
        from pathlib import Path
        page_file = Path(__file__).resolve()
        for candidate in [page_file.parent, *page_file.parents]:
            if (candidate / "lair").is_dir() or (candidate / "index_pychiain").is_dir():
                self._set_root(str(candidate))
                self._set_status(f"Auto-found pagepack root: {candidate}")
                return

        cwd = os.getcwd()
        parts = [p for p in cwd.split(os.sep) if p]
        for i in range(len(parts), 0, -1):
            probe = os.sep + os.path.join(*parts[:i], "guichi")
            if os.path.isdir(probe):
                self._set_root(probe)
                self._set_status(f"Auto-found legacy guichi root: {probe}")
                return

        self._set_status("pagepack root not found — use Choose Root to locate it manually.")

    def _choose_root(self) -> None:
        d = filedialog.askdirectory(title="Select the pagepack_pychiain or /guichi/ directory")
        if not d:
            return
        if os.path.basename(d) == "guichi":
            self._set_root(d)
            return
        probe = os.path.join(d, "guichi")
        if os.path.isdir(probe):
            if messagebox.askyesno("Root Found",
                    f"Found /guichi/ inside selected folder:\n{probe}\n\nUse it?"):
                self._set_root(probe)
        else:
            if messagebox.askyesno("Use Anyway?",
                    f"Selected folder is not named 'guichi':\n{d}\n\n"
                    "Use it as the root anyway?"):
                self._set_root(d)

    def _set_root(self, guichi_path: str) -> None:
        self.guichi_root = guichi_path
        preferred = os.path.join(guichi_path, "index_pychiain", "prompteditor")
        legacy = os.path.join(guichi_path, "index_pychiain", "prompt_editor")
        self.index_root = preferred
        if not os.path.isdir(preferred) and os.path.isdir(legacy):
            self.index_root = legacy
        self.md_root     = os.path.join(self.index_root, "md")
        self.json_root   = os.path.join(self.index_root, "json")
        short = guichi_path if len(guichi_path) <= 55 else "…" + guichi_path[-52:]
        self._path_var.set(f"Root: {short}")
        self._refresh_chooser1()

    def _refresh_chooser1(self) -> None:
        if not self.json_root:
            return
        prompts_dir = os.path.join(self.json_root, "prompts")
        if not os.path.isdir(prompts_dir):
            self._chooser_populate(0, [])
            self._set_status(
                f"prompts dir not found yet: {prompts_dir}  "
                "(create it or save a record to initialise)")
            return
        items = sorted(os.listdir(prompts_dir))
        self._chooser_populate(0, items)
        self._set_status(f"Browsing: {prompts_dir}  ({len(items)} items)")

    def _reload(self) -> None:
        self._refresh_chooser1()
        if self.current_json_path and os.path.isfile(self.current_json_path):
            self._load_json_file(self.current_json_path)
        self._set_status("Reloaded.")

    # ─────────────────────────────────────────────────────────────────────────
    # Record load / save
    # ─────────────────────────────────────────────────────────────────────────

    def _load_record_dlg(self) -> None:
        if not self.json_root:
            messagebox.showwarning("Load", "Set the guichi root first.")
            return
        init = os.path.join(self.json_root, "prompts")
        os.makedirs(init, exist_ok=True)
        path = filedialog.askopenfilename(
            initialdir=init,
            title="Load JSON Record",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self._load_json_file(path)

    def _load_json_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Load Error",
                f"Could not load:\n{path}\n\n{exc}")
            return

        self.current_json_path = path

        # Derive mirror MD path
        if self.json_root and path.startswith(self.json_root):
            rel  = os.path.relpath(path, self.json_root)
            base = os.path.splitext(rel)[0]
            self.current_md_path = os.path.join(self.md_root, base + ".md")
        else:
            self.current_md_path = ""

        # Merge into field_data
        self.field_data = self._empty_record()
        self.field_data.update(data)
        self.easy_fields = list(
            self.field_data.get("easy_view_enabled_fields", EASY_DEFAULT))

        self._populate_all_tabs()
        self._full_refresh_selector()

        fname = os.path.basename(path)
        self._rec_label_var.set(f"Loaded:  {fname}   |   {path}")
        short = path if len(path) <= 55 else "…" + path[-52:]
        self._path_var.set(
            f"Root: {self.guichi_root}    Record: {fname}")
        self._set_status(f"Loaded: {fname}")
        self._machine_view_refresh()

    def _new_record(self) -> None:
        self.field_data        = self._empty_record()
        self.current_json_path = ""
        self.current_md_path   = ""
        self.easy_fields       = list(EASY_DEFAULT)
        self._populate_all_tabs()
        self._full_refresh_selector()
        self._rec_label_var.set("New record (unsaved)")
        self._set_status("New record ready.")
        self._machine_view_refresh()

    def _save_record(self) -> None:
        # Collect from all edit tabs
        for tn in ("skimmer", "easy", "full"):
            self._sync_tab_to_data(tn)

        if not self.field_data.get("file_name", "").strip():
            messagebox.showwarning("Save",
                "File Name is required before saving.")
            return

        if not self.json_root:
            messagebox.showwarning("Save",
                "No root set.  Cannot determine save path.")
            return

        # Prompt for path if first save
        if not self.current_json_path:
            fname = (self.field_data["file_name"]
                     .strip().replace(" ", "_").replace("/", "-"))
            if not fname.endswith(".json"):
                fname += ".json"
            default_dir = os.path.join(self.json_root, "prompts")
            os.makedirs(default_dir, exist_ok=True)
            path = filedialog.asksaveasfilename(
                initialdir=default_dir,
                initialfile=fname,
                title="Save JSON Record",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
            if not path:
                return
            self.current_json_path = path
            rel  = os.path.relpath(path, self.json_root)
            base = os.path.splitext(rel)[0]
            self.current_md_path = os.path.join(self.md_root, base + ".md")

        # Stamp timestamps + easy fields
        self.field_data["last_modified"] = \
            datetime.datetime.now().isoformat(timespec="seconds")
        self.field_data["easy_view_enabled_fields"] = list(self.easy_fields)

        # 1. Write JSON
        try:
            os.makedirs(os.path.dirname(self.current_json_path), exist_ok=True)
            with open(self.current_json_path, "w", encoding="utf-8") as fh:
                json.dump(self.field_data, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            messagebox.showerror("Save Error",
                f"Failed to write JSON:\n{exc}")
            return

        # 2. Write MD mirror
        self._write_md_mirror()

        fname = os.path.basename(self.current_json_path)
        self._rec_label_var.set(f"Saved:  {fname}   |   {self.current_json_path}")
        self._set_status(f"Saved: {fname}")
        self._machine_view_refresh()
        self._refresh_chooser1()

    def _write_md_mirror(self) -> None:
        if not self.current_md_path:
            return
        try:
            os.makedirs(os.path.dirname(self.current_md_path), exist_ok=True)
            with open(self.current_md_path, "w", encoding="utf-8") as fh:
                fh.write(self._render_md())
        except Exception as exc:
            self._set_status(f"MD write warning: {exc}")

    def _render_md(self) -> str:
        """Render the human-readable MD mirror from field_data."""
        d = self.field_data
        lines: list = []

        lines.append(f"# {d.get('file_name', 'Untitled')}\n")

        meta_fields = [
            ("Status",              d.get("status",            "")),
            ("Tags",                d.get("tags",              "")),
            ("Pinned",              d.get("pinned",            False)),
            ("Usage Count",         d.get("usage_count",       0)),
            ("Internal ID",         d.get("internal_id",       "")),
            ("Created On",          d.get("created_on",        "")),
            ("Last Modified",       d.get("last_modified",     "")),
            ("Source / Parent",     d.get("source_parent_group", "")),
            ("Full Name",           d.get("full_name_description", "")),
        ]
        for label, val in meta_fields:
            if val or val == 0:
                lines.append(f"**{label}:** {val}")
        lines.append("")

        sections = [
            ("Notes",                         "notes"),
            ("Prompt Body",                   "prompt_body"),
            ("Detailed Intent",               "detailed_intent"),
            ("Observed Behavior / Results",   "observed_behavior"),
            ("Marker / Header Notes",         "marker_header_notes"),
        ]
        for heading, key in sections:
            content = d.get(key, "").strip()
            if content:
                lines.append(f"## {heading}\n")
                lines.append(content)
                lines.append("")

        lines.append("---")
        lines.append("*pychiain prompt_editor record*")
        return "\n".join(lines) + "\n"

    # ─────────────────────────────────────────────────────────────────────────
    # Sync helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _sync_tab_to_data(self, tab_name: str) -> None:
        """Read widget values from tab_name into self.field_data."""
        reg = self._tab_reg.get(tab_name)
        if not reg:
            return
        for fkey, (get_fn, _set_fn) in reg.get("field_get_set", {}).items():
            try:
                val = get_fn()
                if val is None:
                    continue
                if fkey == "pinned":
                    self.field_data[fkey] = bool(val)
                elif fkey == "usage_count":
                    try:
                        self.field_data[fkey] = int(val)
                    except (ValueError, TypeError):
                        self.field_data[fkey] = 0
                else:
                    self.field_data[fkey] = val
            except Exception:
                pass

    def _populate_tab_from_data(self, tab_name: str) -> None:
        """Push self.field_data values into tab_name's widgets."""
        if tab_name == "machine":
            self._machine_view_refresh()
            return
        reg = self._tab_reg.get(tab_name)
        if not reg:
            return
        for fkey, (_get_fn, set_fn) in reg.get("field_get_set", {}).items():
            val = self.field_data.get(fkey)
            # Convert machine_metadata dict -> str for display
            if fkey == "machine_metadata" and isinstance(val, dict):
                val = json.dumps(val, indent=2, ensure_ascii=False)
            try:
                set_fn("" if val is None else val)
            except Exception:
                pass

    def _populate_all_tabs(self) -> None:
        for tab_name in ("skimmer", "easy", "full"):
            self._populate_tab_from_data(tab_name)
        self._machine_view_refresh()

    # ─────────────────────────────────────────────────────────────────────────
    # Full View: Regenerate MD + Add Field
    # ─────────────────────────────────────────────────────────────────────────

    def _regenerate_md(self) -> None:
        self._sync_tab_to_data("full")
        if not self.current_json_path:
            messagebox.showwarning("Regenerate MD",
                "Save the record first to establish a file path.")
            return
        self._write_md_mirror()
        self._set_status("MD mirror regenerated from current field data.")

    def _add_custom_field(self) -> None:
        """
        Lightweight runtime field addition.
        Adds to global FIELD_DEFS/FIELD_BY_KEY/_all_edit_keys.
        Existing editor panes won't auto-rebuild this session —
        user is informed to reload.  This is intentional: do not overbuild.
        """
        dlg = tk.Toplevel(self.frame)
        dlg.title("Add Custom Field")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.columnconfigure(1, weight=1)

        rows = [
            ("Field key (snake_case):", tk.StringVar()),
            ("Display label:",          tk.StringVar()),
        ]
        for i, (lbl, var) in enumerate(rows):
            ttk.Label(dlg, text=lbl, padding=(10, 6, 4, 2)).grid(
                row=i, column=0, sticky="w")
            ttk.Entry(dlg, textvariable=var, width=34).grid(
                row=i, column=1, padx=(0, 10), pady=(6, 2), sticky="ew")
        rows[0][1].trace_add("write", lambda *_: None)

        def _confirm():
            key   = rows[0][1].get().strip().lower().replace(" ", "_")
            label = rows[1][1].get().strip() or key
            if not key:
                messagebox.showwarning("Add Field", "Key is required.", parent=dlg)
                return
            if key in FIELD_BY_KEY:
                messagebox.showwarning("Add Field",
                    f"Key '{key}' already exists.", parent=dlg)
                return
            new_def = (key, label, "text")
            FIELD_DEFS.append(new_def)
            FIELD_BY_KEY[key] = new_def
            self.field_data.setdefault(key, "")
            dlg.destroy()
            messagebox.showinfo("Field Added",
                f"Field '{key}' registered.\n\n"
                "It will appear fully in editor panes after the page is "
                "reloaded.  The field will be saved in JSON on next Save.")

        btn_row = ttk.Frame(dlg, padding=(8, 8))
        btn_row.grid(row=2, column=0, columnspan=2)
        ttk.Button(btn_row, text="Add",    command=_confirm,   width=10).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy, width=10).pack(side="left", padx=4)
        # Focus key entry
        dlg.winfo_children()[1].focus_set()

    # ─────────────────────────────────────────────────────────────────────────
    # Sync MD ↔ JSON
    # ─────────────────────────────────────────────────────────────────────────

    def _sync_md_json(self) -> None:
        if not self.current_json_path:
            messagebox.showinfo("Sync", "No record is currently loaded.")
            return
        self._load_json_file(self.current_json_path)
        self._set_status("Synced from disk JSON.")

    # ─────────────────────────────────────────────────────────────────────────
    # Search
    # ─────────────────────────────────────────────────────────────────────────

    def _search_records(self) -> None:
        if not self.json_root:
            messagebox.showwarning("Search", "Set root first.")
            return
        prompts_dir = os.path.join(self.json_root, "prompts")
        if not os.path.isdir(prompts_dir):
            messagebox.showinfo("Search",
                f"No prompts directory found:\n{prompts_dir}")
            return

        dlg = tk.Toplevel(self.frame)
        dlg.title("Search Records")
        dlg.geometry("580x460")
        dlg.grab_set()
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(2, weight=1)

        # Query row
        qf = ttk.Frame(dlg, padding=(8, 8, 8, 4))
        qf.grid(row=0, column=0, sticky="ew")
        qf.columnconfigure(1, weight=1)
        ttk.Label(qf, text="Search:").grid(row=0, column=0, sticky="w")
        sq_var = tk.StringVar()
        sq_e   = ttk.Entry(qf, textvariable=sq_var)
        sq_e.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(qf, text="Go", width=6,
                   command=lambda: _do_search()).grid(row=0, column=2)

        ttk.Label(dlg, text="Results — double-click or Load to open:",
                  padding=(8, 2)).grid(row=1, column=0, sticky="w")

        lf = ttk.Frame(dlg, padding=(8, 0, 8, 4))
        lf.grid(row=2, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        res_lb = tk.Listbox(lf, selectmode="single",
                            activestyle="dotbox", exportselection=False,
                            font=("Monospace", 9))
        res_sb = ttk.Scrollbar(lf, orient="vertical", command=res_lb.yview)
        res_lb.configure(yscrollcommand=res_sb.set)
        res_lb.grid(row=0, column=0, sticky="nsew")
        res_sb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(res_lb)

        result_paths: list = []

        def _do_search():
            q = sq_var.get().strip().lower()
            res_lb.delete(0, "end")
            result_paths.clear()
            count = 0
            for dirpath, _dirs, files in os.walk(prompts_dir):
                for fname in sorted(files):
                    if not fname.endswith(".json"):
                        continue
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            raw = fh.read()
                        if not q or q in raw.lower():
                            rel = os.path.relpath(fpath, prompts_dir)
                            res_lb.insert("end", "  " + rel)
                            result_paths.append(fpath)
                            count += 1
                    except Exception:
                        pass
            res_lb.insert("end", f"  ─── {count} record(s) matched ───")

        def _load_sel(event=None):
            sel = res_lb.curselection()
            if not sel:
                return
            i = sel[0]
            if i >= len(result_paths):
                return
            path = result_paths[i]
            dlg.destroy()
            self._load_json_file(path)

        res_lb.bind("<Double-Button-1>", _load_sel)

        bf = ttk.Frame(dlg, padding=(8, 4))
        bf.grid(row=3, column=0, sticky="ew")
        ttk.Button(bf, text="Load Selected", command=_load_sel).pack(side="left")
        ttk.Button(bf, text="Close", command=dlg.destroy).pack(side="right")

        sq_e.bind("<Return>", lambda _e: _do_search())
        sq_e.focus_set()
        _do_search()   # list all on open

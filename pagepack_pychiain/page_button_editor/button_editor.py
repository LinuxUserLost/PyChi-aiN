"""
page_button_editor / button_editor.py
────────────────────────────────────────────────────────────────────────────────
Button Editor page for pychiain.

Action buttons are saved source-calling records stored in /buttonlibrary/.
They call saved prompts or mapped prompts and make them easier to use
from pages like chat.

Button types (rough names, can be cleaned later):
  1. Raw Send         — prompt/map → clipboard
  2. Window Wrap      — map wraps user_input or upload content
  3. Custom Input     — map + user-fillable custom input fields
  4. Slot Switcher    — map with swappable prompt slots per position

Storage:
  /pagepack_pychiain/buttonlibrary/json/buttons/
  /pagepack_pychiain/buttonlibrary/md/buttons/

Status rule (same as prompts/maps):
  Status controls trust/preference, not basic usability.
  Minimum required fields filled = usable.

Tabs: Easy View, Pro View, Machine View
Scroll handling is Linux/Debian/Wayland-safe.
"""

import os
import json
import uuid
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

BUTTON_TYPES = [
    ("raw_send",              "Raw Send"),
    ("window_wrap",           "Window Wrap"),
    ("custom_input_template", "Custom Input Template"),
    ("prompt_slot_switcher",  "Prompt Slot Switcher"),
]
BUTTON_TYPE_KEYS   = [t[0] for t in BUTTON_TYPES]
BUTTON_TYPE_LABELS = [t[1] for t in BUTTON_TYPES]
BUTTON_TYPE_MAP    = dict(BUTTON_TYPES)

STATUS_VALUES = ["", "draft", "review", "copied", "tuning", "active", "archived"]

SEND_MODES = ["clipboard", "display", "input_wrap"]

DEFAULT_COLORS = {
    "bg_color":     "#e8e8e8",
    "button_color": "#4a7abf",
    "text_color":   "#ffffff",
    "border_color": "#3a5a8f",
}

# Field defs: (key, label, is_multiline)
_BUTTON_FIELD_DEFS = [
    ("button_name",     "Button Name",         False),
    ("button_type",     "Button Type",         False),
    ("status",          "Status",              False),
    ("tags",            "Tags",                False),
    ("notes",           "Notes / Description", True),
    ("linked_prompt",   "Linked Prompt",       False),
    ("linked_map",      "Linked Prompt Map",   False),
    ("source_window",   "Source Window",       False),
    ("send_mode",       "Send / Apply Mode",   False),
    ("bg_color",        "Background Color",    False),
    ("button_color",    "Button Color",        False),
    ("text_color",      "Text Color",          False),
    ("border_color",    "Border Color",        False),
    ("border_style",    "Border Style",        False),
    ("created_on",      "Created On",          False),
    ("last_modified",   "Last Modified",       False),
    ("internal_id",     "Internal ID",         False),
]
FIELD_BY_KEY = {d[0]: d for d in _BUTTON_FIELD_DEFS}


# ─────────────────────────────────────────────────────────────────────────────
# Scroll helper
# ─────────────────────────────────────────────────────────────────────────────

def _bind_scroll(widget):
    def _handler(event):
        if event.num == 4:    widget.yview_scroll(-1, "units")
        elif event.num == 5:  widget.yview_scroll(1, "units")
        elif event.delta:     widget.yview_scroll(int(-1*(event.delta/120)), "units")
        return "break"
    widget.bind("<MouseWheel>", _handler, add=False)
    widget.bind("<Button-4>",   _handler, add=False)
    widget.bind("<Button-5>",   _handler, add=False)


def _make_listbox_panel(parent, title, height=6):
    outer = ttk.LabelFrame(parent, text=title, padding=(2,2))
    outer.columnconfigure(0, weight=1); outer.rowconfigure(0, weight=1)
    lb = tk.Listbox(outer, height=height, selectmode="single",
                    activestyle="dotbox", exportselection=False)
    sb = ttk.Scrollbar(outer, orient="vertical", command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.grid(row=0, column=0, sticky="nsew"); sb.grid(row=0, column=1, sticky="ns")
    _bind_scroll(lb)
    return outer, lb


# ═════════════════════════════════════════════════════════════════════════════
# MAIN CLASS
# ═════════════════════════════════════════════════════════════════════════════

class PageButtonEditor:
    """
    Button Editor page for pychiain.

    Shell contract:
        page = PageButtonEditor(parent)
        page.build(parent)
    """

    PAGE_NAME = "button_editor"

    def __init__(self, parent, app=None, page_key="", page_folder="", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)
        self.parent = parent; self.app = app
        self.page_key = page_key; self.page_folder = page_folder

        self.pack_root    = ""
        self.lib_root     = ""   # .../buttonlibrary/
        self.json_buttons = ""
        self.md_buttons   = ""
        self.prompt_json_root = ""

        self.current_json_path = ""
        self.current_md_path   = ""
        self.field_data        = self._empty_record()
        self.custom_fields     = []  # list of {"label":"","default_value":""}

        self._c1_sel = ""; self._c2_sel = ""
        self._active_tab = "easy"

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)

        self._build_top_bar()
        self._build_chooser_area()
        self._build_notebook()
        self._build_bottom_bar()

        self.frame.after(250, self._auto_find_root)

    # ── Shell mount ──────────────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy(); self.parent = container
                self.frame = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1); self.frame.rowconfigure(2, weight=1)
                self._build_top_bar(); self._build_chooser_area()
                self._build_notebook(); self._build_bottom_bar()
                self.frame.after(50, self._auto_find_root)
        except Exception: pass
        try: self.frame.pack(fill="both", expand=True)
        except Exception:
            try: self.frame.grid(row=0, column=0, sticky="nsew")
            except Exception: pass
        return self.frame

    def build(self, parent=None): return self._embed_into_parent(parent)
    def create_widgets(self, parent=None): return self._embed_into_parent(parent)
    def mount(self, parent=None): return self._embed_into_parent(parent)
    def render(self, parent=None): return self._embed_into_parent(parent)

    def _empty_record(self):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        d = {
            "button_name": "", "button_type": "raw_send", "status": "draft",
            "tags": "", "notes": "",
            "linked_prompt": "", "linked_map": "", "source_window": "",
            "send_mode": "clipboard",
            "custom_input_fields": [], "temp_values": {},
            "bg_color": DEFAULT_COLORS["bg_color"],
            "button_color": DEFAULT_COLORS["button_color"],
            "text_color": DEFAULT_COLORS["text_color"],
            "border_color": DEFAULT_COLORS["border_color"],
            "border_style": "solid",
            "created_on": now, "last_modified": now,
            "internal_id": str(uuid.uuid4()),
        }
        return d

    # ═════════════════════════════════════════════════════════════════════════
    # TOP BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_top_bar(self):
        bar = ttk.Frame(self.frame, padding=(4,4))
        bar.grid(row=0, column=0, sticky="ew"); bar.columnconfigure(99, weight=1)
        def _btn(col, text, cmd, width=None):
            w = width or (len(text)+2)
            ttk.Button(bar, text=text, command=cmd, width=w).grid(row=0, column=col, padx=2, pady=2, sticky="w")
        _btn(0, "Auto-Find Root",    self._auto_find_root, width=16)
        _btn(1, "Choose Root\u2026", self._choose_root,    width=14)
        _btn(2, "Reload",            self._reload,         width=9)
        _btn(3, "New Button",        self._new_record,     width=12)
        _btn(4, "Save",              self._save_record,    width=7)
        _btn(5, "Search\u2026",      self._search_buttons, width=10)
        ttk.Separator(bar, orient="vertical").grid(row=0, column=6, sticky="ns", padx=6)
        self._path_var = tk.StringVar(value="No root set")
        ttk.Label(bar, textvariable=self._path_var, anchor="w", foreground="#555",
                  font=("",9)).grid(row=0, column=99, sticky="ew", padx=4)

    # ═════════════════════════════════════════════════════════════════════════
    # CHOOSER
    # ═════════════════════════════════════════════════════════════════════════

    def _build_chooser_area(self):
        cf = ttk.LabelFrame(self.frame, text="Browse Buttons", padding=(4,4))
        cf.grid(row=1, column=0, sticky="ew", padx=6, pady=(2,2))
        cf.columnconfigure(0, weight=1); cf.columnconfigure(1, weight=1); cf.columnconfigure(2, weight=1)
        self._chooser_lbs = []; self._chooser_data = [[],[],[]]
        for i, label in enumerate(["\u2460 Root", "\u2461 Subcategory", "\u2462 Items"]):
            panel, lb = _make_listbox_panel(cf, "  "+label, height=4)
            panel.grid(row=0, column=i, sticky="nsew", padx=3, pady=2)
            self._chooser_lbs.append(lb)
        self._chooser_lbs[0].bind("<<ListboxSelect>>", self._on_c1)
        self._chooser_lbs[1].bind("<<ListboxSelect>>", self._on_c2)
        self._chooser_lbs[2].bind("<<ListboxSelect>>", self._on_c3)
        self._rec_label_var = tk.StringVar(value="No button loaded.")
        ttk.Label(cf, textvariable=self._rec_label_var, anchor="w", foreground="#226",
                  font=("",9)).grid(row=1, column=0, columnspan=3, sticky="ew", padx=6, pady=(2,1))

    def _chooser_populate(self, idx, items):
        lb = self._chooser_lbs[idx]; lb.delete(0, "end")
        for item in items: lb.insert("end", item)
        self._chooser_data[idx] = list(items)
        for j in range(idx+1, 3):
            self._chooser_lbs[j].delete(0, "end"); self._chooser_data[j] = []

    def _browse_base(self):
        return os.path.join(self.lib_root, "json", "buttons") if self.lib_root else ""

    def _on_c1(self, _e=None):
        sel = self._chooser_lbs[0].curselection()
        if not sel: return
        name = self._chooser_data[0][sel[0]]; self._c1_sel = name; self._c2_sel = ""
        path = os.path.join(self._browse_base(), name)
        if os.path.isdir(path): self._chooser_populate(1, sorted(os.listdir(path)))
        elif name.endswith(".json") and os.path.isfile(path):
            self._chooser_populate(1, []); self._load_json_file(path)

    def _on_c2(self, _e=None):
        sel = self._chooser_lbs[1].curselection()
        if not sel: return
        name = self._chooser_data[1][sel[0]]; self._c2_sel = name
        path = os.path.join(self._browse_base(), self._c1_sel, name)
        if os.path.isdir(path): self._chooser_populate(2, sorted(os.listdir(path)))
        elif name.endswith(".json") and os.path.isfile(path):
            self._chooser_populate(2, []); self._load_json_file(path)

    def _on_c3(self, _e=None):
        sel = self._chooser_lbs[2].curselection()
        if not sel: return
        name = self._chooser_data[2][sel[0]]
        path = os.path.join(self._browse_base(), self._c1_sel, self._c2_sel, name)
        if name.endswith(".json") and os.path.isfile(path): self._load_json_file(path)

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

    def _on_tab_change(self, _e=None):
        tabs = ["easy","pro","machine"]; idx = self._nb.index("current")
        if 0 <= idx < len(tabs):
            old = self._active_tab; new = tabs[idx]
            if old != new:
                self._sync_from_tab(old); self._active_tab = new; self._populate_tab(new)

    # ═════════════════════════════════════════════════════════════════════════
    # EASY VIEW — core fields + quick type selector + visual preview
    # ═════════════════════════════════════════════════════════════════════════

    def _build_easy_view(self):
        parent = self._tab_frames["easy"]
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        # Left: core fields
        left = ttk.Frame(pw, padding=(8,6)); left.columnconfigure(1, weight=1)
        pw.add(left, weight=1)

        r = 0
        def _row(label, widget_fn):
            nonlocal r
            ttk.Label(left, text=label, font=("",9)).grid(row=r, column=0, sticky="w", padx=(0,6), pady=3)
            w = widget_fn(left)
            w.grid(row=r, column=1, sticky="ew", pady=3)
            r += 1; return w

        self._easy_name_var = tk.StringVar()
        _row("Name:", lambda p: ttk.Entry(p, textvariable=self._easy_name_var, width=30))

        self._easy_type_var = tk.StringVar(value="raw_send")
        _row("Type:", lambda p: ttk.Combobox(p, textvariable=self._easy_type_var,
             values=BUTTON_TYPE_LABELS, width=24, state="readonly"))

        self._easy_status_var = tk.StringVar(value="draft")
        _row("Status:", lambda p: ttk.Combobox(p, textvariable=self._easy_status_var,
             values=STATUS_VALUES, width=16, state="normal"))

        self._easy_tags_var = tk.StringVar()
        _row("Tags:", lambda p: ttk.Entry(p, textvariable=self._easy_tags_var, width=30))

        self._easy_prompt_var = tk.StringVar()
        pf = ttk.Frame(left); pf.columnconfigure(0, weight=1)
        ttk.Label(left, text="Linked Prompt:", font=("",9)).grid(row=r, column=0, sticky="w", padx=(0,6), pady=3)
        pf.grid(row=r, column=1, sticky="ew", pady=3); r += 1
        ttk.Entry(pf, textvariable=self._easy_prompt_var, width=22).grid(row=0, column=0, sticky="ew")
        ttk.Button(pf, text="Browse\u2026", width=8, command=self._browse_prompt).grid(row=0, column=1, padx=(4,0))

        self._easy_map_var = tk.StringVar()
        mf = ttk.Frame(left); mf.columnconfigure(0, weight=1)
        ttk.Label(left, text="Linked Map:", font=("",9)).grid(row=r, column=0, sticky="w", padx=(0,6), pady=3)
        mf.grid(row=r, column=1, sticky="ew", pady=3); r += 1
        ttk.Entry(mf, textvariable=self._easy_map_var, width=22).grid(row=0, column=0, sticky="ew")
        ttk.Button(mf, text="Browse\u2026", width=8, command=self._browse_map).grid(row=0, column=1, padx=(4,0))

        self._easy_send_var = tk.StringVar(value="clipboard")
        _row("Send Mode:", lambda p: ttk.Combobox(p, textvariable=self._easy_send_var,
             values=SEND_MODES, width=16, state="readonly"))

        ttk.Label(left, text="Notes:", font=("",9)).grid(row=r, column=0, sticky="nw", padx=(0,6), pady=3)
        nf = ttk.Frame(left); nf.grid(row=r, column=1, sticky="nsew", pady=3); r += 1
        nf.columnconfigure(0, weight=1); nf.rowconfigure(0, weight=1)
        left.rowconfigure(r-1, weight=1)
        self._easy_notes = tk.Text(nf, wrap="word", height=4, undo=True, font=("",9))
        nsb = ttk.Scrollbar(nf, orient="vertical", command=self._easy_notes.yview)
        self._easy_notes.configure(yscrollcommand=nsb.set)
        self._easy_notes.grid(row=0, column=0, sticky="nsew"); nsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._easy_notes)

        # Right: visual preview
        right = ttk.Frame(pw, padding=(8,6)); right.columnconfigure(0, weight=1)
        pw.add(right, weight=0)

        ttk.Label(right, text="Button Preview", font=("",10,"bold")).grid(row=0, column=0, sticky="w", pady=(0,8))

        self._preview_frame = ttk.Frame(right)
        self._preview_frame.grid(row=1, column=0, sticky="ew", pady=(0,12))
        self._preview_frame.columnconfigure(0, weight=1)

        self._preview_canvas = tk.Canvas(self._preview_frame, width=160, height=60, highlightthickness=0)
        self._preview_canvas.grid(row=0, column=0)

        ttk.Button(right, text="Refresh Preview", command=self._refresh_preview).grid(row=2, column=0, sticky="w")

        ttk.Separator(right, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=(12,8))

        ttk.Label(right, text="Colors", font=("",9,"bold")).grid(row=4, column=0, sticky="w", pady=(0,4))
        color_f = ttk.Frame(right); color_f.grid(row=5, column=0, sticky="ew")
        color_f.columnconfigure(1, weight=1)

        self._color_vars = {}
        crow = 0
        for key, label in [("bg_color","Background"),("button_color","Button"),
                            ("text_color","Text"),("border_color","Border")]:
            var = tk.StringVar(value=DEFAULT_COLORS[key])
            self._color_vars[key] = var
            ttk.Label(color_f, text=label+":", font=("",8)).grid(row=crow, column=0, sticky="w", padx=(0,4), pady=2)
            swatch = tk.Canvas(color_f, width=20, height=16, highlightthickness=1, highlightbackground="#999")
            swatch.grid(row=crow, column=1, sticky="w", padx=(0,4), pady=2)
            swatch.configure(background=DEFAULT_COLORS[key])
            ttk.Button(color_f, text="Pick", width=5,
                       command=lambda k=key, v=var, s=swatch: self._pick_color(k, v, s)).grid(
                row=crow, column=2, pady=2)
            crow += 1

        ttk.Separator(right, orient="horizontal").grid(row=6, column=0, sticky="ew", pady=(12,8))
        ttk.Label(right, text="Border Style:", font=("",8)).grid(row=7, column=0, sticky="w")
        self._border_style_var = tk.StringVar(value="solid")
        ttk.Combobox(right, textvariable=self._border_style_var,
                     values=["solid","dashed","groove","ridge","flat","none"],
                     width=12, state="readonly").grid(row=8, column=0, sticky="w", pady=(2,0))

    def _pick_color(self, key, var, swatch):
        color = colorchooser.askcolor(initialcolor=var.get(), title=f"Choose {key}")
        if color and color[1]:
            var.set(color[1]); swatch.configure(background=color[1])
            self._refresh_preview()

    def _refresh_preview(self):
        c = self._preview_canvas; c.delete("all")
        bg = self._color_vars.get("bg_color", tk.StringVar(value="#e8e8e8")).get()
        btn = self._color_vars.get("button_color", tk.StringVar(value="#4a7abf")).get()
        txt = self._color_vars.get("text_color", tk.StringVar(value="#ffffff")).get()
        bdr = self._color_vars.get("border_color", tk.StringVar(value="#3a5a8f")).get()
        name = self._easy_name_var.get() or "Button"
        # Background layer
        c.create_rectangle(2, 2, 158, 58, fill=bg, outline=bdr, width=2)
        # Button layer
        c.create_rectangle(12, 10, 148, 48, fill=btn, outline=bdr, width=1)
        # Text
        c.create_text(80, 29, text=name[:18], fill=txt, font=("", 10, "bold"))

    # ═════════════════════════════════════════════════════════════════════════
    # PRO VIEW — all fields + custom input field editor + type-specific
    # ═════════════════════════════════════════════════════════════════════════

    def _build_pro_view(self):
        parent = self._tab_frames["pro"]
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        # Left: field list selector
        left = ttk.Frame(pw, width=190); left.columnconfigure(0, weight=1); left.rowconfigure(1, weight=1)
        pw.add(left, weight=0)
        ttk.Label(left, text="Sections", font=("",9,"bold"), padding=(4,4,0,0)).grid(row=0, column=0, sticky="w")
        self._pro_sec_lb = tk.Listbox(left, selectmode="single", activestyle="dotbox",
            exportselection=False, width=20, relief="flat", borderwidth=1)
        psb = ttk.Scrollbar(left, orient="vertical", command=self._pro_sec_lb.yview)
        self._pro_sec_lb.configure(yscrollcommand=psb.set)
        self._pro_sec_lb.grid(row=1, column=0, sticky="nsew", padx=(4,0), pady=(2,4))
        psb.grid(row=1, column=1, sticky="ns", pady=(2,4))
        _bind_scroll(self._pro_sec_lb)

        sections = ["Identity", "Source Links", "Send / Behavior",
                     "Visual Style", "Custom Input Fields", "Notes"]
        for s in sections: self._pro_sec_lb.insert("end", "  " + s)
        self._pro_sections = sections
        self._pro_sec_lb.bind("<<ListboxSelect>>", self._on_pro_section)

        # Right: section panels
        right = ttk.Frame(pw, padding=(8,4)); right.columnconfigure(0, weight=1); right.rowconfigure(0, weight=1)
        pw.add(right, weight=1)
        self._pro_panels = {}

        # Build each section panel
        self._build_pro_identity(right)
        self._build_pro_sources(right)
        self._build_pro_behavior(right)
        self._build_pro_visual(right)
        self._build_pro_custom_fields(right)
        self._build_pro_notes(right)

        # Show first
        if sections: self._pro_sec_lb.selection_set(0); self._show_pro_section("Identity")

    def _show_pro_section(self, name):
        for p in self._pro_panels.values(): p.grid_remove()
        if name in self._pro_panels: self._pro_panels[name].grid()

    def _on_pro_section(self, _e=None):
        sel = self._pro_sec_lb.curselection()
        if not sel: return
        name = self._pro_sections[sel[0]]
        self._show_pro_section(name)

    def _make_pro_panel(self, parent, name):
        f = ttk.Frame(parent, padding=(8,6)); f.columnconfigure(1, weight=1)
        f.grid(row=0, column=0, sticky="nsew"); f.grid_remove()
        self._pro_panels[name] = f; return f

    def _build_pro_identity(self, parent):
        f = self._make_pro_panel(parent, "Identity"); r = 0
        ttk.Label(f, text="Identity", font=("",11,"bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0,8)); r+=1

        self._pro_name_var = tk.StringVar()
        ttk.Label(f, text="Name:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self._pro_name_var, width=30).grid(row=r, column=1, sticky="ew", pady=3); r+=1

        self._pro_type_var = tk.StringVar(value="raw_send")
        ttk.Label(f, text="Type:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self._pro_type_var, values=BUTTON_TYPE_LABELS, width=24, state="readonly").grid(row=r, column=1, sticky="w", pady=3); r+=1

        self._pro_status_var = tk.StringVar(value="draft")
        ttk.Label(f, text="Status:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self._pro_status_var, values=STATUS_VALUES, width=16, state="normal").grid(row=r, column=1, sticky="w", pady=3); r+=1

        self._pro_tags_var = tk.StringVar()
        ttk.Label(f, text="Tags:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self._pro_tags_var, width=30).grid(row=r, column=1, sticky="ew", pady=3); r+=1

        self._pro_id_var = tk.StringVar()
        ttk.Label(f, text="Internal ID:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self._pro_id_var, width=36, state="readonly").grid(row=r, column=1, sticky="ew", pady=3)

    def _build_pro_sources(self, parent):
        f = self._make_pro_panel(parent, "Source Links"); r = 0
        ttk.Label(f, text="Source Links", font=("",11,"bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(0,8)); r+=1

        self._pro_prompt_var = tk.StringVar()
        ttk.Label(f, text="Linked Prompt:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self._pro_prompt_var, width=26).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse\u2026", width=8, command=self._browse_prompt).grid(row=r, column=2, padx=(4,0), pady=3); r+=1

        self._pro_map_var = tk.StringVar()
        ttk.Label(f, text="Linked Map:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self._pro_map_var, width=26).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Browse\u2026", width=8, command=self._browse_map).grid(row=r, column=2, padx=(4,0), pady=3); r+=1

        self._pro_window_var = tk.StringVar()
        ttk.Label(f, text="Source Window:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self._pro_window_var, values=["","user_input","user_upload"], width=16, state="normal").grid(row=r, column=1, sticky="w", pady=3)

    def _build_pro_behavior(self, parent):
        f = self._make_pro_panel(parent, "Send / Behavior"); r = 0
        ttk.Label(f, text="Send / Behavior", font=("",11,"bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0,8)); r+=1

        self._pro_send_var = tk.StringVar(value="clipboard")
        ttk.Label(f, text="Send Mode:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self._pro_send_var, values=SEND_MODES, width=16, state="readonly").grid(row=r, column=1, sticky="w", pady=3); r+=1

        ttk.Label(f, text="Type Behavior Summary:", font=("",9,"bold"),
                  foreground="#555").grid(row=r, column=0, columnspan=2, sticky="w", pady=(12,4)); r+=1

        info = (
            "Raw Send: prompt/map \u2192 clipboard\n"
            "Window Wrap: map wraps source window content\n"
            "Custom Input: map + user fills custom fields\n"
            "Slot Switcher: map with swappable prompt slots"
        )
        ttk.Label(f, text=info, foreground="#666", font=("",8),
                  wraplength=350, justify="left").grid(row=r, column=0, columnspan=2, sticky="w")

    def _build_pro_visual(self, parent):
        f = self._make_pro_panel(parent, "Visual Style"); r = 0
        ttk.Label(f, text="Visual Style", font=("",11,"bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(0,8)); r+=1

        self._pro_color_vars = {}
        self._pro_swatches = {}
        for key, label in [("bg_color","Background"),("button_color","Button"),
                            ("text_color","Text"),("border_color","Border")]:
            var = tk.StringVar(value=DEFAULT_COLORS[key])
            self._pro_color_vars[key] = var
            ttk.Label(f, text=label+":").grid(row=r, column=0, sticky="w", pady=3)
            ef = ttk.Frame(f); ef.grid(row=r, column=1, sticky="ew", pady=3); ef.columnconfigure(0, weight=1)
            ttk.Entry(ef, textvariable=var, width=10, font=("Monospace",9)).grid(row=0, column=0, sticky="w")
            sw = tk.Canvas(ef, width=20, height=16, highlightthickness=1, highlightbackground="#999")
            sw.grid(row=0, column=1, padx=(6,4)); sw.configure(background=DEFAULT_COLORS[key])
            self._pro_swatches[key] = sw
            ttk.Button(ef, text="Pick", width=5,
                       command=lambda k=key, v=var, s=sw: self._pro_pick_color(k,v,s)).grid(row=0, column=2)
            r += 1

        self._pro_border_var = tk.StringVar(value="solid")
        ttk.Label(f, text="Border Style:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self._pro_border_var, values=["solid","dashed","groove","ridge","flat","none"],
                     width=12, state="readonly").grid(row=r, column=1, sticky="w", pady=3); r+=1

        # Preview
        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8,8)); r+=1
        self._pro_preview = tk.Canvas(f, width=180, height=70, highlightthickness=0)
        self._pro_preview.grid(row=r, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Refresh Preview", command=self._refresh_pro_preview).grid(row=r+1, column=0, sticky="w", pady=(6,0))

    def _pro_pick_color(self, key, var, swatch):
        color = colorchooser.askcolor(initialcolor=var.get(), title=f"Choose {key}")
        if color and color[1]:
            var.set(color[1]); swatch.configure(background=color[1])
            self._refresh_pro_preview()

    def _refresh_pro_preview(self):
        c = self._pro_preview; c.delete("all")
        bg = self._pro_color_vars.get("bg_color", tk.StringVar(value="#e8e8e8")).get()
        btn = self._pro_color_vars.get("button_color", tk.StringVar(value="#4a7abf")).get()
        txt = self._pro_color_vars.get("text_color", tk.StringVar(value="#fff")).get()
        bdr = self._pro_color_vars.get("border_color", tk.StringVar(value="#3a5a8f")).get()
        name = self._pro_name_var.get() or "Button"
        c.create_rectangle(2, 2, 178, 68, fill=bg, outline=bdr, width=2)
        c.create_rectangle(14, 12, 164, 56, fill=btn, outline=bdr, width=1)
        c.create_text(89, 34, text=name[:20], fill=txt, font=("", 11, "bold"))

    def _build_pro_custom_fields(self, parent):
        f = self._make_pro_panel(parent, "Custom Input Fields"); r = 0
        ttk.Label(f, text="Custom Input Fields", font=("",11,"bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0,4)); r+=1
        ttk.Label(f, text="For Custom Input Template buttons. Each field becomes a fillable input.",
                  font=("",8), foreground="#666", wraplength=400).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0,8)); r+=1

        # Custom fields list
        list_f = ttk.Frame(f); list_f.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(0,4)); r+=1
        list_f.columnconfigure(0, weight=1); list_f.rowconfigure(0, weight=1)
        f.rowconfigure(r-1, weight=1)

        self._cf_lb = tk.Listbox(list_f, selectmode="single", activestyle="dotbox",
            exportselection=False, height=6, font=("",9))
        cfsb = ttk.Scrollbar(list_f, orient="vertical", command=self._cf_lb.yview)
        self._cf_lb.configure(yscrollcommand=cfsb.set)
        self._cf_lb.grid(row=0, column=0, sticky="nsew"); cfsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._cf_lb)

        # Add/remove controls
        btn_f = ttk.Frame(f); btn_f.grid(row=r, column=0, columnspan=3, sticky="ew"); r+=1
        ttk.Button(btn_f, text="+ Add Field", command=self._add_custom_field).pack(side="left", padx=(0,4))
        ttk.Button(btn_f, text="Remove", command=self._remove_custom_field).pack(side="left", padx=(0,4))
        ttk.Button(btn_f, text="Edit Label\u2026", command=self._edit_custom_field).pack(side="left")

    def _add_custom_field(self):
        label = self._simple_input("Add Custom Field", "Field label:")
        if not label: return
        self.custom_fields.append({"label": label, "default_value": ""})
        self._refresh_cf_list()

    def _remove_custom_field(self):
        sel = self._cf_lb.curselection()
        if not sel: return
        self.custom_fields.pop(sel[0]); self._refresh_cf_list()

    def _edit_custom_field(self):
        sel = self._cf_lb.curselection()
        if not sel: return
        old = self.custom_fields[sel[0]]["label"]
        new = self._simple_input("Edit Label", "New label:", old)
        if new: self.custom_fields[sel[0]]["label"] = new; self._refresh_cf_list()

    def _refresh_cf_list(self):
        self._cf_lb.delete(0, "end")
        for cf in self.custom_fields:
            self._cf_lb.insert("end", f"  {cf['label']}  [{cf.get('default_value','') or '\u2014'}]")

    def _simple_input(self, title, prompt, initial=""):
        dlg = tk.Toplevel(self.frame); dlg.title(title); dlg.grab_set(); dlg.resizable(False, False)
        ttk.Label(dlg, text=prompt, padding=(10,8)).pack(anchor="w")
        var = tk.StringVar(value=initial)
        ent = ttk.Entry(dlg, textvariable=var, width=30); ent.pack(padx=10, pady=(0,8)); ent.focus_set()
        result = [None]
        def _ok(): result[0] = var.get().strip(); dlg.destroy()
        def _cancel(): dlg.destroy()
        bf = ttk.Frame(dlg, padding=(10,4)); bf.pack()
        ttk.Button(bf, text="OK", command=_ok, width=8).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=_cancel, width=8).pack(side="left", padx=4)
        ent.bind("<Return>", lambda _e: _ok())
        dlg.wait_window(); return result[0]

    def _build_pro_notes(self, parent):
        f = self._make_pro_panel(parent, "Notes"); r = 0
        ttk.Label(f, text="Notes / Description", font=("",11,"bold")).grid(
            row=r, column=0, sticky="w", pady=(0,8)); r+=1
        f.rowconfigure(r, weight=1); f.columnconfigure(0, weight=1)
        self._pro_notes = tk.Text(f, wrap="word", height=10, undo=True, font=("",9))
        nsb = ttk.Scrollbar(f, orient="vertical", command=self._pro_notes.yview)
        self._pro_notes.configure(yscrollcommand=nsb.set)
        self._pro_notes.grid(row=r, column=0, sticky="nsew"); nsb.grid(row=r, column=1, sticky="ns")
        _bind_scroll(self._pro_notes)

    # ═════════════════════════════════════════════════════════════════════════
    # MACHINE VIEW — field-targeted adaptive editor
    # ═════════════════════════════════════════════════════════════════════════

    def _build_machine_view(self):
        parent = self._tab_frames["machine"]
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(pw, width=200); left.columnconfigure(0, weight=1); left.rowconfigure(1, weight=1)
        pw.add(left, weight=0)
        ttk.Label(left, text="Fields", font=("",9,"bold"), padding=(4,4,0,0)).grid(row=0, column=0, sticky="w")
        self._mv_lb = tk.Listbox(left, selectmode="single", activestyle="dotbox",
            exportselection=False, width=22, relief="flat", borderwidth=1)
        msb = ttk.Scrollbar(left, orient="vertical", command=self._mv_lb.yview)
        self._mv_lb.configure(yscrollcommand=msb.set)
        self._mv_lb.grid(row=1, column=0, sticky="nsew", padx=(4,0), pady=(2,4))
        msb.grid(row=1, column=1, sticky="ns", pady=(2,4))
        _bind_scroll(self._mv_lb)

        self._mv_keys = [d[0] for d in _BUTTON_FIELD_DEFS] + ["custom_input_fields"]
        for d in _BUTTON_FIELD_DEFS: self._mv_lb.insert("end", "  " + d[1])
        self._mv_lb.insert("end", "  Custom Fields (JSON)")
        self._mv_lb.bind("<<ListboxSelect>>", self._on_mv_select)

        mv_ctrl = ttk.Frame(left, padding=(4,2)); mv_ctrl.grid(row=2, column=0, columnspan=2, sticky="ew")
        for text, cmd in [("Save", self._save_record), ("Regenerate MD", self._regenerate_md)]:
            ttk.Button(mv_ctrl, text=text, command=cmd).pack(fill="x", pady=1)

        right = ttk.Frame(pw, padding=(8,4)); right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=0); right.rowconfigure(4, weight=1)
        pw.add(right, weight=1)

        hdr = ttk.Frame(right); hdr.grid(row=0, column=0, sticky="ew")
        self._mv_label_var = tk.StringVar(value="Select a field")
        ttk.Label(hdr, textvariable=self._mv_label_var, font=("",10,"bold")).grid(row=0, column=0, sticky="w")
        ttk.Separator(right, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=(2,4))

        # Single-line editor
        sl = ttk.Frame(right); sl.columnconfigure(0, weight=1)
        self._mv_single_var = tk.StringVar()
        ttk.Entry(sl, textvariable=self._mv_single_var, width=60, font=("Monospace",9)).grid(row=0, column=0, sticky="ew")
        ttk.Button(sl, text="Apply", width=8, command=self._mv_apply).grid(row=0, column=1, padx=(6,0))
        self._mv_sl = sl

        # Multi-line editor
        ml = ttk.Frame(right); ml.columnconfigure(0, weight=1); ml.rowconfigure(0, weight=1)
        self._mv_multi = tk.Text(ml, wrap="word", height=6, undo=True, font=("Monospace",9))
        mmsb = ttk.Scrollbar(ml, orient="vertical", command=self._mv_multi.yview)
        self._mv_multi.configure(yscrollcommand=mmsb.set)
        self._mv_multi.grid(row=0, column=0, sticky="nsew"); mmsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._mv_multi)
        ttk.Button(ml, text="Apply", width=8, command=self._mv_apply).grid(row=1, column=0, sticky="w", pady=(2,0))
        self._mv_ml = ml

        sl.grid(row=2, column=0, sticky="ew"); ml.grid(row=2, column=0, sticky="nsew"); ml.grid_remove()
        self._mv_mode = "single"

        ttk.Separator(right, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=(4,4))

        jf = ttk.Frame(right); jf.grid(row=4, column=0, sticky="nsew")
        jf.columnconfigure(0, weight=1); jf.rowconfigure(0, weight=1)
        self._mv_json = tk.Text(jf, wrap="none", state="disabled", font=("Monospace",9),
            background="#f5f5f0", relief="flat", borderwidth=1)
        jy = ttk.Scrollbar(jf, orient="vertical", command=self._mv_json.yview)
        jx = ttk.Scrollbar(jf, orient="horizontal", command=self._mv_json.xview)
        self._mv_json.configure(yscrollcommand=jy.set, xscrollcommand=jx.set)
        self._mv_json.grid(row=0, column=0, sticky="nsew"); jy.grid(row=0, column=1, sticky="ns"); jx.grid(row=1, column=0, sticky="ew")
        _bind_scroll(self._mv_json)
        self._mv_active = None

    def _mv_show(self, mode):
        if mode == "multi" and self._mv_mode != "multi":
            self._mv_sl.grid_remove(); self._mv_ml.grid(); self._mv_mode = "multi"
        elif mode == "single" and self._mv_mode != "single":
            self._mv_ml.grid_remove(); self._mv_sl.grid(); self._mv_mode = "single"

    def _on_mv_select(self, _e=None):
        sel = self._mv_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx >= len(self._mv_keys): return
        fkey = self._mv_keys[idx]; self._mv_active = fkey
        fdef = FIELD_BY_KEY.get(fkey)
        self._mv_label_var.set(fdef[1] if fdef else fkey)
        if fkey == "custom_input_fields":
            val = json.dumps(self.custom_fields, indent=2, ensure_ascii=False)
        else:
            val = self.field_data.get(fkey, ""); 
            if isinstance(val, (dict,list)): val = json.dumps(val, indent=2, ensure_ascii=False)
            val = str(val) if val is not None else ""
        is_multi = fkey in ("notes","custom_input_fields") or (fdef and fdef[2]) or "\n" in val
        if is_multi:
            self._mv_show("multi"); self._mv_multi.delete("1.0","end"); self._mv_multi.insert("1.0", val)
        else:
            self._mv_show("single"); self._mv_single_var.set(val)
        self._mv_refresh_json()

    def _mv_apply(self):
        fkey = self._mv_active
        if not fkey: return
        raw = self._mv_multi.get("1.0","end-1c") if self._mv_mode == "multi" else self._mv_single_var.get()
        if fkey == "custom_input_fields":
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list): self.custom_fields = parsed; self._refresh_cf_list()
                else: messagebox.showwarning("Apply", "Must be a JSON array."); return
            except json.JSONDecodeError as e: messagebox.showerror("JSON Error", str(e)); return
        else: self.field_data[fkey] = raw
        self._mv_refresh_json(); self._set_status(f"Applied: {fkey}")

    def _mv_refresh_json(self):
        data = dict(self.field_data); data["custom_input_fields"] = self.custom_fields
        txt = self._mv_json; txt.configure(state="normal"); txt.delete("1.0","end")
        try: txt.insert("1.0", json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e: txt.insert("1.0", str(e))
        txt.configure(state="disabled")
        txt.tag_remove("highlight","1.0","end"); fkey = self._mv_active
        if fkey:
            txt.tag_configure("highlight", background="#fff3c0")
            pos = txt.search(f'"{fkey}"', "1.0", "end")
            if pos:
                ln = int(pos.split(".")[0]); txt.tag_add("highlight", f"{ln}.0", f"{ln}.end"); txt.see(pos)

    # ═════════════════════════════════════════════════════════════════════════
    # BOTTOM BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.frame, padding=(6,2)); bar.grid(row=3, column=0, sticky="ew"); bar.columnconfigure(1, weight=1)
        ttk.Button(bar, text="Save", command=self._save_record, width=8).grid(row=0, column=0, padx=(0,10))
        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self._status_var, anchor="w", foreground="#555", font=("",9)).grid(row=0, column=1, sticky="ew")

    def _set_status(self, msg): self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # BROWSE PROMPTS / MAPS
    # ═════════════════════════════════════════════════════════════════════════

    def _browse_prompt(self):
        result = self._browse_source("prompts", "Prompt")
        if result:
            if self._active_tab == "easy": self._easy_prompt_var.set(result)
            elif self._active_tab == "pro": self._pro_prompt_var.set(result)

    def _browse_map(self):
        result = self._browse_source("maps", "Map")
        if result:
            if self._active_tab == "easy": self._easy_map_var.set(result)
            elif self._active_tab == "pro": self._pro_map_var.set(result)

    def _browse_source(self, kind, title):
        if kind == "prompts":
            search_dir = os.path.join(self.prompt_json_root, "prompts") if self.prompt_json_root else ""
        else:
            search_dir = os.path.join(self.pack_root, "promptlibrary", "promptmapper", "json", "maps") if self.pack_root else ""
        if not search_dir or not os.path.isdir(search_dir):
            messagebox.showinfo(f"Browse {title}", f"Directory not found:\n{search_dir}"); return None

        dlg = tk.Toplevel(self.frame); dlg.title(f"Browse {title}s"); dlg.geometry("520x380"); dlg.grab_set()
        dlg.columnconfigure(0, weight=1); dlg.rowconfigure(2, weight=1)
        qf = ttk.Frame(dlg, padding=(8,8,8,4)); qf.grid(row=0, column=0, sticky="ew"); qf.columnconfigure(1, weight=1)
        ttk.Label(qf, text="Search:").grid(row=0, column=0, sticky="w")
        sq = tk.StringVar(); se = ttk.Entry(qf, textvariable=sq); se.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(qf, text="Go", width=5, command=lambda: _search()).grid(row=0, column=2)
        ttk.Label(dlg, text=f"Double-click to select:", padding=(8,2)).grid(row=1, column=0, sticky="w")
        lf = ttk.Frame(dlg, padding=(8,0,8,4)); lf.grid(row=2, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)
        rlb = tk.Listbox(lf, selectmode="single", activestyle="dotbox", exportselection=False, font=("Monospace",9))
        rsb = ttk.Scrollbar(lf, orient="vertical", command=rlb.yview); rlb.configure(yscrollcommand=rsb.set)
        rlb.grid(row=0, column=0, sticky="nsew"); rsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(rlb); names = []

        def _search():
            q = sq.get().strip().lower(); rlb.delete(0,"end"); names.clear()
            for dp, _d, fs in os.walk(search_dir):
                for fn in sorted(fs):
                    if not fn.endswith(".json"): continue
                    try:
                        with open(os.path.join(dp,fn),"r",encoding="utf-8") as fh: data = json.load(fh)
                        nm = data.get("file_name", fn.replace(".json",""))
                        if not q or q in f"{nm} {data.get('tags','')} {fn}".lower():
                            rlb.insert("end", f"  {nm}"); names.append(nm)
                    except Exception: pass
            rlb.insert("end", f"  \u2500\u2500\u2500 {len(names)} item(s) \u2500\u2500\u2500")

        result = [None]
        def _sel(e=None):
            s = rlb.curselection()
            if s and s[0] < len(names): result[0] = names[s[0]]; dlg.destroy()
        rlb.bind("<Double-Button-1>", _sel)
        bf = ttk.Frame(dlg, padding=(8,4)); bf.grid(row=3, column=0, sticky="ew")
        ttk.Button(bf, text="Select", command=_sel).pack(side="left")
        ttk.Button(bf, text="Close", command=dlg.destroy).pack(side="right")
        se.bind("<Return>", lambda _e: _search()); se.focus_set(); _search()
        dlg.wait_window(); return result[0]

    def _search_buttons(self):
        if not self._browse_base() or not os.path.isdir(self._browse_base()):
            messagebox.showinfo("Search", "No button library found."); return
        result = self._browse_source_in(self._browse_base(), "Buttons")
        if result: self._load_json_file(result)

    def _browse_source_in(self, search_dir, title):
        dlg = tk.Toplevel(self.frame); dlg.title(f"Search {title}"); dlg.geometry("520x380"); dlg.grab_set()
        dlg.columnconfigure(0, weight=1); dlg.rowconfigure(2, weight=1)
        qf = ttk.Frame(dlg, padding=(8,8,8,4)); qf.grid(row=0, column=0, sticky="ew"); qf.columnconfigure(1, weight=1)
        sq = tk.StringVar(); se = ttk.Entry(qf, textvariable=sq); se.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(qf, text="Search:").grid(row=0, column=0); ttk.Button(qf, text="Go", width=5, command=lambda: _search()).grid(row=0, column=2)
        lf = ttk.Frame(dlg, padding=(8,0,8,4)); lf.grid(row=2, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)
        rlb = tk.Listbox(lf, selectmode="single", font=("Monospace",9), activestyle="dotbox", exportselection=False)
        rsb = ttk.Scrollbar(lf, orient="vertical", command=rlb.yview); rlb.configure(yscrollcommand=rsb.set)
        rlb.grid(row=0, column=0, sticky="nsew"); rsb.grid(row=0, column=1, sticky="ns"); _bind_scroll(rlb)
        paths = []
        def _search():
            q = sq.get().strip().lower(); rlb.delete(0,"end"); paths.clear()
            for dp, _d, fs in os.walk(search_dir):
                for fn in sorted(fs):
                    if not fn.endswith(".json"): continue
                    fp = os.path.join(dp, fn)
                    try:
                        with open(fp,"r",encoding="utf-8") as fh: data = json.load(fh)
                        nm = data.get("button_name", fn.replace(".json",""))
                        if not q or q in f"{nm} {data.get('tags','')} {fn}".lower():
                            rlb.insert("end", f"  {nm}"); paths.append(fp)
                    except Exception: pass
        result = [None]
        def _sel(e=None):
            s = rlb.curselection()
            if s and s[0] < len(paths): result[0] = paths[s[0]]; dlg.destroy()
        rlb.bind("<Double-Button-1>", _sel)
        bf = ttk.Frame(dlg, padding=(8,4)); bf.grid(row=3, column=0, sticky="ew")
        ttk.Button(bf, text="Load", command=_sel).pack(side="left")
        ttk.Button(bf, text="Close", command=dlg.destroy).pack(side="right")
        se.bind("<Return>", lambda _e: _search()); se.focus_set(); _search()
        dlg.wait_window(); return result[0]

    # ═════════════════════════════════════════════════════════════════════════
    # TAB SYNC
    # ═════════════════════════════════════════════════════════════════════════

    def _type_key_from_label(self, label):
        for k, l in BUTTON_TYPES:
            if l == label: return k
        return label

    def _type_label_from_key(self, key):
        return BUTTON_TYPE_MAP.get(key, key)

    def _sync_from_tab(self, tab):
        if tab == "easy":
            self.field_data["button_name"] = self._easy_name_var.get()
            self.field_data["button_type"] = self._type_key_from_label(self._easy_type_var.get())
            self.field_data["status"]      = self._easy_status_var.get()
            self.field_data["tags"]        = self._easy_tags_var.get()
            self.field_data["linked_prompt"] = self._easy_prompt_var.get()
            self.field_data["linked_map"]  = self._easy_map_var.get()
            self.field_data["send_mode"]   = self._easy_send_var.get()
            self.field_data["notes"]       = self._easy_notes.get("1.0","end-1c")
            for k, v in self._color_vars.items(): self.field_data[k] = v.get()
            self.field_data["border_style"] = self._border_style_var.get()
        elif tab == "pro":
            self.field_data["button_name"] = self._pro_name_var.get()
            self.field_data["button_type"] = self._type_key_from_label(self._pro_type_var.get())
            self.field_data["status"]      = self._pro_status_var.get()
            self.field_data["tags"]        = self._pro_tags_var.get()
            self.field_data["linked_prompt"] = self._pro_prompt_var.get()
            self.field_data["linked_map"]  = self._pro_map_var.get()
            self.field_data["source_window"] = self._pro_window_var.get()
            self.field_data["send_mode"]   = self._pro_send_var.get()
            self.field_data["notes"]       = self._pro_notes.get("1.0","end-1c")
            for k, v in self._pro_color_vars.items(): self.field_data[k] = v.get()
            self.field_data["border_style"] = self._pro_border_var.get()

    def _sync_all(self):
        for t in ("easy","pro","machine"): self._sync_from_tab(t)

    def _populate_tab(self, tab):
        if tab == "easy": self._pop_easy()
        elif tab == "pro": self._pop_pro()
        elif tab == "machine": self._mv_refresh_json()

    def _pop_easy(self):
        d = self.field_data
        self._easy_name_var.set(d.get("button_name",""))
        self._easy_type_var.set(self._type_label_from_key(d.get("button_type","raw_send")))
        self._easy_status_var.set(d.get("status","draft"))
        self._easy_tags_var.set(d.get("tags",""))
        self._easy_prompt_var.set(d.get("linked_prompt",""))
        self._easy_map_var.set(d.get("linked_map",""))
        self._easy_send_var.set(d.get("send_mode","clipboard"))
        self._easy_notes.delete("1.0","end"); self._easy_notes.insert("1.0", d.get("notes",""))
        for k in DEFAULT_COLORS:
            if k in self._color_vars: self._color_vars[k].set(d.get(k, DEFAULT_COLORS[k]))
        self._border_style_var.set(d.get("border_style","solid"))
        self._refresh_preview()

    def _pop_pro(self):
        d = self.field_data
        self._pro_name_var.set(d.get("button_name",""))
        self._pro_type_var.set(self._type_label_from_key(d.get("button_type","raw_send")))
        self._pro_status_var.set(d.get("status","draft"))
        self._pro_tags_var.set(d.get("tags",""))
        self._pro_id_var.set(d.get("internal_id",""))
        self._pro_prompt_var.set(d.get("linked_prompt",""))
        self._pro_map_var.set(d.get("linked_map",""))
        self._pro_window_var.set(d.get("source_window",""))
        self._pro_send_var.set(d.get("send_mode","clipboard"))
        self._pro_notes.delete("1.0","end"); self._pro_notes.insert("1.0", d.get("notes",""))
        for k in DEFAULT_COLORS:
            if k in self._pro_color_vars:
                self._pro_color_vars[k].set(d.get(k, DEFAULT_COLORS[k]))
                if k in self._pro_swatches: self._pro_swatches[k].configure(background=d.get(k, DEFAULT_COLORS[k]))
        self._pro_border_var.set(d.get("border_style","solid"))
        self._refresh_cf_list(); self._refresh_pro_preview()

    def _pop_all(self):
        self._pop_easy(); self._pop_pro(); self._mv_refresh_json(); self._refresh_cf_list()

    # ═════════════════════════════════════════════════════════════════════════
    # LOAD / SAVE
    # ═════════════════════════════════════════════════════════════════════════

    def _load_json_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh: data = json.load(fh)
        except Exception as exc: messagebox.showerror("Load Error", str(exc)); return
        self.current_json_path = path
        base = self._browse_base()
        if base and path.startswith(base):
            rel = os.path.relpath(path, base); stem = os.path.splitext(rel)[0]
            self.current_md_path = os.path.join(self.lib_root, "md", "buttons", stem + ".md")
        else: self.current_md_path = ""
        self.field_data = self._empty_record()
        for k, v in data.items():
            if k == "custom_input_fields": self.custom_fields = v if isinstance(v, list) else []
            elif k == "temp_values": self.field_data["temp_values"] = v
            else: self.field_data[k] = v
        self._pop_all()
        self._rec_label_var.set(f"Loaded: {os.path.basename(path)}")
        self._set_status(f"Loaded: {os.path.basename(path)}")

    def _new_record(self):
        self.field_data = self._empty_record(); self.custom_fields = []
        self.current_json_path = ""; self.current_md_path = ""
        self._pop_all(); self._rec_label_var.set("New button (unsaved)"); self._set_status("New button ready.")

    def _save_record(self):
        self._sync_all()
        if not self.field_data.get("button_name","").strip():
            messagebox.showwarning("Save", "Button Name is required."); return
        if not self.lib_root: messagebox.showwarning("Save", "No root set."); return
        json_dir = os.path.join(self.lib_root, "json", "buttons")
        md_dir = os.path.join(self.lib_root, "md", "buttons")
        if not self.current_json_path:
            fn = self.field_data["button_name"].strip().replace(" ","_").replace("/","-")
            if not fn.endswith(".json"): fn += ".json"
            os.makedirs(json_dir, exist_ok=True)
            path = filedialog.asksaveasfilename(initialdir=json_dir, initialfile=fn,
                title="Save Button", defaultextension=".json",
                filetypes=[("JSON","*.json"),("All","*.*")])
            if not path: return
            self.current_json_path = path
            rel = os.path.relpath(path, json_dir); stem = os.path.splitext(rel)[0]
            self.current_md_path = os.path.join(md_dir, stem + ".md")
        self.field_data["last_modified"] = datetime.datetime.now().isoformat(timespec="seconds")
        save = dict(self.field_data); save["custom_input_fields"] = self.custom_fields
        try:
            os.makedirs(os.path.dirname(self.current_json_path), exist_ok=True)
            with open(self.current_json_path, "w", encoding="utf-8") as fh:
                json.dump(save, fh, indent=2, ensure_ascii=False)
        except Exception as exc: messagebox.showerror("Save Error", str(exc)); return
        self._write_md()
        self._rec_label_var.set(f"Saved: {os.path.basename(self.current_json_path)}")
        self._set_status(f"Saved: {os.path.basename(self.current_json_path)}")
        self._refresh_chooser(); self._mv_refresh_json()

    def _write_md(self):
        if not self.current_md_path: return
        d = self.field_data; lines = []
        lines.append(f"# {d.get('button_name','Untitled Button')}\n")
        for lbl, val in [("Type", self._type_label_from_key(d.get("button_type",""))),
            ("Status",d.get("status","")),("Tags",d.get("tags","")),
            ("Send Mode",d.get("send_mode","")),("Linked Prompt",d.get("linked_prompt","")),
            ("Linked Map",d.get("linked_map","")),("Source Window",d.get("source_window","")),
            ("BG Color",d.get("bg_color","")),("Button Color",d.get("button_color","")),
            ("Text Color",d.get("text_color","")),("Border",d.get("border_color","")),
            ("ID",d.get("internal_id","")),("Created",d.get("created_on","")),("Modified",d.get("last_modified",""))]:
            if val: lines.append(f"**{lbl}:** {val}")
        lines.append("")
        notes = d.get("notes","").strip()
        if notes: lines.append("## Notes\n"); lines.append(notes); lines.append("")
        if self.custom_fields:
            lines.append("## Custom Input Fields\n")
            for cf in self.custom_fields:
                lines.append(f"- **{cf.get('label','')}** (default: {cf.get('default_value','') or '\u2014'})")
            lines.append("")
        lines.append("---"); lines.append("*pychiain button record*")
        try:
            os.makedirs(os.path.dirname(self.current_md_path), exist_ok=True)
            with open(self.current_md_path, "w", encoding="utf-8") as fh: fh.write("\n".join(lines)+"\n")
        except Exception as exc: self._set_status(f"MD warning: {exc}")

    def _regenerate_md(self):
        if not self.current_json_path:
            messagebox.showwarning("Regenerate MD", "Save first."); return
        if not messagebox.askyesno("Regenerate MD", "Any existing .md file with the same name will be replaced.\nContinue?"): return
        self._sync_all(); self._write_md(); self._set_status("MD regenerated.")

    # ═════════════════════════════════════════════════════════════════════════
    # ROOT
    # ═════════════════════════════════════════════════════════════════════════

    def _auto_find_root(self):
        from pathlib import Path
        pf = Path(__file__).resolve()
        for c in [pf.parent, *pf.parents]:
            if (c/"buttonlibrary").is_dir() or (c/"promptlibrary").is_dir() or c.name == "pagepack_pychiain":
                self._set_root(str(c)); self._set_status(f"Auto-found: {c}"); return
        cwd = os.getcwd(); parts = [p for p in cwd.split(os.sep) if p]
        for i in range(len(parts), 0, -1):
            probe = os.sep + os.path.join(*parts[:i])
            if os.path.isdir(os.path.join(probe, "pagepack_pychiain")):
                self._set_root(os.path.join(probe, "pagepack_pychiain")); return
            if os.path.basename(probe) == "pagepack_pychiain": self._set_root(probe); return
        self._set_status("Root not found.")

    def _choose_root(self):
        d = filedialog.askdirectory(title="Select pagepack_pychiain")
        if d: self._set_root(d)

    def _set_root(self, p):
        self.pack_root = p
        self.lib_root = os.path.join(p, "buttonlibrary")
        for subpath in [
            os.path.join(p, "promptlibrary", "prompteditor", "json"),
            os.path.join(p, "index_pychiain", "prompteditor", "json"),
        ]:
            if os.path.isdir(subpath): self.prompt_json_root = subpath; break
        else: self.prompt_json_root = os.path.join(p, "promptlibrary", "prompteditor", "json")
        for d in [os.path.join(self.lib_root,"json","buttons"), os.path.join(self.lib_root,"md","buttons")]:
            os.makedirs(d, exist_ok=True)
        short = p if len(p) <= 55 else "\u2026"+p[-52:]
        self._path_var.set(f"Root: {short}"); self._refresh_chooser()

    def _refresh_chooser(self):
        base = self._browse_base()
        if os.path.isdir(base):
            self._chooser_populate(0, sorted(os.listdir(base)))
            self._set_status(f"Browsing: {base}")
        else: self._chooser_populate(0, [])

    def _reload(self):
        self._refresh_chooser()
        if self.current_json_path and os.path.isfile(self.current_json_path):
            self._load_json_file(self.current_json_path)
        self._set_status("Reloaded.")

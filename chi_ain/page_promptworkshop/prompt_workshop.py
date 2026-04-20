"""
promptworkshop / prompt_workshop.py
────────────────────────────────────────────────────────────────────────────────
Prompt Workshop — combined page for editing prompt records and prompt maps.

Tabs:
  1. Prompt Editor  — create/edit individual prompt records
  2. Prompt Maps    — create/edit ordered prompt map assemblies

Each tab has a live JSON machine-view pane showing the full record (including
auto-generated fields) with active-field highlighting.  The preview updates as
the user edits — no manual refresh required.

Storage:
  /pagepack_pychiain/promptworkshop/prompts/   — prompt record JSON files
  /pagepack_pychiain/promptworkshop/maps/      — prompt map JSON files

Scroll handling is Linux/Debian/Wayland-safe: each scrollable widget binds
its own Button-4/Button-5/MouseWheel and returns "break".
"""

import os
import json
import uuid
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bind_scroll(widget: tk.Widget) -> None:
    """Attach scroll events directly to a widget (Wayland-safe)."""
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


def _make_listbox(parent, height=8):
    """Return (frame, listbox) with scrollbar, ready to pack/grid."""
    frm = ttk.Frame(parent)
    frm.columnconfigure(0, weight=1)
    frm.rowconfigure(0, weight=1)
    lb = tk.Listbox(frm, height=height, selectmode="single",
                    activestyle="dotbox", exportselection=False)
    sb = ttk.Scrollbar(frm, orient="vertical", command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    _bind_scroll(lb)
    return frm, lb


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _safe_filename(title: str) -> str:
    """Turn a title into a filesystem-safe stem."""
    s = title.strip().replace(" ", "_").replace("/", "-").replace("\\", "-")
    s = "".join(c for c in s if c.isalnum() or c in ("_", "-", "."))
    return s or "untitled"


def _make_machine_view(parent):
    """
    Build a read-only Text widget styled as a machine-view JSON pane.
    Returns (outer_frame, text_widget).
    """
    outer = ttk.LabelFrame(parent, text="Record JSON", padding=(4, 2))
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)

    mono = ("Consolas", 9) if os.name == "nt" else ("monospace", 9)
    tw = tk.Text(outer, wrap="none", state="disabled",
                 font=mono,
                 background="#1e1e2e", foreground="#cdd6f4",
                 insertbackground="#cdd6f4", relief="flat",
                 borderwidth=0, padx=6, pady=4)
    vsb = ttk.Scrollbar(outer, orient="vertical", command=tw.yview)
    hsb = ttk.Scrollbar(outer, orient="horizontal", command=tw.xview)
    tw.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tw.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    _bind_scroll(tw)

    # Highlight tag for the currently active field's line(s)
    tw.tag_configure("hl_field",
                     background="#313244", foreground="#f9e2af",
                     selectbackground="#45475a")
    # Syntax colour tags
    tw.tag_configure("json_key", foreground="#89b4fa")
    tw.tag_configure("json_str", foreground="#a6e3a1")
    tw.tag_configure("json_lit", foreground="#fab387")

    return outer, tw


def _render_json_highlighted(tw: tk.Text, record: dict, active_field: str):
    """
    Render *record* as pretty-printed JSON into the Text widget *tw*,
    applying lightweight syntax colour and highlighting the line(s) whose
    top-level key matches *active_field*.
    """
    tw.configure(state="normal")
    tw.delete("1.0", "end")

    pretty = json.dumps(record, indent=2, ensure_ascii=False)
    tw.insert("1.0", pretty)

    line_count = int(tw.index("end-1c").split(".")[0])
    active_key_pat = '"' + active_field + '"' if active_field else None

    # Track which top-level key the current line belongs to so that
    # multi-line values (like the blocks array) get fully highlighted.
    hl_start = None          # first line of the active field's value
    hl_end   = None          # last line (inclusive)
    inside_active = False
    indent_at_key = None     # indentation level of the key line

    for lineno in range(1, line_count + 1):
        line = tw.get(f"{lineno}.0", f"{lineno}.end")

        # ── Syntax colouring ─────────────────────────────────────────────
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                j = i + 1
                while j < len(line):
                    if line[j] == '\\':
                        j += 2; continue
                    if line[j] == '"':
                        break
                    j += 1
                end = j + 1
                rest = line[end:].lstrip()
                tag = "json_key" if rest.startswith(":") else "json_str"
                tw.tag_add(tag, f"{lineno}.{i}", f"{lineno}.{end}")
                i = end; continue
            for lit in ("true", "false", "null"):
                if line[i:i+len(lit)] == lit:
                    tw.tag_add("json_lit", f"{lineno}.{i}",
                               f"{lineno}.{i+len(lit)}")
                    i += len(lit); break
            else:
                if ch in "0123456789-":
                    j = i + 1
                    while j < len(line) and line[j] in "0123456789.eE+-":
                        j += 1
                    if j > i + (1 if ch == '-' else 0):
                        tw.tag_add("json_lit", f"{lineno}.{i}", f"{lineno}.{j}")
                    i = j; continue
                i += 1

        # ── Active-field region detection ────────────────────────────────
        if active_key_pat is None:
            continue

        stripped = line.lstrip()
        leading = len(line) - len(stripped)

        # A top-level key line at indent=2 (inside the outer { })
        if leading == 2 and stripped.startswith('"'):
            # Did we just leave the active region?
            if inside_active:
                hl_end = lineno - 1
                inside_active = False
            # Is this the active key?
            if active_key_pat in line:
                hl_start = lineno
                inside_active = True
                indent_at_key = leading
        elif inside_active:
            # We're still inside the active field's value if the indent
            # is deeper than the key, or if it's a closing bracket at
            # the same level (for arrays/objects).
            if leading > indent_at_key:
                pass  # continuation
            elif stripped in ("],", "]", "},", "}"):
                pass  # closing bracket of the value
            else:
                # Reached the next top-level key or outer closing brace
                hl_end = lineno - 1
                inside_active = False
                # Re-check: this line might be the start of a new active key
                if leading == 2 and stripped.startswith('"') and active_key_pat in line:
                    hl_start = lineno
                    inside_active = True
                    indent_at_key = leading

    # Close out if still active at end-of-file
    if inside_active:
        hl_end = line_count

    # Apply the highlight band
    if hl_start is not None and hl_end is not None:
        for ln in range(hl_start, hl_end + 1):
            tw.tag_add("hl_field", f"{ln}.0", f"{ln}.end")
        tw.see(f"{hl_start}.0")

    tw.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_prompt() -> dict:
    now = _now_iso()
    return {
        "id":         str(uuid.uuid4()),
        "title":      "",
        "body":       "",
        "tags":       "",
        "enabled":    True,
        "created_at": now,
        "updated_at": now,
    }


def _empty_map() -> dict:
    now = _now_iso()
    return {
        "id":         str(uuid.uuid4()),
        "title":      "",
        "tags":       "",
        "blocks":     [],
        "enabled":    True,
        "created_at": now,
        "updated_at": now,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main page class
# ─────────────────────────────────────────────────────────────────────────────

class PagePromptWorkshop:
    """
    Prompt Workshop page for pychiain.

    Shell contract (Guichi loader):
        page = PagePromptWorkshop(parent, app, page_key, page_folder)
        # page.frame is the root widget
    """

    PAGE_NAME = "prompt_workshop"

    # How often (ms) the machine-view auto-refreshes from editor fields.
    _MV_INTERVAL = 300

    # ── Init ─────────────────────────────────────────────────────────────────

    def __init__(self, parent: tk.Widget, app=None, page_key: str = "",
                 page_folder: str = "", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)

        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # Storage roots
        self.workshop_root = ""
        self.prompts_dir   = ""
        self.maps_dir      = ""

        # Prompt editor state
        self._pe_record       = _empty_prompt()
        self._pe_json_path    = ""
        self._pe_active_field = "title"
        self._pe_mv_dirty     = True
        self._pe_mv_after_id  = None

        # Map editor state
        self._pm_record       = _empty_map()
        self._pm_json_path    = ""
        self._pm_active_field = "title"
        self._pm_mv_dirty     = True
        self._pm_mv_after_id  = None

        # Build
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self._build_top_bar()
        self._build_notebook()
        self._build_status_bar()

        self.frame.after(250, self._auto_find_root)

        # Start live-update timers
        self._pe_schedule_mv_refresh()
        self._pm_schedule_mv_refresh()

    # ── Shell mount methods ──────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy()
                self.parent = container
                self.frame = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1)
                self.frame.rowconfigure(1, weight=1)
                self._build_top_bar()
                self._build_notebook()
                self._build_status_bar()
                self.frame.after(50, self._auto_find_root)
                self._pe_schedule_mv_refresh()
                self._pm_schedule_mv_refresh()
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

    # ═════════════════════════════════════════════════════════════════════════
    # TOP BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_top_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 4))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(99, weight=1)

        ttk.Button(bar, text="Auto-Find Root", width=15,
                   command=self._auto_find_root).grid(row=0, column=0, padx=2)
        ttk.Button(bar, text="Choose Root\u2026", width=14,
                   command=self._choose_root).grid(row=0, column=1, padx=2)

        ttk.Separator(bar, orient="vertical").grid(
            row=0, column=2, sticky="ns", padx=6)

        self._path_var = tk.StringVar(
            value="No root set \u2014 use Auto-Find Root or Choose Root\u2026")
        ttk.Label(bar, textvariable=self._path_var, anchor="w",
                  foreground="#555", font=("", 9)).grid(
            row=0, column=99, sticky="ew", padx=4)

    # ═════════════════════════════════════════════════════════════════════════
    # NOTEBOOK
    # ═════════════════════════════════════════════════════════════════════════

    def _build_notebook(self):
        self._nb = ttk.Notebook(self.frame)
        self._nb.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 0))

        self._pe_frame = ttk.Frame(self._nb, padding=4)
        self._nb.add(self._pe_frame, text="  Prompt Editor  ")
        self._build_prompt_editor_tab(self._pe_frame)

        self._pm_frame = ttk.Frame(self._nb, padding=4)
        self._nb.add(self._pm_frame, text="  Prompt Maps  ")
        self._build_prompt_maps_tab(self._pm_frame)

    # ═════════════════════════════════════════════════════════════════════════
    # STATUS BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_status_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 2))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self._status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self._status_var, anchor="w",
                  foreground="#666", font=("", 9)).grid(
            row=0, column=0, sticky="ew")

    def _set_status(self, msg: str):
        self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # ROOT FINDING
    # ═════════════════════════════════════════════════════════════════════════

    def _auto_find_root(self):
        from pathlib import Path
        page_file = Path(__file__).resolve()
        for candidate in [page_file.parent, *page_file.parents]:
            if candidate.name == "pagepack_pychiain":
                self._set_root(str(candidate))
                self._set_status(f"Auto-found root: {candidate}")
                return
            if (candidate / "pagepack_pychiain").is_dir():
                self._set_root(str(candidate / "pagepack_pychiain"))
                self._set_status(f"Auto-found root: {candidate / 'pagepack_pychiain'}")
                return
        cwd = os.getcwd()
        probe = os.path.join(cwd, "pagepack_pychiain")
        if os.path.isdir(probe):
            self._set_root(probe); return
        if os.path.basename(cwd) == "pagepack_pychiain":
            self._set_root(cwd); return
        self._set_status("Root not found \u2014 use Choose Root.")

    def _choose_root(self):
        d = filedialog.askdirectory(title="Select pagepack_pychiain directory")
        if d:
            self._set_root(d)

    def _set_root(self, pack_path: str):
        self.workshop_root = os.path.join(pack_path, "promptworkshop")
        self.prompts_dir   = os.path.join(self.workshop_root, "prompts")
        self.maps_dir      = os.path.join(self.workshop_root, "maps")
        os.makedirs(self.prompts_dir, exist_ok=True)
        os.makedirs(self.maps_dir, exist_ok=True)
        short = pack_path if len(pack_path) <= 55 else "\u2026" + pack_path[-52:]
        self._path_var.set(f"Root: {short}")
        self._pe_refresh_file_list()
        self._pm_refresh_file_list()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 1 — PROMPT EDITOR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_prompt_editor_tab(self, parent):
        parent.columnconfigure(0, weight=0)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # ── Left: file list ──────────────────────────────────────────────────
        left = ttk.LabelFrame(parent, text="Saved Prompts", padding=4)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        btn_row = ttk.Frame(left)
        btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(btn_row, text="New", width=7,
                   command=self._pe_new).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Delete", width=7,
                   command=self._pe_delete).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Refresh", width=7,
                   command=self._pe_refresh_file_list).pack(side="left", padx=2)

        lb_frame, self._pe_lb = _make_listbox(left, height=12)
        lb_frame.grid(row=1, column=0, sticky="nsew")
        self._pe_lb.bind("<<ListboxSelect>>", self._pe_on_select)
        self._pe_files = []

        # ── Right: PanedWindow (editor top, machine view bottom) ─────────────
        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.grid(row=0, column=1, sticky="nsew")

        # ── Top pane: editor fields ──────────────────────────────────────────
        editor_frame = ttk.Frame(pane)
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(editor_frame, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(editor_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")
        _bind_scroll(canvas)

        inner = ttk.Frame(canvas, padding=4)
        self._pe_canvas = canvas
        self._pe_inner  = inner
        self._pe_canvas_win = canvas.create_window((0, 0), window=inner,
                                                    anchor="nw")
        inner.columnconfigure(1, weight=1)

        def _on_inner_cfg(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_cfg(event):
            canvas.itemconfigure(self._pe_canvas_win, width=event.width)
        inner.bind("<Configure>", _on_inner_cfg)
        canvas.bind("<Configure>", _on_canvas_cfg)

        def _rebind(widget):
            for child in widget.winfo_children():
                if not isinstance(child, tk.Text):
                    child.bind("<MouseWheel>",
                               lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units") or "break",
                               add=False)
                    child.bind("<Button-4>",
                               lambda e: canvas.yview_scroll(-1, "units") or "break", add=False)
                    child.bind("<Button-5>",
                               lambda e: canvas.yview_scroll(1, "units") or "break", add=False)
                _rebind(child)
        inner.bind("<Map>", lambda e: _rebind(inner))

        row = 0

        def _pe_mark_dirty(*_a):
            self._pe_mv_dirty = True

        # Title
        ttk.Label(inner, text="Title:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        self._pe_title_var = tk.StringVar()
        self._pe_title_var.trace_add("write", _pe_mark_dirty)
        pe_title_entry = ttk.Entry(inner, textvariable=self._pe_title_var,
                                   font=("", 11))
        pe_title_entry.grid(row=row, column=1, sticky="ew", pady=2)
        pe_title_entry.bind("<FocusIn>",
                            lambda e: self._pe_set_active("title"))
        row += 1

        # Tags
        ttk.Label(inner, text="Tags:").grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        self._pe_tags_var = tk.StringVar()
        self._pe_tags_var.trace_add("write", _pe_mark_dirty)
        pe_tags_entry = ttk.Entry(inner, textvariable=self._pe_tags_var)
        pe_tags_entry.grid(row=row, column=1, sticky="ew", pady=2)
        pe_tags_entry.bind("<FocusIn>",
                           lambda e: self._pe_set_active("tags"))
        row += 1

        # Enabled
        self._pe_enabled_var = tk.BooleanVar(value=True)
        self._pe_enabled_var.trace_add("write", _pe_mark_dirty)
        pe_chk = ttk.Checkbutton(inner, text="Enabled",
                                 variable=self._pe_enabled_var)
        pe_chk.grid(row=row, column=1, sticky="w", pady=2)
        pe_chk.bind("<FocusIn>",
                    lambda e: self._pe_set_active("enabled"))
        row += 1

        # Body
        ttk.Label(inner, text="Body:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky="nw", padx=(0, 6), pady=2)
        self._pe_body = tk.Text(inner, height=14, wrap="word",
                                undo=True, font=("", 10))
        self._pe_body.grid(row=row, column=1, sticky="nsew", pady=2)
        inner.rowconfigure(row, weight=1)
        _bind_scroll(self._pe_body)
        self._pe_body.bind("<FocusIn>",
                           lambda e: self._pe_set_active("body"))
        self._pe_body.bind("<KeyRelease>", _pe_mark_dirty)
        row += 1

        # Info line
        self._pe_info_var = tk.StringVar(value="New prompt (unsaved)")
        ttk.Label(inner, textvariable=self._pe_info_var,
                  foreground="#888", font=("", 8)).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row += 1

        # Save buttons
        save_row = ttk.Frame(inner)
        save_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        ttk.Button(save_row, text="Save Prompt", width=14,
                   command=self._pe_save).pack(side="left", padx=2)
        ttk.Button(save_row, text="Save As\u2026", width=12,
                   command=self._pe_save_as).pack(side="left", padx=2)

        pane.add(editor_frame, weight=3)

        # ── Bottom pane: machine view ────────────────────────────────────────
        mv_frame, self._pe_mv_text = _make_machine_view(pane)
        pane.add(mv_frame, weight=2)

    # ── PE: active-field tracking ────────────────────────────────────────────

    def _pe_set_active(self, field: str):
        if self._pe_active_field != field:
            self._pe_active_field = field
            self._pe_mv_dirty = True

    # ── PE: machine view live refresh ────────────────────────────────────────

    def _pe_schedule_mv_refresh(self):
        """Periodic timer that refreshes the machine view when dirty."""
        if self._pe_mv_dirty:
            self._pe_mv_dirty = False
            self._pe_refresh_machine_view()
        try:
            self._pe_mv_after_id = self.frame.after(
                self._MV_INTERVAL, self._pe_schedule_mv_refresh)
        except Exception:
            pass

    def _pe_refresh_machine_view(self):
        self._pe_sync_from_fields()
        _render_json_highlighted(
            self._pe_mv_text, self._pe_record, self._pe_active_field)

    # ── PE: file list ────────────────────────────────────────────────────────

    def _pe_refresh_file_list(self):
        self._pe_lb.delete(0, "end")
        self._pe_files.clear()
        if not self.prompts_dir or not os.path.isdir(self.prompts_dir):
            return
        self._pe_scan_dir(self.prompts_dir, "")

    def _pe_scan_dir(self, base, prefix):
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for name in entries:
            full = os.path.join(base, name)
            if os.path.isdir(full):
                self._pe_scan_dir(full, os.path.join(prefix, name) if prefix else name)
            elif name.endswith(".json"):
                display = os.path.join(prefix, name) if prefix else name
                self._pe_lb.insert("end", display)
                self._pe_files.append(full)

    def _pe_on_select(self, event=None):
        sel = self._pe_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx >= len(self._pe_files): return
        self._pe_load_file(self._pe_files[idx])

    def _pe_load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not load:\n{path}\n\n{exc}"); return
        self._pe_json_path = path
        self._pe_record = _empty_prompt()
        for k, v in data.items():
            if k in self._pe_record:
                self._pe_record[k] = v
        self._pe_populate_fields()
        fname = os.path.basename(path)
        self._pe_info_var.set(f"Loaded: {fname}  |  {path}")
        self._set_status(f"Loaded prompt: {fname}")

    def _pe_populate_fields(self):
        self._pe_title_var.set(self._pe_record.get("title", ""))
        self._pe_tags_var.set(self._pe_record.get("tags", ""))
        self._pe_enabled_var.set(self._pe_record.get("enabled", True))
        self._pe_body.delete("1.0", "end")
        self._pe_body.insert("1.0", self._pe_record.get("body", ""))
        self._pe_mv_dirty = True

    def _pe_sync_from_fields(self):
        self._pe_record["title"]   = self._pe_title_var.get().strip()
        self._pe_record["tags"]    = self._pe_tags_var.get().strip()
        self._pe_record["enabled"] = self._pe_enabled_var.get()
        self._pe_record["body"]    = self._pe_body.get("1.0", "end-1c")

    # ── PE: new / save / delete ──────────────────────────────────────────────

    def _pe_new(self):
        self._pe_record = _empty_prompt()
        self._pe_json_path = ""
        self._pe_populate_fields()
        self._pe_info_var.set("New prompt (unsaved)")
        self._set_status("New prompt ready.")

    def _pe_save(self):
        self._pe_sync_from_fields()
        title = self._pe_record.get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Title is required."); return
        if not self.prompts_dir:
            messagebox.showwarning("Save", "No root set."); return
        self._pe_record["updated_at"] = _now_iso()
        if not self._pe_json_path:
            fname = _safe_filename(title) + ".json"
            path = os.path.join(self.prompts_dir, fname)
            if os.path.exists(path):
                if not messagebox.askyesno("Overwrite?",
                        f"{fname} already exists.\nOverwrite?"):
                    return
            self._pe_json_path = path
        self._pe_write_json(self._pe_json_path)

    def _pe_save_as(self):
        self._pe_sync_from_fields()
        title = self._pe_record.get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Title is required."); return
        if not self.prompts_dir:
            messagebox.showwarning("Save", "No root set."); return
        fname = _safe_filename(title) + ".json"
        path = filedialog.asksaveasfilename(
            initialdir=self.prompts_dir, initialfile=fname,
            title="Save Prompt As", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path: return
        self._pe_record["updated_at"] = _now_iso()
        self._pe_json_path = path
        self._pe_write_json(path)

    def _pe_write_json(self, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._pe_record, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to write:\n{exc}"); return
        fname = os.path.basename(path)
        self._pe_info_var.set(f"Saved: {fname}  |  {path}")
        self._set_status(f"Saved prompt: {fname}")
        self._pe_refresh_file_list()
        self._pe_mv_dirty = True

    def _pe_delete(self):
        sel = self._pe_lb.curselection()
        if not sel:
            messagebox.showinfo("Delete", "Select a prompt to delete."); return
        idx = sel[0]
        if idx >= len(self._pe_files): return
        path = self._pe_files[idx]
        fname = os.path.basename(path)
        if not messagebox.askyesno("Delete",
                f"Delete {fname}?\nThis cannot be undone."):
            return
        try:
            os.remove(path)
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc)); return
        if path == self._pe_json_path:
            self._pe_new()
        self._pe_refresh_file_list()
        self._set_status(f"Deleted: {fname}")

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 2 — PROMPT MAPS
    # ═════════════════════════════════════════════════════════════════════════

    def _build_prompt_maps_tab(self, parent):
        parent.columnconfigure(0, weight=0)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # ── Left: file list ──────────────────────────────────────────────────
        left = ttk.LabelFrame(parent, text="Saved Maps", padding=4)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        btn_row = ttk.Frame(left)
        btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(btn_row, text="New", width=7,
                   command=self._pm_new).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Delete", width=7,
                   command=self._pm_delete).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Refresh", width=7,
                   command=self._pm_refresh_file_list).pack(side="left", padx=2)

        lb_frame, self._pm_lb = _make_listbox(left, height=12)
        lb_frame.grid(row=1, column=0, sticky="nsew")
        self._pm_lb.bind("<<ListboxSelect>>", self._pm_on_select)
        self._pm_files = []

        # ── Right: PanedWindow ───────────────────────────────────────────────
        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.grid(row=0, column=1, sticky="nsew")

        # ── Top pane: editor ─────────────────────────────────────────────────
        editor_frame = ttk.Frame(pane)
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(editor_frame, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(editor_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")
        _bind_scroll(canvas)

        inner = ttk.Frame(canvas, padding=4)
        self._pm_canvas = canvas
        self._pm_inner  = inner
        self._pm_canvas_win = canvas.create_window((0, 0), window=inner,
                                                    anchor="nw")
        inner.columnconfigure(1, weight=1)

        def _on_inner_cfg(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_cfg(event):
            canvas.itemconfigure(self._pm_canvas_win, width=event.width)
        inner.bind("<Configure>", _on_inner_cfg)
        canvas.bind("<Configure>", _on_canvas_cfg)

        row = 0

        def _pm_mark_dirty(*_a):
            self._pm_mv_dirty = True

        # Title
        ttk.Label(inner, text="Title:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        self._pm_title_var = tk.StringVar()
        self._pm_title_var.trace_add("write", _pm_mark_dirty)
        pm_title_entry = ttk.Entry(inner, textvariable=self._pm_title_var,
                                   font=("", 11))
        pm_title_entry.grid(row=row, column=1, sticky="ew", pady=2)
        pm_title_entry.bind("<FocusIn>",
                            lambda e: self._pm_set_active("title"))
        row += 1

        # Tags
        ttk.Label(inner, text="Tags:").grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        self._pm_tags_var = tk.StringVar()
        self._pm_tags_var.trace_add("write", _pm_mark_dirty)
        pm_tags_entry = ttk.Entry(inner, textvariable=self._pm_tags_var)
        pm_tags_entry.grid(row=row, column=1, sticky="ew", pady=2)
        pm_tags_entry.bind("<FocusIn>",
                           lambda e: self._pm_set_active("tags"))
        row += 1

        # Enabled
        self._pm_enabled_var = tk.BooleanVar(value=True)
        self._pm_enabled_var.trace_add("write", _pm_mark_dirty)
        pm_chk = ttk.Checkbutton(inner, text="Enabled",
                                 variable=self._pm_enabled_var)
        pm_chk.grid(row=row, column=1, sticky="w", pady=2)
        pm_chk.bind("<FocusIn>",
                    lambda e: self._pm_set_active("enabled"))
        row += 1

        # ── Block list ───────────────────────────────────────────────────────
        ttk.Separator(inner, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1

        ttk.Label(inner, text="Blocks:",
                  font=("", 10, "bold")).grid(
            row=row, column=0, sticky="nw", padx=(0, 6), pady=2)

        blocks_frame = ttk.Frame(inner)
        blocks_frame.grid(row=row, column=1, sticky="nsew", pady=2)
        blocks_frame.columnconfigure(0, weight=1)
        inner.rowconfigure(row, weight=1)
        row += 1

        blk_lb_frame, self._pm_blk_lb = _make_listbox(blocks_frame, height=6)
        blk_lb_frame.grid(row=0, column=0, sticky="nsew")
        blocks_frame.rowconfigure(0, weight=1)
        self._pm_blk_lb.bind("<FocusIn>",
                             lambda e: self._pm_set_active("blocks"))

        blk_btns = ttk.Frame(blocks_frame)
        blk_btns.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(blk_btns, text="+ Prompt", width=10,
                   command=self._pm_add_prompt_block).pack(side="left", padx=2)
        ttk.Button(blk_btns, text="+ <<user_input>>", width=16,
                   command=self._pm_add_user_input).pack(side="left", padx=2)
        ttk.Button(blk_btns, text="\u25b2 Up", width=6,
                   command=self._pm_move_up).pack(side="left", padx=2)
        ttk.Button(blk_btns, text="\u25bc Down", width=6,
                   command=self._pm_move_down).pack(side="left", padx=2)
        ttk.Button(blk_btns, text="Remove", width=8,
                   command=self._pm_remove_block).pack(side="left", padx=2)

        # ── Assembled preview ────────────────────────────────────────────────
        ttk.Separator(inner, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1

        ttk.Label(inner, text="Preview:",
                  font=("", 10, "bold")).grid(
            row=row, column=0, sticky="nw", padx=(0, 6), pady=2)

        prev_frame = ttk.Frame(inner)
        prev_frame.grid(row=row, column=1, sticky="nsew", pady=2)
        prev_frame.columnconfigure(0, weight=1)
        prev_frame.rowconfigure(0, weight=1)

        self._pm_preview = tk.Text(prev_frame, height=5, wrap="word",
                                   state="disabled", font=("", 9),
                                   background="#f8f8f0")
        prev_sb = ttk.Scrollbar(prev_frame, orient="vertical",
                                command=self._pm_preview.yview)
        self._pm_preview.configure(yscrollcommand=prev_sb.set)
        self._pm_preview.grid(row=0, column=0, sticky="nsew")
        prev_sb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._pm_preview)

        ttk.Button(prev_frame, text="Refresh Preview", width=16,
                   command=self._pm_refresh_preview).grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        row += 1

        # Info + save
        self._pm_info_var = tk.StringVar(value="New map (unsaved)")
        ttk.Label(inner, textvariable=self._pm_info_var,
                  foreground="#888", font=("", 8)).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row += 1

        save_row = ttk.Frame(inner)
        save_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        ttk.Button(save_row, text="Save Map", width=12,
                   command=self._pm_save).pack(side="left", padx=2)
        ttk.Button(save_row, text="Save As\u2026", width=12,
                   command=self._pm_save_as).pack(side="left", padx=2)

        pane.add(editor_frame, weight=3)

        # ── Bottom pane: machine view ────────────────────────────────────────
        mv_frame, self._pm_mv_text = _make_machine_view(pane)
        pane.add(mv_frame, weight=2)

    # ── PM: active-field tracking ────────────────────────────────────────────

    def _pm_set_active(self, field):
        if self._pm_active_field != field:
            self._pm_active_field = field
            self._pm_mv_dirty = True

    # ── PM: machine view live refresh ────────────────────────────────────────

    def _pm_schedule_mv_refresh(self):
        if self._pm_mv_dirty:
            self._pm_mv_dirty = False
            self._pm_refresh_machine_view()
        try:
            self._pm_mv_after_id = self.frame.after(
                self._MV_INTERVAL, self._pm_schedule_mv_refresh)
        except Exception:
            pass

    def _pm_refresh_machine_view(self):
        self._pm_sync_from_fields()
        _render_json_highlighted(
            self._pm_mv_text, self._pm_record, self._pm_active_field)

    # ── PM: file list ────────────────────────────────────────────────────────

    def _pm_refresh_file_list(self):
        self._pm_lb.delete(0, "end")
        self._pm_files.clear()
        if not self.maps_dir or not os.path.isdir(self.maps_dir):
            return
        self._pm_scan_dir(self.maps_dir, "")

    def _pm_scan_dir(self, base, prefix):
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for name in entries:
            full = os.path.join(base, name)
            if os.path.isdir(full):
                self._pm_scan_dir(full, os.path.join(prefix, name) if prefix else name)
            elif name.endswith(".json"):
                display = os.path.join(prefix, name) if prefix else name
                self._pm_lb.insert("end", display)
                self._pm_files.append(full)

    def _pm_on_select(self, event=None):
        sel = self._pm_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx >= len(self._pm_files): return
        self._pm_load_file(self._pm_files[idx])

    def _pm_load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not load:\n{path}\n\n{exc}"); return
        self._pm_json_path = path
        self._pm_record = _empty_map()
        for k, v in data.items():
            if k in self._pm_record:
                self._pm_record[k] = v
        self._pm_populate_fields()
        fname = os.path.basename(path)
        self._pm_info_var.set(f"Loaded: {fname}  |  {path}")
        self._set_status(f"Loaded map: {fname}")

    def _pm_populate_fields(self):
        self._pm_title_var.set(self._pm_record.get("title", ""))
        self._pm_tags_var.set(self._pm_record.get("tags", ""))
        self._pm_enabled_var.set(self._pm_record.get("enabled", True))
        self._pm_refresh_block_list()
        self._pm_mv_dirty = True

    def _pm_sync_from_fields(self):
        self._pm_record["title"]   = self._pm_title_var.get().strip()
        self._pm_record["tags"]    = self._pm_tags_var.get().strip()
        self._pm_record["enabled"] = self._pm_enabled_var.get()

    # ── PM: block operations ─────────────────────────────────────────────────

    def _pm_refresh_block_list(self):
        self._pm_blk_lb.delete(0, "end")
        blocks = self._pm_record.get("blocks", [])
        for i, blk in enumerate(blocks):
            btype = blk.get("type", "?")
            if btype == "user_input":
                label = f"  {i+1}. <<user_input>>"
            elif btype == "prompt_ref":
                ref = blk.get("ref", "(none)")
                label = f"  {i+1}. [prompt] {ref}"
            else:
                label = f"  {i+1}. ({btype})"
            self._pm_blk_lb.insert("end", label)

    def _pm_add_prompt_block(self):
        if not self.prompts_dir or not os.path.isdir(self.prompts_dir):
            messagebox.showinfo("Add Prompt",
                                "No prompts directory found.\nSave a prompt first.")
            return
        available = []
        self._pm_gather_prompts(self.prompts_dir, "", available)
        if not available:
            messagebox.showinfo("Add Prompt", "No saved prompts found.")
            return

        dlg = tk.Toplevel(self.frame)
        dlg.title("Choose Prompt")
        dlg.geometry("400x350")
        dlg.transient(self.frame.winfo_toplevel())
        dlg.grab_set()

        ttk.Label(dlg, text="Select a prompt to add:",
                  padding=6).pack(anchor="w")
        lb_frame, pick_lb = _make_listbox(dlg, height=14)
        lb_frame.pack(fill="both", expand=True, padx=6, pady=2)

        for display, full_path in available:
            pick_lb.insert("end", display)

        def _ok():
            sel = pick_lb.curselection()
            if not sel: return
            idx = sel[0]
            display, full_path = available[idx]
            ref = display
            if ref.endswith(".json"):
                ref = ref[:-5]
            blocks = self._pm_record.get("blocks", [])
            blocks.append({"type": "prompt_ref", "ref": ref})
            self._pm_record["blocks"] = blocks
            self._pm_refresh_block_list()
            self._pm_mv_dirty = True
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        btn_frame = ttk.Frame(dlg, padding=6)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Add", width=10, command=_ok).pack(
            side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", width=10, command=_cancel).pack(
            side="left", padx=4)
        pick_lb.bind("<Double-1>", lambda e: _ok())
        dlg.bind("<Escape>", lambda e: _cancel())

    def _pm_gather_prompts(self, base, prefix, result):
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for name in entries:
            full = os.path.join(base, name)
            if os.path.isdir(full):
                sub = os.path.join(prefix, name) if prefix else name
                self._pm_gather_prompts(full, sub, result)
            elif name.endswith(".json"):
                display = os.path.join(prefix, name) if prefix else name
                result.append((display, full))

    def _pm_add_user_input(self):
        blocks = self._pm_record.get("blocks", [])
        blocks.append({"type": "user_input"})
        self._pm_record["blocks"] = blocks
        self._pm_refresh_block_list()
        self._pm_mv_dirty = True

    def _pm_move_up(self):
        sel = self._pm_blk_lb.curselection()
        if not sel: return
        idx = sel[0]
        blocks = self._pm_record.get("blocks", [])
        if idx <= 0 or idx >= len(blocks): return
        blocks[idx], blocks[idx - 1] = blocks[idx - 1], blocks[idx]
        self._pm_refresh_block_list()
        self._pm_blk_lb.selection_set(idx - 1)
        self._pm_blk_lb.see(idx - 1)
        self._pm_mv_dirty = True

    def _pm_move_down(self):
        sel = self._pm_blk_lb.curselection()
        if not sel: return
        idx = sel[0]
        blocks = self._pm_record.get("blocks", [])
        if idx < 0 or idx >= len(blocks) - 1: return
        blocks[idx], blocks[idx + 1] = blocks[idx + 1], blocks[idx]
        self._pm_refresh_block_list()
        self._pm_blk_lb.selection_set(idx + 1)
        self._pm_blk_lb.see(idx + 1)
        self._pm_mv_dirty = True

    def _pm_remove_block(self):
        sel = self._pm_blk_lb.curselection()
        if not sel: return
        idx = sel[0]
        blocks = self._pm_record.get("blocks", [])
        if 0 <= idx < len(blocks):
            blocks.pop(idx)
            self._pm_refresh_block_list()
            self._pm_mv_dirty = True

    # ── PM: assembled preview ────────────────────────────────────────────────

    def _pm_refresh_preview(self):
        blocks = self._pm_record.get("blocks", [])
        parts = []
        for blk in blocks:
            btype = blk.get("type", "?")
            if btype == "user_input":
                parts.append("<<user_input>>")
            elif btype == "prompt_ref":
                ref = blk.get("ref", "")
                body = self._pm_resolve_prompt_body(ref)
                if body is not None:
                    parts.append(body)
                else:
                    parts.append(f"[unresolved: {ref}]")
            else:
                parts.append(f"[unknown block type: {btype}]")
        assembled = "\n\n---\n\n".join(parts)
        self._pm_preview.configure(state="normal")
        self._pm_preview.delete("1.0", "end")
        self._pm_preview.insert("1.0", assembled if assembled else "(empty map)")
        self._pm_preview.configure(state="disabled")

    def _pm_resolve_prompt_body(self, ref):
        if not self.prompts_dir: return None
        path = os.path.join(self.prompts_dir, ref + ".json")
        if not os.path.isfile(path):
            path = os.path.join(self.prompts_dir, ref)
            if not os.path.isfile(path): return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("body", "")
        except Exception:
            return None

    # ── PM: new / save / delete ──────────────────────────────────────────────

    def _pm_new(self):
        self._pm_record = _empty_map()
        self._pm_json_path = ""
        self._pm_populate_fields()
        self._pm_info_var.set("New map (unsaved)")
        self._set_status("New map ready.")

    def _pm_save(self):
        self._pm_sync_from_fields()
        title = self._pm_record.get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Title is required."); return
        if not self.maps_dir:
            messagebox.showwarning("Save", "No root set."); return
        self._pm_record["updated_at"] = _now_iso()
        if not self._pm_json_path:
            fname = _safe_filename(title) + ".json"
            path = os.path.join(self.maps_dir, fname)
            if os.path.exists(path):
                if not messagebox.askyesno("Overwrite?",
                        f"{fname} already exists.\nOverwrite?"):
                    return
            self._pm_json_path = path
        self._pm_write_json(self._pm_json_path)

    def _pm_save_as(self):
        self._pm_sync_from_fields()
        title = self._pm_record.get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Title is required."); return
        if not self.maps_dir:
            messagebox.showwarning("Save", "No root set."); return
        fname = _safe_filename(title) + ".json"
        path = filedialog.asksaveasfilename(
            initialdir=self.maps_dir, initialfile=fname,
            title="Save Map As", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path: return
        self._pm_record["updated_at"] = _now_iso()
        self._pm_json_path = path
        self._pm_write_json(path)

    def _pm_write_json(self, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._pm_record, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to write:\n{exc}"); return
        fname = os.path.basename(path)
        self._pm_info_var.set(f"Saved: {fname}  |  {path}")
        self._set_status(f"Saved map: {fname}")
        self._pm_refresh_file_list()
        self._pm_mv_dirty = True

    def _pm_delete(self):
        sel = self._pm_lb.curselection()
        if not sel:
            messagebox.showinfo("Delete", "Select a map to delete."); return
        idx = sel[0]
        if idx >= len(self._pm_files): return
        path = self._pm_files[idx]
        fname = os.path.basename(path)
        if not messagebox.askyesno("Delete",
                f"Delete {fname}?\nThis cannot be undone."):
            return
        try:
            os.remove(path)
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc)); return
        if path == self._pm_json_path:
            self._pm_new()
        self._pm_refresh_file_list()
        self._set_status(f"Deleted: {fname}")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Prompt Workshop \u2014 standalone test")
    root.geometry("1050x750")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    page = PagePromptWorkshop(root)
    page.frame.grid(row=0, column=0, sticky="nsew")
    root.mainloop()

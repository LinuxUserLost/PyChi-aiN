"""
page_mdnotes / mdnotes_page.py
────────────────────────────────────────────────────────────────────────────────
Markdown Notes — single-page hybrid workspace for pychiain.

A markdown-first note/log page that saves .md files with YAML frontmatter.
Page-bound prompt buttons pull from /pagepack_pychiain/promptworkshop/ and
insert resolved prompt/prompt_map text into the active text box at cursor.

Layout (single page, three columns):
  [LEFT: Prompt Buttons]  |  [CENTER: YAML + History + Input]  |  [RIGHT: reserved]

Save root:   /pagepack_pychiain/mdnotes/
Source root: /pagepack_pychiain/promptworkshop/

Scroll handling is Linux/Debian/Wayland-safe.
"""

import os
import re
import json
import glob
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ─────────────────────────────────────────────────────────────────────────────
# Scroll helper — Wayland/Linux-safe
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Constants / default dropdown options
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SUBJECT_OPTIONS = [
    "general",
    "linux",
    "python",
    "networking",
    "ai",
    "project",
]

DEFAULT_NOTE_TYPE_OPTIONS = [
    "general_note",
    "class_note",
    "agent_handoff",
    "report",
    "linux_guide",
    "research_note",
]

DEFAULT_SOURCE_OPTIONS = [
    "manual",
    "upload",
    "agent_response",
    "builder_output",
    "mixed",
]

MAX_HISTORY_DISPLAY = 25


# ─────────────────────────────────────────────────────────────────────────────
# Filename helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text, max_len=24):
    """Turn text into a safe filename slug."""
    s = text.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    s = s.strip('_')
    return s[:max_len] if s else "note"


def _make_note_filename(notes_dir, subject_slug):
    """
    Build filename:  2026_wk13_linux_000001.md
    Meaning: full_year, week_number, subject_slug, sequence_number
    """
    now = datetime.datetime.now()
    year = now.strftime("%Y")
    week = now.strftime("%W").zfill(2)
    prefix = f"{year}_wk{week}_{subject_slug}_"

    existing = []
    if os.path.isdir(notes_dir):
        for fname in os.listdir(notes_dir):
            if fname.startswith(prefix) and fname.endswith(".md"):
                num_part = fname[len(prefix):-3]
                try:
                    existing.append(int(num_part))
                except ValueError:
                    pass
    next_num = max(existing, default=0) + 1
    return f"{prefix}{str(next_num).zfill(6)}.md"


def _build_yaml_frontmatter(fields):
    """Build YAML frontmatter string from dict."""
    lines = ["---"]
    for key, val in fields.items():
        if key == "tags" and isinstance(val, list):
            lines.append("tags:")
            for t in val:
                lines.append(f"  - {t.strip()}")
        elif key == "tags" and isinstance(val, str):
            tag_list = [t.strip() for t in val.split(",") if t.strip()]
            if tag_list:
                lines.append("tags:")
                for t in tag_list:
                    lines.append(f"  - {t}")
            else:
                lines.append("tags: []")
        else:
            if isinstance(val, str) and (":" in val or "\n" in val):
                val = f'"{val}"'
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _parse_yaml_frontmatter(text):
    """Simple YAML frontmatter parser. Returns (fields_dict, body_text)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    yaml_block = text[4:end].strip()
    body = text[end + 4:].strip()

    fields = {}
    current_key = None
    current_list = None
    for line in yaml_block.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            continue
        if current_list is not None:
            fields[current_key] = current_list
            current_list = None
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not val:
                current_key = key
                current_list = []
            else:
                fields[key] = val
                current_key = key
    if current_list is not None:
        fields[current_key] = current_list
    return fields, body


# ═════════════════════════════════════════════════════════════════════════════
# SOURCE PICKER POPUP
# ═════════════════════════════════════════════════════════════════════════════

class _SourcePickerPopup(tk.Toplevel):
    """
    Modal popup that browses a directory tree rooted at a prompt source root.
    Shows subdirectories as expandable nodes and JSON prompt/map files as
    selectable items.  Returns the selected (name, type, filepath) on accept.
    """

    def __init__(self, parent, source_root, title="Select Source",
                 type_filter=None):
        """
        Args:
            parent:      parent widget
            source_root: directory to browse
            title:       popup window title
            type_filter: None = show all, "prompt" = prompts only, "map" = maps only
        """
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.resizable(True, True)
        self.geometry("420x460")

        self._source_root = source_root
        self._type_filter = type_filter
        self.result = None  # (name, stype, fpath) or None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Header label ──────────────────────────────────────────────────
        root_short = source_root
        if len(root_short) > 50:
            root_short = "…" + root_short[-47:]
        ttk.Label(self, text=f"Root: {root_short}",
                  font=("", 8), foreground="#555").grid(
            row=0, column=0, sticky="ew", padx=6, pady=(6, 2))

        # ── Treeview ──────────────────────────────────────────────────────
        tree_f = ttk.Frame(self)
        tree_f.grid(row=1, column=0, sticky="nsew", padx=6, pady=2)
        tree_f.columnconfigure(0, weight=1)
        tree_f.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(tree_f, selectmode="browse",
                                  columns=("type",), show="tree headings")
        self._tree.heading("#0", text="Name", anchor="w")
        self._tree.heading("type", text="Type", anchor="w")
        self._tree.column("#0", width=280, minwidth=120)
        self._tree.column("type", width=60, minwidth=50)
        tsb = ttk.Scrollbar(tree_f, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=tsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        tsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._tree)

        self._tree.bind("<Double-1>", lambda e: self._on_accept())

        # ── Button row ────────────────────────────────────────────────────
        btn_f = ttk.Frame(self, padding=(6, 4))
        btn_f.grid(row=2, column=0, sticky="ew")
        btn_f.columnconfigure(0, weight=1)
        ttk.Button(btn_f, text="Select", width=10,
                   command=self._on_accept).pack(side="right", padx=(4, 0))
        ttk.Button(btn_f, text="Cancel", width=8,
                   command=self._on_cancel).pack(side="right")

        # ── Populate ──────────────────────────────────────────────────────
        self._item_map = {}  # tree iid → (name, stype, fpath)
        self._populate()

        # Center on parent
        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        w = self.winfo_width()
        h = self.winfo_height()
        self.geometry(f"+{pw - w // 2}+{ph - h // 2}")

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _populate(self):
        """Walk the source root and build the tree."""
        root = self._source_root
        if not os.path.isdir(root):
            self._tree.insert("", "end", text="(directory not found)",
                              values=("",))
            return

        self._insert_dir("", root)

    def _insert_dir(self, parent_iid, dirpath):
        """Recursively insert a directory's contents into the tree."""
        try:
            entries = sorted(os.listdir(dirpath))
        except OSError:
            return

        # Subdirectories first
        for entry in entries:
            full = os.path.join(dirpath, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                dir_iid = self._tree.insert(
                    parent_iid, "end", text=f"📁 {entry}",
                    values=("folder",), open=False)
                self._insert_dir(dir_iid, full)

        # Then JSON files
        for entry in entries:
            if not entry.endswith(".json"):
                continue
            full = os.path.join(dirpath, entry)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue

            name = data.get("file_name", entry.replace(".json", ""))
            if "slots" in data:
                stype = "map"
            elif "prompt_body" in data:
                stype = "prompt"
            else:
                continue

            if self._type_filter and stype != self._type_filter:
                continue

            tag = "[MAP]" if stype == "map" else "[PRM]"
            display = f"{tag} {name}"
            iid = self._tree.insert(
                parent_iid, "end", text=display,
                values=(stype,))
            self._item_map[iid] = (name, stype, full)

    def _on_accept(self):
        sel = self._tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid in self._item_map:
            self.result = self._item_map[iid]
            self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PAGE CLASS
# ═════════════════════════════════════════════════════════════════════════════

class PageMdNotes:
    """
    Markdown Notes — single-page hybrid workspace.

    Shell contract:
        page = PageMdNotes(parent_widget)
        page.build(parent)
    """

    PAGE_NAME = "markdown_notes"

    def __init__(self, parent, app=None, page_key="", page_folder="", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)
        self.parent = parent
        self.app = app
        self.page_key = page_key
        self.page_folder = page_folder

        # Root paths
        self.pack_root = ""
        self.notes_dir = ""             # /pagepack_pychiain/mdnotes/
        self.prompt_workshop_root = ""  # /pagepack_pychiain/promptworkshop/

        # Prompt sources discovered from promptworkshop/
        self._prompt_sources = []  # list of (name, type, filepath)

        # Note history cache
        self._note_history = []  # list of (filepath, frontmatter_dict, preview)

        # Dropdown option lists (mutable — user can add new values)
        self._subject_options = list(DEFAULT_SUBJECT_OPTIONS)
        self._note_type_options = list(DEFAULT_NOTE_TYPE_OPTIONS)
        self._source_options = list(DEFAULT_SOURCE_OPTIONS)

        # Page-bound button config: list of (display_name, source_name, source_type)
        self._page_buttons = []

        # Build main frame — single page, three columns
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=0)   # left prompt buttons
        self.frame.columnconfigure(1, weight=1)   # center content
        self.frame.columnconfigure(2, weight=0)   # right reserved
        self.frame.rowconfigure(1, weight=1)       # main row

        self._build_top_bar()
        self._build_left_column()
        self._build_center_content()
        self._build_right_reserved()
        self._build_bottom_bar()

        self.frame.after(250, self._auto_find_root)

    # ── Shell mount ──────────────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy()
                self.parent = container
                self.frame = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=0)
                self.frame.columnconfigure(1, weight=1)
                self.frame.columnconfigure(2, weight=0)
                self.frame.rowconfigure(1, weight=1)
                self._build_top_bar()
                self._build_left_column()
                self._build_center_content()
                self._build_right_reserved()
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

    # ═════════════════════════════════════════════════════════════════════════
    # TOP BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_top_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 3))
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        bar.columnconfigure(2, weight=1)
        bar.columnconfigure(5, weight=1)

        # Row 0: auto-find + note save root
        ttk.Button(bar, text="Auto-Find", command=self._auto_find_root,
                   width=10).grid(row=0, column=0, padx=(0, 4), pady=1, sticky="w")

        ttk.Label(bar, text="Notes:", font=("", 8, "bold"),
                  foreground="#446").grid(row=0, column=1, padx=(0, 2), sticky="e")
        self._notes_dir_var = tk.StringVar(value="(not set)")
        ttk.Entry(bar, textvariable=self._notes_dir_var,
                  font=("", 8)).grid(row=0, column=2, sticky="ew", padx=(0, 2))
        ttk.Button(bar, text="…", width=2,
                   command=self._choose_notes_root).grid(
            row=0, column=3, padx=(0, 8), pady=1)

        ttk.Label(bar, text="Prompts:", font=("", 8, "bold"),
                  foreground="#446").grid(row=0, column=4, padx=(0, 2), sticky="e")
        self._prompt_dir_var = tk.StringVar(value="(not set)")
        ttk.Entry(bar, textvariable=self._prompt_dir_var,
                  font=("", 8)).grid(row=0, column=5, sticky="ew", padx=(0, 2))
        ttk.Button(bar, text="…", width=2,
                   command=self._choose_prompt_root).grid(
            row=0, column=6, padx=(0, 4), pady=1)
        ttk.Button(bar, text="Reload All", command=self._reload_all,
                   width=9).grid(row=0, column=7, padx=(0, 0), pady=1)

    # ═════════════════════════════════════════════════════════════════════════
    # LEFT COLUMN — Prompt Buttons + compact config
    # ═════════════════════════════════════════════════════════════════════════

    def _build_left_column(self):
        zone = ttk.LabelFrame(self.frame, text="Prompt Buttons", padding=(4, 4))
        zone.grid(row=1, column=0, sticky="nsew", padx=(6, 2), pady=4)
        zone.columnconfigure(0, weight=1)
        zone.rowconfigure(0, weight=1)

        # Scrollable button area
        canvas = tk.Canvas(zone, width=180, highlightthickness=0)
        vsb = ttk.Scrollbar(zone, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._btn_inner = ttk.Frame(canvas)
        self._btn_canvas = canvas
        self._btn_canvas_win = canvas.create_window(
            (0, 0), window=self._btn_inner, anchor="nw")

        self._btn_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfigure(self._btn_canvas_win, width=e.width))
        _bind_scroll(canvas)

        # ── Controls at bottom ─────────────────────────────────────────
        ctrl = ttk.Frame(zone, padding=(0, 4, 0, 0))
        ctrl.grid(row=1, column=0, columnspan=2, sticky="ew")
        ctrl.columnconfigure(0, weight=1)

        ttk.Button(ctrl, text="+ Add Button",
                   command=self._open_prompt_picker).grid(
            row=0, column=0, sticky="ew", pady=1)
        ttk.Button(ctrl, text="Reload Sources",
                   command=self._reload_prompt_sources).grid(
            row=1, column=0, sticky="ew", pady=1)

        self._btn_widgets = []

    # ── Prompt source scanning ────────────────────────────────────────────

    def _reload_prompt_sources(self):
        """Scan /promptworkshop/ for prompt and prompt_map JSON files."""
        self._prompt_sources = []

        if not self.prompt_workshop_root or \
                not os.path.isdir(self.prompt_workshop_root):
            self._set_status("No promptworkshop directory found.")
            return

        for dirpath, _dirs, files in os.walk(self.prompt_workshop_root):
            for fname in sorted(files):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except Exception:
                    continue

                name = data.get("file_name",
                                fname.replace(".json", ""))
                if "slots" in data:
                    stype = "map"
                elif "prompt_body" in data:
                    stype = "prompt"
                else:
                    continue

                self._prompt_sources.append((name, stype, fpath))

        self._set_status(
            f"Found {len(self._prompt_sources)} prompt source(s).")

    # ── Popup source picker ───────────────────────────────────────────────

    def _open_prompt_picker(self):
        """Open a popup browser to pick a prompt/map source for a new button."""
        if not self.prompt_workshop_root or \
                not os.path.isdir(self.prompt_workshop_root):
            self._set_status(
                "Set a prompt source root first (top bar).")
            return

        popup = _SourcePickerPopup(
            self.frame, self.prompt_workshop_root,
            title="Add Prompt Button", type_filter=None)
        self.frame.wait_window(popup)

        if popup.result is None:
            return
        name, stype, fpath = popup.result

        # Avoid duplicates
        for existing_name, _, _ in self._page_buttons:
            if existing_name == name:
                self._set_status(f"Button '{name}' already exists.")
                return

        self._page_buttons.append((name, name, stype))
        self._rebuild_button_widgets()
        self._set_status(f"Added button: {name}")

    def _rebuild_button_widgets(self):
        """Rebuild the button widget list from self._page_buttons."""
        for w in self._btn_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._btn_widgets = []

        for btn_name, source_name, source_type in self._page_buttons:
            bf = ttk.Frame(self._btn_inner, padding=(1, 1))
            bf.columnconfigure(0, weight=1)

            display = btn_name.replace("_", " ")
            if len(display) > 22:
                display = display[:20] + "…"

            btn = ttk.Button(
                bf, text=display,
                command=lambda sn=source_name, st=source_type:
                    self._fire_prompt_button(sn, st))
            btn.grid(row=0, column=0, sticky="ew")

            ttk.Button(
                bf, text="✕", width=2,
                command=lambda n=btn_name: self._remove_page_button(n)
            ).grid(row=0, column=1, padx=(2, 0))

            bf.pack(fill="x", pady=1)
            self._btn_widgets.append(bf)

    def _remove_page_button(self, btn_name):
        """Remove a page-bound button by name."""
        self._page_buttons = [
            (n, s, t) for n, s, t in self._page_buttons if n != btn_name]
        self._rebuild_button_widgets()
        self._set_status(f"Removed button: {btn_name}")

    # ── Button fire action — INSERT AT CURSOR ─────────────────────────────

    def _fire_prompt_button(self, source_name, source_type):
        """
        Fire a prompt button — load content and INSERT at cursor position
        in the shared text input box.
        """
        content = None
        if source_type == "map":
            content = self._load_map_content(source_name)
        else:
            content = self._load_prompt_field(source_name, "prompt_body")

        if not content:
            self._set_status(f"Could not load: {source_name}")
            return

        # Handle <<user_input>> marker if present
        if "<<user_input>>" in content:
            user_text = self._input_txt.get("1.0", "end-1c").strip()
            content = content.replace("<<user_input>>", user_text)

        # Insert at cursor position
        self._input_txt.insert("insert", content)
        self._input_txt.see("insert")
        self._set_status(f"Inserted: {source_name}")

    # ═════════════════════════════════════════════════════════════════════════
    # RIGHT RESERVED COLUMN
    # ═════════════════════════════════════════════════════════════════════════

    def _build_right_reserved(self):
        """Right column: Map Buttons — quick-fire prompt maps."""
        zone = ttk.LabelFrame(self.frame, text="Map Buttons", padding=(4, 4))
        zone.grid(row=1, column=2, sticky="nsew", padx=(2, 6), pady=4)
        zone.columnconfigure(0, weight=1)
        zone.rowconfigure(0, weight=1)

        # Scrollable map button area
        canvas = tk.Canvas(zone, width=170, highlightthickness=0)
        vsb = ttk.Scrollbar(zone, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._map_btn_inner = ttk.Frame(canvas)
        self._map_btn_canvas = canvas
        self._map_btn_canvas_win = canvas.create_window(
            (0, 0), window=self._map_btn_inner, anchor="nw")

        self._map_btn_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfigure(self._map_btn_canvas_win, width=e.width))
        _bind_scroll(canvas)

        # Quick-add for maps via popup
        ctrl = ttk.Frame(zone, padding=(0, 4, 0, 0))
        ctrl.grid(row=1, column=0, columnspan=2, sticky="ew")
        ctrl.columnconfigure(0, weight=1)

        ttk.Button(ctrl, text="+ Add Map",
                   command=self._open_map_picker).grid(
            row=0, column=0, sticky="ew", pady=1)

        self._map_btn_widgets = []
        self._page_map_buttons = []  # list of (name, source_name)

    def _open_map_picker(self):
        """Open a popup browser to pick a prompt_map source for a new map button."""
        if not self.prompt_workshop_root or \
                not os.path.isdir(self.prompt_workshop_root):
            self._set_status(
                "Set a prompt source root first (top bar).")
            return

        popup = _SourcePickerPopup(
            self.frame, self.prompt_workshop_root,
            title="Add Map Button", type_filter="map")
        self.frame.wait_window(popup)

        if popup.result is None:
            return
        name, stype, fpath = popup.result

        for existing, _ in self._page_map_buttons:
            if existing == name:
                self._set_status(f"Map button '{name}' already exists.")
                return
        self._page_map_buttons.append((name, name))
        self._rebuild_map_button_widgets()
        self._set_status(f"Added map button: {name}")

    def _rebuild_map_button_widgets(self):
        """Rebuild map button widgets."""
        for w in self._map_btn_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._map_btn_widgets = []

        for btn_name, source_name in self._page_map_buttons:
            bf = ttk.Frame(self._map_btn_inner, padding=(1, 1))
            bf.columnconfigure(0, weight=1)

            display = btn_name.replace("_", " ")
            if len(display) > 20:
                display = display[:18] + "…"

            btn = ttk.Button(
                bf, text=display,
                command=lambda sn=source_name: self._fire_map_button(sn))
            btn.grid(row=0, column=0, sticky="ew")
            ttk.Button(
                bf, text="✕", width=2,
                command=lambda n=btn_name: self._remove_map_button(n)
            ).grid(row=0, column=1, padx=(2, 0))
            bf.pack(fill="x", pady=1)
            self._map_btn_widgets.append(bf)

    def _remove_map_button(self, btn_name):
        self._page_map_buttons = [
            (n, s) for n, s in self._page_map_buttons if n != btn_name]
        self._rebuild_map_button_widgets()
        self._set_status(f"Removed map button: {btn_name}")

    def _fire_map_button(self, source_name):
        """Fire a map button — resolve map with <<user_input>> and replace input."""
        content = self._load_map_content(source_name)
        if not content:
            self._set_status(f"Could not load map: {source_name}")
            return
        user_text = self._input_txt.get("1.0", "end-1c").strip()
        if "<<user_input>>" in content:
            assembled = content.replace("<<user_input>>", user_text)
        else:
            assembled = content + ("\n\n" + user_text if user_text else "")
        self._input_txt.delete("1.0", "end")
        self._input_txt.insert("1.0", assembled)
        self._set_status(f"Map applied: {source_name}")

    # ═════════════════════════════════════════════════════════════════════════
    # CENTER CONTENT
    # ═════════════════════════════════════════════════════════════════════════

    def _build_center_content(self):
        center = ttk.Frame(self.frame, padding=(4, 0))
        center.grid(row=1, column=1, sticky="nsew", padx=2, pady=4)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=0)  # yaml header
        center.rowconfigure(1, weight=3)  # note history display
        center.rowconfigure(2, weight=0)  # separator
        center.rowconfigure(3, weight=1)  # input area
        center.rowconfigure(4, weight=0)  # action row

        self._build_yaml_header(center)
        self._build_note_history_display(center)

        ttk.Separator(center, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=(4, 4))

        self._build_input_area(center)
        self._build_action_row(center)

    # ── YAML Header Section ───────────────────────────────────────────────

    def _build_yaml_header(self, parent):
        hdr = ttk.LabelFrame(parent, text="Note Header (YAML)", padding=(6, 3))
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        hdr.columnconfigure(1, weight=1)

        r = 0
        # ── Title (full width) ───────────────────────────────────────────
        ttk.Label(hdr, text="Title:", font=("", 9)).grid(
            row=r, column=0, sticky="e", padx=(0, 4), pady=1)
        self._yaml_title = tk.StringVar(value="")
        ttk.Entry(hdr, textvariable=self._yaml_title).grid(
            row=r, column=1, sticky="ew", pady=1)

        r += 1
        # ── Subject: dropdown | entry | + ────────────────────────────────
        sub_f = ttk.Frame(hdr)
        sub_f.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
        ttk.Label(sub_f, text="Subject:", font=("", 9), width=8,
                  anchor="e").pack(side="left", padx=(0, 4))
        self._yaml_subject = tk.StringVar(value="general")
        self._subject_cb = ttk.Combobox(
            sub_f, textvariable=self._yaml_subject,
            values=self._subject_options, state="readonly", width=14)
        self._subject_cb.pack(side="left", padx=(0, 6))
        self._subject_new_var = tk.StringVar(value="")
        ttk.Entry(sub_f, textvariable=self._subject_new_var,
                  width=12).pack(side="left", padx=(0, 2))
        ttk.Button(sub_f, text="+", width=2,
                   command=self._add_subject).pack(side="left", padx=(0, 12))
        # ── Type on same row ─────────────────────────────────────────────
        ttk.Label(sub_f, text="Type:", font=("", 9)).pack(
            side="left", padx=(0, 4))
        self._yaml_note_type = tk.StringVar(value="general_note")
        self._note_type_cb = ttk.Combobox(
            sub_f, textvariable=self._yaml_note_type,
            values=self._note_type_options, state="readonly", width=14)
        self._note_type_cb.pack(side="left", padx=(0, 6))
        self._type_new_var = tk.StringVar(value="")
        ttk.Entry(sub_f, textvariable=self._type_new_var,
                  width=12).pack(side="left", padx=(0, 2))
        ttk.Button(sub_f, text="+", width=2,
                   command=self._add_note_type).pack(side="left")

        r += 1
        # ── Source: dropdown | entry | + | Tags ──────────────────────────
        src_f = ttk.Frame(hdr)
        src_f.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
        ttk.Label(src_f, text="Source:", font=("", 9), width=8,
                  anchor="e").pack(side="left", padx=(0, 4))
        self._yaml_source = tk.StringVar(value="manual")
        self._source_cb = ttk.Combobox(
            src_f, textvariable=self._yaml_source,
            values=self._source_options, state="readonly", width=14)
        self._source_cb.pack(side="left", padx=(0, 6))
        self._source_new_var = tk.StringVar(value="")
        ttk.Entry(src_f, textvariable=self._source_new_var,
                  width=12).pack(side="left", padx=(0, 2))
        ttk.Button(src_f, text="+", width=2,
                   command=self._add_source).pack(side="left", padx=(0, 12))
        ttk.Label(src_f, text="Tags:", font=("", 9)).pack(
            side="left", padx=(0, 4))
        self._yaml_tags = tk.StringVar(value="")
        ttk.Entry(src_f, textvariable=self._yaml_tags,
                  width=20).pack(side="left", fill="x", expand=True)

    # ── Dropdown add-new helpers ──────────────────────────────────────────

    def _add_subject(self):
        new = self._subject_new_var.get().strip()
        if not new:
            return
        slug = _slugify(new)
        if slug and slug not in self._subject_options:
            self._subject_options.append(slug)
            self._subject_cb.configure(values=self._subject_options)
        self._yaml_subject.set(slug)
        self._subject_new_var.set("")
        self._set_status(f"Subject added: {slug}")

    def _add_note_type(self):
        new = self._type_new_var.get().strip()
        if not new:
            return
        slug = _slugify(new)
        if slug and slug not in self._note_type_options:
            self._note_type_options.append(slug)
            self._note_type_cb.configure(values=self._note_type_options)
        self._yaml_note_type.set(slug)
        self._type_new_var.set("")
        self._set_status(f"Note type added: {slug}")

    def _add_source(self):
        new = self._source_new_var.get().strip()
        if not new:
            return
        slug = _slugify(new)
        if slug and slug not in self._source_options:
            self._source_options.append(slug)
            self._source_cb.configure(values=self._source_options)
        self._yaml_source.set(slug)
        self._source_new_var.set("")
        self._set_status(f"Source added: {slug}")

    # ── Note History Display ──────────────────────────────────────────────

    def _build_note_history_display(self, parent):
        disp_f = ttk.LabelFrame(parent, text="Recent Notes", padding=(6, 4))
        disp_f.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        disp_f.columnconfigure(0, weight=1)
        disp_f.rowconfigure(0, weight=1)

        self._history_txt = tk.Text(
            disp_f, wrap="word", state="disabled",
            font=("Monospace", 10), background="#fafaf5",
            relief="flat", borderwidth=1, padx=8, pady=6)
        hsb = ttk.Scrollbar(disp_f, orient="vertical",
                            command=self._history_txt.yview)
        self._history_txt.configure(yscrollcommand=hsb.set)
        self._history_txt.grid(row=0, column=0, sticky="nsew")
        hsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._history_txt)

        self._history_count_var = tk.StringVar(value="")
        ttk.Label(disp_f, textvariable=self._history_count_var,
                  foreground="#686", font=("", 8),
                  anchor="e").grid(row=1, column=0, columnspan=2,
                                   sticky="e", padx=4)

    # ── Input Area (shared text box — prompt buttons insert here) ─────────

    def _build_input_area(self, parent):
        inp_f = ttk.LabelFrame(parent, text="Input", padding=(6, 4))
        inp_f.grid(row=3, column=0, sticky="nsew", pady=(0, 2))
        inp_f.columnconfigure(0, weight=1)
        inp_f.rowconfigure(0, weight=1)

        self._input_txt = tk.Text(
            inp_f, wrap="word", height=6, undo=True,
            font=("", 11), relief="flat", borderwidth=1,
            padx=8, pady=6, insertwidth=3,
            selectbackground="#c8d8f0")
        isb = ttk.Scrollbar(inp_f, orient="vertical",
                            command=self._input_txt.yview)
        self._input_txt.configure(yscrollcommand=isb.set)
        self._input_txt.grid(row=0, column=0, sticky="nsew")
        isb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._input_txt)

    # ── Action Row ────────────────────────────────────────────────────────

    def _build_action_row(self, parent):
        row = ttk.Frame(parent, padding=(0, 2))
        row.grid(row=4, column=0, sticky="ew")
        row.columnconfigure(6, weight=1)

        ttk.Button(row, text="Submit Note", width=11,
                   command=self._submit_note).grid(
            row=0, column=0, padx=(0, 4))
        ttk.Button(row, text="Copy Input", width=10,
                   command=self._copy_input).grid(
            row=0, column=1, padx=(0, 4))
        ttk.Button(row, text="Clear Input", width=10,
                   command=self._clear_input).grid(
            row=0, column=2, padx=(0, 4))
        ttk.Button(row, text="Upload File…", width=12,
                   command=self._upload_file).grid(
            row=0, column=3, padx=(0, 4))

        ttk.Separator(row, orient="vertical").grid(
            row=0, column=4, sticky="ns", padx=4)

        # Wrap-with-map: select a map, resolve, and insert into text box
        ttk.Label(row, text="Map:", font=("", 8),
                  foreground="#666").grid(row=0, column=5, sticky="e", padx=(0, 4))
        self._map_preset_var = tk.StringVar(value="")
        self._map_preset_cb = ttk.Combobox(
            row, textvariable=self._map_preset_var,
            values=[], width=20, state="readonly")
        self._map_preset_cb.grid(row=0, column=6, sticky="e", padx=(0, 4))
        ttk.Button(row, text="Wrap & Insert", width=11,
                   command=self._wrap_and_insert).grid(row=0, column=7)
        ttk.Button(row, text="Wrap & Send", width=11,
                   command=self._wrap_and_send).grid(row=0, column=8, padx=(4, 0))

    # ═════════════════════════════════════════════════════════════════════════
    # NOTE SAVE FLOW (Submit Note)
    # ═════════════════════════════════════════════════════════════════════════

    def _submit_note(self):
        """Submit current input + YAML header as a new .md note."""
        body = self._input_txt.get("1.0", "end-1c").strip()
        if not body:
            self._set_status("Nothing to submit — type in the input box first.")
            return
        if not self.notes_dir:
            messagebox.showwarning("Submit Note",
                                   "No root set — cannot determine notes directory.")
            return

        os.makedirs(self.notes_dir, exist_ok=True)

        now = datetime.datetime.now()
        subject = self._yaml_subject.get().strip() or "note"
        slug = _slugify(subject)

        # Compute next log number (global across all notes)
        existing_nums = []
        for fname in os.listdir(self.notes_dir):
            if fname.endswith(".md"):
                parts = fname[:-3].rsplit("_", 1)
                if len(parts) == 2:
                    try:
                        existing_nums.append(int(parts[1]))
                    except ValueError:
                        pass
        log_number = max(existing_nums, default=0) + 1

        frontmatter = {
            "title": self._yaml_title.get().strip() or "Untitled Note",
            "subject": subject,
            "log_number": log_number,
            "created_at": now.isoformat(timespec="seconds"),
            "updated_at": now.isoformat(timespec="seconds"),
            "note_type": self._yaml_note_type.get(),
            "tags": self._yaml_tags.get(),
            "source": self._yaml_source.get(),
        }

        fname = _make_note_filename(self.notes_dir, slug)
        fpath = os.path.join(self.notes_dir, fname)

        yaml_str = _build_yaml_frontmatter(frontmatter)
        full_content = yaml_str + "\n\n" + body + "\n"

        try:
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(full_content)
            self._set_status(f"Submitted: {fname}")
            self._refresh_note_history()
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save note:\n{exc}")

    def _upload_file(self):
        """Upload a text file — places content into input for save."""
        fpath = filedialog.askopenfilename(
            title="Upload File",
            filetypes=[("Text/Markdown", "*.txt *.md *.log"),
                       ("All files", "*.*")])
        if not fpath:
            return
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception as exc:
            messagebox.showerror("Read Error", str(exc))
            return

        self._input_txt.delete("1.0", "end")
        self._input_txt.insert("1.0", content)
        self._yaml_source.set("upload")
        if not self._yaml_title.get().strip():
            self._yaml_title.set(os.path.basename(fpath))
        self._set_status(
            f"Loaded file: {os.path.basename(fpath)} — press Submit Note.")

    def _copy_input(self):
        """Copy current input text to clipboard."""
        text = self._input_txt.get("1.0", "end-1c").strip()
        if not text:
            self._set_status("Nothing to copy.")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status(f"Copied {len(text)} chars to clipboard.")
        except Exception as exc:
            self._set_status(f"Clipboard error: {exc}")

    def _clear_input(self):
        self._input_txt.delete("1.0", "end")
        self._set_status("Input cleared.")

    def _wrap_and_insert(self):
        """Wrap input with selected map and replace input with result."""
        preset = self._map_preset_var.get()
        if not preset:
            self._set_status("Select a map preset first.")
            return
        resolved = self._load_map_content(preset)
        if resolved is None:
            self._set_status(f"Could not load map: {preset}")
            return

        user_text = self._input_txt.get("1.0", "end-1c").strip()
        if "<<user_input>>" in resolved:
            assembled = resolved.replace("<<user_input>>", user_text)
        else:
            assembled = resolved + ("\n\n" + user_text if user_text else "")

        self._input_txt.delete("1.0", "end")
        self._input_txt.insert("1.0", assembled)
        self._set_status(f"Wrapped — {len(assembled)} chars in input.")

    def _wrap_and_send(self):
        """Wrap input with selected map, replace input, and submit as note."""
        preset = self._map_preset_var.get()
        if not preset:
            self._set_status("Select a map preset first.")
            return
        resolved = self._load_map_content(preset)
        if resolved is None:
            self._set_status(f"Could not load map: {preset}")
            return

        user_text = self._input_txt.get("1.0", "end-1c").strip()
        if "<<user_input>>" in resolved:
            assembled = resolved.replace("<<user_input>>", user_text)
        else:
            assembled = resolved + ("\n\n" + user_text if user_text else "")

        self._input_txt.delete("1.0", "end")
        self._input_txt.insert("1.0", assembled)
        # Auto-submit the note
        self._submit_note()

    # ═════════════════════════════════════════════════════════════════════════
    # NOTE HISTORY
    # ═════════════════════════════════════════════════════════════════════════

    def _refresh_note_history(self):
        """Reload and display the most recent N notes from disk."""
        self._note_history = []
        if not self.notes_dir or not os.path.isdir(self.notes_dir):
            self._render_history()
            return

        md_files = sorted(
            glob.glob(os.path.join(self.notes_dir, "*.md")),
            key=os.path.getmtime, reverse=True
        )[:MAX_HISTORY_DISPLAY]

        for fpath in md_files:
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    text = fh.read()
                fm, body = _parse_yaml_frontmatter(text)
                preview = body[:200].replace("\n", " ").strip()
                self._note_history.append((fpath, fm, preview))
            except Exception:
                self._note_history.append((fpath, {}, "(read error)"))

        self._render_history()

    def _render_history(self):
        """Render note history into the display text widget."""
        self._history_txt.configure(state="normal")
        self._history_txt.delete("1.0", "end")

        if not self._note_history:
            self._history_txt.insert("1.0", "(no saved notes yet)")
        else:
            for i, (fpath, fm, preview) in enumerate(self._note_history):
                if i > 0:
                    self._history_txt.insert("end", "\n─────────────────────\n")
                fname = os.path.basename(fpath)
                title = fm.get("title", "Untitled")
                created = fm.get("created_at", "")
                ntype = fm.get("note_type", "")
                tags = fm.get("tags", "")
                if isinstance(tags, list):
                    tags = ", ".join(tags)

                header = f"[{fname}]  {title}"
                if ntype:
                    header += f"  ({ntype})"
                self._history_txt.insert("end", header + "\n")
                if created:
                    self._history_txt.insert("end", f"  created: {created}")
                    if tags:
                        self._history_txt.insert("end", f"  |  tags: {tags}")
                    self._history_txt.insert("end", "\n")
                if preview:
                    self._history_txt.insert("end", f"  {preview}\n")

        self._history_txt.configure(state="disabled")
        self._history_count_var.set(
            f"{len(self._note_history)} note(s) shown (max {MAX_HISTORY_DISPLAY})")

    # ═════════════════════════════════════════════════════════════════════════
    # PROMPT / MAP LOADING
    # ═════════════════════════════════════════════════════════════════════════

    def _load_prompt_field(self, prompt_ref, field="prompt_body"):
        """Load a single field from a prompt record in promptworkshop/."""
        if not self.prompt_workshop_root:
            return ""
        target = prompt_ref.strip()
        target_fname = target if target.endswith(".json") else target + ".json"

        for dirpath, _dirs, files in os.walk(self.prompt_workshop_root):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                if fname == target_fname:
                    try:
                        with open(os.path.join(dirpath, fname), "r",
                                  encoding="utf-8") as fh:
                            return str(json.load(fh).get(field, ""))
                    except Exception:
                        return ""
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if data.get("file_name", "").strip() == target:
                        return str(data.get(field, ""))
                except Exception:
                    pass
        return ""

    def _load_map_content(self, map_name):
        """Load a saved prompt map and return its assembled slot content."""
        if not self.prompt_workshop_root:
            return None

        for dirpath, _dirs, files in os.walk(self.prompt_workshop_root):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if "slots" not in data:
                        continue
                    name = data.get("file_name",
                                    fname.replace(".json", ""))
                    if name == map_name or \
                            fname.replace(".json", "") == map_name:
                        return self._assemble_map(data)
                except Exception:
                    pass
        return None

    def _assemble_map(self, map_data):
        """Assemble a map's slots into a single text block."""
        slots = map_data.get("slots", [])
        parts = []
        for slot in slots:
            stype = slot.get("slot_type", "prompt")
            if stype == "header":
                parts.append(slot.get("header_text", ""))
            else:
                content = slot.get("resolved_content", "")
                if content:
                    parts.append(content)
                else:
                    ref = slot.get("prompt_ref", "")
                    field = slot.get("pull_field", "prompt_body")
                    if ref:
                        parts.append(f"<<{ref}.{field}>>")
                    else:
                        parts.append("<<unassigned>>")
        return "\n\n".join(parts)

    # ═════════════════════════════════════════════════════════════════════════
    # MAP PRESET LOADING (for Wrap & Insert combobox)
    # ═════════════════════════════════════════════════════════════════════════

    def _reload_map_presets(self):
        """Populate the Wrap & Insert map combobox from prompt sources."""
        map_names = []
        for name, stype, _ in self._prompt_sources:
            if stype == "map":
                map_names.append(name)
        self._map_preset_cb.configure(values=map_names)

    # ═════════════════════════════════════════════════════════════════════════
    # BOTTOM BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.frame, padding=(6, 2))
        bar.grid(row=2, column=0, columnspan=3, sticky="ew")
        bar.columnconfigure(1, weight=1)

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self._status_var,
                  anchor="w", foreground="#555",
                  font=("", 9)).grid(row=0, column=1, sticky="ew")

    def _set_status(self, msg):
        self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # ROOT FINDING
    # ═════════════════════════════════════════════════════════════════════════

    def _auto_find_root(self):
        from pathlib import Path
        page_file = Path(__file__).resolve()
        for candidate in [page_file.parent, *page_file.parents]:
            if (candidate / "promptworkshop").is_dir() or \
               (candidate / "promptlibrary").is_dir() or \
               (candidate / "index_pychiain").is_dir() or \
               candidate.name == "pagepack_pychiain":
                self._set_root(str(candidate))
                self._set_status(f"Auto-found root: {candidate}")
                return
        cwd = os.getcwd()
        parts = [p for p in cwd.split(os.sep) if p]
        for i in range(len(parts), 0, -1):
            probe = os.sep + os.path.join(*parts[:i])
            if os.path.isdir(os.path.join(probe, "pagepack_pychiain")):
                self._set_root(os.path.join(probe, "pagepack_pychiain"))
                return
            if os.path.basename(probe) == "pagepack_pychiain":
                self._set_root(probe)
                return
        self._set_status(
            "Root not found — use browse buttons to set paths manually.")

    def _choose_notes_root(self):
        """Let user choose the notes save directory independently."""
        d = filedialog.askdirectory(title="Select Notes Save Directory")
        if d:
            self.notes_dir = d
            os.makedirs(self.notes_dir, exist_ok=True)
            self._notes_dir_var.set(d)
            self._refresh_note_history()
            self._set_status(f"Notes root set: {d}")

    def _choose_prompt_root(self):
        """Let user choose the prompt source directory independently."""
        d = filedialog.askdirectory(title="Select Prompt Source Directory")
        if d:
            self.prompt_workshop_root = d
            self._prompt_dir_var.set(d)
            self._reload_prompt_sources()
            self._reload_map_presets()
            self._set_status(f"Prompt root set: {d}")

    def _set_root(self, pack_path):
        self.pack_root = pack_path
        self.notes_dir = os.path.join(pack_path, "mdnotes")
        self.prompt_workshop_root = os.path.join(pack_path, "promptworkshop")

        # Ensure notes directory exists
        os.makedirs(self.notes_dir, exist_ok=True)

        # Update path display vars
        self._notes_dir_var.set(self.notes_dir)
        self._prompt_dir_var.set(self.prompt_workshop_root)

        self._reload_all()

    def _reload_all(self):
        """Reload prompt sources, map presets, and note history."""
        self._reload_prompt_sources()
        self._reload_map_presets()
        self._refresh_note_history()

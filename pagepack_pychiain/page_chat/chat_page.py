"""
page_chat / chat_page.py
────────────────────────────────────────────────────────────────────────────────
Chat / workstation page for pychiain.

This is the live workstation page. It is where uploaded text, saved prompt
maps/presets, user input, and quick action controls come together.

This page USES saved sources. It does not build the deep hotkey system.

Layout (top to bottom, three columns):
  [LEFT HOTKEYS]  |  [CENTER CONTENT]  |  [RIGHT HOTKEYS]

Center content:
  1. Display window (mirrors uploaded text, scrollable)
  2. Upload input area (thin, utilitarian)
  3. Upload action row (Send | Save Log | Clear | preset selector)
  4. Main user input area (larger, inviting)
  5. User input action row (buttons + preset selector)

Left zone:  Prompt Maps — numbered hotkey slots for mapped prompts
Right zone: Quick Presets — mini presets, helper fills, formatting

Log save contract:
  - Upload goes to display first
  - Temp/log flow happens first
  - Final .md save only on explicit button press
  - Save dir: /pagepack_pychiain/uploadedtxt/
  - Filename: yr_(daycount)_log001.md  e.g. 26_048_log001.md

Scroll handling is Linux/Debian/Wayland-safe.
"""

import os
import json
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
# Hotkey slot data
# ─────────────────────────────────────────────────────────────────────────────

def _new_hotkey_slot(number=1):
    return {
        "number":     number,
        "label":      "",
        "source_ref": "",     # filename or path to saved source record
        "source_type": "",    # "prompt_map" | "preset" | ""
    }


# ─────────────────────────────────────────────────────────────────────────────
# Log filename helper
# ─────────────────────────────────────────────────────────────────────────────

def _make_log_filename(log_dir):
    """
    Build filename: yr_(daycount)_log001.md
    Scans existing files to find next increment.
    """
    now = datetime.datetime.now()
    yr = now.strftime("%y")
    day_of_year = now.timetuple().tm_yday
    daycount = str(day_of_year).zfill(3)
    prefix = f"{yr}_{daycount}_log"

    # Find next available number
    existing = []
    if os.path.isdir(log_dir):
        for fname in os.listdir(log_dir):
            if fname.startswith(prefix) and fname.endswith(".md"):
                # Extract number part
                num_part = fname[len(prefix):-3]
                try:
                    existing.append(int(num_part))
                except ValueError:
                    pass
    next_num = max(existing, default=0) + 1
    return f"{prefix}{str(next_num).zfill(3)}.md"


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PAGE CLASS
# ═════════════════════════════════════════════════════════════════════════════

class PageChat:
    """
    Chat / workstation page for pychiain.

    Shell contract:
        page = PageChat(parent_widget)
        page.build(parent)
    """

    PAGE_NAME = "chat"

    def __init__(self, parent, app=None, page_key="", page_folder="", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)
        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # Root paths
        self.pack_root       = ""
        self.log_dir         = ""   # /pagepack_pychiain/uploadedtxt/
        self.prompt_json_root = ""  # for browsing prompts/maps

        # Upload state
        self._upload_buffer = ""     # current uploaded text (temp)
        self._last_log_path = ""     # path of last saved log

        # Hotkey slots — left and right zones
        self._left_slots  = []   # prompt map hotkeys
        self._right_slots = []   # quick preset hotkeys

        # Preset lists (populated from disk)
        self._map_presets    = []  # filenames from maps/active + maps/templates
        self._upload_presets = []  # placeholder for upload-side presets

        # Build
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=0)   # left hotkeys
        self.frame.columnconfigure(1, weight=1)   # center content
        self.frame.columnconfigure(2, weight=0)   # right hotkeys
        self.frame.rowconfigure(1, weight=1)       # main row

        self._build_top_bar()
        self._build_main_layout()
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
                self._build_main_layout()
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
        bar = ttk.Frame(self.frame, padding=(4, 4))
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        bar.columnconfigure(99, weight=1)

        def _btn(col, text, cmd, width=None):
            w = width or (len(text) + 2)
            ttk.Button(bar, text=text, command=cmd, width=w).grid(
                row=0, column=col, padx=2, pady=2, sticky="w")

        _btn(0, "Auto-Find Root",  self._auto_find_root, width=16)
        _btn(1, "Choose Root\u2026", self._choose_root, width=14)
        _btn(2, "Reload Presets",  self._reload_presets, width=14)

        ttk.Separator(bar, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=6)

        self._path_var = tk.StringVar(
            value="No root set \u2014 use Auto-Find Root or Choose Root\u2026")
        ttk.Label(bar, textvariable=self._path_var,
                  anchor="w", foreground="#555",
                  font=("", 9)).grid(row=0, column=99, sticky="ew", padx=4)

    # ═════════════════════════════════════════════════════════════════════════
    # MAIN 3-COLUMN LAYOUT
    # ═════════════════════════════════════════════════════════════════════════

    def _build_main_layout(self):
        self._build_left_hotkey_zone()
        self._build_center_content()
        self._build_right_hotkey_zone()

    # ─────────────────────────────────────────────────────────────────────────
    # LEFT HOTKEY ZONE — Prompt Maps
    # ─────────────────────────────────────────────────────────────────────────

    def _build_left_hotkey_zone(self):
        zone = ttk.LabelFrame(self.frame, text="Prompt Maps", padding=(4, 4))
        zone.grid(row=1, column=0, sticky="nsew", padx=(6, 2), pady=4)
        zone.columnconfigure(0, weight=1)
        zone.rowconfigure(0, weight=1)

        # Scrollable slot list
        canvas = tk.Canvas(zone, width=170, highlightthickness=0)
        vsb = ttk.Scrollbar(zone, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._left_inner = ttk.Frame(canvas)
        self._left_canvas = canvas
        self._left_canvas_win = canvas.create_window((0, 0), window=self._left_inner, anchor="nw")

        self._left_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfigure(self._left_canvas_win, width=e.width))
        _bind_scroll(canvas)

        # Controls
        ctrl = ttk.Frame(zone, padding=(0, 4, 0, 0))
        ctrl.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(ctrl, text="+ Add Slot", command=self._add_left_slot).pack(
            fill="x", pady=1)
        ttk.Button(ctrl, text="Clear All", command=self._clear_left_slots).pack(
            fill="x", pady=1)

        self._left_slot_widgets = []

    # ─────────────────────────────────────────────────────────────────────────
    # RIGHT HOTKEY ZONE — Quick Presets
    # ─────────────────────────────────────────────────────────────────────────

    def _build_right_hotkey_zone(self):
        zone = ttk.LabelFrame(self.frame, text="Quick Presets", padding=(4, 4))
        zone.grid(row=1, column=2, sticky="nsew", padx=(2, 6), pady=4)
        zone.columnconfigure(0, weight=1)
        zone.rowconfigure(0, weight=1)

        canvas = tk.Canvas(zone, width=170, highlightthickness=0)
        vsb = ttk.Scrollbar(zone, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._right_inner = ttk.Frame(canvas)
        self._right_canvas = canvas
        self._right_canvas_win = canvas.create_window((0, 0), window=self._right_inner, anchor="nw")

        self._right_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfigure(self._right_canvas_win, width=e.width))
        _bind_scroll(canvas)

        ctrl = ttk.Frame(zone, padding=(0, 4, 0, 0))
        ctrl.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(ctrl, text="+ Add Slot", command=self._add_right_slot).pack(
            fill="x", pady=1)
        ttk.Button(ctrl, text="Clear All", command=self._clear_right_slots).pack(
            fill="x", pady=1)

        self._right_slot_widgets = []

    # ─────────────────────────────────────────────────────────────────────────
    # CENTER CONTENT
    # ─────────────────────────────────────────────────────────────────────────

    def _build_center_content(self):
        center = ttk.Frame(self.frame, padding=(4, 0))
        center.grid(row=1, column=1, sticky="nsew", padx=2, pady=4)
        center.columnconfigure(0, weight=1)
        # Weight distribution: display gets more, upload thin, main input gets most
        center.rowconfigure(0, weight=3)   # display window
        center.rowconfigure(1, weight=0)   # upload input
        center.rowconfigure(2, weight=0)   # upload action row
        center.rowconfigure(3, weight=0)   # separator
        center.rowconfigure(4, weight=4)   # main user input
        center.rowconfigure(5, weight=0)   # user input action row

        self._build_display_window(center)
        self._build_upload_area(center)
        self._build_upload_action_row(center)

        ttk.Separator(center, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=(6, 6))

        self._build_main_input_area(center)
        self._build_input_action_row(center)

    # ── Display Window ────────────────────────────────────────────────────

    def _build_display_window(self, parent):
        disp_f = ttk.LabelFrame(parent, text="Display", padding=(6, 4))
        disp_f.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        disp_f.columnconfigure(0, weight=1)
        disp_f.rowconfigure(0, weight=1)

        self._display_txt = tk.Text(
            disp_f, wrap="word", state="disabled",
            font=("Monospace", 10), background="#fafaf5",
            relief="flat", borderwidth=1, padx=8, pady=6)
        dsb = ttk.Scrollbar(disp_f, orient="vertical",
                            command=self._display_txt.yview)
        self._display_txt.configure(yscrollcommand=dsb.set)
        self._display_txt.grid(row=0, column=0, sticky="nsew")
        dsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._display_txt)

        # Save marker label
        self._display_marker_var = tk.StringVar(value="")
        ttk.Label(disp_f, textvariable=self._display_marker_var,
                  foreground="#686", font=("", 8),
                  anchor="e").grid(row=1, column=0, columnspan=2,
                                   sticky="e", padx=4)

    # ── Upload Input Area ─────────────────────────────────────────────────

    def _build_upload_area(self, parent):
        upl_f = ttk.LabelFrame(parent, text="Upload / Paste", padding=(4, 2))
        upl_f.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        upl_f.columnconfigure(0, weight=1)
        upl_f.rowconfigure(0, weight=1)

        self._upload_txt = tk.Text(
            upl_f, wrap="word", height=2, undo=True,
            font=("", 9), relief="flat", borderwidth=1, padx=6, pady=4)
        usb = ttk.Scrollbar(upl_f, orient="vertical",
                            command=self._upload_txt.yview)
        self._upload_txt.configure(yscrollcommand=usb.set)
        self._upload_txt.grid(row=0, column=0, sticky="nsew")
        usb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._upload_txt)

    # ── Upload Action Row ─────────────────────────────────────────────────

    def _build_upload_action_row(self, parent):
        row = ttk.Frame(parent, padding=(0, 2))
        row.grid(row=2, column=0, sticky="ew")
        row.columnconfigure(4, weight=1)

        ttk.Button(row, text="Send", width=7,
                   command=self._upload_send).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(row, text="Save Log", width=9,
                   command=self._upload_save_log).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(row, text="Clear", width=7,
                   command=self._upload_clear).grid(row=0, column=2, padx=(0, 8))

        ttk.Separator(row, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=4)

        # Small preset selector
        ttk.Label(row, text="Preset:", font=("", 8),
                  foreground="#666").grid(row=0, column=4, sticky="e", padx=(0, 4))
        self._upload_preset_var = tk.StringVar(value="")
        self._upload_preset_cb = ttk.Combobox(
            row, textvariable=self._upload_preset_var,
            values=[], width=20, state="readonly")
        self._upload_preset_cb.grid(row=0, column=5, sticky="e", padx=(0, 4))
        ttk.Button(row, text="Apply", width=6,
                   command=self._apply_upload_preset).grid(row=0, column=6)

    # ── Main User Input Area ──────────────────────────────────────────────

    def _build_main_input_area(self, parent):
        inp_f = ttk.LabelFrame(parent, text="User Input", padding=(6, 4))
        inp_f.grid(row=4, column=0, sticky="nsew", pady=(0, 4))
        inp_f.columnconfigure(0, weight=1)
        inp_f.rowconfigure(0, weight=1)

        # Slightly softer styling — larger, more inviting
        self._input_txt = tk.Text(
            inp_f, wrap="word", height=8, undo=True,
            font=("", 11), relief="flat", borderwidth=1,
            padx=10, pady=8, insertwidth=3,
            selectbackground="#c8d8f0")
        isb = ttk.Scrollbar(inp_f, orient="vertical",
                            command=self._input_txt.yview)
        self._input_txt.configure(yscrollcommand=isb.set)
        self._input_txt.grid(row=0, column=0, sticky="nsew")
        isb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._input_txt)

    # ── User Input Action Row ─────────────────────────────────────────────

    def _build_input_action_row(self, parent):
        row = ttk.Frame(parent, padding=(0, 2))
        row.grid(row=5, column=0, sticky="ew")
        row.columnconfigure(4, weight=1)

        ttk.Button(row, text="Copy Output", width=12,
                   command=self._copy_assembled).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(row, text="Clear Input", width=10,
                   command=self._clear_input).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(row, text="Wrap with Map", width=14,
                   command=self._wrap_with_map).grid(row=0, column=2, padx=(0, 8))

        ttk.Separator(row, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=4)

        ttk.Label(row, text="Preset:", font=("", 8),
                  foreground="#666").grid(row=0, column=4, sticky="e", padx=(0, 4))
        self._input_preset_var = tk.StringVar(value="")
        self._input_preset_cb = ttk.Combobox(
            row, textvariable=self._input_preset_var,
            values=[], width=20, state="readonly")
        self._input_preset_cb.grid(row=0, column=5, sticky="e", padx=(0, 4))
        ttk.Button(row, text="Apply", width=6,
                   command=self._apply_input_preset).grid(row=0, column=6)

    # ═════════════════════════════════════════════════════════════════════════
    # BOTTOM BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.frame, padding=(6, 2))
        bar.grid(row=2, column=0, columnspan=3, sticky="ew")
        bar.columnconfigure(1, weight=1)

        ttk.Button(bar, text="Save Log", command=self._upload_save_log,
                   width=10).grid(row=0, column=0, padx=(0, 10))

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self._status_var,
                  anchor="w", foreground="#555",
                  font=("", 9)).grid(row=0, column=1, sticky="ew")

    def _set_status(self, msg):
        self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # UPLOAD FLOW
    # ═════════════════════════════════════════════════════════════════════════

    def _upload_send(self):
        """Send upload text to display window."""
        text = self._upload_txt.get("1.0", "end-1c").strip()
        if not text:
            self._set_status("Nothing to send \u2014 paste or type in the upload box.")
            return
        self._upload_buffer = text

        # Write to display
        self._display_txt.configure(state="normal")
        # Append with separator if display already has content
        existing = self._display_txt.get("1.0", "end-1c").strip()
        if existing:
            self._display_txt.insert("end", "\n\n\u2500\u2500\u2500 new upload \u2500\u2500\u2500\n\n")
        self._display_txt.insert("end", text)
        self._display_txt.configure(state="disabled")
        self._display_txt.see("end")

        self._display_marker_var.set("")
        self._set_status(f"Uploaded {len(text)} chars to display.")

    def _upload_clear(self):
        """Clear the upload input box."""
        self._upload_txt.delete("1.0", "end")
        self._set_status("Upload box cleared.")

    def _upload_save_log(self):
        """Save current display content to .md log file."""
        content = self._display_txt.get("1.0", "end-1c").strip()
        if not content:
            self._set_status("Nothing in display to save.")
            return
        if not self.log_dir:
            messagebox.showwarning("Save Log", "No root set \u2014 cannot determine log directory.")
            return

        os.makedirs(self.log_dir, exist_ok=True)
        fname = _make_log_filename(self.log_dir)
        fpath = os.path.join(self.log_dir, fname)

        # Build log content
        now = datetime.datetime.now()
        lines = [
            f"# Upload Log \u2014 {fname}",
            f"**Saved:** {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            content,
            "",
            "---",
            "*pychiain upload log*",
        ]
        log_text = "\n".join(lines) + "\n"

        try:
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(log_text)
            self._last_log_path = fpath
            self._display_marker_var.set(
                f"\u2713 saved: {fname}  ({now.strftime('%H:%M:%S')})")
            self._set_status(f"Log saved: {fpath}")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save log:\n{exc}")

    # ═════════════════════════════════════════════════════════════════════════
    # USER INPUT ACTIONS
    # ═════════════════════════════════════════════════════════════════════════

    def _clear_input(self):
        self._input_txt.delete("1.0", "end")
        self._set_status("Input cleared.")

    def _copy_assembled(self):
        """Copy display + input content to clipboard."""
        display = self._display_txt.get("1.0", "end-1c").strip()
        user_input = self._input_txt.get("1.0", "end-1c").strip()
        parts = [p for p in [display, user_input] if p]
        if not parts:
            self._set_status("Nothing to copy.")
            return
        assembled = "\n\n".join(parts)
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(assembled)
            self._set_status(f"Copied {len(assembled)} chars to clipboard.")
        except Exception as exc:
            self._set_status(f"Clipboard error: {exc}")

    def _wrap_with_map(self):
        """Wrap user input with a selected prompt map's resolved content."""
        preset = self._input_preset_var.get()
        if not preset:
            self._set_status("Select a preset/map first.")
            return
        # Try to load the map and wrap user input into its slot structure
        user_text = self._input_txt.get("1.0", "end-1c")
        resolved = self._load_map_content(preset)
        if resolved is None:
            self._set_status(f"Could not load map: {preset}")
            return
        # Replace <<user_input>> placeholder if present, else append
        if "<<user_input>>" in resolved:
            wrapped = resolved.replace("<<user_input>>", user_text)
        else:
            wrapped = resolved + "\n\n" + user_text
        self._input_txt.delete("1.0", "end")
        self._input_txt.insert("1.0", wrapped)
        self._set_status(f"Wrapped input with map: {preset}")

    def _load_map_content(self, map_name):
        """Load a saved prompt map and return its assembled slot content."""
        if not self.pack_root:
            return None
        mapper_root = os.path.join(self.pack_root, "promptlibrary", "promptmapper")
        # Search in active then templates
        for subdir in ["json/maps/active", "json/maps/templates"]:
            search_dir = os.path.join(mapper_root, subdir)
            if not os.path.isdir(search_dir):
                continue
            for dirpath, _dirs, files in os.walk(search_dir):
                for fname in files:
                    if not fname.endswith(".json"):
                        continue
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                        name = data.get("file_name", fname.replace(".json", ""))
                        if name == map_name or fname.replace(".json", "") == map_name:
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
    # PRESET LOADING
    # ═════════════════════════════════════════════════════════════════════════

    def _apply_upload_preset(self):
        """Apply selected upload preset (placeholder for phase 1)."""
        preset = self._upload_preset_var.get()
        if not preset:
            self._set_status("Select an upload preset first.")
            return
        # Phase 1: upload presets are simple — load the map and show in display
        content = self._load_map_content(preset)
        if content is not None:
            self._display_txt.configure(state="normal")
            existing = self._display_txt.get("1.0", "end-1c").strip()
            if existing:
                self._display_txt.insert("end", "\n\n\u2500\u2500\u2500 preset applied \u2500\u2500\u2500\n\n")
            self._display_txt.insert("end", content)
            self._display_txt.configure(state="disabled")
            self._set_status(f"Upload preset applied: {preset}")
        else:
            self._set_status(f"Could not load preset: {preset}")

    def _apply_input_preset(self):
        """Apply selected input preset — wrap or insert into user input."""
        preset = self._input_preset_var.get()
        if not preset:
            self._set_status("Select an input preset first.")
            return
        self._wrap_with_map()

    def _reload_presets(self):
        """Scan disk for available maps/presets and populate comboboxes."""
        self._map_presets = []
        if not self.pack_root:
            return
        mapper_root = os.path.join(self.pack_root, "promptlibrary", "promptmapper")
        for subdir in ["json/maps/active", "json/maps/templates"]:
            search_dir = os.path.join(mapper_root, subdir)
            if not os.path.isdir(search_dir):
                continue
            for dirpath, _dirs, files in os.walk(search_dir):
                for fname in sorted(files):
                    if not fname.endswith(".json"):
                        continue
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                        name = data.get("file_name", fname.replace(".json", ""))
                        if name and name not in self._map_presets:
                            self._map_presets.append(name)
                    except Exception:
                        pass

        self._upload_preset_cb.configure(values=self._map_presets)
        self._input_preset_cb.configure(values=self._map_presets)
        self._set_status(f"Loaded {len(self._map_presets)} preset(s).")

    # ═════════════════════════════════════════════════════════════════════════
    # HOTKEY SLOT MANAGEMENT
    # ═════════════════════════════════════════════════════════════════════════
    # Both sides use the same pattern: scrollable list of slot widgets,
    # each with a number, label, source selector, and fire button.

    def _build_slot_widget(self, parent, slot_data, side):
        """Build one hotkey slot widget inside the scrollable zone."""
        num = slot_data["number"]
        sf = ttk.Frame(parent, padding=(2, 3))
        sf.columnconfigure(1, weight=1)

        # Number badge
        ttk.Label(sf, text=f"{num}", font=("", 9, "bold"),
                  foreground="#446", width=3,
                  anchor="center").grid(row=0, column=0, padx=(0, 4))

        # Source selector (combobox)
        var = tk.StringVar(value=slot_data.get("source_ref", ""))
        cb = ttk.Combobox(sf, textvariable=var, values=self._map_presets,
                         width=14, state="normal", font=("", 8))
        cb.grid(row=0, column=1, sticky="ew")

        # Fire button
        def _fire(v=var, s=side):
            ref = v.get()
            if not ref:
                self._set_status(f"Slot {num}: no source assigned.")
                return
            self._fire_hotkey(ref, s)

        ttk.Button(sf, text="\u25b6", width=3,
                   command=_fire).grid(row=0, column=2, padx=(4, 0))

        # Remove button
        def _remove(slot_d=slot_data, s=side):
            if s == "left":
                self._left_slots = [x for x in self._left_slots if x["number"] != slot_d["number"]]
                self._rebuild_left_slots()
            else:
                self._right_slots = [x for x in self._right_slots if x["number"] != slot_d["number"]]
                self._rebuild_right_slots()

        ttk.Button(sf, text="\u2715", width=2,
                   command=_remove).grid(row=0, column=3, padx=(2, 0))

        sf.pack(fill="x", pady=1)
        return {"frame": sf, "var": var, "slot_data": slot_data}

    def _add_left_slot(self):
        num = len(self._left_slots) + 1
        slot = _new_hotkey_slot(num)
        slot["source_type"] = "prompt_map"
        self._left_slots.append(slot)
        w = self._build_slot_widget(self._left_inner, slot, "left")
        self._left_slot_widgets.append(w)
        self._set_status(f"Left slot {num} added.")

    def _add_right_slot(self):
        num = len(self._right_slots) + 1
        slot = _new_hotkey_slot(num)
        slot["source_type"] = "preset"
        self._right_slots.append(slot)
        w = self._build_slot_widget(self._right_inner, slot, "right")
        self._right_slot_widgets.append(w)
        self._set_status(f"Right slot {num} added.")

    def _clear_left_slots(self):
        self._left_slots = []
        self._rebuild_left_slots()
        self._set_status("Left hotkey slots cleared.")

    def _clear_right_slots(self):
        self._right_slots = []
        self._rebuild_right_slots()
        self._set_status("Right hotkey slots cleared.")

    def _rebuild_left_slots(self):
        for w in self._left_slot_widgets:
            w["frame"].destroy()
        self._left_slot_widgets = []
        # Renumber
        for i, slot in enumerate(self._left_slots):
            slot["number"] = i + 1
        for slot in self._left_slots:
            w = self._build_slot_widget(self._left_inner, slot, "left")
            self._left_slot_widgets.append(w)

    def _rebuild_right_slots(self):
        for w in self._right_slot_widgets:
            w["frame"].destroy()
        self._right_slot_widgets = []
        for i, slot in enumerate(self._right_slots):
            slot["number"] = i + 1
        for slot in self._right_slots:
            w = self._build_slot_widget(self._right_inner, slot, "right")
            self._right_slot_widgets.append(w)

    def _fire_hotkey(self, source_ref, side):
        """
        Fire a hotkey — load the referenced source and act on it.
        Left side (prompt maps): load map content into the input area.
        Right side (presets): insert preset content into the input area.
        """
        content = self._load_map_content(source_ref)
        if content is None:
            # Try loading as a raw prompt instead
            content = self._load_prompt_field(source_ref, "prompt_body")

        if not content:
            self._set_status(f"Could not load: {source_ref}")
            return

        if side == "left":
            # Prompt map: wrap user input
            user_text = self._input_txt.get("1.0", "end-1c")
            if "<<user_input>>" in content:
                wrapped = content.replace("<<user_input>>", user_text)
            else:
                wrapped = content + "\n\n" + user_text if user_text.strip() else content
            self._input_txt.delete("1.0", "end")
            self._input_txt.insert("1.0", wrapped)
            self._set_status(f"Map applied: {source_ref}")
        else:
            # Preset: insert at cursor
            self._input_txt.insert("insert", content)
            self._set_status(f"Preset inserted: {source_ref}")

    def _load_prompt_field(self, prompt_ref, field="prompt_body"):
        """Read-only: load a single field from a prompt record."""
        if not self.prompt_json_root:
            return ""
        prompts_dir = os.path.join(self.prompt_json_root, "prompts")
        if not os.path.isdir(prompts_dir):
            return ""
        target = prompt_ref.strip()
        target_fname = target if target.endswith(".json") else target + ".json"
        for dirpath, _dirs, files in os.walk(prompts_dir):
            for fname in files:
                if fname == target_fname:
                    try:
                        with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as fh:
                            return str(json.load(fh).get(field, ""))
                    except Exception:
                        return ""
        for dirpath, _dirs, files in os.walk(prompts_dir):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if data.get("file_name", "").strip() == prompt_ref.strip():
                        return str(data.get(field, ""))
                except Exception:
                    pass
        return ""

    # ═════════════════════════════════════════════════════════════════════════
    # ROOT FINDING
    # ═════════════════════════════════════════════════════════════════════════

    def _auto_find_root(self):
        from pathlib import Path
        page_file = Path(__file__).resolve()
        for candidate in [page_file.parent, *page_file.parents]:
            if (candidate / "promptlibrary").is_dir() or \
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
            "Root not found \u2014 use Choose Root to locate pagepack_pychiain.")

    def _choose_root(self):
        d = filedialog.askdirectory(title="Select pagepack_pychiain directory")
        if d:
            self._set_root(d)

    def _set_root(self, pack_path):
        self.pack_root = pack_path
        self.log_dir = os.path.join(pack_path, "uploadedtxt")

        # Locate prompt json root
        for subpath in [
            os.path.join(pack_path, "promptlibrary", "prompteditor", "json"),
            os.path.join(pack_path, "index_pychiain", "prompteditor", "json"),
            os.path.join(pack_path, "index_pychiain", "prompt_editor", "json"),
        ]:
            if os.path.isdir(subpath):
                self.prompt_json_root = subpath
                break
        else:
            self.prompt_json_root = os.path.join(
                pack_path, "promptlibrary", "prompteditor", "json")

        # Ensure log dir exists
        os.makedirs(self.log_dir, exist_ok=True)

        short = pack_path if len(pack_path) <= 55 else "\u2026" + pack_path[-52:]
        self._path_var.set(f"Root: {short}")

        # Auto-load presets
        self._reload_presets()

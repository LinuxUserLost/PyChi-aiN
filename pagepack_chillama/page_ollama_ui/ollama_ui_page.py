"""
page_ollama_ui / ollama_ui_page.py
──────────────────────────────────────────────────────────────────────────────
Ollama chat interface page for Guichi shell — pagepack_chillama.

Shell contract:
    page = AIInterface(parent_widget)
    page.build(parent)          # also: create_widgets / mount / render

Layout (chat-first):
    ┌─────────────────────────────────────────────┐
    │  Status strip: dot · URL · model · refresh  │
    ├─────────────────────────────────────────────┤
    │  ┌─[Chat]──[Attachments]──[Tools]─────────┐ │
    │  │                                         │ │
    │  │   Conversation display (scrollable)     │ │
    │  │     AI messages left                    │ │
    │  │              User messages right         │ │
    │  │                                         │ │
    │  ├─────────────────────────────────────────┤ │
    │  │  [Upload File] [Upload Dir] [Save Chat] │ │
    │  │  ┌──────────────────────────┐ [Send]    │ │
    │  │  │  Composer input          │           │ │
    │  │  └──────────────────────────┘           │ │
    │  └─────────────────────────────────────────┘ │
    ├─────────────────────────────────────────────┤
    │  Status bar                                  │
    └─────────────────────────────────────────────┘

Backend: local Ollama REST API via helpers/ollama_client.py
"""

import os
import sys
import json
import datetime
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ── Ensure pack root is importable ──────────────────────────────────────────
_THIS_FILE = os.path.abspath(__file__)
_PAGE_DIR = os.path.dirname(_THIS_FILE)
_PACK_DIR = os.path.dirname(_PAGE_DIR)
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

from helpers.ollama_client import OllamaClient, DEFAULT_BASE_URL


# ── Constants ───────────────────────────────────────────────────────────────

READABLE_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".html", ".css", ".js", ".ts", ".xml", ".sql", ".sh",
}

MAX_SCAN_FILES = 50
MAX_SCAN_BYTES = 1_048_576  # 1 MB

# ── Colors ──────────────────────────────────────────────────────────────────
_CLR_OK        = "#2e7d32"
_CLR_WARN      = "#e65100"
_CLR_ERR       = "#c62828"
_CLR_MUTED     = "#888"
_CLR_STRIP_BG  = "#f0f0ed"
_CLR_BG_PANE   = "#fafaf8"
_CLR_BG_INPUT  = "#ffffff"

# Chat bubble colors
_CLR_USER_BG   = "#dbeafe"   # soft blue
_CLR_USER_FG   = "#1e293b"
_CLR_AI_BG     = "#f1f5f9"   # soft gray
_CLR_AI_FG     = "#1e293b"
_CLR_SYS_FG    = "#64748b"   # muted for system/meta lines
_CLR_CHAT_BG   = "#ffffff"   # conversation pane background
_CLR_THINKING  = "#94a3b8"   # "thinking" indicator


# ── Scroll helper (Wayland/Linux safe) ──────────────────────────────────────

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


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PAGE CLASS
# ═════════════════════════════════════════════════════════════════════════════

class AIInterface:
    """
    Ollama chat UI — Guichi shell page.  Chat-first layout.

    Instantiation: AIInterface(parent_frame)
    GUI mount:     .build(parent) / .create_widgets(parent) / .mount(parent) / .render(parent)
    """

    PAGE_NAME = "Ollama UI"

    def __init__(self, parent, app=None, page_key="", page_folder="", *args, **kwargs):
        app = kwargs.pop("controller", app)
        page_key = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder", page_folder)

        self.parent = parent
        self.app = app
        self.page_key = page_key
        self.page_folder = page_folder

        # Client
        self._client = OllamaClient(DEFAULT_BASE_URL)
        self._connected = False
        self._models = []
        self._selected_model = ""

        # Conversation history — list of {"role": str, "content": str}
        self._history = []

        # Attachment state (preserved from v1)
        self._attachments = []
        self._scan_cancel = False
        self._scanning = False

        # Exchange state
        self._last_response = ""
        self._last_exchange = {}

        # Build root frame
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)   # notebook gets all stretch
        self.frame.rowconfigure(2, weight=0)   # status bar

        self._build_status_strip()   # row 0
        self._build_notebook()       # row 1
        self._build_bottom_bar()     # row 2

        # Initial connection check
        self.frame.after(300, self._check_connection)

    # ── Shell mount methods (unchanged contract) ────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy()
                self.parent = container
                self.frame = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1)
                self.frame.rowconfigure(1, weight=1)
                self.frame.rowconfigure(2, weight=0)
                self._build_status_strip()
                self._build_notebook()
                self._build_bottom_bar()
                self.frame.after(300, self._check_connection)
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
    # STATUS STRIP  (row 0 — compact top bar)
    # ═════════════════════════════════════════════════════════════════════════

    def _build_status_strip(self):
        strip = tk.Frame(self.frame, bg=_CLR_STRIP_BG, padx=8, pady=4)
        strip.grid(row=0, column=0, sticky="ew")
        strip.columnconfigure(20, weight=1)

        # Dot
        self._status_dot = tk.Label(strip, text="\u25cf", font=("", 13),
                                    fg=_CLR_MUTED, bg=_CLR_STRIP_BG)
        self._status_dot.grid(row=0, column=0, padx=(0, 3))

        # Connection label
        self._conn_label = tk.Label(strip, text="Not checked", font=("", 9),
                                    fg=_CLR_MUTED, bg=_CLR_STRIP_BG, anchor="w")
        self._conn_label.grid(row=0, column=1, padx=(0, 8))

        # URL
        tk.Label(strip, text="URL:", font=("", 9), bg=_CLR_STRIP_BG,
                 fg="#555").grid(row=0, column=2, padx=(0, 2))
        self._url_var = tk.StringVar(value=DEFAULT_BASE_URL)
        url_e = ttk.Entry(strip, textvariable=self._url_var, width=26, font=("", 9))
        url_e.grid(row=0, column=3, padx=(0, 4))
        url_e.bind("<Return>", lambda e: self._check_connection())

        # Refresh
        ttk.Button(strip, text="\u21bb", width=3,
                   command=self._check_connection).grid(row=0, column=4, padx=(0, 8))

        # Model
        tk.Label(strip, text="Model:", font=("", 9), bg=_CLR_STRIP_BG,
                 fg="#555").grid(row=0, column=5, padx=(0, 2))
        self._model_var = tk.StringVar(value="(none)")
        self._model_combo = ttk.Combobox(strip, textvariable=self._model_var,
                                         values=[], width=26, state="readonly",
                                         font=("", 9))
        self._model_combo.grid(row=0, column=6, padx=(0, 4))
        self._model_combo.bind("<<ComboboxSelected>>", self._on_model_select)

        # Temp
        tk.Label(strip, text="Temp:", font=("", 9), bg=_CLR_STRIP_BG,
                 fg="#555").grid(row=0, column=7, padx=(0, 2))
        self._temp_var = tk.StringVar(value="0.7")
        ttk.Entry(strip, textvariable=self._temp_var, width=5,
                  font=("", 9)).grid(row=0, column=8)

        # Spacer
        tk.Frame(strip, bg=_CLR_STRIP_BG).grid(row=0, column=20, sticky="ew")

    # ═════════════════════════════════════════════════════════════════════════
    # NOTEBOOK — three tabs
    # ═════════════════════════════════════════════════════════════════════════

    def _build_notebook(self):
        self._notebook = ttk.Notebook(self.frame)
        self._notebook.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 0))

        # Tab 1: Chat
        self._chat_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._chat_tab, text="  Chat  ")
        self._build_chat_tab()

        # Tab 2: Attachments
        self._attach_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._attach_tab, text="  Attachments  ")
        self._build_attachments_tab()

        # Tab 3: Tools / Settings
        self._tools_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._tools_tab, text="  Tools / Settings  ")
        self._build_tools_tab()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 1: CHAT
    # ═════════════════════════════════════════════════════════════════════════

    def _build_chat_tab(self):
        tab = self._chat_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)   # conversation display
        tab.rowconfigure(1, weight=0)   # utility row
        tab.rowconfigure(2, weight=0)   # composer

        # ── Conversation display ────────────────────────────────────────────
        conv_frame = ttk.Frame(tab)
        conv_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 2))
        conv_frame.columnconfigure(0, weight=1)
        conv_frame.rowconfigure(0, weight=1)

        self._chat_display = tk.Text(
            conv_frame, wrap="word", state="disabled",
            font=("", 10), background=_CLR_CHAT_BG,
            relief="flat", borderwidth=0, padx=12, pady=10,
            cursor="arrow", spacing3=2)
        csb = ttk.Scrollbar(conv_frame, orient="vertical",
                             command=self._chat_display.yview)
        self._chat_display.configure(yscrollcommand=csb.set)
        self._chat_display.grid(row=0, column=0, sticky="nsew")
        csb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._chat_display)

        # Configure text tags for chat bubbles
        self._chat_display.tag_configure("user_name",
            font=("", 9, "bold"), foreground=_CLR_USER_FG,
            justify="right", spacing1=12)
        self._chat_display.tag_configure("user_msg",
            font=("", 10), foreground=_CLR_USER_FG,
            background=_CLR_USER_BG, justify="right",
            lmargin1=120, lmargin2=120, rmargin=8,
            spacing1=2, spacing3=4,
            relief="flat", borderwidth=0)

        self._chat_display.tag_configure("ai_name",
            font=("", 9, "bold"), foreground=_CLR_AI_FG,
            justify="left", spacing1=12)
        self._chat_display.tag_configure("ai_msg",
            font=("", 10), foreground=_CLR_AI_FG,
            background=_CLR_AI_BG, justify="left",
            lmargin1=8, lmargin2=8, rmargin=120,
            spacing1=2, spacing3=4,
            relief="flat", borderwidth=0)

        self._chat_display.tag_configure("meta",
            font=("", 8), foreground=_CLR_SYS_FG,
            justify="center", spacing1=2, spacing3=6)

        self._chat_display.tag_configure("thinking",
            font=("", 9, "italic"), foreground=_CLR_THINKING,
            justify="left", lmargin1=8,
            spacing1=2, spacing3=4)

        self._chat_display.tag_configure("separator",
            font=("", 4), foreground=_CLR_CHAT_BG,
            justify="center", spacing1=0, spacing3=0)

        # Welcome message
        self._append_chat_meta(
            "Ollama Chat \u2014 connect and select a model above, then type below.")

        # ── Utility row (upload buttons, save chat, attachment badge) ───────
        util_row = ttk.Frame(tab, padding=(6, 3))
        util_row.grid(row=1, column=0, sticky="ew")

        ttk.Button(util_row, text="\U0001f4c4 Upload File",
                   command=self._choose_file, width=14).pack(side="left", padx=(0, 4))
        ttk.Button(util_row, text="\U0001f4c1 Upload Dir",
                   command=self._choose_directory, width=14).pack(side="left", padx=(0, 4))

        ttk.Separator(util_row, orient="vertical").pack(side="left", fill="y",
                                                         padx=6, pady=2)

        ttk.Button(util_row, text="\U0001f4be Save Chat",
                   command=self._save_chat, width=12).pack(side="left", padx=(4, 4))
        ttk.Button(util_row, text="Clear Chat",
                   command=self._clear_chat, width=10).pack(side="left", padx=(0, 4))

        # Attachment badge (shows count if any files are attached)
        self._attach_badge_var = tk.StringVar(value="")
        self._attach_badge = ttk.Label(util_row, textvariable=self._attach_badge_var,
                                        font=("", 8), foreground=_CLR_MUTED)
        self._attach_badge.pack(side="right", padx=8)

        # ── Composer area ───────────────────────────────────────────────────
        comp_frame = ttk.Frame(tab, padding=(6, 2, 6, 6))
        comp_frame.grid(row=2, column=0, sticky="ew")
        comp_frame.columnconfigure(0, weight=1)

        # Text entry + send button in a single row
        input_row = ttk.Frame(comp_frame)
        input_row.pack(fill="x")
        input_row.columnconfigure(0, weight=1)

        self._composer = tk.Text(
            input_row, wrap="word", height=3, undo=True,
            font=("", 10), relief="solid", borderwidth=1,
            bg=_CLR_BG_INPUT, padx=8, pady=6, insertwidth=2,
            selectbackground="#c8d8f0")
        self._composer.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        _bind_scroll(self._composer)

        # Bind Ctrl+Enter and Shift+Enter for send
        self._composer.bind("<Control-Return>", lambda e: (self._on_send(), "break"))
        self._composer.bind("<Shift-Return>", lambda e: (self._on_send(), "break"))

        # Send button — tall to match composer
        self._send_btn = ttk.Button(input_row, text="Send \u25b6",
                                     command=self._on_send, width=8)
        self._send_btn.grid(row=0, column=1, sticky="ns")

    # ── Chat display helpers ────────────────────────────────────────────────

    def _append_chat_user(self, text):
        """Append a user message bubble to the conversation display."""
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n", "separator")
        d.insert("end", "You\n", "user_name")
        d.insert("end", text + "\n", "user_msg")
        d.configure(state="disabled")
        d.see("end")

    def _append_chat_ai(self, text, meta=""):
        """Append an AI response bubble to the conversation display."""
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n", "separator")
        model_name = self._selected_model or "AI"
        short = model_name.split(":")[0] if ":" in model_name else model_name
        d.insert("end", f"{short}\n", "ai_name")
        d.insert("end", text + "\n", "ai_msg")
        if meta:
            d.insert("end", meta + "\n", "meta")
        d.configure(state="disabled")
        d.see("end")

    def _append_chat_meta(self, text):
        """Append a centered meta/system line."""
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n" + text + "\n", "meta")
        d.configure(state="disabled")
        d.see("end")

    def _show_thinking(self):
        """Show a 'thinking' indicator. Returns line index for later removal."""
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n", "separator")
        model_name = self._selected_model or "AI"
        short = model_name.split(":")[0] if ":" in model_name else model_name
        d.insert("end", f"{short}\n", "ai_name")
        # Mark position before thinking text
        mark_line = int(d.index("end-1c").split(".")[0])
        d.insert("end", "Thinking\u2026\n", "thinking")
        d.configure(state="disabled")
        d.see("end")
        return mark_line

    def _remove_thinking(self, mark_line):
        """Remove the thinking block (name + thinking text + separator before it)."""
        d = self._chat_display
        d.configure(state="normal")
        try:
            # The block is: separator line, ai_name line, thinking line
            # mark_line is the thinking text line; name is mark_line-1,
            # separator is mark_line-2
            del_from = max(1, mark_line - 2)
            d.delete(f"{del_from}.0", "end")
        except Exception:
            pass
        d.configure(state="disabled")

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 2: ATTACHMENTS (preserved from v1, adapted to tab)
    # ═════════════════════════════════════════════════════════════════════════

    def _build_attachments_tab(self):
        tab = self._attach_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # Top controls
        ctrl = ttk.Frame(tab, padding=(8, 6))
        ctrl.grid(row=0, column=0, sticky="ew")

        ttk.Button(ctrl, text="\U0001f4c4 Choose File\u2026", width=18,
                   command=self._choose_file).pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="\U0001f4c1 Choose Dir\u2026", width=18,
                   command=self._choose_directory).pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="\u2716 Clear All", width=12,
                   command=self._clear_attachments).pack(side="left", padx=(0, 8))

        self._cancel_btn = ttk.Button(ctrl, text="\u23f9 Cancel Scan",
                                       width=14, command=self._cancel_scan)
        # not packed — shown only during scans

        self._scan_status_var = tk.StringVar(value="")
        ttk.Label(ctrl, textvariable=self._scan_status_var,
                  font=("", 8), foreground=_CLR_MUTED).pack(side="left", padx=8)

        # Context mode toggle
        toggle_f = ttk.Frame(ctrl)
        toggle_f.pack(side="right")
        self._context_mode_var = tk.StringVar(value="paths_only")
        ttk.Radiobutton(toggle_f, text="Paths only",
                         variable=self._context_mode_var,
                         value="paths_only").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(toggle_f, text="Include contents",
                         variable=self._context_mode_var,
                         value="include_contents").pack(side="left")

        # Treeview
        list_frame = ttk.Frame(tab, padding=(8, 0, 8, 4))
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        cols = ("name", "type", "size", "status")
        self._attach_tree = ttk.Treeview(list_frame, columns=cols,
                                          show="headings", height=10,
                                          selectmode="extended")
        self._attach_tree.heading("name", text="Name")
        self._attach_tree.heading("type", text="Type")
        self._attach_tree.heading("size", text="Size")
        self._attach_tree.heading("status", text="Status")
        self._attach_tree.column("name", width=300, minwidth=120)
        self._attach_tree.column("type", width=70, minwidth=50, anchor="center")
        self._attach_tree.column("size", width=80, minwidth=50, anchor="e")
        self._attach_tree.column("status", width=100, minwidth=60, anchor="center")

        vsb = ttk.Scrollbar(list_frame, orient="vertical",
                             command=self._attach_tree.yview)
        self._attach_tree.configure(yscrollcommand=vsb.set)
        self._attach_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Placeholder label
        self._drop_label = tk.Label(
            list_frame,
            text="No attachments.\nUse buttons above or on the Chat tab to add files.",
            font=("", 9), fg=_CLR_MUTED, bg=_CLR_BG_PANE, justify="center")
        self._drop_label.place(relx=0.5, rely=0.5, anchor="center")

        # Bottom row
        rm_frame = ttk.Frame(list_frame)
        rm_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(rm_frame, text="Remove Selected", width=16,
                   command=self._remove_selected_attachments).pack(side="left")
        self._attach_count_var = tk.StringVar(value="0 files")
        ttk.Label(rm_frame, textvariable=self._attach_count_var,
                  font=("", 8), foreground=_CLR_MUTED).pack(side="right", padx=4)

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 3: TOOLS / SETTINGS
    # ═════════════════════════════════════════════════════════════════════════

    def _build_tools_tab(self):
        tab = self._tools_tab
        tab.columnconfigure(0, weight=1)

        pad = {"padx": 10, "pady": (6, 2), "sticky": "ew"}

        # ── System prompt ───────────────────────────────────────────────────
        sys_lf = ttk.LabelFrame(tab, text="System Prompt", padding=(8, 4))
        sys_lf.grid(row=0, column=0, **pad)
        sys_lf.columnconfigure(0, weight=1)

        self._system_txt = tk.Text(
            sys_lf, wrap="word", height=4, undo=True,
            font=("", 9), relief="flat", borderwidth=1,
            bg=_CLR_BG_INPUT, padx=6, pady=4)
        ssb = ttk.Scrollbar(sys_lf, orient="vertical",
                             command=self._system_txt.yview)
        self._system_txt.configure(yscrollcommand=ssb.set)
        self._system_txt.grid(row=0, column=0, sticky="ew")
        ssb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._system_txt)

        ttk.Label(sys_lf,
                  text="Applied to every message sent. Leave blank for default behavior.",
                  font=("", 8), foreground=_CLR_MUTED).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # ── Save / Export section ───────────────────────────────────────────
        save_lf = ttk.LabelFrame(tab, text="Save / Export", padding=(8, 6))
        save_lf.grid(row=1, column=0, **pad)

        btn_row = ttk.Frame(save_lf)
        btn_row.pack(fill="x")

        ttk.Button(btn_row, text="\U0001f4be Save Last Response",
                   command=self._save_response, width=22).pack(
            side="left", padx=(0, 6))
        ttk.Button(btn_row, text="\U0001f4cb Copy Last Response",
                   command=self._copy_response, width=22).pack(
            side="left", padx=(0, 6))
        ttk.Button(btn_row, text="\U0001f4e6 Save Exchange Bundle",
                   command=self._save_exchange_bundle, width=24).pack(side="left")

        # ── Debug info ──────────────────────────────────────────────────────
        debug_lf = ttk.LabelFrame(tab, text="Connection / Debug", padding=(8, 6))
        debug_lf.grid(row=2, column=0, **pad)
        debug_lf.columnconfigure(0, weight=1)

        self._debug_info_var = tk.StringVar(value="No connection info yet.")
        ttk.Label(debug_lf, textvariable=self._debug_info_var,
                  font=("", 9), foreground=_CLR_MUTED, wraplength=600,
                  anchor="w", justify="left").grid(
            row=0, column=0, sticky="ew")

        ttk.Button(debug_lf, text="Refresh Connection Info",
                   command=self._refresh_debug_info, width=24).grid(
            row=1, column=0, sticky="w", pady=(6, 0))

    # ═════════════════════════════════════════════════════════════════════════
    # BOTTOM BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _build_bottom_bar(self):
        bar = ttk.Frame(self.frame, padding=(8, 2))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self._status_var, anchor="w",
                  foreground="#555", font=("", 9)).grid(
            row=0, column=0, sticky="ew")

    def _set_status(self, msg):
        self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # CONNECTION / MODELS  (unchanged backend logic)
    # ═════════════════════════════════════════════════════════════════════════

    def _check_connection(self):
        url = self._url_var.get().strip()
        if url:
            self._client.base_url = url.rstrip("/")

        self._set_status("Checking connection\u2026")
        self._conn_label.configure(text="Checking\u2026", fg=_CLR_WARN)
        self._status_dot.configure(fg=_CLR_WARN)

        def _bg():
            ping = self._client.ping()
            models_result = None
            if ping["ok"]:
                models_result = self._client.list_models()
            self.frame.after(0, lambda: self._apply_connection(ping, models_result))

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_connection(self, ping, models_result):
        if ping["ok"]:
            self._connected = True
            ver = ping.get("version") or ""
            label = "Connected" + (f" (v{ver})" if ver else "")
            self._conn_label.configure(text=label, fg=_CLR_OK)
            self._status_dot.configure(fg=_CLR_OK)

            if models_result and models_result["ok"]:
                self._models = models_result["models"]
                names = [m["name"] for m in self._models]
                self._model_combo.configure(values=names)
                if names:
                    if self._selected_model not in names:
                        self._model_var.set(names[0])
                        self._selected_model = names[0]
                    else:
                        self._model_var.set(self._selected_model)
                self._set_status(f"Connected. {len(names)} model(s) available.")
            else:
                err = (models_result or {}).get("error", "unknown")
                self._set_status(f"Connected but model list failed: {err}")
        else:
            self._connected = False
            self._conn_label.configure(text="Disconnected", fg=_CLR_ERR)
            self._status_dot.configure(fg=_CLR_ERR)
            self._set_status(
                f"Connection failed: {ping.get('error', 'unknown')}")

    def _on_model_select(self, event=None):
        self._selected_model = self._model_var.get()
        self._set_status(f"Model: {self._selected_model}")

    def _refresh_debug_info(self):
        parts = [
            f"Base URL: {self._client.base_url}",
            f"Connected: {self._connected}",
            f"Models loaded: {len(self._models)}",
            f"Selected: {self._selected_model or '(none)'}",
            f"Attachments: {len(self._attachments)}",
            f"History turns: {len(self._history)}",
        ]
        self._debug_info_var.set("  |  ".join(parts))

    # ═════════════════════════════════════════════════════════════════════════
    # ATTACHMENT MANAGEMENT  (preserved from v1)
    # ═════════════════════════════════════════════════════════════════════════

    def _choose_file(self):
        paths = filedialog.askopenfilenames(
            title="Choose file(s) to attach",
            filetypes=[("All files", "*.*")])
        for p in paths:
            self._add_attachment(p, is_dir=False)
        self._refresh_attach_display()
        self._update_attach_badge()

    def _choose_directory(self):
        d = filedialog.askdirectory(title="Choose directory to scan")
        if d:
            self._scan_directory(d)

    def _scan_directory(self, root_path):
        if self._scanning:
            self._set_status("Scan already in progress.")
            return

        self._scanning = True
        self._scan_cancel = False
        self._cancel_btn.pack(side="left", padx=(4, 0))
        self._scan_status_var.set("Scanning\u2026")

        def _bg():
            count = 0
            total_bytes = 0
            try:
                for dirpath, dirnames, filenames in os.walk(root_path):
                    if self._scan_cancel:
                        break
                    dirnames[:] = [d for d in dirnames
                                   if not d.startswith(".")
                                   and d != "__pycache__"]
                    for fname in sorted(filenames):
                        if self._scan_cancel:
                            break
                        if count >= MAX_SCAN_FILES:
                            break
                        if fname.startswith("."):
                            continue
                        fpath = os.path.join(dirpath, fname)
                        try:
                            fsize = os.path.getsize(fpath)
                        except OSError:
                            fsize = 0

                        ext = os.path.splitext(fname)[1].lower()
                        readable = ext in READABLE_EXTENSIONS
                        if readable and (total_bytes + fsize) > MAX_SCAN_BYTES:
                            readable = False

                        entry = {
                            "path": fpath,
                            "name": fname,
                            "is_dir": False,
                            "readable": readable,
                            "ext": ext,
                            "size": fsize,
                            "status": "readable" if readable else "skipped",
                        }
                        self._attachments.append(entry)
                        if readable:
                            total_bytes += fsize
                        count += 1

                        if count % 10 == 0:
                            self.frame.after(
                                0, lambda c=count:
                                self._scan_status_var.set(
                                    f"Scanning\u2026 {c} files"))

                    if count >= MAX_SCAN_FILES:
                        break
            except Exception as exc:
                self.frame.after(
                    0, lambda: self._set_status(f"Scan error: {exc}"))
            finally:
                cancelled = self._scan_cancel
                self._scanning = False
                self._scan_cancel = False

                def _finish():
                    self._cancel_btn.pack_forget()
                    tag = " (cancelled)" if cancelled else ""
                    self._scan_status_var.set(f"Done: {count} files{tag}")
                    self._refresh_attach_display()
                    self._update_attach_badge()
                    self._set_status(
                        f"Directory scan complete: {count} file(s){tag}.")

                self.frame.after(0, _finish)

        threading.Thread(target=_bg, daemon=True).start()

    def _cancel_scan(self):
        self._scan_cancel = True
        self._scan_status_var.set("Cancelling\u2026")

    def _add_attachment(self, path, is_dir=False):
        for a in self._attachments:
            if a["path"] == path:
                return
        ext = os.path.splitext(path)[1].lower()
        readable = ext in READABLE_EXTENSIONS
        try:
            size = os.path.getsize(path) if not is_dir else 0
        except OSError:
            size = 0
        self._attachments.append({
            "path": path,
            "name": os.path.basename(path),
            "is_dir": is_dir,
            "readable": readable,
            "ext": ext,
            "size": size,
            "status": "readable" if readable else "skipped",
        })

    def _refresh_attach_display(self):
        self._attach_tree.delete(*self._attach_tree.get_children())
        for i, a in enumerate(self._attachments):
            ext = a.get("ext", "")
            size_str = self._fmt_size(a["size"])
            status = a["status"]
            self._attach_tree.insert(
                "", "end", iid=str(i),
                values=(a["name"], ext, size_str, status))
        n = len(self._attachments)
        self._attach_count_var.set(f"{n} file{'s' if n != 1 else ''}")
        if n == 0:
            self._drop_label.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self._drop_label.place_forget()

    def _remove_selected_attachments(self):
        sel = self._attach_tree.selection()
        if not sel:
            return
        indices = sorted([int(s) for s in sel], reverse=True)
        for idx in indices:
            if 0 <= idx < len(self._attachments):
                del self._attachments[idx]
        self._refresh_attach_display()
        self._update_attach_badge()

    def _clear_attachments(self):
        self._attachments.clear()
        self._refresh_attach_display()
        self._update_attach_badge()
        self._scan_status_var.set("")
        self._set_status("Attachments cleared.")

    def _update_attach_badge(self):
        n = len(self._attachments)
        if n > 0:
            self._attach_badge_var.set(
                f"\U0001f4ce {n} file{'s' if n != 1 else ''} attached")
        else:
            self._attach_badge_var.set("")

    @staticmethod
    def _fmt_size(n):
        if n < 1024:
            return f"{n} B"
        elif n < 1048576:
            return f"{n / 1024:.1f} KB"
        else:
            return f"{n / 1048576:.1f} MB"

    # ═════════════════════════════════════════════════════════════════════════
    # CONTEXT ASSEMBLY  (unchanged from v1)
    # ═════════════════════════════════════════════════════════════════════════

    def _assemble_context(self) -> str:
        if not self._attachments:
            return ""

        mode = self._context_mode_var.get()
        parts = []
        total_read = 0

        for a in self._attachments:
            path = a["path"]
            if mode == "paths_only":
                parts.append(f"[attached] {path}")
            else:
                if a["readable"] and a["status"] == "readable":
                    try:
                        with open(path, "r", encoding="utf-8",
                                  errors="replace") as fh:
                            budget = MAX_SCAN_BYTES - total_read
                            if budget <= 0:
                                parts.append(
                                    f"[attached: budget exceeded] {path}")
                                continue
                            content = fh.read(budget)
                            total_read += len(
                                content.encode("utf-8", errors="replace"))
                            parts.append(
                                f"--- {path} ---\n{content}\n"
                                f"--- end {os.path.basename(path)} ---")
                    except Exception as exc:
                        parts.append(
                            f"[attached: read error: {exc}] {path}")
                else:
                    parts.append(f"[attached: binary/skipped] {path}")

        return "\n\n".join(parts)

    # ═════════════════════════════════════════════════════════════════════════
    # SEND  (chat-style: builds full conversation history)
    # ═════════════════════════════════════════════════════════════════════════

    def _on_send(self):
        model = self._model_var.get().strip()
        if not model or model == "(none)":
            self._set_status("Select a model first.")
            return
        if not self._connected:
            self._set_status(
                "Not connected to Ollama \u2014 check URL and refresh.")
            return

        user_text = self._composer.get("1.0", "end-1c").strip()
        if not user_text:
            self._set_status("Type a message first.")
            return

        system_text = self._system_txt.get("1.0", "end-1c").strip()

        # Assemble context from attachments
        context = self._assemble_context()
        if context:
            full_user = (f"{user_text}\n\n"
                         f"--- Attached Context ---\n{context}")
        else:
            full_user = user_text

        # Parse temperature
        try:
            temp = float(self._temp_var.get())
            temp = max(0.0, min(2.0, temp))
        except ValueError:
            temp = 0.7

        # Add user message to display immediately
        self._append_chat_user(user_text)
        if self._attachments:
            n = len(self._attachments)
            mode = self._context_mode_var.get().replace("_", " ")
            self._append_chat_meta(
                f"\U0001f4ce {n} file{'s' if n != 1 else ''} "
                f"attached ({mode})")

        # Clear composer
        self._composer.delete("1.0", "end")

        # Store in history
        self._history.append({"role": "user", "content": full_user})

        # Build messages for API (full conversation history)
        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        for turn in self._history:
            messages.append({
                "role": turn["role"], "content": turn["content"]})

        # Show thinking indicator
        thinking_mark = self._show_thinking()

        # Disable send
        self._send_btn.configure(state="disabled")
        self._set_status(f"Sending to {model}\u2026")

        def _bg():
            result = self._client.chat(
                model=model, messages=messages, temperature=temp)
            self.frame.after(0, lambda: self._handle_response(
                result, model, system_text, user_text,
                full_user, temp, thinking_mark))

        threading.Thread(target=_bg, daemon=True).start()

    def _handle_response(self, result, model, system_text, user_text,
                         full_user, temperature, thinking_mark):
        self._send_btn.configure(state="normal")

        # Remove thinking indicator
        self._remove_thinking(thinking_mark)

        if result["ok"]:
            content = result["content"]
            self._last_response = content

            # Timing meta
            dur = result.get("total_duration", 0)
            evals = result.get("eval_count", 0)
            dur_s = dur / 1e9 if dur else 0
            meta_parts = []
            if dur_s:
                meta_parts.append(f"{dur_s:.1f}s")
            if evals:
                meta_parts.append(f"{evals} tokens")
            meta = " \u2022 ".join(meta_parts) if meta_parts else ""

            # Add to conversation display
            self._append_chat_ai(content, meta=meta)

            # Store in history
            self._history.append({"role": "assistant", "content": content})

            # Store exchange for bundle save
            self._last_exchange = {
                "timestamp": datetime.datetime.now().isoformat(),
                "model": model,
                "temperature": temperature,
                "system_prompt": system_text,
                "user_prompt": user_text,
                "context_mode": self._context_mode_var.get(),
                "attachments": [
                    {"path": a["path"], "status": a["status"],
                     "readable": a["readable"]}
                    for a in self._attachments
                ],
                "full_user_content": full_user,
                "response": content,
                "total_duration_ns": result.get("total_duration", 0),
                "eval_count": result.get("eval_count", 0),
            }
            self._set_status(f"Response received ({len(content)} chars).")
        else:
            err = result.get("error", "unknown error")
            self._last_response = ""
            self._append_chat_ai(f"[Error] {err}")
            self._set_status(f"Request failed: {err}")

        # Return focus to composer
        self._composer.focus_set()

    # ═════════════════════════════════════════════════════════════════════════
    # SAVE / COPY / CLEAR  (preserved logic, adapted for chat)
    # ═════════════════════════════════════════════════════════════════════════

    def _copy_response(self):
        text = self._last_response
        if not text:
            self._set_status("No response to copy.")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status(f"Copied {len(text)} chars to clipboard.")
        except Exception as exc:
            self._set_status(f"Clipboard error: {exc}")

    def _save_response(self):
        text = self._last_response
        if not text:
            self._set_status("No response to save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Response",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"),
                       ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._set_status(f"Response saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save:\n{exc}")

    def _save_chat(self):
        """Save the full conversation history as Markdown or JSON."""
        if not self._history:
            self._set_status("No conversation to save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Chat",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"),
                       ("JSON", "*.json"), ("All", "*.*")])
        if not path:
            return

        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".json":
                export = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "model": self._selected_model,
                    "system_prompt":
                        self._system_txt.get("1.0", "end-1c").strip(),
                    "turns": self._history,
                }
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(export, fh, indent=2, ensure_ascii=False)
            else:
                lines = [
                    f"# Chat Log",
                    f"**Date:** "
                    f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"**Model:** {self._selected_model}",
                    "",
                    "---",
                    "",
                ]
                for turn in self._history:
                    role = turn["role"].capitalize()
                    lines.append(f"### {role}")
                    lines.append("")
                    lines.append(turn["content"])
                    lines.append("")
                    lines.append("---")
                    lines.append("")

                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")

            self._set_status(f"Chat saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error",
                                 f"Failed to save chat:\n{exc}")

    def _clear_chat(self):
        """Clear the conversation display and history."""
        self._history.clear()
        self._last_response = ""
        self._last_exchange = {}
        d = self._chat_display
        d.configure(state="normal")
        d.delete("1.0", "end")
        d.configure(state="disabled")
        self._append_chat_meta(
            "Chat cleared. Type a message below to start a new conversation.")
        self._set_status("Chat cleared.")

    # ═════════════════════════════════════════════════════════════════════════
    # SAVE EXCHANGE BUNDLE  (preserved from v1, plus conversation history)
    # ═════════════════════════════════════════════════════════════════════════

    def _save_exchange_bundle(self):
        if not self._last_exchange:
            self._set_status(
                "No exchange to save \u2014 send a prompt first.")
            return

        folder = filedialog.askdirectory(
            title="Choose folder for exchange bundle")
        if not folder:
            return

        now = datetime.datetime.now()
        stamp = now.strftime("%Y%m%d_%H%M%S")
        bundle_dir = os.path.join(folder, f"exchange_{stamp}")
        os.makedirs(bundle_dir, exist_ok=True)

        ex = self._last_exchange

        # 1. Machine-readable JSON
        json_path = os.path.join(bundle_dir, "exchange.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(ex, fh, indent=2, ensure_ascii=False)

        # 2. Human-readable summary
        summary_lines = [
            f"# Exchange Summary",
            f"**Date:** {ex.get('timestamp', '')}",
            f"**Model:** {ex.get('model', '')}",
            f"**Temperature:** {ex.get('temperature', '')}",
            "",
            "## System Prompt",
            ex.get("system_prompt", "(none)") or "(none)",
            "",
            "## User Prompt",
            ex.get("user_prompt", ""),
            "",
            "## Attachments",
            f"Mode: {ex.get('context_mode', 'paths_only')}",
        ]
        for att in ex.get("attachments", []):
            summary_lines.append(
                f"- {att['path']}  [{att['status']}]")
        summary_lines += [
            "",
            "## Response",
            ex.get("response", ""),
            "",
            "---",
            f"*Duration: {ex.get('total_duration_ns', 0) / 1e9:.1f}s "
            f"| Tokens: {ex.get('eval_count', 0)}*",
        ]
        md_path = os.path.join(bundle_dir, "exchange_summary.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(summary_lines) + "\n")

        # 3. Attachment manifest
        manifest = {
            "context_mode": ex.get("context_mode", "paths_only"),
            "files": ex.get("attachments", []),
        }
        manifest_path = os.path.join(bundle_dir, "attachment_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)

        # 4. Model/settings metadata
        meta = {
            "model": ex.get("model", ""),
            "temperature": ex.get("temperature", 0.7),
            "base_url": self._client.base_url,
            "timestamp": ex.get("timestamp", ""),
            "total_duration_ns": ex.get("total_duration_ns", 0),
            "eval_count": ex.get("eval_count", 0),
        }
        meta_path = os.path.join(bundle_dir, "settings_metadata.json")
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)

        # 5. Full conversation history
        history_path = os.path.join(bundle_dir, "conversation_history.json")
        with open(history_path, "w", encoding="utf-8") as fh:
            json.dump({"turns": self._history}, fh, indent=2,
                      ensure_ascii=False)

        self._set_status(f"Bundle saved: {bundle_dir}")

"""
page_ollama_ui / ollama_ui_page.py
──────────────────────────────────────────────────────────────────────────────
Ollama chat interface page for Guichi shell — pagepack_chillama.

Session persistence: one ChillamaSession lives at module scope and survives
page unmount/remount when keep_alive=True. Widgets are rebuilt each mount;
state (history, attachments, connection) is restored from the session object.

Shell contract:
    page = AIInterface(parent_widget)
    page.build(parent)

Layout:
    ┌──────────────────────────────────────────────────┐
    │  Status strip: dot · URL · model · temp ·session │
    ├──────────────────────────────────────────────────┤
    │  [Chat]  [Attachments]  [Tools / Settings]       │
    │    conversation display (scrollable)              │
    │    utility row (upload · save · copy · clear)    │
    │    composer + Send                               │
    └──────────────────────────────────────────────────┘
"""

import os
import sys
import json
import datetime
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

_THIS_FILE    = os.path.abspath(__file__)
_PAGE_DIR     = os.path.dirname(_THIS_FILE)
_PACK_DIR     = os.path.dirname(_PAGE_DIR)
_REPO_DIR     = os.path.dirname(_PACK_DIR)
# helpers/ lives in chi_reader (moved pack); point sys.path there
_HELPERS_ROOT = os.path.join(_REPO_DIR, "chi_reader")
if _HELPERS_ROOT not in sys.path:
    sys.path.insert(0, _HELPERS_ROOT)

from helpers.ollama_client import OllamaClient, DEFAULT_BASE_URL


# ── Constants ────────────────────────────────────────────────────────────────

READABLE_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".html", ".css", ".js", ".ts", ".xml", ".sql", ".sh",
}

MAX_SCAN_FILES = 50
MAX_SCAN_BYTES = 1_048_576  # 1 MB

# ── Colors ───────────────────────────────────────────────────────────────────
_CLR_OK           = "#2e7d32"
_CLR_WARN         = "#e65100"
_CLR_ERR          = "#c62828"
_CLR_MUTED        = "#888"
_CLR_STRIP_BG     = "#f0f0ed"
_CLR_BG_PANE      = "#fafaf8"
_CLR_BG_INPUT     = "#ffffff"
_CLR_USER_BG      = "#dbeafe"
_CLR_USER_FG      = "#1e293b"
_CLR_AI_BG        = "#f1f5f9"
_CLR_AI_FG        = "#1e293b"
_CLR_SYS_FG       = "#64748b"
_CLR_CHAT_BG      = "#ffffff"
_CLR_THINKING     = "#94a3b8"
_CLR_SESSION_LIVE = "#2e7d32"
_CLR_SESSION_NONE = "#888888"


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
# SESSION SINGLETON
# ═════════════════════════════════════════════════════════════════════════════

class ChillamaSession:
    """
    Persistent Chillama session state.
    Lives at module scope; survives page unmount/remount when keep_alive=True.
    Only one session exists at a time (_LIVE_SESSION).
    """
    def __init__(self):
        self.history        = []   # [{"role", "content", "display"?}]
        self.attachments    = []
        self.last_response  = ""
        self.last_exchange  = {}
        self.system_prompt  = ""
        self.temperature    = "0.7"
        self.context_mode   = "include_contents"
        self.base_url       = DEFAULT_BASE_URL
        self.selected_model = ""
        self.keep_alive     = True
        self.connected      = False
        self.models         = []   # [{"name": str, ...}]
        self.created_at     = datetime.datetime.now().strftime("%H:%M:%S")
        self.client         = OllamaClient(DEFAULT_BASE_URL)

    def is_live(self):
        return bool(self.history) or self.connected


_LIVE_SESSION = None   # type: ChillamaSession | None


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PAGE CLASS
# ═════════════════════════════════════════════════════════════════════════════

class AIInterface:
    """
    Ollama chat UI — Guichi shell page.

    Session persistence contract:
    - Module-level _LIVE_SESSION holds one ChillamaSession.
    - Each mount checks for a live session; if found and keep_alive=True, resumes it.
    - Frame <Destroy> saves widget state to session; clears session if keep_alive=False.
    - _kill_session() resets state without dropping the Ollama connection.
    """

    PAGE_NAME = "Ollama UI"

    def __init__(self, parent, app=None, page_key="", page_folder="", *args, **kwargs):
        global _LIVE_SESSION
        app        = kwargs.pop("controller",    app)
        page_key   = kwargs.pop("page_context",  page_key)
        page_folder= kwargs.pop("page_folder",   page_folder)

        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # ── Session: reuse or create ─────────────────────────────────────────
        if _LIVE_SESSION is not None and _LIVE_SESSION.keep_alive:
            self._session     = _LIVE_SESSION
            self._is_resuming = True
        else:
            self._session     = ChillamaSession()
            _LIVE_SESSION     = self._session
            self._is_resuming = False

        # Ephemeral per-mount state (not persisted)
        self._scan_cancel = False
        self._scanning    = False

        # Build root frame
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        self.frame.rowconfigure(2, weight=0)

        self._build_status_strip()
        self._build_notebook()
        self._build_bottom_bar()

        # Unmount handler — saves widget state, honours keep_alive
        self.frame.bind("<Destroy>", self._on_frame_destroy)

        # Resume or fresh start
        if self._is_resuming:
            self._restore_session_to_widgets()
        else:
            self.frame.after(300, self._check_connection)

        self._update_session_status()

    # ── Session state proxies ────────────────────────────────────────────────
    # All non-widget state lives in self._session so it survives remount.

    @property
    def _client(self):               return self._session.client

    @property
    def _connected(self):            return self._session.connected
    @_connected.setter
    def _connected(self, v):         self._session.connected = v

    @property
    def _models(self):               return self._session.models
    @_models.setter
    def _models(self, v):            self._session.models = v

    @property
    def _selected_model(self):       return self._session.selected_model
    @_selected_model.setter
    def _selected_model(self, v):    self._session.selected_model = v

    @property
    def _history(self):              return self._session.history
    @_history.setter
    def _history(self, v):           self._session.history = v

    @property
    def _attachments(self):          return self._session.attachments
    @_attachments.setter
    def _attachments(self, v):       self._session.attachments = v

    @property
    def _last_response(self):        return self._session.last_response
    @_last_response.setter
    def _last_response(self, v):     self._session.last_response = v

    @property
    def _last_exchange(self):        return self._session.last_exchange
    @_last_exchange.setter
    def _last_exchange(self, v):     self._session.last_exchange = v

    # ── Shell mount methods ──────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy()
                self.parent = container
                self.frame  = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1)
                self.frame.rowconfigure(1, weight=1)
                self.frame.rowconfigure(2, weight=0)
                self._build_status_strip()
                self._build_notebook()
                self._build_bottom_bar()
                self.frame.bind("<Destroy>", self._on_frame_destroy)
                if self._is_resuming:
                    self._restore_session_to_widgets()
                else:
                    self.frame.after(300, self._check_connection)
                self._update_session_status()
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

    def build(self, parent=None):           return self._embed_into_parent(parent)
    def create_widgets(self, parent=None):  return self._embed_into_parent(parent)
    def mount(self, parent=None):           return self._embed_into_parent(parent)
    def render(self, parent=None):          return self._embed_into_parent(parent)

    # ═════════════════════════════════════════════════════════════════════════
    # SESSION MANAGEMENT
    # ═════════════════════════════════════════════════════════════════════════

    def _on_frame_destroy(self, event=None):
        """Unmount handler: save widget state to session; clear session if no keep-alive."""
        global _LIVE_SESSION
        if event and event.widget is not self.frame:
            return
        try:
            self._session.system_prompt = self._system_txt.get("1.0", "end-1c").strip()
            self._session.temperature   = self._temp_var.get()
            self._session.context_mode  = self._context_mode_var.get()
            self._session.base_url      = self._url_var.get().strip()
        except Exception:
            pass
        if not self._session.keep_alive:
            _LIVE_SESSION = None

    def _kill_session(self):
        """Reset session state while preserving the Ollama connection."""
        global _LIVE_SESSION
        new = ChillamaSession()
        new.keep_alive     = self._session.keep_alive
        new.base_url       = self._session.base_url
        new.client         = self._session.client
        new.connected      = self._session.connected
        new.models         = self._session.models
        new.selected_model = self._session.selected_model
        _LIVE_SESSION = new
        self._session = new

        d = self._chat_display
        d.configure(state="normal")
        d.delete("1.0", "end")
        d.configure(state="disabled")
        self._append_chat_meta("Session reset \u2014 history and attachments cleared.")

        self._refresh_attach_display()
        self._update_attach_badge()
        self._update_session_status()
        self._set_status("Session reset.")

    def _on_keep_alive_toggle(self):
        self._session.keep_alive = self._keep_alive_var.get()
        self._update_session_status()

    def _restore_session_to_widgets(self):
        """Called on remount: populate widgets from session state, rebuild chat display."""
        self._url_var.set(self._session.base_url)
        self._temp_var.set(self._session.temperature)

        if self._session.models:
            names = [m["name"] for m in self._session.models]
            self._model_combo.configure(values=names)

        if self._session.selected_model:
            self._model_var.set(self._session.selected_model)

        if self._session.connected:
            self._conn_label.configure(text="Connected (restored)", fg=_CLR_OK)
            self._status_dot.configure(fg=_CLR_OK)
        else:
            self.frame.after(300, self._check_connection)

        self._system_txt.delete("1.0", "end")
        if self._session.system_prompt:
            self._system_txt.insert("1.0", self._session.system_prompt)

        self._context_mode_var.set(self._session.context_mode)
        self._keep_alive_var.set(self._session.keep_alive)

        self._rebuild_chat_from_history()
        self._refresh_attach_display()
        self._update_attach_badge()

        n = len([h for h in self._session.history if h["role"] == "assistant"])
        self._append_chat_meta(
            f"Session restored \u2014 {n} exchange(s) \u2014 started {self._session.created_at}"
        )

    def _rebuild_chat_from_history(self):
        """Reconstruct the chat display from session.history."""
        d = self._chat_display
        d.configure(state="normal")
        d.delete("1.0", "end")
        d.configure(state="disabled")
        self._append_chat_meta(
            "Ollama Chat \u2014 connect and select a model above, then type below.")
        for turn in self._session.history:
            role = turn.get("role", "")
            # "display" stores the user-visible text (without injected file contents)
            text = turn.get("display") or turn.get("content", "")
            if role == "user":
                self._append_chat_user(text)
            elif role == "assistant":
                self._append_chat_ai(text)

    def _update_session_status(self):
        """Refresh the session status label in the status strip."""
        s = _LIVE_SESSION
        if s is None or not s.is_live():
            self._session_status_var.set("No session")
            self._session_status_lbl.configure(fg=_CLR_SESSION_NONE)
        else:
            n  = len(s.history)
            ka = "keep-alive on" if s.keep_alive else "keep-alive off"
            self._session_status_var.set(
                f"\u25cf session \u2022 {n} msg(s) \u2022 {s.created_at} \u2022 {ka}")
            self._session_status_lbl.configure(fg=_CLR_SESSION_LIVE)

    # ═════════════════════════════════════════════════════════════════════════
    # STATUS STRIP  (row 0)
    # ═════════════════════════════════════════════════════════════════════════

    def _build_status_strip(self):
        strip = tk.Frame(self.frame, bg=_CLR_STRIP_BG, padx=8, pady=4)
        strip.grid(row=0, column=0, sticky="ew")
        strip.columnconfigure(20, weight=1)

        self._status_dot = tk.Label(strip, text="\u25cf", font=("", 13),
                                    fg=_CLR_MUTED, bg=_CLR_STRIP_BG)
        self._status_dot.grid(row=0, column=0, padx=(0, 3))

        self._conn_label = tk.Label(strip, text="Not checked", font=("", 9),
                                    fg=_CLR_MUTED, bg=_CLR_STRIP_BG, anchor="w")
        self._conn_label.grid(row=0, column=1, padx=(0, 8))

        tk.Label(strip, text="URL:", font=("", 9), bg=_CLR_STRIP_BG,
                 fg="#555").grid(row=0, column=2, padx=(0, 2))
        self._url_var = tk.StringVar(value=self._session.base_url)
        url_e = ttk.Entry(strip, textvariable=self._url_var, width=26, font=("", 9))
        url_e.grid(row=0, column=3, padx=(0, 4))
        url_e.bind("<Return>", lambda e: self._check_connection())

        ttk.Button(strip, text="\u21bb", width=3,
                   command=self._check_connection).grid(row=0, column=4, padx=(0, 8))

        tk.Label(strip, text="Model:", font=("", 9), bg=_CLR_STRIP_BG,
                 fg="#555").grid(row=0, column=5, padx=(0, 2))
        self._model_var = tk.StringVar(
            value=self._session.selected_model or "(none)")
        self._model_combo = ttk.Combobox(strip, textvariable=self._model_var,
                                         values=[], width=26, state="readonly",
                                         font=("", 9))
        self._model_combo.grid(row=0, column=6, padx=(0, 4))
        self._model_combo.bind("<<ComboboxSelected>>", self._on_model_select)

        tk.Label(strip, text="Temp:", font=("", 9), bg=_CLR_STRIP_BG,
                 fg="#555").grid(row=0, column=7, padx=(0, 2))
        self._temp_var = tk.StringVar(value=self._session.temperature)
        ttk.Entry(strip, textvariable=self._temp_var, width=5,
                  font=("", 9)).grid(row=0, column=8)

        # Spacer
        tk.Frame(strip, bg=_CLR_STRIP_BG).grid(row=0, column=20, sticky="ew")

        # Session status (right side)
        self._session_status_var = tk.StringVar(value="No session")
        self._session_status_lbl = tk.Label(
            strip, textvariable=self._session_status_var,
            font=("", 8), fg=_CLR_SESSION_NONE, bg=_CLR_STRIP_BG, anchor="e")
        self._session_status_lbl.grid(row=0, column=21, padx=(8, 4))

    # ═════════════════════════════════════════════════════════════════════════
    # NOTEBOOK — three tabs
    # ═════════════════════════════════════════════════════════════════════════

    def _build_notebook(self):
        self._notebook = ttk.Notebook(self.frame)
        self._notebook.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 0))

        self._chat_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._chat_tab, text="  Chat  ")
        self._build_chat_tab()

        self._attach_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._attach_tab, text="  Attachments  ")
        self._build_attachments_tab()

        self._tools_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._tools_tab, text="  Tools / Settings  ")
        self._build_tools_tab()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 1: CHAT
    # ═════════════════════════════════════════════════════════════════════════

    def _build_chat_tab(self):
        tab = self._chat_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=0)
        tab.rowconfigure(2, weight=0)

        # ── Conversation display ─────────────────────────────────────────────
        conv_frame = ttk.Frame(tab)
        conv_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 2))
        conv_frame.columnconfigure(0, weight=1)
        conv_frame.rowconfigure(0, weight=1)

        self._chat_display = tk.Text(
            conv_frame, wrap="word", state="disabled",
            font=("", 10), background=_CLR_CHAT_BG,
            relief="flat", borderwidth=0, padx=12, pady=10,
            cursor="xterm", spacing3=2)
        csb = ttk.Scrollbar(conv_frame, orient="vertical",
                             command=self._chat_display.yview)
        self._chat_display.configure(yscrollcommand=csb.set)
        self._chat_display.grid(row=0, column=0, sticky="nsew")
        csb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._chat_display)

        # Ctrl+C copies selected text even in disabled state
        self._chat_display.bind("<Control-c>", self._copy_chat_selection)
        self._chat_display.bind("<Control-C>", self._copy_chat_selection)

        # Text tags
        self._chat_display.tag_configure("user_name",
            font=("", 9, "bold"), foreground=_CLR_USER_FG,
            justify="right", spacing1=12)
        self._chat_display.tag_configure("user_msg",
            font=("", 10), foreground=_CLR_USER_FG,
            background=_CLR_USER_BG, justify="right",
            lmargin1=120, lmargin2=120, rmargin=8,
            spacing1=2, spacing3=4)
        self._chat_display.tag_configure("ai_name",
            font=("", 9, "bold"), foreground=_CLR_AI_FG,
            justify="left", spacing1=12)
        self._chat_display.tag_configure("ai_msg",
            font=("", 10), foreground=_CLR_AI_FG,
            background=_CLR_AI_BG, justify="left",
            lmargin1=8, lmargin2=8, rmargin=120,
            spacing1=2, spacing3=4)
        self._chat_display.tag_configure("meta",
            font=("", 8), foreground=_CLR_SYS_FG,
            justify="center", spacing1=2, spacing3=6)
        self._chat_display.tag_configure("thinking",
            font=("", 9, "italic"), foreground=_CLR_THINKING,
            justify="left", lmargin1=8, spacing1=2, spacing3=4)
        self._chat_display.tag_configure("separator",
            font=("", 4), foreground=_CLR_CHAT_BG,
            justify="center", spacing1=0, spacing3=0)

        self._append_chat_meta(
            "Ollama Chat \u2014 connect and select a model above, then type below.")

        # ── Utility row ──────────────────────────────────────────────────────
        util_row = ttk.Frame(tab, padding=(6, 3))
        util_row.grid(row=1, column=0, sticky="ew")

        ttk.Button(util_row, text="\U0001f4c4 Upload File",
                   command=self._choose_file, width=14).pack(side="left", padx=(0, 4))
        ttk.Button(util_row, text="\U0001f4c1 Upload Dir",
                   command=self._choose_directory, width=14).pack(side="left", padx=(0, 4))

        ttk.Separator(util_row, orient="vertical").pack(
            side="left", fill="y", padx=6, pady=2)

        ttk.Button(util_row, text="\U0001f4be Save Chat",
                   command=self._save_chat, width=12).pack(side="left", padx=(4, 4))
        ttk.Button(util_row, text="\U0001f4cb Copy Last Response",
                   command=self._copy_response, width=20).pack(side="left", padx=(0, 4))
        ttk.Button(util_row, text="Clear Chat",
                   command=self._clear_chat, width=10).pack(side="left", padx=(0, 4))

        self._attach_badge_var = tk.StringVar(value="")
        self._attach_badge = ttk.Label(util_row, textvariable=self._attach_badge_var,
                                        font=("", 8), foreground=_CLR_MUTED)
        self._attach_badge.pack(side="right", padx=8)

        # ── Composer ─────────────────────────────────────────────────────────
        comp_frame = ttk.Frame(tab, padding=(6, 2, 6, 6))
        comp_frame.grid(row=2, column=0, sticky="ew")
        comp_frame.columnconfigure(0, weight=1)

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

        self._composer.bind("<Control-Return>", lambda e: (self._on_send(), "break"))
        self._composer.bind("<Shift-Return>",   lambda e: (self._on_send(), "break"))

        self._send_btn = ttk.Button(input_row, text="Send \u25b6",
                                     command=self._on_send, width=8)
        self._send_btn.grid(row=0, column=1, sticky="ns")

    # ── Chat display helpers ─────────────────────────────────────────────────

    def _append_chat_user(self, text):
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n", "separator")
        d.insert("end", "You\n", "user_name")
        d.insert("end", text + "\n", "user_msg")
        d.configure(state="disabled")
        d.see("end")

    def _append_chat_ai(self, text, meta=""):
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n", "separator")
        short = (self._selected_model.split(":")[0]
                 if ":" in self._selected_model else self._selected_model) or "AI"
        d.insert("end", f"{short}\n", "ai_name")
        d.insert("end", text + "\n", "ai_msg")
        if meta:
            d.insert("end", meta + "\n", "meta")
        d.configure(state="disabled")
        d.see("end")

    def _append_chat_meta(self, text):
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n" + text + "\n", "meta")
        d.configure(state="disabled")
        d.see("end")

    def _show_thinking(self):
        d = self._chat_display
        d.configure(state="normal")
        d.insert("end", "\n", "separator")
        short = (self._selected_model.split(":")[0]
                 if ":" in self._selected_model else self._selected_model) or "AI"
        d.insert("end", f"{short}\n", "ai_name")
        mark_line = int(d.index("end-1c").split(".")[0])
        d.insert("end", "Thinking\u2026\n", "thinking")
        d.configure(state="disabled")
        d.see("end")
        return mark_line

    def _remove_thinking(self, mark_line):
        d = self._chat_display
        d.configure(state="normal")
        try:
            del_from = max(1, mark_line - 2)
            d.delete(f"{del_from}.0", "end")
        except Exception:
            pass
        d.configure(state="disabled")

    def _copy_chat_selection(self, event=None):
        """Copy selected transcript text to clipboard (Ctrl+C on disabled widget)."""
        try:
            text = self._chat_display.get("sel.first", "sel.last")
            if text:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(text)
                self._set_status(f"Copied {len(text)} chars.")
        except tk.TclError:
            pass
        return "break"

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 2: ATTACHMENTS
    # ═════════════════════════════════════════════════════════════════════════

    def _build_attachments_tab(self):
        tab = self._attach_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

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

        self._scan_status_var = tk.StringVar(value="")
        ttk.Label(ctrl, textvariable=self._scan_status_var,
                  font=("", 8), foreground=_CLR_MUTED).pack(side="left", padx=8)

        # Context mode toggle — initialised from session
        toggle_f = ttk.Frame(ctrl)
        toggle_f.pack(side="right")
        self._context_mode_var = tk.StringVar(value=self._session.context_mode)
        self._context_mode_var.trace_add(
            "write", lambda *_: self._on_context_mode_change())
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
        self._attach_tree.heading("name",   text="Name")
        self._attach_tree.heading("type",   text="Type")
        self._attach_tree.heading("size",   text="Size")
        self._attach_tree.heading("status", text="Status")
        self._attach_tree.column("name",   width=300, minwidth=120)
        self._attach_tree.column("type",   width=70,  minwidth=50,  anchor="center")
        self._attach_tree.column("size",   width=80,  minwidth=50,  anchor="e")
        self._attach_tree.column("status", width=100, minwidth=60,  anchor="center")

        vsb = ttk.Scrollbar(list_frame, orient="vertical",
                             command=self._attach_tree.yview)
        self._attach_tree.configure(yscrollcommand=vsb.set)
        self._attach_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._drop_label = tk.Label(
            list_frame,
            text="No attachments.\nUse buttons above or on the Chat tab to add files.",
            font=("", 9), fg=_CLR_MUTED, bg=_CLR_BG_PANE, justify="center")
        self._drop_label.place(relx=0.5, rely=0.5, anchor="center")

        rm_frame = ttk.Frame(list_frame)
        rm_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(rm_frame, text="Remove Selected", width=16,
                   command=self._remove_selected_attachments).pack(side="left")
        self._attach_count_var = tk.StringVar(value="0 files")
        ttk.Label(rm_frame, textvariable=self._attach_count_var,
                  font=("", 8), foreground=_CLR_MUTED).pack(side="right", padx=4)

    def _on_context_mode_change(self):
        self._session.context_mode = self._context_mode_var.get()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 3: TOOLS / SETTINGS
    # ═════════════════════════════════════════════════════════════════════════

    def _build_tools_tab(self):
        tab = self._tools_tab
        tab.columnconfigure(0, weight=1)

        pad = {"padx": 10, "pady": (6, 2), "sticky": "ew"}

        # ── System prompt ────────────────────────────────────────────────────
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

        # ── Save / Export ────────────────────────────────────────────────────
        save_lf = ttk.LabelFrame(tab, text="Save / Export", padding=(8, 6))
        save_lf.grid(row=1, column=0, **pad)

        btn_row = ttk.Frame(save_lf)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="\U0001f4be Save Last Response",
                   command=self._save_response, width=22).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="\U0001f4cb Copy Last Response",
                   command=self._copy_response, width=22).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="\U0001f4e6 Save Exchange Bundle",
                   command=self._save_exchange_bundle, width=24).pack(side="left")

        # ── Background Session ───────────────────────────────────────────────
        sess_lf = ttk.LabelFrame(tab, text="Background Session", padding=(8, 6))
        sess_lf.grid(row=2, column=0, **pad)
        sess_lf.columnconfigure(0, weight=1)

        self._keep_alive_var = tk.BooleanVar(value=self._session.keep_alive)
        ttk.Checkbutton(
            sess_lf,
            text="Keep session alive in background (preserve history across page switches)",
            variable=self._keep_alive_var,
            command=self._on_keep_alive_toggle,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        ttk.Label(
            sess_lf,
            text="When enabled: switching away and back restores your conversation.\n"
                 "When disabled: leaving the page ends the session.",
            font=("", 8), foreground=_CLR_MUTED, justify="left",
        ).grid(row=1, column=0, sticky="w", padx=(18, 0))

        kill_row = ttk.Frame(sess_lf)
        kill_row.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(kill_row, text="\u2716 Reset / Kill Session",
                   command=self._kill_session, width=24).pack(side="left")
        ttk.Label(kill_row,
                  text="  Clears history and attachments. Connection is preserved.",
                  font=("", 8), foreground=_CLR_MUTED).pack(side="left")

        # ── Connection / Debug ───────────────────────────────────────────────
        debug_lf = ttk.LabelFrame(tab, text="Connection / Debug", padding=(8, 6))
        debug_lf.grid(row=3, column=0, **pad)
        debug_lf.columnconfigure(0, weight=1)

        self._debug_info_var = tk.StringVar(value="No connection info yet.")
        ttk.Label(debug_lf, textvariable=self._debug_info_var,
                  font=("", 9), foreground=_CLR_MUTED, wraplength=600,
                  anchor="w", justify="left").grid(row=0, column=0, sticky="ew")

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
                  foreground="#555", font=("", 9)).grid(row=0, column=0, sticky="ew")

    def _set_status(self, msg):
        self._status_var.set(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # CONNECTION / MODELS
    # ═════════════════════════════════════════════════════════════════════════

    def _check_connection(self):
        url = self._url_var.get().strip()
        if url:
            self._client.base_url    = url.rstrip("/")
            self._session.base_url   = url.rstrip("/")

        self._set_status("Checking connection\u2026")
        self._conn_label.configure(text="Checking\u2026", fg=_CLR_WARN)
        self._status_dot.configure(fg=_CLR_WARN)

        def _bg():
            ping = self._client.ping()
            models_result = self._client.list_models() if ping["ok"] else None
            self.frame.after(0, lambda: self._apply_connection(ping, models_result))

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_connection(self, ping, models_result):
        if ping["ok"]:
            self._connected = True
            ver   = ping.get("version") or ""
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
            self._set_status(f"Connection failed: {ping.get('error', 'unknown')}")

        self._update_session_status()

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
            f"Session keep-alive: {self._session.keep_alive}",
            f"Session since: {self._session.created_at}",
        ]
        self._debug_info_var.set("  |  ".join(parts))

    # ═════════════════════════════════════════════════════════════════════════
    # ATTACHMENT MANAGEMENT
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
        self._scanning    = True
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
                                   if not d.startswith(".") and d != "__pycache__"]
                    for fname in sorted(filenames):
                        if self._scan_cancel or count >= MAX_SCAN_FILES:
                            break
                        if fname.startswith("."):
                            continue
                        fpath = os.path.join(dirpath, fname)
                        try:
                            fsize = os.path.getsize(fpath)
                        except OSError:
                            fsize = 0
                        ext      = os.path.splitext(fname)[1].lower()
                        readable = ext in READABLE_EXTENSIONS
                        if readable and (total_bytes + fsize) > MAX_SCAN_BYTES:
                            readable = False
                        entry = {
                            "path": fpath, "name": fname, "is_dir": False,
                            "readable": readable, "ext": ext, "size": fsize,
                            "status": "readable" if readable else "skipped",
                        }
                        self._attachments.append(entry)
                        if readable:
                            total_bytes += fsize
                        count += 1
                        if count % 10 == 0:
                            self.frame.after(
                                0, lambda c=count:
                                self._scan_status_var.set(f"Scanning\u2026 {c} files"))
                    if count >= MAX_SCAN_FILES:
                        break
            except Exception as exc:
                self.frame.after(0, lambda: self._set_status(f"Scan error: {exc}"))
            finally:
                cancelled = self._scan_cancel
                self._scanning = self._scan_cancel = False
                def _finish():
                    self._cancel_btn.pack_forget()
                    tag = " (cancelled)" if cancelled else ""
                    self._scan_status_var.set(f"Done: {count} files{tag}")
                    self._refresh_attach_display()
                    self._update_attach_badge()
                    self._set_status(f"Directory scan complete: {count} file(s){tag}.")
                self.frame.after(0, _finish)

        threading.Thread(target=_bg, daemon=True).start()

    def _cancel_scan(self):
        self._scan_cancel = True
        self._scan_status_var.set("Cancelling\u2026")

    def _add_attachment(self, path, is_dir=False):
        for a in self._attachments:
            if a["path"] == path:
                return
        ext      = os.path.splitext(path)[1].lower()
        readable = ext in READABLE_EXTENSIONS
        try:
            size = os.path.getsize(path) if not is_dir else 0
        except OSError:
            size = 0
        self._attachments.append({
            "path": path, "name": os.path.basename(path), "is_dir": is_dir,
            "readable": readable, "ext": ext, "size": size,
            "status": "readable" if readable else "skipped",
        })

    def _refresh_attach_display(self):
        self._attach_tree.delete(*self._attach_tree.get_children())
        for i, a in enumerate(self._attachments):
            self._attach_tree.insert(
                "", "end", iid=str(i),
                values=(a["name"], a.get("ext", ""),
                        self._fmt_size(a["size"]), a["status"]))
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
        for idx in sorted([int(s) for s in sel], reverse=True):
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
        self._attach_badge_var.set(
            f"\U0001f4ce {n} file{'s' if n != 1 else ''} attached" if n else "")

    @staticmethod
    def _fmt_size(n):
        if n < 1024:       return f"{n} B"
        elif n < 1048576:  return f"{n / 1024:.1f} KB"
        else:              return f"{n / 1048576:.1f} MB"

    # ═════════════════════════════════════════════════════════════════════════
    # CONTEXT ASSEMBLY
    # ═════════════════════════════════════════════════════════════════════════

    def _assemble_context(self) -> str:
        if not self._attachments:
            return ""
        mode  = self._context_mode_var.get()
        parts = []
        total_read = 0
        for a in self._attachments:
            path = a["path"]
            if mode == "paths_only":
                parts.append(f"[attached] {path}")
            else:
                if a["readable"] and a["status"] == "readable":
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as fh:
                            budget = MAX_SCAN_BYTES - total_read
                            if budget <= 0:
                                parts.append(f"[attached: budget exceeded] {path}")
                                continue
                            content = fh.read(budget)
                            total_read += len(content.encode("utf-8", errors="replace"))
                            parts.append(
                                f"--- {path} ---\n{content}\n"
                                f"--- end {os.path.basename(path)} ---")
                    except Exception as exc:
                        parts.append(f"[attached: read error: {exc}] {path}")
                else:
                    parts.append(f"[attached: binary/skipped] {path}")
        return "\n\n".join(parts)

    # ═════════════════════════════════════════════════════════════════════════
    # SEND
    # ═════════════════════════════════════════════════════════════════════════

    def _on_send(self):
        model = self._model_var.get().strip()
        if not model or model == "(none)":
            self._set_status("Select a model first.")
            return
        if not self._connected:
            self._set_status("Not connected to Ollama \u2014 check URL and refresh.")
            return
        user_text = self._composer.get("1.0", "end-1c").strip()
        if not user_text:
            self._set_status("Type a message first.")
            return

        system_text = self._system_txt.get("1.0", "end-1c").strip()

        # Persist widget state to session before send
        self._session.system_prompt = system_text
        self._session.context_mode  = self._context_mode_var.get()
        self._session.base_url      = self._url_var.get().strip()
        try:
            self._session.temperature = self._temp_var.get()
        except Exception:
            pass

        context = self._assemble_context()
        full_user = (f"{user_text}\n\n--- Attached Context ---\n{context}"
                     if context else user_text)

        try:
            temp = float(self._temp_var.get())
            temp = max(0.0, min(2.0, temp))
        except ValueError:
            temp = 0.7

        self._append_chat_user(user_text)
        if self._attachments:
            n    = len(self._attachments)
            mode = self._context_mode_var.get().replace("_", " ")
            self._append_chat_meta(
                f"\U0001f4ce {n} file{'s' if n != 1 else ''} attached ({mode})")

        self._composer.delete("1.0", "end")

        # "display" stores the visible bubble text; "content" has the full context payload
        self._history.append({"role": "user", "content": full_user, "display": user_text})

        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        for turn in self._history:
            messages.append({"role": turn["role"], "content": turn["content"]})

        thinking_mark = self._show_thinking()
        self._send_btn.configure(state="disabled")
        self._set_status(f"Sending to {model}\u2026")

        def _bg():
            result = self._client.chat(
                model=model, messages=messages, temperature=temp)
            self.frame.after(0, lambda: self._handle_response(
                result, model, system_text, user_text, full_user, temp, thinking_mark))

        threading.Thread(target=_bg, daemon=True).start()

    def _handle_response(self, result, model, system_text, user_text,
                         full_user, temperature, thinking_mark):
        self._send_btn.configure(state="normal")
        self._remove_thinking(thinking_mark)

        if result["ok"]:
            content = result["content"]
            self._last_response = content

            dur   = result.get("total_duration", 0)
            evals = result.get("eval_count", 0)
            dur_s = dur / 1e9 if dur else 0
            meta_parts = []
            if dur_s:  meta_parts.append(f"{dur_s:.1f}s")
            if evals:  meta_parts.append(f"{evals} tokens")
            meta = " \u2022 ".join(meta_parts)

            self._append_chat_ai(content, meta=meta)
            self._history.append({"role": "assistant", "content": content})

            self._last_exchange = {
                "timestamp":          datetime.datetime.now().isoformat(),
                "model":              model,
                "temperature":        temperature,
                "system_prompt":      system_text,
                "user_prompt":        user_text,
                "context_mode":       self._context_mode_var.get(),
                "attachments":        [{"path": a["path"], "status": a["status"],
                                        "readable": a["readable"]}
                                       for a in self._attachments],
                "full_user_content":  full_user,
                "response":           content,
                "total_duration_ns":  result.get("total_duration", 0),
                "eval_count":         result.get("eval_count", 0),
            }
            self._set_status(f"Response received ({len(content)} chars).")
        else:
            err = result.get("error", "unknown error")
            self._last_response = ""
            self._append_chat_ai(f"[Error] {err}")
            self._set_status(f"Request failed: {err}")

        self._update_session_status()
        self._composer.focus_set()

    # ═════════════════════════════════════════════════════════════════════════
    # SAVE / COPY / CLEAR
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
            title="Save Response", defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._set_status(f"Response saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save:\n{exc}")

    def _save_chat(self):
        if not self._history:
            self._set_status("No conversation to save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Chat", defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"),
                       ("JSON", "*.json"), ("All", "*.*")])
        if not path:
            return
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".json":
                export = {
                    "timestamp":   datetime.datetime.now().isoformat(),
                    "model":       self._selected_model,
                    "system_prompt": self._system_txt.get("1.0", "end-1c").strip(),
                    "turns":       self._history,
                }
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(export, fh, indent=2, ensure_ascii=False)
            else:
                lines = [
                    "# Chat Log",
                    f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"**Model:** {self._selected_model}",
                    "", "---", "",
                ]
                for turn in self._history:
                    role = turn["role"].capitalize()
                    text = turn.get("display") or turn["content"]
                    lines += [f"### {role}", "", text, "", "---", ""]
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            self._set_status(f"Chat saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save chat:\n{exc}")

    def _clear_chat(self):
        self._history.clear()
        self._last_response  = ""
        self._last_exchange  = {}
        d = self._chat_display
        d.configure(state="normal")
        d.delete("1.0", "end")
        d.configure(state="disabled")
        self._append_chat_meta(
            "Chat cleared. Type a message below to start a new conversation.")
        self._set_status("Chat cleared.")
        self._update_session_status()

    # ═════════════════════════════════════════════════════════════════════════
    # SAVE EXCHANGE BUNDLE
    # ═════════════════════════════════════════════════════════════════════════

    def _save_exchange_bundle(self):
        if not self._last_exchange:
            self._set_status("No exchange to save \u2014 send a prompt first.")
            return
        folder = filedialog.askdirectory(title="Choose folder for exchange bundle")
        if not folder:
            return

        now       = datetime.datetime.now()
        stamp     = now.strftime("%Y%m%d_%H%M%S")
        bundle_dir = os.path.join(folder, f"exchange_{stamp}")
        os.makedirs(bundle_dir, exist_ok=True)

        ex = self._last_exchange

        with open(os.path.join(bundle_dir, "exchange.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(ex, fh, indent=2, ensure_ascii=False)

        summary_lines = [
            "# Exchange Summary",
            f"**Date:** {ex.get('timestamp', '')}",
            f"**Model:** {ex.get('model', '')}",
            f"**Temperature:** {ex.get('temperature', '')}",
            "", "## System Prompt",
            ex.get("system_prompt", "(none)") or "(none)",
            "", "## User Prompt",
            ex.get("user_prompt", ""),
            "", "## Attachments",
            f"Mode: {ex.get('context_mode', 'paths_only')}",
        ]
        for att in ex.get("attachments", []):
            summary_lines.append(f"- {att['path']}  [{att['status']}]")
        summary_lines += [
            "", "## Response",
            ex.get("response", ""),
            "", "---",
            f"*Duration: {ex.get('total_duration_ns', 0) / 1e9:.1f}s "
            f"| Tokens: {ex.get('eval_count', 0)}*",
        ]
        with open(os.path.join(bundle_dir, "exchange_summary.md"),
                  "w", encoding="utf-8") as fh:
            fh.write("\n".join(summary_lines) + "\n")

        with open(os.path.join(bundle_dir, "attachment_manifest.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"context_mode": ex.get("context_mode", "paths_only"),
                       "files": ex.get("attachments", [])},
                      fh, indent=2, ensure_ascii=False)

        with open(os.path.join(bundle_dir, "settings_metadata.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({
                "model":              ex.get("model", ""),
                "temperature":        ex.get("temperature", 0.7),
                "base_url":           self._client.base_url,
                "timestamp":          ex.get("timestamp", ""),
                "total_duration_ns":  ex.get("total_duration_ns", 0),
                "eval_count":         ex.get("eval_count", 0),
            }, fh, indent=2, ensure_ascii=False)

        with open(os.path.join(bundle_dir, "conversation_history.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"turns": self._history}, fh, indent=2, ensure_ascii=False)

        self._set_status(f"Bundle saved: {bundle_dir}")

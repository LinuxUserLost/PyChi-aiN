"""
page_chilaude_terminal / chilaude_terminal_page.py
────────────────────────────────────────────────────────────────────────────────
Chilaude Terminal — Claude CLI inspired terminal page for pagepack_chilaude_terminal.

Cloned from pagepack_chilos/page_terminal_session. Behavior is identical to the
source; specialization for Claude CLI usage is deferred to a later pass.

Shell contract:
    page = PageChilaudeTerminal(parent_widget)
    page.build(parent)   # also accepted: create_widgets / mount / render

Tabs:
    1. Session        — task/subtask selection, ordered queue, command runner,
                        live session log view with Last/Next CMD navigation
    2. Command Editor — browse/edit .json command library with machine-view pane

Storage (all paths relative to pack root, created at runtime):
    /pagepack_chilaude_terminal/terminalhistory/   — .md session log files
    /pagepack_chilaude_terminal/linuxcommands/     — .json command records
    /pagepack_chilaude_terminal/task_options.json  — persisted task/subtask lists

Execution model:
    subprocess.run(shell=True, capture_output=True, timeout=120)
    Runs in a background thread; results posted back via frame.after(0, ...).
    One command at a time. Run controls disabled during execution.

Scroll handling: Linux/Wayland-safe (Button-4 / Button-5 / MouseWheel).
"""

import os
import re
import json
import uuid
import datetime
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path

# Live-terminal additions (Linux/POSIX)
import select
import signal
import queue as _queue
try:
    import pty       # POSIX-only; falls back to inert if absent
    import fcntl
    import termios
    import struct
    _PTY_AVAILABLE = True
except Exception:
    _PTY_AVAILABLE = False

# ANSI / OSC escape stripper for the session view
_ANSI_CSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)")
_ANSI_OTHER_RE = re.compile(r"\x1B[@-Z\\-_]")

def _strip_ansi(s: str) -> str:
    s = _ANSI_OSC_RE.sub("", s)
    s = _ANSI_CSI_RE.sub("", s)
    s = _ANSI_OTHER_RE.sub("", s)
    # Translate carriage returns to newlines for sane line flow.
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _yaml_scalar(s: str) -> str:
    """Quote a scalar for the YAML front matter if it contains specials."""
    s = "" if s is None else str(s)
    if s == "":
        return '""'
    bad = any(c in s for c in (':', '#', '\n', '"', "'", '{', '}', '[', ']', ',', '&', '*', '!', '|', '>', '%', '@', '`'))
    if bad or s != s.strip():
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bind_scroll(widget):
    """Attach scroll events directly to a widget (Wayland/Linux-safe)."""
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


def _bind_text_shortcuts(widget):
    """Bind Ctrl+A (select all) to a Text or Entry widget."""
    is_text = isinstance(widget, tk.Text)

    def _select_all(event):
        if is_text:
            state_before = str(widget.cget("state"))
            if state_before == "disabled":
                widget.configure(state="normal")
            widget.tag_add("sel", "1.0", "end")
            widget.mark_set("insert", "end")
            if state_before == "disabled":
                widget.configure(state="disabled")
        else:
            widget.select_range(0, "end")
            widget.icursor("end")
        return "break"

    widget.bind("<Control-a>", _select_all, add=False)
    widget.bind("<Control-A>", _select_all, add=False)


def _make_listbox(parent, height=8):
    """Return (frame, listbox) with vertical scrollbar, ready to grid."""
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


def _make_machine_view(parent):
    """
    Build a read-only Text widget styled as a dark machine-view JSON pane.
    Returns (outer_labelframe, text_widget).
    """
    outer = ttk.LabelFrame(parent, text="Record JSON", padding=(4, 2))
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)

    mono = ("Consolas", 9) if os.name == "nt" else ("monospace", 9)
    tw = tk.Text(outer, wrap="none", state="disabled", font=mono,
                 background="#1e1e2e", foreground="#cdd6f4",
                 insertbackground="#cdd6f4", relief="flat",
                 borderwidth=0, padx=6, pady=4)
    vsb = ttk.Scrollbar(outer, orient="vertical",  command=tw.yview)
    hsb = ttk.Scrollbar(outer, orient="horizontal", command=tw.xview)
    tw.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tw.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    _bind_scroll(tw)
    _bind_text_shortcuts(tw)

    tw.tag_configure("hl_field",
                     background="#313244", foreground="#f9e2af",
                     selectbackground="#45475a")
    tw.tag_configure("json_key", foreground="#89b4fa")
    tw.tag_configure("json_str", foreground="#a6e3a1")
    tw.tag_configure("json_lit", foreground="#fab387")
    return outer, tw


def _render_json_highlighted(tw, record, active_field):
    """
    Render *record* as pretty-printed JSON into *tw* with syntax colour and
    active-field line highlighting.  Copied from prompt_workshop pattern.
    """
    tw.configure(state="normal")
    tw.delete("1.0", "end")
    pretty = json.dumps(record, indent=2, ensure_ascii=False)
    tw.insert("1.0", pretty)

    line_count = int(tw.index("end-1c").split(".")[0])
    active_key_pat = '"' + active_field + '"' if active_field else None

    hl_start = hl_end = None
    inside_active = False
    indent_at_key = None

    for lineno in range(1, line_count + 1):
        line = tw.get(f"{lineno}.0", f"{lineno}.end")

        # ── Syntax colouring ────────────────────────────────────────────────
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                j = i + 1
                while j < len(line):
                    if line[j] == '\\': j += 2; continue
                    if line[j] == '"':  break
                    j += 1
                end = j + 1
                tag = "json_key" if line[end:].lstrip().startswith(":") else "json_str"
                tw.tag_add(tag, f"{lineno}.{i}", f"{lineno}.{end}")
                i = end; continue
            matched_lit = False
            for lit in ("true", "false", "null"):
                if line[i:i+len(lit)] == lit:
                    tw.tag_add("json_lit", f"{lineno}.{i}", f"{lineno}.{i+len(lit)}")
                    i += len(lit); matched_lit = True; break
            if matched_lit:
                continue
            if ch in "0123456789-":
                j = i + 1
                while j < len(line) and line[j] in "0123456789.eE+-":
                    j += 1
                if j > i + (1 if ch == '-' else 0):
                    tw.tag_add("json_lit", f"{lineno}.{i}", f"{lineno}.{j}")
                i = j; continue
            i += 1

        if active_key_pat is None:
            continue

        stripped = line.lstrip()
        leading  = len(line) - len(stripped)

        if leading == 2 and stripped.startswith('"'):
            if inside_active:
                hl_end = lineno - 1
                inside_active = False
            if active_key_pat in line:
                hl_start = lineno
                inside_active = True
                indent_at_key = leading
        elif inside_active:
            if leading > indent_at_key:
                pass
            elif stripped in ("],", "]", "},", "}"):
                pass
            else:
                hl_end = lineno - 1
                inside_active = False
                if leading == 2 and stripped.startswith('"') and active_key_pat in line:
                    hl_start = lineno
                    inside_active = True
                    indent_at_key = leading

    if inside_active:
        hl_end = line_count
    if hl_start is not None and hl_end is not None:
        for ln in range(hl_start, hl_end + 1):
            tw.tag_add("hl_field", f"{ln}.0", f"{ln}.end")
        tw.see(f"{hl_start}.0")

    tw.configure(state="disabled")


def _slugify(text, max_len=32):
    """Turn arbitrary text into a filesystem-safe lowercase slug."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:max_len] if s else "untitled"


def _safe_filename(title):
    """Turn a title into a filesystem-safe stem (preserves case, allows digits)."""
    s = title.strip().replace(" ", "_").replace("/", "-").replace("\\", "-")
    s = "".join(c for c in s if c.isalnum() or c in ("_", "-", "."))
    return s or "untitled"


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _find_project_root() -> str:
    """
    Walk upward from this file's location and return the first directory
    that contains guichi.py (the Guichi launcher).
    Falls back to os.getcwd(), then os.path.expanduser("~").
    """
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "guichi.py").exists():
            return str(candidate)
    try:
        return os.getcwd()
    except Exception:
        return os.path.expanduser("~")


def _unique_path(directory, stem, ext=".json"):
    """Return a path that does not exist, appending _1, _2 … as needed."""
    candidate = os.path.join(directory, stem + ext)
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        candidate = os.path.join(directory, f"{stem}_{n}{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _next_log_number(terminalhistory_dir):
    """
    Scan terminalhistory_dir for existing log filenames and return max+1.
    Filename pattern:  YYYY_wkWW_D_logNNNNN.md
    """
    max_n = 0
    if not os.path.isdir(terminalhistory_dir):
        return 1
    for fname in os.listdir(terminalhistory_dir):
        if not fname.endswith(".md"):
            continue
        m = re.search(r"log(\d+)", fname)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _make_log_filename(log_num):  # noqa — kept in place; full body below
    """
    Build a log filename:  2026_wk13_0_log00001.md
    Components: year, wkWW (zero-padded), weekday 0–6 (Mon=0), log#####
    """
    now  = datetime.datetime.now()
    year = now.strftime("%Y")
    week = now.strftime("%W").zfill(2)   # %W: week number, Monday start
    day  = str(now.weekday())            # 0=Monday … 6=Sunday
    return f"{year}_wk{week}_{day}_log{str(log_num).zfill(5)}.md"


# ─────────────────────────────────────────────────────────────────────────────
# Save Command As — modal popup
# ─────────────────────────────────────────────────────────────────────────────

class SaveCommandPopup(tk.Toplevel):
    """
    Modal popup for saving a selected command to the linuxcommands/ library.
    Lets the user browse/create subdirectories, set a title, edit the command,
    and save a .json record.  Calls on_save_callback(saved_path) on success.
    """

    def __init__(self, parent, linuxcommands_dir, prefill_cmd, on_save_callback):
        super().__init__(parent)
        self.title("Save Command As")
        self.transient(parent)
        self.grab_set()
        self.resizable(True, True)
        self.geometry("560x540")
        self._linuxcommands_dir = linuxcommands_dir
        self._prefill_cmd       = prefill_cmd
        self._on_save_callback  = on_save_callback
        self._current_dir       = linuxcommands_dir
        self._build_ui()
        self._populate_dir_list()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        path_f = ttk.Frame(self, padding=(8, 4))
        path_f.grid(row=0, column=0, sticky="ew")
        path_f.columnconfigure(1, weight=1)
        ttk.Label(path_f, text="Saving to:", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self._path_var = tk.StringVar(value=self._linuxcommands_dir)
        ttk.Label(path_f, textvariable=self._path_var, foreground="#555",
                  font=("", 8), anchor="w").grid(row=0, column=1, sticky="ew")

        browser_f = ttk.LabelFrame(self, text="Browse Folders", padding=4)
        browser_f.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        browser_f.columnconfigure(0, weight=1)
        browser_f.rowconfigure(0, weight=1)
        lb_frm, self._dir_lb = _make_listbox(browser_f, height=6)
        lb_frm.grid(row=0, column=0, sticky="nsew")
        self._dir_lb.bind("<Double-1>", lambda e: self._open_selected_dir())
        nav_row = ttk.Frame(browser_f)
        nav_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(nav_row, text="\u2191 Up",      width=7,
                   command=self._go_up).pack(side="left", padx=2)
        ttk.Button(nav_row, text="Open",           width=7,
                   command=self._open_selected_dir).pack(side="left", padx=2)
        ttk.Button(nav_row, text="New Folder\u2026", width=12,
                   command=self._new_subfolder).pack(side="left", padx=2)

        fname_f = ttk.Frame(self, padding=(8, 4))
        fname_f.grid(row=2, column=0, sticky="ew")
        fname_f.columnconfigure(1, weight=1)
        ttk.Label(fname_f, text="Title / Filename:", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self._title_var = tk.StringVar(value=_safe_filename(self._prefill_cmd[:48]))
        ttk.Entry(fname_f, textvariable=self._title_var).grid(row=0, column=1, sticky="ew")

        cmd_f = ttk.LabelFrame(self, text="Command  (editable before save)", padding=4)
        cmd_f.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 4))
        cmd_f.columnconfigure(0, weight=1)
        cmd_f.rowconfigure(0, weight=1)
        mono = ("Consolas", 10) if os.name == "nt" else ("monospace", 10)
        self._cmd_txt = tk.Text(cmd_f, height=6, wrap="word", undo=True, font=mono)
        csb = ttk.Scrollbar(cmd_f, orient="vertical", command=self._cmd_txt.yview)
        self._cmd_txt.configure(yscrollcommand=csb.set)
        self._cmd_txt.grid(row=0, column=0, sticky="nsew")
        csb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._cmd_txt)
        _bind_text_shortcuts(self._cmd_txt)
        self._cmd_txt.insert("1.0", self._prefill_cmd)

        btn_f = ttk.Frame(self, padding=(8, 6))
        btn_f.grid(row=4, column=0, sticky="ew")
        ttk.Button(btn_f, text="Save",   width=10,
                   command=self._on_save).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Cancel", width=10,
                   command=self.destroy).pack(side="left", padx=4)
        self._popup_status = tk.StringVar(value="Choose a folder, set a title, then Save.")
        ttk.Label(btn_f, textvariable=self._popup_status,
                  foreground="#666", font=("", 8)).pack(side="left", padx=8)

    def _populate_dir_list(self):
        self._dir_lb.delete(0, "end")
        self._path_var.set(self._current_dir)
        try:
            entries = sorted(os.listdir(self._current_dir))
        except OSError:
            return
        for name in entries:
            full = os.path.join(self._current_dir, name)
            if os.path.isdir(full):
                self._dir_lb.insert("end", f"[dir]  {name}")
            elif name.endswith(".json"):
                self._dir_lb.insert("end", f"       {name}")

    def _open_selected_dir(self):
        sel = self._dir_lb.curselection()
        if not sel:
            return
        raw  = self._dir_lb.get(sel[0])
        # strip leading tag
        name = re.sub(r"^\[dir\]\s*", "", raw).strip()
        full = os.path.join(self._current_dir, name)
        if os.path.isdir(full):
            self._current_dir = full
            self._populate_dir_list()

    def _go_up(self):
        parent_dir = os.path.dirname(self._current_dir)
        root_abs   = os.path.abspath(self._linuxcommands_dir)
        if len(os.path.abspath(parent_dir)) >= len(root_abs):
            self._current_dir = parent_dir
            self._populate_dir_list()

    def _new_subfolder(self):
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name:
            return
        slug = _slugify(name)
        if not slug:
            messagebox.showwarning("Invalid Name",
                                   "Could not create a valid folder name.", parent=self)
            return
        new_path = os.path.join(self._current_dir, slug)
        try:
            os.makedirs(new_path, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Error", str(exc), parent=self); return
        self._current_dir = new_path
        self._populate_dir_list()
        self._popup_status.set(f"Created: {slug}")

    def _on_save(self):
        title    = self._title_var.get().strip()
        cmd_text = self._cmd_txt.get("1.0", "end-1c").strip().replace("\r\n", "\n").replace("\r", "\n")
        if not title:
            self._popup_status.set("Title is required."); return
        if not cmd_text:
            self._popup_status.set("Command text is empty."); return
        stem      = _safe_filename(title)
        save_path = _unique_path(self._current_dir, stem, ".json")
        now       = _now_iso()
        record    = {
            "id":         str(uuid.uuid4()),
            "title":      title,
            "command":    cmd_text,
            "notes":      "",
            "created_at": now,
            "updated_at": now,
        }
        try:
            os.makedirs(self._current_dir, exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            messagebox.showerror("Save Error", str(exc), parent=self); return
        self._on_save_callback(save_path)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Main page class
# ─────────────────────────────────────────────────────────────────────────────

class PageChilaudeTerminal:
    """
    Chilaude Terminal page for pagepack_chilaude_terminal.

    Shell contract (Guichi loader):
        page = PageChilaudeTerminal(parent_frame)
        page.build(parent)   # also: create_widgets / mount / render
    """

    PAGE_NAME   = "chilaude_terminal"
    _MV_INTERVAL = 300   # ms between machine-view auto-refreshes

    # ─────────────────────────────────────────────────────────────────────────
    # Init
    # ─────────────────────────────────────────────────────────────────────────

    def __init__(self, parent, app=None, page_key="", page_folder="",
                 *args, **kwargs):
        app         = kwargs.pop("controller",    app)
        page_key    = kwargs.pop("page_context",  page_key)
        page_folder = kwargs.pop("page_folder",   page_folder)

        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # ── Path state ──────────────────────────────────────────────────────
        self.pack_root            = ""
        self.terminalhistory_dir  = ""
        self.linuxcommands_dir    = ""

        # ── Task / subtask state ─────────────────────────────────────────────
        self._task_options    = ["general"]
        self._subtask_options = {"general": ["misc"]}

        # ── Session / queue state ────────────────────────────────────────────
        # Each entry dict: {cmd, status, exit, output, ts}
        # status values: "pending" | "running" | "done" | "error"
        self._cwd_var             = tk.StringVar(value="(not started)")
        self._session_queue       = []
        self._running             = False
        self._cmd_nav_lines       = []   # line numbers of ## CMD [ headers in session view
        self._cmd_nav_current_idx = -1   # index into _cmd_nav_lines (used by rebuild/nav)

        # ── Input history state ───────────────────────────────────────────────
        self._input_history       = []   # commands sent via Enter, in order
        self._input_history_idx   = -1   # current position for Last/Next navigation
        self._selected_history_cmd = ""  # last command loaded by Last/Next or sent
        self._last_terminal_response = ""  # most recent terminal output from _pty_pump

        # ── Token Monitor state ──────────────────────────────────────────────
        self._tm_current_var  = tk.StringVar(value="\u2014")
        self._tm_last_var     = tk.StringVar(value="\u2014")
        self._tm_session_var  = tk.StringVar(value="0")
        self._tm_source_var   = tk.StringVar(value="unavailable")
        self._tm_session_total = 0   # running local total, not persisted

        # ── Command editor state ─────────────────────────────────────────────
        self._ce_record       = _empty_command()
        self._ce_json_path    = ""
        self._ce_active_field = "title"
        self._ce_mv_dirty     = True
        self._ce_mv_after_id  = None
        self._ce_files        = []

        # ── Saved command picker state ───────────────────────────────────────
        self._sc_map          = {}   # display label → absolute file path

        # ── Live shell (PTY) state ───────────────────────────────────────────
        self._pty_master_fd  = None
        self._pty_proc       = None      # subprocess.Popen
        self._pty_reader_thr = None
        self._pty_alive      = False
        self._pty_started_at = None
        self._pty_shell_path = os.environ.get("SHELL", "/bin/bash") or "/bin/bash"
        self._pty_out_queue  = _queue.Queue()  # bytes/strings from reader
        self._pty_pump_after = None            # tk after() id for drain loop

        # ── Root frame ───────────────────────────────────────────────────────
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self._build_top_bar()
        self._build_notebook()
        self._build_status_bar()

        self.frame.after(250, self._auto_find_root)
        self._ce_schedule_mv_refresh()

    # ─────────────────────────────────────────────────────────────────────────
    # Shell mount methods  (exact contract — do not rename)
    # ─────────────────────────────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                if self._ce_mv_after_id is not None:
                    try: self.frame.after_cancel(self._ce_mv_after_id)
                    except Exception: pass
                self.frame.destroy()
                self.parent = container
                self.frame  = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1)
                self.frame.rowconfigure(1, weight=1)
                self._build_top_bar()
                self._build_notebook()
                self._build_status_bar()
                self.frame.after(50, self._auto_find_root)
                self._ce_schedule_mv_refresh()
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

    def build(self,           parent=None): return self._embed_into_parent(parent)
    def create_widgets(self,  parent=None): return self._embed_into_parent(parent)
    def mount(self,           parent=None): return self._embed_into_parent(parent)
    def render(self,          parent=None): return self._embed_into_parent(parent)

    # ─────────────────────────────────────────────────────────────────────────
    # Top bar
    # ─────────────────────────────────────────────────────────────────────────

    def _build_top_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 4))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(99, weight=1)
        ttk.Button(bar, text="Auto-Find Root", width=15,
                   command=self._auto_find_root).grid(row=0, column=0, padx=2)
        ttk.Button(bar, text="Choose Root\u2026", width=13,
                   command=self._choose_root).grid(row=0, column=1, padx=2)

        # Live-shell controls
        ttk.Separator(bar, orient="vertical").grid(row=0, column=2, sticky="ns", padx=6)
        self._btn_start_shell = ttk.Button(bar, text="Start Terminal", width=14,
                                           command=self._start_shell)
        self._btn_start_shell.grid(row=0, column=3, padx=2)
        self._btn_stop_shell  = ttk.Button(bar, text="Stop Terminal", width=13,
                                           command=self._stop_shell, state="disabled")
        self._btn_stop_shell.grid(row=0, column=4, padx=2)
        self._btn_ctrl_c      = ttk.Button(bar, text="Send Ctrl-C", width=12,
                                           command=lambda: self._send_signal_byte(b"\x03"),
                                           state="disabled")
        self._btn_ctrl_c.grid(row=0, column=5, padx=2)

        self._shell_status_var = tk.StringVar(value="shell: stopped")
        ttk.Label(bar, textvariable=self._shell_status_var,
                  foreground="#666", font=("", 8), anchor="w"
                  ).grid(row=0, column=6, sticky="w", padx=(8, 4))

        self._root_var = tk.StringVar(value="Root: (not set)")
        ttk.Label(bar, textvariable=self._root_var, foreground="#666",
                  font=("", 8), anchor="w").grid(row=0, column=99, sticky="ew", padx=8)

    # ─────────────────────────────────────────────────────────────────────────
    # Notebook
    # ─────────────────────────────────────────────────────────────────────────

    def _build_notebook(self):
        self._notebook = ttk.Notebook(self.frame)
        self._notebook.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 0))

        # Tab 1 — Session
        session_tab = ttk.Frame(self._notebook)
        session_tab.columnconfigure(0, weight=1)
        session_tab.rowconfigure(3, weight=1)
        self._notebook.add(session_tab, text="Session")
        self._build_session_tab(session_tab)

        # Tab 2 — Command Editor
        editor_tab = ttk.Frame(self._notebook)
        editor_tab.columnconfigure(0, weight=1)
        editor_tab.rowconfigure(0, weight=1)
        self._notebook.add(editor_tab, text="Command Editor")
        self._build_command_editor_tab(editor_tab)

    # ─────────────────────────────────────────────────────────────────────────
    # Status bar
    # ─────────────────────────────────────────────────────────────────────────

    def _build_status_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 2))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self._status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self._status_var, anchor="w",
                  foreground="#666", font=("", 9)).grid(row=0, column=0, sticky="ew")

    def _set_status(self, msg):
        self._status_var.set(msg)

    # ─────────────────────────────────────────────────────────────────────────
    # Root finding
    # ─────────────────────────────────────────────────────────────────────────

    def _auto_find_root(self):
        # Pack root is always two levels up: pack/page_folder/this_file
        pack_root = Path(__file__).resolve().parent.parent
        if pack_root.is_dir():
            self._set_root(str(pack_root)); return
        self._set_status("Root not found \u2014 use Choose Root.")

    def _choose_root(self):
        d = filedialog.askdirectory(title="Select chi_ain chipack directory")
        if d:
            self._set_root(d)

    def _set_root(self, pack_path):
        self.pack_root           = pack_path
        self.terminalhistory_dir = os.path.join(pack_path, "terminalhistory")
        self.linuxcommands_dir   = os.path.join(pack_path, "linuxcommands")
        os.makedirs(self.terminalhistory_dir, exist_ok=True)
        os.makedirs(self.linuxcommands_dir,   exist_ok=True)
        short = pack_path if len(pack_path) <= 60 else "\u2026" + pack_path[-57:]
        self._root_var.set(f"Root: {short}")
        self._load_task_options()
        self._ce_refresh_file_list()
        if hasattr(self, "_sc_lb"):
            self._sc_refresh_list()
        self._set_status(f"Root: {pack_path}")

    # ─────────────────────────────────────────────────────────────────────────
    # Task / Subtask
    # ─────────────────────────────────────────────────────────────────────────

    def _task_options_path(self):
        return os.path.join(self.pack_root, "task_options.json") if self.pack_root else ""

    def _load_task_options(self):
        path = self._task_options_path()
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._task_options    = data.get("tasks",    ["general"])
                self._subtask_options = data.get("subtasks", {"general": ["misc"]})
            except Exception:
                pass
        self._refresh_task_dropdowns()

    def _save_task_options(self):
        path = self._task_options_path()
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"tasks": self._task_options,
                           "subtasks": self._subtask_options},
                          fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._set_status(f"Could not save task options: {exc}")

    def _refresh_task_dropdowns(self):
        try:
            self._task_cb.configure(values=self._task_options)
            if self._task_options and not self._task_var.get():
                self._task_var.set(self._task_options[0])
                self._on_task_changed()
        except Exception:
            pass

    def _on_task_changed(self, event=None):
        task = self._task_var.get()
        subs = self._subtask_options.get(task, [])
        try:
            self._subtask_cb.configure(values=subs)
            self._subtask_var.set(subs[0] if subs else "")
        except Exception:
            pass

    def _add_task(self):
        raw  = self._task_new_var.get().strip()
        slug = _slugify(raw)
        if not slug:
            return
        if slug not in self._task_options:
            self._task_options.append(slug)
            if slug not in self._subtask_options:
                self._subtask_options[slug] = []
            self._task_cb.configure(values=self._task_options)
            self._save_task_options()
        self._task_var.set(slug)
        self._task_new_var.set("")
        self._on_task_changed()
        self._set_status(f"Task added: {slug}")

    def _add_subtask(self):
        task = self._task_var.get()
        if not task:
            self._set_status("Select a task first."); return
        raw  = self._subtask_new_var.get().strip()
        slug = _slugify(raw)
        if not slug:
            return
        if task not in self._subtask_options:
            self._subtask_options[task] = []
        if slug not in self._subtask_options[task]:
            self._subtask_options[task].append(slug)
            self._subtask_cb.configure(values=self._subtask_options[task])
            self._save_task_options()
        self._subtask_var.set(slug)
        self._subtask_new_var.set("")
        self._set_status(f"Subtask added: {slug}")

    # ─────────────────────────────────────────────────────────────────────────
    # SESSION TAB — build
    # ─────────────────────────────────────────────────────────────────────────

    def _build_session_tab(self, parent):
        # Row 0: task/subtask + session header
        hdr = ttk.LabelFrame(parent, text="Session Setup", padding=(8, 4))
        hdr.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        self._build_task_subtask_row(hdr)

        # Row 1: action buttons
        action_bar = ttk.Frame(parent, padding=(4, 2))
        action_bar.grid(row=1, column=0, sticky="ew")
        self._build_action_bar(action_bar)

        # Row 2: Token Monitor
        tm_bar = ttk.Frame(parent, padding=(4, 0))
        tm_bar.grid(row=2, column=0, sticky="ew")
        self._build_token_monitor(tm_bar)

        # Row 3 (expand): HPanedWindow — saved commands | session view
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.grid(row=3, column=0, sticky="nsew", padx=4, pady=(2, 2))

        # ── Left: saved commands ──────────────────────────────────────────────
        sc_outer = ttk.LabelFrame(paned, text="Saved Commands", padding=4)
        sc_outer.columnconfigure(0, weight=1)
        sc_outer.rowconfigure(1, weight=1)

        sc_btn_row = ttk.Frame(sc_outer)
        sc_btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(sc_btn_row, text="+ Add Command", width=14,
                   command=self._sc_add_command).pack(side="left", padx=2)
        ttk.Button(sc_btn_row, text="Refresh", width=7,
                   command=self._sc_refresh_list).pack(side="left", padx=2)

        sc_lb_f, self._sc_lb = _make_listbox(sc_outer, height=10)
        sc_lb_f.grid(row=1, column=0, sticky="nsew")
        paned.add(sc_outer, weight=1)

        # Hidden queue listbox — keeps queue management methods intact.
        self._queue_lb = tk.Listbox(self.frame)

        # ── Right: session view ───────────────────────────────────────────────
        sv_outer = ttk.LabelFrame(paned, text="Session View", padding=4)
        sv_outer.columnconfigure(0, weight=1)
        sv_outer.rowconfigure(1, weight=1)

        # Row 0: current directory display
        cwd_row = ttk.Frame(sv_outer)
        cwd_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        cwd_row.columnconfigure(1, weight=1)
        ttk.Label(cwd_row, text="Current Dir:", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(2, 4))
        ttk.Label(cwd_row, textvariable=self._cwd_var, anchor="w",
                  foreground="#555", font=("", 9)).grid(
            row=0, column=1, sticky="ew")

        # Row 1: session text widget
        mono = ("Consolas", 10) if os.name == "nt" else ("monospace", 10)
        self._session_view = tk.Text(
            sv_outer, wrap="word", state="disabled", font=mono,
            background="#fafaf8", relief="flat", borderwidth=1,
            padx=8, pady=6)
        sv_sb = ttk.Scrollbar(sv_outer, orient="vertical",
                               command=self._session_view.yview)
        self._session_view.configure(yscrollcommand=sv_sb.set)
        self._session_view.grid(row=1, column=0, sticky="nsew")
        sv_sb.grid(row=1, column=1, sticky="ns")
        _bind_scroll(self._session_view)
        _bind_text_shortcuts(self._session_view)

        # CMD-block navigation highlight tag
        self._session_view.tag_configure(
            "cmd_selected", background="#dbeafe", foreground="#1e3a5f")

        # Readability colour tags — page-owned, not ANSI
        self._session_view.tag_configure(
            "cmd_header",     foreground="#2563eb", font=(mono[0], mono[1], "bold"))
        self._session_view.tag_configure(
            "cmd_text",       foreground="#16a34a")
        self._session_view.tag_configure(
            "terminal_output", foreground="#1c1c1c")
        self._session_view.tag_configure(
            "shell_marker",   foreground="#9ca3af")

        paned.add(sv_outer, weight=3)

        # Row 4: command input
        cmd_f = ttk.LabelFrame(parent, text="Command Input  (Enter / Numpad Enter = Send  •  Ctrl+Enter = Add to Queue)",
                                padding=(6, 4))
        cmd_f.grid(row=4, column=0, sticky="ew", padx=4, pady=(2, 4))
        cmd_f.columnconfigure(0, weight=1)
        mono2 = ("Consolas", 11) if os.name == "nt" else ("monospace", 11)
        self._cmd_input = tk.Text(cmd_f, height=4, wrap="word", undo=True,
                                   font=mono2, relief="flat", borderwidth=1,
                                   padx=6, pady=4, insertwidth=2)
        ci_sb = ttk.Scrollbar(cmd_f, orient="vertical", command=self._cmd_input.yview)
        self._cmd_input.configure(yscrollcommand=ci_sb.set)
        self._cmd_input.grid(row=0, column=0, sticky="ew")
        ci_sb.grid(row=0, column=1, sticky="ns")
        ttk.Button(cmd_f, text="Send", width=6,
                   command=self._send_input_to_shell).grid(
            row=0, column=2, sticky="ns", padx=(4, 0))
        _bind_scroll(self._cmd_input)
        _bind_text_shortcuts(self._cmd_input)
        self._cmd_input.bind("<Control-Return>",
                             lambda e: (self._add_to_queue(), "break"))
        self._cmd_input.bind("<Return>",
                             lambda e: (self._send_input_to_shell(), "break"))
        self._cmd_input.bind("<KP_Enter>",
                             lambda e: (self._send_input_to_shell(), "break"))

    def _build_task_subtask_row(self, parent):
        # Task
        ttk.Label(parent, text="Task:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._task_var = tk.StringVar()
        self._task_cb  = ttk.Combobox(parent, textvariable=self._task_var,
                                       values=self._task_options,
                                       state="readonly", width=14)
        self._task_cb.grid(row=0, column=1, padx=(0, 2))
        self._task_cb.bind("<<ComboboxSelected>>", self._on_task_changed)
        self._task_new_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self._task_new_var, width=10).grid(
            row=0, column=2, padx=2)
        ttk.Button(parent, text="+", width=3,
                   command=self._add_task).grid(row=0, column=3, padx=(0, 12))

        # Subtask
        ttk.Label(parent, text="Subtask:").grid(row=0, column=4, sticky="w", padx=(0, 4))
        self._subtask_var = tk.StringVar()
        self._subtask_cb  = ttk.Combobox(parent, textvariable=self._subtask_var,
                                          values=[], state="readonly", width=14)
        self._subtask_cb.grid(row=0, column=5, padx=(0, 2))
        self._subtask_new_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self._subtask_new_var, width=10).grid(
            row=0, column=6, padx=2)
        ttk.Button(parent, text="+", width=3,
                   command=self._add_subtask).grid(row=0, column=7, padx=(0, 4))

    def _build_action_bar(self, parent):
        ttk.Button(parent, text="\u25c4 Last Cmd", width=11,
                   command=self._nav_last).pack(side="left", padx=2)
        ttk.Button(parent, text="Next Cmd \u25ba", width=11,
                   command=self._nav_next).pack(side="left", padx=2)

        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(parent, text="Save Session Log",  width=16,
                   command=self._save_session_log).pack(side="left", padx=2)
        ttk.Button(parent, text="Save Command As\u2026", width=17,
                   command=self._save_command_as).pack(side="left", padx=2)
        ttk.Button(parent, text="Copy Last Response", width=20,
                   command=self._copy_last_response).pack(side="left", padx=2)

    # ─────────────────────────────────────────────────────────────────────────
    # Token Monitor panel
    # ─────────────────────────────────────────────────────────────────────────

    def _build_token_monitor(self, parent):
        lf = ttk.LabelFrame(parent, text="Token Monitor", padding=(6, 2))
        lf.pack(side="left", fill="x", expand=True)

        small = ("", 9)
        fields = [
            ("Current task:",  self._tm_current_var),
            ("Last task:",     self._tm_last_var),
            ("Session total:", self._tm_session_var),
            ("Source:",        self._tm_source_var),
        ]
        for col, (label, var) in enumerate(fields):
            ttk.Label(lf, text=label, font=small).grid(
                row=0, column=col * 2, sticky="e", padx=(6, 2))
            ttk.Label(lf, textvariable=var, font=small, width=10, anchor="w").grid(
                row=0, column=col * 2 + 1, sticky="w")

        ttk.Button(lf, text="Reset Session", width=13,
                   command=self._tm_reset_session).grid(
            row=0, column=len(fields) * 2, padx=(10, 4))

    _TM_BLOCK_RE = re.compile(
        r"<<<chilaude_usage_v1>>>\s*"
        r"current_task_tokens=(\d+)\s*"
        r"last_task_tokens=(\d+)\s*"
        r"session_total_tokens=(\d+)\s*"
        r"<<<end_chilaude_usage_v1>>>",
        re.DOTALL,
    )

    def _tm_parse_block(self, text):
        """Return (current, last, session) ints if usage block present, else None."""
        m = self._TM_BLOCK_RE.search(text)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
        return None

    def _tm_update(self, text):
        """Update Token Monitor from new terminal output chunk."""
        parsed = self._tm_parse_block(text)
        if parsed:
            cur, last, sess = parsed
            self._tm_session_total = sess
            self._tm_current_var.set(f"{cur:,}")
            self._tm_last_var.set(f"{last:,}")
            self._tm_session_var.set(f"{sess:,}")
            self._tm_source_var.set("parsed")
        else:
            est = max(1, round(len(text) / 4))
            self._tm_session_total += est
            self._tm_current_var.set(f"~{est:,}")
            self._tm_last_var.set(self._tm_current_var.get())
            self._tm_session_var.set(f"~{self._tm_session_total:,}")
            self._tm_source_var.set("estimated")

    def _tm_reset_session(self):
        self._tm_session_total = 0
        self._tm_session_var.set("0")
        self._set_status("Token Monitor: session total reset.")

    # ─────────────────────────────────────────────────────────────────────────
    # Saved Commands panel (Session tab left panel)
    # ─────────────────────────────────────────────────────────────────────────

    def _sc_refresh_list(self):
        """Scan linuxcommands_dir and repopulate the saved-command listbox."""
        self._sc_map.clear()
        try:
            self._sc_lb.delete(0, "end")
        except Exception:
            return
        if not self.linuxcommands_dir or not os.path.isdir(self.linuxcommands_dir):
            return
        self._sc_scan_dir(self.linuxcommands_dir, "")
        for label in self._sc_map:
            self._sc_lb.insert("end", label)

    def _sc_scan_dir(self, base, prefix):
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for name in entries:
            full = os.path.join(base, name)
            if os.path.isdir(full):
                sub = os.path.join(prefix, name) if prefix else name
                self._sc_scan_dir(full, sub)
            elif name.endswith(".json"):
                stem  = os.path.splitext(name)[0]
                label = None
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    title = (data.get("title") or "").strip()
                    if title:
                        label = f"{prefix}/{title}" if prefix else title
                except Exception:
                    pass
                if not label:
                    label = f"{prefix}/{stem}" if prefix else stem
                # Deduplicate
                if label in self._sc_map:
                    label = f"{label} ({stem})"
                self._sc_map[label] = full

    def _sc_add_command(self):
        if not self.linuxcommands_dir:
            self._set_status("Root not set — cannot insert saved command.")
            return
        sel = self._sc_lb.curselection()
        if not sel:
            self._set_status("No saved command selected.")
            return
        label = self._sc_lb.get(sel[0])
        path = self._sc_map.get(label)
        if not path:
            self._set_status("No saved command selected.")
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            self._set_status(f"Could not read saved command: {exc}")
            return
        cmd = (data.get("command") or "").strip()
        if not cmd:
            self._set_status(f"Saved command has no 'command' field: {os.path.basename(path)}")
            return
        self._cmd_input.delete("1.0", "end")
        self._cmd_input.insert("1.0", cmd)
        self._set_status(f"Inserted command: {label}")

    # ─────────────────────────────────────────────────────────────────────────
    # Queue management
    # ─────────────────────────────────────────────────────────────────────────

    def _session_ready(self):
        """Return True if task+subtask are set; warn and return False otherwise."""
        if not self._task_var.get():
            messagebox.showwarning("Session",
                "Select a task before adding or running commands.")
            return False
        if not self._subtask_var.get():
            messagebox.showwarning("Session",
                "Select a subtask before adding or running commands.")
            return False
        return True

    def _add_to_queue(self):
        if not self._session_ready():
            return
        raw = self._cmd_input.get("1.0", "end-1c")
        cmd = raw.strip().replace("\r\n", "\n").replace("\r", "\n")
        if not cmd:
            self._set_status("Command input is empty."); return
        entry = {"cmd": cmd, "status": "pending",
                 "exit": None, "output": None, "ts": None}
        self._session_queue.append(entry)
        self._queue_lb_sync(len(self._session_queue) - 1)
        self._cmd_input.delete("1.0", "end")
        self._set_status(f"Added to queue ({len(self._session_queue)} total).")

    def _queue_lb_sync(self, idx):
        """Insert or replace the listbox row at *idx* from _session_queue."""
        entry       = self._session_queue[idx]
        icons       = {"pending": "\u2022", "running": "\u25b6",
                       "sent":    "\u2192",
                       "done":    "\u2713", "error":   "\u2717"}
        colours     = {"pending": "#333333", "running": "#1565c0",
                       "sent":    "#1565c0",
                       "done":    "#2e7d32", "error":   "#c62828"}
        icon        = icons.get(entry["status"], "?")
        preview     = entry["cmd"].replace("\n", " ")[:52]
        label       = f"[{icon}] {preview}"
        if idx < self._queue_lb.size():
            self._queue_lb.delete(idx)
            self._queue_lb.insert(idx, label)
        else:
            self._queue_lb.insert("end", label)
        self._queue_lb.itemconfigure(
            idx, foreground=colours.get(entry["status"], "#333"))

    def _queue_remove(self):
        sel = self._queue_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._session_queue):
            return
        if self._session_queue[idx]["status"] == "running":
            self._set_status("Cannot remove a running command."); return
        self._session_queue.pop(idx)
        self._queue_lb.delete(idx)
        self._set_status("Entry removed.")

    def _queue_clear_pending(self):
        if self._running:
            self._set_status("Cannot clear while a command is running."); return
        indices = [i for i, e in enumerate(self._session_queue)
                   if e["status"] == "pending"]
        if not indices:
            self._set_status("No pending entries."); return
        for i in reversed(indices):
            self._session_queue.pop(i)
            self._queue_lb.delete(i)
        self._set_status("Pending entries cleared.")

    # ─────────────────────────────────────────────────────────────────────────
    # Command execution
    # ─────────────────────────────────────────────────────────────────────────

    def _run_selected(self):
        if not self._session_ready(): return
        sel = self._queue_lb.curselection()
        if not sel:
            self._set_status("Select a queue entry to run."); return
        idx = sel[0]
        if idx >= len(self._session_queue): return
        entry = self._session_queue[idx]
        if entry["status"] != "pending":
            self._set_status(f"Entry is already {entry['status']}."); return
        if self._running:
            self._set_status("A command is already running."); return
        self._execute_command(idx)

    def _run_next(self):
        if not self._session_ready(): return
        if self._running:
            self._set_status("A command is already running."); return
        for idx, entry in enumerate(self._session_queue):
            if entry["status"] == "pending":
                self._execute_command(idx); return
        self._set_status("No pending commands in queue.")

    def _execute_command(self, queue_idx):
        """
        v1 live-terminal execution path.

        Sends the queued command into the live PTY shell instead of
        spawning a one-shot subprocess. Output streams back via the reader
        thread; exit code is not synchronously known (no OSC 133 yet) so
        the entry is marked 'sent' once the bytes are written.
        """
        entry = self._session_queue[queue_idx]
        cmd   = entry["cmd"]
        ts    = _now_iso()

        # Auto-start the shell if needed so Run buttons "just work".
        if not self._pty_alive:
            self._start_shell()
        if not self._pty_alive:
            entry["status"] = "error"
            entry["exit"]   = -1
            entry["output"] = "(shell not running)"
            entry["ts"]     = ts
            self._queue_lb_sync(queue_idx)
            self._sv_append(f"## CMD [{ts}]\n\n", tag="cmd_header")
            self._sv_append(f"```sh\n{cmd}\n```\n\n", tag="cmd_text")
            self._sv_append(f"**Exit:** \u2014 \u2014 shell not running\n\n---\n\n")
            self._set_status("Cannot run \u2014 shell not started.")
            self._rebuild_nav_index()
            return

        entry["status"] = "sent"
        entry["ts"]     = ts
        self._queue_lb_sync(queue_idx)

        # Header into session view; live output from the PTY reader will
        # follow naturally after this header as the shell echoes and runs.
        # Leading newline ensures the header starts at column 0 even if the
        # last visible line is a shell prompt without a trailing newline.
        self._sv_append(f"\n## CMD [{ts}]\n\n", tag="cmd_header")
        self._sv_append(f"```sh\n{cmd}\n```\n\n", tag="cmd_text")
        self._rebuild_nav_index()

        ok = self._send_to_shell(cmd + "\n")
        if ok:
            self._set_status(f"Sent to shell: {cmd[:60]}")
        else:
            self._set_status("Send failed (shell may have exited).")

    # ─────────────────────────────────────────────────────────────────────
    # Live PTY shell — lifecycle, I/O, signals
    # ─────────────────────────────────────────────────────────────────────

    def _start_shell(self):
        """Spawn a long-lived interactive shell under a PTY."""
        if self._pty_alive:
            self._set_status("Shell already running.")
            return
        if not _PTY_AVAILABLE:
            messagebox.showerror("Live terminal",
                "PTY support is unavailable on this platform.\n"
                "This page requires POSIX pty (Linux/macOS).")
            self._set_status("PTY unavailable on this platform.")
            return

        try:
            master_fd, slave_fd = pty.openpty()
            # Make master non-blocking for clean reader-thread loop.
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            # Set a sensible window size so TUI-aware programs render.
            try:
                ws = struct.pack("HHHH", 40, 120, 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ws)
            except Exception:
                pass

            env = dict(os.environ)
            env["TERM"] = env.get("TERM", "dumb")  # we strip ANSI; dumb avoids
                                                    # programs sending heavy escapes
            env["PS1"] = "$ "                       # predictable prompt
            env.pop("PROMPT_COMMAND", None)

            shell_argv = [self._pty_shell_path, "--noediting", "-i"] \
                if "bash" in os.path.basename(self._pty_shell_path) \
                else [self._pty_shell_path, "-i"]

            shell_cwd = _find_project_root()
            proc = subprocess.Popen(
                shell_argv,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
                env=env,
                cwd=shell_cwd,
            )
            os.close(slave_fd)

            self._pty_master_fd  = master_fd
            self._pty_proc       = proc
            self._pty_alive      = True
            self._pty_started_at = _now_iso()

            self._pty_reader_thr = threading.Thread(
                target=self._shell_reader_loop, daemon=True,
                name="chilos-pty-reader")
            self._pty_reader_thr.start()
            # Start UI-side drain pump (runs in main thread).
            self._pty_schedule_pump()

            try:
                self._btn_start_shell.configure(state="disabled")
                self._btn_stop_shell.configure(state="normal")
                self._btn_ctrl_c.configure(state="normal")
                self._shell_status_var.set(f"shell: running (pid {proc.pid})")
                self._cwd_var.set(shell_cwd)
            except Exception:
                pass
            self._sv_append(
                f"\n--- shell started [{self._pty_started_at}] "
                f"({self._pty_shell_path} pid={proc.pid}) ---\n\n",
                tag="shell_marker")
            self._set_status(f"Shell started (pid {proc.pid}).")
        except Exception as exc:
            self._pty_alive = False
            self._set_status(f"Failed to start shell: {exc}")

    def _stop_shell(self):
        """Gracefully terminate the live shell."""
        if not self._pty_alive:
            self._set_status("Shell is not running.")
            return
        proc = self._pty_proc
        try:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
        finally:
            self._pty_finalize()

    def _pty_finalize(self):
        """Close fds, mark stopped, restore button state. Idempotent."""
        try:
            if self._pty_master_fd is not None:
                os.close(self._pty_master_fd)
        except Exception:
            pass
        self._pty_master_fd = None
        self._pty_proc      = None
        self._pty_alive     = False
        try:
            self._btn_start_shell.configure(state="normal")
            self._btn_stop_shell.configure(state="disabled")
            self._btn_ctrl_c.configure(state="disabled")
            self._shell_status_var.set("shell: stopped")
        except Exception:
            pass
        self._sv_append(f"\n--- shell stopped [{_now_iso()}] ---\n\n",
                        tag="shell_marker")

    def _shell_reader_loop(self):
        """
        Background thread: read bytes from PTY master and enqueue chunks.
        The Tk main thread drains the queue via _pty_pump.
        Never touches Tk directly (not thread-safe).
        """
        fd = self._pty_master_fd
        while self._pty_alive and fd is not None:
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not r:
                # Has the child exited?
                if self._pty_proc is None or self._pty_proc.poll() is not None:
                    self._pty_out_queue.put(("__EOF__", None))
                    return
                continue
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            except OSError:
                self._pty_out_queue.put(("__EOF__", None))
                return
            if not chunk:
                self._pty_out_queue.put(("__EOF__", None))
                return
            try:
                text = chunk.decode("utf-8", errors="replace")
            except Exception:
                text = repr(chunk)
            self._pty_out_queue.put(("data", _strip_ansi(text)))

    def _pty_schedule_pump(self):
        """Schedule the UI-side queue drain pump (Tk main thread)."""
        try:
            self._pty_pump_after = self.frame.after(60, self._pty_pump)
        except Exception:
            self._pty_pump_after = None

    def _pty_pump(self):
        """Drain the output queue and append to the session view. Tk thread."""
        try:
            drained = []
            eof = False
            while True:
                try:
                    kind, payload = self._pty_out_queue.get_nowait()
                except _queue.Empty:
                    break
                if kind == "__EOF__":
                    eof = True
                    break
                drained.append(payload)
            if drained:
                joined = "".join(drained)
                self._last_terminal_response = joined
                self._sv_append(joined, tag="terminal_output")
                self._tm_update(joined)
            if eof:
                self._pty_finalize()
                return
        except Exception:
            # Never let the pump die silently; log and reschedule.
            pass
        if self._pty_alive:
            self._pty_pump_after = self.frame.after(60, self._pty_pump)
        else:
            self._pty_pump_after = None

    def _send_to_shell(self, data: str) -> bool:
        """Write data to the PTY master. Returns True on success."""
        if not self._pty_alive or self._pty_master_fd is None:
            return False
        try:
            os.write(self._pty_master_fd, data.encode("utf-8", errors="replace"))
            return True
        except OSError:
            self._pty_out_queue.put(("__EOF__", None))
            return False

    def _send_signal_byte(self, b: bytes):
        """Send a control byte (e.g. b'\\x03' for Ctrl-C) to the shell."""
        if not self._pty_alive or self._pty_master_fd is None:
            self._set_status("Shell not running.")
            return
        try:
            os.write(self._pty_master_fd, b)
            self._set_status(f"Sent control byte {b!r}.")
        except OSError as exc:
            self._set_status(f"Send failed: {exc}")

    def _send_input_to_shell(self):
        """Plain-Enter handler on the command-input Text: send line to PTY."""
        if not self._pty_alive:
            # Auto-start so the user can just type and hit Enter.
            self._start_shell()
        if not self._pty_alive:
            return
        text = self._cmd_input.get("1.0", "end-1c")
        self._cmd_input.delete("1.0", "end")
        if not text:
            self._send_to_shell("\n")
            return
        # Record non-empty command in history and write a visible marker.
        cmd_clean = text.strip().replace("\r\n", "\n").replace("\r", "\n")
        if cmd_clean:
            self._input_history.append(cmd_clean)
            self._input_history_idx  = len(self._input_history) - 1
            self._selected_history_cmd = cmd_clean
            ts = _now_iso()
            self._sv_append(f"\n## CMD [{ts}]\n\n", tag="cmd_header")
            self._sv_append(f"```sh\n{cmd_clean}\n```\n\n", tag="cmd_text")
            self._rebuild_nav_index()
        # Ensure single trailing newline and send unchanged original text.
        if not text.endswith("\n"):
            text += "\n"
        self._send_to_shell(text)

    def _execute_command_legacy_subprocess(self, queue_idx):
        """
        Retained dead reference to the old subprocess.run path for audit.
        Not wired to any button. Do not call.
        """
        raise RuntimeError("legacy subprocess.run path is removed in v1 live terminal")

    def _post_result(self, queue_idx, ts, cmd, exit_code, output):
        entry              = self._session_queue[queue_idx]
        entry["status"]    = "done" if exit_code == 0 else "error"
        entry["exit"]      = exit_code
        entry["output"]    = output
        entry["ts"]        = ts
        self._queue_lb_sync(queue_idx)

        status_word = "success" if exit_code == 0 else "error"
        result_block = f"**Exit:** {exit_code} \u2014 {status_word}\n\n"
        if output:
            result_block += f"**Output:**\n```\n{output}\n```\n\n---\n\n"
        else:
            result_block += "**Output:** (none)\n\n---\n\n"
        self._sv_append(result_block)

        self._running = False
        self._set_run_controls("normal")
        self._set_status(f"Done \u2014 exit {exit_code} ({status_word})")
        self._rebuild_nav_index()

    def _set_run_controls(self, state):
        try: self._btn_run_s.configure(state=state)
        except Exception: pass
        try: self._btn_run_n.configure(state=state)
        except Exception: pass

    # ─────────────────────────────────────────────────────────────────────────
    # Session view helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _sv_append(self, text, tag=None):
        self._session_view.configure(state="normal")
        if tag:
            start = self._session_view.index("end")
            self._session_view.insert("end", text)
            self._session_view.tag_add(tag, start, "end")
        else:
            self._session_view.insert("end", text)
        self._session_view.configure(state="disabled")
        self._session_view.see("end")

    def _rebuild_nav_index(self):
        """Scan session view for ## CMD [ lines and rebuild navigation index."""
        self._cmd_nav_lines = []
        content = self._session_view.get("1.0", "end")
        for lineno, line in enumerate(content.split("\n"), start=1):
            if line.startswith("## CMD ["):
                self._cmd_nav_lines.append(lineno)
        # Auto-select the most recently added block
        self._cmd_nav_current_idx = len(self._cmd_nav_lines) - 1
        if self._cmd_nav_current_idx >= 0:
            self._scroll_to_nav(self._cmd_nav_current_idx, highlight=False)

    def _scroll_to_nav(self, idx, highlight=True):
        if idx < 0 or idx >= len(self._cmd_nav_lines):
            return
        dest = self._cmd_nav_lines[idx]
        self._session_view.configure(state="normal")
        self._session_view.tag_remove("cmd_selected", "1.0", "end")
        if highlight:
            self._session_view.tag_add(
                "cmd_selected", f"{dest}.0", f"{dest}.end")
        self._session_view.mark_set("insert", f"{dest}.0")
        self._session_view.see(f"{dest}.0")
        self._session_view.configure(state="disabled")

    def _nav_last(self):
        if not self._input_history:
            self._set_status("No command history yet."); return
        self._input_history_idx = max(0, self._input_history_idx - 1)
        cmd = self._input_history[self._input_history_idx]
        self._selected_history_cmd = cmd
        self._cmd_input.delete("1.0", "end")
        self._cmd_input.insert("1.0", cmd)
        total = len(self._input_history)
        self._set_status(f"Selected command {self._input_history_idx + 1} of {total}")

    def _nav_next(self):
        if not self._input_history:
            self._set_status("No command history yet."); return
        self._input_history_idx = min(
            len(self._input_history) - 1, self._input_history_idx + 1)
        cmd = self._input_history[self._input_history_idx]
        self._selected_history_cmd = cmd
        self._cmd_input.delete("1.0", "end")
        self._cmd_input.insert("1.0", cmd)
        total = len(self._input_history)
        self._set_status(f"Selected command {self._input_history_idx + 1} of {total}")

    # ─────────────────────────────────────────────────────────────────────────
    # Save Session Log
    # ─────────────────────────────────────────────────────────────────────────

    def _save_session_log(self):
        if not self.terminalhistory_dir:
            messagebox.showwarning("Save Log", "Root not set."); return
        task    = self._task_var.get().strip()
        subtask = self._subtask_var.get().strip()
        if not task:
            messagebox.showwarning("Save Log",
                "Select a task and subtask before saving."); return

        # Source: the live session view itself. Under v1 live-terminal, the
        # session view holds both the ## CMD [...] headers and the streamed
        # PTY output. We split into CMD blocks and chunk by 50.
        full_text = self._session_view.get("1.0", "end-1c")
        blocks = self._split_session_into_cmd_blocks(full_text)

        # Also include queue-sent entries that produced no on-screen header
        # yet (rare; defensive). Anything counted in queue with status in
        # {sent, done, error} that isn't in blocks is appended as a stub.
        queue_eligible = [e for e in self._session_queue
                          if e.get("status") in ("sent", "done", "error")]

        if not blocks and not queue_eligible:
            messagebox.showinfo("Save Log", "Nothing to save \u2014 no commands run yet.");
            return

        # If there are no parsed blocks but there are queue entries, synth blocks.
        if not blocks and queue_eligible:
            blocks = []
            for e in queue_eligible:
                ts  = e.get("ts") or _now_iso()
                cmd = e.get("cmd", "")
                blocks.append(f"## CMD [{ts}]\n\n```sh\n{cmd}\n```\n\n"
                              f"**Outcome:** {e.get('status')}\n\n---\n")

        CHUNK      = 50
        chunks     = [blocks[i:i+CHUNK] for i in range(0, len(blocks), CHUNK)]
        start_num  = _next_log_number(self.terminalhistory_dir)
        saved      = []
        started    = self._pty_started_at or _now_iso()

        for ci, chunk in enumerate(chunks):
            log_num  = start_num + ci
            filename = _make_log_filename(log_num)
            filepath = os.path.join(self.terminalhistory_dir, filename)

            header_lines = [
                "---",
                "schema_version: 1",
                "chipack: chi_ain",
                "page: chilaude_terminal",
                f"task: {_yaml_scalar(task)}",
                f"subtask: {_yaml_scalar(subtask)}",
                f"saved_at: {_now_iso()}",
                f"shell_started_at: {_yaml_scalar(started)}",
                f"shell_path: {_yaml_scalar(self._pty_shell_path)}",
                f"chunk_index: {ci + 1}",
                f"chunk_count: {len(chunks)}",
                f"entry_count: {len(chunk)}",
                f"live_mode: {'true' if self._pty_alive or self._pty_started_at else 'false'}",
                "redacted: false",
                "---",
                "",
            ]
            body = "\n".join(chunk).rstrip() + "\n"
            try:
                with open(filepath, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(header_lines))
                    fh.write(body)
                saved.append(filename)
            except OSError as exc:
                messagebox.showerror("Save Error", str(exc)); return

        summary = f"Saved {len(saved)} file(s):\n" + "\n".join(saved)
        messagebox.showinfo("Session Log Saved", summary)
        self._set_status("Log saved: " + ", ".join(saved))

    def _split_session_into_cmd_blocks(self, text):
        """
        Split the live session view into a list of strings, one per
        ## CMD [ts] block. Each block runs from its header to (but not
        including) the next ## CMD header, or to end-of-text.
        Returns [] if no headers found.
        """
        if not text or "## CMD [" not in text:
            return []
        out = []
        current = []
        for line in text.splitlines(keepends=False):
            if line.startswith("## CMD ["):
                if current:
                    out.append("\n".join(current).rstrip() + "\n")
                current = [line]
            elif current:
                current.append(line)
        if current:
            out.append("\n".join(current).rstrip() + "\n")
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # Save Command As
    # ─────────────────────────────────────────────────────────────────────────

    def _get_selected_cmd_text(self):
        """
        Extract command text from the ## CMD block nearest the current nav index.
        Returns the contents of the ```sh fence, or None if not found.
        """
        if not self._cmd_nav_lines or self._cmd_nav_current_idx < 0:
            return None

        content = self._session_view.get("1.0", "end")
        lines   = content.split("\n")

        # The selected block starts at this 1-based line number
        block_start_1 = self._cmd_nav_lines[self._cmd_nav_current_idx]
        block_start   = block_start_1 - 1   # 0-based index

        # Find the end of this block: next ## CMD [ or end of content
        if self._cmd_nav_current_idx + 1 < len(self._cmd_nav_lines):
            block_end = self._cmd_nav_lines[self._cmd_nav_current_idx + 1] - 1
        else:
            block_end = len(lines)

        block_lines = lines[block_start:block_end]

        # Find ```sh fence within the block
        fence_start = fence_end = None
        for i, ln in enumerate(block_lines):
            if ln.strip() == "```sh" and fence_start is None:
                fence_start = i + 1
            elif fence_start is not None and ln.strip() == "```":
                fence_end = i
                break

        if fence_start is None or fence_end is None:
            return None

        cmd_text = "\n".join(block_lines[fence_start:fence_end])
        return cmd_text.strip().replace("\r\n", "\n").replace("\r", "\n")

    def _save_command_as(self):
        if not self.linuxcommands_dir:
            messagebox.showwarning("Save Command As", "Root not set."); return
        cmd_text = self._selected_history_cmd or self._get_selected_cmd_text()
        if not cmd_text:
            messagebox.showinfo("Save Command As",
                "Send a command first, or use \u25c4 Last Cmd / Next Cmd \u25ba "
                "to select one,\nthen click Save Command As.")
            return

        def _on_saved(save_path):
            self._ce_refresh_file_list()
            self._notebook.select(1)          # switch to Command Editor tab
            self._ce_open_file(save_path)
            self._set_status(f"Saved command: {os.path.basename(save_path)}")

        SaveCommandPopup(
            self.frame.winfo_toplevel(),
            self.linuxcommands_dir,
            cmd_text,
            _on_saved,
        )

    def _copy_last_response(self):
        if not self._last_terminal_response:
            self._set_status("No terminal response captured yet.")
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(self._last_terminal_response)
        self._set_status("Last response copied to clipboard.")

    # ─────────────────────────────────────────────────────────────────────────
    # COMMAND EDITOR TAB — build
    # ─────────────────────────────────────────────────────────────────────────

    def _build_command_editor_tab(self, parent):
        parent.columnconfigure(0, weight=0)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # ── Left: file list ───────────────────────────────────────────────────
        left = ttk.LabelFrame(parent, text="Saved Commands", padding=4)
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 2), pady=4)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        btn_row = ttk.Frame(left)
        btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(btn_row, text="New",     width=7,
                   command=self._ce_new).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Delete",  width=7,
                   command=self._ce_delete).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Refresh", width=7,
                   command=self._ce_refresh_file_list).pack(side="left", padx=2)

        lb_frm, self._ce_lb = _make_listbox(left, height=14)
        lb_frm.grid(row=1, column=0, sticky="nsew")
        self._ce_lb.bind("<<ListboxSelect>>", self._ce_on_select)

        # ── Right: PanedWindow — editor (top) + machine view (bottom) ─────────
        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.grid(row=0, column=1, sticky="nsew", padx=(2, 4), pady=4)

        # ── Top pane: editor fields in a scrollable canvas ────────────────────
        editor_frame = ttk.Frame(pane)
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(editor_frame, highlightthickness=0, borderwidth=0)
        vsb    = ttk.Scrollbar(editor_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")
        _bind_scroll(canvas)

        inner = ttk.Frame(canvas, padding=6)
        self._ce_canvas     = canvas
        self._ce_canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(1, weight=1)

        def _on_inner_cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_cfg(e):
            canvas.itemconfigure(self._ce_canvas_win, width=e.width)
        inner.bind("<Configure>", _on_inner_cfg)
        canvas.bind("<Configure>", _on_canvas_cfg)

        # Propagate scroll from non-Text children up to the canvas
        def _rebind(widget):
            for child in widget.winfo_children():
                if not isinstance(child, tk.Text):
                    child.bind("<MouseWheel>",
                               lambda e: canvas.yview_scroll(
                                   int(-1*(e.delta/120)), "units") or "break",
                               add=False)
                    child.bind("<Button-4>",
                               lambda e: canvas.yview_scroll(-1, "units") or "break",
                               add=False)
                    child.bind("<Button-5>",
                               lambda e: canvas.yview_scroll(1, "units") or "break",
                               add=False)
                _rebind(child)
        inner.bind("<Map>", lambda e: _rebind(inner))

        def _dirty(*_): self._ce_mv_dirty = True

        row = 0

        # Info line
        self._ce_info_var = tk.StringVar(value="New command (unsaved)")
        ttk.Label(inner, textvariable=self._ce_info_var,
                  foreground="#888", font=("", 8)).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1

        # Title
        ttk.Label(inner, text="Title:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        self._ce_title_var = tk.StringVar()
        self._ce_title_var.trace_add("write", _dirty)
        ce_title = ttk.Entry(inner, textvariable=self._ce_title_var, font=("", 11))
        ce_title.grid(row=row, column=1, sticky="ew", pady=2)
        ce_title.bind("<FocusIn>", lambda e: self._ce_set_active("title"))
        row += 1

        # Command
        ttk.Label(inner, text="Command:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky="nw", padx=(0, 6), pady=2)
        mono = ("Consolas", 10) if os.name == "nt" else ("monospace", 10)
        self._ce_command = tk.Text(inner, height=8, wrap="word", undo=True, font=mono)
        self._ce_command.grid(row=row, column=1, sticky="nsew", pady=2)
        inner.rowconfigure(row, weight=2)
        _bind_scroll(self._ce_command)
        _bind_text_shortcuts(self._ce_command)
        self._ce_command.bind("<FocusIn>", lambda e: self._ce_set_active("command"))
        self._ce_command.bind("<KeyRelease>", _dirty)
        row += 1

        # Notes
        ttk.Label(inner, text="Notes:").grid(
            row=row, column=0, sticky="nw", padx=(0, 6), pady=2)
        self._ce_notes = tk.Text(inner, height=4, wrap="word", undo=True, font=("", 10))
        self._ce_notes.grid(row=row, column=1, sticky="nsew", pady=2)
        inner.rowconfigure(row, weight=1)
        _bind_scroll(self._ce_notes)
        _bind_text_shortcuts(self._ce_notes)
        self._ce_notes.bind("<FocusIn>", lambda e: self._ce_set_active("notes"))
        self._ce_notes.bind("<KeyRelease>", _dirty)
        row += 1

        # Save buttons
        save_row = ttk.Frame(inner)
        save_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        ttk.Button(save_row, text="Save",      width=10,
                   command=self._ce_save).pack(side="left", padx=2)
        ttk.Button(save_row, text="Save As\u2026", width=10,
                   command=self._ce_save_as).pack(side="left", padx=2)

        pane.add(editor_frame, weight=3)

        # ── Bottom pane: machine view ─────────────────────────────────────────
        mv_frame, self._ce_mv_text = _make_machine_view(pane)
        pane.add(mv_frame, weight=2)

    # ─────────────────────────────────────────────────────────────────────────
    # Command editor — active field + machine view
    # ─────────────────────────────────────────────────────────────────────────

    def _ce_set_active(self, field):
        if self._ce_active_field != field:
            self._ce_active_field = field
            self._ce_mv_dirty     = True

    def _ce_schedule_mv_refresh(self):
        if self._ce_mv_dirty:
            self._ce_mv_dirty = False
            self._ce_refresh_machine_view()
        try:
            self._ce_mv_after_id = self.frame.after(
                self._MV_INTERVAL, self._ce_schedule_mv_refresh)
        except Exception:
            pass

    def _ce_refresh_machine_view(self):
        self._ce_sync_from_fields()
        try:
            _render_json_highlighted(
                self._ce_mv_text, self._ce_record, self._ce_active_field)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Command editor — file list
    # ─────────────────────────────────────────────────────────────────────────

    def _ce_refresh_file_list(self):
        try:
            self._ce_lb.delete(0, "end")
        except Exception:
            return
        self._ce_files.clear()
        if not self.linuxcommands_dir or not os.path.isdir(self.linuxcommands_dir):
            return
        self._ce_scan_dir(self.linuxcommands_dir, "")

    def _ce_scan_dir(self, base, prefix):
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for name in entries:
            full = os.path.join(base, name)
            if os.path.isdir(full):
                sub = os.path.join(prefix, name) if prefix else name
                self._ce_scan_dir(full, sub)
            elif name.endswith(".json"):
                display = os.path.join(prefix, name) if prefix else name
                self._ce_lb.insert("end", display)
                self._ce_files.append(full)

    def _ce_on_select(self, event=None):
        sel = self._ce_lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx < len(self._ce_files):
            self._ce_load_file(self._ce_files[idx])

    def _ce_open_file(self, path):
        """Open *path* in the editor and highlight it in the file list."""
        self._ce_refresh_file_list()
        if path in self._ce_files:
            idx = self._ce_files.index(path)
            self._ce_lb.selection_clear(0, "end")
            self._ce_lb.selection_set(idx)
            self._ce_lb.see(idx)
        self._ce_load_file(path)

    def _ce_load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not load:\n{path}\n\n{exc}")
            return
        self._ce_json_path = path
        self._ce_record    = _empty_command()
        self._ce_record.update(data)    # preserve all disk keys, fill missing with defaults
        self._ce_populate_fields()
        fname = os.path.basename(path)
        self._ce_info_var.set(f"Loaded: {fname}  |  {path}")
        self._set_status(f"Loaded: {fname}")

    def _ce_populate_fields(self):
        self._ce_title_var.set(self._ce_record.get("title", ""))
        self._ce_command.delete("1.0", "end")
        self._ce_command.insert("1.0", self._ce_record.get("command", ""))
        self._ce_notes.delete("1.0", "end")
        self._ce_notes.insert("1.0", self._ce_record.get("notes", ""))
        self._ce_mv_dirty = True

    def _ce_sync_from_fields(self):
        self._ce_record["title"] = self._ce_title_var.get().strip()
        try:
            self._ce_record["command"] = self._ce_command.get("1.0", "end-1c")
        except Exception:
            pass
        try:
            self._ce_record["notes"] = self._ce_notes.get("1.0", "end-1c")
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Command editor — new / save / delete
    # ─────────────────────────────────────────────────────────────────────────

    def _ce_new(self):
        self._ce_record    = _empty_command()
        self._ce_json_path = ""
        self._ce_populate_fields()
        self._ce_info_var.set("New command (unsaved)")
        self._set_status("New command ready.")

    def _ce_save(self):
        self._ce_sync_from_fields()
        title = self._ce_record.get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Title is required."); return
        if not self.linuxcommands_dir:
            messagebox.showwarning("Save", "No root set."); return
        self._ce_record["updated_at"] = _now_iso()
        if not self._ce_json_path:
            stem               = _safe_filename(title)
            self._ce_json_path = _unique_path(self.linuxcommands_dir, stem)
        self._ce_write_json(self._ce_json_path)

    def _ce_save_as(self):
        self._ce_sync_from_fields()
        title = self._ce_record.get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Title is required."); return
        if not self.linuxcommands_dir:
            messagebox.showwarning("Save", "No root set."); return
        stem = _safe_filename(title)
        path = filedialog.asksaveasfilename(
            initialdir=self.linuxcommands_dir, initialfile=stem + ".json",
            title="Save Command As", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path: return
        self._ce_record["updated_at"] = _now_iso()
        self._ce_json_path = path
        self._ce_write_json(path)

    def _ce_write_json(self, path):
        try:
            dir_part = os.path.dirname(path)
            if dir_part:
                os.makedirs(dir_part, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._ce_record, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to write:\n{exc}"); return
        fname = os.path.basename(path)
        self._ce_info_var.set(f"Saved: {fname}  |  {path}")
        self._set_status(f"Saved: {fname}")
        self._ce_refresh_file_list()
        self._ce_mv_dirty = True

    def _ce_delete(self):
        sel = self._ce_lb.curselection()
        if not sel:
            messagebox.showinfo("Delete", "Select a command to delete."); return
        idx = sel[0]
        if idx >= len(self._ce_files): return
        path  = self._ce_files[idx]
        fname = os.path.basename(path)
        if not messagebox.askyesno("Delete",
                f"Delete {fname}?\nThis cannot be undone."):
            return
        try:
            os.remove(path)
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc)); return
        if path == self._ce_json_path:
            self._ce_new()
        self._ce_refresh_file_list()
        self._set_status(f"Deleted: {fname}")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level factory helpers (used by PageChilaudeTerminal)
# ─────────────────────────────────────────────────────────────────────────────

def _empty_command():
    now = _now_iso()
    return {
        "id":         str(uuid.uuid4()),
        "title":      "",
        "command":    "",
        "notes":      "",
        "created_at": now,
        "updated_at": now,
    }

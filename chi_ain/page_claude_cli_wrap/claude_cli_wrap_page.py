"""
page_claude_cli_wrap / claude_cli_wrap_page.py
────────────────────────────────────────────────────────────────────────────────
Claude CLI Wrap — launcher page for pagepack_chilaude_terminal.

Opens an external terminal emulator in a selected working directory and
optionally starts the Claude Code CLI inside it.

This is a launcher/wrapper only — it does not embed a terminal, capture
Claude Code session output, or use the Anthropic API or SDK.

Shell contract (Guichi loader):
    page = PageClaudeCliWrap(parent_frame)
    page.build(parent)
"""

import os
import shutil
import datetime
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_terminal_emulators():
    """Return ordered list of terminal emulator candidates present on PATH."""
    candidates = [
        "konsole",
        "gnome-terminal",
        "xfce4-terminal",
        "mate-terminal",
        "lxterminal",
        "xterm",
        "x-terminal-emulator",
    ]
    return [t for t in candidates if shutil.which(t)]


# ─────────────────────────────────────────────────────────────────────────────
# Page class
# ─────────────────────────────────────────────────────────────────────────────

class PageClaudeCliWrap:
    """
    Claude CLI Wrap launcher page for pagepack_chilaude_terminal.

    Shell contract (Guichi loader):
        page = PageClaudeCliWrap(parent_frame)
        page.build(parent)
    """

    PAGE_NAME = "claude_cli_wrap"

    def __init__(self, parent, app=None, page_key="", page_folder="",
                 *args, **kwargs):
        app         = kwargs.pop("controller",   app)
        page_key    = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder",  page_folder)

        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        self._dir_var    = tk.StringVar(value=os.path.expanduser("~"))
        self._claude_var = tk.StringVar(value="claude")

    # ─────────────────────────────────────────────────────────────────────────
    # Guichi entry points
    # ─────────────────────────────────────────────────────────────────────────

    def build(self, parent):
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)
        self.frame.pack(fill="both", expand=True)
        self._build_ui()

    # aliases accepted by some Guichi loader variants
    create_widgets = build
    mount          = build
    render         = build

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_config_panel()
        self._build_launch_panel()
        self._build_log_panel()

    def _build_config_panel(self):
        cf = ttk.LabelFrame(self.frame, text="Configuration", padding=(10, 6))
        cf.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        cf.columnconfigure(1, weight=1)

        # Working directory
        ttk.Label(cf, text="Working directory:", font=("", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(cf, textvariable=self._dir_var, font=("monospace", 10)).grid(
            row=0, column=1, sticky="ew", pady=3)
        ttk.Button(cf, text="Browse…", width=9,
                   command=self._browse_dir).grid(
            row=0, column=2, padx=(6, 0), pady=3)

        # Claude executable
        ttk.Label(cf, text="Claude executable:", font=("", 10, "bold")).grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(cf, textvariable=self._claude_var, font=("monospace", 10)).grid(
            row=1, column=1, sticky="ew", pady=3)
        ttk.Label(cf, text='(name or full path, e.g. "claude")',
                  font=("", 9), foreground="#666").grid(
            row=1, column=2, padx=(6, 0), pady=3, sticky="w")

    def _build_launch_panel(self):
        lf = ttk.LabelFrame(self.frame, text="Launch", padding=(10, 6))
        lf.grid(row=1, column=0, sticky="ew", padx=8, pady=4)

        ttk.Button(lf, text="Launch Claude CLI", width=22,
                   command=self._launch_claude).pack(side="left", padx=(0, 12))
        ttk.Button(lf, text="Launch Plain Terminal Here", width=26,
                   command=self._launch_plain_terminal).pack(side="left")

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(lf, textvariable=self._status_var,
                  font=("", 9), foreground="#444", anchor="w").pack(
            side="left", padx=(16, 0), fill="x", expand=True)

    def _build_log_panel(self):
        lf = ttk.LabelFrame(self.frame, text="Launch Log", padding=(6, 4))
        lf.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        self._log = tk.Text(lf, wrap="word", state="disabled",
                            font=("monospace", 9),
                            background="#fafaf8", relief="flat",
                            borderwidth=1, padx=6, pady=4)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        ttk.Button(lf, text="Clear Log", width=10,
                   command=self._clear_log).grid(
            row=1, column=0, sticky="w", pady=(4, 0))

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _browse_dir(self):
        chosen = filedialog.askdirectory(
            title="Select working directory",
            initialdir=self._dir_var.get() or os.path.expanduser("~"),
        )
        if chosen:
            self._dir_var.set(chosen)

    def _launch_claude(self):
        work_dir = self._dir_var.get().strip()
        claude   = self._claude_var.get().strip() or "claude"
        if not self._validate_dir(work_dir):
            return
        self._open_terminal(work_dir, claude_exe=claude)

    def _launch_plain_terminal(self):
        work_dir = self._dir_var.get().strip()
        if not self._validate_dir(work_dir):
            return
        self._open_terminal(work_dir, claude_exe=None)

    # ─────────────────────────────────────────────────────────────────────────
    # Terminal launch logic
    # ─────────────────────────────────────────────────────────────────────────

    def _open_terminal(self, work_dir, claude_exe=None):
        """
        Open an external terminal emulator.

        Launch order:
          1. konsole   — supports --workdir and -e natively
          2. gnome-terminal / xfce4-terminal / mate-terminal — support --working-directory
          3. x-terminal-emulator / xterm — fallback via bash -lc
        """
        label = f"Claude CLI ({claude_exe})" if claude_exe else "Plain terminal"
        available = _find_terminal_emulators()

        if not available:
            msg = "No terminal emulator found on PATH."
            self._log_line(f"ERROR  {label} — {msg}")
            self._status_var.set(msg)
            return

        term = available[0]

        try:
            cmd = self._build_launch_cmd(term, work_dir, claude_exe)
            subprocess.Popen(cmd)
            self._log_line(
                f"OK     {label}\n"
                f"       terminal: {term}\n"
                f"       dir: {work_dir}\n"
                f"       cmd: {' '.join(cmd)}"
            )
            self._status_var.set(f"Launched {term} in {os.path.basename(work_dir) or work_dir}")
        except Exception as exc:
            self._log_line(f"ERROR  {label} — {exc}")
            self._status_var.set(f"Launch failed: {exc}")

    def _build_launch_cmd(self, term, work_dir, claude_exe):
        """Return the Popen argument list for the chosen terminal."""
        # Quote directory for shell invocation where needed
        safe_dir = work_dir.replace('"', '\\"')

        if term == "konsole":
            if claude_exe:
                return ["konsole", "--workdir", work_dir, "-e", claude_exe]
            else:
                return ["konsole", "--workdir", work_dir]

        if term in ("gnome-terminal", "xfce4-terminal", "mate-terminal"):
            base = [term, f"--working-directory={work_dir}"]
            if claude_exe:
                base += ["--", claude_exe]
            return base

        if term == "lxterminal":
            if claude_exe:
                shell_cmd = f'cd "{safe_dir}" && {claude_exe}'
                return ["lxterminal", f"--working-directory={work_dir}",
                        "-e", f"bash -lc '{shell_cmd}'"]
            else:
                return ["lxterminal", f"--working-directory={work_dir}"]

        # Generic fallback: x-terminal-emulator, xterm, anything else
        if claude_exe:
            shell_cmd = f'cd "{safe_dir}" && {claude_exe}; exec bash'
        else:
            shell_cmd = f'cd "{safe_dir}"; exec bash'
        return [term, "-e", f"bash -lc '{shell_cmd}'"]

    # ─────────────────────────────────────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_dir(self, work_dir):
        if not work_dir:
            msg = "No working directory set."
            self._log_line(f"ERROR  {msg}")
            self._status_var.set(msg)
            return False
        if not os.path.isdir(work_dir):
            msg = f"Directory does not exist: {work_dir}"
            self._log_line(f"ERROR  {msg}")
            self._status_var.set(msg)
            return False
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Log helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _log_line(self, text):
        self._log.configure(state="normal")
        self._log.insert("end", f"[{_now_ts()}] {text}\n\n")
        self._log.configure(state="disabled")
        self._log.see("end")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._status_var.set("Log cleared.")

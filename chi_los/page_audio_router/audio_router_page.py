"""
page_audio_router / audio_router_page.py
────────────────────────────────────────────────────────────────────────────────
Manual Linux desktop audio router page for pagepack_chilos (Guichi v1).

Shell contract:
    page = PageAudioRouter(parent_widget)
    page.build(parent)          # also: create_widgets / mount / render

Backend:
    subprocess calls to `pactl` (primary) and optionally `wpctl` (diagnostic).
    No third-party Python dependencies. No daemons. No background polling.
    No work at import time beyond defining the class.

Layout:
    Top bar      — Refresh / Set Default / Move Stream / wpctl status
    Body         — left: Outputs (sinks) listbox
                   right: Active Streams (sink-inputs) listbox
    Status / log — bottom: read-only Text with timestamped messages
"""

import os
import re
import shutil
import datetime
import subprocess
import tkinter as tk
from tkinter import ttk


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers (kept small and readable)
# ─────────────────────────────────────────────────────────────────────────────

def _bind_scroll(widget):
    """Linux/Wayland-safe scroll binding for any scrollable widget."""
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


def _run(argv, timeout=5):
    """
    Run a command, capture stdout/stderr, never raise into the caller.
    Returns: {"rc": int, "stdout": str, "stderr": str, "error": str|None}
    """
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"rc": r.returncode, "stdout": r.stdout or "",
                "stderr": r.stderr or "", "error": None}
    except FileNotFoundError:
        return {"rc": -1, "stdout": "", "stderr": "",
                "error": f"command not found: {argv[0]}"}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "stdout": "", "stderr": "",
                "error": f"timeout after {timeout}s: {' '.join(argv)}"}
    except Exception as ex:
        return {"rc": -1, "stdout": "", "stderr": "",
                "error": f"{type(ex).__name__}: {ex}"}


def _parse_sinks_short(stdout):
    """
    Parse `pactl list short sinks` output. Columns are tab-separated:
        <id>\\t<name>\\t<driver>\\t<sample_spec>\\t<state>
    Returns list of {"id", "name", "state"} dicts; skips malformed lines.
    """
    out = []
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        out.append({
            "id":    parts[0].strip(),
            "name":  parts[1].strip(),
            "state": parts[4].strip() if len(parts) >= 5 else "",
        })
    return out


def _parse_sink_inputs(stdout):
    """
    Parse `pactl list sink-inputs` (verbose). Best-effort extraction of
    id, current sink index, and application.name for each entry.
    Returns list of {"id", "sink_id", "app_name"} dicts.
    """
    streams = []
    cur = None
    for raw in stdout.splitlines():
        line = raw.rstrip()
        m = re.match(r"^Sink Input #(\d+)", line)
        if m:
            if cur is not None:
                streams.append(cur)
            cur = {"id": m.group(1), "sink_id": "", "app_name": ""}
            continue
        if cur is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Sink:"):
            cur["sink_id"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("application.name") and "=" in stripped:
            val = stripped.split("=", 1)[1].strip()
            cur["app_name"] = val.strip('"')
    if cur is not None:
        streams.append(cur)
    return streams


# ─────────────────────────────────────────────────────────────────────────────
# Page class
# ─────────────────────────────────────────────────────────────────────────────

class PageAudioRouter:
    """
    Linux audio router page.

    Shell contract (Guichi loader):
        page = PageAudioRouter(parent_frame)
        page.build(parent)   # also: create_widgets / mount / render
    """

    PAGE_NAME = "audio_router"

    # ─────────────────────────────────────────────────────────────────────
    # Init
    # ─────────────────────────────────────────────────────────────────────

    def __init__(self, parent, app=None, page_key="", page_folder="",
                 *args, **kwargs):
        app         = kwargs.pop("controller",   app)
        page_key    = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder",  page_folder)

        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # Tool availability — resolved once; re-resolved in rebuild.
        self._have_pactl = bool(shutil.which("pactl"))
        self._have_wpctl = bool(shutil.which("wpctl"))

        # Snapshot state
        self._sinks = []          # [{id, name, state}]
        self._streams = []        # [{id, sink_id, app_name}]
        self._default_sink = ""   # sink name, empty if unknown

        # Widgets (created in _build_*)
        self._var_default = None
        self._var_status  = None
        self._lb_sinks    = None
        self._lb_streams  = None
        self._txt_log     = None
        self._btn_refresh = None
        self._btn_default = None
        self._btn_move    = None
        self._btn_wpctl   = None

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self._build_top_bar()
        self._build_body()
        self._build_status_bar()

        # First refresh on UI idle — NEVER at import time and NEVER in ctor
        # synchronously before the frame is visible.
        self.frame.after(100, self._initial_refresh)

    # ─────────────────────────────────────────────────────────────────────
    # Shell mount methods (exact contract — do not rename)
    # ─────────────────────────────────────────────────────────────────────

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        try:
            if container is not None and self.frame.master is not container:
                self.frame.destroy()
                self.parent = container
                self.frame  = ttk.Frame(container)
                self.frame.columnconfigure(0, weight=1)
                self.frame.rowconfigure(1, weight=1)
                self._build_top_bar()
                self._build_body()
                self._build_status_bar()
                self.frame.after(50, self._initial_refresh)
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

    def build(self,          parent=None): return self._embed_into_parent(parent)
    def create_widgets(self, parent=None): return self._embed_into_parent(parent)
    def mount(self,          parent=None): return self._embed_into_parent(parent)
    def render(self,         parent=None): return self._embed_into_parent(parent)

    # ─────────────────────────────────────────────────────────────────────
    # Top bar
    # ─────────────────────────────────────────────────────────────────────

    def _build_top_bar(self):
        bar = ttk.Frame(self.frame, padding=(4, 4))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(99, weight=1)

        self._btn_refresh = ttk.Button(bar, text="Refresh", width=10,
                                       command=self._on_refresh)
        self._btn_refresh.grid(row=0, column=0, padx=2)

        self._btn_default = ttk.Button(bar, text="Set Selected Sink as Default",
                                       command=self._on_set_default)
        self._btn_default.grid(row=0, column=1, padx=2)

        self._btn_move = ttk.Button(bar, text="Move Stream \u2192 Selected Sink",
                                    command=self._on_move_stream)
        self._btn_move.grid(row=0, column=2, padx=2)

        self._btn_wpctl = ttk.Button(bar, text="wpctl status",
                                     command=self._on_wpctl_status)
        self._btn_wpctl.grid(row=0, column=3, padx=2)

        self._var_default = tk.StringVar(value="Default sink: (unknown)")
        ttk.Label(bar, textvariable=self._var_default,
                  foreground="#555", anchor="e"
                  ).grid(row=0, column=99, sticky="ew", padx=8)

    # ─────────────────────────────────────────────────────────────────────
    # Body (two list panes, side by side)
    # ─────────────────────────────────────────────────────────────────────

    def _build_body(self):
        body = ttk.Frame(self.frame, padding=(4, 2))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=260)
        body.columnconfigure(1, weight=1, minsize=260)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        self._lb_sinks   = self._build_list_pane(body, 0, 0,
                                                 "Outputs (sinks)")
        self._lb_streams = self._build_list_pane(body, 0, 1,
                                                 "Active Streams (sink-inputs)")

        # Log pane spans both columns at the bottom
        log_outer = ttk.LabelFrame(body, text="Status / Log", padding=(4, 2))
        log_outer.grid(row=1, column=0, columnspan=2, sticky="nsew",
                       pady=(6, 0))
        log_outer.columnconfigure(0, weight=1)
        log_outer.rowconfigure(0, weight=1)

        mono = ("Consolas", 9) if os.name == "nt" else ("monospace", 9)
        self._txt_log = tk.Text(log_outer, wrap="word", height=8,
                                state="disabled", font=mono,
                                relief="solid", borderwidth=1)
        sb = ttk.Scrollbar(log_outer, orient="vertical",
                           command=self._txt_log.yview)
        self._txt_log.configure(yscrollcommand=sb.set)
        self._txt_log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._txt_log)

    def _build_list_pane(self, parent, row, col, title):
        outer = ttk.LabelFrame(parent, text=title, padding=(4, 2))
        outer.grid(row=row, column=col, sticky="nsew",
                   padx=(0, 4) if col == 0 else (4, 0))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        lb = tk.Listbox(outer, height=10, selectmode="single",
                        activestyle="dotbox", exportselection=False,
                        font=("monospace", 9))
        sb = ttk.Scrollbar(outer, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(lb)
        return lb

    # ─────────────────────────────────────────────────────────────────────
    # Status bar
    # ─────────────────────────────────────────────────────────────────────

    def _build_status_bar(self):
        self._var_status = tk.StringVar(value="Ready.")
        bar = ttk.Frame(self.frame, padding=(4, 2))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self._var_status, anchor="w",
                  foreground="#444", font=("", 9)
                  ).grid(row=0, column=0, sticky="ew")

    # ─────────────────────────────────────────────────────────────────────
    # Logging / status helpers
    # ─────────────────────────────────────────────────────────────────────

    def _now(self):
        return datetime.datetime.now().strftime("%H:%M:%S")

    def _set_status(self, msg):
        try:
            self._var_status.set(f"[{self._now()}] {msg}")
        except Exception:
            pass

    def _log(self, msg):
        if self._txt_log is None:
            return
        try:
            self._txt_log.configure(state="normal")
            self._txt_log.insert("end", f"[{self._now()}] {msg}\n")
            self._txt_log.see("end")
            self._txt_log.configure(state="disabled")
        except Exception:
            pass

    def _log_block(self, title, text):
        if not text:
            return
        self._log(f"{title}:")
        try:
            self._txt_log.configure(state="normal")
            for line in text.splitlines():
                self._txt_log.insert("end", f"  {line}\n")
            self._txt_log.see("end")
            self._txt_log.configure(state="disabled")
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Tool availability gate
    # ─────────────────────────────────────────────────────────────────────

    def _update_button_state(self):
        state_actions = "normal" if self._have_pactl else "disabled"
        state_wpctl   = "normal" if self._have_wpctl else "disabled"
        for b in (self._btn_refresh, self._btn_default, self._btn_move):
            if b is not None:
                b.configure(state=state_actions)
        if self._btn_wpctl is not None:
            self._btn_wpctl.configure(state=state_wpctl)

    def _initial_refresh(self):
        # Re-resolve tools in case PATH changed since ctor.
        self._have_pactl = bool(shutil.which("pactl"))
        self._have_wpctl = bool(shutil.which("wpctl"))
        self._update_button_state()

        if not self._have_pactl and not self._have_wpctl:
            msg = ("Neither 'pactl' nor 'wpctl' was found on PATH. "
                   "Install pulseaudio-utils (pactl) or wireplumber (wpctl) "
                   "to use this page.")
            self._set_status("Audio tools not available.")
            self._log(msg)
            return

        if not self._have_pactl:
            self._set_status("pactl not found — read-only mode (wpctl diagnostic only).")
            self._log("pactl not found on PATH. Set-default and move-stream are "
                      "disabled. Use 'wpctl status' for a diagnostic snapshot.")
            return

        self._do_refresh()

    # ─────────────────────────────────────────────────────────────────────
    # Refresh: query pactl for sinks / sink-inputs / default
    # ─────────────────────────────────────────────────────────────────────

    def _on_refresh(self):
        if not self._have_pactl:
            self._set_status("pactl not available.")
            return
        self._do_refresh()

    def _do_refresh(self):
        # 1. Sinks (short form for parsing)
        r_sinks = _run(["pactl", "list", "short", "sinks"])
        if r_sinks["error"] is not None or r_sinks["rc"] != 0:
            self._sinks = []
            err = (r_sinks["error"] or r_sinks["stderr"].strip()
                   or f"rc={r_sinks['rc']}")
            self._log(f"pactl list short sinks failed: {err}")
            self._log_block("raw stdout", r_sinks["stdout"])
        else:
            try:
                self._sinks = _parse_sinks_short(r_sinks["stdout"])
            except Exception as ex:
                self._sinks = []
                self._log(f"sink parser failed: {ex}")
                self._log_block("raw stdout", r_sinks["stdout"])

        # 2. Sink-inputs (verbose for app names)
        r_streams = _run(["pactl", "list", "sink-inputs"])
        if r_streams["error"] is not None or r_streams["rc"] != 0:
            self._streams = []
            self._log(f"pactl list sink-inputs failed: "
                      f"{r_streams['error'] or r_streams['stderr'].strip() or 'non-zero exit'}")
            self._log_block("raw stdout", r_streams["stdout"])
        else:
            try:
                self._streams = _parse_sink_inputs(r_streams["stdout"])
            except Exception as ex:
                self._streams = []
                self._log(f"sink-input parser failed: {ex}")
                self._log_block("raw stdout", r_streams["stdout"])

        # 3. Default sink (name)
        r_def = _run(["pactl", "get-default-sink"])
        if r_def["error"] is None and r_def["rc"] == 0:
            self._default_sink = r_def["stdout"].strip()
        else:
            self._default_sink = ""

        self._render_lists()
        self._set_status(
            f"Refreshed: {len(self._sinks)} sink(s), "
            f"{len(self._streams)} active stream(s)."
        )

    # ─────────────────────────────────────────────────────────────────────
    # Rendering
    # ─────────────────────────────────────────────────────────────────────

    def _render_lists(self):
        # Default-sink label
        if self._default_sink:
            self._var_default.set(f"Default sink: {self._default_sink}")
        else:
            self._var_default.set("Default sink: (unknown)")

        # Sinks
        self._lb_sinks.delete(0, "end")
        if not self._sinks:
            self._lb_sinks.insert("end", "(no sinks)")
        else:
            for s in self._sinks:
                marker = "*" if s["name"] == self._default_sink else " "
                label = f"{marker} {s['id']:>3}  {s['name']}"
                if s.get("state"):
                    label += f"  [{s['state']}]"
                self._lb_sinks.insert("end", label)

        # Streams
        self._lb_streams.delete(0, "end")
        if not self._streams:
            self._lb_streams.insert("end", "(no active streams)")
        else:
            for st in self._streams:
                app = st.get("app_name") or "(unknown app)"
                sink_ref = st.get("sink_id") or "?"
                label = f"{st['id']:>3}  {app}  \u2192 sink {sink_ref}"
                self._lb_streams.insert("end", label)

    # ─────────────────────────────────────────────────────────────────────
    # Selection helpers
    # ─────────────────────────────────────────────────────────────────────

    def _selected_sink(self):
        if not self._sinks:
            return None
        sel = self._lb_sinks.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self._sinks):
            return self._sinks[idx]
        return None

    def _selected_stream(self):
        if not self._streams:
            return None
        sel = self._lb_streams.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self._streams):
            return self._streams[idx]
        return None

    # ─────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────

    def _on_set_default(self):
        if not self._have_pactl:
            self._set_status("pactl not available.")
            return
        sink = self._selected_sink()
        if sink is None:
            self._set_status("Select a sink first.")
            return
        name = sink["name"]
        r = _run(["pactl", "set-default-sink", name])
        if r["error"] is not None or r["rc"] != 0:
            err = r["error"] or r["stderr"].strip() or f"rc={r['rc']}"
            self._log(f"set-default-sink {name!r} failed: {err}")
            self._log_block("raw stdout", r["stdout"])
            self._set_status("Set default failed.")
            return
        self._log(f"set-default-sink: {name}")
        self._set_status(f"Default sink set to {name}.")
        self._do_refresh()

    def _on_move_stream(self):
        if not self._have_pactl:
            self._set_status("pactl not available.")
            return
        stream = self._selected_stream()
        sink   = self._selected_sink()
        if stream is None:
            self._set_status("Select a stream first.")
            return
        if sink is None:
            self._set_status("Select a target sink first.")
            return
        r = _run(["pactl", "move-sink-input", stream["id"], sink["name"]])
        if r["error"] is not None or r["rc"] != 0:
            err = r["error"] or r["stderr"].strip() or f"rc={r['rc']}"
            self._log(
                f"move-sink-input {stream['id']} -> {sink['name']} failed: {err}"
            )
            self._log_block("raw stdout", r["stdout"])
            self._set_status("Move stream failed.")
            return
        self._log(
            f"move-sink-input: stream {stream['id']} "
            f"({stream.get('app_name') or 'unknown app'}) "
            f"\u2192 {sink['name']}"
        )
        self._set_status(
            f"Moved stream {stream['id']} to {sink['name']}."
        )
        self._do_refresh()

    def _on_wpctl_status(self):
        if not self._have_wpctl:
            self._set_status("wpctl not available.")
            return
        r = _run(["wpctl", "status"], timeout=5)
        if r["error"] is not None:
            self._log(f"wpctl status failed: {r['error']}")
            self._set_status("wpctl status failed.")
            return
        self._log_block("wpctl status", r["stdout"] or r["stderr"])
        self._set_status("wpctl status captured.")

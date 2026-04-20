"""
page_browserdock / browserdock_page.py
────────────────────────────────────────────────────────────────────────────────
Browser Dock page for pagepack_browserdock.

A lightweight launcher / controller page that saves website "tools"
(NaturalReader, school tools, note tools, web apps, etc.) and launches them
in the user's chosen external browser, optionally in app-mode and/or with a
named profile.

This page does NOT embed a foreign browser. There is no Chromium widget,
no fake HTML renderer, no Selenium, no Playwright. It only spawns external
browser processes via subprocess.

Shell contract (Guichi v2 loader):
    page = PageBrowserdock(parent_widget)
    page.build(parent)   # also accepted: create_widgets / mount / render

Storage:
    /pagepack_browserdock/browserdock_data/tools.json

Layout:
    Top bar       — root status + Auto-Find Root / Choose Root
    Main body     — left: saved tools listbox + list buttons + quick-launch
                    right: editor form + launch controls + browser test
    Status bar    — last action / launch result feedback

Linux-first. Mouse-scroll safe (Button-4 / Button-5 / MouseWheel).
"""

import os
import json
import shutil
import subprocess
import webbrowser
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PACK_DIR_NAME = "chi_los"
DATA_DIR_NAME = "browserdock_data"
TOOLS_FILE    = "tools.json"

BROWSER_CHOICES = ["default", "firefox", "brave", "chromium", "chrome"]
LAUNCH_MODES    = ["normal", "new-window", "app"]
CATEGORIES      = ["general", "reader", "school", "notes", "dev", "media", "other"]

# Candidate executable names per browser. First found on PATH wins.
BROWSER_EXES = {
    "firefox":  ["firefox", "firefox-esr"],
    "brave":    ["brave-browser", "brave", "brave-browser-stable"],
    "chromium": ["chromium", "chromium-browser"],
    "chrome":   ["google-chrome", "google-chrome-stable", "chrome"],
}

# Browsers that meaningfully support an "app mode" launch (chromium family).
APP_MODE_SUPPORTED = {"brave", "chromium", "chrome"}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
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


def _find_browser_exe(browser_key):
    """Return absolute path to a browser exe, or None if not on PATH."""
    if browser_key == "default":
        return None  # special-cased: use webbrowser/xdg-open
    for name in BROWSER_EXES.get(browser_key, []):
        path = shutil.which(name)
        if path:
            return path
    return None


def _empty_tool():
    return {
        "name":     "",
        "url":      "",
        "category": "general",
        "browser":  "default",
        "mode":     "normal",
        "profile":  "",
        "notes":    "",
    }


def _is_safe_url(url):
    """
    Conservative URL gatekeeper. We only ever pass the URL as a single
    argv element (never through a shell), but we still reject obviously
    bad/empty values and disallow non-web schemes.
    """
    if not isinstance(url, str):
        return False
    u = url.strip()
    if not u:
        return False
    low = u.lower()
    if low.startswith(("http://", "https://", "file://", "about:")):
        return True
    # Reject any other scheme (anything with a colon before the first slash):
    # this catches javascript:, data:, file: without //, vbscript:, etc.
    head = low.split("/", 1)[0]
    if ":" in head:
        return False
    # Accept bare 'example.com/path' — caller will prepend https://
    return bool(u) and " " not in u and "\t" not in u and "\n" not in u


def _normalize_url(url):
    """Prepend https:// if no scheme is present. Caller has already validated."""
    u = url.strip()
    low = u.lower()
    if low.startswith(("http://", "https://", "file://", "about:")):
        return u
    return "https://" + u


# ─────────────────────────────────────────────────────────────────────────────
# Page class
# ─────────────────────────────────────────────────────────────────────────────

class PageBrowserdock:
    """
    Browser Dock page.

    Shell contract (Guichi loader):
        page = PageBrowserdock(parent_frame)
        page.build(parent)   # also: create_widgets / mount / render
    """

    PAGE_NAME = "browserdock"

    # ─────────────────────────────────────────────────────────────────────
    # Init
    # ─────────────────────────────────────────────────────────────────────

    def __init__(self, parent, app=None, page_key="", page_folder="",
                 *args, **kwargs):
        # Tolerate alternate kwarg names used by some shell variants.
        app         = kwargs.pop("controller",   app)
        page_key    = kwargs.pop("page_context", page_key)
        page_folder = kwargs.pop("page_folder",  page_folder)

        self.parent      = parent
        self.app         = app
        self.page_key    = page_key
        self.page_folder = page_folder

        # ── Path state ──────────────────────────────────────────────────
        self.pack_root = ""
        self.data_dir  = ""
        self.tools_path = ""

        # ── Tool state ──────────────────────────────────────────────────
        self._tools = []          # list[dict]
        self._selected_idx = -1   # index into self._tools

        # ── Form vars (created in _build_editor) ─────────────────────────
        self._var_name     = None
        self._var_url      = None
        self._var_category = None
        self._var_browser  = None
        self._var_mode     = None
        self._var_profile  = None
        self._txt_notes    = None  # tk.Text
        self._var_status   = None
        self._var_root     = None

        # ── Root frame ──────────────────────────────────────────────────
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self._build_top_bar()
        self._build_body()
        self._build_status_bar()

        self.frame.after(100, self._auto_find_root)

    # ─────────────────────────────────────────────────────────────────────
    # Shell mount methods (do not rename)
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

        ttk.Button(bar, text="Auto-Find Root", width=15,
                   command=self._auto_find_root).grid(row=0, column=0, padx=2)
        ttk.Button(bar, text="Choose Root\u2026", width=13,
                   command=self._choose_root).grid(row=0, column=1, padx=2)
        ttk.Button(bar, text="Reload Tools", width=12,
                   command=self._load_tools).grid(row=0, column=2, padx=2)

        self._var_root = tk.StringVar(value="Root: (not set)")
        ttk.Label(bar, textvariable=self._var_root, foreground="#666",
                  font=("", 8), anchor="w").grid(row=0, column=99,
                                                 sticky="ew", padx=8)

    # ─────────────────────────────────────────────────────────────────────
    # Body
    # ─────────────────────────────────────────────────────────────────────

    def _build_body(self):
        body = ttk.Frame(self.frame, padding=(4, 2))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=220)
        body.columnconfigure(1, weight=2, minsize=320)
        body.rowconfigure(0, weight=1)

        self._build_list_pane(body)
        self._build_editor_pane(body)

    # ── Left: tool list ─────────────────────────────────────────────────
    def _build_list_pane(self, parent):
        left = ttk.LabelFrame(parent, text="Saved Tools", padding=(4, 2))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        # Listbox + scrollbar
        list_frm = ttk.Frame(left)
        list_frm.grid(row=0, column=0, sticky="nsew")
        list_frm.columnconfigure(0, weight=1)
        list_frm.rowconfigure(0, weight=1)

        self._listbox = tk.Listbox(list_frm, height=12, selectmode="single",
                                   activestyle="dotbox", exportselection=False)
        sb = ttk.Scrollbar(list_frm, orient="vertical",
                           command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=sb.set)
        self._listbox.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._listbox)
        self._listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self._listbox.bind("<Double-Button-1>", lambda _e: self._launch_selected())

        # List buttons
        btn_row = ttk.Frame(left)
        btn_row.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        for i in range(4):
            btn_row.columnconfigure(i, weight=1)
        ttk.Button(btn_row, text="New",       command=self._new_tool      ).grid(row=0, column=0, padx=1, sticky="ew")
        ttk.Button(btn_row, text="Duplicate", command=self._duplicate_tool).grid(row=0, column=1, padx=1, sticky="ew")
        ttk.Button(btn_row, text="Delete",    command=self._delete_tool   ).grid(row=0, column=2, padx=1, sticky="ew")
        ttk.Button(btn_row, text="Save",      command=self._save_tools    ).grid(row=0, column=3, padx=1, sticky="ew")

        # Quick-launch (top 3)
        ql = ttk.LabelFrame(left, text="Quick Launch (top 3)", padding=(4, 2))
        ql.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for i in range(3):
            ql.columnconfigure(i, weight=1)
        self._quick_buttons = []
        for i in range(3):
            b = ttk.Button(ql, text=f"({i+1})", state="disabled",
                           command=lambda idx=i: self._quick_launch(idx))
            b.grid(row=0, column=i, sticky="ew", padx=1)
            self._quick_buttons.append(b)

    # ── Right: editor ───────────────────────────────────────────────────
    def _build_editor_pane(self, parent):
        right = ttk.LabelFrame(parent, text="Tool Editor", padding=(6, 4))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        self._var_name     = tk.StringVar()
        self._var_url      = tk.StringVar()
        self._var_category = tk.StringVar(value="general")
        self._var_browser  = tk.StringVar(value="default")
        self._var_mode     = tk.StringVar(value="normal")
        self._var_profile  = tk.StringVar()

        r = 0
        ttk.Label(right, text="Name:").grid(row=r, column=0, sticky="e", padx=2, pady=2)
        ttk.Entry(right, textvariable=self._var_name).grid(row=r, column=1, columnspan=3, sticky="ew", padx=2, pady=2)

        r += 1
        ttk.Label(right, text="URL:").grid(row=r, column=0, sticky="e", padx=2, pady=2)
        ttk.Entry(right, textvariable=self._var_url).grid(row=r, column=1, columnspan=3, sticky="ew", padx=2, pady=2)

        r += 1
        ttk.Label(right, text="Category:").grid(row=r, column=0, sticky="e", padx=2, pady=2)
        ttk.Combobox(right, textvariable=self._var_category,
                     values=CATEGORIES, state="normal", width=14
                     ).grid(row=r, column=1, sticky="w", padx=2, pady=2)

        ttk.Label(right, text="Browser:").grid(row=r, column=2, sticky="e", padx=2, pady=2)
        ttk.Combobox(right, textvariable=self._var_browser,
                     values=BROWSER_CHOICES, state="readonly", width=12
                     ).grid(row=r, column=3, sticky="w", padx=2, pady=2)

        r += 1
        ttk.Label(right, text="Mode:").grid(row=r, column=0, sticky="e", padx=2, pady=2)
        ttk.Combobox(right, textvariable=self._var_mode,
                     values=LAUNCH_MODES, state="readonly", width=14
                     ).grid(row=r, column=1, sticky="w", padx=2, pady=2)

        ttk.Label(right, text="Profile:").grid(row=r, column=2, sticky="e", padx=2, pady=2)
        ttk.Entry(right, textvariable=self._var_profile, width=18
                  ).grid(row=r, column=3, sticky="ew", padx=2, pady=2)

        r += 1
        ttk.Label(right, text="Notes:").grid(row=r, column=0, sticky="ne", padx=2, pady=2)
        notes_frm = ttk.Frame(right)
        notes_frm.grid(row=r, column=1, columnspan=3, sticky="nsew", padx=2, pady=2)
        notes_frm.columnconfigure(0, weight=1)
        notes_frm.rowconfigure(0, weight=1)
        right.rowconfigure(r, weight=1)

        self._txt_notes = tk.Text(notes_frm, height=4, wrap="word",
                                  relief="solid", borderwidth=1)
        nsb = ttk.Scrollbar(notes_frm, orient="vertical",
                            command=self._txt_notes.yview)
        self._txt_notes.configure(yscrollcommand=nsb.set)
        self._txt_notes.grid(row=0, column=0, sticky="nsew")
        nsb.grid(row=0, column=1, sticky="ns")
        _bind_scroll(self._txt_notes)

        # Editor action row
        r += 1
        act = ttk.Frame(right)
        act.grid(row=r, column=0, columnspan=4, sticky="ew", pady=(6, 2))
        for i in range(4):
            act.columnconfigure(i, weight=1)
        ttk.Button(act, text="Apply to Selected",
                   command=self._apply_form_to_selected).grid(row=0, column=0, padx=1, sticky="ew")
        ttk.Button(act, text="Clear Form",
                   command=self._clear_form).grid(row=0, column=1, padx=1, sticky="ew")
        ttk.Button(act, text="Test Browser",
                   command=self._test_selected_browser).grid(row=0, column=2, padx=1, sticky="ew")
        ttk.Button(act, text="Launch \u25B6",
                   command=self._launch_from_form).grid(row=0, column=3, padx=1, sticky="ew")

    # ── Status bar ──────────────────────────────────────────────────────
    def _build_status_bar(self):
        self._var_status = tk.StringVar(value="Ready.")
        bar = ttk.Frame(self.frame, padding=(4, 2))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self._var_status, anchor="w",
                  foreground="#444", font=("", 9)
                  ).grid(row=0, column=0, sticky="ew")

    # ─────────────────────────────────────────────────────────────────────
    # Status helper
    # ─────────────────────────────────────────────────────────────────────

    def _set_status(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        try:
            self._var_status.set(f"[{ts}] {msg}")
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Root resolution + persistence
    # ─────────────────────────────────────────────────────────────────────

    def _auto_find_root(self):
        page_file = Path(__file__).resolve()
        for candidate in [page_file.parent, *page_file.parents]:
            if candidate.name == PACK_DIR_NAME:
                self._set_root(str(candidate)); return
            if (candidate / PACK_DIR_NAME).is_dir():
                self._set_root(str(candidate / PACK_DIR_NAME)); return
        cwd = os.getcwd()
        probe = os.path.join(cwd, PACK_DIR_NAME)
        if os.path.isdir(probe):
            self._set_root(probe); return
        if os.path.basename(cwd) == PACK_DIR_NAME:
            self._set_root(cwd); return
        self._set_status("Root not found \u2014 use Choose Root.")

    def _choose_root(self):
        d = filedialog.askdirectory(title=f"Select {PACK_DIR_NAME} directory")
        if d:
            self._set_root(d)

    def _set_root(self, pack_path):
        self.pack_root  = pack_path
        self.data_dir   = os.path.join(pack_path, DATA_DIR_NAME)
        self.tools_path = os.path.join(self.data_dir, TOOLS_FILE)
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except Exception as ex:
            self._set_status(f"Cannot create data dir: {ex}")
            return
        short = pack_path if len(pack_path) <= 60 else "\u2026" + pack_path[-57:]
        self._var_root.set(f"Root: {short}")
        self._load_tools()

    def _load_tools(self):
        if not self.tools_path:
            return
        if not os.path.isfile(self.tools_path):
            self._tools = []
            self._refresh_list()
            self._set_status("No tools file yet \u2014 add your first entry.")
            return
        try:
            with open(self.tools_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as ex:
            self._set_status(f"Failed to read tools.json: {ex}")
            return
        raw = data.get("tools", []) if isinstance(data, dict) else []
        cleaned = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            t = _empty_tool()
            for k in t.keys():
                if k in item and isinstance(item[k], str):
                    t[k] = item[k]
            cleaned.append(t)
        self._tools = cleaned
        self._refresh_list()
        self._set_status(f"Loaded {len(self._tools)} tool(s).")

    def _save_tools(self):
        if not self.tools_path:
            self._set_status("No root set \u2014 cannot save.")
            return
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            payload = {
                "version": 1,
                "tools": self._tools,
            }
            tmp = self.tools_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.tools_path)
            self._set_status(f"Saved {len(self._tools)} tool(s).")
        except Exception as ex:
            self._set_status(f"Save failed: {ex}")

    # ─────────────────────────────────────────────────────────────────────
    # List / form sync
    # ─────────────────────────────────────────────────────────────────────

    def _refresh_list(self):
        self._listbox.delete(0, "end")
        for t in self._tools:
            label = t.get("name") or t.get("url") or "(unnamed)"
            cat = t.get("category", "")
            if cat:
                label = f"{label}  [{cat}]"
            self._listbox.insert("end", label)
        # Restore selection if possible
        if 0 <= self._selected_idx < len(self._tools):
            self._listbox.selection_set(self._selected_idx)
            self._listbox.see(self._selected_idx)
        self._refresh_quick_buttons()

    def _refresh_quick_buttons(self):
        for i, btn in enumerate(self._quick_buttons):
            if i < len(self._tools):
                name = self._tools[i].get("name") or self._tools[i].get("url") or f"Tool {i+1}"
                if len(name) > 18:
                    name = name[:17] + "\u2026"
                btn.configure(text=f"{i+1}. {name}", state="normal")
            else:
                btn.configure(text=f"({i+1})", state="disabled")

    def _on_list_select(self, _event=None):
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self._tools):
            self._selected_idx = idx
            self._load_form_from(self._tools[idx])

    def _load_form_from(self, t):
        self._var_name.set(t.get("name", ""))
        self._var_url.set(t.get("url", ""))
        self._var_category.set(t.get("category", "general") or "general")
        b = t.get("browser", "default")
        self._var_browser.set(b if b in BROWSER_CHOICES else "default")
        m = t.get("mode", "normal")
        self._var_mode.set(m if m in LAUNCH_MODES else "normal")
        self._var_profile.set(t.get("profile", ""))
        self._txt_notes.delete("1.0", "end")
        self._txt_notes.insert("1.0", t.get("notes", ""))

    def _form_to_dict(self):
        return {
            "name":     self._var_name.get().strip(),
            "url":      self._var_url.get().strip(),
            "category": self._var_category.get().strip() or "general",
            "browser":  self._var_browser.get() if self._var_browser.get() in BROWSER_CHOICES else "default",
            "mode":     self._var_mode.get() if self._var_mode.get() in LAUNCH_MODES else "normal",
            "profile":  self._var_profile.get().strip(),
            "notes":    self._txt_notes.get("1.0", "end-1c"),
        }

    def _clear_form(self):
        self._load_form_from(_empty_tool())
        self._listbox.selection_clear(0, "end")
        self._selected_idx = -1
        self._set_status("Form cleared.")

    # ─────────────────────────────────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────────────────────────────────

    def _new_tool(self):
        t = self._form_to_dict()
        if not t["name"] and not t["url"]:
            t = _empty_tool()
            t["name"] = "New Tool"
        if not t["url"]:
            self._set_status("New entry created \u2014 fill URL then Apply.")
        elif not _is_safe_url(t["url"]):
            self._set_status("Rejected: URL has unsupported scheme or whitespace.")
            return
        self._tools.append(t)
        self._selected_idx = len(self._tools) - 1
        self._refresh_list()
        self._save_tools()

    def _duplicate_tool(self):
        if not (0 <= self._selected_idx < len(self._tools)):
            self._set_status("Select a tool to duplicate.")
            return
        src = dict(self._tools[self._selected_idx])
        src["name"] = (src.get("name", "") + " (copy)").strip()
        self._tools.insert(self._selected_idx + 1, src)
        self._selected_idx += 1
        self._refresh_list()
        self._save_tools()

    def _delete_tool(self):
        if not (0 <= self._selected_idx < len(self._tools)):
            self._set_status("Select a tool to delete.")
            return
        name = self._tools[self._selected_idx].get("name") or "(unnamed)"
        if not messagebox.askyesno("Delete tool",
                                   f"Delete '{name}'?", parent=self.frame):
            return
        del self._tools[self._selected_idx]
        if self._selected_idx >= len(self._tools):
            self._selected_idx = len(self._tools) - 1
        self._refresh_list()
        if 0 <= self._selected_idx < len(self._tools):
            self._load_form_from(self._tools[self._selected_idx])
        else:
            self._clear_form()
        self._save_tools()

    def _apply_form_to_selected(self):
        t = self._form_to_dict()
        if t["url"] and not _is_safe_url(t["url"]):
            self._set_status("Rejected: URL has unsupported scheme or whitespace.")
            return
        if 0 <= self._selected_idx < len(self._tools):
            self._tools[self._selected_idx] = t
            self._set_status(f"Updated entry: {t['name'] or t['url']}")
        else:
            if not t["name"] and not t["url"]:
                self._set_status("Nothing to add \u2014 fill name or URL first.")
                return
            self._tools.append(t)
            self._selected_idx = len(self._tools) - 1
            self._set_status(f"Added new entry: {t['name'] or t['url']}")
        self._refresh_list()
        self._save_tools()

    # ─────────────────────────────────────────────────────────────────────
    # Launch
    # ─────────────────────────────────────────────────────────────────────

    def _quick_launch(self, idx):
        if 0 <= idx < len(self._tools):
            self._launch_tool(self._tools[idx])

    def _launch_selected(self):
        if not (0 <= self._selected_idx < len(self._tools)):
            self._set_status("Select a tool first.")
            return
        self._launch_tool(self._tools[self._selected_idx])

    def _launch_from_form(self):
        t = self._form_to_dict()
        if not t["url"]:
            self._set_status("URL is empty \u2014 nothing to launch.")
            return
        self._launch_tool(t)

    def _launch_tool(self, tool):
        url_raw = (tool.get("url") or "").strip()
        if not _is_safe_url(url_raw):
            self._set_status(f"Refused unsafe URL: {url_raw!r}")
            return
        url = _normalize_url(url_raw)

        browser = tool.get("browser", "default")
        mode    = tool.get("mode", "normal")
        profile = (tool.get("profile") or "").strip()
        name    = tool.get("name") or url

        # Default browser path: hand off to the OS via webbrowser.
        # No app/profile support here — fall back softly with a note.
        if browser == "default":
            soft_notes = []
            if mode == "app":
                soft_notes.append("app mode unsupported by default browser")
            if profile:
                soft_notes.append("profile ignored by default browser")
            try:
                ok = webbrowser.open(url, new=(1 if mode == "new-window" else 0))
                if not ok:
                    self._set_status(f"Default browser refused to open {name}.")
                    return
            except Exception as ex:
                self._set_status(f"Default browser failed: {ex}")
                return
            note = (" \u2014 " + "; ".join(soft_notes)) if soft_notes else ""
            self._set_status(f"Launched (default): {name}{note}")
            return

        # Named browser: must be on PATH.
        exe = _find_browser_exe(browser)
        if not exe:
            self._set_status(
                f"Browser '{browser}' not found on PATH \u2014 install it or pick another."
            )
            return

        argv = [exe]
        soft_notes = []

        # App mode
        effective_mode = mode
        if mode == "app" and browser not in APP_MODE_SUPPORTED:
            soft_notes.append(f"app mode unsupported by {browser}, using new-window")
            effective_mode = "new-window"

        if effective_mode == "app":
            # Chromium-family: --app=URL gives a chromeless app-like window.
            argv.append(f"--app={url}")
        elif effective_mode == "new-window":
            if browser == "firefox":
                argv += ["-new-window", url]
            else:
                argv += ["--new-window", url]
        else:  # normal
            argv.append(url)

        # Profile handling (best-effort; differs per browser family)
        if profile:
            if browser == "firefox":
                # If looks like a path, use --profile <path>; else -P <name>
                if os.sep in profile or profile.startswith("~"):
                    argv[1:1] = ["--profile", os.path.expanduser(profile)]
                else:
                    argv[1:1] = ["-P", profile, "--no-remote"]
            else:
                # Chromium family
                if os.sep in profile or profile.startswith("~"):
                    argv.insert(1, f"--user-data-dir={os.path.expanduser(profile)}")
                else:
                    argv.insert(1, f"--profile-directory={profile}")

        # Spawn detached. Never use shell=True. URL is a separate argv element.
        try:
            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin":  subprocess.DEVNULL,
                "close_fds": True,
            }
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(argv, **popen_kwargs)
        except FileNotFoundError:
            self._set_status(f"Launch failed: '{exe}' disappeared.")
            return
        except Exception as ex:
            self._set_status(f"Launch failed ({browser}): {ex}")
            return

        note = (" \u2014 " + "; ".join(soft_notes)) if soft_notes else ""
        self._set_status(
            f"Launched ({browser}/{effective_mode}"
            + (f", profile={profile}" if profile else "")
            + f"): {name}{note}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # Browser availability test
    # ─────────────────────────────────────────────────────────────────────

    def _test_selected_browser(self):
        choice = self._var_browser.get()
        if choice == "default":
            self._set_status("Default browser uses OS handler (xdg-open / webbrowser).")
            return
        exe = _find_browser_exe(choice)
        if exe:
            self._set_status(f"Browser '{choice}' OK: {exe}")
        else:
            tried = ", ".join(BROWSER_EXES.get(choice, []))
            self._set_status(f"Browser '{choice}' NOT FOUND on PATH (tried: {tried})")

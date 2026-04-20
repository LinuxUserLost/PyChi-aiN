"""
shell_gui.py — Guichi Shell GUI
Tkinter-based GUI surface for the shell backend.
Reads registry on startup, displays packs/pages in a sidebar,
shows info panels in the content area.

Dev mode adds a persistent dev-loaded layer for manual .py file loading.
Dev-loaded items are stored in dev_loaded.json, separate from the registry.

Milestone: shell_sidepanel_control
  - Custom shell toolbar (collapsible, tied to left sidebar width)
  - Left sidebar (collapsible, default: navigation_sidebar)
  - Right sidebar (collapsible, default: jsondisplayer placeholder)
  - Layout state persistence
  - Same-sidepage duplication prevention
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import sys
import json
import inspect
import traceback
import importlib.util
from datetime import datetime, timezone

# Ensure we can import sibling modules and guichi.py
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SHELL_DIR = os.path.dirname(_THIS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _SHELL_DIR not in sys.path:
    sys.path.insert(0, _SHELL_DIR)

import guichi
import shell_registry
import shell_loader
import shell_theme


# ── Sidebar ID separator and builders ───────────────────────
# Sidebar treeview item IDs encode type + identity fields.
# Uses null byte separator (cannot appear in filesystem paths
# or normal IDs). If this assumption is ever violated, the
# _make_sidebar_*_id and _parse_sidebar_id functions are the
# only places to fix.

_ID_SEP = "\x00"

# Fixed ID for the DEV LOADED section header node
_DEV_SECTION_ID = "_dev_section"


def _make_sidebar_pack_id(pack_id, source_path):
    """Build a sidebar treeview item ID for a pack."""
    return f"{pack_id}{_ID_SEP}{source_path}"


def _make_sidebar_page_id(pack_id, source_path, page_id):
    """Build a sidebar treeview item ID for a page."""
    return f"{pack_id}{_ID_SEP}{source_path}{_ID_SEP}{page_id}"


def _make_sidebar_dev_item_id(file_path, class_name):
    """Build a sidebar treeview item ID for a dev-loaded item."""
    return f"_dev_item{_ID_SEP}{file_path}{_ID_SEP}{class_name}"


def _parse_sidebar_id(item_id):
    """
    Parse a sidebar treeview item ID.
    Returns:
        ("pack", pack_id, source_path)
        ("page", pack_id, source_path, page_id)
        ("dev_section",)
        ("dev_item", file_path, class_name)
        None on failure
    """
    # Dev section header
    if item_id == _DEV_SECTION_ID:
        return ("dev_section",)

    # Dev-loaded item: _dev_item\x00file_path\x00class_name
    if item_id.startswith("_dev_item" + _ID_SEP):
        rest = item_id[len("_dev_item" + _ID_SEP):]
        parts = rest.split(_ID_SEP, 1)
        if len(parts) == 2:
            return ("dev_item", parts[0], parts[1])
        return None

    # Normal pack/page
    parts = item_id.split(_ID_SEP)
    if len(parts) == 2:
        return ("pack", parts[0], parts[1])
    elif len(parts) == 3:
        return ("page", parts[0], parts[1], parts[2])
    return None


# ── Page GUI mount probing ──────────────────────────────────
# Probed method names, in order (first match wins).
# This is a convenience probe, not a contract.

_PAGE_GUI_METHODS = ["build", "create_widgets", "mount", "render"]


# ── Dev-loaded items persistence ────────────────────────────

DEV_LOADED_PATH = os.path.join(guichi.STATE_DIR, "dev_loaded.json")


def _load_dev_items():
    """Load dev-loaded items from disk. Returns list of item dicts."""
    if not os.path.isfile(DEV_LOADED_PATH):
        return []
    try:
        with open(DEV_LOADED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_dev_items(items):
    """Save dev-loaded items to disk."""
    os.makedirs(os.path.dirname(DEV_LOADED_PATH), exist_ok=True)
    with open(DEV_LOADED_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, indent=2, ensure_ascii=False)


def _find_dev_item(items, file_path, class_name):
    """Find index of a dev item by file_path + class_name. Returns index or -1."""
    for i, item in enumerate(items):
        if item.get("file_path") == file_path and item.get("class_name") == class_name:
            return i
    return -1


# ── Class scanning ──────────────────────────────────────────

def _scan_classes_in_file(file_path):
    """
    Lightweight scan of a .py file for class definitions.
    Returns (class_names_list, error_string_or_none).
    Classes are filtered to those defined in the file itself
    (not imported from other modules).
    """
    module_name = f"_dev_scan_{os.path.splitext(os.path.basename(file_path))[0]}"
    parent_dir = os.path.dirname(os.path.abspath(file_path))

    path_added = False
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
        path_added = True

    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            return [], f"could not build import spec for: {file_path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        classes = []
        for name in sorted(dir(module)):
            obj = getattr(module, name, None)
            if isinstance(obj, type) and getattr(obj, "__module__", None) == module.__name__:
                classes.append(name)
        return classes, None

    except Exception:
        return [], traceback.format_exc()
    finally:
        if path_added:
            try:
                sys.path.remove(parent_dir)
            except ValueError:
                pass


# ── Color and style constants ───────────────────────────────

STATUS_COLORS = {
    "ok":          {"fg": "#d0d0d0", "bg": ""},
    "warning":     {"fg": "#e8a838", "bg": ""},
    "unavailable": {"fg": "#e05050", "bg": ""},
    "hidden":      {"fg": "#707070", "bg": ""},
    "error":       {"fg": "#e05050", "bg": ""},
    "failed":      {"fg": "#e05050", "bg": ""},
    "dev_loaded":  {"fg": "#40c0c0", "bg": ""},
    "dev_section": {"fg": "#40c0c0", "bg": ""},
    "not_loaded":  {"fg": "#808080", "bg": ""},
}

INFO_LABEL_FONT = ("TkDefaultFont", 10, "bold")
INFO_VALUE_FONT = ("TkFixedFont", 10)
INFO_WARN_FONT = ("TkDefaultFont", 9)

WINDOW_TITLE = "Guichi Shell"
WINDOW_TITLE_DEV = "Guichi Shell \u2014 Dev Mode"
WINDOW_MIN_W = 800
WINDOW_MIN_H = 500

# ── Layout constants (shell_sidepanel_control milestone) ────

SIDEBAR_OPEN_W = 240
LEFT_COLLAPSED_W = 28
RIGHT_SIDEBAR_OPEN_W = 250
RIGHT_COLLAPSED_W = 24
TOOLBAR_HEIGHT = 32
TOOLBAR_MIN_W = 68                    # two buttons + padding
_T = shell_theme.get_theme()
COLLAPSED_BAR_BG     = _T["panel_bg"]
COLLAPSED_BAR_BORDER = _T["border"]
COLLAPSED_TEXT_COLOR = _T["text_muted"]
TOOLBAR_BG           = _T["topbar_bg"]
TOOLBAR_FG           = _T["text_main"]
TOOLBAR_BTN_FG       = _T["text_active"]
SIDEBAR_HEADER_BG    = _T["sidebar_bg"]
SIDEBAR_HEADER_FG    = _T["text_muted"]
BUTTON_BG            = _T["button_bg"]
BUTTON_HOVER_BG      = _T["button_hover"]
BUTTON_ACTIVE_FG     = _T["button_active"]
STATUS_BG            = _T["panel_bg"]
STATUS_FG            = _T["text_main"]
TREE_BG              = _T["panel_bg"]
TREE_FG              = _T["text_main"]
TREE_SELECTED_BG     = _T["accent"]
TREE_SELECTED_FG     = _T["text_on_accent"]
APP_BG               = _T["app_bg"]


# ── Main window ─────────────────────────────────────────────

class GuichiShell:
    """Main GUI shell window."""

    def __init__(self, root):
        self.root = root
        self.root.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self.root.geometry("1100x650")

        # Shell state
        self.config = guichi.load_config()
        self.registry = shell_registry.load_registry(guichi.REGISTRY_PATH)

        # Dev-loaded items (persistent)
        self._dev_items = _load_dev_items()

        # UI state
        self.show_hidden = tk.BooleanVar(value=False)
        self.show_hidden.trace_add("write", lambda *_: self.refresh_sidebar())

        self._dev_mode_var = tk.BooleanVar(value=self.config.get("dev_mode", False))
        self._dev_mode_var.trace_add("write", lambda *_: self._on_dev_mode_toggle())

        self._theme_var = tk.StringVar(value=self.config.get("current_theme", shell_theme.DEFAULT_THEME))

        # Track what's selected for context actions
        self._selected_pack_id = None
        self._selected_source_path = None
        self._selected_page_id = None

        # ── Layout state (three booleans, one apply path) ───
        self.left_open = self.config.get("left_sidebar_open", True)
        self.right_open = self.config.get("right_sidebar_open", False)
        self.toolbar_full = self.config.get("toolbar_full", True)

        # Sidepage tracking for same-sidepage prevention
        self._left_sidepage = "navigation_sidebar"
        self._right_sidepage = "jsondisplayer"

        self._build_ui()
        self._update_title()
        self.refresh_sidebar()
        self._load_right_sidebar_content()
        self._apply_layout()
        self.set_status("ready")

    # ── UI construction ─────────────────────────────────────

    def _build_ui(self):
        """Build all UI elements."""
        self.root.configure(bg=APP_BG)
        self._build_menu()
        self._build_status_bar()       # pack bottom first
        self._build_toolbar()           # custom shell toolbar, pack top
        self._build_panes()             # three-column layout fills rest

    def _build_menu(self):
        """Build the menu bar. Dev Mode menu sits between View and Tools.
        OS menu bar — unchanged from baseline."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # Shell menu
        shell_menu = tk.Menu(menubar, tearoff=0)
        shell_menu.add_command(label="Discover packs...", command=self._on_discover)
        shell_menu.add_command(
            label="Discover (broad scan)...",
            command=lambda: self._on_discover(scan_style=2),
        )
        shell_menu.add_command(label="Rebuild registry", command=self._on_rebuild)
        shell_menu.add_separator()
        shell_menu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="Shell", menu=shell_menu)

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_checkbutton(
            label="Show hidden packs",
            variable=self.show_hidden,
        )
        menubar.add_cascade(label="View", menu=view_menu)

        # Dev Mode menu
        self._dev_menu = tk.Menu(menubar, tearoff=0)
        self._dev_menu.add_checkbutton(
            label="Enable Dev Mode",
            variable=self._dev_mode_var,
        )
        self._dev_menu.add_separator()
        self._dev_menu.add_command(
            label="Load Python Page File\u2026",
            command=self._on_dev_load_py,
        )
        self._dev_menu.add_separator()
        self._dev_menu.add_command(
            label="Reset dev mode",
            command=self._on_dev_reset,
        )
        menubar.add_cascade(label="Dev Mode", menu=self._dev_menu)

        # Store indices for gated items (0=toggle, 1=sep, 2=load, 3=sep, 4=reset)
        self._dev_menu_load_idx = 2
        self._dev_menu_reset_idx = 4
        self._update_dev_menu_state()

        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Discovery report", command=self._on_report)
        tools_menu.add_command(label="Problems report", command=self._on_problems_report)
        menubar.add_cascade(label="Tools", menu=tools_menu)

    def _update_dev_menu_state(self):
        """Enable or disable dev-only menu items based on dev mode toggle."""
        state = tk.NORMAL if self._dev_mode_var.get() else tk.DISABLED
        self._dev_menu.entryconfigure(self._dev_menu_load_idx, state=state)
        self._dev_menu.entryconfigure(self._dev_menu_reset_idx, state=state)

    # ── Custom shell toolbar ────────────────────────────────

    def _build_toolbar(self):
        """Build the custom shell toolbar frame (below OS menu bar, above content).
        Collapses leftward tied to left sidebar state."""
        # Outer wrapper — always full width, provides the toolbar row
        self._toolbar_wrapper = tk.Frame(self.root, bg=TOOLBAR_BG)
        self._toolbar_wrapper.pack(side=tk.TOP, fill=tk.X)

        # Toolbar frame — visual toolbar with border, variable width
        self._toolbar_frame = tk.Frame(
            self._toolbar_wrapper, bg=TOOLBAR_BG,
            relief=tk.GROOVE, bd=1,
            height=TOOLBAR_HEIGHT,
        )
        self._toolbar_frame.pack_propagate(False)

        # ── Full toolbar content (shown when toolbar_full=True) ──
        self._toolbar_full_content = tk.Frame(self._toolbar_frame, bg=TOOLBAR_BG)

        self._btn_left_toggle_full = tk.Button(
            self._toolbar_full_content, text="\u2630", width=3,
            command=self._toggle_left,
            relief=tk.FLAT, bg=TOOLBAR_BG, fg=TOOLBAR_BTN_FG,
            activebackground=BUTTON_HOVER_BG, activeforeground=BUTTON_ACTIVE_FG,
            font=("TkDefaultFont", 11),
        )
        self._btn_left_toggle_full.pack(side=tk.LEFT, padx=(4, 2), pady=2)

        self._toolbar_label = tk.Label(
            self._toolbar_full_content,
            text=WINDOW_TITLE, bg=TOOLBAR_BG, fg=TOOLBAR_FG,
            font=("TkDefaultFont", 9),
        )
        self._toolbar_label.pack(side=tk.LEFT, padx=8)

        # Display button lives in _toolbar_wrapper (the always-present outer frame),
        # not the nested _toolbar_full_content. _apply_layout manages its visibility.
        self._display_btn = tk.Button(
            self._toolbar_wrapper, text="Display",
            relief=tk.FLAT, bg=TOOLBAR_BG, fg=TOOLBAR_BTN_FG,
            activebackground=BUTTON_HOVER_BG, activeforeground=BUTTON_ACTIVE_FG,
            font=("TkDefaultFont", 9),
            command=self._show_display_menu,
        )

        self._btn_toolbar_collapse = tk.Button(
            self._toolbar_full_content, text="\u00ab", width=3,
            command=self._collapse_toolbar,
            relief=tk.FLAT, bg=TOOLBAR_BG, fg=TOOLBAR_BTN_FG,
            activebackground=BUTTON_HOVER_BG, activeforeground=BUTTON_ACTIVE_FG,
            font=("TkDefaultFont", 11),
        )
        self._btn_toolbar_collapse.pack(side=tk.RIGHT, padx=(2, 4), pady=2)

        self._btn_right_toggle_full = tk.Button(
            self._toolbar_full_content, text="\u25eb", width=3,
            command=self._toggle_right,
            relief=tk.FLAT, bg=TOOLBAR_BG, fg=TOOLBAR_BTN_FG,
            activebackground=BUTTON_HOVER_BG, activeforeground=BUTTON_ACTIVE_FG,
            font=("TkDefaultFont", 11),
        )
        self._btn_right_toggle_full.pack(side=tk.RIGHT, padx=2, pady=2)

        # ── Collapsed toolbar content (shown when toolbar_full=False) ──
        self._toolbar_min_content = tk.Frame(self._toolbar_frame, bg=TOOLBAR_BG)

        self._btn_left_toggle_min = tk.Button(
            self._toolbar_min_content, text="\u2630", width=3,
            command=self._toggle_left,
            relief=tk.FLAT, bg=TOOLBAR_BG, fg=TOOLBAR_BTN_FG,
            activebackground=BUTTON_HOVER_BG, activeforeground=BUTTON_ACTIVE_FG,
            font=("TkDefaultFont", 11),
        )
        self._btn_left_toggle_min.pack(side=tk.LEFT, padx=(4, 2), pady=2)

        self._btn_toolbar_expand = tk.Button(
            self._toolbar_min_content, text="\u00bb", width=3,
            command=self._expand_toolbar,
            relief=tk.FLAT, bg=TOOLBAR_BG, fg=TOOLBAR_BTN_FG,
            activebackground=BUTTON_HOVER_BG, activeforeground=BUTTON_ACTIVE_FG,
            font=("TkDefaultFont", 11),
        )
        self._btn_toolbar_expand.pack(side=tk.LEFT, padx=2, pady=2)

    # ── Three-column layout ─────────────────────────────────

    def _build_panes(self):
        """Build the left sidebar | content | right sidebar layout."""
        self._main_frame = tk.Frame(self.root, bg=APP_BG)
        self._main_frame.pack(fill=tk.BOTH, expand=True)

        # ── Left panel container ────────────────────────────
        self._left_panel = tk.Frame(self._main_frame, width=SIDEBAR_OPEN_W, bg=COLLAPSED_BAR_BG)
        self._left_panel.pack_propagate(False)
        self._left_panel.pack(side=tk.LEFT, fill=tk.Y)

        # Left open content: header + sidebar treeview
        self._left_content = tk.Frame(self._left_panel, bg=SIDEBAR_HEADER_BG)

        left_header = tk.Frame(self._left_content, bg=SIDEBAR_HEADER_BG)
        left_header.pack(fill=tk.X)
        tk.Label(
            left_header, text="navigation_sidebar",
            font=("TkDefaultFont", 8), bg=SIDEBAR_HEADER_BG,
            fg=SIDEBAR_HEADER_FG, anchor=tk.W,
        ).pack(side=tk.LEFT, padx=6, pady=3)

        sidebar_frame = tk.Frame(self._left_content, bg=SIDEBAR_HEADER_BG)
        sidebar_frame.pack(fill=tk.BOTH, expand=True)
        sidebar_frame.columnconfigure(0, weight=1)
        sidebar_frame.rowconfigure(0, weight=1)

        _tree_style = ttk.Style()
        _tree_style.configure(
            "GuichiSidebar.Treeview",
            background=TREE_BG,
            fieldbackground=TREE_BG,
            foreground=TREE_FG,
            borderwidth=0,
        )
        _tree_style.map(
            "GuichiSidebar.Treeview",
            background=[("selected", TREE_SELECTED_BG)],
            foreground=[("selected", TREE_SELECTED_FG)],
        )

        self.sidebar_tree = ttk.Treeview(
            sidebar_frame,
            show="tree",
            selectmode="browse",
            style="GuichiSidebar.Treeview",
        )
        sidebar_scroll = ttk.Scrollbar(
            sidebar_frame, orient=tk.VERTICAL,
            command=self.sidebar_tree.yview,
        )
        self.sidebar_tree.configure(yscrollcommand=sidebar_scroll.set)
        self.sidebar_tree.grid(row=0, column=0, sticky="nsew")
        sidebar_scroll.grid(row=0, column=1, sticky="ns")

        self.sidebar_tree.bind("<<TreeviewSelect>>", self._on_sidebar_select)

        # Right-click context menu
        self.sidebar_tree.bind("<Button-3>", self._on_sidebar_right_click)
        self.sidebar_tree.bind("<Button-2>", self._on_sidebar_right_click)

        # Configure tag colors for treeview items
        for status, colors in STATUS_COLORS.items():
            self.sidebar_tree.tag_configure(status, foreground=colors["fg"])

        # Left collapsed bar
        self._left_collapsed = self._create_collapsed_bar(
            self._left_panel, "navigation_sidebar",
            LEFT_COLLAPSED_W, 90, self._toggle_left,
        )

        # ── Right panel container ───────────────────────────
        # Pack right panel BEFORE content frame so side=RIGHT works correctly
        self._right_panel = tk.Frame(self._main_frame, width=RIGHT_COLLAPSED_W, bg=COLLAPSED_BAR_BG)
        self._right_panel.pack_propagate(False)
        self._right_panel.pack(side=tk.RIGHT, fill=tk.Y)

        # Right open content: header + sidewindow host area
        self._right_content = tk.Frame(self._right_panel, bg=SIDEBAR_HEADER_BG)

        right_header = tk.Frame(self._right_content, bg=SIDEBAR_HEADER_BG)
        right_header.pack(fill=tk.X)
        tk.Label(
            right_header, text="jsondisplayer",
            font=("TkDefaultFont", 8), bg=SIDEBAR_HEADER_BG,
            fg=SIDEBAR_HEADER_FG, anchor=tk.W,
        ).pack(side=tk.LEFT, padx=6, pady=3)

        # Sidewindow host area (jsondisplayer content goes here)
        self._right_sw_area = tk.Frame(self._right_content, bg=SIDEBAR_HEADER_BG)
        self._right_sw_area.pack(fill=tk.BOTH, expand=True)

        # Right collapsed bar
        self._right_collapsed = self._create_collapsed_bar(
            self._right_panel, "jsondisplayer",
            RIGHT_COLLAPSED_W, 270, self._toggle_right,
        )

        # ── Content frame (center page area) ────────────────
        self.content_frame = tk.Frame(self._main_frame)
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._show_welcome()

    def _create_collapsed_bar(self, parent, codename, bar_width, text_angle, on_click):
        """
        Create a collapsed sidebar bar with vertical text inside a padded border.
        Returns the bar frame (not yet packed — _apply_layout manages visibility).
        """
        bar = tk.Frame(parent, bg=COLLAPSED_BAR_BG)

        # Inner canvas with border look
        inner = tk.Frame(bar, bg=COLLAPSED_BAR_BORDER, padx=1, pady=1)
        inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=4)

        canvas = tk.Canvas(
            inner, width=max(bar_width - 8, 12),
            bg=COLLAPSED_BAR_BG, highlightthickness=0,
            cursor="hand2",
        )
        canvas.pack(fill=tk.BOTH, expand=True)

        text_id = canvas.create_text(
            max(bar_width - 8, 12) // 2, 50,
            text=codename, angle=text_angle,
            fill=COLLAPSED_TEXT_COLOR,
            font=("TkDefaultFont", 9, "bold"),
            anchor="center",
        )

        def _reposition(event):
            cx = event.width // 2
            cy = event.height // 2
            canvas.coords(text_id, cx, cy)

        canvas.bind("<Configure>", _reposition)
        canvas.bind("<Button-1>", lambda e: on_click())

        return bar

    # ── Layout state machine ────────────────────────────────

    def _apply_layout(self):
        """
        Single layout update path. Reads left_open, right_open, toolbar_full
        and configures all panels + toolbar accordingly.
        """
        # ── Left panel ──────────────────────────────────────
        self._left_content.pack_forget()
        self._left_collapsed.pack_forget()

        if self.left_open:
            self._left_panel.configure(width=SIDEBAR_OPEN_W)
            self._left_content.pack(fill=tk.BOTH, expand=True)
        else:
            self._left_panel.configure(width=LEFT_COLLAPSED_W)
            self._left_collapsed.pack(fill=tk.BOTH, expand=True)

        # ── Right panel ─────────────────────────────────────
        self._right_content.pack_forget()
        self._right_collapsed.pack_forget()

        if self.right_open:
            self._right_panel.configure(width=RIGHT_SIDEBAR_OPEN_W)
            self._right_content.pack(fill=tk.BOTH, expand=True)
        else:
            self._right_panel.configure(width=RIGHT_COLLAPSED_W)
            self._right_collapsed.pack(fill=tk.BOTH, expand=True)

        # ── Custom toolbar ──────────────────────────────────
        self._toolbar_full_content.pack_forget()
        self._toolbar_min_content.pack_forget()
        self._toolbar_frame.pack_forget()
        self._display_btn.pack_forget()

        if self.toolbar_full:
            # Display claims the right edge first; toolbar frame fills remaining space.
            self._display_btn.pack(side=tk.RIGHT, padx=(2, 4), pady=2)
            self._toolbar_frame.pack_propagate(True)
            self._toolbar_frame.pack(fill=tk.X, expand=True, padx=0, pady=0)
            self._toolbar_full_content.pack(fill=tk.BOTH, expand=True)
        else:
            # Collapsed: toolbar width matches left sidebar (or minimum two buttons)
            if self.left_open:
                toolbar_w = SIDEBAR_OPEN_W
            else:
                toolbar_w = TOOLBAR_MIN_W
            self._toolbar_frame.configure(width=toolbar_w, height=TOOLBAR_HEIGHT)
            self._toolbar_frame.pack_propagate(False)
            self._toolbar_frame.pack(side=tk.LEFT, padx=0, pady=0)
            self._toolbar_min_content.pack(fill=tk.BOTH, expand=True)

        # ── Update toolbar label for dev mode ───────────────
        if self._dev_mode_var.get():
            self._toolbar_label.configure(text=WINDOW_TITLE_DEV)
        else:
            self._toolbar_label.configure(text=WINDOW_TITLE)

        # ── Persist layout state ────────────────────────────
        self._save_layout_state()

    def _toggle_left(self):
        """Toggle left sidebar open/collapsed."""
        self.left_open = not self.left_open
        self._apply_layout()

    def _toggle_right(self):
        """Toggle right sidebar open/collapsed."""
        self.right_open = not self.right_open
        self._apply_layout()

    def _collapse_toolbar(self):
        """Collapse toolbar to left-aligned minimum."""
        self.toolbar_full = False
        self._apply_layout()

    def _expand_toolbar(self):
        """Expand toolbar to full width."""
        self.toolbar_full = True
        self._apply_layout()

    def _save_layout_state(self):
        """Persist layout visibility booleans to config. Non-fatal on failure."""
        self.config["left_sidebar_open"] = self.left_open
        self.config["right_sidebar_open"] = self.right_open
        self.config["toolbar_full"] = self.toolbar_full
        try:
            guichi.save_config(self.config)
        except Exception:
            pass  # never crash on persistence failure

    # ── Same-sidepage duplication guard ─────────────────────

    def _can_assign_sidepage(self, codename, target_side):
        """
        Check whether a sidepage with the given codename can be assigned to
        target_side ("left" or "right") without duplicating across panels.
        Returns True if assignment is allowed, False if it would duplicate.
        """
        if target_side == "left":
            return self._right_sidepage != codename
        else:
            return self._left_sidepage != codename

    # ── Right sidebar sidewindow loading ────────────────────

    def _load_right_sidebar_content(self):
        """
        Load the jsondisplayer sidewindow into the right sidebar content area.
        On any failure: shows error in the sidebar, does not crash.
        """
        # Same-sidepage guard
        if not self._can_assign_sidepage("jsondisplayer", "right"):
            self._show_right_sidebar_error(
                "jsondisplayer is already loaded in the left sidebar.\n"
                "Same sidepage cannot be open on both sides."
            )
            return

        try:
            sw_dir = os.path.join(_THIS_DIR, "sidewindows", "jsondisplayer")
            init_path = os.path.join(sw_dir, "__init__.py")

            if not os.path.isfile(init_path):
                self._show_right_sidebar_error(
                    f"jsondisplayer not found:\n{init_path}"
                )
                return

            spec = importlib.util.spec_from_file_location(
                "guichi_sw_jsondisplayer", init_path,
            )
            if spec is None:
                self._show_right_sidebar_error(
                    "could not build import spec for jsondisplayer"
                )
                return

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            build_fn = getattr(mod, "build", None)
            if build_fn is None:
                self._show_right_sidebar_error(
                    "jsondisplayer module has no build() function"
                )
                return

            build_fn(self._right_sw_area)
            self._right_sidepage = getattr(mod, "CODENAME", "jsondisplayer")

        except Exception:
            tb = traceback.format_exc()
            self._show_right_sidebar_error(
                f"jsondisplayer load failed:\n{tb}"
            )

    def _show_right_sidebar_error(self, message):
        """Show an error/info message in the right sidebar content area."""
        for child in self._right_sw_area.winfo_children():
            child.destroy()

        tk.Label(
            self._right_sw_area, text="sidewindow error",
            font=("TkDefaultFont", 9, "bold"), fg="#e05050",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=6, pady=(8, 2))

        tk.Label(
            self._right_sw_area, text=message,
            font=("TkFixedFont", 8), fg="#c08080",
            anchor=tk.NW, wraplength=220, justify=tk.LEFT,
        ).pack(fill=tk.BOTH, padx=6, pady=4, expand=True)

        self.set_status("right sidebar: sidewindow load failed")

    # ── Status bar ──────────────────────────────────────────

    def _build_status_bar(self):
        """Build the bottom status bar."""
        self.status_var = tk.StringVar(value="")
        self.status_bar = tk.Label(
            self.root, textvariable=self.status_var,
            anchor=tk.W, relief=tk.SUNKEN, padx=6, pady=2,
            bg=STATUS_BG, fg=STATUS_FG,
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _update_title(self):
        """Set window title based on dev mode state."""
        if self._dev_mode_var.get():
            self.root.title(WINDOW_TITLE_DEV)
        else:
            self.root.title(WINDOW_TITLE)

    # ── Dev mode toggle ─────────────────────────────────────

    def _on_dev_mode_toggle(self):
        """Handle dev mode enable/disable toggle."""
        enabled = self._dev_mode_var.get()
        self.config["dev_mode"] = enabled
        guichi.save_config(self.config)
        self._update_title()
        self._update_dev_menu_state()
        # Also update toolbar label
        self._apply_layout()
        state_label = "enabled" if enabled else "disabled"
        self.set_status(f"dev mode {state_label}")

    # ── Theme selector ───────────────────────────────────────

    def _on_theme_select(self, selected_name):
        """Save selected theme to config. Restart required to fully apply."""
        try:
            self.config["current_theme"] = selected_name
            guichi.save_config(self.config)
            self.set_status(f"Theme saved: {selected_name}. Restart Guichi to fully apply.")
        except Exception as e:
            self.set_status(f"Theme save failed: {e}")

    def _show_display_menu(self):
        """Build and pop up the Display dropdown on demand.
        Menu is constructed fresh each click so no persistent tk.Menu widget
        exists for KDE Appmenu to surface in the Tk menu layer."""
        menu = tk.Menu(self._display_btn, tearoff=0)

        theme_sub = tk.Menu(menu, tearoff=0)
        for _name in shell_theme.list_themes():
            theme_sub.add_radiobutton(
                label=_name,
                variable=self._theme_var,
                value=_name,
                command=lambda n=_name: self._on_theme_select(n),
            )
        menu.add_cascade(label="Theme", menu=theme_sub)

        size_sub = tk.Menu(menu, tearoff=0)
        for _label, _geo in [
            ("800 \u00d7 500  (minimum)",        "800x500"),
            ("1100 \u00d7 650  (default)",        "1100x650"),
            ("1280 \u00d7 720",                   "1280x720"),
            ("1600 \u00d7 900",                   "1600x900"),
            ("1920 \u00d7 1080  (FHD)",           "1920x1080"),
            ("2560 \u00d7 1080  (workbench wide)", "2560x1080"),
            ("2560 \u00d7 1440  (QHD)",           "2560x1440"),
            ("3200 \u00d7 1800",                  "3200x1800"),
            ("3840 \u00d7 2160  (4K UHD)",        "3840x2160"),
        ]:
            size_sub.add_command(
                label=_label,
                command=lambda g=_geo: self.root.geometry(g),
            )
        menu.add_cascade(label="Window Size", menu=size_sub)

        # Hold reference until next click so the menu isn't GC'd mid-popup.
        self._active_display_menu = menu
        menu.tk_popup(
            self._display_btn.winfo_rootx(),
            self._display_btn.winfo_rooty() + self._display_btn.winfo_height(),
        )

    # ── Sidebar ─────────────────────────────────────────────

    def refresh_sidebar(self):
        """Rebuild the sidebar treeview from registry + dev-loaded items."""
        tree = self.sidebar_tree

        old_sel = tree.selection()

        for item in tree.get_children():
            tree.delete(item)

        # ── Normal registered packs ─────────────────────────
        include_hidden = self.show_hidden.get()
        packs = guichi.action_list(
            self.registry, include_hidden=include_hidden
        )

        pack_count = 0
        page_count = 0
        problem_count = 0

        for pack in packs:
            pack_id = pack.get("pack_id") or "(no id)"
            suffix = pack.get("display_suffix", "")
            status = pack.get("status", "ok")
            source_path = pack.get("source_path", "")
            hidden = pack.get("hidden", False)

            label_id = pack_id[len("pagepack_"):] if pack_id.startswith("pagepack_") else pack_id
            display_name = f"{label_id}{suffix}"
            if hidden:
                display_name += "  [hidden]"

            if hidden:
                tag = "hidden"
            elif status == "unavailable":
                tag = "unavailable"
            elif status == "warning":
                tag = "warning"
            else:
                tag = "ok"

            if status in ("warning", "unavailable"):
                problem_count += 1

            item_id = _make_sidebar_pack_id(pack_id, source_path)

            tree.insert(
                "", tk.END,
                iid=item_id,
                text=display_name,
                tags=(tag,),
                open=True,
            )

            pack_count += 1

            for page in pack.get("pages", []):
                pid = page.get("page_id") or "(no id)"
                page_name = page.get("page_name") or pid
                page_status = page.get("status", "ok")

                if page.get("errors") or page_status == "warning":
                    page_tag = "warning"
                else:
                    page_tag = tag

                page_item_id = _make_sidebar_page_id(pack_id, source_path, pid)

                tree.insert(
                    item_id, tk.END,
                    iid=page_item_id,
                    text=f"  {page_name}",
                    tags=(page_tag,),
                )
                page_count += 1

        # ── DEV LOADED section ──────────────────────────────
        if self._dev_items:
            tree.insert(
                "", tk.END,
                iid=_DEV_SECTION_ID,
                text="\u2500\u2500\u2500 DEV LOADED \u2500\u2500\u2500",
                tags=("dev_section",),
                open=True,
            )

            for dev_item in self._dev_items:
                fp = dev_item.get("file_path", "?")
                cn = dev_item.get("class_name", "?")
                display = dev_item.get("display_name", f"{os.path.basename(fp)} \u2192 {cn}")
                status = dev_item.get("status", "not_loaded")

                if status in ("ok", "warning"):
                    tag = "dev_loaded"
                elif status == "not_loaded":
                    tag = "not_loaded"
                else:
                    tag = "failed"

                dev_iid = _make_sidebar_dev_item_id(fp, cn)
                tree.insert(
                    _DEV_SECTION_ID, tk.END,
                    iid=dev_iid,
                    text=f"  {display}",
                    tags=(tag,),
                )

        # Restore selection
        for sel_id in old_sel:
            if tree.exists(sel_id):
                tree.selection_set(sel_id)
                break

        dev_count = len(self._dev_items)
        status_parts = [f"{pack_count} pack(s)", f"{page_count} page(s)", f"{problem_count} problem(s)"]
        if dev_count:
            status_parts.append(f"{dev_count} dev-loaded")
        self.set_status(", ".join(status_parts))

    def _resolve_pack_for_item(self, item_id):
        """
        Given a sidebar item ID (pack or page), resolve to pack identity.
        Returns (pack_id, source_path, is_hidden) or None.
        """
        parsed = _parse_sidebar_id(item_id)
        if parsed is None:
            return None

        if parsed[0] == "pack":
            pack_id, source_path = parsed[1], parsed[2]
        elif parsed[0] == "page":
            pack_id, source_path = parsed[1], parsed[2]
        else:
            return None

        matches = shell_registry.lookup_pack(
            self.registry, pack_id, source_path=source_path
        )
        if not matches:
            return None

        is_hidden = matches[0].get("hidden", False)
        return (pack_id, source_path, is_hidden)

    def _parse_sidebar_selection(self):
        """
        Parse the currently selected sidebar item.
        Sets self._selected_* fields.
        Returns ("pack", pack_entry), ("page", page_entry, pack_entry),
                ("dev_item", dev_item_dict), ("dev_section",), or None.
        """
        sel = self.sidebar_tree.selection()
        if not sel:
            self._selected_pack_id = None
            self._selected_source_path = None
            self._selected_page_id = None
            return None

        parsed = _parse_sidebar_id(sel[0])
        if parsed is None:
            self._selected_pack_id = None
            self._selected_source_path = None
            self._selected_page_id = None
            return None

        if parsed[0] == "dev_section":
            self._selected_pack_id = None
            self._selected_source_path = None
            self._selected_page_id = None
            return ("dev_section",)

        if parsed[0] == "dev_item":
            file_path, class_name = parsed[1], parsed[2]
            self._selected_pack_id = None
            self._selected_source_path = None
            self._selected_page_id = None
            idx = _find_dev_item(self._dev_items, file_path, class_name)
            if idx < 0:
                return None
            return ("dev_item", self._dev_items[idx])

        if parsed[0] == "page":
            _, pack_id, source_path, page_id = parsed
            self._selected_pack_id = pack_id
            self._selected_source_path = source_path
            self._selected_page_id = page_id

            matches = shell_registry.lookup_pack(
                self.registry, pack_id, source_path=source_path
            )
            if not matches:
                return None
            pack_entry = matches[0]
            for p in pack_entry.get("pages", []):
                if p.get("page_id") == page_id:
                    return ("page", p, pack_entry)
            return None

        if parsed[0] == "pack":
            _, pack_id, source_path = parsed
            self._selected_pack_id = pack_id
            self._selected_source_path = source_path
            self._selected_page_id = None

            matches = shell_registry.lookup_pack(
                self.registry, pack_id, source_path=source_path
            )
            if not matches:
                return None
            return ("pack", matches[0])

        return None

    def _on_sidebar_select(self, event=None):
        """Handle sidebar selection change."""
        parsed = self._parse_sidebar_selection()
        if parsed is None:
            self._show_welcome()
            return

        kind = parsed[0]
        if kind == "pack":
            self._show_pack_info(parsed[1])
        elif kind == "page":
            pack_id     = self._selected_pack_id
            page_id     = self._selected_page_id
            source_path = self._selected_source_path
            key = (pack_id, page_id, source_path)
            # Guard against re-fire from refresh_sidebar's selection_set
            # restoring the same selection after a refresh.
            if key != getattr(self, "_last_autoloaded_page", None):
                self._last_autoloaded_page = key
                self._on_load_page(pack_id, page_id, source_path)
        elif kind == "dev_item":
            self._show_dev_item_info(parsed[1])
        elif kind == "dev_section":
            self._show_welcome()

    # ── Sidebar context menu ────────────────────────────────

    def _on_sidebar_right_click(self, event):
        """Show context menu on right-click over a sidebar item."""
        item_id = self.sidebar_tree.identify_row(event.y)
        if not item_id:
            return

        self.sidebar_tree.selection_set(item_id)

        parsed = _parse_sidebar_id(item_id)
        if parsed is None:
            return

        menu = tk.Menu(self.sidebar_tree, tearoff=0)

        if parsed[0] == "dev_item":
            file_path, class_name = parsed[1], parsed[2]
            menu.add_command(
                label="Reload",
                command=lambda: self._on_dev_item_reload(file_path, class_name),
            )
            menu.add_command(
                label="Remove",
                command=lambda: self._on_dev_item_remove(file_path, class_name),
            )
            menu.tk_popup(event.x_root, event.y_root)
            return

        if parsed[0] == "dev_section":
            return  # no context menu on the section header

        # Normal pack/page context menu
        pack_info = self._resolve_pack_for_item(item_id)
        if pack_info is None:
            return

        pack_id, source_path, is_hidden = pack_info

        # Page-specific: offer Load
        if parsed[0] == "page":
            page_id = parsed[3]
            menu.add_command(
                label="Load page",
                command=lambda: self._on_load_page(pack_id, page_id, source_path),
            )
            menu.add_separator()

        # Pack-level actions
        if is_hidden:
            menu.add_command(
                label="Unhide",
                command=lambda: self._on_unhide_pack(pack_id, source_path),
            )
        else:
            menu.add_command(
                label="Hide",
                command=lambda: self._on_hide_pack(pack_id, source_path),
            )

        menu.add_command(
            label="Remove\u2026",
            command=lambda: self._on_remove_pack(pack_id, source_path),
        )

        menu.tk_popup(event.x_root, event.y_root)

    def _on_hide_pack(self, pack_id, source_path):
        """Hide a pack (shortcut for remove choice 3)."""
        result = guichi.action_apply_remove(self.registry, pack_id, source_path, 3)
        if result:
            self.refresh_sidebar()
            self._show_welcome()
            self.set_status(f"hidden: {pack_id}")
        else:
            self.set_status(f"hide failed: {pack_id} not found")

    def _on_unhide_pack(self, pack_id, source_path):
        """Unhide a previously hidden pack."""
        found = guichi.action_unhide(self.registry, pack_id, source_path)
        if found:
            self.refresh_sidebar()
            self._show_welcome()
            self.set_status(f"unhidden: {pack_id}")
        else:
            self.set_status(f"unhide failed: {pack_id} not found")

    def _on_remove_pack(self, pack_id, source_path):
        """Show the three-choice remove dialog for a pack."""
        choice = _RemoveDialog.ask(self.root, pack_id, source_path)
        if choice is None:
            return

        result = guichi.action_apply_remove(self.registry, pack_id, source_path, choice)
        if result:
            self.refresh_sidebar()
            self._show_welcome()
            self.set_status(result)
        else:
            self.set_status(f"remove failed: {pack_id} not found")

    # ── Normal page loading ─────────────────────────────────

    def _on_load_page(self, pack_id, page_id, source_path):
        """Load a normal registered page."""
        self.set_status(f"loading: {page_id}...")

        result = guichi.action_load_page(
            self.config, self.registry,
            pack_id, page_id,
            source_path=source_path,
            instantiate=False,
        )

        if result.get("status") == "failed":
            self.show_load_result(result)
            self.set_status(f"load failed: {page_id}")
            return

        page_class = result.get("page_class")

        if page_class is None:
            self.show_load_result(result)
            self.set_status(f"load failed: {page_id} (no class returned)")
            return

        embedded, method_used, embed_error = self._try_embed_page(page_class)

        if embedded:
            self.set_status(f"loaded: {page_id} (embedded via {method_used})")
        elif embed_error:
            self.show_load_result_with_embed_error(result, method_used, embed_error)
            self.set_status(f"loaded: {page_id} (embed failed: {method_used})")
        else:
            self.show_load_result_with_no_gui(result)
            self.set_status(f"loaded: {page_id} (no GUI method found)")

    def _try_embed_page(self, page_class):
        """
        Instantiate page_class with content_frame as parent, then probe for
        a GUI mount method and call it. Clears content before instantiation so
        the new frame is a child of content_frame and survives clear_content()
        on subsequent loads.
        Returns:
            (True,  method_name, None)        — embedded successfully
            (False, method_name, error_str)   — instantiation or method raised
            (False, None,        None)        — no GUI method found
        """
        self.clear_content()
        try:
            page_instance = page_class(self.content_frame)
        except Exception:
            tb = traceback.format_exc()
            return (False, "instantiation", tb)

        for method_name in _PAGE_GUI_METHODS:
            method = getattr(page_instance, method_name, None)
            if method is None:
                continue
            if not callable(method):
                continue
            try:
                # Support both build() and build(parent) conventions.
                # inspect.signature on a bound method excludes 'self', so
                # len==0 means no extra args expected; len>=1 means pass parent.
                sig = inspect.signature(method)
                if sig.parameters:
                    method(self.content_frame)
                else:
                    method()
                return (True, method_name, None)
            except Exception:
                tb = traceback.format_exc()
                return (False, method_name, tb)

        return (False, None, None)

    # ── Dev mode: load .py file ─────────────────────────────

    def _on_dev_load_py(self):
        """Open file dialog, scan for classes, add to dev-loaded items, attempt load."""
        file_path = filedialog.askopenfilename(
            parent=self.root,
            title="Load Python Page File (dev mode)",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if not file_path:
            return

        file_path = os.path.abspath(file_path)

        # Scan for classes
        self.set_status(f"scanning: {os.path.basename(file_path)}...")
        classes, scan_error = _scan_classes_in_file(file_path)

        if scan_error:
            self.set_status(f"scan failed: {os.path.basename(file_path)}")
            self._show_dev_scan_error(file_path, scan_error)
            return

        if not classes:
            self.set_status(f"no classes found: {os.path.basename(file_path)}")
            self._show_dev_scan_error(
                file_path,
                "No class definitions found in this file.\n"
                "The file may be a utility module or data file.",
            )
            return

        # Choose class
        if len(classes) == 1:
            class_name = classes[0]
        else:
            class_name = _ClassChooserDialog.ask(
                self.root, file_path, classes
            )
            if class_name is None:
                return  # cancelled

        # Add to dev items
        display_name = f"{os.path.basename(file_path)} \u2192 {class_name}"

        # Check for existing entry with same identity — replace it
        idx = _find_dev_item(self._dev_items, file_path, class_name)
        new_entry = {
            "file_path": file_path,
            "class_name": class_name,
            "display_name": display_name,
            "status": "not_loaded",
            "message": "not yet loaded",
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        if idx >= 0:
            self._dev_items[idx] = new_entry
        else:
            self._dev_items.append(new_entry)

        _save_dev_items(self._dev_items)
        self.refresh_sidebar()

        # Immediately load it
        self._dev_load_and_show(file_path, class_name)

    def _dev_load_and_show(self, file_path, class_name):
        """
        Load a dev .py file via shell_loader.load_page with synthetic entries.
        Updates the dev item's status/message and shows the result.
        """
        self.set_status(f"loading: {os.path.basename(file_path)} \u2192 {class_name}...")

        # Construct synthetic entries for shell_loader
        synthetic_pack = {
            "pack_id": f"dev_{os.path.splitext(os.path.basename(file_path))[0]}",
            "source_path": os.path.dirname(file_path),
        }
        synthetic_page = {
            "page_id": f"dev_{os.path.splitext(os.path.basename(file_path))[0]}_{class_name}",
            "page_name": f"{os.path.basename(file_path)} \u2192 {class_name}",
            "page_path": os.path.basename(file_path),
            "page_class": class_name,
        }

        result = shell_loader.load_page(
            synthetic_pack, synthetic_page,
            dev_mode=True, instantiate=False,
        )

        # Update persisted status
        idx = _find_dev_item(self._dev_items, file_path, class_name)
        if idx >= 0:
            self._dev_items[idx]["status"] = result.get("status", "failed")
            self._dev_items[idx]["message"] = result.get("message", "")
            _save_dev_items(self._dev_items)
            self.refresh_sidebar()

        # Show result
        if result.get("status") == "failed":
            self.show_load_result(result)
            self.set_status(f"dev load failed: {class_name}")
            return

        page_class = result.get("page_class")
        if page_class is None:
            self.show_load_result(result)
            self.set_status(f"dev load failed: {class_name} (no class returned)")
            return

        embedded, method_used, embed_error = self._try_embed_page(page_class)

        if embedded:
            self.set_status(f"dev loaded: {class_name} (embedded via {method_used})")
        elif embed_error:
            self.show_load_result_with_embed_error(result, method_used, embed_error)
            self.set_status(f"dev loaded: {class_name} (embed failed: {method_used})")
        else:
            self.show_load_result_with_no_gui(result)
            self.set_status(f"dev loaded: {class_name} (no GUI method found)")

    def _on_dev_item_reload(self, file_path, class_name):
        """Reload a dev-loaded item (re-import, re-instantiate, re-embed)."""
        self._dev_load_and_show(file_path, class_name)

    def _on_dev_item_remove(self, file_path, class_name):
        """Remove a single dev-loaded item."""
        idx = _find_dev_item(self._dev_items, file_path, class_name)
        if idx >= 0:
            self._dev_items.pop(idx)
            _save_dev_items(self._dev_items)
            self.refresh_sidebar()
            self._show_welcome()
            self.set_status(f"removed dev item: {class_name}")

    def _on_dev_reset(self):
        """Master dev-mode reset: clear all dev-loaded items."""
        if not self._dev_items:
            self.set_status("dev reset: nothing to clear")
            return

        count = len(self._dev_items)
        confirm = messagebox.askyesno(
            "Reset dev mode",
            f"Remove all {count} dev-loaded item(s)?\n\n"
            "This clears the dev-loaded layer only.\n"
            "Normal registered packs are not affected.",
            parent=self.root,
        )
        if not confirm:
            return

        self._dev_items.clear()
        _save_dev_items(self._dev_items)
        self.refresh_sidebar()
        self._show_welcome()
        self.set_status(f"dev reset: cleared {count} item(s)")

    def _show_dev_scan_error(self, file_path, error_text):
        """Show a scan error for a dev .py file load attempt."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        tk.Label(
            frame, text="Dev Load: scan failed",
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame, text=f"file: {file_path}",
            font=INFO_VALUE_FONT, anchor=tk.W,
        ).pack(fill=tk.X, pady=(4, 0))

        self._render_detail_text(frame, error_text)

    # ── Content area ────────────────────────────────────────

    def clear_content(self):
        """Remove all widgets from the content frame."""
        for child in self.content_frame.winfo_children():
            child.destroy()

    def _show_welcome(self):
        """Show the empty/welcome state in the content area.
        Also clears selection tracking."""
        self.clear_content()
        self._selected_pack_id = None
        self._selected_source_path = None
        self._selected_page_id = None
        msg = tk.Label(
            self.content_frame,
            text="Guichi Shell\n\nUse Shell \u2192 Discover packs to scan for page packs.",
            justify=tk.CENTER, pady=40,
        )
        msg.pack(expand=True)

    def _show_pack_info(self, pack_entry):
        """Show pack information in the content area."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        pack_id = pack_entry.get("pack_id") or "(no pack_id)"
        suffix = pack_entry.get("display_suffix", "")
        status = pack_entry.get("status", "?")
        source_path = pack_entry.get("source_path", "?")
        hidden = pack_entry.get("hidden", False)

        tk.Label(
            frame, text=f"{pack_id}{suffix}",
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        status_text = f"status: {status}"
        if hidden:
            status_text += "  (hidden)"
        status_color = STATUS_COLORS.get(status, STATUS_COLORS["ok"])["fg"]
        tk.Label(frame, text=status_text, fg=status_color, anchor=tk.W).pack(fill=tk.X)

        tk.Label(
            frame, text=f"source: {source_path}",
            font=INFO_VALUE_FONT, anchor=tk.W,
        ).pack(fill=tk.X, pady=(4, 0))

        tk.Label(
            frame, text=f"last scanned: {pack_entry.get('last_scanned', '?')}",
            font=INFO_VALUE_FONT, anchor=tk.W,
        ).pack(fill=tk.X)

        pages = pack_entry.get("pages", [])
        tk.Label(frame, text=f"pages: {len(pages)}", anchor=tk.W).pack(fill=tk.X, pady=(8, 0))

        for page in pages:
            pid = page.get("page_id") or "(no id)"
            pname = page.get("page_name") or ""
            pstatus = page.get("status", "ok")
            ptag = STATUS_COLORS.get(pstatus, STATUS_COLORS["ok"])["fg"]
            tk.Label(
                frame, text=f"  {pid} \u2014 {pname}  [{pstatus}]",
                fg=ptag, anchor=tk.W,
            ).pack(fill=tk.X)

        self._render_warnings_errors(frame, pack_entry)

    def _show_page_info(self, page_entry, pack_entry):
        """Show page information in the content area, with a Load button."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        page_id = page_entry.get("page_id") or "(no page_id)"
        page_name = page_entry.get("page_name") or "(no name)"
        page_status = page_entry.get("status", "?")
        pack_id = pack_entry.get("pack_id") or "(no pack_id)"
        source_path = pack_entry.get("source_path") or "?"

        header_row = tk.Frame(frame)
        header_row.pack(fill=tk.X)

        tk.Label(
            header_row, text=page_name,
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
        ).pack(side=tk.LEFT)

        tk.Button(
            header_row, text="Load page",
            command=lambda: self._on_load_page(pack_id, page_id, source_path),
            padx=8,
        ).pack(side=tk.RIGHT)

        status_color = STATUS_COLORS.get(page_status, STATUS_COLORS["ok"])["fg"]
        tk.Label(frame, text=f"status: {page_status}", fg=status_color, anchor=tk.W).pack(fill=tk.X)

        fields = [
            ("page_id", page_id),
            ("page_path", page_entry.get("page_path") or "(no path)"),
            ("page_class", page_entry.get("page_class") or "(no class)"),
            ("pack", f"{pack_id} at {source_path}"),
        ]
        for opt in ("page_title", "page_folder_path", "page_config_path"):
            val = page_entry.get(opt)
            if val:
                fields.append((opt, val))

        for label, value in fields:
            row = tk.Frame(frame)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"{label}:", font=INFO_LABEL_FONT, anchor=tk.W, width=18).pack(side=tk.LEFT)
            tk.Label(row, text=value, font=INFO_VALUE_FONT, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X)

        self._render_warnings_errors(frame, page_entry)

    def _show_dev_item_info(self, dev_item):
        """Show dev-loaded item info in the content area, with a Load button."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        file_path = dev_item.get("file_path", "?")
        class_name = dev_item.get("class_name", "?")
        display_name = dev_item.get("display_name", "?")
        status = dev_item.get("status", "not_loaded")
        message = dev_item.get("message", "")
        added_at = dev_item.get("added_at", "?")

        # Header with Load button
        header_row = tk.Frame(frame)
        header_row.pack(fill=tk.X)

        tk.Label(
            header_row, text=display_name,
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
            fg=STATUS_COLORS["dev_loaded"]["fg"],
        ).pack(side=tk.LEFT)

        tk.Button(
            header_row, text="Load page",
            command=lambda: self._dev_load_and_show(file_path, class_name),
            padx=8,
        ).pack(side=tk.RIGHT)

        # Dev-loaded marker
        tk.Label(
            frame, text="[dev-loaded \u2014 not canonically registered]",
            fg=STATUS_COLORS["dev_loaded"]["fg"], anchor=tk.W,
        ).pack(fill=tk.X)

        # Status
        status_color = STATUS_COLORS.get(status, STATUS_COLORS["not_loaded"])["fg"]
        tk.Label(frame, text=f"status: {status}", fg=status_color, anchor=tk.W).pack(fill=tk.X)

        # Fields
        fields = [
            ("file", file_path),
            ("class", class_name),
            ("added", added_at),
        ]
        if message:
            fields.append(("message", message))

        for label, value in fields:
            row = tk.Frame(frame)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"{label}:", font=INFO_LABEL_FONT, anchor=tk.W, width=12).pack(side=tk.LEFT)
            tk.Label(row, text=value, font=INFO_VALUE_FONT, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X)

    def _render_warnings_errors(self, parent, entry):
        """Render warnings and errors from a pack or page entry dict."""
        warnings = entry.get("warnings", [])
        if warnings:
            tk.Label(
                parent, text="warnings:", font=INFO_LABEL_FONT,
                anchor=tk.W, pady=(8, 0),
            ).pack(fill=tk.X)
            for w in warnings:
                tk.Label(
                    parent, text=f"  {w}", font=INFO_WARN_FONT,
                    fg="#e8a838", anchor=tk.W, wraplength=500,
                ).pack(fill=tk.X)

        errors = entry.get("errors", [])
        if errors:
            tk.Label(
                parent, text="errors:", font=INFO_LABEL_FONT,
                anchor=tk.W, pady=(8, 0),
            ).pack(fill=tk.X)
            for e in errors:
                tk.Label(
                    parent, text=f"  {e}", font=INFO_WARN_FONT,
                    fg="#e05050", anchor=tk.W, wraplength=500,
                ).pack(fill=tk.X)

    def show_load_result(self, result):
        """Show a load result in the content area. Fallback for failed loads."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        status = result.get("status", "?")
        page_id = result.get("page_id") or "(no id)"
        page_name = result.get("page_name") or ""
        message = result.get("message") or ""

        tk.Label(
            frame, text=f"Load: {page_name or page_id}",
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        status_color = STATUS_COLORS.get(status, STATUS_COLORS["ok"])["fg"]
        tk.Label(frame, text=f"status: {status}", fg=status_color, anchor=tk.W).pack(fill=tk.X)

        if message:
            tk.Label(frame, text=message, anchor=tk.W, wraplength=500).pack(fill=tk.X, pady=(4, 0))

        error_detail = result.get("error_detail")
        if error_detail:
            self._render_detail_text(frame, error_detail)

    def show_load_result_with_no_gui(self, result):
        """Show a successful load result where no GUI method was found."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        page_name = result.get("page_name") or result.get("page_id") or "(unknown)"

        tk.Label(
            frame, text=f"Loaded: {page_name}",
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame, text="status: ok (class loaded, no GUI method found)",
            fg=STATUS_COLORS["warning"]["fg"], anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame,
            text=f"probed methods: {', '.join(_PAGE_GUI_METHODS)}",
            font=INFO_VALUE_FONT, anchor=tk.W,
        ).pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            frame,
            text="The page loaded successfully but does not expose a GUI mount method.\n"
                 "This is normal for non-visual pages.",
            anchor=tk.W, wraplength=500, justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(8, 0))

        message = result.get("message")
        if message:
            tk.Label(frame, text=message, font=INFO_VALUE_FONT, anchor=tk.W).pack(fill=tk.X, pady=(8, 0))

    def show_load_result_with_embed_error(self, result, method_name, error_detail):
        """Show a load result where a GUI method was found but raised an error."""
        self.clear_content()
        frame = tk.Frame(self.content_frame, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True, anchor=tk.NW)

        page_name = result.get("page_name") or result.get("page_id") or "(unknown)"

        tk.Label(
            frame, text=f"Loaded: {page_name}",
            font=("TkDefaultFont", 14, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame,
            text=f"status: embed failed ({method_name} raised an error)",
            fg=STATUS_COLORS["error"]["fg"], anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame,
            text=f"The page class loaded and instantiated, but {method_name}(parent) failed.",
            anchor=tk.W, wraplength=500,
        ).pack(fill=tk.X, pady=(8, 0))

        self._render_detail_text(frame, error_detail)

    def _render_detail_text(self, parent, text):
        """Render a scrollable read-only detail/traceback text block."""
        tk.Label(
            parent, text="detail:", font=INFO_LABEL_FONT,
            anchor=tk.W, pady=(8, 0),
        ).pack(fill=tk.X)
        detail_frame = tk.Frame(parent)
        detail_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL)
        detail_widget = tk.Text(
            detail_frame, height=10, wrap=tk.WORD, font=INFO_VALUE_FONT,
            yscrollcommand=detail_scroll.set,
        )
        detail_scroll.configure(command=detail_widget.yview)
        detail_widget.grid(row=0, column=0, sticky="nsew")
        detail_scroll.grid(row=0, column=1, sticky="ns")
        detail_widget.insert(tk.END, text)
        detail_widget.configure(state=tk.DISABLED)

    # ── Status bar ──────────────────────────────────────────

    def set_status(self, text):
        """Update the status bar text."""
        self.status_var.set(text)

    # ── Discover ────────────────────────────────────────────

    def _on_discover(self, scan_style=None):
        """Run pack discovery from a user-chosen root directory."""
        if scan_style is None:
            scan_style = self.config.get("default_scan_style", 1)

        initial_dir = self.config.get("last_selected_root") or guichi.SHELL_DIR
        if not os.path.isdir(initial_dir):
            initial_dir = guichi.SHELL_DIR

        root_path = filedialog.askdirectory(
            parent=self.root,
            title=f"Select Guichi root to scan (style {scan_style})",
            initialdir=initial_dir,
        )
        if not root_path:
            return

        result, merge_actions = guichi.action_discover(
            self.config, self.registry, root=root_path, scan_style=scan_style,
        )

        self.refresh_sidebar()

        if result.get("scan_errors"):
            err_text = "\n".join(result["scan_errors"])
            messagebox.showerror("Scan errors", err_text, parent=self.root)
            self.set_status(f"discover: {len(result['scan_errors'])} error(s)")
        else:
            found = len(result.get("findings", []))
            added = sum(1 for a in merge_actions if a["action"] in ("added", "added_no_id"))
            updated = sum(1 for a in merge_actions if a["action"] == "updated")
            self.set_status(
                f"discovered {found} pack(s) in {root_path} "
                f"({added} new, {updated} updated)"
            )

    def _on_rebuild(self):
        """Rebuild the registry by re-walking all known source paths."""
        actions = guichi.action_rebuild(self.config, self.registry)
        self.refresh_sidebar()

        refreshed = sum(1 for a in actions if a["action"] == "refreshed")
        unavail = sum(1 for a in actions if a["action"] == "marked_unavailable")
        self.set_status(
            f"rebuild complete: {refreshed} refreshed, {unavail} unavailable"
        )

    # ── Report viewer ───────────────────────────────────────

    def _on_report(self):
        """Show the full discovery report in a viewer window."""
        report_text = guichi.action_report(
            self.registry,
            include_hidden=self.show_hidden.get(),
        )
        self._open_report_window("Discovery Report", report_text)

    def _on_problems_report(self):
        """Show the problems-only report in a viewer window."""
        report_text = guichi.action_report(
            self.registry,
            problems_only=True,
            include_hidden=self.show_hidden.get(),
        )
        self._open_report_window("Problems Report", report_text)

    def _open_report_window(self, title, report_text):
        """Open a top-level window displaying a copyable text report."""
        win = tk.Toplevel(self.root)
        win.title(f"Guichi \u2014 {title}")
        win.geometry("700x500")
        win.minsize(400, 300)

        text_frame = tk.Frame(win)
        text_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        text_widget = tk.Text(
            text_frame, wrap=tk.WORD, font=INFO_VALUE_FONT,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=text_widget.yview)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        text_widget.insert(tk.END, report_text)
        text_widget.configure(state=tk.DISABLED)

        btn_frame = tk.Frame(win, pady=6, padx=6)
        btn_frame.pack(fill=tk.X)

        def copy_report():
            win.clipboard_clear()
            win.clipboard_append(report_text)
            copy_btn.configure(text="copied")
            win.after(1500, lambda: copy_btn.configure(text="Copy to clipboard"))

        copy_btn = tk.Button(btn_frame, text="Copy to clipboard", command=copy_report)
        copy_btn.pack(side=tk.LEFT)

        tk.Button(btn_frame, text="Close", command=win.destroy).pack(side=tk.RIGHT)

        self.set_status(f"opened: {title}")


# ── Remove dialog ───────────────────────────────────────────

class _RemoveDialog(tk.Toplevel):
    """Modal dialog for the three-choice remove/hide action."""

    def __init__(self, parent, pack_id, source_path):
        super().__init__(parent)
        self.title("Remove / Hide pack")
        self.resizable(False, False)
        self.result = None

        self.transient(parent)
        self.grab_set()

        frame = tk.Frame(self, padx=16, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame, text=f"Pack: {pack_id}",
            font=("TkDefaultFont", 11, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame, text=source_path,
            font=INFO_VALUE_FONT, anchor=tk.W, fg="#888888",
        ).pack(fill=tk.X, pady=(0, 12))

        choices = [
            (1, "Remove from shell list only"),
            (2, "Remove and forget saved state"),
            (3, "Hide instead"),
        ]

        for num, label in choices:
            tk.Button(
                frame, text=label, anchor=tk.W, padx=8, pady=4,
                command=lambda n=num: self._choose(n),
            ).pack(fill=tk.X, pady=2)

        tk.Button(
            frame, text="Cancel", padx=8, pady=4,
            command=self._cancel,
        ).pack(fill=tk.X, pady=(8, 0))

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w = self.winfo_width()
        h = self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _choose(self, choice):
        self.result = choice
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    @classmethod
    def ask(cls, parent, pack_id, source_path):
        dlg = cls(parent, pack_id, source_path)
        dlg.wait_window()
        return dlg.result


# ── Class chooser dialog ────────────────────────────────────

class _ClassChooserDialog(tk.Toplevel):
    """
    Modal dialog for choosing a class from a .py file
    when multiple classes are found.
    Returns the chosen class name or None if cancelled.
    """

    def __init__(self, parent, file_path, class_names):
        super().__init__(parent)
        self.title("Choose class")
        self.resizable(False, False)
        self.result = None

        self.transient(parent)
        self.grab_set()

        frame = tk.Frame(self, padx=16, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame, text=os.path.basename(file_path),
            font=("TkDefaultFont", 11, "bold"), anchor=tk.W,
        ).pack(fill=tk.X)

        tk.Label(
            frame, text=f"{len(class_names)} classes found \u2014 choose one:",
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 8))

        for name in class_names:
            tk.Button(
                frame, text=name, anchor=tk.W, padx=8, pady=4,
                command=lambda n=name: self._choose(n),
            ).pack(fill=tk.X, pady=2)

        tk.Button(
            frame, text="Cancel", padx=8, pady=4,
            command=self._cancel,
        ).pack(fill=tk.X, pady=(8, 0))

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w = self.winfo_width()
        h = self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _choose(self, name):
        self.result = name
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    @classmethod
    def ask(cls, parent, file_path, class_names):
        dlg = cls(parent, file_path, class_names)
        dlg.wait_window()
        return dlg.result


# ── Launch function (called from guichi.py) ─────────────────

def launch():
    """Create and run the GUI shell."""
    root = tk.Tk()
    app = GuichiShell(root)
    root.mainloop()


if __name__ == "__main__":
    launch()

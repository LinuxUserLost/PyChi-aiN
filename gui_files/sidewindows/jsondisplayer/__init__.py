"""
jsondisplayer — Guichi Shell sidewindow placeholder
First external sidewindow example for the right sidebar.

Loads the first .json file (alphabetically) from its library/ subdirectory
and displays it in a scrollable read-only text widget.

This is a controlled placeholder, not final sidewindow architecture.
"""

import os
import json
import tkinter as tk
from tkinter import ttk


CODENAME = "jsondisplayer"

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBRARY_DIR = os.path.join(_THIS_DIR, "library")


def _find_first_json():
    """
    Find the first .json file in the library directory (alphabetical order).
    Returns (file_path, None) on success, (None, error_string) on failure.
    """
    if not os.path.isdir(_LIBRARY_DIR):
        return None, f"library directory not found: {_LIBRARY_DIR}"

    json_files = sorted(
        f for f in os.listdir(_LIBRARY_DIR)
        if f.lower().endswith(".json") and os.path.isfile(os.path.join(_LIBRARY_DIR, f))
    )

    if not json_files:
        return None, "no .json files found in library/"

    return os.path.join(_LIBRARY_DIR, json_files[0]), None


def _load_json(file_path):
    """
    Load and return JSON data from file_path.
    Returns (data, filename, None) on success, (None, None, error_string) on failure.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, os.path.basename(file_path), None
    except (json.JSONDecodeError, OSError) as e:
        return None, None, str(e)


def build(parent):
    """
    Build the jsondisplayer widget inside the given parent frame.
    This is the shell mount interface for this sidewindow.

    Always succeeds (never raises). On load failure, shows an error
    message inside the parent instead of JSON content.
    """
    # Find and load JSON
    file_path, find_error = _find_first_json()

    if find_error:
        _build_error(parent, find_error)
        return

    data, filename, load_error = _load_json(file_path)

    if load_error:
        _build_error(parent, f"failed to load {os.path.basename(file_path)}:\n{load_error}")
        return

    # Build display
    _build_display(parent, filename, data)


def _build_display(parent, filename, data):
    """Build the normal JSON display view."""
    # Filename header
    header = tk.Label(
        parent, text=filename,
        font=("TkDefaultFont", 9, "bold"),
        anchor=tk.W, padx=6, pady=4,
    )
    header.pack(fill=tk.X)

    # Separator
    sep = tk.Frame(parent, height=1, bg="#555555")
    sep.pack(fill=tk.X, padx=4)

    # Scrollable JSON text
    text_frame = tk.Frame(parent)
    text_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
    text_widget = tk.Text(
        text_frame,
        wrap=tk.WORD,
        font=("TkFixedFont", 9),
        yscrollcommand=scrollbar.set,
        padx=4, pady=4,
        relief=tk.FLAT,
        borderwidth=0,
    )
    scrollbar.configure(command=text_widget.yview)

    text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Insert formatted JSON
    formatted = json.dumps(data, indent=2, ensure_ascii=False)
    text_widget.insert(tk.END, formatted)
    text_widget.configure(state=tk.DISABLED)


def _build_error(parent, message):
    """Build an error display when JSON loading fails."""
    tk.Label(
        parent, text="jsondisplayer",
        font=("TkDefaultFont", 9, "bold"),
        fg="#e05050", anchor=tk.W, padx=6, pady=4,
    ).pack(fill=tk.X)

    tk.Label(
        parent, text=message,
        font=("TkFixedFont", 8),
        fg="#c08080", anchor=tk.NW,
        wraplength=220, justify=tk.LEFT,
        padx=6, pady=4,
    ).pack(fill=tk.BOTH, expand=True)

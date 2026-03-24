"""
guichi.py — Guichi Shell Loader
Entry point and orchestrator.
Wires discovery, registry, loader, and reporter.

Action functions are the backend API — no print, no input.
GUI and CLI are separate surfaces that call these functions.
"""

import os
import sys
import json

# Resolve paths
SHELL_DIR = os.path.dirname(os.path.abspath(__file__))
GUICHI_FILES = os.path.join(SHELL_DIR, "guichi_files")
CONFIG_DIR = os.path.join(GUICHI_FILES, "config")
STATE_DIR = os.path.join(GUICHI_FILES, "state")
LOGS_DIR = os.path.join(GUICHI_FILES, "logs")  # placeholder — not yet used by any code path

CONFIG_PATH = os.path.join(CONFIG_DIR, "shell_config.json")
REGISTRY_PATH = os.path.join(STATE_DIR, "registry.json")

# Ensure guichi_files is importable
if GUICHI_FILES not in sys.path:
    sys.path.insert(0, GUICHI_FILES)

import shell_discovery
import shell_registry
import shell_loader
import shell_reporter


# ── Config ──────────────────────────────────────────────────

def load_config():
    """Load shell config from disk. Returns dict with defaults applied."""
    defaults = {
        "dev_mode": False,
        "default_scan_style": 1,
        "suppress_dev_warnings": False,
        "last_selected_root": None,
        "known_roots": [],
    }
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def save_config(config):
    """Save config to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


# ── Core actions (pure backend — no print, no input) ────────

def action_discover(config, registry, root=None, scan_style=None):
    """
    Run discovery on a root directory, merge findings into registry.
    Returns (discovery_result, merge_actions).
    """
    if root is None:
        root = config.get("last_selected_root") or SHELL_DIR
    if scan_style is None:
        scan_style = config.get("default_scan_style", 1)

    dev_mode = config.get("dev_mode", False)
    suppress_dev_warnings = config.get("suppress_dev_warnings", False)

    result = shell_discovery.discover(
        root, scan_style, dev_mode=dev_mode,
        suppress_dev_warnings=suppress_dev_warnings,
    )

    if result["scan_errors"]:
        return result, []

    merge_actions = shell_registry.merge_findings(registry, result["findings"])
    shell_registry.save_registry(registry, REGISTRY_PATH)

    # Update config with this root
    config["last_selected_root"] = root
    known = config.get("known_roots", [])
    if root not in known:
        known.append(root)
        config["known_roots"] = known
    save_config(config)

    return result, merge_actions


def action_rebuild(config, registry):
    """
    Rebuild registry by re-walking all known source paths.
    Returns list of rebuild actions.
    """
    dev_mode = config.get("dev_mode", False)
    suppress_dev_warnings = config.get("suppress_dev_warnings", False)

    def rescan_folder(folder_path, dm):
        return shell_discovery._scan_folder(
            folder_path, dev_mode=dm,
            suppress_dev_warnings=suppress_dev_warnings,
        )

    actions = shell_registry.rebuild_registry(registry, rescan_folder, dev_mode=dev_mode)
    shell_registry.save_registry(registry, REGISTRY_PATH)
    return actions


def action_list(registry, include_hidden=False, problems_only=False):
    """Return filtered list of known packs from the registry."""
    return shell_registry.list_packs(
        registry, include_hidden=include_hidden, problems_only=problems_only
    )


def action_load_page(config, registry, pack_id, page_id, source_path=None, instantiate=False):
    """
    Load a specific page from a pack.
    Returns structured load result dict. Always returns a dict, never None.
    On lookup failure, returns a dict with status="failed" and a message.
    """
    matches = shell_registry.lookup_pack(registry, pack_id, source_path=source_path)
    if not matches:
        return {"status": "failed", "message": f"pack not found: {pack_id}"}

    if len(matches) > 1 and source_path is None:
        paths = [m.get("source_path") for m in matches]
        return {
            "status": "failed",
            "message": f"multiple entries for pack_id '{pack_id}' — specify source_path",
            "ambiguous_paths": paths,
        }

    pack_entry = matches[0]

    page_entry = None
    for p in pack_entry.get("pages", []):
        if p.get("page_id") == page_id:
            page_entry = p
            break

    if page_entry is None:
        return {"status": "failed", "message": f"page not found: {page_id} in pack {pack_id}"}

    dev_mode = config.get("dev_mode", False)
    suppress_dev_warnings = config.get("suppress_dev_warnings", False)
    return shell_loader.load_page(
        pack_entry, page_entry, dev_mode=dev_mode,
        suppress_dev_warnings=suppress_dev_warnings,
        instantiate=instantiate,
    )


def action_apply_remove(registry, pack_id, source_path, choice):
    """
    Apply a remove/hide action to a pack entry.
    choice: 1 = remove from list, 2 = remove and forget state, 3 = hide
    Returns action description string or None if entry not found.
    """
    result = shell_registry.apply_remove_action(registry, pack_id, source_path, choice)
    if result:
        shell_registry.save_registry(registry, REGISTRY_PATH)
    return result


def action_unhide(registry, pack_id, source_path):
    """
    Unhide a previously hidden pack entry.
    Returns True if unhidden, False if not found.
    """
    found = shell_registry.unhide_pack(registry, pack_id, source_path)
    if found:
        shell_registry.save_registry(registry, REGISTRY_PATH)
    return found


def action_report(registry, problems_only=False, include_hidden=False):
    """Generate and return the full discovery/issues report string."""
    return shell_reporter.report_all(
        registry,
        problems_only=problems_only,
        include_hidden=include_hidden,
    )


# ── CLI surface ─────────────────────────────────────────────

USAGE = """\
guichi shell commands:
  discover [root] [style]  — scan for packs (style: 1=pattern, 2=broad)
  rebuild                  — refresh registry from all known source paths
  list [--all] [--problems] — list known packs
  load <pack_id> <page_id> [source_path] — load a page
  remove <pack_id> <source_path> <choice> — remove(1)/forget(2)/hide(3)
  unhide <pack_id> <source_path> — unhide a hidden pack
  report [--all] [--problems] — print discovery report
  config                   — show current config
  help                     — show this message

launch modes:
  python guichi.py             — launch GUI (default)
  python guichi.py --cli       — show CLI help
  python guichi.py <command>   — run single CLI command
"""


def cli_main(args):
    """CLI entry — prints results from action functions."""
    config = load_config()
    registry = shell_registry.load_registry(REGISTRY_PATH)

    if not args:
        print(USAGE)
        return

    cmd = args[0]

    if cmd == "help":
        print(USAGE)

    elif cmd == "discover":
        root = args[1] if len(args) > 1 else None
        style = int(args[2]) if len(args) > 2 else None
        result, merge_actions = action_discover(config, registry, root=root, scan_style=style)
        if result["scan_errors"]:
            for err in result["scan_errors"]:
                print(f"  scan error: {err}")
        else:
            print(f"  found {len(result['findings'])} pack(s), skipped {len(result['skipped'])} folder(s)")
            for ma in merge_actions:
                print(f"  {ma['action']}: {ma.get('pack_id', '?')} at {ma.get('source_path', '?')}")

    elif cmd == "rebuild":
        actions = action_rebuild(config, registry)
        for a in actions:
            print(f"  {a['action']}: {a.get('pack_id', '?')} at {a.get('source_path', '?')}")
        print(f"  rebuild complete: {len(actions)} action(s)")

    elif cmd == "list":
        include_hidden = "--all" in args
        problems_only = "--problems" in args
        packs = action_list(registry, include_hidden=include_hidden, problems_only=problems_only)
        if not packs:
            print("(no packs)")
        else:
            for entry in packs:
                pid = entry.get("pack_id") or "(no id)"
                suffix = entry.get("display_suffix", "")
                status = entry.get("status", "?")
                hidden_tag = " [HIDDEN]" if entry.get("hidden") else ""
                page_count = len(entry.get("pages", []))
                print(f"  {pid}{suffix}  [{status}]{hidden_tag}  ({page_count} page(s))  — {entry.get('source_path', '?')}")

    elif cmd == "load":
        if len(args) < 3:
            print("usage: load <pack_id> <page_id> [source_path]")
            return
        pack_id = args[1]
        page_id = args[2]
        source_path = args[3] if len(args) > 3 else None
        result = action_load_page(config, registry, pack_id, page_id, source_path=source_path)
        if "page_id" in result:
            # Full loader result — use reporter
            print(shell_reporter.report_load_result(result))
        else:
            # Lookup failure — result has status + message only
            print(f"  {result.get('message', 'unknown error')}")

    elif cmd == "remove":
        if len(args) < 4:
            print("usage: remove <pack_id> <source_path> <choice>")
            print("  choices: 1=remove from list, 2=remove and forget, 3=hide")
            return
        choice = int(args[3])
        result = action_apply_remove(registry, args[1], args[2], choice)
        print(f"  {result}" if result else "  entry not found")

    elif cmd == "unhide":
        if len(args) < 3:
            print("usage: unhide <pack_id> <source_path>")
            return
        found = action_unhide(registry, args[1], args[2])
        print(f"  unhidden" if found else "  entry not found")

    elif cmd == "report":
        include_hidden = "--all" in args
        problems_only = "--problems" in args
        print(action_report(registry, problems_only=problems_only, include_hidden=include_hidden))

    elif cmd == "config":
        print(json.dumps(config, indent=2))

    else:
        print(f"unknown command: {cmd}")
        print(USAGE)


# ── Entry point ─────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    # Any args = CLI mode
    if args:
        if args[0] == "--cli":
            cli_main(args[1:])
        else:
            cli_main(args)
        return

    # Default: launch GUI
    try:
        import shell_gui
        shell_gui.launch()
    except ImportError as e:
        print(f"GUI not available: {e}")
        print("run with --cli or a command for command-line mode")
        print(USAGE)
    except Exception as e:
        print(f"GUI launch failed: {e}")
        print("run with --cli for command-line mode")


if __name__ == "__main__":
    main()

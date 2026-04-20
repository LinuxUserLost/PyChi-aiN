"""
shell_registry.py
Persistent index of known packs/pages.
Registry is cache only — disk files are canon.
Supports merge, rebuild, duplicate handling, hide/remove distinction.
"""

import os
import json
import copy
from datetime import datetime, timezone


REGISTRY_VERSION = 1

REMOVE_CHOICES = {
    1: "remove from shell list only",
    2: "remove and forget saved shell state/history",
    3: "hide instead",
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _make_runtime_key(pack_id, source_path):
    """
    Runtime identity for a pack entry: pack_id + source_path.
    Used for registry-internal matching only. The GUI sidebar uses
    a separate ID scheme (see shell_gui._make_sidebar_pack_id).
    """
    return f"{pack_id}::{source_path}"


def _empty_registry():
    return {"version": REGISTRY_VERSION, "packs": []}


def load_registry(registry_path):
    """Load registry from disk. Returns registry dict."""
    if not os.path.isfile(registry_path):
        return _empty_registry()
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "packs" not in data:
            return _empty_registry()
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_registry()


def save_registry(registry, registry_path):
    """Save registry to disk."""
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def _entry_runtime_key(entry):
    """Extract runtime key from a registry pack entry."""
    return _make_runtime_key(entry.get("pack_id"), entry.get("source_path"))


def _find_pack_index(registry, pack_id, source_path):
    """Find index of a pack entry by runtime key (pack_id + source_path). Returns index or -1."""
    target_key = _make_runtime_key(pack_id, source_path)
    for i, entry in enumerate(registry["packs"]):
        if _entry_runtime_key(entry) == target_key:
            return i
    return -1


def _assign_dupe_suffixes(registry):
    """
    Scan all packs and assign display_suffix for duplicates.
    Duplicates = same pack_id from different source_paths.
    """
    id_groups = {}
    for entry in registry["packs"]:
        pid = entry.get("pack_id") or "(no_id)"
        id_groups.setdefault(pid, []).append(entry)

    for pid, entries in id_groups.items():
        if len(entries) <= 1:
            for e in entries:
                e["display_suffix"] = ""
        else:
            for idx, e in enumerate(entries):
                e["display_suffix"] = f"_dupe{idx + 1}"


def _pack_entry_from_finding(finding):
    """Convert a discovery finding into a registry pack entry."""
    page_entries = []
    for pf in finding.get("pages", []):
        pd = pf.get("data", {})
        page_entry = {
            "page_id": pd.get("page_id"),
            "page_name": pd.get("page_name"),
            "page_path": pd.get("page_path"),
            "page_class": pd.get("page_class"),
            "page_title": pd.get("page_title"),
            "page_folder_path": pd.get("page_folder_path"),
            "page_config_path": pd.get("page_config_path"),
            "status": pf.get("status", "ok"),
            "warnings": pf.get("warnings", []),
            "errors": pf.get("errors", []),
        }
        page_entries.append(page_entry)

    pack_status = "ok"
    if finding.get("errors"):
        pack_status = "warning"
    # Check if any page has errors
    for pe in page_entries:
        if pe.get("errors"):
            pack_status = "warning"

    return {
        "pack_id": finding.get("pack_id"),
        "source_path": finding.get("source_path"),
        "folder_name": finding.get("folder_name"),
        "display_suffix": "",
        "status": pack_status,
        "hidden": False,
        "pages": page_entries,
        "warnings": finding.get("warnings", []),
        "errors": finding.get("errors", []),
        "last_scanned": finding.get("scan_time") or _now_iso(),
    }


def merge_findings(registry, findings_list):
    """
    Merge discovery findings into the registry.
    - Adds new packs
    - Updates existing packs (matched by pack_id + source_path)
    - Never wipes entries from other roots/sources
    - Preserves hidden state on update
    Returns list of merge actions taken (for reporting).
    """
    actions = []

    for finding in findings_list:
        pack_id = finding.get("pack_id")
        source_path = finding.get("source_path")
        new_entry = _pack_entry_from_finding(finding)

        if not pack_id:
            # No pack_id — store it as a problem entry, but deduplicate by source_path
            existing_noid = next(
                (i for i, e in enumerate(registry["packs"])
                 if not e.get("pack_id") and e.get("source_path") == source_path),
                -1,
            )
            if existing_noid >= 0:
                registry["packs"][existing_noid] = new_entry
                actions.append({"action": "updated_no_id", "source_path": source_path})
            else:
                registry["packs"].append(new_entry)
                actions.append({"action": "added_no_id", "source_path": source_path})
            continue

        existing_idx = _find_pack_index(registry, pack_id, source_path)

        if existing_idx >= 0:
            # Update existing — preserve hidden flag
            was_hidden = registry["packs"][existing_idx].get("hidden", False)
            new_entry["hidden"] = was_hidden
            registry["packs"][existing_idx] = new_entry
            actions.append({
                "action": "updated",
                "pack_id": pack_id,
                "source_path": source_path,
            })
        else:
            registry["packs"].append(new_entry)
            actions.append({
                "action": "added",
                "pack_id": pack_id,
                "source_path": source_path,
            })

    _assign_dupe_suffixes(registry)
    return actions


def rebuild_registry(registry, discover_fn, dev_mode=False):
    """
    Re-walk all previously known source_paths.
    - Uses discover_fn to re-validate each pack folder directly.
    - Updates statuses.
    - Marks truly-gone items as unavailable (does not delete them).

    discover_fn signature: discover_fn(folder_path, dev_mode) -> finding_or_none
        This should call shell_discovery._scan_folder on the folder.

    Returns list of rebuild actions.
    """
    actions = []

    for entry in registry["packs"]:
        source_path = entry.get("source_path")
        if not source_path:
            continue

        if not os.path.isdir(source_path):
            if entry["status"] != "unavailable":
                entry["status"] = "unavailable"
                entry["warnings"].append(
                    f"source path no longer exists: {source_path} (marked unavailable at {_now_iso()})"
                )
                actions.append({
                    "action": "marked_unavailable",
                    "pack_id": entry.get("pack_id"),
                    "source_path": source_path,
                })
            continue

        finding = discover_fn(source_path, dev_mode)
        if finding is None:
            # Folder exists but no longer has discovery files
            if entry["status"] != "unavailable":
                entry["status"] = "unavailable"
                entry["warnings"].append(
                    f"discovery files missing at {source_path} (marked unavailable at {_now_iso()})"
                )
                actions.append({
                    "action": "marked_unavailable",
                    "pack_id": entry.get("pack_id"),
                    "source_path": source_path,
                })
        else:
            # Refresh data from disk
            refreshed = _pack_entry_from_finding(finding)
            was_hidden = entry.get("hidden", False)
            refreshed["hidden"] = was_hidden
            entry.update(refreshed)
            actions.append({
                "action": "refreshed",
                "pack_id": entry.get("pack_id"),
                "source_path": source_path,
            })

    _assign_dupe_suffixes(registry)
    return actions


def get_remove_choices():
    """
    Return the three-choice remove/hide options.
    Currently not called by the GUI (which uses _RemoveDialog with its own labels)
    or CLI (which takes choice as a positional arg). Kept as the canonical
    description source for programmatic callers.
    """
    return copy.deepcopy(REMOVE_CHOICES)


def apply_remove_action(registry, pack_id, source_path, choice):
    """
    Apply a remove/hide action to a pack entry.
    choice: 1 = remove from list, 2 = remove and forget state, 3 = hide
    Returns action description string or None if entry not found.
    """
    idx = _find_pack_index(registry, pack_id, source_path)
    if idx < 0:
        return None

    if choice == 1:
        registry["packs"].pop(idx)
        _assign_dupe_suffixes(registry)
        return f"removed from list: {pack_id} at {source_path}"

    elif choice == 2:
        registry["packs"].pop(idx)
        _assign_dupe_suffixes(registry)
        # NOTE: no per-pack state files exist yet in current implementation.
        # When pack state persistence is added, delete those files here.
        return f"removed and forgot state: {pack_id} at {source_path} (no per-pack state files to clear yet)"

    elif choice == 3:
        registry["packs"][idx]["hidden"] = True
        return f"hidden: {pack_id} at {source_path}"

    return None


def unhide_pack(registry, pack_id, source_path):
    """Unhide a previously hidden pack."""
    idx = _find_pack_index(registry, pack_id, source_path)
    if idx < 0:
        return False
    registry["packs"][idx]["hidden"] = False
    return True


def list_packs(registry, include_hidden=False, problems_only=False):
    """
    Return filtered list of pack entries.
    Each returned entry is a copy.
    """
    results = []
    for entry in registry["packs"]:
        if not include_hidden and entry.get("hidden", False):
            continue
        if problems_only:
            has_problem = (
                entry.get("status") in ("warning", "unavailable")
                or entry.get("errors")
                or any(p.get("errors") for p in entry.get("pages", []))
            )
            if not has_problem:
                continue
        results.append(copy.deepcopy(entry))
    return results


def lookup_pack(registry, pack_id, source_path=None):
    """
    Look up a pack by pack_id. If source_path given, match by runtime key.
    If source_path is None and multiple exist, return all matches.
    Returns list of matching entries (copies).
    """
    matches = []
    if source_path:
        target_key = _make_runtime_key(pack_id, source_path)
        for entry in registry["packs"]:
            if _entry_runtime_key(entry) == target_key:
                matches.append(copy.deepcopy(entry))
    else:
        for entry in registry["packs"]:
            if entry.get("pack_id") == pack_id:
                matches.append(copy.deepcopy(entry))
    return matches

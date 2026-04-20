"""
shell_reporter.py
Generate copyable discovery/issue reports from registry state.
Plain text output, no rich formatting dependencies.
"""

from datetime import datetime, timezone


SEPARATOR = "─" * 60


def _status_marker(status):
    if status == "ok":
        return "[OK]"
    elif status == "warning":
        return "[WARN]"
    elif status == "unavailable":
        return "[UNAVAIL]"
    else:
        return f"[{status.upper()}]" if status else "[?]"


def report_pack(pack_entry, scan_mode=None):
    """
    Generate a report block for a single pack entry.
    Returns a list of text lines.
    """
    lines = []
    pack_id = pack_entry.get("pack_id") or "(no pack_id)"
    source_path = pack_entry.get("source_path") or "(no source_path)"
    suffix = pack_entry.get("display_suffix", "")
    status = pack_entry.get("status", "?")
    hidden = pack_entry.get("hidden", False)

    display_name = f"{pack_id}{suffix}"
    lines.append(f"Pack: {display_name}  {_status_marker(status)}")
    if hidden:
        lines.append("  [HIDDEN]")
    lines.append(f"  source_path: {source_path}")
    lines.append(f"  last_scanned: {pack_entry.get('last_scanned', '?')}")
    if scan_mode:
        lines.append(f"  scan_mode: style {scan_mode}")

    # Pack-level warnings/errors
    for w in pack_entry.get("warnings", []):
        lines.append(f"  warning: {w}")
    for e in pack_entry.get("errors", []):
        lines.append(f"  ERROR: {e}")

    # Pages
    pages = pack_entry.get("pages", [])
    if not pages:
        lines.append("  pages: (none)")
    else:
        for page in pages:
            page_id = page.get("page_id") or "(no page_id)"
            page_name = page.get("page_name") or "(no page_name)"
            page_status = page.get("status", "?")
            lines.append(
                f"  page: {page_id} — {page_name}  {_status_marker(page_status)}"
            )
            if not page.get("page_path"):
                lines.append(f"    missing: page_path")
            if not page.get("page_class"):
                lines.append(f"    missing: page_class")
            for w in page.get("warnings", []):
                lines.append(f"    warning: {w}")
            for e in page.get("errors", []):
                lines.append(f"    ERROR: {e}")

            # Suggest likely fix zone
            fixes = _suggest_fixes_page(page, source_path)
            for fix in fixes:
                lines.append(f"    fix: {fix}")

    # Pack-level fix suggestions
    pack_fixes = _suggest_fixes_pack(pack_entry)
    for fix in pack_fixes:
        lines.append(f"  fix: {fix}")

    return lines


def _suggest_fixes_pack(pack_entry):
    """Suggest likely fix zones for pack-level issues."""
    fixes = []
    source = pack_entry.get("source_path", "?")
    for err in pack_entry.get("errors", []):
        if "module_manifest.json" in err:
            fixes.append(f"create or fix module_manifest.json in {source}")
        elif "pages.json" in err:
            fixes.append(f"create or fix pages.json in {source}")
        elif "pack_id" in err:
            fixes.append(f"add pack_id to module_manifest.json in {source}")
    return fixes


def _suggest_fixes_page(page_entry, source_path):
    """Suggest likely fix zones for page-level issues."""
    fixes = []
    for err in page_entry.get("errors", []):
        if "page_id" in err:
            fixes.append("add page_id to this page entry in pages.json")
        elif "page_name" in err:
            fixes.append("add page_name to this page entry in pages.json")
        elif "page_path" in err:
            fixes.append("add page_path to this page entry in pages.json")
        elif "page_class" in err:
            fixes.append("add page_class to this page entry in pages.json")
    return fixes


def report_all(registry, scan_mode=None, problems_only=False, include_hidden=False):
    """
    Generate a full report across all packs in the registry.
    Returns a single string (copyable).
    """
    lines = []
    lines.append("Guichi Shell — Discovery Report")
    lines.append(f"generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(SEPARATOR)

    packs = registry.get("packs", [])
    if not packs:
        lines.append("(no packs in registry)")
        return "\n".join(lines)

    shown = 0
    for entry in packs:
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
        lines.extend(report_pack(entry, scan_mode=scan_mode))
        lines.append(SEPARATOR)
        shown += 1

    lines.append(f"total packs shown: {shown} / {len(packs)}")
    return "\n".join(lines)


def report_load_result(load_result):
    """
    Generate a report block for a load attempt result from shell_loader.
    Also handles lookup-failure dicts from action_load_page (which have
    status and message but no page_id/page_name).
    Returns a string.
    """
    lines = []
    status = load_result.get("status", "?")

    # Lookup-failure dicts from action_load_page lack page_id
    if "page_id" not in load_result:
        lines.append(f"Load: {_status_marker(status)}")
        if load_result.get("message"):
            lines.append(f"  {load_result['message']}")
        return "\n".join(lines)

    page_id = load_result.get("page_id") or "(no page_id)"
    page_name = load_result.get("page_name") or ""

    lines.append(f"Load: {page_id} — {page_name}  {_status_marker(status)}")
    if load_result.get("message"):
        lines.append(f"  message: {load_result['message']}")
    if load_result.get("error_detail"):
        lines.append(f"  detail:")
        for detail_line in load_result["error_detail"].splitlines():
            lines.append(f"    {detail_line}")
    return "\n".join(lines)

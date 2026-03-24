"""
shell_discovery.py
Scan a chosen root for pack/page folders. Validate discovery files.
Return structured findings. Does not touch the registry.
"""

import os
import json
import re
import uuid
from datetime import datetime, timezone


# Naming patterns for style-1 (pattern-only) scan
PACK_PATTERNS = [
    re.compile(r"^page_"),
    re.compile(r"^pagepack_"),
]

# Reference constants — REQUIRED_DISCOVERY_FILES and REQUIRED_PACK_FIELDS
# define canonical names but are not directly referenced in code (the checks
# are inline in _scan_folder and _validate_manifest). Kept as the single
# source of truth for what discovery requires.
# REQUIRED_PAGE_FIELDS and OPTIONAL_PAGE_FIELDS are actively used by
# _validate_pages.
REQUIRED_DISCOVERY_FILES = ("module_manifest.json", "pages.json")
REQUIRED_PACK_FIELDS = ("pack_id",)
REQUIRED_PAGE_FIELDS = ("page_id", "page_name", "page_path", "page_class")
OPTIONAL_PAGE_FIELDS = ("page_title", "page_folder_path", "page_config_path")


def _matches_pack_pattern(folder_name):
    """Check if a folder name matches any known pack/page naming pattern."""
    for pat in PACK_PATTERNS:
        if pat.match(folder_name):
            return True
    return False


def _read_json_file(filepath):
    """Read and parse a JSON file. Returns (data, error_string)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, None
    except FileNotFoundError:
        return None, f"file not found: {filepath}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {filepath}: {e}"
    except Exception as e:
        return None, f"error reading {filepath}: {e}"


def _validate_manifest(manifest_data, folder_path, dev_mode=False, suppress_dev_warnings=False):
    """
    Validate module_manifest.json contents.
    Returns (pack_info, warnings, errors).
    """
    warnings = []
    errors = []
    pack_info = {"source_path": folder_path}

    if not isinstance(manifest_data, dict):
        errors.append("module_manifest.json root must be a JSON object")
        return pack_info, warnings, errors

    pack_id = manifest_data.get("pack_id")
    if not pack_id:
        if dev_mode:
            generated_id = f"dev_{uuid.uuid4().hex[:8]}"
            pack_info["pack_id"] = generated_id
            if not suppress_dev_warnings:
                warnings.append(f"missing pack_id — dev-mode generated: {generated_id}")
        else:
            errors.append("missing required field: pack_id")
            pack_info["pack_id"] = None
    else:
        pack_info["pack_id"] = pack_id

    return pack_info, warnings, errors


def _validate_pages(pages_data, folder_path, dev_mode=False, suppress_dev_warnings=False):
    """
    Validate pages.json contents.
    Returns (page_list, warnings, errors).
    """
    warnings = []
    errors = []
    page_list = []

    if not isinstance(pages_data, dict):
        errors.append("pages.json root must be a JSON object")
        return page_list, warnings, errors

    raw_pages = pages_data.get("pages")
    if not isinstance(raw_pages, list):
        errors.append("pages.json must contain a 'pages' array")
        return page_list, warnings, errors

    for i, raw_page in enumerate(raw_pages):
        page_entry = {}
        page_warnings = []
        page_errors = []

        if not isinstance(raw_page, dict):
            page_errors.append(f"page entry [{i}] is not a JSON object")
            page_list.append({
                "raw_index": i,
                "data": {},
                "warnings": page_warnings,
                "errors": page_errors,
                "status": "error",
            })
            continue

        # Check required fields
        for field in REQUIRED_PAGE_FIELDS:
            val = raw_page.get(field)
            if val:
                page_entry[field] = val
            else:
                if dev_mode and field in ("page_id",):
                    generated = f"dev_{uuid.uuid4().hex[:8]}"
                    page_entry[field] = generated
                    if not suppress_dev_warnings:
                        page_warnings.append(
                            f"missing required field '{field}' — dev-mode generated: {generated}"
                        )
                else:
                    page_entry[field] = None
                    page_errors.append(f"missing required field: {field}")

        # Check optional fields
        for field in OPTIONAL_PAGE_FIELDS:
            val = raw_page.get(field)
            if val:
                page_entry[field] = val
            else:
                page_warnings.append(f"optional field missing: {field}")

        status = "ok"
        if page_errors:
            status = "warning"  # still attempt-loadable in regular mode
        page_list.append({
            "raw_index": i,
            "data": page_entry,
            "warnings": page_warnings,
            "errors": page_errors,
            "status": status,
        })

    return page_list, warnings, errors


def _scan_folder(folder_path, dev_mode=False, suppress_dev_warnings=False):
    """
    Inspect a single candidate folder for discovery files.
    Returns a pack finding dict or None if not a valid pack folder.
    """
    manifest_path = os.path.join(folder_path, "module_manifest.json")
    pages_path = os.path.join(folder_path, "pages.json")

    # Check both discovery files exist
    has_manifest = os.path.isfile(manifest_path)
    has_pages = os.path.isfile(pages_path)

    if not has_manifest and not has_pages:
        return None  # not a pack folder at all, skip silently

    finding = {
        "source_path": folder_path,
        "folder_name": os.path.basename(folder_path),
        "pack_id": None,
        "pages": [],
        "warnings": [],
        "errors": [],
        "scan_time": datetime.now(timezone.utc).isoformat(),
    }

    if not has_manifest:
        if dev_mode:
            if not suppress_dev_warnings:
                finding["warnings"].append(
                    "module_manifest.json missing — dev-mode best-effort"
                )
        else:
            finding["errors"].append("missing required file: module_manifest.json")
    else:
        manifest_data, read_err = _read_json_file(manifest_path)
        if read_err:
            finding["errors"].append(read_err)
        else:
            pack_info, m_warnings, m_errors = _validate_manifest(
                manifest_data, folder_path, dev_mode=dev_mode,
                suppress_dev_warnings=suppress_dev_warnings,
            )
            finding["pack_id"] = pack_info.get("pack_id")
            finding["warnings"].extend(m_warnings)
            finding["errors"].extend(m_errors)

    if not has_pages:
        if dev_mode:
            if not suppress_dev_warnings:
                finding["warnings"].append("pages.json missing — dev-mode best-effort")
        else:
            finding["errors"].append("missing required file: pages.json")
    else:
        pages_data, read_err = _read_json_file(pages_path)
        if read_err:
            finding["errors"].append(read_err)
        else:
            page_list, p_warnings, p_errors = _validate_pages(
                pages_data, folder_path, dev_mode=dev_mode,
                suppress_dev_warnings=suppress_dev_warnings,
            )
            finding["pages"] = page_list
            finding["warnings"].extend(p_warnings)
            finding["errors"].extend(p_errors)

    return finding


def discover(root_path, scan_style, dev_mode=False, suppress_dev_warnings=False):
    """
    Scan a root directory for pack folders.

    scan_style 1: only check child folders matching page_*/pagepack_* patterns
    scan_style 2: check all direct child folders

    Returns:
        {
            "root": str,
            "scan_style": int,
            "dev_mode": bool,
            "findings": [pack_finding, ...],
            "skipped": [folder_name, ...],
            "scan_errors": [str, ...],
        }
    """
    result = {
        "root": root_path,
        "scan_style": scan_style,
        "dev_mode": dev_mode,
        "findings": [],
        "skipped": [],
        "scan_errors": [],
    }

    if not os.path.isdir(root_path):
        result["scan_errors"].append(f"root path is not a directory: {root_path}")
        return result

    try:
        children = sorted(os.listdir(root_path))
    except OSError as e:
        result["scan_errors"].append(f"cannot list root directory: {e}")
        return result

    # Never scan guichi_files as a pack candidate
    skip_names = {"guichi_files", "__pycache__"}

    for child_name in children:
        if child_name in skip_names:
            continue

        child_path = os.path.join(root_path, child_name)
        if not os.path.isdir(child_path):
            continue

        # Style 1: pattern match only
        if scan_style == 1 and not _matches_pack_pattern(child_name):
            result["skipped"].append(child_name)
            continue

        finding = _scan_folder(
            child_path, dev_mode=dev_mode,
            suppress_dev_warnings=suppress_dev_warnings,
        )
        if finding is None:
            result["skipped"].append(child_name)
        else:
            result["findings"].append(finding)

    return result

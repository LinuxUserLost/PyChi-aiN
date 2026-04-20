"""
shell_loader.py
Load a page class from a registry entry.
Returns structured load results — never crashes the shell.
"""

import os
import sys
import importlib
import importlib.util
import traceback


# Load result statuses
LOAD_OK = "ok"
LOAD_WARNING = "warning"
LOAD_FAILED = "failed"


def _make_load_result(status, page_entry, message=None, page_class=None,
                      page_instance=None, error_detail=None):
    """Build a structured load result dict."""
    return {
        "status": status,
        "page_id": page_entry.get("page_id"),
        "page_name": page_entry.get("page_name"),
        "page_path": page_entry.get("page_path"),
        "page_class_name": page_entry.get("page_class"),
        "message": message,
        "page_class": page_class,
        "page_instance": page_instance,
        "error_detail": error_detail,
    }


def load_page(pack_entry, page_entry, dev_mode=False, suppress_dev_warnings=False,
              instantiate=False):
    """
    Attempt to load a page class from a pack's registry entry.

    Args:
        pack_entry: registry pack dict with source_path
        page_entry: registry page dict with page_path, page_class, etc.
        dev_mode: if True, allow best-effort loading with missing fields
        suppress_dev_warnings: if True, suppress dev-mode-specific warnings
        instantiate: if True, also instantiate the class (no-arg constructor)

    Returns:
        Structured load result dict with:
        - status: "ok", "warning", or "failed"
        - page_class: the loaded class (or None on failure)
        - page_instance: instantiated object if requested (or None)
        - message: human-readable status message
        - error_detail: traceback string on failure
    """
    source_path = pack_entry.get("source_path")
    page_path_rel = page_entry.get("page_path")
    page_class_name = page_entry.get("page_class")

    warnings = []

    # --- Check required fields ---
    missing_required = []
    if not page_path_rel:
        missing_required.append("page_path")
    if not page_class_name:
        missing_required.append("page_class")
    if not page_entry.get("page_id"):
        missing_required.append("page_id")
    if not page_entry.get("page_name"):
        missing_required.append("page_name")

    if missing_required:
        field_list = ", ".join(missing_required)
        if not dev_mode:
            # Regular mode: warn but still attempt load if page_path and page_class are present.
            # This matches the approved rule: missing required page fields may still
            # attempt load with a clear warning.
            warnings.append(f"missing required fields: {field_list}")
            if not page_path_rel or not page_class_name:
                return _make_load_result(
                    LOAD_FAILED,
                    page_entry,
                    message=f"cannot load — missing critical fields: {field_list}",
                    error_detail=f"page_path={page_path_rel}, page_class={page_class_name}",
                )
        else:
            # Dev mode: may go further with repair/best-effort, but still cannot
            # load without page_path and page_class.
            if not suppress_dev_warnings:
                warnings.append(
                    f"dev-mode: proceeding with missing fields: {field_list}"
                )
            if not page_path_rel or not page_class_name:
                return _make_load_result(
                    LOAD_FAILED,
                    page_entry,
                    message=f"even dev-mode cannot load without page_path and page_class",
                    error_detail=f"page_path={page_path_rel}, page_class={page_class_name}",
                )

    # --- Resolve file path ---
    if not source_path:
        return _make_load_result(
            LOAD_FAILED,
            page_entry,
            message="no source_path on pack entry",
            error_detail="pack_entry is missing source_path",
        )

    module_file = os.path.join(source_path, page_path_rel)

    if not os.path.isfile(module_file):
        return _make_load_result(
            LOAD_FAILED,
            page_entry,
            message=f"page file not found: {module_file}",
            error_detail=f"resolved path does not exist: {module_file}",
        )

    # --- Import module ---
    module_name = os.path.splitext(os.path.basename(page_path_rel))[0]
    # Prefix to avoid collisions with other modules
    safe_module_name = f"guichi_loaded.{pack_entry.get('pack_id', 'unknown')}.{module_name}"

    # Temporarily add source_path to sys.path for the import
    path_added = False
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
        path_added = True

    try:
        spec = importlib.util.spec_from_file_location(safe_module_name, module_file)
        if spec is None:
            return _make_load_result(
                LOAD_FAILED,
                page_entry,
                message=f"could not build import spec for: {module_file}",
                error_detail="importlib.util.spec_from_file_location returned None",
            )

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    except Exception:
        tb = traceback.format_exc()
        return _make_load_result(
            LOAD_FAILED,
            page_entry,
            message=f"import error in {page_path_rel}",
            error_detail=tb,
        )
    finally:
        if path_added:
            try:
                sys.path.remove(source_path)
            except ValueError:
                pass

    # --- Retrieve class ---
    cls = getattr(module, page_class_name, None)
    if cls is None:
        available = [n for n in dir(module) if not n.startswith("_")]
        return _make_load_result(
            LOAD_FAILED,
            page_entry,
            message=f"class '{page_class_name}' not found in {page_path_rel}",
            error_detail=f"available names: {available}",
        )

    # --- Optionally instantiate ---
    instance = None
    if instantiate:
        try:
            instance = cls()
        except Exception:
            tb = traceback.format_exc()
            return _make_load_result(
                LOAD_WARNING,
                page_entry,
                message=f"class loaded but instantiation failed: {page_class_name}",
                page_class=cls,
                error_detail=tb,
            )

    # --- Build final result ---
    status = LOAD_OK
    message = "loaded"
    if warnings:
        status = LOAD_WARNING
        message = "loaded with warnings: " + "; ".join(warnings)

    return _make_load_result(
        status,
        page_entry,
        message=message,
        page_class=cls,
        page_instance=instance,
    )

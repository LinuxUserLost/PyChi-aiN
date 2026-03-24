"""
page_prompt_editor
──────────────────
Prompt Editor page for pychiain / Guichi loader.

Guichi loader usage
───────────────────
    from page_prompt_editor import PagePromptEditor
    page = PagePromptEditor(parent, app, page_key, page_folder)
    # loader places page.frame
"""

from .prompt_editor import PagePromptEditor

__all__ = ["PagePromptEditor"]

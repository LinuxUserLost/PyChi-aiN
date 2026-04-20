"""
promptworkshop
──────────────
Prompt Workshop page for pychiain / Guichi loader.

Guichi loader usage
───────────────────
    from promptworkshop import PagePromptWorkshop
    page = PagePromptWorkshop(parent, app, page_key, page_folder)
    # loader places page.frame
"""

from .prompt_workshop import PagePromptWorkshop

__all__ = ["PagePromptWorkshop"]

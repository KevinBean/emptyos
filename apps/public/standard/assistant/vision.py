"""Vision helpers — turn vault image paths into data URLs for the think providers.

Moved to ``emptyos/sdk/attachments.py`` when the agent became the second
consumer (CLAUDE.md rule 9); this module re-exports it so the assistant's
imports keep working. The SDK version confines each path to the vault — this
module's original read ``vault_root / rel_path`` unchecked.
"""

from __future__ import annotations

from emptyos.sdk.attachments import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    is_image_path,
    path_to_data_url,
    resolve_images,
)

__all__ = ["IMAGE_EXTENSIONS", "MAX_IMAGE_BYTES", "is_image_path", "path_to_data_url", "resolve_images"]

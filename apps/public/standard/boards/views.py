"""Boards-specific re-export of the SDK ViewStore.

The generic implementation lives in ``emptyos/sdk/view_store.py``. This module
keeps the import path stable for existing boards code.
"""

from emptyos.sdk.view_store import ALLOWED_KEYS as _ALLOWED_KEYS  # noqa: F401
from emptyos.sdk.view_store import ViewStore  # noqa: F401

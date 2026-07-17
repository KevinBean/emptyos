from __future__ import annotations

import sys
from pathlib import Path

# Put services/ on the path so the service imports as the ``chatbot`` package
# (its modules use relative imports: from .config import ...).
SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

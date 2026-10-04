from __future__ import annotations

import sys
from pathlib import Path

# services/ on the path so both external_lab and chatbot import as packages.
SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

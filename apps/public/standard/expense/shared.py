"""expense — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (analytics/income/recurring) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations





_RECURRING_JOB = "expense-recurring"

"""Pages that were moved onto `EOS_UI.errorState` must stay there.

`scripts/check_error_state.py` is advisory repo-wide (a silent degrade can be
deliberate), so nothing gates a page that slides back to painting a failed fetch
in empty-state grey. This pins the six pages fixed in the 2026-09-03 app
improvement pass at **zero findings each**, using the scanner's own pure
`scan_text`, so a re-introduced `'<div class="empty">Error</div>'` fails here
rather than being one more advisory row nobody reads.

Add a page to ``HONEST_PAGES`` when you convert it; never remove one to make
the test pass.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_error_state.py"
_spec = importlib.util.spec_from_file_location("check_error_state_for_pins", SCRIPT)
ces = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ces)

#: page -> number of catch sites converted on 2026-09-03. Exact, not a floor:
#: one site sliding back to grey (or to `EOS_UI.emptyState`) must fail here.
HONEST_PAGES = {
    "apps/public/standard/expense/pages/index.html": 4,
    "apps/public/standard/journal/pages/index.html": 2,
    "apps/public/core/providers/pages/index.html": 4,
    "apps/public/core/store/pages/index.html": 2,
    "apps/public/core/settings/pages/index.html": 4,
    "apps/public/core/quick-action/pages/index.html": 1,
}

#: A render, not a mention: the helper's return assigned into the DOM. A
#: comment or a stray reference does not count.
RENDER_RE = re.compile(r"(?:innerHTML|textContent)\s*=\s*EOS_UI\.errorState\(")


@pytest.mark.parametrize("rel", sorted(HONEST_PAGES))
def test_page_renders_no_failure_as_an_empty_state(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    findings = ces.scan_text(src)
    assert findings == [], f"{rel}: {len(findings)} catch block(s) paint a failure as empty state"


@pytest.mark.parametrize("rel", sorted(HONEST_PAGES))
def test_page_renders_the_shared_error_state_at_every_converted_site(rel):
    """Zero findings is also what a page with no catch blocks scores — so
    require the positive, and count it: every converted site still renders
    the helper."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    n = len(RENDER_RE.findall(src))
    assert n == HONEST_PAGES[rel], f"{rel}: {n} errorState renders, expected {HONEST_PAGES[rel]}"

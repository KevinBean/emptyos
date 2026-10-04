"""Every page that draws with EOS_AGENT_VIEW loads it — and loads it first.

tests/js/eos_agent_view.test.mjs proves the helpers work; it cannot see whether
a page loads them. A consumer script whose page forgot the tag (or put it after
the consumer) throws `EOS_AGENT_VIEW is not defined` only when a session opens,
long after the page looked fine. Same for the portal split: portal.js's boot
code runs at parse time and calls into its portal-*.js siblings, so those must
be loaded before it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"
_SRC = re.compile(r'<script[^>]*\bsrc="([^"]+)"')

VIEW = "eos-agent-view.js"
COMPONENTS = "eos-components.js"


def _consumers() -> list[Path]:
    return sorted(
        p for p in APPS.rglob("pages/*.js")
        if "EOS_AGENT_VIEW" in p.read_text(encoding="utf-8", errors="replace")
    )


_TAG = re.compile(r"<script\b[^>]*>", re.I)


def _script_names(page: Path) -> list[str]:
    return [Path(s).name for s in _SRC.findall(page.read_text(encoding="utf-8", errors="replace"))]


def _deferred(page: Path) -> list[str]:
    """Script tags that do NOT run in document order — list order means nothing
    for them, so an ordering assertion over names alone would pass while
    portal.js's boot code throws a ReferenceError."""
    html = page.read_text(encoding="utf-8", errors="replace")
    return [t for t in _TAG.findall(html) if re.search(r"\b(defer|async)\b", t) or 'type="module"' in t]


def test_the_known_consumers_are_found():
    names = {p.parent.parent.name + "/" + p.name for p in _consumers()}
    assert {"agent/agent.js", "portal/portal-agent.js"} <= names, names


def test_every_consumer_page_loads_the_view_before_the_consumer():
    checked = 0
    for js in _consumers():
        for page in js.parent.glob("*.html"):
            scripts = _script_names(page)
            if js.name not in scripts:
                continue
            checked += 1
            where = f"{page.relative_to(ROOT).as_posix()}"
            assert VIEW in scripts, f"{where} loads {js.name} but not {VIEW}"
            assert COMPONENTS in scripts, f"{where} lacks {COMPONENTS} (EOS_UI.esc)"
            assert scripts.index(COMPONENTS) < scripts.index(VIEW) < scripts.index(js.name), (
                f"{where}: load order must be {COMPONENTS} → {VIEW} → {js.name}, got {scripts}"
            )
            late = [t for t in _deferred(page) if any(n in t for n in (VIEW, js.name, COMPONENTS))]
            assert not late, f"{where}: deferred/async/module tags break document order: {late}"
    assert checked >= 2, f"only {checked} consumer page(s) checked — the walk found nothing"


def test_portal_core_loads_after_its_siblings():
    scripts = _script_names(APPS / "public/standard/portal/pages/index.html")
    siblings = [s for s in scripts if s.startswith("portal-") and s.endswith(".js")]
    assert {"portal-agent.js", "portal-verbs.js", "portal-sidebar.js", "portal-chat.js", "portal-projects.js"} <= set(siblings), siblings
    assert all(scripts.index(s) < scripts.index("portal.js") for s in siblings), scripts
    late = [t for t in _deferred(APPS / "public/standard/portal/pages/index.html") if "/portal/pages/" in t]
    assert not late, f"a deferred portal script runs after portal.js's boot: {late}"

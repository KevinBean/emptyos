"""Unit tests for scripts/check_ai_native.py — the AI-native scorecard scanner.

Three properties matter, and all three are the ones a drifting heuristic breaks
first (`.claude/rules/audits.md`):

  1. It detects the real thing — an LLM call on `self` / `app` / `ctx.app`, the
     manifest reach declarations, and the shared AI-UI tokens.
  2. It is **silent on healthy code** — a deterministic engineering calculator
     with no `think()` is `no-ai`, never a finding; and a `.select(` on some
     other object (BeautifulSoup, Playwright) is not an LLM call.
  3. `DARK_OK` really suppresses a finding — the skill tells people to edit it,
     so it must not be dead code, and `tiers.dark` must agree with `data.dark`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# The scanner does `from check_common import load_by_path`, which resolves via
# sys.path[0] when run as a script. Under pytest we add scripts/ only for the
# duration of the exec — leaving it on sys.path would let scripts/ shadow
# module names for every other test in the session.
sys.path.insert(0, str(_SCRIPTS))
try:
    _spec = importlib.util.spec_from_file_location("check_ai_native", _SCRIPTS / "check_ai_native.py")
    scanner = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(scanner)
finally:
    sys.path.remove(str(_SCRIPTS))


def _app(root: Path, rel: str, *, manifest: str = "", py: str = "", page: str = "") -> Path:
    """Materialise one app dir under a fake apps/ root."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    app_id = rel.rsplit("/", 1)[-1]
    (d / "manifest.toml").write_text(
        manifest or f'[app]\nid = "{app_id}"\n', encoding="utf-8"
    )
    if py:
        (d / "app.py").write_text(py, encoding="utf-8")
    if page:
        (d / "pages").mkdir(exist_ok=True)
        (d / "pages" / "index.html").write_text(page, encoding="utf-8")
    return d


# ── 1. backend detection ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "src,expected",
    [
        ("x = await self.think(P)", ["think"]),
        ("await self.think_stream(P)", ["think_stream"]),
        ("await app.think(P)", ["think"]),  # labs `work` calls module-level app.think
        ("await ctx.app.think_cached(P)", ["think_cached"]),
        ("v = await self.select(q, choices)", ["select"]),
        ("await self.suggest_field('x', I)", ["suggest_field"]),
        ("r = await self.fan_out_think(P)", ["fan_out_think"]),
    ],
)
def test_scan_backend_detects_llm_calls(tmp_path: Path, src: str, expected: list[str]):
    d = _app(tmp_path, "apps/x", py=src)
    assert scanner._scan_backend(d) == expected


@pytest.mark.parametrize(
    "src",
    [
        "rows = soup.select('div.item')",       # BeautifulSoup
        "await page.select('#dropdown', 'a')",  # Playwright
        "self.selected = True",                 # attribute, not a call
        "think(prompt)",                        # bare fn, no receiver
        "other.think(prompt)",                  # some other object
    ],
)
def test_scan_backend_ignores_non_llm_calls(tmp_path: Path, src: str):
    """The receiver anchor is what keeps `.select(` from crying wolf."""
    d = _app(tmp_path, "apps/x", py=src)
    assert scanner._scan_backend(d) == []


def test_scan_backend_reads_helper_modules(tmp_path: Path):
    """Multi-module apps put think() in helpers that take `self` (multi-module-apps.md)."""
    d = _app(tmp_path, "apps/x")
    (d / "generate.py").write_text("async def gen(self):\n    return await self.think(P)\n", encoding="utf-8")
    assert scanner._scan_backend(d) == ["think"]


# ── 2. page AI-UI detection ───────────────────────────────────────────────


def test_scan_pages_detects_each_token(tmp_path: Path):
    d = _app(tmp_path, "apps/x", page="""
        EOS_UI.modelPill({app: 'x'});
        EOS_UI.provenance(p);
        EOS_UI.aiFormFill({...});
        <textarea data-suggest-field="prompt"></textarea>
        EOS.registerActions({go: fn});
    """)
    assert scanner._scan_pages(d) == {
        "pill": True, "prov": True, "form": True, "suggest": True, "actions": True,
    }


def test_data_ai_output_counts_as_platform_provenance_chip(tmp_path: Path):
    d = _app(
        tmp_path,
        "apps/x",
        page='<div id="answer" data-ai-output="/x/api/answer"></div>',
    )
    assert scanner._scan_pages(d)["prov"] is True


def test_scan_pages_skips_vendor_and_minified(tmp_path: Path):
    """A vendored lib mentioning our tokens must not credit the app with an AI UI."""
    d = _app(tmp_path, "apps/x")
    (d / "pages").mkdir(exist_ok=True)
    (d / "pages" / "vis.min.js").write_text("EOS_UI.modelPill(", encoding="utf-8")
    (d / "pages" / "vendor").mkdir()
    (d / "pages" / "vendor" / "lib.js").write_text("EOS_UI.provenance(", encoding="utf-8")
    assert not any(scanner._scan_pages(d).values())


# ── 3. manifest reach detection ───────────────────────────────────────────


def test_scan_manifest_reads_verbs_voice_slash_suggest_prompts(tmp_path: Path):
    d = _app(tmp_path, "apps/x", manifest="""
[app]
id = "x"

[[provides.verbs]]
verb = "x.add"
eligibility = "stable"
voice = { method = "voice_add" }

[[provides.verbs]]
verb = "x.list"
eligibility = "gated"
surfaces = ["assistant"]

[[provides.field_suggest]]
field = "prompt"

[provides.prompts]
module = "prompts"
""")
    m = scanner._scan_manifest(d)
    assert m["verbs"] == 2 and m["verbs_stable"] == 1
    assert m["voice"] is True          # via the `voice = {...}` subtable
    assert m["slash"] is True          # via surfaces = ["assistant"]
    assert m["field_suggest"] == 1
    assert m["prompts"] is True
    assert m["error"] == ""


def test_scan_manifest_reads_legacy_declarations(tmp_path: Path):
    """Pre-registry apps declare [provides.assistant] + contributes voice intents."""
    d = _app(tmp_path, "apps/x", manifest="""
[app]
id = "x"

[provides.assistant]
server_actions = ["add"]

[[contributes.voice-assistant.intent]]
verb = "x.add"
method = "voice_add"
""")
    m = scanner._scan_manifest(d)
    assert m["slash"] is True and m["voice"] is True and m["verbs"] == 0


def test_scan_manifest_surfaces_unreadable(tmp_path: Path):
    d = _app(tmp_path, "apps/x")
    (d / "manifest.toml").write_text("[app\nbroken", encoding="utf-8")
    assert scanner._scan_manifest(d)["error"].startswith("manifest unreadable")


# ── 4. classify + score ───────────────────────────────────────────────────


def _rec(**kw) -> dict:
    base = {
        "id": "x", "backend": [], "verbs": 0, "voice": False, "slash": False,
        "field_suggest": 0, "prompts": False,
        "ui": {"pill": False, "prov": False, "form": False, "suggest": False, "actions": False},
    }
    ui = {**base["ui"], **kw.pop("ui", {})}
    return {**base, **kw, "ui": ui}


def test_classify_no_ai_app_is_never_a_finding():
    """A deterministic calculator is correct-by-design, not a gap."""
    calc = _rec(id="earthing")
    assert scanner.classify(calc) == "no-ai"
    assert scanner.score(calc) == 0


def test_classify_conversation_stack_is_exempt():
    assert scanner.classify(_rec(id="assistant", backend=["think"])) == "surface"


def test_classify_dark_is_backend_only():
    assert scanner.classify(_rec(backend=["think"])) == "dark"


def test_classify_partial_when_reach_or_ui_but_not_both():
    assert scanner.classify(_rec(backend=["think"], slash=True)) == "partial"
    assert scanner.classify(_rec(backend=["think"], ui={"actions": True})) == "partial"


def test_classify_exemplar_needs_reach_and_a_chip():
    """A chip is modelPill or provenance — registerActions alone isn't visible AI."""
    assert scanner.classify(_rec(backend=["think"], verbs=1, ui={"pill": True})) == "exemplar"
    assert scanner.classify(_rec(backend=["think"], verbs=1, ui={"actions": True})) == "partial"


def test_score_is_bounded_and_additive():
    full = _rec(backend=["think"], verbs=3, prompts=True,
                ui={"pill": True, "suggest": True})
    assert scanner.score(full) == 5
    assert scanner.score(_rec(backend=["think"])) == 1


# ── 5. scan() end-to-end on a hermetic tree ───────────────────────────────


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    apps = tmp_path / "apps"
    _app(apps, "public/core/darkling", py="await self.think(P)")
    _app(apps, "public/core/shiny", py="await self.think(P)",
         page="EOS_UI.modelPill({});",
         manifest='[app]\nid = "shiny"\n\n[[provides.verbs]]\nverb = "shiny.go"\n')
    _app(apps, "extension/engineering/earthing", py="def r(): return 1/2")
    return apps


def test_scan_classifies_a_small_tree(tree: Path):
    r = scanner.scan(apps_root=tree, dark_ok=set())
    assert r["total"] == 3 and r["ai_apps"] == 2
    assert r["tiers"] == {"dark": 1, "exemplar": 1, "no-ai": 1}
    assert [a["id"] for a in r["dark"]] == ["darkling"]
    assert r["unreadable"] == []


def test_dark_ok_suppresses_finding_and_tier_count(tree: Path):
    """The allowlist the skill tells people to edit must not be dead code, and
    tiers.dark must not disagree with data.dark (a JSON consumer reads both)."""
    r = scanner.scan(apps_root=tree, dark_ok={"darkling"})
    assert r["dark"] == []
    assert r["tiers"]["dark"] == 0
    assert r["dark_ok"] == ["darkling"]


# ── 6. split AI chrome — chip on a secondary page, primary surface bare ────
# Regression pin for the 2026-07-11 kb bug: `_scan_pages` ORs across every page,
# so a chip on docs.html made the whole app read as "has chip" while /kb/ — the
# page ~every visit lands on — spent the user's budget with no signal at all.


def _split_app(apps: Path, *, primary: str, secondary: str) -> Path:
    d = _app(apps, "public/standard/splitty", py="await self.think(P)", page=primary)
    (d / "pages" / "other.html").write_text(secondary, encoding="utf-8")
    return d


def test_split_chrome_fires_when_only_a_secondary_page_has_the_chip(tmp_path: Path):
    apps = tmp_path / "apps"
    _split_app(apps, primary="<h1>main</h1>", secondary="EOS_UI.modelPill({});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert [a["id"] for a in r["split_chrome"]] == ["splitty"]


def test_split_chrome_silent_when_primary_has_the_chip(tmp_path: Path):
    """The fixed shape — chip on the primary surface — must not be a finding."""
    apps = tmp_path / "apps"
    _split_app(apps, primary="EOS_UI.modelPill({});", secondary="EOS_UI.modelPill({});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


def test_split_chrome_counts_sibling_js_loaded_by_the_primary_page(tmp_path: Path):
    """kb's real shape: index.html is markup-only and mounts the pill from its
    sibling kb.js. That sibling IS the primary surface — not a finding."""
    apps = tmp_path / "apps"
    d = _split_app(apps, primary='<script src="/x/pages/main.js"></script>',
                   secondary="EOS_UI.modelPill({});")
    (d / "pages" / "main.js").write_text("EOS_UI.modelPill({app:'x'});", encoding="utf-8")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


# ── provenanceLine — the wrapped chip the token list did not know ─────────
# 2026-09-03 architecture review: 11 apps (incl. apps/personal; 6 on a public
# clone) render their chip through `EOS_UI.provenanceLine(prov)`, a wrapper over
# `EOS_UI.provenance(`. The substring `EOS_UI.provenance(` does NOT occur inside
# `EOS_UI.provenanceLine(`, so every adopter scanned as chip-less: 3 "dark" apps
# and 8 provenance-drops. All three tests use ONLY the wrapped form, so the
# bare-token match cannot satisfy them by accident, and each fixture has exactly
# one route to a clean verdict — the token — so it cannot pass through a
# healthy neighbour (audits.md § Failure mode 3).


def test_provenance_line_counts_as_a_chip(tmp_path: Path):
    d = _app(tmp_path, "apps/x",
             page="el.innerHTML = EOS_UI.provenanceLine(r.provenance, {suffix: ' · edit'});")
    ui = scanner._scan_pages(d)
    assert ui["prov"] is True
    assert ui["pill"] is False  # it is a chip, not a pill — no mount to resolve


def test_provenance_line_lifts_a_backend_only_app_out_of_dark(tmp_path: Path):
    """The boards / hub-life / work-fit shape: one page, backend think, the
    chip rendered through provenanceLine and nothing else. No secondary page,
    so `partial` cannot be reached through a pill — only the token clears it."""
    apps = tmp_path / "apps"
    _app(apps, "public/standard/solo", py="await self.think(P)",
         page="el.innerHTML = EOS_UI.provenanceLine(r.provenance);")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert [a["id"] for a in r["dark"]] == []


def test_provenance_line_in_loaded_sibling_js_covers_the_primary_surface(tmp_path: Path):
    """A chip reached only through a sibling script the primary page loads is
    chrome for that page (same rule as the modelPill sibling case above), and
    that holds for the wrapped token too. Not projects' shape — projects'
    sibling chip sits in an uncalled function, so its page carries an inline
    ignore marker instead of relying on this."""
    apps = tmp_path / "apps"
    d = _split_app(apps, primary='<script src="/x/pages/dialogs.js"></script>',
                   secondary="EOS_UI.modelPill({});")
    (d / "pages" / "dialogs.js").write_text(
        "var pv = EOS_UI.provenanceLine(r.provenance);", encoding="utf-8")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


def test_split_chrome_inline_ignore_marker_opts_out(tmp_path: Path):
    """An inline marker at the call site beats a central allowlist — a new
    legitimate case (index is a chooser, AI lives in tabs) must not break CI."""
    apps = tmp_path / "apps"
    _split_app(apps,
               primary=f"<!-- {scanner._SPLIT_IGNORE}: index is a chooser -->",
               secondary="EOS_UI.modelPill({});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


def test_split_chrome_silent_for_apps_without_backend_ai(tmp_path: Path):
    apps = tmp_path / "apps"
    d = _app(apps, "public/core/calc", py="def r(): return 1/2", page="<h1>calc</h1>")
    (d / "pages" / "other.html").write_text("EOS_UI.provenance({});", encoding="utf-8")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


# ── 7. provenance-drop — self.last_provenance() called, no chip anywhere ───
# Added 2026-08-21 (eos-ai-native-audit, 5th run) after the shape recurred 8
# times across three runs (boards, cockpit, then 6 more found by grepping
# every self.last_provenance() call site for a matching chip). Same
# false-positive discipline as split-chrome: fires only when the author
# demonstrably knew provenance existed (they called the accessor) yet no
# page in the app renders a chip for it.


def test_provenance_drop_fires_when_called_with_no_chip(tmp_path: Path):
    apps = tmp_path / "apps"
    _app(apps, "personal/dropper",
         py="async def h(self):\n    return {'text': x, 'provenance': self.last_provenance()}",
         page="<div>no chip here</div>")
    r = scanner.scan(apps_root=apps, dark_ok=set(), provenance_ok=set())
    assert [a["id"] for a in r["provenance_drop"]] == ["dropper"]


def test_provenance_drop_silent_when_chip_present(tmp_path: Path):
    """The fixed shape — a chip renders the returned provenance — is not a
    finding, regardless of which chip token (modelPill/provenance/
    data-ai-output) satisfies it."""
    apps = tmp_path / "apps"
    _app(apps, "personal/fixed",
         py="async def h(self):\n    return {'provenance': self.last_provenance()}",
         page="<div data-ai-output='/fixed/api/h'></div>")
    r = scanner.scan(apps_root=apps, dark_ok=set(), provenance_ok=set())
    assert r["provenance_drop"] == []


def test_provenance_drop_silent_without_a_last_provenance_call(tmp_path: Path):
    """A plain dark app (think(), no provenance accessor at all) is the
    existing `dark` finding, not this one — the two checks are independent."""
    apps = tmp_path / "apps"
    _app(apps, "personal/plain-dark", py="await self.think(P)", page="<h1>x</h1>")
    r = scanner.scan(apps_root=apps, dark_ok=set(), provenance_ok=set())
    assert r["provenance_drop"] == []


def test_provenance_ok_suppresses_finding(tmp_path: Path):
    """Mirror of DARK_OK — a judged exception must actually suppress, not be
    dead code the skill tells people to edit for nothing."""
    apps = tmp_path / "apps"
    _app(apps, "personal/bespoke",
         py="async def h(self):\n    return {'provenance': self.last_provenance()}",
         page="<div>estimated · openai (bespoke, not a shared chip token)</div>")
    r = scanner.scan(apps_root=apps, dark_ok=set(), provenance_ok={"bespoke"})
    assert r["provenance_drop"] == []
    assert r["provenance_ok"] == ["bespoke"]


def test_provenance_drop_does_not_affect_tier_or_dark_count(tmp_path: Path):
    """Advisory only: an app with reach (so it's `partial`, not `dark`) that
    also drops provenance must not be double-counted into `dark`, and its
    tier must be unaffected by the new check."""
    apps = tmp_path / "apps"
    _app(apps, "public/core/reachy",
         manifest='[app]\nid = "reachy"\n\n[[provides.verbs]]\nverb = "reachy.go"\n'
                  'method = "go"\neligibility = "stable"\nsurfaces = ["voice"]\n\n'
                  '[provides.verbs.voice]\nmethod = "go"\n',
         py="async def go(self):\n    x = await self.think(P)\n    return {'provenance': self.last_provenance()}",
         page="<h1>reachy</h1>")
    r = scanner.scan(apps_root=apps, dark_ok=set(), provenance_ok=set())
    assert [a["id"] for a in r["provenance_drop"]] == ["reachy"]
    assert r["dark"] == []
    assert r["tiers"].get("dark", 0) == 0
    reachy = next(a for a in r["apps"] if a["id"] == "reachy")
    assert reachy["class"] == "partial"


# ── 6b. split AI chrome — the sibling fold must be EFFECTIVE, not nominal ──
# Regression pin for the 2026-08-30 dictionary + projects bugs. The fold that
# makes a sibling script part of the primary surface was satisfied by two things
# that do not put a chip on that surface: a pill mounting into a slot the page
# never contains (dictionary — the pill lived in the pack-compose flow while the
# default Look-up tab spent think bare), and a filename appearing only in a code
# comment (projects — `tabs.js` mentions "workspace-page.js" in prose, so that
# file's pill was folded into a page that never loads it).


def _sibling_app(apps: Path, *, primary: str, sibling: str) -> Path:
    """index.html + one sibling main.js, plus a secondary page carrying a chip
    (the secondary is what makes this a *split* candidate at all)."""
    d = _split_app(apps, primary=primary, secondary="EOS_UI.modelPill({});")
    (d / "pages" / "main.js").write_text(sibling, encoding="utf-8")
    return d


_LOADS_MAIN = '<script src="/x/pages/main.js"></script>'


def test_split_chrome_fires_when_sibling_pill_anchor_is_absent_from_primary(tmp_path: Path):
    """The dictionary shape: the sibling IS loaded, but mounts the pill into a
    slot another flow renders — so the primary surface shows no chip."""
    apps = tmp_path / "apps"
    _sibling_app(apps, primary=_LOADS_MAIN,
                 sibling="EOS_UI.modelPill({app:'x', mount:'#pc-model'});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert [a["id"] for a in r["split_chrome"]] == ["splitty"]


def test_split_chrome_silent_when_sibling_pill_anchor_is_in_primary(tmp_path: Path):
    """The fixed shape — the slot exists in the page's own markup."""
    apps = tmp_path / "apps"
    _sibling_app(apps, primary=_LOADS_MAIN + '<span id="model-pill"></span>',
                 sibling="EOS_UI.modelPill({app:'x', mount:'#model-pill'});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


def test_split_chrome_resolves_a_mount_held_in_a_variable(tmp_path: Path):
    """dictionary mounts via `var slot = document.getElementById('pc-model')`,
    so a literal-only resolver would miss it and pass the app blindly."""
    apps = tmp_path / "apps"
    _sibling_app(
        apps, primary=_LOADS_MAIN,
        sibling="var slot = document.getElementById('pc-model');\n"
                "EOS_UI.modelPill({app:'x', mount: slot});",
    )
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert [a["id"] for a in r["split_chrome"]] == ["splitty"]
    # ...and the same code is silent once the page carries that slot.
    apps2 = tmp_path / "apps2"
    _sibling_app(
        apps2, primary=_LOADS_MAIN + '<div id="pc-model"></div>',
        sibling="var slot = document.getElementById('pc-model');\n"
                "EOS_UI.modelPill({app:'x', mount: slot});",
    )
    assert scanner.scan(apps_root=apps2, dark_ok=set())["split_chrome"] == []


def test_split_chrome_never_fires_on_an_unresolvable_mount(tmp_path: Path):
    """Conservative edge: a mount the scanner cannot resolve counts as covered,
    so the gate never fires on a guess (audits.md — ambiguity must not gate)."""
    apps = tmp_path / "apps"
    _sibling_app(apps, primary=_LOADS_MAIN,
                 sibling="EOS_UI.modelPill({app:'x', mount: pickSlot(cfg)});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


def test_split_chrome_ignores_a_filename_named_only_in_a_comment(tmp_path: Path):
    """A filename NAMED in the primary page is not a filename LOADED by it.
    A bare-substring fold swallows other.js's chip on the strength of a comment
    and passes an app whose surface never runs that file."""
    apps = tmp_path / "apps"
    d = _sibling_app(
        apps,
        primary=_LOADS_MAIN + "\n<!-- the pill is mounted by other.js -->",
        sibling="var x = 1;",
    )
    (d / "pages" / "other.js").write_text("EOS_UI.modelPill({app:'x'});", encoding="utf-8")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert [a["id"] for a in r["split_chrome"]] == ["splitty"]


def test_split_chrome_fold_is_not_transitive_through_sibling_content(tmp_path: Path):
    """The projects shape proper: index.html loads main.js, whose COMMENT names
    other.js. The original fold appended each sibling into the very string it
    was scanning, so a sibling's prose could pull a third file's chip onto the
    surface — which is how projects passed while /projects/ rendered no pill."""
    apps = tmp_path / "apps"
    d = _sibling_app(
        apps, primary=_LOADS_MAIN,
        sibling="// renderWorkspaceLayout() lives in other.js\n",
    )
    (d / "pages" / "other.js").write_text("EOS_UI.modelPill({app:'x'});", encoding="utf-8")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert [a["id"] for a in r["split_chrome"]] == ["splitty"]


def test_split_chrome_counts_a_cache_busted_script_src(tmp_path: Path):
    """music-studio's real shape: `src="pages/music-studio.js?v=..."`. A load
    test that ignores the query string would call this app a finding."""
    apps = tmp_path / "apps"
    _sibling_app(apps,
                 primary='<script src="pages/main.js?v=2026-07-28-2"></script>',
                 sibling="EOS_UI.modelPill({app:'x', mount: anything});")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []


def test_split_chrome_silent_when_sibling_renders_a_provenance_chip(tmp_path: Path):
    """`EOS_UI.provenance(obj)` RETURNS a string its caller interpolates — there
    is no mount anchor to resolve, so it covers wherever its caller renders."""
    apps = tmp_path / "apps"
    _sibling_app(apps, primary=_LOADS_MAIN,
                 sibling="el.innerHTML = EOS_UI.provenance(resp.provenance);")
    r = scanner.scan(apps_root=apps, dark_ok=set())
    assert r["split_chrome"] == []

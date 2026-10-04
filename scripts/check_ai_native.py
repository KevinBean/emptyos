#!/usr/bin/env python3
"""Advisory AI-native scorecard — per-app AI integration facts + tier classification.

Graduates the 2026-07-10 AI-native audit (insights/outputs/2026-07-10-ai-native-audit.md)
into a rerunnable scanner, per .claude/rules/self-audit-loops.md. For every app it
grep-derives three fact groups:

  backend   — own LLM calls (self.think / think_stream / select / suggest_field / ...)
  reach     — assistant integration declared in manifest.toml ([[provides.verbs]],
              voice intents, [provides.assistant] slash, field_suggest, prompts)
  page UI   — shared AI chrome mounted in pages/ (EOS_UI.modelPill, provenance,
              data-ai-output auto-provenance sinks, aiFormFill,
              data-suggest-field/fieldSuggest, registerActions)

Classification (only apps WITH backend AI can be findings — deterministic
calculators/connectors are "no-ai" by design and never flagged, per
.claude/rules/audits.md false-positive discipline):

  exemplar — backend AI + assistant reach + a visible chip (modelPill/provenance)
  partial  — backend AI + (reach OR any AI UI), but not the full composition
  dark     — backend AI with NO reach and NO AI UI at all — the user gets AI
             output that looks hand-authored and assistants can't invoke it.
             THE finding class; exit code = dark count (minus DARK_OK).
  surface  — the conversation stack itself (assistant/rooms/...) — exempt
  no-ai    — no LLM call; informational count only

AI-native score per AI app (0-5): backend(1) + reach(1) + chip(1) +
extra UI (suggest/formfill/registerActions)(1) + [provides.prompts](1).

Two secondary advisory checks (informational — never affect tier/score/exit
code beyond dark+split, see below):

  split-chrome     — a secondary page mounts AI chrome, pages/index.html
                      (the primary surface) does not. Opt out inline with
                      `<!-- ai-native: ignore split-chrome (why) -->`.
  provenance-drop  — self.last_provenance() is called somewhere in the app's
                      Python but no chip renders anywhere in pages/. Added
                      2026-08-21 after the same shape recurred 8 times across
                      three audit runs (boards, cockpit, then 6 more in one
                      sweep) — see insights/outputs/2026-08-21-ai-native-audit.md.
                      Judged exceptions go in PROVENANCE_OK (mirror of DARK_OK).
                      Purely advisory: does not affect the exit code.

Registered gate=False in scripts/preflight.py (scope: apps). The judgment layer
(tier narrative, gap ranking, report) is the eos-ai-native-audit skill — this
script is its deterministic substrate.

Pure file I/O — does NOT import emptyos.kernel or the emptyos.sdk package
(app_layout is loaded by path via `check_common.load_by_path`). Safe while the
daemon is up.

Usage::

    python scripts/check_ai_native.py            # summary + dark list
    python scripts/check_ai_native.py --full     # per-app table (AI apps)
    python scripts/check_ai_native.py --all      # per-app table incl. no-ai
    python scripts/check_ai_native.py --json     # agent-cli envelope
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

from check_common import load_by_path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"

# The conversation stack + its thin faces ARE the AI surface — scoring them on
# "do you have a chip / verbs" is a category error (assistant has no verbs
# because it is the host). Exempt from findings, listed as class "surface".
SURFACE_APPS = {
    "assistant", "agent", "voice-assistant", "rooms", "staff",
    "portal", "dictation", "hands-free",
}

# Scaffolds / templates — never findings.
SKIP_APPS = {"_example", "tmpl", "test-app", "test-app-wiring"}

# Judged exceptions: apps with backend think that legitimately need neither an
# on-page chip nor assistant reach (e.g. output lands only in the vault /
# another app's surface). Add ids here ONLY after a human triage — with a
# trailing comment saying why (mirror of DESKTOP_ONLY in test_sys_mobile.py).
DARK_OK: set[str] = {
    # Triaged 2026-07-10 (eos-ai-native-audit; read-verified per app).
    "cad",                # FALSE POSITIVE — AI panel is shared static eos-cad-ai-panel.js (mounts modelPill + provenance); scanner only scans the app dir
    "engineering-scene",  # FALSE POSITIVE — same shared eos-cad-ai-panel.js via the CAD scene-editor view; own page is a redirect stub
    "feature-pipeline",   # BACKGROUND — think fires in reactor stage-chain; PR draft goes through the rooms [DO:] review gate, no page render
    "grill",              # VAULT-WRITER — spec_md written to a vault note (tags:[grill-spec]); page shows only the note link
    "kb-gap-miner",       # CONSUMED-ELSEWHERE — proposals land as pending actions in the rooms review gate (author="ai"); page shows one-word triage badges
    "synth",              # ARTIFACT-WRITER — generated rows/QC land in run artifacts (write_artifact); page shows a deterministic count summary
}

# Judged exceptions for the provenance-drop check below (mirror of DARK_OK).
# Add ids here only after confirming the app's provenance signal is real but
# invisible to the CHIP_TOKENS scan (e.g. a bespoke non-EOS_UI chip string) —
# not as a way to silence a genuine drop.
PROVENANCE_OK: set[str] = {
    # Triaged 2026-08-21 (eos-ai-native-audit, 5th run; read-verified per app).
    "cad",                # FALSE POSITIVE — same shared eos-cad-ai-panel.js as DARK_OK (mounts modelPill); scanner only scans the app dir
    "engineering-scene",  # FALSE POSITIVE — same shared eos-cad-ai-panel.js as DARK_OK; own page is a redirect stub
    "workflows",          # BESPOKE — templates.html renders `resp.provenance.mode` inline ("Result · cloud"); real signal, not a CHIP_TOKENS string
    "model-bench",        # ARTIFACT-WRITER — provenance captured into benchmark run metrics (write_artifact), same shape as DARK_OK's `synth`; page never reads it
}

# Backend LLM-call detection. Anchored on the receiver (self/app/ctx.app) so a
# bare `.select(` on some other object (BeautifulSoup, playwright) can't FP.
_BACKEND_RE = re.compile(
    r"\b(?:self|app|ctx\.app)\."
    r"(think|think_stream|think_cached|think_pinned|think_compare|think_safe|"
    r"select|suggest_field|fan_out_think)\s*\("
)

# Page AI-UI tokens (scanned over *.html / *.js under the app dir, excluding
# vendor/ and *.min.js).
_UI_TOKENS = {
    "pill": ("EOS_UI.modelPill(",),
    # `provenanceLine(` is the wrapped form of `provenance(` (extracted 2026-08-21,
    # c5145ece) and is not a substring of the bare token, so every adopter scanned
    # as chip-less: 11 adopters, 3 dark apps + 8 provenance-drops on a tree that
    # includes apps/personal (6 / 1 / 4 on a public clone). The projects
    # split-chrome that vanished with this token was NOT this gap — its only
    # provenanceLine sits in an uncalled function — and is dispositioned by an
    # inline ignore marker on that page instead.
    "prov": ("EOS_UI.provenance(", "EOS_UI.provenanceLine(", "eos-badge-provenance", "data-ai-output"),
    "form": ("aiFormFill(",),
    "suggest": ("data-suggest-field", "EOS_UI.fieldSuggest("),
    "actions": ("registerActions(",),
}


# Any AI-chrome marker — a page carrying one of these tells the user an LLM is
# spending their budget behind this surface (FDL §6).
_CHIP_TOKENS = _UI_TOKENS["pill"] + _UI_TOKENS["prov"]

# Opt-out for the split-chrome check, placed at the call site (an inline marker
# beats a central allowlist — a new legitimate case shouldn't break the build):
#   <!-- ai-native: ignore split-chrome (index is a chooser; AI lives in tabs) -->
_SPLIT_IGNORE = "ai-native: ignore split-chrome"

# `self.last_provenance()` is the app-author's own signal that a render site
# carries AI-authored content worth marking — so a call site with NO chip
# anywhere in the app is the same authoring miss as split-chrome, just for
# provenance instead of the model pill. Measured 2026-08-21 (eos-ai-native-audit,
# 5th run): 6/8 candidates found this way were live drops (boards + cockpit in
# prior runs, improv/finance/nest/work-fit/task/calendar this run — all since
# fixed); the other 2 were dead endpoints with no page/voice/slash consumer at
# all (a different, already-named bug class — wire-or-remove, not a drop).
_PROV_CALL_RE = re.compile(r"\bself\.last_provenance\s*\(")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _scan_backend(app_dir: Path) -> list[str]:
    """Distinct LLM-call method names used anywhere in the app's Python."""
    found: set[str] = set()
    for py in app_dir.rglob("*.py"):
        for m in _BACKEND_RE.finditer(_read(py)):
            found.add(m.group(1))
    return sorted(found)


def _scan_provenance_calls(app_dir: Path) -> bool:
    return any(_PROV_CALL_RE.search(_read(py)) for py in app_dir.rglob("*.py"))


def _scan_pages(app_dir: Path) -> dict[str, bool]:
    hits = {k: False for k in _UI_TOKENS}
    for f in list(app_dir.rglob("*.html")) + list(app_dir.rglob("*.js")):
        rel = str(f.relative_to(app_dir)).replace("\\", "/")
        if "/vendor/" in rel or rel.startswith("vendor/") or f.name.endswith(".min.js"):
            continue
        text = _read(f)
        for key, tokens in _UI_TOKENS.items():
            if not hits[key] and any(t in text for t in tokens):
                hits[key] = True
    return hits


def _page_files(app_dir: Path) -> list[Path]:
    out: list[Path] = []
    for f in list(app_dir.rglob("*.html")) + list(app_dir.rglob("*.js")):
        rel = str(f.relative_to(app_dir)).replace("\\", "/")
        if "/vendor/" in rel or rel.startswith("vendor/") or f.name.endswith(".min.js"):
            continue
        out.append(f)
    return out


# `EOS_UI.modelPill({mount: ...})` renders INTO a slot; `EOS_UI.provenance(obj)`
# RETURNS a string the caller interpolates. Only the pill has an anchor to
# resolve, so only the pill can be proven absent from a given surface.
_PILL_MOUNT_LITERAL_RE = re.compile(r"""mount\s*:\s*['"]#([\w-]+)['"]""")
_PILL_MOUNT_VAR_RE = re.compile(r"mount\s*:\s*([A-Za-z_$][\w$]*)")
# How far past `EOS_UI.modelPill(` to look for the mount key. Wide enough for a
# multi-line options object, short enough not to reach the next call site.
_MOUNT_LOOKAHEAD = 400


def _pill_anchor_ids(js: str) -> tuple[set[str], int]:
    """DOM ids the modelPill calls in this script mount into, plus the number of
    call sites whose mount could NOT be resolved statically.

    Resolves `mount: '#id'` directly, and `mount: someVar` by finding that var's
    `document.getElementById('id')` / `querySelector('#id')` assignment in the
    same file. Anything else is reported unresolved, never guessed at.
    """
    ids: set[str] = set()
    unresolved = 0
    for m in re.finditer(re.escape(_UI_TOKENS["pill"][0]), js):
        seg = js[m.end(): m.end() + _MOUNT_LOOKAHEAD]
        lit = _PILL_MOUNT_LITERAL_RE.search(seg)
        if lit:
            ids.add(lit.group(1))
            continue
        var = _PILL_MOUNT_VAR_RE.search(seg)
        if var:
            back = re.search(
                r"\b%s\s*=\s*document\.(?:getElementById\(\s*['\"]([\w-]+)['\"]"
                r"|querySelector\(\s*['\"]#([\w-]+)['\"])" % re.escape(var.group(1)),
                js,
            )
            if back:
                ids.add(back.group(1) or back.group(2))
                continue
        unresolved += 1
    return ids, unresolved


def _has_dom_id(markup: str, dom_id: str) -> bool:
    return f'id="{dom_id}"' in markup or f"id='{dom_id}'" in markup


def _loads_script(markup: str, name: str) -> bool:
    """Does this page actually LOAD the named script?

    A bare filename match is not enough: a code comment naming a file counts as
    a load, which is what hid the projects bug — `tabs.js` mentions
    "workspace-page.js" in a comment, so that file's pill was folded into a
    surface that never loads it. Requires a real `src=` reference, tolerating
    the cache-busting query string (`music-studio.js?v=...`) real pages carry.
    """
    pat = r"""src\s*=\s*['"][^'"]*%s(?:[?#][^'"]*)?['"]""" % re.escape(name)
    return re.search(pat, markup) is not None


def _primary_has_chrome(primary: str, pages: list[Path]) -> bool:
    """Does AI chrome actually land on the primary surface?

    A chip written into index.html counts outright. A chip reached only through
    a sibling script counts only if it can LAND here: a string-returning
    provenance chip renders wherever its caller renders (no anchor to check),
    but a pill mounts into a slot — so its anchor must exist in this page's own
    markup. A pill whose slot is built by another flow's renderer is chrome for
    THAT flow, not for this one (the dictionary bug: the pill lived in the
    pack-compose slot while the default Look-up tab spent think with no chip).

    Conservative on both edges: an unresolvable mount counts as covered, so the
    check never fires on a guess.
    """
    if any(t in primary for t in _CHIP_TOKENS):
        return True
    for f in pages:
        if f.suffix != ".js" or not _loads_script(primary, f.name):
            continue
        js = _read(f)
        if any(t in js for t in _UI_TOKENS["prov"]):
            return True  # string-returning chip: renders with its caller
        ids, unresolved = _pill_anchor_ids(js)
        if unresolved:
            return True  # cannot prove absence
        if any(_has_dom_id(primary, i) for i in ids):
            return True
    return False


def _scan_split_chrome(app_dir: Path) -> bool:
    """True when SOME page of a multi-page app mounts AI chrome but the PRIMARY
    surface (pages/index.html + the sibling .js it loads) does not.

    `_scan_pages` ORs across every page, so an app whose chip lives only on a
    secondary page scores as "has chip" while its main entry point spends the
    user's budget invisibly — exactly the kb bug (pill on docs.html only, absent
    from the page 99% of visits land on). Precise by construction: it fires only
    when the author demonstrably knows the chip is required (they mounted one
    elsewhere) yet the primary surface lacks it.

    What counts as "the primary surface" is `_primary_has_chrome` — and that is
    where this check was blind until 2026-08-30. Folding every sibling whose
    NAME appeared in index.html, into the very string being scanned, let two
    non-loads pass as loads: a comment naming a file (projects) and a pill
    mounting into a slot the page never draws (dictionary). Both apps rendered
    no chip at all while scoring clean.
    """
    idx = app_dir / "pages" / "index.html"
    if not idx.exists():
        return False  # no primary surface (redirect stub, headless app)
    pages = _page_files(app_dir)
    primary = _read(idx)
    if _SPLIT_IGNORE in primary:
        return False
    if _primary_has_chrome(primary, pages):
        return False  # primary surface is marked — nothing to report
    return any(
        any(t in _read(f) for t in _CHIP_TOKENS)
        for f in pages
        if f != idx
    )


def _scan_manifest(app_dir: Path) -> dict:
    out = {
        "verbs": 0, "verbs_stable": 0, "voice": False, "slash": False,
        "field_suggest": 0, "prompts": False, "error": "",
    }
    try:
        with open(app_dir / "manifest.toml", "rb") as f:
            m = tomllib.load(f)
    except Exception as e:
        out["error"] = f"manifest unreadable: {e}"
        return out
    provides = m.get("provides") or {}
    verbs = provides.get("verbs") or []
    if isinstance(verbs, list):
        out["verbs"] = len(verbs)
        out["verbs_stable"] = sum(
            1 for v in verbs if isinstance(v, dict) and v.get("eligibility") == "stable"
        )
        for v in verbs:
            if isinstance(v, dict) and ("voice" in v or "voice" in (v.get("surfaces") or [])):
                out["voice"] = True
            if isinstance(v, dict) and (
                "assistant" in v or "assistant" in (v.get("surfaces") or [])
            ):
                out["slash"] = True
    fs = provides.get("field_suggest") or []
    out["field_suggest"] = len(fs) if isinstance(fs, list) else 1
    out["prompts"] = "prompts" in provides
    if "assistant" in provides:
        out["slash"] = True
    contributes = m.get("contributes") or {}
    va = contributes.get("voice-assistant") or {}
    if isinstance(va, dict) and va.get("intent"):
        out["voice"] = True
    return out


def classify(app: dict) -> str:
    if app["id"] in SURFACE_APPS:
        return "surface"
    if not app["backend"]:
        return "no-ai"
    reach = app["verbs"] > 0 or app["voice"] or app["slash"]
    chip = app["ui"]["pill"] or app["ui"]["prov"]
    extra = app["ui"]["suggest"] or app["ui"]["form"] or app["ui"]["actions"]
    if reach and chip:
        return "exemplar"
    if not reach and not chip and not extra:
        return "dark"
    return "partial"


def score(app: dict) -> int:
    """0-5 AI-native score; only meaningful for apps with backend AI."""
    if not app["backend"]:
        return 0
    s = 1
    s += 1 if (app["verbs"] > 0 or app["voice"] or app["slash"]) else 0
    s += 1 if (app["ui"]["pill"] or app["ui"]["prov"]) else 0
    s += 1 if (app["ui"]["suggest"] or app["ui"]["form"] or app["ui"]["actions"]) else 0
    s += 1 if app["prompts"] else 0
    return s


def scan(
    apps_root: Path = APPS,
    dark_ok: set[str] | None = None,
    provenance_ok: set[str] | None = None,
) -> dict:
    """Scan every app under ``apps_root``. Args exist so tests can drive a
    hermetic tree; production callers take the defaults."""
    dark_ok = DARK_OK if dark_ok is None else dark_ok
    provenance_ok = PROVENANCE_OK if provenance_ok is None else provenance_ok
    al = load_by_path("app_layout_ai_native", "emptyos/sdk/app_layout.py")
    apps: list[dict] = []
    for app_id, app_dir in al.iter_app_dirs(apps_root, include_personal=True):
        if app_id in SKIP_APPS:
            continue
        mf = _scan_manifest(app_dir)
        rec = {
            "id": app_id,
            "dir": str(app_dir.relative_to(apps_root)).replace("\\", "/"),
            "backend": _scan_backend(app_dir),
            "ui": _scan_pages(app_dir),
            **mf,
        }
        rec["split_chrome"] = bool(rec["backend"]) and _scan_split_chrome(app_dir)
        rec["provenance_call"] = _scan_provenance_calls(app_dir)
        rec["provenance_drop"] = (
            rec["provenance_call"]
            and not (rec["ui"]["pill"] or rec["ui"]["prov"])
            and app_id not in provenance_ok
        )
        rec["class"] = classify(rec)
        rec["score"] = score(rec)
        apps.append(rec)

    grouped: dict[str, list[dict]] = {}
    for a in apps:
        grouped.setdefault(a["class"], []).append(a)
    dark = [a for a in grouped.get("dark", []) if a["id"] not in dark_ok]
    split = [a for a in apps if a["split_chrome"] and a["class"] != "surface"]
    prov_drop = [a for a in apps if a["provenance_drop"] and a["class"] != "surface"]
    ai_apps = [a for a in apps if a["backend"] and a["class"] != "surface"]
    # `tiers.dark` must agree with the `dark` list a consumer reads — count the
    # allowlisted ones out of the tier, not just out of the findings list.
    tiers = {k: len(v) for k, v in sorted(grouped.items())}
    if "dark" in tiers:
        tiers["dark"] = len(dark)
    return {
        "total": len(apps),
        "ai_apps": len(ai_apps),
        "avg_score": round(sum(a["score"] for a in ai_apps) / len(ai_apps), 2) if ai_apps else 0,
        "tiers": tiers,
        "dark": dark,
        "split_chrome": split,
        "provenance_drop": prov_drop,
        "dark_ok": sorted(dark_ok),
        "provenance_ok": sorted(provenance_ok),
        "unreadable": [a["id"] for a in apps if a["error"]],
        "apps": apps,
    }


def _row(a: dict) -> str:
    ui = a["ui"]
    chips = "".join([
        "P" if ui["pill"] else "-",
        "V" if ui["prov"] else "-",
        "F" if ui["form"] else "-",
        "S" if ui["suggest"] else "-",
        "A" if ui["actions"] else "-",
    ])
    reach = []
    if a["verbs"]:
        reach.append(f"V({a['verbs']})")
    if a["voice"]:
        reach.append("voice")
    if a["slash"]:
        reach.append("slash")
    if a["field_suggest"]:
        reach.append("fs")
    if a["prompts"]:
        reach.append("prompts")
    return (
        f"  {a['class']:<9} {a['score']}/5  {a['id']:<24} "
        f"ui={chips}  reach={','.join(reach) or '-'}  "
        f"be={','.join(a['backend']) or '-'}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope")
    ap.add_argument("--full", action="store_true", help="per-app table (AI apps only)")
    ap.add_argument("--all", action="store_true", help="per-app table incl. no-ai apps")
    args = ap.parse_args()

    r = scan()
    n_dark = len(r["dark"])
    n_split = len(r["split_chrome"])
    n_prov = len(r["provenance_drop"])
    t = r["tiers"]
    msg = (
        f"{r['ai_apps']} AI apps / {r['total']} total — "
        f"exemplar {t.get('exemplar', 0)}, partial {t.get('partial', 0)}, "
        f"dark {n_dark}, surface {t.get('surface', 0)}, no-ai {t.get('no-ai', 0)}; "
        f"avg score {r['avg_score']}/5"
        + (f"; split-chrome {n_split}" if n_split else "")
        + (f"; provenance-drop {n_prov}" if n_prov else "")
    )

    if args.json:
        data = dict(r)
        if not (args.full or args.all):
            data.pop("apps")
        ok = n_dark == 0 and n_split == 0
        print(json.dumps({
            "ok": ok,
            "code": "ok" if ok else ("dark_ai" if n_dark else "split_chrome"),
            "message": msg,
            "data": data,
        }))
        return n_dark + n_split

    print(f"AI-native scorecard: {msg}")
    if r["unreadable"]:
        print(f"  (manifest unreadable, scored reach=0: {', '.join(r['unreadable'])})")
    if args.full or args.all:
        print("  (ui chips: P=modelPill V=provenance F=aiFormFill S=field-suggest A=registerActions)")
        for a in sorted(r["apps"], key=lambda x: (-x["score"], x["id"])):
            if not args.all and (not a["backend"] or a["class"] == "surface"):
                continue
            print(_row(a))
    elif n_dark:
        print(f"  dark AI ({n_dark} — backend think, no chip, no assistant reach):")
        for a in sorted(r["dark"], key=lambda x: x["id"]):
            print(f"    - {a['id']:<24} ({a['dir']})  be={','.join(a['backend'])}")
        print("  → triage via the eos-ai-native-audit skill; judged exceptions go in DARK_OK.")

    if n_split:
        print(f"  split AI chrome ({n_split} — a secondary page shows the chip, "
              f"pages/index.html does not):")
        for a in sorted(r["split_chrome"], key=lambda x: x["id"]):
            print(f"    - {a['id']:<24} ({a['dir']})  be={','.join(a['backend'])}")
        print(f"  → mount EOS_UI.modelPill on the primary surface, or mark the page "
              f"with an inline `{_SPLIT_IGNORE}` comment saying why.")

    if n_prov:
        print(f"  provenance-drop ({n_prov} — self.last_provenance() is called but "
              f"no chip renders anywhere in the app; advisory, does not affect tier/score):")
        for a in sorted(r["provenance_drop"], key=lambda x: x["id"]):
            print(f"    - {a['id']:<24} ({a['dir']})  be={','.join(a['backend'])}")
        print("  → triage via the eos-ai-native-audit skill: mount EOS_UI.provenance() "
              "at the render site, or if the endpoint has no consumer, wire it or "
              "remove it. Judged exceptions (a real but non-EOS_UI chip) go in "
              "PROVENANCE_OK.")

    # provenance-drop is advisory only (per the report that added it) — it does
    # not affect the exit code, unlike dark/split-chrome above.
    return n_dark + n_split


if __name__ == "__main__":
    sys.exit(main())

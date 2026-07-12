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
    "prov": ("EOS_UI.provenance(", "eos-badge-provenance", "data-ai-output"),
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


def _scan_split_chrome(app_dir: Path) -> bool:
    """True when SOME page of a multi-page app mounts AI chrome but the PRIMARY
    surface (pages/index.html + the sibling .js it loads) does not.

    `_scan_pages` ORs across every page, so an app whose chip lives only on a
    secondary page scores as "has chip" while its main entry point spends the
    user's budget invisibly — exactly the kb bug (pill on docs.html only, absent
    from the page 99% of visits land on). Precise by construction: it fires only
    when the author demonstrably knows the chip is required (they mounted one
    elsewhere) yet the primary surface lacks it. 0 hits on a healthy tree.
    """
    idx = app_dir / "pages" / "index.html"
    if not idx.exists():
        return False  # no primary surface (redirect stub, headless app)
    pages = _page_files(app_dir)
    primary = _read(idx)
    if _SPLIT_IGNORE in primary:
        return False
    # Sibling scripts the primary page actually loads are part of that surface.
    for f in pages:
        if f.suffix == ".js" and f.name in primary:
            primary += _read(f)
    if any(t in primary for t in _CHIP_TOKENS):
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


def scan(apps_root: Path = APPS, dark_ok: set[str] | None = None) -> dict:
    """Scan every app under ``apps_root``. Args exist so tests can drive a
    hermetic tree; production callers take the defaults."""
    dark_ok = DARK_OK if dark_ok is None else dark_ok
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
        rec["class"] = classify(rec)
        rec["score"] = score(rec)
        apps.append(rec)

    grouped: dict[str, list[dict]] = {}
    for a in apps:
        grouped.setdefault(a["class"], []).append(a)
    dark = [a for a in grouped.get("dark", []) if a["id"] not in dark_ok]
    split = [a for a in apps if a["split_chrome"] and a["class"] != "surface"]
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
        "dark_ok": sorted(dark_ok),
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
    t = r["tiers"]
    msg = (
        f"{r['ai_apps']} AI apps / {r['total']} total — "
        f"exemplar {t.get('exemplar', 0)}, partial {t.get('partial', 0)}, "
        f"dark {n_dark}, surface {t.get('surface', 0)}, no-ai {t.get('no-ai', 0)}; "
        f"avg score {r['avg_score']}/5"
        + (f"; split-chrome {n_split}" if n_split else "")
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
    return n_dark + n_split


if __name__ == "__main__":
    sys.exit(main())

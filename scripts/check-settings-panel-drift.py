#!/usr/bin/env python
"""Flag apps whose in-app ⚙ panel hides settings their manifest declares.

`.claude/rules/app-ui-patterns.md` makes the in-app settings panel mandatory for
any app with `[provides.settings]` — users should not need `/settings` to
configure an app. But `EOS_UI.settingsPanel({fields: [...]})` takes a hand-written
field list, so the panel and the manifest are two declarations of the same thing.
Add a setting to the manifest and the panel silently keeps rendering the old set:
the setting appears in `/settings` and nowhere else.

That has already happened. `vault-backup` gained two settings that never reached
its own panel, and this scan found four more apps in the same state.

The fix is to stop declaring twice. Pass `app: '<app-id>'` instead of `fields`
and the panel derives its fields from the manifest schema at open time (the same
source `/settings` renders from). An app that does this is exempt here, because
drift is then impossible.

ADVISORY (see `scripts/preflight.py`): a key may be omitted on purpose — a dark
feature flag, or a secret the author wants only in `/settings`. Silence a
deliberate omission with a comment anywhere in the page:

    // settings-panel-drift: ignore <settings.key>

Usage:
    python scripts/check-settings-panel-drift.py
    python scripts/check-settings-panel-drift.py --json
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# `python scripts/x.py` puts scripts/ on sys.path, but tests load this module by
# file path — where it is not. Anchor the sibling import either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_PANEL_CALL = re.compile(r"EOS_UI\.settingsPanel\s*\(\s*\{")
_APP_OPT = re.compile(r"\bapp\s*:\s*['\"]([\w.-]+)['\"]")
_FIELD_KEY = re.compile(r"""\{\s*key\s*:\s*['"]([^'"]+)['"]""")
_IGNORE = re.compile(r"settings-panel-drift:\s*ignore\s+([\w.\-]+)")


def _opts_blob(text: str, brace_at: int) -> str:
    """Return the settingsPanel opts object starting at `brace_at` (its `{`).

    Brace-counting rather than a regex: `app:` can sit after any key, including
    an `onSave: function(){...}` whose braces a `[^{}]*` guard would trip over —
    reordering two keys must not turn a healthy app into a finding. Quote-aware
    because hint strings legitimately contain braces (`{snapshot} {name}`).
    """
    depth, i, n = 0, brace_at, len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_at:i + 1]
        i += 1
    return text[brace_at:]  # unbalanced (minified/truncated) — treat as the rest


def _derives_from_manifest(text: str, app_id: str) -> bool:
    """True if any settingsPanel call on this page derives fields for `app_id`."""
    for m in _PANEL_CALL.finditer(text):
        blob = _opts_blob(text, m.end() - 1)  # the opts `{` the pattern ends on
        found = _APP_OPT.search(blob)
        if found and found.group(1) == app_id:
            return True
    return False


def _page_text(app_dir: Path) -> str:
    pages = app_dir / "pages"
    if not pages.is_dir():
        return ""
    parts = []
    for f in sorted(pages.rglob("*")):
        if f.suffix in (".html", ".js") and f.is_file():
            parts.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def scan(repo: Path = REPO) -> dict:
    """Return {"drift": [...], "no_panel": [...]} over every app with a schema."""
    drift, no_panel = [], []

    # Tracked track-tree apps only. `apps/personal/` is gitignored (per-machine,
    # so a repo checker would report noise nobody else can act on) and
    # `_retired/` holds archived apps that are not served at all.
    for man in sorted(repo.glob("apps/*/*/*/manifest.toml")):
        if "_retired" in man.parts:
            continue
        try:
            m = tomllib.load(open(man, "rb"))
        except Exception:
            continue
        schema = (m.get("provides", {}).get("settings") or {}).get("schema") or []
        keys = [f["key"] for f in schema if isinstance(f, dict) and "key" in f]
        if not keys:
            continue

        app_dir = man.parent
        app_id = (m.get("app") or {}).get("id") or app_dir.name
        text = _page_text(app_dir)

        if not _PANEL_CALL.search(text):
            # No page at all is fine (panel-only / hub-panel apps). A page with
            # no panel is a separate rule; report it, don't fail on it.
            if text:
                no_panel.append({"app": app_id, "keys": len(keys)})
            continue

        if _derives_from_manifest(text, app_id):
            continue  # manifest-derived — cannot drift

        shown = set(_FIELD_KEY.findall(text))
        ignored = set(_IGNORE.findall(text))
        missing = [k for k in keys if k not in shown and k not in ignored]
        if missing:
            drift.append({"app": app_id, "missing": missing,
                          "path": app_dir.relative_to(repo).as_posix()})

    return {"drift": drift, "no_panel": no_panel}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    args = ap.parse_args()

    result = scan()
    drift, no_panel = result["drift"], result["no_panel"]
    n = sum(len(d["missing"]) for d in drift)

    if args.json:
        return emit_json(not drift, "settings_panel_drift",
                         f"{n} manifest setting(s) hidden from {len(drift)} in-app panel(s)",
                         result)

    if not drift:
        print("check-settings-panel-drift: OK — every in-app panel shows its manifest settings")
    else:
        print(f"check-settings-panel-drift: {n} setting(s) hidden across {len(drift)} app(s)\n")
        for d in drift:
            print(f"  {d['app']}  ({d['path']})")
            for k in d["missing"]:
                print(f"    hidden: {k}")
            print(f"    fix: drop `fields: [...]` and pass `app: '{d['app']}'` "
                  f"— or mark intent with `// settings-panel-drift: ignore <key>`")
            print()

    if no_panel:
        print(f"note: {len(no_panel)} app(s) declare settings but have a page with no ⚙ panel "
              f"({', '.join(a['app'] for a in no_panel)})")

    return len(drift)


if __name__ == "__main__":
    sys.exit(main())

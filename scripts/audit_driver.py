"""Shared Playwright driver for the rendered-DOM audits.

Extracted at the second consumer (CLAUDE.md rule 9): check_readability.py and
check_ui_affordance.py had independently grown the same plumbing — fetch the
app list, launch an authenticated headless context, load a page, settle it,
inject the shared walk + the audit, evaluate, and write data/<audit>/report.json.

This module owns that plumbing. Each scanner keeps what actually differs: its
targets (readability sweeps every app × 6 themes; affordance sweeps app roots or
explicit --url paths), its finding shape, and how it prints them.

NOTE the filename: `scripts/_*.py` is gitignored (see .gitignore), so a leading
underscore here would leave this silently untracked and break a fresh clone —
the trap `_eos_browser.py` only escapes because it was force-added.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "emptyos" / "web" / "static"

# The shared harness must EXECUTE before any audit module that depends on it.
WALK_JS = STATIC / "eos-audit-walk.js"


def audit_sources(audit_js: str | Path) -> list[str]:
    """[harness, audit] source strings, in required execution order."""
    audit_path = audit_js if isinstance(audit_js, Path) else STATIC / audit_js
    return [
        WALK_JS.read_text(encoding="utf-8"),
        audit_path.read_text(encoding="utf-8"),
    ]


def fetch_apps(base: str, token: str) -> list[dict] | None:
    """Every app with a web prefix, or None when the daemon is unreachable."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{base}/api/apps", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR: daemon unreachable at {base} ({e})")
        return None
    apps = data.get("apps", data) if isinstance(data, dict) else data
    return [a for a in apps if a.get("web_prefix")]


def select_apps(apps: list[dict], only: list[str], max_apps: int = 0) -> list[dict] | None:
    """Apply --app / --max-apps filters. None when --app matched nothing."""
    if only:
        wanted = set(only)
        apps = [a for a in apps if a["id"] in wanted]
        missing = wanted - {a["id"] for a in apps}
        if missing:
            print(f"WARNING: no app matched {sorted(missing)}")
        if not apps:
            return None
    return apps[:max_apps] if max_apps else apps


def audit_page(page, url: str, sources: list[str], expr: str, settle_ms: int = 500) -> dict:
    """Load `url`, inject `sources` in order, and evaluate `expr` -> the audit result."""
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_load_state("networkidle", timeout=2500)
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)   # let async content paint before measuring
    for src in sources:
        page.add_script_tag(content=src)
    return page.evaluate(expr)


def write_report(out_dir: Path, payload: dict) -> Path:
    """Persist the full report and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def build_report(base: str, findings: list[dict], errors: list[dict], t0: float, **extra) -> dict:
    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base": base,
        "duration_s": round(time.time() - t0, 1),
        "summary": {"fails": len(fails), "warns": len(warns),
                    "findings": len(findings), "errors": len(errors)},
        "findings": findings,
        "errors": errors,
        **extra,
    }


def exit_code(fails: list | int) -> int:
    """Exit = number of confident FAILs, capped so it never collides with 2 (setup error)."""
    n = fails if isinstance(fails, int) else len(fails)
    return min(n, 99)

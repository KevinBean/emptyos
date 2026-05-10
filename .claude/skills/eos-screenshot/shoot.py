"""One-shot capture for the eos-screenshot skill.

Usage:
    python .claude/skills/eos-screenshot/shoot.py --scenario ai-offline-banner

Loads scenarios.toml + .eos-personal + .eos-branding, captures via Playwright,
runs the redaction text-scan, writes to {vault}/30_Resources/Published/media/.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


def load_patterns(path: Path) -> list[re.Pattern]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(re.compile(line))
        except re.error as e:
            print(f"  ! skipping invalid pattern in {path.name}: {line!r} ({e})", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", help="Recipe name in scenarios.toml")
    ap.add_argument("--url")
    ap.add_argument("--selector")
    ap.add_argument("--blur", help="Comma-separated CSS selectors to blur before capture")
    ap.add_argument("--out", help="Output stem (no extension)")
    ap.add_argument("--alt", help="Alt text")
    ap.add_argument("--viewport", default="1440x900")
    ap.add_argument("--wait", type=int, default=1000)
    ap.add_argument("--prep", help="JS to evaluate after navigation, before capture")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    here = Path(__file__).parent
    project_root = here.parent.parent.parent  # .claude/skills/eos-screenshot/ → project root
    scenarios_path = here / "scenarios.toml"
    scenarios = tomllib.loads(scenarios_path.read_text(encoding="utf-8")) if scenarios_path.exists() else {}

    recipe = {}
    if args.scenario:
        if args.scenario not in scenarios:
            print(f"unknown scenario: {args.scenario}", file=sys.stderr)
            print(f"available: {', '.join(scenarios)}", file=sys.stderr)
            sys.exit(2)
        recipe = scenarios[args.scenario]

    url = args.url or recipe.get("url")
    selector = args.selector or recipe.get("selector")
    blur = args.blur or recipe.get("blur")
    out_stem = args.out or recipe.get("out")
    alt = args.alt or recipe.get("alt", "")
    viewport = args.viewport or recipe.get("viewport", "1440x900")
    wait_ms = args.wait if args.wait != 1000 else recipe.get("wait_ms", 1000)
    prep = args.prep or recipe.get("prep")

    if not url or not out_stem:
        print("need --url + --out (or --scenario)", file=sys.stderr)
        sys.exit(2)

    vault_conn = project_root / ".claude" / "vault-connection.json"
    if not vault_conn.exists():
        print("vault not connected — missing .claude/vault-connection.json", file=sys.stderr)
        sys.exit(2)
    vault_path = Path(json.loads(vault_conn.read_text())["vault_path"])
    media_dir = vault_path / "30_Resources" / "Published" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    final_png = media_dir / f"{out_stem}.png"
    if final_png.exists() and not args.force:
        print(f"refusing to overwrite {final_png} — pass --force", file=sys.stderr)
        sys.exit(2)

    patterns = load_patterns(project_root / ".eos-personal") + load_patterns(project_root / ".eos-branding")

    from playwright.sync_api import sync_playwright

    w, h = (int(x) for x in viewport.split("x"))
    tmp_png = final_png.with_suffix(".tmp.png")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        if prep:
            page.evaluate(f"async () => {{ {prep} }}")
            page.wait_for_timeout(wait_ms)
        if blur:
            page.add_style_tag(content=f"{blur} {{ filter: blur(10px) !important; }}")

        try:
            visible_text = page.locator("body").inner_text()
        except Exception:
            visible_text = ""

        if selector:
            page.locator(selector).screenshot(path=str(tmp_png))
        else:
            page.screenshot(path=str(tmp_png), full_page=True)
        browser.close()

    hits: list[tuple[str, str]] = []
    for pat in patterns:
        for m in pat.finditer(visible_text):
            hits.append((pat.pattern, m.group(0)))

    if hits and not args.force:
        print(f"REDACTION FAIL — {final_png.name} would leak protected patterns:", file=sys.stderr)
        seen = set()
        for pat, snippet in hits:
            key = (pat, snippet)
            if key in seen:
                continue
            seen.add(key)
            print(f"  pattern={pat!r}  matched={snippet!r}", file=sys.stderr)
        print("Either add the offending element to --blur, navigate to a clean state, or pass --force after review.", file=sys.stderr)
        tmp_png.unlink(missing_ok=True)
        sys.exit(2)

    tmp_png.replace(final_png)
    if alt:
        (final_png.with_suffix(".alt.txt")).write_text(alt, encoding="utf-8")

    # Strip auth tokens / API keys from the URL before writing to the manifest.
    # The published-media folder may sync to backup/cloud; the manifest must
    # not leak per-machine secrets.
    safe_url = re.sub(r"(?i)(token|api[_-]?key|access[_-]?token)=[^&#\s]+", r"\1=<REDACTED>", url)
    manifest = media_dir / ".shots.toml"
    entry = (
        f"\n[{out_stem}]\n"
        f"url = {json.dumps(safe_url)}\n"
        f"viewport = {json.dumps(viewport)}\n"
        f"captured_at = {json.dumps(datetime.now(timezone.utc).isoformat())}\n"
        f"alt = {json.dumps(alt)}\n"
    )
    with manifest.open("a", encoding="utf-8") as f:
        f.write(entry)

    size_kb = final_png.stat().st_size // 1024
    print(f"OK  {final_png}")
    print(f"    {viewport}, {size_kb}KB")
    if alt:
        print(f"    alt: {alt}")
    if hits and args.force:
        print(f"    WARNING: shipped with {len(hits)} pattern hit(s) — review the file before publishing.")


if __name__ == "__main__":
    main()

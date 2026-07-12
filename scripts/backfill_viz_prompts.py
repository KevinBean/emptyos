"""One-shot backfill: set viz record `prompt` to `history[0].prompt` so
existing artifacts created/iterated before the original-brief fix display
their root brief instead of the most recent change request.

Safe to re-run; only rewrites records where `prompt != history[0].prompt`.
"""
from __future__ import annotations
import sys, tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = yaml.safe_load(text[3:end]) or {}
    body = text[end + 4 :].lstrip("\n")
    return fm if isinstance(fm, dict) else {}, body


def serialize(fm: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body


def main() -> int:
    with open(REPO / "emptyos.toml", "rb") as f:
        vault = Path(tomllib.load(f)["notes"]["path"])
    outputs = vault / "30_Resources/EmptyOS/viz/outputs"
    if not outputs.exists():
        print(f"no outputs dir at {outputs}")
        return 1

    changed = skipped = missing = 0
    for sub in sorted(outputs.iterdir()):
        rec = sub / "record.md"
        if not rec.exists():
            missing += 1
            continue
        text = rec.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        history = fm.get("history") or []
        if not history:
            skipped += 1
            continue
        original = (history[0] or {}).get("prompt", "")
        current = fm.get("prompt", "")
        if not original or original == current:
            skipped += 1
            continue
        fm["prompt"] = original
        latest = (history[-1] or {}).get("prompt", "") if len(history) > 1 else original
        new_body = (
            f"# Viz artifact `{sub.name}`\n\n"
            f"**Original brief:** {original}\n\n"
            f"**Latest change:** {latest}\n\n"
            f"[Open scene.html](30_Resources/EmptyOS/viz/outputs/{sub.name}/scene.html)\n"
        )
        rec.write_text(serialize(fm, new_body), encoding="utf-8")
        changed += 1
        print(f"  ✓ {sub.name}: {current[:50]!r} → {original[:50]!r}")
    print(f"\n{changed} updated, {skipped} unchanged, {missing} missing record.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

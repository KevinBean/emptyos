"""One-shot classification: add dimensions=[...] to manifest.toml [app] blocks.

Conservative mapping — only includes apps whose primary dimension is clear.
Meta-infrastructure apps (capture, search, assistant, hub, reactor, settings)
and templates stay unclassified so the audit treats them as cross-cutting.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CLASSIFY: dict[str, list[str]] = {
    # Physical
    "workout": ["physical"],
    "nutrition": ["physical"],
    "sleep": ["physical"],
    "tracker": ["physical"],
    "recipes": ["physical"],
    "habits": ["physical"],
    # Social
    "contacts": ["social"],
    # Intellectual
    "english": ["intellectual"],
    "shadowing": ["intellectual"],
    "lessons": ["intellectual"],
    "quickref": ["intellectual"],
    "dictionary": ["intellectual"],
    "speaking": ["intellectual", "social"],
    "news-center": ["intellectual"],
    "bookmarks": ["intellectual"],
    "podcast": ["intellectual"],
    "media": ["intellectual"],
    "fiction-engine": ["intellectual"],
    "voice-review": ["intellectual"],
    "review": ["intellectual"],
    "studio": ["intellectual"],
    "music-studio": ["intellectual"],
    "3d-studio": ["intellectual"],
    "comfyui-app": ["intellectual"],
    "gpts": ["intellectual"],
    # Emotional
    "healing": ["emotional"],
    # Spiritual
    "meditation": ["spiritual"],
    "divination": ["spiritual"],
    "reflect": ["spiritual", "emotional"],
    # Environmental
    "places": ["environmental"],
    "items": ["environmental"],
    "weather": ["environmental"],
    # Financial
    "finance": ["financial"],
    "expense": ["financial"],
    "billing": ["financial"],
    # Occupational
    "jobs": ["occupational"],
    "projects": ["occupational"],
    "task": ["occupational"],
    "focus": ["occupational"],
    "staff": ["occupational"],
    "briefing": ["occupational"],
    "digest": ["occupational"],
    "github-connector": ["occupational"],
    "release": ["occupational"],
    "git": ["occupational"],
    "run": ["occupational"],
    "app-gen": ["occupational"],
    "plugin-gen": ["occupational"],
    "publish": ["occupational"],
    "system-log": ["occupational"],
    "model-bench": ["occupational"],
    "app-analytics": ["occupational"],
    "vault-analytics": ["occupational"],
    "ai-queue": ["occupational"],
    "cable": ["occupational"],
    "sheath-voltage": ["occupational"],
    # Multi-dim
    "journal": ["emotional", "intellectual"],
}

ID_RE = re.compile(r'^id\s*=\s*"([^"]+)"', re.M)
DIM_RE = re.compile(r"^dimensions\s*=", re.M)


def rewrite(mf: Path) -> str:
    text = mf.read_text(encoding="utf-8")
    m_id = ID_RE.search(text)
    if not m_id:
        return "no-id"
    app_id = m_id.group(1)
    dims = CLASSIFY.get(app_id)
    if not dims:
        return f"skip ({app_id})"
    if DIM_RE.search(text):
        return f"already-set ({app_id})"
    # insert dimensions line directly after description line (or id if no description)
    dim_line = 'dimensions = [' + ", ".join(f'"{d}"' for d in dims) + ']\n'
    lines = text.splitlines(keepends=True)
    insert_at = None
    in_app_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[app]":
            in_app_block = True
            continue
        if in_app_block and stripped.startswith("[") and stripped != "[app]":
            insert_at = i
            break
        if in_app_block and stripped.startswith("description"):
            insert_at = i + 1
            break
    if insert_at is None:
        return f"no-anchor ({app_id})"
    lines.insert(insert_at, dim_line)
    mf.write_text("".join(lines), encoding="utf-8")
    return f"added {dims} → {app_id}"


def main():
    apps_dirs = [ROOT / "apps", ROOT / "apps" / "personal"]
    results: dict[str, list[str]] = {"added": [], "skipped": [], "existing": []}
    for d in apps_dirs:
        if not d.exists():
            continue
        for mf in sorted(d.glob("*/manifest.toml")):
            r = rewrite(mf)
            if r.startswith("added"):
                results["added"].append(r)
            elif r.startswith("already"):
                results["existing"].append(r)
            else:
                results["skipped"].append(r)
    print(f"Added:    {len(results['added'])}")
    for r in results["added"]:
        print(f"  {r}")
    print(f"\nSkipped:  {len(results['skipped'])}")
    print(f"Existing: {len(results['existing'])}")


if __name__ == "__main__":
    main()

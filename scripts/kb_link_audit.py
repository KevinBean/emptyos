"""KB link integrity audit — broken related: slugs + [[wikilinks]].

Pure file I/O. For every KB note, check that:
  - each `related:` slug resolves to a KB note (any kb subdir)
  - each [[wikilink]] target (slug or 'slug#section' or 'slug|alias') resolves
    to a KB note OR a vault note elsewhere (we only flag links that look like
    KB-internal slugs, i.e. kebab-case with no spaces/path).
Run: python scripts/kb_link_audit.py
     python scripts/kb_link_audit.py --root 30_Resources/KB   # other vault corpus
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

from kb_paths import resolve_kb_root, vault_root  # co-located scripts/ sibling

VAULT = vault_root()

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
# inline markdown anchor link: [text](#some-slug) — the KB app resolves the
# hash to a KB note slug (a missing one renders the "Note not found" page).
ANCHOR_LINK = re.compile(r"\]\(#([a-z0-9][a-z0-9-]*)\)")


def kb_slugs(kb: Path) -> set[str]:
    return {p.stem for p in kb.rglob("*.md")}


def vault_stems() -> set[str]:
    return {p.stem for p in VAULT.rglob("*.md")}


def parse_related(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end < 0:
        return []
    body = text[3:end]
    out: list[str] = []
    in_rel = False
    for line in body.splitlines():
        if re.match(r"^related:\s*$", line):
            in_rel = True
            continue
        if in_rel:
            if line.startswith(" ") and line.strip().startswith("- "):
                slug = line.strip()[2:].strip().strip('"').strip("'")
                # normalize wikilink-style entries ("[[slug]]", "[[slug|alias]]")
                # the same way the kb app's _related_targets does
                if slug.startswith("[[") and slug.endswith("]]"):
                    slug = slug[2:-2]
                out.append(slug.split("|")[0].split("#")[0].strip())
            elif line.strip() and not line.startswith(" "):
                in_rel = False
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="KB root to scan (absolute, or relative to the vault); default: 30_Resources/EmptyOS/kb")
    args = ap.parse_args()
    KB = resolve_kb_root(args.root)
    if not KB.is_dir():
        print(f"no such KB root: {KB}")
        return 2

    kb = kb_slugs(KB)
    vault = vault_stems()
    rel_findings: list[str] = []
    wl_findings: list[str] = []
    anchor_findings: list[str] = []
    rel_total = wl_total = anchor_total = 0

    for p in sorted(KB.rglob("*.md")):
        rel = p.relative_to(KB)
        if "outputs" in rel.parts:
            continue  # AI-authored reports, not KB notes
        text = p.read_text(encoding="utf-8", errors="replace")

        for slug in parse_related(text):
            if not slug:
                continue
            rel_total += 1
            if slug not in kb and slug not in vault:
                rel_findings.append(f"{rel}: related -> {slug} (no such note)")

        # body wikilinks (skip frontmatter region)
        body = text
        if text.startswith("---"):
            e = text.find("\n---", 3)
            if e > 0:
                body = text[e + 4:]
        for m in WIKILINK.finditer(body):
            raw = m.group(1).strip()
            target = raw.split("|")[0].split("#")[0].strip()
            # only consider KB-style slugs (kebab-case, no spaces, no slashes)
            if not target or " " in target or "/" in target:
                continue
            if not re.match(r"^[a-z0-9][a-z0-9-]*$", target):
                continue
            wl_total += 1
            if target not in kb and target not in vault:
                wl_findings.append(f"{rel}: [[{target}]] (no such note)")

        # inline [text](#slug) body links — the KB viewer resolves these to KB
        # note slugs, so a missing one dead-ends on "Note not found".
        for m in ANCHOR_LINK.finditer(body):
            slug = m.group(1).strip()
            anchor_total += 1
            if slug not in kb and slug not in vault:
                anchor_findings.append(f"{rel}: [...](#{slug}) (no such note)")

    print(f"=== related: links checked: {rel_total} | broken: {len(rel_findings)} ===")
    for f in rel_findings:
        print("  " + f)
    print(f"=== KB-style [[wikilinks]] checked: {wl_total} | broken: {len(set(wl_findings))} ===")
    for f in sorted(set(wl_findings)):
        print("  " + f)
    # Advisory only: inline [...](#slug) links frequently point at intentional
    # "to be created" stubs, so this section is informational and does not gate.
    print(f"=== inline [...](#slug) body links checked: {anchor_total} | unresolved: {len(set(anchor_findings))} (advisory — may include intentional stubs) ===")
    for f in sorted(set(anchor_findings)):
        print("  " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

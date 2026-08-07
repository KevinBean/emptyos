"""KB claim audit — verify implemented_in paths + verified_against anchors resolve.

Pure file I/O (no kernel import). Walks the vault KB, parses frontmatter
implemented_in / verified_against, and checks:
  - implemented_in path (minus ::symbol) exists under the repo root
  - implemented_in ::symbol (if present) appears in that file
  - verified_against slug resolves to a KB note file (any kb subdir)
Run: python scripts/kb_claim_audit.py
     python scripts/kb_claim_audit.py --root 30_Resources/KB   # other vault corpus
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

from md_frontmatter import parse_fm  # co-located scripts/ siblings
from kb_paths import REPO, resolve_kb_root


def kb_slugs(kb: Path) -> set[str]:
    return {p.stem for p in kb.rglob("*.md")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="KB root to scan (absolute, or relative to the vault); default: 30_Resources/EmptyOS/kb")
    args = ap.parse_args()
    KB = resolve_kb_root(args.root)
    if not KB.is_dir():
        print(f"no such KB root: {KB}")
        return 2

    slugs = kb_slugs(KB)
    impl_findings: list[str] = []
    sym_findings: list[str] = []
    va_findings: list[str] = []
    unverifiable: list[str] = []
    impl_total = va_total = 0

    for p in sorted(KB.rglob("*.md")):
        if "outputs" in p.relative_to(KB).parts:
            continue  # AI-authored reports, not KB notes
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"READ-ERR {p}: {e}")
            continue
        fm = parse_fm(text)
        rel = p.relative_to(KB)

        impl = fm.get("implemented_in")
        impls = impl if isinstance(impl, list) else ([impl] if impl else [])
        for raw in impls:
            raw = (raw or "").strip()
            if not raw:
                continue
            if raw.startswith("method: "):
                # method-registry id, not a file path — needs the daemon to verify
                unverifiable.append(f"{rel}: {raw}")
                continue
            if raw.startswith("external: "):
                # a repo outside this one; nothing here can resolve it. Tallied
                # rather than dropped, because an unverifiable claim that is also
                # invisible is how a stale one survives.
                unverifiable.append(f"{rel}: {raw}")
                continue
            if raw.startswith("path: "):
                raw = raw[len("path: "):].strip()
            raw = raw.split(" — ")[0].strip()  # drop freehand "path — symbols" annotation
            impl_total += 1
            pathpart, _, sym = raw.partition("::")
            target = REPO / pathpart
            if not target.exists():
                impl_findings.append(f"{rel}: implemented_in MISSING -> {raw}")
                continue
            if sym:
                try:
                    src = target.read_text(encoding="utf-8")
                except Exception:
                    src = ""
                # dotted "Class.method" rarely appears literally — accept if every part does
                parts = [s for s in sym.split(".") if s]
                if sym not in src and not (parts and all(s in src for s in parts)):
                    sym_findings.append(f"{rel}: implemented_in symbol '{sym}' NOT FOUND in {pathpart}")

        va = fm.get("verified_against")
        vas = va if isinstance(va, list) else ([va] if va else [])
        for slug in vas:
            slug = (slug or "").strip().strip('"').strip("'")
            if slug.startswith("[[") and slug.endswith("]]"):
                slug = slug[2:-2]
            slug = slug.split("|")[0].split("#")[0].strip()
            if not slug:
                continue
            va_total += 1
            if slug not in slugs:
                va_findings.append(f"{rel}: verified_against MISSING KB note -> {slug}")

    print(f"=== implemented_in paths checked: {impl_total} | broken: {len(impl_findings)} ===")
    for f in impl_findings:
        print("  " + f)
    print(f"=== implemented_in ::symbols broken: {len(sym_findings)} ===")
    for f in sym_findings:
        print("  " + f)
    print(f"=== verified_against anchors checked: {va_total} | missing: {len(va_findings)} ===")
    for f in va_findings:
        print("  " + f)
    print(
        f"=== implemented_in unverifiable from here: {len(unverifiable)} "
        "(advisory — method-registry ids need the daemon, external: names another repo) ==="
    )
    for f in unverifiable:
        print("  " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

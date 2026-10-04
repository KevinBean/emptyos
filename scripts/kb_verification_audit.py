#!/usr/bin/env python
"""KB verification audit — which numeric clause/case notes has nobody compared
to the print, and which of those does an engine or a calculator lean on?

    python scripts/kb_verification_audit.py            # counts + the engine-backed list
    python scripts/kb_verification_audit.py --all      # every numeric transcribed note
    python scripts/kb_verification_audit.py --sample 5 # hand N to an eos-citation-verify session
    python scripts/kb_verification_audit.py --json

Population: clause/case notes whose body carries a fence, a markdown table row
or an equation line (a formula, a constant, a table value — the content that
text extraction garbles and that the 2026-09-30 IEC 60949 incident was made
of). Tier per note comes from the kb app's own ``note_verification`` (loaded
from ``apps/public/standard/kb/shared.py``, never a second copy): ``pdf`` /
``fulltext`` / ``transcribed``.

Findings vs coverage (``.claude/rules/audits.md`` failure mode 1): only the
**engine-backed** transcribed notes are listed — a note with ``implemented_in``
or one named in an app manifest ``references = ["[[slug]]"]``. Every other
transcribed note is a count. Measured on the live corpus 2026-09-30: 1325
clause/case notes, 511 numeric (0 pdf / 18 fulltext / 493 transcribed), 31
engine-backed transcribed — every one of the 31 is a note an engine test, a
conformance case or a manifest reference names, so the list is signal. The
list is short and the count is large, which is the correct shape; do not
widen the list to make the count look worked on.

Always exits 0 (advisory, like the other ``kb`` preflight rows): a transcribed
note is the honest state until someone opens the page, not a defect. The act
stage is a person or a session running ``eos-citation-verify`` in KB clause-note
mode on the ``--sample`` output; nothing here can mark a note verified.
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # co-located siblings
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))      # emptyos.sdk for kb/shared.py

from audit_kb_app_alignment import _manifest_kb_refs  # noqa: E402
from kb_paths import REPO, resolve_kb_root  # noqa: E402
from md_frontmatter import parse_frontmatter  # noqa: E402
from scanner_lib import emit_json  # noqa: E402

# A fence, a table row, or an equation line (`=` plus a maths glyph or a super/
# subscript). Deliberately loose: it decides which notes are IN the population,
# and a note that carries none of these has nothing a page read could correct.
NUMERIC_RE = re.compile(
    r"(^```)|(^\|.*\|\s*$)|(^[^\n]*=[^\n]*[√∑∫αβγδεζηθικλμνξπρστυφχψωΩΓΔΘΛΞΠΣΦΨ⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉])",
    re.M,
)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
KINDS = ("clause", "case")


def load_note_verification():
    """The kb app's classifier, by path — one tier logic, not a copy."""
    path = REPO / "apps" / "public" / "standard" / "kb" / "shared.py"
    spec = importlib.util.spec_from_file_location("eos_kb_shared_for_audit", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.note_verification


def manifest_referenced_slugs(manifests_root: Path) -> dict[str, list[str]]:
    """slug -> [manifest paths] for every ``[[slug]]`` in a manifest ``references``."""
    out: dict[str, list[str]] = {}
    if not manifests_root.is_dir():
        return out
    for path in sorted(manifests_root.rglob("manifest.toml")):
        if any(part.startswith("_") for part in path.relative_to(manifests_root).parts):
            continue
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        rel = path.relative_to(manifests_root.parent).as_posix() if manifests_root.parent in path.parents else path.as_posix()
        for kind, slug in _manifest_kb_refs(data):
            if kind == "manifest_reference" and rel not in out.setdefault(slug, []):
                out[slug].append(rel)
    return out


def scan(kb_root: Path, manifests_root: Path, note_verification) -> dict:
    referenced = manifest_referenced_slugs(manifests_root)
    by_tier: Counter = Counter()
    transcribed: list[dict] = []
    total = 0
    for f in sorted(list(kb_root.glob("sources/*.md")) + list(kb_root.glob("notes/*.md"))):
        try:
            fm, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if fm.get("kind") not in KINDS or not NUMERIC_RE.search(body):
            continue
        total += 1
        ver = note_verification(fm, SECTION_RE.findall(body))
        by_tier[ver["tier"]] += 1
        if ver["tier"] != "transcribed":
            continue
        slug = f.stem
        reasons = []
        impl = fm.get("implemented_in")
        if impl:
            reasons.append("implemented_in: " + ", ".join(str(x) for x in (impl if isinstance(impl, list) else [impl])))
        if slug in referenced:
            reasons.append("manifest references: " + ", ".join(referenced[slug]))
        transcribed.append({
            "slug": slug,
            "path": f.relative_to(kb_root).as_posix(),
            "engine_backed": bool(reasons),
            "reasons": reasons,
        })
    engine_backed = [t for t in transcribed if t["engine_backed"]]
    return {
        "population": total,
        "by_tier": dict(by_tier),
        "engine_backed_transcribed": engine_backed,
        "transcribed": transcribed,
        "manifest_referenced_slugs": len(referenced),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kb-root", default=None, help="KB root (default: the vault's EmptyOS KB)")
    ap.add_argument("--manifests-root", default=None, help="apps/ tree to read manifests from")
    ap.add_argument("--all", action="store_true", help="list every numeric transcribed note, not only engine-backed")
    ap.add_argument("--sample", type=int, default=0, help="print N slugs to hand to eos-citation-verify")
    ap.add_argument("--seed", type=int, default=None, help="make --sample reproducible")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        kb_root = Path(args.kb_root) if args.kb_root else resolve_kb_root(None)
    except Exception as e:  # no vault on this machine — advisory, not a failure
        if args.json:
            return emit_json(True, "ok", f"no vault: {e}", {"population": 0})
        print(f"kb_verification_audit: no vault ({e}); nothing to scan")
        return 0
    if not kb_root.is_dir():
        if args.json:
            return emit_json(True, "ok", f"no KB root at {kb_root}", {"population": 0})
        print(f"kb_verification_audit: no KB root at {kb_root}; nothing to scan")
        return 0
    manifests_root = Path(args.manifests_root) if args.manifests_root else REPO / "apps"
    result = scan(kb_root, manifests_root, load_note_verification())

    listed = result["transcribed"] if args.all else result["engine_backed_transcribed"]
    if args.sample:
        pool = result["engine_backed_transcribed"] or result["transcribed"]
        rng = random.Random(args.seed)
        listed = rng.sample(pool, min(args.sample, len(pool)))

    if args.json:
        return emit_json(True, "ok",
                         f"{result['population']} numeric clause/case notes; "
                         f"{len(result['engine_backed_transcribed'])} engine-backed transcribed",
                         {**{k: v for k, v in result.items() if k != "transcribed"}, "listed": listed})

    t = result["by_tier"]
    print(f"KB verification — {result['population']} numeric clause/case notes: "
          f"pdf {t.get('pdf', 0)} · fulltext {t.get('fulltext', 0)} · transcribed {t.get('transcribed', 0)}")
    print(f"engine-backed transcribed (an engine or a manifest leans on these): "
          f"{len(result['engine_backed_transcribed'])}")
    for row in listed:
        why = "; ".join(row["reasons"]) or "numeric, not engine-backed"
        print(f"  - {row['slug']}  [{why}]")
    if args.sample:
        print(f"\n{len(listed)} note(s) for eos-citation-verify (KB clause-note mode).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

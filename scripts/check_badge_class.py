#!/usr/bin/env python3
"""scripts/check_badge_class.py — every emitted `eos-badge-*` class must exist in CSS.

`EOS_UI.statusBadge` / `statusBadgeClass` own the class concatenation precisely
because call sites got it wrong (see the comment block above them in
`emptyos/web/static/eos-components.js`). A page that assembles the class by hand
has two ways to emit a class no stylesheet defines, and **both fail silently** —
the badge renders, the word is right, and only the colour is missing:

  1. unknown token   `'eos-badge-status-' + st` where st is `recorded` / `mixed`
                     — a status outside the shared 11-name STATUS_VARIANTS set.
  2. double prefix   `'eos-badge-status-' + statusVariant(x)` — statusVariant
                     already returns `status-active`, so this emits
                     `eos-badge-status-status-active`.

Both collapse to one provable question: **does the full class name the page
emits have a rule in eos-components.css?** No judgment, no threshold — the CSS
either defines `.eos-badge-<x>` or it does not. That is what earns a gate, where
the broader signal ("this page hand-rolls a badge") does not: of the 6 hand-rolled
sites measured on the tree at graduation (2026-08-28), 4 resolved to valid
variants and were merely bypassing the helper. Reporting those 4 would be the
>30% false-positive band `.claude/rules/audits.md` says never to ship.

Resolution. A literal class is checked directly. A concatenation is resolved to
its candidate token set by four shapes, all of which appear in the tree:

  S1 ternary        `'eos-badge-status-' + (closed ? 'completed' : 'active')`
  S2 guard map      `known[st] ? 'eos-badge-status-' + st : ...`   -> keys of `known`
  S3 value map      `var b = MAP[x] || 'draft'` ... + b            -> values of MAP + default
  S4 helper         `+ EOS_UI.statusVariant(...)`                  -> safe by construction

Anything else is reported `unresolved` and is **advisory, never a finding** —
a scanner that silently passes what it could not read is the vacuous pass
`.claude/rules/audits.md` warns about, so the unreadable case is stated out loud
rather than counted as clean.

Exit code = number of hard findings (exit-code-as-signal, `.claude/rules/agent-cli.md`).
**Gates** in preflight (`ui`, `release`): the 19 findings it surfaced across 10
apps at registration were all fixed the same day (2026-08-28), so a healthy tree
is silent. Runtime-valued concatenations it cannot read — a lookup keyed on live
data, an app-local `badge(label, variant)` wrapper — are listed separately as
advisory and never counted, so the exit code stays a statement about what was
actually proven rather than about what happened to parse.
Pure file I/O — does NOT import emptyos.kernel (safe while the daemon is up,
`.claude/rules/daemon-handling.md`).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json, page_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSS_REL = Path("emptyos") / "web" / "static" / "eos-components.css"

# `class="... eos-badge-<tok> ..."` — a fully literal class in markup.
RE_LITERAL = re.compile(r"\beos-badge-([a-z0-9][a-z0-9-]*)\b")
# `'...eos-badge-status-' +` — a literal class prefix being concatenated onto.
RE_CONCAT = re.compile(
    r"[\"']([^\"']*\beos-badge-(?:status-|priority-|age-|level-)?)[\"']\s*\+\s*"
)
RE_TERNARY = re.compile(
    r"\?\s*['\"]([a-z0-9-]+)['\"]\s*:\s*['\"]([a-z0-9-]*)['\"]"
)
# An identifier that is the WHOLE operand — a trailing `.` or `(` means the value
# comes from a property or a call, and resolving the bare name would read an
# unrelated binding. podcast's `'eos-badge-' + src.cls` resolved that way to a
# ternary elsewhere in the file and reported two classes that expression cannot
# emit; a wrong answer from an unsound path is worse than admitting `unresolved`.
RE_IDENT = re.compile(r"\(?\s*([A-Za-z_$][\w$]*)\s*(?![\w$.(\[])")
RE_OBJ_LITERAL = re.compile(r"\{([^{}]*)\}")
# A `//` line comment, but not the `//` inside a `http://` URL.
RE_LINE_COMMENT = re.compile(r"(?<![:\w])//[^\n]*")
RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)
RE_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)


def _badge_rules(text: str) -> set[str]:
    return set(re.findall(r"\.(eos-badge-[a-z0-9][a-z0-9-]*)", text))


def allowed_classes(root: Path) -> set[str]:
    """Full `eos-badge-*` class names that have a rule in the shared stylesheet."""
    css = root / CSS_REL
    if not css.is_file():
        return set()
    return _badge_rules(css.read_text(encoding="utf-8", errors="replace"))


def local_classes(path: Path, src: str) -> set[str]:
    """Badge rules a page defines for itself — inline `<style>` + sibling `.css`.

    Rare (4 pages define any `.eos-badge-*` locally) but not optional: an app
    that extends the family for its own vocabulary is doing something legal, and
    reading only the shared stylesheet would report it as a defect. This is the
    same blind spot `check_ui_structure.py` had to close when apps started
    extracting page CSS to a sibling file per `.claude/rules/multi-module-apps.md`.
    """
    found: set[str] = set()
    for block in RE_STYLE_BLOCK.findall(src):
        found |= _badge_rules(block)
    for sibling in sorted(path.parent.glob("*.css")):
        try:
            found |= _badge_rules(sibling.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return found


def blank_comments(src: str) -> str:
    """Replace comment bodies with spaces, preserving every offset and newline.

    `eos-components.js` documents the double-prefix bug by writing the broken
    class out in prose, so a scanner that reads comments reports the warning as
    the defect. Blanking rather than deleting keeps line numbers exact.
    """
    def blank(m: re.Match[str]) -> str:
        return "".join(c if c == "\n" else " " for c in m.group(0))

    return RE_LINE_COMMENT.sub(blank, RE_BLOCK_COMMENT.sub(blank, src))


def _object_tokens(src: str, name: str, *, keys: bool) -> set[str]:
    """Tokens from `<name> = { ... }` — its keys, or its string values."""
    m = re.search(r"\b" + re.escape(name) + r"\s*=\s*", src)
    if not m:
        return set()
    obj = RE_OBJ_LITERAL.search(src, m.end())
    if not obj or obj.start() > m.end() + 8:  # the literal must follow the `=`
        return set()
    body = obj.group(1)
    if keys:
        return set(re.findall(r"['\"]?([A-Za-z_][\w-]*)['\"]?\s*:", body))
    return set(re.findall(r":\s*['\"]([a-z0-9-]+)['\"]", body))


def resolve(src: str, region: str) -> tuple[set[str], str]:
    """Candidate tokens for a concatenated class suffix, and how they were found."""
    head = region[:200]

    if "statusVariant" in head[:48] or "statusBadgeClass" in head[:48]:
        return set(), "helper"  # S4 — the sanctioned owner of the concatenation

    stripped = head.lstrip()
    if stripped.startswith("("):  # S1 — inline ternary literals
        tern = RE_TERNARY.search(stripped[:120])
        if tern:
            return {t for t in tern.groups() if t}, "ternary"

    ident = RE_IDENT.match(head)
    if not ident:
        return set(), "unresolved"
    name = ident.group(1)

    guard = re.search(
        r"\b([A-Za-z_$][\w$]*)\s*\[\s*" + re.escape(name) + r"\s*\]", src
    )
    if guard:  # S2 — membership guard; its map's KEYS are the reachable tokens
        toks = _object_tokens(src, guard.group(1), keys=True)
        if toks:
            return toks, "guard-map"

    assign = re.search(
        r"\b" + re.escape(name) + r"\s*=\s*([A-Za-z_$][\w$]*)\s*\[[^\]]*\]"
        r"\s*(?:\|\|\s*['\"]([a-z0-9-]+)['\"])?",
        src,
    )
    if assign:  # S3 — assigned from a lookup table; its VALUES are the tokens
        toks = _object_tokens(src, assign.group(1), keys=False)
        if assign.group(2):
            toks.add(assign.group(2))
        if toks:
            return toks, "value-map"

    inline = re.search(r"\b" + re.escape(name) + r"\s*=\s*\(?[^;\n]*?\?", src)
    if inline:
        tern = RE_TERNARY.search(src, inline.end() - 1)
        if tern:
            return {t for t in tern.groups() if t}, "ternary"

    return set(), "unresolved"


def scan_text(raw: str, rel: str, allowed: set[str]) -> tuple[list[dict], list[dict]]:
    """Findings + unresolved sites for one file's source."""
    findings: list[dict] = []
    unresolved: list[dict] = []
    src = blank_comments(raw)

    for m in RE_CONCAT.finditer(src):
        prefix = m.group(1).rsplit(" ", 1)[-1]
        if not prefix.startswith("eos-badge-"):
            continue
        toks, how = resolve(src, src[m.end():])
        if how == "helper":
            continue
        line = src.count("\n", 0, m.start()) + 1
        if how == "unresolved":
            unresolved.append({"file": rel, "line": line, "prefix": prefix})
            continue
        bad = sorted(t for t in toks if prefix + t not in allowed)
        if bad:
            findings.append({
                "file": rel, "line": line, "prefix": prefix, "how": how,
                "tokens": bad, "emits": [prefix + t for t in bad],
            })

    for m in RE_LITERAL.finditer(src):
        tok = m.group(1)
        cls = "eos-badge-" + tok
        if cls in allowed:
            continue
        if m.start() and src[m.start() - 1] in ".-":
            continue  # a CSS selector, or the tail of a longer token
        # `\b` stops before the `-` in `'eos-badge-status-' + x`, so the prefix
        # of every concatenation also reaches here as the token `status`. That
        # site is the CONCAT rule's, and reporting it twice would put a finding
        # on each of the four call sites that concatenate a *valid* variant.
        if m.end() < len(src) and src[m.end()] == "-":
            continue
        findings.append({
            "file": rel, "line": src.count("\n", 0, m.start()) + 1,
            "prefix": "eos-badge-", "how": "literal",
            "tokens": [tok], "emits": [cls],
        })
    return findings, unresolved


def collect_files(root: Path) -> list[Path]:
    """App pages, plus the shared bundle — a broken class there reaches every page."""
    files = [p for p in page_files(root) if not p.name.endswith(".legacy.html")]
    static = root / "emptyos" / "web" / "static"
    if static.is_dir():
        files += sorted(
            p for p in static.glob("*.js") if not p.name.endswith(".min.js")
        )
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="Undefined eos-badge-* class scanner.")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    ap.add_argument("--root", default=str(ROOT), help="repo root to scan")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    allowed = allowed_classes(root)
    files = collect_files(root)

    findings: list[dict] = []
    unresolved: list[dict] = []
    for path in files:
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        hits, unread = scan_text(src, rel, allowed | local_classes(path, src))
        findings += hits
        unresolved += unread

    if args.json:
        return emit_json(
            not findings,
            "ok" if not findings else "badge_class_undefined",
            f"{len(findings)} undefined badge class(es), "
            f"{len(unresolved)} unresolved",
            {
                "findings": findings,
                "unresolved": unresolved,
                "scanned": len(files),
                "allowed": sorted(allowed),
            },
        )

    for f in findings:
        print(
            f"  {f['file']}:{f['line']}  emits {', '.join(f['emits'])} "
            f"- no CSS rule (via {f['how']}); use EOS_UI.statusBadge()"
        )
    if unresolved:
        print(
            f"\n  advisory - {len(unresolved)} hand-built badge class(es) whose "
            f"tokens could not be read statically:"
        )
        for u in unresolved:
            print(f"    {u['file']}:{u['line']}  {u['prefix']}+<expr>")
    print(
        f"\n{len(findings)} undefined badge class(es) "
        f"across {len(files)} files scanned."
    )
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())

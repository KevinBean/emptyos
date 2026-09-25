#!/usr/bin/env python3
"""check_hardcoded_hex — DL-1: a literal hex colour where a theme token belongs.

`docs/FRONTEND-DESIGN-LANGUAGE.md` §1/§4 asks every colour in an app page to
come from `theme.css`. A literal is frozen where the token adapts, so it paints
the same pixels on all ten themes — the defect the 2026-07-11 readability audit
found at scale (a light `#f5f5f7` code block invisible on a dark theme; a dark
`#0d0d0d` panel invisible on a light one).

`check-text-tokens.py` already covers the four *high-signal* shapes (T1-T4:
`#fff` on `--accent`, status hex as text, `:root` token override, status hex on
a surface). This scanner covers the **general** case those four deliberately
leave out, and it exists because nothing did: every run of the
`eos-design-system-audit` skill hand-rolled the scan, and on 2026-09-03 that
hand-rolled version shipped four separate filter bugs in one session — three of
which pointed the migration at the *best-behaved* apps.

Those four bugs are the exclusion list, and each one is load-bearing:

  1. `/_retired/` is not loaded by the app loader (`app_layout._SKIP_TOP`), so a
     finding there is in code that cannot run. It supplied the top two
     "offenders" (195 hits).
  2. `var(--token, #fallback)` — the token is already correctly wired and the
     fallback is documented as acceptable. The comma satisfies the value-anchor,
     so it reads as a violation; it was 86 of 189 remaining hits, concentrated in
     apps that had done the right thing.
  3. Brand islands — a file declaring its own `--xx-*` namespace is a deliberate
     visual island whose palette IS the design. Detected mechanically via
     `scanner_lib.is_brand_island`, never re-derived here.
  4. (A `<meta name="theme-color" content="#...">` exclusion used to sit here.
     It was DEAD once the quote-anchor landed — the value is quote-opened, so it
     was already out of scope — and its test passed against a scanner with the
     rule deleted. Removed rather than kept as decoration.)

Two further exclusions are measured rather than inherited, and both had to exist
before the signal separated at all — see `_SKIP_LITERALS` and `_looks_like_ramp`.

Opt-out lives at the call site, never in a central allowlist here (a central
list turns every new legitimate island into a build break —
`.claude/rules/audits.md`):

    <!-- design-hex: island — standalone doc, does not load theme.css -->
    /* design-hex: island — ground matched to a fixed-dark renderer */
    background: #0d1117;  /* design-hex: ignore — the viz artifact palette */

A file-level `island` marker exempts the whole file; an `ignore` marker exempts
**exactly the line it sits on**, so it goes inline at the end of that line. A
wider "until the next blank line" form was tried and dropped: this codebase
writes dense single-line CSS rules with no blank lines between them, so the
exemption would have run to the end of the block and silently covered hexes
added later. Both markers require a reason after the dash — a bare marker is
refused, so every exemption carries its own audit context.

Graduation (`.claude/rules/audits.md`): static scan, no daemon. Registered in
`scripts/preflight.py` under the `ui` scope.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `scripts/theme_css.py` is a back-compat shim; its own docstring says to import
# the SDK module in new code.
from emptyos.sdk.theme_tokens import global_token_prefixes  # noqa: E402
from scanner_lib import SKIP_PAGE_DIRS, emit_json, is_brand_island, page_files  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: Beyond scanner_lib's set: build output and dead code kept for reference.
#: `.legacy.` is a *filename* convention, not a directory — a filter matching
#: only `/legacy/` misses `index.legacy.html`, which is how 32 hex in an
#: unreferenced file once became a run's top recommendation.
_EXTRA_SKIP = ("/dist/", "/legacy/", "tailwind")

#: `apps/personal/` is gitignored, so it is absent from CI and from every fresh
#: clone — this gate protects what ships. It is also the one tree a review pass
#: is told not to edit (`eos-simplify` § Safety), and a gate whose findings
#: nobody may fix is a gate that gets switched off. Measured 2026-09-03: 113 of
#: 198 findings were personal, all in apps the author alone maintains. Pass
#: `--include-personal` to see them locally; do not wire that into preflight.
_PERSONAL = "apps/personal/"

#: A hex used as a colour sits after `:` `,` `(` or whitespace. The anchor — not
#: a length bound — is what excludes HTML entities (`&#8203;`). Both lengths are
#: matched: an earlier 6-only version claimed 3-digit forms "almost never
#: appear" (there are hundreds), and missing them left a base `pre` rule
#: hardcoded while its own inline overrides were migrated to tokens. The 4- and
#: 8-digit alpha forms are matched too — `_VAR_FALLBACK` already accepted
#: `{3,8}`, so excluding them here was an inconsistency inside one file, and
#: `#0d0d0dcc` is as hardcoded as `#0d0d0d`.
_HEX = re.compile(
    r"(?<=[:,(\s])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b"
)

#: DELIBERATELY NOT MATCHED: a hex opened by a quote rather than sitting in a
#: CSS value position — `fill="#123456"`, `ctx.fillStyle = '#abcdef'`,
#: `data-color="#abcdef"`. Measured 2026-09-03: widening the anchor to accept
#: `"` and `'` takes the tree from 0 findings to 304 across 49 files, and the
#: mass of them is canvas/SVG drawing code and chart palettes where the literal
#: IS the correct value — far past the 30% false-positive bar in
#: `.claude/rules/audits.md`, and this scanner gates. The design language grants
#: SVG `fill="#..."` an explicit exception for icons that must not re-theme.
#: This boundary is a decision, not an accident of the regex; it is pinned by
#: `test_a_quote_opened_hex_is_out_of_scope` so nobody "fixes" it silently.

#: The token is wired; the fallback is documented as acceptable.
_VAR_FALLBACK = re.compile(r"var\(\s*--[\w-]+\s*,\s*#[0-9a-fA-F]{3,8}\s*\)")

_MARKER = re.compile(r"design-hex:\s*(island|ignore)\s*[-—]\s*(.*)")
#: The reason must be real prose, not the comment terminator. A naive
#: "dash then a non-space" test passes `<!-- design-hex: island -->` — the `-`
#: of `-->` IS the non-space — which would let a reasonless exemption ship
#: while the rule claiming to forbid it looked satisfied. Caught by
#: `test_island_marker_without_a_reason_is_refused`, not by review.
_REASON = re.compile(r"[A-Za-z]{3}")


def _marked(line: str, kind: str) -> bool:
    """True when `line` carries a `design-hex: <kind>` marker WITH a reason."""
    for found, rest in _MARKER.findall(line):
        if found != kind:
            continue
        reason = rest.replace("-->", " ").replace("*/", " ").strip()
        if _REASON.search(reason):
            return True
    return False

#: A hex named in prose is not a declaration. The assistant page documents its
#: measured contrast ratio ("raw #da7756 measured 2.33:1 on digital-garden") in
#: a comment above the rule — flagging that reads as drift and, worse, punishes
#: the file for explaining itself. Testing only the line *start* is not enough:
#: that sentence is a continuation line of a `/* */` block, so it begins with a
#: word. Comment spans have to be tracked properly.
_COMMENT_SPAN = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)
#: `//` only starts a comment when it opens one. Excluding just `://` is not
#: enough: a PROTOCOL-RELATIVE url (`href="//cdn.example/x"`) has no scheme, so
#: a scheme-only guard treats it as a comment, blanks the rest of that line and
#: silently hides every colour after it.
_LINE_COMMENT = re.compile(r"""(?<![:/\w"'])//[^\n]*""")


def _blank_comments(text: str) -> str:
    """Replace comment bodies with spaces, preserving every newline.

    Line numbers must survive so a finding still points at the real line, which
    rules out simply deleting the spans.
    """

    def blank(m: re.Match[str]) -> str:
        return "".join(c if c == "\n" else " " for c in m.group(0))

    return _LINE_COMMENT.sub(blank, _COMMENT_SPAN.sub(blank, text))

def _comments_only(text: str) -> str:
    """Keep comment bodies, blank the code. Line-count preserving.

    The exact inverse of `_blank_comments`, and the projection markers are read
    from — so a marker written inside a *string literal* is not a marker. Read
    from the raw text instead, a page that merely documents this convention
    (a design-system viewer, a `<pre>` sample, this scanner's own help output)
    would silently exempt itself.
    """
    cover = bytearray(len(text))
    spans = [m.span() for m in _COMMENT_SPAN.finditer(text)]
    for m in _LINE_COMMENT.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            spans.append(m.span())
    for a, b in spans:
        for i in range(a, b):
            cover[i] = 1
    return "".join(
        ch if (ch == "\n" or cover[i]) else " " for i, ch in enumerate(text)
    )


#: A file-level `island` marker is honoured only inside this many leading lines.
#: An exemption appended *below* the code it silences is invisible to a reviewer
#: reading top-down, and would let a marker be bolted onto the bottom of a file
#: to quiet a gate rather than to describe the file.
_ISLAND_HEADER_LINES = 40


#: Pure black and white are not theme colours and have no token to migrate to.
#: They are the ink/paper of things that must not re-theme: a QR code's quiet
#: zone (a themed ground breaks the scanner), a print stylesheet, an SVG mask,
#: `#fff` as inverse text on an already-tinted button (a documented skip).
#: Measured: including them adds 180 findings and not one is actionable.
_SKIP_LITERALS = {"#fff", "#ffffff", "#000", "#000000"}


def _is_ink_or_paper(hexval: str) -> bool:
    """Black or white, with or without an alpha suffix.

    The alpha forms are the same class as the opaque ones: `#0008` is a scrim,
    not a theme colour. Matching only the opaque forms would flag `#0008` while
    skipping `#000` on the neighbouring line — an inconsistency that reads as a
    bug in the scanner rather than a rule.
    """
    v = hexval.lower()
    base = v[:4] if len(v) == 5 else v[:7] if len(v) == 9 else v
    return base in _SKIP_LITERALS

#: A line declaring three or more distinct hex values is a *palette* — a
#: categorical chart series, a gradient stop list, a speaker/phase colour table.
#: Those are content, not chrome: they carry identity, so re-theming them would
#: destroy the distinction they exist to draw. The threshold is measured, not
#: guessed — two hexes is routinely one fg/bg pair that SHOULD be tokens.
_RAMP_MIN = 3


def _looks_like_ramp(declaration: str) -> bool:
    """True when ONE declaration value lists `_RAMP_MIN`+ distinct hexes.

    Scoped to a single declaration, never the whole line. This codebase writes
    dense one-line rules, so a per-line test would read
    `.a{color:#111;background:#222;border-color:#333}` — three unrelated
    properties, i.e. exactly the drift this scanner exists to catch — as a
    palette and skip it.
    """
    return len(set(_HEX.findall(declaration))) >= _RAMP_MIN


def _skip_path(rel_posix: str, *, include_personal: bool) -> bool:
    """`rel_posix` is the path RELATIVE to the scan root, never the absolute one.

    Matching `/apps/personal/` against an absolute path looks equivalent and is
    not: called with a relative root (`scan(Path("."))`, which is how an ad-hoc
    probe reaches it) the paths carry no leading slash, the substring never
    matches, and the personal tree is silently scanned after all. The failure is
    invisible — it produces *more* findings, so it reads as the scanner working
    harder rather than as a broken exclusion.
    """
    if any(s in rel_posix for s in _EXTRA_SKIP) or ".legacy." in rel_posix:
        return True
    return rel_posix.startswith(_PERSONAL) and not include_personal


def scan(root: Path, *, include_personal: bool = False) -> tuple[list[dict], int]:
    """Return (findings, files_scanned)."""
    prefixes = global_token_prefixes()
    files = [
        p
        for p in page_files(root, suffixes=(".html", ".css", ".js"), skip=SKIP_PAGE_DIRS)
        if not _skip_path(p.relative_to(root).as_posix(), include_personal=include_personal)
    ]
    findings: list[dict] = []
    auto_islands: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        # Markers are read off a COMMENT-ONLY projection, never the raw text: a
        # marker is a comment, and reading raw means the string
        # `var doc = 'design-hex: island - explaining the convention'` exempts
        # the whole file. A page that documents this very convention — a
        # design-system viewer, a <pre> sample — would self-exempt.
        comments = _comments_only(text)
        # An `island` marker must sit in the file HEADER. Read from anywhere it
        # could be appended below the code it silences, where a reviewer reading
        # top-down never sees it.
        if _marked("\n".join(comments.splitlines()[:_ISLAND_HEADER_LINES]), "island"):
            continue
        if is_brand_island(text, global_prefixes=prefixes):
            auto_islands.append(rel)
            continue
        raw_lines = text.splitlines()
        comment_lines = comments.splitlines()
        lines = _blank_comments(text).splitlines()
        for n, line in enumerate(lines, 1):
            if _marked(comment_lines[n - 1], "ignore"):
                continue
            stripped = _VAR_FALLBACK.sub("var(--x)", line)
            for decl in stripped.split(";"):
                if _looks_like_ramp(decl):
                    continue
                for hit in _HEX.findall(decl):
                    if _is_ink_or_paper(hit):
                        continue
                    findings.append(
                        {
                            "file": rel,
                            "line": n,
                            "hex": hit,
                            "text": raw_lines[n - 1].strip()[:120],
                        }
                    )
    return findings, len(files), auto_islands


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument(
        "--include-personal",
        action="store_true",
        help="also scan apps/personal/ (gitignored; local inspection only, never in preflight)",
    )
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    findings, n_files, auto_islands = scan(root, include_personal=args.include_personal)
    by_file: dict[str, int] = {}
    for f in findings:
        by_file[f["file"]] = by_file.get(f["file"], 0) + 1

    # An auto-detected island buys a whole-file exemption with no marker, no
    # reason, and nothing in the diff — a wider escape hatch than the markers
    # this scanner argues for. Printing the count is what keeps its growth
    # visible; a page that adds three `--myapp-*` tokens must not go dark
    # silently. `test_auto_island_count_is_pinned` caps it.
    islands = f"{len(auto_islands)} file(s) auto-exempt as brand islands"

    if args.json:
        return emit_json(
            not findings,
            "hardcoded_hex",
            f"{len(findings)} hardcoded hex across {len(by_file)} file(s); {islands}",
            {
                "findings": findings,
                "files_scanned": n_files,
                "auto_islands": auto_islands,
            },
        )

    if not findings:
        print(f"clean — scanned {n_files} page file(s), no hardcoded hex; {islands}.")
        return 0

    print(f"check_hardcoded_hex — {len(findings)} literal(s) across {len(by_file)} file(s)")
    print("-" * 60)
    for rel in sorted(by_file, key=lambda k: -by_file[k]):
        print(f"  {by_file[rel]:3d}  {rel}")
        for f in [x for x in findings if x["file"] == rel][:4]:
            print(f"        {f['line']}: {f['text']}")
    print("-" * 60)
    print("Use a theme.css token, or mark the call site:")
    print("  <!-- design-hex: island — <why this file has its own palette> -->")
    print("  background: #hex;  /* design-hex: ignore — <why this literal> */")
    # NOT `len(findings)`: an exit status is masked to its low 8 bits, so
    # exactly 256 findings would exit 0 and the gate would report success on its
    # worst-ever run. Sibling scanners return the count; this one gates.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Flag EOS_UI.confirm() options the component cannot read.

`EOS_UI.confirm` takes a small vocabulary. A key outside it is *dropped in
silence* — there is no error, no console warning, and the dialog still opens.
That is why this is worth a checker rather than a code review: the failure is
invisible in the diff, invisible to `node --check`, and invisible to a 200.

Measured 2026-07-31 across 134 object-form call sites, before the component
learned the aliases:

  * 11 sites in 6 apps passed keys that were being dropped.
  * Two rendered a bare **"Are you sure?"** behind a *primary* button on a
    destructive action — `/settings` "restart every local Python process" and
    `vault-backup` snapshot restore.
  * One (`substation-project` delete) passed its callback as `onConfirm`, which
    was never wired, so clicking **Delete** ran nothing at all.

The component now accepts every alias that survey found, so a healthy tree is
silent. This guards the *next* key nobody thought to support.

Exit code is the number of findings (0 = clean), per
`.claude/rules/agent-cli.md`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

# Everything eos-components.js::confirm reads. Keep in sync with that function
# — adding an alias there without adding it here turns a working call into a
# false positive, which is how a gating checker gets disabled.
KNOWN = {
    "message", "body",                                   # → message
    "action", "confirmText", "confirmLabel",             # → action
    "okText", "okLabel",                                 # → action
    "cancelLabel", "cancelText",                         # → cancel
    "danger",                                            # → button style
    "title",                                             # → modal title
    "onYes", "onConfirm",                                # → callback
}

SKIP_DIRS = {"node_modules", "dist", "vendor", ".git", ".codex-tmp", "__pycache__"}
IGNORE = re.compile(r"confirm-keys:\s*ignore", re.I)


def _top_level_keys(src: str, brace_at: int) -> set[str]:
    """Keys of the object literal at `brace_at` — top level only, string-aware.

    Two false positives this deliberately avoids, both measured on healthy code:

    * A naive brace counter walks through `'...{...'` and template literals and
      swallows whatever follows.
    * Depth matters more. The common shape is
      ``{message: …, onYes: function(){ fetch(u, {method:'POST'}) }}`` — that
      ``method`` is lexically inside the confirm literal, and counting it flagged
      five known-good apps (learn ×2, daily-brief, cable-pulling, devboard) on
      this scan's first run. Only depth-1 keys are options.
    """
    keys: set[str] = set()
    depth = 0
    quote = None
    i = brace_at
    key_re = re.compile(r"\s*([A-Za-z_$][\w$]*)\s*:")
    while i < len(src):
        ch = src[i]
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
            if depth == 1:
                m = key_re.match(src, i + 1)
                if m:
                    keys.add(m.group(1))
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return keys
        elif ch == "," and depth == 1:
            m = key_re.match(src, i + 1)
            if m:
                keys.add(m.group(1))
        i += 1
    return keys


def scan(root: Path) -> list[dict]:
    findings: list[dict] = []
    for path in root.rglob("*"):
        if path.suffix not in (".html", ".js", ".mjs") or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "EOS_UI.confirm" not in src:
            continue
        for m in re.finditer(r"EOS_UI\.confirm\(\s*\{", src):
            brace = src.index("{", m.start())
            keys = _top_level_keys(src, brace)
            unknown = sorted(keys - KNOWN)
            if not unknown:
                continue
            line = src[: m.start()].count("\n") + 1
            # An inline marker on the call line or the one above opts out.
            context = "\n".join(src.splitlines()[max(0, line - 2): line])
            if IGNORE.search(context):
                continue
            findings.append({
                "file": str(path.relative_to(root)).replace("\\", "/"),
                "line": line,
                "unknown_keys": unknown,
                "read_keys": sorted(keys & KNOWN),
            })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="tree to scan (default: cwd)")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    findings = scan(root)
    ok = not findings
    msg = ("no EOS_UI.confirm options are being dropped" if ok
           else f"{len(findings)} EOS_UI.confirm call site(s) pass keys the component ignores")

    if args.json:
        emit_json(ok, "ok" if ok else "dropped_options", msg, {"findings": findings})
        return len(findings)

    print(msg)
    for f in findings:
        print(f"  {f['file']}:{f['line']}")
        print(f"      dropped : {', '.join(f['unknown_keys'])}")
        print(f"      read    : {', '.join(f['read_keys']) or 'NOTHING — renders a bare \"Are you sure?\"'}")
    if findings:
        print("\nEOS_UI.confirm reads: " + ", ".join(sorted(KNOWN)))
        print("Add a real alias in eos-components.js::confirm, or mark the line "
              "`// confirm-keys: ignore <why>`.")
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prove a new test fails against the code it was written to pin.

A test added alongside a fix is evidence of nothing until you have watched it
fail without the fix. Both directions are required: a test that passes at the
base ref pins no behaviour, and one that fails in the working tree is simply
broken. Only the pair — red at base, green here — shows the test is bound to
the change.

This is the hand-runnable form of the discipline fix-agent applies to its own
merges. Nothing else in this tree could run it, so it was being done by hand
with a throwaway checkout, or (more often) not at all.

    python scripts/check_test_fails_at_base.py tests/test_sdk_utils.py::test_name
    python scripts/check_test_fails_at_base.py tests/test_foo.py --base main --json

Only the *test* files are carried back to the base ref; the source stays as it
was. That asymmetry is the whole mechanism — copy the source too and the test
would pass at base and prove nothing.

Exit 0 when every named test is red at base and green here.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scanner_lib import emit_json  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emptyos.sdk.worktree import ensure_worktree, git_run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# A daemon-backed test cannot answer this question: it talks to :9000, which
# serves the *working tree*, so it would report the same result at either ref
# and quietly certify a test that pins nothing.
DAEMON_BACKED = ("test_sys_", "test_dogfood", "test_journeys", "test_ui")


def _split(node: str) -> tuple[str, str]:
    """`path::test_name` → (path, full node id)."""
    return node.split("::", 1)[0], node


def _pytest(target: str, cwd: Path) -> tuple[bool, str]:
    # sys.executable, never bare `pytest`: PATH may resolve to a different
    # interpreter than the one running this check (CLAUDE.md § Testing).
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", target, "-q", "--no-header"],
                           cwd=str(cwd), capture_output=True, text=True, timeout=600)
        return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:][0] if (
            r.stdout or r.stderr) else ""
    except Exception as exc:  # a timeout or a missing pytest is not a pass
        return False, str(exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("nodes", nargs="+", help="pytest node ids, e.g. tests/test_x.py::test_y")
    ap.add_argument("--base", default="", help="ref to test against (default: merge-base with main)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    daemon = [n for n in args.nodes if any(m in n for m in DAEMON_BACKED)]
    if daemon:
        msg = (f"daemon-backed test(s) cannot pin a base: {', '.join(daemon)} — they reach the "
               f"running daemon, which serves the working tree at either ref")
        return emit_json(False, "daemon-backed", msg) if args.json else (print(msg) or 2)

    base = args.base
    if not base:
        rc, out, _ = git_run(["merge-base", "HEAD", "main"], cwd=ROOT)
        base = out.strip() if rc == 0 and out.strip() else "HEAD"

    results: list[dict] = []
    tmp = Path(tempfile.mkdtemp(prefix="eos-base-"))
    wt = tmp / "base"
    try:
        _, err = ensure_worktree(ROOT, wt, ref=base)
        if err:
            return emit_json(False, "worktree", err) if args.json else (print(err) or 2)

        for node in args.nodes:
            rel, full = _split(node)
            src = ROOT / rel
            if not src.is_file():
                results.append({"node": node, "ok": False, "reason": "test file not found"})
                continue

            here_ok, here_msg = _pytest(full, ROOT)

            # Carry the test file across, leaving the base's source untouched.
            dst = wt / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            base_ok, base_msg = _pytest(full, wt)

            results.append({
                "node": node, "ok": here_ok and not base_ok,
                "here": "pass" if here_ok else "FAIL", "here_detail": here_msg,
                "base": "pass" if base_ok else "fail", "base_detail": base_msg,
                "reason": ("" if here_ok and not base_ok
                           else "does not fail at base — pins nothing" if base_ok
                           else "fails in the working tree"),
            })
    finally:
        git_run(["worktree", "remove", "--force", str(wt)], cwd=ROOT)
        git_run(["worktree", "prune"], cwd=ROOT)  # Windows can hold the dir; prune clears it
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r["ok"]]
    if args.json:
        return emit_json(not bad, "unpinned",
                         f"{len(bad)} of {len(results)} test(s) do not pin their change",
                         {"base": base, "results": results})

    print(f"base {base[:12]}\n")
    for r in results:
        mark = "OK  " if r["ok"] else "BAD "
        print(f"  {mark} {r['node']}")
        print(f"        working tree: {r.get('here','?')}   at base: {r.get('base','?')}"
              + (f"   — {r['reason']}" if r["reason"] else ""))
    if bad:
        print(f"\n{len(bad)} test(s) prove nothing about the change they ship with.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

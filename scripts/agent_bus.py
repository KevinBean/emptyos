"""Agent Context Bus — standalone CLI entrypoint ('Ripple').

The implementation lives in :mod:`emptyos.sdk.agent_bus`; this script is a thin
argparse shim so the tool works without the ``eos`` CLI being on PATH (handy
for non-EmptyOS workspaces, fresh clones before ``pip install -e .``, or
operators who just want a single file to copy around).

For day-to-day use inside an EmptyOS repo, prefer ``eos bus {import,ripple,
status}`` — same code, discoverable from ``eos --help``, tab-completable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Force UTF-8 stdio so non-ASCII section titles don't blow up under cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except AttributeError:
    pass

# Ensure the SDK module is importable when this script is run from a checkout
# that hasn't been `pip install -e .`'d yet.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from emptyos.sdk.agent_bus import (  # noqa: E402
    RippleError,
    check_dry_run,
    run_import,
    run_status,
    run_transpile,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Context Bus — 'Ripple'")
    sub = parser.add_subparsers(dest="command")

    p_import = sub.add_parser("import", help="Import boot files + rules + skills into the bus")
    p_import.add_argument("--workspace", default=".")

    p_trans = sub.add_parser("transpile", help="Regenerate native files from the bus")
    p_trans.add_argument("--workspace", default=".")
    p_trans.add_argument("--force", action="store_true", help="Overwrite native edits")

    p_ripple = sub.add_parser("ripple", help="Detect drift and (unless --dry-run) transpile")
    p_ripple.add_argument("--workspace", default=".")
    p_ripple.add_argument("--dry-run", action="store_true")
    p_ripple.add_argument("--force", action="store_true", help="Overwrite native edits")

    p_status = sub.add_parser("status", help="Show Context Bus status")
    p_status.add_argument("--workspace", default=".")

    args = parser.parse_args()

    try:
        if args.command == "import":
            run_import(args.workspace)
        elif args.command == "transpile":
            run_transpile(args.workspace, force=args.force)
        elif args.command == "ripple":
            if args.dry_run:
                sys.exit(1 if check_dry_run(args.workspace) else 0)
            run_transpile(args.workspace, force=args.force)
        elif args.command == "status":
            run_status(args.workspace)
        else:
            parser.print_help()
    except RippleError as e:
        print(f"[ERROR] {e}")
        sys.exit(e.code)


if __name__ == "__main__":
    main()

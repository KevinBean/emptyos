"""Auto-relaunch dogfood-agent fix-drain rounds until the queue is empty.

Polls `/dogfood-agent/api/fix-drain/status` on a fixed interval. When a round
ends, immediately starts the next. Exits when the pending queue is empty,
when --max-rounds is hit, or when drain start refuses (e.g. orchestrator-dirty
state from a prior merge that touched :9000's own code).

Emits one stdout line per state change — designed to be wrapped by Claude's
Monitor tool or `tee` to a log. Each line is human-readable + grep-friendly
("ROUND_START", "ROUND_END", "QUEUE_EMPTY", "HALT").

Env:
    EOS_TOKEN   required — auth bearer (read from emptyos.toml [network] auth_token)
    EOS_BASE    optional — daemon base URL (default http://127.0.0.1:9000)

Examples:
    EOS_TOKEN=... python scripts/drain_until_empty.py
    EOS_TOKEN=... python scripts/drain_until_empty.py --auto-stash --max-fixes 20
    EOS_TOKEN=... python scripts/drain_until_empty.py --max-rounds 3 --no-dedupe
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Loop drain rounds until queue is empty.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--max-rounds", type=int, default=999,
        help="Stop after this many rounds even if queue isn't empty (default 999)",
    )
    p.add_argument(
        "--max-fixes", type=int, default=10,
        help="Per-round fix budget passed to drain start (default 10)",
    )
    p.add_argument(
        "--poll-interval", type=float, default=60.0,
        help="Seconds between status checks (default 60)",
    )
    p.add_argument(
        "--auto-stash", action="store_true",
        help="Stash conflicting WIP before each round; pop at drain end",
    )
    p.add_argument(
        "--force-dirty", action="store_true",
        help="Run even when queue overlaps dirty working-tree apps (ff-merge may fail)",
    )
    p.add_argument(
        "--no-dedupe", action="store_true",
        help="Disable pre-flight duplicate-prompt folding (default: on)",
    )
    return p.parse_args()


def _token() -> str:
    tok = os.environ.get("EOS_TOKEN")
    if not tok:
        sys.exit(
            "error: set EOS_TOKEN environment variable "
            "(read it from emptyos.toml [network] auth_token)"
        )
    return tok


def _base() -> str:
    return os.environ.get("EOS_BASE", "http://127.0.0.1:9000").rstrip("/")


def _queue_dir() -> Path:
    # Repo root is the parent of scripts/.
    return (Path(__file__).resolve().parent.parent
            / "data" / "apps" / "dogfood-agent" / "fix-prompts")


def request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_base() + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def count_pending() -> int:
    d = _queue_dir()
    if not d.exists():
        return 0
    return sum(1 for p in d.glob("*.md") if p.name != "_queue.md")


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _start_body(args: argparse.Namespace) -> dict:
    return {
        "max_fixes": args.max_fixes,
        "dedupe": not args.no_dedupe,
        "auto_stash": args.auto_stash,
        "force_dirty": args.force_dirty,
    }


def _print_dedupe(res: dict) -> None:
    d = res.get("dedupe") or {}
    if d.get("deduped"):
        print(
            f"[{_ts()}] DEDUPE folded={d['deduped']} clusters={d.get('remaining_keyed_clusters')}",
            flush=True,
        )


def _print_stash(res: dict) -> None:
    s = res.get("auto_stash") or {}
    if s.get("ok") and s.get("count"):
        print(f"[{_ts()}] STASH pushed={s.get('count')} ref={s.get('msg')}", flush=True)


def _summarize_round(status: dict) -> str:
    hist = status.get("history") or []
    if not hist:
        return "no history"
    h = hist[0]
    parts = [
        f"applied={h.get('applied_count')}",
        f"stuck={h.get('stuck_count')}",
        f"result={h.get('result')}",
    ]
    pop = h.get("auto_stash_pop")
    if pop:
        parts.append(f"stash_pop={'ok' if pop.get('ok') else pop.get('error','err')}")
    # Surface blocked / attempts_before signals from steps, if any.
    blocked = sum(1 for s in (h.get("steps") or []) if s.get("blocked"))
    if blocked:
        parts.append(f"blocked={blocked}")
    return " ".join(parts)


def main() -> int:
    args = parse_args()
    rounds = 0
    last_active: bool | None = None

    while True:
        ts = _ts()
        try:
            status = request("/dogfood-agent/api/fix-drain/status")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"[{ts}] STATUS_ERR {e}", flush=True)
            time.sleep(args.poll_interval)
            continue
        except Exception as e:
            print(f"[{ts}] STATUS_ERR_OTHER {type(e).__name__}: {e}", flush=True)
            time.sleep(args.poll_interval)
            continue

        active = bool(status.get("active"))
        pending = count_pending()

        # Round ended: report summary once on the transition.
        if last_active and not active:
            print(f"[{_ts()}] ROUND_END pending={pending} {_summarize_round(status)}", flush=True)
        last_active = active

        if pending == 0:
            print(f"[{_ts()}] QUEUE_EMPTY rounds_run={rounds}", flush=True)
            return 0

        if rounds >= args.max_rounds:
            print(f"[{_ts()}] MAX_ROUNDS_REACHED rounds={rounds} pending={pending}", flush=True)
            return 2

        if not active:
            try:
                res = request("/dogfood-agent/api/fix-drain/start",
                              method="POST", body=_start_body(args))
            except Exception as e:
                print(f"[{_ts()}] START_ERR {type(e).__name__}: {e}", flush=True)
                time.sleep(args.poll_interval)
                continue
            if res.get("ok"):
                rounds += 1
                _print_dedupe(res)
                _print_stash(res)
                print(
                    f"[{_ts()}] ROUND_START #{rounds} pending={res.get('pending_count')}"
                    f" max_fixes={res.get('max_fixes')}",
                    flush=True,
                )
            else:
                err = res.get("error", "")
                print(f"[{_ts()}] START_REFUSED {err}", flush=True)
                # Hard stop on signals the operator must act on; soft retry otherwise.
                err_low = err.lower()
                if any(s in err_low for s in (
                    "uncommitted", "orchestrator", "disabled", "drift",
                )):
                    print(f"[{_ts()}] HALT need-operator-action", flush=True)
                    return 1
                # Otherwise (e.g. transient daemon issue) wait + retry.

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())

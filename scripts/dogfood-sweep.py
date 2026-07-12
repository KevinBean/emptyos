"""Fire dogfood scenarios sequentially against the :9001 daemon and wait for
each to finish before kicking the next.

Sequential is mandatory -- claude-cli has a global session lock; two parallel
runs would clobber each other. Total wall-clock per run is ~3-15 minutes
depending on turn budget + LLM latency. The script prints status as it
goes and the final coverage delta when done.

Usage:
    python scripts/dogfood-sweep.py
    python scripts/dogfood-sweep.py --daemon-url http://localhost:9001
    python scripts/dogfood-sweep.py --max-wait 1200  # per-run timeout (seconds)
    python scripts/dogfood-sweep.py --rotation tuesday-evening:kevin-weekday,engineering-evening:kevin-weekday

The default sweep covers every scenario currently shipped, paired with
sensible personas. Override via --rotation if you want a different mix.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_SWEEP = [
    # (scenario, persona) — sensible defaults. Edit or override via --rotation.
    ("tuesday-evening", "kevin-weekday"),
    ("saturday-morning", "kevin-weekday"),
    ("engineering-evening", "kevin-weekday"),
    ("tuesday-evening", "new-user"),
]


def get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def daemon_alive(daemon_url: str) -> bool:
    try:
        urllib.request.urlopen(f"{daemon_url}/api/apps", timeout=5).read()
        return True
    except Exception:
        return False


def run_one(daemon_url: str, scenario: str, persona: str, max_wait: int) -> dict:
    """Fire one (scenario, persona) and poll until it finishes or times out."""
    started = time.time()
    print(f"  start  scenario={scenario} persona={persona}", flush=True)
    try:
        resp = post_json(
            f"{daemon_url}/dogfood-agent/api/run",
            {"scenario": scenario, "persona": persona},
        )
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code} on /api/run: {e.read().decode('utf-8', 'replace')}"}
    except Exception as e:
        return {"error": f"could not start run: {e}"}

    if resp.get("error"):
        return {"error": resp["error"]}
    rid = resp.get("run_id")
    if not rid:
        return {"error": f"no run_id in response: {resp}"}

    poll_url = f"{daemon_url}/dogfood-agent/api/runs/{rid}"
    poll_interval = 5
    while True:
        elapsed = time.time() - started
        if elapsed > max_wait:
            return {"run_id": rid, "error": f"timeout after {max_wait}s"}
        time.sleep(poll_interval)
        try:
            detail = get_json(poll_url, timeout=15)
        except Exception as e:
            print(f"    poll error (will retry): {e}", flush=True)
            continue
        run = detail.get("run") or {}
        status = run.get("status", "running")
        if status == "running":
            print(f"    still running ({int(elapsed)}s elapsed)", flush=True)
            continue
        # Terminal — surface friction count from behavior.json.
        behavior = detail.get("behavior") or {}
        friction = behavior.get("friction") or []
        new_count = sum(1 for f in friction if f.get("is_new"))
        return {
            "run_id": rid, "status": status,
            "duration_s": run.get("duration_s"),
            "friction_total": len(friction),
            "friction_new": new_count,
        }


def parse_rotation(s: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"bad rotation item '{item}' — expected scenario:persona")
        scenario, persona = item.split(":", 1)
        out.append((scenario.strip(), persona.strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daemon-url", default="http://localhost:9001")
    ap.add_argument("--max-wait", type=int, default=900, help="per-run timeout in seconds (default 900 = 15 min)")
    ap.add_argument("--rotation", help="comma-separated scenario:persona pairs; defaults to every shipped scenario")
    ap.add_argument("--gap", type=int, default=10, help="seconds to wait between runs (lets claude-cli session settle)")
    args = ap.parse_args()

    if not daemon_alive(args.daemon_url):
        print(f"ERROR: daemon at {args.daemon_url} not reachable.", file=sys.stderr)
        print("Start it with `dogfood\\start.bat` (Windows) or check the dogfood daemon logs.", file=sys.stderr)
        return 2

    sweep = parse_rotation(args.rotation) if args.rotation else DEFAULT_SWEEP

    print(f"Daemon: {args.daemon_url}")
    print(f"Sweep: {len(sweep)} runs × ~5-15 min each = ~{len(sweep) * 8} min wall-clock")
    print()

    summary = []
    for i, (scenario, persona) in enumerate(sweep, 1):
        print(f"[{i}/{len(sweep)}]", flush=True)
        result = run_one(args.daemon_url, scenario, persona, args.max_wait)
        result["scenario"] = scenario
        result["persona"] = persona
        if "error" in result:
            print(f"  FAIL  {result['error']}", flush=True)
        else:
            print(
                f"  done  status={result['status']} "
                f"dur={result['duration_s']}s "
                f"friction={result['friction_total']} new={result['friction_new']}",
                flush=True,
            )
        summary.append(result)
        if i < len(sweep):
            print(f"  (gap {args.gap}s)", flush=True)
            time.sleep(args.gap)
        print()

    print("=" * 60)
    print(f"Summary: {len(summary)} runs")
    ok = sum(1 for r in summary if r.get("status") == "ok")
    err = sum(1 for r in summary if "error" in r)
    new_total = sum(r.get("friction_new", 0) for r in summary)
    print(f"  ok={ok}  errored={err}  new_friction_total={new_total}")
    print()
    print("Next: python scripts/release-readiness.py --coverage")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

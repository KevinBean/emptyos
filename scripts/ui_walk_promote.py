"""Manual UI-walk triage bridge — promote walk findings into the fix-prompt queue.

The `eos-ui-walk` skill's manual walks land evidence in
``data/ui-walk/usecases/<walk>/`` (steplog.jsonl + screenshots + report.html),
but the report is a terminal artifact: rendering it never mutates any queue.
This script is the deliberate human bridge across that seam
(`.claude/rules/loop-traceability.md`): it lists the promotable
``fail | confusing | missing`` rows, and — only on an explicit command —
promotes one into the shared fix-prompt queue (``emptyos/sdk/fix_queue.py``)
with the full trace identity (`usecase_id / milestone_id / step_id / walk_id`)
and evidence references, or records a dismiss/defer/decline triage decision
without ever queueing it.

Like ``ui_walk_report.py`` it imports no kernel — only the pure fix_queue SDK
module + stdlib — so it is safe to run anytime, daemon up or down. The running
daemon sees promotions immediately (dogfood's ``api_queue`` globs the
directory live).

Identity: trace key = ``ui-walk::<usecase_id>::<milestone_id>::<step_id>``.
New steplogs carry ``usecase_id`` / ``milestone_id`` on each row; legacy rows
derive them deterministically (scenario.md frontmatter → H1 slug → walk dir
name; free-text ``usecase`` → slug), so re-listing the same walk always yields
the same keys and re-promotion updates the same file instead of duplicating.

Triage decisions append to ``<walk>/triage.jsonl`` (per-walk on purpose — a
NEW walk re-surfacing the same gap should re-ask; the cross-walk guard is the
``done/_ledger.jsonl`` refusal). ``missing`` findings are feature gaps, not
bugs: they may close via ``close --disposition planned|deferred|declined``
without pretending a code fix was verified, and ``defer`` prints a proposed
``docs/DEFERRED-WORK.md`` row (propose-only — this script never edits docs).

Usage:
  python scripts/ui_walk_promote.py list    --walk data/ui-walk/usecases/2026-07-17-1631 [--json]
  python scripts/ui_walk_promote.py promote --walk <dir> --step-key <usecase::milestone::sN> [--kind bug] [--app soil] [--force-regression]
  python scripts/ui_walk_promote.py dismiss --walk <dir> --step-key <k> --reason "noise"
  python scripts/ui_walk_promote.py defer   --walk <dir> --step-key <k> --reason "build when X"
  python scripts/ui_walk_promote.py decline --walk <dir> --step-key <k> --reason "posture says no"
  python scripts/ui_walk_promote.py close   <filename> --disposition shipped|planned|deferred|declined|dismissed \
      [--learning-outcome regression-test|conformance-case|lesson|rule|design-principle|none] \
      [--learning-ref tests/test_x.py] [--evidence data/ui-walk/usecases/<new>/steplog.jsonl::<key>] [--reason "..."]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from scanner_lib import emit_json  # noqa: E402

from emptyos.sdk.fix_queue import (  # noqa: E402
    DISPOSITIONS,
    LEARNING_OUTCOMES,
    TRACE_PREFIX,
    FixPromptQueue,
    friction_block,
    ledger_info_from_prompt,
    persona_scenario_line,
    render_frontmatter,
    slug_for_key,
)

# Trace identity derivation lives in the SDK (emptyos/sdk/fix_queue.py) so this
# promote bridge and fix-agent's attest evidence check can never disagree on a
# steplog row's key. Re-exported under the historical local names.
from emptyos.sdk.fix_queue import slugify_trace as slugify  # noqa: E402
from emptyos.sdk.fix_queue import steplog_row_trace as row_trace  # noqa: E402

PROMOTABLE = ("fail", "confusing", "missing")
# steplog status → queue kind (the queue's vocabulary is bug|confusing|missing)
STATUS_TO_KIND = {"fail": "bug", "confusing": "confusing", "missing": "missing"}
TRIAGE_ACTIONS = ("promoted", "dismissed", "deferred", "declined")


def parse_scenario_meta(scenario_md: str) -> dict:
    """Flat ``key: value`` fields from scenario.md YAML frontmatter (block
    style), plus ``h1`` — enough for identity; no YAML dependency."""
    meta: dict = {}
    lines = (scenario_md or "").splitlines()
    if lines and lines[0].strip() == "---":
        for ln in lines[1:]:
            if ln.strip() == "---":
                break
            m = re.match(r"^(\w[\w-]*):\s*(.*)$", ln)
            if m and m.group(2).strip():
                meta[m.group(1)] = m.group(2).strip().strip("\"'")
    for ln in lines:
        if ln.startswith("# "):
            meta["h1"] = ln[2:].strip()
            break
    return meta


def walk_usecase_id(walk_dir: Path) -> str:
    """usecase_id for a walk: scenario.md frontmatter → H1 slug → dir name."""
    scenario = walk_dir / "scenario.md"
    meta = parse_scenario_meta(scenario.read_text(encoding="utf-8")) if scenario.exists() else {}
    return meta.get("usecase_id") or slugify(meta.get("h1", "")) or walk_dir.name


def app_from_url(url: str) -> str:
    """Best-effort app attribution from the step's URL path (`/soil/…` → soil)."""
    m = re.search(r"^[a-z]+://[^/]+/([a-z0-9-]+)", str(url or ""))
    return m.group(1) if m else ""


# ─── walk-side stores (steplog read-only; triage sidecar append-only) ─────────

def load_steplog(walk_dir: Path) -> list[dict]:
    path = walk_dir / "steplog.jsonl"
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_triage(walk_dir: Path) -> dict[str, dict]:
    """Last triage action per trace key (append-only sidecar; last row wins)."""
    path = walk_dir / "triage.jsonl"
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("step_key"):
            out[row["step_key"]] = row
    return out


def append_triage(walk_dir: Path, row: dict) -> None:
    row = {"ts": datetime.now(UTC).isoformat(), "by": "human", **row}
    with (walk_dir / "triage.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def ledger_row_for(queue: FixPromptQueue, filename: str) -> dict | None:
    """Last close-ledger row for a filename (cross-walk re-promotion guard)."""
    return queue.ledger_by_filename().get(filename)


def _regression_variants(queue: FixPromptQueue, filename: str) -> list[Path]:
    """Pending ``<stem>-rN.md`` regression prompts for a base filename,
    sorted by N (numeric, so -r10 follows -r9)."""
    stem = filename[:-3]
    out = []
    for p in queue.dir.glob(f"{stem}-r*.md"):
        m = re.fullmatch(rf"{re.escape(stem)}-r(\d+)\.md", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return [p for _, p in sorted(out)]


# ─── promotable-row listing ───────────────────────────────────────────────────

def promotable_rows(walk_dir: Path, queue: FixPromptQueue) -> list[dict]:
    usecase_id = walk_usecase_id(walk_dir)
    triage = load_triage(walk_dir)
    ledger_map = queue.ledger_by_filename()  # one bounded read, not one per row
    out = []
    for row in load_steplog(walk_dir):
        status = str(row.get("status") or "").strip().lower()
        if status not in PROMOTABLE:
            continue
        trace = row_trace(row, usecase_id, walk_dir.name)
        filename = slug_for_key(trace["key"])
        pending = (queue.dir / filename).exists() or bool(_regression_variants(queue, filename))
        done = (queue.dir / "done" / filename).exists()
        ledger = ledger_map.get(filename)
        t = triage.get(trace["key"])
        if pending:
            state = "queued"
        elif ledger or done:
            state = f"closed:{(ledger or {}).get('disposition', 'done')}"
        elif t:
            state = f"triaged:{t.get('action')}"
        else:
            state = "new"
        out.append({
            **trace,
            "filename": filename,
            "status": status,
            "kind": STATUS_TO_KIND[status],
            "action": str(row.get("action") or ""),
            "note": str(row.get("note") or ""),
            "url": str(row.get("url") or ""),
            "shot": str(row.get("shot") or ""),
            "console": row.get("console") if isinstance(row.get("console"), list) else [],
            "app": app_from_url(row.get("url")),
            "state": state,
        })
    return out


# ─── prompt build / update ────────────────────────────────────────────────────

def build_prompt(item: dict, walk_dir: Path, *, count: int = 1, first_seen: str = "",
                 need: str = "", regression_of: str = "") -> str:
    now = datetime.now(UTC).isoformat()
    fm: dict = {"kind": item["kind"]}
    if item.get("app"):
        fm["app"] = item["app"]
    fm.update({
        "key": item["key"],
        "source": "ui-walk",
        "usecase_id": item["usecase_id"],
        "milestone_id": item["milestone_id"],
        "step_id": item["step_id"],
        "walk_id": item["walk_id"],
        "evidence": str((walk_dir / "steplog.jsonl").as_posix()),
    })
    if item.get("shot"):
        fm["shot"] = item["shot"]
    if regression_of:
        fm["regression_of"] = regression_of
    fm.update({"count": count, "first_seen": first_seen or now, "last_seen": now})

    note = " ".join(str(item.get("note") or item.get("action") or "").split())
    evidence_lines = [f"- Walk: `{item['walk_id']}` — {item['milestone_id']} step {item['step_id']}"]
    if item.get("url"):
        evidence_lines.append(f"- URL: {item['url']}")
    if item.get("shot"):
        evidence_lines.append(f"- Screenshot: `{(walk_dir / item['shot']).as_posix()}`")
    for line in (item.get("console") or [])[:5]:
        evidence_lines.append(f"- Console: `{line}`")
    evidence_lines.append(f"- Steplog: `{(walk_dir / 'steplog.jsonl').as_posix()}`")
    if need:
        evidence_lines.append(f"- Real user need: {need}")

    if item["kind"] == "missing":
        task = ("This is a FEATURE GAP (a needed capability is absent), not a code bug. "
                "It may close through a planning decision — "
                "`python scripts/ui_walk_promote.py close <filename> --disposition planned|deferred|declined` — "
                "without a code fix. Only build it if the gap is accepted as planned work. "
                "See `.claude/rules/loop-traceability.md`.")
    else:
        task = ("Investigate and fix the root cause, then verify by re-walking the originating "
                "step (this finding came from a manual UI walk — there is no dogfood scenario to "
                "re-run; verification is a human re-walk, attested on the fix-agent run). "
                "When resolved, close with "
                "`python scripts/ui_walk_promote.py close <filename> --disposition shipped --evidence <re-walk steplog ref>`.")

    return "\n".join([
        render_frontmatter(fm),
        "",
        f"# Fix: {item['action'] or note[:80]}",
        "",
        persona_scenario_line("Kevin", item["usecase_id"]),
        "",
        friction_block(note),
        "",
        "## Evidence",
        *evidence_lines,
        "",
        "## Your task",
        task,
        "",
    ])


def _fm_field(content: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def promote(walk_dir: Path, queue: FixPromptQueue, step_key: str, *,
            kind: str = "", app: str = "", force_regression: bool = False) -> dict:
    step_key = step_key if step_key.startswith(f"{TRACE_PREFIX}::") else f"{TRACE_PREFIX}::{step_key}"
    rows = [r for r in promotable_rows(walk_dir, queue) if r["key"] == step_key]
    if not rows:
        return {"ok": False, "error": f"no promotable row with key {step_key}"}
    item = dict(rows[-1])
    if kind:
        item["kind"] = kind
    if app:
        item["app"] = app
    scenario = walk_dir / "scenario.md"
    need = parse_scenario_meta(scenario.read_text(encoding="utf-8")).get("need", "") if scenario.exists() else ""

    filename = item["filename"]
    pending_path = queue.dir / filename
    if not pending_path.exists():
        # a pending -rN regression prompt for this finding counts as pending —
        # re-promotion updates it rather than minting -r(N+1)
        variants = _regression_variants(queue, filename)
        if variants:
            pending_path = variants[-1]
            filename = pending_path.name
    prior = ledger_row_for(queue, item["filename"])
    done_exists = (queue.dir / "done" / item["filename"]).exists()

    if pending_path.exists():
        # same finding re-promoted while pending → update in place, no duplicate
        old = pending_path.read_text(encoding="utf-8")
        count = int(_fm_field(old, "count") or 1) + 1
        first_seen = _fm_field(old, "first_seen")
        content = build_prompt(item, walk_dir, count=count, first_seen=first_seen, need=need,
                               regression_of=_fm_field(old, "regression_of"))
        queue.write(filename, content)
        queue.rebuild_index()
        append_triage(walk_dir, {"step_key": step_key, "action": "promoted",
                                 "kind": item["kind"], "filename": filename})
        return {"ok": True, "filename": filename, "updated": True, "count": count}

    if (prior or done_exists) and not force_regression:
        disp = (prior or {}).get("disposition", "done")
        return {"ok": False, "error": f"already closed as '{disp}' ({filename}) — "
                                      "re-promote with --force-regression if it regressed"}
    regression_of = ""
    if (prior or done_exists) and force_regression:
        regression_of = filename
        stem = filename[:-3]
        n = 2
        while (queue.dir / f"{stem}-r{n}.md").exists() or (queue.dir / "done" / f"{stem}-r{n}.md").exists():
            n += 1
        filename = f"{stem}-r{n}.md"

    content = build_prompt(item, walk_dir, need=need, regression_of=regression_of)
    queue.write(filename, content)
    queue.rebuild_index()
    append_triage(walk_dir, {"step_key": step_key, "action": "promoted",
                             "kind": item["kind"], "filename": filename})
    return {"ok": True, "filename": filename, "updated": False,
            **({"regression_of": regression_of} if regression_of else {})}


def close_prompt(queue: FixPromptQueue, filename: str, *, disposition: str,
                 learning_outcome: str = "none", learning_ref: str = "",
                 evidence: str = "", reason: str = "") -> dict:
    if disposition not in DISPOSITIONS:
        return {"ok": False, "error": f"disposition must be one of {DISPOSITIONS}"}
    if learning_outcome not in LEARNING_OUTCOMES:
        return {"ok": False, "error": f"learning_outcome must be one of {LEARNING_OUTCOMES}"}
    if not filename.endswith(".md"):
        filename += ".md"
    path = queue.dir / filename
    if not path.exists():
        return {"ok": False, "error": f"no pending prompt {filename}"}
    content = path.read_text(encoding="utf-8")

    # stamp the close decision into the prompt frontmatter (survives in done/)
    stamp = [f"disposition: {disposition}", f"learning_outcome: {learning_outcome}"]
    if learning_ref:
        stamp.append(f"learning_ref: {learning_ref}")
    if evidence:
        stamp.append(f"close_evidence: {evidence}")
    if reason:
        stamp.append(f"close_reason: {reason}")
    lines = content.splitlines()
    if lines and lines[0] == "---" and "---" in lines[1:]:
        end = lines[1:].index("---") + 1
        lines[end:end] = stamp
        content = "\n".join(lines) + ("\n" if not content.endswith("\n") else "")
        path.write_text(content, encoding="utf-8")

    info = {
        **ledger_info_from_prompt(content),
        "learning_outcome": learning_outcome,
        "learning_ref": learning_ref,
        "evidence": evidence,
        "reason": reason,
        "by": "human",
    }
    if not queue.move_to_done(filename, disposition=disposition, info=info):
        return {"ok": False, "error": f"move_to_done failed for {filename}"}
    queue.rebuild_index()
    return {"ok": True, "filename": filename, "disposition": disposition,
            "learning_outcome": learning_outcome}


def deferred_work_row(item: dict, reason: str) -> str:
    """A proposed docs/DEFERRED-WORK.md row (printed, never written)."""
    feature = " ".join((item.get("note") or item.get("action") or "").split())[:90]
    return (f"| {feature} | {reason or 'TBD — name the trigger'} | "
            f"ui-walk `{item['walk_id']}` | `{item['key']}` | "
            f"{time.strftime('%Y-%m-%d')} | deferred |")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_walk(p):
        p.add_argument("--walk", required=True, help="walk dir (data/ui-walk/usecases/<id>)")
        p.add_argument("--data-dir", default=str(REPO / "data"), help="daemon data dir (default: data/)")

    p_list = sub.add_parser("list", help="list promotable fail|confusing|missing rows")
    add_walk(p_list)
    p_list.add_argument("--json", action="store_true")

    p_prom = sub.add_parser("promote", help="promote one finding into the fix-prompt queue")
    add_walk(p_prom)
    p_prom.add_argument("--step-key", required=True, help="<usecase>::<milestone>::sN (ui-walk:: prefix optional)")
    p_prom.add_argument("--kind", choices=("bug", "confusing", "missing"), default="")
    p_prom.add_argument("--app", default="", help="override app attribution")
    p_prom.add_argument("--force-regression", action="store_true",
                        help="re-open a finding that was already closed (writes a -rN regression prompt)")

    for name, hlp in (("dismiss", "not a real finding (noise / not-a-bug)"),
                      ("defer", "real gap, build later — prints a proposed DEFERRED-WORK.md row"),
                      ("decline", "real gap, deliberately not building")):
        p = sub.add_parser(name, help=hlp)
        add_walk(p)
        p.add_argument("--step-key", required=True)
        p.add_argument("--reason", default="", help="why (recorded in triage.jsonl)")

    p_close = sub.add_parser("close", help="close a pending queue item with a disposition")
    p_close.add_argument("filename", help="fix-prompt filename (in fix-prompts/)")
    p_close.add_argument("--data-dir", default=str(REPO / "data"))
    p_close.add_argument("--disposition", required=True, choices=[d for d in DISPOSITIONS if d != "done"])
    p_close.add_argument("--learning-outcome", default="none", choices=LEARNING_OUTCOMES)
    p_close.add_argument("--learning-ref", default="", help="path/slug of the durable learning artifact")
    p_close.add_argument("--evidence", default="", help="re-walk evidence locator (steplog.jsonl::<key>)")
    p_close.add_argument("--reason", default="")

    args = ap.parse_args()
    queue = FixPromptQueue(args.data_dir)

    if args.cmd == "list":
        walk_dir = Path(args.walk)
        if not (walk_dir / "steplog.jsonl").exists():
            msg = f"no steplog.jsonl under {walk_dir}"
            return emit_json(False, "not_found", msg) if args.json else (print(f"ERROR: {msg}") or 2)
        rows = promotable_rows(walk_dir, queue)
        if args.json:
            return emit_json(True, "ok", f"{len(rows)} promotable", {"rows": rows})
        if not rows:
            print("No promotable (fail|confusing|missing) rows in this walk.")
            return 0
        print(f"{len(rows)} promotable finding(s) in {walk_dir.name} "
              f"(usecase: {walk_usecase_id(walk_dir)}):\n")
        for r in rows:
            print(f"  [{r['status']:9}] {r['key']}")
            print(f"              kind={r['kind']}  app={r['app'] or '?'}  state={r['state']}")
            print(f"              {r['action'][:100]}")
        print("\nPromote one:  python scripts/ui_walk_promote.py promote "
              f"--walk {args.walk} --step-key <key>")
        return 0

    if args.cmd == "promote":
        res = promote(Path(args.walk), queue, args.step_key,
                      kind=args.kind, app=args.app, force_regression=args.force_regression)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    if args.cmd in ("dismiss", "defer", "decline"):
        walk_dir = Path(args.walk)
        step_key = args.step_key if args.step_key.startswith(f"{TRACE_PREFIX}::") \
            else f"{TRACE_PREFIX}::{args.step_key}"
        rows = [r for r in promotable_rows(walk_dir, queue) if r["key"] == step_key]
        if not rows:
            print(f"ERROR: no promotable row with key {step_key}")
            return 1
        action = {"dismiss": "dismissed", "defer": "deferred", "decline": "declined"}[args.cmd]
        append_triage(walk_dir, {"step_key": step_key, "action": action,
                                 "kind": rows[-1]["kind"], "reason": args.reason})
        print(f"{action}: {step_key}")
        if args.cmd == "defer" and rows[-1]["kind"] == "missing":
            print("\nProposed docs/DEFERRED-WORK.md row (propose-only — paste it yourself):")
            print(deferred_work_row(rows[-1], args.reason))
        return 0

    if args.cmd == "close":
        res = close_prompt(queue, args.filename, disposition=args.disposition,
                           learning_outcome=args.learning_outcome,
                           learning_ref=args.learning_ref,
                           evidence=args.evidence, reason=args.reason)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

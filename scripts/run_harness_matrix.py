#!/usr/bin/env python3
"""Comprehensive harness benchmark driver — model-bench shootout matrix.

Runs EmptyOS's agent harness head-to-head against external agent CLIs
(aider / goose / codex / claude) across several scenarios AND several
Ollama-served models, then writes a Model x Harness pass-rate report.

It is a thin CLIENT over the running daemon's `/model-bench/api/agent-run`
(no app changes, re-runnable, writes a file) — one POST per (model, scenario),
each returning that scenario's results for every subject, already attributable
to the model because the driver set `shootout_model` for that POST.

Usage (daemon must be running on :9000 with the harness-shootout flag on):
    python scripts/run_harness_matrix.py                # full default matrix
    python scripts/run_harness_matrix.py --scenarios write-new-util --models gpt-oss:120b-cloud
    python scripts/run_harness_matrix.py --json         # agent-cli envelope on stdout

Wall-time, not cost, is the constraint (Ollama is free). Results stream to a
JSONL as they complete, so a long run never loses progress. Run it in the
background for the full matrix.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOML = REPO / "emptyos.toml"
REPORT_DIR = REPO / "data" / "apps" / "model-bench" / "reports"

# 3 free-tier, tool-capable, large-context cloud models (3 distinct families).
DEFAULT_MODELS = ["gpt-oss:120b-cloud", "minimax-m2.5:cloud", "gemma4:31b-cloud"]

# Curated core — fair, scratch-based scenarios spanning every task shape.
# (Project-root app-creation scenarios are excluded: they need EOS CLAUDE.md
# context, which would unfairly favor the EOS harness.)
DEFAULT_SCENARIOS = [
    "write-new-util",
    "add-temperature",
    "grep-replace",
    "multi-file-refactor",
    "debug-and-fix",
    "long-context-needle",
    "cross-format-spec",
    "delete-with-callers",
    "error-recovery",
    "false-premise",
]

# External CLI subjects (the EOS harness subject is added per-model below).
EXTERNAL_SUBJECTS = ["ext:aider", "ext:goose", "ext:codex", "ext:claude"]

PER_REQUEST_TIMEOUT_S = 1200  # one (model, scenario) POST = all subjects, sequential


def _load_daemon():
    cfg = tomllib.loads(TOML.read_text(encoding="utf-8"))
    net = cfg.get("network", {})
    port = net.get("port", 9000)
    tok = net.get("auth_token", "")
    return f"http://127.0.0.1:{port}", tok


def _post(base: str, tok: str, path: str, body: dict, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(base + path, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _norm_subject(sid: str) -> str:
    """Collapse the model-carrying EOS subject id to a stable label so it lines
    up across models: 'eos+ollama:gpt-oss:120b-cloud' -> 'eos+ollama'."""
    if sid.startswith("eos+"):
        return sid.split(":", 1)[0]
    return sid


# Subject templates: "eos+ollama" expands per-model to eos+ollama:<model>; every
# other id is used verbatim (model-fixed subjects like claude-external /
# eos+openai:gpt-5.4-mini, or the cheap ext:* CLIs). Override with --subjects.
DEFAULT_SUBJECT_TEMPLATES = ["eos+ollama"] + EXTERNAL_SUBJECTS


def _subjects_for(model: str, templates: list[str]) -> list[str]:
    return [f"eos+ollama:{model}" if t == "eos+ollama" else t for t in templates]


# Ollama cloud is NOT per-token billed — it's a flat ~$20/mo subscription for
# ~50× the free quota. The API reports $0, which is misleading. We attribute a
# MARGINAL per-request estimate (one model round-trip ≈ one request → use the
# run's `iterations`) so the report isn't $0; the flat $20/mo floor is noted in
# build_report. Rate ≈ $20/mo ÷ ~109k requests/mo. Override via env if needed.
OLLAMA_CLOUD_USD_PER_REQUEST = float(
    __import__("os").environ.get("EOS_OLLAMA_CLOUD_USD_PER_REQUEST", "0.0002")
)


def _is_ollama_cloud(r: dict) -> bool:
    # Cloud model tags vary: "minimax-m2.5:cloud" / "glm-4.7:cloud" end ":cloud",
    # but "gpt-oss:120b-cloud" / "gemma4:31b-cloud" end "-cloud" — match either.
    return r.get("subject", "").startswith("eos+ollama") and str(r.get("model", "")).endswith("cloud")


def _attributed_cost(r: dict) -> float:
    """Cost to display: real usage cost for priced APIs; an Ollama-cloud marginal
    estimate (iterations × per-request rate) for :cloud models (they report $0);
    0 for local/own-GPU models."""
    if _is_ollama_cloud(r):
        return round((r.get("iterations") or 0) * OLLAMA_CLOUD_USD_PER_REQUEST, 5)
    return r.get("cost_usd") or 0.0


def _cell(r: dict) -> str:
    """Compact per-run cell: pass/fail + tool calls + wall seconds + cost.
    Ollama-cloud costs are marginal estimates, marked with a leading ~."""
    mark = "PASS" if r.get("ok") else "FAIL"
    cost = _attributed_cost(r)
    approx = "~" if _is_ollama_cloud(r) else ""
    cost_s = f"·{approx}${cost:.4f}" if cost else ""
    return f"{mark} ({r.get('tool_calls', 0)}t·{round((r.get('wall_ms') or 0) / 1000, 1)}s{cost_s})"


def _load_existing(jsonl_path: Path):
    """Return (rows, done_cells) from a prior JSONL — for --resume. A
    restart.bat mid-run kills all python (driver included), so resume lets a
    re-launch skip completed (model, scenario) cells instead of starting over."""
    rows: list[dict] = []
    done: set[tuple[str, str]] = set()
    if not jsonl_path.exists():
        return rows, done
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("subject"):  # a per-subject result row — a real, completed cell
            rows.append(d)
            done.add((d.get("model"), d.get("scenario")))
        # Transient-error markers (daemon was down/respawning) are NOT marked
        # done, so a resume re-runs them instead of recording false failures.
    return rows, done


def run_matrix(models, scenarios, *, base, tok, jsonl_path, timeout,
               existing_rows=None, done_cells=None, templates=None):
    """Run the matrix, streaming results to jsonl_path. Returns all rows.

    Skips (model, scenario) cells in `done_cells` (resume); appends when
    `existing_rows` is provided so a re-launch continues the same report."""
    rows: list[dict] = list(existing_rows or [])
    done_cells = done_cells or set()
    templates = templates or DEFAULT_SUBJECT_TEMPLATES
    total = len(models) * len(scenarios)
    step = 0
    mode = "a" if existing_rows else "w"
    with jsonl_path.open(mode, encoding="utf-8") as jf:
        for mi, model in enumerate(models, 1):
            for si, scenario in enumerate(scenarios, 1):
                step += 1
                if (model, scenario) in done_cells:
                    print(f"[{step}/{total}] {model} · {scenario} — skip (done)", flush=True)
                    continue
                print(f"[{step}/{total}] model {mi}/{len(models)} {model} · "
                      f"scenario {si}/{len(scenarios)} {scenario} ...", flush=True)
                body = {
                    "scenario_id": scenario,
                    "subject_ids": _subjects_for(model, templates),
                    "shootout_model": model,
                }
                # Ride through a watchdog respawn (~30s): one retry after a wait.
                # Catch EVERYTHING (incl. JSONDecodeError from a mid-respawn HTML
                # body) so a transient blip never kills the whole run.
                resp = None
                for attempt in (1, 2):
                    try:
                        resp = _post(base, tok, "/model-bench/api/agent-run", body, timeout)
                        break
                    except Exception as e:  # noqa: BLE001 — transient transport guard
                        print(f"    transport error (attempt {attempt}): "
                              f"{type(e).__name__}: {e}", flush=True)
                        if attempt == 1:
                            time.sleep(25)
                if resp is None or resp.get("error"):
                    # Transient cell — write a marker only (no fake subject rows),
                    # and MOVE ON (don't loop: the cell may be wedging the daemon).
                    # resume re-runs transient cells next launch.
                    err = (resp or {}).get("error") or "transport error after retry"
                    jf.write(json.dumps({"model": model, "scenario": scenario,
                                         "error": err, "transient": True}) + "\n")
                    jf.flush()
                    print(f"    SKIP (transient): {str(err)[:80]}", flush=True)
                    continue
                for r in resp.get("results", []):
                    row = {
                        "model": model,
                        "scenario": scenario,
                        "subject": _norm_subject(r.get("subject_id", "")),
                        "ok": bool(r.get("ok")),
                        "skipped": bool(r.get("skipped")),
                        "tool_calls": r.get("tool_calls", 0),
                        "iterations": r.get("iterations", 0),
                        "wall_ms": r.get("wall_ms", 0),
                        "cost_usd": r.get("cost_usd", 0.0),
                        "error": r.get("error"),
                    }
                    rows.append(row)
                    jf.write(json.dumps(row) + "\n")
                jf.flush()
                _ran = [r for r in resp.get("results", []) if not r.get("skipped")]
                _nskip = len(resp.get("results", [])) - len(_ran)
                npass = sum(1 for r in _ran if r.get("ok"))
                _skiptxt = f" ({_nskip} n/a)" if _nskip else ""
                print(f"    {npass}/{len(_ran)} passed{_skiptxt}", flush=True)
    return rows


def build_report(rows, models, scenarios, stamp) -> str:
    by = {(r["model"], r["scenario"], r["subject"]): r for r in rows}
    # Subjects present in the data (eos+ first, then the rest, stable).
    seen = []
    for r in rows:
        if r["subject"] not in seen:
            seen.append(r["subject"])
    subjects = [s for s in seen if s.startswith("eos+")] + [s for s in seen if not s.startswith("eos+")]

    def passrate(subject, model):
        # Denominator excludes deliberately-skipped runs (e.g. aider n/a on a
        # discovery scenario) — a skip is not a FAIL (2026-06-30 codex audit).
        denom = [s for s in scenarios
                 if not by.get((model, s, subject), {}).get("skipped")]
        n = sum(1 for s in denom if by.get((model, s, subject), {}).get("ok"))
        return n, len(denom)

    def ran_count(subject):
        # Non-skipped (model, scenario) cells for this subject across all models.
        return sum(1 for m in models for s in scenarios
                   if not by.get((m, s, subject), {}).get("skipped"))

    def totalcost(subject):
        return sum(_attributed_cost(by[(m, s, subject)])
                   for m in models for s in scenarios if (m, s, subject) in by)

    def avglatency_s(subject):
        ws = [(by[(m, s, subject)].get("wall_ms") or 0)
              for m in models for s in scenarios if (m, s, subject) in by]
        return (sum(ws) / len(ws) / 1000.0) if ws else 0.0

    has_ollama_cloud = any(_is_ollama_cloud(r) for r in rows)

    out = [f"# Harness Benchmark — {stamp}", ""]
    out.append(f"**Models:** {', '.join(models)}")
    out.append(f"**Scenarios ({len(scenarios)}):** {', '.join(scenarios)}")
    out.append(f"**Subjects:** {', '.join(subjects)}")
    out.append("")
    out.append("## Pass-rate — harness × model")
    out.append("")
    out.append("| Harness | " + " | ".join(models) + " | **Total** |")
    out.append("|" + "---|" * (len(models) + 2))
    totals = {}
    for subj in subjects:
        cells, tp, tt = [], 0, 0
        for m in models:
            n, d = passrate(subj, m)
            cells.append(f"{n}/{d}")
            tp += n
            tt += d
        totals[subj] = tp
        out.append(f"| {subj} | " + " | ".join(cells) + f" | **{tp}/{tt}** |")
    out.append("")

    # Cost + efficiency — the economic verdict
    out.append("## Cost & efficiency (the replace-question)")
    out.append("")
    out.append("| Harness | passes | total $ | $/task | passes per $ | avg latency |")
    out.append("|---|---|---|---|---|---|")
    for subj in sorted(subjects, key=lambda s: totals[s], reverse=True):
        c = totalcost(subj)
        denom = ran_count(subj)  # excludes skipped (n/a) runs from the denominator
        per_task = c / denom if denom else 0.0
        ppd = (totals[subj] / c) if c > 0 else float("inf")
        ppd_s = "∞ ($0 local)" if c <= 0 else f"{ppd:.0f}"
        out.append(f"| {subj} | {totals[subj]}/{denom} | ${c:.4f} | ${per_task:.4f} | {ppd_s} "
                   f"| {avglatency_s(subj):.1f}s |")
    out.append("")
    if has_ollama_cloud:
        out.append(
            f"> Ollama-cloud (`*:cloud`) costs are **marginal estimates** "
            f"(iterations × ${OLLAMA_CLOUD_USD_PER_REQUEST:.4f}/request) **on top of a flat "
            f"~$20/mo subscription** (small free quota, then 50× the free tier). They are NOT "
            f"per-token-billed; the API reports $0. Usage-priced subjects (openai/claude) show real "
            f"cost. $0 = local/own-GPU only."
        )
        out.append("")

    # Verdict
    ranked = sorted(subjects, key=lambda s: totals[s], reverse=True)
    out.append("## Verdict")
    out.append("")
    if "eos+ollama" in totals:
        # Same denominator the cost table uses: non-skipped runs for this subject.
        nruns = ran_count("eos+ollama")
        out.append(f"- **EOS harness (cheap):** {totals['eos+ollama']}/{nruns} "
                   f"— rank **{ranked.index('eos+ollama') + 1} of {len(subjects)}**, $0 (local).")
    out.append("- Ranking (most→least passes): " +
               ", ".join(f"{s} ({totals[s]}, ${totalcost(s):.2f})" for s in ranked))
    out.append("")

    # Per-scenario detail per model
    out.append("## Per-scenario detail")
    for m in models:
        out.append("")
        out.append(f"### {m}")
        out.append("")
        out.append("| scenario | " + " | ".join(subjects) + " |")
        out.append("|" + "---|" * (len(subjects) + 1))
        for s in scenarios:
            cells = []
            for subj in subjects:
                r = by.get((m, s, subj))
                cells.append(_cell(r) if r else "—")
            out.append(f"| {s} | " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the harness benchmark matrix.")
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--scenarios", nargs="*", default=DEFAULT_SCENARIOS)
    ap.add_argument("--timeout", type=int, default=PER_REQUEST_TIMEOUT_S)
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    ap.add_argument("--resume", nargs="?", const="auto", default=None,
                    help="resume a prior run: a JSONL path, or 'auto' for the latest")
    ap.add_argument("--base", default=None,
                    help="daemon base URL (default :9000 from emptyos.toml; "
                         "set to a sandbox member e.g. http://127.0.0.1:9002 to isolate load)")
    ap.add_argument("--subjects", nargs="*", default=None,
                    help="subject templates (default cheap set). 'eos+ollama' expands "
                         "per-model; model-fixed subjects (claude-external, eos+openai:gpt-5.4-mini) "
                         "used verbatim — run those with a single --models for one pass.")
    args = ap.parse_args(argv)

    base, tok = _load_daemon()
    if args.base:
        base = args.base
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    existing_rows, done_cells = None, None
    if args.resume:
        if args.resume == "auto":
            cands = sorted(REPORT_DIR.glob("harness-matrix-*.jsonl"))
            jsonl_path = cands[-1] if cands else None
            if not jsonl_path:
                print("FATAL: --resume auto found no prior JSONL")
                return 1
        else:
            jsonl_path = Path(args.resume)
        stamp = jsonl_path.stem.replace("harness-matrix-", "")
        existing_rows, done_cells = _load_existing(jsonl_path)
        print(f"Resuming {jsonl_path.name}: {len(done_cells)} cells already done.", flush=True)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        jsonl_path = REPORT_DIR / f"harness-matrix-{stamp}.jsonl"
    report_path = REPORT_DIR / f"harness-matrix-{stamp}.md"

    try:
        rows = run_matrix(args.models, args.scenarios, base=base, tok=tok,
                          jsonl_path=jsonl_path, timeout=args.timeout,
                          existing_rows=existing_rows, done_cells=done_cells,
                          templates=args.subjects)
    except Exception as e:  # noqa: BLE001 — top-level driver guard
        msg = f"{type(e).__name__}: {e}"
        if args.json:
            print(json.dumps({"ok": False, "code": "error", "message": msg}))
        else:
            print(f"FATAL: {msg}")
        return 1

    report = build_report(rows, args.models, args.scenarios, stamp)
    report_path.write_text(report, encoding="utf-8")

    subjects = sorted({r["subject"] for r in rows})
    totals = {s: sum(1 for r in rows if r["subject"] == s and r["ok"]) for s in subjects}
    costs = {s: round(sum(_attributed_cost(r) for r in rows if r["subject"] == s), 4)
             for s in subjects}
    if args.json:
        print(json.dumps({"ok": True, "code": "ok",
                          "message": f"matrix complete → {report_path.name}",
                          "data": {"report": str(report_path), "totals": totals, "costs": costs}}))
    else:
        print(f"\nReport written: {report_path}")
        print("Totals: " + ", ".join(f"{s}={totals[s]} (${costs[s]})" for s in subjects))
    return 0


if __name__ == "__main__":
    sys.exit(main())

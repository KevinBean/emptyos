"""Bench arbitrary OpenRouter models on the canonical model-bench buckets.

Answers one question: *is this candidate model worth adding to the think chain?*
— before it is added to `emptyos.toml`, and without booting the kernel.

It does NOT restate the benchmark. Prompts and graders are imported from the
live model-bench app (`apps/extension/dev/model-bench/{prompts,graders}.py`), so
a bucket added there is benched here, and a scoring rule can never drift between
the two. The only thing this file owns is the transport (stdlib HTTP to
OpenRouter), the concurrency, and the report.

Relationship to the app's own paths:
  * `/model-bench/api/run` compares providers **in the live chain** — a
    candidate has to be added to the chain first, which is backwards.
  * `/model-bench/api/judge-run` accepts ad-hoc `openrouter:<slug>` contestants
    but scores generative buckets with an LLM judge (codex), and skips the
    deterministic ones.
  * This script is the deterministic half for off-chain models: every bucket,
    every score from `graders.grade`, no judge, no kernel.

Request shape mirrors `OpenAICompatThinkProvider` (temperature 0.7,
max_tokens 4096) so the numbers describe what the chain would actually do.
A reasoning model that spends its whole budget thinking and returns empty
content is reported as such rather than hidden — that failure mode has bitten
this repo before (see the qwythos `<think>`-drop fix, commit b3a6b67d).

Usage (PowerShell):
    $env:OPENROUTER_API_KEY = "sk-or-v1-..."
    python scripts/bench/openrouter_bucket_eval.py \
        --models moonshotai/kimi-k3,moonshotai/kimi-k2.6 \
        --label kimi-k3-eval

Fixtures only — the four builders that prefer live vault content are handed a
stub whose `_live_*` return None, so the harness contains no personal data and
two runs are comparable.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Sibling scripts/ modules aren't importable from a subdirectory on their own —
# only scripts/bench/ lands on sys.path when this file runs as __main__.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from vault_paths import vault_root  # noqa: E402
BENCH_APP = REPO_ROOT / "apps" / "extension" / "dev" / "model-bench"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"

# Mirrors OpenAICompatThinkProvider defaults so scores describe production.
TEMPERATURE = 0.7
MAX_TOKENS = 4096
REQUEST_TIMEOUT = 420


# ─── Load the app's prompt + grader modules without booting the kernel ──────


def _load(name: str):
    """Import a model-bench module by path. Both are stdlib-only and have no
    relative imports, so plain spec loading is enough — no package fakery."""
    path = BENCH_APP / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_mb_{name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], and blows up if the module isn't there yet.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_prompts = _load("prompts")
_graders = _load("graders")


class _FixtureOnly:
    """Stub for the four builders that prefer live vault content.

    Returning None everywhere makes them fall back to their module fixtures,
    which is what keeps the harness free of personal content and keeps two
    runs comparable.
    """

    async def _live_tasks(self):
        return None

    async def _live_titles(self):
        return None

    async def _live_note(self):
        return None


# BUCKETS lives in app.py, which DOES import the kernel — so the list is
# rebuilt here from the prompt-builder registry (the app's own source of
# truth for which buckets exist) rather than importing app.py.
def build_scenarios(only: list[str] | None = None) -> list[dict]:
    async def _build() -> list[dict]:
        stub = _FixtureOnly()
        out: list[dict] = []
        for bid, builder_name in _prompts.PROMPT_BUILDERS.items():
            if only and bid not in only:
                continue
            built = await getattr(_prompts, builder_name)(stub)
            if isinstance(built, tuple):
                prompt, ctx = built[0], (built[1] if len(built) > 1 else {})
            else:
                prompt, ctx = built, {}
            out.append({"id": bid, "prompt": prompt, "ctx": ctx or {}})
        return out

    return asyncio.run(_build())


# ─── Transport ─────────────────────────────────────────────────────────────


def _post(url: str, payload: dict, key: str, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Title": "EmptyOS model-bench",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_pricing(slugs: set[str]) -> dict[str, tuple[float, float]]:
    """slug -> ($/token in, $/token out). Empty on failure — cost then reads 0."""
    try:
        with urllib.request.urlopen(MODELS_URL, timeout=30) as r:
            data = json.load(r).get("data") or []
    except Exception as e:  # pricing is nice-to-have, never fatal
        print(f"  ! pricing lookup failed ({e}); costs will read $0", file=sys.stderr)
        return {}
    out = {}
    for m in data:
        if m.get("id") in slugs:
            p = m.get("pricing") or {}
            try:
                out[m["id"]] = (float(p.get("prompt") or 0), float(p.get("completion") or 0))
            except (TypeError, ValueError):
                pass
    return out


def call_model(slug: str, prompt: str, key: str) -> dict:
    payload = {
        "model": slug,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    t0 = time.time()
    try:
        body = _post(API_URL, payload, key, REQUEST_TIMEOUT)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return {"error": f"HTTP {e.code}: {detail}", "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "latency_ms": int((time.time() - t0) * 1000)}
    lat = int((time.time() - t0) * 1000)
    choices = body.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    text = msg.get("content") or ""
    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "response": text,
        "latency_ms": lat,
        "in_tokens": usage.get("prompt_tokens") or 0,
        "out_tokens": usage.get("completion_tokens") or 0,
        "reasoning_tokens": details.get("reasoning_tokens") or 0,
        "finish_reason": (choices[0].get("native_finish_reason") or choices[0].get("finish_reason") or "")
        if choices
        else "",
        # An empty body from a successful call is the reasoning-budget failure
        # mode: the model spent max_tokens thinking and returned no content.
        "error": None if text.strip() else "empty content (reasoning consumed the token budget?)",
    }


# ─── Run ───────────────────────────────────────────────────────────────────


def run(models: list[str], scenarios: list[dict], key: str, workers: int) -> list[dict]:
    jobs = [(m, s) for m in models for s in scenarios]
    rows: list[dict] = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(call_model, m, s["prompt"], key): (m, s) for m, s in jobs}
        for fut in concurrent.futures.as_completed(futs):
            model, scen = futs[fut]
            res = fut.result()
            grade = None
            if not res.get("error"):
                grade = _graders.grade(scen["id"], res["response"], scen["ctx"])
                grade = {
                    "ok": grade.ok,
                    "score": grade.score,
                    "codes": list(grade.codes or []),
                    "notes": grade.notes,
                }
            rows.append({"model": model, "bucket": scen["id"], "grade": grade, **res})
            done += 1
            mark = "!" if res.get("error") else f"{(grade or {}).get('score')}"
            # flush: a redirected run is block-buffered, so progress on a
            # 10-minute sweep would otherwise appear only at exit.
            print(f"  [{done}/{len(jobs)}] {model} · {scen['id']} · {mark} · {res['latency_ms']}ms",
                  flush=True)
    return rows


def summarize(rows: list[dict], models: list[str], buckets: list[str],
              pricing: dict[str, tuple[float, float]]) -> list[dict]:
    out = []
    for m in models:
        mine = [r for r in rows if r["model"] == m]
        pin, pout = pricing.get(m, (0.0, 0.0))
        cost = sum(r.get("in_tokens", 0) * pin + r.get("out_tokens", 0) * pout for r in mine)
        scored = [r["grade"]["score"] for r in mine
                  if r.get("grade") and r["grade"].get("score") is not None]
        lats = [r["latency_ms"] for r in mine if not r.get("error")]
        per_bucket = {}
        for b in buckets:
            hit = next((r for r in mine if r["bucket"] == b), None)
            if not hit or hit.get("error"):
                per_bucket[b] = None
            else:
                per_bucket[b] = (hit.get("grade") or {}).get("score")
        out.append({
            "model": m,
            "overall": round(sum(scored) / len(scored), 3) if scored else None,
            "graded": len(scored),
            "per_bucket": per_bucket,
            "cost_usd": round(cost, 5),
            "avg_latency_ms": int(sum(lats) / len(lats)) if lats else None,
            "fails": sum(1 for r in mine if r.get("error")),
            "reasoning_tokens": sum(r.get("reasoning_tokens", 0) for r in mine),
        })
    out.sort(key=lambda r: (-(r["overall"] or -1), r["cost_usd"]))
    return out


# ─── Report ────────────────────────────────────────────────────────────────


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def render(summary: list[dict], rows: list[dict], buckets: list[str],
           wall_s: float, label: str) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    total = sum(s["cost_usd"] for s in summary)
    L = [
        "---", "tag: bench", "tags:", "  - bench", "  - openrouter", "  - model-bench",
        f"created: {today}", "---", "",
        f"# {label} — EmptyOS model-bench buckets", "",
        f"Generated: {datetime.now(UTC).isoformat(timespec='seconds')} · "
        f"wall {wall_s/60:.1f} min · {len(rows)} calls", "",
        "Prompts and scoring are imported from the live model-bench app "
        "(`apps/extension/dev/model-bench/{prompts,graders}.py`) — this harness owns only "
        "transport and reporting, so it cannot drift from the app's own bench. "
        f"Request shape mirrors the chain provider (temperature {TEMPERATURE}, "
        f"max_tokens {MAX_TOKENS}). Fixtures only, no vault content.", "",
        "`code/js-exec` is the one bucket graded by **execution** — generated code is run "
        "against stated I/O pairs, so a model that passes `node --check` while returning "
        "wrong values scores what it deserves.", "",
        "## Scoreboard", "",
        "| # | Model | Overall | " + " | ".join(f"`{b.split('/')[-1]}`" for b in buckets)
        + " | Cost | Avg lat | Fails |",
        "|---|---|---|" + "---|" * len(buckets) + "---|---|---|",
    ]
    for i, s in enumerate(summary, 1):
        cells = " | ".join(_fmt(s["per_bucket"].get(b)) for b in buckets)
        lat = "—" if s["avg_latency_ms"] is None else f"{s['avg_latency_ms']/1000:.1f}s"
        overall = "—" if s["overall"] is None else f"**{s['overall']:.2f}**"
        L.append(f"| {i} | `{s['model']}` | {overall} | {cells} | "
                 f"${s['cost_usd']:.4f} | {lat} | {s['fails']} |")
    L += ["", f"**Total spend this run: ${total:.4f}**", ""]

    # Never report an average without saying what it averaged. `code/js-exec`
    # returns UNGRADED when `node` is absent, and an overall quietly computed
    # from the remaining buckets reads as full coverage when it is not.
    partial = [s for s in summary if s["graded"] < len(buckets)]
    if partial:
        L += ["> ⚠ **Partial coverage** — overall is averaged over graded buckets only: "
              + ", ".join(f"`{s['model']}` {s['graded']}/{len(buckets)}" for s in partial)
              + ". An ungraded `code/js-exec` usually means `node` is not on PATH.", ""]

    reasoners = [s for s in summary if s["reasoning_tokens"]]
    if reasoners:
        L += ["Reasoning tokens billed (output tokens spent thinking, not returned): "
              + ", ".join(f"`{s['model']}` {s['reasoning_tokens']:,}" for s in reasoners), ""]

    L += ["## Per-call detail", "",
          "| Model | Bucket | Score | Note | Lat | In | Out | Reasoning |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["bucket"], r["model"])):
        g = r.get("grade") or {}
        score = "ERR" if r.get("error") else _fmt(g.get("score"))
        note = (r.get("error") or g.get("notes") or "")[:120].replace("|", "/")
        L.append(f"| `{r['model']}` | {r['bucket']} | {score} | {note} | "
                 f"{r['latency_ms']/1000:.1f}s | {r.get('in_tokens', 0)} | "
                 f"{r.get('out_tokens', 0)} | {r.get('reasoning_tokens', 0)} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", required=True, help="comma-separated OpenRouter slugs")
    ap.add_argument("--buckets", default="", help="comma-separated bucket ids (default: all)")
    ap.add_argument("--label", default="openrouter-eval", help="report title + filename slug")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="", help="output .md path (default: vault bench dir)")
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    only = [b.strip() for b in args.buckets.split(",") if b.strip()] or None
    scenarios = build_scenarios(only)
    buckets = [s["id"] for s in scenarios]
    print(f"{len(models)} models x {len(buckets)} buckets = {len(models)*len(buckets)} calls")

    pricing = fetch_pricing(set(models))
    missing = [m for m in models if m not in pricing]
    if missing:
        print(f"  ! no pricing for: {', '.join(missing)}", file=sys.stderr)

    t0 = time.time()
    rows = run(models, scenarios, key, args.workers)
    wall = time.time() - t0
    summary = summarize(rows, models, buckets, pricing)

    if args.out:
        md_path = Path(args.out)
    else:
        vault = vault_root()
        base = (vault / "10_Projects" / "emptyos" / "bench") if vault else (REPO_ROOT / "dist" / "bench")
        base.mkdir(parents=True, exist_ok=True)
        md_path = base / f"{datetime.now(UTC).strftime('%Y-%m-%d')}-{args.label}.md"

    md_path.write_text(render(summary, rows, buckets, wall, args.label), encoding="utf-8")
    md_path.with_suffix(".json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    print(f"\nreport: {md_path}")
    for s in summary:
        print(f"  {s['overall']} {s['model']} ${s['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

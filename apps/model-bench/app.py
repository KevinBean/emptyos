"""Model Bench — compare LLM providers on real EmptyOS usage buckets.

Scenarios are grouped into **canonical buckets** of (domain, task_shape) that
mirror how `self.think(...)` is actually called across apps. The bucket list
is the grounded taxonomy — if the system grows a new kind of call, run the
`eos-scenario-audit` skill to refresh it.

Each scenario: real prompt + (when possible) real vault data, with an
embedded fallback so bench runs on a fresh install too.

Bench targets the **live `think` capability chain** — whatever providers
emptyos.toml seeds plus whatever the Providers app has enabled. Configure
providers at `/providers/`; this app is read-only against that chain.

APIs:
- GET  /api/scenarios            list scenarios (with bucket tags)
- GET  /api/buckets              the canonical bucket taxonomy
- GET  /api/run                  run every scenario across the live chain
- GET  /api/run?scenario=<id>    run a single scenario
- GET  /api/results?limit=N      past results
- GET  /api/latest               most recent run
- GET  /api/compare              side-by-side of last 2 runs
- GET  /api/ranking              per-bucket variant rollup + suggested order
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from emptyos.sdk import BaseApp, cli_command, web_route

from . import agent_bench
from . import prompts as _prompts
from .agent_scenarios import build_scenarios as build_agent_scenarios


# Canonical bucket taxonomy — (id, domain, task_shape, description).
# Keep in sync with the audit skill at .claude/skills/eos-scenario-audit/.
BUCKETS: list[tuple[str, str, str, str]] = [
    ("text/classify",     "text", "classify",     "Classify an inbox capture into one tag"),
    ("text/rank",         "text", "rank",         "Prioritize a list of tasks"),
    ("text/json-extract", "text", "json-extract", "Pick 5 notes as blog posts, return JSON"),
    ("text/summarize",    "text", "summarize",    "Summarize a vault note in 2-3 sentences"),
    ("text/rewrite",      "text", "rewrite",      "Polish a paragraph without changing meaning"),
    ("text/draft",        "text", "draft",        "Generate short original prose from a brief"),
    ("text/qa",           "text", "qa",           "Answer a question from provided note context"),
    ("text/reason",       "text", "reason",       "Multi-step analysis over app-usage data"),
    ("code/code-gen",     "code", "code-gen",     "Generate an app manifest from a description"),
]


# Fixtures + prompt builders live in prompts.py (extracted for P4 Atomic).


class ModelBenchApp(BaseApp):

    async def setup(self):
        await super().setup()
        # Re-apply persisted bucket chains to the live think capability.
        chains = self._load_chains()
        if chains:
            await self._apply_chains_to_kernel(chains)

    def _results_path(self) -> Path:
        return self.data_dir / "results.json"

    def _chains_path(self) -> Path:
        return self.data_dir / "bucket_chains.json"

    def _load_chains(self) -> dict[str, list[str]]:
        p = self._chains_path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def _save_chains(self, chains: dict[str, list[str]]):
        self._chains_path().write_text(
            json.dumps(chains, indent=2), encoding="utf-8"
        )

    def _all_live_providers(self):
        """Every Provider currently in the think capability, keyed by variant_id."""
        cap = self.kernel.capabilities.get("think")
        seen: dict = {}
        for p in cap.providers:
            seen.setdefault(p.variant_id, p)
        for domain_ps in cap._domains.values():
            for p in domain_ps:
                seen.setdefault(p.variant_id, p)
        for bucket_ps in cap._buckets.values():
            for p in bucket_ps:
                seen.setdefault(p.variant_id, p)
        return seen

    async def _apply_chains_to_kernel(self, chains: dict[str, list[str]]):
        """Attach each bucket's chain to the live capability. Unknown variants skipped."""
        cap = self.kernel.capabilities.get("think")
        live = self._all_live_providers()
        for bucket, variant_ids in chains.items():
            built = [live[vid] for vid in variant_ids if vid in live]
            if built:
                cap.add_bucket(bucket, built)

    def _load_results(self) -> list[dict]:
        p = self._results_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_result(self, result: dict):
        results = self._load_results()
        results.append(result)
        if len(results) > 100:
            results = results[-100:]
        self._results_path().write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8"
        )

    # --- Live vault probes — fall back to fixtures if vault absent/empty ---

    async def _live_tasks(self) -> list[str]:
        try:
            hits = await self.search("- [ ]", path=str(self.kernel.config.notes_path))
            lines: list[str] = []
            for r in hits[:5]:
                path = r if isinstance(r, str) else r.get("path", "")
                try:
                    content = await self.read(path)
                except Exception:
                    continue
                for ln in content.split("\n"):
                    if "- [ ]" in ln and 10 < len(ln.strip()) < 160:
                        lines.append(ln.strip())
                        if len(lines) >= 8:
                            return lines
            return lines
        except Exception:
            return []

    async def _live_titles(self, limit: int = 10) -> list[str]:
        try:
            vault = self.kernel.config.notes_path
            if not vault:
                return []
            titles: list[str] = []
            for md in Path(vault).rglob("*.md"):
                try:
                    rel = md.relative_to(Path(vault))
                except Exception:
                    continue
                if any(part.startswith((".", "_")) for part in rel.parts):
                    continue
                titles.append(f"{md.stem.replace('-', ' ')} ({rel})")
                if len(titles) >= limit:
                    break
            return titles
        except Exception:
            return []

    async def _live_note(self, min_chars: int = 400) -> str:
        try:
            vault = self.kernel.config.notes_path
            if not vault:
                return ""
            for md in Path(vault).rglob("*.md"):
                try:
                    rel = md.relative_to(Path(vault))
                except Exception:
                    continue
                if any(part.startswith((".", "_")) for part in rel.parts):
                    continue
                content = await self.read(str(md))
                if content and len(content) >= min_chars:
                    return content[:2000]
            return ""
        except Exception:
            return ""

    # --- Bucket prompts — sourced from real app prompts ---------------------

    # ── Prompt builders — bound from prompts.py ──────────────

    _prompt_classify = _prompts._prompt_classify
    _prompt_rank = _prompts._prompt_rank
    _prompt_json_extract = _prompts._prompt_json_extract
    _prompt_summarize = _prompts._prompt_summarize
    _prompt_rewrite = _prompts._prompt_rewrite
    _prompt_draft = _prompts._prompt_draft
    _prompt_qa = _prompts._prompt_qa
    _prompt_reason = _prompts._prompt_reason
    _prompt_code_gen = _prompts._prompt_code_gen
    _PROMPT_BUILDERS = _prompts.PROMPT_BUILDERS

    async def _build_scenarios(self, only: str | None = None) -> list[dict]:
        """Build scenarios, one per bucket. `only` filters to a single bucket id."""
        scenarios: list[dict] = []
        for bid, domain, shape, description in BUCKETS:
            if only and bid != only:
                continue
            builder = self._PROMPT_BUILDERS.get(bid)
            if not builder:
                continue
            try:
                prompt = await getattr(self, builder)()
            except Exception as e:
                prompt = f"[scenario build failed: {e}]"
            scenarios.append({
                "id": bid,
                "domain": domain,
                "task_shape": shape,
                "description": description,
                "prompt": prompt,
            })
        return scenarios

    # --- CLI ----------------------------------------------------------------

    @cli_command("model-bench", help="Compare LLM models on canonical usage buckets")
    async def cmd_bench(self, action: str = "run", scenario: str = ""):
        if action == "run":
            scenarios = await self._build_scenarios(only=scenario or None)
            if not scenarios:
                self.print_rich(f"[red]No scenario matched '{scenario}'[/red]")
                return
            self.print_rich(f"[bold]Running {len(scenarios)} scenarios across the live think chain...[/bold]")
            print()
            for s in scenarios:
                print(f"  [{s['id']}] {s['description']}")
                results = await self.think_compare(s["prompt"])
                if not results:
                    print("    No providers available")
                    continue
                for r in results:
                    status = "OK" if not r["error"] else f"ERR: {r['error'][:40]}"
                    preview = (r["response"] or "")[:80].replace("\n", " ")
                    print(f"    {r['provider']:<16} {r['latency_ms']:>5}ms  {status}")
                    if preview:
                        print(f"      > {preview}")
                self._save_result({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "scenario": s["id"],
                    "domain": s["domain"],
                    "task_shape": s["task_shape"],
                    "description": s["description"],
                    "results": results,
                })
                print()
            await self.emit("model-bench:completed", {"scenarios": len(scenarios)})
            self.print_rich("[green]Done. Results saved.[/green]")

        elif action == "buckets":
            for bid, domain, shape, desc in BUCKETS:
                print(f"  {bid:<22} {desc}")

        elif action == "results":
            results = self._load_results()
            if not results:
                print("No results yet. Run: eos model-bench run")
                return
            for r in results[-10:]:
                ts = r["timestamp"][:19]
                print(f"  [{ts}] {r['scenario']}: {r['description']}")
                for p in r["results"]:
                    status = f"{p['latency_ms']}ms" if not p["error"] else "ERROR"
                    print(f"    {p['provider']:<16} {status}")
            print(f"\n  Total: {len(results)} results stored")

        else:
            print("Usage: eos model-bench [run|buckets|results] [scenario=<id>]")

    # --- HTTP ---------------------------------------------------------------

    @web_route("GET", "/api/buckets")
    async def api_buckets(self, request):
        return [
            {"id": bid, "domain": domain, "task_shape": shape, "description": desc}
            for bid, domain, shape, desc in BUCKETS
        ]

    @web_route("GET", "/api/scenarios")
    async def api_scenarios(self, request):
        only = request.query_params.get("scenario") or None
        scenarios = await self._build_scenarios(only=only)
        return [
            {
                "id": s["id"],
                "domain": s["domain"],
                "task_shape": s["task_shape"],
                "description": s["description"],
                "prompt_preview": (s["prompt"] or "")[:240],
            }
            for s in scenarios
        ]

    @web_route("GET", "/api/run")
    async def api_run(self, request):
        only = request.query_params.get("scenario") or None
        scenarios = await self._build_scenarios(only=only)
        if not scenarios:
            return {"error": f"No scenario matched '{only}'"} if only else {"error": "No scenarios"}
        all_results = []
        for s in scenarios:
            results = await self.think_compare(s["prompt"])
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "scenario": s["id"],
                "domain": s["domain"],
                "task_shape": s["task_shape"],
                "description": s["description"],
                "results": results,
            }
            self._save_result(entry)
            all_results.append(entry)
        await self.emit("model-bench:completed", {"scenarios": len(scenarios)})
        return all_results

    @web_route("GET", "/api/results")
    async def api_results(self, request):
        limit = int(request.query_params.get("limit", "20"))
        scenario = request.query_params.get("scenario") or ""
        results = self._load_results()
        if scenario:
            results = [r for r in results if r.get("scenario") == scenario]
        return results[-limit:]

    @web_route("GET", "/api/latest")
    async def api_latest(self, request):
        results = self._load_results()
        if not results:
            return {"error": "No benchmark results yet. Run a benchmark first."}
        latest = results[-1]
        return {
            "timestamp": latest["timestamp"],
            "scenario": latest["scenario"],
            "domain": latest.get("domain"),
            "task_shape": latest.get("task_shape"),
            "description": latest["description"],
            "providers": [
                {
                    "provider": r.get("provider"),
                    "model": r.get("model", ""),
                    "mode": r.get("mode", ""),
                    "variant": r.get("variant") or r.get("provider"),
                    "latency_ms": r["latency_ms"],
                    "error": r.get("error"),
                    "response_preview": (r.get("response") or "")[:200],
                }
                for r in latest["results"]
            ],
        }

    @web_route("GET", "/api/ranking")
    async def api_ranking(self, request):
        """Per-bucket provider rollup + suggested order.

        For each canonical bucket, aggregates historical results into
        per-provider stats (runs, OK rate, median latency, latest response
        snippet) and proposes an ordering. Ordering rule: providers with
        higher success-rate come first; ties broken by lower median latency.
        Providers with zero successful runs are listed last.

        The user reads the table + response snippets and decides which
        chain to commit to `emptyos.toml`.
        """
        results = self._load_results()
        bucket_ids = [b[0] for b in BUCKETS]
        # bucket_id -> variant_id -> list of runs
        rollup: dict[str, dict[str, list[dict]]] = {bid: {} for bid in bucket_ids}
        for entry in results:
            bid = entry.get("scenario")
            if bid not in rollup:
                continue
            for r in entry.get("results", []):
                # Variant key = explicit variant if present, else fall back to
                # provider name + any recorded model/mode (for legacy entries
                # written before variant tracking was added).
                variant = r.get("variant")
                if not variant:
                    parts = [r.get("provider") or "unknown"]
                    if r.get("model"):
                        parts.append(r["model"])
                    if r.get("mode"):
                        parts.append(str(r["mode"]))
                    variant = ":".join(parts)
                rollup[bid].setdefault(variant, []).append({
                    "variant": variant,
                    "provider": r.get("provider") or variant.split(":", 1)[0],
                    "model": r.get("model") or "",
                    "mode": r.get("mode") or "",
                    "latency_ms": r.get("latency_ms", 0),
                    "error": r.get("error") or "",
                    "response": r.get("response") or "",
                    "timestamp": entry.get("timestamp", ""),
                })

        def _variant_stats(runs: list[dict]) -> dict:
            # Consent skips mean the provider never saw the prompt — they reflect
            # user approval state, not provider behavior. Exclude from OK-rate.
            real_runs = [r for r in runs if not (r["error"] or "").startswith("skipped:")]
            skipped = len(runs) - len(real_runs)
            n = len(real_runs)
            ok_runs = [r for r in real_runs if not r["error"]]
            ok_count = len(ok_runs)
            ok_latencies = [r["latency_ms"] for r in ok_runs if r["latency_ms"]]
            med_latency = int(median(ok_latencies)) if ok_latencies else 0
            latest = max(runs, key=lambda r: r["timestamp"]) if runs else None
            latest_response = (latest["response"] or "")[:4000] if latest else ""
            latest_error = (latest["error"] or "")[:500] if latest else ""
            first = runs[0]
            return {
                "variant": first["variant"],
                "provider": first["provider"],
                "model": first["model"],
                "mode": first["mode"],
                "runs": n,
                "ok_count": ok_count,
                "ok_rate": round(ok_count / n, 3) if n else 0.0,
                "skipped": skipped,
                "median_latency_ms": med_latency,
                "latest_response": latest_response,
                "latest_error": latest_error,
                "latest_timestamp": latest["timestamp"] if latest else "",
            }

        buckets_out = []
        for bid, domain, shape, desc in BUCKETS:
            variants_map = rollup.get(bid, {})
            variant_stats = [_variant_stats(runs) for runs in variants_map.values()]
            # Rank: ok_rate DESC, median_latency ASC, variant asc
            variant_stats.sort(key=lambda s: (
                -s["ok_rate"],
                s["median_latency_ms"] if s["ok_count"] else 10**9,
                s["variant"],
            ))
            suggested_order = [s["variant"] for s in variant_stats if s["ok_count"]]
            buckets_out.append({
                "id": bid,
                "domain": domain,
                "task_shape": shape,
                "description": desc,
                "n_runs": sum(s["runs"] for s in variant_stats),
                "variants": variant_stats,
                "suggested_order": suggested_order,
                "suggested_chain": ",".join(suggested_order),
            })
        return {
            "buckets": buckets_out,
            "total_result_entries": len(results),
        }

    @web_route("GET", "/api/chains")
    async def api_chains(self, request):
        """Return persisted bucket → variant-list overrides (what's active right now)."""
        return {"chains": self._load_chains()}

    @web_route("POST", "/api/apply-chain")
    async def api_apply_chain(self, request):
        """Apply a bucket's provider chain — persists + hot-reloads (no restart).

        Body: {"bucket": "text/classify", "variants": ["openai:gpt-4.1-mini", "ollama:qwen3"]}

        Persists to data/apps/model-bench/bucket_chains.json (re-applied on boot)
        and attaches the chain to the live think capability immediately.
        """
        body = await request.json()
        bucket = str(body.get("bucket") or "").strip()
        variants = body.get("variants") or body.get("providers") or []
        if not bucket or "/" not in bucket:
            return {"error": "bucket must be 'domain/task_shape'"}
        if not isinstance(variants, list) or not all(isinstance(v, str) for v in variants):
            return {"error": "variants must be a list of strings"}

        live = self._all_live_providers()
        unknown = [v for v in variants if v not in live]
        resolved = [v for v in variants if v in live]
        if not resolved:
            return {
                "error": "none of the supplied variants are currently loaded",
                "unknown": unknown,
                "available": sorted(live.keys()),
            }

        chains = self._load_chains()
        chains[bucket] = resolved
        self._save_chains(chains)

        cap = self.kernel.capabilities.get("think")
        cap.add_bucket(bucket, [live[v] for v in resolved])

        await self.emit("model-bench:chain_applied", {"bucket": bucket, "variants": resolved})
        return {"ok": True, "bucket": bucket, "variants": resolved, "unknown": unknown}

    @web_route("DELETE", "/api/apply-chain")
    async def api_clear_chain(self, request):
        """Remove a persisted bucket override. Live chain reverts on next restart."""
        bucket = request.query_params.get("bucket", "").strip()
        if not bucket:
            return {"error": "bucket query param required"}
        chains = self._load_chains()
        if bucket in chains:
            del chains[bucket]
            self._save_chains(chains)
        cap = self.kernel.capabilities.get("think")
        cap._buckets.pop(bucket, None)
        return {"ok": True, "bucket": bucket}

    @web_route("GET", "/api/compare")
    async def api_compare(self, request):
        results = self._load_results()
        if len(results) < 2:
            return {"error": "Need at least 2 benchmark results to compare."}
        last_two = results[-2:]
        return [
            {
                "timestamp": e["timestamp"],
                "scenario": e["scenario"],
                "domain": e.get("domain"),
                "task_shape": e.get("task_shape"),
                "description": e["description"],
                "providers": [
                    {
                        "provider": r["provider"],
                        "latency_ms": r["latency_ms"],
                        "error": r.get("error"),
                        "response_preview": (r.get("response") or "")[:200],
                    }
                    for r in e["results"]
                ],
            }
            for e in last_two
        ]

    # ── Agent-bench — tool-use scenarios across providers ─────────────

    def _agent_scenarios_cached(self):
        if not hasattr(self, "_agent_scenarios"):
            self._agent_scenarios = {s.id: s for s in build_agent_scenarios()}
        return self._agent_scenarios

    @web_route("GET", "/api/agent-scenarios")
    async def api_agent_scenarios(self, request):
        scenarios = self._agent_scenarios_cached()
        return [
            {
                "id": s.id,
                "title": s.title,
                "description": s.description,
                "tags": s.tags,
                "expected_tool_floor": s.expected_tool_floor,
                "max_iters": s.max_iters,
                "task_preview": s.task_template.strip().split("\n")[0][:120],
            }
            for s in scenarios.values()
        ]

    @web_route("GET", "/api/agent-subjects")
    async def api_agent_subjects(self, request):
        """List the benchmark subjects, their runnability, and their current model."""
        import shutil as _shutil
        agent_app = self.kernel.apps.instances.get("agent")
        results = []
        for sid in agent_bench.ALL_SUBJECTS:
            available = True
            reason = ""
            model = ""
            if sid in ("claude-external", "claude-code-eos"):
                if not _shutil.which("claude"):
                    available = False
                    reason = "claude CLI not on PATH"
                else:
                    model = "(CLI default)"
                    if sid == "claude-code-eos":
                        model = "(CLI default — EOS context)"
            elif sid.startswith("eos+"):
                base_sid, model_override = agent_bench._parse_subject(sid)
                alias = base_sid.split("+", 1)[1]
                if agent_app is None:
                    available = False
                    reason = "agent app not loaded"
                else:
                    # Same semantic resolver the bench runs with — see
                    # agent_bench._resolve_bench_subject_provider.
                    provider = agent_bench._resolve_bench_subject_provider(agent_app, alias)
                    if provider is None:
                        available = False
                        reason = f"no provider resolved for alias {alias!r}"
                    else:
                        if model_override:
                            model = model_override
                        else:
                            for attr in ("model", "_model"):
                                m = getattr(provider, attr, None)
                                if m:
                                    model = str(m)
                                    break
                        # Add the provider class name so users can see
                        # "claude" alias actually resolves to "claude-cli".
                        model = f"{getattr(provider, 'name', '?')} / {model}" if model else getattr(provider, "name", "?")
            results.append({
                "id": sid, "available": available, "reason": reason,
                "model": model,
            })
        return results

    @web_route("POST", "/api/agent-run")
    async def api_agent_run(self, request):
        body = await request.json() if request else {}
        scenario_id = (body.get("scenario_id") or "").strip()
        subject_ids = body.get("subject_ids") or list(agent_bench.ALL_SUBJECTS)
        run_group_id = (body.get("run_group_id") or "").strip()
        variant_id = (body.get("variant_id") or "").strip()
        # apply_overlay — when True (default), inject provider-tier-specific
        # system-prompt scaffolding (e.g. ollama gets stricter procedural
        # discipline rules). Set False for A/B baseline comparisons.
        apply_overlay = body.get("apply_overlay")
        if apply_overlay is None:
            apply_overlay = True
        # reps — number of repetitions per (scenario, subject). >=2 surfaces
        # stochastic variance. Default 1.
        try:
            reps = int(body.get("reps") or 1)
        except (TypeError, ValueError):
            reps = 1
        if reps < 1 or reps > 20:
            reps = max(1, min(reps, 20))
        if not scenario_id:
            return {"error": "scenario_id required"}
        scenarios = self._agent_scenarios_cached()
        scenario = scenarios.get(scenario_id)
        if scenario is None:
            return {"error": f"unknown scenario_id {scenario_id!r}",
                    "available": list(scenarios.keys())}
        unknown = [s for s in subject_ids if s not in agent_bench.ALL_SUBJECTS]
        if unknown:
            return {"error": f"unknown subject_ids: {unknown}",
                    "available": list(agent_bench.ALL_SUBJECTS)}

        # If the caller didn't supply a group id, mint one per-request so
        # single-scenario runs still get grouped (one row == one group).
        if not run_group_id:
            run_group_id = agent_bench.new_run_group_id()

        results = await agent_bench.run_scenario(
            app=self, scenario=scenario, subject_ids=list(subject_ids),
            data_dir=self.data_dir,
            run_group_id=run_group_id, variant_id=variant_id,
            apply_overlay=bool(apply_overlay),
            reps=reps,
        )
        agent_bench.save_results(self.data_dir, results)
        await self.emit("model-bench:agent_run_completed", {
            "scenario_id": scenario_id,
            "subject_ids": list(subject_ids),
            "run_group_id": run_group_id,
            "variant_id": variant_id,
            "ok": [r.ok for r in results],
        })
        from dataclasses import asdict as _asdict
        return {
            "scenario_id": scenario_id,
            "run_group_id": run_group_id,
            "variant_id": variant_id,
            "results": [_asdict(r) for r in results],
        }

    @web_route("GET", "/api/agent-run-groups")
    async def api_agent_run_groups(self, request):
        """Return the list of distinct run_group_ids with per-group rollups.

        Used by UI to offer A/B comparison between prompt/sha variants.
        """
        results = agent_bench.load_results(self.data_dir)
        groups: dict[str, dict] = {}
        for r in results:
            gid = r.get("run_group_id") or ""
            if not gid:
                continue
            g = groups.setdefault(gid, {
                "run_group_id": gid,
                "runs": 0, "ok": 0,
                "first_seen": r.get("timestamp", ""),
                "last_seen": r.get("timestamp", ""),
                "subjects": set(),
                "scenarios": set(),
                "variants": set(),
                "git_shas": set(),
                "prompt_hashes": set(),
            })
            g["runs"] += 1
            if r.get("ok"): g["ok"] += 1
            ts = r.get("timestamp", "")
            if ts < g["first_seen"]: g["first_seen"] = ts
            if ts > g["last_seen"]: g["last_seen"] = ts
            g["subjects"].add(r.get("subject_id", ""))
            g["scenarios"].add(r.get("scenario_id", ""))
            g["variants"].add(r.get("variant_id", "") or "baseline")
            sha = r.get("eos_git_sha", "")
            if sha: g["git_shas"].add(sha[:10])
            ph = r.get("system_prompt_hash", "")
            if ph: g["prompt_hashes"].add(ph)
        out = []
        for g in groups.values():
            g2 = dict(g)
            for k in ("subjects", "scenarios", "variants", "git_shas", "prompt_hashes"):
                g2[k] = sorted(g2[k])
            out.append(g2)
        out.sort(key=lambda g: g["last_seen"], reverse=True)
        return out

    @web_route("GET", "/api/agent-results")
    async def api_agent_results(self, request):
        scenario_id = request.query_params.get("scenario_id") or ""
        subject_id = request.query_params.get("subject_id") or ""
        try:
            limit = int(request.query_params.get("limit") or "50")
        except ValueError:
            limit = 50
        results = agent_bench.load_results(self.data_dir)
        if scenario_id:
            results = [r for r in results if r.get("scenario_id") == scenario_id]
        if subject_id:
            results = [r for r in results if r.get("subject_id") == subject_id]
        return results[-limit:][::-1]   # newest first

    @web_route("GET", "/api/agent-results/{run_id}/transcript")
    async def api_agent_transcript(self, request):
        run_id = request.path_params["run_id"]
        path = agent_bench.transcripts_root(self.data_dir) / f"{run_id}.jsonl"
        if not path.exists():
            return {"error": f"transcript not found: {run_id}"}
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
        return {"run_id": run_id, "events": events}

    @web_route("GET", "/api/agent-leaderboard")
    async def api_agent_leaderboard(self, request):
        """Rollup: per-subject success rate, avg tool_calls, avg wall, avg efficiency."""
        results = agent_bench.load_results(self.data_dir)
        by_subject: dict[str, list[dict]] = {}
        for r in results:
            by_subject.setdefault(r.get("subject_id", "?"), []).append(r)
        rows = []
        for sid in agent_bench.ALL_SUBJECTS:
            runs = by_subject.get(sid, [])
            n = len(runs)
            oks = sum(1 for r in runs if r.get("ok"))
            tool_calls_avg = round(
                sum(r.get("tool_calls") or 0 for r in runs) / n, 2,
            ) if n else 0
            wall_avg = int(
                sum(r.get("wall_ms") or 0 for r in runs) / n,
            ) if n else 0
            efficiency_vals = [r.get("efficiency") or 0 for r in runs if r.get("ok")]
            efficiency_avg = round(
                sum(efficiency_vals) / len(efficiency_vals), 3,
            ) if efficiency_vals else 0.0
            total_cost = round(sum(r.get("cost_usd") or 0.0 for r in runs), 6)
            ok_costs = [r.get("cost_usd") or 0.0 for r in runs if r.get("ok")]
            avg_cost_per_pass = round(
                sum(ok_costs) / len(ok_costs), 6,
            ) if ok_costs else 0.0
            rows.append({
                "subject_id": sid,
                "runs": n,
                "ok_count": oks,
                "ok_rate": round(oks / n, 3) if n else 0.0,
                "avg_tool_calls": tool_calls_avg,
                "avg_wall_ms": wall_avg,
                "avg_efficiency": efficiency_avg,
                "total_cost_usd": total_cost,
                "avg_cost_per_pass_usd": avg_cost_per_pass,
            })
        return rows

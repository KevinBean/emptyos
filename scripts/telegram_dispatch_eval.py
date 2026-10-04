"""Telegram free-talk dispatch eval — does a phone utterance route to the right verb?

The Telegram bridge hands every free-text / voice-note turn to the
``telegram-bridge`` rooms agent, which answers with ``[DO:app.method({...})]``
tokens drawn from ~50 voice verbs. Nothing measured whether "午饭 45" becomes an
expense or "明天下午三点交报告" a task due tomorrow. This script does.

Two halves:

* **Scoring** (pure, unit-tested in ``tests/test_unit_telegram_dispatch_eval.py``):
  ``score_case(case, actions, today)`` compares the actions one turn emitted
  against the case's acceptable outcomes. Date and time args are scored by the
  value the *verb* will actually use — each goes through the same SDK
  normaliser the verb calls (``normalize_relative_date`` / ``normalize_clock_time``),
  so ``due: "明天"`` counts as the empty string it becomes, not as "tomorrow".
* **Running** (``run``): copies the live bridge agent's definition from a source
  daemon (read-only GET), registers it on a target daemon — a leased sandbox
  member, never ``:9000`` — and plays each corpus utterance as a fresh turn
  (history cleared between turns). Stable verbs may auto-apply on the target,
  which is why the target must be a throwaway sandbox. The script's own HTTP
  never writes to ``:9000``; the claude-cli child each turn spawns does load the
  user-global Claude Code hooks, which can post agent-fleet events there and
  have, twice in the first run, replaced a reply with an unrelated harness
  message (see ``notes`` in the baseline).

Corpus: ``tests/fixtures/telegram_dispatch/corpus.json``.

    python scripts/telegram_dispatch_eval.py run --target http://127.0.0.1:9002 \\
        --out tests/fixtures/telegram_dispatch/baseline.json
    python scripts/telegram_dispatch_eval.py score tests/fixtures/telegram_dispatch/baseline.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emptyos.sdk.utils import (  # noqa: E402
    normalize_clock_time,
    normalize_relative_date,
    normalize_relative_offset,
    relative_day_offset,
)
from scripts._eos_browser import load_auth_token  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "telegram_dispatch" / "corpus.json"
AGENT_ID = "telegram-bridge"

# A `[DO:app.method` left in the reply text: the model meant an action but the
# rooms parser (emptyos/sdk/do_token.py DO_RE — a JSON object or bare `()`
# between the parens) did not take it, so nothing ran and the raw token reached
# the phone. Parsed tokens are stripped from the reply, so any survivor is
# unparsed: non-object args like `(text)` or `([1])`.
_UNPARSED_DO_RE = re.compile(r"\[DO:\s*([\w-]+\.\w+)")

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ── Effective arg values — what the verb will actually use ──────────────────


def _iso_or_empty(raw: str) -> str:
    try:
        return date.fromisoformat(str(raw).strip()[:10]).isoformat()
    except (ValueError, TypeError):
        return ""


def _calendar_day(raw: str, today: date) -> str:
    # calendar.voice_today_agenda runs a non-empty `day` through
    # normalize_relative_date (since T2); empty means today; anything it
    # cannot read is refused by the verb, so it scores as no date at all.
    s = str(raw or "").strip()
    if not s:
        return today.isoformat()
    return _relative(s, today)


def _relative(raw: str, today: date) -> str:
    # normalize_relative_date reads the wall clock for today/tomorrow/明天;
    # rebase the same word table onto the run's `today` so a report re-scores
    # identically later. (In gate mode the verb resolves the word when Apply
    # is tapped, so a card applied the next day drifts; ISO dates don't.)
    # An offset ("in 3 hours") is not a day word: the verb stores the wall-clock
    # day, which a report holding only `run_date` cannot rebase, so it reads
    # as undated here. No corpus case expects a due date from an offset.
    s = str(raw or "").strip().lower()
    offset = relative_day_offset(s)
    if offset is not None:
        return (today + timedelta(days=offset)).isoformat()
    return normalize_relative_date(s)


# (verb, arg) → how that verb turns the raw arg into a date.
DATE_ARGS = {
    ("task.voice_add_task", "due"): _relative,
    ("reminders.voice_add_reminder", "due"): _relative,
    ("calendar.voice_today_agenda", "day"): _calendar_day,
}


def expected_dates(spec: dict | list, today: date) -> set[str]:
    """Every acceptable reading of a corpus date spec (a list = any of them)."""
    specs = spec if isinstance(spec, list) else [spec]
    return {expected_date(s, today) for s in specs}


def expected_date(spec: dict, today: date) -> str:
    """Resolve a corpus date spec against the run date.

    ``{"offset": n}`` → today + n days. ``{"weekday": "fri"}`` → the next such
    day on or after today ("by Friday"). ``{"after": "fri"}`` → the next such
    day strictly after today ("on Friday", said on a Friday); ``"weeks": 1`` adds
    a week (English "next Wednesday" read as the one after). ``{"next_week":
    "wed"}`` → that day in the following Monday-based week ("下周三").
    """
    if "offset" in spec:
        return (today + timedelta(days=int(spec["offset"]))).isoformat()
    if "weekday" in spec:
        wd = _WEEKDAYS.index(spec["weekday"])
        return (today + timedelta(days=(wd - today.weekday()) % 7)).isoformat()
    if "after" in spec:
        wd = _WEEKDAYS.index(spec["after"])
        days = ((wd - today.weekday()) % 7 or 7) + 7 * int(spec.get("weeks", 0))
        return (today + timedelta(days=days)).isoformat()
    if "next_week" in spec:
        wd = _WEEKDAYS.index(spec["next_week"])
        monday_next = today + timedelta(days=7 - today.weekday())
        return (monday_next + timedelta(days=wd)).isoformat()
    raise ValueError(f"unknown date spec {spec!r}")


def _norm_word(v) -> str:
    return str(v or "").strip().lower().replace("-", "_").replace(" ", "_")


def arg_matches(verb: str, name: str, spec, raw, today: date) -> bool:
    """One expected arg against the raw value the model emitted."""
    if isinstance(spec, dict) and "date" in spec:
        conv = DATE_ARGS.get((verb, name), lambda r, _t: _iso_or_empty(r))
        return conv(raw, today) in expected_dates(spec["date"], today)
    if isinstance(spec, dict) and "time" in spec:
        # The verb calls normalize_clock_time on the raw value; a non-string
        # (e.g. the int 20) raises there, so it is a miss, not "20:00".
        return isinstance(raw, str) and normalize_clock_time(raw) == spec["time"]
    if isinstance(spec, dict) and "any" in spec:
        return _norm_word(raw) in {_norm_word(x) for x in spec["any"]}
    if isinstance(spec, bool):
        return raw is spec
    if isinstance(spec, (int, float)):
        try:
            return abs(float(raw) - float(spec)) < 1e-6
        except (TypeError, ValueError):
            return False
    return _norm_word(raw) == _norm_word(spec)


# ── Scoring ─────────────────────────────────────────────────────────────────


def _refuses(verb: str, args: dict) -> str:
    """Why the verb itself would refuse these args, or "" — mirrors the early
    returns in each voice verb, so a card that does nothing when applied is a
    miss even when the arg the corpus names is right."""
    if verb == "reminders.voice_add_reminder":
        due, time_ = args.get("due"), args.get("time")
        if not str(args.get("text") or "").strip():
            return "text"
        # reminders/app.py: `due` may be a day word / ISO date, or an offset
        # from now ("in 30 minutes", "半小时后") in either `due` or `time` — an
        # offset in `time` fills an unreadable day; anything else is "couldn't
        # parse".
        if due and not (normalize_relative_date(str(due)) or normalize_relative_offset(str(due))[0]
                        or normalize_relative_offset(str(time_ or ""))[0]):
            return "due"
    elif verb == "calendar.voice_today_agenda":
        day = str(args.get("day") or "").strip()  # the verb strips first: " " is today
        if day and not normalize_relative_date(day):  # calendar/app.py: "couldn't read"
            return "day"
    elif verb == "expense.voice_add_expense":
        try:
            if float(args.get("amount") or 0) <= 0:
                return "amount"
        except (TypeError, ValueError):
            return "amount"
        if not str(args.get("description") or "").strip():
            return "description"
    elif verb == "task.voice_add_task":
        if not str(args.get("text") or "").strip():
            return "text"
    return ""


def _action_misses(want: str, outcome: dict, action: dict, today: date,
                   offered: set[str] | None) -> list[str]:
    """Reasons this emitted action does not satisfy the outcome ([] = it does)."""
    if action.get("status") in ("failed", "error"):
        return ["status"]
    if offered is not None and want not in offered:
        return ["not_offered"]
    args = action.get("args") or {}
    bad = [n for n, spec in (outcome.get("args") or {}).items()
           if not arg_matches(want, n, spec, args.get(n), today)]
    refused = _refuses(want, args)
    if refused and refused not in bad:
        bad.append(refused)
    return bad


def score_case(case: dict, actions: list[dict], today: date,
               offered: set[str] | None = None) -> dict:
    """Score one turn.

    ``actions`` is ``[{"verb": "app.method", "args": {...}, "status": ...}]`` in
    emit order. ``offered`` is the set of verbs the agent was given; a verb
    outside it is a miss even if the gate would turn it into a card. An outcome
    with ``verb: ""`` means "no action" (a plain reply) and matches only a turn
    that emitted nothing.

    Returns ``verb_ok`` (some acceptable verb was emitted — or nothing, when
    nothing is acceptable), ``args_ok`` (one emitted action fully satisfies
    one outcome: args, status, offered, and the verb would not refuse),
    ``bad_args`` (why the best near-miss failed), ``extra`` (actions beyond the
    match) and ``broken_extra`` (other actions naming an expected verb that
    would still misbehave — a second card on the phone giving a wrong answer).
    """
    emitted = [a.get("verb", "") for a in actions]
    best = {"verb_ok": False, "args_ok": False, "bad_args": [], "matched": "", "index": -1}
    for outcome in case["expect"]:
        want = outcome.get("verb", "")
        if want == "":
            if not actions:
                best = {"verb_ok": True, "args_ok": True, "bad_args": [], "matched": "",
                        "index": -1}
                break
            continue
        for i, a in enumerate(actions):
            if a.get("verb") != want:
                continue
            bad = _action_misses(want, outcome, a, today, offered)
            if not bad:
                best = {"verb_ok": True, "args_ok": True, "bad_args": [], "matched": want,
                        "index": i}
                break
            if not best["verb_ok"] or len(bad) < len(best["bad_args"]):
                best = {"verb_ok": True, "args_ok": False, "bad_args": bad, "matched": want,
                        "index": i}
        if best["args_ok"]:
            break
    broken = 0
    for i, a in enumerate(actions):
        if i == best["index"]:
            continue
        outs = [o for o in case["expect"] if o.get("verb") and o["verb"] == a.get("verb")]
        if outs and all(_action_misses(o["verb"], o, a, today, offered) for o in outs):
            broken += 1
    matched = 1 if best["index"] >= 0 else 0
    best.update(extra=len(actions) - matched, broken_extra=broken, emitted=emitted)
    del best["index"]
    return best


def unparsed_do(reply: str) -> list[str]:
    """Verbs of ``[DO:`` tokens still in the reply text (never executed)."""
    return _UNPARSED_DO_RE.findall(reply or "")


def summarize(scored: list[dict]) -> dict:
    """Per-category and overall verb / verb+args pass counts, plus how many
    turns left an unparsed ``[DO:`` token in the reply or sent a broken
    extra card."""
    by_cat: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "verb_ok": 0, "args_ok": 0, "unparsed": 0, "broken_extra": 0})
    for row in scored:
        for key in (row["category"], "_all"):
            c = by_cat[key]
            c["n"] += 1
            c["verb_ok"] += int(row["score"]["verb_ok"])
            c["args_ok"] += int(row["score"]["args_ok"])
            c["unparsed"] += int(bool(row.get("unparsed_do")))
            c["broken_extra"] += int(bool(row["score"].get("broken_extra")))
    return dict(sorted(by_cat.items()))


def load_corpus(path: Path = CORPUS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def actions_from_chat(result: dict) -> list[dict]:
    """``[{verb, args, status}]`` from a rooms chat response's server_results."""
    out = []
    for r in result.get("server_results") or []:
        if isinstance(r, dict) and r.get("app") and r.get("method"):
            out.append({"verb": f"{r['app']}.{r['method']}", "args": r.get("args") or {},
                        "status": r.get("status") or ("ok" if r.get("ok") else "error")})
    return out


# ── Running against a daemon ────────────────────────────────────────────────


def _http(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    tok = load_auth_token()
    # The token is this machine's daemon credential; never hand it to another host.
    if tok and urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1"):
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8") or "{}"
    return json.loads(raw)


def _wait_ready(target: str, budget_s: float = 120.0) -> None:
    """Block until the target's rooms app answers. A sandbox `/restart` returns
    at `stage: start`, and `/api/health` is green while apps are still loading,
    so the first GET below raced the boot and 404'd (2026-09-30, twice)."""
    deadline = time.monotonic() + budget_s
    while True:
        try:
            _http("GET", f"{target}/rooms/api/agents", timeout=10)
            return
        except Exception as e:  # 404 / connection refused while booting
            if time.monotonic() > deadline:
                sys.exit(f"target {target} never answered /rooms/api/agents: {e}")
            time.sleep(3)


def _refuse_user_daemon(target: str) -> None:
    port = urllib.parse.urlsplit(target).port
    if port is None or port < 9002:
        sys.exit("refusing: --target must be a leased sandbox member (:9002+), "
                 "never the user-owned :9000/:9001")


def offered_verbs(agent: dict) -> list[str]:
    return sorted(f"{app}.{m}" for app, ms in (agent.get("server_actions") or {}).items()
                  for m in ms)


def _bridge_module():
    """plugins/telegram/bridge.py, loaded from source (kernel-free)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "telegram_bridge_for_eval", ROOT / "plugins" / "telegram" / "bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shipped_persona() -> str:
    """The plugin's current persona seed, read from source (kernel-free)."""
    return _bridge_module().TELEGRAM_BRIDGE_PERSONA


def manager_context_from(manager_dir: Path | None) -> str:
    """The live context the bot sends each turn (profile + newest log lines),
    built by the bot's own helper from a manager directory; '' when none."""
    if manager_dir is None:
        return ""
    if not (manager_dir / "profile.md").is_file():
        # A typo'd path would otherwise score a silent no-context run.
        sys.exit(f"--manager-dir {manager_dir} has no profile.md")

    def _read(name: str) -> str:
        try:
            return (manager_dir / name).read_text(encoding="utf-8")
        except OSError:
            return ""

    return _bridge_module().manager_context(_read("profile.md"), _read("log.md"))


def target_bridge_actions(target: str) -> dict[str, list[str]]:
    """The allowlist the bridge would build on *target* — every voice-surface
    verb's dispatch method, grouped by app, voice-assistant's own excluded —
    read from the target's intent registry (`merged_voice_entries` is what
    both consume). Mirrors ``TelegramPlugin._build_bridge_actions``."""
    reg = _http("GET", f"{target}/voice-assistant/debug/intents").get("registry") or []
    actions: dict[str, list[str]] = {}
    for rec in reg:
        app_id = rec.get("app") or rec.get("_app_id") or ""
        method = rec.get("method") or ""
        if not app_id or not method or app_id == "voice-assistant":
            continue
        methods = actions.setdefault(app_id, [])
        if method not in methods:
            methods.append(method)
    if not actions:
        sys.exit(f"target {target} exposes no voice intents — is voice-assistant loaded?")
    return actions


def run(source: str, target: str, out: Path, limit: int = 0, turn_timeout: float = 240,
        think: str = "", persona: str = "source", actions: str = "source",
        manager_dir: Path | None = None) -> dict:
    """``persona`` / ``actions`` pick what the target agent is built from:
    ``source`` copies the live record (what the phone runs today); ``seed``
    uses the plugin's shipped persona and ``target`` derives the allowlist from
    the target's own registry — what the phone will run once the source daemon
    restarts on the same tree (the seed reaches a live bot whose prompt is an
    earlier seed; a hand-edited prompt survives), which is the question after a
    code change."""
    _refuse_user_daemon(target)
    _wait_ready(target)
    agent = _http("GET", f"{source}/rooms/api/agents/{AGENT_ID}")
    agent = agent.get("agent", agent)
    if not agent.get("system_prompt"):
        sys.exit(f"source {source} has no {AGENT_ID} agent definition")
    if agent.get("gate_mode") != "gate":
        # A non-gate agent returns server_results without args, so every
        # arg-bearing case would score as a miss. The bridge is always gated.
        sys.exit(f"source {AGENT_ID} gate_mode={agent.get('gate_mode')!r}, expected 'gate'")
    if persona == "seed":
        agent["system_prompt"] = shipped_persona()
    if actions == "target":
        agent["server_actions"] = target_bridge_actions(target)
    offered = set(offered_verbs(agent))
    spec = {k: agent[k] for k in ("id", "name", "system_prompt", "server_actions", "gate_mode",
                                  "model", "effort", "provider", "strict_provider",
                                  "timeout_s", "temperature") if k in agent}
    existing = _http("GET", f"{target}/rooms/api/agents/{AGENT_ID}")
    if existing.get("id") or existing.get("agent"):
        _http("PUT", f"{target}/rooms/api/agents/{AGENT_ID}", spec)
    else:
        _http("POST", f"{target}/rooms/api/agents", spec)

    context = manager_context_from(manager_dir)
    cases = load_corpus()
    if limit:
        cases = cases[:limit]
    today = date.today()
    rows = []
    for i, case in enumerate(cases, 1):
        _http("DELETE", f"{target}/rooms/api/history/{AGENT_ID}")
        t0 = time.monotonic()
        try:
            res = _http("POST", f"{target}/rooms/api/chat",
                        {"agent_id": AGENT_ID, "text": case["text"], "context": context},
                        timeout=turn_timeout)
            err = res.get("error", "")
        except Exception as e:  # a timed-out turn is a result, not a crash
            res, err = {}, f"{type(e).__name__}: {e}"
        emitted = actions_from_chat(res)
        score = score_case(case, emitted, today, offered)
        reply = str(res.get("response") or "")
        rows.append({"id": case["id"], "category": case["category"], "lang": case["lang"],
                     "text": case["text"], "actions": emitted, "reply": reply, "error": err,
                     "unparsed_do": unparsed_do(reply),
                     "seconds": round(time.monotonic() - t0, 1), "score": score})
        mark = "ok " if score["args_ok"] else ("VRB" if score["verb_ok"] else "BAD")
        print(f"[{i:>2}/{len(cases)}] {mark} {case['id']:<5} {case['text'][:40]:<40} "
              f"-> {', '.join(score['emitted']) or '(none)'}", flush=True)
    report = {"run_date": today.isoformat(), "source": source, "target": target,
              "think": think, "persona": persona, "actions": actions,
              "manager_context_chars": len(context),
              "agent_prompt_chars": len(agent.get("system_prompt", "")),
              "offered_verbs": sorted(offered), "notes": {},
              "summary": summarize(rows), "cases": rows}
    write_report(out, report)
    return report


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def rescore(report_path: Path) -> dict:
    """Re-score a saved report against the current corpus (same run date)."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    today = date.fromisoformat(report["run_date"])
    offered = set(report["offered_verbs"]) if "offered_verbs" in report else None
    by_id = {c["id"]: c for c in load_corpus()}
    for row in report["cases"]:
        if row["id"] in by_id:
            row["score"] = score_case(by_id[row["id"]], row["actions"], today, offered)
        row["unparsed_do"] = unparsed_do(row.get("reply", ""))
    report["summary"] = summarize(report["cases"])
    return report


def print_summary(summary: dict) -> None:
    print(f"{'category':<12} {'n':>3} {'verb':>6} {'verb+args':>10} {'unparsed':>9} "
          f"{'broken+':>8}")
    for cat, c in summary.items():
        print(f"{cat:<12} {c['n']:>3} {c['verb_ok']:>6} {c['args_ok']:>10} {c['unparsed']:>9} "
              f"{c['broken_extra']:>8}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", default="http://127.0.0.1:9000",
                   help="daemon to copy the live bridge agent from (read-only)")
    r.add_argument("--target", required=True, help="leased sandbox member URL")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--think", default="",
                   help="provenance label: the think providers the member was leased with")
    r.add_argument("--persona", choices=("source", "seed"), default="source",
                   help="source = the live agent's prompt; seed = the plugin's shipped seed")
    r.add_argument("--actions", choices=("source", "target"), default="source",
                   help="source = the live agent's allowlist; target = derived from the "
                        "target's own intent registry (picks up new verbs)")
    r.add_argument("--manager-dir", type=Path, default=None,
                   help="vault manager/ dir: send its profile + newest log lines as live "
                        "context, read once and sent unchanged with every turn (the bot "
                        "re-reads per turn; corpus turns are independent) (default: none)")
    s = sub.add_parser("score")
    s.add_argument("report", type=Path)
    s.add_argument("--write", action="store_true", help="save the re-scored report in place")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        report = run(args.source.rstrip("/"), args.target.rstrip("/"), args.out, args.limit,
                     think=args.think, persona=args.persona, actions=args.actions,
                     manager_dir=args.manager_dir)
    else:
        report = rescore(args.report)
        if args.write:
            write_report(args.report, report)
    print_summary(report["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

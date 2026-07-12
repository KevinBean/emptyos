"""`eos autopilot ...` — autopilot grants + tamper-evident audit chain.

Inspection (verify, list-grants, log, policy, review) + grant issuance
(grant, revoke).
The `grant` command is the operator-side surface for the outbound MCP foundry:
`eos autopilot grant <client-id> task.add` lets an external MCP client
(`actor_type="mcp-client"`, scope `mcp:<client-id>`) auto-run an eligible verb.
The room toolbar chip per `.claude/rules/autopilot-grants.md` is the other
(session-scoped) issuer; both write the same `data/autopilot/grants.json`.

Kept kernel-free: pure filesystem reads/writes of `data/autopilot/`. Fast.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from emptyos.cli._common import emit, resolve_data_dir
from emptyos.sdk.autopilot import (
    all_budgets,
    budget_status,
    load_grants,
    load_holds,
    load_policy,
    review_grants,
    revoke_grant,
    revoke_hold,
    save_grant,
    save_hold,
    set_budget,
    verify_audit,
)

autopilot_app = typer.Typer(
    name="autopilot",
    help="Autopilot grants + tamper-evident audit chain.",
    no_args_is_help=True,
)
console = Console()


def _actor_str(rec: dict) -> str:
    """`type/id` chip for a grant/hold record's actor dict."""
    a = rec.get("actor") or {}
    return f"{a.get('type', '?')}/{a.get('id', '*') or '*'}"


def _exp_str(rec: dict) -> str:
    """`expires_at` trimmed to seconds, or `never`."""
    exp = rec.get("expires_at") or "never"
    return exp[:19] if exp != "never" else exp


def _resolve_data_dir(data_dir: str | None) -> Path:
    """Backward-compatible ``--data-dir`` wrapper around shared plumbing."""
    return resolve_data_dir(explicit=data_dir)


@autopilot_app.command("verify")
def verify_cmd(
    data_dir: str = typer.Option(
        None, "--data-dir", "-d",
        help="Override data directory. Defaults to the daemon's data path.",
    ),
):
    """Walk audit.jsonl and verify the HMAC chain.

    Exits non-zero if any tampering or chain breaks are detected.
    """
    root = _resolve_data_dir(data_dir)
    result = verify_audit(root)
    audit_path = root / "autopilot" / "audit.jsonl"

    if result.get("reason"):
        console.print(f"[red]Read error:[/red] {result['reason']}")
        raise typer.Exit(2)

    n = result.get("lines_checked", 0)
    tampered = result.get("tampered", [])
    ok = result.get("ok", False)

    console.print(f"audit log: [cyan]{audit_path.as_posix()}[/cyan]")
    console.print(f"lines checked: [bold]{n}[/bold]")
    if ok:
        console.print(f"[green]✓ chain intact[/green]")
        raise typer.Exit(0)
    console.print(f"[red]✗ {len(tampered)} broken line(s):[/red] "
                  + ", ".join(str(i) for i in tampered[:10])
                  + (" …" if len(tampered) > 10 else ""))
    raise typer.Exit(1)


@autopilot_app.command("log")
def log_cmd(
    tail: int = typer.Option(20, "--tail", "-n", help="Show last N entries."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Tail the audit log."""
    root = _resolve_data_dir(data_dir)
    audit_path = root / "autopilot" / "audit.jsonl"
    if not audit_path.exists():
        console.print("[dim]No audit entries yet.[/dim]")
        raise typer.Exit(0)
    lines = audit_path.read_text(encoding="utf-8").splitlines()[-tail:]
    table = Table(title=f"Last {len(lines)} audit entries", header_style="bold")
    table.add_column("ts", style="dim", no_wrap=True)
    table.add_column("actor", no_wrap=True)
    table.add_column("verb", style="cyan")
    table.add_column("grant", no_wrap=True)
    table.add_column("ok", justify="center")
    for raw in lines:
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except Exception:
            table.add_row("?", "?", "?", "?", "[red]corrupt[/red]")
            continue
        actor = e.get("actor") or {}
        actor_str = f"{actor.get('type', '?')}/{actor.get('id', '?')[:20]}"
        verb = f"{e.get('app', '?')}.{e.get('method', '?')}"
        gid = e.get("grant_id") or "—"
        ok_str = "[green]✓[/green]" if e.get("ok") else "[red]✗[/red]"
        table.add_row(e.get("ts", "")[:19], actor_str, verb, gid, ok_str)
    console.print(table)


@autopilot_app.command("list-grants")
def list_grants_cmd(
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Show active (non-expired) autopilot grants."""
    root = _resolve_data_dir(data_dir)
    grants = load_grants(root)
    if not grants:
        console.print("[dim]No active grants.[/dim]")
        raise typer.Exit(0)
    table = Table(title=f"{len(grants)} active grant(s)", header_style="bold")
    table.add_column("id", no_wrap=True)
    table.add_column("actor", no_wrap=True)
    table.add_column("verb pattern", style="cyan")
    table.add_column("scope")
    table.add_column("expires")
    for g in grants:
        table.add_row(
            g.get("id", "?"), _actor_str(g),
            g.get("verb_pattern", "?"), g.get("scope", "?"),
            _exp_str(g),
        )
    console.print(table)


@autopilot_app.command("review")
def review_cmd(
    stale_days: int = typer.Option(
        14, "--stale-days",
        help="A persistent grant unfired for this many days counts as stale."),
    as_json: bool = typer.Option(
        False, "--json",
        help="Emit one machine-readable JSON object (agent-cli envelope)."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Periodic grant re-review — flag stale/expiring grants + weekly usage.

    Read-only: never revokes anything. A persistent grant (no expiry) that
    hasn't fired in ``--stale-days`` is flagged stale — revoke it with
    ``eos autopilot revoke <id>`` if the trust is no longer earning its keep.
    Exit code 1 when any stale grants exist, so scripts can use the exit
    code as the re-review signal.
    """
    root = _resolve_data_dir(data_dir)
    rep = review_grants(root, stale_after_days=stale_days)
    stale = rep["stale_ids"]
    if as_json:
        emit(
            not stale,
            "stale_grants" if stale else "ok",
            (
                f"{len(stale)} stale grant(s): " + ", ".join(stale)
                if stale
                else f"{len(rep['grants'])} grant(s), none stale"
            ),
            rep,
            as_json=True,
        )
    week = rep["week"]
    if not rep["grants"]:
        console.print("[dim]No active grants.[/dim]")
    else:
        table = Table(
            title=f"{len(rep['grants'])} active grant(s)", header_style="bold")
        table.add_column("id", no_wrap=True)
        table.add_column("actor", no_wrap=True)
        table.add_column("verb pattern", style="cyan")
        table.add_column("scope")
        table.add_column("fires", justify="right")
        table.add_column("last fired", style="dim")
        table.add_column("status")
        status_style = {
            "stale": "[yellow]stale — consider revoking[/yellow]",
            "expiring": "[magenta]expiring[/magenta]",
            "fresh": "[dim]fresh[/dim]",
            "active": "[green]active[/green]",
        }
        for g in rep["grants"]:
            last = g.get("last_fired_at") or "never"
            table.add_row(
                g.get("id", "?"), _actor_str(g),
                g.get("verb_pattern", "?"), g.get("scope", "?"),
                str(g.get("fire_count", 0)),
                last[:19] if last != "never" else last,
                status_style.get(g.get("status"), g.get("status", "?")),
            )
        console.print(table)
    summary = (
        f"fired [bold]{week['fired']}[/bold]× this week "
        f"({week['ok']} ok / {week['failed']} failed) · "
        f"[yellow]{len(stale)}[/yellow] stale · "
        f"{len(rep['expiring_ids'])} expiring · "
        f"{len(rep['budgets_over'])} over-budget actor(s)"
    )
    console.print(summary)
    raise typer.Exit(1 if stale else 0)


@autopilot_app.command("policy")
def policy_cmd(
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Show the autopilot-eligibility policy (which verbs may ever be granted)."""
    root = _resolve_data_dir(data_dir)
    pol = load_policy(root)
    verbs = pol.get("eligible_verbs") or []
    console.print(f"[bold]{len(verbs)} eligible verbs[/bold] "
                  f"(at [dim]{(root / 'autopilot' / 'policy.json').as_posix()}[/dim]):")
    for v in sorted(verbs):
        console.print(f"  • {v}")


@autopilot_app.command("grant")
def grant_cmd(
    actor_id: str = typer.Argument(
        ..., help="External actor id, e.g. the MCP client id 'codex'."),
    verb_pattern: str = typer.Argument(
        ..., help="Verb, or <app>.* / <app>.<glob> (e.g. task.add)."),
    actor_type: str = typer.Option(
        "mcp-client", "--actor-type",
        help="Actor type. Default mcp-client (the outbound foundry's actor)."),
    scope: str = typer.Option(
        None, "--scope", "-s",
        help="Grant scope. Default mcp:<actor_id> (matches the foundry)."),
    ttl: int = typer.Option(
        None, "--ttl", help="Time-to-live in seconds. Default: until revoked."),
    rationale: str = typer.Option(
        "", "--rationale", help="Why this trust is issued (free text)."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Issue an autopilot grant so an external actor may auto-run a verb.

    Default actor-type is ``mcp-client`` and default scope is
    ``mcp:<actor_id>`` — exactly the scope the outbound MCP foundry checks — so
    ``eos autopilot grant codex task.add`` is enough to let the 'codex' MCP
    client call task.add through the foundry. Refused if the verb isn't
    autopilot-eligible (the policy.json floor; a grant can never override it).
    """
    root = _resolve_data_dir(data_dir)
    scope = scope or f"mcp:{actor_id}"
    try:
        g = save_grant(
            root,
            actor_type=actor_type,
            actor_id=actor_id,
            verb_pattern=verb_pattern,
            scope=scope,
            ttl_seconds=ttl,
            rationale=rationale,
        )
    except ValueError as e:
        console.print(f"[red]refused:[/red] {e}")
        raise typer.Exit(1)
    console.print(
        f"[green]✓ granted[/green] [bold]{g['id']}[/bold] · "
        f"{actor_type}/{actor_id} · [cyan]{verb_pattern}[/cyan] · "
        f"scope={scope} · expires={_exp_str(g)}"
    )


@autopilot_app.command("revoke")
def revoke_cmd(
    grant_id: str = typer.Argument(
        ..., help="Grant id (from `eos autopilot list-grants`)."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Revoke a grant by id. Does NOT undo past auto-applies (they stay in the
    audit log); it only stops future ones."""
    root = _resolve_data_dir(data_dir)
    if revoke_grant(root, grant_id):
        console.print(f"[green]✓ revoked[/green] {grant_id}")
    else:
        console.print(f"[yellow]no grant with id[/yellow] {grant_id}")
        raise typer.Exit(1)


# ── Holds — the pause switch (pivot 2026-06-07) ─────────────────────────
# A hold re-gates a normally-auto `stable` verb: "pause auto for this verb in
# this scope." The inverse of a grant. Useful when `auto_stable_default` is on
# and you want one verb to require review again without killing all automation.


@autopilot_app.command("hold")
def hold_cmd(
    verb_pattern: str = typer.Argument(
        ..., help="Verb to pause, or <app>.* / <app>.<glob> (e.g. task.add)."),
    actor_type: str = typer.Option(
        "cli", "--actor-type",
        help="Actor type to hold. Default cli (the rooms gate's CLI actor)."),
    actor_id: str = typer.Option(
        "", "--actor-id",
        help="Specific actor id, or empty = any actor of this type."),
    scope: str = typer.Option(
        "global", "--scope", "-s",
        help="Hold scope. Default global (pause everywhere)."),
    ttl: int = typer.Option(
        None, "--ttl", help="Time-to-live in seconds. Default: until removed."),
    rationale: str = typer.Option(
        "", "--rationale", help="Why this verb is paused (free text)."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Re-gate an auto-running stable verb (the pause switch).

    ``eos autopilot hold task.add`` makes ``task.add`` require per-instance
    review again even with ``auto_stable_default`` on. No eligibility check — a
    hold only ever adds friction, so it can never widen access. Remove with
    ``eos autopilot unhold <hold-id>``.
    """
    root = _resolve_data_dir(data_dir)
    try:
        h = save_hold(
            root,
            actor_type=actor_type,
            actor_id=actor_id,
            verb_pattern=verb_pattern,
            scope=scope,
            ttl_seconds=ttl,
            rationale=rationale,
        )
    except ValueError as e:
        console.print(f"[red]refused:[/red] {e}")
        raise typer.Exit(1)
    who = f"{actor_type}/{actor_id or '*'}"
    console.print(
        f"[yellow]⏸ held[/yellow] [bold]{h['id']}[/bold] · {who} · "
        f"[cyan]{verb_pattern}[/cyan] · scope={scope} · "
        f"expires={_exp_str(h)}"
    )


@autopilot_app.command("unhold")
def unhold_cmd(
    hold_id: str = typer.Argument(
        ..., help="Hold id (from `eos autopilot list-holds`)."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Remove a hold by id — resume auto-running the verb."""
    root = _resolve_data_dir(data_dir)
    if revoke_hold(root, hold_id):
        console.print(f"[green]✓ resumed[/green] {hold_id}")
    else:
        console.print(f"[yellow]no hold with id[/yellow] {hold_id}")
        raise typer.Exit(1)


# ── Budget caps — per-actor monthly spend ceilings ──────────────────────
# The ledger lives at data/autopilot/budgets.json, fed by the billing
# bridge (`[autopilot] meter_spend`) and enforced at the rooms gate
# (`[autopilot] enforce_budget_caps`). A cap is a ceiling *under* the
# eligibility floor — it only ever adds friction, never widens access.

budget_app = typer.Typer(
    name="budget",
    help="Per-actor monthly spend caps for autopilot.",
    no_args_is_help=True,
)
autopilot_app.add_typer(budget_app)


@budget_app.command("set")
def budget_set_cmd(
    actor_id: str = typer.Argument(
        ..., help="Actor id (app id for billing-metered spend, e.g. 'staff')."),
    monthly_usd: float = typer.Argument(
        ..., help="Monthly cap in USD. 0 or negative clears the cap."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Set (or clear, with 0) an actor's monthly USD cap.

    Accrued spend for the current window is preserved — raising or lowering
    a cap mid-month doesn't reset what was already spent.
    """
    root = _resolve_data_dir(data_dir)
    try:
        rec = set_budget(root, actor_id, monthly_usd)
    except ValueError as e:
        console.print(f"[red]refused:[/red] {e}")
        raise typer.Exit(1)
    cap = rec.get("monthly_cap_usd")
    if cap is None:
        console.print(f"[yellow]cap cleared[/yellow] for [bold]{actor_id}[/bold] "
                      f"(spend tracking continues: ${rec.get('spent_usd', 0):.4f} this window)")
    else:
        console.print(f"[green]✓ cap set[/green] [bold]{actor_id}[/bold] · "
                      f"${cap:.2f}/month · ${rec.get('spent_usd', 0):.4f} already spent this window")


@budget_app.command("show")
def budget_show_cmd(
    actor_id: str = typer.Argument(
        None, help="Actor id. Omit to list every tracked actor."),
    as_json: bool = typer.Option(
        False, "--json", help="Emit one machine-readable JSON object (agent-cli envelope)."),
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Show budget status for one actor, or all tracked actors."""
    root = _resolve_data_dir(data_dir)
    rows = [budget_status(root, actor_id)] if actor_id else all_budgets(root)
    if as_json:
        over = [r["actor_id"] for r in rows if r.get("over")]
        emit(
            not over,
            "over_budget" if over else "ok",
            (
                f"{len(over)} actor(s) over cap: " + ", ".join(over)
                if over
                else f"{len(rows)} actor(s) tracked"
            ),
            {"budgets": rows},
            as_json=True,
        )
    if not rows:
        console.print("[dim]No budgets tracked yet.[/dim]")
        raise typer.Exit(0)
    table = Table(title=f"{len(rows)} actor budget(s)", header_style="bold")
    table.add_column("actor", no_wrap=True)
    table.add_column("cap/month", justify="right")
    table.add_column("spent", justify="right")
    table.add_column("remaining", justify="right")
    table.add_column("window", style="dim")
    for r in rows:
        cap = r.get("monthly_cap_usd")
        rem = r.get("remaining_usd")
        over = r.get("over")
        table.add_row(
            r.get("actor_id", "?"),
            f"${cap:.2f}" if cap is not None else "—",
            f"${r.get('spent_usd', 0):.4f}",
            ("[red]over[/red]" if over
             else (f"${rem:.4f}" if rem is not None else "—")),
            (r.get("window_start") or "")[:10],
        )
    console.print(table)


@autopilot_app.command("list-holds")
def list_holds_cmd(
    data_dir: str = typer.Option(None, "--data-dir", "-d"),
):
    """Show active (non-expired) holds (paused stable verbs)."""
    root = _resolve_data_dir(data_dir)
    holds = load_holds(root)
    if not holds:
        console.print("[dim]No active holds.[/dim]")
        raise typer.Exit(0)
    table = Table(title=f"{len(holds)} active hold(s)", header_style="bold")
    table.add_column("id", no_wrap=True)
    table.add_column("actor", no_wrap=True)
    table.add_column("verb pattern", style="cyan")
    table.add_column("scope")
    table.add_column("expires")
    for h in holds:
        table.add_row(
            h.get("id", "?"), _actor_str(h),
            h.get("verb_pattern", "?"), h.get("scope", "?"),
            _exp_str(h),
        )
    console.print(table)

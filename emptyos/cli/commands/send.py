"""`eos send` — the human-typed gate for the `send` capability.

    eos send you@example.com --subject "Bundle" --body "see attached" \
        --attach dist/worklog-standalone-1.0.0.zip
    eos send … --dry-run          # resolve, scan, render, send nothing
    eos send … --json --yes       # agent-CLI envelope, no prompt

## Why a CLI and not an HTTP route

Outbound to a third party is the one class CLAUDE.md keeps permanently
human-gated — never autopilot-eligible, no grant can raise it, because there is
nothing to undo once it lands. An HTTP endpoint is a surface any page, agent or
`[DO:]` token could POST to; a command someone typed carries the intent in the
act itself. So the capability stays reachable only from here (plus whatever an
app deliberately wires for its own domain, as `bookme` does for confirmations).

## Provider-general on purpose

Everything goes through the `send` capability, never `smtplib`. Today the chain
is `email-smtp` → `human`; the capability table already reserves the slot for
SMS/Resend/SES, and those arrive as providers. So `--to` is "whatever this
provider addresses", not "an email address", and nothing here parses one.
`--attach` is forwarded as a kwarg; a provider that ignores it is told to say so
rather than silently dropping it (see `--require-attachments`).

## Three guards, in order

1. **Leak scan** (`capabilities/outbound_scan.scan_outbound`) over subject +
   body. Secrets and `.eos-personal` shapes abort by default — this is the last
   point before the text leaves the machine. `--allow-findings` overrides, and
   prints what it is overriding.
2. **Render + confirm.** The full provider `consent_summary` is shown, including
   attachment names and sizes, then a y/N prompt. `--yes` skips the prompt (for
   scripts); `--dry-run` stops here unconditionally.
3. **The capability's own cloud-consent gate** still applies underneath. A
   non-local SMTP host is cloud-classified, so an unapproved provider is skipped
   by the chain — which this command reports as a failure rather than a success,
   because "the chain fell through to `human`" is not "sent".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from emptyos.cli._common import emit

# A LEAF command, deliberately not a `typer.Typer()` sub-app: `eos send <to>` has
# no subcommands, and registering a group makes Typer demand one and mis-bind the
# positional argument ("Missing argument 'TO'" against a supplied recipient).
# main.py wires it with `app.command(name="send")(send_cmd)`.


def _resolve_attachments(raw: list[str]) -> tuple[list[Path], list[str]]:
    """(paths, problems). Relative paths resolve against the CALLER's cwd here —
    correct for a CLI, and made absolute before the provider sees them because
    the provider refuses relative paths (it cannot know whose cwd they meant)."""
    paths: list[Path] = []
    problems: list[str] = []
    for item in raw or []:
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.is_file():
            problems.append(f"not a file: {item}")
            continue
        paths.append(p)
    return paths, problems


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / (1024 * 1024):.1f} MB"


def send_cmd(
    to: str = typer.Argument(..., help="Recipient, as the active provider addresses it"),
    subject: str = typer.Option("", "--subject", "-s", help="Subject line"),
    body: str = typer.Option("", "--body", "-b", help="Message body"),
    body_file: str = typer.Option(None, "--body-file", help="Read the body from a file"),
    attach: list[str] = typer.Option(None, "--attach", "-a",
                                     help="File to attach (repeatable)"),
    require_attachments: bool = typer.Option(
        True, "--require-attachments/--allow-drop",
        help="Fail if the provider did not report carrying the attachments"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Resolve, scan and render; send nothing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    allow_findings: bool = typer.Option(
        False, "--allow-findings",
        help="Send even if the leak scan flagged the subject/body"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """Send a message. Prints what will go out and asks before it does."""
    if body_file:
        bf = Path(body_file).expanduser()
        if not bf.is_file():
            emit(False, "invalid_args", f"--body-file not found: {body_file}",
                 None, as_json=as_json)
        body = bf.read_text(encoding="utf-8")
    if not (subject or body or attach):
        emit(False, "invalid_args",
             "nothing to send — give at least one of --subject / --body / --attach",
             None, as_json=as_json)

    paths, problems = _resolve_attachments(attach)
    if problems:
        emit(False, "invalid_args", "; ".join(problems),
             {"attachments": problems}, as_json=as_json)

    # ── guard 1: leak scan over what a human wrote ──────────────────────────
    from emptyos.capabilities.outbound_scan import scan_outbound

    findings = scan_outbound(f"{subject}\n{body}")
    if findings and not allow_findings:
        emit(False, "leak_scan",
             f"{len(findings)} finding(s) in subject/body — "
             "review, or re-run with --allow-findings",
             {"findings": [{"pattern": f.pattern_name, "preview": f.preview}
                            for f in findings]},
             as_json=as_json)

    total = sum(p.stat().st_size for p in paths)
    plan = {
        "to": to,
        "subject": subject,
        "body_chars": len(body),
        "attachments": [{"name": p.name, "path": str(p), "bytes": p.stat().st_size}
                         for p in paths],
        "attachment_bytes": total,
        "findings_overridden": [f.pattern_name for f in findings] if findings else [],
    }

    # ── guard 2: render + confirm ──────────────────────────────────────────
    if not as_json:
        typer.echo(f"To       {to}")
        typer.echo(f"Subject  {subject or '(none)'}")
        for p in paths:
            typer.echo(f"Attach   {p.name}  ({_kb(p.stat().st_size)})  {p}")
        if paths:
            typer.echo(f"         total {_kb(total)}")
        if findings:
            typer.echo(f"WARNING  overriding {len(findings)} leak-scan finding(s): "
                       + ", ".join(f"{f.pattern_name} ({f.preview})" for f in findings))
        typer.echo("")
        typer.echo(body[:1200] + ("\n…" if len(body) > 1200 else ""))
        typer.echo("")

    if dry_run:
        emit(True, "dry_run", "dry run — nothing sent", plan, as_json=as_json)
    if not yes and not as_json and not typer.confirm("Send this?", default=False):
        emit(False, "cancelled", "cancelled — nothing sent", plan, as_json=as_json)
    if not yes and as_json:
        emit(False, "needs_confirm",
             "--json is non-interactive; pass --yes to send or --dry-run to preview",
             plan, as_json=True)

    # ── send through the capability, never a mail library ───────────────────
    async def _run():
        from emptyos.cli.main import _get_kernel

        kernel = _get_kernel()
        await kernel.start()
        try:
            cap = kernel.capability("send")
            if cap is None:
                raise RuntimeError("no `send` capability registered")
            kwargs = {"to": to, "subject": subject, "body": body}
            if paths:
                kwargs["attachments"] = [str(p) for p in paths]
            return await cap.execute(**kwargs)
        finally:
            await kernel.stop()

    try:
        result = asyncio.run(_run())
    except Exception as e:
        emit(False, "error", f"send failed: {type(e).__name__}: {e}",
             plan, as_json=as_json)

    # Capability.execute returns a Result(value, provider, is_cloud, …); the
    # provider's own dict is in `.value`. Read the provider name off the Result,
    # not the value, so a provider that returns a bare string still reports one.
    provider = str(getattr(result, "provider", "") or "")
    value = getattr(result, "value", result)
    value = value if isinstance(value, dict) else {"raw": value}
    detail = str(value.get("detail") or "")
    out = {**plan, "provider": provider, "detail": detail,
           "is_cloud": bool(getattr(result, "is_cloud", False)), "result": value}

    # The chain falling through to `human` is not a send. Report it as failure —
    # a cloud provider skipped by an unapproved consent gate lands exactly here,
    # and calling that "ok" is how a silent non-delivery gets recorded as sent.
    if provider in ("", "human") or value.get("ok") is False:
        emit(False, "not_sent",
             f"not sent — provider was {provider or 'none'!r}. "
             "A cloud `send` provider needs cloud consent granted "
             "(Settings, or [cloud] consent) before it will run.",
             out, as_json=as_json)

    # An attachment the provider never mentions is the failure this whole
    # command exists to make impossible: a mail that arrives without its file.
    if paths and require_attachments and not any(p.name in detail for p in paths):
        emit(False, "attachments_unconfirmed",
             f"{provider} reported {detail!r} without naming the attachment(s) — "
             "it may not support them. Re-run with --allow-drop to accept that.",
             out, as_json=as_json)

    emit(True, "ok",
         f"sent to {to} via {provider}"
         + (f" with {len(paths)} attachment(s), {_kb(total)}" if paths else ""),
         out, as_json=as_json)

"""`eos context ...` — Context Packing subcommand.

Thin Typer wrapper over :mod:`emptyos.context`. Kept kernel-free: packing is
pure filesystem + in-memory work, so this stays in the fast CLI startup path
(no daemon boot, no VaultIndex). Used for manual verification of reducers and
ref round-trips.
"""

from __future__ import annotations

from pathlib import Path

import typer

from emptyos.cli._common import resolve_data_dir
from emptyos.context import load_original, pack_text
from emptyos.context.reducers import REDUCERS

context_app = typer.Typer(
    name="context",
    help="Context Packing — compress large logs/json/diffs before an LLM call; recover originals by ref.",
    no_args_is_help=True,
)


def _store_root() -> Path:
    """Resolve <data_dir>/context without booting the kernel.

    Honors EOS_CONFIG so a ref written under one config's data_dir (e.g. a
    sandbox member, or `eos event log --packed` run with EOS_CONFIG set) is
    recoverable by `eos context show` under the same env.
    """
    return resolve_data_dir() / "context"


@context_app.command("pack")
def pack_cmd(
    file: str = typer.Argument(..., help="Path to the file to pack"),
    kind: str = typer.Option(
        "log", "--kind", "-k", help=f"Reducer kind: {', '.join(sorted(REDUCERS))}"
    ),
) -> None:
    """Pack a file with the given reducer, store the original, print the summary."""
    if kind not in REDUCERS:
        typer.secho(
            f"[ERROR] unknown kind '{kind}' (choose: {', '.join(sorted(REDUCERS))})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    path = Path(file)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        typer.secho(f"[ERROR] {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    result = pack_text(text, kind, source=path.name, store_root=_store_root())
    s = result.stats
    typer.secho(
        f"original {s.get('original_est_tokens', 0)} est tok -> "
        f"packed {s.get('packed_est_tokens', 0)} est tok "
        f"({s.get('reduction_pct', 0)}% reduction)",
        fg=typer.colors.GREEN,
    )
    if result.fail_open:
        typer.secho("(fail-open: returned original unchanged)", fg=typer.colors.YELLOW)
    for ref_id in result.refs:
        typer.echo(f"ref: {ref_id}  (recover with: eos context show {ref_id})")
    typer.echo("")
    typer.echo(result.text)


@context_app.command("show")
def show_cmd(
    ref_id: str = typer.Argument(..., help="A ctx_* ref id from `eos context pack`"),
) -> None:
    """Print the exact original text behind a ctx_* ref (or report it's gone)."""
    original = load_original(_store_root(), ref_id)
    if original is None:
        typer.secho(
            f"[not found] {ref_id} — unknown, malformed, or expired (TTL)",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(original)

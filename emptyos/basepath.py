"""Base-relative path resolution — one implementation, five providers.

A `read`/`write` provider takes an optional `base_path` (the vault root) and must
turn a caller-supplied path into a real one: absolute paths pass through, relative
paths resolve under the base, and with no base a relative path stays relative.

**Top-level and stdlib-only on purpose**, for the same reason as `nethost.py` and
`frontmatter.py`: both the kernel (`capabilities/providers/filesystem.py`) and
plugins outside it need this, and the kernel never imports `emptyos.sdk` at module
level. Keep it dependency-free.

**It exists because there were five of it.** `FilesystemReadProvider._resolve`,
`FilesystemWriteProvider._resolve`, and the `markitdown` / `ocr` / `legacy-doc`
read providers each carried a verbatim copy, three of them under a comment reading
*"Mirror FilesystemReadProvider so vault-relative paths resolve identically"* — a
promise a comment cannot keep. `frontmatter.py` records what happens when a
near-verbatim copy is left to drift; this had more copies than that one did.

The rule the five agreed on, and that this preserves exactly:

    absolute path            -> returned unchanged
    relative + base_path     -> base_path / path
    relative + no base_path  -> returned unchanged (relative, resolved by the caller's cwd)

Two of those three branches are **explicit rather than load-bearing**, which mutation
testing established and which is worth stating so nobody "discovers" it later and
deletes them as dead code with no test to object:

  - the `is_absolute()` early return is redundant, because `Path.__truediv__` already
    discards the left side when the right side is absolute;
  - `if base:` versus `if base is not None:` cannot be observed either, because
    `Path("")` is `Path(".")` and joining that changes nothing.

They stay because they state the intent at the point of decision, and because both
become load-bearing the moment the join stops being plain `/`. No test pins them --
none can.

The branch that IS observable, and the one to protect: this deliberately does NOT
call `Path.resolve()`. No symlink following, no cwd lookup, no filesystem access at
all. It is pure joining, so it works on paths that do not exist yet (what the *write*
provider needs) and never binds a relative result to whichever cwd the caller happens
to have -- which differs between the daemon, the CLI, and a test run.

"""

from __future__ import annotations

from pathlib import Path

__all__ = ["resolve_under_base"]


def resolve_under_base(path: str | Path, base: str | Path | None) -> Path:
    """Resolve `path` under `base`, leaving absolute paths untouched.

    `base` accepts a str, a Path, or None; empty string and None both mean
    "no base", so a caller can pass an unset config value straight through.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    if base:
        return Path(base) / p
    return p

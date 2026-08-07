"""Flag provider hosts that reach hardware you may not own, with no declared trust.

`Provider.is_cloud` infers local-vs-cloud from the host address, and
`host_is_local()` counts private ranges, Tailscale CGNAT (100.64/10) and
`*.ts.net` as local. That is correct for your own machines and wrong for a
*rented* GPU you tunnelled to — the convenient way to reach rented compute makes
someone else's box read as local and skip the cloud-consent gate.

Address inference cannot close that gap, because the address genuinely looks the
same. Only a declaration can. This check finds configured hosts that are neither
loopback nor declared, so the ambiguity is surfaced rather than assumed away.

Reads the machine's own `emptyos.toml` (per-machine, gitignored), so it is a
local hygiene check, not a repo scan. Silent on a healthy config.

Exit code = number of findings that need a decision. See
`.claude/rules/rented-compute.md`.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

VALID_TRUST = {"owned", "rented", "service"}


def _is_ambiguous(host: str) -> bool:
    """True only for hosts where address inference genuinely cannot decide.

    Three zones, and only the middle one is a finding:

    - **loopback** — unambiguously this machine. Nothing to declare.
    - **private / CGNAT / ``*.ts.net`` / ``*.local``** — `host_is_local` says
      local, but a rented GPU on your tailnet lives here too and looks
      identical. **This is the gap.**
    - **public** — `host_is_local` says cloud, the consent gate already fires
      correctly. Requiring a declaration here would flag every OpenAI endpoint
      on a healthy config, which is noise, not signal.

    Uses the real classifiers rather than copies of their literals, so the zones
    cannot drift apart from the code they describe.
    """
    from emptyos.capabilities.consent import host_is_local, host_is_loopback

    return not host_is_loopback(host) and host_is_local(host)


def _walk(table: dict, path: str, out: list) -> None:
    """Collect every sub-table carrying a `host` key, with its trust (if any)."""
    for key, val in table.items():
        if not isinstance(val, dict):
            continue
        here = f"{path}.{key}" if path else key
        if "host" in val and isinstance(val["host"], str):
            out.append((here, val["host"], str(val.get("trust", "") or "")))
        _walk(val, here, out)


def scan(config_path: Path) -> list[dict]:
    if not config_path.exists():
        return []
    with config_path.open("rb") as fh:
        cfg = tomllib.load(fh)

    entries: list[tuple[str, str, str]] = []
    for root in ("plugins", "capabilities", "services"):
        if isinstance(cfg.get(root), dict):
            _walk(cfg[root], root, entries)

    findings = []
    for where, host, trust in entries:
        t = trust.strip().lower()
        if t and t not in VALID_TRUST:
            findings.append({
                "where": where, "host": host, "trust": trust,
                "issue": "invalid_trust",
                "detail": f"trust={trust!r} is not one of {sorted(VALID_TRUST)}; "
                          "it fails closed (treated as cloud), but fix the typo",
            })
            continue
        if t or not _is_ambiguous(host):
            continue
        findings.append({
            "where": where, "host": host, "trust": "",
            "issue": "undeclared_trust",
            "detail": "private/tailnet host with no `trust`. This address reads as "
                      "LOCAL to the consent gate, so if it is a rented or borrowed "
                      "machine, cloud consent silently never fires. Declare "
                      "trust=\"owned\" (your hardware) or \"rented\"/\"service\".",
        })
    return findings


def main() -> int:
    as_json = "--json" in sys.argv
    root = Path(__file__).resolve().parent.parent
    cfg = Path(sys.argv[sys.argv.index("--config") + 1]) if "--config" in sys.argv \
        else root / "emptyos.toml"

    findings = scan(cfg)
    ok = not findings

    if as_json:
        emit_json(
            ok,
            "ok" if ok else "undeclared_trust",
            "all provider hosts declared or loopback" if ok
            else f"{len(findings)} provider host(s) with undeclared trust",
            {"findings": findings, "config": str(cfg)},
        )
        return len(findings)

    if not cfg.exists():
        print(f"no config at {cfg} — nothing to check")
        return 0
    if ok:
        print(f"provider trust: OK ({cfg.name})")
        return 0

    print(f"provider trust: {len(findings)} host(s) need a declaration\n")
    for f in findings:
        print(f"  [{f['issue']}] {f['where']}")
        print(f"      host  = {f['host']}")
        print(f"      {f['detail']}")
    print("\n  See .claude/rules/rented-compute.md")
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())

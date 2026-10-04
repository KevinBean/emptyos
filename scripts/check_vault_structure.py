#!/usr/bin/env python3
"""Scan (and optionally purge) structural drift from a vault.

The companion of ``check_vault_test_leak.py`` (test fixtures) for *general*
structural mess: the low-grade drift that accumulates in a living vault —
empty folder husks, sync-conflict duplicate copies, ``.tmp``/zero-byte/
``Untitled`` litter, and top-level folders/files nobody declared. What "clean"
means is declared in a per-vault contract at
``30_Resources/EmptyOS/_vault-structure.toml`` (next to ``_vault-map.toml``;
auto-generated from the PARA canon on first run, user-editable thereafter).

Classification, same discipline as the leak guard:

  safe      Content-lossless removals, auto-purgeable under ``--purge``:
            empty directories (fixpoint — a dir holding only empty dirs is
            empty), duplicate copies ("name 2.md" / Syncthing
            ``.sync-conflict-*``) whose bytes are IDENTICAL to their base
            file, ``.tmp`` files, and zero-byte files older than the age
            gate (default 24h, so a note mid-creation is never raced).
  review    Needs a human, NEVER auto-touched: divergent duplicate copies
            (base differs or is missing — could hold real edits), non-empty
            ``Untitled*.md``, and any top-level entry not in the contract's
            allowlist (undeclared folders are a naming decision, not a
            deletion).
  advisory  Metrics only, never in the exit code: inbox backlog (count/age),
            unbounded notes (> max_note_kb), app-data dirs over a file-count
            threshold.

``--purge`` first takes an incremental ``fs_snapshot`` of the vault into the
same ``<vault>-backups/`` pool the vault-backup app uses (restorable through
its UI) and ABORTS if the snapshot fails. Review items are never touched —
``.claude/rules/vault-operator.md``: never delete vault files without explicit
user confirmation; running ``--purge`` is that explicit act, scoped to the
lossless classes.

Pure file I/O + tomllib. Does NOT import ``emptyos.kernel`` (no syslog handle,
safe while the daemon is up); ``emptyos.sdk.fs_snapshot`` is kernel-free.

Usage::

    python scripts/check_vault_structure.py                  # report
    python scripts/check_vault_structure.py --json           # agent-cli envelope
    python scripts/check_vault_structure.py --purge          # snapshot, then remove safe classes
    python scripts/check_vault_structure.py --vault D:/Other --contract path.toml

Exit code is the number of *review* findings (0 ⇔ ok), so preflight / a
release gate can treat "needs a human" as the signal while auto-fixable
drift returns 0.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CONTRACT_REL = "30_Resources/EmptyOS/_vault-structure.toml"

# Dirs never walked (dot-dirs are also skipped generically — viewer config,
# search indexes, trash — same leading-dot rule as fs_snapshot). venv trees
# embedded in the vault carry legitimate zero-byte py.typed markers.
_SKIP_DIRS = {".git", ".obsidian", ".trash", ".stfolder", "node_modules",
              "__pycache__", "_claude", "venv", "venv_mac", "site-packages"}

_NUM_DUP = re.compile(r"^(?P<base>.+) (?P<n>\d+)(?P<ext>\.[A-Za-z0-9]+)$")
_SYNC_CONFLICT = re.compile(
    r"^(?P<base>.+)\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+(?P<ext>\.[^.]+)?$")
_UNTITLED = re.compile(r"^Untitled.*\.md$")


def default_contract() -> dict:
    """The PARA canon — deliberately NOT derived from the current tree, which
    would bless whatever drift already exists."""
    return {
        "toplevel": {
            "folders": ["00_Inbox", "10_Projects", "20_Areas", "30_Resources",
                        "40_Archive", "50_Journal", "99_Attachments", "_claude"],
            "files": ["CLAUDE.md", "AGENTS.md", "GEMINI.md"],
        },
        "empty_dirs": {
            # Vault-relative globs never flagged/pruned when empty.
            "keep": ["00_Inbox", "99_Attachments"],
        },
        "junk": {
            "numbered_dup_extensions": [".md"],
            "tmp_suffixes": [".tmp"],
            "untitled": True,
            "sync_conflict": True,
            "min_age_hours": 24,
        },
        "advisory": {
            "inbox_max_items": 30,
            "inbox_max_age_days": 14,
            "max_note_kb": 150,
            "app_dir_max_files": 1500,
        },
        # Designated backup/scratch dirs with a time-to-live: files older than
        # N days inside a listed dir become safe-purgeable (retention_expired).
        # Empty by default — each vault opts its own dirs in, e.g.
        #   [retention]
        #   "99_Attachments/temp-backup" = 90
        "retention": {},
    }


def load_contract(vault: Path, explicit: str | None = None,
                  force_write: bool = False) -> tuple[dict, Path, bool]:
    """Return ``(contract, path, generated)``. Missing contract → generate the
    default and persist it (the ``_vault-map.toml`` lifecycle). A present
    contract is merged over the defaults key-by-key so a hand-trimmed file
    still gets sane values for anything it omits."""
    path = Path(explicit) if explicit else vault / CONTRACT_REL
    contract = default_contract()
    if path.exists() and not force_write:
        try:
            with open(path, "rb") as f:
                loaded = tomllib.load(f)
        except Exception as e:
            raise SystemExit(f"could not parse contract {path}: {e}")
        for section, values in loaded.items():
            if isinstance(values, dict):
                contract.setdefault(section, {}).update(values)
        return contract, path, False
    write_contract(path, contract)
    return contract, path, True


def write_contract(path: Path, contract: dict) -> None:
    def _fmt(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(json.dumps(x) for x in v) + "]"
        return json.dumps(v)

    lines = [
        "# EmptyOS Vault Structure Contract",
        "# Generated by scripts/check_vault_structure.py — user-editable.",
        "# Declares what 'clean' means for THIS vault: the allowed top-level",
        "# entries, junk patterns, and advisory thresholds the structure",
        "# scanner enforces. See .claude/rules/vault-operator.md.",
        "",
    ]
    for section in ("toplevel", "empty_dirs", "junk", "advisory", "retention"):
        lines.append(f"[{section}]")
        for k, v in contract.get(section, {}).items():
            key = k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)
            lines.append(f"{key} = {_fmt(v)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass
class Findings:
    safe: dict[str, list[Path]] = field(default_factory=dict)       # category -> paths
    review: dict[str, list[dict]] = field(default_factory=dict)     # category -> {path, reason}
    advisory: dict[str, list[dict]] = field(default_factory=dict)   # category -> metric rows

    def add_safe(self, cat: str, p: Path) -> None:
        self.safe.setdefault(cat, []).append(p)

    def add_review(self, cat: str, path: str, reason: str) -> None:
        self.review.setdefault(cat, []).append({"path": path, "reason": reason})

    def add_advisory(self, cat: str, row: dict) -> None:
        self.advisory.setdefault(cat, []).append(row)

    @property
    def safe_count(self) -> int:
        return sum(len(v) for v in self.safe.values())

    @property
    def review_count(self) -> int:
        return sum(len(v) for v in self.review.values())


def _skipped(rel_parts: tuple[str, ...]) -> bool:
    return any(p in _SKIP_DIRS or p.startswith(".") for p in rel_parts)


def _same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _kept(rel_posix: str, keep_globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, g) for g in keep_globs)


def _win_extended(p: Path) -> str:
    """``\\\\?\\`` extended-length form — the only way Windows deletes reserved
    names (``nul``, ``con``) and trailing-space/dot paths, which are exactly
    the junk artifacts this scanner hunts. Built lexically from str(p) —
    both resolve() and os.path.abspath() remap reserved names to device
    paths (GetFullPathName), which would point at the wrong object."""
    raw = str(p)
    if not p.is_absolute():
        raw = os.path.join(os.getcwd(), raw)
    return "\\\\?\\" + raw


def _force_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        if os.name != "nt":
            raise
        os.remove(_win_extended(p))


def _force_rmdir(p: Path) -> None:
    try:
        p.rmdir()
    except OSError:
        if os.name != "nt":
            raise
        os.rmdir(_win_extended(p))


def scan(vault: Path, contract: dict) -> Findings:
    f = Findings()
    vault = vault.resolve()
    now = time.time()

    junk = contract.get("junk", {})
    min_age_s = float(junk.get("min_age_hours", 24)) * 3600
    dup_exts = {e.lower() for e in junk.get("numbered_dup_extensions", [".md"])}
    tmp_sufs = {e.lower() for e in junk.get("tmp_suffixes", [".tmp"])}
    keep_globs = list(contract.get("empty_dirs", {}).get("keep", []))
    adv = contract.get("advisory", {})
    retention = {k.replace("\\", "/").strip("/"): float(v)
                 for k, v in (contract.get("retention") or {}).items()}

    def _old_enough(p: Path) -> bool:
        try:
            return (now - p.stat().st_mtime) >= min_age_s
        except OSError:
            return False

    # ── top-level allowlist ──────────────────────────────────────────────
    allowed_folders = set(contract.get("toplevel", {}).get("folders", []))
    allowed_files = set(contract.get("toplevel", {}).get("files", []))
    for entry in sorted(vault.iterdir()):
        name = entry.name
        if entry.is_dir():
            if name in _SKIP_DIRS or name.startswith("."):
                continue  # tool/config dirs — not structure drift
            if name not in allowed_folders:
                f.add_review("toplevel_undeclared", name + "/",
                             "folder not in contract [toplevel].folders — move it, or declare it")
        else:
            if name not in allowed_files:
                f.add_review("toplevel_undeclared", name,
                             "file not in contract [toplevel].files — file it, or declare it")

    # ── junk file classes ────────────────────────────────────────────────
    contract_abs = (vault / CONTRACT_REL).resolve()
    for p in vault.rglob("*"):
        if p.is_dir():
            continue
        rel_parts = p.relative_to(vault).parts
        if _skipped(rel_parts[:-1]):
            continue
        if p.resolve() == contract_abs:
            continue
        name = p.name
        if name.startswith("."):
            continue  # dot-files are machine markers (.seeded, .gitignore) — not junk
        rel = "/".join(rel_parts)

        # Retention dirs (designated backup/scratch) — TTL is the ONLY rule
        # inside them: expired → safe, fresh → left alone (backup copies must
        # never be flagged as dups/untitled; they're deliberate copies).
        rdays = next((d for pfx, d in retention.items()
                      if rel == pfx or rel.startswith(pfx + "/")), None)
        if rdays is not None:
            try:
                if (now - p.stat().st_mtime) >= rdays * 86400:
                    f.add_safe("retention_expired", p)
            except OSError:
                pass
            continue

        m = _SYNC_CONFLICT.match(name)
        if junk.get("sync_conflict", True) and m:
            base = p.with_name(m.group("base") + (m.group("ext") or ""))
            if base.exists() and _same_content(p, base):
                f.add_safe("sync_conflict", p)
            else:
                f.add_review("sync_conflict", rel,
                             "conflict copy diverges from base — merge by hand" if base.exists()
                             else "conflict copy with no base file — inspect")
            continue

        m = _NUM_DUP.match(name)
        if m and m.group("ext").lower() in dup_exts:
            base = p.with_name(m.group("base") + m.group("ext"))
            if base.exists():
                if _same_content(p, base):
                    f.add_safe("numbered_dups", p)
                else:
                    f.add_review("numbered_dups", rel,
                                 "diverges from base copy — merge by hand")
            # No base file → could be a legitimate name ("Chapter 2.md"); leave alone.
            continue

        if p.suffix.lower() in tmp_sufs:
            if _old_enough(p):
                f.add_safe("tmp_files", p)
            continue

        try:
            size = p.stat().st_size
        except OSError:
            continue

        if size == 0:
            if _old_enough(p):
                f.add_safe("zero_byte", p)
            continue

        if junk.get("untitled", True) and _UNTITLED.match(name) and _old_enough(p):
            f.add_review("untitled", rel, "non-empty Untitled note — name it or delete it")
            continue

        max_kb = int(adv.get("max_note_kb", 150))
        if p.suffix.lower() == ".md" and size > max_kb * 1024:
            f.add_advisory("big_notes", {"path": rel, "kb": size // 1024})

    # ── empty directories (fixpoint over the set, nothing deleted here) ──
    # Depth-1 dirs are governed by the top-level allowlist, never pruned as
    # empty — a drained 20_Areas/ must not vanish because it's empty today.
    empty: set[Path] = set()
    for root, dirs, files in os.walk(vault, topdown=False):
        rp = Path(root)
        if rp == vault:
            continue
        rel_parts = rp.relative_to(vault).parts
        if len(rel_parts) == 1 or _skipped(rel_parts):
            continue
        if files:
            continue
        # A child that is skipped/kept is "present" — parent isn't empty.
        if all((rp / d) in empty for d in dirs):
            rel_posix = "/".join(rel_parts)
            if not _kept(rel_posix, keep_globs):
                empty.add(rp)
    for d in sorted(empty):
        f.add_safe("empty_dirs", d)

    # ── advisories ───────────────────────────────────────────────────────
    inbox = vault / "00_Inbox"
    if inbox.is_dir():
        items = [p for p in inbox.rglob("*")
                 if p.is_file() and not _skipped(p.relative_to(vault).parts[:-1])]
        oldest_days = 0.0
        for p in items:
            try:
                oldest_days = max(oldest_days, (now - p.stat().st_mtime) / 86400)
            except OSError:
                pass
        if len(items) > int(adv.get("inbox_max_items", 30)) or \
                oldest_days > float(adv.get("inbox_max_age_days", 14)):
            f.add_advisory("inbox", {"items": len(items), "oldest_days": round(oldest_days)})

    app_root = vault / "30_Resources" / "EmptyOS"
    if app_root.is_dir():
        cap = int(adv.get("app_dir_max_files", 1500))
        for d in sorted(app_root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            n = sum(1 for p in d.rglob("*") if p.is_file())
            if n > cap:
                f.add_advisory("app_dirs", {"path": f"30_Resources/EmptyOS/{d.name}", "files": n})

    # Embedded regenerable environments (venvs, node_modules) — advisory only,
    # never purged: they're skipped from every other scan, so without this row
    # a 500MB venv creeping into the vault would be invisible.
    env_report = {"venv", "venv_mac", ".venv", "node_modules", "site-packages"}
    for root, dirs, files in os.walk(vault):
        rp = Path(root)
        keep = []
        for d in dirs:
            if d in env_report:
                rel = "/".join((rp / d).relative_to(vault).parts)
                f.add_advisory("embedded_env", {"path": rel})
            if d.startswith(".") or d in _SKIP_DIRS or d in env_report:
                continue  # prune — don't descend
            keep.append(d)
        dirs[:] = keep

    return f


def snapshot_before_purge(vault: Path) -> dict:
    """Incremental fs_snapshot into the vault-backup app's pool — restorable
    through its UI, retention-pruned with the nightly backups."""
    sys.path.insert(0, str(REPO))
    from emptyos.sdk import fs_snapshot
    dest_root = vault.parent / f"{vault.name}-backups"
    return fs_snapshot.snapshot_tree(vault, dest_root, mode="incremental")


def purge(vault: Path, findings: Findings, contract: dict) -> dict:
    """Remove SAFE classes only; review + advisory are never touched.
    Empty dirs are re-checked live and pruned to fixpoint (dirs emptied by
    the file deletions above get swept in the same run)."""
    summary: dict = {"files_deleted": 0, "dirs_deleted": 0,
                     "review_skipped": findings.review_count, "errors": 0}
    for cat, paths in findings.safe.items():
        if cat == "empty_dirs":
            continue
        for p in paths:
            try:
                _force_unlink(p)
                summary["files_deleted"] += 1
            except OSError:
                summary["errors"] += 1

    keep_globs = list(contract.get("empty_dirs", {}).get("keep", []))
    while True:
        removed = 0
        for root, dirs, files in os.walk(vault, topdown=False):
            rp = Path(root)
            if rp == vault:
                continue
            rel_parts = rp.relative_to(vault).parts
            if len(rel_parts) == 1 or _skipped(rel_parts):
                continue
            if _kept("/".join(rel_parts), keep_globs):
                continue
            try:
                next(rp.iterdir())
            except StopIteration:
                try:
                    _force_rmdir(rp)
                    removed += 1
                except OSError:
                    summary["errors"] += 1
            except OSError:
                pass
        summary["dirs_deleted"] += removed
        if not removed:
            break
    return summary


def _rel(p: Path, vault: Path) -> str:
    try:
        return str(p.relative_to(vault)).replace("\\", "/")
    except Exception:
        return str(p)


def _report(f: Findings, vault: Path, contract_path: Path, generated: bool) -> None:
    v = vault.resolve()
    if generated:
        print(f"• generated default structure contract: {_rel(contract_path, v)}")

    if f.safe_count:
        print(f"\n── auto-fixable (--purge removes these) — {f.safe_count}")
        for cat, paths in sorted(f.safe.items()):
            print(f"   {cat}: {len(paths)}")
            for p in paths[:8]:
                print(f"      {_rel(p, v)}")
            if len(paths) > 8:
                print(f"      … +{len(paths) - 8} more")

    if f.review_count:
        print(f"\n── needs manual review (NEVER auto-touched) — {f.review_count}")
        for cat, rows in sorted(f.review.items()):
            print(f"   {cat}: {len(rows)}")
            for r in rows[:8]:
                print(f"      {r['path']} — {r['reason']}")
            if len(rows) > 8:
                print(f"      … +{len(rows) - 8} more")

    if f.advisory:
        print("\n── advisory (metrics, not counted)")
        for cat, rows in sorted(f.advisory.items()):
            for r in rows[:10]:
                print(f"   {cat}: {r}")
            if len(rows) > 10:
                print(f"   {cat}: … +{len(rows) - 10} more")

    # NOTE: the one-line total is printed by main() as the LAST stdout line —
    # scripts/preflight.py uses the last line as the check's summary.


def resolve_vault(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    cfg = REPO / "emptyos.toml"
    if cfg.exists():
        try:
            with open(cfg, "rb") as fh:
                data = tomllib.load(fh)
            p = (data.get("notes") or {}).get("path") or ""
            if p:
                return Path(p)
        except Exception:
            pass
    raise SystemExit("could not resolve vault path — pass --vault")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan/purge structural drift from a vault.")
    ap.add_argument("--vault", help="vault root (default: emptyos.toml notes.path)")
    ap.add_argument("--contract", help=f"structure contract path (default: <vault>/{CONTRACT_REL})")
    ap.add_argument("--write-contract", action="store_true",
                    help="regenerate the default contract (overwrites)")
    ap.add_argument("--purge", action="store_true",
                    help="snapshot the vault, then remove safe classes (review items stay)")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="with --purge: skip the fs_snapshot (tests only)")
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope")
    args = ap.parse_args(argv)

    vault = resolve_vault(args.vault)
    if not vault.exists():
        raise SystemExit(f"vault not found: {vault}")

    contract, contract_path, generated = load_contract(
        vault, args.contract, force_write=args.write_contract)

    findings = scan(vault, contract)
    adv_n = sum(len(v) for v in findings.advisory.values())

    data = {
        "vault": str(vault),
        "contract": str(contract_path),
        "contract_generated": generated,
        "safe": {cat: [_rel(p, vault) for p in paths] for cat, paths in findings.safe.items()},
        "review": findings.review,
        "advisory": findings.advisory,
        "safe_count": findings.safe_count,
        "review_count": findings.review_count,
    }

    if args.purge and findings.safe_count:
        if not args.no_snapshot:
            try:
                snap = snapshot_before_purge(vault)
                data["snapshot"] = snap.get("snapshot")
            except Exception as e:
                msg = f"snapshot failed — purge aborted: {e}"
                if args.json:
                    print(json.dumps({"ok": False, "code": "error", "message": msg, "data": data}))
                else:
                    print(msg)
                return max(1, findings.review_count)
        data["purged"] = purge(vault, findings, contract)

    ok = findings.review_count == 0
    if args.json:
        msg = f"{findings.safe_count} auto-fixable, {findings.review_count} review, {adv_n} advisory"
        print(json.dumps({"ok": ok, "code": "ok" if ok else "review",
                          "message": msg, "data": data}))
    else:
        _report(findings, vault, contract_path, generated)
        if args.purge and "purged" in data:
            pg = data["purged"]
            print(f"purged: {pg['files_deleted']} file(s), {pg['dirs_deleted']} dir(s)"
                  + (f" — snapshot: {data.get('snapshot')}" if data.get("snapshot") else ""))
        elif findings.safe_count:
            print("run with --purge to remove the auto-fixable classes (snapshot taken first).")
        if findings.safe_count == 0 and findings.review_count == 0 and adv_n == 0:
            print(f"✓ vault structure clean under {vault.resolve()}")
        else:
            print(f"structure: {findings.safe_count} auto-fixable · "
                  f"{findings.review_count} review · {adv_n} advisory")

    return findings.review_count


if __name__ == "__main__":
    sys.exit(main())

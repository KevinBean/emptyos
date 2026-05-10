#!/usr/bin/env python3
"""App development auto-log — Claude Code Stop hook.

Detects new-app creations and "big" manifest changes during a Claude Code
session and appends structured entries to a rolling log in the vault at
`{vault}/10_Projects/emptyos/log/app-development.md`.

Exit code is always 0 — hooks must never block Claude. Errors go to
`data/hook_errors.log` in the project root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _relpath(fp: str, project_root: Path) -> str:
    """Normalize a path to a forward-slash, project-root-relative form."""
    fp_n = fp.replace("\\", "/")
    rp = str(project_root).replace("\\", "/").rstrip("/")
    if fp_n.startswith(rp):
        return fp_n[len(rp) :].lstrip("/")
    return fp_n


def log_error(project_root: Path, err: str) -> None:
    try:
        log_dir = project_root / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        with (log_dir / "hook_errors.log").open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] log_app_development: {err}\n")
    except Exception:
        pass


def is_emptyos_project(project_root: Path) -> bool:
    rtoml = project_root / "release.toml"
    if not rtoml.exists():
        return False
    try:
        data = tomllib.loads(rtoml.read_text(encoding="utf-8"))
    except Exception:
        return False
    return "release" in data and isinstance(data["release"].get("version"), str)


def read_version(project_root: Path) -> str:
    try:
        data = tomllib.loads((project_root / "release.toml").read_text(encoding="utf-8"))
        return data.get("release", {}).get("version", "?")
    except Exception:
        return "?"


def read_vault_path(project_root: Path) -> Path | None:
    vc = project_root / ".claude" / "vault-connection.json"
    if vc.exists():
        try:
            data = json.loads(vc.read_text(encoding="utf-8"))
            if data.get("connected") and data.get("vault_path"):
                return Path(data["vault_path"])
        except Exception:
            pass
    emptyos_toml = project_root / "emptyos.toml"
    if emptyos_toml.exists():
        try:
            data = tomllib.loads(emptyos_toml.read_text(encoding="utf-8"))
            p = data.get("notes", {}).get("path")
            if p:
                return Path(p)
        except Exception:
            pass
    return None


def git_status(project_root: Path) -> list[tuple[str, str]]:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "apps/"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode != 0:
            return []
    except Exception:
        return []
    rows = []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:].strip().strip('"')
        rows.append((code, path))
    return rows


def git_head_show(project_root: Path, path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode != 0:
            return None
        return out.stdout
    except Exception:
        return None


def detect_events(project_root: Path) -> list[dict]:
    """Return list of {app_id, event, manifest_path, diff_summary}."""
    events: list[dict] = []
    for code, path in git_status(project_root):
        norm = path.replace("\\", "/")
        if not norm.endswith("/manifest.toml") or not norm.startswith("apps/"):
            continue
        # Resolve app id from manifest itself so we pick up apps/personal/<id>/ too.
        full = project_root / norm
        if not full.exists():
            continue
        try:
            manifest = tomllib.loads(full.read_text(encoding="utf-8"))
        except Exception:
            continue
        app_id = manifest.get("app", {}).get("id") or norm.split("/")[-2]
        if code.strip() == "??":
            events.append(
                {
                    "app_id": app_id,
                    "event": "created",
                    "manifest_path": norm,
                    "manifest": manifest,
                    "diff_summary": [],
                }
            )
        elif "M" in code:
            prev = git_head_show(project_root, norm)
            if prev is None:
                continue
            try:
                old = tomllib.loads(prev)
            except Exception:
                continue
            diff = diff_manifest(old, manifest)
            if diff:
                events.append(
                    {
                        "app_id": app_id,
                        "event": "changed",
                        "manifest_path": norm,
                        "manifest": manifest,
                        "diff_summary": diff,
                    }
                )
    return events


def diff_manifest(old: dict, new: dict) -> list[str]:
    diff: list[str] = []
    ov = old.get("app", {}).get("version")
    nv = new.get("app", {}).get("version")
    if ov and nv and ov != nv:
        diff.append(f"`[app].version`: {ov} → {nv}")

    def set_of(d: dict, *keys) -> set:
        cur = d
        for k in keys:
            if not isinstance(cur, dict):
                return set()
            cur = cur.get(k, {})
        if isinstance(cur, list):
            return set(cur)
        return set()

    for path, label in [
        (("requires", "capabilities"), "[requires.capabilities]"),
        (("requires", "apps"), "[requires.apps]"),
        (("provides", "events", "emits"), "[provides.events.emits]"),
        (("provides", "cli", "commands"), "[provides.cli.commands]"),
    ]:
        added = set_of(new, *path) - set_of(old, *path)
        if added:
            diff.append(f"`{label}` added: {', '.join(sorted(added))}")

    # Any new [provides.*] subsection, e.g. adding [provides.web]
    old_provides = set((old.get("provides") or {}).keys())
    new_provides = set((new.get("provides") or {}).keys())
    for added in sorted(new_provides - old_provides):
        diff.append(f"new `[provides.{added}]` block")

    old_contributes = set((old.get("contributes") or {}).keys())
    new_contributes = set((new.get("contributes") or {}).keys())
    for added in sorted(new_contributes - old_contributes):
        diff.append(f"new `[contributes.{added}]` block")

    return diff


def parse_transcript(path: Path) -> dict:
    """Extract duration, per-model tokens, agent invocations, skill trigger, files touched."""
    first_ts: str | None = None
    last_ts: str | None = None
    tokens_by_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    agents_by_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "prompt_chars": 0, "result_chars": 0}
    )
    tool_use_to_subagent: dict[str, str] = {}
    skill_triggers: list[str] = []
    files_touched: set[str] = set()

    if not path.exists():
        return {"duration_min": 0, "tokens_by_model": {}, "agents": {}, "skills": [], "files": []}

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            ts = d.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if t == "assistant":
                msg = d.get("message") or {}
                model = msg.get("model") or "unknown"
                usage = msg.get("usage") or {}
                bucket = tokens_by_model[model]
                bucket["input"] += int(usage.get("input_tokens") or 0)
                bucket["output"] += int(usage.get("output_tokens") or 0)
                bucket["cache_create"] += int(usage.get("cache_creation_input_tokens") or 0)
                bucket["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)

                for c in msg.get("content") or []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        name = c.get("name")
                        inp = c.get("input") or {}
                        if name in ("Agent", "Task"):
                            sa = inp.get("subagent_type") or "default"
                            tool_use_to_subagent[c.get("id", "")] = sa
                            agents_by_type[sa]["count"] += 1
                            agents_by_type[sa]["prompt_chars"] += len(
                                json.dumps(inp.get("prompt") or "")
                            )
                        if name in ("Write", "Edit", "MultiEdit"):
                            fp = inp.get("file_path")
                            if fp:
                                files_touched.add(fp.replace("\\", "/"))
                        if name == "Bash":
                            # Best-effort: mention a relative-looking manifest path in the command.
                            cmd = inp.get("command") or ""
                            for m in re.finditer(r"apps/[\w./-]+/manifest\.toml", cmd):
                                files_touched.add(m.group(0))

            if t == "user":
                msg = d.get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    for m in re.finditer(r"<command-name>([^<]+)</command-name>", content):
                        skill_triggers.append(m.group(1).strip())
                elif isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_result":
                            use_id = c.get("tool_use_id", "")
                            sa = tool_use_to_subagent.get(use_id)
                            if sa:
                                text = c.get("content")
                                if isinstance(text, list):
                                    text = json.dumps(text)
                                agents_by_type[sa]["result_chars"] += len(str(text or ""))

    duration_min = 0
    if first_ts and last_ts:
        try:

            def parse(ts: str) -> datetime:
                # Handle both Z-suffix and explicit offsets
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                return datetime.fromisoformat(ts)

            duration_min = max(0, int((parse(last_ts) - parse(first_ts)).total_seconds() // 60))
        except Exception:
            pass

    seen: set[str] = set()
    skills_unique: list[str] = []
    for s in skill_triggers:
        if s not in seen:
            seen.add(s)
            skills_unique.append(s)

    return {
        "duration_min": duration_min,
        "tokens_by_model": {k: dict(v) for k, v in tokens_by_model.items()},
        "agents": {k: dict(v) for k, v in agents_by_type.items()},
        "skills": skills_unique,
        "files": sorted(files_touched),
        "started_at": first_ts,
    }


def fmt_int(n: int) -> str:
    return f"{n:,}"


_NOISE_COMMANDS = {
    "/clear",
    "/compact",
    "/loop",
    "/exit",
    "/help",
    "/init",
    "/review",
    "/security-review",
}


def pick_trigger(skills: list[str]) -> str:
    for s in skills:
        if s.startswith("/eos-") or "eos-new-app" in s or "eos-new-plugin" in s:
            return f"`{s}` skill"
    for s in skills:
        if s not in _NOISE_COMMANDS:
            return f"`{s}` command"
    return "manual / direct tool use"


def format_entry(
    *,
    event: dict,
    metrics: dict,
    version: str,
    session_id: str,
    project_root: Path,
) -> str:
    now = datetime.now()
    header = f"## {now:%Y-%m-%d %H:%M} — `{event['app_id']}` — {event['event']}"

    manifest = event["manifest"] or {}
    app_meta = manifest.get("app", {})
    dims = app_meta.get("dimensions") or []
    desc = app_meta.get("description") or ""

    trigger = pick_trigger(metrics.get("skills", []))

    lines = [
        header,
        "",
        f"- **Event**: {event['event']}",
        f"- **Trigger**: {trigger}",
        f"- **EmptyOS**: v{version}",
        f"- **Session duration**: {metrics.get('duration_min', 0)} min",
        f"- **Session ID**: `{session_id[:12]}…`" if session_id else "- **Session ID**: unknown",
        f"- **Manifest**: `{event['manifest_path']}`",
    ]
    if dims:
        lines.append(f"- **Dimensions**: {', '.join(dims)}")
    if desc:
        lines.append(f"- **Description**: {desc}")

    if event["event"] == "changed" and event.get("diff_summary"):
        lines += ["", "### Manifest diff"]
        for d in event["diff_summary"]:
            lines.append(f"- {d}")

    tbm = metrics.get("tokens_by_model") or {}
    if tbm:
        lines += ["", "### Token usage (per model)"]
        for model, u in sorted(tbm.items()):
            lines.append(
                f"- {model}: {fmt_int(u.get('input', 0))} in / "
                f"{fmt_int(u.get('output', 0))} out / "
                f"{fmt_int(u.get('cache_create', 0))} cache-create / "
                f"{fmt_int(u.get('cache_read', 0))} cache-read"
            )

    agents = metrics.get("agents") or {}
    if agents:
        lines += ["", "### Agents invoked (per sub-agent)"]
        for sa, u in sorted(agents.items()):
            est = (u.get("prompt_chars", 0) + u.get("result_chars", 0)) // 4
            lines.append(
                f"- {sa} × {u.get('count', 0)} — ~{fmt_int(est)} tokens (est. from prompt+result chars)"
            )
    else:
        lines += ["", "### Agents invoked", "- (none)"]

    interesting_prefixes = ("apps/", "tests/", "emptyos/", "release.toml", "scripts/")
    files = []
    for fp in metrics.get("files") or []:
        rel = _relpath(fp, project_root)
        if rel.startswith(interesting_prefixes):
            files.append(rel)
    if files:
        lines += ["", "### Files touched this session"]
        for rel in files:
            lines.append(f"- `{rel}`")

    lines += ["", "---", ""]
    return "\n".join(lines)


LOG_HEADER = """---
type: app-development-log
tags: [emptyos, dev-log]
---

# EmptyOS App Development Log

Rolling record of app creations and major changes. Entries written by the Stop hook (`scripts/log_app_development.py`).

"""


def _acquire_lock(lock_path: Path, timeout_s: float = 5.0) -> Path:
    """Cross-platform advisory lock via exclusive-create of a sidecar file."""
    import time

    start = time.monotonic()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            # Exclusive-create; fails if another process holds it.
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock_path
        except FileExistsError:
            if time.monotonic() - start > timeout_s:
                # Stale lock? If older than 30s, steal it.
                try:
                    if time.time() - lock_path.stat().st_mtime > 30:
                        lock_path.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass
                raise TimeoutError(f"could not acquire {lock_path}") from None
            time.sleep(0.05)


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def write_log(log_path: Path, session_id: str, entries: list[tuple[dict, str]]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock = log_path.with_suffix(log_path.suffix + ".lock")
    _acquire_lock(lock)
    try:
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else LOG_HEADER
        if not existing.startswith("---"):
            existing = LOG_HEADER + existing

        for event, section in entries:
            sig = f"<!-- session={session_id} app={event['app_id']} event={event['event']} -->"
            full = sig + "\n" + section
            pattern = re.compile(
                re.escape(sig) + r"\n.*?(?=\n<!-- session=|\Z)",
                re.DOTALL,
            )
            if pattern.search(existing):
                existing = pattern.sub(full.rstrip() + "\n", existing, count=1)
            else:
                existing = existing.rstrip() + "\n\n" + full

        # Atomic write: tmp + replace
        tmp = log_path.with_suffix(log_path.suffix + ".tmp")
        tmp.write_text(existing, encoding="utf-8")
        os.replace(tmp, log_path)
    finally:
        _release_lock(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description="App development auto-log (Stop hook).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be logged instead of writing."
    )
    parser.add_argument("--transcript", help="Override transcript path (dry-run diagnostics).")
    parser.add_argument("--session-id", help="Override session_id (dry-run diagnostics).")
    args = parser.parse_args()

    project_root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()

    try:
        if not is_emptyos_project(project_root):
            return 0

        # Parse stdin (Claude Code hook JSON). Tolerate empty stdin in dry-run.
        stdin_data: dict = {}
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                try:
                    stdin_data = json.loads(raw)
                except Exception:
                    stdin_data = {}

        session_id = args.session_id or stdin_data.get("session_id") or ""
        transcript_path = args.transcript or stdin_data.get("transcript_path") or ""

        events = detect_events(project_root)
        if not events:
            return 0

        # Only write when we have a real session context (i.e. fired by the Stop
        # hook with stdin JSON). Manual invocations without --dry-run just no-op.
        if not args.dry_run and not session_id:
            return 0

        metrics = (
            parse_transcript(Path(transcript_path))
            if transcript_path
            else {"duration_min": 0, "tokens_by_model": {}, "agents": {}, "skills": [], "files": []}
        )

        # Prevent uncommitted work from past sessions being re-attributed to
        # whichever session's Stop hook fires next — require the manifest to
        # appear in the current transcript.
        touched_rel = {_relpath(fp, project_root) for fp in metrics.get("files") or []}
        events = [e for e in events if e["manifest_path"] in touched_rel]
        if not events:
            return 0

        version = read_version(project_root)

        entries: list[tuple[dict, str]] = []
        for event in events:
            section = format_entry(
                event=event,
                metrics=metrics,
                version=version,
                session_id=session_id,
                project_root=project_root,
            )
            entries.append((event, section))

        if args.dry_run:
            print(f"# Dry-run — {len(entries)} event(s) detected")
            print(f"# project_root: {project_root}")
            print(f"# session_id: {session_id}")
            print(f"# transcript: {transcript_path}")
            print()
            for _, section in entries:
                print(section)
            return 0

        vault = read_vault_path(project_root)
        if vault is None:
            log_error(
                project_root,
                "vault path not resolvable (no vault-connection.json or emptyos.toml notes.path)",
            )
            return 0

        log_path = vault / "10_Projects" / "emptyos" / "log" / "app-development.md"
        write_log(log_path, session_id, entries)
        return 0
    except Exception:
        log_error(project_root, traceback.format_exc())
        return 0


if __name__ == "__main__":
    sys.exit(main())

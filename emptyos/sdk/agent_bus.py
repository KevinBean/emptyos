"""Agent Context Bus — workspace-level config sync ('Ripple').

Splits boot files (``CLAUDE.md`` / ``GEMINI.md`` / ``AGENTS.md``) into per-section
files under ``.agent-bus/``, mirrors ``.claude/rules`` and ``.claude/skills``
into the same store, and rebuilds the native files from the canonical store on
demand.

Two consumer paths:

1. **External agents** (Claude Code, codex, gemini, cursor) — read the
   reassembled boot files + native ``.claude/`` directly at session start.
   Kept in sync via :func:`run_transpile` (a.k.a. ``ripple``).
2. **Internal think** (``self.think`` from any app, ``eos staff``, ``eos
   code`` agent participants) — read the canonical store selectively via
   :func:`load_section` / :func:`load_rule` / :func:`load_skill` /
   :func:`assemble_boot_file`. The :class:`BaseApp` helpers
   ``bus_context()`` and ``bus_assemble()`` wrap these.

Design notes:

- The boot-file parser respects fenced code blocks — a ``## Timeline`` line
  inside a triple-backtick example block is NOT treated as a real header.
- Section bodies are stored verbatim (the slice between ``## `` headers); a
  clean re-import + immediate ripple is byte-equal to the source.
- TOML serialization escapes backslashes + quotes so Windows paths survive
  round-trip through tomllib.
- Ripple is one-way (canonical → native). :func:`detect_native_divergence`
  surfaces direct native edits so callers can refuse to clobber them.

This module is dependency-free (stdlib only) so the script under
``scripts/agent_bus.py`` can shim into it even from a Python install that
hasn't run ``pip install -e .`` against the EmptyOS repo.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from emptyos.runtime.atomic_io import atomic_write_text

__all__ = [
    "split_markdown_into_sections",
    "slugify",
    "load_toml",
    "dump_toml",
    "discover_rules_and_skills_dirs",
    "rel_posix",
    "run_import",
    "run_transpile",
    "check_dry_run",
    "run_status",
    "detect_native_divergence",
    "assemble_boot_file",
    "load_section",
    "load_rule",
    "load_skill",
    "rules_for_paths",
    "rules_menu_for_paths",
]


# ─── Markdown parser (fenced-code-block aware) ────────────────────────────────

_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")


def split_markdown_into_sections(content: str):
    """Split markdown by top-level ``## `` headers, ignoring headers inside fenced code blocks.

    Returns ``(header_text, [(section_title, section_body), ...])``. Spacing is
    preserved verbatim — ``section_body`` holds every line that lived between
    the section's header and the next one's, so a clean import followed
    immediately by ripple produces a byte-equal boot file.
    """
    lines = content.splitlines()
    header_lines: list[str] = []
    sections: list[tuple[str, str]] = []

    current_title: str | None = None
    current_body: list[str] = []
    in_fence = False
    fence_marker: str | None = None

    for line in lines:
        m = _FENCE_RE.match(line)
        if m:
            this_marker = m.group(2)[0]  # ` or ~
            if not in_fence:
                in_fence = True
                fence_marker = this_marker
            elif this_marker == fence_marker:
                in_fence = False
                fence_marker = None

        is_header = (
            (not in_fence)
            and line.startswith("## ")
            and not line.startswith("### ")
        )

        if is_header:
            if current_title is None:
                header_lines = current_body
            else:
                sections.append((current_title, "\n".join(current_body)))
            current_title = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)

    if current_title is None:
        header_lines = current_body
    else:
        sections.append((current_title, "\n".join(current_body)))

    return "\n".join(header_lines), sections


def slugify(text: str) -> str:
    """Filename-safe slug for a section title."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-")


# ─── TOML I/O (Windows-path safe) ─────────────────────────────────────────────


def _toml_escape(s: str) -> str:
    """Escape a string for TOML basic-string serialization."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _toml_str(s: str) -> str:
    return f'"{_toml_escape(s)}"'


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, (int, float)):
        return str(v)
    raise TypeError(f"Unsupported TOML value type: {type(v).__name__}")


def load_toml(path):
    """Load a TOML file via stdlib ``tomllib`` (Python 3.11+, required by EmptyOS)."""
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def dump_toml(data: dict) -> str:
    """Serialize a one-deep dict to TOML with proper escaping."""
    lines = []
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k} = {_toml_value(v)}")
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        lines.append(f"\n[{k}]")
        for subk, subv in v.items():
            lines.append(f"{subk} = {_toml_value(subv)}")
    return "\n".join(lines) + "\n"


# ─── Workspace discovery ──────────────────────────────────────────────────────


def discover_rules_and_skills_dirs(workspace_root):
    """Find the canonical rules/ + skills/ dirs under .claude/ or _claude/.

    Returns ``(rules_dir, skills_dir)`` as absolute :class:`Path`, defaulting
    to ``.claude/{rules,skills}`` when neither prefix exists.
    """
    root = Path(workspace_root)
    rules_dir = None
    skills_dir = None
    for prefix in ["_claude", ".claude"]:
        p = root / prefix
        if not p.is_dir():
            continue
        r = p / "rules"
        s = p / "skills"
        if r.is_dir() and rules_dir is None:
            rules_dir = r
        if s.is_dir() and skills_dir is None:
            skills_dir = s
    if rules_dir is None:
        rules_dir = root / ".claude" / "rules"
    if skills_dir is None:
        skills_dir = root / ".claude" / "skills"
    return rules_dir, skills_dir


def rel_posix(p, root) -> str:
    """Path relative to ``root``, using forward slashes (Windows-safe in TOML)."""
    return str(Path(p).resolve().relative_to(Path(root).resolve())).replace("\\", "/")


# ─── Assembly (canonical → boot file string) ──────────────────────────────────


def assemble_boot_file(manifest: dict, sections_dir, assembly_key: str) -> str:
    """Rebuild a boot file's full text from the canonical sections.

    The inverse of :func:`split_markdown_into_sections`: concatenate
    ``header_body`` and each ``## Title\\n<body>`` block with single-``\\n``
    separators, ensuring exactly one trailing newline.

    Raises :class:`FileNotFoundError` on a missing referenced section so we
    never silently emit a half-broken boot file.
    """
    ass = manifest[assembly_key]
    sections_list = ass["sections"]
    titles = manifest.get("sections", {})

    parts = []
    for sec_key in sections_list:
        sec_file = Path(sections_dir) / f"{sec_key}.md"
        if not sec_file.exists():
            raise FileNotFoundError(
                f"Manifest references section '{sec_key}' but {sec_file} is missing. "
                f"Re-run `agent-bus import` to rebuild the canonical store."
            )
        body = sec_file.read_text(encoding="utf-8")
        if sec_key.endswith("-header"):
            parts.append(body)
        else:
            display = titles.get(sec_key, sec_key)
            parts.append(f"## {display}\n{body}")

    text = "\n".join(parts)
    if not text.endswith("\n"):
        text += "\n"
    return text


# ─── Selective readers (used by BaseApp.bus_context / bus_assemble) ───────────


def _bus_dir(workspace) -> Path:
    return Path(workspace).resolve() / ".agent-bus"


def load_section(workspace, key: str) -> str | None:
    """Return the verbatim body of ``.agent-bus/sections/<key>.md`` or None."""
    f = _bus_dir(workspace) / "sections" / f"{key}.md"
    return f.read_text(encoding="utf-8") if f.exists() else None


def load_rule(workspace, name: str) -> str | None:
    """Return the verbatim text of ``.agent-bus/rules/<name>.md`` or None.

    Accepts ``name`` with or without the ``.md`` suffix.
    """
    if not name.endswith(".md"):
        name = f"{name}.md"
    f = _bus_dir(workspace) / "rules" / name
    return f.read_text(encoding="utf-8") if f.exists() else None


def load_skill(workspace, name: str) -> str | None:
    """Return the SKILL.md body of ``.agent-bus/skills/<name>/SKILL.md`` or None."""
    f = _bus_dir(workspace) / "skills" / name / "SKILL.md"
    return f.read_text(encoding="utf-8") if f.exists() else None


# ─── L0 index (the abstract menu — scan-then-drill) ──────────────────────────


def _fm_value(text: str, *keys: str) -> str:
    """Return the first matching top-level frontmatter scalar, or "".

    Deliberate narrow re-implementation of the scalar slice of
    ``emptyos.sdk.utils.parse_frontmatter``: this module is stdlib-only by
    contract (see module docstring) so the ``scripts/agent_bus.py`` shim works
    without ``pip install -e .`` — importing ``sdk.utils`` would break that.
    Only handles the ``key: value`` scalar shape we need for abstracts; lists
    and nested blocks are ignored.
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end < 0:
        return ""
    block = text[3:end]
    wanted = {k.lower() for k in keys}
    for line in block.splitlines():
        if ":" not in line or line[:1] in (" ", "\t", "#", "-"):
            continue
        k, _, v = line.partition(":")
        if k.strip().lower() in wanted:
            return v.strip().strip("'\"")
    return ""


def _strip_frontmatter(text: str) -> str:
    # Stdlib-only twin of emptyos.sdk.utils.strip_frontmatter (see _fm_value).
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    rest = text[end + 4 :]
    return rest[rest.find("\n") + 1 :] if "\n" in rest else ""


def _extract_abstract(text: str, *, fallback: str = "", limit: int = 240) -> str:
    """L0 one-liner for a bus entry.

    Priority: frontmatter ``abstract:`` > frontmatter ``description:`` (skills)
    > first markdown heading line > ``fallback``. Truncated to ``limit`` chars.
    """
    for key in ("abstract", "description"):
        v = _fm_value(text, key)
        if v:
            return v[:limit]
    for line in _strip_frontmatter(text).splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("# ").strip()[:limit]
    return fallback[:limit]


def list_bus_entries(workspace, *, rules=True, sections=True, skills=True) -> list[dict]:
    """The L0 menu of bus entries as ``[{kind, name, abstract}]``.

    Reads the same canonical ``.agent-bus/`` store that :func:`load_rule` /
    :func:`load_section` / :func:`load_skill` read, so the menu and a follow-up
    ``bus_context()`` drill never disagree. Pure file IO; never raises — an
    unreadable entry is skipped.
    """
    bus = _bus_dir(workspace)
    out: list[dict] = []

    if sections:
        manifest_path = bus / "manifest.toml"
        if manifest_path.exists():
            try:
                manifest = load_toml(manifest_path)
            except Exception:
                manifest = {}
            for key, title in (manifest.get("sections") or {}).items():
                try:
                    text = load_section(bus.parent, key) or ""
                except Exception:
                    continue
                out.append({
                    "kind": "section",
                    "name": key,
                    "abstract": _extract_abstract(text, fallback=str(title or key)),
                })

    if rules:
        for f in sorted((bus / "rules").glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            out.append({"kind": "rule", "name": f.stem,
                        "abstract": _extract_abstract(text, fallback=f.stem)})

    if skills:
        for f in sorted((bus / "skills").glob("*/SKILL.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            out.append({"kind": "skill", "name": f.parent.name,
                        "abstract": _extract_abstract(text, fallback=f.parent.name)})

    return out


# ─── Path-scoped rules (deterministic path → rule matching) ──────────────────


def _fm_list(text: str, key: str) -> list[str]:
    """Return a top-level frontmatter list value (block-style or inline), or [].

    Handles the two shapes rule files use::

        paths:
          - "apps/**/pages/*.html"
          - emptyos/web/static/**

        paths: ["apps/**", "tests/**"]

    Stdlib-only by module contract (see :func:`_fm_value`).
    """
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end < 0:
        return []
    key_l = key.lower()
    in_list = False
    out: list[str] = []
    for line in text[3:end].splitlines():
        if in_list:
            s = line.strip()
            if s.startswith("- "):
                out.append(s[2:].strip().strip("'\""))
                continue
            if not s or s.startswith("#"):
                continue
            break  # next top-level key — list ended
        if line[:1] in (" ", "\t", "#", "-") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip().lower() != key_l:
            continue
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            return [p.strip().strip("'\"") for p in v[1:-1].split(",") if p.strip().strip("'\"")]
        in_list = True
    return out


def rules_for_paths(workspace, paths) -> list[dict]:
    """Match file paths against ``paths:`` frontmatter globs in rule files.

    The alibaba/open-code-review borrow (2026-06-12): path-pattern → rule
    matching is done by deterministic code, not the LLM — given the repo paths
    a change touches, return the rules whose ``paths:`` frontmatter matches at
    least one, as ``[{name, abstract, patterns}]`` sorted by rule name. See
    ``.claude/rules/path-scoped-rules.md``.

    Reads the NATIVE rules dir (what a spawned CLI will itself read), falling
    back to the canonical ``.agent-bus/rules`` store when no native dir
    exists. Rules without a ``paths:`` key never match — they're unscoped
    (always loaded anyway), and injecting them would re-add the noise this
    resolver exists to cut.

    Matching is :func:`fnmatch.fnmatchcase` per pattern, with both sides
    normalised to forward slashes and casefolded. fnmatch's ``*`` crosses
    ``/`` so ``**`` and ``*`` are equivalent here; patterns are written with
    ``**`` for compatibility with real glob engines. Pure file IO; never
    raises — unreadable entries are skipped.
    """
    import fnmatch

    norm = [
        s for s in (str(p).replace("\\", "/").lstrip("./").casefold() for p in paths or [])
        if s
    ]
    if not norm:
        return []

    rules_dir, _ = discover_rules_and_skills_dirs(workspace)
    if not rules_dir.is_dir():
        rules_dir = _bus_dir(workspace) / "rules"
        if not rules_dir.is_dir():
            return []

    out: list[dict] = []
    for f in sorted(rules_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        patterns = _fm_list(text, "paths")
        if not patterns:
            continue
        pats = [pt.replace("\\", "/").casefold() for pt in patterns]
        if any(fnmatch.fnmatchcase(p, pt) for p in norm for pt in pats):
            out.append({
                "name": f.stem,
                "abstract": _extract_abstract(text, fallback=f.stem),
                "patterns": patterns,
            })
    return out


def rules_menu_for_paths(workspace, paths, *, limit: int = 8) -> str:
    """Prompt-injectable menu of path-matched rules ("" when none match).

    One line per matched rule — ``- .claude/rules/<name>.md — <abstract>`` —
    capped at ``limit`` so a wide diff can't flood the prompt.
    """
    matched = rules_for_paths(workspace, paths)[: max(0, int(limit))]
    return "\n".join(f"- .claude/rules/{m['name']}.md — {m['abstract']}" for m in matched)


# ─── Commands ─────────────────────────────────────────────────────────────────


def _replace_dir(staged: Path, live: Path) -> None:
    """Swap ``staged`` into ``live``'s place using renames only.

    Two ``os.replace`` calls, both metadata-only: move the live directory aside,
    move the staged one in, then delete the old copy. Nothing here reads or
    writes file *contents*, so the one operation that can block on a file lock
    (the copying) has already finished before anything destructive starts.

    If the second rename fails the first is rolled back, so the caller never
    observes a missing store.
    """
    backup = live.with_name(f"{live.name}.old-{os.getpid()}")
    shutil.rmtree(backup, ignore_errors=True)
    if live.exists():
        os.replace(live, backup)
    try:
        os.replace(staged, live)
    except OSError:
        if backup.exists() and not live.exists():
            os.replace(backup, live)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def run_import(workspace_path, *, log=print) -> None:
    """Initialize/refresh ``.agent-bus/`` from boot files + ``.claude/``.

    Builds into a staging directory and swaps it in at the end, rather than
    wiping the live store first and refilling it.

    The old order made a *partial* store the normal outcome of any failure: it
    ``rmtree``'d ``sections/``, ``rules/`` and ``skills/`` up front, then copied
    files back one at a time. A single locked source file — another process
    holding a rule open, which is routine on a machine running several agent
    sessions — aborted the loop with the store already emptied. Observed
    2026-07-25: 35 of 62 rules survived, and the store stayed that way, because
    nothing about the failure said "your context store is now half missing".

    Staging inverts that: every fallible read/write happens while the live store
    is untouched, so a lock now costs an aborted import rather than a truncated
    store. A failed import is a no-op.
    """
    root = Path(workspace_path).resolve()
    log(f"[*] Importing agent context from workspace: {root}")

    bus_dir = root / ".agent-bus"
    bus_dir.mkdir(parents=True, exist_ok=True)

    staging = bus_dir / f".staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    sections_dir = staging / "sections"
    rules_dest_dir = staging / "rules"
    skills_dest_dir = staging / "skills"
    for d in (sections_dir, rules_dest_dir, skills_dest_dir):
        d.mkdir(parents=True, exist_ok=True)

    try:
        _build_import(
            root, staging, sections_dir, rules_dest_dir, skills_dest_dir,
            bus_dir, log=log,
        )
    except BaseException:
        # The live store was never touched — leave it exactly as it was.
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _build_import(
    root: Path,
    staging: Path,
    sections_dir: Path,
    rules_dest_dir: Path,
    skills_dest_dir: Path,
    bus_dir: Path,
    *,
    log=print,
) -> None:
    """Populate the staging dirs, then swap them into the live store."""
    manifest: dict = {
        "version": "1.0.0",
        "workspace_name": root.name,
        "last_import": datetime.now().isoformat(),
        "sections": {},
    }

    boot_files = ["CLAUDE.md", "GEMINI.md", "AGENTS.md"]
    assemblies: dict = {}

    for filename in boot_files:
        filepath = root / filename
        if not filepath.exists():
            continue
        log(f"    [+] Parsing boot file: {filename}")
        content = filepath.read_text(encoding="utf-8")
        header, sections = split_markdown_into_sections(content)
        file_slug = filename.split(".")[0].lower()
        header_key = f"{file_slug}-header"

        (sections_dir / f"{header_key}.md").write_text(header, encoding="utf-8")
        assembly_sections = [header_key]

        seen_slugs: set[str] = set()
        for title, body in sections:
            base_slug = slugify(title) or "section"
            slug = base_slug
            n = 2
            while f"{file_slug}-{slug}" in seen_slugs:
                slug = f"{base_slug}-{n}"
                n += 1
            seen_slugs.add(f"{file_slug}-{slug}")
            section_key = f"{file_slug}-{slug}"
            (sections_dir / f"{section_key}.md").write_text(body, encoding="utf-8")
            manifest["sections"][section_key] = title
            assembly_sections.append(section_key)

        assemblies[file_slug] = {"boot_file": filename, "sections": assembly_sections}

    src_rules_dir, src_skills_dir = discover_rules_and_skills_dirs(root)

    if src_rules_dir.exists():
        log(f"    [+] Copying rules from {rel_posix(src_rules_dir, root)}...")
        for item in src_rules_dir.iterdir():
            if item.is_file() and item.suffix == ".md":
                shutil.copy2(item, rules_dest_dir / item.name)

    if src_skills_dir.exists():
        log(f"    [+] Copying skills from {rel_posix(src_skills_dir, root)}...")
        for item in src_skills_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                dest_skill = skills_dest_dir / item.name
                if dest_skill.exists():
                    shutil.rmtree(dest_skill)
                shutil.copytree(item, dest_skill)

    manifest["original_rules_dir"] = rel_posix(src_rules_dir, root)
    manifest["original_skills_dir"] = rel_posix(src_skills_dir, root)
    for name, ass in assemblies.items():
        manifest[f"assembly_{name}"] = ass

    # Everything fallible is done — the staged tree is complete. From here on
    # it is renames only.
    for name in ("sections", "rules", "skills"):
        _replace_dir(staging / name, bus_dir / name)
    shutil.rmtree(staging, ignore_errors=True)

    # The manifest names what the store now holds, so it is written last and
    # atomically (.claude/rules/atomic-persistence.md) — a torn manifest beside
    # a good store is the same class of half-state this function exists to avoid.
    manifest_path = bus_dir / "manifest.toml"
    atomic_write_text(manifest_path, dump_toml(manifest))
    log(f"[OK] Import complete. Manifest written to {rel_posix(manifest_path, root)}\n")


def detect_native_divergence(manifest: dict, root, bus_dir) -> list[str]:
    """Detect cases where the native side was edited directly since the last ripple.

    Returns a list of human-readable strings — empty when in sync (or when the
    native side is purely behind the canonical side, which ripple will fix).
    """
    root = Path(root)
    bus_dir = Path(bus_dir)
    rules_src_dir = bus_dir / "rules"
    skills_src_dir = bus_dir / "skills"
    rules_native = root / manifest.get("original_rules_dir", ".claude/rules")
    skills_native = root / manifest.get("original_skills_dir", ".claude/skills")

    divergences = []

    if rules_src_dir.exists() and rules_native.exists():
        for native in rules_native.iterdir():
            if not (native.is_file() and native.suffix == ".md"):
                continue
            canonical = rules_src_dir / native.name
            if canonical.exists():
                if native.read_text(encoding="utf-8") != canonical.read_text(encoding="utf-8"):
                    divergences.append(
                        f"native edit: {rel_posix(native, root)} differs from "
                        f"{rel_posix(canonical, root)}"
                    )

    if skills_src_dir.exists() and skills_native.exists():
        for canonical_skill in skills_src_dir.iterdir():
            if not canonical_skill.is_dir():
                continue
            native_skill = skills_native / canonical_skill.name
            if not native_skill.exists():
                continue
            for canonical_file in canonical_skill.rglob("*"):
                if not canonical_file.is_file():
                    continue
                rel = canonical_file.relative_to(canonical_skill)
                native_file = native_skill / rel
                if not native_file.exists():
                    continue
                if canonical_file.read_bytes() != native_file.read_bytes():
                    divergences.append(
                        f"native edit: {rel_posix(native_file, root)} differs from canonical"
                    )

    return divergences


class RippleError(RuntimeError):
    """Raised by :func:`run_transpile` for caller-actionable failures.

    Carries a numeric ``code`` so CLI wrappers can map it to an exit status:

    - ``2`` — native divergence detected, ``--force`` not passed.
    - ``3`` — manifest references a missing section file.
    """

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def run_transpile(workspace_path, *, force: bool = False, log=print) -> None:
    """Regenerate native files from the canonical store ('ripple')."""
    root = Path(workspace_path).resolve()
    log(f"[*] Transpiling agent context in workspace: {root}")

    bus_dir = root / ".agent-bus"
    manifest_path = bus_dir / "manifest.toml"
    if not manifest_path.exists():
        raise RippleError(1, f"Manifest not found at {rel_posix(manifest_path, root)}. Run `import` first.")

    manifest = load_toml(manifest_path)

    divergences = detect_native_divergence(manifest, root, bus_dir)
    if divergences and not force:
        msg = "Native files have diverged from the canonical store:\n" + "\n".join(
            f"    - {d}" for d in divergences
        ) + (
            "\n\nRipple is one-way (canonical → native). Either:\n"
            "  - pull those edits back into .agent-bus/ (and re-import), or\n"
            "  - re-run with --force to discard the native edits and overwrite."
        )
        raise RippleError(2, msg)

    sections_dir = bus_dir / "sections"
    rules_src_dir = bus_dir / "rules"
    skills_src_dir = bus_dir / "skills"
    rules_native = root / manifest.get("original_rules_dir", ".claude/rules")
    skills_native = root / manifest.get("original_skills_dir", ".claude/skills")

    assembly_keys = [k for k in manifest.keys() if k.startswith("assembly_")]
    for key in assembly_keys:
        ass = manifest[key]
        boot_file = ass["boot_file"]
        log(f"    [+] Regenerating boot file: {boot_file}")
        try:
            content = assemble_boot_file(manifest, sections_dir, key)
        except FileNotFoundError as e:
            raise RippleError(3, str(e))
        (root / boot_file).write_text(content, encoding="utf-8")

    if rules_src_dir.exists():
        log(f"    [+] Synchronizing rules to {rel_posix(rules_native, root)}...")
        rules_native.mkdir(parents=True, exist_ok=True)
        existing = {p.name for p in rules_native.iterdir() if p.is_file() and p.suffix == ".md"}
        copied = set()
        for item in rules_src_dir.iterdir():
            if item.is_file() and item.suffix == ".md":
                shutil.copy2(item, rules_native / item.name)
                copied.add(item.name)
        for old in existing - copied:
            log(f"    [-] Removing obsolete rule: {old}")
            (rules_native / old).unlink()

    if skills_src_dir.exists():
        log(f"    [+] Synchronizing skills to {rel_posix(skills_native, root)}...")
        skills_native.mkdir(parents=True, exist_ok=True)
        existing = {p.name for p in skills_native.iterdir() if p.is_dir()}
        copied = set()
        for item in skills_src_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                dest_skill = skills_native / item.name
                if dest_skill.exists():
                    shutil.rmtree(dest_skill)
                shutil.copytree(item, dest_skill)
                copied.add(item.name)
        for old in existing - copied:
            if old == "_retired":
                continue
            log(f"    [-] Removing obsolete skill: {old}")
            shutil.rmtree(skills_native / old)

    manifest["last_ripple"] = datetime.now().isoformat()
    manifest_path.write_text(dump_toml(manifest), encoding="utf-8")
    log("[OK] Transpilation complete.\n")


def check_dry_run(workspace_path, *, log=print) -> int:
    """Print what ``run_transpile`` would change, without writing anything.

    Returns the count of changes that would be applied (0 = in sync).
    """
    root = Path(workspace_path).resolve()
    bus_dir = root / ".agent-bus"
    manifest_path = bus_dir / "manifest.toml"
    if not manifest_path.exists():
        log(f"[-] Context Bus not initialized in {root}. Skipping (run `import` to initialize).")
        return 0

    manifest = load_toml(manifest_path)
    sections_dir = bus_dir / "sections"
    rules_src_dir = bus_dir / "rules"
    skills_src_dir = bus_dir / "skills"
    rules_native = root / manifest.get("original_rules_dir", ".claude/rules")
    skills_native = root / manifest.get("original_skills_dir", ".claude/skills")

    changes: list[str] = []

    assembly_keys = [k for k in manifest.keys() if k.startswith("assembly_")]
    for key in assembly_keys:
        ass = manifest[key]
        boot_file = ass["boot_file"]
        try:
            new_content = assemble_boot_file(manifest, sections_dir, key)
        except FileNotFoundError as e:
            changes.append(f"[BROKEN] {boot_file}: {e}")
            continue
        boot_path = root / boot_file
        if not boot_path.exists():
            changes.append(f"[NEW] {boot_file}")
        elif boot_path.read_text(encoding="utf-8") != new_content:
            changes.append(f"[MODIFY] {boot_file}")

    if rules_src_dir.exists():
        existing = (
            {p.name for p in rules_native.iterdir() if p.is_file() and p.suffix == ".md"}
            if rules_native.exists()
            else set()
        )
        canonical = set()
        for item in rules_src_dir.iterdir():
            if not (item.is_file() and item.suffix == ".md"):
                continue
            canonical.add(item.name)
            dest = rules_native / item.name
            if not dest.exists():
                changes.append(f"[NEW] {rel_posix(rules_native, root)}/{item.name}")
            elif item.read_text(encoding="utf-8") != dest.read_text(encoding="utf-8"):
                changes.append(f"[MODIFY] {rel_posix(rules_native, root)}/{item.name}")
        for old in existing - canonical:
            changes.append(f"[DELETE] {rel_posix(rules_native, root)}/{old}")

    if skills_src_dir.exists():
        existing = (
            {p.name for p in skills_native.iterdir() if p.is_dir()}
            if skills_native.exists()
            else set()
        )
        canonical = set()
        for item in skills_src_dir.iterdir():
            if not (item.is_dir() and not item.name.startswith(".")):
                continue
            canonical.add(item.name)
            dest = skills_native / item.name
            if not dest.exists():
                changes.append(f"[NEW] {rel_posix(skills_native, root)}/{item.name}/")
        for old in existing - canonical:
            if old == "_retired":
                continue
            changes.append(f"[DELETE] {rel_posix(skills_native, root)}/{old}/")

    if not changes:
        log("[*] No changes detected. All files are in sync.\n")
        return 0

    log("[*] Dry-run changes that would be applied:")
    for change in changes:
        log(f"    {change}")
    log("")
    return len(changes)


def run_status(workspace_path, *, log=print) -> None:
    """Print a status summary of the canonical store."""
    root = Path(workspace_path).resolve()
    bus_dir = root / ".agent-bus"
    manifest_path = bus_dir / "manifest.toml"
    if not manifest_path.exists():
        log(f"[-] Context Bus not initialized in workspace: {root}")
        log("    Run `eos bus import` (or `python scripts/agent_bus.py import`) to initialize.")
        return

    manifest = load_toml(manifest_path)
    log(f"[*] Agent Context Bus Status — {manifest.get('workspace_name', 'Unknown')}")
    log(f"    Last Import:  {manifest.get('last_import', 'Never')}")
    log(f"    Last Ripple:  {manifest.get('last_ripple', 'Never')}")

    sections = manifest.get("sections", {})
    log(f"\n    Sections ({len(sections)}):")
    for key, title in sections.items():
        log(f'      - {key}: "{title}"')

    assembly_keys = [k for k in manifest.keys() if k.startswith("assembly_")]
    log(f"\n    Active Assemblies ({len(assembly_keys)}):")
    for key in assembly_keys:
        ass = manifest[key]
        log(f"      - {ass['boot_file']} ({len(ass['sections'])} sections)")

    rules_src_dir = bus_dir / "rules"
    rules_count = (
        len([p for p in rules_src_dir.iterdir() if p.is_file() and p.suffix == ".md"])
        if rules_src_dir.exists()
        else 0
    )
    log(f"\n    Rules:  {rules_count} in canonical store ({manifest.get('original_rules_dir')})")

    skills_src_dir = bus_dir / "skills"
    skills_count = (
        len([p for p in skills_src_dir.iterdir() if p.is_dir()])
        if skills_src_dir.exists()
        else 0
    )
    log(f"    Skills: {skills_count} in canonical store ({manifest.get('original_skills_dir')})\n")

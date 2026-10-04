"""File-index search providers — instant filename/path discovery, per OS.

The `search` capability's `"files"` domain does near-instant filename/path
lookups via each platform's native file index. Opt-in via
``self.search(q, domain="files")`` — NOT in the default chain, because the
default mode means "files whose *content* matches" (grep) whereas these match
the *name/path*. Different queries, so this lives in its own domain exactly
like Semble's ``"code"`` domain.

One shared base (`IndexedFileSearchProvider`) owns everything that's identical
across platforms — fail-closed root scoping, the content-mode fall-through,
the parse/post-filter/dedupe pipeline, the health shape. Each OS subclass
supplies only its binary name(s), an availability probe, and the argv builder:

| OS      | Backend            | Index        | Install                          |
|---------|--------------------|--------------|----------------------------------|
| Windows | Everything (es.exe)| live (MFT)   | voidtools CLI tools              |
| macOS   | mdfind (Spotlight) | live (OS)    | built-in                         |
| Linux   | fd                 | live (walk)  | `apt install fd-find` (fd/fdfind)|

Three deliberate restraints, shared by all backends:

1. **Filename/path only, never content.** A ``mode="content"`` call raises so
   the capability chain falls through to grep — the right tool for content.
2. **Scoped to allowed_roots, fail-closed.** A whole-machine index (Everything,
   Spotlight) is scoped both at the query level *and* post-filtered against
   ``allowed_roots`` so it can never leak filenames outside the user's
   vault/repo. With no roots the provider reports unavailable.
3. **No network surface.** We never use Everything's HTTP server (it exposes
   every indexed file for download by default). Local CLI/IPC only.

When the backend binary is missing or the OS doesn't match, ``available()``
returns False and the chain falls through to grep — the same graceful-
enhancement pattern as Semble / Playwright.

Selected by platform in setup.py; only the matching provider is registered.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from emptyos.capabilities import Provider


class IndexedFileSearchProvider(Provider):
    """Base for filename/path index search. Subclass per OS.

    Shares the ``GrepSearchProvider.execute`` kwarg shape so callers don't
    branch. Filters the index can't express (``context``) are accepted and
    ignored; ``type`` (extension) and ``glob`` are applied as uniform
    post-filters so semantics are identical across all backends. Always local
    (``is_cloud`` is False via the ``Provider`` default with no ``host``).

    Subclass contract:
        _platforms            tuple of sys.platform prefixes this backend runs on
        _binary_names         tuple of executable names to look for on PATH
        _label                human name for messages
        _platform_reason      health reason when the OS doesn't match
        _missing_binary_reason  health reason when the binary isn't found
        _unreachable_reason   health reason when the binary is present but the
                              index/service isn't reachable (probe-based backends)
        _build_runs(...)      argv list(s) to run for a query
        _probe_reachable(...) optional — defaults to True (binary present = ready)
    """

    name: str = "indexed-file-search"

    # Subclass overrides.
    _platforms: tuple[str, ...] = ()
    _binary_names: tuple[str, ...] = ()
    _label: str = "file index"
    _platform_reason: str = "Unsupported platform for this backend."
    _missing_binary_reason: str = "Search binary not found."
    _unreachable_reason: str = "Search backend not reachable."

    _MAX_RAW = 5000  # hard cap on raw lines read across runs (memory bound)

    def __init__(self, binary_path: str = "", allowed_roots: list[str] | None = None,
                 timeout: float = 10.0):
        self._binary_path_cfg = binary_path or ""
        self._roots: list[Path] = []
        for r in allowed_roots or []:
            if r:
                try:
                    self._roots.append(Path(r).resolve())
                except (OSError, ValueError):
                    continue
        self._timeout = float(timeout)
        self._binary: str | None = None
        self._available_cached: bool | None = None

    # ── platform / binary resolution ──────────────────────────────────────
    def _supported_platform(self) -> bool:
        return any(sys.platform.startswith(p) for p in self._platforms)

    def _resolve_binary(self) -> str | None:
        if self._binary is not None:
            return self._binary or None
        if self._binary_path_cfg and Path(self._binary_path_cfg).is_file():
            self._binary = self._binary_path_cfg
            return self._binary
        for n in self._binary_names:
            found = shutil.which(n)
            if found:
                self._binary = found
                return found
        self._binary = ""  # sentinel: looked, found nothing
        return None

    def _within_roots(self, p: Path) -> bool:
        """True when ``p`` resolves inside one of the allowed roots.

        No roots → False (fail-closed): an unscoped whole-machine index is
        exactly the leak these providers exist to prevent.
        """
        if not self._roots:
            return False
        try:
            rp = p.resolve()
        except (OSError, ValueError):
            return False
        for root in self._roots:
            try:
                rp.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    # ── availability ──────────────────────────────────────────────────────
    async def available(self) -> bool:
        if self._available_cached is not None:
            return self._available_cached
        self._available_cached = await self._probe()
        return self._available_cached

    async def _probe(self) -> bool:
        if not self._supported_platform():
            return False
        if not self._roots:  # fail-closed: never search the whole machine
            return False
        binary = self._resolve_binary()
        if not binary:
            return False
        return await self._probe_reachable(binary)

    async def _probe_reachable(self, binary: str) -> bool:
        """Backend-specific liveness check. Default: binary present = ready.

        Everything overrides this to ping the running service over IPC.
        """
        return True

    async def health(self) -> dict:
        if await self.available():
            return {"available": True, "reason": None, "recovery": None}
        if not self._supported_platform():
            reason = self._platform_reason
        elif not self._roots:
            reason = "No allowed_roots configured — refusing to search machine-wide."
        elif not self._resolve_binary():
            reason = self._missing_binary_reason
        else:
            reason = self._unreachable_reason
        return {
            "available": False,
            "reason": reason,
            "recovery": {
                "kind": "config",
                "path": "emptyos.toml",
                "section": "[capabilities.search.files]",
            },
        }

    # ── execution ─────────────────────────────────────────────────────────
    def _build_runs(
        self, binary: str, query: str, scope_roots: list[Path],
        case_insensitive: bool, fetch_limit: int,
    ) -> list[list[str]]:
        """Return one or more argv lists to run. Each must print one path/line."""
        raise NotImplementedError

    @staticmethod
    def _passes_glob(p: Path, glob: str) -> bool:
        try:
            return bool(p.match(glob))
        except ValueError:
            return False

    async def execute(
        self,
        *,
        query: str,
        path: str = "",
        mode: str = "files_with_matches",
        case_insensitive: bool = True,
        glob: str = "",
        type: str = "",
        context: int = 0,  # accepted, ignored (no content lines)
        limit: int = 200,
        **kwargs: Any,
    ) -> list[dict]:
        # Validate inputs before shelling out, so a bad-mode / out-of-scope call
        # raises the *right* error (→ falls through to grep) even when the
        # backend binary isn't installed.

        # These indexes match paths, not content — content/regex is grep's job.
        if mode == "content":
            raise RuntimeError(f"{self.name}: content search unsupported (use grep)")

        q = (query or "").strip()
        if not q:
            raise RuntimeError(f"{self.name}: empty query")

        # Flag-injection guard: a query starting with "-" could be smuggled into
        # a backend's argv as an option — fd's --exec/-x runs a command per match
        # (RCE), mdfind's -live spins forever (DoS). All three backends do
        # substring matching, so a leading "-" is droppable, not a lost capability
        # (search "foo", not "-foo", still finds "-foo.txt"). fd also gets a `--`
        # separator below as defense in depth.
        if q.startswith("-"):
            raise RuntimeError(f"{self.name}: query may not start with '-' (flag-injection guard)")

        # Resolve scope. An explicit path kwarg must sit inside the allowed roots
        # (raise → fall through to grep, mirroring Semble's out-of-scope guard).
        if path:
            target = Path(path).resolve()
            if not self._within_roots(target):
                raise RuntimeError(
                    f"{self.name}: path {target} is outside allowed roots {self._roots}"
                )
            scope_roots = [target]
        else:
            scope_roots = list(self._roots)
        if not scope_roots:
            raise RuntimeError(f"{self.name}: no scope roots (refusing machine-wide search)")

        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(f"{self.name}: {self._label} binary not available")

        limit = max(1, int(limit))
        # type/glob are uniform post-filters, so when they're active we overfetch
        # from the index and truncate after — otherwise a post-filter could drop
        # results below `limit` that the engine cap already excluded.
        has_postfilter = bool(type or glob)
        fetch_limit = limit if not has_postfilter else min(limit * 20, 2000)

        runs = self._build_runs(binary, q, scope_roots, case_insensitive, fetch_limit)

        raw: list[str] = []
        for argv in runs:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except (asyncio.TimeoutError, OSError) as e:
                raise RuntimeError(f"{self.name}: {self._label} call failed ({e})") from e
            raw.extend(stdout.decode(errors="replace").splitlines())
            if len(raw) >= self._MAX_RAW:
                break

        return self._postfilter(raw, type=type, glob=glob, limit=limit)

    def _postfilter(self, raw: list[str], *, type: str, glob: str, limit: int) -> list[dict]:
        """Parse raw path lines → scoped, type/glob-filtered, deduped, capped.

        Pure given ``self._roots`` — the precise scope enforcement (engine-level
        scoping is coarse), extension + glob filtering, dedup, and final cap.
        """
        type_ext = ("." + type.lstrip(".")).lower() if type else ""
        out: list[dict] = []
        seen: set[str] = set()
        for line in raw:
            line = line.strip()
            if not line:
                continue
            p = Path(line)
            if not self._within_roots(p):  # precise scope enforcement
                continue
            if type_ext and p.suffix.lower() != type_ext:
                continue
            if glob and not self._passes_glob(p, glob):
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            out.append({"path": key})
            if len(out) >= limit:
                break
        return out


class EverythingSearchProvider(IndexedFileSearchProvider):
    """Windows — voidtools Everything via `es.exe` (live NTFS/MFT index)."""

    name = "everything"
    _label = "Everything"
    _platforms = ("win32",)
    _binary_names = ("es", "es.exe")
    _platform_reason = "Everything is Windows-only (es.exe + the Everything service)."
    _missing_binary_reason = (
        "es.exe not found — install Everything's command-line tools from voidtools."
    )
    _unreachable_reason = "Everything service not reachable — is Everything running?"

    async def _probe_reachable(self, binary: str) -> bool:
        # Query the *running* Everything instance over IPC; a non-zero exit
        # means it isn't indexed/reachable right now.
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "-get-everything-version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return proc.returncode == 0
        except (asyncio.TimeoutError, OSError):
            return False

    def _build_runs(self, binary, query, scope_roots, case_insensitive, fetch_limit):
        # es.exe scopes with the `-path <dir>` flag (one dir each), so one run per
        # root — the base merges them. The `path:` query modifier / `<..>` grouping
        # silently returns nothing against a real es.exe, so `-path` is the only
        # reliable form. The base's _within_roots post-filter stays the precise
        # backstop. The query is a trailing positional; the execute() flag-injection
        # guard keeps a "-"-prefixed query from being parsed as an option.
        runs = []
        for r in scope_roots:
            cmd = [binary, "-n", str(fetch_limit), "-path", str(r)]
            if not case_insensitive:
                cmd.append("-case")  # es default is case-insensitive; -case = match case
            cmd.append(query)
            runs.append(cmd)
        return runs


class MdfindSearchProvider(IndexedFileSearchProvider):
    """macOS — `mdfind` (Spotlight, live OS-maintained index). Built-in."""

    name = "mdfind"
    _label = "mdfind"
    _platforms = ("darwin",)
    _binary_names = ("mdfind",)
    _platform_reason = "mdfind/Spotlight is macOS-only."
    _missing_binary_reason = "mdfind not found (it ships with macOS)."
    _unreachable_reason = "Spotlight not reachable."

    def _build_runs(self, binary, query, scope_roots, case_insensitive, fetch_limit):
        # `-onlyin` takes a single dir, so one run per root; the base merges them.
        # Spotlight is inherently case-insensitive, so case_insensitive is ignored.
        # No `-n` flag — the base caps via its _MAX_RAW read guard + truncation.
        # The query can't start with "-" (base execute() flag-injection guard), so
        # it can't be smuggled past `-name` as an mdfind option — no `--` needed
        # here (mdfind's `--` support is undocumented; relying on the base guard).
        return [[binary, "-onlyin", str(r), "-name", query] for r in scope_roots]


class FdSearchProvider(IndexedFileSearchProvider):
    """Linux — `fd` (live filesystem walk, always fresh). `apt install fd-find`.

    Debian/Ubuntu install the binary as ``fdfind`` (the ``fd`` name collides
    with another package), so both names are probed. Configured for coverage
    parity with the whole-machine indexers: ``--hidden --no-ignore`` so it
    surfaces the same files Everything/Spotlight would, rather than respecting
    ``.gitignore`` (which those backends don't know about).
    """

    name = "fd"
    _label = "fd"
    _platforms = ("linux",)
    _binary_names = ("fd", "fdfind")
    _platform_reason = "the fd backend is used on Linux here."
    _missing_binary_reason = "fd not found — install it (apt: fd-find → 'fdfind')."
    _unreachable_reason = "fd not runnable."

    def _build_runs(self, binary, query, scope_roots, case_insensitive, fetch_limit):
        cmd = [
            binary,
            "--max-results", str(fetch_limit),
            "--color", "never",
            "--fixed-strings",   # literal substring, not regex
            "--hidden",          # match Spotlight/Everything coverage
            "--no-ignore",       # don't honor .gitignore (the indexers don't either)
        ]
        cmd.append("--ignore-case" if case_insensitive else "--case-sensitive")
        cmd.append("--")  # stop option parsing — pattern + roots are positional
        cmd.append(query)
        cmd += [str(r) for r in scope_roots]  # fd accepts multiple search paths
        return [cmd]

#!/usr/bin/env python
"""Static check on `[[provides.verbs]] args` declarations.

The `args` table on a verb entry was prompt decoration for most of its life —
rendered into the model's tool list, validated on the voice surface only (see
`.claude/rules/verb-registry.md`). Now that `[verbs] arg_gate` can make it a
real contract at dispatch, a wrong declaration stops being a documentation nit
and starts being able to reject a working call. This scanner catches the two
ways a declaration goes wrong, before anyone turns the flag on.

Two findings, both **advisory** — this never gates:

  bad-type   an `args` value outside {string, number, boolean} (+ `?` suffix).
             `validate_args` silently skips a token it doesn't recognise, so
             `tags = "list[str]"` reads as a declared constraint and enforces
             nothing at all. Deliberately not a runtime failure (a typo in a
             manifest must not break a shipped verb) — which is exactly why it
             needs to be caught statically instead.

  no-param   a declared arg name the target method's signature can't accept.
             Under the gate the model is told an arg exists, sends it, and
             `fn(**kwargs)` raises TypeError. Skipped entirely when the method
             takes `**kwargs`.

Signatures are read with `ast`, never by importing the app — importing boots
kernel machinery (`.claude/rules/daemon-handling.md`). Methods are searched
across every `*.py` in the app dir because multi-module apps define them in
helpers and re-bind in `app.py`.

The canonical `method` is the one checked. Per-surface overrides
(`voice.method`) wrap it and legitimately differ, so checking those too would
report the wrapper's `(self, **kwargs)` shape as a mismatch.

    python scripts/check_verb_args.py [--json] [--app <id>]
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from scanner_lib import emit_json  # noqa: E402

VALID_TYPES = frozenset({"string", "number", "boolean"})
# Opt-out at the declaration site, not a central allowlist — a new legitimate
# case shouldn't need an edit here (`.claude/rules/audits.md`).
IGNORE_MARKER = "verb-args-check: ignore"


def _app_dirs() -> list[Path]:
    apps = REPO / "apps"
    return [p.parent for p in apps.rglob("manifest.toml") if "_retired" not in p.parts]


def _params_by_method(app_dir: Path) -> dict[str, tuple[set[str], bool]]:
    """{method_name: (param_names, accepts_kwargs)} across the app's modules.

    Last definition wins on a name collision, which is fine — we only use this
    to answer "could this name possibly be accepted", and a false *negative*
    (staying quiet) is the right failure direction for an advisory check.
    """
    out: dict[str, tuple[set[str], bool]] = {}
    for py in app_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            a = node.args
            names = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
            out[node.name] = (names - {"self"}, a.kwarg is not None)
    return out


def scan(only_app: str | None = None) -> list[dict]:
    findings: list[dict] = []
    for app_dir in _app_dirs():
        mf = app_dir / "manifest.toml"
        try:
            raw_text = mf.read_text(encoding="utf-8", errors="replace")
            data = tomllib.loads(raw_text)
        except Exception:
            continue
        verbs = (data.get("provides") or {}).get("verbs") or []
        if not verbs:
            continue
        app_id = (data.get("app") or {}).get("id") or app_dir.name
        if only_app and app_id != only_app:
            continue
        if IGNORE_MARKER in raw_text:
            continue

        params: dict[str, tuple[set[str], bool]] | None = None
        for entry in verbs:
            if not isinstance(entry, dict):
                continue
            verb = entry.get("verb") or "?"
            args = entry.get("args")
            if not isinstance(args, dict) or not args:
                continue

            for key, spec in args.items():
                if not isinstance(spec, str):
                    findings.append({
                        "app": app_id, "verb": verb, "kind": "bad-type", "arg": key,
                        "detail": f"type must be a string, got {type(spec).__name__}",
                    })
                    continue
                base = spec.rstrip("?")
                if base not in VALID_TYPES:
                    findings.append({
                        "app": app_id, "verb": verb, "kind": "bad-type", "arg": key,
                        "detail": f"unknown type {spec!r} — enforces nothing; "
                                  f"use one of {', '.join(sorted(VALID_TYPES))}",
                    })

            method = entry.get("method")
            if not method:
                continue
            if params is None:
                params = _params_by_method(app_dir)
            sig = params.get(method)
            if sig is None:
                continue  # method-existence is the loader's job, not ours
            names, accepts_kwargs = sig
            if accepts_kwargs:
                continue
            for key in args:
                if key not in names:
                    findings.append({
                        "app": app_id, "verb": verb, "kind": "no-param", "arg": key,
                        "detail": f"{method}() has no parameter {key!r} "
                                  f"(accepts: {', '.join(sorted(names)) or 'none'})",
                    })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--app", default=None, help="scan a single app id")
    a = ap.parse_args()

    findings = scan(a.app)
    # Exit = finding count (capped), matching the advisory-scanner convention:
    # registered `gate: False`, so preflight renders non-zero as *warn*, not
    # fail. Returning 0 unconditionally would render "ok" and hide the findings
    # entirely — which is the whole point of running it.
    code = min(len(findings), 99)

    if a.json:
        emit_json(
            not findings, "verb_args",
            f"{len(findings)} verb-args finding(s)", {"findings": findings},
        )
        return code

    if not findings:
        print("verb-args: clean")
        return 0
    print(f"verb-args: {len(findings)} advisory finding(s)\n")
    for f in findings:
        print(f"  [{f['kind']}] {f['verb']} · {f['arg']}\n      {f['detail']}")
    print("\nAdvisory only — does not gate. Silence one manifest with an inline")
    print(f"`# {IGNORE_MARKER}` comment.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

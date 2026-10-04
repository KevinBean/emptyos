#!/usr/bin/env python3
"""Audit third-party Python dependencies against an allowed-license list.

Runs three checks against the declared dependency set:

1. **License classification** — every dep's installed `License` /
   `License-Expression` metadata is classified into one of three buckets:
       - allowed (MIT, BSD, Apache 2.0, ISC, MPL, PSF, Unlicense, CC0, ...)
       - warn    (LGPL — dynamic linking is fine but flag it)
       - forbidden (GPL, AGPL, SSPL — viral / incompatible with permissive shipping)
   Forbidden hits fail the check; warns surface but pass.

2. **LICENSES.md coverage** — every declared dep should appear in the top-
   level LICENSES.md so attribution clauses (Apache 2.0 §4(c), BSD-3) are
   satisfied for any public distribution.

3. **Unused-declaration sweep** — best-effort check that every dep in
   requirements.txt / pyproject.toml is actually imported somewhere in the
   tracked tree. Catches stale pins like `g2p_en` (Python-3.13 incompatible
   and not actually loaded) and `phonemizer` (installed for the wav2vec2
   tokenizer but bypassed once we read vocab.json directly).

Sources audited:
- `pyproject.toml` `[project] dependencies` + every
  `[project.optional-dependencies]` group
- Every `**/requirements*.txt` file under the repo

Usage:
    python scripts/check-licenses.py              # full audit
    python scripts/check-licenses.py --staged     # check only staged files
    python scripts/check-licenses.py --strict     # fail on warn-level too

This script is best-effort. It only inspects deps *installed in the current
Python environment* — if a dep is declared but not pip-installed locally,
its license can't be checked and it's reported as "unknown". A clean public
release should be cut from an env where every declared dep is installed.
"""

from __future__ import annotations

import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from check_base import REPO_ROOT

# ── License classification ──────────────────────────────────────────────────

# Permissive families — fine to ship in any product (commercial or otherwise),
# as long as attribution is preserved in LICENSES.md.
ALLOWED_LICENSE_PATTERNS = [
    re.compile(r"\bMIT\b", re.I),
    re.compile(r"\bBSD\b", re.I),
    re.compile(r"\bApache\b", re.I),  # Apache 1.0/1.1/2.0 all permissive
    re.compile(r"\bASL\b", re.I),
    re.compile(r"\bISC\b", re.I),
    re.compile(r"\bMPL[ -]?(?:Version\s*)?2", re.I),
    re.compile(r"\bMozilla\s*Public\s*License[ -]?2", re.I),
    re.compile(r"\bPSF\b", re.I),
    re.compile(r"\bPython\s*Software\s*Foundation", re.I),
    re.compile(r"\bUnlicense\b", re.I),
    re.compile(r"\bCC0\b", re.I),
    re.compile(r"\bPublic\s*Domain\b", re.I),
    re.compile(r"\bHPND\b", re.I),  # historical-permission-notice-and-disclaimer (Pillow)
    re.compile(r"\bZPL\b", re.I),    # Zope Public License (zope.* deps)
    re.compile(r"\bBlue\s*Oak\b", re.I),
]

# Weak-copyleft — OK with dynamic linking, but worth flagging so we
# notice before shipping a static build / single-binary product.
WARN_LICENSE_PATTERNS = [
    re.compile(r"\bLGPL\b", re.I),
    re.compile(r"\bLesser\s*GPL\b", re.I),
]

# Viral / network-copyleft — forbidden for our public-release intent.
# If a dep matches one of these, either drop it or move to plugin-only
# integration where the user provides their own install.
#
# `\bGPL` works because `\b` doesn't match between two word chars — so
# AGPL/LGPL don't trigger this pattern (they have their own above).
FORBIDDEN_LICENSE_PATTERNS = [
    re.compile(r"\bAGPL\b", re.I),
    re.compile(r"\bAffero\b", re.I),
    re.compile(r"\bSSPL\b", re.I),
    re.compile(r"\bGPL(?:[ -]?v?\d|\b)", re.I),  # GPL, GPL-3.0, GPLv2, GPL 3, etc.
    re.compile(r"\bGNU\s*General\s*Public", re.I),
]

# Known-bad classifier strings on PyPI that pre-empt the regexes above.
KNOWN_FORBIDDEN_CLASSIFIERS = {
    "License :: OSI Approved :: GNU Affero General Public License v3",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
    "License :: OSI Approved :: GNU General Public License (GPL)",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
}

# Hand-curated overrides for deps whose pip metadata is incomplete or
# misleading (license is set to "UNKNOWN" but the LICENSE file in the repo
# is clear). Only add entries here after manually verifying the upstream
# repo's actual license file.
LICENSE_OVERRIDES: dict[str, str] = {
    "soundfile": "BSD-3-Clause",
    "cmudict": "GPL-3.0",  # cmudict the package is GPL-3.0; the CMU dictionary DATA is public-domain. See note in LICENSES.md.
    "librosa": "ISC",
    "edge-tts": "GPL-3.0",  # edge-tts python wrapper is GPL-3.0
    "kokoro-onnx": "MIT",
    "faster-whisper": "MIT",
}

# Module-name → distribution-name aliases for cases where the import name
# differs from the pip package name. Used in the unused-declaration check.
IMPORT_ALIASES = {
    "python-multipart": "multipart",
    "pyyaml": "yaml",
    "python-docx": "docx",
    "opencv-python": "cv2",
    "faiss-cpu": "faiss",
    "huggingface_hub": "huggingface_hub",
    "edge-tts": "edge_tts",
    "kokoro-onnx": "kokoro_onnx",
    "faster-whisper": "faster_whisper",
    "g2p_en": "g2p_en",
    "uvicorn[standard]": "uvicorn",
    "pyzmq": "zmq",
    "prompt_toolkit": "prompt_toolkit",
    "firecrawl-anydoc": "anydoc",
}

# Deps that are tools / runtime plugins / transitive consumers — they don't
# appear in tracked `import` statements but are genuinely needed at runtime.
# Skipping the unused-imports check for these keeps the report signal-high.
IMPORT_CHECK_EXEMPT = {
    # CLI tools, no import in app code
    "ruff",
    # pytest plugin system: discovered as entry-points, not imported
    "pytest-asyncio",
    "pytest-playwright",
    "pytest-rerunfailures",
    "pytest-timeout",
    # FastAPI uses python-multipart internally for form/file uploads;
    # apps never import it directly
    "python-multipart",
    # uvicorn serves /ws only when a WebSocket library is importable; it loads
    # it itself, so nothing here imports it directly
    "websockets",
    # pyinstaller / build tooling
    "pyinstaller",
    # transitive-but-pinned deps that surface only via downstream libs
    "watchfiles",
}


# ── Helpers ─────────────────────────────────────────────────────────────────


def normalize_dep(spec: str) -> str:
    """Strip version pins and extras: `fastapi>=0.115` → `fastapi`."""
    spec = spec.strip()
    # Drop everything from the first version-comparison operator on
    for op in (">=", "<=", "==", "~=", "!=", ">", "<", ";"):
        idx = spec.find(op)
        if idx >= 0:
            spec = spec[:idx]
    # Strip [extras]
    if "[" in spec:
        spec = spec[: spec.index("[")]
    return spec.strip()


def parse_pyproject() -> dict[str, list[str]]:
    """Returns {group_name: [dep, ...]} including the main + optional groups."""
    path = REPO_ROOT / "pyproject.toml"
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    out: dict[str, list[str]] = {}
    out["[main]"] = [normalize_dep(d) for d in data.get("project", {}).get("dependencies", [])]
    for group, deps in (data.get("project", {}).get("optional-dependencies") or {}).items():
        out[f"[optional:{group}]"] = [normalize_dep(d) for d in deps]
    return out


def parse_requirements_files() -> dict[str, list[str]]:
    """Returns {rel_path: [dep, ...]} for every requirements.txt under the repo."""
    out: dict[str, list[str]] = {}
    for path in REPO_ROOT.rglob("requirements*.txt"):
        # Skip anything in gitignored paths (data/, node_modules)
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("data/", "node_modules/", ".venv/", "venv/")):
            continue
        deps: list[str] = []
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith(("-r", "-e", "--")):
                continue
            deps.append(normalize_dep(line))
        if deps:
            out[rel] = deps
    return out


def get_license(name: str) -> tuple[str, str]:
    """Return (license_text, source) for an installed distribution.

    Resolution order (best signal first):
      1. Hand-curated override
      2. `License-Expression` (PEP 639, SPDX short form)
      3. `License` field IF short (single line, < 100 chars — i.e. an SPDX
         id, not a verbatim license file dump)
      4. `License` OSI classifier
      5. First line of a long `License` field (last resort)

    numpy/scipy/etc. dump the full BSD-3 text into `License`, which would
    confuse any regex classifier. Steps 3–5 keep the matcher honest by
    preferring short-form metadata when available.
    """
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name], "override"
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        for alt in (name.lower(), name.replace("-", "_"), name.replace("_", "-")):
            try:
                dist = metadata.distribution(alt)
                break
            except metadata.PackageNotFoundError:
                continue
        else:
            return "", "not-installed"
    md = dist.metadata

    le = (md.get("License-Expression") or "").strip()
    if le and le.upper() != "UNKNOWN":
        return le, "License-Expression"

    lic = (md.get("License") or "").strip()
    if lic and lic.upper() != "UNKNOWN" and "\n" not in lic and len(lic) < 100:
        return lic, "License"

    classifiers = md.get_all("Classifier") or []
    license_classifiers = [c for c in classifiers if c.startswith("License ::")]
    if license_classifiers:
        # Prefer the most specific OSI classifier (usually the longest)
        return max(license_classifiers, key=len), "Classifier"

    if lic and lic.upper() != "UNKNOWN":
        # Long license text — fall back to first line for classification
        first_line = lic.split("\n", 1)[0].strip()
        return first_line, "License-firstline"

    return "", "unknown"


def classify(license_text: str) -> str:
    """Return 'allowed' / 'warn' / 'forbidden' / 'unknown'."""
    if not license_text:
        return "unknown"
    if license_text in KNOWN_FORBIDDEN_CLASSIFIERS:
        return "forbidden"
    for pat in FORBIDDEN_LICENSE_PATTERNS:
        if pat.search(license_text):
            return "forbidden"
    for pat in WARN_LICENSE_PATTERNS:
        if pat.search(license_text):
            return "warn"
    for pat in ALLOWED_LICENSE_PATTERNS:
        if pat.search(license_text):
            return "allowed"
    return "unknown"


def read_licenses_md_listed() -> set[str]:
    """Parse LICENSES.md for dep names actually documented there.

    Looks for lines starting with `|` (markdown table rows) and grabs the
    first cell's content as the dep name. Tolerant of header/separator rows."""
    path = REPO_ROOT / "LICENSES.md"
    if not path.exists():
        return set()
    listed: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s.startswith("|") or set(s) <= {"|", "-", ":", " "}:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        # Strip markdown link syntax: `[name](url)` → `name`
        m = re.match(r"\[([^\]]+)\]\([^\)]+\)", first)
        if m:
            first = m.group(1)
        # Strip backticks `name`
        first = first.strip("` ")
        # Skip the header row literal
        if first.lower() in ("package", "dependency", "name"):
            continue
        listed.add(first.lower())
    return listed


def scan_imports_for_dep(dep: str) -> bool:
    """Return True if `dep` (or its import alias) appears in any tracked
    Python file. Best-effort — substring match against import statements."""
    target = IMPORT_ALIASES.get(dep, dep).replace("-", "_")
    pattern = re.compile(rf"^\s*(?:from|import)\s+{re.escape(target)}\b", re.MULTILINE)
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("data/", ".venv/", "venv/", "node_modules/", "scripts/")):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(content):
            return True
    return False


# ── Report ─────────────────────────────────────────────────────────────────


def main() -> int:
    strict = "--strict" in sys.argv
    # --staged is accepted for parity with sibling scripts but doesn't change
    # behaviour here (license audit operates on the env, not staged files).
    _staged = "--staged" in sys.argv

    pyproject = parse_pyproject()
    reqs = parse_requirements_files()

    # Build the union of all declared deps, tracking each declaration site.
    all_deps: dict[str, list[str]] = {}
    for group, deps in pyproject.items():
        for d in deps:
            all_deps.setdefault(d, []).append(f"pyproject:{group}")
    for path, deps in reqs.items():
        for d in deps:
            all_deps.setdefault(d, []).append(path)

    if not all_deps:
        print("No dependencies declared. Nothing to check.")
        return 0

    # Cache: read LICENSES.md once
    listed = read_licenses_md_listed()

    forbidden: list[tuple[str, str, list[str]]] = []  # (dep, license, sources)
    warn: list[tuple[str, str, list[str]]] = []
    unknown: list[tuple[str, str, list[str]]] = []
    not_installed: list[tuple[str, list[str]]] = []
    missing_attribution: list[tuple[str, str]] = []
    unused: list[tuple[str, list[str]]] = []

    for dep in sorted(all_deps):
        sources = sorted(set(all_deps[dep]))
        lic, source = get_license(dep)
        if source == "not-installed":
            not_installed.append((dep, sources))
            continue
        bucket = classify(lic)
        if bucket == "forbidden":
            forbidden.append((dep, lic, sources))
        elif bucket == "warn":
            warn.append((dep, lic, sources))
        elif bucket == "unknown":
            unknown.append((dep, lic, sources))

        if dep.lower() not in listed:
            missing_attribution.append((dep, lic))

        if dep not in IMPORT_CHECK_EXEMPT and not scan_imports_for_dep(dep):
            unused.append((dep, sources))

    sep = "=" * 60
    summary_lines = []
    summary_lines.append(f"Audited {len(all_deps)} declared dependencies")
    summary_lines.append(
        f"  forbidden: {len(forbidden)}  warn: {len(warn)}  "
        f"unknown: {len(unknown)}  not-installed: {len(not_installed)}"
    )
    summary_lines.append(
        f"  missing-from-LICENSES.md: {len(missing_attribution)}  unused: {len(unused)}"
    )

    for line in summary_lines:
        print(line)

    if forbidden:
        print(f"\n{sep}\n  FORBIDDEN LICENSES ({len(forbidden)})\n{sep}")
        for dep, lic, sources in forbidden:
            print(f"  {dep:30s} {lic[:80]}")
            for s in sources:
                print(f"    ->{s}")
        print(
            "\nGPL/AGPL/SSPL deps are incompatible with permissive shipping.\n"
            "Drop them, replace with a permissive alternative, or move to a\n"
            "user-installed plugin path."
        )

    if warn:
        print(f"\n{sep}\n  WEAK-COPYLEFT WARNINGS ({len(warn)})\n{sep}")
        for dep, lic, sources in warn:
            print(f"  {dep:30s} {lic[:80]}")
            for s in sources:
                print(f"    ->{s}")
        print("\nLGPL is fine for dynamic linking. Flag if you ever ship a static build.")

    if unknown:
        print(f"\n{sep}\n  UNKNOWN LICENSES ({len(unknown)})\n{sep}")
        for dep, lic, sources in unknown:
            print(f"  {dep:30s} {(lic or '(no metadata)')[:80]}")
            for s in sources:
                print(f"    ->{s}")
        print(
            "\nAdd an entry to LICENSE_OVERRIDES in this script after\n"
            "verifying the upstream repo's LICENSE file."
        )

    if not_installed:
        print(f"\n{sep}\n  NOT INSTALLED IN THIS ENV ({len(not_installed)})\n{sep}")
        for dep, sources in not_installed:
            print(f"  {dep}")
            for s in sources:
                print(f"    ->{s}")
        print(
            "\nInstall every declared dep before cutting a public release so\n"
            "the license audit can verify them."
        )

    if missing_attribution:
        print(f"\n{sep}\n  MISSING FROM LICENSES.md ({len(missing_attribution)})\n{sep}")
        for dep, lic in missing_attribution:
            print(f"  {dep:30s} ({(lic or 'unknown license')[:60]})")
        print(
            "\nAdd these to LICENSES.md so attribution clauses are satisfied\n"
            "before public distribution."
        )

    if unused:
        print(f"\n{sep}\n  POSSIBLY UNUSED ({len(unused)})\n{sep}")
        for dep, sources in unused:
            print(f"  {dep}")
            for s in sources:
                print(f"    ->{s}")
        print(
            "\nNo `import {dep}` found in tracked .py files. Either drop the\n"
            "dep from the source above, or add it to IMPORT_ALIASES in this\n"
            "script if the import name differs from the package name."
        )

    # Exit code: fail on forbidden; fail on warn only when --strict.
    if forbidden:
        return 1
    if strict and (warn or unknown or missing_attribution):
        return 1
    if not (forbidden or warn or unknown or missing_attribution or unused):
        print("\nOK: All declared dependencies pass the license check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

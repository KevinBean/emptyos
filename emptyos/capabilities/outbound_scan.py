"""Local outbound-data scanner.

Runs before a cloud provider call. Flags likely secrets and personal data
in the text that will leave the machine. Does NOT block — the consent UI
surfaces findings so the user can decide whether to proceed.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import NamedTuple

# High-confidence secret patterns. Conservative on purpose — fixed prefixes
# / well-defined formats so false positives are rare.
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("OpenAI API key", re.compile(r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("Slack token", re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Bearer token (header)", re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._-]{20,}")),
]


class Finding(NamedTuple):
    pattern_name: str
    preview: str  # redacted short preview of the match


_CACHED_PERSONAL: list[tuple[str, re.Pattern]] | None = None


def _load_personal_patterns(repo_root: Path) -> list[tuple[str, re.Pattern]]:
    patterns: list[tuple[str, re.Pattern]] = []
    f = repo_root / ".eos-personal"
    if not f.exists():
        return patterns
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(("Personal data (.eos-personal)", re.compile(line)))
        except re.error:
            continue
    return patterns


def _personal_patterns() -> list[tuple[str, re.Pattern]]:
    global _CACHED_PERSONAL
    if _CACHED_PERSONAL is None:
        here = Path(__file__).resolve()
        found: list[tuple[str, re.Pattern]] = []
        for parent in here.parents:
            if (parent / ".eos-personal").exists():
                found = _load_personal_patterns(parent)
                break
        _CACHED_PERSONAL = found
    return _CACHED_PERSONAL


def _redact(match: str) -> str:
    if len(match) <= 16:
        return "*" * len(match)
    return match[:4] + "*" * (len(match) - 8) + match[-4:]


def scan_outbound(text: str) -> list[Finding]:
    """Scan a string about to leave the machine. Returns list of findings.

    Each finding has a pattern name and a short redacted preview so the UI
    can show what was detected without re-exposing the secret in full.
    """
    if not text:
        return []
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for name, pat in _SECRET_PATTERNS + _personal_patterns():
        for m in pat.finditer(text):
            matched = m.group(0)
            preview = _redact(matched)
            key = (name, preview)
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(name, preview))
    return findings


# --- Local-LLM semantic scan ------------------------------------------------
#
# A second layer above the deterministic regex pass. Uses whatever local
# provider is already in the `think` chain (Ollama by default) to classify
# or rewrite the outbound text. Off by default — gated by:
#   cloud.llm_scan.mode     off | classify | redact
#   cloud.llm_scan.on_flag  warn | block
#   cloud.llm_scan.provider optional variant_id (default: first local)
#   cloud.llm_scan.max_chars text cap before scanning (default: 4000)

_CLASSIFY_SYSTEM = (
    "You are a privacy classifier. Examine the user's text and decide whether "
    "it contains personal, sensitive, or confidential content that the user "
    "might not want to send to a cloud LLM. Examples of sensitive content: "
    "full names, home addresses, phone numbers, email addresses, medical or "
    "therapy details, financial balances, private relationship details, "
    "login credentials, third-party personal data. Public knowledge, code, "
    "and general questions are NOT sensitive. Respond in exactly this format "
    "and nothing else:\nFLAG: yes|no\nREASONS: <comma-separated short reasons "
    "if yes; empty if no>"
)

_REDACT_SYSTEM = (
    "You are a privacy redactor. Rewrite the user's text so that personal, "
    "sensitive, or confidential spans are replaced with bracketed placeholders "
    "like [NAME], [ADDRESS], [PHONE], [EMAIL], [AMOUNT], [DATE], [PERSON]. "
    "Preserve the structure, intent, and non-sensitive wording exactly. Do "
    "NOT add commentary, explanations, or quotes. Output only the redacted "
    "text, nothing else."
)


def _pick_local_provider(kernel, preferred_variant: str = ""):
    """Return the first available local (non-cloud, non-human) think provider.

    Honors `preferred_variant` if it matches; otherwise picks the first local
    provider in chain order. Returns None if no local provider is loaded.
    """
    try:
        think = kernel.capabilities.get("think")
    except Exception:
        return None
    local = [p for p in think.providers if not getattr(p, "is_cloud", False) and p.name != "human"]
    if not local:
        return None
    if preferred_variant:
        for p in local:
            if p.variant_id == preferred_variant:
                return p
    return local[0]


def _parse_classify(raw: str) -> tuple[bool, str]:
    """Parse 'FLAG: yes|no\\nREASONS: ...' output. Best-effort."""
    flagged = False
    reasons = ""
    for line in (raw or "").splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("flag:"):
            val = s.split(":", 1)[1].strip().lower()
            flagged = val.startswith("yes") or val.startswith("y")
        elif low.startswith("reasons:"):
            reasons = s.split(":", 1)[1].strip()
    return flagged, reasons


async def llm_classify(
    text: str, kernel, *, max_chars: int = 4000, preferred_variant: str = "", timeout: float = 5.0
) -> dict:
    """Run the local classifier. Never raises — returns a neutral dict on failure.

    `timeout` caps how long the scan may take. Exceeding it returns
    `ran=False` with a `reasons="timeout"` note so the caller knows the scan
    didn't actually run (rather than silently treating it as "not sensitive").
    """
    if not text or not kernel:
        return {"ran": False, "flagged": False, "reasons": "", "provider": ""}
    provider = _pick_local_provider(kernel, preferred_variant)
    if provider is None:
        return {"ran": False, "flagged": False, "reasons": "no local provider", "provider": ""}
    try:
        if not await provider.available():
            return {
                "ran": False,
                "flagged": False,
                "reasons": "local provider offline",
                "provider": provider.variant_id,
            }
        sample = text[:max_chars]
        raw = await asyncio.wait_for(
            provider.execute(prompt=sample, system=_CLASSIFY_SYSTEM, temperature=0.1),
            timeout=timeout,
        )
        flagged, reasons = _parse_classify(str(raw))
        return {
            "ran": True,
            "flagged": flagged,
            "reasons": reasons,
            "provider": provider.variant_id,
        }
    except TimeoutError:
        return {
            "ran": False,
            "flagged": False,
            "reasons": f"timeout after {timeout:g}s",
            "provider": provider.variant_id,
        }
    except Exception as e:
        return {
            "ran": False,
            "flagged": False,
            "reasons": f"error: {e}",
            "provider": provider.variant_id,
        }


async def llm_redact(
    text: str, kernel, *, max_chars: int = 4000, preferred_variant: str = "", timeout: float = 5.0
) -> dict:
    """Run the local redactor. Returns {'ran', 'redacted', 'provider'}.

    `redacted` is the rewritten text, or the original text if redaction
    was skipped, timed out, or failed. `timeout` caps how long the rewrite
    may take so a slow model can't starve the downstream cloud call.
    """
    if not text or not kernel:
        return {"ran": False, "redacted": text, "provider": ""}
    provider = _pick_local_provider(kernel, preferred_variant)
    if provider is None:
        return {"ran": False, "redacted": text, "provider": ""}
    try:
        if not await provider.available():
            return {"ran": False, "redacted": text, "provider": provider.variant_id}
        # If text exceeds budget, only redact the prefix and keep the tail.
        # This avoids unbounded local-LLM latency on very long prompts.
        if len(text) > max_chars:
            head = text[:max_chars]
            tail = text[max_chars:]
        else:
            head, tail = text, ""
        raw = await asyncio.wait_for(
            provider.execute(prompt=head, system=_REDACT_SYSTEM, temperature=0.2),
            timeout=timeout,
        )
        rewritten = str(raw or "").strip()
        if not rewritten:
            return {"ran": False, "redacted": text, "provider": provider.variant_id}
        return {"ran": True, "redacted": rewritten + tail, "provider": provider.variant_id}
    except TimeoutError:
        # Returning the original text means the cloud provider still sees
        # unredacted content. That matches the "warn" principle — fail open
        # on latency. Users who need strict privacy should pair redact with
        # on_flag=block (the classifier still runs in classify mode upstream).
        return {"ran": False, "redacted": text, "provider": provider.variant_id}
    except Exception:
        return {"ran": False, "redacted": text, "provider": ""}

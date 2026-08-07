#!/usr/bin/env python3
"""Recompute the LIVE remaining T2 queue by re-measuring the T1 flag on disk.

The T1 criterion (message_count >= 7 AND digest body < 300 chars) is
recomputed against the current vault, so records already re-digested
self-resolve out of the queue — no stale done-list subtraction, no reliance
on the 2026-08-05 audit snapshots (which predate the gate for ChatGPT).

Handles both providers and both privacy tiers:
  30_Resources/conversations/*.md            (standard digests, flat)
  30_Resources/conversations/claude/*.md     (legacy folder)
  40_Archive/AI Conversations/{claude,chatgpt}/*.md   (sensitive digests)
Sources under the matching originals/ dirs. Where one provider_id has
several digest files (superseded stub beside its suffixed replacement),
the longest digest body wins — same rule as build_cluster.py.

Topic classes mirror the plan's buckets (engineering / career / code /
other) via title keywords; a keyword classifier is triage, not truth —
the plan's own caveat ("'engineering' is a topic label, not a population")
applies, and the manifest records the matched keyword for auditability.

Read-only over the vault; writes only data/imports/t2-remaining-queue.json.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data/imports/t2-remaining-queue.json"

DIGEST_RE = re.compile(r"^## Digest\s*\n(.*?)(?=\n## |\Z)", re.S | re.M)
SID_RE = re.compile(r"^source_conversation_id:\s*['\"]?([0-9a-f-]{8,})['\"]?", re.M)
MSGS_RE = re.compile(r"^message_count:\s*(\d+)", re.M)
THIN_CHARS = 300
THIN_MSGS = 7

ENG_KW = (
    "cable", "emf", "field", "earthing", "grounding", "lightning", "voltage",
    "current", "transformer", "substation", "insulation", "arc", "fault",
    "impedance", "conductor", "transmission", "distribution", "hv", "kv",
    "power-line", "power line", "electric", "magnetic", "thermal", "rating",
    "protection", "relay", "switchgear", "busbar", "cymcap", "etap", "pscad",
    "atp", "emtp", "short-circuit", "short circuit", "induced", "sheath",
    "screen", "bonding", "ampacity", "csa", "iec", "ieee", "as-nzs", "as/nzs",
)
CAREER_KW = (
    "resume", "cv", "cover letter", "interview", "job", "recruiter", "salary",
    "linkedin", "niw", "visa", "immigration", "employment", "career", "pr ",
    "permanent resid", "eb-2", "i-140", "petition", "reference letter",
)
CODE_KW = (
    "emptyos", "python", "javascript", "app", "plugin", "api", "daemon",
    "vault", "obsidian", "docker", "git", "sql", "regex", "debug", "code",
    "script", "html", "css", "server", "fastapi",
)


def classify(title: str) -> tuple[str, str]:
    t = title.lower()
    for kw in CAREER_KW:
        if kw in t:
            return "career", kw
    for kw in ENG_KW:
        if kw in t:
            return "engineering", kw
    for kw in CODE_KW:
        if kw in t:
            return "code", kw
    return "other", ""


def vault_root() -> Path:
    cfg = tomllib.loads((REPO / "emptyos.toml").read_text(encoding="utf-8"))
    return Path(cfg["notes"]["path"])


def main() -> int:
    vault = vault_root()
    digest_dirs = [
        (vault / "30_Resources/conversations", "standard"),
        (vault / "30_Resources/conversations/claude", "legacy"),
        (vault / "40_Archive/AI Conversations/claude", "sensitive"),
        (vault / "40_Archive/AI Conversations/chatgpt", "sensitive"),
    ]
    source_dirs = [
        vault / "30_Resources/conversations/originals/claude",
        vault / "30_Resources/conversations/originals/chatgpt",
        vault / "40_Archive/AI Conversations/originals/claude",
        vault / "40_Archive/AI Conversations/originals/chatgpt",
    ]

    # source index: short_id -> (path, message_count)
    sources: dict[str, tuple[Path, int | None]] = {}
    for sdir in source_dirs:
        if not sdir.is_dir():
            continue
        for p in sdir.glob("*.md"):
            m = re.search(r"--([0-9a-f]{8})\.md$", p.name)
            if not m:
                continue
            short = m.group(1)
            if short in sources:
                continue
            mm = MSGS_RE.search(p.read_text(encoding="utf-8", errors="replace")[:4000])
            sources[short] = (p, int(mm.group(1)) if mm else None)

    rows: dict[str, dict] = {}
    n_digests = 0
    for ddir, tier in digest_dirs:
        if not ddir.is_dir():
            continue
        for p in ddir.glob("*.md"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "record_kind: conversation-digest" not in text:
                continue
            n_digests += 1
            m = SID_RE.search(text)
            if not m:
                continue
            sid = m.group(1)
            short = sid[:8]
            dm = DIGEST_RE.search(text)
            chars = len(dm.group(1).strip()) if dm else 0
            prior = rows.get(short)
            if prior and prior["digest_chars"] >= chars:
                continue
            provider = "chatgpt" if "provider: chatgpt" in text[:800] else "claude"
            src = sources.get(short)
            topic, kw = classify(p.stem)
            rows[short] = {
                "short_id": short,
                "provider_id": sid,
                "provider": provider,
                "tier": tier,
                "date": p.name[:10],
                "title": p.stem,
                "digest_path": str(p.relative_to(vault)).replace("\\", "/"),
                "source_path": (
                    str(src[0].relative_to(vault)).replace("\\", "/") if src else None
                ),
                "source_kb": (src[0].stat().st_size // 1024) if src else None,
                "message_count": src[1] if src else None,
                "digest_chars": chars,
                "topic": topic,
                "topic_kw": kw,
            }

    flagged = [
        r for r in rows.values()
        if (r["message_count"] or 0) >= THIN_MSGS and r["digest_chars"] < THIN_CHARS
    ]
    flagged.sort(key=lambda r: (r["date"], r["short_id"]))

    by_topic: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for r in flagged:
        by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
        by_provider[r["provider"]] = by_provider.get(r["provider"], 0) + 1

    payload = {
        "schema_version": 1,
        "task": "T2-remaining",
        "criterion": f"message_count >= {THIN_MSGS} AND digest body < {THIN_CHARS} chars, recomputed live",
        "digests_scanned": n_digests,
        "unique_records": len(rows),
        "remaining": len(flagged),
        "by_topic": by_topic,
        "by_provider": by_provider,
        "items": flagged,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{n_digests} digest files, {len(rows)} unique records")
    print(f"T2 remaining (live flag): {len(flagged)}")
    print(f"  by provider: {by_provider}")
    print(f"  by topic:    {by_topic}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

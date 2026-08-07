#!/usr/bin/env python3
"""Stage the missing schema for legacy digests that predate the v4 contract.

These digests are not wrong, they are old. They carry a real digest, real
decisions, a real fact-check and a real routing note -- everything expensive
was already written by a human-reviewed pass. What they lack is the machinery
the auditor added afterwards: a `## Domain coverage` table, an
`## Evidence chain` section, and a `derived_notes:` frontmatter key.

So this is a schema completion, not a re-digest. The prose is never rewritten
and never re-reasoned; the local model only classifies the fifteen audited
domains against text that already exists. Anything it marks as a durable delta
then goes through `stage_routing_evidence.py` exactly like any other row, so a
legacy record ends up held to the same evidence bar as a fresh one.

Read-only with respect to the Vault. `apply_legacy_schema_batch.py` writes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[3]
CONFIG = REPO / "emptyos.toml"

if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = load_module(
    "conversation_legacy_audit", SCRIPT_DIR / "audit_evidence_graph.py"
)
APPLY = load_module(
    "conversation_legacy_io", SCRIPT_DIR / "apply_no_mutation_browser_batch.py"
)

CLASSIFY_SYSTEM = """You classify an existing conversation digest against a \
fixed set of fifteen domains.

You are NOT summarising and NOT re-analysing. The digest below was already \
written and reviewed. Your only job is to say, for each domain, whether the \
digest evidences a durable change.

List ONLY the domains this digest actually touches. Every domain you omit is \
recorded as `not-present`, which is the correct answer for most of them -- a \
typical conversation touches three to six. Do not pad the list.

Status vocabulary for the ones you do list:
- `delta` -- the digest evidences something durable in this domain.
- `mentioned-no-delta` -- the domain appears but nothing durable is \
established.
- `needs-review` -- genuinely undecidable from the digest text.

Rules:
- Absence of content means OMIT the domain, never `needs-review`. Reserve \
`needs-review` for real ambiguity; it blocks completion.
- Be conservative with `delta`. A passing mention, a question, or an \
unanswered idea is not a durable change.
- List `Transient/no durable delta` as `delta` only when the conversation as \
a whole carries nothing durable anywhere.
- `notes` is one short factual clause grounded in the digest, never a guess."""


def classify_schema(domains: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "domains": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(domains),
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "enum": list(domains)},
                        "status": {
                            "type": "string",
                            "enum": [
                                "delta",
                                "mentioned-no-delta",
                                "not-present",
                                "needs-review",
                            ],
                        },
                        "notes": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                    },
                    "required": ["domain", "status", "notes"],
                },
            }
        },
        "required": ["domains"],
    }


def load_ollama_settings(config_path: Path) -> tuple[str, str]:
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    think = (config.get("capabilities") or {}).get("think") or {}
    ollama = think.get("ollama") or {}
    host = str(ollama.get("host") or "http://127.0.0.1:11434").rstrip("/")
    model = str(ollama.get("model") or "").split("#", 1)[0].strip()
    if not model:
        raise ValueError("No capabilities.think.ollama.model configured")
    return host, model


def ollama_json(
    *, host: str, model: str, system: str, user: str,
    schema: dict[str, Any], timeout: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": 0.1, "num_ctx": 32768, "num_predict": 2400},
    }
    request = urllib.request.Request(
        host + "/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    content = str((envelope.get("message") or {}).get("content") or "").strip()
    if not content:
        raise ValueError("Ollama returned no content")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Ollama response is not a JSON object")
    return value


def normalise_coverage(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Complete the inventory, defaulting anything unlisted to not-present.

    Asking for all fifteen rows in one response does not work: the model
    returns a partial list and the run refuses. Asking only for the domains it
    can actually evidence is both more reliable and honest about what it did
    -- an unlisted domain is recorded as `not-present` with a note saying so,
    rather than being dressed up as a positive finding.
    """
    rows: dict[str, dict[str, str]] = {}
    for row in raw.get("domains") or []:
        domain = str(row.get("domain") or "")
        if domain not in AUDIT.DOMAIN_LABELS or domain in rows:
            continue
        status = str(row.get("status") or "")
        if status not in AUDIT.DOMAIN_STATUSES:
            status = "needs-review"
        rows[domain] = {
            "status": status,
            "notes": str(row.get("notes") or "").strip()
            or "No explanation supplied.",
        }
    for domain in AUDIT.DOMAIN_LABELS:
        rows.setdefault(
            domain,
            {
                "status": "not-present",
                "notes": "Not evidenced in the digest text.",
            },
        )
    return resolve_transient_contradiction(rows)


TRANSIENT_DOMAIN = "Transient/no durable delta"


def resolve_transient_contradiction(
    rows: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """`Transient` and a real delta cannot both be true of one conversation.

    The row means "carries nothing durable anywhere", but the model reads it
    as "parts of this were throwaway" and marks it alongside genuine
    findings. Left alone the coverage table contradicts itself. The two are
    definitionally exclusive, and the specific findings are the evidenced
    ones, so the blanket row yields and records that it was overruled.
    """
    durable = [
        d
        for d, r in rows.items()
        if d != TRANSIENT_DOMAIN and r["status"] == "delta"
    ]
    transient = rows.get(TRANSIENT_DOMAIN)
    if not durable or not transient or transient["status"] != "delta":
        return rows
    resolved = dict(rows)
    resolved[TRANSIENT_DOMAIN] = {
        "status": "mentioned-no-delta",
        "notes": (
            "Not wholly transient: durable content is recorded under "
            + ", ".join(sorted(durable)[:3])
            + ("." if len(durable) <= 3 else ", and others.")
        ),
    }
    return resolved


def digest_context(text: str) -> str:
    """The prose the classifier reads. Never rewritten, only read."""
    parts = []
    for heading in (
        "Digest",
        "Decisions and durable deltas",
        "Fact-check notes",
        "Routing",
    ):
        section = AUDIT.markdown_section(text, heading)
        if section.strip():
            parts.append(f"## {heading}\n{section.strip()}")
    return "\n\n".join(parts)


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    base_url, headers = APPLY.connection(args.config)
    host, model = load_ollama_settings(args.config)
    if args.model:
        model = args.model

    queue = json.loads(args.queue.read_text(encoding="utf-8-sig"))
    items = list(queue.get("items") or [])
    if args.id:
        wanted = set(args.id)
        items = [i for i in items if i.get("provider_id") in wanted]

    output: dict[str, Any] = {
        "schema_version": 1,
        "record_kind": "legacy-schema-stage",
        "provider": queue.get("provider"),
        "generator": {"backend": "ollama", "host": host, "model": model},
        "items": [],
        "errors": [],
    }
    if args.output.exists() and not args.force:
        previous = json.loads(args.output.read_text(encoding="utf-8-sig"))
        output["items"] = list(previous.get("items") or [])
        output["errors"] = list(previous.get("errors") or [])
    done = {str(r.get("provider_id")) for r in output["items"]}

    staged = 0
    schema = classify_schema(AUDIT.DOMAIN_LABELS)
    for item in items:
        provider_id = str(item.get("provider_id") or "")
        if provider_id in done and not args.force:
            continue
        if staged >= args.limit:
            break
        started = time.time()
        try:
            digest_path = str(item.get("digest_path") or "")
            text = APPLY.api_read(base_url, headers, digest_path)
            if not text:
                raise ValueError(f"Digest unreadable at {digest_path}")
            context = digest_context(text)
            if not context.strip():
                raise ValueError("Digest carries none of the expected sections")
            raw = ollama_json(
                host=host,
                model=model,
                system=CLASSIFY_SYSTEM,
                user=(
                    f"Conversation: {item.get('title') or ''}\n\n{context}"
                    f"\n\nClassify all {len(AUDIT.DOMAIN_LABELS)} domains."
                ),
                schema=schema,
                timeout=args.timeout,
            )
            coverage = normalise_coverage(raw)
            deltas = [
                d
                for d, row in coverage.items()
                if row["status"] == "delta"
                and d != "Transient/no durable delta"
            ]
            output["items"].append(
                {
                    "provider_id": provider_id,
                    "title": item.get("title"),
                    "digest_path": digest_path,
                    "digest_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "domain_coverage": coverage,
                    "delta_domains": deltas,
                    "needs_review_domains": [
                        d
                        for d, r in coverage.items()
                        if r["status"] == "needs-review"
                    ],
                    "elapsed_s": round(time.time() - started, 2),
                }
            )
            output["errors"] = [
                e
                for e in output["errors"]
                if str(e.get("provider_id")) != provider_id
            ]
        except Exception as exc:  # noqa: BLE001 — recorded, never swallowed
            output["errors"].append(
                {
                    "provider_id": provider_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        staged += 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(args.output)

    return output["items"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    return APPLY.run_cli(lambda: build(args), noun="digests classified")


if __name__ == "__main__":
    raise SystemExit(main())

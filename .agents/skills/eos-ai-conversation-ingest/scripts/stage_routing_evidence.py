#!/usr/bin/env python3
"""Stage routing-evidence receipts for unevidenced `digest-contained` claims.

A digest may say "this delta already lives in the digest, no living note was
mutated". `audit_evidence_graph` only accepts that claim with a receipt naming
what was searched and what was compared. 370 historical digests assert it with
no receipt at all, which is the routing-review debt.

This worker earns the receipt. It reads the Vault (search + note reads) but
**never writes to it** — the deterministic writer is
``apply_routing_evidence_batch.py``. Splitting them keeps the sibling worker
``generate_candidate_specs.py`` Vault-blind, which is a tested contract it
relies on to run with the daemon down.

Per delta it asks the local model two narrow questions — *what would you search
for* and *does this candidate note already carry this fact* — and never lets it
author prose that reaches the Vault. The receipt is assembled from what the
search actually returned, so a citation cannot be hallucinated: every
``checked:`` wikilink is a path a real query returned in this run.

Resumable: the output file is rewritten after every item, and re-running skips
provider IDs already staged unless ``--force`` is given.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[3]
CONFIG = REPO / "emptyos.toml"

if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
from scanner_lib import emit_json  # noqa: E402


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = load_module(
    "conversation_routing_audit",
    SCRIPT_DIR / "audit_evidence_graph.py",
)
APPLY = load_module(
    "conversation_routing_io",
    SCRIPT_DIR / "apply_no_mutation_browser_batch.py",
)

# A conversation archive is not a living note. Citing one as the place a fact
# "already lives" would satisfy the auditor's grammar while saying nothing —
# the auditor never opens what `checked:` names, so this filter is the only
# thing standing between a real comparison and a circular one. Backups are
# excluded for the same reason plus a practical one: this drain writes ~370 of
# them, and `_search_hits` greps every `.md` in the vault.
NON_LIVING_PREFIXES = (
    "30_Resources/conversations/",
    "40_Archive/AI Conversations/",
    "99_Attachments/temp-backup/",
)

# Extracted source documents are not living notes either. A PDF page fragment
# will match almost any technical term, so without this filter the top hits are
# dominated by standards extracts and the receipt cites them instead of the
# note a fact would actually live in. Observed on the first live run: querying
# "cable" returned fifteen page-fragments and zero real notes.
EXTRACT_MARKERS = (".pdf.md", ".docx.md", ".pptx.md", ".epub.md")
EXTRACT_RE = re.compile(r"/part-\d+-pages-\d+", re.IGNORECASE)

# Neither is a place a durable fact "already lives": a sync-conflict copy is a
# duplicate, and tooling/config notes match on vocabulary rather than content.
NOISE_RE = re.compile(
    r"\.sync-conflict-|^_claude/|^\.obsidian/|/templates?/"
    r"|/kb/sources/_fulltext/",
    re.IGNORECASE,
)


def relevance(path: str, terms: list[str]) -> tuple[int, int]:
    """Rank a hit by how much its *path* speaks to the query.

    The search returns paths with no score, in index order, so a broad term
    like "cable" buries the one note actually named for the subject under
    whatever the walker reached first. Sorting by term-in-path puts
    `cable-current-rating-moc.md` above `inbox.md`, which is the difference
    between a receipt that names the right note and one that names noise.
    """
    lowered = path.lower()
    stem = Path(lowered).stem
    hits = sum(1 for term in terms if term.lower() in lowered)
    in_name = sum(1 for term in terms if term.lower() in stem)
    return (-(hits + in_name * 2), len(path))

TRANSIENT_DOMAIN = "Transient/no durable delta"

# Domains where a wrong "already covered" call is expensive and a local model
# has no business making it alone. Staged, but flagged for a strong reviewer.
SENSITIVE_DOMAINS = frozenset(
    {
        "Finance and assets",
        "Personal chronology",
        "Work and career",
        "Legal, immigration, and administration",
        "Emotional and health",
        "Tasks and commitments",
        "People and relationships",
    }
)

QUERY_SYSTEM = """You turn a conversation's subject matter into literal search \
terms for a personal Markdown vault.

The search is a LITERAL substring grep, not a semantic index. A multi-word \
phrase almost always returns nothing. Emit SHORT terms: one or two words each.

You are given the conversation digest and one domain of interest. Ground the \
terms in the SPECIFIC subject the conversation is about, never in the generic \
wording of the routing note.

Return JSON only:
{"en": ["term", "term"], "zh": ["词"]}

Rules:
- 2 to 3 English terms, each 1-2 words, lowercase, no punctuation.
- 1 to 2 Chinese terms, each 2-4 characters. The vault is bilingual.
- Prefer DISTINCTIVE nouns: a technical term, a product, a person, a standard \
number. Something a note on this exact subject would literally contain.
- Reject terms so broad they would match hundreds of notes ("cable", "data", \
"project", "work", "code", "note", "rating" alone).
- Terms must differ meaningfully between domains. If a domain adds nothing \
beyond the conversation's main subject, still bias toward that domain's \
vocabulary."""

JUDGE_SYSTEM = """You decide whether an existing vault note ALREADY CONTAINS a \
specific fact.

Return JSON only:
{"covered": true|false, "best_index": <int>, "why": "<one short sentence>"}

Rules:
- `covered` is true ONLY if a candidate note states this specific fact, not \
merely the same general topic. Same subject area is NOT coverage.
- If unsure, answer false. A false negative leaves the fact in the digest, \
which is safe. A false positive claims the vault holds something it does not.
- `best_index` is the 0-based index of the most relevant candidate, or -1 when \
none is relevant.
- Judge only from the candidate text shown. Never assume unseen content."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


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


def vault_root(base_url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(f"{base_url}/api/health", headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    return str(payload.get("vault_path") or "").replace("\\", "/").rstrip("/")


def relative_to_vault(path: str, root: str) -> str:
    normalized = str(path).replace("\\", "/")
    if root and normalized.startswith(root):
        normalized = normalized[len(root) :]
    return normalized.lstrip("/")


def is_living_note(path: str) -> bool:
    lowered = path.lower()
    return (
        path.endswith(".md")
        and not path.startswith(NON_LIVING_PREFIXES)
        and not lowered.endswith(EXTRACT_MARKERS)
        and not EXTRACT_RE.search(path)
        and not NOISE_RE.search(path)
    )


def ollama_json(
    *,
    host: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    timeout: float,
    num_predict: int = 400,
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
        "options": {
            "temperature": 0.1,
            "num_ctx": 32768,
            "num_predict": num_predict,
        },
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
        raise ValueError(
            "Ollama returned no content "
            f"(done_reason={envelope.get('done_reason')!r})"
        )
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Ollama response is not a JSON object")
    return value


QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "en": {
            "type": "array",
            "items": {"type": "string", "minLength": 2, "maxLength": 40},
            "minItems": 1,
            "maxItems": 3,
        },
        "zh": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 20},
            "minItems": 0,
            "maxItems": 2,
        },
    },
    "required": ["en", "zh"],
}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "covered": {"type": "boolean"},
        "best_index": {"type": "integer"},
        "why": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    "required": ["covered", "best_index", "why"],
}


def clean_terms(values: Any, *, limit: int) -> list[str]:
    """Keep short, literal, greppable terms; drop what the grep would waste."""
    out: list[str] = []
    for raw in values or []:
        term = re.sub(r"\s+", " ", str(raw)).strip().strip(".,;:!?\"'")
        if not term or len(term) > 40:
            continue
        if len(term.split()) > 2:
            continue
        if term.lower() in out:
            continue
        out.append(term.lower() if term.isascii() else term)
        if len(out) >= limit:
            break
    return out


def search_vault(
    base_url: str,
    headers: dict[str, str],
    query: str,
    *,
    root: str,
    timeout: float,
    cache: dict[str, list[str]],
) -> list[str]:
    """One literal grep over the vault, memoized across the whole run."""
    if query in cache:
        return cache[query]
    url = f"{base_url}/search/api/search?" + urllib.parse.urlencode(
        {"q": query}
    )
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        hits = [
            relative_to_vault(str(item), root)
            for item in payload.get("results") or []
        ]
    except (urllib.error.URLError, TimeoutError, OSError):
        # A timed-out query is recorded as producing nothing, never silently
        # merged into the "no candidate found" conclusion by omission.
        hits = []
    cache[query] = hits
    return hits


def evidence_cell(queries: list[str], checked: list[str]) -> str:
    """Render the receipt in the exact grammar the auditor parses.

    The separator must be U+00B7. `routing_table` escapes a literal `|` to
    `\\|`, and `markdown_table_cells` splits on the raw character, so a pipe
    here would silently split the cell in two.
    """
    query_text = "; ".join(queries)
    if checked:
        checked_text = ", ".join(f"[[{path.removesuffix('.md')}]]" for path in checked)
    else:
        checked_text = "no candidate found"
    return f"queries: {query_text} · checked: {checked_text}"


def stage_item(
    row_ctx: dict[str, Any],
    *,
    base_url: str,
    headers: dict[str, str],
    root: str,
    host: str,
    model: str,
    timeout: float,
    search_timeout: float,
    cache: dict[str, list[str]],
    candidate_chars: int,
) -> dict[str, Any]:
    """Earn one row's receipt. Returns the staged patch row."""
    domain = row_ctx["domain"]
    claim = row_ctx["claim"]

    queries_raw = ollama_json(
        host=host,
        model=model,
        system=QUERY_SYSTEM,
        user=(
            f"Conversation: {row_ctx['title']}\n\n"
            f"Digest:\n{row_ctx['digest']}\n\n"
            f"Domain of interest: {domain}\n"
            f"Routing note (generic boilerplate, do not mine for terms):\n"
            f"{claim}"
        ),
        schema=QUERY_SCHEMA,
        timeout=timeout,
        num_predict=200,
    )
    terms = clean_terms(queries_raw.get("en"), limit=3) + clean_terms(
        queries_raw.get("zh"), limit=2
    )
    if not terms:
        terms = clean_terms([row_ctx["title"]], limit=1) or [domain]

    hits: list[str] = []
    for term in terms:
        for path in search_vault(
            base_url,
            headers,
            term,
            root=root,
            timeout=search_timeout,
            cache=cache,
        ):
            if is_living_note(path) and path not in hits:
                hits.append(path)
        if len(hits) >= 12:
            break
    hits.sort(key=lambda path: relevance(path, terms))

    candidates: list[tuple[str, str]] = []
    unreadable: list[str] = []
    for path in hits[:5]:
        # A candidate is optional context. One note the daemon cannot serve --
        # observed as an intermittent 500 on a single path -- must not cost the
        # whole conversation its receipt, when four other notes were read fine
        # and the comparison stands on them. The skip is recorded rather than
        # swallowed, because `checked:` may only cite what was actually read.
        try:
            text = APPLY.api_read(base_url, headers, path)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            unreadable.append(f"{path}: {type(exc).__name__}")
            continue
        if text:
            candidates.append((path, text[:candidate_chars]))

    covered = False
    best_path = ""
    why = "No living-note candidate was returned by any query."
    if candidates:
        listing = "\n\n".join(
            f"[{index}] {path}\n{text}"
            for index, (path, text) in enumerate(candidates)
        )
        verdict = ollama_json(
            host=host,
            model=model,
            system=JUDGE_SYSTEM,
            user=(
                f"Fact ({domain}):\n{claim}\n\n"
                f"Candidate notes:\n{listing}"
            ),
            schema=JUDGE_SCHEMA,
            timeout=timeout,
            num_predict=300,
        )
        why = str(verdict.get("why") or "").strip()
        index = int(verdict.get("best_index", -1))
        if 0 <= index < len(candidates):
            best_path = candidates[index][0]
        covered = bool(verdict.get("covered")) and bool(best_path)

    # `checked` must name what was actually read and put in front of the
    # judge -- not merely what a query returned. Citing an unread hit would
    # claim a comparison that never happened, and the auditor never opens
    # these paths to catch it.
    compared = [path for path, _ in candidates]
    checked = [best_path] if best_path else compared[:3]

    # The disposition is ALWAYS `digest-contained`. The local model is not
    # trusted to rule that an existing note already carries a fact, because
    # measured against the first three upgrades it proposed, it was wrong
    # three times out of three: it matched topic adjacency and routing
    # boilerplate rather than content -- offering an inverter-sizing note for
    # a cable-rating claim, a CIGRE tunnel clause for a project-execution
    # delta, and a daily worklog for a CSS behaviour.
    #
    # Getting this wrong is the one outcome here that changes meaning: a
    # `reused-no-change` row asserts the vault already holds the fact, and the
    # auditor validates only that the target *exists*, never that it covers
    # anything. Leaving the fact in the digest costs nothing and loses
    # nothing, so the opinion is recorded as metadata for a human to promote
    # rather than acted on.
    patch = {
        "domain": domain,
        "disposition": "digest-contained",
        "routing_evidence": evidence_cell(terms, checked),
        "queries": terms,
        "hits": hits,
        "candidates_compared": compared,
        "model_verdict": {"covered": covered, "best": best_path, "why": why},
        "unreadable_candidates": unreadable,
        "model_suggests_reuse": bool(covered),
        "needs_strong_review": bool(covered) or domain in SENSITIVE_DOMAINS,
    }
    return patch


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    base_url, headers = APPLY.connection(args.config)
    root = vault_root(base_url, headers)
    host, model = load_ollama_settings(args.config)
    if args.model:
        model = args.model

    queue = read_json(args.queue)
    items = list(queue.get("items") or [])
    if args.id:
        wanted = set(args.id)
        items = [item for item in items if item.get("provider_id") in wanted]

    output: dict[str, Any] = {
        "schema_version": 1,
        "record_kind": "routing-evidence-stage",
        "provider": queue.get("provider"),
        "generator": {"backend": "ollama", "host": host, "model": model},
        "items": [],
        "errors": [],
    }
    if args.output.exists() and not args.force:
        previous = read_json(args.output)
        output["items"] = list(previous.get("items") or [])
        output["errors"] = list(previous.get("errors") or [])
    done = {str(row.get("provider_id")) for row in output["items"]}

    cache: dict[str, list[str]] = {}
    staged = 0
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

            statuses = AUDIT.domain_statuses(text)
            rows = AUDIT.delta_dispositions(text)
            pending = [
                row
                for row in rows
                if row["disposition"] == "digest-contained"
                and statuses.get(row["domain"]) == "delta"
                and row["domain"] != TRANSIENT_DOMAIN
                and not AUDIT.valid_digest_contained_routing_evidence(
                    row["routing_evidence"]
                )
            ]
            digest_body = AUDIT.markdown_section(text, "Digest") or text[:4000]

            patches = [
                stage_item(
                    {
                        "domain": row["domain"],
                        "claim": row["reason"],
                        "title": str(item.get("title") or ""),
                        "digest": digest_body[:2500],
                    },
                    base_url=base_url,
                    headers=headers,
                    root=root,
                    host=host,
                    model=model,
                    timeout=args.timeout,
                    search_timeout=args.search_timeout,
                    cache=cache,
                    candidate_chars=args.candidate_chars,
                )
                for row in pending
            ]
            output["items"].append(
                {
                    "provider_id": provider_id,
                    "title": item.get("title"),
                    "digest_path": digest_path,
                    "digest_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "rows_total": len(rows),
                    "rows_staged": len(patches),
                    "patches": patches,
                    "needs_strong_review": any(
                        patch["needs_strong_review"] for patch in patches
                    ),
                    "elapsed_s": round(time.time() - started, 2),
                }
            )
            output["errors"] = [
                row
                for row in output["errors"]
                if str(row.get("provider_id")) != provider_id
            ]
        except Exception as exc:  # noqa: BLE001 — recorded, never swallowed
            output["errors"].append(
                {
                    "provider_id": provider_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        staged += 1
        write_json(args.output, output)

    return output["items"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--search-timeout", type=float, default=10.0)
    parser.add_argument("--candidate-chars", type=int, default=1200)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    def run() -> list[dict[str, Any]]:
        return build(args)

    return APPLY.run_cli(run, noun="conversations staged")


if __name__ == "__main__":
    raise SystemExit(main())

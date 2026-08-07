#!/usr/bin/env python3
"""Assemble a reviewed writer spec from per-record reading findings.

    python data/imports/t2-work/build_spec_from_findings.py titles
    python data/imports/t2-work/build_spec_from_findings.py build \
        --findings data/imports/t2-work/findings-2025-04-30.json \
        --out data/imports/claude-export-2026-07-24/reviewed-spec-t2-emf-2025-04-30.json

The 31 remaining EMFieldCalc records are re-digests, and every one of them takes
the *same* mechanical flags. The existing ``build_spec_*.py`` scripts hand-roll
those flags once per record at roughly 17 KB of Python each, which is both the
bottleneck and the place a silent omission hides -- ``repair_source_digest_link``
is inert without ``reuse_existing_source`` and reports nothing when you forget.

So the split here is deliberate: **the reader supplies every judgment** (digest
narrative, the fifteen-domain coverage, decisions, fact-checks, dispositions,
derived mutations, topic tags) and **this script supplies only mechanics** that
must not vary between records:

* ``title`` comes from the export via the writer's own ``find_conversation``,
  never from the findings file. A byte mismatch is a ``ValueError`` deep inside
  ``apply_item()``; the two cannot disagree if they read the same function.
* ``reuse_existing_source`` is always true (sources are archived and
  hash-verified; T2 is a digest rewrite, never a recapture), and
  ``repair_source_digest_link`` is **derived per record** from the source's
  actual ``related:`` link shape (``source_link_shape``) — legacy-fullpath
  gets the repair, suffixed-fullpath doesn't need it, and bare/none links are
  refused for the hand-repair shape. The earlier forced-true pair encoded the
  EMF cluster's premise ("all remaining records are legacy-path") that a new
  cluster must measure, not inherit.
* ``emfieldcalc`` is forced into ``tags``. The writer rebuilds ``tags:`` wholesale
  from the spec, so a re-digest silently evicts a record from its own cluster
  unless the spec repeats the marker (commit ``ba6c56fb`` reports the drop; this
  prevents it).

Validation mirrors ``audit_evidence_graph.assess_no_mutation_routing`` rather
than restating it from memory, because that auditor is what judges the write.
The load-bearing subtlety: the whole disposition requirement is gated on
``if not has_derived_notes``. A record with any derived mutation needs no
disposition rows at all; a record with none needs one row per non-transient
``delta`` domain, and ``deferred-unverified`` is a *failure* even though it is a
member of ``NO_MUTATION_DISPOSITIONS``.

Read-only with respect to the Vault. Writes only under ``data/imports/``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / "data/imports/t2-work"  # session data stays here; only the code moved
SKILL = REPO / ".agents/skills/eos-ai-conversation-ingest/scripts"
CLUSTER = REPO / "data/imports/t2-emfieldcalc-cluster.json"  # default; --cluster overrides
TITLE_CACHE = HERE / "emf-titles.json"
EXPORT = REPO / "data/imports/claude-export-2026-07-24/batch-0000/conversations.json"

CLUSTER_TAG = "emfieldcalc"  # default; --tag overrides
TZ = timezone(timedelta(hours=10))  # the ingest lane stamps +10:00

sys.path.insert(0, str(REPO / "scripts"))
from vault_paths import require_vault_root  # noqa: E402


def _load(name: str) -> Any:
    """Import a skill script by path so we share its constants, not a copy."""
    spec = importlib.util.spec_from_file_location(name, SKILL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _load("audit_evidence_graph")
CLAUDE = _load("claude_export_queue")

DOMAIN_LABELS = AUDIT.DOMAIN_LABELS
COVERAGE_STATUSES = {"delta", "mentioned-no-delta", "not-present", "needs-review"}
TRANSIENT = "Transient/no durable delta"
# deferred-unverified is a member of NO_MUTATION_DISPOSITIONS but the auditor
# flags it unconditionally, so it can never be a valid assembled disposition.
USABLE_DISPOSITIONS = AUDIT.NO_MUTATION_DISPOSITIONS - {"deferred-unverified"}


# ---------------------------------------------------------------- titles


def cmd_titles(args: argparse.Namespace) -> int:
    """One streaming pass over the 571 MB export for every wanted title.

    ``find_conversation`` rescans the whole file per call; 31 calls is 31 passes.
    """
    global CLUSTER, EXPORT, TITLE_CACHE
    if getattr(args, "cluster", None):
        CLUSTER = Path(args.cluster)
        TITLE_CACHE = HERE / (CLUSTER.stem + "-titles.json")
    if getattr(args, "export", None):
        EXPORT = Path(args.export)
    cluster = json.loads(CLUSTER.read_text(encoding="utf-8"))
    wanted = {
        str(i["provider_id"]).lower(): i["short_id"]
        for i in cluster["items"]
        if args.all or not i["done"]
    }
    found: dict[str, dict[str, str]] = {}
    for conversation in CLAUDE.iter_json_array(str(EXPORT)):
        cid = CLAUDE.conversation_id(conversation)
        if cid in wanted:
            found[cid] = {
                "short_id": wanted[cid],
                "title": str(
                    conversation.get("title") or conversation.get("name") or "Untitled"
                ),
            }
            if len(found) == len(wanted):
                break
    missing = sorted(set(wanted) - set(found))
    TITLE_CACHE.write_text(
        json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"resolved {len(found)}/{len(wanted)} titles -> {TITLE_CACHE}")
    for cid in missing:
        print(f"  MISSING {wanted[cid]} {cid}")
    return 1 if missing else 0


# ---------------------------------------------------------------- build


def captured_at_of(source_path: str) -> str:
    """Read the existing source note's captured_at; it must survive a reuse."""
    note = require_vault_root() / source_path
    head = note.read_text(encoding="utf-8", errors="replace")[:4000]
    match = re.search(r'^captured_at:\s*"?([^"\n]+)"?\s*$', head, re.M)
    if not match:
        raise SystemExit(f"no captured_at in {source_path}")
    return match.group(1).strip()


def source_link_shape(source_path: str) -> tuple[str, str | None]:
    """Classify the source's ``related:`` digest wikilink.

    Decides the reuse/repair flags PER RECORD instead of assuming the EMF
    premise ("every remaining record is legacy-path") holds for a new
    cluster. Verified 2026-08-07 on cabletool: 44/46 legacy-fullpath,
    2 suffixed-fullpath, 0 bare — but the next cluster gets measured, not
    trusted. Shapes:

    * ``legacy-fullpath``   — full-path link, no ``--shortid`` suffix; the
      writer's repair rewrites it (reuse=True, repair=True).
    * ``suffixed-fullpath`` — already points at the suffixed digest; the
      writer reuses in place, nothing to repair (reuse=True, repair=False).
    * ``bare``              — the repairer refuses bare-filename links AND
      ``source_related_digest()`` resolves them into the wrong folder; needs
      the hand-repair shape (body hash unchanged, contract passing). Refused.
    * ``none``              — no digest wikilink in frontmatter. Refused.
    """
    head = (require_vault_root() / source_path).read_text(
        encoding="utf-8", errors="replace")[:4000]
    fm_end = head.find("\n---", 4)
    fm = head[:fm_end] if fm_end > 0 else head
    links = [l for l in re.findall(r"\[\[([^\]|]+)", fm)
             if re.match(r"^\d{4}-\d{2}-\d{2}", Path(l).name)]
    if not links:
        return "none", None
    link = links[0].strip()
    if "/" not in link:
        return "bare", link
    suffixed = bool(re.search(r"--[0-9a-f]{8}$", Path(link).name))
    return ("suffixed-fullpath" if suffixed else "legacy-fullpath"), link


def validate(item: dict[str, Any], short_id: str) -> list[str]:
    """Mirror the auditor's routing gate; refuse before the writer touches disk."""
    problems: list[str] = []
    coverage = item.get("domain_coverage") or {}

    missing_domains = [d for d in DOMAIN_LABELS if d not in coverage]
    if missing_domains:
        problems.append(f"domain_coverage missing: {', '.join(missing_domains)}")
    for domain, row in coverage.items():
        if domain not in DOMAIN_LABELS:
            problems.append(f"unknown domain {domain!r}")
            continue
        status = (row or {}).get("status")
        if status not in COVERAGE_STATUSES:
            problems.append(f"{domain}: bad status {status!r}")
        # `or ""` not `.get(k, "")`: the findings file is LLM-authored, so an
        # explicit `"notes": null` is plausible, and the two-arg default only
        # fires when the key is *absent* — present-but-None would reach .strip()
        # and raise AttributeError instead of reporting the empty notes.
        if not ((row or {}).get("notes") or "").strip():
            problems.append(f"{domain}: empty notes")

    has_derived = bool(item.get("derived_mutations"))
    durable_deltas = [
        d
        for d, row in coverage.items()
        if (row or {}).get("status") == "delta" and d != TRANSIENT
    ]
    needs_review = [
        d for d, row in coverage.items() if (row or {}).get("status") == "needs-review"
    ]

    # The auditor gates this entire block on `not has_derived_notes`.
    if not has_derived:
        for domain in needs_review:
            problems.append(f"needs-review unresolved with no derived note: {domain}")
        by_domain = {
            r.get("domain"): r for r in (item.get("delta_dispositions") or [])
        }
        for domain in durable_deltas:
            row = by_domain.get(domain)
            if row is None:
                problems.append(f"delta-disposition-missing: {domain}")
                continue
            disposition = row.get("disposition")
            if disposition not in USABLE_DISPOSITIONS:
                problems.append(
                    f"delta-disposition-invalid: {domain} ({disposition!r})"
                )
                continue
            reason = (row.get("reason") or "").strip()
            if not reason or reason in {"-", "—"}:
                problems.append(f"delta-disposition-reason-missing: {domain}")
            normalized = re.sub(r"\s+", " ", reason.lower())
            if any(
                marker in normalized
                for marker in AUDIT.GENERIC_NO_MUTATION_REASON_MARKERS
            ):
                problems.append(f"delta-disposition-generic: {domain}")
            target = AUDIT.first_wikilink(row.get("target") or "")
            if disposition in {"reused-no-change", "duplicate-of"}:
                if not target:
                    problems.append(f"delta-disposition-target-missing: {domain}")
                else:
                    # The auditor resolves the wikilink and reads the note. A bare
                    # slug normalises to `<slug>.md` at the Vault ROOT, so it never
                    # resolves for a note that lives under a folder — the same
                    # full-path-only rule `repair_source_digest_link` has. Catch it
                    # here; otherwise the whole batch is refused after the read.
                    target_path = AUDIT.normalize_vault_note_path(target)
                    if target_path is None or not (
                        require_vault_root() / target_path
                    ).exists():
                        problems.append(
                            f"delta-disposition-target-not-found: {domain} "
                            f"({target!r} -> {target_path}). Use a full-path wikilink."
                        )
            if disposition == "digest-contained" and not (
                AUDIT.valid_digest_contained_routing_evidence(
                    row.get("routing_evidence") or ""
                )
            ):
                problems.append(
                    f"delta-digest-contained-routing-evidence-missing: {domain}"
                )

    for check in item.get("fact_checks") or []:
        if not check.get("claim") or not check.get("check"):
            problems.append("fact_check row missing claim/check")
    if not (item.get("digest") or "").strip():
        problems.append("empty digest")
    return problems


def cmd_build(args: argparse.Namespace) -> int:
    global CLUSTER, EXPORT, TITLE_CACHE, CLUSTER_TAG
    if getattr(args, "cluster", None):
        CLUSTER = Path(args.cluster)
        TITLE_CACHE = HERE / (CLUSTER.stem + "-titles.json")
    if getattr(args, "export", None):
        EXPORT = Path(args.export)
    if getattr(args, "tag", None):
        CLUSTER_TAG = args.tag
    cluster = json.loads(CLUSTER.read_text(encoding="utf-8"))
    by_short = {i["short_id"]: i for i in cluster["items"]}
    if not TITLE_CACHE.exists():
        raise SystemExit(f"run `titles` first: {TITLE_CACHE} missing")
    titles = json.loads(TITLE_CACHE.read_text(encoding="utf-8"))
    title_by_short = {v["short_id"]: v["title"] for v in titles.values()}

    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    if isinstance(findings, dict):
        findings = findings.get("items") or []

    today = datetime.now(TZ).date().isoformat()
    stamp = datetime.now(TZ).replace(microsecond=0).isoformat()

    items: list[dict[str, Any]] = []
    refused: list[tuple[str, list[str]]] = []
    for f in findings:
        short_id = str(f["short_id"])
        record = by_short.get(short_id)
        if record is None:
            refused.append((short_id, ["not in cluster manifest"]))
            continue
        if short_id not in title_by_short:
            refused.append((short_id, ["no cached export title"]))
            continue

        shape, _link = source_link_shape(record["source_path"])
        if shape in ("bare", "none"):
            refused.append((short_id, [
                f"source related: link shape {shape!r} — the repairer cannot "
                f"rewrite it; apply the hand-repair shape first "
                f"(body hash unchanged, source contract passing)"]))
            continue

        tags = list(dict.fromkeys([*(f.get("tags") or []), CLUSTER_TAG]))
        item = {
            "provider_id": record["provider_id"],
            # byte-exact from the export, never from the findings file
            "title": title_by_short[short_id],
            "conversation_date": record["date"],
            "ingestion_date": today,
            "captured_at": captured_at_of(record["source_path"]),
            "digested_at": stamp,
            "private": False,
            # measured per record from the source's related: link, never
            # assumed — the EMF "all legacy-path" premise was cluster-specific
            "reuse_existing_source": True,
            "repair_source_digest_link": shape == "legacy-fullpath",
            "tags": tags,
            "digest": f.get("digest") or "",
            "source_summary": f.get("source_summary") or "",
            "domain_coverage": f.get("domain_coverage") or {},
            "decisions": f.get("decisions") or [],
            "fact_checks": f.get("fact_checks") or [],
            "delta_dispositions": f.get("delta_dispositions") or [],
            "derived_mutations": f.get("derived_mutations") or [],
            "ledger_summary": f.get("ledger_summary") or "",
        }
        problems = validate(item, short_id)
        if problems:
            refused.append((short_id, problems))
            continue
        items.append(item)

    for short_id, problems in refused:
        print(f"REFUSED {short_id}")
        for p in problems:
            print(f"    {p}")
    if refused and not args.partial:
        print(f"\n{len(refused)} refused; wrote nothing (pass --partial to emit the rest)")
        return 1

    out = Path(args.out)
    out.write_text(
        json.dumps({"items": items}, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote {len(items)} item(s) -> {out}")
    if refused:
        print(f"({len(refused)} refused and omitted)")
    return 1 if refused else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_titles = sub.add_parser("titles", help="cache export titles in one pass")
    p_titles.add_argument("--all", action="store_true", help="include done records")
    p_titles.add_argument("--cluster", help="cluster manifest path (default: EMF)")
    p_titles.add_argument("--export", help="provider conversations.json (default: claude 07-24)")
    p_titles.set_defaults(func=cmd_titles)

    p_build = sub.add_parser("build", help="findings -> reviewed spec")
    p_build.add_argument("--findings", required=True)
    p_build.add_argument("--cluster", help="cluster manifest path (default: EMF)")
    p_build.add_argument("--export", help="provider conversations.json (default: claude 07-24)")
    p_build.add_argument("--tag", help="cluster tag forced into every spec (default: emfieldcalc)")
    p_build.add_argument("--out", required=True)
    p_build.add_argument(
        "--partial",
        action="store_true",
        help="emit the valid items even when some are refused",
    )
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

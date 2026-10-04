#!/usr/bin/env python3
"""Sync payslips from Gmail → vault PDFs + a derived Income-Log.md.

    python scripts/payslip_sync.py                 # latest payslip only
    python scripts/payslip_sync.py --backfill      # every payslip in the mailbox
    python scripts/payslip_sync.py --backfill --limit 50
    python scripts/payslip_sync.py --reparse       # skip Gmail, re-read local PDFs

WHAT LANDS WHERE
  {vault}/20_Areas/Finances/payslips/payslip-<period-end>.pdf
      Primary evidence. The vault gitignores *.pdf (Strategy A, 2026-06-15), so
      these never reach GitHub; Syncthing still replicates them to the desktop.
      Named by PERIOD END, not email date, so they sort by what they measure.
  {vault}/20_Areas/Finances/Income-Log.md
      Derived table, one row per pay. Markdown, git-tracked. If the parser is
      ever wrong the PDF is right there to check against.

RULE 19: parsing is local + deterministic (pypdf + regex in emptyos/sdk/payslip).
Payslip content never reaches a model. Do not add an LLM fallback here.

No kernel import — safe to run while the daemon is up.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from emptyos.sdk.payslip import PayslipParseError, PayslipRecord, parse_payslip  # noqa: E402

PAYSLIP_QUERY = "subject:Payslip from:PayrollServices"
VAULT_SUBDIR = "20_Areas/Finances/payslips"
LOG_PATH = "20_Areas/Finances/Income-Log.md"


def _load_gmail_client():
    spec = importlib.util.spec_from_file_location(
        "gmail_client", REPO / "plugins" / "gmail" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vault_root() -> Path:
    cfg = REPO / "emptyos.toml"
    if not cfg.exists():
        raise SystemExit(f"missing {cfg} — cannot locate the vault")
    with cfg.open("rb") as fh:
        data = tomllib.load(fh)
    path = (data.get("notes") or {}).get("path")
    if not path:
        raise SystemExit("emptyos.toml has no [notes] path")
    root = Path(path)
    if not root.is_dir():
        raise SystemExit(f"vault path does not exist: {root}")
    return root


def _pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:
        raise PayslipParseError("payslip PDF is encrypted — passphrase support not built")
    return "\n".join((p.extract_text() or "") for p in reader.pages)


# ── fetch ─────────────────────────────────────────────────────────────────────


def fetch_payslips(limit: int, dest: Path) -> list[Path]:
    """Download payslip PDFs not already on disk. Returns every local PDF path."""
    gc = _load_gmail_client()
    creds = gc.load_credentials(REPO / "data" / "secrets")
    if creds is None:
        raise SystemExit("no Gmail token — run: python scripts/gmail_auth.py")

    msg_ids = gc.list_message_ids(creds, PAYSLIP_QUERY, limit)
    print(f"Gmail: {len(msg_ids)} payslip message(s) matched")

    for mid in msg_ids:
        atts = [a for a in gc.list_attachments(creds, mid)
                if a["filename"].lower().endswith(".pdf")]
        if not atts:
            print(f"  ! {mid}: no PDF attachment, skipped")
            continue
        raw = gc.get_attachment(creds, mid, atts[0]["attachment_id"])
        try:
            rec = parse_payslip(_pdf_text(raw))
        except PayslipParseError as exc:
            print(f"  ! {mid}: {exc}")
            continue
        out = dest / f"payslip-{rec.period_end}.pdf"
        if out.exists():
            continue                      # idempotent: already have this period
        out.write_bytes(raw)
        print(f"  + {out.name}  ({len(raw):,} bytes)")

    return sorted(dest.glob("payslip-*.pdf"))


# ── render ────────────────────────────────────────────────────────────────────

HEADER = """---
tags:
  - finance
  - income
  - payslip
type: income-log
lifecycle: living
---

# Income Log

> Derived from payslip PDFs in `payslips/` by `scripts/payslip_sync.py`.
> **Source of truth is the PDF** — if a figure here looks wrong, open the payslip.
> Regenerate: `python scripts/payslip_sync.py --reparse`

"""


def _fmt(records: list[PayslipRecord]) -> str:
    rows = [
        "| Period end | Pay date | Grade | Rate | Hrs | Gross | Tax | Net | Super | YTD gross |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(records, key=lambda x: x.period_end, reverse=True):
        rows.append(
            f"| {r.period_end} | {r.pay_date} | {r.classification} | "
            f"${r.hourly_rate:,.2f} | {r.base_hours:g} | ${r.gross:,.2f} | "
            f"${r.tax:,.2f} | ${r.net:,.2f} | ${r.super_total:,.2f} | ${r.ytd_gross:,.2f} |"
        )

    body = HEADER + "\n".join(rows) + "\n"

    if records:
        latest = max(records, key=lambda x: x.period_end)
        body += f"""
## Current position (as at {latest.period_end})

| Metric | Value |
|---|---|
| Classification | {latest.classification} |
| Hourly rate | ${latest.hourly_rate:,.2f} |
| Fortnightly gross | ${latest.gross:,.2f} |
| Fortnightly net | ${latest.net:,.2f} |
| **Annualised base** (rate x 1950 hrs) | **${latest.annualised_base:,.2f}** |
| **Annualised net** (net x 26) | **${latest.annualised_net:,.2f}** |
| Super this pay | ${latest.super_total:,.2f} |
"""
        if latest.leave:
            body += "\n### Leave balances (hours)\n\n| Type | Balance |\n|---|---|\n"
            for k, v in latest.leave.items():
                body += f"| {k.replace('_', ' ').title()} | {v:g} |\n"

        rates = sorted({(r.period_end, r.hourly_rate, r.classification) for r in records})
        changes = [rates[0]] + [
            cur for prev, cur in zip(rates, rates[1:]) if cur[1] != prev[1]
        ]
        if len(changes) > 1:
            body += "\n## Rate history\n\n| Effective (period end) | Rate | Grade | Annualised |\n|---|---|---|---|\n"
            for pe, rate, grade in changes:
                body += f"| {pe} | ${rate:,.2f} | {grade} | ${rate * 1950:,.2f} |\n"

    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="fetch every payslip, not just the latest")
    ap.add_argument("--limit", type=int, default=0, help="cap messages fetched (0 = default)")
    ap.add_argument("--reparse", action="store_true", help="skip Gmail; re-read local PDFs only")
    args = ap.parse_args()

    vault = _vault_root()
    dest = vault / VAULT_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)

    if args.reparse:
        pdfs = sorted(dest.glob("payslip-*.pdf"))
        print(f"Reparse: {len(pdfs)} local PDF(s)")
    else:
        limit = args.limit or (100 if args.backfill else 1)
        pdfs = fetch_payslips(limit, dest)

    records, failed = [], []
    for p in pdfs:
        try:
            records.append(parse_payslip(_pdf_text(p.read_bytes())))
        except PayslipParseError as exc:
            failed.append((p.name, str(exc)))

    if failed:
        print(f"\n{len(failed)} PDF(s) failed to parse:")
        for name, err in failed:
            print(f"  ! {name}: {err}")

    if not records:
        print("No payslips parsed — nothing written.")
        return 1

    (vault / LOG_PATH).write_text(_fmt(records), encoding="utf-8")
    latest = max(records, key=lambda x: x.period_end)
    print(f"\nWrote {LOG_PATH}: {len(records)} pay period(s)")
    print(f"Latest: {latest.period_end}  {latest.classification}  "
          f"${latest.hourly_rate:,.2f}/hr  ->  base ${latest.annualised_base:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

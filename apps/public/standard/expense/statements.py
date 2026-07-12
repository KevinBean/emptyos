"""Expense — bank-statement CSV import with column mapping, dedupe, and a gate.

The old ``POST /api/import`` pasted a CSV whose columns were already named the
way EmptyOS names them, wrote every row immediately, and deduped nothing — so a
real bank export (``Date``/``Narrative``/``Debit Amount``, DD/MM/YYYY dates,
signed amounts) couldn't round-trip. This module is the upgrade: sniff the
bank's columns, normalise each row, classify new-vs-already-imported, and hand
the whole batch to a **preview** the user reviews and edits before a **confirm**
writes anything.

It composes what already existed — ``csv_to_rows`` (SDK), ``detect_category``
(categories.py), and ``self.add`` (the locked, event-emitting write primitive).
Nothing new is invented; the import is propose→preview→confirm over existing
parts (`.claude/rules/proposed-action.md`, impact-shaped).

**Scope:** CSV only. OFX/QIF need an SGML parser that exists nowhere in the
repo — a separate build, not this composition. Noted as a follow-up.

Pure helpers are module-level (unit-tested in
``tests/test_unit_expense_statements.py``); the two routes are bound onto
``ExpenseApp`` in app.py.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import csv_to_rows

from .categories import detect_category

if TYPE_CHECKING:
    from .app import ExpenseApp  # noqa: F401 — for type hints only


# ─── Bind to ExpenseApp class as ─────────────────────────────────────
#   api_import_preview = _statements.api_import_preview   # POST /api/import/preview
#   api_import_confirm = _statements.api_import_confirm   # POST /api/import/confirm
# Adding a route here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


MAX_IMPORT_BYTES = 10 * 1024 * 1024  # a statement CSV is never 10 MB
MAX_IMPORT_ROWS = 5000  # a sane ceiling on one import

# Canonical field → header variants a bank might use (lowercased, checked as
# substrings so "Debit Amount (AUD)" still matches "debit"). Order matters:
# more specific variants first.
_FIELD_ALIASES: dict[str, list[str]] = {
    "date": ["transaction date", "posting date", "processed date", "date"],
    "debit": ["debit amount", "withdrawal", "money out", "debit", "paid out"],
    "credit": ["credit amount", "deposit", "money in", "credit", "paid in"],
    "amount": ["amount", "value"],
    "description": [
        "description", "narrative", "transaction details", "details",
        "particulars", "reference", "memo", "transaction", "payee",
    ],
}

# Date formats tried in order — AU DD/MM/YYYY first (this is an AU-first tool),
# ISO next, US MM/DD/YYYY last so an ambiguous "03/04" reads as 3 April.
_DATE_FORMATS = [
    "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y",
    "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%Y/%m/%d", "%m/%d/%Y",
]


def sniff_columns(headers: list[str]) -> dict[str, str]:
    """Best-effort map of canonical field → the actual header that carries it.

    Returns a dict with any of ``date``/``amount``/``debit``/``credit``/
    ``description`` that matched. A header is claimed by the first canonical
    field whose alias it contains, so one column can't fill two roles.
    """
    normalized = [(h, (h or "").strip().lower()) for h in headers]
    claimed: set[str] = set()
    mapping: dict[str, str] = {}
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            hit = next(
                (h for h, low in normalized if h not in claimed and alias in low),
                None,
            )
            if hit is not None:
                mapping[field] = hit
                claimed.add(hit)
                break
    return mapping


def parse_amount(raw: str) -> float | None:
    """Parse a money cell → signed float. Negative = an outgoing expense.

    Handles ``$``, thousands separators, ``(12.30)`` parentheses-negative, and
    trailing ``DR``/``CR`` (DR = debit → negative, CR = credit → positive).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    sign = 1.0
    low = s.lower()
    if low.endswith("dr"):
        sign, s = -1.0, s[:-2].strip()
    elif low.endswith("cr"):
        sign, s = 1.0, s[:-2].strip()
    if s.startswith("(") and s.endswith(")"):
        sign, s = -1.0 * sign, s[1:-1].strip()
    s = re.sub(r"[^\d.\-]", "", s)  # drop $, commas, spaces, letters
    if s in ("", "-", ".", "-."):
        return None
    try:
        return round(sign * float(s), 2)
    except ValueError:
        return None


def parse_date(raw: str) -> str | None:
    """Parse a date cell → ISO ``YYYY-MM-DD``, trying AU DD/MM/YYYY first."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # An ISO datetime ("2026-07-10 00:00:00" / OFX-ish) → keep the date head.
    head = s.split("T")[0].split(" ")[0]
    for candidate in (s, head):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def normalize_desc(s: str) -> str:
    """Fold a description for dedupe: lowercase, collapse whitespace, and drop
    trailing reference/card digit runs that vary between exports of the same
    transaction."""
    t = (s or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\b(?:x{2,}\d+|\d{6,})\b", "", t)  # masked card / long ref numbers
    return re.sub(r"\s+", " ", t).strip()


def dedupe_key(date: str, amount: float, description: str) -> str:
    """Stable key for "already imported?". Mirrors app._expense_id's shape but
    normalises the description so two exports of one transaction collapse."""
    return f"{date}|{float(amount):.2f}|{normalize_desc(description)}"


def _row_amount(row: dict, mapping: dict[str, str], *, signed_file: bool) -> float | None:
    """Resolve a row's expense magnitude (positive) — or None if it's income.

    Prefers an explicit debit column; else the amount column. ``signed_file``
    (computed once over the whole file) decides how a positive amount reads:
    in a signed file positives are income (skip), in an all-positive file every
    amount is an expense.
    """
    if "debit" in mapping:
        debit = parse_amount(row.get(mapping["debit"], ""))
        if debit is not None and debit != 0:
            return abs(debit)
        # A debit column present but empty → this row is a credit; skip.
        if "credit" in mapping:
            return None
    if "amount" in mapping:
        amt = parse_amount(row.get(mapping["amount"], ""))
        if amt is None or amt == 0:
            return None
        if amt < 0:
            return abs(amt)
        # Positive: an expense only when the whole file has no negatives.
        return None if signed_file else amt
    return None


def parse_statement(csv_text: str) -> dict:
    """CSV text → ``{rows, columns, mapping, warnings, skipped}``.

    ``rows`` are ``{date, amount, description}`` for outgoing expenses only.
    ``skipped`` counts income/credit and unparseable rows. Never raises.
    """
    warnings: list[str] = []
    try:
        raw_rows = csv_to_rows(csv_text)
    except Exception:  # noqa: BLE001 — a malformed CSV is a warning, not a crash
        return {"rows": [], "columns": [], "mapping": {}, "warnings": ["couldn't parse the CSV"], "skipped": 0}

    if not raw_rows:
        return {"rows": [], "columns": [], "mapping": {}, "warnings": ["no rows found"], "skipped": 0}

    columns = list(raw_rows[0].keys())
    mapping = sniff_columns(columns)

    for need, label in (("date", "date"), ("description", "description")):
        if need not in mapping:
            warnings.append(f"couldn't find a {label} column")
    if "amount" not in mapping and "debit" not in mapping:
        warnings.append("couldn't find an amount or debit column")

    # A signed amount column (any negative present) means positives are income;
    # an all-positive amount column means every row is an expense. Decide once
    # over the whole file so one salary row can't be mistaken for a purchase.
    signed_file = False
    if "amount" in mapping and "debit" not in mapping:
        signed_file = any(
            (parse_amount(r.get(mapping["amount"], "")) or 0) < 0 for r in raw_rows
        )

    rows: list[dict] = []
    skipped = 0
    for raw in raw_rows[:MAX_IMPORT_ROWS]:
        date = parse_date(raw.get(mapping.get("date", ""), "")) if "date" in mapping else None
        amount = _row_amount(raw, mapping, signed_file=signed_file)
        desc = str(raw.get(mapping.get("description", ""), "")).strip() if "description" in mapping else ""
        if not date or amount is None or amount <= 0:
            skipped += 1
            continue
        rows.append({"date": date, "amount": amount, "description": desc or "(no description)"})

    if len(raw_rows) > MAX_IMPORT_ROWS:
        warnings.append(f"only the first {MAX_IMPORT_ROWS} rows were read")

    return {"rows": rows, "columns": columns, "mapping": mapping, "warnings": warnings, "skipped": skipped}


# ─── Routes (bound onto ExpenseApp) ──────────────────────────────────


async def _existing_keys(self, dates: list[str]) -> set[str]:
    """Dedupe set over every year touched by the incoming rows."""
    years = {d[:4] for d in dates if d}
    keys: set[str] = set()
    for y in years:
        try:
            for e in await self.list_expenses(year=int(y)):
                keys.add(dedupe_key(e["date"], e["amount"], e["description"]))
        except (ValueError, KeyError):
            continue
    return keys


async def _read_import_payload(self, request) -> tuple[str, str]:
    """Return (csv_text, error). Accepts a multipart file or JSON csv_content."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return "", "no file uploaded"
        data = await upload.read()
        if not data:
            return "", "the file is empty"
        if len(data) > MAX_IMPORT_BYTES:
            return "", "file is too large"
        return data.decode("utf-8-sig", "replace"), ""
    body = await request.json()
    text = body.get("csv_content", "")
    return (text, "") if text else ("", "csv_content required")


@web_route("POST", "/api/import/preview")
async def api_import_preview(self, request):
    """Parse a statement and classify each row new-vs-duplicate. Writes nothing.

    Returns ``{rows, columns, mapping, summary, warnings}`` where each row is
    ``{date, amount, description, category, duplicate}``.
    """
    csv_text, err = await _read_import_payload(self, request)
    if err:
        return {"error": err}

    parsed = parse_statement(csv_text)
    rows = parsed["rows"]
    if not rows:
        return {
            "error": "no importable transactions found",
            "warnings": parsed["warnings"],
            "columns": parsed["columns"],
            "mapping": parsed["mapping"],
        }

    default_cat = self.setting("expense.default_category", "Other")
    existing = await _existing_keys(self, [r["date"] for r in rows])

    seen_in_batch: set[str] = set()
    out_rows = []
    new_count = 0
    by_category: dict[str, float] = {}
    for r in rows:
        key = dedupe_key(r["date"], r["amount"], r["description"])
        duplicate = key in existing or key in seen_in_batch
        seen_in_batch.add(key)
        category = detect_category(r["description"]) or default_cat
        if not duplicate:
            new_count += 1
            by_category[category] = round(by_category.get(category, 0) + r["amount"], 2)
        out_rows.append({**r, "category": category, "duplicate": duplicate})

    dates = sorted(r["date"] for r in rows)
    return {
        "rows": out_rows,
        "columns": parsed["columns"],
        "mapping": parsed["mapping"],
        "warnings": parsed["warnings"],
        "summary": {
            "total": len(out_rows),
            "new": new_count,
            "duplicate": len(out_rows) - new_count,
            "skipped": parsed["skipped"],
            "date_range": [dates[0], dates[-1]] if dates else [],
            "by_category": by_category,
        },
    }


@web_route("POST", "/api/import/confirm")
async def api_import_confirm(self, request):
    """Write the reviewed rows. Body: ``{rows: [{date, amount, description,
    category}]}``. Re-dedupes against the current vault (the source of truth may
    have grown since preview), so a stale confirm can never double-write.
    """
    body = await request.json()
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return {"error": "rows required"}
    if len(rows) > MAX_IMPORT_ROWS:
        return {"error": "too many rows"}

    default_cat = self.setting("expense.default_category", "Other")
    existing = await _existing_keys(self, [str(r.get("date", "")) for r in rows if isinstance(r, dict)])

    imported = 0
    skipped = 0
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            skipped += 1
            continue
        date = parse_date(r.get("date", "")) or str(r.get("date", ""))
        amount = parse_amount(str(r.get("amount", "")))
        desc = str(r.get("description", "")).strip() or "(no description)"
        if not date or amount is None or amount <= 0:
            skipped += 1
            continue
        amount = abs(amount)
        key = dedupe_key(date, amount, desc)
        if key in existing or key in seen:
            skipped += 1
            continue
        seen.add(key)
        category = str(r.get("category", "")).strip() or detect_category(desc) or default_cat
        await self.add(amount, desc, category, entry_date=date)
        imported += 1

    return {"ok": True, "imported": imported, "skipped": skipped}

"""Payslip text → structured record. Pure: no I/O, no kernel, no network.

Parses the text layer of a label-anchored payslip PDF — the common Australian
agency/employer layout, stable across FY2024-25 → FY2026-27. Which employer's
payslip this targets is per-machine config, not a fact about the codebase
(CLAUDE.md rule 13).
Extraction of that text is the caller's job (pypdf); this module only ever sees
a `str`, which is what makes it unit-testable without a daemon or a mailbox.

RULE 19 NOTE: this parser is deterministic regex. Payslip text must never be
handed to a model — the whole point of parsing locally is that salary data
stays on the machine. Do not add an LLM fallback for unmatched fields; raise
PayslipParseError instead and fix the pattern.

FAIL LOUD: a payslip drives housing/retirement decisions, so a field that does
not match raises rather than defaulting. A silently-wrong salary is worse than
a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

# Standard hours per fortnight × fortnights per year → annualisation basis.
STD_HOURS_PER_FORTNIGHT = 75.0
FORTNIGHTS_PER_YEAR = 26
STD_HOURS_PER_YEAR = 1950.0


class PayslipParseError(ValueError):
    """A required field was absent or unparseable. Never swallowed."""


@dataclass
class PayslipRecord:
    period_start: str            # ISO yyyy-mm-dd
    period_end: str              # ISO yyyy-mm-dd
    pay_date: str                # ISO yyyy-mm-dd
    classification: str          # e.g. "GRD07 06"
    hourly_rate: float
    base_hours: float          # hours paid at the base rate (leave+ordinary+PH)
    gross: float
    net: float
    tax: float
    super_total: float
    ytd_gross: float
    ytd_tax: float
    ytd_super: float
    annualised_base: float       # hourly_rate × 1950
    annualised_net: float        # net × 26
    leave: dict = field(default_factory=dict)   # AL / SICK / LSL / FLEX balances

    def as_dict(self) -> dict:
        return asdict(self)


# ── helpers ───────────────────────────────────────────────────────────────────

_NUM = r"-?[\d,]+\.?\d*"


def _num(s: str) -> float:
    """'5,956.30' → 5956.30 ; '-1,596.00' → -1596.0"""
    return float(s.replace(",", "").strip())


def _iso(dmy: str) -> str:
    """'03.07.2026' → '2026-07-03'"""
    d, m, y = dmy.split(".")
    return f"{y}-{m}-{d}"


_FLAGS = re.IGNORECASE | re.MULTILINE   # ^ must anchor per LINE, not per string


def _find(text: str, pattern: str, label: str, group: int = 1) -> str:
    m = re.search(pattern, text, _FLAGS)
    if not m:
        raise PayslipParseError(
            f"could not find {label!r} — payslip layout may have changed; "
            f"fix the pattern in emptyos/sdk/payslip.py rather than guessing"
        )
    return m.group(group)


def _find_opt(text: str, pattern: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, _FLAGS)
    return m.group(group) if m else None


def _earnings_rows(text: str) -> list[tuple[float, float, float]]:
    """(hours, rate, amount) for every earnings line in the Description block.

    Rows are shaped '<description> [X] <hours> <rate> <amount>', but the
    description itself can contain numbers ('AL Loading 17.50%',
    'LVE Loading Env 1 (Hrs/%)'), so the last three numeric tokens on the line
    are the data and anything earlier is prose.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.lower().startswith("description"))
    except StopIteration:
        raise PayslipParseError("no 'Description' earnings header found")
    end = next((i for i, l in enumerate(lines)
                if l.lower().startswith("gross this pay")), len(lines))

    rows: list[tuple[float, float, float]] = []
    for line in lines[start + 1:end]:
        nums = re.findall(_NUM, line)
        if len(nums) < 3:
            continue
        hours, rate, amount = (_num(n) for n in nums[-3:])
        if rate > 0:                     # rate 0 => accrual marker, not earnings
            rows.append((hours, rate, amount))
    return rows


def _base_rate_and_hours(text: str) -> tuple[float, float]:
    """Base hourly rate = the MODAL rate across earnings rows; hours = sum at it.

    Modal rather than max/first because a fortnight need not contain ordinary
    pay at all (the Christmas periods are entirely leave + public holidays), and
    because loading rows carry their own lower rate (17.5% AL loading) while a
    future penalty row could carry a higher one. The rate paid on the most rows
    is the substantive one in every layout seen 2024-2026.
    """
    rows = _earnings_rows(text)
    if not rows:
        raise PayslipParseError("no earnings rows with a positive rate found")

    tally: dict[float, list[int | float]] = {}
    for hours, rate, amount in rows:
        slot = tally.setdefault(rate, [0, 0.0])
        slot[0] += 1                     # row count -> the mode
        slot[1] += amount                # total -> tie-break
    rate = max(tally, key=lambda r: (tally[r][0], tally[r][1]))
    hours = sum(h for h, r, _ in rows if r == rate)
    return rate, hours


# ── the parser ────────────────────────────────────────────────────────────────


def parse_payslip(text: str) -> PayslipRecord:
    """Parse payslip text into a PayslipRecord. Raises PayslipParseError."""
    if not text or len(text) < 200:
        raise PayslipParseError(f"text too short to be a payslip ({len(text or '')} chars)")

    period_start = _iso(_find(text, r"^PERIOD\s+(\d{2}\.\d{2}\.\d{4})", "PERIOD"))
    period_end = _iso(_find(text, r"^TO\s+(\d{2}\.\d{2}\.\d{4})", "TO (period end)"))
    pay_date = _iso(_find(text, r"^PAY DATE\s+(\d{2}\.\d{2}\.\d{4})", "PAY DATE"))
    classification = _find(text, r"^CLASSIFICATION\s+(.+?)\s*$", "CLASSIFICATION").strip()

    hourly_rate, base_hours = _base_rate_and_hours(text)

    gross = _num(_find(text, rf"Gross This Pay\s+({_NUM})", "Gross This Pay"))
    net = _num(_find(text, rf"Total Net Pay\s+({_NUM})", "Total Net Pay"))

    # "Full Income tax  -1,596.00  -1,596.00"  → this-pay, YTD (both negative)
    tax_m = re.search(rf"Full Income tax\s+({_NUM})\s+({_NUM})", text, _FLAGS)
    if not tax_m:
        raise PayslipParseError("no 'Full Income tax' line found")
    tax, ytd_tax = abs(_num(tax_m.group(1))), abs(_num(tax_m.group(2)))

    # YTD block: "Total Gross <ytd>" sits under "YTD Details".
    ytd_gross = _num(_find(text, rf"Total Gross\s+({_NUM})", "Total Gross (YTD)"))

    # Super: one or more "UniSuper <amount> <ytd>" rows (12% SG + 1% extra).
    super_rows = re.findall(rf"UniSuper\s+({_NUM})\s+({_NUM})", text, _FLAGS)
    if not super_rows:
        raise PayslipParseError("no 'UniSuper' contribution line found")
    super_total = sum(_num(a) for a, _ in super_rows)
    ytd_super = sum(_num(y) for _, y in super_rows)

    leave: dict = {}
    for key, pat in (
        ("annual_leave", rf"^AL\s+({_NUM})\s+({_NUM})\s+({_NUM})"),
        ("long_service", rf"^LSL\s+({_NUM})\s+({_NUM})\s+({_NUM})"),
        ("sick", rf"^SICK\s+({_NUM})\s+({_NUM})\s+({_NUM})"),
    ):
        m = re.search(pat, text, _FLAGS)
        if m:
            leave[key] = _num(m.group(3))     # balance column
    flex = _find_opt(text, rf"^FLEX\s+({_NUM})")
    if flex is not None:
        leave["flex"] = _num(flex)

    return PayslipRecord(
        period_start=period_start,
        period_end=period_end,
        pay_date=pay_date,
        classification=classification,
        hourly_rate=hourly_rate,
        base_hours=base_hours,
        gross=gross,
        net=net,
        tax=tax,
        super_total=super_total,
        ytd_gross=ytd_gross,
        ytd_tax=ytd_tax,
        ytd_super=ytd_super,
        annualised_base=round(hourly_rate * STD_HOURS_PER_YEAR, 2),
        annualised_net=round(net * FORTNIGHTS_PER_YEAR, 2),
        leave=leave,
    )

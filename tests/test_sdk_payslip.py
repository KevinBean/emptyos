"""Unit tests for emptyos/sdk/payslip.py — pure, no daemon, no mailbox.

Fixtures are REDACTED payslip layouts: real structure, invented figures. Never
commit a real payslip (the vault gitignores *.pdf for the same reason).
"""

import pytest

from emptyos.sdk.payslip import PayslipParseError, parse_payslip

# Layout as of FY2026-27 (GRD07 06). Figures invented.
RECENT = """ABN 25090664878
NAME Mr Test User
PERS NUM. 01000000
PERIOD 20.06.2026
PAY DATE 08.07.2026
LOCATION Station Place
TO 03.07.2026
CLASSIFICATION GRD07 06
Description Retro Hours Rate Amount
Personal Leave FP X 0.5     80.00 40.00
Ordinry Pay X -0.5     80.00 -40.00
Ordinry Pay 75     80.00 6,000.00
Flex accrued 1      0.00 0
Gross This Pay 6,000.00
Adj This Pay 0
YTD Details  YTD
Total Gross 6,000.00
Taxable Gross 6,000.00
Company Super Contributions Amount YTD
UniSuper 720.00 720.00
UniSuper 60.00 60.00
Pre Tax Deduction Retro Amount YTD
Post Tax Deduction Retro Amount YTD
Full Income tax -1,600.00 -1,600.00
Bank Transfers Amount
CBA Adelaide         4,400.00
Total Net Pay         4,400.00
Absences Working Days
Mo 22.06 Std Hrs 7.5
Leave Entitlement Planned Balance
LSL 0 0 0
AL 160.00 37.5 122.50
SICK 71.5 0 71.5
Hours Balances Hours
EQLX 0
FLEX 25.5
TIL 0
"""

# Layout as of FY2024-25 (GRD07 04) — proves the parser isn't overfit.
OLD = """ABN 25090664878
NAME Mr Test User
PERS NUM. 01000000
PERIOD 07.12.2024
PAY DATE 25.12.2024
LOCATION Station Place
TO 20.12.2024
CLASSIFICATION GRD07 04
Description Retro Hours Rate Amount
Flex accrued X 3.5      0.00 0
Personal Leave FP 7.5     70.00 525.00
Ordinry Pay 67.5     70.00 4,725.00
Flex accrued 1.5      0.00 0
Gross This Pay 5,250.00
Adj This Pay 0
YTD Details  YTD
Total Gross 26,250.00
Taxable Gross 26,250.00
Company Super Contributions Amount YTD
UniSuper 600.00 3,000.00
UniSuper 52.50 262.50
Pre Tax Deduction Retro Amount YTD
Post Tax Deduction Retro Amount YTD
Full Income tax -1,400.00 -7,000.00
Bank Transfers Amount
CBA Adelaide         3,850.00
Total Net Pay         3,850.00
Absences
Personal Leave - Paid 09.12.2024 to 09.12.2024
Working Days
Tu 10.12 Std Hrs 7.5
"""


class TestParseRecent:
    def test_dates_iso(self):
        r = parse_payslip(RECENT)
        assert r.period_start == "2026-06-20"
        assert r.period_end == "2026-07-03"
        assert r.pay_date == "2026-07-08"

    def test_core_money(self):
        r = parse_payslip(RECENT)
        assert r.gross == 6000.00
        assert r.net == 4400.00
        assert r.tax == 1600.00          # sign normalised to positive

    def test_rate_picks_substantive_row_not_adjustment(self):
        """Two 'Ordinry Pay' rows exist; the -0.5h adjustment must not win."""
        r = parse_payslip(RECENT)
        assert r.hourly_rate == 80.00
        assert r.base_hours == 75.0

    def test_super_sums_both_components(self):
        r = parse_payslip(RECENT)
        assert r.super_total == 780.00   # 720 (12%) + 60 (1%)
        assert r.ytd_super == 780.00

    def test_annualisation(self):
        r = parse_payslip(RECENT)
        assert r.annualised_base == 156000.00   # 80.00 × 1950
        assert r.annualised_net == 114400.00    # 4400 × 26

    def test_leave_balances(self):
        r = parse_payslip(RECENT)
        assert r.leave["annual_leave"] == 122.50
        assert r.leave["sick"] == 71.5
        assert r.leave["flex"] == 25.5


class TestParseOldLayout:
    def test_older_financial_year_still_parses(self):
        r = parse_payslip(OLD)
        assert r.classification == "GRD07 04"
        assert r.hourly_rate == 70.00
        assert r.gross == 5250.00
        assert r.net == 3850.00
        assert r.ytd_gross == 26250.00
        assert r.ytd_tax == 7000.00

    def test_absent_leave_block_is_not_fatal(self):
        """OLD has no Leave Entitlement block — optional fields stay optional."""
        r = parse_payslip(OLD)
        assert r.leave == {}


class TestFailLoud:
    def test_empty_raises(self):
        with pytest.raises(PayslipParseError):
            parse_payslip("")

    def test_truncated_raises(self):
        with pytest.raises(PayslipParseError):
            parse_payslip("PERIOD 20.06.2026")

    def test_missing_required_field_raises_not_defaults(self):
        broken = RECENT.replace("Total Net Pay         4,400.00", "")
        with pytest.raises(PayslipParseError, match="Total Net Pay"):
            parse_payslip(broken)

    def test_no_earnings_rows_raises(self):
        """Strip every priced row — nothing left to infer a rate from."""
        broken = "\n".join(
            l for l in RECENT.splitlines()
            if not l.startswith(("Ordinry Pay", "Personal Leave"))
        )
        with pytest.raises(PayslipParseError, match="earnings rows"):
            parse_payslip(broken)


# Christmas/New-Year fortnight: entirely leave + flexi + public holidays, with
# NO 'Ordinry Pay' row at all. Two real payslips (Jan 2025, Jan 2026) have this
# shape and the first parser rejected both. Figures invented; structure real.
LEAVE_ONLY = """ABN 25090664878
NAME Mr Test User
PERS NUM. 01000000
PERIOD 20.12.2025
PAY DATE 07.01.2026
LOCATION Station Place
TO 02.01.2026
CLASSIFICATION GRD07 04
Description Retro Hours Rate Amount
Flexi Taken 30     78.00 2,340.00
Annual Lve 22.5     78.00 1,755.00
AL Loading 17.50% 22.5     13.65 307.13
Public Hol 22.5     78.00 1,755.00
Gross This Pay 6,157.13
Adj This Pay 0
YTD Details  YTD
Total Gross 60,000.00
Taxable Gross 60,000.00
Company Super Contributions Amount YTD
UniSuper 738.86 7,200.00
UniSuper 61.57 600.00
Pre Tax Deduction Retro Amount YTD
Post Tax Deduction Retro Amount YTD
Full Income tax -1,700.00 -16,000.00
Bank Transfers Amount
CBA Adelaide         4,457.13
Total Net Pay         4,457.13
"""


class TestLeaveOnlyFortnight:
    """Regression: the two Christmas payslips must parse, not raise."""

    def test_parses_without_ordinary_pay(self):
        r = parse_payslip(LEAVE_ONLY)
        assert r.period_end == "2026-01-02"
        assert r.gross == 6157.13

    def test_modal_rate_ignores_loading_row(self):
        """78.00 appears on 3 rows; the 17.5% loading rate 13.65 on 1."""
        r = parse_payslip(LEAVE_ONLY)
        assert r.hourly_rate == 78.00

    def test_base_hours_sums_rows_at_base_rate(self):
        """30 flexi + 22.5 AL + 22.5 PH = 75, the standard fortnight."""
        r = parse_payslip(LEAVE_ONLY)
        assert r.base_hours == 75.0

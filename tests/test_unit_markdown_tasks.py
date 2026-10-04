"""Unit tests for the canonical checkbox-mutation primitives.

Promoted from ``apps/public/core/task/mutations.py`` to
``emptyos/sdk/markdown_tasks.py`` (2026-07-03) once ``apps/public/standard/projects``
became a second consumer. ``tests/test_unit_task_mutations.py`` already covers
the app-side re-export shim end-to-end; this file exercises the canonical
module directly and the toggle/staleness-guard shape both callers rely on.
"""

from datetime import date

from emptyos.sdk import markdown_tasks as mt


def test_toggle_open_to_done_stamps_completion_date():
    new_line, action = mt.toggle("- [ ] write review", "2026-07-03")
    assert action == "completed"
    assert new_line == "- [ ] write review".replace("[ ]", "[x]") + " ✅ 2026-07-03"


def test_toggle_done_to_open_strips_completion_date():
    new_line, action = mt.toggle("- [x] write review ✅ 2026-07-03", "2026-07-04")
    assert action == "reopened"
    assert new_line == "- [ ] write review"


def test_toggle_non_checkbox_line_returns_none():
    assert mt.toggle("just a prose line", "2026-07-03") == (None, None)


def test_text_matches_guards_a_stale_line():
    line = "- [ ] write review 📅 2026-06-14"
    # Correct text — guard passes.
    assert mt.text_matches(line, "write review")
    # A line that shifted under a stale (file, line) address — guard refuses.
    assert not mt.text_matches(line, "an unrelated task")
    # Empty expected text means "caller opted out" — never a safe identity
    # check on its own, but callers gate on non-empty before relying on this.
    assert mt.text_matches(line, "")


# ── priority ─────────────────────────────────────────────────────────────────


def test_set_priority_adds_marker_before_due():
    line = "- [ ] pay rent 📅 2026-07-10"
    out = mt.set_priority(line, "high")
    assert out == "- [ ] pay rent ⏫ 📅 2026-07-10"
    assert mt.priority_level(out) == "high"


def test_set_priority_replaces_existing_and_is_idempotent():
    line = "- [ ] pay rent 🔽"
    out = mt.set_priority(line, "highest")
    assert out == "- [ ] pay rent 🔺"
    # Re-applying the same level does not stack markers.
    assert mt.set_priority(out, "highest") == out


def test_set_priority_empty_clears_marker():
    assert mt.set_priority("- [ ] pay rent ⏫", "") == "- [ ] pay rent"
    assert mt.priority_level("- [ ] pay rent") == ""


def test_set_priority_non_checkbox_returns_none():
    assert mt.set_priority("just prose", "high") is None


# ── recurrence ───────────────────────────────────────────────────────────────


def test_next_occurrence_intervals():
    base = date(2026, 7, 3)
    assert mt.next_occurrence("daily", base) == date(2026, 7, 4)
    assert mt.next_occurrence("weekly", base) == date(2026, 7, 10)
    assert mt.next_occurrence("biweekly", base) == date(2026, 7, 17)
    assert mt.next_occurrence("monthly", base) == date(2026, 8, 3)
    assert mt.next_occurrence("yearly", base) == date(2027, 7, 3)
    assert mt.next_occurrence("never", base) is None


def test_next_occurrence_month_end_clamps():
    # Jan 31 + 1 month → Feb 28 (2026 is not a leap year), not an invalid date.
    assert mt.next_occurrence("monthly", date(2026, 1, 31)) == date(2026, 2, 28)


def test_regenerate_recurring_rolls_from_existing_due():
    line = "- [x] water plants 🔁 weekly 📅 2026-07-03 ✅ 2026-07-05"
    out = mt.regenerate_recurring(line, date(2026, 7, 5))
    # Fresh OPEN line, keeps 🔁, no ✅, due rolled from the 📅 (not the completion).
    assert out == "- [ ] water plants 🔁 weekly 📅 2026-07-10"


def test_regenerate_recurring_rolls_from_completion_when_no_due():
    line = "- [ ] standup 🔁 daily"
    out = mt.regenerate_recurring(line, date(2026, 7, 3))
    assert out == "- [ ] standup 🔁 daily 📅 2026-07-04"


def test_regenerate_recurring_non_recurring_returns_none():
    assert mt.regenerate_recurring("- [x] one-off task ✅ 2026-07-03", date(2026, 7, 3)) is None


# ── set_due across both due syntaxes the parser reads ───────────────────────
# Regression: set_due only knew the canonical 📅 form, so snoozing a task
# written with the inline `due:` syntax APPENDED a 📅 instead of updating in
# place, leaving two contradictory dates on one line — the human reads the
# stale one, the app uses the new one.

def test_set_due_replaces_inline_due_syntax_in_place():
    out = mt.set_due("- [ ] pay rent due:2026-05-10", "2026-06-03")
    assert out == "- [ ] pay rent due:2026-06-03"
    assert "📅" not in out  # must not append a second, contradictory marker


def test_set_due_replaces_inline_due_mid_text():
    out = mt.set_due("- [ ] pay rent due:2026-05-10 before weekend", "2026-06-03")
    assert out == "- [ ] pay rent due:2026-06-03 before weekend"


def test_set_due_still_replaces_canonical_marker():
    out = mt.set_due("- [ ] call depot 📅 2026-05-10", "2026-06-03")
    assert out == "- [ ] call depot 📅 2026-06-03"


def test_set_due_appends_canonical_marker_when_line_has_none():
    assert mt.set_due("- [ ] no date", "2026-06-03") == "- [ ] no date 📅 2026-06-03"


def test_set_due_empty_strips_both_syntaxes():
    assert mt.set_due("- [ ] pay rent due:2026-05-10", "") == "- [ ] pay rent"
    assert mt.set_due("- [ ] call depot 📅 2026-05-10", "") == "- [ ] call depot"


def test_snooze_on_inline_due_does_not_duplicate_the_date():
    out = mt.snooze("- [ ] pay rent due:2026-05-10", 24, date(2026, 5, 10))
    assert out == "- [ ] pay rent due:2026-06-03"
    # the stale date must be gone, not merely shadowed by a new marker
    assert "2026-05-10" not in out


def test_set_due_keeps_body_matchable_for_line_lookup():
    """mutations locate the file line via substring match on the task text."""
    line = "- [ ] pay rent due:2026-05-10"
    assert mt.matches(mt.set_due(line, "2026-06-03"), "pay rent")

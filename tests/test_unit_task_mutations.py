from apps.task import mutations
from emptyos.sdk import TASK_RE


def test_completion_note_lines_are_child_markdown_not_tasks():
    lines = mutations.completion_note_lines("shipped the fix\n- [ ] follow-up stays prose")

    assert lines == [
        "  - Completion note: shipped the fix",
        "    > - [ ] follow-up stays prose",
    ]
    assert not any(TASK_RE.match(line.strip()) for line in lines)


def test_completion_note_lines_ignore_blank_input():
    assert mutations.completion_note_lines("  \n\t") == []


def test_text_matches_uses_indexed_task_text_exactly():
    line = "- [ ] write review 📅 2026-06-14"

    assert mutations.task_text(line) == "write review"
    assert mutations.text_matches(line, "write review")
    assert not mutations.text_matches(line, "other task")

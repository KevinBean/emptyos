"""Offline unit tests for the reactor's boards event listeners.

``test_unit_`` prefix -> runs offline per conftest (no daemon). The boards
PM round (comments/attachments/Planner import) added 5 new
``[provides.events] emits`` entries to boards' manifest with zero reactor
engagement, unlike their 11 sibling board:* events (created/column_*/
item_*/view_*) which already have ``_log_action`` observability wiring.
This pins the matching handlers added for the 5 new events.

Drives the async handlers with a fake ``self`` (no kernel) via
``asyncio.run``, same pattern as test_unit_reactor_journal_ripple.py.
"""

from __future__ import annotations

import asyncio

from apps.reactor.reactions_work import WorkReactionsMixin
from helpers import fake_event, fake_reactor


def test_comment_added_logs_only():
    log, ripple = [], []
    fake = fake_reactor(log, ripple)
    asyncio.run(
        WorkReactionsMixin.on_board_comment_added(
            fake, fake_event(board="b1", file="item.md")
        )
    )
    assert log == [("board:comment_added", "b1/item.md")]
    assert ripple == []


def test_comment_deleted_logs_only():
    log, ripple = [], []
    fake = fake_reactor(log, ripple)
    asyncio.run(
        WorkReactionsMixin.on_board_comment_deleted(
            fake, fake_event(board="b1", file="item.md")
        )
    )
    assert log == [("board:comment_deleted", "b1/item.md")]
    assert ripple == []


def test_attachment_added_logs_only():
    log, ripple = [], []
    fake = fake_reactor(log, ripple)
    asyncio.run(
        WorkReactionsMixin.on_board_attachment_added(
            fake, fake_event(board="b1", file="item.md", name="spec.pdf")
        )
    )
    assert log == [("board:attachment_added", "b1/item.md: spec.pdf")]
    assert ripple == []


def test_attachment_deleted_logs_only():
    log, ripple = [], []
    fake = fake_reactor(log, ripple)
    asyncio.run(
        WorkReactionsMixin.on_board_attachment_deleted(
            fake, fake_event(board="b1", file="item.md", name="spec.pdf")
        )
    )
    assert log == [("board:attachment_deleted", "b1/item.md: spec.pdf")]
    assert ripple == []


def test_planner_imported_logs_and_ripples_when_rows_changed():
    log, ripple = [], []
    fake = fake_reactor(log, ripple)
    asyncio.run(
        WorkReactionsMixin.on_board_planner_imported(
            fake, fake_event(board="proj-plan", created=3, updated=5, unchanged=10)
        )
    )
    assert log == [("board:planner_imported", "proj-plan: +3 ~5")]
    assert len(ripple) == 1
    emoji, text, dim = ripple[0]
    assert "proj-plan" in text and "3 new" in text and "5 updated" in text


def test_planner_imported_skips_ripple_when_nothing_changed():
    """A re-import with zero created/updated rows is a no-op sync check — not
    worth a journal breadcrumb, though it's still observable via _log_action."""
    log, ripple = [], []
    fake = fake_reactor(log, ripple)
    asyncio.run(
        WorkReactionsMixin.on_board_planner_imported(
            fake, fake_event(board="proj-plan", created=0, updated=0, unchanged=20)
        )
    )
    assert log == [("board:planner_imported", "proj-plan: +0 ~0")]
    assert ripple == []

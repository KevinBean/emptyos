"""System: worklog free-text search.

Search runs over ~1,500 short items with AND-over-terms substring matching —
no index, because substring beats stemming on strings like "12345" or "TB 908"
and the corpus is small enough that a real index would be machinery with no
payoff. These pin the contract, the filters, and the caps.
"""

from uuid import uuid4

import pytest
from helpers import TEST_PREFIX, WORKLOG_TEST_DATE, assert_dict_response, assert_ok


@pytest.fixture(scope="class")
def seeded(http_client):
    """One distinctive item + a plan on the sentinel day."""
    token = f"{TEST_PREFIX}zebra{uuid4().hex[:6]}"
    assert_ok(http_client.post("/worklog/api/log", json={
        "date": WORKLOG_TEST_DATE, "project": TEST_PREFIX + "SearchProj",
        "text": f"{token} calibrated the relay", "status": "blocked"}))
    assert_ok(http_client.post("/worklog/api/plan", json={
        "date": WORKLOG_TEST_DATE, "text": f"{token} plan prose for the day"}))
    return token


@pytest.mark.api
class TestWorklogSearch:
    def test_requires_a_query(self, http_client):
        assert assert_dict_response(http_client.get("/worklog/api/search?q=")).get("error")
        assert assert_dict_response(http_client.get("/worklog/api/search?q=%20%20")).get("error")

    def test_finds_an_item_and_reports_shape(self, http_client, seeded):
        d = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}"),
            required_keys=["query", "terms", "count", "truncated", "results"])
        assert d["count"] >= 1, d
        hit = next(r for r in d["results"] if r["kind"] == "item")
        assert {"date", "weekday", "employer", "kind", "project", "status", "text"} <= set(hit)
        assert seeded in hit["text"]
        assert hit["date"] == WORKLOG_TEST_DATE

    def test_searches_plan_prose_not_just_items(self, http_client, seeded):
        """The Plan is written on 80% of days and is often the only place a
        decision is recorded — items-only search would miss it."""
        d = assert_dict_response(http_client.get(f"/worklog/api/search?q={seeded}"))
        assert any(r["kind"] == "plan" for r in d["results"]), d["results"]

    def test_terms_are_anded_not_ored(self, http_client, seeded):
        both = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}+calibrated"))
        assert both["count"] >= 1
        # A term that appears nowhere alongside it must yield nothing.
        neither = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}+zzzznotpresent"))
        assert neither["count"] == 0, neither

    def test_case_insensitive(self, http_client, seeded):
        upper = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded.upper()}"))
        assert upper["count"] >= 1

    def test_project_name_is_part_of_the_haystack(self, http_client, seeded):
        """"searchproj relay" should match an item whose own text says only
        "…calibrated the relay"."""
        d = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={TEST_PREFIX}SearchProj+relay"))
        assert d["count"] >= 1, d

    def test_status_filter_narrows_and_excludes_prose(self, http_client, seeded):
        blocked = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}&status=blocked"))
        assert blocked["count"] >= 1
        assert all(r["kind"] == "item" for r in blocked["results"]), \
            "prose carries no status, so a status filter must exclude it"
        assert all(r["status"] == "blocked" for r in blocked["results"])
        none = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}&status=complete"))
        assert none["count"] == 0, none

    def test_date_range_filter(self, http_client, seeded):
        inside = assert_dict_response(http_client.get(
            f"/worklog/api/search?q={seeded}&from={WORKLOG_TEST_DATE}&to={WORKLOG_TEST_DATE}"))
        assert inside["count"] >= 1
        outside = assert_dict_response(http_client.get(
            f"/worklog/api/search?q={seeded}&from=2099-01-01"))
        assert outside["count"] == 0, outside

    def test_no_match_is_an_empty_result_not_an_error(self, http_client):
        d = assert_dict_response(
            http_client.get(f"/worklog/api/search?q=qqq{uuid4().hex}"))
        assert not d.get("error") and d["count"] == 0 and d["results"] == []

    def test_truncation_is_reported_never_silent(self, http_client):
        """A capped response that looks complete is worse than a short one."""
        d = assert_dict_response(http_client.get("/worklog/api/search?q=e"))
        assert d["count"] >= len(d["results"])
        assert d["truncated"] == d["count"] - len(d["results"])


@pytest.mark.api
class TestUntaggedBacklog:
    """81% of this corpus carries no status, which makes it invisible to the
    hub panels, the blocked/review rollup and the calendar tone. `status=none`
    is the sentinel that surfaces it — a plain status value can't say "absent".
    """

    def test_filter_alone_is_a_valid_search(self, http_client):
        """The backlog pass has no query — requiring one would make it
        unreachable."""
        d = assert_dict_response(http_client.get("/worklog/api/search?status=none"))
        assert not d.get("error"), d
        assert isinstance(d["results"], list)

    def test_still_requires_a_query_or_a_filter(self, http_client):
        assert assert_dict_response(http_client.get("/worklog/api/search?q=")).get("error")

    def test_returns_only_untagged_items(self, http_client):
        d = assert_dict_response(http_client.get("/worklog/api/search?status=none"))
        assert all(r["status"] == "" for r in d["results"]), \
            [r for r in d["results"] if r["status"]][:3]
        assert all(r["kind"] == "item" for r in d["results"]), \
            "prose is untaggable, so a status filter must exclude it"

    def test_untagged_and_tagged_are_disjoint(self, http_client, seeded):
        """The seeded item is blocked, so it must not appear in the untagged
        set — and a tagged filter must not return untagged rows."""
        untagged = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}&status=none"))
        assert untagged["count"] == 0, untagged
        blocked = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={seeded}&status=blocked"))
        assert blocked["count"] >= 1

    def test_tagging_removes_an_item_from_the_backlog(self, http_client):
        """The whole loop: find an untagged item, tag it, it leaves the set."""
        token = f"{TEST_PREFIX}untagged{uuid4().hex[:6]}"
        assert_ok(http_client.post("/worklog/api/log", json={
            "date": WORKLOG_TEST_DATE, "project": TEST_PREFIX + "BacklogProj",
            "text": token, "status": "note"}))          # 'note' => plain bullet
        before = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={token}&status=none"))
        assert before["count"] == 1, before
        assert_ok(http_client.post("/worklog/api/status", json={
            "date": WORKLOG_TEST_DATE, "project": TEST_PREFIX + "BacklogProj",
            "item": token, "status": "todo"}))
        after = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={token}&status=none"))
        assert after["count"] == 0, "a tagged item must leave the untagged set"
        tagged = assert_dict_response(
            http_client.get(f"/worklog/api/search?q={token}&status=todo"))
        assert tagged["count"] == 1, tagged

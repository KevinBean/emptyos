"""System app tests: BookMe — scheduling page.

Covers the owner-side config CRUD, the public booking flow (event lookup →
slots → book → .ics), booking management (list + cancel), and that the
public endpoints work without auth (they're declared in `public_routes`).

Test data uses a throwaway event type so we don't disturb real config; the
booking it creates is cleaned by the session fixture (vault notes under
30_Resources/EmptyOS/bookme/ tagged `booking`).
"""

from __future__ import annotations

import datetime

import pytest

from helpers import TEST_PREFIX, assert_ok

TEST_TYPE = "PLAYWRIGHT-TEST-evt"


def _next_weekday(weekday: int = 0) -> str:
    """Return the next date (YYYY-MM-DD) that falls on `weekday` (0=Mon)."""
    d = datetime.date.today() + datetime.timedelta(days=1)
    for _ in range(8):
        if d.weekday() == weekday:
            return d.isoformat()
        d += datetime.timedelta(days=1)
    return d.isoformat()


@pytest.fixture(scope="module")
def seeded_config(http_client):
    """Install a known event type + Mon 09–17 availability for the test run."""
    cfg = assert_ok(http_client.get("/bookme/api/config"))
    types = [et for et in cfg.get("event_types", []) if et.get("id") != TEST_TYPE]
    types.append(
        {
            "id": TEST_TYPE,
            "name": "Test Meeting",
            "duration_min": 30,
            "buffer_min": 0,
            "description": "test",
            "active": True,
        }
    )
    avail = dict(cfg.get("availability", {}))
    avail["mon"] = [["09:00", "17:00"]]
    body = {
        "owner_name": cfg.get("owner_name", ""),
        "timezone": cfg.get("timezone", "UTC"),
        "event_types": types,
        "availability": avail,
    }
    assert_ok(http_client.post("/bookme/api/config", json=body))
    return body


@pytest.mark.api
class TestBookMeConfig:
    def test_config_shape(self, http_client):
        cfg = assert_ok(http_client.get("/bookme/api/config"))
        for key in ("event_types", "availability", "timezone"):
            assert key in cfg

    def test_save_roundtrip(self, http_client, seeded_config):
        cfg = assert_ok(http_client.get("/bookme/api/config"))
        ids = {et["id"] for et in cfg["event_types"]}
        assert TEST_TYPE in ids


@pytest.mark.api
class TestBookMePublic:
    def test_public_event_lookup(self, http_client, seeded_config):
        ev = assert_ok(http_client.get(f"/bookme/api/public/event/{TEST_TYPE}"))
        assert ev["name"] == "Test Meeting"
        assert ev["duration_min"] == 30

    def test_public_event_unknown(self, http_client):
        ev = http_client.get("/bookme/api/public/event/does-not-exist").json()
        assert ev.get("error")

    def test_slots_returns_times(self, http_client, seeded_config):
        date = _next_weekday(0)  # a Monday
        r = assert_ok(http_client.get(f"/bookme/api/public/slots/{TEST_TYPE}?date={date}"))
        assert isinstance(r["slots"], list)
        assert "09:00" in r["slots"]

    def test_book_then_slot_gone(self, http_client, seeded_config):
        date = _next_weekday(0)
        before = assert_ok(http_client.get(f"/bookme/api/public/slots/{TEST_TYPE}?date={date}"))
        if not before["slots"]:
            pytest.skip("no open slots to book")
        slot = before["slots"][0]
        res = assert_ok(
            http_client.post(
                "/bookme/api/public/book",
                json={
                    "type": TEST_TYPE,
                    "date": date,
                    "time": slot,
                    "name": f"{TEST_PREFIX}booker",
                    "email": "test@example.com",
                    "note": "test booking",
                },
            )
        )
        assert res["ok"] and res["booking_id"]
        # The booked slot must disappear from availability.
        after = assert_ok(http_client.get(f"/bookme/api/public/slots/{TEST_TYPE}?date={date}"))
        assert slot not in after["slots"]
        # The .ics is downloadable.
        ics = http_client.get(res["ics_url"])
        assert ics.status_code == 200
        assert "BEGIN:VCALENDAR" in ics.text

    def test_book_validation(self, http_client, seeded_config):
        r = http_client.post(
            "/bookme/api/public/book",
            json={"type": TEST_TYPE, "date": _next_weekday(0), "time": "09:00", "name": "", "email": ""},
        ).json()
        assert r.get("error")


@pytest.mark.api
class TestBookMeManagement:
    def test_bookings_list(self, http_client, seeded_config):
        r = assert_ok(http_client.get("/bookme/api/bookings"))
        assert isinstance(r["bookings"], list)

    def test_cancel_unknown(self, http_client):
        r = http_client.post("/bookme/api/bookings/bk-nope/cancel").json()
        assert r.get("error")

    def test_hub_panel_registered(self, http_client, seeded_config):
        # Ask for this one panel, not /api/panels/all: that endpoint hydrates
        # every lazy contributor at once and swings from 12 s to >90 s under
        # load, which timed this test out on a healthy tree (2026-09-12).
        # /api/panel/{id} narrows before anything is called (sdk.panels `only=`).
        #
        # The panel answers None ("nothing to show") without an upcoming
        # confirmed booking, and the aggregator drops None rows — so book one
        # here rather than depend on test order. The session fixture cleans it.
        date = _next_weekday(0)   # the weekday seeded_config guarantees has windows
        slots = assert_ok(http_client.get(f"/bookme/api/public/slots/{TEST_TYPE}?date={date}"))["slots"]
        if not slots:
            pytest.skip("no open slots to book")
        booked = assert_ok(http_client.post(
            "/bookme/api/public/book",
            json={"type": TEST_TYPE, "date": date, "time": slots[0],
                  "name": f"{TEST_PREFIX}panel-booker", "email": "test@example.com", "note": ""},
        ))
        assert booked["ok"], booked
        data = assert_ok(http_client.get("/hub/api/panel/bookme-upcoming", timeout=30))
        assert isinstance(data, dict)
        # Registration is the claim: the row exists with its declared shape. Its
        # rows are the owner's next five bookings, so the fixture's own entry is
        # not asserted — with five earlier real bookings it would legitimately
        # fall outside the panel's `limit`.
        assert data.get("id") == "bookme-upcoming", data
        assert "lazy" in data and "renderer" in data
        assert isinstance(data.get("data"), list) and data["data"], data

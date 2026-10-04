"""Sandbox seed: a two-lesson Learn course for lesson-video verification.

Idempotent — the KB notes are created only when missing, and saving the course
overwrites it in place.

Usage:
    python tests/fixtures/sandbox/learn_video_course.py http://127.0.0.1:9002

Lesson 1 is a process (a good candidate for an animated scene); lesson 2 is a
definition-shaped note (should come back as slides only). Designed for
Claude-driven sessions per `.claude/rules/sandbox-driven-testing.md` — never run
it against the main daemon's real vault.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

COURSE_ID = "lesson-video-demo"

NOTES = [
    {
        "slug": "lv-demo-heat-flow",
        "kind": "concept",
        "title": "How heat leaves a buried cable",
        "domain": "engineering",
        "topic": "cable-rating",
        "body": (
            "## Summary\n\n"
            "Current flowing in a conductor produces I²R losses as heat. That heat must travel "
            "outward through the insulation, the sheath, and the surrounding soil before it "
            "reaches the ground surface.\n\n"
            "## The thermal path\n\n"
            "Each layer resists heat flow. The insulation usually has the largest thermal "
            "resistance per millimetre; the soil has the largest total resistance because the "
            "path through it is long. The conductor temperature rises until the heat leaving "
            "equals the heat produced.\n\n"
            "## Why it matters\n\n"
            "The maximum allowed conductor temperature sets the current rating. Dry soil holds "
            "heat in, so the same cable carries less current in dry ground than in moist ground.\n"
        ),
    },
    {
        "slug": "lv-demo-thermal-resistivity",
        "kind": "concept",
        "title": "Soil thermal resistivity",
        "domain": "engineering",
        "topic": "cable-rating",
        "body": (
            "## Definition\n\n"
            "Soil thermal resistivity describes how strongly soil resists heat flow, in kelvin "
            "metres per watt.\n\n"
            "## What changes it\n\n"
            "- Moisture content: drier soil resists heat more.\n"
            "- Density: loose soil resists heat more than compacted soil.\n"
            "- Material: sand, clay, and backfill mixes differ.\n\n"
            "## In practice\n\n"
            "Designers measure or assume a resistivity value and often place a controlled backfill "
            "around the cable so the value near the cable is known.\n"
        ),
    },
]


def _http(host: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        host.rstrip("/") + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:  # noqa: BLE001 — a seed script reports, it does not crash
        return {"error": str(e)[:200]}


_USER_DAEMON_PORTS = (":9000", ":9001")


def seed(host: str) -> dict:
    # These notes carry no TEST_PREFIX, so the conftest leak sweep would never
    # remove them from a real vault — refuse the user-owned daemons outright.
    if host.rstrip("/").endswith(_USER_DAEMON_PORTS):
        raise SystemExit(f"refusing to seed {host}: that is a user-owned daemon, lease a sandbox member")
    report: dict = {"notes": {}, "course": None}
    for note in NOTES:
        existing = _http(host, "GET", f"/kb/api/notes/{note['slug']}")
        # A miss answers {"error": "not found", "slug": ...} — it echoes the
        # slug, so only the absence of an error means the note exists.
        if not existing.get("error") and not existing.get("http_error") and existing.get("body"):
            report["notes"][note["slug"]] = "present"
            continue
        res = _http(host, "POST", "/kb/api/notes", note)
        report["notes"][note["slug"]] = "created" if not res.get("error") and not res.get("http_error") else res
    report["course"] = _http(host, "POST", "/learn/api/courses/save", {
        "course_id": COURSE_ID,
        "title": "Lesson video demo — cable heat",
        "description": "Two short lessons used to verify generated teaching videos.",
        "level": "beginner",
        "domain": "engineering",
        "topic": "cable-rating",
        "lessons": [
            {"slug": NOTES[0]["slug"], "title": "How heat leaves a cable", "kind": "read"},
            {"slug": NOTES[1]["slug"], "title": "Soil thermal resistivity", "kind": "read"},
        ],
    })
    return report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: learn_video_course.py <sandbox host, e.g. http://127.0.0.1:9002>")
    print(json.dumps(seed(sys.argv[1]), indent=2, ensure_ascii=False))

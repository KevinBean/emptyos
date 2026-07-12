"""now_iso() — UTC ISO timestamp with seconds precision."""

import re
from datetime import datetime, timezone

from emptyos.sdk import now_iso


ISO_SECONDS_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


def test_now_iso_format_is_utc_seconds():
    s = now_iso()
    assert ISO_SECONDS_UTC_RE.match(s), s


def test_now_iso_roundtrips_to_utc():
    parsed = datetime.fromisoformat(now_iso())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)

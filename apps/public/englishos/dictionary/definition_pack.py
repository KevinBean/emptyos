"""dictionary — the definition pack: read-only headword definitions shipped with a build.

A lookup of a common word is the same for every learner, so a hosted build ships
the answers instead of asking a model once per learner: the cost of a pack grows
with the vocabulary, not with the number of users. `lookup()` reads the pack after
the learner's own saved words and before the model.

The pack is one SQLite file opened read-only, so a daemon pays for the rows it
reads rather than holding the whole pack in memory — one learner daemon per user
means that memory is paid once per user. It is never written at runtime: a shared
write path would let one learner plant a definition every learner then sees. To
update a pack, build a new file and point the config at it; on Windows a running
daemon holds the old file open, so it cannot be replaced in place.

Pure module — no `self`, no kernel. Packs are written through `write_pack`, so
reader and writer share one schema.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

SCHEMA_VERSION = 1

# The keys `lookup()` returns; an entry is stored with exactly these.
ENTRY_KEYS = (
    "word", "phonetic", "part_of_speech", "definition", "example",
    "synonyms", "antonyms", "chinese", "etymology", "usage_notes",
)
_LIST_KEYS = {"synonyms", "antonyms"}

# A headword is lower-case ASCII letters with inner apostrophes, hyphens or
# single spaces ("don't", "well-being", "ice cream"). This keeps pack keys to
# real word shapes; it is not a security boundary (the query is parameterised).
# Accented loanwords ("café", "naïve") are left out on purpose: they are rare,
# so a miss costing one model call is cheaper than a second key form.
_HEADWORD = re.compile(r"^[a-z](?:[a-z' \-]*[a-z])?$")
# Not from a source: room for the longest everyday words (~20-30 letters) and
# short phrases ("ice cream"); anything longer is not a dictionary lookup.
_MAX_LEN = 40
# The typographic apostrophe books, the web and phone keyboards produce.
# U+2018 is an opening quote and never an inner apostrophe, so it is not mapped.
_APOSTROPHES = str.maketrans({"’": "'"})
# Not from a source: bounds a runaway synonym/antonym list in a generated entry.
_MAX_LIST = 8


def normalize_headword(word: str) -> str | None:
    """The pack key for `word` (case-folded), or None when it cannot be a headword.

    Used when WRITING a pack. Reads go through `DefinitionPack.get`, which also
    refuses capitalised input — see there.
    """
    key = " ".join(str(word or "").translate(_APOSTROPHES).strip().lower().split())
    if not key or len(key) > _MAX_LEN or not _HEADWORD.match(key):
        return None
    return key


def clean_entry(raw: dict, headword: str) -> dict | None:
    """Coerce a generated entry to the stored shape, or None if unusable.

    An entry without a definition is useless to a learner, so it is refused
    rather than stored and served.
    """
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for key in ENTRY_KEYS:
        value = raw.get(key)
        if key in _LIST_KEYS:
            if not isinstance(value, list):
                value = []
            out[key] = [str(v).strip() for v in value if str(v).strip()][:_MAX_LIST]
        else:
            out[key] = str(value).strip() if value is not None else ""
    out["word"] = headword
    if not out["definition"]:
        return None
    return out


def write_pack(path: Path, entries: dict[str, dict], meta: dict) -> int:
    """Write a pack file from ``{headword: entry}``; returns the rows stored.

    Entries are normalised and cleaned on the way in; ones that fail either are
    skipped, never stored half-formed. Keys that normalise to the same headword
    collapse to one row (the last wins). Overwrites an existing file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(tmp))
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE entries (word TEXT PRIMARY KEY, entry TEXT NOT NULL)")
        for word, raw in entries.items():
            key = normalize_headword(word)
            entry = clean_entry(raw, key) if key else None
            if entry is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO entries VALUES (?, ?)",
                (key, json.dumps(entry, ensure_ascii=False)),
            )
        stored = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        full_meta = {
            "generated_at": date.today().isoformat(),
            **meta,
            "schema_version": SCHEMA_VERSION,
            "count": stored,
        }
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in full_meta.items()],
        )
        conn.commit()
    finally:
        conn.close()
    try:
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return stored


class DefinitionPack:
    """A read-only pack. Unusable packs report ``available`` False, and neither
    opening nor reading ever raises — a broken pack falls through to the model.

    Opened with ``immutable=1``: the file is never written while a daemon runs,
    and immutable mode skips the locking and change checks a live database needs.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.meta: dict = {}
        self.error = ""
        self._conn: sqlite3.Connection | None = None
        if not self.path.is_file():
            self.error = f"pack not found: {self.path}"
            return
        conn = None
        try:
            uri = f"{self.path.resolve().as_uri()}?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self.meta = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM meta")}
            conn.execute("SELECT 1 FROM entries LIMIT 1").fetchall()
        except (sqlite3.Error, ValueError) as e:
            if conn is not None:
                conn.close()
            self.error = f"pack unreadable: {type(e).__name__}: {e}"
            return
        if self.meta.get("schema_version") != SCHEMA_VERSION:
            conn.close()
            self.error = (
                f"pack schema {self.meta.get('schema_version')!r} is not "
                f"{SCHEMA_VERSION}; rebuild it"
            )
            return
        self._conn = conn

    @property
    def available(self) -> bool:
        return self._conn is not None

    def get(self, word: str) -> dict | None:
        """The entry for `word`, or None on a miss or an unusable pack.

        Capitalised input is always a miss. Pack keys are case-folded, so
        "Polish", "May" or "Turkey" would otherwise be served the definition of
        "polish", "may" or "turkey"; the model sees the case and gets it right.
        """
        raw = str(word or "").strip()
        if raw != raw.lower():
            return None
        key = normalize_headword(raw)
        if key is None or self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT entry FROM entries WHERE word = ?", (key,)
            ).fetchone()
            entry = json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError):
            return None
        return entry if isinstance(entry, dict) else None

    def headwords(self) -> list[str]:
        """Every headword in the pack, or [] for an unusable pack.

        The pack's headwords are real dictionary spellings (SCOWL-filtered at
        build time), which is what makes them the vocabulary for autocomplete
        and did-you-mean: a common misspelling is not a headword.
        """
        if self._conn is None:
            return []
        try:
            return [row[0] for row in self._conn.execute("SELECT word FROM entries")]
        except sqlite3.Error:
            return []

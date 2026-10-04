"""Dictionary — word lookup, vault storage, SRS flashcards, quizzes.

Provides definitions via LLM, saves words to vault as markdown,
spaced repetition review, and quiz generation from saved vocabulary.
Autocomplete and did-you-mean are local (suggest.py): nothing typed leaves the machine.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from emptyos.sdk import (
    cli_command,
    load_json,
    parse_frontmatter,
    save_json,
    strip_frontmatter,
)
from emptyos.sdk.external_service import ExternalServiceBase

from . import crosswalk as _crosswalk
from . import lookup as _lookup
from . import picture_catalog
from . import picture_compose as _pic_compose
from . import picture_core as _pic_core
from . import picture_images as _pic_images
from . import picture_progress as _pic_progress
from . import picture_quiz as _pic_quiz
from . import picture_speak as _pic_speak
from . import pronounce as _pronounce
from . import reading as _reading
from . import srs as _srs
from . import vocab as _vocab
from . import vocab_loop as _vocab_loop
from .shared import (  # noqa: F401  (re-exported for tests / external imports)
    DEFAULT_DEFAULT_DICT_FOLDER,
    DICTIONARY_BASIC_SYSTEM,
    DICTIONARY_CONTEXT_SYSTEM,
    DICTIONARY_EXAMPLES_SYSTEM,
    DICTIONARY_LOOKUP_SYSTEM,
    DICTIONARY_WOTD_SYSTEM,
    best_example,
)
from .vocab_schema import parse_senses


class DictionaryApp(ExternalServiceBase):

    # ExternalServiceBase (a BaseApp subclass — see emptyos/sdk/external_service.py)
    # gives the throttle, configurable user agent, and public-mode gate that the
    # picture packs need for the Wikimedia photo API. Absorbed from picture-dict
    # 2026-08-19; every pre-existing route is unaffected, since the base only adds.
    DEMO_BASE = "https://en.wikipedia.org"
    SERVICE_LABEL = "Photographs via the Wikimedia public API"
    MIN_INTERVAL_S = 0.35
    DEFAULT_USER_AGENT = (
        "EmptyOS-PictureDict/1.0 (https://github.com/KevinBean/emptyos; "
        "picture dictionary for language learning)"
    )

    # Only these widths are actually served; anything else is snapped to one of
    # them. See the module docstring in picture_images.py.
    IMAGE_WIDTHS = (330, 500, 960)

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        # Picture-pack indexes, populated in setup() from packs/.
        # by_category keys are qualified ("animals:sea"); by_pack is the
        # whole-pack pool a quiz falls back to. See picture_catalog.
        self.items: dict = {}
        self.by_category: dict = {}
        self.by_pack: dict = {}
        self.order: list = []
        self.categories: list = []
        self.pack_index: list = []
        self._packs_fp: tuple = ()
        self._index: dict = {}
        self._prog_lock = asyncio.Lock()
        self._prefetch: dict = {"running": False, "total": 0, "done": 0,
                                "ok": 0, "failed": 0, "current": "",
                                "last_error": "", "cancel": False}
        self._prefetch_ever = False

    def _packs_fingerprint(self) -> tuple:
        """Cheap "did packs/ change on disk" signal — (count, newest mtime, bytes).

        Pack files are authored from outside the daemon as well as inside it
        (scripts/install_pack.py), and before this a CLI-written pack stayed
        invisible until someone restarted — with no hint that it existed. Three
        stats on a directory nobody writes often is a fair price for never
        having to remember.
        """
        try:
            files = sorted((Path(__file__).parent / "packs").glob("*"))
            stats = [f.stat() for f in files if f.is_file()]
        except Exception:
            return ()
        return (len(stats), max((s.st_mtime_ns for s in stats), default=0),
                sum(s.st_size for s in stats))

    def reload_packs_if_changed(self) -> bool:
        """Reload only when packs/ actually moved. Returns whether it did."""
        fp = self._packs_fingerprint()
        if fp and fp == getattr(self, "_packs_fp", None):
            return False
        self.reload_packs()
        return True

    def reload_packs(self) -> dict:
        """Re-read packs/ into the in-memory indexes.

        Called at setup() and again whenever a pack is written. That second call
        is load-bearing: a pack is read once at boot, so anything running against
        a stale in-memory copy re-downloads the OLD sources and reports success —
        the trap `/eos-picture-pack-review` names explicitly.
        """
        packs = picture_catalog.load_packs(Path(__file__).parent / "packs")
        self._packs_fp = self._packs_fingerprint()
        self.items = packs["items"]
        self.by_category = packs["by_category"]
        self.by_pack = packs["by_pack"]
        self.order = packs["order"]
        self.categories = packs["categories"]
        self.pack_index = packs["packs"]
        # A membership naming a slug the object store does not define is an
        # authoring mistake that would otherwise be invisible — the pack simply
        # renders one tile short. Say so rather than swallowing it.
        for w in packs["warnings"]:
            self.log_warn(f"picture packs: {w}")
        return packs

    async def setup(self):
        await super().setup()
        self.reload_packs()
        self._index = self.load_index()
        # Deliberately no prefetch here: an awaited warm-up would block the app
        # loader, and a backgrounded one would fire on every daemon boot even for
        # someone who never opens the pictures page. api_picture_status starts it
        # on first open.

    _srs_path = _srs._srs_path

    _load_srs = _srs._load_srs

    _save_srs = _srs._save_srs

    _card_context_on = _srs._card_context_on

    _threads_on = _srs._threads_on

    _card_context = _srs._card_context

    _order_new = _srs._order_new

    def _vault_dir(self) -> str:
        return self.vault_config("words_dir", DEFAULT_DEFAULT_DICT_FOLDER)

    async def get_summary(self) -> dict:
        """Summary for staff observers — word count, due reviews, recent words."""
        words = await self._vault_words()
        srs = self._load_srs()
        today = date.today().isoformat()
        due = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
        recent = words[-10:] if words else []
        return {
            "dictionary_words": len(words),
            "recent_words": recent,
            "in_srs": len(srs),
            "due_for_review": due,
        }

    async def _vault_words(self) -> list[str]:
        """List saved words from vault dictionary folder."""
        folder = self.vault_config_path("words_dir", DEFAULT_DEFAULT_DICT_FOLDER)
        if folder and folder.exists():
            return sorted(f.stem for f in folder.glob("*.md") if not f.name.startswith("_"))
        return []

    async def _read_vault_word(self, word: str) -> dict | None:
        """Read a saved word's markdown and parse frontmatter + content."""
        path = f"{self._vault_dir()}/{word}.md"
        try:
            content = await self.read(path)
        except Exception:
            return None
        meta = parse_frontmatter(content)
        body = strip_frontmatter(content).strip()
        return {"word": word, "meta": meta, "body": body, "raw": content}

    @staticmethod
    def _parse_sections(body: str) -> dict[str, str]:
        """Split markdown body into `## Section` -> text map."""
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in body.split("\n"):
            if line.startswith("## "):
                if current is not None:
                    sections[current] = "\n".join(buf).strip()
                current = line[3:].strip()
                buf = []
            else:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        return sections

    @staticmethod
    def _parse_meanings_legacy(body: str) -> dict | None:
        """Parse a legacy `## Meanings` / `### POS` dictionary layout.

        Picks the first prose line after the first `### POS` as the definition,
        and the first `> "..."` line as the example. Skips synonym lists and
        alternate senses separated by `---` — the quick-lookup card only needs
        the primary meaning.
        """
        pos = ""
        definition = ""
        example = ""
        seen_pos = False
        for raw in body.split("\n"):
            s = raw.strip()
            if s.startswith("### "):
                if definition:
                    break
                if not pos:
                    pos = s[4:].strip()
                seen_pos = True
                continue
            if not seen_pos:
                continue
            if s == "---":
                if definition:
                    break
                continue
            if not s:
                continue
            if s.startswith("> "):
                if not example:
                    example = s[2:].strip().strip('"')
                continue
            if not definition:
                definition = s
        if not definition:
            return None
        return {"definition": definition, "part_of_speech": pos, "example": example}

    def _vault_as_lookup(self, entry: dict) -> dict | None:
        """Reshape a saved vault entry into the same shape as `lookup()`.

        Returns None if the saved note lacks a definition (fall back to LLM).
        Handles both the EmptyOS `## Definition` layout and the legacy
        `## Meanings` / `### POS` layout produced by some vault dictionary tools.
        """
        meta = entry.get("meta", {}) or {}
        body = entry.get("body", "")
        sections = self._parse_sections(body)
        definition = sections.get("Definition", "").strip()
        part_of_speech = meta.get("part_of_speech", "")
        example = sections.get("Example", "").strip()
        if example.startswith("> "):
            example = example[2:].strip()

        # The current writer emits `## Sense:` blocks and no `## Definition` /
        # `## Example` section at all, so on a note it produced every lookup above
        # this line comes back empty and the whole function returns None — which
        # is why the review card's reveal was blank for every recently-saved word.
        # Legacy layouts still win when present; this is the fallback that makes
        # the modern layout readable.
        if not definition:
            senses = parse_senses(body)
            for sense in senses:
                if sense.get("definition"):
                    definition = str(sense["definition"])
                    break
            example = example or best_example(entry.get("word", ""), senses)

        if not definition:
            legacy = self._parse_meanings_legacy(sections.get("Meanings", ""))
            if legacy:
                definition = legacy["definition"]
                if not part_of_speech:
                    part_of_speech = legacy["part_of_speech"]
                if not example:
                    example = legacy["example"]

        if not definition:
            return None
        synonyms = [s.strip() for s in sections.get("Synonyms", "").split(",") if s.strip()]
        antonyms = [s.strip() for s in sections.get("Antonyms", "").split(",") if s.strip()]
        return {
            "word": entry.get("word", ""),
            "phonetic": meta.get("phonetic", ""),
            "part_of_speech": part_of_speech,
            "definition": definition,
            "example": example,
            "synonyms": synonyms,
            "antonyms": antonyms,
            "chinese": meta.get("chinese", "") or sections.get("Chinese", "").strip(),
            "etymology": sections.get("Etymology", "").strip(),
            "usage_notes": sections.get("Usage Notes", "").strip(),
            "from_vault": True,
        }

    @cli_command("dict", help="Look up a word")
    async def cmd_dict(self, word: str = ""):
        if not word:
            print("  Usage: eos dict <word>")
            return
        result = await self.lookup(word)
        print(f"\n  {result.get('word', word)}", end="")
        if result.get("phonetic"):
            print(f"  {result['phonetic']}", end="")
        print()
        if result.get("part_of_speech"):
            print(f"  [{result['part_of_speech']}]")
        print(f"  {result.get('definition', 'No definition')}")
        if result.get("example"):
            print(f"  Example: {result['example']}")
        if result.get("chinese"):
            print(f"  Chinese: {result['chinese']}")
        if result.get("synonyms"):
            print(f"  Synonyms: {', '.join(result['synonyms'][:5])}")
        print()

    api_srs_deck = _srs.api_srs_deck

    api_srs_review = _srs.api_srs_review

    api_srs_stats = _srs.api_srs_stats

    # Cross-app contract for learn's unified review queue.
    srs_due = _srs.srs_due

    srs_grade = _srs.srs_grade

    def _freq_path(self) -> Path:
        return self.data_dir / "frequency.json"

    def _load_freq(self) -> dict:
        return load_json(self._freq_path(), {})

    def _save_freq(self, data: dict):
        save_json(self._freq_path(), data)

    def _track_lookup(self, word: str):
        """Increment lookup frequency for a word."""
        freq = self._load_freq()
        today = date.today().isoformat()
        if word not in freq:
            freq[word] = {"count": 0, "first": today, "last": today}
        freq[word]["count"] += 1
        freq[word]["last"] = today
        self._save_freq(freq)

    # ── Lookup (extracted to lookup.py) ──
    _definition_pack = _lookup._definition_pack
    _known_word = _lookup._known_word
    lookup          = _lookup.lookup
    voice_lookup    = _lookup.voice_lookup
    api_lookup      = _lookup.api_lookup
    api_explain     = _lookup.api_explain
    api_examples    = _lookup.api_examples
    api_word_of_day = _lookup.api_word_of_day
    api_suggest     = _lookup.api_suggest
    api_didyoumean  = _lookup.api_didyoumean
    api_pronounce   = _lookup.api_pronounce
    api_audio       = _lookup.api_audio

    # ── Adaptive browser reading layer (extension) ──
    api_reading_status        = _reading.api_reading_status
    api_reading_models        = _reading.api_reading_models
    api_reading_warm          = _reading.api_reading_warm
    api_reading_settings      = _reading.api_reading_settings
    api_reading_settings_save = _reading.api_reading_settings_save
    api_reading_known         = _reading.api_reading_known
    api_reading_analyze       = _reading.api_reading_analyze
    api_reading_lookup        = _reading.api_reading_lookup
    api_reading_save          = _reading.api_reading_save
    api_reading_feedback      = _reading.api_reading_feedback

    # ── Pronounce (extracted to pronounce.py) ──
    _EVENTS_MAX_LINES        = _pronounce._EVENTS_MAX_LINES
    _weak_phones_path        = _pronounce._weak_phones_path
    _pronounce_pairs_path    = _pronounce._pronounce_pairs_path
    _pronounce_events_path   = _pronounce._pronounce_events_path
    _load_weak_phones        = _pronounce._load_weak_phones
    _save_weak_phones        = _pronounce._save_weak_phones
    _load_pronounce_pairs    = _pronounce._load_pronounce_pairs
    _save_pronounce_pairs    = _pronounce._save_pronounce_pairs
    _append_pronounce_event  = _pronounce._append_pronounce_event
    _read_pronounce_events   = _pronounce._read_pronounce_events
    log_pronounce_events     = _pronounce.log_pronounce_events
    pronounce_snapshot       = _pronounce.pronounce_snapshot
    log_weak_phones          = _pronounce.log_weak_phones
    _write_weak_phones_vault = _pronounce._write_weak_phones_vault
    due_weak_phones          = _pronounce.due_weak_phones
    api_weak_phones          = _pronounce.api_weak_phones
    api_weak_phones_review   = _pronounce.api_weak_phones_review
    api_pronounce_patterns   = _pronounce.api_pronounce_patterns
    api_pronounce_events     = _pronounce.api_pronounce_events

    # ── Vocab (extracted to vocab.py) ──
    save_word             = _vocab.save_word
    api_save              = _vocab.api_save
    api_vault             = _vocab.api_vault

    api_threads           = _vocab.api_threads
    api_vault_word        = _vocab.api_vault_word
    api_favorite          = _vocab.api_favorite
    set_word_difficulty   = _vocab.set_word_difficulty
    api_difficulty        = _vocab.api_difficulty
    difficulty_map        = _vocab.difficulty_map
    api_delete_vault_word = _vocab.api_delete_vault_word
    api_quiz              = _vocab.api_quiz
    api_export            = _vocab.api_export
    api_vault_import_preview = _vocab.api_vault_import_preview
    api_vault_import_confirm = _vocab.api_vault_import_confirm
    api_frequency         = _vocab.api_frequency
    api_word_addons       = _vocab.api_word_addons

    # ── Vocab-expansion loop (extracted to vocab_loop.py; dark-flagged) ──
    _vl_path              = _vocab_loop._vl_path
    _vl_load              = _vocab_loop._vl_load
    _vl_save              = _vocab_loop._vl_save
    _vl_on                = _vocab_loop._vl_on
    _harvest_text         = _vocab_loop._harvest_text

    _digest_moments       = _vocab_loop._digest_moments
    _ensure_feed          = _vocab_loop._ensure_feed
    _on_video_digested    = _vocab_loop._on_video_digested
    api_loop_status       = _vocab_loop.api_loop_status
    api_progress          = _vocab_loop.api_progress
    api_log_test          = _vocab_loop.api_log_test
    api_harvest           = _vocab_loop.api_harvest
    api_harvest_inbox     = _vocab_loop.api_harvest_inbox
    api_harvest_resolve   = _vocab_loop.api_harvest_resolve
    api_vocab_feed        = _vocab_loop.api_vocab_feed
    api_feed_add          = _vocab_loop.api_feed_add
    api_production_prompt = _vocab_loop.api_production_prompt
    api_production_check  = _vocab_loop.api_production_check
    panel_vocab_today     = _vocab_loop.panel_vocab_today

    # ── Vowel↔spelling crosswalk (extracted to crosswalk.py) ──
    api_crosswalk         = _crosswalk.api_crosswalk
    api_crosswalk_kb      = _crosswalk.api_crosswalk_kb
    panel_vowel_crosswalk = _crosswalk.panel_vowel_crosswalk

    # ── Picture packs (absorbed from picture-dict 2026-08-19) ──
    #
    # Every helper-module method reachable as self.X must be listed here. A
    # missing line is a NameError that only fires at call time, and for a
    # @web_route it means the endpoint silently never exists.
    #
    # picture_catalog.py is pure and is called as picture_catalog.fn(...) —
    # nothing to bind.

    # picture_core.py — pack spine, star, verbs, CLI
    # ── Pack composer (extracted to picture_compose.py) ──
    composer_enabled         = _pic_compose.composer_enabled
    _proposal_path           = _pic_compose._proposal_path
    _read_proposal           = _pic_compose._read_proposal
    _write_proposal          = _pic_compose._write_proposal
    _prune_proposals         = _pic_compose._prune_proposals
    _verify_candidates       = _pic_compose._verify_candidates
    _retitle_round           = _pic_compose._retitle_round
    _compose_pack            = _pic_compose._compose_pack
    api_pack_propose         = _pic_compose.api_pack_propose
    api_pack_proposal        = _pic_compose.api_pack_proposal
    api_pack_proposals       = _pic_compose.api_pack_proposals
    api_pack_proposal_edit   = _pic_compose.api_pack_proposal_edit
    api_pack_proposal_apply  = _pic_compose.api_pack_proposal_apply
    api_pack_proposal_reject = _pic_compose.api_pack_proposal_reject

    _wiki_host               = _pic_core._wiki_host
    _image_width             = _pic_core._image_width
    _item_payload            = _pic_core._item_payload
    api_picture_status       = _pic_core.api_picture_status
    api_picture_catalog      = _pic_core.api_picture_catalog
    api_picture_item         = _pic_core.api_picture_item
    save_picture_word        = _pic_core.save_picture_word
    api_picture_save_word    = _pic_core.api_picture_save_word
    api_picture_unsave       = _pic_core.api_picture_unsave
    lookup_picture           = _pic_core.lookup_picture
    voice_picture_lookup     = _pic_core.voice_picture_lookup
    cli_picture              = _pic_core.cli_picture
    picture_for_word         = _pic_core.picture_for_word
    improv_sources           = _pic_core.improv_sources
    improv_words             = _pic_core.improv_words
    improv_result            = _pic_core.improv_result
    api_picture_for_word     = _pic_core.api_picture_for_word

    # picture_images.py — Wikimedia lookup, download cache, prefetch job
    load_index               = _pic_images.load_index
    save_index               = _pic_images.save_index
    image_url                = _pic_images.image_url
    slugs_with_image         = _pic_images.slugs_with_image
    image_credit             = _pic_images.image_credit
    coverage                 = _pic_images.coverage
    start_prefetch           = _pic_images.start_prefetch
    _prefetch_loop           = _pic_images._prefetch_loop
    _fetch_one               = _pic_images._fetch_one
    _lookup_lead_files       = _pic_images._lookup_lead_files
    _lookup_file_info        = _pic_images._lookup_file_info
    _download                = _pic_images._download
    _api_get                 = _pic_images._api_get
    api_picture_image        = _pic_images.api_picture_image
    api_picture_prefetch     = _pic_images.api_picture_prefetch
    api_picture_prefetch_status = _pic_images.api_picture_prefetch_status
    api_picture_prefetch_cancel = _pic_images.api_picture_prefetch_cancel
    api_picture_refetch      = _pic_images.api_picture_refetch

    # picture_progress.py — picture-progress.json, FSRS schedule, learn contract half
    load_picture_progress    = _pic_progress.load_picture_progress
    save_picture_progress    = _pic_progress.save_picture_progress
    picture_record_answer    = _pic_progress.picture_record_answer
    picture_enroll           = _pic_progress.picture_enroll
    picture_due              = _pic_progress.picture_due
    picture_grade            = _pic_progress.picture_grade
    picture_stats            = _pic_progress.picture_stats
    picture_scene_outcome    = _pic_progress.picture_scene_outcome
    api_picture_srs_due      = _pic_progress.api_picture_srs_due
    api_picture_srs_grade    = _pic_progress.api_picture_srs_grade
    api_picture_srs_enroll   = _pic_progress.api_picture_srs_enroll
    api_picture_srs_unenroll = _pic_progress.api_picture_srs_unenroll
    panel_picture_due        = _pic_progress.panel_picture_due
    voice_picture_due        = _pic_progress.voice_picture_due

    # picture_quiz.py — round construction, distractors, grading
    api_picture_quiz_start   = _pic_quiz.api_picture_quiz_start
    api_picture_quiz_answer  = _pic_quiz.api_picture_quiz_answer
    api_picture_quiz_finish  = _pic_quiz.api_picture_quiz_finish
    _session_path            = _pic_quiz._session_path
    _prune_sessions          = _pic_quiz._prune_sessions

    # picture_speak.py — reference clip, recorded attempt, pronunciation score
    api_picture_say          = _pic_speak.api_picture_say
    api_picture_clip         = _pic_speak.api_picture_clip
    api_picture_speak_upload = _pic_speak.api_picture_speak_upload
    api_picture_speak_attempt = _pic_speak.api_picture_speak_attempt
    api_picture_speak_fast   = _pic_speak.api_picture_speak_fast
    _score_spoken            = _pic_speak._score_spoken
    _setting_float           = _pic_speak._setting_float
    api_picture_talk         = _pic_speak.api_picture_talk
    _attempt_audio           = _pic_speak._attempt_audio

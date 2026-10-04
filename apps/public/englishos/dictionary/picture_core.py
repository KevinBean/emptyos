"""Dictionary (pictures) — the pack spine: catalog routes, the star, the verbs.

Absorbed from the retired ``picture-dict`` app 2026-08-19. Owns the loaded pack
indexes' *routes* (the indexes themselves live on the app instance, populated in
``app.setup``), the browse/detail payload shape, the star that writes a vault
note, and the picture verbs.

The two content tiers this app now holds are deliberate and load-bearing:

  * ``packs/*.jsonl`` — 130 shipped reference rows, git-tracked, never written
    to the vault. They are not the learner's content.
  * ``30_Resources/Learning/Dictionary/*.md`` — the learner's own words.

Starring a picture is the *only* bridge between them, and it stays on demand.
Seeding 130 notes nobody wrote would make the real vocabulary store less useful
— that refusal came from the picture-dict INTENT and survives the merge.

Cross-module callers reach these via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import random
import urllib.parse
from typing import TYPE_CHECKING

from emptyos.sdk import cli_command, web_route

from . import picture_catalog

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ─────────────────────────────────
#   _wiki_host            = _pic_core._wiki_host
#   _image_width          = _pic_core._image_width
#   _item_payload         = _pic_core._item_payload
#   api_picture_status    = _pic_core.api_picture_status
#   api_picture_catalog   = _pic_core.api_picture_catalog
#   api_picture_item      = _pic_core.api_picture_item
#   save_picture_word     = _pic_core.save_picture_word
#   api_picture_save_word = _pic_core.api_picture_save_word
#   api_picture_unsave    = _pic_core.api_picture_unsave
#   lookup_picture        = _pic_core.lookup_picture
#   voice_picture_lookup  = _pic_core.voice_picture_lookup
#   cli_picture           = _pic_core.cli_picture
#   picture_for_word      = _pic_core.picture_for_word
#   improv_sources        = _pic_core.improv_sources
#   improv_words          = _pic_core.improv_words
#   improv_result         = _pic_core.improv_result
#   api_picture_for_word  = _pic_core.api_picture_for_word
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# ─── Small helpers ───────────────────────────────────────────────────


def _wiki_host(self) -> str:
    return urllib.parse.urlparse(self._base_url()).hostname or "en.wikipedia.org"


def _image_width(self) -> int:
    try:
        want = int(self.setting_or_config("picture-dict.image_width", 500))
    except (TypeError, ValueError):
        want = 500
    return min(self.IMAGE_WIDTHS, key=lambda w: abs(w - want))


def _item_payload(self, slug: str, prog: dict | None = None) -> dict:
    item = dict(self.items[slug])
    entry = (prog or {}).get(slug, {}) if prog is not None else {}
    item["image"] = self.image_url(slug)
    item["credit"] = self.image_credit(slug)
    item["starred"] = bool(entry.get("saved"))
    item["enrolled"] = isinstance(entry.get("srs"), dict)
    item["seen"] = int(entry.get("seen", 0))
    return item


# ─── Catalog routes ──────────────────────────────────────────────────


@web_route("GET", "/api/picture/status")
async def api_picture_status(self, request):
    """First call the pictures page makes. Also the first-open prefetch trigger."""
    # A pack authored from outside the daemon (scripts/install_pack.py) would
    # otherwise stay invisible until a restart, with nothing to say it existed.
    self.reload_packs_if_changed()
    gate = self._status()
    cov = self.coverage()
    if (
        self.setting_or_config("picture-dict.prefetch_on_open", True)
        and not self._prefetch.get("running")
        and not self._prefetch_ever
        and cov["missing"] > 0
        and gate.get("enabled", True)
    ):
        self._prefetch_ever = True
        self.spawn_background(self.start_prefetch(),
                              label="dictionary picture autoprefetch")
    return {
        # `packs` is derived from packs/meta.json, not a hardcoded list — the
        # earlier objection ("a second source of truth") is spent, and with more
        # than one pack the page needs a selector. No `quiz_length`: the quiz
        # reads its length setting server-side in picture_quiz.py.
        "packs": self.pack_index,
        # The page mounts the composer only when this is true, so a learner with
        # the flag off never loads its DOM.
        "composer": self.composer_enabled(),
        "categories": self.categories,
        "coverage": cov,
        "progress": self.picture_stats(),
        "prefetch": {k: v for k, v in self._prefetch.items() if k != "cancel"},
        "network": gate,
        # English-only by default for a global edition; the picture-card view
        # offers to enable 中文 on first open (pictures.js). A per-learner setting.
        "show_chinese": bool(self.setting_or_config("picture-dict.show_chinese", False)),
    }


@web_route("GET", "/api/picture/catalog")
async def api_picture_catalog(self, request):
    q = (request.query_params.get("q") or "").strip().lower()
    category = (request.query_params.get("category") or "").strip()
    pack = (request.query_params.get("pack") or "").strip()
    starred_only = request.query_params.get("starred") == "1"
    # Accepts both "animals:sea" and the bare "sea" a pre-split link carries;
    # ambiguous resolves to "" and is answered in band rather than guessed at.
    key = picture_catalog.resolve_category(self.by_category, category)
    if category and not key:
        return {"error": f"unknown category '{category}'"}
    if pack and pack not in self.by_pack:
        return {"error": f"unknown pack '{pack}'"}

    prog = self.load_picture_progress()
    if key:
        slugs = self.by_category[key]
    elif pack:
        slugs = self.by_pack[pack]
    else:
        slugs = self.order
    out = []
    for s in slugs:
        it = self._item_payload(s, prog)
        if starred_only and not it["starred"]:
            continue
        if q and q not in it["name"].lower() and q not in it["chinese"] \
                and q not in (it.get("pinyin") or "").lower():
            continue
        out.append(it)
    return {"items": out, "count": len(out), "categories": self.categories}


@web_route("GET", "/api/picture/item/{slug}")
async def api_picture_item(self, request):
    slug = request.path_params.get("slug", "")
    if slug not in self.items:
        return {"error": f"unknown animal '{slug}'"}
    prog = self.load_picture_progress()
    payload = self._item_payload(slug, prog)
    payload["speak"] = (prog.get(slug, {}) or {}).get("speak", {})
    payload["srs"] = (prog.get(slug, {}) or {}).get("srs", {})
    payload["scene"] = (prog.get(slug, {}) or {}).get("scene", {})
    return payload


# ─── Vault hand-off ──────────────────────────────────────────────────


async def save_picture_word(self, slug: str = "") -> dict:
    """Star one animal — the only path from the shipped pack to the vault, and
    only ever on demand. We pass the Wikipedia page as the source so the photo is
    one click away (the vocabulary note format has no image field of its own).

    ``enroll_srs=False`` is deliberate. Before the merge this was a cross-app
    call and the word landed in *both* decks — the word deck via save_word's
    default, and the picture deck via the star below — so the same animal came
    up twice in learn's unified queue. The picture card is the better review
    affordance for a word learned from a photograph (it carries the photograph),
    so it owns the schedule and the word note is stored unenrolled.
    """
    slug = (slug or "").strip()
    item = self.items.get(slug)
    if not item:
        return {"error": f"unknown animal '{slug}'"}

    try:
        res = await self.save_word(
            word=item["name"], chinese=item["chinese"],
            definition=item.get("hint", ""), part_of_speech="noun",
            # The pack, not the literal "animals" this used to hardcode — which
            # would file a starred carrot under `animals` in the vault the moment
            # a second pack shipped.
            topics=[t for t in (item.get("pack"), item.get("category")) if t],
            source_url=f"https://en.wikipedia.org/wiki/"
                       f"{urllib.parse.quote(item['wiki'].replace(' ', '_'))}",
            enroll_srs=False,
        )
    except Exception as e:  # a vault write can fail; the star should say so
        return {"error": f"could not save to your vocabulary: {e}"}

    async with self._prog_lock:
        prog = self.load_picture_progress()
        entry = prog.setdefault(slug, {})
        entry["saved"] = True
        entry.setdefault("srs", {})  # starring also enrols it for review
        self.save_picture_progress(prog)

    self.spawn_background(
        self.emit("dictionary:picture_word_saved",
                  {"slug": slug, "word": item["name"]}),
        label="dictionary picture_word_saved",
    )
    return {"ok": True, "slug": slug, "word": item["name"], "result": res}


@web_route("POST", "/api/picture/save-word")
async def api_picture_save_word(self, request):
    body = await request.json() if await request.body() else {}
    return await self.save_picture_word(str(body.get("slug") or ""))


@web_route("POST", "/api/picture/unsave")
async def api_picture_unsave(self, request):
    """Clears our star only. Never deletes the vault note — the learner made
    that note deliberately, and this button does not mean 'delete my word'."""
    body = await request.json() if await request.body() else {}
    slug = str(body.get("slug") or "")
    if slug not in self.items:
        return {"error": f"unknown animal '{slug}'"}
    async with self._prog_lock:
        prog = self.load_picture_progress()
        prog.setdefault(slug, {})["saved"] = False
        self.save_picture_progress(prog)
    return {"ok": True, "slug": slug}


# ─── Verbs ───────────────────────────────────────────────────────────


async def lookup_picture(self, name: str = "") -> dict:
    """Named ``lookup_picture``, not ``lookup`` — ``self.lookup`` is the word
    lookup this app has always had, and the two must not shadow each other."""
    item = picture_catalog.resolve_name(self.items, name)
    if not item:
        return {"error": f"no animal called '{name}' in the pack"}
    return self._item_payload(item["slug"], self.load_picture_progress())


async def voice_picture_lookup(self, name: str = "") -> dict:
    item = picture_catalog.resolve_name(self.items, name)
    if not item:
        return {"say": f"I do not have {name} in the picture dictionary."}
    bits = [f"{item['name']} is {item['chinese']}."]
    if item.get("hint"):
        bits.append(item["hint"])
    return {
        "say": " ".join(bits),
        "card": {"renderer": "entity-card", "data": {
            "title": item["name"], "subtitle": item["chinese"],
            "fields": [{"label": "Group", "value": item["category"]},
                       {"label": "Hint", "value": item.get("hint", "")}]}},
        "link": {"text": "Open in the picture dictionary",
                 "href": f"/dictionary/#pictures/{item['slug']}"},
    }


# ─── CLI ─────────────────────────────────────────────────────────────


@cli_command("picture-dict", "Browse and review the picture dictionary")
async def cli_picture(self, action: str = "status", arg: str = "") -> str:
    action = (action or "status").lower()
    if action == "status":
        cov, st = self.coverage(), self.picture_stats()
        packs = " · ".join(f"{p['title']} {p['count']}" for p in self.pack_index)
        return (f"{cov['total']} objects · {cov['with_photo']} with photos · "
                f"{st['learning']} in review · {st['due']} due today"
                + (f"\n  {packs}" if packs else ""))
    if action == "due":
        res = await self.picture_due(limit=20)
        if not res["cards"]:
            return "Nothing due."
        return "\n".join(f"  {c['name']:<16} {c['chinese']}" for c in res["cards"])
    if action == "list":
        # <arg> is a pack, a qualified "pack:group", or a bare group name from
        # before packs had their own namespace. Ambiguity is refused, not guessed.
        slugs = self.order
        if arg:
            key = picture_catalog.resolve_category(self.by_category, arg)
            if key:
                slugs = self.by_category[key]
            elif arg in self.by_pack:
                slugs = self.by_pack[arg]
            else:
                return f"unknown pack or category '{arg}'"
        return "\n".join(f"  {self.items[s]['emoji']} {self.items[s]['name']:<16} "
                         f"{self.items[s]['chinese']}" for s in slugs)
    if action == "prefetch":
        return str(await self.start_prefetch())
    return "usage: eos picture-dict [status|due|list <pack|category>|prefetch]"


# ─── Word card ↔ pack bridge ─────────────────────────────────────────


def picture_for_word(self, word: str) -> dict:
    """The pack photograph for a saved vocabulary word, or ``{}``.

    This is the whole point of folding the packs into the dictionary: 41 of the
    440 words in a real vault already had a licence-verified photograph sitting
    in the Animals pack and no way to see it, because they were two apps.

    Returns ``{}`` unless the word matches a pack entry EXACTLY *and* that
    entry's photo is on disk — `image_url` returns "" when it is not, and this
    never guesses. Most vocabulary is not picturable (`obviate`, `contingency`),
    and a wrong picture teaches a wrong association faster than text does, so
    the silent-empty case is the common one by design.
    """
    if not self.setting_or_config("dictionary.picture_on_word_card", True):
        return {}
    item = picture_catalog.exact_match(self.items, word)
    if not item:
        return {}
    url = self.image_url(item["slug"])
    if not url:
        return {}
    return {
        "slug": item["slug"],
        "name": item["name"],
        "chinese": item.get("chinese", ""),
        "url": url,
        # Named so a homonym is visibly wrong rather than silently wrong: a
        # learner who saved `crane` the machine sees a bird captioned
        # "Animals pack", and knows instantly what happened.
        "pack": item.get("pack", ""),
        "credit": self.image_credit(item["slug"]),
        "href": f"/dictionary/#pictures/{item['slug']}",
    }


# Enough objects for a five-minute scene at about one per partner turn; the
# caller asks for 8. Also the ceiling on a hand-built request.
SCENE_MAX_WORDS = 12
# Below this a scene is one or two props, not a place — the partner runs out
# of objects to set up within a couple of turns, so the source is not offered.
SCENE_MIN_WORDS = 4


# ─── Improv word sources ─────────────────────────────────────────────
#
# `[[contributes.improv.wordsource]]`: Improv asks every app what it can
# offer as scene vocabulary, fetches the words for the one the learner picks,
# and reports back which were said. Improv holds no dictionary-specific code,
# and this app decides what a "source" means — a pack, or today's due cards.


async def improv_sources(self) -> list[dict]:
    """What this app can supply as scene words: each pack, plus today's due
    cards when there are enough of them to build a scene from."""
    out = []
    # Judge by real cards, not the count: a due row whose object has left the
    # packs is counted but never returned, and would promise an empty scene.
    due_info = await self.picture_due(limit=SCENE_MIN_WORDS)
    due = due_info.get("due_count", 0)
    if len(due_info.get("cards") or []) >= SCENE_MIN_WORDS:
        out.append({
            "id": "due", "title": "Words due for review today",
            "subtitle": f"{due} waiting · the ones you are about to forget",
            "setting": "", "count": due,
        })
    for pack in self.pack_index:
        pid = str(pack.get("id") or "")
        count = len(self.by_pack.get(pid, []))
        if pid and count >= SCENE_MIN_WORDS:
            out.append({
                "id": f"pack:{pid}", "title": str(pack.get("title") or pid),
                "subtitle": f"{count} objects", "setting": str(pack.get("title") or pid),
                "count": count, "emoji": str(pack.get("emoji") or ""),
            })
    return out


async def improv_words(self, source: str = "", count: int = 8) -> list[dict]:
    """The words for one source: the most overdue cards first, or a random
    sample of the pack.

    Returns ``{id, label}`` rows — the labels are this app's own, so nothing a
    caller passes can reach a model prompt as text.
    """
    try:
        limit = max(1, min(SCENE_MAX_WORDS, int(count)))
    except (TypeError, ValueError):
        limit = 8
    source = str(source or "")
    if source == "due":
        cards = (await self.picture_due(limit=limit)).get("cards", [])
        return [{"id": c["slug"], "label": c["name"]} for c in cards]
    if source.startswith("pack:"):
        # `pack:<id>/<slug>` puts that object first — the one just talked about.
        pid, _, first = source[5:].partition("/")
        members = self.by_pack.get(pid) or []
        lead = [first] if first in members else []
        rest = [s for s in members if s not in lead]
        chosen = lead + random.sample(rest, min(limit - len(lead), len(rest)))
        return [{"id": s, "label": self.items[s]["name"]} for s in chosen if s in self.items]
    return []


async def improv_result(self, source: str = "", used: list | None = None,
                        missed: list | None = None) -> dict:
    """Take back a scene's outcome.

    Words said are recorded as produced in conversation, and a due card said
    in the scene counts as reviewed (``picture_scene_outcome``). Words not
    said go into review; a due one simply stays due, since the partner may
    never have set up a moment for it.
    """
    used = [s for s in (used or []) if isinstance(s, str)]
    missed = [s for s in (missed or []) if isinstance(s, str)]
    added = []
    for slug in missed:
        if slug in self.items and (await self.picture_enroll(slug)).get("added"):
            added.append(self.items[slug]["name"])
    reviewed = await self.picture_scene_outcome(used, source) if used else []
    return {"added": added, "reviewed": [self.items[s]["name"] for s in reviewed]}


@web_route("GET", "/api/picture/for-word/{word}")
async def api_picture_for_word(self, request):
    return self.picture_for_word(request.path_params.get("word", ""))

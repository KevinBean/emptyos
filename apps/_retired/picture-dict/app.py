"""Picture Dictionary — learn English vocabulary from real photographs.

The spine. Holds the app class, the loaded catalog indexes, the shared locks, the
catalog-facing routes, and **every binding line** for the helper modules.

Decomposed per ``.claude/rules/multi-module-apps.md``: four learning surfaces plus
photo fetching plus scheduling would put a single file well past the ~1200-line
atomic threshold.

  catalog.py   pure pack loading + name resolution (no self, no I/O beyond a read)
  images.py    Wikimedia lookup, download cache, the background prefetch job
  progress.py  progress.json, the FSRS schedule, the ``learn`` contract
  quiz.py      round construction, distractor choice, grading
  speak.py     reference clip, recorded attempt, pronunciation score

This app writes nothing to the vault itself. Starring a word routes through
``dictionary.save_word``, which owns that contract — so the durable vocabulary
store stays the dictionary and this never becomes a second one.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from pathlib import Path

from emptyos.sdk import cli_command, web_route
from emptyos.sdk.external_service import ExternalServiceBase

from . import catalog
from . import images as _images
from . import progress as _progress
from . import quiz as _quiz
from . import speak as _speak


class PictureDictApp(ExternalServiceBase):
    """Visual vocabulary: browse, quiz, review, and say it out loud."""

    # ExternalServiceBase gives us the throttle, the configurable user agent, and
    # the public-mode gate that geocode/routing already use for OSM.
    DEMO_BASE = "https://en.wikipedia.org"
    SERVICE_LABEL = "Photographs via the Wikimedia public API"
    MIN_INTERVAL_S = 0.35
    DEFAULT_USER_AGENT = (
        "EmptyOS-PictureDict/1.0 (https://github.com/KevinBean/emptyos; "
        "picture dictionary for language learning)"
    )

    # Only these widths are actually served; anything else is snapped to one of
    # them. See the module docstring in images.py.
    IMAGE_WIDTHS = (330, 500, 960)

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self.items: dict = {}
        self.by_category: dict = {}
        self.order: list = []
        self.categories: list = []
        self._index: dict = {}
        self._prog_lock = asyncio.Lock()
        self._prefetch: dict = {"running": False, "total": 0, "done": 0,
                                "ok": 0, "failed": 0, "current": "",
                                "last_error": "", "cancel": False}
        self._prefetch_ever = False

    async def setup(self):
        await super().setup()
        packs = catalog.load_packs(Path(__file__).parent / "packs")
        self.items = packs["items"]
        self.by_category = packs["by_category"]
        self.order = packs["order"]
        self.categories = packs["categories"]
        self._index = self.load_index()
        # Deliberately no prefetch here: an awaited warm-up would block the app
        # loader, and a backgrounded one would fire on every daemon boot even for
        # someone who never opens the app. api_status starts it on first open.

    # ─── Small helpers ───────────────────────────────────────────────

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

    # ─── Catalog routes ──────────────────────────────────────────────

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        """First call the page makes. Also the first-open prefetch trigger."""
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
                                  label="picture-dict autoprefetch")
        return {
            # No `packs` / `quiz_length` here: the page never read either, and the
            # hardcoded pack list was a second source of truth for packs/meta.json.
            # The quiz reads its length setting server-side in quiz.py.
            "categories": self.categories,
            "coverage": cov,
            "progress": self.progress_stats(),
            "prefetch": {k: v for k, v in self._prefetch.items() if k != "cancel"},
            "network": gate,
            "show_chinese": bool(self.setting_or_config("picture-dict.show_chinese", True)),
        }

    @web_route("GET", "/api/catalog")
    async def api_catalog(self, request):
        q = (request.query_params.get("q") or "").strip().lower()
        category = (request.query_params.get("category") or "").strip()
        starred_only = request.query_params.get("starred") == "1"
        if category and category not in self.by_category:
            return {"error": f"unknown category '{category}'"}

        prog = self.load_progress()
        slugs = self.by_category[category] if category else self.order
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

    @web_route("GET", "/api/item/{slug}")
    async def api_item(self, request):
        slug = request.path_params.get("slug", "")
        if slug not in self.items:
            return {"error": f"unknown animal '{slug}'"}
        prog = self.load_progress()
        payload = self._item_payload(slug, prog)
        payload["speak"] = (prog.get(slug, {}) or {}).get("speak", {})
        payload["srs"] = (prog.get(slug, {}) or {}).get("srs", {})
        return payload

    # ─── Vault hand-off ──────────────────────────────────────────────

    async def save_to_vocabulary(self, slug: str = "") -> dict:
        """Star one animal — the only path that reaches the vault, and only ever
        on demand. The note is written by ``dictionary``, which owns the schema;
        we pass the Wikipedia page as the source so the photo is one click away
        (the dictionary note format has no image field of its own)."""
        slug = (slug or "").strip()
        item = self.items.get(slug)
        if not item:
            return {"error": f"unknown animal '{slug}'"}

        res, err = await self.try_call_app(
            "dictionary", "save_word",
            word=item["name"], chinese=item["chinese"],
            definition=item.get("hint", ""), part_of_speech="noun",
            topics=["animals", item["category"]],
            source_url=f"https://en.wikipedia.org/wiki/"
                       f"{urllib.parse.quote(item['wiki'].replace(' ', '_'))}",
        )
        if err:
            return {"error": f"could not save to your vocabulary: {err}"}

        async with self._prog_lock:
            prog = self.load_progress()
            entry = prog.setdefault(slug, {})
            entry["saved"] = True
            entry.setdefault("srs", {})  # starring also enrols it for review
            self.save_progress(prog)

        self.spawn_background(
            self.emit("picture-dict:word_saved", {"slug": slug, "word": item["name"]}),
            label="picture-dict word_saved",
        )
        return {"ok": True, "slug": slug, "word": item["name"], "result": res}

    @web_route("POST", "/api/save-word")
    async def api_save_word(self, request):
        body = await request.json() if await request.body() else {}
        return await self.save_to_vocabulary(str(body.get("slug") or ""))

    @web_route("POST", "/api/unsave")
    async def api_unsave(self, request):
        """Clears our star only. Never deletes the vault note — the learner made
        that note deliberately, and this button does not mean 'delete my word'."""
        body = await request.json() if await request.body() else {}
        slug = str(body.get("slug") or "")
        if slug not in self.items:
            return {"error": f"unknown animal '{slug}'"}
        async with self._prog_lock:
            prog = self.load_progress()
            prog.setdefault(slug, {})["saved"] = False
            self.save_progress(prog)
        return {"ok": True, "slug": slug}

    # ─── Verbs ───────────────────────────────────────────────────────

    async def lookup(self, name: str = "") -> dict:
        item = catalog.resolve_name(self.items, name)
        if not item:
            return {"error": f"no animal called '{name}' in the pack"}
        return self._item_payload(item["slug"], self.load_progress())

    async def voice_lookup(self, name: str = "") -> dict:
        item = catalog.resolve_name(self.items, name)
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
            "link": {"text": "Open in Picture Dictionary",
                     "href": f"/picture-dict/#{item['slug']}"},
        }

    # ─── CLI ─────────────────────────────────────────────────────────

    @cli_command("picture-dict", "Browse and review the picture dictionary")
    async def cli(self, action: str = "status", arg: str = "") -> str:
        action = (action or "status").lower()
        if action == "status":
            cov, st = self.coverage(), self.progress_stats()
            return (f"{cov['total']} animals · {cov['with_photo']} with photos · "
                    f"{st['learning']} in review · {st['due']} due today")
        if action == "due":
            res = await self.srs_due(limit=20)
            if not res["cards"]:
                return "Nothing due."
            return "\n".join(f"  {c['name']:<16} {c['chinese']}" for c in res["cards"])
        if action == "list":
            slugs = self.by_category.get(arg, self.order) if arg else self.order
            return "\n".join(f"  {self.items[s]['emoji']} {self.items[s]['name']:<16} "
                             f"{self.items[s]['chinese']}" for s in slugs)
        if action == "prefetch":
            return str(await self.start_prefetch())
        return "usage: eos picture-dict [status|due|list <category>|prefetch]"

    # ─── Bindings ────────────────────────────────────────────────────
    # Every helper-module method reachable as self.X must be listed here.
    # A missing line is a NameError that only fires at call time, and for a
    # @web_route it means the endpoint silently never exists.

    # images.py
    load_index = _images.load_index
    save_index = _images.save_index
    image_url = _images.image_url
    slugs_with_image = _images.slugs_with_image
    image_credit = _images.image_credit
    coverage = _images.coverage
    start_prefetch = _images.start_prefetch
    _prefetch_loop = _images._prefetch_loop
    _fetch_one = _images._fetch_one
    _lookup_lead_files = _images._lookup_lead_files
    _lookup_file_info = _images._lookup_file_info
    _download = _images._download
    _api_get = _images._api_get
    api_image = _images.api_image
    api_prefetch = _images.api_prefetch
    api_prefetch_status = _images.api_prefetch_status
    api_prefetch_cancel = _images.api_prefetch_cancel
    api_refetch = _images.api_refetch

    # progress.py
    load_progress = _progress.load_progress
    save_progress = _progress.save_progress
    record_answer = _progress.record_answer
    enroll = _progress.enroll
    srs_due = _progress.srs_due
    srs_grade = _progress.srs_grade
    progress_stats = _progress.progress_stats
    api_srs_due = _progress.api_srs_due
    api_srs_grade = _progress.api_srs_grade
    api_srs_enroll = _progress.api_srs_enroll
    api_srs_unenroll = _progress.api_srs_unenroll
    panel_due = _progress.panel_due
    voice_due = _progress.voice_due

    # quiz.py
    api_quiz_start = _quiz.api_quiz_start
    api_quiz_answer = _quiz.api_quiz_answer
    api_quiz_finish = _quiz.api_quiz_finish
    _session_path = _quiz._session_path
    _prune_sessions = _quiz._prune_sessions

    # speak.py
    api_say = _speak.api_say
    api_clip = _speak.api_clip
    api_speak_upload = _speak.api_speak_upload
    api_speak_attempt = _speak.api_speak_attempt

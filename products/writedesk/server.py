"""WriteDesk mini-server — the daemon-shaped backend for the standalone exe.

Serves the writing-editor + jianpu pages (copied verbatim-with-patches from
``apps/public/standard/`` by ``build.py``) and implements ONLY the API surface
those two pages call. No EmptyOS import — this product must freeze into a
small PyInstaller exe with zero kernel/vault machinery.

Storage: plain markdown files with EmptyOS-compatible frontmatter (block-style
tags) under the user's Documents folder, so the files can be dropped into a
real vault later without conversion.

AI calls (Whisper dictation, polish/lint, UI translation fallback) go straight
to the OpenAI REST API via httpx using the key from config.toml.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

_ILLEGAL_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.S)
_FENCE_RE = re.compile(r"```jianpu[ \t]*\n(.*?)```", re.S)

OPENAI_BASE = "https://api.openai.com/v1"

_I18N_SYSTEM = (
    "You translate short UI strings from English to {language}. "
    "Reply with STRICT JSON: an object mapping EACH input string to its translation. "
    "Keep translations short and natural for buttons/labels. Do NOT translate "
    "placeholders like {{word}}, file paths, or proper nouns. Output ONLY the JSON object."
)


# ── tiny frontmatter (no YAML dep; flat keys + block-style tag lists) ──────

def parse_fm(content: str) -> tuple[dict, str]:
    m = _FM_RE.match(content)
    if not m:
        return {}, content
    fm: dict = {}
    last_list_key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\s+-\s+", line) and last_list_key:
            fm[last_list_key].append(line.split("-", 1)[1].strip())
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"')
            if v == "":
                fm[k] = []
                last_list_key = k
            else:
                fm[k] = v
                last_list_key = None
    return fm, content[m.end():]


def serialize_fm(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines.extend(f"  - {item}" for item in v)
        elif isinstance(v, str) and (":" in v or '"' in v or "[[" in v):
            lines.append(f'{k}: "{v}"')
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def word_count(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    latin = len([w for w in _CJK_RE.sub(" ", text).split() if w])
    return cjk + latin


def safe_filename(folder: Path, title: str, fallback_prefix: str) -> str:
    stem = _ILLEGAL_FN.sub("", (title or "").strip()).strip(". ")[:60]
    if not stem:
        stem = f"{fallback_prefix}-{uuid.uuid4().hex[:8]}"
    name, n = stem, 2
    while (folder / f"{name}.md").exists():
        name = f"{stem}-{n}"
        n += 1
    return f"{name}.md"


# ── note store (shared by articles + songs) ────────────────────────────────

class NoteStore:
    def __init__(self, folder: Path, tag: str, meta_fields: tuple[str, ...]):
        self.folder = folder
        self.tag = tag
        self.meta_fields = meta_fields

    def _path(self, filename: str) -> Path:
        name = Path(filename).name  # traversal guard
        if not name.endswith(".md"):
            name += ".md"
        return self.folder / name

    def list(self) -> list[dict]:
        if not self.folder.exists():
            return []
        out = []
        for p in self.folder.glob("*.md"):
            try:
                fm, _ = parse_fm(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            row = {"file": p.name}
            for f in self.meta_fields:
                row[f] = fm.get(f, "")
            out.append(row)
        out.sort(key=lambda r: str(r.get("updated", "")), reverse=True)
        return out

    def create(self, title: str, extra_fm: dict, body: str, fallback_prefix: str) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(self.folder, title, fallback_prefix)
        today = date.today().isoformat()
        fm = {"title": title, **extra_fm, "created": today, "updated": today,
              "tags": [self.tag]}
        self._path(filename).write_text(
            serialize_fm(fm) + "\n\n" + body, encoding="utf-8")
        return filename

    def read(self, filename: str) -> tuple[dict, str] | None:
        p = self._path(filename)
        if not p.exists():
            return None
        fm, body = parse_fm(p.read_text(encoding="utf-8"))
        return fm, body

    def write(self, filename: str, fm: dict, body: str):
        self._path(filename).write_text(
            serialize_fm(fm) + "\n\n" + body.lstrip("\n"), encoding="utf-8")

    def delete(self, filename: str) -> bool:
        p = self._path(filename)
        if not p.exists():
            return False
        p.unlink()
        return True


# ── OpenAI helpers ─────────────────────────────────────────────────────────

async def openai_chat(api_key: str, system: str, user: str, *, temperature: float = 0.3,
                      model: str = "gpt-4o-mini") -> str:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{OPENAI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "temperature": temperature,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def openai_whisper(api_key: str, audio: bytes, filename: str, language: str) -> str:
    data = {"model": "whisper-1"}
    if language:
        data["language"] = language
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{OPENAI_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (filename or "audio.webm", audio)},
        )
        r.raise_for_status()
        return r.json().get("text", "")


def parse_llm_json(raw: str):
    raw = (raw or "").strip()
    raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw, flags=re.S).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


# ── app factory ────────────────────────────────────────────────────────────

# Injected into every served page: the open window pings the server so the
# launcher can tie server lifetime to "a window is still using me" instead of
# the Edge process handle (Edge re-execs / hands off to an existing instance,
# so the spawned process often exits while the window lives on).
_PING_SCRIPT = ("<script>setInterval(function(){"
                "fetch('/api/ping').catch(function(){});},3000);</script>")


def create_app(cfg: dict, assets: Path, app_dir: Path) -> FastAPI:
    """cfg: parsed config.toml; assets: bundled assets dir; app_dir: exe dir
    (writable — settings.json + i18n cache live here)."""
    app = FastAPI(title="WriteDesk", docs_url=None, redoc_url=None)
    app.state.last_seen = time.time()

    @app.middleware("http")
    async def _touch(request, call_next):
        app.state.last_seen = time.time()
        return await call_next(request)

    def _page(name: str) -> str:
        html = (assets / name).read_text(encoding="utf-8")
        if "</body>" in html:
            return html.replace("</body>", _PING_SCRIPT + "\n</body>", 1)
        return html + _PING_SCRIPT

    data_root = Path(cfg.get("data_dir") or (Path.home() / "Documents" / "WriteDesk"))
    articles = NoteStore(data_root / "articles", "article",
                         ("title", "status", "created", "updated", "words"))
    songs = NoteStore(data_root / "jianpu", "jianpu",
                      ("title", "key", "meter", "tempo", "created", "updated"))

    api_key = str(cfg.get("openai_api_key") or "").strip()
    language = str(cfg.get("language") or "zh").strip()

    prompts = json.loads((assets / "prompts.json").read_text(encoding="utf-8"))

    settings_file = app_dir / "settings.json"
    settings_lock = threading.Lock()

    def load_settings() -> dict:
        try:
            return json.loads(settings_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_settings(d: dict):
        with settings_lock:
            settings_file.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    def setting(key: str, default):
        return load_settings().get(key, default)

    i18n_file = app_dir / f"i18n-{language}.json"
    i18n_lock = threading.Lock()

    def load_i18n() -> dict:
        try:
            return json.loads(i18n_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ── pages + static ──
    @app.get("/", response_class=HTMLResponse)
    async def home():
        return _page("home.html")

    @app.get("/writing-editor/", response_class=HTMLResponse)
    async def writing_page():
        return _page("writing-editor.html")

    @app.get("/jianpu/", response_class=HTMLResponse)
    async def jianpu_page():
        return _page("jianpu.html")

    @app.get("/jianpu/pages/jianpu-render.js")
    async def render_js():
        return FileResponse(assets / "jianpu-render.js", media_type="text/javascript")

    @app.get("/jianpu/pages/print.html", response_class=HTMLResponse)
    async def jianpu_print():
        return _page("print.html")

    @app.get("/writing-editor/print.html", response_class=HTMLResponse)
    async def article_print():
        return _page("article-print.html")

    @app.get("/static/{name}")
    async def static_file(name: str):
        p = assets / "static" / Path(name).name
        if not p.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        mt = {"css": "text/css", "js": "text/javascript"}.get(p.suffix.lstrip("."),
                                                              "application/octet-stream")
        return FileResponse(p, media_type=mt)

    @app.get("/favicon.ico")
    async def favicon():
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<text y="52" font-size="52">✍️</text></svg>')
        return Response(svg, media_type="image/svg+xml")

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    # ── daemon-shape stubs eos.js probes at boot ──
    @app.get("/api/health")
    async def health(full: bool = False):
        return {"ok": True, "status": "ok", "app": "writedesk"}

    @app.get("/api/apps")
    async def apps_list():
        return {"apps": [
            {"id": "writing-editor", "name": "Writing", "icon": "✍️", "web": "/writing-editor/"},
            {"id": "jianpu", "name": "Jianpu", "icon": "🎼", "web": "/jianpu/"},
        ]}

    @app.get("/api/apps/clusters")
    async def clusters():
        return []

    @app.get("/api/jobs")
    async def jobs():
        return []

    @app.get("/api/presentation/state")
    async def presentation():
        return {"enabled": False}

    @app.get("/app-analytics/api/ranking")
    async def ranking():
        return {"ranking": []}

    @app.get("/api/apps/{app_id}")
    async def app_info(app_id: str):
        return {"id": app_id, "name": app_id, "running": True}

    @app.get("/api/shortcuts")
    async def shortcuts():
        return {"shortcuts": []}

    @app.get("/tour/api/steps")
    async def tour_steps():
        return {"steps": []}

    @app.get("/api/sdk/timeline-apps")
    async def timeline_apps():
        return {"apps": []}

    @app.get("/api/demo/status")
    async def demo_status():
        return {"enabled": False}

    @app.get("/api/think-status")
    async def think_status():
        return {"ok": True, "providers": []}

    @app.get("/manifest.webmanifest")
    async def webmanifest():
        return {"name": "WriteDesk", "short_name": "WriteDesk",
                "start_url": "/", "display": "standalone", "icons": []}

    @app.get("/sw.js")
    async def sw():
        return Response("// no service worker in WriteDesk\n",
                        media_type="text/javascript")

    @app.post("/api/quit")
    async def quit_app():
        """Hard-exit the process (localhost-only server; used by tests and as
        a future in-app Quit affordance)."""
        import os
        import threading as _t
        _t.Timer(0.3, lambda: os._exit(0)).start()
        return {"ok": True, "bye": True}

    # ── settings (the panel + font-size reads) ──
    @app.get("/settings/api/config")
    async def settings_config():
        return {"settings": load_settings()}

    @app.get("/settings/api/get")
    async def settings_get(key: str = ""):
        return {"key": key, "value": load_settings().get(key)}

    @app.post("/settings/api/set-bulk")
    async def settings_set_bulk(request: Request):
        body = await request.json()
        d = load_settings()
        for k, v in (body.get("settings") or body or {}).items():
            if isinstance(k, str):
                d[k] = v
        save_settings(d)
        return {"ok": True}

    # ── i18n (cache + OpenAI fallback; fail-open) ──
    @app.get("/api/i18n/lang")
    async def i18n_lang():
        return {"lang": language, "rtl": False}

    @app.post("/api/i18n/batch")
    async def i18n_batch(request: Request):
        body = await request.json()
        strings = [s for s in (body.get("strings") or []) if isinstance(s, str)]
        cache = load_i18n()
        missing = [s for s in strings if s not in cache]
        if missing and api_key:
            lang_name = {"zh": "Simplified Chinese"}.get(language, language)
            try:
                raw = await openai_chat(
                    api_key,
                    _I18N_SYSTEM.format(language=lang_name),
                    json.dumps(missing, ensure_ascii=False),
                    temperature=0.2,
                )
                got = parse_llm_json(raw) or {}
                for s in missing:
                    if isinstance(got.get(s), str) and got[s].strip():
                        cache[s] = got[s].strip()
                with i18n_lock:
                    i18n_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
            except Exception:
                pass  # fail-open: untranslated chrome beats a broken page
        return {"translations": {s: cache[s] for s in strings if s in cache}}

    # ── articles ──
    @app.get("/writing-editor/api/articles")
    async def articles_list():
        return {"articles": articles.list()}

    @app.post("/writing-editor/api/articles")
    async def articles_create(request: Request):
        body = await request.json()
        title = (body.get("title") or "").strip()
        if not title:
            return {"error": "title is required"}
        f = articles.create(title, {"status": "draft", "words": 0}, "", "article")
        return {"ok": True, "file": f}

    @app.get("/writing-editor/api/articles/{file}")
    async def articles_detail(file: str):
        got = articles.read(file)
        if not got:
            return {"error": "article not found"}
        fm, body = got
        return {"file": articles._path(file).name, "title": fm.get("title", ""),
                "updated": fm.get("updated", ""), "words": fm.get("words", 0),
                "body": body.strip("\n")}

    @app.post("/writing-editor/api/articles/{file}")
    async def articles_save(file: str, request: Request):
        got = articles.read(file)
        if not got:
            return {"error": "article not found"}
        fm, body = got
        data = await request.json()
        if data.get("body") is not None:
            body = str(data["body"])
            fm["words"] = word_count(body)
        if data.get("title"):
            fm["title"] = str(data["title"]).strip()
        fm["updated"] = date.today().isoformat()
        articles.write(file, fm, body.rstrip() + "\n")
        return {"ok": True, "file": file, "words": fm.get("words")}

    @app.delete("/writing-editor/api/articles/{file}")
    async def articles_delete(file: str):
        return {"ok": True} if articles.delete(file) else {"error": "article not found"}

    # ── dictation ──
    @app.post("/writing-editor/api/transcribe")
    async def transcribe(request: Request):
        if not api_key:
            return {"error": "no API key configured — fill openai_api_key in config.toml"}
        form = await request.form()
        audio = form.get("audio")
        if audio is None:
            return {"error": "no audio file"}
        try:
            blob = await audio.read()
            lang = str(setting("writing-editor.dictation_language", language) or "")
            text = await openai_whisper(api_key, blob, getattr(audio, "filename", ""), lang)
            return {"text": text}
        except Exception as e:
            return {"error": f"transcription failed: {e}"}

    # ── revise / lint / options (same prompts as the EmptyOS app, extracted
    #    at build time — see build.py) ──
    @app.get("/writing-editor/api/options")
    async def options():
        return {
            "audiences": [{"id": k, "desc": v} for k, v in prompts["AUDIENCES"].items()],
            "tones": [{"id": k, "desc": v} for k, v in prompts["TONES"].items()],
            "default_audience": "peer", "default_tone": "default",
            "max_draft_len": 5000,
        }

    @app.post("/writing-editor/api/revise")
    async def revise(request: Request):
        if not api_key:
            return {"ok": False, "error": "no API key configured"}
        body = await request.json()
        draft = (body.get("draft") or "").strip()
        if not draft:
            return {"ok": False, "error": "draft required"}
        if len(draft) > 5000:
            return {"ok": False, "error": "draft too long (>5000 chars)"}
        audience = body.get("audience") or "external"
        tone = body.get("tone") or "default"
        a_desc = prompts["AUDIENCES"].get(audience, "")
        t_desc = prompts["TONES"].get(tone, "")
        try:
            raw = await openai_chat(
                api_key, prompts["REVISE_SYSTEM"],
                f"Audience: {audience} — {a_desc}\nTone: {tone} — {t_desc}\n\nDraft:\n{draft}",
                temperature=0.3,
            )
        except Exception as e:
            return {"ok": False, "error": f"think failed: {e}"}
        parsed = parse_llm_json(raw) or {}
        return {"ok": True, "audience": audience, "tone": tone,
                "revised": str(parsed.get("revised") or "").strip(),
                "edits": parsed.get("edits") if isinstance(parsed.get("edits"), list) else [],
                "pattern": parsed.get("pattern") if isinstance(parsed.get("pattern"), dict) else None}

    @app.post("/writing-editor/api/lint")
    async def lint(request: Request):
        if not api_key:
            return {"ok": False, "error": "no API key configured"}
        body = await request.json()
        draft = (body.get("draft") or "").strip()
        if not draft:
            return {"ok": False, "error": "draft required"}
        try:
            raw = await openai_chat(api_key, prompts["LINT_SYSTEM"],
                                    f"Draft:\n{draft}", temperature=0.2)
        except Exception as e:
            return {"ok": False, "error": f"think failed: {e}"}
        parsed = parse_llm_json(raw) or {}
        findings = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
        return {"ok": True, "findings": findings}

    # ── songs ──
    @app.get("/jianpu/api/songs")
    async def songs_list():
        return {"songs": songs.list()}

    @app.post("/jianpu/api/songs")
    async def songs_create(request: Request):
        body = await request.json()
        title = (body.get("title") or "").strip()
        if not title:
            return {"error": "title is required"}
        extra = {"key": (body.get("key") or "1=C").strip(),
                 "meter": (body.get("meter") or "4/4").strip(),
                 "tempo": int(body.get("tempo") or 80)}
        f = songs.create(title, extra, "## Source\n\n```jianpu\n\n```\n", "song")
        return {"ok": True, "file": f}

    @app.get("/jianpu/api/songs/{file}")
    async def songs_detail(file: str):
        got = songs.read(file)
        if not got:
            return {"error": "song not found"}
        fm, body = got
        m = _FENCE_RE.search(body)
        return {"file": songs._path(file).name, "title": fm.get("title", ""),
                "key": fm.get("key", ""), "meter": fm.get("meter", ""),
                "tempo": int(fm.get("tempo") or 0), "updated": fm.get("updated", ""),
                "source": m.group(1).rstrip("\n") if m else ""}

    @app.post("/jianpu/api/songs/{file}")
    async def songs_save(file: str, request: Request):
        got = songs.read(file)
        if not got:
            return {"error": "song not found"}
        fm, body = got
        data = await request.json()
        if "source" in data:
            new_fence = f"```jianpu\n{str(data.get('source') or '').rstrip()}\n```"
            if _FENCE_RE.search(body):
                body = _FENCE_RE.sub(lambda _m: new_fence, body, count=1)
            else:
                body = body.rstrip() + f"\n\n## Source\n\n{new_fence}\n"
        for k in ("title", "key", "meter"):
            if data.get(k):
                fm[k] = str(data[k]).strip()
        if data.get("tempo"):
            fm["tempo"] = int(data["tempo"])
        fm["updated"] = date.today().isoformat()
        songs.write(file, fm, body)
        return {"ok": True, "file": file}

    @app.delete("/jianpu/api/songs/{file}")
    async def songs_delete(file: str):
        return {"ok": True} if songs.delete(file) else {"error": "song not found"}

    return app

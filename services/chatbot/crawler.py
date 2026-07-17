"""Bounded website-source crawler for Studio-managed knowledge."""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .security import fence_external_text, validate_public_url

MAX_PAGES = 25
MAX_BODY_BYTES = 1_000_000


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.title: list[str] = []
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title": self._in_title = True
        if tag in {"script", "style", "noscript", "svg"}: self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title": self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._skip: self._skip -= 1

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value or self._skip: return
        self.text.append(value)
        if self._in_title: self.title.append(value)


def _cache_path(site_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", site_id)
    if safe != site_id or not safe: raise ValueError("invalid site id")
    path = Path(os.environ.get("CHATBOT_DATA_DIR", "./data")) / "corpora"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{safe}.json"


async def _fetch(url: str, *, allow_local: bool) -> tuple[str, str]:
    current = validate_public_url(url, allow_local=allow_local)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, headers={"User-Agent": "EmptyOS-Chatbot-Studio/1.0"}) as client:
        for _ in range(5):
            response = await client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location: raise ValueError("redirect missing location")
                current = validate_public_url(urljoin(current, location), allow_local=allow_local)
                continue
            response.raise_for_status()
            if len(response.content) > MAX_BODY_BYTES: raise ValueError("source exceeds 1 MB")
            return current, response.text
    raise ValueError("too many redirects")


def _sitemap_urls(xml_text: str) -> list[str]:
    try: root = ET.fromstring(xml_text)
    except ET.ParseError: return []
    return [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text][:MAX_PAGES]


async def build_corpus(site_id: str, sources: list[str], *, ttl_seconds: int = 3600, force: bool = False) -> dict:
    path = _cache_path(site_id)
    if path.exists() and not force and time.time() - path.stat().st_mtime < ttl_seconds:
        return json.loads(path.read_text(encoding="utf-8"))
    allow_local = os.environ.get("CHATBOT_ALLOW_LOCAL_SOURCES") == "1"
    queue = list(dict.fromkeys(sources))[:MAX_PAGES]
    pages: list[tuple[str, str]] = []
    while queue and len(pages) < MAX_PAGES:
        source = queue.pop(0)
        final_url, text = await _fetch(source, allow_local=allow_local)
        if final_url.lower().endswith(".xml") or "<urlset" in text[:500] or "<sitemapindex" in text[:500]:
            for item in _sitemap_urls(text):
                validate_public_url(item, allow_local=allow_local)
                if item not in queue: queue.append(item)
            continue
        pages.append((final_url, text))
    chunks = []
    for index, (url, html) in enumerate(pages):
        parser = _TextParser(); parser.feed(html)
        title = " ".join(parser.title) or urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).hostname
        text = " ".join(parser.text)[:24000]
        if text:
            chunks.append({"id": f"web:{index + 1}", "type": "web", "slug": "", "title": title, "section": "", "tags": [], "url": url, "text": fence_external_text(text, source=url)})
    payload = {"site_name": site_id, "generated_at": datetime.now(UTC).isoformat(), "chunks": chunks, "faqs": []}
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8"); tmp.replace(path)
    return payload


def invalidate_corpus(site_id: str) -> None:
    path = _cache_path(site_id)
    if path.exists(): path.unlink()

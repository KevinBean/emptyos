"""Capture all songs from a Suno user profile to local files.

For each song on /<profile>?page=songs, writes:
  - <output>/songs.json                   — id+title list
  - <output>/metadata/{id}.json           — title, voice, version, created, tags, plays, likes, lyrics
  - <output>/screenshots/{id}.png         — full-page screenshot

Idempotent — skips ids whose metadata + screenshot already exist.

Requires: pip install playwright && playwright install chromium

Usage:
    python scripts/suno_capture.py --profile kevinbean --output scratch/suno-songs
    python scripts/suno_capture.py --profile kevinbean --output scratch/suno-songs --start 19 --delay 5
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DATE_RE = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d+,\s+\d{4}\s+at\s+\d+:\d+\s*[AP]M"
)
VERSION_RE = re.compile(r"^v\d(\.\d)?$")


def make_log(log_path: Path):
    def _log(msg: str) -> None:
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(msg.encode("utf-8", errors="replace") + b"\n")
            sys.stdout.flush()
        with log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    return _log


async def collect_song_list(page, profile: str) -> list[dict]:
    url = f"https://suno.com/@{profile}?page=songs"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    return await page.evaluate(
        r"""async () => {
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            let lastCount = 0, stable = 0;
            for (let i = 0; i < 80; i++) {
                window.scrollBy(0, 5000);
                document.querySelectorAll('*').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight;
                });
                await sleep(700);
                const c = document.querySelectorAll('a[href^="/song/"]').length;
                if (c === lastCount) { stable++; if (stable >= 4) break; } else stable = 0;
                lastCount = c;
            }
            const seen = new Map();
            document.querySelectorAll('a[href^="/song/"]').forEach(a => {
                const m = a.getAttribute('href').match(/^\/song\/([0-9a-f-]+)/);
                if (!m) return;
                const id = m[1];
                const t = (a.textContent || '').trim();
                if (t && !seen.get(id)) seen.set(id, t);
            });
            return Array.from(seen, ([id, title]) => ({id, title}));
        }"""
    )


async def extract_song(page) -> dict:
    return await page.evaluate(
        r"""() => {
            const cleanWS = s => (s || '').replace(/\s+/g, ' ').trim();
            let lyrics = '';
            const ta = document.querySelector('textarea');
            if (ta && ta.value && ta.value.length > 40) lyrics = ta.value;
            if (!lyrics) {
                const cands = Array.from(document.querySelectorAll('div, p, pre'));
                let best = '';
                for (const el of cands) {
                    const t = el.innerText || '';
                    const nl = (t.match(/\n/g) || []).length;
                    if (nl >= 4 && t.length > best.length && t.length < 8000) best = t;
                }
                lyrics = best;
            }
            const tags = Array.from(document.querySelectorAll('a[href^="/style/"], a[href^="/voice/"]'))
                .map(a => cleanWS(a.textContent)).filter(Boolean);
            const voiceLink = document.querySelector('a[href^="/voice/"]');
            const voice = voiceLink ? cleanWS(voiceLink.textContent) : null;
            const statsBtns = Array.from(document.querySelectorAll('button'))
                .map(b => (b.textContent || '').trim())
                .filter(t => /^\d[\d,]*$/.test(t));
            return {
                lyrics: lyrics.slice(0, 8000),
                tags: tags.slice(0, 16),
                voice: voice,
                stats_digits: statsBtns.slice(0, 6),
                body: document.body.innerText.slice(0, 20000),
            };
        }"""
    )


def parse_stats(digits: list[str]) -> dict:
    plays = likes = comments = None
    if len(digits) >= 1:
        try: plays = int(digits[0].replace(",", ""))
        except: pass
    if len(digits) >= 2:
        try: likes = int(digits[1].replace(",", ""))
        except: pass
    if len(digits) >= 3:
        try: comments = int(digits[2].replace(",", ""))
        except: pass
    return {"plays": plays, "likes": likes, "comments": comments}


async def capture_one(page, song: dict, profile: str, meta_dir: Path, shot_dir: Path, delay: float, log) -> bool:
    sid = song["id"]
    meta_path = meta_dir / f"{sid}.json"
    shot_path = shot_dir / f"{sid}.png"
    if meta_path.exists() and shot_path.exists():
        log(f"  skip (already captured): {song['title']}")
        return True

    url = f"https://suno.com/song/{sid}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log(f"  ERR navigate {sid}: {e}")
        return False
    await asyncio.sleep(4.5)

    try:
        data = await extract_song(page)
    except Exception as e:
        log(f"  ERR extract {sid}: {e}")
        return False

    body = data.pop("body", "") or ""
    dm = DATE_RE.search(body)
    created = dm.group(0) if dm else None
    version = None
    for tok in body.split():
        t = tok.strip().rstrip(".,)")
        if VERSION_RE.match(t):
            version = t
            break
    stats = parse_stats(data.get("stats_digits") or [])

    record = {
        "id": sid,
        "title": song["title"],
        "url": url,
        "artist": profile,
        "voice": data.get("voice"),
        "version": version,
        "created": created,
        "tags": data.get("tags") or [],
        **stats,
        "lyrics": data.get("lyrics") or "",
        "screenshot": f"screenshots/{sid}.png",
    }

    try:
        await page.screenshot(path=str(shot_path), full_page=True)
    except Exception as e:
        log(f"  ERR screenshot {sid}: {e}")
        return False

    meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  ok  {song['title']} | {version} | plays={stats['plays']} likes={stats['likes']}")
    await asyncio.sleep(delay)
    return True


async def run(args) -> int:
    out = Path(args.output).resolve()
    meta_dir = out / "metadata"
    shot_dir = out / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(exist_ok=True)
    shot_dir.mkdir(exist_ok=True)
    log = make_log(out / "capture.log")
    log(f"[start] profile={args.profile} output={out}")

    songs_file = out / "songs.json"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 820})
        page = await ctx.new_page()

        # Build/refresh song list
        if songs_file.exists() and not args.refresh_list:
            data = json.loads(songs_file.read_text(encoding="utf-8"))
            songs = data["songs"]
            log(f"[list] reusing {len(songs)} from {songs_file.name} (pass --refresh-list to rescrape)")
        else:
            log("[list] scraping profile…")
            songs = await collect_song_list(page, args.profile)
            songs_file.write_text(
                json.dumps({"profile": args.profile, "count": len(songs), "songs": songs}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log(f"[list] saved {len(songs)} songs")

        todo = songs[args.start - 1:]
        log(f"[capture] total={len(songs)} starting_at={args.start} todo={len(todo)} delay={args.delay}s")
        ok = fail = 0
        for i, song in enumerate(todo, start=args.start):
            log(f"[{i}/{len(songs)}] {song['title']}  ({song['id']})")
            try:
                if await capture_one(page, song, args.profile, meta_dir, shot_dir, args.delay, log):
                    ok += 1
                else:
                    fail += 1
            except KeyboardInterrupt:
                log("[interrupt]")
                break
            except Exception as e:
                log(f"  ERR {song['id']}: {e}")
                fail += 1
        await browser.close()
    log(f"[done] ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture a Suno profile's songs to JSON + screenshots.")
    ap.add_argument("--profile", required=True, help="Suno @handle (no @)")
    ap.add_argument("--output", required=True, help="Output dir (will be created)")
    ap.add_argument("--start", type=int, default=1, help="1-based song index to resume from")
    ap.add_argument("--delay", type=float, default=5.0, help="Seconds between songs (be kind to Suno)")
    ap.add_argument("--refresh-list", action="store_true", help="Re-scrape songs.json from the profile")
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())

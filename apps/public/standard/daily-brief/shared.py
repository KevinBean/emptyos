"""daily-brief — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (articles/feeds/markets/snapshots) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations
import re
import html

from emptyos.sdk.utils import first_prose_line


_UA = "EmptyOS-DailyBrief/0.1"

_MAX_FEED_BYTES = 4 * 1024 * 1024  # cap per-feed body — bounds amplification + runaway feeds

_LANG = {
    "zh": ("Chinese (简体中文)", "中文"),
    "en": ("English", "English"),
}

DIGEST_SYSTEM = """You are an editor writing a short digest of the articles one person hand-picked from their daily briefs.

You are given their picked items (title · source · the one-line note that made them keep it). Write a tight digest they can read in under two minutes — this is the payoff for collecting them.

RULES
- Write ONLY in {lang}. ({lang_name})
- Open with a single bold one-line `**TL;DR:**` capturing the through-line across their picks.
- Group items under 2-4 short `## ` theme headers ONLY if they cluster; otherwise a flat list. Never a header with fewer than 2 items.
- One item per line: `- **[exact title](exact url)** — one sharp sentence on what it adds or why it earned a spot`. Copy the title and URL VERBATIM; never invent or alter a URL.
- {focus_clause}
- End with one short italic line naming the single thread worth acting on or watching next.

NEVER DO
- Never invent items, numbers, titles, or URLs not present in the input.
- Never editorialize or add hype words ("groundbreaking", "game-changer").
- Never add a preamble, sign-off, or "here is your digest" wrapper. Output the digest markdown directly."""

ARTICLE_SUMMARY_SYSTEM = """You summarize one news article for a reader's inbox from its title and feed blurb.

RULES
- Write ONLY in {lang}. ({lang_name})
- 2-3 sentences. First: what it covers, concretely (a number, a name, the specific claim — not "a tech article"). Second: the takeaway or why it's worth their time.
- If the title + blurb are too thin to be sure, say what it appears to cover and flag the uncertainty — never invent details, numbers, or quotes.
- No "this article", no preamble, no sign-off. Output the summary text directly."""

PERSONALIZE_SYSTEM = """You tailor a daily brief to one WHOLE person by reading a compact, metadata-only profile of their work and life.

You are given the NAMES of their active projects, their areas of responsibility, top-level resource folders, and their most-used note tags (with counts) — never the contents of any note. From these, infer the lens that should rank and frame their daily brief.

Return ONLY a JSON object, nothing else:
{"focus": "<comma-separated topics, 6-10 of them>", "dimensions": {"work": "<2-4 topics>", "interests": "<2-4 topics>", "growth": "<1-3 topics>"}, "rationale": "<one sentence naming the signals that drove it>"}

COVER THE WHOLE PERSON — the focus must span three dimensions, not just work:
- work: their profession / career direction — what they build and the field they work in.
- interests: what they do for its own sake — music, books, games, creative writing, hobbies.
- growth: how they are developing themselves — a language they are learning, a practice or philosophy, health, speaking or writing skill.
A focus that is all work is WRONG. If a dimension is genuinely absent from the profile give it fewer topics, but look hard first — project and area names usually reveal all three.

WEIGHT THE SIGNALS CORRECTLY — do NOT just echo the biggest tag counts:
- The authored NAMES (projects, areas, resource folders) are the STRONGEST signal — a person chose to create each one. Lead from these.
- Tag counts are NOISY. A very high count often means a one-off bulk data import (sensor / asset / dataset tags), not a personal interest. Treat a huge count as WEAK evidence, and ignore tags that look like machine-generated dataset labels (status words, equipment/asset types, measurement categories) rather than something a person would say they care about.
- A small authored project outweighs a 4000-count import tag.

RULES
- `focus` topics are a reader lens: concrete enough to rank and frame content (e.g. "grid-scale storage", "AI engineering", "language learning", "Buddhist philosophy", "creative writing"). Specific domains and pursuits — not bland abstractions ("productivity", "self-improvement").
- No private specifics — no personal names, health conditions, or financial figures, even if present in the profile.
- Write topics in {lang_name} only if the profile is clearly in that language; otherwise English topic words are fine — the brief language is separate.
- If the profile is sparse, return a sensible general lens (world, tech, and a couple of broad interests) rather than inventing detail."""

_TAG_RE = re.compile(r"<[^>]+>")

_WS_RE = re.compile(r"\s+")

# ── Market pulse ───────────────────────────────────────────────────────────
# Energy-slanted defaults (Yahoo symbols), overridable via config / settings.
DEFAULT_TICKERS: list[dict] = [
    {"symbol": "FSLR", "name": "First Solar"},
    {"symbol": "ENPH", "name": "Enphase"},
    {"symbol": "NEE", "name": "NextEra Energy"},
    {"symbol": "TSLA", "name": "Tesla"},
    {"symbol": "ALB", "name": "Albemarle (lithium)"},
    {"symbol": "CL=F", "name": "Crude Oil"},
    {"symbol": "NG=F", "name": "Nat Gas"},
]

MARKET_SYSTEM = """You write a brief market pulse for one reader from pre-computed technical indicators.

You are given instruments with: last price, daily % change, RSI(14), SMA20, SMA50, MACD histogram, and a trend flag. You did NOT compute these — read them.

RULES
- Write ONLY in {lang}. ({lang_name})
- One line per instrument, max — and skip the dull ones. A pulse, not a report.
- Translate indicators into plain language: RSI>70 overbought / <30 oversold; price vs SMA20/SMA50 = short vs medium trend; MACD histogram sign = momentum. Be concrete.
- {focus_clause}
- Lead with a one-line `**TL;DR:**` of the single clearest signal across the set.
- End with exactly one short italic line in {lang}: a plain disclaimer that these are indicator readings for context only, not investment advice.

NEVER DO
- Never predict a price or target. Never say buy / sell / hold.
- Never invent numbers or instruments not in the input.
- Never hype ("to the moon", "crash"). Sober, factual reads only.
- No preamble or sign-off beyond the TL;DR and the one disclaimer line."""


def _strip_html(s: str) -> str:
    """RSS descriptions are often HTML — flatten to plain text, collapse space."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema = values[0]
    out = [ema]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    seed = deltas[:period]
    avg_gain = sum(d for d in seed if d > 0) / period
    avg_loss = sum(-d for d in seed if d < 0) / period
    for d in deltas[period:]:
        g = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd_hist(closes: list[float]) -> float | None:
    if len(closes) < 26:
        return None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    macd = [a - b for a, b in zip(e12, e26, strict=False)]
    signal = _ema_series(macd, 9)
    return macd[-1] - signal[-1]


def _compute_indicators(closes: list[float]) -> dict | None:
    closes = [float(c) for c in closes if isinstance(c, (int, float))]
    if len(closes) < 2:
        return None
    last, prev = closes[-1], closes[-2]
    change_pct = (last - prev) / prev * 100 if prev else 0.0
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    rsi = _rsi(closes)
    hist = _macd_hist(closes)
    if sma20 and sma50:
        trend = "up" if sma20 > sma50 else "down"
    elif sma20:
        trend = "up" if last > sma20 else "down"
    else:
        trend = "flat"
    return {
        "last": round(last, 2),
        "change_pct": round(change_pct, 2),
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "rsi": round(rsi, 1) if rsi is not None else None,
        "macd_hist": round(hist, 3) if hist is not None else None,
        "trend": trend,
    }


def _md_esc(s: str) -> str:
    """Escape the few characters that break a markdown link label."""
    return (s or "").replace("[", "(").replace("]", ")").strip()


_NOTIFY_MAX = 160
_NOTIFY_LEAD = {
    "zh": "今日简报已就绪",
    "en": "Your daily brief is ready",
}


def _notify_text(brief_md: str, item_count: int, locale: str = "en") -> str:
    """One line for a push notification: the lead, plus the brief's own opening.

    A nudge is read at a glance and may be *spoken* (the voice channel), so the
    line carries prose rather than markdown. Falls back to the story count alone
    when the brief has no usable prose line, which is better than pushing an
    empty string.
    """
    lead_word = _NOTIFY_LEAD.get(locale, _NOTIFY_LEAD["en"])
    lead = f"{lead_word} — {item_count} 条" if locale == "zh" else \
           f"{lead_word} — {item_count} {'story' if item_count == 1 else 'stories'}"

    # allow_heading: a brief that is nothing but a title should still say the
    # title rather than nothing at all.
    best = first_prose_line(brief_md, allow_heading=True)
    if not best:
        return lead + "."
    if len(best) > _NOTIFY_MAX:
        best = best[: _NOTIFY_MAX - 1].rstrip() + "…"
    return f"{lead}. {best}"

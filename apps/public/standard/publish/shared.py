"""publish — pure URL helpers shared across builder / deploy.

Extracted so the canonical post-URL formula lives in ONE place. It was already
duplicated inside builder.py (once for the RSS feed, once for the sitemap) and
was about to be copied a third time into deploy.py, to stamp `canonical_url`
onto `publish:deployed` for the distribution tracker. A third copy is how the
feed, the sitemap and the tracker start disagreeing about what a post's URL is —
and the tracker's whole job is to be the thing you can join external analytics
against, so a URL that doesn't match the sitemap is worse than no URL.

Pure functions only — no ``self``, no I/O, no kernel. Import freely from any
publish helper module (this file imports nothing from the app, so it cannot
cycle).
"""

from __future__ import annotations


def site_url(domain: str) -> str:
    """Absolute site root WITH a trailing slash, or "" when no domain is set.

    Mirrors builder.py's long-standing ``f"https://{domain}/" if domain else ""``.
    """
    d = (domain or "").strip().strip("/")
    return f"https://{d}/" if d else ""


def post_url(domain: str, slug: str) -> str:
    """Canonical absolute URL for one post, or "" if either part is missing.

    The single source of truth for the shape the sitemap and RSS feed already
    emit: ``https://<domain>/posts/<slug>.html``. Returns "" rather than a
    half-formed URL when there is no domain — a tracker row with an empty
    canonical_url is honest; one pointing at ``https:///posts/x.html`` is not.
    """
    base = site_url(domain)
    s = (slug or "").strip().lstrip("/")
    if not base or not s:
        return ""
    return f"{base}posts/{s}.html"

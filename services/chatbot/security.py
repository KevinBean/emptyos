"""Network and prompt-boundary helpers for merchant-provided sources."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def validate_public_url(url: str, *, allow_local: bool = False) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https")
    host = parsed.hostname.lower()
    if host == "localhost" and allow_local:
        return url
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parsed.port or 443)}
    except OSError as exc:
        raise ValueError("URL hostname could not be resolved") from exc
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if not ip.is_global:
            if allow_local and ip.is_loopback:
                continue
            raise ValueError("URL must resolve to a public address")
    return url


def fence_external_text(text: str, *, source: str) -> str:
    """Mark fetched text as inert data and defang nested boundary markers."""
    clean = (text or "").replace("<<<EXTERNAL", "< < < EXTERNAL")
    clean = clean.replace(">>>END_EXTERNAL", "> > > END_EXTERNAL")
    return f"<<<EXTERNAL_DATA source={source!r}>>>\n{clean}\n>>>END_EXTERNAL_DATA<<<"

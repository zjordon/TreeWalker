"""URL parsing helpers shared across agent / registry / skill loader.

Pure functions (standard library only, zero runtime deps). Centralized so that
``extract_host`` can be reused by anything that dispatches behavior by hostname
— e.g. the skill loader (read ``domain-skills/<host>/``).
"""
from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["extract_host"]


def extract_host(url: str | None) -> str | None:
    """Return the hostname of ``url`` (e.g. ``www.bilibili.com``).

    Returns ``None`` for empty/None input or anything that does not parse to a
    hostname. Schemeless strings that look like a host (``www.bilibili.com/x``)
    are handled by re-parsing with a ``//`` prefix, since ``urlparse`` otherwise
    treats them as a path. Start with full hostname (no eTLD+1 aggregation);
    introduce tldextract later only if needed.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host and "://" not in url:
            # schemeless (www.bilibili.com/x): urlparse treats the host as path
            parsed = urlparse("//" + url)
            host = parsed.hostname
        # reject garbage: a schemeless non-url ("not a url") parses to a host
        # containing spaces — not a real hostname.
        if not host or " " in host:
            return None
        return host
    except (ValueError, TypeError):
        return None

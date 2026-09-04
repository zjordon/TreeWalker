"""URL parsing helpers shared across agent / registry / skill loader.

Pure functions (standard library only, zero runtime deps). Centralized so that
``extract_host`` can be reused by anything that dispatches behavior by hostname
— e.g. the skill loader (read ``domain-skills/<host>/``).
"""
from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["extract_host", "extract_host_with_port"]


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


def extract_host_with_port(url: str | None) -> str | None:
    """Return the domain-skills directory key for ``url``: ``host`` or ``host_port``.

    P7 form_interaction 建议4：带显式端口的 URL 用 ``localhost_7780`` 形式的 key
    （Windows 目录名不能含 ``:``），无端口的保持裸 host（bilibili/douyin 存量目录
    不受影响）——让 localhost:7780（Magento 评测）与 localhost:5173（tw-web 前端）
    等本机不同服务各挂各的 skill，互不误注入。垃圾输入的拒绝规则同 extract_host。
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host and "://" not in url:
            parsed = urlparse("//" + url)
            host = parsed.hostname
        if not host or " " in host:
            return None
        port = parsed.port
        return f"{host}_{port}" if port else host
    except (ValueError, TypeError):
        return None

"""Image resizing helpers for screenshots (LLM-bound and on-disk).

Pillow is an OPTIONAL dependency: if unavailable, all helpers degrade
gracefully (return the original bytes). Keeps the base install slim.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore[assignment]
    _PILLOW_AVAILABLE = False
    logger.info("Pillow not installed; screenshot resizing is a no-op")


def is_resize_available() -> bool:
    """True iff Pillow is importable."""
    return _PILLOW_AVAILABLE


def resize_screenshot_bytes(data: bytes, target: tuple[int, int] | None) -> bytes:
    """Downscale screenshot bytes to fit within ``target`` (w, h), keeping aspect.

    Mirrors browser-use agent/prompts.py:360-386:
      - target is None / Pillow missing / empty data -> return data unchanged.
      - image already <= target in both dims -> unchanged (idempotent).
      - else LANCZOS-resample to fit-within (preserves aspect), re-encode as PNG.
      - ANY exception -> log warning, return original (never raises).

    Output is always PNG (per project decision: no JPEG/quality path here).
    """
    if target is None or not _PILLOW_AVAILABLE or not data:
        return data

    tw, th = target
    if tw < 100 or th < 100:
        # browser-use guards >=100px; refuse to produce postage stamps
        logger.warning("resize target %s below 100px floor, skipping", target)
        return data

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        logger.warning("resize: cannot decode image (%s), returning original", e)
        return data

    w, h = img.size
    if w <= tw and h <= th:
        return data  # idempotent: nothing to do

    # Fit-within (preserve aspect), browser-use parity
    scale = min(tw / w, th / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    try:
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning("resize failed (%s), returning original", e)
        return data

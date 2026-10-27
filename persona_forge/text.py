"""文本与临时内容小工具。"""

from __future__ import annotations

from astrbot.core.agent.message import TextPart


def mask_secret(value: str) -> str:
    """Render a configured secret without revealing it."""
    return "••••••" if str(value or "").strip() else ""


def make_temp_part(text: str) -> TextPart:
    """Mark injected context as provider-facing only, so it is not persisted.

    AstrBot ships ``mark_as_temp()``; some versions/docs spell it
    ``make_as_temp()``. Try both, and never break the conversation if neither
    exists.
    """
    part = TextPart(text=text)
    for name in ("mark_as_temp", "make_as_temp"):
        fn = getattr(part, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
            break
    return part

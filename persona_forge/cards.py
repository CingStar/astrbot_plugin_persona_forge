"""生命卡命名与归档成员安全校验。"""

from __future__ import annotations


def card_filename(scope: str, persona_id: str, ts: int) -> str:
    """Build a life-card file name for a persona or shared card."""
    if scope == "persona" and persona_id:
        return f"life-card-persona-{persona_id}-{int(ts)}.pialife"
    return f"life-card-shared-{int(ts)}.pialife"


def is_safe_card_member(name: str) -> bool:
    """Reject archive members that could escape the restore target."""
    text = str(name or "")
    if text == "manifest.json":
        return True
    if not text or text.startswith(("/", "\\")) or ":" in text:
        return False
    normalized = text.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return normalized.startswith(
        ("personas/", "profiles/", "memory/", "avatars/", "versions/", "reviews/", "wiki/")
    )

"""记忆作用域与可见性规则。"""

from __future__ import annotations


def memory_default_scope(kind: str, configured: str) -> str:
    """Pick the storage scope for a new memory item."""
    if kind == "knowledge":
        return "global"
    if kind == "session":
        return "chat"
    return configured if configured in {"chat", "persona"} else "chat"


def memory_is_visible(item, umo: str, is_group: bool, isolation: bool = True) -> bool:
    """Decide whether a memory item may be injected into the current chat."""
    if not isolation:
        return True
    kind = item.get("kind")
    scope = item.get("scope")
    item_umo = str(item.get("umo") or "")
    current = str(umo or "")
    if kind == "knowledge" or scope == "global":
        return True
    if kind == "session":
        return item_umo == current
    if scope == "chat":
        return item_umo == current
    # "persona" scope (and legacy items without a scope): private chats share,
    # groups only see what was written inside that same group.
    return (not is_group) or item_umo == current

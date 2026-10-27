"""数据目录解析与旧路径迁移判定。"""

from __future__ import annotations

from pathlib import Path


def astrbot_root_path() -> Path:
    """Return the AstrBot root directory, falling back to the process cwd."""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_root

        return Path(get_astrbot_root())
    except Exception:
        return Path.cwd()


def resolve_plugin_path(value, default_rel: str, root, legacy_raw=()) -> str:
    """Resolve a configured data path into an absolute path.

    Empty values and the historical hard-coded defaults map to
    ``data/plugin_data/astrbot_plugin_persona_forge/<default_rel>``.
    """
    raw = str(value or "").strip()
    if not raw or raw in legacy_raw:
        raw = f"data/plugin_data/astrbot_plugin_persona_forge/{default_rel}"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    return str(path)


def should_migrate_path(new_path, legacy_path, new_exists: bool, legacy_exists: bool) -> bool:
    """Only move legacy data when the new location does not exist yet."""
    if not legacy_exists or new_exists:
        return False
    try:
        return Path(new_path) != Path(legacy_path)
    except Exception:
        return str(new_path) != str(legacy_path)


def rewrite_avatar_path(value, old_root, new_root) -> str:
    """Rebase a stored avatar path after the plugin data directory moved."""
    text = str(value or "").strip()
    if not text:
        return text
    try:
        rel = Path(text).relative_to(Path(old_root))
    except Exception:
        return text
    return str(Path(new_root) / rel)

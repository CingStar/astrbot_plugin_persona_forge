"""插件常量与元信息。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_persona_forge"
PLUGIN_VERSION = "0.3.1"
AUTO_PROMPT_HEADER = "\n\n【人格专属知识（自动迭代）】"
PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
MAX_INLINE_AVATAR_BYTES = 256 * 1024
CARD_FORMAT_PERSONA = "persona-forge/persona-card"
CARD_FORMAT_SHARED = "persona-forge/shared-card"
CARD_FORMAT_VERSION = 1
DEV_NOTE_LIMIT = 200
DEV_MODE_TTL = 24 * 3600
LEGACY_DEFAULTS = {
    "profile_dir": "/AstrBot/data/persona_profiles",
    "memory_dir": "/AstrBot/data/persona_memory",
    "wiki_file": "/AstrBot/data/shared_wiki.json",
    "backup_dir": "/AstrBot/data/life_card/backups",
    "request_dir": "/AstrBot/data/life_card/requests",
    "result_dir": "/AstrBot/data/life_card/results",
}
LEGACY_DEFAULT_VALUES = tuple(LEGACY_DEFAULTS.values())

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _compute_update_id() -> str:
    """Hash the entry point plus every package module to detect code updates."""
    digest = hashlib.sha256()
    digest.update((_PLUGIN_ROOT / "main.py").read_bytes())
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


PLUGIN_UPDATE_ID = _compute_update_id()

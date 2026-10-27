"""人格工坊插件入口。

真正的逻辑都在 ``persona_forge/`` 包里；这里只做模块组合，
方便按域维护，同时保持 AstrBot 的 main.py 入口约定。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Star

try:  # AstrBot loads main.py as data.plugins.<dir>.main (package import).
    from .persona_forge.api import ApiMixin
    from .persona_forge.core import CoreMixin
    from .persona_forge.dev_mode import DevModeMixin
    from .persona_forge.events import EventsMixin
    from .persona_forge.handlers import adopt_mixin_handlers
    from .persona_forge.knowledge import KnowledgeMixin
    from .persona_forge.legacy_natural import LegacyNaturalMixin
    from .persona_forge.life_cards import LifeCardsMixin
    from .persona_forge.memory_cmds import MemoryCommandsMixin
    from .persona_forge.persona_commands import PersonaCommandsMixin
except ImportError:  # loaded as a top-level module (tools / tests)
    from persona_forge.api import ApiMixin
    from persona_forge.core import CoreMixin
    from persona_forge.dev_mode import DevModeMixin
    from persona_forge.events import EventsMixin
    from persona_forge.handlers import adopt_mixin_handlers
    from persona_forge.knowledge import KnowledgeMixin
    from persona_forge.legacy_natural import LegacyNaturalMixin
    from persona_forge.life_cards import LifeCardsMixin
    from persona_forge.memory_cmds import MemoryCommandsMixin
    from persona_forge.persona_commands import PersonaCommandsMixin


class PersonaForgePlugin(
    CoreMixin,
    MemoryCommandsMixin,
    KnowledgeMixin,
    PersonaCommandsMixin,
    DevModeMixin,
    LifeCardsMixin,
    LegacyNaturalMixin,
    ApiMixin,
    EventsMixin,
    Star,
):
    """人格工坊：人格管理、分层记忆、共享 Wiki、提示词版本、数字生命卡。"""


def _adopt_handlers() -> None:
    """Keep AstrBot handler ownership on this entry module after the split.

    Works both for package imports (``data.plugins.<dir>.main``) and for
    loaders that import main.py as a top-level module: in the latter case the
    entry path AstrBot puts in ``star_map`` is reconstructed from the plugin
    directory name.
    """
    dir_name = Path(__file__).resolve().parent.name
    owner = __name__.rsplit(".", 1)[0] if "." in __name__ else ""
    if owner.startswith("data.plugins.") or owner.startswith("astrbot.builtin_stars."):
        package = owner
    else:
        package = f"data.plugins.{dir_name}"
    entry = f"{package}.main"
    try:
        from astrbot.core.star.star_handler import star_handlers_registry

        count = adopt_mixin_handlers(star_handlers_registry, entry, package)
        logger.info("[persona_forge] adopted %d handlers under %s", count, entry)
        if count == 0:
            logger.warning(
                "[persona_forge] no handlers adopted; commands may not respond. "
                "Check the plugin load log / AstrBot version."
            )
    except Exception as e:  # pragma: no cover - only for unusual AstrBot builds
        logger.warning("[persona_forge] handler adoption failed: %s", e)


_adopt_handlers()

__all__ = ["PersonaForgePlugin"]

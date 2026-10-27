"""共享 Wiki / AstrBot 知识库与 /wiki 指令。"""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import html
import io
import json
import re
import shutil
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path

import aiohttp
from sqlalchemy import select, update

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.db.po import Persona

from .cards import card_filename, is_safe_card_member
from .config import (
    ALLOWED_MIME,
    AUTO_PROMPT_HEADER,
    CARD_FORMAT_PERSONA,
    CARD_FORMAT_SHARED,
    CARD_FORMAT_VERSION,
    DEV_MODE_TTL,
    DEV_NOTE_LIMIT,
    LEGACY_DEFAULT_VALUES,
    LEGACY_DEFAULTS,
    MAX_INLINE_AVATAR_BYTES,
    PERSONA_ID_RE,
    PLUGIN_NAME,
    PLUGIN_UPDATE_ID,
    PLUGIN_VERSION,
)
from .dev import build_dev_rewrite_prompt
from .memory_scope import memory_default_scope, memory_is_visible
from .paths import astrbot_root_path, resolve_plugin_path, rewrite_avatar_path, should_migrate_path
from .text import make_temp_part, mask_secret



class KnowledgeMixin:
    """共享 Wiki / AstrBot 知识库与 /wiki 指令。"""

    def _wiki_load(self) -> list[dict]:
        data = self._load_json(self.wiki_file, [])
        return data if isinstance(data, list) else []

    def _wiki_save(self, items: list[dict]) -> None:
        self._save_json(self.wiki_file, items[-2000:])

    def _wiki_add(self, text: str, title: str = "", source: str = "manual") -> dict:
        text = text.strip()
        if not text:
            raise RuntimeError("知识内容为空")
        items = self._wiki_load()
        for item in items:
            if item.get("text") == text:
                return item
        item = {
            "id": uuid.uuid4().hex[:12],
            "title": title.strip()[:80] or text[:40],
            "text": text[:4000],
            "source": source,
            "created_at": int(time.time()),
        }
        items.append(item)
        self._wiki_save(items)
        return item

    def _wiki_search(self, query: str, limit: int = 6) -> list[dict]:
        items = self._wiki_load()
        q = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", query or ""))
        scored = []
        for item in items:
            text = item.get("title", "") + " " + item.get("text", "")
            score = len(q & set(re.findall(r"[\w\u4e00-\u9fff]{2,}", text)))
            if score or not q:
                scored.append((score, item.get("created_at", 0), item))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [x[2] for x in scored[:limit]]

    async def _astrbot_kb_add(self, title: str, text: str) -> str:
        if not self.astrbot_kb_name:
            return ""
        mgr = None
        for attr in ("knowledge_base_manager", "kb_manager", "knowledge_base_mgr"):
            mgr = getattr(self.context, attr, None)
            if mgr is not None:
                break
        if mgr is None or not hasattr(mgr, "get_kb_by_name"):
            return "（未找到 AstrBot 知识库管理器）"
        try:
            kb = await mgr.get_kb_by_name(self.astrbot_kb_name)
            if kb is None:
                return f"（AstrBot 知识库 {self.astrbot_kb_name} 不存在）"
            await kb.upload_document(
                file_name=f"{title or 'shared-wiki'}-{uuid.uuid4().hex[:6]}.md",
                file_content=text.encode("utf-8"),
                file_type="md",
                pre_chunked_text=[text],
            )
            return f"（已同步到 AstrBot 知识库 {self.astrbot_kb_name}）"
        except Exception as e:
            logger.warning("[persona_forge] AstrBot KB add failed: %s", e)
            return f"（AstrBot 知识库同步失败：{e}）"

    def _wiki_list_text(self) -> str:
        items = self._wiki_load()
        if not items:
            return "共享知识库还是空的。"
        lines = [f"{i + 1}. [{x.get('title', '')}] {x.get('text', '')[:100]}" for i, x in enumerate(items[-20:])]
        return "共享知识库：\n" + "\n".join(lines)

    def _promote_memories(self, persona_id: str, keyword: str = "") -> int:
        """Only explicit knowledge-type memories are promoted.

        Episodic/personal memories stay persona-private and are not copied into
        the shared knowledge base verbatim.
        """
        items = self._load_memory(persona_id)
        n = 0
        for m in items:
            if m.get("kind") != "knowledge":
                continue
            text = m.get("text", "")
            if not text or (keyword and keyword not in text):
                continue
            before = len(self._wiki_load())
            self._wiki_add(text, title=f"来自 {persona_id} 的知识", source=f"persona:{persona_id}")
            if len(self._wiki_load()) > before:
                n += 1
        return n

    async def _astrbot_kb_context(self, query: str) -> str:
        if not self.astrbot_kb_name:
            return ""
        mgr = None
        for attr in ("knowledge_base_manager", "kb_manager", "knowledge_base_mgr"):
            mgr = getattr(self.context, attr, None)
            if mgr is not None:
                break
        if mgr is None or not hasattr(mgr, "retrieve"):
            return ""
        try:
            result = await mgr.retrieve(query, [self.astrbot_kb_name], top_m_final=4)
        except Exception as e:
            logger.warning("[persona_forge] AstrBot KB retrieve failed: %s", e)
            return ""
        if not result:
            return ""
        return str(result.get("context_text") or "")

    @filter.command_group("wiki")
    def wiki(self):
        """共享知识库：add/list/search/remove。"""
        pass

    @wiki.command("add")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wiki_add(self, event: AstrMessageEvent, raw: str = ""):
        raw = self._raw_after(event, "add", "wiki") or raw
        parts = self._split(raw)
        title = ""
        text = raw
        if len(parts) >= 2:
            title, text = parts[0], "|".join(parts[1:])
        try:
            item = self._wiki_add(text, title=title)
        except Exception as e:
            yield event.plain_result(str(e))
            return
        kb_msg = await self._astrbot_kb_add(item["title"], item["text"])
        yield event.plain_result(f"已加入共享 wiki：{item['title']} {kb_msg}")

    @wiki.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wiki_list(self, event: AstrMessageEvent):
        yield event.plain_result(self._wiki_list_text())

    @wiki.command("search")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wiki_search(self, event: AstrMessageEvent, query: str = ""):
        items = self._wiki_search(query or self._raw_after(event, "search", "wiki"), limit=10)
        if not items:
            yield event.plain_result("没有找到相关知识。")
            return
        yield event.plain_result(
            "共享 wiki：\n" + "\n".join(f"- [{x.get('title','')}] {x.get('text','')[:150]}" for x in items)
        )

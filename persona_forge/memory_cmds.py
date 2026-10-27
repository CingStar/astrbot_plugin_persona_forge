"""人格记忆与 /memory 指令。"""

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



class MemoryCommandsMixin:
    """人格记忆与 /memory 指令。"""

    def _memory_path(self, persona_id: str) -> Path:
        return self.memory_dir / f"{persona_id}.json"

    def _load_memory(self, persona_id: str) -> list[dict]:
        data = self._load_json(self._memory_path(persona_id), [])
        return data if isinstance(data, list) else []

    def _save_memory(self, persona_id: str, items: list[dict]) -> None:
        self._save_json(self._memory_path(persona_id), items[-500:])

    def _memory_add(self, text: str, kind: str = "episodic", umo: str = "", pid: str = "") -> str:
        pid = pid or self._active_persona()
        text = text.strip()
        if not text:
            raise RuntimeError("记忆内容为空")
        items = self._load_memory(pid)
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text[:2000],
            "kind": kind if kind in {"episodic", "knowledge"} else "episodic",
            "scope": memory_default_scope(kind, self.memory_scope),
            "umo": str(umo or ""),
            "created_at": int(time.time()),
        }
        items.append(item)
        self._save_memory(pid, items)
        label = "知识型记忆" if item["kind"] == "knowledge" else "经历/偏好记忆"
        return f"已写入人格 {pid} 的{label}：{text[:80]}"

    @staticmethod
    def _memory_scope_of(item: dict) -> str:
        scope = str(item.get("scope") or "")
        if scope:
            return scope
        return "global" if item.get("kind") == "knowledge" else "persona"

    def _memory_source_label(self, item: dict) -> str:
        scope = self._memory_scope_of(item)
        if scope == "global":
            return "全局"
        umo = str(item.get("umo") or "")
        if not umo:
            return "旧数据" if not item.get("scope") else "私聊"
        parts = umo.split(":")
        if len(parts) >= 2 and parts[1].lower() in {"friendmessage", "private", "friend"}:
            return "私聊"
        return f"群 {parts[-1]}" if parts else "会话"

    def _memory_list(self, persona_id: str | None = None) -> str:
        pid = persona_id or self._active_persona()
        items = self._load_memory(pid)
        if not items:
            return f"人格 {pid} 还没有记忆。"
        kind_label = {"knowledge": "知识", "session": "会话"}
        lines = [
            f"{m['id']} [{kind_label.get(m.get('kind'), '经历')}] "
            f"scope={self._memory_scope_of(m)} 来源={self._memory_source_label(m)} {m['text']}"
            for m in items[-20:]
        ]
        return f"人格 {pid} 的记忆：\n" + "\n".join(lines)

    def _memory_forget(self, keyword: str, pid: str = "") -> str:
        pid = pid or self._active_persona()
        items = self._load_memory(pid)
        kept = [m for m in items if keyword not in m.get("text", "")]
        removed = len(items) - len(kept)
        self._save_memory(pid, kept)
        return f"人格 {pid} 删除 {removed} 条匹配记忆。"

    def _memory_clear(self, pid: str = "") -> str:
        pid = pid or self._active_persona()
        self._save_memory(pid, [])
        return f"已清空人格 {pid} 的记忆。"

    def _memory_set_scope(self, keyword: str, scope: str, pid: str = "") -> str:
        pid = pid or self._active_persona()
        items = self._load_memory(pid)
        updated = 0
        for item in items:
            if keyword and keyword not in str(item.get("text", "")):
                continue
            if item.get("kind") == "session":
                continue
            item["scope"] = "global" if item.get("kind") == "knowledge" else scope
            updated += 1
        if updated:
            self._save_memory(pid, items)
        return f"人格 {pid} 已更新 {updated} 条记忆的 scope={scope}。"

    def _memory_preview(self, umo: str, is_group: bool, query: str = "") -> list[dict]:
        pid = self._active_persona()
        items = [
            m for m in self._load_memory(pid)
            if memory_is_visible(m, umo, is_group, isolation=self.memory_chat_isolation)
        ]
        needle = str(query or "").strip().lower()
        if needle:
            items = [m for m in items if needle in str(m.get("text", "")).lower()]
        return items

    @filter.command_group("memory")
    def memory(self):
        """人格记忆：add/know/list/snapshot/forget/clear/promote/preview/scope。"""
        pass

    @memory.command("add")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_add(self, event: AstrMessageEvent, text: str = ""):
        text = text or self._raw_after(event, "add", "memory")
        try:
            yield event.plain_result(self._memory_add(text, umo=event.unified_msg_origin))
        except Exception as e:
            yield event.plain_result(str(e))

    @memory.command("know")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_know(self, event: AstrMessageEvent, text: str = ""):
        """写入知识型记忆（会进入人格提示词，对所有会话生效）。"""
        text = text or self._raw_after(event, "know", "memory")
        try:
            msg = self._memory_add(text, kind="knowledge", umo=event.unified_msg_origin)
            yield event.plain_result(
                msg + "\n注意：知识型记忆会写入人格提示词，对所有会话（含群聊）生效；私密内容请用 /memory add。"
            )
        except Exception as e:
            yield event.plain_result(str(e))

    @memory.command("snapshot")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_snapshot(self, event: AstrMessageEvent, persona_id: str = ""):
        """把当前会话最近 20 条消息抓取到人格记忆（会话快照）。"""
        pid = persona_id or self._active_persona()
        try:
            result = await self._capture_conversation(pid, event.unified_msg_origin, limit=20)
        except Exception as e:
            yield event.plain_result(f"抓取失败：{e}")
            return
        yield event.plain_result(
            f"已把当前会话最近 {result['messages']} 条消息写入人格 {pid} 的会话记忆。\n"
            "人格记忆不会自动同步 QQ 聊天；切换人格时也会自动抓取一次。"
        )

    @memory.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_list(self, event: AstrMessageEvent):
        yield event.plain_result(self._memory_list())

    @memory.command("preview")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_preview(self, event: AstrMessageEvent, query: str = ""):
        """查看当前会话实际会注入哪些记忆。"""
        umo = event.unified_msg_origin
        is_group = bool(self._group_id(event)) or not self._is_private(event)
        items = self._memory_preview(umo, is_group, query)
        if not items:
            yield event.plain_result("当前会话不会注入任何人格记忆。")
            return
        kind_label = {"knowledge": "知识", "session": "会话"}
        lines = [
            f"[{kind_label.get(m.get('kind'), '经历')}] scope={self._memory_scope_of(m)} "
            f"来源={self._memory_source_label(m)} {str(m.get('text', ''))[:80]}"
            for m in items[-20:]
        ]
        yield event.plain_result(
            f"当前会话（{'群聊' if is_group else '私聊'}）可见记忆 {len(items)} 条：\n" + "\n".join(lines)
        )

    @memory.command("scope")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_scope_cmd(self, event: AstrMessageEvent, keyword: str = "", scope: str = ""):
        if scope not in {"chat", "persona"}:
            yield event.plain_result("用法：/memory scope <关键词> chat|persona")
            return
        yield event.plain_result(self._memory_set_scope(keyword, scope))

    @memory.command("forget")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_forget(self, event: AstrMessageEvent, keyword: str = ""):
        if not keyword:
            yield event.plain_result("用法：/memory forget <关键词>")
            return
        yield event.plain_result(self._memory_forget(keyword))

    @memory.command("clear")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_clear(self, event: AstrMessageEvent):
        yield event.plain_result(self._memory_clear())

    @memory.command("promote")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_promote(self, event: AstrMessageEvent, keyword: str = ""):
        """把当前人格记忆中可复用的知识提取到共享 wiki。"""
        pid = self._active_persona()
        n = self._promote_memories(pid, keyword.strip())
        if n == 0:
            yield event.plain_result(
                f"人格 {pid} 没有可同步的知识型记忆。\n"
                "经历/偏好记忆不会自动进知识库；请用 /memory know 或“知识：…”写入知识型记忆。"
            )
        else:
            yield event.plain_result(f"已从人格 {pid} 同步 {n} 条知识型记忆到共享 wiki。")

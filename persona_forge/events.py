"""事件钩子：记忆注入、群聊门禁、待续输入、生命周期。"""

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



class EventsMixin:
    """事件钩子：记忆注入、群聊门禁、待续输入、生命周期。"""

    @filter.on_astrbot_loaded()
    async def on_plugin_updated(self):
        """On a plugin file update: new conversations, then reconcile QQ profile."""
        try:
            if self.state.get("update_id") == PLUGIN_UPDATE_ID:
                return
            active = self._active_persona()
            await self._switch_all_conversations(active, force_new=True)
            report = await self._reconcile_persona(fix=True)
            self.state.update({
                "update_id": PLUGIN_UPDATE_ID,
                "last_reconcile": report,
                "last_reconcile_at": int(time.time()),
            })
            self._save_state()
            logger.info("[persona_forge] plugin update reconcile: %s", report)
        except Exception as e:
            logger.warning("[persona_forge] plugin update reconcile failed: %s", e)

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req):
        query = event.message_str or ""
        is_admin = not (hasattr(event, "is_admin") and not event.is_admin())
        umo = getattr(event, "unified_msg_origin", "") or ""
        is_group = bool(self._group_id(event)) or not self._is_private(event)
        # Shared wiki is visible to owner and guests; persona tone still comes from the active persona.
        wiki = [] if (not is_admin and self.guest_wiki_read in {"off", "none"}) else self._wiki_search(query, limit=5)
        if wiki:
            block = "【共享知识库（所有人格共用；请用当前人格的口吻表达，不要逐条复述）】\n" + "\n".join(
                f"- {x.get('title', '')}: {x.get('text', '')}" for x in wiki
            )
            try:
                req.extra_user_content_parts.append(make_temp_part(block))
            except Exception:
                pass
        kb_ctx = "" if (not is_admin and self.guest_wiki_read in {"off", "none"}) else await self._astrbot_kb_context(query)
        if kb_ctx:
            try:
                req.extra_user_content_parts.append(make_temp_part(kb_ctx))
            except Exception:
                pass

        # Private persona memory is owner-only.
        if hasattr(event, "is_admin") and not event.is_admin():
            return
        pid = self._active_persona()
        items = [
            m for m in self._load_memory(pid)
            if memory_is_visible(m, umo, is_group, isolation=self.memory_chat_isolation)
        ]
        if not items:
            return
        # Latest conversation snapshot for this chat: carried across persona
        # switches, but never borrowed from another chat.
        current_conv = self.context.conversation_manager.session_conversations.get(umo, "")
        sessions = [
            m for m in items
            if m.get("kind") == "session" and m.get("conv_id") != current_conv
        ]
        if sessions:
            latest = max(sessions, key=lambda m: int(m.get("created_at", 0)))
            try:
                req.extra_user_content_parts.append(make_temp_part(
                    "【该人格上一轮对话记忆（仅作参考，不要原样复述）】\n"
                    + str(latest.get("text", ""))[:3000]
                ))
            except Exception:
                pass
        q = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", query))
        scored = []
        for m in items[-200:]:
            if m.get("kind") == "session":
                continue
            text = m.get("text", "")
            score = len(q & set(re.findall(r"[\w\u4e00-\u9fff]{2,}", text)))
            if score:
                scored.append((score, m.get("created_at", 0), text))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        selected = [x[2] for x in scored[:8]]
        if not selected:
            return
        block = "【人格记忆（仅作参考，不要逐条复述）】\n" + "\n".join(f"- {s}" for s in selected)
        try:
            req.extra_user_content_parts.append(make_temp_part(block))
        except Exception:
            pass

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def group_gate(self, event: AstrMessageEvent):
        if self.group_mode == "off":
            return
        group_id = self._group_id(event)
        if self.group_whitelist and group_id not in self.group_whitelist:
            self._halt(event)
            return
        if self.require_at and not bool(getattr(event, "is_at_or_wake_command", True)):
            self._halt(event)
            return
        if hasattr(event, "is_admin") and event.is_admin():
            return
        now = time.time()
        hits = [t for t in self._guest_hits.get(group_id + str(event.get_sender_id()), []) if now - t < 60]
        if len(hits) >= self.guest_rate_limit:
            self._halt(event)
            return
        hits.append(now)
        self._guest_hits[group_id + str(event.get_sender_id())] = hits

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def on_command_pending(self, event: AstrMessageEvent):
        user = str(event.get_sender_id())
        state = self.command_pending.get(user)
        if not state:
            return
        text = (event.message_str or "").strip()
        kind = state.get("kind")
        self._halt(event)
        if text in {"取消", "cancel"}:
            self.command_pending.pop(user, None)
            yield event.plain_result("已取消。")
            return
        if time.time() - state.get("ts", 0) > 180:
            self.command_pending.pop(user, None)
            yield event.plain_result("操作超时，已取消。")
            return
        if kind == "create":
            stage = state.get("stage")
            if stage == "prompt":
                if not text:
                    yield event.plain_result("请发送人格提示词文本。")
                    return
                state.update({"stage": "nickname", "prompt": text, "ts": time.time()})
                yield event.plain_result("收到提示词。现在发 QQ 昵称。")
                return
            if stage == "nickname":
                if not text:
                    yield event.plain_result("请发送昵称。")
                    return
                pid = state["id"]
                try:
                    await self._upsert_native_persona(pid, state["prompt"])
                    p = self._profile(pid)
                    p.update({
                        "nickname": text[:40], "prompt": state["prompt"],
                        "prompt_base": state["prompt"], "updated_at": int(time.time()),
                    })
                    p.setdefault("avatar_path", "")
                    self._save_profiles()
                except Exception as e:
                    yield event.plain_result(f"创建失败：{e}")
                    return
                self.command_pending.pop(user, None)
                yield event.plain_result(
                    f"已创建人格 {pid}。\n上传头像：/persona avatar {pid} set，然后发图片。"
                )
                return
        if kind == "update":
            pid = state["id"]
            try:
                await self._upsert_native_persona(pid, text)
                p = self._profile(pid)
                p["prompt"] = text
                p["prompt_base"] = text
                p["updated_at"] = int(time.time())
                self._save_profiles()
            except Exception as e:
                yield event.plain_result(f"更新失败：{e}")
                return
            self.command_pending.pop(user, None)
            yield event.plain_result(f"已更新人格 {pid} 的提示词。")
            return
        if kind == "avatar":
            pid = state["id"]
            url = self._first_image(event) or text
            if not url:
                yield event.plain_result("请发图片或图片 URL。")
                return
            try:
                await self._snapshot_version(pid, reason="avatar_update")
                await self._save_avatar(pid, url)
            except Exception as e:
                yield event.plain_result(f"头像保存失败：{e}")
                return
            self.command_pending.pop(user, None)
            yield event.plain_result(f"已更新人格 {pid} 的头像。")

    async def terminate(self):
        logger.info("[persona_forge] terminated")

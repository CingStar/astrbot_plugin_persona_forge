"""/persona 指令组。"""

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



class PersonaCommandsMixin:
    """/persona 指令组。"""

    @filter.command_group("persona")
    def persona(self):
        """人格管理：创建/修改/切换/默认人格。"""
        pass

    @persona.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def list_personas(self, event: AstrMessageEvent):
        yield event.plain_result(self._persona_list_text())

    @persona.command("show")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def show_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        if not persona_id:
            yield event.plain_result("用法：/persona show <id>")
            return
        try:
            p = await self.context.persona_manager.get_persona(persona_id)
        except Exception:
            yield event.plain_result(f"人格 {persona_id} 不存在")
            return
        yield event.plain_result(f"人格 {persona_id}：\n{(p.system_prompt or '')[:1200]}")

    @persona.command("iterate")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def iterate_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        """按该人格专属知识记忆，自动迭代其提示词。"""
        pid = persona_id or self._active_persona()
        result = await self._iterate_persona_prompt(pid)
        if result == "no_knowledge":
            yield event.plain_result(f"人格 {pid} 还没有知识型记忆，未迭代。")
        elif result == "no_base":
            yield event.plain_result(f"人格 {pid} 没有基础提示词，未迭代。")
        elif result == "unchanged":
            yield event.plain_result(f"人格 {pid} 提示词已是最新。")
        else:
            yield event.plain_result(f"人格 {pid} 提示词已迭代：{result}")

    @persona.command("rewrite")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def rewrite_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        """用当前聊天模型重写人格提示词（专属知识，不含共享 wiki）。"""
        pid = persona_id or self._active_persona()
        result = await self._rewrite_persona_prompt_llm(pid, event.unified_msg_origin)
        if result == "no_knowledge":
            yield event.plain_result(f"人格 {pid} 没有知识型记忆，无法重写。")
        elif result == "no_base":
            yield event.plain_result(f"人格 {pid} 没有基础提示词，无法重写。")
        elif result == "no_provider":
            yield event.plain_result("没有可用的聊天模型 Provider，无法重写。")
        elif result == "empty":
            yield event.plain_result("模型没有返回有效提示词。")
        elif result.startswith("error:"):
            yield event.plain_result(f"重写失败：{result[6:]}")
        else:
            yield event.plain_result(f"人格 {pid} 提示词已用模型重写：{result}")

    @persona.command("view")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def view_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        if not persona_id:
            yield event.plain_result("用法：/persona view <id>")
            return
        try:
            p = await self.context.persona_manager.get_persona(persona_id)
        except Exception:
            yield event.plain_result(f"人格 {persona_id} 不存在")
            return
        prof = self._profile(persona_id)
        yield event.plain_result(
            f"人格 {persona_id}\n"
            f"昵称：{prof.get('nickname', '-')}\n"
            f"头像：{prof.get('avatar_path', '-')}\n"
            f"工具：{p.tools if p.tools is not None else '全部'}\n"
            f"Skills：{p.skills if p.skills is not None else '全部'}\n"
            f"开场对话：{len(p.begin_dialogs or [])} 条\n"
            f"提示词：\n{(p.system_prompt or '')[:1500]}"
        )

    @persona.command("delete")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def delete_persona(self, event: AstrMessageEvent, persona_id: str = "", confirm: str = ""):
        if not persona_id or persona_id == "default":
            yield event.plain_result("default 不能删除；用法：/persona delete <id> confirm")
            return
        if confirm != "confirm":
            yield event.plain_result("确认删除请追加 confirm")
            return
        if persona_id not in self._persona_ids():
            yield event.plain_result("人格不存在。")
            return
        async with self._db().get_db() as session:
            obj = (
                await session.execute(select(Persona).where(Persona.persona_id == persona_id))
            ).scalar_one_or_none()
            if obj:
                await session.delete(obj)
                await session.commit()
        self.profiles.pop(persona_id, None)
        self._save_profiles()
        await self.context.persona_manager.initialize()
        yield event.plain_result(f"已删除人格 {persona_id}")

    @persona.command("fields")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def fields_persona(self, event: AstrMessageEvent, raw: str = ""):
        raw = self._raw_after(event, "fields") or raw
        parts = raw.split(maxsplit=2)
        if len(parts) < 2:
            yield event.plain_result(
                "用法：/persona fields <id> tools=all|none|a,b | skills=... | error=... | dialogs=<json>"
            )
            return
        pid, expr = parts[0], " ".join(parts[1:]).strip()
        if "=" not in expr:
            yield event.plain_result("字段格式：key=value")
            return
        key, value = expr.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            await self._snapshot_version(pid, reason=f"field_{key}")
            if key == "tools":
                val = None if value == "all" else ([] if value == "none" else [x.strip() for x in value.split(",") if x.strip()])
                await self._update_native_fields(pid, tools=val)
            elif key == "skills":
                val = None if value == "all" else ([] if value == "none" else [x.strip() for x in value.split(",") if x.strip()])
                await self._update_native_fields(pid, skills=val)
            elif key == "error":
                await self._update_native_fields(pid, custom_error_message=value or None)
            elif key == "dialogs":
                data = json.loads(value)
                if not isinstance(data, list) or len(data) % 2 != 0:
                    raise ValueError("dialogs 必须是偶数条 user/assistant 列表")
                await self._update_native_fields(pid, begin_dialogs=data)
            else:
                yield event.plain_result("只支持 tools/skills/error/dialogs")
                return
        except Exception as e:
            yield event.plain_result(f"字段更新失败：{e}")
            return
        yield event.plain_result(f"已更新人格 {pid} 字段 {key}")

    @persona.command("versions")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def versions_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        pid = persona_id or self._active_persona()
        nums = self._list_versions(pid)
        if not nums:
            yield event.plain_result(f"人格 {pid} 还没有版本。")
            return
        yield event.plain_result(f"人格 {pid} 版本：\n" + "\n".join(f"v{n:04d}" for n in nums[-20:]))

    @persona.command("rollback")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def rollback_persona(self, event: AstrMessageEvent, persona_id: str = "", version: int = 0):
        if not persona_id or not version:
            yield event.plain_result("用法：/persona rollback <id> <version>")
            return
        try:
            msg = await self._apply_version(persona_id, int(version))
        except Exception as e:
            yield event.plain_result(f"回滚失败：{e}")
            return
        yield event.plain_result(msg)

    @persona.command("export")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def export_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        pid = persona_id or self._active_persona()
        try:
            p = await self.context.persona_manager.get_persona(pid)
        except Exception:
            yield event.plain_result("人格不存在。")
            return
        prof = self._profile(pid)
        text = (
            f"# {pid}\n\n"
            f"- 昵称：{prof.get('nickname', '-')}\n"
            f"- 头像：{prof.get('avatar_path', '-')}\n"
            f"- 工具：{p.tools if p.tools is not None else '全部'}\n"
            f"- Skills：{p.skills if p.skills is not None else '全部'}\n\n"
            f"## System Prompt\n\n{p.system_prompt or ''}\n"
        )
        out = self.exports_dir / f"{pid}-{int(time.time())}.md"
        out.write_text(text, encoding="utf-8")
        try:
            yield event.chain_result([File(file=str(out), name=out.name)])
        except Exception:
            yield event.plain_result(f"已导出人格 {pid}：{out}")

    @persona.command("review")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def review_persona(self, event: AstrMessageEvent, raw: str = ""):
        raw = self._raw_after(event, "review") or raw
        parts = raw.split(maxsplit=2)
        action = parts[0] if parts else "list"
        if action == "list":
            items = self._list_reviews()
            if not items:
                yield event.plain_result("没有待审草稿。")
            else:
                yield event.plain_result(
                    "待审：\n" + "\n".join(
                        f"{x['persona_id']} [{x['source']}] {time.strftime('%m-%d %H:%M', time.localtime(x['created_at']))}"
                        for x in items
                    )
                )
            return
        if len(parts) < 2:
            yield event.plain_result("用法：/persona review list|show <id>|approve <id>|reject <id>")
            return
        pid = parts[1]
        path = self._review_path(pid)
        if action == "show":
            if not path.is_file():
                yield event.plain_result("没有草稿。")
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            yield event.plain_result(f"人格 {pid} 草稿 diff：\n{data.get('diff', '')[:3000]}")
            return
        if action == "approve":
            source = ""
            try:
                source = str(json.loads(path.read_text(encoding="utf-8")).get("source") or "")
            except Exception:
                source = ""
            try:
                msg = await self._approve_review(pid)
            except Exception as e:
                yield event.plain_result(f"批准失败：{e}")
                return
            self._record_review(pid, "approve", source)
            yield event.plain_result(msg)
            return
        if action == "reject":
            source = ""
            try:
                source = str(json.loads(path.read_text(encoding="utf-8")).get("source") or "")
            except Exception:
                source = ""
            path.unlink(missing_ok=True)
            self._record_review(pid, "reject", source)
            yield event.plain_result(f"已拒绝人格 {pid} 的草稿。")
            return
        yield event.plain_result("未知操作。")

    @persona.command("compress")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def compress_persona(self, event: AstrMessageEvent, persona_id: str = "", level: str = "medium"):
        pid = persona_id or self._active_persona()
        base = self._base_prompt(pid)
        if not base:
            yield event.plain_result("人格没有提示词。")
            return
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
            if not provider_id:
                raise RuntimeError("没有可用 Provider")
            prompt = (
                f"请压缩下面的人格提示词，强度={level}。保留身份、性格、语气、边界和禁忌；"
                "删除重复、空话和过长例子；只输出压缩后的提示词，不要解释。\n\n"
                + base
            )
            resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            draft = (resp.completion_text or "").strip()
            if not draft:
                raise RuntimeError("模型未返回文本")
            self._create_review(pid, draft, source=f"compress:{level}", base=base)
        except Exception as e:
            yield event.plain_result(f"压缩失败：{e}")
            return
        yield event.plain_result(f"已生成人格 {pid} 的压缩草稿。\n/persona review show {pid} 查看，approve 批准。")

    @persona.command("create")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def create_persona(self, event: AstrMessageEvent, raw: str = ""):
        """创建/更新人格：/persona create id|昵称|提示词"""
        raw = self._raw_after(event, "create") or raw
        parts = self._split(raw)
        if len(parts) == 1 and PERSONA_ID_RE.match(parts[0]):
            pid = parts[0]
            if pid in self._persona_ids():
                yield event.plain_result(f"人格 {pid} 已存在，用 /persona update {pid}")
                return
            self.command_pending[str(event.get_sender_id())] = {
                "kind": "create", "stage": "prompt", "id": pid, "ts": time.time(),
            }
            yield event.plain_result(
                f"创建人格 {pid}：请发送人格提示词文本（下一条消息），然后发昵称。"
            )
            return
        if len(parts) < 3:
            yield event.plain_result("用法：/persona create id|昵称|提示词 或 /persona create <id>")
            return
        persona_id, nickname, prompt = parts[0], parts[1], "|".join(parts[2:]).strip()
        if not PERSONA_ID_RE.match(persona_id) or not nickname or not prompt:
            yield event.plain_result("id/昵称/提示词不合法")
            return
        await self._upsert_native_persona(persona_id, prompt)
        p = self._profile(persona_id)
        p.update({
            "nickname": nickname, "prompt": prompt, "prompt_base": prompt,
            "updated_at": int(time.time()),
        })
        p.setdefault("avatar_path", "")
        self._save_profiles()
        yield event.plain_result(
            f"已写入 AstrBot 人格 {persona_id}。\n上传头像：回复图片并配文 /persona avatar {persona_id}"
        )

    @persona.command("edit")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def edit_persona(self, event: AstrMessageEvent, raw: str = ""):
        raw = self._raw_after(event, "edit") or raw
        parts = self._split(raw)
        if len(parts) < 2:
            yield event.plain_result("用法：/persona edit id|提示词")
            return
        persona_id, prompt = parts[0], "|".join(parts[1:]).strip()
        await self._upsert_native_persona(persona_id, prompt)
        p = self._profile(persona_id)
        p["prompt"] = prompt
        p["prompt_base"] = prompt
        self._save_profiles()
        yield event.plain_result(f"已更新人格 {persona_id} 的 prompt。")

    @persona.command("update")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def update_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        if not persona_id or persona_id not in self._persona_ids():
            yield event.plain_result("用法：/persona update <id>")
            return
        self.command_pending[str(event.get_sender_id())] = {
            "kind": "update", "id": persona_id, "ts": time.time(),
        }
        yield event.plain_result(f"请发送 {persona_id} 的新提示词文本。")

    @persona.command("avatar")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def avatar(self, event: AstrMessageEvent, persona_id: str = "", url: str = ""):
        if not persona_id:
            yield event.plain_result("用法：/persona avatar <id> get|set|remove")
            return
        if persona_id not in self._persona_ids():
            yield event.plain_result(f"人格 {persona_id} 不存在")
            return
        op = (url or "").strip().lower()
        if op == "get":
            p = self._profile(persona_id)
            yield event.plain_result(
                f"人格 {persona_id} 头像：{p.get('avatar_path', '-')}\nSHA256：{p.get('avatar_sha256', '-')}"
            )
            return
        if op == "remove":
            await self._snapshot_version(persona_id, reason="avatar_remove")
            p = self._profile(persona_id)
            p.pop("avatar_path", None)
            p.pop("avatar_sha256", None)
            self._save_profiles()
            yield event.plain_result(f"已移除人格 {persona_id} 的头像记录。")
            return
        if op == "set" or not self._first_image(event):
            self.command_pending[str(event.get_sender_id())] = {
                "kind": "avatar", "id": persona_id, "ts": time.time(),
            }
            yield event.plain_result(f"请发送 {persona_id} 的头像图片或图片 URL。")
            return
        image_url = url.strip() or self._first_image(event)
        try:
            await self._snapshot_version(persona_id, reason="avatar_update")
            await self._save_avatar(persona_id, image_url)
        except Exception as e:
            yield event.plain_result(f"保存头像失败：{e}")
            return
        yield event.plain_result(f"已保存人格 {persona_id} 的头像。\n/persona switch {persona_id} 切换。")

    @persona.command("use")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def use_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        if not persona_id:
            yield event.plain_result("用法：/persona use <id>")
            return
        try:
            msg = await self._use_persona(
                persona_id, event.unified_msg_origin, self._group_id(event)
            )
        except Exception as e:
            yield event.plain_result(f"切换失败，保持当前人格：{e}")
            return
        yield event.plain_result(msg)

    @persona.command("switch")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def switch_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        if not persona_id:
            yield event.plain_result("用法：/persona switch <id>")
            return
        try:
            msg = await self._use_persona(
                persona_id, event.unified_msg_origin, self._group_id(event)
            )
        except Exception as e:
            yield event.plain_result(f"切换失败，保持当前人格：{e}")
            return
        yield event.plain_result(msg)

    @persona.command("exit")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def exit_persona(self, event: AstrMessageEvent):
        """退出当前人格，恢复默认人格的 prompt/昵称/头像。"""
        try:
            msg = await self._use_persona(
                "default", event.unified_msg_origin, self._group_id(event)
            )
        except Exception as e:
            yield event.plain_result(
                f"退出失败，保持当前人格：{e}\n"
                "先设置默认人格：/persona create default|昵称|提示词，"
                "再 /persona avatar default 上传头像。"
            )
            return
        yield event.plain_result(msg)

    @persona.command("default")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def default_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        if not persona_id:
            p = self._profile("default")
            ready = "✅" if p.get("nickname") and p.get("avatar_path") else "⚠️"
            yield event.plain_result(
                f"{ready} 当前基础默认人格：default\n"
                f"昵称：{p.get('nickname', '-')}\n"
                f"头像：{p.get('avatar_path', '-')}\n"
                "修改：/persona create default|昵称|提示词\n"
                "头像：/persona avatar default（回复图片）\n"
                "退出角色：/persona exit"
            )
            return
        if persona_id not in self._persona_ids():
            yield event.plain_result(f"人格 {persona_id} 不存在")
            return
        await self._set_default_native_persona(persona_id)
        yield event.plain_result(f"已设置 AstrBot 默认人格：{persona_id}")

    @persona.command("profile")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def profile_persona(self, event: AstrMessageEvent, persona_id: str = ""):
        """查看人格已保存的昵称/头像/提示词资料。"""
        pid = persona_id or self._active_persona()
        p = self._profile(pid)
        yield event.plain_result(
            f"人格资料 {pid}:\n"
            f"昵称：{p.get('nickname', '-')}\n"
            f"头像：{p.get('avatar_path', '-')}\n"
            f"头像 SHA256：{p.get('avatar_sha256', '-')}\n"
            f"提示词已保存：{'是' if p.get('prompt') else '否'}\n"
            f"更新时间：{p.get('updated_at', '-')}"
        )

    @persona.command("check")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def check_persona(self, event: AstrMessageEvent):
        """核对当前 QQ 昵称/头像与当前人格资料。"""
        yield event.plain_result(await self._reconcile_persona(fix=False))

    @persona.command("sync")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def sync_persona(self, event: AstrMessageEvent):
        """新建会话并强制按当前人格同步昵称/头像。"""
        await self._switch_all_conversations(
            self._active_persona(), event.unified_msg_origin, force_new=True
        )
        yield event.plain_result(await self._reconcile_persona(fix=True))

    @persona.command("debug")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def debug_persona(self, event: AstrMessageEvent):
        """查看默认人格 prompt/资料与当前会话状态。"""
        try:
            default = await self.context.persona_manager.get_persona("default")
            prompt = (default.system_prompt or "")[:500]
        except Exception as e:
            prompt = f"<读取失败: {e}>"
        p = self._profile("default")
        active = self._active_persona()
        conv_id = self.context.conversation_manager.session_conversations.get(event.unified_msg_origin, "-")
        yield event.plain_result(
            f"active_persona={active}\n"
            f"session_conversation={conv_id}\n"
            f"default_prompt={prompt!r}\n"
            f"default_nickname={p.get('nickname', '-')}\n"
            f"default_avatar={p.get('avatar_path', '-')}"
        )

    @persona.command("resetdefault")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reset_default(self, event: AstrMessageEvent, confirm: str = ""):
        """重置 default 的 prompt 和资料（需 confirm）。"""
        if confirm != "confirm":
            yield event.plain_result("这会清空 default 的提示词和昵称/头像。确认请发：/persona resetdefault confirm")
            return
        await self._upsert_native_persona("default", "You are a helpful and friendly assistant.")
        p = self._profile("default")
        p.pop("nickname", None)
        p.pop("avatar_path", None)
        p["prompt"] = "You are a helpful and friendly assistant."
        p["prompt_base"] = p["prompt"]
        self._save_profiles()
        yield event.plain_result("已重置 default 的 prompt/资料。请重新设置昵称和头像。")

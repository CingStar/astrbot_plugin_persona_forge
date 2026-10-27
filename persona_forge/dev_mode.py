"""开发者模式（教学模式）与 /dev 指令。"""

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



class DevModeMixin:
    """开发者模式（教学模式）与 /dev 指令。"""

    def _dev_path(self, pid: str) -> Path:
        return self.dev_dir / f"{pid}.json"

    def _dev_state(self) -> dict:
        raw = self.state.get("dev_mode")
        return raw if isinstance(raw, dict) else {}

    def _dev_active_pid(self) -> str:
        st = self._dev_state()
        pid = str(st.get("persona_id") or "")
        if not pid:
            return ""
        expires = int(st.get("expires_at") or 0)
        if expires and int(time.time()) > expires:
            self.state.pop("dev_mode", None)
            self._save_state()
            return ""
        return pid

    def _dev_enable(self, pid: str) -> None:
        now = int(time.time())
        self.state["dev_mode"] = {"persona_id": pid, "since": now, "expires_at": now + DEV_MODE_TTL}
        self._save_state()

    def _dev_disable(self) -> None:
        self.state.pop("dev_mode", None)
        self._save_state()

    def _dev_notes(self, pid: str) -> list[dict]:
        data = self._load_json(self._dev_path(pid), [])
        return data if isinstance(data, list) else []

    def _dev_add_note(self, pid: str, text: str, umo: str = "") -> int:
        text = (text or "").strip()
        if not text:
            raise RuntimeError("教学笔记内容为空")
        notes = self._dev_notes(pid)
        notes.append({"text": text[:2000], "umo": umo or "", "created_at": int(time.time())})
        notes = notes[-DEV_NOTE_LIMIT:]
        self._save_json(self._dev_path(pid), notes)
        return len(notes)

    def _dev_clear(self, pid: str) -> int:
        notes = self._dev_notes(pid)
        self._dev_path(pid).unlink(missing_ok=True)
        return len(notes)

    async def _dev_apply(self, pid: str, umo: str = "") -> str:
        base = self._base_prompt(pid)
        if not base:
            return "no_base"
        notes = [n.get("text", "") for n in self._dev_notes(pid)][-50:]
        if not notes:
            return "no_notes"
        knowledge = self._knowledge_texts(pid)
        prompt = build_dev_rewrite_prompt(base, knowledge, notes)
        provider_id = await self.context.get_current_chat_provider_id(umo=umo or "")
        if not provider_id:
            return "no_provider"
        resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
        draft = (resp.completion_text or "").strip()
        if draft.startswith("```"):
            draft = re.sub(r"^```[a-zA-Z]*\n?", "", draft)
            draft = re.sub(r"\n?```$", "", draft).strip()
        if not draft:
            return "empty"
        self._create_review(pid, draft[:12000], source="dev", base=base)
        return f"drafted:{len(notes)}"

    @filter.command_group("dev")
    def dev(self):
        """开发者模式：教人格更像角色（不进入扮演）。"""
        pass

    @dev.command("on")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def dev_on(self, event: AstrMessageEvent, persona_id: str = ""):
        pid = persona_id or self._active_persona()
        if pid not in self._persona_ids() and pid not in self.profiles:
            yield event.plain_result(f"人格 {pid} 不存在")
            return
        self._dev_enable(pid)
        yield event.plain_result(
            f"开发者模式已开启：{pid}\n"
            "接下来私聊里发的普通消息都会作为教学笔记记录，不会进入角色扮演。\n"
            f"{DEV_MODE_TTL // 3600} 小时后自动关闭；/dev off 立即退出，/dev apply 生成草稿。"
        )

    @dev.command("off")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def dev_off(self, event: AstrMessageEvent):
        pid = self._dev_active_pid()
        self._dev_disable()
        yield event.plain_result("开发者模式已关闭。" + (f"（{pid}）" if pid else ""))

    @dev.command("status")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def dev_status(self, event: AstrMessageEvent):
        pid = self._dev_active_pid()
        if not pid:
            yield event.plain_result("开发者模式未开启。用 /dev on [人格id] 开启。")
            return
        st = self._dev_state()
        notes = self._dev_notes(pid)
        expires = int(st.get("expires_at") or 0)
        left = max(0, expires - int(time.time())) if expires else 0
        yield event.plain_result(
            f"开发者模式：{pid}\n教学笔记：{len(notes)} 条\n"
            f"剩余时间：{left // 3600} 小时 {left % 3600 // 60} 分"
        )

    @dev.command("apply")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def dev_apply(self, event: AstrMessageEvent, persona_id: str = ""):
        pid = persona_id or self._dev_active_pid() or self._active_persona()
        try:
            result = await self._dev_apply(pid, event.unified_msg_origin)
        except Exception as e:
            yield event.plain_result(f"生成草稿失败：{e}")
            return
        if result == "no_notes":
            yield event.plain_result(f"人格 {pid} 还没有教学笔记。")
        elif result == "no_base":
            yield event.plain_result(f"人格 {pid} 没有基础提示词。")
        elif result == "no_provider":
            yield event.plain_result("没有可用的聊天模型 Provider。")
        elif result == "empty":
            yield event.plain_result("模型没有返回有效提示词。")
        else:
            count = result.split(":", 1)[-1]
            yield event.plain_result(
                f"已生成人格 {pid} 的教学草稿（{count} 条笔记）。\n"
                f"用 /persona review show {pid} 查看，approve 批准。"
            )

    @dev.command("clear")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def dev_clear(self, event: AstrMessageEvent, persona_id: str = ""):
        pid = persona_id or self._dev_active_pid() or self._active_persona()
        n = self._dev_clear(pid)
        yield event.plain_result(f"已清空人格 {pid} 的 {n} 条教学笔记。")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def on_owner_natural(self, event: AstrMessageEvent):
        """Capture free text as teaching notes while developer mode is on.

        Command-first mode stays the default: with no active dev persona this
        handler returns immediately and the message flows to the LLM normally.
        """
        text = (event.message_str or "").strip()
        user = str(event.get_sender_id())
        if not text or text.startswith("/"):
            return
        if self.command_pending.get(user) or self.pending.get(user):
            return
        pid = self._dev_active_pid()
        if not pid:
            return
        self._halt(event)
        try:
            count = self._dev_add_note(pid, text, event.unified_msg_origin)
        except Exception as e:
            yield event.plain_result(str(e))
            return
        yield event.plain_result(
            f"已记录第 {count} 条教学笔记（人格 {pid}）。\n"
            "发 /dev apply 生成待审草稿，/dev status 查看，/dev off 退出开发者模式。"
        )
        return
        text = (event.message_str or "").strip()
        user = str(event.get_sender_id())
        state = self.pending.get(user)

        if state:
            self._halt(event)  # never let the LLM answer while a persona flow is active
            if state.get("kind") == "default_edit":
                async for result in self._advance_default_edit(event, state):
                    yield result
            elif state.get("kind") == "profile_edit":
                async for result in self._advance_profile_edit(event, state):
                    yield result
            else:
                async for result in self._advance_natural_create(event, state):
                    yield result
            return

        if not text or text.startswith("/"):
            return

        create_hints = ("创建", "新建", "生成", "做一个", "做个", "写一个", "设计", "添加", "加一个", "建一个", "开一个")

        if ("核对" in text or "检查" in text) and any(w in text for w in ("人格", "头像", "昵称")):
            self._halt(event)
            yield event.plain_result(await self._reconcile_persona(fix=False))
            return
        if any(w in text for w in ("同步人格", "修复人格", "同步头像", "同步昵称")):
            await self._switch_all_conversations(
                self._active_persona(), event.unified_msg_origin, force_new=True
            )
            self._halt(event)
            yield event.plain_result(await self._reconcile_persona(fix=True))
            return

        # Digital life card, fully natural.
        if "生命卡" in text:
            if "备份" in text:
                scope = "qq" if ("qq" in text.lower() or "协议" in text) else ("agent" if ("人格" in text or "记忆" in text) else "all")
                hot = "热" in text
                rid = self._write_life_request("backup", scope=scope, hot=hot, requested_by=event.get_sender_id())
                self._halt(event)
                yield event.plain_result(
                    f"已提交{'热' if hot else '冷'}备份 {rid[:8]}（{scope}）。\n"
                    "机器人可能短暂离线，完成后说“查看生命卡状态”。"
                )
                return
            if "恢复" in text:
                m = re.search(r"(life-card-[\w.-]+\.pialife)", text)
                if m and self.allow_restore:
                    archive = m.group(1)
                    if (self.backup_dir / archive).is_file():
                        rid = self._write_life_request(
                            "restore", archive=str(self.backup_dir / archive),
                            scope="all", requested_by=event.get_sender_id(),
                        )
                        self._halt(event)
                        yield event.plain_result(f"已提交恢复请求 {rid[:8]}。机器人会离线。")
                        return
                self._halt(event)
                yield event.plain_result(
                    "恢复需要明确到具体生命卡：\n" + self._life_list_text() +
                    "\n然后说“恢复生命卡 life-card-xxx.pialife”，或直接用宿主 life-card-host。"
                )
                return
            if "状态" in text:
                self._halt(event)
                yield event.plain_result(self._life_status_text())
                return
            if any(w in text for w in ("查看", "列表", "有什么", "看看")):
                self._halt(event)
                yield event.plain_result(self._life_list_text())
                return

        # Shared wiki.
        if "wiki" in text.lower() or "知识库" in text or "共享知识" in text:
            if any(w in text for w in ("添加", "加入", "写入", "存到", "记录到", "挂载", "挂到")):
                raw = re.split(r"[：:]\s*", text, maxsplit=1)
                content = raw[1].strip() if len(raw) > 1 else ""
                if content:
                    item = self._wiki_add(content, title=content[:30], source="natural")
                    kb_msg = await self._astrbot_kb_add(item["title"], item["text"])
                    self._halt(event)
                    yield event.plain_result(f"已加入共享 wiki：{item['title']} {kb_msg}")
                    return
            if any(w in text for w in ("提取", "沉淀", "归档")):
                pid = self._find_persona_mention(text) or self._active_persona()
                keyword = ""
                m = re.search(r"(?:提取|沉淀|归档)[：:\s]*(.+)$", text, re.S)
                if m:
                    raw_kw = m.group(1).strip()
                    if not re.match(r"^(?:到|进|入|至)", raw_kw):
                        keyword = raw_kw
                n = self._promote_memories(pid, keyword)
                self._halt(event)
                if n == 0:
                    yield event.plain_result(
                        f"人格 {pid} 没有可同步的知识型记忆。\n"
                        "经历/偏好不会直接进知识库；请用“知识：…”或 /memory know 先写入知识型记忆。"
                    )
                else:
                    yield event.plain_result(f"已从人格 {pid} 同步 {n} 条知识型记忆到共享 wiki。")
                return
            if any(w in text for w in ("搜索", "查找", "查一下")):
                q = re.sub(r".*(?:wiki|知识库|共享知识)", "", text, flags=re.I).strip(" ：:，,")
                items = self._wiki_search(q, limit=8)
                self._halt(event)
                if not items:
                    yield event.plain_result("共享 wiki 里没有找到相关知识。")
                else:
                    yield event.plain_result(
                        "共享 wiki：\n" + "\n".join(f"- [{x.get('title','')}] {x.get('text','')[:150]}" for x in items)
                    )
                return
            if any(w in text for w in ("查看", "列表", "有什么", "看看")):
                self._halt(event)
                yield event.plain_result(self._wiki_list_text())
                return

        # Prompt-card drafting.
        if (
            any(w in text for w in ("生成", "写", "设计", "起草", "帮我做"))
            and any(w in text for w in ("提示卡", "提示词", "人格卡", "人设", "角色卡"))
            and not any(w in text for w in ("创建", "新建", "做一个", "做个", "加一个", "建一个", "开一个"))
        ):
            m = re.search(r"(?:叫|名字是|角色是|角色名[是为：:]?)\s*([^\s，,。；;]+)", text)
            name = m.group(1).strip() if m else "新角色"
            desc = text
            m = re.search(r"(?:设定|描述|人设|角色是|是)\s*[：:，,]?\s*(.+)$", text, re.S)
            if m:
                desc = m.group(1).strip()
            self._halt(event)
            yield event.plain_result(
                "给你一版可复制的人格提示卡：\n\n" + self._generate_prompt_card(name, desc)
            )
            return

        persona_words = ("人格", "角色", "人设", "角色卡", "persona")
        if any(h in text for h in create_hints) and any(w in text for w in persona_words):
            nickname = self._extract_nickname(text)
            if not nickname:
                m = re.search(r"(?:叫|叫做)\s*([^\s，,。；;、]+)", text)
                nickname = m.group(1).strip() if m else None
            prompt = self._extract_prompt(text)
            if nickname and prompt:
                m = re.search(r"(?:id|标识)\s*(?:是|为|：|:)?\s*([A-Za-z0-9_-]{1,40})", text)
                persona_id = m.group(1) if m else "persona_" + uuid.uuid4().hex[:8]
                if persona_id in self._persona_ids():
                    self._halt(event)
                    yield event.plain_result(f"人格 {persona_id} 已存在，换个 id 或直接修改它。")
                    return
                try:
                    await self._upsert_native_persona(persona_id, prompt)
                    p = self._profile(persona_id)
                    p.update({
                        "nickname": nickname[:40],
                        "prompt": prompt,
                        "prompt_base": prompt,
                        "updated_at": int(time.time()),
                    })
                    image_url = self._first_image(event)
                    if image_url:
                        await self._save_avatar(persona_id, image_url)
                    else:
                        p.setdefault("avatar_path", "")
                    self._save_profiles()
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"创建失败：{e}")
                    return
                self._halt(event)
                logger.info("[persona_forge] one-shot persona create: %s", persona_id)
                if image_url:
                    yield event.plain_result(
                        f"已创建人格 {persona_id}（昵称={nickname}，提示词/人格卡已写入）。\n"
                        f"/persona use {persona_id} 切换。"
                    )
                else:
                    yield event.plain_result(
                        f"已创建人格 {persona_id}（昵称={nickname}），但还没头像。\n"
                        f"发图并配文 /persona avatar {persona_id}，再 /persona use {persona_id}。"
                    )
                return
            self.pending[user] = {"stage": "id", "ts": time.time()}
            self._halt(event)
            logger.info("[persona_forge] natural create intent from %s: %s", user, text[:80])
            yield event.plain_result(
                "好，创建人格。先发一个英文 id（字母/数字/下划线/短横线），或发“取消”。\n"
                "也可以一句话说全：创建人格，id=liv，昵称=丽芙，提示词=你是一只……"
            )
            return

        if any(w in text for w in ("重写", "润色", "改写")) and any(w in text for w in ("提示词", "人格卡", "人设", "设定")):
            pid = self._find_persona_mention(text) or self._active_persona()
            result = await self._rewrite_persona_prompt_llm(pid, event.unified_msg_origin)
            self._halt(event)
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
            return

        if ("迭代" in text or "自动更新" in text or "根据记忆" in text) and "提示词" in text:
            pid = self._find_persona_mention(text) or self._active_persona()
            result = await self._iterate_persona_prompt(pid)
            self._halt(event)
            if result == "no_knowledge":
                yield event.plain_result(f"人格 {pid} 还没有知识型记忆，未迭代。")
            elif result == "no_base":
                yield event.plain_result(f"人格 {pid} 没有基础提示词，未迭代。")
            elif result == "unchanged":
                yield event.plain_result(f"人格 {pid} 提示词已是最新。")
            else:
                yield event.plain_result(f"人格 {pid} 提示词已迭代：{result}")
            return

        if self._looks_like_profile_edit(event, text):
            target = self._find_persona_mention(text)
            if not target:
                self._halt(event)
                yield event.plain_result("要修改哪个人格？说“修改丽芙的昵称/提示词/头像”。")
                return
            nickname = self._extract_nickname(text)
            prompt = self._extract_prompt(text)
            avatar = self._extract_avatar(event, text)
            if not any((nickname, prompt, avatar)):
                if "头像" in text or "图片" in text:
                    query = self._image_query(text, {"nickname": self._profile(target).get("nickname", ""), "id": target})
                    urls = await self._search_image_urls(query)
                    state = {
                        "kind": "profile_edit", "target": target, "id": target,
                        "nickname": nickname, "prompt": prompt,
                        "candidates": urls, "index": 0, "preview_path": "",
                        "stage": "avatar_pick" if urls else "avatar_upload", "ts": time.time(),
                    }
                    self.pending[user] = state
                    self._halt(event)
                    if not urls:
                        yield event.plain_result(
                            f"没搜到合适头像。请直接发一张图片给 {target}，流程不会退出。"
                        )
                        return
                    path, msg = await self._prepare_candidate(state)
                    if not path:
                        state["stage"] = "avatar_upload"
                        yield event.plain_result(f"{msg} 请直接发图片。")
                        return
                    yield event.image_result(path)
                    yield event.plain_result(msg)
                    return
                self._halt(event)
                yield event.plain_result(
                    f"要修改人格 {target} 的什么？\n"
                    "可以一句话一起发：昵称改成XX，提示词改成XX，并附一张头像图。"
                )
                return
            try:
                msg = await self._apply_profile_edit(target, nickname, prompt, avatar)
            except Exception as e:
                self._halt(event)
                yield event.plain_result(f"修改失败：{e}")
                return
            self._halt(event)
            yield event.plain_result(msg)
            return

        if re.search(
            r"(退出.{0,4}(人格|角色)"
            r"|(恢复|回到|变回|切回|退回|换回|改回).{0,6}(默认|基础).{0,6}(人格|角色)?"
            r"|(切换|换)(到|回)?\s*(默认|基础)人格"
            r"|用(默认|基础)人格"
            r"|恢复默认|回到默认)",
            text,
        ):
            try:
                yield event.plain_result(await self._use_persona("default", event.unified_msg_origin))
            except Exception as e:
                yield event.plain_result(
                    f"退出失败，保持当前人格：{e}\n"
                    "先设置默认人格：/persona create default|昵称|提示词，"
                    "再 /persona avatar default 上传头像。"
                )
            self._halt(event)
            return

        if re.search(r"(人格列表|有哪些人格|查看人格|看看人格|所有人格)", text):
            self._halt(event)
            yield event.plain_result(self._persona_list_text())
            return

        if re.search(r"(查看|看看|显示).{0,4}人格资料|人格资料", text):
            pid = self._find_persona_mention(text) or self._active_persona()
            p = self._profile(pid)
            self._halt(event)
            yield event.plain_result(
                f"人格资料 {pid}:\n"
                f"昵称：{p.get('nickname', '-')}\n"
                f"头像：{p.get('avatar_path', '-')}\n"
                f"头像 SHA256：{p.get('avatar_sha256', '-')}\n"
                f"提示词已保存：{'是' if p.get('prompt') else '否'}"
            )
            return

        if re.search(
            r"(修改|编辑|更改|调整|配置).{0,8}(默认|基础)人格|(默认|基础)人格.{0,8}(修改|编辑|更改|配置)",
            text,
        ):
            # Direct edit if a value was supplied in the same message.
            image_url = self._first_image(event)
            if image_url and "头像" in text:
                try:
                    await self._save_avatar("default", image_url)
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"保存默认头像失败：{e}")
                    return
                self._halt(event)
                yield event.plain_result("默认人格头像已更新。")
                return

            m = re.search(
                r"(?:昵称|名字|称呼).{0,10}?(?:改成|改为|设置成|设置为|设为|叫|是)\s*([^\s，。！？]+)",
                text,
            )
            if m:
                nickname = m.group(1).strip()
                p = self._profile("default")
                p["nickname"] = nickname[:40]
                p["updated_at"] = int(time.time())
                self._save_profiles()
                self._halt(event)
                yield event.plain_result(f"默认人格昵称已改为：{nickname}")
                return

            m = re.search(
                r"(?:提示词|设定|人设|人格卡|prompt).{0,10}?(?:改成|改为|设置成|设置为|设为|是|为)\s*(.+)$",
                text, re.S,
            )
            if m:
                prompt = m.group(1).strip()
                await self._upsert_native_persona("default", prompt)
                p = self._profile("default")
                p["prompt"] = prompt
                p["prompt_base"] = prompt
                self._save_profiles()
                self._halt(event)
                yield event.plain_result("默认人格提示词/人格卡已更新。")
                return

            stage = "menu"
            if "昵称" in text or "名字" in text:
                stage = "nickname"
            elif "提示词" in text or "人格卡" in text or "人设" in text or "设定" in text:
                stage = "prompt"
            elif "头像" in text:
                stage = "avatar"
            elif "全部" in text or "都改" in text:
                stage = "nickname"
            self.pending[user] = {
                "kind": "default_edit", "stage": stage, "mode": "all" if "全部" in text or "都改" in text else "one",
                "ts": time.time(),
            }
            self._halt(event)
            if stage == "nickname":
                yield event.plain_result("发新的默认人格昵称。")
            elif stage == "prompt":
                yield event.plain_result("发新的默认人格提示词/人格卡。")
            elif stage == "avatar":
                yield event.plain_result("发一张图片作为默认人格头像。")
            else:
                yield event.plain_result(
                    "要改默认人格的什么？回复：昵称 / 提示词 / 头像 / 全部 / 取消"
                )
            return

        if "默认人格" in text or "基础人格" in text:
            # Show default profile.
            if re.search(r"(查看|看看|是什么|是谁|资料|信息|当前|状态)", text) and not re.search(
                r"(设为|设置|改成|改为|叫|昵称|头像|提示词|设定)", text
            ):
                p = self._profile("default")
                ready = "✅" if p.get("nickname") and p.get("avatar_path") else "⚠️"
                self._halt(event)
                yield event.plain_result(
                    f"{ready} 基础默认人格：default\n"
                    f"昵称：{p.get('nickname', '-')}\n"
                    f"头像：{p.get('avatar_path', '-')}\n"
                    "修改昵称：默认人格昵称叫XXX\n"
                    "修改头像：发图并说“默认人格头像”\n"
                    "修改提示词：默认人格提示词是XXX\n"
                    "设为默认：把默认人格设置为XXX"
                )
                return

            # Default nickname.
            m = re.search(
                r"(?:昵称|名字|称呼).{0,8}?(?:改成|改为|设置成|设置为|设为|叫|是)\s*([^\s，。！？]+)",
                text,
            )
            if m:
                nickname = m.group(1).strip()
                p = self._profile("default")
                p["nickname"] = nickname[:40]
                p["updated_at"] = int(time.time())
                self._save_profiles()
                self._halt(event)
                yield event.plain_result(f"默认人格昵称已改为：{nickname}")
                return

            # Default avatar: send an image with a message containing 默认人格头像.
            image_url = self._first_image(event)
            if image_url and "头像" in text:
                try:
                    await self._save_avatar("default", image_url)
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"保存默认头像失败：{e}")
                    return
                self._halt(event)
                yield event.plain_result("默认人格头像已更新。")
                return

            # Default prompt.
            m = re.search(
                r"(?:提示词|设定|人设|人格卡|prompt).{0,8}?(?:改成|改为|设置成|设置为|设为|是|为)\s*(.+)$",
                text, re.S,
            )
            if m:
                prompt = m.group(1).strip()
                await self._upsert_native_persona("default", prompt)
                p = self._profile("default")
                p["prompt"] = prompt
                p["prompt_base"] = prompt
                self._save_profiles()
                self._halt(event)
                yield event.plain_result("默认人格提示词已更新。")
                return

            # Set default persona to another existing persona.
            m = re.search(
                r"(?:默认|基础)人格.{0,14}?(?:设为|设置成|设置为|改成|改为|换成|是|为|叫)\s*([^\s，。！？]+)",
                text,
            )
            if not m:
                m = re.search(r"(?:默认|基础)人格[：:\s]+([^\s，。！？]+)", text)
            if m:
                pid = self._resolve_persona(m.group(1).strip())
                if not pid:
                    self._halt(event)
                    yield event.plain_result(f"找不到人格：{m.group(1).strip()}")
                    return
                await self._set_default_native_persona(pid)
                self._halt(event)
                yield event.plain_result(f"已设置默认人格：{pid}")
                return

            self._halt(event)
            yield event.plain_result(
                "默认识别：\n"
                "把默认人格设置为 XXX\n"
                "默认人格昵称叫 XXX\n"
                "发图并说“默认人格头像”\n"
                "默认人格提示词是 XXX"
            )
            return

        m = re.match(r"^(?:切换到|切换|使用|换到|用)\s*(.+)$", text)
        if m and "人格" in text:
            ident = re.sub(r"^人格\s*", "", m.group(1).strip())
            ident = re.sub(r"\s*人格$", "", ident).strip()
            pid = self._resolve_persona(ident)
            if not pid:
                yield event.plain_result(f"找不到人格：{ident}")
            else:
                try:
                    yield event.plain_result(await self._use_persona(pid, event.unified_msg_origin))
                except Exception as e:
                    yield event.plain_result(f"切换失败，保持当前人格：{e}")
            self._halt(event)
            return

        if re.search(r"(用|换|切换|模式|扮演|来|和|跟|聊)", text):
            pid = self._find_persona_mention(text)
            if pid and pid != self._active_persona():
                try:
                    yield event.plain_result(await self._use_persona(pid, event.unified_msg_origin))
                except Exception as e:
                    yield event.plain_result(f"切换失败，保持当前人格：{e}")
                self._halt(event)
                return

        m = re.match(r"^(?:默认人格|设默认人格)[：:\s]*(?:是|为)?\s*(.+)$", text)
        if m:
            pid = self._resolve_persona(m.group(1).strip())
            if not pid:
                yield event.plain_result("找不到这个人格。")
            else:
                await self._set_default_native_persona(pid)
                yield event.plain_result(f"已设置默认人格：{pid}")
            self._halt(event)
            return

        m = re.match(r"^(?:知识|学习|记知识|知识型记忆)[：:，,\s]*(.+)$", text, re.S)
        if m:
            try:
                yield event.plain_result(self._memory_add(m.group(1), kind="knowledge"))
            except Exception as e:
                yield event.plain_result(str(e))
            self._halt(event)
            return

        m = re.match(r"^(?:记住|请记住|帮我记住)[：:，,\s]*(.+)$", text, re.S)
        if m:
            try:
                yield event.plain_result(self._memory_add(m.group(1)))
            except Exception as e:
                yield event.plain_result(str(e))
            self._halt(event)
            return

        m = re.match(r"^(?:忘掉|忘记|删除记忆)[：:，,\s]*(.+)$", text, re.S)
        if m:
            yield event.plain_result(self._memory_forget(m.group(1).strip()))
            self._halt(event)
            return

        if re.search(r"(查看|列出|看看).{0,6}记忆", text):
            yield event.plain_result(self._memory_list())
            self._halt(event)
            return

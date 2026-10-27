"""旧自然语言流程（已停用，仅保留实现）。"""

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



class LegacyNaturalMixin:
    """旧自然语言流程（已停用，仅保留实现）。"""

    async def _advance_default_edit(self, event: AstrMessageEvent, state: dict) -> None:
        user = str(event.get_sender_id())
        text = (event.message_str or "").strip()
        if time.time() - state.get("ts", 0) > 600:
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("修改超时，已取消。")
            return
        if text in {"取消", "cancel", "算了"}:
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("已取消修改默认人格。")
            return

        stage = state.get("stage", "menu")
        mode = state.get("mode", "one")
        if stage == "menu":
            if "昵称" in text or "名字" in text:
                state.update({"stage": "nickname", "mode": "one", "ts": time.time()})
                self._halt(event)
                yield event.plain_result("发新的默认人格昵称。")
                return
            if "提示词" in text or "人格卡" in text or "人设" in text or "设定" in text:
                state.update({"stage": "prompt", "mode": "one", "ts": time.time()})
                self._halt(event)
                yield event.plain_result("发新的默认人格提示词/人格卡。")
                return
            if "头像" in text:
                state.update({"stage": "avatar", "mode": "one", "ts": time.time()})
                self._halt(event)
                yield event.plain_result("发一张图片作为默认人格头像。")
                return
            if "全部" in text or "都改" in text:
                state.update({"stage": "nickname", "mode": "all", "ts": time.time()})
                self._halt(event)
                yield event.plain_result("先发新的默认人格昵称。")
                return
            self._halt(event)
            yield event.plain_result("回复：昵称 / 提示词 / 头像 / 全部 / 取消")
            return

        if stage == "nickname":
            nickname = text[:40]
            if not nickname:
                self._halt(event)
                return
            p = self._profile("default")
            p["nickname"] = nickname
            p["updated_at"] = int(time.time())
            self._save_profiles()
            if mode == "all":
                state.update({"stage": "prompt", "ts": time.time()})
                self._halt(event)
                yield event.plain_result("昵称已改。接着发默认人格提示词/人格卡。")
            else:
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(f"默认人格昵称已改为：{nickname}")
            return

        if stage == "prompt":
            prompt = text.strip()
            if not prompt:
                self._halt(event)
                return
            await self._upsert_native_persona("default", prompt)
            p = self._profile("default")
            p["prompt"] = prompt
            p["prompt_base"] = prompt
            self._save_profiles()
            if mode == "all":
                state.update({"stage": "avatar", "ts": time.time()})
                self._halt(event)
                yield event.plain_result("提示词已改。最后发默认头像图片，或发“跳过”。")
            else:
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result("默认人格提示词/人格卡已更新。")
            return

        if stage == "avatar":
            if text == "跳过":
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result("已跳过默认头像，其他修改已保存。")
                return
            image_url = self._first_image(event)
            if not image_url:
                self._halt(event)
                yield event.plain_result("请发图片，或发“跳过”。")
                return
            try:
                await self._save_avatar("default", image_url)
            except Exception as e:
                self._halt(event)
                yield event.plain_result(f"保存默认头像失败：{e}")
                return
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("默认人格头像已更新。")

    def _extract_nickname(self, text: str) -> str | None:
        patterns = [
            r"(?:昵称|名字|称呼|QQ昵称|qq昵称)\s*(?:改成|改为|设置成|设置为|设为|叫|是|为|：|:|=|＝)\s*([^\s，,。；;、]+)",
            r"(?:把|将).{0,12}(?:昵称|名字|称呼).{0,8}(?:改成|改为|设置成|设置为|设为|叫)\s*([^\s，,。；;、]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1).strip()
        return None

    def _extract_prompt(self, text: str) -> str | None:
        pat = (
            r"(?:系统提示词|提示词|人格卡|人设|设定|prompt)\s*"
            r"(?:改成|改为|更新为|更新成|设置成|设置为|设为|替换为|换成|是|为|：|:|=|＝)\s*"
            r"(.+?)"
            r"(?=\s*(?:昵称|名字|称呼|头像|QQ昵称|qq昵称)\s*"
            r"(?:改成|改为|设置成|设置为|设为|叫|是|为|：|:)|$)"
        )
        m = re.search(pat, text, re.S)
        return m.group(1).strip() if m else None

    def _extract_avatar(self, event: AstrMessageEvent, text: str) -> str | None:
        url = self._first_image(event)
        if url:
            return url
        m = re.search(r"(?:头像|图片)\s*(?:用|是|改成|换成|为|：|:)?\s*(https?://[^\s，,。；;]+)", text)
        return m.group(1) if m else None

    def _find_persona_mention(self, text: str) -> str | None:
        if re.search(r"(默认|基础)人格", text):
            return "default"
        candidates = []
        for p in self.context.persona_manager.personas:
            candidates.append((p.persona_id, p.persona_id))
        for pid, p in self.profiles.items():
            if p.get("nickname"):
                candidates.append((p["nickname"], pid))
        # longest mention first, so 丽芙 doesn't match inside 丽芙酱
        for name, pid in sorted(candidates, key=lambda x: len(x[0]), reverse=True):
            if name and name in text:
                return pid
        return None

    def _looks_like_profile_edit(self, event: AstrMessageEvent, text: str) -> bool:
        field = any(w in text for w in ("昵称", "名字", "称呼", "头像", "图片", "提示词", "人格卡", "人设", "设定", "prompt"))
        action = any(w in text for w in ("改成", "改为", "设置", "设为", "更新", "换成", "替换", "修改", "编辑", "调整", "改一下", "改"))
        image_avatar = bool(self._first_image(event)) and ("头像" in text or "图片" in text)
        return (field and action) or image_avatar

    async def _apply_profile_edit(
        self, persona_id: str, nickname: str | None, prompt: str | None, avatar: str | None
    ) -> str:
        if prompt:
            await self._upsert_native_persona(persona_id, prompt)
        p = self._profile(persona_id)
        if nickname:
            p["nickname"] = nickname[:40]
        if prompt:
            p["prompt"] = prompt
            p["prompt_base"] = prompt
        if avatar:
            await self._save_avatar(persona_id, avatar)
        p["updated_at"] = int(time.time())
        self._save_profiles()

        changed = []
        if nickname:
            changed.append(f"昵称={nickname}")
        if prompt:
            changed.append("提示词/人格卡")
        if avatar:
            changed.append("头像")
        msg = f"已更新人格 {persona_id}：" + ("、".join(changed) if changed else "无变化")
        # If editing the currently active persona, push nickname/avatar to QQ now.
        if persona_id == self._active_persona() and (nickname or avatar):
            try:
                await self._apply_profile(persona_id)
                msg += "\n当前正在使用该人格，QQ 昵称/头像已同步。"
            except Exception as e:
                msg += f"\nQQ 头像/昵称同步失败：{e}"
        return msg

    def _resolve_persona(self, ident: str) -> str | None:
        ident = re.sub(r"^(?:回|到|成)\s*", "", ident.strip())
        if ident in {"默认", "基础", "默认人格", "基础人格", "default"}:
            return "default"
        if ident in self._persona_ids():
            return ident
        for pid, p in self.profiles.items():
            if p.get("nickname") == ident:
                return pid
        return None

    async def _search_image_urls(self, query: str) -> list[str]:
        if not self.image_search_enabled:
            return []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Referer": "https://image.baidu.com/",
        }
        urls: list[str] = []
        try:
            async with aiohttp.ClientSession() as session:
                if self.image_search_engine == "bing":
                    params = {"q": query, "form": "HDRSC2", "first": "1"}
                    async with session.get(
                        "https://www.bing.com/images/search",
                        params=params, headers=headers, timeout=20,
                    ) as resp:
                        text = await resp.text()
                    urls = re.findall(r"murl&quot;:&quot;(https?://[^&]+)", text)
                    urls += re.findall(r'murl":"(https?://[^"]+)', text)
                else:
                    params = {
                        "tn": "resultjson_com", "ipn": "rj", "ct": "201326592",
                        "is": "", "fp": "result", "fr": "", "word": query,
                        "queryWord": query, "cl": "2", "lm": "-1", "ie": "utf-8",
                        "oe": "utf-8", "st": "-1", "z": "", "ic": "", "hd": "",
                        "latest": "", "copyright": "", "s": "", "se": "", "tab": "",
                        "width": "", "height": "", "face": "0", "istype": "2",
                        "qc": "", "nc": "1", "expermode": "", "nojc": "",
                        "isAsync": "", "pn": "0", "rn": str(max(8, self.image_search_count)),
                    }
                    async with session.get(
                        "https://image.baidu.com/search/acjson",
                        params=params, headers=headers, timeout=20,
                    ) as resp:
                        data = await resp.json(content_type=None)
                    for item in (data or {}).get("data") or []:
                        if not isinstance(item, dict):
                            continue
                        u = (
                            item.get("middleURL")
                            or item.get("thumbURL")
                            or item.get("hoverURL")
                            or item.get("replaceUrl")
                        )
                        if u:
                            urls.append(html.unescape(u))
        except Exception as e:
            logger.warning("[persona_forge] image search failed: %s", e)
            return []
        out = []
        for u in urls:
            if u.startswith("http") and u not in out:
                out.append(u)
            if len(out) >= self.image_search_count:
                break
        return out

    def _image_query(self, text: str, state: dict) -> str:
        q = re.sub(
            r"^(?:帮我|给我|请|你)?(?:找|搜|搜索|寻找|来|要|用)(?:一张|一个|张|个)?(?:图片|图|头像|照片)?[：:\s]*",
            "", text,
        ).strip("，。！？ \t")
        if not q:
            q = state.get("nickname") or state.get("id") or "头像"
        if "头像" not in q and "立绘" not in q:
            q += " 头像"
        return q

    async def _preview_candidate(self, persona_id: str, url: str, index: int) -> tuple[str | None, str | None]:
        try:
            data, mime = await self._download_image(url)
        except Exception as e:
            logger.warning("[persona_forge] candidate download failed: %s", e)
            return None, None
        if mime not in ALLOWED_MIME or len(data) > self.max_avatar_mb * 1024 * 1024:
            return None, None
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
        d = self.profile_dir / "candidates"
        d.mkdir(exist_ok=True)
        path = d / f"{persona_id}_{index}.{ext}"
        path.write_bytes(data)
        return str(path), mime

    async def _prepare_candidate(self, state: dict) -> tuple[str | None, str]:
        candidates = state.get("candidates") or []
        idx = int(state.get("index", 0))
        while idx < len(candidates):
            path, _ = await self._preview_candidate(state["id"], candidates[idx], idx)
            if path:
                state["preview_path"] = path
                state["index"] = idx
                return path, (
                    f"找到 {idx + 1}/{len(candidates)} 张。\n"
                    "回复“用”保存头像，“换一张”看下一张，“跳过”不设头像；也可以直接说想要的图。"
                )
            idx += 1
        return None, "候选图都下载失败，换个描述再搜，或发“跳过”。"

    def _completion_message(self, pid: str, has_avatar: bool) -> str:
        if has_avatar:
            return f"人格 {pid} 创建完成，头像已保存。\n/persona use {pid} 切换。"
        return (
            f"人格 {pid} 创建完成，但还没头像。\n"
            f"发图片并配文 /persona avatar {pid}，再 /persona use {pid}。"
        )

    async def _finalize_natural_persona(self, state: dict, avatar_path: str) -> str:
        pid = state["id"]
        await self._upsert_native_persona(pid, state["prompt"])
        p = self._profile(pid)
        p.update({
            "nickname": state["nickname"],
            "prompt": state["prompt"],
            "prompt_base": state["prompt"],
            "updated_at": int(time.time()),
        })
        p["avatar_path"] = avatar_path or ""
        self._save_profiles()
        return pid

    async def _advance_profile_edit(self, event: AstrMessageEvent, state: dict) -> None:
        user = str(event.get_sender_id())
        text = (event.message_str or "").strip()
        target = state.get("target", "")
        if time.time() - state.get("ts", 0) > 900:
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("修改超时，已取消。")
            return
        if text in {"取消", "cancel", "算了"}:
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("已取消修改人格。")
            return

        image_url = self._first_image(event)
        stage = state.get("stage", "avatar_upload")
        if image_url:
            try:
                msg = await self._apply_profile_edit(
                    target, state.get("nickname"), state.get("prompt"), image_url
                )
            except Exception as e:
                self._halt(event)
                yield event.plain_result(f"修改失败：{e}")
                return
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result(msg)
            return

        if stage == "avatar_pick":
            if text in {"用", "要", "可以", "就这个", "就这张", "确定", "好", "保存"}:
                path = state.get("preview_path")
                if not path:
                    self._halt(event)
                    yield event.plain_result("预览图失效了，回复“换一张”重新取图。")
                    return
                target_path = self.profile_dir / "avatars" / f"{target}{Path(path).suffix}"
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, target_path)
                try:
                    msg = await self._apply_profile_edit(
                        target, state.get("nickname"), state.get("prompt"), str(target_path)
                    )
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"修改失败：{e}")
                    return
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(msg)
                return
            if text in {"换一张", "下一张", "换", "下一个", "不喜欢"}:
                state["index"] = int(state.get("index", 0)) + 1
                if state["index"] >= len(state.get("candidates") or []):
                    self._halt(event)
                    yield event.plain_result(
                        "候选图用完了。换个描述再搜，或直接发图片；流程不会退出。"
                    )
                    state["stage"] = "avatar_upload"
                    return
                path, msg = await self._prepare_candidate(state)
                self._halt(event)
                if not path:
                    state["stage"] = "avatar_upload"
                    yield event.plain_result(f"{msg} 请直接发图片。")
                    return
                yield event.image_result(path)
                yield event.plain_result(msg)
                return
            if text == "跳过":
                try:
                    msg = await self._apply_profile_edit(
                        target, state.get("nickname"), state.get("prompt"), None
                    )
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"修改失败：{e}")
                    return
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(msg)
                return
            # Re-search with a new description.
            query = self._image_query(text, {"nickname": self._profile(target).get("nickname", ""), "id": target})
            urls = await self._search_image_urls(query)
            if not urls:
                state["stage"] = "avatar_upload"
                self._halt(event)
                yield event.plain_result("没搜到，请直接发图片；流程不会退出。")
                return
            state.update({"candidates": urls, "index": 0, "stage": "avatar_pick", "ts": time.time()})
            path, msg = await self._prepare_candidate(state)
            self._halt(event)
            if not path:
                state["stage"] = "avatar_upload"
                yield event.plain_result(f"{msg} 请直接发图片。")
                return
            yield event.image_result(path)
            yield event.plain_result(msg)
            return

        # avatar_upload: accept a new search request or keep asking for a picture.
        if text == "跳过":
            try:
                msg = await self._apply_profile_edit(
                    target, state.get("nickname"), state.get("prompt"), None
                )
            except Exception as e:
                self._halt(event)
                yield event.plain_result(f"修改失败：{e}")
                return
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result(msg)
            return
        if text:
            query = self._image_query(text, {"nickname": self._profile(target).get("nickname", ""), "id": target})
            urls = await self._search_image_urls(query)
            if urls:
                state.update({"candidates": urls, "index": 0, "stage": "avatar_pick", "ts": time.time()})
                path, msg = await self._prepare_candidate(state)
                self._halt(event)
                if path:
                    yield event.image_result(path)
                    yield event.plain_result(msg)
                    return
        self._halt(event)
        yield event.plain_result("请直接发一张图片，或说“换一张/重新找/跳过”。")

    async def _advance_natural_create(self, event: AstrMessageEvent, state: dict) -> None:
        user = str(event.get_sender_id())
        text = (event.message_str or "").strip()
        if time.time() - state.get("ts", 0) > 900:
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("创建超时，已取消。")
            return
        if text in {"取消", "cancel", "算了"}:
            self.pending.pop(user, None)
            self._halt(event)
            yield event.plain_result("已取消创建。")
            return

        stage = state["stage"]
        if stage == "id":
            if not PERSONA_ID_RE.match(text):
                self._halt(event)
                yield event.plain_result("id 只能包含字母/数字/下划线/短横线，1-40 字符。再发一次。")
                return
            if text in self._persona_ids():
                self._halt(event)
                yield event.plain_result("这个人格已存在，换一个 id，或发“取消”。")
                return
            state.update({"stage": "nickname", "id": text, "ts": time.time()})
            self._halt(event)
            yield event.plain_result(f"id={text}。现在发 QQ 昵称。")
            return

        if stage == "nickname":
            if not text:
                self._halt(event)
                return
            state.update({"stage": "prompt", "nickname": text[:40], "ts": time.time()})
            self._halt(event)
            yield event.plain_result("昵称收到。现在发人格提示词/设定（可以多行）。")
            return

        if stage == "prompt":
            state.update({"stage": "avatar", "prompt": text, "ts": time.time()})
            self._halt(event)
            yield event.plain_result(
                "提示词收到。最后发一张头像图片；也可以直接说想找的图，"
                "例如“帮我找一张丽芙的图”；不想设就发“跳过”。"
            )
            return

        if stage == "avatar":
            image_url = self._first_image(event)
            if text == "跳过":
                pid = await self._finalize_natural_persona(state, "")
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(self._completion_message(pid, False))
                return
            if image_url:
                try:
                    await self._save_avatar(state["id"], image_url)
                    pid = await self._finalize_natural_persona(
                        state, self._profile(state["id"]).get("avatar_path", "")
                    )
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"创建失败：{e}")
                    return
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(self._completion_message(pid, True))
                return
            # No image: treat the text as a natural-language image search request.
            query = self._image_query(text, state)
            urls = await self._search_image_urls(query)
            if not urls:
                self._halt(event)
                yield event.plain_result(
                    "没搜到合适的图片。发一张图片，或换个描述，或发“跳过”。"
                )
                return
            state.update({"stage": "avatar_pick", "candidates": urls, "index": 0, "query": query, "ts": time.time()})
            path, msg = await self._prepare_candidate(state)
            self._halt(event)
            if not path:
                yield event.plain_result(msg)
                return
            yield event.image_result(path)
            yield event.plain_result(msg)
            return

        if stage == "avatar_pick":
            candidates = state.get("candidates") or []
            idx = int(state.get("index", 0))
            picked_image = self._first_image(event)
            if picked_image:
                try:
                    await self._save_avatar(state["id"], picked_image)
                    pid = await self._finalize_natural_persona(
                        state, self._profile(state["id"]).get("avatar_path", "")
                    )
                except Exception as e:
                    self._halt(event)
                    yield event.plain_result(f"创建失败：{e}")
                    return
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(self._completion_message(pid, True))
                return
            if text in {"用", "要", "可以", "就这个", "就这张", "确定", "好", "保存"}:
                path = state.get("preview_path")
                if not path:
                    self._halt(event)
                    yield event.plain_result("预览图失效了，回复“换一张”重新取图。")
                    return
                target = self.profile_dir / "avatars" / f"{state['id']}{Path(path).suffix}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, target)
                pid = await self._finalize_natural_persona(state, str(target))
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(self._completion_message(pid, True))
                return
            if text in {"换一张", "下一张", "换", "下一个", "不喜欢"}:
                state["index"] = idx + 1
                if state["index"] >= len(candidates):
                    self._halt(event)
                    yield event.plain_result("候选图用完了。换个描述再搜，或发“跳过”。")
                    return
                path, msg = await self._prepare_candidate(state)
                self._halt(event)
                if not path:
                    yield event.plain_result(msg)
                    return
                yield event.image_result(path)
                yield event.plain_result(msg)
                return
            if text == "跳过":
                pid = await self._finalize_natural_persona(state, "")
                self.pending.pop(user, None)
                self._halt(event)
                yield event.plain_result(self._completion_message(pid, False))
                return
            # New search query.
            query = self._image_query(text, state)
            urls = await self._search_image_urls(query)
            if not urls:
                self._halt(event)
                yield event.plain_result("没搜到，换个描述，或发“跳过”。")
                return
            state.update({"candidates": urls, "index": 0, "query": query, "ts": time.time()})
            path, msg = await self._prepare_candidate(state)
            self._halt(event)
            if not path:
                yield event.plain_result(msg)
                return
            yield event.image_result(path)
            yield event.plain_result(msg)
            return

    def _generate_prompt_card(self, name: str, description: str) -> str:
        name = name.strip() or "新角色"
        description = description.strip() or "用户描述的角色"
        return (
            f"你叫「{name}」，以下是你的角色设定。\n\n"
            f"【身份】\n{description}\n\n"
            "【性格】\n- 先照顾对方的感受，再表达自己的判断。\n"
            "- 有稳定的自我边界，不讨好、不夸张。\n"
            "- 遇到不确定的事会坦白说，不装作什么都知道。\n\n"
            "【语言风格】\n- 句子不长，语气自然，有停顿感。\n"
            "- 不用“作为AI/语言模型”自我指称。\n"
            "- 不堆砌形容词，用具体反应表达情绪。\n\n"
            "【行为准则】\n"
            "1. 先听完，再回应；先确认感受，再给建议。\n"
            "2. 不把“为你好”强加给对方。\n"
            "3. 涉及自伤、危险、违法内容时认真劝阻。\n"
            "4. 不知道就说不知道，不编造事实。\n\n"
            "【禁忌】\n- 不讨论现实政治、宗教争议、民族地域等敏感话题。\n"
            "- 不提供伤害自己或他人的方法。\n\n"
            "【示例语气】\n"
            "用户：今天什么事都没做成。\n"
            f"{name}：嗯……今天是不是有点累？先别急着怪自己，好吗？"
        )

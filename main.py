from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import re
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path

import aiohttp
from sqlalchemy import select, update

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image
from astrbot.api.web import error_response, json_response, request
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.db.po import ConversationV2, Persona

PLUGIN_UPDATE_ID = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
AUTO_PROMPT_HEADER = "\n\n【人格专属知识（自动迭代）】"
PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}


class AssistantPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        cfg = config or {}
        self.profile_dir = Path(cfg.get("profile_dir", "/AstrBot/data/persona_profiles"))
        self.memory_dir = Path(cfg.get("memory_dir", "/AstrBot/data/persona_memory"))
        self.wiki_file = Path(cfg.get("wiki_file", "/AstrBot/data/shared_wiki.json"))
        self.astrbot_kb_name = str(cfg.get("astrbot_kb_name", "")).strip()
        self._switch_lock = asyncio.Lock()
        self.backup_dir = Path(cfg.get("backup_dir", "/AstrBot/data/life_card/backups"))
        self.request_dir = Path(cfg.get("request_dir", "/AstrBot/data/life_card/requests"))
        self.result_dir = Path(cfg.get("result_dir", "/AstrBot/data/life_card/results"))
        self.allow_restore = bool(cfg.get("allow_restore", False))
        self.snowluma_http = str(cfg.get("snowluma_http", "")).rstrip("/")
        self.snowluma_token = str(cfg.get("snowluma_token", "")).strip()
        self.max_avatar_mb = int(cfg.get("max_avatar_mb", 5))
        self.auto_llm_rewrite = bool(cfg.get("auto_llm_rewrite", False))
        self.image_search_enabled = bool(cfg.get("image_search_enabled", True))
        self.image_search_engine = str(cfg.get("image_search_engine", "baidu"))
        self.image_search_count = int(cfg.get("image_search_count", 5))
        self.version_retention = int(cfg.get("version_retention", 20))
        self.group_mode = str(cfg.get("group_mode", "off"))
        self.group_whitelist = {str(x) for x in (cfg.get("group_whitelist", []) or [])}
        self.require_at = bool(cfg.get("require_at", True))
        self.guest_wiki_read = str(cfg.get("guest_wiki", "read"))
        self.guest_rate_limit = int(cfg.get("guest_rate_limit_per_minute", 5))
        self.nickname_sync_mode = str(cfg.get("nickname_sync_mode", "profile"))
        self._guest_hits: dict[str, list[float]] = {}
        self.versions_dir = self.profile_dir / "versions"
        self.reviews_dir = self.profile_dir / "reviews"
        self.exports_dir = self.profile_dir / "exports"
        for d in (self.profile_dir, self.memory_dir, self.backup_dir, self.request_dir, self.result_dir,
                  self.versions_dir, self.reviews_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)
        (self.profile_dir / "avatars").mkdir(exist_ok=True)
        self.profiles_path = self.profile_dir / "profiles.json"
        self.state_path = self.profile_dir / "runtime_state.json"
        self.state = self._load_json(self.state_path, {})
        gm = self.state.get("group_mode") if isinstance(self.state.get("group_mode"), dict) else {}
        if gm:
            self.group_mode = str(gm.get("group_mode", self.group_mode))
            self.group_whitelist = {str(x) for x in (gm.get("group_whitelist") or [])}
            self.require_at = bool(gm.get("require_at", self.require_at))
            self.guest_wiki_read = str(gm.get("guest_wiki", self.guest_wiki_read))
            self.guest_rate_limit = int(gm.get("guest_rate_limit_per_minute", self.guest_rate_limit))
        self.profiles = self._load_json(self.profiles_path, {})
        changed = False
        for _pid, _p in self.profiles.items():
            _ap = _p.get("avatar_path")
            if _ap and not _p.get("avatar_sha256") and Path(_ap).is_file():
                _p["avatar_sha256"] = hashlib.sha256(Path(_ap).read_bytes()).hexdigest()
                changed = True
        if changed:
            self._save_profiles()
        self.pending: dict[str, dict] = {}
        self.command_pending: dict[str, dict] = {}
        self._register_web_apis()
        logger.info("[assistant] profile=%s memory=%s", self.profile_dir, self.memory_dir)

    # ---------- plugin view APIs ----------
    def _register_web_apis(self) -> None:
        prefix = "/astrbot_plugin_assistant"
        routes = [
            (f"{prefix}/personas", self.api_personas, ["GET"], "List personas"),
            (f"{prefix}/versions", self.api_versions, ["GET"], "Persona versions"),
            (f"{prefix}/reviews", self.api_reviews, ["GET"], "Pending reviews"),
            (f"{prefix}/reviews/action", self.api_review_action, ["POST"], "Approve/reject review"),
            (f"{prefix}/switch", self.api_switch, ["POST"], "Switch persona"),
            (f"{prefix}/group-mode", self.api_group_mode, ["GET", "POST"], "Group mode settings"),
            (f"{prefix}/life-card", self.api_life_card, ["GET"], "Life card status"),
        ]
        for route, handler, methods, desc in routes:
            try:
                self.context.register_web_api(route, handler, methods, desc)
            except Exception as e:
                logger.warning("[assistant] register_web_api %s failed: %s", route, e)

    async def api_personas(self):
        active = self._active_persona()
        data = []
        for p in self.context.persona_manager.personas:
            prof = self._profile(p.persona_id)
            data.append({
                "id": p.persona_id,
                "nickname": prof.get("nickname", ""),
                "avatar": bool(prof.get("avatar_path")),
                "ready": bool(prof.get("nickname") and prof.get("avatar_path")),
                "active": p.persona_id == active,
                "default": p.persona_id == "default",
                "version": prof.get("prompt_version", 0),
            })
        return json_response({"active": active, "personas": data})

    async def api_versions(self):
        pid = request.query.get("pid", "") or self._active_persona()
        return json_response({"pid": pid, "versions": self._list_versions(pid)})

    async def api_reviews(self):
        return json_response({"reviews": self._list_reviews()})

    async def api_review_action(self):
        payload = await request.json(default={})
        pid = (payload.get("pid") or "").strip()
        action = (payload.get("action") or "").strip()
        if not pid or action not in {"approve", "reject"}:
            return error_response("pid/action required", 400)
        if action == "approve":
            return json_response({"message": await self._approve_review(pid)})
        self._review_path(pid).unlink(missing_ok=True)
        return json_response({"message": f"rejected {pid}"})

    async def api_switch(self):
        payload = await request.json(default={})
        pid = (payload.get("pid") or "").strip()
        try:
            msg = await self._use_persona(pid, None)
            return json_response({"message": msg})
        except Exception as e:
            return error_response(str(e), 400)

    async def api_group_mode(self):
        if request.method == "GET":
            return json_response({
                "group_mode": self.group_mode,
                "group_whitelist": sorted(self.group_whitelist),
                "require_at": self.require_at,
                "guest_wiki": self.guest_wiki_read,
                "guest_rate_limit_per_minute": self.guest_rate_limit,
            })
        payload = await request.json(default={})
        self.group_mode = str(payload.get("group_mode", self.group_mode))
        self.group_whitelist = {str(x) for x in (payload.get("group_whitelist") or [])}
        self.require_at = bool(payload.get("require_at", self.require_at))
        self.guest_wiki_read = str(payload.get("guest_wiki", self.guest_wiki_read))
        self.guest_rate_limit = int(payload.get("guest_rate_limit_per_minute", self.guest_rate_limit))
        self.state["group_mode"] = {
            "group_mode": self.group_mode, "group_whitelist": sorted(self.group_whitelist),
            "require_at": self.require_at, "guest_wiki": self.guest_wiki_read,
            "guest_rate_limit_per_minute": self.guest_rate_limit,
        }
        self._save_state()
        return json_response({"saved": True})

    async def api_life_card(self):
        return json_response({
            "backups": [p.name for p in sorted(self.backup_dir.glob("life-card-*.pialife"))],
            "results": self._life_status_text(),
        })

    # ---------- generic ----------
    @staticmethod
    def _load_json(path: Path, default):
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error("[assistant] invalid json %s: %s", path, e)
        return default

    @staticmethod
    def _save_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _save_profiles(self) -> None:
        self._save_json(self.profiles_path, self.profiles)

    def _profile(self, persona_id: str) -> dict:
        return self.profiles.setdefault(persona_id, {})

    # ---------- persona versions ----------
    def _version_dir(self, persona_id: str) -> Path:
        d = self.versions_dir / persona_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _list_versions(self, persona_id: str) -> list[int]:
        nums = []
        for p in self._version_dir(persona_id).glob("v*.json"):
            try:
                nums.append(int(p.stem[1:]))
            except ValueError:
                pass
        return sorted(nums)

    async def _snapshot_version(self, persona_id: str, reason: str = "update") -> int | None:
        try:
            native = await self.context.persona_manager.get_persona(persona_id)
        except Exception:
            return None
        versions = self._list_versions(persona_id)
        n = (versions[-1] if versions else 0) + 1
        d = self._version_dir(persona_id)
        profile = dict(self._profile(persona_id))
        avatar_saved = ""
        ap = profile.get("avatar_path")
        if ap and Path(ap).is_file():
            suffix = Path(ap).suffix or ".png"
            avatar_saved = f"v{n:04d}.avatar{suffix}"
            shutil.copy2(ap, d / avatar_saved)
        data = {
            "version": n,
            "persona_id": persona_id,
            "created_at": int(time.time()),
            "reason": reason,
            "prompt": native.system_prompt or "",
            "begin_dialogs": native.begin_dialogs or [],
            "tools": native.tools,
            "skills": native.skills,
            "custom_error_message": native.custom_error_message,
            "profile": profile,
            "avatar_file": avatar_saved,
        }
        self._save_json(d / f"v{n:04d}.json", data)
        (d / f"v{n:04d}.prompt.txt").write_text(data["prompt"], encoding="utf-8")
        # retention
        for old in self._list_versions(persona_id)[:-self.version_retention]:
            for f in d.glob(f"v{old:04d}.*"):
                f.unlink(missing_ok=True)
        return n

    async def _update_native_fields(self, persona_id: str, **fields) -> None:
        async with self._db().get_db() as session:
            obj = (
                await session.execute(select(Persona).where(Persona.persona_id == persona_id))
            ).scalar_one_or_none()
            if not obj:
                raise RuntimeError(f"人格 {persona_id} 不存在")
            for k, v in fields.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            await session.commit()
        await self.context.persona_manager.initialize()

    async def _apply_version(self, persona_id: str, version: int) -> str:
        path = self._version_dir(persona_id) / f"v{version:04d}.json"
        if not path.is_file():
            raise RuntimeError(f"版本 {version} 不存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        await self._snapshot_version(persona_id, reason="rollback_pre")
        await self._update_native_fields(
            persona_id,
            system_prompt=data.get("prompt", ""),
            begin_dialogs=data.get("begin_dialogs") or [],
            tools=data.get("tools"),
            skills=data.get("skills"),
            custom_error_message=data.get("custom_error_message"),
        )
        profile = data.get("profile") or {}
        p = self._profile(persona_id)
        p.update(profile)
        avatar_file = data.get("avatar_file")
        if avatar_file:
            src = self._version_dir(persona_id) / avatar_file
            if src.is_file():
                target = self.profile_dir / "avatars" / f"{persona_id}{src.suffix}"
                shutil.copy2(src, target)
                p["avatar_path"] = str(target)
                p["avatar_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        p["updated_at"] = int(time.time())
        self._save_profiles()
        return f"人格 {persona_id} 已回滚到 v{version:04d}"

    # ---------- prompt review ----------
    def _review_path(self, persona_id: str) -> Path:
        return self.reviews_dir / f"{persona_id}.json"

    def _create_review(self, persona_id: str, draft: str, source: str, base: str = "") -> dict:
        import difflib
        if not base:
            base = self._base_prompt(persona_id)
        diff = "\n".join(difflib.unified_diff(
            base.splitlines(), draft.splitlines(),
            fromfile="current", tofile="draft", lineterm="",
        ))
        data = {
            "persona_id": persona_id,
            "base_prompt": base,
            "draft_prompt": draft,
            "source": source,
            "diff": diff[:20000],
            "created_at": int(time.time()),
            "status": "pending",
        }
        self._save_json(self._review_path(persona_id), data)
        return data

    def _list_reviews(self) -> list[dict]:
        out = []
        for p in self.reviews_dir.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return sorted(out, key=lambda x: x.get("created_at", 0), reverse=True)

    async def _approve_review(self, persona_id: str) -> str:
        path = self._review_path(persona_id)
        if not path.is_file():
            raise RuntimeError("没有待审草稿")
        data = json.loads(path.read_text(encoding="utf-8"))
        await self._snapshot_version(persona_id, reason="review_approve")
        await self._update_native_fields(persona_id, system_prompt=data["draft_prompt"])
        p = self._profile(persona_id)
        p["prompt"] = data["draft_prompt"]
        p["prompt_base"] = data["draft_prompt"]
        p["prompt_version"] = int(p.get("prompt_version", 0)) + 1
        p["prompt_updated_at"] = int(time.time())
        self._save_profiles()
        path.unlink(missing_ok=True)
        return f"已应用人格 {persona_id} 的提示词草稿"

    @staticmethod
    def _split(text: str) -> list[str]:
        return [x.strip() for x in text.split("|")]

    @staticmethod
    def _raw_after(event: AstrMessageEvent, subcmd: str, group: str = "persona") -> str:
        text = (event.message_str or "").strip()
        marker = f"/{group} {subcmd}"
        idx = text.find(marker)
        return text[idx + len(marker):].strip() if idx >= 0 else ""

    @staticmethod
    def _group_id(event: AstrMessageEvent) -> str:
        obj = getattr(event, "message_obj", None)
        return str(
            getattr(obj, "group_id", "") if obj is not None else ""
        ) or ""

    @staticmethod
    def _halt(event: AstrMessageEvent) -> None:
        """Stop this event before the LLM stage; plugin intent wins."""
        try:
            event.stop_event()
        except Exception:
            pass
        try:
            event.should_call_llm(False)
        except Exception:
            pass

    def _is_private(self, event: AstrMessageEvent) -> bool:
        if hasattr(event, "is_private_chat"):
            try:
                return bool(event.is_private_chat())
            except Exception:
                pass
        umo = getattr(event, "unified_msg_origin", "") or ""
        parts = umo.split(":")
        return len(parts) >= 2 and parts[1].lower() in {"friendmessage", "private", "friend"}

    def _db(self):
        """AstrBot Context has no public `.db`; managers own the database handle."""
        pm = getattr(self.context, "persona_manager", None)
        if pm is not None and hasattr(pm, "db"):
            return pm.db
        cm = getattr(self.context, "conversation_manager", None)
        if cm is not None and hasattr(cm, "db"):
            return cm.db
        raise RuntimeError("AstrBot database handle not found on context")

    def _persona_ids(self) -> set[str]:
        return {p.persona_id for p in self.context.persona_manager.personas}

    def _persona_list_text(self) -> str:
        active = self._active_persona()
        ids = [p.persona_id for p in self.context.persona_manager.personas]
        if "default" not in ids:
            ids.insert(0, "default")
        lines = []
        for pid in ids:
            p = self._profile(pid)
            ready = "✅" if p.get("nickname") and p.get("avatar_path") else "⚠️"
            marks = []
            if pid == "default":
                marks.append("默认")
            if pid == active:
                marks.append("当前")
            mark = f" [{'/'.join(marks)}]" if marks else ""
            lines.append(
                f"{ready} {pid}{mark}  昵称={p.get('nickname', '-')}  头像={'有' if p.get('avatar_path') else '无'}"
            )
        return "所有人格：\n" + "\n".join(lines)

    # ---------- native persona ----------
    async def _upsert_native_persona(self, persona_id: str, prompt: str) -> None:
        try:
            await self._snapshot_version(persona_id, reason="prompt_update")
        except Exception:
            pass
        async with self._db().get_db() as session:
            existing = (
                await session.execute(select(Persona).where(Persona.persona_id == persona_id))
            ).scalar_one_or_none()
            if existing:
                existing.system_prompt = prompt
                existing.begin_dialogs = existing.begin_dialogs or []
            else:
                session.add(
                    Persona(
                        persona_id=persona_id,
                        system_prompt=prompt,
                        begin_dialogs=[],
                        tools=None,
                        skills=None,
                        custom_error_message=None,
                    )
                )
            await session.commit()
        await self.context.persona_manager.initialize()

    async def _set_default_native_persona(self, persona_id: str) -> None:
        try:
            conf = self.context.persona_manager.acm.default_conf
            runner = conf.setdefault("agent_runner", {})
            runner.setdefault("config", {}).setdefault("persona", {})["persona_id"] = persona_id
            if hasattr(conf, "save_config"):
                conf.save_config()
        except Exception as e:
            logger.warning("[assistant] persist default persona failed: %s", e)
        self.context.persona_manager.default_persona = persona_id
        await self.context.persona_manager.initialize()

    async def _snapshot_current_conversation(self, umo: str, persona_id: str) -> None:
        """Save a compact snapshot of the current conversation into persona memory.

        The next persona gets a fresh conversation (no old-persona history),
        but when switching back the snapshot is injected, so its previous
        dialogue context is inherited as memory rather than raw role history.
        """
        try:
            conv_id = self.context.conversation_manager.session_conversations.get(umo)
            convs = await self._db().get_conversations(user_id=umo)
            conv = None
            if conv_id:
                conv = next((c for c in convs if c.conversation_id == conv_id), None)
            if conv is None and convs:
                conv = max(convs, key=lambda c: int(c.inner_conversation_id or 0))
            content = (conv.content or []) if conv else []
            lines = []
            for m in content[-20:]:
                role = m.get("role", "?")
                text = m.get("content", "")
                if isinstance(text, list):
                    text = " ".join(str(x.get("text", "")) for x in text if isinstance(x, dict))
                if text:
                    lines.append(f"{'用户' if role == 'user' else '助手'}: {str(text)[:500]}")
            if not lines:
                return
            items = self._load_memory(persona_id)
            items.append({
                "id": uuid.uuid4().hex[:12],
                "text": "\n".join(lines),
                "kind": "session",
                "umo": umo,
                "conv_id": conv.conversation_id if conv else "",
                "created_at": int(time.time()),
            })
            self._save_memory(persona_id, items)
        except Exception as e:
            logger.warning("[assistant] snapshot conversation failed: %s", e)

    async def _switch_all_conversations(
        self, persona_id: str, current_umo: str | None = None, force_new: bool = False
    ) -> None:
        """Restore that persona's latest plugin-created conversation per peer.

        Only conversations tagged `persona:<id>` are restored, so a legacy
        default conversation full of another role's history cannot bleed in.
        If none exists, a new conversation is created. Old conversations stay.
        """
        peers: dict[str, str] = {}
        tagged: dict[tuple[str, str], tuple[int, str]] = {}
        try:
            convs = await self._db().get_conversations()
            for c in convs:
                peers[c.user_id] = c.platform_id
                if c.persona_id == persona_id and (c.title or "") == f"persona:{persona_id}":
                    key = (c.user_id, persona_id)
                    seq = int(c.inner_conversation_id or 0)
                    if key not in tagged or seq > tagged[key][0]:
                        tagged[key] = (seq, c.conversation_id)
        except Exception as e:
            logger.warning("[assistant] list conversations failed: %s", e)
        if current_umo:
            peers.setdefault(current_umo, current_umo.split(":")[0] if ":" in current_umo else "unknown")
        for umo, platform_id in peers.items():
            found = None if force_new else tagged.get((umo, persona_id))
            if found:
                self.context.conversation_manager.session_conversations[umo] = found[1]
                continue
            try:
                await self.context.conversation_manager.new_conversation(
                    umo, platform_id=platform_id, persona_id=persona_id,
                    title=f"persona:{persona_id}",
                )
            except Exception as e:
                logger.warning("[assistant] new conversation failed for %s: %s", umo, e)

    # ---------- SnowLuma profile ----------
    async def _snowluma_post(self, action: str, payload: dict) -> dict:
        if not self.snowluma_http:
            raise RuntimeError("未配置 snowluma_http，无法同步 QQ 头像/昵称")
        headers = {"Content-Type": "application/json"}
        if self.snowluma_token:
            headers["Authorization"] = f"Bearer {self.snowluma_token}"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.snowluma_http}/{action}", json=payload, headers=headers, timeout=20
            ) as resp:
                body = await resp.text()
                if resp.status == 401 and self.snowluma_token:
                    # Some SnowLuma builds/proxies only accept query auth.
                    url = f"{self.snowluma_http}/{action}?access_token={urllib.parse.quote(self.snowluma_token)}"
                    async with session.post(url, json=payload, timeout=20) as resp2:
                        body = await resp2.text()
                        if resp2.status != 200:
                            raise RuntimeError(
                                f"SnowLuma {action} HTTP {resp2.status}: {body[:200]}"
                            )
                        data = json.loads(body) if body else {}
                        if data.get("retcode") not in (0, None):
                            raise RuntimeError(f"SnowLuma {action} failed: {data}")
                        return data.get("data") or {}
                if resp.status != 200:
                    raise RuntimeError(f"SnowLuma {action} HTTP {resp.status}: {body[:200]}")
                data = json.loads(body) if body else {}
                if data.get("retcode") not in (0, None):
                    raise RuntimeError(f"SnowLuma {action} failed: {data}")
                return data.get("data") or {}

    async def _apply_profile(self, persona_id: str, group_id: str = "") -> None:
        p = self._profile(persona_id)
        nickname = p.get("nickname")
        avatar = p.get("avatar_path")
        if not nickname or not avatar:
            raise RuntimeError("人格资料不完整：请先设置昵称并上传头像")
        mode = self.nickname_sync_mode
        if group_id and mode in {"group_card", "hybrid"}:
            _, uin, _ = await self._qq_profile_status()
            await self._snowluma_post(
                "set_group_card",
                {"group_id": int(group_id), "user_id": uin, "card": nickname},
            )
        elif not (group_id and mode == "group_card"):
            await self._snowluma_post("set_qq_profile", {"nickname": nickname})
        raw = Path(avatar).read_bytes()
        if len(raw) > self.max_avatar_mb * 1024 * 1024:
            raise RuntimeError("头像超过大小限制")
        await self._snowluma_post(
            "set_qq_avatar",
            {"file": "base64://" + base64.b64encode(raw).decode("ascii")},
        )

    def _save_state(self) -> None:
        self._save_json(self.state_path, self.state)

    @staticmethod
    def _dhash(data: bytes) -> int | None:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8))
            px = list(img.getdata())
            bits = 0
            for row in range(8):
                for col in range(8):
                    bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
            return bits
        except Exception:
            return None

    async def _qq_profile_status(self) -> tuple[str, int, str]:
        data = await self._snowluma_post("get_login_info", {})
        nickname = str(data.get("nickname") or "")
        uin = int(data.get("user_id") or 0)
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640" if uin else ""
        return nickname, uin, avatar_url

    async def _avatar_matches(self, profile_avatar: str, uin: int) -> bool | None:
        if not profile_avatar or not uin:
            return None
        try:
            remote, _ = await self._download_image(f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640")
            local = Path(profile_avatar).read_bytes()
        except Exception:
            return None
        if remote == local:
            return True
        a, b = self._dhash(remote), self._dhash(local)
        if a is None or b is None:
            return None
        return (a ^ b).bit_count() <= 10

    async def _reconcile_persona(self, fix: bool = False) -> str:
        pid = self._active_persona()
        p = self._profile(pid)
        if not p.get("nickname") or not p.get("avatar_path"):
            return f"当前人格 {pid} 资料不完整，无法核对。"
        try:
            nick, uin, _ = await self._qq_profile_status()
        except Exception as e:
            return f"读取 QQ 资料失败：{e}"
        if not nick:
            return "SnowLuma 没有返回当前昵称，无法核对。"
        nick_ok = nick == p.get("nickname")
        avatar_ok = await self._avatar_matches(p.get("avatar_path", ""), uin)
        lines = [
            f"当前人格：{pid}",
            f"QQ 昵称：{nick} | 人格昵称：{p.get('nickname')} | {'一致' if nick_ok else '不一致'}",
            f"QQ 头像：{'一致' if avatar_ok else ('不一致' if avatar_ok is False else '无法确认')}",
        ]
        if fix and (not nick_ok or avatar_ok is False):
            try:
                await self._apply_profile(pid)
                lines.append("已按当前人格资料重新同步昵称/头像。")
            except Exception as e:
                lines.append(f"重新同步失败：{e}")
        return "\n".join(lines)

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
            logger.info("[assistant] plugin update reconcile: %s", report)
        except Exception as e:
            logger.warning("[assistant] plugin update reconcile failed: %s", e)

    def _default_profile_ready(self) -> bool:
        p = self._profile("default")
        return bool(p.get("nickname") and p.get("avatar_path"))

    def _base_prompt(self, persona_id: str) -> str:
        p = self._profile(persona_id)
        base = p.get("prompt_base") or p.get("prompt") or ""
        if not base:
            for persona in self.context.persona_manager.personas:
                if persona.persona_id == persona_id:
                    base = persona.system_prompt or ""
                    break
        if AUTO_PROMPT_HEADER in base:
            base = base.split(AUTO_PROMPT_HEADER, 1)[0]
        return base.rstrip()

    def _knowledge_texts(self, persona_id: str) -> list[str]:
        items = self._load_memory(persona_id)
        out = []
        for m in items:
            if m.get("kind") == "knowledge" and m.get("text"):
                out.append(str(m["text"])[:500])
        return out[-30:]

    async def _iterate_persona_prompt(self, persona_id: str) -> str:
        """Rebuild only this persona's prompt from its private knowledge.

        Shared wiki is intentionally not used; this is persona-exclusive memory.
        """
        base = self._base_prompt(persona_id)
        if not base:
            return "no_base"
        knowledge = self._knowledge_texts(persona_id)
        if not knowledge:
            return "no_knowledge"
        section = AUTO_PROMPT_HEADER + "\n" + "\n".join(f"- {k}" for k in knowledge)
        new_prompt = base + section
        p = self._profile(persona_id)
        if p.get("prompt") == new_prompt:
            return "unchanged"
        await self._upsert_native_persona(persona_id, new_prompt)
        p["prompt_base"] = base
        p["prompt"] = new_prompt
        p["prompt_version"] = int(p.get("prompt_version", 0)) + 1
        p["prompt_updated_at"] = int(time.time())
        self._save_profiles()
        return f"updated:{len(knowledge)}"

    async def _rewrite_persona_prompt_llm(self, persona_id: str, umo: str | None) -> str:
        """Use the active chat model to rewrite this persona's prompt.

        Only base prompt + this persona's private knowledge memories are sent.
        Shared wiki is deliberately excluded.
        """
        base = self._base_prompt(persona_id)
        if not base:
            return "no_base"
        knowledge = self._knowledge_texts(persona_id)
        if not knowledge:
            return "no_knowledge"
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo or "")
            if not provider_id:
                return "no_provider"
            prompt = (
                "你是人格提示词编辑器。请把下面的人格基础设定，与该人格的专属知识记忆融合，"
                "重写成一份更完整、自然、可直接使用的人格系统提示词。\n"
                "要求：\n"
                "1. 保留原人格的核心身份、语气和边界；\n"
                "2. 把专属知识自然融入设定，不要逐条罗列记忆；\n"
                "3. 不要添加与角色无关的通用助手腔；\n"
                "4. 不要提及“记忆文件”“知识库”“自动迭代”等实现细节；\n"
                "5. 只输出重写后的提示词正文，不要解释，不要代码块。\n\n"
                f"【原人格设定】\n{base}\n\n"
                "【该人格专属知识】\n" + "\n".join(f"- {k}" for k in knowledge)
            )
            resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            text = (resp.completion_text or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text).strip()
            if not text:
                return "empty"
            text = text[:12000]
            await self._upsert_native_persona(persona_id, text)
            p = self._profile(persona_id)
            p["prompt_base"] = text
            p["prompt"] = text
            p["prompt_version"] = int(p.get("prompt_version", 0)) + 1
            p["prompt_rewrite_mode"] = "llm"
            p["prompt_updated_at"] = int(time.time())
            self._save_profiles()
            return f"rewritten:{len(text)}"
        except Exception as e:
            logger.warning("[assistant] LLM prompt rewrite failed: %s", e)
            return f"error:{e}"

    async def _use_persona(self, persona_id: str, umo: str | None = None, group_id: str = "") -> str:
        if persona_id not in self._persona_ids():
            raise RuntimeError(f"人格 {persona_id} 不存在")
        if persona_id != "default" and not self._default_profile_ready():
            raise RuntimeError(
                "默认人格资料不完整，先设置 /persona create default|昵称|提示词 "
                "和 /persona avatar default，否则退出时无法恢复"
            )
        p = self._profile(persona_id)
        if not p.get("nickname") or not p.get("avatar_path"):
            raise RuntimeError(
                f"人格 {persona_id} 资料不完整：请先设置昵称并上传头像"
            )
        async with self._switch_lock:
            source = self._active_persona()
            if umo and source != persona_id:
                await self._snapshot_current_conversation(umo, source)
            if source != persona_id:
                if self.auto_llm_rewrite:
                    r = await self._rewrite_persona_prompt_llm(source, umo)
                    if not r.startswith("rewritten"):
                        await self._iterate_persona_prompt(source)
                else:
                    await self._iterate_persona_prompt(source)
            if self.auto_llm_rewrite:
                r = await self._rewrite_persona_prompt_llm(persona_id, umo)
                if not r.startswith("rewritten"):
                    await self._iterate_persona_prompt(persona_id)
            else:
                await self._iterate_persona_prompt(persona_id)
            await self._apply_profile(persona_id, group_id)
            await self._set_default_native_persona(persona_id)
            await self._switch_all_conversations(persona_id, umo)
            p["last_used_at"] = int(time.time())
            self._save_profiles()
        return f"已切换人格：{persona_id}（昵称/头像已同步，已继承该人格上一轮对话记忆）"

    # ---------- image ----------
    async def _download_image(self, src: str) -> tuple[bytes, str]:
        if src.startswith("file://"):
            p = Path(src[7:])
            data = p.read_bytes()
            return data, self._guess_mime(data)
        if src.startswith("base64://"):
            data = base64.b64decode(src[9:])
            return data, self._guess_mime(data)
        if not src.startswith(("http://", "https://")):
            p = Path(src)
            if p.is_file():
                data = p.read_bytes()
                return data, self._guess_mime(data)
        async with aiohttp.ClientSession() as session:
            async with session.get(src, timeout=30) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.read()
                mime = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                return data, mime or self._guess_mime(data)

    @staticmethod
    def _guess_mime(data: bytes) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return "application/octet-stream"

    def _first_image(self, event: AstrMessageEvent) -> str:
        for seg in event.get_messages():
            if isinstance(seg, Image):
                return getattr(seg, "url", "") or getattr(seg, "file", "")
        return ""

    async def _save_avatar(self, persona_id: str, url: str) -> str:
        data, mime = await self._download_image(url)
        if mime not in ALLOWED_MIME:
            raise RuntimeError(f"不支持的图片类型：{mime}")
        if len(data) > self.max_avatar_mb * 1024 * 1024:
            raise RuntimeError("头像超过大小限制")
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
        target = self.profile_dir / "avatars" / f"{persona_id}.{ext}"
        target.write_bytes(data)
        p = self._profile(persona_id)
        p["avatar_path"] = str(target)
        p["avatar_sha256"] = hashlib.sha256(data).hexdigest()
        p["avatar_saved_at"] = int(time.time())
        p["updated_at"] = int(time.time())
        self._save_profiles()
        return str(target)

    # ---------- shared wiki ----------
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
            logger.warning("[assistant] AstrBot KB add failed: %s", e)
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

    # ---------- memory ----------
    def _memory_path(self, persona_id: str) -> Path:
        return self.memory_dir / f"{persona_id}.json"

    def _load_memory(self, persona_id: str) -> list[dict]:
        data = self._load_json(self._memory_path(persona_id), [])
        return data if isinstance(data, list) else []

    def _save_memory(self, persona_id: str, items: list[dict]) -> None:
        self._save_json(self._memory_path(persona_id), items[-500:])

    def _active_persona(self) -> str:
        return getattr(self.context.persona_manager, "default_persona", None) or "default"

    def _memory_add(self, text: str, kind: str = "episodic") -> str:
        pid = self._active_persona()
        text = text.strip()
        if not text:
            raise RuntimeError("记忆内容为空")
        items = self._load_memory(pid)
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text[:2000],
            "kind": kind if kind in {"episodic", "knowledge"} else "episodic",
            "created_at": int(time.time()),
        }
        items.append(item)
        self._save_memory(pid, items)
        label = "知识型记忆" if item["kind"] == "knowledge" else "经历/偏好记忆"
        return f"已写入人格 {pid} 的{label}：{text[:80]}"

    def _memory_list(self, persona_id: str | None = None) -> str:
        pid = persona_id or self._active_persona()
        items = self._load_memory(pid)
        if not items:
            return f"人格 {pid} 还没有记忆。"
        lines = [
            f"{i + 1}. [{'知识' if m.get('kind') == 'knowledge' else '经历'}] {m['text']}"
            for i, m in enumerate(items[-20:])
        ]
        return f"人格 {pid} 的记忆：\n" + "\n".join(lines)

    def _memory_forget(self, keyword: str) -> str:
        pid = self._active_persona()
        items = self._load_memory(pid)
        kept = [m for m in items if keyword not in m.get("text", "")]
        removed = len(items) - len(kept)
        self._save_memory(pid, kept)
        return f"人格 {pid} 删除 {removed} 条匹配记忆。"

    def _memory_clear(self) -> str:
        pid = self._active_persona()
        self._save_memory(pid, [])
        return f"已清空人格 {pid} 的记忆。"

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
            logger.warning("[assistant] AstrBot KB retrieve failed: %s", e)
            return ""
        if not result:
            return ""
        return str(result.get("context_text") or "")

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req):
        query = event.message_str or ""
        is_admin = not (hasattr(event, "is_admin") and not event.is_admin())
        # Shared wiki is visible to owner and guests; persona tone still comes from the active persona.
        wiki = [] if (not is_admin and self.guest_wiki_read in {"off", "none"}) else self._wiki_search(query, limit=5)
        if wiki:
            block = "【共享知识库（所有人格共用；请用当前人格的口吻表达，不要逐条复述）】\n" + "\n".join(
                f"- {x.get('title', '')}: {x.get('text', '')}" for x in wiki
            )
            try:
                req.extra_user_content_parts.append(TextPart(text=block))
            except Exception:
                pass
        kb_ctx = "" if (not is_admin and self.guest_wiki_read in {"off", "none"}) else await self._astrbot_kb_context(query)
        if kb_ctx:
            try:
                req.extra_user_content_parts.append(TextPart(text=kb_ctx))
            except Exception:
                pass

        # Private persona memory is owner-only.
        if hasattr(event, "is_admin") and not event.is_admin():
            return
        pid = self._active_persona()
        items = self._load_memory(pid)
        if not items:
            return
        # Latest conversation snapshot for this persona: always carried across
        # switches, even when the keyword score is zero.
        current_conv = self.context.conversation_manager.session_conversations.get(event.unified_msg_origin, "")
        sessions = [
            m for m in items
            if m.get("kind") == "session" and m.get("conv_id") != current_conv
        ]
        if sessions:
            latest = max(sessions, key=lambda m: int(m.get("created_at", 0)))
            try:
                req.extra_user_content_parts.append(TextPart(
                    text="【该人格上一轮对话记忆（仅作参考，不要原样复述）】\n"
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
            req.extra_user_content_parts.append(TextPart(text=block))
        except Exception:
            pass

    # ---------- persona commands ----------
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
            try:
                msg = await self._approve_review(pid)
            except Exception as e:
                yield event.plain_result(f"批准失败：{e}")
                return
            yield event.plain_result(msg)
            return
        if action == "reject":
            path.unlink(missing_ok=True)
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

    # ---------- memory commands ----------
    @filter.command_group("memory")
    def memory(self):
        """人格记忆：add/list/forget/clear。"""
        pass

    @memory.command("add")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_add(self, event: AstrMessageEvent, text: str = ""):
        text = text or self._raw_after(event, "add", "memory")
        try:
            yield event.plain_result(self._memory_add(text))
        except Exception as e:
            yield event.plain_result(str(e))

    @memory.command("know")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_know(self, event: AstrMessageEvent, text: str = ""):
        """写入知识型记忆（可后续同步到共享 wiki）。"""
        text = text or self._raw_after(event, "know", "memory")
        try:
            yield event.plain_result(self._memory_add(text, kind="knowledge"))
        except Exception as e:
            yield event.plain_result(str(e))

    @memory.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def memory_list(self, event: AstrMessageEvent):
        yield event.plain_result(self._memory_list())

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

    # ---------- wiki commands ----------
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

    # ---------- group chat gate ----------
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

    # ---------- command follow-up input ----------
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

    # ---------- natural language (disabled) ----------
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def on_owner_natural(self, event: AstrMessageEvent):
        # Command-first mode: free-text intent parsing is intentionally disabled.
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
                logger.info("[assistant] one-shot persona create: %s", persona_id)
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
            logger.info("[assistant] natural create intent from %s: %s", user, text[:80])
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
            logger.warning("[assistant] image search failed: %s", e)
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
            logger.warning("[assistant] candidate download failed: %s", e)
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

    # ---------- life card / prompt card helpers ----------
    def _life_list_text(self) -> str:
        files = sorted(self.backup_dir.glob("life-card-*.pialife"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return "还没有数字生命卡备份。"
        return "数字生命卡：\n" + "\n".join(
            f"{i + 1}. {p.name} ({p.stat().st_size // 1024}KB)" for i, p in enumerate(files[:20])
        )

    def _life_status_text(self) -> str:
        results = sorted(self.result_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not results:
            return "还没有数字生命卡任务。"
        lines = []
        for p in results[:5]:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                lines.append(f"{r.get('action')} {r.get('status')}: {r.get('message', '')}")
            except Exception:
                continue
        return "最近生命卡任务：\n" + "\n".join(lines)

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

    def _write_life_request(self, action: str, **extra) -> str:
        rid = uuid.uuid4().hex
        payload = {"id": rid, "action": action, "created_at": int(time.time()), **extra}
        tmp = self.request_dir / f".{rid}.tmp"
        dst = self.request_dir / f"{rid}.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(dst)
        return rid

    @filter.command_group("life")
    def life(self):
        """数字生命卡：备份/恢复/状态。"""
        pass

    @life.command("status")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def life_status(self, event: AstrMessageEvent):
        results = sorted(self.result_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not results:
            yield event.plain_result("还没有数字生命卡任务。")
            return
        lines = []
        for p in results[:5]:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                lines.append(f"{r.get('action')} {r.get('status')}: {r.get('message', '')}")
            except Exception:
                continue
        yield event.plain_result("最近任务：\n" + "\n".join(lines))

    @life.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def life_list(self, event: AstrMessageEvent):
        files = sorted(self.backup_dir.glob("life-card-*.pialife"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            yield event.plain_result("还没有备份。")
            return
        yield event.plain_result("生命卡：\n" + "\n".join(f"{p.name} ({p.stat().st_size // 1024}KB)" for p in files[:20]))

    @life.command("backup")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def life_backup(self, event: AstrMessageEvent, scope: str = "all", mode: str = ""):
        if scope not in {"all", "agent", "qq"}:
            yield event.plain_result("scope 只能是 all / agent / qq")
            return
        rid = self._write_life_request("backup", scope=scope, hot=(mode == "hot"), requested_by=event.get_sender_id())
        yield event.plain_result(
            f"已提交{'热' if mode == 'hot' else '冷'}备份请求 {rid[:8]}（{scope}）。\n机器人可能短暂离线，完成后 /life status 查看。"
        )

    @life.command("restore")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def life_restore(self, event: AstrMessageEvent, archive: str = "", confirm: str = ""):
        if not self.allow_restore:
            yield event.plain_result("插件未允许从 QQ 发起恢复，请用宿主 life-card-host。")
            return
        if not archive.startswith("life-card-") or not archive.endswith(".pialife") or confirm != "confirm":
            yield event.plain_result("用法：/life restore life-card-xxx.pialife confirm")
            return
        target = self.backup_dir / archive
        if not target.is_file():
            yield event.plain_result("找不到这个生命卡。")
            return
        rid = self._write_life_request("restore", archive=str(target), scope="all", requested_by=event.get_sender_id())
        yield event.plain_result(f"已提交恢复请求 {rid[:8]}。机器人会离线，恢复后重启。")

    async def terminate(self):
        logger.info("[assistant] terminated")

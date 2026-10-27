"""插件核心：初始化、数据目录、人格版本、切换、Wiki、提示词与审核。"""

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



class CoreMixin:
    """插件核心：初始化、数据目录、人格版本、切换、Wiki、提示词与审核。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        cfg = config or {}
        self.config = config
        self._data_root = astrbot_root_path()
        self._migrated_paths: list[str] = []
        self.profile_dir = Path(resolve_plugin_path(
            cfg.get("profile_dir", ""), "persona_profiles", self._data_root, LEGACY_DEFAULT_VALUES))
        self.memory_dir = Path(resolve_plugin_path(
            cfg.get("memory_dir", ""), "persona_memory", self._data_root, LEGACY_DEFAULT_VALUES))
        self.wiki_file = Path(resolve_plugin_path(
            cfg.get("wiki_file", ""), "shared_wiki.json", self._data_root, LEGACY_DEFAULT_VALUES))
        self.backup_dir = Path(resolve_plugin_path(
            cfg.get("backup_dir", ""), "life_card/backups", self._data_root, LEGACY_DEFAULT_VALUES))
        self.request_dir = Path(resolve_plugin_path(
            cfg.get("request_dir", ""), "life_card/requests", self._data_root, LEGACY_DEFAULT_VALUES))
        self.result_dir = Path(resolve_plugin_path(
            cfg.get("result_dir", ""), "life_card/results", self._data_root, LEGACY_DEFAULT_VALUES))
        self._migrate_legacy("profile_dir", self.profile_dir)
        self._rewrite_profile_avatar_paths(Path(LEGACY_DEFAULTS["profile_dir"]), self.profile_dir)
        self._migrate_legacy("memory_dir", self.memory_dir)
        self._migrate_legacy("wiki_file", self.wiki_file)
        self._migrate_legacy("backup_dir", self.backup_dir)
        self._migrate_legacy("request_dir", self.request_dir)
        self._migrate_legacy("result_dir", self.result_dir)
        self.astrbot_kb_name = str(cfg.get("astrbot_kb_name", "")).strip()
        self._switch_lock = asyncio.Lock()
        self._card_lock = asyncio.Lock()
        self._restore_lock = asyncio.Lock()
        self.allow_restore = bool(cfg.get("allow_restore", False))
        self.snowluma_http = str(cfg.get("snowluma_http", "")).rstrip("/")
        self.snowluma_token = str(cfg.get("snowluma_token", "")).strip()
        self.max_avatar_mb = int(cfg.get("max_avatar_mb", 5))
        self.max_card_mb = int(cfg.get("max_card_mb", 200))
        self.auto_llm_rewrite = bool(cfg.get("auto_llm_rewrite", False))
        self.image_search_enabled = bool(cfg.get("image_search_enabled", True))
        self.image_search_engine = str(cfg.get("image_search_engine", "baidu"))
        self.image_search_count = int(cfg.get("image_search_count", 5))
        self.version_retention = int(cfg.get("version_retention", 20))
        self.memory_scope = str(cfg.get("memory_scope", "chat"))
        self.memory_chat_isolation = bool(cfg.get("memory_chat_isolation", True))
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
        self.dev_dir = self.profile_dir / "dev"
        for d in (self.profile_dir, self.memory_dir, self.backup_dir, self.request_dir, self.result_dir,
                  self.versions_dir, self.reviews_dir, self.exports_dir, self.dev_dir):
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
        if self._migrated_paths:
            merged = sorted(set(self.state.get("migrated_paths", []) or []) | set(self._migrated_paths))
            self.state["migrated_paths"] = merged
            self._save_state()
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
        logger.info("[persona_forge] profile=%s memory=%s wiki=%s", self.profile_dir, self.memory_dir, self.wiki_file)

    def _migrate_legacy(self, key: str, target: Path) -> None:
        legacy = Path(LEGACY_DEFAULTS[key])
        if not should_migrate_path(str(target), str(legacy), target.exists(), legacy.exists()):
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(target))
            self._migrated_paths.append(key)
            logger.info("[persona_forge] migrated legacy %s -> %s", legacy, target)
        except Exception as e:
            logger.warning("[persona_forge] migrate %s failed: %s", key, e)

    def _rewrite_profile_avatar_paths(self, old_root: Path, new_root: Path) -> None:
        path = new_root / "profiles.json"
        data = self._load_json(path, {})
        if not isinstance(data, dict) or not data:
            return
        changed = False
        for prof in data.values():
            if not isinstance(prof, dict):
                continue
            before = prof.get("avatar_path")
            after = rewrite_avatar_path(before, old_root, new_root)
            if after != before:
                prof["avatar_path"] = after
                changed = True
        if changed:
            self._save_json(path, data)
            logger.info("[persona_forge] rewrote avatar paths under %s", new_root)

    def _review_history_path(self) -> Path:
        return self.reviews_dir / "history.jsonl"

    def _record_review(self, pid: str, action: str, source: str = "") -> None:
        try:
            entry = {
                "persona_id": pid,
                "action": action,
                "source": source,
                "created_at": int(time.time()),
            }
            with self._review_history_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("[persona_forge] review history failed: %s", e)

    def _load_review_history(self, limit: int = 100) -> list[dict]:
        path = self._review_history_path()
        if not path.is_file():
            return []
        out = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            return []
        return out[-limit:][::-1]

    @staticmethod
    def _load_json(path: Path, default):
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error("[persona_forge] invalid json %s: %s", path, e)
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

    def _make_temp_dir(self, prefix: str) -> Path:
        """Create a short-lived working directory inside plugin data.

        Avoid ``tempfile.mkdtemp``: some Windows sandboxes make freshly
        created temp directories unreadable to the process that created them.
        """
        target = self.profile_dir / f".{prefix}{uuid.uuid4().hex[:12]}"
        target.mkdir(parents=True, exist_ok=False)
        return target

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
            logger.warning("[persona_forge] persist default persona failed: %s", e)
        self.context.persona_manager.default_persona = persona_id
        await self.context.persona_manager.initialize()

    async def _capture_conversation(
        self, persona_id: str, umo: str | None = None, limit: int = 20
    ) -> dict:
        """Write the latest messages of a conversation into persona memory.

        Chat history is **not** synced automatically: this runs only from a
        persona switch or an explicit capture (``/memory snapshot`` / WebUI).
        Re-capturing the same conversation updates its existing snapshot
        instead of appending duplicates.
        """
        conversations = list(await self._db().get_conversations())
        if not conversations:
            raise RuntimeError("还没有任何会话记录")
        conv = None
        if umo:
            same_umo = [c for c in conversations if c.user_id == umo]
            if not same_umo:
                raise RuntimeError(f"没有找到会话 {umo}")
            conv_id = self.context.conversation_manager.session_conversations.get(umo)
            if conv_id:
                conv = next((c for c in same_umo if c.conversation_id == conv_id), None)
            if conv is None:
                conv = max(same_umo, key=lambda c: int(c.inner_conversation_id or 0))
        else:
            candidates = [c for c in conversations if c.persona_id == persona_id]
            if not candidates:
                candidates = [c for c in conversations if (c.title or "") == f"persona:{persona_id}"]
            if not candidates:
                candidates = conversations
            conv = max(candidates, key=lambda c: int(c.inner_conversation_id or 0))
            umo = conv.user_id
        content = conv.content or []
        lines = []
        for message in content[-max(1, int(limit)):]:
            role = message.get("role", "?")
            text = message.get("content", "")
            if isinstance(text, list):
                text = " ".join(str(x.get("text", "")) for x in text if isinstance(x, dict))
            if text:
                lines.append(f"{'用户' if role == 'user' else '助手'}: {str(text)[:500]}")
        if not lines:
            raise RuntimeError("这个会话还没有可抓取的消息")
        items = self._load_memory(persona_id)
        existing = None
        for item in reversed(items):
            if item.get("kind") == "session" and item.get("conv_id") == conv.conversation_id:
                existing = item
                break
        if existing is not None:
            existing["text"] = "\n".join(lines)
            existing["created_at"] = int(time.time())
            existing["umo"] = umo or ""
            existing["scope"] = "chat"
        else:
            items.append({
                "id": uuid.uuid4().hex[:12],
                "text": "\n".join(lines),
                "kind": "session",
                "scope": "chat",
                "umo": umo or "",
                "conv_id": conv.conversation_id,
                "created_at": int(time.time()),
            })
        self._save_memory(persona_id, items)
        return {
            "persona_id": persona_id,
            "umo": umo or "",
            "conv_id": conv.conversation_id,
            "messages": len(lines),
        }

    async def _snapshot_current_conversation(self, umo: str, persona_id: str) -> None:
        """Snapshot the current conversation when switching personas."""
        try:
            await self._capture_conversation(persona_id, umo=umo, limit=20)
        except Exception as e:
            logger.warning("[persona_forge] snapshot conversation failed: %s", e)

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
            logger.warning("[persona_forge] list conversations failed: %s", e)
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
                logger.warning("[persona_forge] new conversation failed for %s: %s", umo, e)

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
        # Record when the local persona profile was last pushed to QQ.
        p["qq_synced_at"] = int(time.time())
        p["qq_sync_mode"] = mode
        self._save_profiles()

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
        p["qq_checked_at"] = int(time.time())
        p["qq_remote_nickname"] = nick
        self._save_profiles()
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
            logger.warning("[persona_forge] LLM prompt rewrite failed: %s", e)
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
            try:
                await self._apply_profile(persona_id, group_id)
                synced = True
            except Exception as e:
                # SnowLuma is optional; a failed profile sync must not block
                # switching (especially from the WebUI, which has no chat umo).
                synced = False
                logger.warning("[persona_forge] profile sync skipped: %s", e)
            await self._set_default_native_persona(persona_id)
            await self._switch_all_conversations(persona_id, umo)
            p["last_used_at"] = int(time.time())
            self._save_profiles()
        suffix = "昵称/头像已同步" if synced else "未配置 SnowLuma，已跳过 QQ 资料同步"
        return f"已切换人格：{persona_id}（{suffix}，已继承该人格上一轮对话记忆）"

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

    def _active_persona(self) -> str:
        return getattr(self.context.persona_manager, "default_persona", None) or "default"

    def _read_card_manifest(self, path: Path) -> dict | None:
        try:
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read("manifest.json").decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

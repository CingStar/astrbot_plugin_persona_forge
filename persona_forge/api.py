"""插件视图 Web API（bridge 对接）。"""

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


from astrbot.api.web import error_response, json_response, request

try:  # AstrBot >= 4.13 exposes the upload wrapper; older builds keep a clear error.
    from astrbot.api.web import PluginUploadFile
except Exception:  # pragma: no cover - only for older AstrBot builds
    PluginUploadFile = None  # type: ignore[assignment]


class ApiMixin:
    """插件视图 Web API（bridge 对接）。"""

    def _register_web_apis(self) -> None:
        prefix = "/astrbot_plugin_persona_forge"
        routes = [
            (f"{prefix}/personas", self.api_personas, ["GET"], "List personas"),
            (f"{prefix}/persona", self.api_persona_detail, ["GET"], "Persona detail"),
            (f"{prefix}/persona/update", self.api_persona_update, ["POST"], "Update persona"),
            (f"{prefix}/persona/avatar/upload/<pid>", self.api_avatar_upload, ["POST"], "Upload avatar"),
            (f"{prefix}/persona/avatar/url", self.api_avatar_url, ["POST"], "Avatar from URL"),
            (f"{prefix}/persona/avatar/remove", self.api_avatar_remove, ["POST"], "Remove avatar"),
            (f"{prefix}/persona/sync", self.api_persona_sync, ["POST"], "Push profile to QQ"),
            (f"{prefix}/persona/switch", self.api_switch, ["POST"], "Switch persona"),
            (f"{prefix}/persona/versions", self.api_versions, ["GET"], "Persona versions"),
            (f"{prefix}/persona/version", self.api_persona_version, ["GET"], "Persona version detail"),
            (f"{prefix}/persona/version/rollback", self.api_persona_rollback, ["POST"], "Rollback version"),
            (f"{prefix}/persona/iterate", self.api_persona_iterate, ["POST"], "Iterate prompt"),
            (f"{prefix}/persona/dev", self.api_dev, ["GET", "POST"], "Developer mode"),
            (f"{prefix}/persona/dev/notes", self.api_dev_notes, ["GET", "POST"], "Dev notes"),
            (f"{prefix}/persona/dev/compact", self.api_dev_compact, ["POST"], "Compact dev notes"),
            (f"{prefix}/persona/dev/clear", self.api_dev_clear, ["POST"], "Clear dev notes"),
            (f"{prefix}/memory", self.api_memory, ["GET", "POST"], "Persona memory"),
            (f"{prefix}/memory/capture", self.api_memory_capture, ["POST"], "Capture conversation into memory"),
            (f"{prefix}/reviews", self.api_reviews, ["GET"], "Reviews"),
            (f"{prefix}/review", self.api_review_detail, ["GET"], "Review detail"),
            (f"{prefix}/reviews/action", self.api_review_action, ["POST"], "Approve/reject review"),
            (f"{prefix}/settings", self.api_settings, ["GET", "POST"], "Settings"),
            (f"{prefix}/status", self.api_status, ["GET"], "Plugin status"),
            (f"{prefix}/life", self.api_life, ["GET"], "Life cards"),
            (f"{prefix}/life/backup", self.api_life_backup, ["POST"], "Create life cards"),
            (f"{prefix}/life/restore", self.api_life_restore, ["POST"], "Restore life card"),
            # Legacy endpoints kept for older dashboards.
            (f"{prefix}/versions", self.api_versions, ["GET"], "Persona versions (legacy)"),
            (f"{prefix}/switch", self.api_switch, ["POST"], "Switch persona (legacy)"),
            (f"{prefix}/group-mode", self.api_group_mode, ["GET", "POST"], "Group mode (legacy)"),
            (f"{prefix}/life-card", self.api_life_card, ["GET"], "Life card status (legacy)"),
        ]
        for route, handler, methods, desc in routes:
            try:
                self.context.register_web_api(route, handler, methods, desc)
            except Exception as e:
                logger.warning("[persona_forge] register_web_api %s failed: %s", route, e)

    def _avatar_payload(self, pid: str) -> dict:
        prof = self.profiles.get(pid) or {}
        sha = str(prof.get("avatar_sha256") or "")
        path = str(prof.get("avatar_path") or "")
        if not path or not Path(path).is_file():
            return {"avatar_data": "", "avatar_sha": sha}
        try:
            raw = Path(path).read_bytes()
        except Exception:
            return {"avatar_data": "", "avatar_sha": sha}
        if len(raw) > MAX_INLINE_AVATAR_BYTES:
            return {"avatar_data": "", "avatar_sha": sha, "avatar_too_large": True}
        mime = self._guess_mime(raw)
        if mime not in ALLOWED_MIME:
            mime = "image/png"
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        return {"avatar_data": data_url, "avatar_sha": sha}

    def _persona_summary(self, persona_id: str, active: str) -> dict:
        prof = self.profiles.get(persona_id) or {}
        payload = self._avatar_payload(persona_id)
        return {
            "id": persona_id,
            "nickname": prof.get("nickname", ""),
            "avatar": bool(prof.get("avatar_path")),
            **payload,
            "ready": bool(prof.get("nickname") and prof.get("avatar_path")),
            "active": persona_id == active,
            "default": persona_id == "default",
            "version": int(prof.get("prompt_version", 0) or 0),
            "updated_at": int(prof.get("updated_at", 0) or 0),
            "memory_count": len(self._load_memory(persona_id)),
        }

    @staticmethod
    def _parse_list_field(value):
        if value is None:
            return None
        if isinstance(value, list):
            return [str(x) for x in value if str(x).strip()]
        text = str(value).strip()
        if text in {"", "all", "全部"}:
            return None
        if text in {"none", "无"}:
            return []
        return [x.strip() for x in text.split(",") if x.strip()]

    def _paths_payload(self) -> dict:
        return {
            "profile_dir": str(self.profile_dir),
            "memory_dir": str(self.memory_dir),
            "wiki_file": str(self.wiki_file),
            "backup_dir": str(self.backup_dir),
            "request_dir": str(self.request_dir),
            "result_dir": str(self.result_dir),
        }

    def _qq_payload(self, pid: str) -> dict:
        prof = self.profiles.get(pid) or {}
        return {
            "configured": bool(self.snowluma_http),
            "mode": self.nickname_sync_mode,
            "synced_at": int(prof.get("qq_synced_at") or 0),
            "checked_at": int(prof.get("qq_checked_at") or 0),
            "avatar_saved_at": int(prof.get("avatar_saved_at") or 0),
            "remote_nickname": str(prof.get("qq_remote_nickname") or ""),
        }

    def _status_counts(self) -> dict:
        return {
            "personas": len(self._persona_ids()),
            "memory": len(self._load_memory(self._active_persona())),
            "wiki": len(self._wiki_load()),
            "reviews": len(self._list_reviews()),
            "life_cards": len(self._life_cards()),
        }

    def _apply_config_value(self, key: str, value) -> bool:
        if key == "auto_llm_rewrite":
            self.auto_llm_rewrite = bool(value)
        elif key == "version_retention":
            self.version_retention = max(1, int(value))
        elif key == "image_search_enabled":
            self.image_search_enabled = bool(value)
        elif key == "image_search_engine":
            self.image_search_engine = str(value) if str(value) in {"baidu", "bing"} else self.image_search_engine
        elif key == "image_search_count":
            self.image_search_count = max(1, min(20, int(value)))
        elif key == "memory_scope":
            self.memory_scope = str(value) if str(value) in {"chat", "persona"} else self.memory_scope
        elif key == "memory_chat_isolation":
            self.memory_chat_isolation = bool(value)
        elif key == "snowluma_http":
            self.snowluma_http = str(value or "").rstrip("/")
        elif key == "snowluma_token":
            self.snowluma_token = str(value or "").strip()
        elif key == "nickname_sync_mode":
            self.nickname_sync_mode = str(value) if str(value) in {"profile", "group_card", "hybrid"} else self.nickname_sync_mode
        elif key == "max_avatar_mb":
            self.max_avatar_mb = max(1, min(50, int(value)))
        elif key == "astrbot_kb_name":
            self.astrbot_kb_name = str(value or "").strip()
        elif key == "allow_restore":
            self.allow_restore = bool(value)
        elif key == "max_card_mb":
            self.max_card_mb = max(1, min(4096, int(value)))
        else:
            return False
        return True

    @staticmethod
    def _iterate_message(pid: str, result: str) -> str:
        if result == "no_knowledge":
            return f"人格 {pid} 还没有知识型记忆，未迭代。"
        if result == "no_base":
            return f"人格 {pid} 没有基础提示词，未迭代。"
        if result == "unchanged":
            return f"人格 {pid} 提示词已是最新。"
        if result == "no_provider":
            return "没有可用的聊天模型 Provider。"
        if result == "empty":
            return "模型没有返回有效提示词。"
        if result.startswith("error:"):
            return f"重写失败：{result[6:]}"
        return f"人格 {pid} 提示词已更新：{result}"

    async def api_personas(self):
        active = self._active_persona()
        data = [self._persona_summary(p.persona_id, active) for p in self.context.persona_manager.personas]
        return json_response({"active": active, "version": PLUGIN_VERSION, "personas": data})

    async def api_persona_detail(self):
        pid = request.query.get("pid", "") or self._active_persona()
        if pid not in self._persona_ids() and pid not in self.profiles:
            return error_response("人格不存在", 404)
        prof = self.profiles.get(pid) or {}
        try:
            native = await self.context.persona_manager.get_persona(pid)
        except Exception:
            native = None
        dev_pid = self._dev_active_pid()
        return json_response({
            "id": pid,
            "nickname": prof.get("nickname", ""),
            **self._avatar_payload(pid),
            "prompt": (native.system_prompt if native else prof.get("prompt", "")) or "",
            "prompt_base": prof.get("prompt_base", ""),
            "prompt_version": int(prof.get("prompt_version", 0) or 0),
            "updated_at": int(prof.get("updated_at", 0) or 0),
            "tools": getattr(native, "tools", None) if native else None,
            "skills": getattr(native, "skills", None) if native else None,
            "error": (getattr(native, "custom_error_message", None) or "") if native else "",
            "begin_dialogs": (getattr(native, "begin_dialogs", None) or []) if native else [],
            "memory_count": len(self._load_memory(pid)),
            "version_count": len(self._list_versions(pid)),
            "qq": self._qq_payload(pid),
            "dev": {
                "active": dev_pid == pid,
                "persona_id": dev_pid,
                "notes": self._dev_notes(pid),
                "expires_at": int(self._dev_state().get("expires_at") or 0),
                "ttl": DEV_MODE_TTL,
            },
        })

    async def api_persona_update(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        if not pid or (pid not in self._persona_ids() and pid not in self.profiles):
            return error_response("人格不存在", 404)
        try:
            fields = {}
            if payload.get("prompt") is not None:
                fields["system_prompt"] = str(payload.get("prompt") or "")[:20000]
            if "tools" in payload:
                fields["tools"] = self._parse_list_field(payload.get("tools"))
            if "skills" in payload:
                fields["skills"] = self._parse_list_field(payload.get("skills"))
            if "error" in payload:
                fields["custom_error_message"] = str(payload.get("error") or "") or None
            if "dialogs" in payload:
                dialogs = payload.get("dialogs") or []
                if not isinstance(dialogs, list) or len(dialogs) % 2 != 0:
                    return error_response("dialogs 必须是偶数条 user/assistant 列表", 400)
                fields["begin_dialogs"] = dialogs
            await self._snapshot_version(pid, reason="web_update")
            if fields:
                await self._update_native_fields(pid, **fields)
            prof = self._profile(pid)
            if payload.get("nickname") is not None:
                prof["nickname"] = str(payload.get("nickname") or "")[:40]
            if "system_prompt" in fields:
                prof["prompt"] = fields["system_prompt"]
                prof["prompt_base"] = fields["system_prompt"]
            prof["updated_at"] = int(time.time())
            self._save_profiles()
            return json_response({"message": "已保存"})
        except Exception as e:
            return error_response(str(e), 400)

    async def api_avatar_upload(self, pid: str):
        if not PERSONA_ID_RE.match(pid or ""):
            return error_response("人格 id 非法", 400)
        if PluginUploadFile is None:
            return error_response("当前 AstrBot 版本不支持文件上传", 400)
        files = await request.files()
        upload = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return error_response("缺少文件", 400)
        tmpdir = self._make_temp_dir("pf-up-")
        try:
            tmp = tmpdir / "upload"
            await upload.save(tmp)
            data = tmp.read_bytes()
            mime = self._guess_mime(data)
            if mime not in ALLOWED_MIME:
                return error_response("只支持 PNG / JPEG / WebP", 400)
            if len(data) > self.max_avatar_mb * 1024 * 1024:
                return error_response(f"头像超过 {self.max_avatar_mb}MB", 400)
            await self._snapshot_version(pid, reason="web_avatar")
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
            target = self.profile_dir / "avatars" / f"{pid}.{ext}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            prof = self._profile(pid)
            prof["avatar_path"] = str(target)
            prof["avatar_sha256"] = hashlib.sha256(data).hexdigest()
            prof["avatar_saved_at"] = int(time.time())
            prof["updated_at"] = int(time.time())
            self._save_profiles()
            return json_response({"message": "头像已更新", **self._avatar_payload(pid)})
        except Exception as e:
            return error_response(str(e), 400)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def api_avatar_url(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        if not PERSONA_ID_RE.match(pid):
            return error_response("人格 id 非法", 400)
        url = str(payload.get("url") or "").strip()
        source = str(payload.get("source") or "").strip()
        try:
            if source == "qq":
                _, _, url = await self._qq_profile_status()
            if not url:
                return error_response("缺少图片 URL", 400)
            await self._snapshot_version(pid, reason="web_avatar")
            await self._save_avatar(pid, url)
            return json_response({"message": "头像已更新", **self._avatar_payload(pid)})
        except Exception as e:
            return error_response(str(e), 400)

    async def api_avatar_remove(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        if not PERSONA_ID_RE.match(pid):
            return error_response("人格 id 非法", 400)
        prof = self._profile(pid)
        prof.pop("avatar_path", None)
        prof.pop("avatar_sha256", None)
        prof["updated_at"] = int(time.time())
        self._save_profiles()
        return json_response({"message": "已移除头像"})

    async def api_persona_sync(self):
        """Push the current persona's nickname/avatar to QQ via SnowLuma."""
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        if not pid:
            return error_response("缺少人格 id", 400)
        if not self.snowluma_http:
            return error_response("未配置 SnowLuma，无法同步到 QQ", 400)
        if pid != self._active_persona():
            return error_response("只能把当前人格同步到 QQ，请先切换人格", 400)
        try:
            await self._apply_profile(pid, str(payload.get("group_id") or ""))
        except Exception as e:
            return error_response(str(e), 400)
        return json_response({"message": "已同步到 QQ", "qq": self._qq_payload(pid)})

    async def api_switch(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        try:
            msg = await self._use_persona(pid, None)
            return json_response({"message": msg})
        except Exception as e:
            return error_response(str(e), 400)

    async def api_versions(self):
        pid = request.query.get("pid", "") or self._active_persona()
        items = []
        for n in self._list_versions(pid):
            path = self._version_dir(pid) / f"v{n:04d}.json"
            created_at, reason = 0, ""
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created_at = int(data.get("created_at") or 0)
                reason = str(data.get("reason") or "")
            except Exception:
                pass
            items.append({"n": n, "created_at": created_at, "reason": reason})
        return json_response({"pid": pid, "versions": items})

    async def api_persona_version(self):
        pid = request.query.get("pid", "") or self._active_persona()
        try:
            n = int(request.query.get("n", 0))
        except (TypeError, ValueError):
            return error_response("版本号非法", 400)
        path = self._version_dir(pid) / f"v{n:04d}.json"
        if not path.is_file():
            return error_response("版本不存在", 404)
        data = json.loads(path.read_text(encoding="utf-8"))
        prompt = str(data.get("prompt") or "")
        current = self._base_prompt(pid)
        diff = "\n".join(difflib.unified_diff(
            current.splitlines(), prompt.splitlines(),
            fromfile="current", tofile=f"v{n:04d}", lineterm="",
        ))
        return json_response({
            "pid": pid,
            "n": n,
            "created_at": int(data.get("created_at") or 0),
            "reason": str(data.get("reason") or ""),
            "prompt": prompt,
            "diff": diff[:20000],
        })

    async def api_persona_rollback(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        try:
            n = int(payload.get("n") or 0)
            msg = await self._apply_version(pid, n)
            return json_response({"message": msg})
        except Exception as e:
            return error_response(str(e), 400)

    async def api_persona_iterate(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or self._active_persona()).strip()
        mode = str(payload.get("mode") or "knowledge")
        try:
            if mode == "llm":
                result = await self._rewrite_persona_prompt_llm(pid, None)
            else:
                result = await self._iterate_persona_prompt(pid)
            return json_response({"message": self._iterate_message(pid, result)})
        except Exception as e:
            return error_response(str(e), 400)

    async def api_dev(self):
        if request.method == "GET":
            pid = request.query.get("pid", "") or self._dev_active_pid() or self._active_persona()
            active_pid = self._dev_active_pid()
            return json_response({
                "active_pid": active_pid,
                "persona_id": pid,
                "notes": self._dev_notes(pid),
                "expires_at": int(self._dev_state().get("expires_at") or 0),
                "ttl": DEV_MODE_TTL,
            })
        payload = await request.json(default={})
        enabled = bool(payload.get("enabled"))
        pid = str(payload.get("pid") or "").strip()
        if enabled:
            if not PERSONA_ID_RE.match(pid):
                return error_response("人格 id 非法", 400)
            self._dev_enable(pid)
            return json_response({"message": f"开发者模式已开启：{pid}", "active_pid": pid})
        self._dev_disable()
        return json_response({"message": "开发者模式已关闭", "active_pid": ""})

    async def api_dev_notes(self):
        if request.method == "GET":
            pid = request.query.get("pid", "") or self._dev_active_pid() or self._active_persona()
            return json_response({"persona_id": pid, "notes": self._dev_notes(pid)})
        payload = await request.json(default={})
        pid = str(payload.get("pid") or self._dev_active_pid() or self._active_persona()).strip()
        try:
            count = self._dev_add_note(pid, str(payload.get("text") or ""))
        except Exception as e:
            return error_response(str(e), 400)
        return json_response({"count": count, "message": f"已记录第 {count} 条教学笔记"})

    async def api_dev_compact(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or self._dev_active_pid() or self._active_persona()).strip()
        try:
            result = await self._dev_apply(pid, str(payload.get("umo") or ""))
        except Exception as e:
            return error_response(str(e), 400)
        if result == "no_notes":
            return json_response({"message": f"人格 {pid} 还没有教学笔记", "count": 0})
        if result == "no_base":
            return json_response({"message": f"人格 {pid} 没有基础提示词", "count": 0})
        if result == "no_provider":
            return json_response({"message": "没有可用的聊天模型 Provider", "count": 0})
        if result == "empty":
            return json_response({"message": "模型没有返回有效提示词", "count": 0})
        return json_response({"message": f"已生成人格 {pid} 的教学草稿", "count": len(self._dev_notes(pid))})

    async def api_dev_clear(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or self._dev_active_pid() or self._active_persona()).strip()
        count = self._dev_clear(pid)
        return json_response({"message": f"已清空人格 {pid} 的 {count} 条教学笔记", "count": count})

    async def api_memory(self):
        if request.method == "GET":
            pid = request.query.get("pid", "") or self._active_persona()
            items = []
            for m in self._load_memory(pid):
                items.append({
                    "id": m.get("id", ""),
                    "kind": m.get("kind", "episodic"),
                    "scope": self._memory_scope_of(m),
                    "source": self._memory_source_label(m),
                    "text": m.get("text", ""),
                    "created_at": int(m.get("created_at", 0) or 0),
                })
            return json_response({"pid": pid, "items": items, "active_scope": self.memory_scope})
        payload = await request.json(default={})
        pid = str(payload.get("pid") or self._active_persona()).strip()
        action = str(payload.get("action") or "").strip()
        try:
            if action == "add":
                msg = self._memory_add(str(payload.get("text") or ""), pid=pid)
            elif action == "know":
                msg = self._memory_add(str(payload.get("text") or ""), kind="knowledge", pid=pid)
            elif action == "forget":
                msg = self._memory_forget(str(payload.get("keyword") or ""), pid=pid)
            elif action == "clear":
                msg = self._memory_clear(pid=pid)
            elif action == "promote":
                n = self._promote_memories(pid, str(payload.get("keyword") or ""))
                msg = f"已从人格 {pid} 同步 {n} 条知识型记忆到共享 wiki。"
            elif action == "scope":
                scope = str(payload.get("scope") or "chat")
                if scope not in {"chat", "persona"}:
                    return error_response("scope 只能是 chat / persona", 400)
                msg = self._memory_set_scope(str(payload.get("keyword") or ""), scope, pid=pid)
            else:
                return error_response("未知操作", 400)
        except Exception as e:
            return error_response(str(e), 400)
        return json_response({"message": msg})

    async def api_memory_capture(self):
        """Capture the latest conversation into persona memory on demand."""
        payload = await request.json(default={})
        pid = str(payload.get("pid") or self._active_persona()).strip()
        umo = str(payload.get("umo") or "").strip() or None
        try:
            result = await self._capture_conversation(pid, umo=umo, limit=int(payload.get("limit") or 20))
        except Exception as e:
            return error_response(str(e), 400)
        return json_response({
            "message": f"已抓取最近会话 {result['messages']} 条消息到人格 {pid} 的记忆",
            "capture": result,
        })

    async def api_reviews(self):
        status = request.query.get("status", "pending")
        if status == "history":
            return json_response({"history": self._load_review_history()})
        return json_response({"reviews": self._list_reviews()})

    async def api_review_detail(self):
        pid = request.query.get("pid", "") or ""
        path = self._review_path(pid)
        if not path.is_file():
            return error_response("没有待审草稿", 404)
        data = json.loads(path.read_text(encoding="utf-8"))
        return json_response({
            "persona_id": data.get("persona_id", pid),
            "source": data.get("source", ""),
            "created_at": int(data.get("created_at", 0) or 0),
            "diff": data.get("diff", ""),
            "base_prompt": data.get("base_prompt", ""),
            "draft_prompt": data.get("draft_prompt", ""),
        })

    async def api_review_action(self):
        payload = await request.json(default={})
        pid = str(payload.get("pid") or "").strip()
        action = str(payload.get("action") or "").strip()
        if not pid or action not in {"approve", "reject"}:
            return error_response("pid/action required", 400)
        source = ""
        path = self._review_path(pid)
        if path.is_file():
            try:
                source = str(json.loads(path.read_text(encoding="utf-8")).get("source") or "")
            except Exception:
                source = ""
        if action == "approve":
            try:
                msg = await self._approve_review(pid)
            except Exception as e:
                return error_response(str(e), 400)
            self._record_review(pid, "approve", source)
            return json_response({"message": msg})
        path.unlink(missing_ok=True)
        self._record_review(pid, "reject", source)
        return json_response({"message": f"已拒绝人格 {pid} 的草稿"})

    async def api_settings(self):
        if request.method == "GET":
            config_payload = {
                "auto_llm_rewrite": bool(self.auto_llm_rewrite),
                "version_retention": self.version_retention,
                "image_search_enabled": self.image_search_enabled,
                "image_search_engine": self.image_search_engine,
                "image_search_count": self.image_search_count,
                "memory_scope": self.memory_scope,
                "memory_chat_isolation": self.memory_chat_isolation,
                "snowluma_http": self.snowluma_http,
                "snowluma_token": mask_secret(self.snowluma_token),
                "nickname_sync_mode": self.nickname_sync_mode,
                "max_avatar_mb": self.max_avatar_mb,
                "astrbot_kb_name": self.astrbot_kb_name,
                "allow_restore": self.allow_restore,
                "max_card_mb": self.max_card_mb,
            }
            return json_response({
                "version": PLUGIN_VERSION,
                "config": config_payload,
                "runtime": {
                    "group_mode": self.group_mode,
                    "group_whitelist": sorted(self.group_whitelist),
                    "require_at": self.require_at,
                    "guest_wiki": self.guest_wiki_read,
                    "guest_rate_limit_per_minute": self.guest_rate_limit,
                },
                "paths": self._paths_payload(),
                "counts": self._status_counts(),
                "migrated": list(self.state.get("migrated_paths", []) or []),
                "token_configured": bool(self.snowluma_token),
            })
        payload = await request.json(default={})
        config_payload = payload.get("config") or {}
        runtime_payload = payload.get("runtime") or {}
        if not isinstance(config_payload, dict) or not isinstance(runtime_payload, dict):
            return error_response("config/runtime 必须是对象", 400)
        invalid = []
        saved_config = {}
        for key, value in config_payload.items():
            if key == "snowluma_token" and (not value or value == mask_secret("x")):
                continue
            try:
                if self._apply_config_value(key, value):
                    saved_config[key] = value
            except (TypeError, ValueError):
                invalid.append(key)
        if invalid:
            return error_response(f"配置项类型不正确：{', '.join(sorted(set(invalid)))}", 400)
        if saved_config and self.config is not None:
            try:
                for key, value in saved_config.items():
                    self.config[key] = value
                self.config.save_config()
            except Exception as e:
                logger.warning("[persona_forge] save config failed: %s", e)
        if runtime_payload:
            try:
                if "group_mode" in runtime_payload:
                    self.group_mode = str(runtime_payload.get("group_mode") or self.group_mode)
                if "group_whitelist" in runtime_payload:
                    raw = runtime_payload.get("group_whitelist")
                    if isinstance(raw, str):
                        parts = re.split(r"[,，、;\s]+", raw)
                    else:
                        parts = [str(x) for x in (raw or [])]
                    self.group_whitelist = {p.strip() for p in parts if str(p).strip()}
                if "require_at" in runtime_payload:
                    self.require_at = bool(runtime_payload.get("require_at"))
                if "guest_wiki" in runtime_payload:
                    self.guest_wiki_read = str(runtime_payload.get("guest_wiki") or self.guest_wiki_read)
                if "guest_rate_limit_per_minute" in runtime_payload:
                    self.guest_rate_limit = max(
                        1, min(100, int(runtime_payload.get("guest_rate_limit_per_minute")))
                    )
            except (TypeError, ValueError):
                return error_response("runtime 字段类型不正确", 400)
            self.state["group_mode"] = {
                "group_mode": self.group_mode,
                "group_whitelist": sorted(self.group_whitelist),
                "require_at": self.require_at,
                "guest_wiki": self.guest_wiki_read,
                "guest_rate_limit_per_minute": self.guest_rate_limit,
            }
            self._save_state()
        return json_response({"message": "已保存", "saved": True})

    async def api_status(self):
        dev_pid = self._dev_active_pid()
        handlers = 0
        try:
            from astrbot.core.star.star_handler import star_handlers_registry

            owner = self.__class__.__module__
            handlers = sum(
                1
                for handler in star_handlers_registry
                if getattr(handler, "handler_module_path", "") == owner
            )
        except Exception:
            handlers = 0
        return json_response({
            "version": PLUGIN_VERSION,
            "active": self._active_persona(),
            "paths": self._paths_payload(),
            "counts": self._status_counts(),
            "migrated": list(self.state.get("migrated_paths", []) or []),
            "handlers": handlers,
            "dev": {
                "active_pid": dev_pid,
                "expires_at": int(self._dev_state().get("expires_at") or 0),
                "ttl": DEV_MODE_TTL,
            },
        })

    async def api_life(self):
        cards = self._life_cards()
        by_persona = []
        for pid in self._card_persona_ids():
            persona_cards = [c for c in cards if c.get("type") == "persona" and c.get("persona_id") == pid]
            by_persona.append({
                "id": pid,
                "nickname": (self.profiles.get(pid) or {}).get("nickname", ""),
                "latest": persona_cards[0] if persona_cards else None,
            })
        return json_response({
            "cards": cards,
            "personas": by_persona,
            "tasks": self._life_tasks(),
            "allow_restore": self.allow_restore,
            "max_card_mb": self.max_card_mb,
        })

    async def api_life_backup(self):
        payload = await request.json(default={})
        target = str(payload.get("target") or "plugin")
        try:
            async with self._card_lock:
                if target == "shared":
                    path = await self._create_shared_card()
                    created = [path]
                elif target.startswith("persona:"):
                    pid = target.split(":", 1)[1].strip()
                    if not PERSONA_ID_RE.match(pid):
                        return error_response("人格 id 非法", 400)
                    if pid not in self._card_persona_ids():
                        return error_response("人格不存在", 404)
                    created = [await self._create_persona_card(pid)]
                else:
                    created = [await self._create_persona_card(pid) for pid in self._card_persona_ids()]
                    if self.wiki_file.is_file():
                        created.append(await self._create_shared_card())
            return json_response({
                "message": f"已生成 {len(created)} 张生命卡",
                "cards": [{"name": p.name, "size_kb": p.stat().st_size // 1024} for p in created],
            })
        except Exception as e:
            return error_response(str(e), 400)

    async def api_life_restore(self):
        if not self.allow_restore:
            return error_response("未允许恢复，请先在设置里打开 allow_restore", 400)
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not payload.get("confirm"):
            return error_response("需要 confirm=true", 400)
        if not name.startswith("life-card-") or not name.endswith(".pialife") or Path(name).name != name:
            return error_response("卡片名非法", 400)
        target = self.backup_dir / name
        if not target.is_file():
            return error_response("找不到这个生命卡", 404)
        manifest = await asyncio.to_thread(self._read_card_manifest, target)
        if not manifest or manifest.get("format") not in {CARD_FORMAT_PERSONA, CARD_FORMAT_SHARED}:
            return error_response("宿主卡请在宿主恢复", 400)
        try:
            async with self._restore_lock:
                if manifest.get("format") == CARD_FORMAT_PERSONA:
                    result = await self._restore_persona_card(target)
                    return json_response({
                        "message": f"已恢复人格 {result['persona_id']}：记忆 {result['memory']} 条、版本 {result['versions']} 个"
                    })
                result = await self._restore_shared_card(target)
                return json_response({"message": f"已恢复共享 Wiki：{result['items']} 条"})
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
            "backups": [c["name"] for c in self._life_cards()],
            "results": self._life_status_text(),
        })

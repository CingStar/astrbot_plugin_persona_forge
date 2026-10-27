"""按人格拆分的数字生命卡与 /life 指令。"""

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



class LifeCardsMixin:
    """按人格拆分的数字生命卡与 /life 指令。"""

    def _life_tasks(self, limit: int = 10) -> list[dict]:
        results = sorted(self.result_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for p in results[:limit]:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({
                "action": str(r.get("action") or ""),
                "status": str(r.get("status") or ""),
                "message": str(r.get("message") or ""),
            })
        return out

    def _life_cards(self, limit: int = 60) -> list[dict]:
        files = sorted(self.backup_dir.glob("life-card-*.pialife"), key=lambda p: p.stat().st_mtime, reverse=True)
        cards = []
        for p in files[:limit]:
            manifest = self._read_card_manifest(p) or {}
            fmt = manifest.get("format")
            card_type = "host"
            if fmt == CARD_FORMAT_PERSONA:
                card_type = "persona"
            elif fmt == CARD_FORMAT_SHARED:
                card_type = "shared"
            cards.append({
                "name": p.name,
                "type": card_type,
                "persona_id": str(manifest.get("persona_id") or ""),
                "size_kb": p.stat().st_size // 1024,
                "modified_at": int(p.stat().st_mtime),
                "created_at": int(manifest.get("created_at") or 0),
            })
        return cards

    def _life_list_text(self) -> str:
        cards = self._life_cards()
        if not cards:
            return "还没有数字生命卡备份。"
        label = {"persona": "人格卡", "shared": "共享卡", "host": "宿主卡"}
        lines = []
        for i, c in enumerate(cards[:20]):
            who = f" {c['persona_id']}" if c.get("persona_id") else ""
            lines.append(f"{i + 1}. [{label.get(c['type'], '卡片')}]{who} {c['name']} ({c['size_kb']}KB)")
        return "数字生命卡：\n" + "\n".join(lines)

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

    def _write_life_request(self, action: str, **extra) -> str:
        rid = uuid.uuid4().hex
        payload = {"id": rid, "action": action, "created_at": int(time.time()), **extra}
        tmp = self.request_dir / f".{rid}.tmp"
        dst = self.request_dir / f"{rid}.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(dst)
        return rid

    def _card_persona_ids(self) -> list[str]:
        ids = set(self.profiles.keys())
        try:
            ids |= {p.persona_id for p in self.context.persona_manager.personas}
        except Exception:
            pass
        for f in self.memory_dir.glob("*.json"):
            ids.add(f.stem)
        return sorted(x for x in ids if PERSONA_ID_RE.match(x))

    def _card_counts(self, pid: str) -> dict:
        return {
            "memory": len(self._load_memory(pid)),
            "versions": len(self._list_versions(pid)),
            "review": 1 if self._review_path(pid).is_file() else 0,
        }

    async def _native_persona_row(self, pid: str) -> dict | None:
        try:
            async with self._db().get_db() as session:
                obj = (
                    await session.execute(select(Persona).where(Persona.persona_id == pid))
                ).scalar_one_or_none()
        except Exception as e:
            logger.warning("[persona_forge] read persona row failed: %s", e)
            return None
        if obj is None:
            return None
        return {
            "persona_id": obj.persona_id,
            "system_prompt": obj.system_prompt or "",
            "begin_dialogs": obj.begin_dialogs or [],
            "tools": obj.tools,
            "skills": obj.skills,
            "custom_error_message": obj.custom_error_message,
        }

    async def _restore_native_persona(self, row: dict) -> None:
        pid = str(row.get("persona_id") or "")
        if not PERSONA_ID_RE.match(pid):
            return
        async with self._db().get_db() as session:
            obj = (
                await session.execute(select(Persona).where(Persona.persona_id == pid))
            ).scalar_one_or_none()
            if obj is None:
                obj = Persona(persona_id=pid, system_prompt="", begin_dialogs=[])
                session.add(obj)
            obj.system_prompt = str(row.get("system_prompt") or "")
            obj.begin_dialogs = row.get("begin_dialogs") or []
            obj.tools = row.get("tools")
            obj.skills = row.get("skills")
            obj.custom_error_message = row.get("custom_error_message")
            await session.commit()

    def _build_persona_card_bytes(self, pid: str, row: dict | None) -> bytes:
        buf = io.BytesIO()
        files: dict[str, str] = {}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            def add(name: str, data: bytes) -> None:
                zf.writestr(name, data)
                files[name] = hashlib.sha256(data).hexdigest()

            profile = dict(self.profiles.get(pid) or {})
            avatar = str(profile.get("avatar_path") or "")
            if avatar and Path(avatar).is_file():
                raw = Path(avatar).read_bytes()
                suffix = Path(avatar).suffix or ".png"
                rel = f"avatars/{pid}{suffix}"
                add(rel, raw)
                profile["avatar_path"] = rel
                profile["avatar_sha256"] = hashlib.sha256(raw).hexdigest()
            add(f"profiles/{pid}.json", json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8"))
            if row:
                add(f"personas/{pid}.json", json.dumps(row, ensure_ascii=False, indent=2).encode("utf-8"))
            memory_path = self._memory_path(pid)
            if memory_path.is_file():
                add(f"memory/{pid}.json", memory_path.read_bytes())
            version_dir = self.versions_dir / pid
            if version_dir.is_dir():
                for item in sorted(version_dir.glob("v*.*")):
                    if item.is_file():
                        add(f"versions/{pid}/{item.name}", item.read_bytes())
            review_path = self._review_path(pid)
            if review_path.is_file():
                add(f"reviews/{pid}.json", review_path.read_bytes())
            manifest = {
                "format": CARD_FORMAT_PERSONA,
                "format_version": CARD_FORMAT_VERSION,
                "plugin_version": PLUGIN_VERSION,
                "scope": "persona",
                "persona_id": pid,
                "created_at": int(time.time()),
                "counts": self._card_counts(pid),
                "files": files,
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        return buf.getvalue()

    def _build_shared_card_bytes(self) -> bytes:
        raw = self.wiki_file.read_bytes()
        manifest = {
            "format": CARD_FORMAT_SHARED,
            "format_version": CARD_FORMAT_VERSION,
            "plugin_version": PLUGIN_VERSION,
            "scope": "shared",
            "created_at": int(time.time()),
            "files": {"wiki/shared_wiki.json": hashlib.sha256(raw).hexdigest()},
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("wiki/shared_wiki.json", raw)
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        return buf.getvalue()

    async def _create_persona_card(self, pid: str, pre: bool = False) -> Path:
        row = await self._native_persona_row(pid)
        data = await asyncio.to_thread(self._build_persona_card_bytes, pid, row)
        if len(data) > self.max_card_mb * 1024 * 1024:
            raise RuntimeError(f"生命卡超过 {self.max_card_mb}MB 上限")
        ts = int(time.time())
        name = card_filename("persona", pid, ts)
        if pre:
            name = name.replace(".pialife", "-pre-restore.pialife")
        target = self.backup_dir / name
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        return target

    async def _create_shared_card(self) -> Path:
        if not self.wiki_file.is_file():
            raise RuntimeError("共享 Wiki 还不存在")
        data = await asyncio.to_thread(self._build_shared_card_bytes)
        target = self.backup_dir / card_filename("shared", "", int(time.time()))
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        return target

    def _validate_card_sync(self, path: Path) -> dict:
        if path.stat().st_size > self.max_card_mb * 1024 * 1024:
            raise RuntimeError(f"生命卡超过 {self.max_card_mb}MB 上限")
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                raise RuntimeError("不是人格工坊生命卡")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            if manifest.get("format") not in {CARD_FORMAT_PERSONA, CARD_FORMAT_SHARED}:
                raise RuntimeError("不支持的生命卡格式")
            if int(manifest.get("format_version") or 0) > CARD_FORMAT_VERSION:
                raise RuntimeError("生命卡版本过新，请升级插件")
            for name in names:
                if name.endswith("/"):
                    continue
                if not is_safe_card_member(name):
                    raise RuntimeError(f"生命卡包含非法路径：{name}")
            for name, digest in (manifest.get("files") or {}).items():
                if name not in names:
                    raise RuntimeError(f"生命卡缺少文件：{name}")
                if hashlib.sha256(zf.read(name)).hexdigest() != digest:
                    raise RuntimeError(f"生命卡校验失败：{name}")
        return manifest

    def _extract_card_sync(self, path: Path, target: Path) -> dict:
        manifest = self._validate_card_sync(path)
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not is_safe_card_member(info.filename):
                    continue
                zf.extract(info, target)
        return manifest

    async def _restore_persona_card(self, path: Path) -> dict:
        tmpdir = self._make_temp_dir("pf-card-")
        try:
            manifest = await asyncio.to_thread(self._extract_card_sync, path, tmpdir)
            pid = str(manifest.get("persona_id") or "")
            if not PERSONA_ID_RE.match(pid):
                raise RuntimeError("生命卡中的人格 id 非法")
            if pid in self.profiles or self._memory_path(pid).is_file() or (await self._native_persona_row(pid)):
                await self._create_persona_card(pid, pre=True)

            profile: dict = {}
            profile_path = tmpdir / "profiles" / f"{pid}.json"
            if profile_path.is_file():
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
            avatar_rel = str(profile.get("avatar_path") or "")
            if avatar_rel:
                src = tmpdir / avatar_rel
                if not Path(avatar_rel).is_absolute() and src.is_file():
                    suffix = Path(avatar_rel).suffix or ".png"
                    target_avatar = self.profile_dir / "avatars" / f"{pid}{suffix}"
                    target_avatar.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target_avatar)
                    profile["avatar_path"] = str(target_avatar)
                    profile["avatar_sha256"] = hashlib.sha256(target_avatar.read_bytes()).hexdigest()
                else:
                    profile.pop("avatar_path", None)
                    profile.pop("avatar_sha256", None)
            else:
                profile.pop("avatar_path", None)
                profile.pop("avatar_sha256", None)
            merged = dict(self.profiles.get(pid) or {})
            merged.update(profile)
            merged["updated_at"] = int(time.time())
            self.profiles[pid] = merged
            self._save_profiles()

            memory_src = tmpdir / "memory" / f"{pid}.json"
            if memory_src.is_file():
                shutil.copy2(memory_src, self._memory_path(pid))
            versions_src = tmpdir / "versions" / pid
            if versions_src.is_dir():
                versions_dst = self.versions_dir / pid
                if versions_dst.exists():
                    shutil.rmtree(versions_dst)
                shutil.copytree(versions_src, versions_dst)
            review_src = tmpdir / "reviews" / f"{pid}.json"
            if review_src.is_file():
                shutil.copy2(review_src, self._review_path(pid))
            row_path = tmpdir / "personas" / f"{pid}.json"
            if row_path.is_file():
                await self._restore_native_persona(json.loads(row_path.read_text(encoding="utf-8")))
                await self.context.persona_manager.initialize()
            return {
                "persona_id": pid,
                "memory": len(self._load_memory(pid)),
                "versions": len(self._list_versions(pid)),
            }
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _restore_shared_card(self, path: Path) -> dict:
        tmpdir = self._make_temp_dir("pf-card-")
        try:
            await asyncio.to_thread(self._extract_card_sync, path, tmpdir)
            src = tmpdir / "wiki" / "shared_wiki.json"
            if not src.is_file():
                raise RuntimeError("共享卡里没有 Wiki 数据")
            if self.wiki_file.is_file():
                await self._create_shared_card()
            shutil.copy2(src, self.wiki_file)
            return {"items": len(self._wiki_load())}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @filter.command_group("life")
    def life(self):
        """数字生命卡：按人格备份/恢复、共享卡、宿主卡。"""
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
        yield event.plain_result(self._life_list_text())

    @life.command("backup")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def life_backup(self, event: AstrMessageEvent, target: str = "plugin", mode: str = ""):
        """target: plugin(默认) | persona:<id> | shared | all|agent|qq"""
        target = (target or "plugin").strip()
        if target in {"all", "agent", "qq"}:
            rid = self._write_life_request(
                "backup", scope=target, hot=(mode == "hot"), requested_by=event.get_sender_id()
            )
            yield event.plain_result(
                f"已提交{'热' if mode == 'hot' else '冷'}备份请求 {rid[:8]}（{target}）。\n"
                "机器人可能短暂离线，完成后 /life status 查看。"
            )
            return
        try:
            async with self._card_lock:
                if target == "shared":
                    path = await self._create_shared_card()
                    yield event.plain_result(f"已生成共享卡 {path.name}（{path.stat().st_size // 1024}KB）。")
                    return
                if target.startswith("persona:"):
                    pid = target.split(":", 1)[1].strip()
                    if not PERSONA_ID_RE.match(pid):
                        yield event.plain_result("人格 id 不合法。")
                        return
                    if pid not in self._card_persona_ids():
                        yield event.plain_result(f"人格 {pid} 不存在。")
                        return
                    path = await self._create_persona_card(pid)
                    yield event.plain_result(f"已生成人格卡 {path.name}（{path.stat().st_size // 1024}KB）。")
                    return
                created = []
                for pid in self._card_persona_ids():
                    created.append(await self._create_persona_card(pid))
                if self.wiki_file.is_file():
                    created.append(await self._create_shared_card())
                total_kb = sum(p.stat().st_size for p in created) // 1024
                yield event.plain_result(
                    f"已生成 {len(created)} 张生命卡（{total_kb}KB）：\n"
                    + "\n".join(f"- {p.name}" for p in created[:20])
                )
        except Exception as e:
            yield event.plain_result(f"备份失败：{e}")

    @life.command("restore")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def life_restore(self, event: AstrMessageEvent, archive: str = "", confirm: str = ""):
        if not self.allow_restore:
            yield event.plain_result("插件未允许从 QQ 发起恢复：请在设置里打开 allow_restore。")
            return
        name = (archive or "").strip()
        if (
            confirm != "confirm"
            or not name.startswith("life-card-")
            or not name.endswith(".pialife")
            or Path(name).name != name
        ):
            yield event.plain_result("用法：/life restore life-card-xxx.pialife confirm")
            return
        target = self.backup_dir / name
        if not target.is_file():
            yield event.plain_result("找不到这个生命卡。")
            return
        manifest = await asyncio.to_thread(self._read_card_manifest, target)
        is_native = bool(manifest) and manifest.get("format") in {CARD_FORMAT_PERSONA, CARD_FORMAT_SHARED}
        if not is_native:
            rid = self._write_life_request(
                "restore", archive=str(target), scope="all", requested_by=event.get_sender_id()
            )
            yield event.plain_result(f"已提交宿主恢复请求 {rid[:8]}。机器人会离线，恢复后重启。")
            return
        try:
            async with self._restore_lock:
                if manifest.get("format") == CARD_FORMAT_PERSONA:
                    result = await self._restore_persona_card(target)
                    yield event.plain_result(
                        f"已恢复人格 {result['persona_id']}：记忆 {result['memory']} 条、版本 {result['versions']} 个。"
                    )
                else:
                    result = await self._restore_shared_card(target)
                    yield event.plain_result(f"已恢复共享 Wiki：{result['items']} 条。")
        except Exception as e:
            yield event.plain_result(f"恢复失败：{e}")

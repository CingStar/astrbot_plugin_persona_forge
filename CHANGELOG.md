# Changelog

## 0.3.1

- **后端拆包**：`main.py` 只保留入口与 mixin 组合，业务逻辑拆到 `persona_forge/` 包（`config` / `paths` / `memory_scope` / `cards` / `dev` / `text` / `handlers` / `core` / `persona_commands` / `memory_cmds` / `knowledge` / `dev_mode` / `life_cards` / `legacy_natural` / `events` / `api`）；纯函数模块不依赖 AstrBot。
- **Handler 归属修正**：拆包后 AstrBot 按 `handler_module_path` 会找不到 Handler 所属插件（路径变成 `...persona_forge.api` 等子模块），新增 `persona_forge/handlers.py`，在 `main.py` 导入完成时把这些 Handler 的 `handler_module_path` / `handler_full_name` / `star_handlers_map` 键改回入口模块，避免指令失效；同时兼容「顶层模块导入」的加载方式，并在启动日志输出 `adopted N handlers`、`/status` 返回 `handlers` 计数，便于排查指令不响应。
- **会话记忆抓取**：新增 `/memory snapshot` 与 WebUI 记忆页「抓取最近会话」，按需把当前会话最近 20 条消息写入当前人格（`session` / `scope=chat`，重复抓取更新同一条快照）；明确人格记忆不会实时同步 QQ 聊天记录。
- **QQ 资料同步可视化**：人格抽屉显示「上次推送到 QQ」「最近核对 QQ 资料」时间，新增「立即同步到 QQ」（`POST persona/sync`）；插件不会从 QQ 反向拉取昵称/头像。
- **数据目录合规整改**：所有数据统一收敛到 `data/plugin_data/astrbot_plugin_persona_forge/**`（资料 / 记忆 / Wiki / 生命卡），不再写 `AstrBot/data/` 根目录；相对路径基于 AstrBot 根目录解析，绝对路径原样保留。
- **旧数据自动迁移**：启动时仅当「旧路径存在且新路径不存在」才整体移动，并重写 `profiles.json` 里的头像绝对路径；迁移结果记录在 `runtime_state.json` 的 `migrated_paths`，WebUI 设置中心「存储」分区可见，失败只告警不覆盖数据。
- **安全整改**：`snowluma_token` 标记为 secret 且接口 / WebUI 只回显 `••••••`；头像改为接口返回 data URL，不再用 `/api/...` 直链；头像上传校验 MIME 与 `max_avatar_mb`。
- **记忆隔离**：记忆新增 `scope`（`global` / `chat` / `persona`）与 `umo`；新增 `memory_scope`、`memory_chat_isolation` 配置；群 A / 群 B / 私聊三向隔离，0.3.0 旧记忆（无 `umo`）在群聊默认不可见。
- **记忆指令**：新增 `/memory preview [关键词]`（预览当前会话实际注入的记忆）与 `/memory scope <关键词> chat|persona`（批量改作用域）。
- **按人格数字生命卡**：新增人格卡 `life-card-persona-<id>-<ts>.pialife` 与共享卡 `life-card-shared-<ts>.pialife`，ZIP 内含 manifest、资料、记忆、头像、版本与待审草稿；`/life backup` 支持 `plugin`（默认）/ `persona:<id>` / `shared` / 宿主卡 `all|agent|qq`；`/life list` 标注三种卡型。
- **生命卡恢复安全**：manifest 格式与版本校验、成员路径白名单（`manifest.json`、`personas/`、`profiles/`、`memory/`、`avatars/`、`versions/`、`reviews/`、`wiki/`）、逐文件 SHA256 校验、`max_card_mb` 上限、覆盖前自动生成 `-pre-restore` 保护卡。
- **开发者模式（教学模式）**：新增 `/dev on|off|status|apply|clear`。开启后私聊普通消息记录为教学笔记（单人格 200 条，24 小时自动关闭），`/dev apply` 融合当前提示词 + 知识记忆 + 笔记生成**待审草稿**，不直接改动线上人格；该模式不进入角色扮演。
- **全新 WebUI**：蓝色系纯静态视图，人格 / 审查 / 生命卡三页 + 设置中心六个分区（通用 / 群聊 / QQ 资料 / 知识库 / 生命卡 / 存储）；新增人格详情、头像上传与 URL、版本回滚、记忆管理、开发者模式面板、生命卡备份恢复；无 `innerHTML`，头像走 data URL，支持深色主题与响应式。
- **插件视图 API 扩充**：`/astrbot_plugin_persona_forge` 下新增 `persona`、`persona/update`、`persona/avatar/{upload,url,remove}`、`persona/dev*`、`memory`、`persona/version(s)`、`settings`、`status`、`life*` 等路由。
- **CI**：新增 Python 3.10 / 3.12 单测矩阵（`unittest`，纯标准库，79 个用例，按模块 AST 抽取纯函数）、测试发现数量守卫、`persona_forge/config.py` 与 `metadata.yaml` 版本一致性检查、`compileall` 覆盖 `main.py` + `tests` + `persona_forge`、WebUI JS 语法检查、WebUI 纯函数单测（`node:test`，55 个用例）与 `innerHTML` 禁令。

## 0.3.0

- 项目更名为「人格工坊」，仓库与插件标识由 `astrbot_plugin_assistant` 改为 `astrbot_plugin_persona_forge`；WebUI API 前缀改为 `/astrbot_plugin_persona_forge`，日志标签改为 `[persona_forge]`，Release 包名改为 `astrbot_plugin_persona_forge.zip`。
- **不兼容**：升级时需把插件目录重命名为 `astrbot_plugin_persona_forge` 并重启 AstrBot；插件配置按插件名保存，旧配置需要重新确认（数据目录默认值保持不变）。
- README 重构：居中文字标题与徽章导航、能力总览、完整指令表、分组配置说明与数据目录；修正 `metadata.yaml` 中占位的仓库地址。
- 命令优先：移除自由文本意图识别，统一 `/persona` 指令组。
- 人格版本：修改前自动快照、版本列表、回滚、导出 `.md`。
- 完整字段：`tools`、`skills`、`begin_dialogs`、`custom_error_message`。
- 提示词审查：压缩/重写草稿、diff、批准/拒绝。
- 提示词压缩：light / medium / extreme。
- 头像：get / set / remove。
- QQ 资料：profile / group_card / hybrid 同步模式。
- 群聊模式：off / chat / admin、群白名单、@ 触发、群友限速、guest wiki 开关。
- WebUI：人格列表、切换、版本、review、群聊设置。
- 人格切换：按人格恢复会话/快照，避免旧角色上下文串味。
- 插件更新：自动新会话 + 核对 QQ 昵称/头像与人格资料。
- 知识：共享 Wiki 与人格私有 knowledge 分离；`/memory promote` 仅同步 knowledge。

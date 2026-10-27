<div align="center">

<h1>人格工坊</h1>

**人格管理 · 分层记忆与隔离 · 共享 Wiki · 提示词版本与审查 · 开发者模式 · 数字生命卡**

让角色不只活在提示词里，而是有资料、有记忆、有版本、可回滚。

[![Version](https://img.shields.io/badge/version-0.3.1-6366F1?style=flat-square)](CHANGELOG.md)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0%20%3C6-0EA5E9?style=flat-square)](https://github.com/AstrBotDevs/AstrBot)
[![CI](https://github.com/CingStar/astrbot_plugin_persona_forge/actions/workflows/ci.yml/badge.svg)](https://github.com/CingStar/astrbot_plugin_persona_forge/actions/workflows/ci.yml)
[![Command First](https://img.shields.io/badge/mode-command--first-8B5CF6?style=flat-square)](#commands)
[![License](https://img.shields.io/badge/license-AGPL--3.0-22C55E?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-EC4899?style=flat-square)](https://github.com/CingStar/astrbot_plugin_persona_forge/pulls)

[快速开始](#quickstart) · [能力一览](#features) · [指令](#commands) · [WebUI](#webui) · [配置](#config) · [更新日志](CHANGELOG.md)

</div>

> [!IMPORTANT]
> 更新插件代码后需要**重载插件或重启 AstrBot**，插件不会热加载。人格、记忆、Wiki 等数据本身是实时读写，不受影响。

---

<a name="features"></a>

## ✨ 能力一览

| 🎭 人格引擎 | 🧠 分层记忆 | 📚 共享知识库 |
| :---: | :---: | :---: |
| 创建 / 切换 / 退出 / 默认人格<br/>昵称与头像自动同步到 QQ | 经历、知识、会话快照分层<br/>按关键词注入，切换不串味 | 所有人格共用 Wiki<br/>可选同步 AstrBot 知识库 |

| 🧾 版本与审查 | 🛠 开发者模式 | 💳 数字生命卡 |
| :---: | :---: | :---: |
| 修改前自动快照，压缩草稿<br/>diff 审批，一键回滚 / 导出 | 教学模式而非扮演：私聊口述纠正<br/>生成待审草稿，不直接改线上人格 | 人格卡 / 共享卡 / 宿主卡<br/>按人格冷备、就地恢复 |

| 🔒 记忆隔离 | 💻 WebUI 控制台 | 🧪 质量保障 |
| :---: | :---: | :---: |
| 记忆带 `umo` + `scope`<br/>群 A / 群 B / 私聊互不可见 | 人格 / 审查 / 生命卡三页<br/>设置中心六分区可视化配置 | 纯标准库单测（3.10 / 3.12 矩阵）<br/>JS 语法检查 + innerHTML 禁令 |

<a name="quickstart"></a>

## 🚀 快速开始

### 环境要求

| 项目 | 要求 |
| --- | --- |
| AstrBot | `>= 4.0, < 6` |
| Python | 跟随 AstrBot 运行环境即可（CI 覆盖 3.10 / 3.12） |
| 额外依赖 | 无；`Pillow` 可选（仅头像相似度核对） |
| QQ 资料同步（可选） | SnowLuma OneBot HTTP，需支持 `set_qq_profile` / `set_qq_avatar` |

### 安装

**方式 A：Git（推荐，便于更新）**

```bash
cd AstrBot/data/plugins
git clone https://github.com/CingStar/astrbot_plugin_persona_forge.git
```

**方式 B：Release 包**

下载 Release 中的 `astrbot_plugin_persona_forge.zip`，解压到 `AstrBot/data/plugins/astrbot_plugin_persona_forge/`。

> 目录名请保持 `astrbot_plugin_persona_forge`，WebUI 视图与元数据路径依赖它。

### 启用

重启 AstrBot，或在 WebUI → 插件管理 中重载插件。首次启动会自动创建数据目录：

```text
AstrBot/data/plugin_data/astrbot_plugin_persona_forge/     # 0.3.1 起的统一数据根目录
```

- 数据全部收敛到 `data/plugin_data/astrbot_plugin_persona_forge/**`，不再散落在 `AstrBot/data/` 根下；
- 配置项里的**相对路径**基于 AstrBot 根目录解析，**绝对路径**原样使用；
- 从 0.3.0 及更早版本升级时，旧路径的数据会**自动迁移**，详见 [升级与迁移](#migration)。

### 初始化默认人格

`default` 是「退出角色」时的兜底人格，**必须先配置昵称与头像**，否则切换人格时无法回退：

```text
/persona create default|小助手|你是一个温柔、可靠的中文助手。
/persona avatar default        # 下一条消息发送头像图片或图片 URL
/persona default               # 核对默认人格资料是否完整
```

### 创建第一个角色

```text
/persona create 温言|温言|你说话轻声细语，擅长安慰人，偶尔引用诗句。
/persona avatar 温言 set       # 下一条消息发送头像
/persona switch 温言
```

> `switch` 会同步 QQ 昵称/头像，因此需要先配置 `snowluma_http`；未配置时会报错并保持当前人格。也可以只使用 `default` 人格。

<a name="commands"></a>

## 🎭 人格管理

一个人格 = **AstrBot 原生 Persona** + **插件扩展资料** + **私有记忆** + **版本快照** + **教学笔记**。

| 组成 | 存放位置 | 内容 |
| --- | --- | --- |
| 原生 Persona | AstrBot 数据库 | 系统提示词、工具、Skills、开场对话、错误文案 |
| 扩展资料 | `persona_profiles/profiles.json` | 昵称、头像、提示词副本、更新时间 |
| 私有记忆 | `persona_memory/<id>.json` | 经历 / 知识 / 会话快照（带 `umo`、`scope`） |
| 版本快照 | `persona_profiles/versions/<id>/` | 提示词、字段、资料与头像副本 |
| 教学笔记 | `persona_profiles/dev/<id>.json` | 开发者模式记录的口述纠正（最多 200 条） |

> 上表路径都相对于数据根目录 `data/plugin_data/astrbot_plugin_persona_forge/`。

### 人格指令

> 以下以 AstrBot 默认指令前缀 `/` 为例；指令前缀可在 AstrBot 配置中修改。管理指令仅 AstrBot 管理员可用。

| 指令 | 说明 |
| --- | --- |
| `/persona list` | 列出所有人格，标注默认人格与当前人格 |
| `/persona view <id>` | 查看昵称、头像、工具、Skills、开场对话与提示词 |
| `/persona show <id>` | 快速查看系统提示词（截断 1200 字） |
| `/persona profile [id]` | 查看昵称、头像路径、头像 SHA256、提示词状态 |
| `/persona create <id>` | 交互式创建：下一条发提示词，再发昵称（180 秒内有效） |
| `/persona create <id>\|<昵称>\|<提示词>` | 一步创建；id 建议用字母 / 数字 / `_` / `-` |
| `/persona update <id>` | 下一条消息作为新提示词，修改前自动快照 |
| `/persona edit <id>\|<提示词>` | 直接覆盖提示词 |
| `/persona fields <id> key=value` | 修改 `tools` / `skills` / `error` / `dialogs` |
| `/persona avatar <id> get\|set\|remove` | 查看 / 设置 / 移除头像 |
| `/persona delete <id> confirm` | 删除人格（`default` 受保护，不可删除） |
| `/persona switch <id>` | 切换人格：快照会话 → 同步 QQ 资料 → 恢复专属会话 |
| `/persona use <id>` | 同 `switch` |
| `/persona exit` | 退出角色，回到 `default` 人格 |
| `/persona default [id]` | 查看或设置 AstrBot 默认人格 |
| `/persona versions [id]` | 列出最近 20 个版本号 |
| `/persona rollback <id> <version>` | 回滚到指定版本（回滚前会再快照一次） |
| `/persona export [id]` | 导出人格资料为 `.md` 文件 |
| `/persona iterate [id]` | 用该人格私有知识重建提示词（追加「人格专属知识」段落） |
| `/persona rewrite [id]` | 用聊天模型重写提示词并立即应用 |
| `/persona compress [id] light\|medium\|extreme` | 生成压缩草稿，进入审查 |
| `/persona review list\|show <id>\|approve <id>\|reject <id>` | 草稿审查（无参数时等价 `list`） |
| `/persona check` | 核对 QQ 昵称 / 头像与人格资料是否一致 |
| `/persona sync` | 新建会话并强制同步昵称 / 头像 |
| `/persona debug` | 排障：默认人格、提示词、当前会话状态 |
| `/persona resetdefault confirm` | 重置 `default` 的提示词与资料 |

### 创建人格的两种方式

**一步创建**

```text
/persona create 温言|温言|你说话轻声细语，擅长安慰人。
```

**交互创建**（适合提示词很长）

```text
/persona create 温言
# 下一条消息：发送人格提示词
# 再下一条消息：发送昵称
```

> 创建流程 180 秒内有效；发送 `取消` 或 `cancel` 可随时中止。

### 人格字段

```text
/persona fields <id> tools=all|none|a,b     # 可用工具
/persona fields <id> skills=all|none|a,b    # 可用 Skills
/persona fields <id> error=文本             # 自定义错误文案
/persona fields <id> dialogs=<JSON 数组>    # 开场对话，user / assistant 成对
```

- `tools` / `skills`：`all` 表示全部，`none` 表示禁用，`a,b` 表示白名单；
- `dialogs`：按 AstrBot 原生 `begin_dialogs` 结构填写，条数需为偶数；
- 任何字段修改前都会自动生成版本快照，可用 `/persona rollback` 撤销。

### 头像

```text
/persona avatar <id> get
/persona avatar <id> set
/persona avatar <id> remove
```

`set` 之后下一条消息发送头像，支持：

| 来源 | 示例 |
| --- | --- |
| 图片消息 | 直接发送图片（可配文指令） |
| 图片 URL | `https://example.com/avatar.png` |
| 本地文件 | `file:///AstrBot/data/avatar.png` 或绝对路径 |
| Base64 | `base64://iVBORw0KGgo...` |

仅接受 `PNG` / `JPEG` / `WebP`，大小受 `max_avatar_mb` 限制（默认 5 MB）。WebUI 还支持：

- **上传图片**（multipart，不走 data URL 直塞 `<img>`）；
- **填 URL 后服务端下载**；
- **一键使用机器人当前 QQ 头像**（`source=qq`）。

保存后插件会记录头像文件、SHA256 与更新时间，`/persona check` 可核对 QQ 侧是否同步。

### 切换人格时发生了什么

1. 把当前会话的最后 20 条消息压缩成快照，写入当前人格的私有记忆（`kind=session`）；
2. 用私有知识迭代当前人格与目标人格的提示词（开启 `auto_llm_rewrite` 时改用模型重写）；
3. 通过 SnowLuma 同步 QQ 昵称 / 头像（或群名片，取决于 `nickname_sync_mode`）；
4. 将该人格设为 AstrBot 默认人格，并按 `persona:<id>` 标签恢复每个会话的专属对话；
5. 目标人格没有专属会话时新建一个，旧会话保留不删除。

> 切换是**全局**行为，会影响所有会话与群；目标人格必须已有昵称和头像，且 `default` 资料必须完整，否则切换会失败并保持原人格。

## 🧠 记忆系统

记忆按人格隔离，保存在 `persona_memory/<id>.json`，单个文件最多保留 500 条。

### 记忆模型

0.3.1 起每条记忆除 `kind` / `text` 外，还带两个字段：

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `kind` | `episodic` / `knowledge` / `session` | 经历偏好 / 知识 / 会话快照 |
| `scope` | `global` / `chat` / `persona` | 全局 / 仅本会话 / 本人格（私聊通用） |
| `umo` | `unified_msg_origin` | 写入时所在的会话（私聊或某群） |

新记忆的默认 `scope` 由 `memory_scope` 配置决定（默认 `chat`）：

| 类型 | 写入方式 | 默认 scope | 典型用途 |
| --- | --- | --- | --- |
| `episodic` | `/memory add` | `chat`（可配 `persona`） | 经历、偏好、约定、称呼 |
| `knowledge` | `/memory know` | `global`（跟着人格提示词走） | 世界观、设定、事实、口癖 |
| `session` | 切换人格时自动写入 | `chat` | 上一轮对话快照，切回时参考 |

### 记忆指令

| 指令 | 说明 |
| --- | --- |
| `/memory add 内容` | 写入经历 / 偏好记忆 |
| `/memory know 内容` | 写入知识型记忆（会进入人格提示词，对所有会话生效） |
| `/memory list` | 查看当前人格记忆（最近 20 条，带 `scope=` 与来源） |
| `/memory snapshot` | 立即把当前会话最近 20 条消息写入当前人格记忆（切换人格时也会自动抓取一次） |
| `/memory preview` | 预览当前会话**可见**的人格记忆（注入前的候选集，排查串味用） |
| `/memory scope <关键词> chat\|persona` | 批量修改匹配记忆的 `scope`（知识型固定 `global`，会话快照跳过） |
| `/memory forget 关键词` | 删除包含关键词的记忆 |
| `/memory clear` | 清空当前人格记忆 |
| `/memory promote [关键词]` | 把知识型记忆同步到共享 Wiki，可带关键词过滤 |

> `/memory preview` 与对话时的注入使用**同一套可见性规则**：它展示的是候选集（最终注入还会按关键词打分取前 8 条），排查串味时看这个列表就够了。
> `/memory scope 关键词 persona` 适合把「只在该群学过的事」升级为该人格私聊通用；反过来 `chat` 可以把通用记忆收窄回某个会话。
> 人格记忆**不会实时同步 QQ 聊天记录**：只有切换人格、`/memory snapshot` 或 WebUI 记忆页的「抓取最近会话」时才会写入；抓取的是会话快照（`kind=session`），按 `umo` 隔离。

### 注入规则

对话前插件会注入三层内容（都标记为临时上下文，不写回对话历史）：

1. **共享知识库**：按关键词打分取前 **5** 条 Wiki 条目（群友能否读取由 `guest_wiki` 控制）；
2. **人格私有记忆**：先按 [记忆隔离](#isolation) 规则过滤，再从最近 **200** 条中按关键词命中数取前 **8** 条；
3. **该人格上一轮会话快照**：取最新一条不属于当前会话的 `session` 记忆（截断 3000 字），切换人格也能接上。

私有记忆**只对管理员 / owner 注入**，群友永远看不到；提示词迭代只取 `knowledge`，不会把共享 Wiki 混进某个人格。

<a name="isolation"></a>

## 🔒 记忆隔离

0.3.1 修复了「A 群学到的事在 B 群被说出来」的问题。可见性由 `memory_chat_isolation`（默认 `true`）控制：

| 记忆 scope | 私聊 | 当前群聊 | 其他群聊 |
| --- | --- | :---: | :---: |
| `global`（知识型） | ✅ | ✅ | ✅ |
| `chat` 且 `umo` 相同 | ✅ | ✅ | ❌ |
| `persona` | ✅ | ✅（同群写入的） | ❌ |
| 旧数据（无 `scope`） | ✅ | ✅（同群写入的） | ❌ |

- `memory_chat_isolation: false` 时退回 0.3.0 行为：所有记忆对所有会话可见（**不推荐**，仅用于兼容／排障）；
- 群 A / 群 B / 私聊三向隔离：`persona` 作用域的记忆在任意私聊可见，但在群聊只对「写入它的那个群」可见；
- 0.3.0 留下的旧记忆没有 `umo`，在群聊里一律隐藏，私聊照常可见——需要时用 `/memory scope` 或 WebUI 调整；
- WebUI 抽屉里的「记忆」分区会展示 scope 与来源标签，并支持新增 / 知识 / 遗忘 / 清空 / 提升 / 改 scope。

## 📚 共享 Wiki

所有人格共用的知识库，保存在 `wiki_file`（默认 `shared_wiki.json`），最多保留 2000 条。

| 指令 | 说明 |
| --- | --- |
| `/wiki add 标题\|内容` | 添加条目；只给内容时自动取前 40 字作标题 |
| `/wiki add 内容` | 快速添加，自动生成标题 |
| `/wiki list` | 查看最近 20 条 |
| `/wiki search 关键词` | 检索条目（最多 10 条） |

- 相同内容自动去重；
- 对话时按关键词打分取前 **5** 条注入，用当前人格的口吻表达；
- 配置 `astrbot_kb_name` 后：`/wiki add` 会同步写入 AstrBot 知识库，对话时额外检索该知识库前 4 条；
- 群友能否读到共享 Wiki 由 `guest_wiki` 控制（`read` / `off`）。

## 🧾 版本与审查

修改提示词 / 字段 / 头像前，插件会自动生成版本快照，默认每人格保留 20 版（`version_retention`）。

- **版本内容**：系统提示词、开场对话、工具、Skills、错误文案、人格资料、头像副本；
- **导出**：`/persona export <id>` 生成 `.md` 到 `exports/`，可直接发到聊天里；
- **迭代**：`/persona iterate <id>` 把该人格的 `knowledge` 记忆追加成「人格专属知识」段落；
- **重写**：`/persona rewrite <id>` 调用聊天模型重写提示词，立即生效（同样会快照）；
- **压缩**：`/persona compress <id> light|medium|extreme` 生成草稿，不会直接覆盖；
- **开发者草稿**：`/dev apply` 生成的草稿同样进入审查队列（`source=dev`）；
- **审查**：`/persona review show <id>` 看 diff，`approve` 应用，`reject` 丢弃。

待审草稿保存在 `persona_profiles/reviews/<id>.json`，包含 `base_prompt`、`draft_prompt` 与 unified diff。

## 🛠 开发者模式（教学模式）

开发者模式**不是扮演模式**：开启后，你在私聊里发的普通消息会被当成「教学笔记」记下来，而不是让角色回话；攒够后由模型把笔记融合进人格提示词，但**只生成待审草稿**，你批准后才生效。

| 指令 | 说明 |
| --- | --- |
| `/dev on [人格id]` | 开启（默认当前人格），24 小时后自动关闭 |
| `/dev off` | 立即关闭 |
| `/dev status` | 查看当前人格、笔记数量、剩余时间 |
| `/dev apply [人格id]` | 把「当前提示词 + 知识记忆 + 教学笔记」交给模型重写成草稿 |
| `/dev clear [人格id]` | 清空该人格的教学笔记 |

典型工作流：

```text
/dev on 温言
# 私聊发几句：说话别这么快；不要用「作为AI」；安慰时先复述我的感受
/dev status
/dev apply            # 生成草稿，进入审查队列
/persona review show 温言
/persona review approve 温言
```

- 仅 AstrBot 管理员可用，只捕获私聊消息；正在进行的创建 / 编辑 / 设头像流程（180 秒窗口）与斜杠指令不会被当成笔记；
- 单人格最多保留 200 条笔记，每条截断 2000 字并记录 `umo`；`/dev apply` 只取最近 50 条参与重写；
- 笔记单独保存在 `persona_profiles/dev/<id>.json`，不写进记忆、不影响对话上下文；
- WebUI 人格抽屉的「开发者模式」分区可以开关、追加笔记、压缩与清空，等价于上面五条指令。

## 💳 数字生命卡

0.3.1 起生命卡按**作用域**分成三种：

| 类型 | 文件名 | 内容 | 生成方 |
| --- | --- | --- | --- |
| **人格卡** | `life-card-persona-<id>-<ts>.pialife` | 该人格的原生 Persona、资料、记忆、头像、版本快照、待审草稿 | 插件自身（zip） |
| **共享卡** | `life-card-shared-<ts>.pialife` | 共享 Wiki（`wiki/shared_wiki.json`） | 插件自身（zip） |
| **宿主卡** | `life-card-*.pialife`（无 manifest 或旧格式） | AstrBot / QQ 侧全量数据 | 宿主 `life-card-host` |

```text
插件写请求 ─▶ requests/<id>.json ─▶ life-card-host ─▶ results/<id>.json ─▶ 插件读结果
```

### 指令

| 指令 | 说明 |
| --- | --- |
| `/life backup [target] [hot]` | `target` 见下表；`hot` 只对宿主卡有效 |
| `/life list` | 列出生命卡，标注 `[人格卡]` / `[共享卡]` / `[宿主卡]` |
| `/life status` | 查看最近 5 条任务状态（宿主卡任务） |
| `/life restore <文件名> confirm` | 恢复生命卡 |

`/life backup` 的 `target`：

| target | 行为 |
| --- | --- |
| `plugin`（默认） | 为所有已知人格各生成一张人格卡，并生成共享卡 |
| `persona:<id>` | 只生成该人格的人格卡，例如 `/life backup persona:温言` |
| `shared` | 只生成共享卡 |
| `all` / `agent` / `qq` | 提交**宿主卡**备份请求给 `life-card-host`（AstrBot 侧 / QQ 侧 / 全部） |

### 恢复与安全

- 人格卡 / 共享卡由插件**就地恢复**：恢复人格卡会覆盖同名人格的资料、记忆、头像与版本；恢复共享卡会先为现有 Wiki 生成一份备份再覆盖；
- 恢复前 ZIP 会做三重校验：`manifest.json` 的格式与版本、成员路径必须在白名单内（`manifest.json`、`personas/`、`profiles/`、`memory/`、`avatars/`、`versions/`、`reviews/`、`wiki/`），每个文件的 SHA256；
- 覆盖已存在的人格前会自动生成 `life-card-persona-<id>-<ts>-pre-restore.pialife` 保护卡；
- 单卡大小受 `max_card_mb`（默认 200 MB）限制，超限直接拒绝；
- `host` 格式（宿主卡）仍交给 `life-card-host` 执行，恢复期间机器人会离线；
- 从 QQ / WebUI 发起恢复需要 `allow_restore: true`（默认关闭）。

## 👥 群聊模式

群聊门禁只作用于群消息，私聊不受影响；管理指令始终仅 AstrBot 管理员可用。

| 模式 | 行为 |
| --- | --- |
| `off` | 关闭门禁，群聊按 AstrBot 原有逻辑处理 |
| `chat` | 启用门禁：白名单 → @ 校验 → 群友限速 |
| `admin` | 与 `chat` 相同的门禁规则；管理指令始终仅管理员可用 |

```json
{
  "group_mode": "chat",
  "group_whitelist": ["123456789"],
  "require_at": true,
  "guest_wiki": "read",
  "guest_rate_limit_per_minute": 5
}
```

- 白名单为空表示不限制群；
- 管理员不受群友限速影响，但白名单与 @ 要求同样生效；
- 群友**不会**读到人格私有记忆，只能按 `guest_wiki` 读共享知识；
- `nickname_sync_mode` 决定切换人格时改什么：`profile` 改资料、`group_card` 群内改群名片 / 私聊改资料、`hybrid` 两者都改。

> 门禁设置可以在 WebUI 设置中心的「群聊」分区直接改，会写入 `runtime_state.json`，优先级高于配置文件里的默认值。

<a name="webui"></a>

## 💻 WebUI

插件详情页 → **人格工坊** 视图（纯静态 HTML/CSS/ES Module，无构建链、无外部 CDN）：

| 页面 | 内容 |
| --- | --- |
| 人格 `#/personas` | 卡片网格（头像 / 昵称 / 记忆数 / 版本数）、切换、搜索、右侧人格抽屉 |
| 审查 `#/reviews` | 待审草稿 + 历史、diff 着色、来源说明侧栏、批准 / 拒绝 |
| 生命卡 `#/life` | 按人格分组的最新卡、共享卡、备份与恢复、宿主卡任务状态 |
| 设置中心 `#/settings` | 六个分区：通用 / 群聊 / QQ 资料 / 知识库 / 生命卡 / 存储 |

人格抽屉里可以完成：改昵称与提示词、上传头像 / 填 URL / 用机器人 QQ 头像、迭代与重写（「提炼当前人格」：知识 / 模型两种模式）、查看与回滚版本、管理记忆（新增 / 知识 / 遗忘 / 清空 / 提升 / 改 scope）、开关开发者模式并追加笔记、查看工具与 Skills 及更新时间。

设置中心对应关系（以后端 `settings` 字段为准；路径类字段只读展示）：

| 分区 | 主要覆盖配置 |
| --- | --- |
| 通用 | `auto_llm_rewrite`、`version_retention`、`allow_restore`、`memory_scope`、`memory_chat_isolation`、`image_search_*`（预留） |
| 群聊 | `group_mode`、`group_whitelist`、`require_at`、`guest_wiki`、`guest_rate_limit_per_minute`（写入 `runtime_state.json`） |
| QQ 资料 | `snowluma_http`、`snowluma_token`（只回显掩码，留空不覆盖）、`nickname_sync_mode`、`max_avatar_mb` |
| 知识库 | `astrbot_kb_name`、共享 Wiki 条目数 |
| 生命卡 | `max_card_mb`（可改）；`backup_dir` / `request_dir` / `result_dir` 只读展示，最近卡片与宿主任务 |
| 存储 | 各数据目录的实际绝对路径、条目计数、已迁移项 `migrated_paths` |

- 视图接口前缀为 `/astrbot_plugin_persona_forge`：`/personas`、`/persona`、`/persona/update`、`/persona/avatar/{upload,url,remove}`、`/persona/switch`、`/persona/version(s)`、`/persona/iterate`、`/persona/dev*`、`/memory`、`/reviews`、`/review`、`/settings`、`/status`、`/life*`（旧接口 `/versions`、`/switch`、`/group-mode`、`/life-card` 保留兼容）；
- 头像以接口返回的 data URL（`avatar_data`）渲染，不直接用 `/api/...` 直连；
- 仅依赖 AstrBot 支持插件 `views/`；旧版本 AstrBot 不显示视图时，指令功能完全不受影响；
- 深色主题、响应式（≤900px 侧栏收起、抽屉全屏、卡片单列）、空态 / 骨架屏 / 错误 banner / toast 均已实现；
- 视图不使用 `innerHTML`，CI 里有对应的静态检查。

<a name="config"></a>

## 🧩 配置

在 AstrBot 的插件配置页修改（对应 `_conf_schema.json`），也可以在 WebUI 设置中心改。

### 存储

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `profile_dir` | `data/plugin_data/astrbot_plugin_persona_forge/persona_profiles` | 人格资料、头像、版本、审查草稿、导出、教学笔记 |
| `memory_dir` | `data/plugin_data/astrbot_plugin_persona_forge/persona_memory` | 人格私有记忆 |
| `wiki_file` | `data/plugin_data/astrbot_plugin_persona_forge/shared_wiki.json` | 共享 Wiki |
| `version_retention` | `20` | 每个人格保留的版本数 |

> 相对路径基于 AstrBot 根目录；留空或保留 0.3.0 的旧默认值（`/AstrBot/data/...`）都会回落到新的 `data/plugin_data/astrbot_plugin_persona_forge/**`。

### 知识库与模型

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `astrbot_kb_name` | `""` | 可选，同步 / 检索 AstrBot 知识库 |
| `auto_llm_rewrite` | `false` | 切换人格时用模型重写提示词（会消耗模型额度） |

### QQ 资料同步

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `snowluma_http` | `""` | SnowLuma OneBot HTTP 地址，如 `http://snowluma:3000` |
| `snowluma_token` | `""` | access token（**secret**，界面与接口只回显 `••••••`），支持 Bearer 与 query 两种鉴权 |
| `nickname_sync_mode` | `profile` | `profile` / `group_card` / `hybrid` |
| `max_avatar_mb` | `5` | 头像大小上限（MB） |

> WebUI 人格抽屉会显示「上次推送到 QQ」「最近核对 QQ 资料」时间，并提供「立即同步到 QQ」按钮；插件**不会从 QQ 反向拉取**昵称或头像，QQ 侧被改动后可用 `/persona check` 查看差异、`/persona sync` 强制修复。

### 记忆

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `memory_scope` | `chat` | 新记忆默认作用域：`chat` 只在写入的会话可见；`persona` 该人格私聊通用 |
| `memory_chat_isolation` | `true` | 按会话隔离人格记忆；关闭后退回 0.3.0 行为 |

### 群聊门禁

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `group_mode` | `off` | `off` / `chat` / `admin` |
| `group_whitelist` | `[]` | 群号白名单，空表示不限制 |
| `require_at` | `true` | 群聊是否必须 @ 才响应 |
| `guest_wiki` | `read` | 群友可读共享 Wiki：`read` / `off` |
| `guest_rate_limit_per_minute` | `5` | 每位群友每分钟最多响应次数 |

### 数字生命卡

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `backup_dir` | `data/plugin_data/astrbot_plugin_persona_forge/life_card/backups` | 备份目录 |
| `request_dir` | `data/plugin_data/astrbot_plugin_persona_forge/life_card/requests` | 宿主 CLI 请求目录 |
| `result_dir` | `data/plugin_data/astrbot_plugin_persona_forge/life_card/results` | 宿主 CLI 结果目录 |
| `allow_restore` | `false` | 是否允许从 QQ / WebUI 发起恢复 |
| `max_card_mb` | `200` | 单张生命卡大小上限（MB） |

### 预留配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `image_search_enabled` | `true` | 自然语言找头像（指令模式下未启用） |
| `image_search_engine` | `baidu` | `baidu` / `bing` |
| `image_search_count` | `5` | 候选图片数量 |

## 📁 数据目录

```text
AstrBot/data/plugin_data/astrbot_plugin_persona_forge/
├── persona_profiles/
│   ├── profiles.json              # 人格资料（昵称 / 头像 / 提示词副本）
│   ├── runtime_state.json         # 运行态（WebUI 群聊设置、dev 模式、migrated_paths）
│   ├── avatars/<id>.<ext>         # 头像文件
│   ├── versions/<id>/v0001.json   # 版本快照 + 头像副本
│   ├── reviews/<id>.json          # 待审草稿 + unified diff
│   ├── dev/<id>.json              # 开发者模式教学笔记
│   └── exports/<id>-<ts>.md       # 导出的人格资料
├── persona_memory/
│   └── <id>.json                  # episodic / knowledge / session（含 scope、umo）
├── shared_wiki.json               # 共享 Wiki
└── life_card/
    ├── backups/life-card-*.pialife
    ├── requests/<id>.json
    └── results/<id>.json
```

> 整机迁移或备份建议直接打包上面这个目录；再配合 `/life backup plugin` 可以把每个人格单独导出成可恢复的生命卡。

## 🏗 项目结构

0.3.1 起后端拆成 `persona_forge/` 包，`main.py` 只负责组合 mixin 与修正 Handler 归属，方便按域维护：

| 文件 | 职责 |
| --- | --- |
| `main.py` | 插件入口：组合各 mixin 成 `PersonaForgePlugin`，无业务逻辑 |
| `persona_forge/config.py` | `PLUGIN_NAME` / `PLUGIN_VERSION` / 生命卡格式 / `LEGACY_DEFAULTS` 等常量 |
| `persona_forge/paths.py` | 数据目录解析、旧路径迁移判定、头像路径重写 |
| `persona_forge/memory_scope.py` | 记忆作用域与会话可见性规则 |
| `persona_forge/cards.py` | 生命卡命名与归档成员安全校验 |
| `persona_forge/dev.py` | 开发者模式提示词构建 |
| `persona_forge/text.py` | 密钥掩码、临时上下文标记 |
| `persona_forge/core.py` | 初始化、数据目录、版本快照 / 回滚、人格切换、提示词迭代与审核 |
| `persona_forge/persona_commands.py` | `/persona` 指令组 |
| `persona_forge/memory_cmds.py` | `/memory` 指令与记忆管理 |
| `persona_forge/knowledge.py` | 共享 Wiki、AstrBot 知识库与 `/wiki` 指令 |
| `persona_forge/dev_mode.py` | `/dev` 指令与教学模式 |
| `persona_forge/life_cards.py` | `/life` 指令与按人格生命卡 |
| `persona_forge/events.py` | 事件钩子：记忆注入、群聊门禁、生命周期 |
| `persona_forge/legacy_natural.py` | 0.2.x 自然语言流程（保留实现，默认不启用） |
| `persona_forge/api.py` | 插件视图 Web API（与 `window.AstrBotPluginView` 对接） |
| `persona_forge/handlers.py` | 拆包后把子模块 Handler 的归属改回入口模块，保证 AstrBot 能找到插件 |
| `views/persona-manager/` | 静态 WebUI（`index.html` + `css/` + `js/`，无构建链） |
| `tests/` | 纯标准库单测：AST 抽取纯函数后 `exec`，不 import AstrBot |

> `persona_forge/{paths,memory_scope,cards,dev,handlers}.py` 刻意不 import `astrbot`，单测与工具可以直接复用；`text.py` 里的 `mask_secret` 同样按 AST 方式测试。CI 会跑 `compileall`、单测矩阵、版本一致性以及 WebUI 的 JS 语法 / `innerHTML` 检查。

> **Handler 归属修正**：AstrBot 注册 Handler 时把 `handler_module_path` 记为函数所在模块，加载后按 `star_map[handler_module_path]` 找插件。拆包后 Handler 定义在 `persona_forge/*.py`，归属会变成 `...persona_forge.api` 这类子模块，导致 Handler 被跳过、指令失效。`persona_forge/handlers.py` 会在 `main.py` 导入完成时，把这些 Handler 的 `handler_module_path`、`handler_full_name` 与 `star_handlers_map` 键统一改回入口模块（不改变任何处理逻辑）。

<a name="migration"></a>

## 🧳 升级与迁移

从 0.3.0（或更早）升级到 0.3.1 时，数据目录统一收敛到 `data/plugin_data/astrbot_plugin_persona_forge/`。插件在启动时自动处理，无需手动搬文件：

1. 对六个位置（资料 / 记忆 / Wiki / 备份 / 请求 / 结果）逐个判断：**仅当旧路径存在、新路径尚不存在**时，把旧目录整体移动到新位置；
2. 迁移后重写 `profiles.json` 里指向旧根的**头像绝对路径**，保证头像仍然可见；
3. 迁移过的键记录在 `runtime_state.json` 的 `migrated_paths`，WebUI 设置中心「存储」分区可见；
4. 迁移失败（例如跨盘移动或权限问题）只写一条 warning，插件继续用新路径启动，不会覆盖任何旧数据。

升级前建议：

- 停掉 AstrBot，把 `AstrBot/data/` 下的 `persona_profiles/`、`persona_memory/`、`shared_wiki.json`、`life_card/` 复制一份备份；
- 0.3.0 的旧记忆没有 `umo` / `scope`，升级后在群聊默认不可见（私聊正常），需要时用 `/memory scope` 或 WebUI 调整；
- 原生 Persona 保存在 AstrBot 自己的数据库里，迁移不涉及数据库。

## 📄 License

[AGPL-3.0](LICENSE) © CingStars

本项目以 AGPL-3.0 授权：修改后再分发（包括作为网络服务提供）需要开放对应源码。

---

<div align="center">

**如果这个插件帮到了你，欢迎点个 ⭐ Star**

[仓库](https://github.com/CingStar/astrbot_plugin_persona_forge) · [问题反馈](https://github.com/CingStar/astrbot_plugin_persona_forge/issues) · [更新日志](CHANGELOG.md) · [AstrBot](https://github.com/AstrBotDevs/AstrBot)

</div>

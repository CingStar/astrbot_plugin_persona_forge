<div align="center">

<h1>人格工坊</h1>

**人格管理 · 三层记忆 · 共享 Wiki · 提示词版本与审查 · 数字生命卡**

让角色不只活在提示词里，而是有资料、有记忆、有版本、可回滚。

[![Version](https://img.shields.io/badge/version-0.3.0-6366F1?style=flat-square)](CHANGELOG.md)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0%20%3C6-0EA5E9?style=flat-square)](https://github.com/AstrBotDevs/AstrBot)
[![OneBot](https://img.shields.io/badge/platform-OneBot%20v11-2563EB?style=flat-square)](https://github.com/botuniverse/onebot)
[![Command First](https://img.shields.io/badge/mode-command--first-8B5CF6?style=flat-square)](#commands)
[![License](https://img.shields.io/badge/license-AGPL--3.0-22C55E?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-EC4899?style=flat-square)](https://github.com/CingStar/astrbot_plugin_persona_forge/pulls)

[快速开始](#quickstart) · [能力一览](#features) · [指令](#commands) · [配置](#config) · [更新日志](CHANGELOG.md)

</div>

> [!IMPORTANT]
> 更新插件代码后需要**重载插件或重启 AstrBot**，插件不会热加载。人格、记忆、Wiki 等数据本身是实时读写，不受影响。

---

<a name="features"></a>

## ✨ 能力一览

| 🎭 人格引擎 | 🧠 三层记忆 | 📚 共享知识库 |
| :---: | :---: | :---: |
| 创建 / 切换 / 退出 / 默认人格<br/>昵称与头像自动同步到 QQ | 经历、知识、会话快照分层<br/>按关键词注入，切换不串味 | 所有人格共用 Wiki<br/>可选同步 AstrBot 知识库 |

| 🧾 版本与审查 | 💳 数字生命卡 | 💻 WebUI 控制台 |
| :---: | :---: | :---: |
| 修改前自动快照，压缩草稿<br/>diff 审批，一键回滚 / 导出 | 冷 / 热备份与恢复任务<br/>交给宿主 `life-card-host` 执行 | 人格列表、版本、待审 diff<br/>群聊门禁可视化配置 |

<a name="quickstart"></a>

## 🚀 快速开始

### 环境要求

| 项目 | 要求 |
| --- | --- |
| 支持平台 | **OneBot v11（QQ）**，如 SnowLuma；其他 AstrBot 平台可用人格 / 记忆 / Wiki，但无法同步 QQ 昵称与头像 |
| AstrBot | `>= 4.0, < 6` |
| Python | 跟随 AstrBot 运行环境即可（CI 使用 3.12） |
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

重启 AstrBot，或在 WebUI → 插件管理 中重载插件。首次启动会自动创建数据目录（默认位于容器内 `/AstrBot/data/...`）。

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

一个人格 = **AstrBot 原生 Persona** + **插件扩展资料** + **私有记忆** + **版本快照**。

| 组成 | 存放位置 | 内容 |
| --- | --- | --- |
| 原生 Persona | AstrBot 数据库 | 系统提示词、工具、Skills、开场对话、错误文案 |
| 扩展资料 | `persona_profiles/profiles.json` | 昵称、头像、提示词副本、更新时间 |
| 私有记忆 | `persona_memory/<id>.json` | 经历 / 知识 / 会话快照 |
| 版本快照 | `persona_profiles/versions/<id>/` | 提示词、字段、资料与头像副本 |

### 人格指令

> 以下以 AstrBot 默认指令前缀 `/` 为例；指令前缀可在 AstrBot 配置中修改。

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
| `/persona iterate [id]` | 用该人格私有知识重建提示词 |
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

仅接受 `PNG` / `JPEG` / `WebP`，大小受 `max_avatar_mb` 限制（默认 5 MB）。

### 切换人格时发生了什么

1. 把当前会话的最后 20 条消息压缩成快照，写入当前人格的私有记忆（`kind=session`）；
2. 用私有知识迭代当前人格与目标人格的提示词（开启 `auto_llm_rewrite` 时改用模型重写）；
3. 通过 SnowLuma 同步 QQ 昵称 / 头像（或群名片，取决于 `nickname_sync_mode`）；
4. 将该人格设为 AstrBot 默认人格，并按 `persona:<id>` 标签恢复每个会话的专属对话；
5. 目标人格没有专属会话时新建一个，旧会话保留不删除。

> 切换是**全局**行为，会影响所有会话与群；目标人格必须已有昵称和头像，且 `default` 资料必须完整，否则切换会失败并保持原人格。

## 🧠 记忆系统

记忆按人格隔离，保存在 `persona_memory/<id>.json`，单个文件最多保留 500 条。

| 类型 | 写入方式 | 作用范围 | 典型用途 |
| --- | --- | --- | --- |
| `episodic` | `/memory add` | 当前人格私有 | 经历、偏好、约定、称呼 |
| `knowledge` | `/memory know` | 当前人格私有，可 promote | 世界观、设定、事实、口癖 |
| `session` | 切换人格时自动写入 | 当前人格私有 | 上一轮对话快照，切回时参考 |

### 记忆指令

| 指令 | 说明 |
| --- | --- |
| `/memory add 内容` | 写入经历 / 偏好记忆 |
| `/memory know 内容` | 写入知识型记忆（可同步到共享 Wiki） |
| `/memory list` | 查看当前人格记忆（最近 20 条） |
| `/memory forget 关键词` | 删除包含关键词的记忆 |
| `/memory clear` | 清空当前人格记忆 |
| `/memory promote [关键词]` | 把知识型记忆同步到共享 Wiki，可带关键词过滤 |

### 注入规则

- 从最近 200 条记忆中按关键词命中数取前 **8** 条注入；
- 额外注入该人格**最新一次会话快照**（截断 3000 字），切换回来也能接上上下文；
- 私有记忆**只对管理员 / owner 生效**，群友不会看到；
- 提示词迭代只取 `knowledge`，不会把共享 Wiki 混进某个人格。

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
- **审查**：`/persona review show <id>` 看 diff，`approve` 应用，`reject` 丢弃。

## 💳 数字生命卡

插件只负责**提交任务**与**查询结果**，真正的停服 / 打包 / 恢复由宿主上的 `life-card-host` 执行。

```text
插件写请求 ─▶ requests/<id>.json ─▶ life-card-host ─▶ results/<id>.json ─▶ 插件读结果
```

| 指令 | 说明 |
| --- | --- |
| `/life status` | 查看最近 5 条任务状态 |
| `/life list` | 列出备份文件（最多 20 个） |
| `/life backup all\|agent\|qq [hot]` | 提交备份请求；`hot` 为热备份 |
| `/life restore life-card-xxx.pialife confirm` | 提交恢复请求 |

- `scope`：`all` 全部、`agent` AstrBot 侧数据、`qq` QQ 侧数据（具体范围由 `life-card-host` 决定）；
- 恢复默认关闭，需要配置 `allow_restore: true` 才允许从 QQ 发起；
- 备份 / 恢复期间机器人可能短暂离线，完成后用 `/life status` 查看结果。

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

> 门禁设置可以在 WebUI 的「群聊」页直接改，会写入 `runtime_state.json`，优先级高于配置文件里的默认值。

## 💻 WebUI

插件详情页 → **人格工坊** 视图，包含三个标签页：

| 标签 | 内容 |
| --- | --- |
| 人格 | 人格列表、当前人格、切换、版本号 |
| 审查 | 待审草稿列表、diff 预览、批准 / 拒绝 |
| 群聊 | 群聊模式、群白名单、@ 开关、群友 Wiki、限速 |

- 依赖 AstrBot 支持插件 `views/`；旧版本 AstrBot 不显示视图时，指令功能不受影响；
- 群聊设置改动会立即生效，并持久化到 `runtime_state.json`。

<a name="config"></a>

## 🧩 配置

在 AstrBot 的插件配置页修改（对应 `_conf_schema.json`）。

### 存储

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `profile_dir` | `/AstrBot/data/persona_profiles` | 人格资料、头像、版本、审查草稿、导出 |
| `memory_dir` | `/AstrBot/data/persona_memory` | 人格私有记忆 |
| `wiki_file` | `/AstrBot/data/shared_wiki.json` | 共享 Wiki |
| `version_retention` | `20` | 每个人格保留的版本数 |

### 知识库与模型

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `astrbot_kb_name` | `""` | 可选，同步 / 检索 AstrBot 知识库 |
| `auto_llm_rewrite` | `false` | 切换人格时用模型重写提示词（会消耗模型额度） |

### QQ 资料同步

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `snowluma_http` | `""` | SnowLuma OneBot HTTP 地址，如 `http://snowluma:3000` |
| `snowluma_token` | `""` | access token，支持 Bearer 与 query 两种鉴权 |
| `nickname_sync_mode` | `profile` | `profile` / `group_card` / `hybrid` |
| `max_avatar_mb` | `5` | 头像大小上限（MB） |

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
| `backup_dir` | `/AstrBot/data/life_card/backups` | 备份目录 |
| `request_dir` | `/AstrBot/data/life_card/requests` | 宿主 CLI 请求目录 |
| `result_dir` | `/AstrBot/data/life_card/results` | 宿主 CLI 结果目录 |
| `allow_restore` | `false` | 是否允许从 QQ 发起恢复 |

### 预留配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `image_search_enabled` | `true` | 自然语言找头像（0.3.0 指令模式下未启用） |
| `image_search_engine` | `baidu` | `baidu` / `bing` |
| `image_search_count` | `5` | 候选图片数量 |

## 📁 数据目录

```text
AstrBot/data/
├── persona_profiles/
│   ├── profiles.json              # 人格资料（昵称 / 头像 / 提示词副本）
│   ├── runtime_state.json         # 运行态（WebUI 群聊设置等）
│   ├── avatars/<id>.<ext>         # 头像文件
│   ├── versions/<id>/v0001.json   # 版本快照 + 头像副本
│   ├── reviews/<id>.json          # 待审草稿 + unified diff
│   └── exports/<id>-<ts>.md       # 导出的人格资料
├── persona_memory/
│   └── <id>.json                  # episodic / knowledge / session
├── shared_wiki.json               # 共享 Wiki
└── life_card/
    ├── backups/life-card-*.pialife
    ├── requests/<id>.json
    └── results/<id>.json
```

> 迁移时复制 `profile_dir`、`memory_dir`、`wiki_file` 三处即可；原生 Persona 保存在 AstrBot 自己的数据库里，整机迁移建议直接备份 AstrBot 的 data 目录。

## 📄 License

[AGPL-3.0](LICENSE) © CingStars

本项目以 AGPL-3.0 授权：修改后再分发（包括作为网络服务提供）需要开放对应源码。

---

<div align="center">

**如果这个插件帮到了你，欢迎点个 ⭐ Star**

[仓库](https://github.com/CingStar/astrbot_plugin_persona_forge) · [问题反馈](https://github.com/CingStar/astrbot_plugin_persona_forge/issues) · [更新日志](CHANGELOG.md) · [AstrBot](https://github.com/AstrBotDevs/AstrBot)

</div>

# 人格助手（指令版）

AstrBot 人格管理、人格记忆、共享 Wiki、提示词版本与审查、数字生命卡。

**0.3.0 起改为指令优先，不再做自由文本意图识别。**

## 人格指令

别名：`/persona`、`/pp`

```text
/persona list
/persona view <id>
/persona create <id>                # 下一条发提示词，再发昵称
/persona create <id>|<昵称>|<提示词>
/persona update <id>                # 下一条发新提示词
/persona delete <id> confirm
/persona switch <id>
/persona use <id>
/persona exit
/persona default
/persona default <id>
/persona profile <id>
/persona fields <id> tools=all|none|a,b
/persona fields <id> skills=all|none|a,b
/persona fields <id> error=文本
/persona fields <id> dialogs=[...]
```

## 头像

```text
/persona avatar <id> get
/persona avatar <id> set            # 下一条发图片/URL/base64
/persona avatar <id> remove
```

支持 URL、本地文件、`base64://`、`data:image/...`。

## 版本 / 回滚 / 导出

```text
/persona versions <id>
/persona rollback <id> <version>
/persona export <id>
```

- 每次提示词/字段修改前自动快照；
- 默认保留 20 版；
- 导出 `.md` 文件。

## 提示词迭代 / 重写 / 压缩

```text
/persona iterate <id>               # 结构化追加人格专属知识
/persona rewrite <id>               # 模型重写，直接应用并记录版本
/persona compress <id> light|medium|extreme
```

压缩会生成草稿，进入审查：

```text
/persona review list
/persona review show <id>
/persona review approve <id>
/persona review reject <id>
```

## 核对 / 同步

```text
/persona check
/persona sync
```

## 记忆

```text
/memory add 内容
/memory know 知识内容
/memory list
/memory forget 关键词
/memory clear
/memory promote 关键词
```

- 记忆按人格隔离；
- `episodic` 默认，不共享；
- `knowledge` 可同步到共享 Wiki；
- `/memory promote` 只同步 knowledge。

## 共享 Wiki

```text
/wiki add 标题|内容
/wiki list
/wiki search 关键词
```

配置 `astrbot_kb_name` 后可同时检索/同步 AstrBot 知识库。

## 数字生命卡

```text
/life status
/life list
/life backup all|agent|qq [hot]
/life restore <archive.pialife> confirm
```

宿主机 `life-card-host` 负责真正停服/备份/恢复。

## 群聊模式

配置：

```json
{
  "group_mode": "off|chat|admin",
  "group_whitelist": [],
  "require_at": true,
  "guest_wiki": "read|off",
  "guest_rate_limit_per_minute": 5,
  "nickname_sync_mode": "profile|group_card|hybrid"
}
```

- `chat`：白名单群、@ 才响应，群友仅聊天；
- 管理指令仍只对 AstrBot 管理员开放；
- 群友不读私有记忆；
- 群名片同步用 `group_card`/`hybrid`。

## WebUI

插件详情页 → 人格助手视图：

- 人格列表 / 切换；
- 版本；
- 待审草稿 / diff / 批准拒绝；
- 群聊模式设置。

## 数据目录

```text
data/persona_profiles/profiles.json
data/persona_profiles/avatars/
data/persona_profiles/versions/<id>/
data/persona_profiles/reviews/
data/persona_profiles/exports/
data/persona_memory/<id>.json
data/shared_wiki.json
data/life_card/
```

## 注意

- 插件代码更新后必须重载/重启，不会自动热加载；
- WebUI 视图需要 AstrBot 支持插件 `views/`；
- 群名片同步依赖 SnowLuma/OneBot 的 `set_group_card`；
- 版本号：0.3.0。

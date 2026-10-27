# Changelog

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

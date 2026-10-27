"""人格工坊插件（Persona Forge）内部包。

保持 ``__init__`` 轻量：不要在这里 import 依赖 astrbot 的模块，
这样测试和工具可以直接导入 ``persona_forge.paths`` 等纯逻辑模块。

模块：
- ``config``          常量与插件元信息
- ``paths``           数据目录解析与迁移判定
- ``memory_scope``    记忆作用域与可见性
- ``cards``           生命卡命名与归档成员校验
- ``dev``             开发者模式提示词构建
- ``text``            文本/临时内容小工具
- ``core``            插件核心（初始化、版本、切换、Wiki、提示词迭代）
- ``persona_commands`` /persona 指令
- ``memory_cmds``     /memory 指令
- ``knowledge``       /wiki 指令
- ``dev_mode``        /dev 指令与教学模式
- ``life_cards``      /life 指令与按人格生命卡
- ``events``          事件钩子
- ``legacy_natural``  旧自然语言流程（保留但不再启用）
- ``api``             插件视图 Web API
"""

__all__ = [
    "config",
    "paths",
    "memory_scope",
    "cards",
    "dev",
    "handlers",
    "text",
    "core",
    "persona_commands",
    "memory_cmds",
    "knowledge",
    "dev_mode",
    "life_cards",
    "events",
    "legacy_natural",
    "api",
]

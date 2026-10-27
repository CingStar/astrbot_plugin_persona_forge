"""把拆分到子模块里的 Handler 归属回插件入口模块。

AstrBot 在注册 Handler 时把 ``handler_module_path`` 设为函数定义所在的模块
（``handler.__module__``），加载插件后又按 ``star_map[handler_module_path]``
查找插件元数据。逻辑拆到 ``persona_forge/`` 子包后，这个路径会变成
``...persona_forge.api`` 之类的子模块，导致 Handler 找不到归属而被跳过。

这里在入口模块导入完成后，把属于本包子模块的 Handler 统一改回入口模块路径，
不改变任何处理逻辑，只是修正注册元数据。
"""

from __future__ import annotations


def adopt_mixin_handlers(registry, entry_name: str, package_prefix: str) -> int:
    """Rewrite handler ownership so AstrBot finds the plugin entry module.

    Args:
        registry: ``star_handlers_registry`` like object (iterable + ``star_handlers_map``).
        entry_name: the plugin entry module path, e.g. ``data.plugins.x.main``.
        package_prefix: the plugin package path, e.g. ``data.plugins.x``.

    Returns:
        Number of handlers rewritten.
    """
    if not entry_name or not package_prefix or registry is None:
        return 0
    module_prefix = f"{package_prefix}.persona_forge"
    handlers_map = getattr(registry, "star_handlers_map", None)
    changed = 0
    for handler in list(registry):
        function = getattr(handler, "handler", None)
        module = str(getattr(function, "__module__", "") or "")
        if not module.startswith(module_prefix):
            continue
        old_name = getattr(handler, "handler_full_name", "")
        handler.handler_module_path = entry_name
        handler.handler_full_name = f"{entry_name}_{handler.handler_name}"
        if handlers_map is not None:
            if old_name:
                handlers_map.pop(old_name, None)
            handlers_map[handler.handler_full_name] = handler
        changed += 1
    return changed

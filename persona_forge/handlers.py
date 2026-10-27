"""把拆分到子模块里的 Handler 归属回插件入口模块。

AstrBot 在注册 Handler 时把 ``handler_module_path`` 设为函数定义所在的模块
（``handler.__module__``），加载插件后又按 ``star_map[handler_module_path]``
查找插件元数据。逻辑拆到 ``persona_forge/`` 子包后，这个路径会变成
``...persona_forge.api`` 之类的子模块，导致 Handler 找不到归属而被跳过。

这里在入口模块导入完成后，把属于本包子模块的 Handler 统一改回入口模块路径，
不改变任何处理逻辑，只是修正注册元数据。
"""

from __future__ import annotations


def collect_function_ids(classes) -> set[int]:
    """Collect ids of callables defined on the given classes (mixin MRO)."""
    ids: set[int] = set()
    for cls in classes or ():
        for value in vars(cls).values():
            if callable(value):
                ids.add(id(value))
    return ids


def adopt_mixin_handlers(
    registry, entry_name: str, package_prefix: str, function_ids=None
) -> int:
    """Rewrite handler ownership so AstrBot finds the plugin entry module.

    Args:
        registry: ``star_handlers_registry`` like object (iterable + ``star_handlers_map``).
        entry_name: the plugin entry module path, e.g. ``data.plugins.x.main``.
        package_prefix: the plugin package path, e.g. ``data.plugins.x``.
        function_ids: optional ids of the plugin's handler functions. When given,
            handlers are matched by identity instead of module name, which also
            covers loaders that import ``main.py`` as a top-level module.

    Returns:
        Number of handlers rewritten.
    """
    if not entry_name or registry is None:
        return 0
    prefixes = ["persona_forge"]
    if package_prefix:
        prefixes.insert(0, f"{package_prefix}.persona_forge")
    handlers_map = getattr(registry, "star_handlers_map", None)
    changed = 0
    for handler in list(registry):
        function = getattr(handler, "handler", None)
        module = str(getattr(function, "__module__", "") or "")
        by_id = function_ids is not None and id(function) in function_ids
        # Command-group roots are replaced by RegisteringCommandable objects in
        # the class namespace, so identity matching cannot see them; fall back
        # to the module prefix for those.
        by_module = any(
            module == prefix or module.startswith(prefix + ".")
            for prefix in prefixes
        )
        if not (by_id or by_module):
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

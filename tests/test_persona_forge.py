"""Unit tests for the pure helpers of the persona_forge plugin.

The plugin depends on ``astrbot`` (plus aiohttp / sqlalchemy) at import time,
which is not installed in CI.  The pure helpers therefore live in dependency
free modules under ``persona_forge/`` and these tests never import them:

1. parse a module file with :mod:`ast`,
2. keep only the whitelisted top-level function definitions,
3. ``exec`` them into a private namespace that provides the handful of names
   the functions reference (``Path`` plus a few stdlib modules), and
4. exercise the resulting callables.

``persona_forge/config.py`` is inspected the same way (literal constants only),
so the version-consistency check also works without AstrBot installed.
Everything here is standard library only and runs on ``python:3.10`` /
``python:3.12``.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = PLUGIN_ROOT / "main.py"
CONFIG_PY = PLUGIN_ROOT / "persona_forge" / "config.py"
METADATA_YAML = PLUGIN_ROOT / "metadata.yaml"

#: module path -> top-level helpers that are pure enough to test without AstrBot.
PURE_MODULES = {
    "persona_forge/paths.py": (
        "resolve_plugin_path",
        "should_migrate_path",
        "rewrite_avatar_path",
    ),
    "persona_forge/memory_scope.py": (
        "memory_default_scope",
        "memory_is_visible",
    ),
    "persona_forge/cards.py": (
        "card_filename",
        "is_safe_card_member",
    ),
    "persona_forge/dev.py": (
        "build_dev_rewrite_prompt",
    ),
    "persona_forge/text.py": (
        "mask_secret",
    ),
    "persona_forge/handlers.py": (
        "adopt_mixin_handlers",
    ),
}

#: Modules that must stay importable without AstrBot (tests rely on it).
ASTRBOT_FREE_MODULES = (
    "persona_forge/paths.py",
    "persona_forge/memory_scope.py",
    "persona_forge/cards.py",
    "persona_forge/dev.py",
    "persona_forge/handlers.py",
)

REQUIRED_FUNCTIONS = tuple(name for names in PURE_MODULES.values() for name in names)

#: Mirrors ``LEGACY_DEFAULTS`` in persona_forge/config.py (0.2.x/0.3.0 paths).
LEGACY_DEFAULT_VALUES = (
    "/AstrBot/data/persona_profiles",
    "/AstrBot/data/persona_memory",
    "/AstrBot/data/shared_wiki.json",
    "/AstrBot/data/life_card/backups",
    "/AstrBot/data/life_card/requests",
    "/AstrBot/data/life_card/results",
)

PLUGIN_DATA_DIR = "data/plugin_data/astrbot_plugin_persona_forge"


def read_module(rel_path: str) -> str:
    """Read a file below the plugin root, failing loudly if it moved."""
    path = PLUGIN_ROOT / rel_path
    if not path.is_file():
        raise AssertionError(
            f"{rel_path} is missing: the test suite expects the persona_forge "
            "package layout (see persona_forge/__init__.py)"
        )
    return path.read_text(encoding="utf-8")


def extract_functions(rel_path: str, names) -> dict:
    """Extract the named top-level functions from one module and exec them.

    The module itself is never imported, so missing AstrBot dependencies cannot
    break the test run.
    """
    source = read_module(rel_path)
    module = ast.parse(source, filename=str(PLUGIN_ROOT / rel_path))
    wanted = tuple(names)
    selected = [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    ]
    missing = sorted(set(wanted) - {node.name for node in selected})
    if missing:
        raise AssertionError(f"{rel_path} no longer defines these helpers: {missing}")

    namespace: dict = {
        "__name__": f"persona_forge_pure_{Path(rel_path).stem}",
        "__file__": str(PLUGIN_ROOT / rel_path),
        "Path": Path,
        "json": json,
        "re": re,
    }
    # Keep the module's "from __future__ import annotations" semantics so a
    # later annotation change cannot break the exec.
    future_import = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations")],
        level=0,
    )
    body = ast.Module(body=[future_import, *selected], type_ignores=[])
    ast.fix_missing_locations(body)
    exec(compile(body, str(PLUGIN_ROOT / rel_path), "exec"), namespace)  # noqa: S102
    return {name: namespace[name] for name in wanted}


def load_constant(rel_path: str, name: str):
    """Read a module-level literal constant without importing the module."""
    module = ast.parse(read_module(rel_path), filename=str(PLUGIN_ROOT / rel_path))
    for node in module.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"constant {name} not found in {rel_path}")


PURE: dict = {}
for _rel_path, _names in PURE_MODULES.items():
    PURE.update(extract_functions(_rel_path, _names))
del _rel_path, _names


class PackageLayoutTests(unittest.TestCase):
    """The package split is part of the contract: keep it where tests expect."""

    def test_pure_modules_exist(self):
        for rel_path in PURE_MODULES:
            with self.subTest(module=rel_path):
                self.assertTrue((PLUGIN_ROOT / rel_path).is_file())

    def test_all_required_helpers_are_extracted(self):
        self.assertEqual(set(PURE), set(REQUIRED_FUNCTIONS))
        for name in REQUIRED_FUNCTIONS:
            with self.subTest(name=name):
                self.assertTrue(callable(PURE[name]))

    def test_main_py_has_no_pure_helpers(self):
        # main.py must stay a thin entry point that only composes mixins.
        module = ast.parse(read_module("main.py"), filename=str(MAIN_PY))
        top_level = {
            node.name
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(top_level & set(REQUIRED_FUNCTIONS), set())

    def test_main_py_composes_mixins(self):
        module = ast.parse(read_module("main.py"), filename=str(MAIN_PY))
        classes = [node for node in module.body if isinstance(node, ast.ClassDef)]
        plugin = next((cls for cls in classes if cls.name == "PersonaForgePlugin"), None)
        self.assertIsNotNone(plugin, "PersonaForgePlugin not found in main.py")
        bases = [ast.unparse(base) for base in plugin.bases]
        self.assertIn("Star", bases)
        self.assertGreaterEqual(
            len([base for base in bases if base.endswith("Mixin")]), 3
        )

    def test_pure_modules_do_not_import_astrbot(self):
        # If this breaks, the AST-extraction approach has to be revisited.
        for rel_path in ASTRBOT_FREE_MODULES:
            module = ast.parse(read_module(rel_path), filename=str(PLUGIN_ROOT / rel_path))
            imported = []
            for node in module.body:
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            with self.subTest(module=rel_path):
                self.assertFalse(
                    [name for name in imported if name.split(".")[0] == "astrbot"],
                    f"{rel_path} must stay importable without AstrBot",
                )

    def test_legacy_defaults_match_config(self):
        defaults = load_constant("persona_forge/config.py", "LEGACY_DEFAULTS")
        self.assertEqual(tuple(defaults.values()), LEGACY_DEFAULT_VALUES)
        self.assertEqual(
            set(defaults),
            {"profile_dir", "memory_dir", "wiki_file", "backup_dir", "request_dir", "result_dir"},
        )


class VersionConsistencyTests(unittest.TestCase):
    """metadata.yaml and persona_forge/config.py must advertise the same version."""

    @staticmethod
    def plugin_version() -> str:
        text = read_module("persona_forge/config.py")
        match = re.search(r"^PLUGIN_VERSION\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if not match:
            raise AssertionError("PLUGIN_VERSION not found in persona_forge/config.py")
        return match.group(1)

    @staticmethod
    def metadata_version() -> str:
        text = METADATA_YAML.read_text(encoding="utf-8")
        match = re.search(r"^version:\s*[\"']?([^\"'\s]+)[\"']?\s*$", text, re.MULTILINE)
        if not match:
            raise AssertionError("version not found in metadata.yaml")
        return match.group(1)

    def test_plugin_version_is_semver(self):
        self.assertRegex(self.plugin_version(), r"^\d+\.\d+\.\d+$")

    def test_metadata_matches_plugin_version(self):
        self.assertEqual(self.metadata_version(), self.plugin_version())


class ResolvePluginPathTests(unittest.TestCase):
    """``resolve_plugin_path`` keeps every data file under plugin_data."""

    def setUp(self):
        self.root = PLUGIN_ROOT
        self.default = str(Path(self.root) / PLUGIN_DATA_DIR / "persona_profiles")

    def test_empty_value_uses_plugin_data_default(self):
        self.assertEqual(
            PURE["resolve_plugin_path"]("", "persona_profiles", self.root, LEGACY_DEFAULT_VALUES),
            self.default,
        )

    def test_none_value_uses_plugin_data_default(self):
        self.assertEqual(
            PURE["resolve_plugin_path"](None, "persona_profiles", self.root, LEGACY_DEFAULT_VALUES),
            self.default,
        )

    def test_whitespace_only_value_uses_plugin_data_default(self):
        self.assertEqual(
            PURE["resolve_plugin_path"]("   ", "persona_profiles", self.root, LEGACY_DEFAULT_VALUES),
            self.default,
        )

    def test_legacy_hard_coded_defaults_are_remapped(self):
        resolver = PURE["resolve_plugin_path"]
        expectations = {
            "/AstrBot/data/persona_profiles": "persona_profiles",
            "/AstrBot/data/persona_memory": "persona_memory",
            "/AstrBot/data/shared_wiki.json": "shared_wiki.json",
            "/AstrBot/data/life_card/backups": "life_card/backups",
            "/AstrBot/data/life_card/requests": "life_card/requests",
            "/AstrBot/data/life_card/results": "life_card/results",
        }
        for legacy, rel in expectations.items():
            with self.subTest(legacy=legacy):
                self.assertEqual(
                    resolver(legacy, rel, self.root, LEGACY_DEFAULT_VALUES),
                    str(Path(self.root) / PLUGIN_DATA_DIR / rel),
                )

    def test_absolute_path_is_kept_verbatim(self):
        absolute = str(Path(self.root) / "elsewhere" / "profiles.json")
        self.assertEqual(
            PURE["resolve_plugin_path"](
                absolute, "persona_profiles", self.root, LEGACY_DEFAULT_VALUES
            ),
            absolute,
        )

    def test_unrelated_legacy_looking_path_is_not_remapped(self):
        # Only the exact historical defaults are rewritten.
        absolute = str(Path(self.root) / "AstrBot" / "data" / "persona_profiles")
        self.assertEqual(
            PURE["resolve_plugin_path"](
                absolute, "persona_profiles", self.root, LEGACY_DEFAULT_VALUES
            ),
            absolute,
        )

    def test_relative_path_is_joined_onto_root(self):
        self.assertEqual(
            PURE["resolve_plugin_path"]("custom/avatars", "persona_profiles", self.root),
            str(Path(self.root) / "custom" / "avatars"),
        )

    def test_nested_default_relative_path(self):
        self.assertEqual(
            PURE["resolve_plugin_path"]("", "life_card/backups", self.root, LEGACY_DEFAULT_VALUES),
            str(Path(self.root) / PLUGIN_DATA_DIR / "life_card" / "backups"),
        )

    def test_result_is_always_a_string(self):
        result = PURE["resolve_plugin_path"](
            "", "shared_wiki.json", self.root, LEGACY_DEFAULT_VALUES
        )
        self.assertIsInstance(result, str)
        self.assertTrue(result.endswith("shared_wiki.json"))

    def test_empty_legacy_blacklist_still_remaps_empty_value(self):
        self.assertEqual(
            PURE["resolve_plugin_path"]("", "persona_memory", self.root, ()),
            str(Path(self.root) / PLUGIN_DATA_DIR / "persona_memory"),
        )


class ShouldMigratePathTests(unittest.TestCase):
    """Legacy data is moved only into a not-yet-existing new location."""

    def test_migrates_when_only_legacy_exists(self):
        self.assertTrue(
            PURE["should_migrate_path"]("/new/profiles", "/old/profiles", False, True)
        )

    def test_does_not_migrate_when_new_already_exists(self):
        self.assertFalse(
            PURE["should_migrate_path"]("/new/profiles", "/old/profiles", True, True)
        )

    def test_does_not_migrate_when_legacy_missing(self):
        self.assertFalse(
            PURE["should_migrate_path"]("/new/profiles", "/old/profiles", False, False)
        )

    def test_does_not_migrate_when_paths_are_identical(self):
        self.assertFalse(PURE["should_migrate_path"]("/same/path", "/same/path", False, True))

    def test_migrates_when_both_missing_is_false(self):
        self.assertFalse(PURE["should_migrate_path"]("/new/x", "/old/x", False, False))

    def test_accepts_path_objects(self):
        self.assertTrue(
            PURE["should_migrate_path"](Path("/new/x"), Path("/old/x"), False, True)
        )


class RewriteAvatarPathTests(unittest.TestCase):
    """Stored avatar paths are rebased after the data directory moved."""

    OLD_ROOT = "/AstrBot/data/persona_profiles"

    def setUp(self):
        self.new_root = str(Path(PLUGIN_ROOT) / PLUGIN_DATA_DIR / "persona_profiles")

    def test_path_under_old_root_is_rebased(self):
        old = f"{self.OLD_ROOT}/avatars/温言.png"
        result = PURE["rewrite_avatar_path"](old, self.OLD_ROOT, self.new_root)
        self.assertEqual(Path(result), Path(self.new_root) / "avatars" / "温言.png")

    def test_relative_subpath_is_preserved(self):
        old = f"{self.OLD_ROOT}/versions/温言/v0001.json"
        result = PURE["rewrite_avatar_path"](old, self.OLD_ROOT, self.new_root)
        self.assertEqual(
            Path(result), Path(self.new_root) / "versions" / "温言" / "v0001.json"
        )
        self.assertNotIn(self.OLD_ROOT, result)

    def test_path_outside_old_root_is_untouched(self):
        old = "/AstrBot/data/persona_memory/温言.json"
        self.assertEqual(PURE["rewrite_avatar_path"](old, self.OLD_ROOT, self.new_root), old)

    def test_sibling_prefix_is_not_treated_as_child(self):
        old = f"{self.OLD_ROOT}_backup/avatars/x.png"
        self.assertEqual(PURE["rewrite_avatar_path"](old, self.OLD_ROOT, self.new_root), old)

    def test_empty_value_is_returned_unchanged(self):
        self.assertEqual(PURE["rewrite_avatar_path"]("", self.OLD_ROOT, self.new_root), "")

    def test_none_value_becomes_empty_string(self):
        self.assertEqual(PURE["rewrite_avatar_path"](None, self.OLD_ROOT, self.new_root), "")

    def test_whitespace_value_is_returned_unchanged(self):
        self.assertEqual(PURE["rewrite_avatar_path"]("   ", self.OLD_ROOT, self.new_root), "")


class MemoryDefaultScopeTests(unittest.TestCase):
    """New memory items get a scope that matches their kind."""

    def test_knowledge_is_global(self):
        self.assertEqual(PURE["memory_default_scope"]("knowledge", "chat"), "global")
        self.assertEqual(PURE["memory_default_scope"]("knowledge", "persona"), "global")

    def test_session_is_chat(self):
        self.assertEqual(PURE["memory_default_scope"]("session", "persona"), "chat")

    def test_episodic_follows_configured_scope(self):
        self.assertEqual(PURE["memory_default_scope"]("episodic", "persona"), "persona")
        self.assertEqual(PURE["memory_default_scope"]("episodic", "chat"), "chat")

    def test_invalid_configured_scope_falls_back_to_chat(self):
        for configured in ("", "global", "bogus", "PERSONA", None):
            with self.subTest(configured=configured):
                self.assertEqual(PURE["memory_default_scope"]("episodic", configured), "chat")

    def test_unknown_kind_uses_configured_scope(self):
        self.assertEqual(PURE["memory_default_scope"]("misc", "persona"), "persona")

    def test_default_without_argument_is_chat(self):
        self.assertEqual(PURE["memory_default_scope"]("episodic", "chat"), "chat")


def memory_item(kind="episodic", scope=None, umo=""):
    """Build a memory record the way the plugin stores it."""
    item = {"kind": kind, "umo": umo}
    if scope is not None:
        item["scope"] = scope
    return item


class MemoryIsVisibleTests(unittest.TestCase):
    """Per-chat isolation rules for persona memory injection."""

    GROUP_A = "aiocqhttp:GroupMessage:111"
    GROUP_B = "aiocqhttp:GroupMessage:222"
    PRIVATE = "aiocqhttp:FriendMessage:333"

    def visible(self, item, umo, is_group, isolation=True):
        return PURE["memory_is_visible"](item, umo, is_group, isolation=isolation)

    def test_isolation_disabled_makes_everything_visible(self):
        cases = (
            (memory_item(kind="session", scope="chat", umo=self.GROUP_B), self.GROUP_A, True),
            (memory_item(scope="persona", umo=self.GROUP_B), self.GROUP_A, True),
            (memory_item(scope="chat", umo=self.PRIVATE), self.GROUP_A, True),
            (memory_item(kind="knowledge", umo=self.GROUP_B), self.GROUP_A, False),
        )
        for item, umo, is_group in cases:
            with self.subTest(item=item, umo=umo):
                self.assertTrue(self.visible(item, umo, is_group, isolation=False))

    def test_knowledge_is_visible_everywhere(self):
        item = memory_item(kind="knowledge", scope="global", umo=self.GROUP_B)
        self.assertTrue(self.visible(item, self.GROUP_A, True))
        self.assertTrue(self.visible(item, self.PRIVATE, False))

    def test_explicit_global_scope_is_visible_everywhere(self):
        item = memory_item(scope="global", umo=self.GROUP_B)
        self.assertTrue(self.visible(item, self.GROUP_A, True))
        self.assertTrue(self.visible(item, self.PRIVATE, False))

    def test_session_items_only_in_their_own_chat(self):
        item = memory_item(kind="session", scope="chat", umo=self.GROUP_A)
        self.assertTrue(self.visible(item, self.GROUP_A, True))
        self.assertFalse(self.visible(item, self.GROUP_B, True))
        self.assertFalse(self.visible(item, self.PRIVATE, False))

    def test_chat_scope_only_in_their_own_chat(self):
        item = memory_item(scope="chat", umo=self.GROUP_A)
        self.assertTrue(self.visible(item, self.GROUP_A, True))
        self.assertFalse(self.visible(item, self.GROUP_B, True))
        self.assertFalse(self.visible(item, self.PRIVATE, False))

    def test_persona_scope_is_shared_in_private_chats(self):
        item = memory_item(scope="persona", umo=self.PRIVATE)
        self.assertTrue(self.visible(item, self.PRIVATE, False))
        self.assertTrue(self.visible(item, self.GROUP_A, False))

    def test_persona_scope_in_groups_requires_same_group(self):
        item = memory_item(scope="persona", umo=self.GROUP_A)
        self.assertTrue(self.visible(item, self.GROUP_A, True))
        self.assertFalse(self.visible(item, self.GROUP_B, True))

    def test_legacy_items_without_scope_are_private_only_in_groups(self):
        legacy = memory_item(umo=self.GROUP_A)
        self.assertNotIn("scope", legacy)
        self.assertTrue(self.visible(legacy, self.GROUP_A, True))
        self.assertFalse(self.visible(legacy, self.GROUP_B, True))
        # Legacy items stay visible in private chats (0.3.0 behaviour).
        self.assertTrue(self.visible(legacy, self.PRIVATE, False))

    def test_legacy_item_without_umo_is_hidden_in_other_groups(self):
        legacy = memory_item()
        self.assertFalse(self.visible(legacy, self.GROUP_A, True))
        self.assertTrue(self.visible(legacy, self.PRIVATE, False))

    def test_three_way_group_isolation(self):
        in_a = memory_item(scope="persona", umo=self.GROUP_A)
        in_b = memory_item(scope="persona", umo=self.GROUP_B)
        self.assertTrue(self.visible(in_a, self.GROUP_A, True))
        self.assertFalse(self.visible(in_a, self.GROUP_B, True))
        self.assertTrue(self.visible(in_b, self.GROUP_B, True))
        self.assertFalse(self.visible(in_b, self.GROUP_A, True))
        # Private chat sees persona-scoped memory from any peer.
        self.assertTrue(self.visible(in_a, self.PRIVATE, False))
        self.assertTrue(self.visible(in_b, self.PRIVATE, False))

    def test_session_snapshot_from_group_is_invisible_in_other_group(self):
        snapshot = memory_item(kind="session", scope="chat", umo=self.GROUP_A)
        self.assertFalse(self.visible(snapshot, self.GROUP_B, True))
        self.assertFalse(self.visible(snapshot, self.PRIVATE, False))

    def test_unknown_scope_behaves_like_persona_scope(self):
        item = memory_item(scope="team", umo=self.GROUP_B)
        self.assertFalse(self.visible(item, self.GROUP_A, True))
        self.assertTrue(self.visible(item, self.PRIVATE, False))

    def test_item_umo_none_is_treated_as_empty(self):
        item = memory_item(kind="session", scope="chat")
        item["umo"] = None
        self.assertFalse(self.visible(item, self.GROUP_A, True))
        self.assertTrue(self.visible(item, "", True))


class MaskSecretTests(unittest.TestCase):
    """Secrets are never echoed back to the UI or logs."""

    def test_non_empty_secret_is_masked(self):
        self.assertEqual(PURE["mask_secret"]("super-secret-token"), "••••••")

    def test_mask_does_not_leak_any_prefix(self):
        masked = PURE["mask_secret"]("token-123456")
        self.assertNotIn("token", masked)
        self.assertNotIn("123456", masked)

    def test_empty_secret_stays_empty(self):
        self.assertEqual(PURE["mask_secret"](""), "")

    def test_none_and_whitespace_secret_stay_empty(self):
        self.assertEqual(PURE["mask_secret"](None), "")
        self.assertEqual(PURE["mask_secret"]("   "), "")

    def test_mask_strips_nothing_visible(self):
        self.assertEqual(PURE["mask_secret"]("  padded  "), "••••••")


class CardFilenameTests(unittest.TestCase):
    """Life-card file names encode their scope."""

    def test_persona_card_name(self):
        self.assertEqual(
            PURE["card_filename"]("persona", "温言", 1700000000),
            "life-card-persona-温言-1700000000.pialife",
        )

    def test_persona_card_requires_persona_id(self):
        self.assertEqual(
            PURE["card_filename"]("persona", "", 42), "life-card-shared-42.pialife"
        )

    def test_shared_card_name(self):
        self.assertEqual(PURE["card_filename"]("shared", "", 42), "life-card-shared-42.pialife")

    def test_unknown_scope_falls_back_to_shared(self):
        for scope in ("plugin", "all", "", None):
            with self.subTest(scope=scope):
                self.assertEqual(
                    PURE["card_filename"](scope, "温言", 7), "life-card-shared-7.pialife"
                )

    def test_timestamp_is_truncated_to_int(self):
        self.assertEqual(
            PURE["card_filename"]("shared", "", 1700000000.9),
            "life-card-shared-1700000000.pialife",
        )


class IsSafeCardMemberTests(unittest.TestCase):
    """Archive members must stay inside the restore target."""

    ALLOWED = (
        "manifest.json",
        "personas/温言.json",
        "profiles/profiles.json",
        "memory/温言.json",
        "avatars/温言.png",
        "versions/温言/v0001.json",
        "reviews/温言.json",
        "wiki/shared_wiki.json",
        "memory/nested/deeper.json",
    )
    REJECTED = (
        "",
        None,
        "/etc/passwd",
        "\\Windows\\system32\\config",
        "C:/Windows/win.ini",
        "personas/../../../etc/passwd",
        "personas/../profiles.json",
        "personas/./x.json",
        "personas//x.json",
        "personas/",
        "personas",
        "other/x.json",
        "manifest.json.bak",
        " manifest.json",
        "PERSONAS/x.json",
        "..",
        ".",
    )

    def test_allowed_members(self):
        for name in self.ALLOWED:
            with self.subTest(name=name):
                self.assertTrue(PURE["is_safe_card_member"](name))

    def test_rejected_members(self):
        for name in self.REJECTED:
            with self.subTest(name=name):
                self.assertFalse(PURE["is_safe_card_member"](name))

    def test_windows_separators_are_normalised(self):
        self.assertTrue(PURE["is_safe_card_member"]("personas\\温言.json"))

    def test_traversal_with_windows_separators_is_rejected(self):
        self.assertFalse(PURE["is_safe_card_member"]("personas\\..\\..\\evil.json"))


class FakeHandler:
    """Minimal stand-in for an AstrBot handler registry entry."""

    def __init__(self, module, name="handler"):
        def _func():
            """Handler callable; only __module__ matters here."""

        _func.__module__ = module
        self.handler = _func
        self.handler_name = name
        self.handler_module_path = module
        self.handler_full_name = f"{module}_{name}"


class FakeRegistry:
    """Minimal stand-in for ``star_handlers_registry``."""

    def __init__(self, handlers, handlers_map=None):
        self.handlers = list(handlers)
        self.star_handlers_map = handlers_map

    def __iter__(self):
        return iter(self.handlers)


class AdoptMixinHandlersTests(unittest.TestCase):
    """After the package split, handlers must stay owned by the entry module.

    AstrBot resolves a handler's plugin through
    ``star_map[handler.handler_module_path]``; without this rewrite every
    handler defined in ``persona_forge/*.py`` would be skipped.
    """

    ENTRY = "data.plugins.demo.main"
    PREFIX = "data.plugins.demo"
    SUBMODULE = f"{PREFIX}.persona_forge.api"

    def setUp(self):
        self.owned = FakeHandler(self.SUBMODULE, "api_personas")
        self.other_plugin = FakeHandler("data.plugins.other.main", "other_handler")
        self.sibling_module = FakeHandler(f"{self.PREFIX}.helpers", "helper")
        self.registry = FakeRegistry(
            [self.owned, self.other_plugin, self.sibling_module],
            {
                self.owned.handler_full_name: self.owned,
                self.other_plugin.handler_full_name: self.other_plugin,
                self.sibling_module.handler_full_name: self.sibling_module,
            },
        )

    def adopt(self, registry=None, entry=None, prefix=None):
        return PURE["adopt_mixin_handlers"](
            self.registry if registry is None else registry,
            self.ENTRY if entry is None else entry,
            self.PREFIX if prefix is None else prefix,
        )

    def test_rewrites_only_handlers_defined_in_the_persona_forge_package(self):
        changed = self.adopt()
        self.assertEqual(changed, 1)
        self.assertEqual(self.owned.handler_module_path, self.ENTRY)
        self.assertEqual(self.owned.handler_full_name, f"{self.ENTRY}_api_personas")
        self.assertNotEqual(self.other_plugin.handler_module_path, self.ENTRY)
        self.assertEqual(
            self.other_plugin.handler_full_name,
            "data.plugins.other.main_other_handler",
        )
        self.assertEqual(self.sibling_module.handler_module_path, f"{self.PREFIX}.helpers")

    def test_rewrites_registry_map_keys(self):
        old_key = self.owned.handler_full_name
        new_key = f"{self.ENTRY}_api_personas"
        self.adopt()
        self.assertNotIn(old_key, self.registry.star_handlers_map)
        self.assertIn(new_key, self.registry.star_handlers_map)
        self.assertIs(self.registry.star_handlers_map[new_key], self.owned)
        # Handlers of other plugins keep their existing map entries.
        self.assertIn(
            "data.plugins.other.main_other_handler", self.registry.star_handlers_map
        )

    def test_does_not_change_handler_identity_or_name(self):
        original_func = self.owned.handler
        self.adopt()
        self.assertIs(self.owned.handler, original_func)
        self.assertEqual(self.owned.handler_name, "api_personas")

    def test_rewrites_every_submodule_of_the_package(self):
        handlers = [
            FakeHandler(f"{self.PREFIX}.persona_forge.api", "a"),
            FakeHandler(f"{self.PREFIX}.persona_forge.core", "b"),
            FakeHandler(f"{self.PREFIX}.persona_forge.events", "c"),
            FakeHandler(f"{self.PREFIX}.persona_forge", "d"),
            FakeHandler("data.plugins.other.main", "e"),
        ]
        registry = FakeRegistry(handlers, {h.handler_full_name: h for h in handlers})
        changed = self.adopt(registry=registry)
        self.assertEqual(changed, 4)
        for handler in handlers[:4]:
            self.assertEqual(handler.handler_module_path, self.ENTRY)
        self.assertEqual(handlers[4].handler_module_path, "data.plugins.other.main")

    def test_missing_registry_map_is_tolerated(self):
        stale = FakeHandler(self.SUBMODULE, "stale")
        registry = FakeRegistry([stale], None)
        self.assertEqual(self.adopt(registry=registry), 1)
        self.assertEqual(stale.handler_module_path, self.ENTRY)
        self.assertEqual(stale.handler_full_name, f"{self.ENTRY}_stale")

    def test_empty_arguments_return_zero(self):
        adopt = PURE["adopt_mixin_handlers"]
        self.assertEqual(adopt(self.registry, "", self.PREFIX), 0)
        self.assertEqual(adopt(self.registry, self.ENTRY, ""), 0)
        self.assertEqual(adopt(None, self.ENTRY, self.PREFIX), 0)
        self.assertEqual(self.owned.handler_module_path, self.SUBMODULE)

    def test_unknown_registry_object_returns_zero(self):
        empty = FakeRegistry([], {})
        self.assertEqual(self.adopt(registry=empty), 0)

    def test_handler_without_module_metadata_is_skipped(self):
        def _func():
            """Plain callable without __module__ override."""

        anonymous = FakeHandler("", "anon")
        anonymous.handler = _func
        registry = FakeRegistry([anonymous], {anonymous.handler_full_name: anonymous})
        # A function's default __module__ is this test module, so nothing matches.
        self.assertEqual(self.adopt(registry=registry), 0)
        self.assertEqual(anonymous.handler_module_path, "")


class BuildDevRewritePromptTests(unittest.TestCase):
    """Developer-mode (teaching) prompt keeps every section distinct."""

    BASE = "你叫温言，说话轻声细语。"

    def build(self, knowledge=(), notes=()):
        return PURE["build_dev_rewrite_prompt"](self.BASE, knowledge, notes)

    def test_prompt_contains_base_persona(self):
        prompt = self.build(["设定一"], ["语气再轻一点"])
        self.assertIn(self.BASE, prompt)

    def test_sections_are_present_in_order(self):
        prompt = self.build(["设定一"], ["语气再轻一点"])
        base_at = prompt.index("【当前人格设定】")
        knowledge_at = prompt.index("【人格专属知识】")
        notes_at = prompt.index("【开发者教学笔记】")
        self.assertLess(base_at, knowledge_at)
        self.assertLess(knowledge_at, notes_at)

    def test_empty_knowledge_and_notes_render_placeholder(self):
        self.assertEqual(self.build().count("（无）"), 2)

    def test_empty_notes_only_renders_one_placeholder(self):
        prompt = self.build(["设定一"])
        self.assertEqual(prompt.count("（无）"), 1)
        self.assertIn("- 设定一", prompt)

    def test_empty_knowledge_only_renders_one_placeholder(self):
        prompt = self.build([], ["笔记一"])
        self.assertEqual(prompt.count("（无）"), 1)
        self.assertIn("- 笔记一", prompt)

    def test_items_are_bulleted(self):
        prompt = self.build(["知识A", "知识B"], ["笔记A"])
        for fragment in ("- 知识A", "- 知识B", "- 笔记A"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, prompt)

    def test_none_inputs_are_tolerated(self):
        prompt = PURE["build_dev_rewrite_prompt"](self.BASE, None, None)
        self.assertIn(self.BASE, prompt)
        self.assertEqual(prompt.count("（无）"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

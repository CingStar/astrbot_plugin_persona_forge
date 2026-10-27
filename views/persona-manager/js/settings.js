/**
 * 设置中心：左侧分区导航 + 右侧卡片表单。
 *
 * 分区：通用 / 群聊 / QQ 资料 / 知识库 / 生命卡 / 存储
 * 数据来源：
 *   GET  settings -> { version, config, runtime, paths, counts, migrated, token_configured }
 *   POST settings { config?, runtime? } -> { message, saved }
 */

import { endpoints } from "./api.js";
import {
  button,
  clear,
  el,
  emptyState,
  field,
  icon,
  input,
  kvCard,
  notice,
  replaceChildren,
  runBusy,
  select,
  skeletonPanel,
  textarea,
  toast,
  toggle,
} from "./dom.js";
import {
  clampInt,
  formatNumber,
  formatWhitelist,
  parseWhitelist,
  stableStringify,
  toBool,
  toNumber,
} from "./util.js";

const SECTIONS = [
  {
    key: "general",
    label: "通用",
    icon: "sliders",
    title: "通用设置",
    subtitle: "影响人格生成、头像与版本策略的基础选项。",
    fields: [
      {
        key: "auto_llm_rewrite",
        scope: "config",
        type: "bool",
        label: "切换人格时用模型重写提示词",
        hint: "开启后会消耗聊天模型额度；默认关闭，切换人格只做静态迁移。",
      },
      {
        key: "version_retention",
        scope: "config",
        type: "number",
        label: "每个人格保留的版本数",
        hint: "超过数量的旧快照会被自动清理（1 - 100）。",
        min: 1,
        max: 100,
      },
      {
        key: "image_search_enabled",
        scope: "config",
        type: "bool",
        label: "允许联网搜索头像",
        hint: "创建人格时可以用自然语言描述去找头像。",
      },
      {
        key: "image_search_engine",
        scope: "config",
        type: "select",
        label: "图片搜索源",
        options: [
          { value: "baidu", label: "百度（baidu）" },
          { value: "bing", label: "必应（bing）" },
        ],
      },
      {
        key: "image_search_count",
        scope: "config",
        type: "number",
        label: "每次返回的候选图片数",
        hint: "1 - 20，超出范围会自动收敛。",
        min: 1,
        max: 20,
      },
      {
        key: "max_avatar_mb",
        scope: "config",
        type: "number",
        label: "头像大小上限（MB）",
        hint: "超过上限的上传会被拒绝（1 - 50）。",
        min: 1,
        max: 50,
      },
      {
        key: "memory_scope",
        scope: "config",
        type: "select",
        label: "新记忆默认作用域",
        hint: "chat：只在写入的会话可见；persona：该人格私聊通用。",
        options: [
          { value: "chat", label: "本会话（chat）" },
          { value: "persona", label: "人格私聊（persona）" },
        ],
      },
      {
        key: "memory_chat_isolation",
        scope: "config",
        type: "bool",
        label: "按会话隔离人格记忆",
        hint: "关闭后退回 0.3.0 行为：同一人格的记忆在群聊之间共享。",
      },
    ],
  },
  {
    key: "group",
    label: "群聊",
    icon: "chat",
    title: "群聊",
    subtitle: "群聊触发与限流。这里的改动写入运行态 runtime_state.json，优先级高于配置文件默认值。",
    fields: [
      {
        key: "group_mode",
        scope: "runtime",
        type: "select",
        label: "群聊模式",
        hint: "off：不在群里响应；chat：按白名单响应；admin：仅管理员可用指令。",
        options: [
          { value: "off", label: "关闭（off）" },
          { value: "chat", label: "对话（chat）" },
          { value: "admin", label: "仅管理员（admin）" },
        ],
      },
      {
        key: "group_whitelist",
        scope: "runtime",
        type: "whitelist",
        label: "群号白名单",
        hint: "支持中英文逗号、空格、换行分隔；留空表示不限制。",
        placeholder: "123456789, 987654321",
      },
      {
        key: "require_at",
        scope: "runtime",
        type: "bool",
        label: "群聊必须 @ 才响应",
      },
      {
        key: "guest_wiki",
        scope: "runtime",
        type: "select",
        label: "群友可读共享知识库",
        hint: "off：群友提问时不注入共享 wiki。",
        options: [
          { value: "read", label: "允许读取（read）" },
          { value: "off", label: "关闭（off）" },
        ],
      },
      {
        key: "guest_rate_limit_per_minute",
        scope: "runtime",
        type: "number",
        label: "群友每分钟最多响应次数",
        hint: "owner 不受这个限制（1 - 100）。",
        min: 1,
        max: 100,
      },
    ],
  },
  {
    key: "qq",
    label: "QQ 资料",
    icon: "user",
    title: "QQ 资料",
    subtitle: "同步机器人 QQ 昵称 / 头像需要 SnowLuma OneBot HTTP 接口。",
    fields: [
      {
        key: "snowluma_http",
        scope: "config",
        type: "text",
        label: "SnowLuma OneBot HTTP 地址",
        hint: "例如 http://snowluma:3000 ，留空表示不启用 QQ 资料同步。",
        placeholder: "http://snowluma:3000",
      },
      {
        key: "snowluma_token",
        scope: "config",
        type: "password",
        label: "OneBot access token",
        hint: "只写不读：输入新值才会更新，留空保持原值。",
        placeholder: "留空表示不修改",
      },
      {
        key: "nickname_sync_mode",
        scope: "config",
        type: "select",
        label: "昵称同步策略",
        hint: "profile：QQ 资料昵称；group_card：群名片；hybrid：优先群名片再回退资料。",
        options: [
          { value: "profile", label: "QQ 资料（profile）" },
          { value: "group_card", label: "群名片（group_card）" },
          { value: "hybrid", label: "混合（hybrid）" },
        ],
      },
    ],
  },
  {
    key: "knowledge",
    label: "知识库",
    icon: "book",
    title: "知识库",
    subtitle: "共享 wiki 与 AstrBot 知识库的接入方式。所有人格共用同一份知识库。",
    fields: [
      {
        key: "astrbot_kb_name",
        scope: "config",
        type: "text",
        label: "AstrBot 知识库名称",
        hint: "填写后会把该知识库的检索结果作为补充上下文；留空表示只用插件内 wiki。",
        placeholder: "可选，例如 persona_wiki",
      },
    ],
  },
  {
    key: "life",
    label: "生命卡",
    icon: "heart",
    title: "生命卡",
    subtitle: "备份与恢复的开关和大小限制。",
    fields: [
      {
        key: "allow_restore",
        scope: "config",
        type: "bool",
        label: "允许从 WebUI / QQ 恢复生命卡",
        hint: "关闭时只能备份；恢复会用卡片覆盖当前人格资料与记忆，请谨慎开启。",
      },
      {
        key: "max_card_mb",
        scope: "config",
        type: "number",
        label: "单张生命卡大小上限（MB）",
        hint: "1 - 4096。",
        min: 1,
        max: 4096,
      },
    ],
  },
  {
    key: "storage",
    label: "存储",
    icon: "database",
    title: "存储",
    subtitle: "数据目录、数量统计与历史迁移记录。路径来自插件配置，相对路径基于 AstrBot 根目录。",
    fields: [],
  },
];

const COERCE = {
  bool: (value) => toBool(value, false),
  select: (value) => String(value ?? ""),
  text: (value) => String(value ?? "").trim(),
  password: (value) => String(value ?? "").trim(),
  whitelist: (value) => parseWhitelist(value),
  number: (value, def, fallback) =>
    clampInt(value, def.min, def.max, fallback === undefined ? 0 : fallback),
};

export function mount(app, root) {
  const page = el("section", { class: "page", id: "page-settings" });

  const sub = el("p", { class: "page-sub" }, "正在加载设置…");
  const head = el(
    "div",
    { class: "page-head" },
    el(
      "div",
      { class: "grow" },
      el("h1", { class: "page-title" }, icon("gear", { size: 24 }), "设置中心"),
      sub
    )
  );
  const actions = el("div", { class: "page-actions" });
  const reloadBtn = button({ icon: "refresh", label: "重新加载", onClick: () => load() });
  actions.appendChild(reloadBtn);
  head.appendChild(actions);

  const nav = el("nav", { class: "settings-nav", id: "settings-nav", attrs: { "aria-label": "设置分区" } });
  const sectionHost = el("div", { class: "settings-main", id: "settings-main" });
  const layout = el("div", { class: "settings-layout" }, nav, sectionHost);
  page.append(head, layout);
  root.appendChild(page);

  let destroyed = false;
  let payload = null;
  let activeKey = "general";
  let query = app.getSearch ? app.getSearch() : "";
  let draft = { config: {}, runtime: {} };
  let baseline = { config: {}, runtime: {} };

  async function load() {
    replaceChildren(sectionHost, skeletonPanel());
    clear(nav);
    if (sub) sub.textContent = "正在加载设置…";
    try {
      const data = await endpoints.settings();
      if (destroyed) return;
      payload = data && typeof data === "object" ? data : {};
      seedDraft(payload);
      if (app.clearError) app.clearError();
      if (app.setVersion && payload.version) app.setVersion(String(payload.version));
      if (app.refreshStatus) app.refreshStatus();
      renderNav();
      renderSection();
    } catch (err) {
      if (destroyed) return;
      payload = null;
      clear(nav);
      if (sub) sub.textContent = "加载失败";
      replaceChildren(
        sectionHost,
        emptyState({
          icon: "alert",
          title: "设置加载失败",
          hint: err && err.message ? err.message : "请稍后重试。",
          actions: [button({ icon: "refresh", label: "重试", variant: "primary", onClick: () => load() })],
        })
      );
      if (app.setError) app.setError(err && err.message ? err.message : "加载失败");
    }
  }

  function seedDraft(data) {
    const config = data.config && typeof data.config === "object" ? data.config : {};
    const runtime = data.runtime && typeof data.runtime === "object" ? data.runtime : {};
    draft = {
      config: { ...config, snowluma_token: "" },
      runtime: { ...runtime, group_whitelist: formatWhitelist(runtime.group_whitelist) },
    };
    baseline = collect();
  }

  function coerceField(def, raw, fallback) {
    const fn = COERCE[def.type] || COERCE.text;
    return fn(raw, def, fallback);
  }

  function collect() {
    const config = {};
    const runtime = {};
    for (const section of SECTIONS) {
      for (const def of section.fields) {
        if (!def.type) continue;
        const target = def.scope === "config" ? config : runtime;
        const raw = draft[def.scope] ? draft[def.scope][def.key] : undefined;
        if (def.type === "password") {
          const secret = String(raw ?? "").trim();
          if (secret) target[def.key] = secret;
          continue;
        }
        const fallback = baselineValue(def);
        target[def.key] = coerceField(def, raw, fallback);
      }
    }
    return { config, runtime };
  }

  function baselineValue(def) {
    if (!payload) return def.min !== undefined ? def.min : 0;
    const source = def.scope === "config" ? payload.config : payload.runtime;
    if (!source) return def.min !== undefined ? def.min : 0;
    if (def.type === "number") return toNumber(source[def.key], def.min ?? 0);
    return source[def.key];
  }

  function changedPayload() {
    const current = collect();
    const config = {};
    const runtime = {};
    for (const [key, value] of Object.entries(current.config)) {
      if (stableStringify(value) !== stableStringify(baseline.config[key])) config[key] = value;
    }
    for (const [key, value] of Object.entries(current.runtime)) {
      if (stableStringify(value) !== stableStringify(baseline.runtime[key])) runtime[key] = value;
    }
    return { config, runtime, count: Object.keys(config).length + Object.keys(runtime).length };
  }

  function updateDirtyState() {
    const state = document.getElementById("save-state");
    const saveBtn = document.getElementById("save-btn");
    if (!state) return;
    const changes = changedPayload();
    const dirty = changes.count > 0;
    state.classList.toggle("is-dirty", dirty);
    state.querySelector(".save-label").textContent = dirty
      ? `有 ${changes.count} 项未保存改动`
      : "已与服务端同步";
    if (saveBtn) saveBtn.disabled = !dirty;
  }

  function setValue(def, value) {
    if (!draft[def.scope]) draft[def.scope] = {};
    draft[def.scope][def.key] = value;
    updateDirtyState();
  }

  function visibleSections() {
    if (!query) return SECTIONS;
    return SECTIONS.filter((section) => sectionMatches(section));
  }

  function sectionMatches(section) {
    const hay = [
      section.label,
      section.title,
      section.subtitle,
      ...section.fields.map((def) => `${def.label} ${def.hint || ""} ${def.key}`),
    ].join(" ");
    return hay.toLowerCase().includes(query.toLowerCase());
  }

  function renderNav() {
    clear(nav);
    const visible = visibleSections();
    if (visible.length === 0) {
      nav.appendChild(el("p", { class: "drawer-note", style: { padding: "10px" } }, "没有匹配的分区。"));
      return;
    }
    if (!visible.some((section) => section.key === activeKey)) {
      activeKey = visible[0].key;
    }
    for (const section of visible) {
      const node = el(
        "button",
        {
          type: "button",
          class: "settings-nav-item",
          attrs: {
            "aria-current": String(section.key === activeKey),
            "data-key": section.key,
          },
        },
        icon(section.icon, { size: 17 }),
        el("span", null, section.label)
      );
      node.addEventListener("click", () => {
        if (activeKey === section.key) return;
        activeKey = section.key;
        renderNav();
        renderSection();
      });
      nav.appendChild(node);
    }
  }

  function renderSection() {
    const section = SECTIONS.find((item) => item.key === activeKey) || SECTIONS[0];
    const panel = el("section", { class: "settings-section" });
    panel.appendChild(
      el(
        "div",
        { class: "settings-section-head" },
        el(
          "div",
          null,
          el(
            "h2",
            { class: "settings-section-title" },
            icon(section.icon, { size: 18 }),
            el("span", null, section.title)
          ),
          el("p", { class: "settings-section-sub" }, section.subtitle)
        )
      )
    );

    if (section.fields.length > 0) {
      const grid = el("div", { class: "field-grid" });
      for (const def of section.fields) grid.appendChild(buildField(def));
      panel.appendChild(grid);
    }

    const extras = buildExtras(section.key);
    if (extras) panel.appendChild(extras);

    replaceChildren(sectionHost, panel, buildSaveBar());
    updateDirtyState();
    if (sub) {
      const counts = payload && payload.counts ? payload.counts : {};
      sub.textContent = `配置版本 ${payload && payload.version ? payload.version : "—"} · 人格 ${formatNumber(
        counts.personas
      )} · 记忆 ${formatNumber(counts.memory)} · 知识 ${formatNumber(counts.wiki)}`;
    }
  }

  function buildField(def) {
    const id = `setting-${def.scope}-${def.key}`;

    if (def.type === "bool") {
      const wrap = el("div", { class: "field is-wide" });
      wrap.appendChild(
        toggle({
          id,
          checked: toBool(draft[def.scope] ? draft[def.scope][def.key] : false, false),
          label: def.label,
          onChange: (event) => setValue(def, event.target.checked),
        })
      );
      if (def.hint) wrap.appendChild(el("p", { class: "field-hint" }, def.hint));
      return wrap;
    }

    let control;
    if (def.type === "select") {
      control = select({
        id,
        options: def.options || [],
        value: draft[def.scope] ? draft[def.scope][def.key] : "",
        onChange: (event) => setValue(def, event.target.value),
      });
    } else if (def.type === "number") {
      control = input({
        id,
        type: "number",
        value: String(draft[def.scope] && draft[def.scope][def.key] != null ? draft[def.scope][def.key] : ""),
        min: def.min,
        max: def.max,
        step: 1,
      });
      control.addEventListener("input", () => setValue(def, control.value));
      // 失焦时把显示值收敛到合法区间，保证「看到的」=「提交的」，也避免把空串发给后端
      control.addEventListener("change", () => {
        const clamped = clampInt(control.value, def.min, def.max, baselineValue(def));
        control.value = String(clamped);
        setValue(def, control.value);
      });
    } else if (def.type === "password") {
      control = input({
        id,
        type: "password",
        value: "",
        placeholder: def.placeholder || "留空表示不修改",
        autocomplete: "new-password",
      });
      control.addEventListener("input", () => setValue(def, control.value));
    } else if (def.type === "whitelist") {
      control = textarea({
        id,
        rows: 3,
        value: String(draft[def.scope] && draft[def.scope][def.key] != null ? draft[def.scope][def.key] : ""),
        placeholder: def.placeholder || "逗号 / 空格 / 换行分隔",
      });
      control.addEventListener("input", () => setValue(def, control.value));
    } else {
      control = input({
        id,
        value: String(draft[def.scope] && draft[def.scope][def.key] != null ? draft[def.scope][def.key] : ""),
        placeholder: def.placeholder || "",
      });
      control.addEventListener("input", () => setValue(def, control.value));
    }

    return field({
      id,
      label: def.label,
      hint: def.hint,
      wide: def.type === "whitelist" || def.type === "password",
      control,
    });
  }

  function buildSaveBar() {
    const state = el(
      "div",
      { class: "save-state", id: "save-state" },
      el("span", { class: "dot", attrs: { "aria-hidden": "true" } }),
      el("span", { class: "save-label" }, "已与服务端同步")
    );
    const saveActions = el("div", { class: "save-actions" });
    const resetBtn = button({
      icon: "refresh",
      label: "放弃更改",
      variant: "ghost",
      onClick: () => {
        if (payload) seedDraft(payload);
        renderSection();
        toast("已恢复为服务端当前值", "info");
      },
    });
    const saveBtn = button({
      id: "save-btn",
      icon: "save",
      label: "保存设置",
      variant: "primary",
      onClick: (event, btn) => save(btn),
    });
    saveActions.append(resetBtn, saveBtn);
    return el("div", { class: "save-bar" }, state, saveActions);
  }

  async function save(btn) {
    const changes = changedPayload();
    if (changes.count === 0) {
      toast("没有需要保存的改动", "info");
      return;
    }
    const request = {};
    if (Object.keys(changes.config).length > 0) request.config = changes.config;
    if (Object.keys(changes.runtime).length > 0) request.runtime = changes.runtime;

    await runBusy(
      btn,
      async () => {
        const result = await endpoints.saveSettings(request);
        toast((result && result.message) || "设置已保存", "success");
        await load();
        if (app.refreshStatus) await app.refreshStatus();
      },
      "保存设置失败"
    );
  }

  function buildExtras(key) {
    if (key === "qq") {
      const wrap = el("div", { class: "stack-sm" });
      const configured = Boolean(payload && payload.token_configured);
      wrap.appendChild(
        el(
          "div",
          { class: "token-state" + (configured ? "" : " is-missing") },
          el("span", { class: "dot", attrs: { "aria-hidden": "true" } }),
          el("span", null, configured ? "access token 已配置" : "access token 未配置")
        )
      );
      wrap.appendChild(
        notice(
          "QQ 昵称 / 头像同步会调用 OneBot 的 get_login_info 等接口；由于隐私原因，token 只写不读，页面上永远只显示掩码。",
          "info"
        )
      );
      return wrap;
    }

    if (key === "knowledge") {
      const wrap = el("div", { class: "stack-sm" });
      const paths = (payload && payload.paths) || {};
      const counts = (payload && payload.counts) || {};
      wrap.appendChild(
        el(
          "div",
          { class: "path-list" },
          pathRow("共享 wiki 文件", paths.wiki_file),
          pathRow("记忆目录", paths.memory_dir)
        )
      );
      wrap.appendChild(
        el(
          "div",
          { class: "kv-grid" },
          kvCard("共享知识条数", formatNumber(counts.wiki)),
          kvCard("当前人格记忆", formatNumber(counts.memory))
        )
      );
      wrap.appendChild(
        notice(
          "生命卡备份会包含共享 wiki；恢复共享卡会覆盖当前 wiki 内容。知识型记忆可以用人格抽屉里的「同步到共享库」显式提升。",
          "info"
        )
      );
      return wrap;
    }

    if (key === "life") {
      const wrap = el("div", { class: "stack-sm" });
      const allowRestore = Boolean(payload && payload.config && payload.config.allow_restore);
      wrap.appendChild(
        el(
          "div",
          { class: "token-state" + (allowRestore ? "" : " is-missing") },
          el("span", { class: "dot", attrs: { "aria-hidden": "true" } }),
          el("span", null, allowRestore ? "恢复功能已开启" : "恢复功能已关闭（只能备份）")
        )
      );
      wrap.appendChild(
        notice(
          "人格卡包含资料、提示词、记忆、版本与头像快照；共享卡只包含共享知识库；宿主卡由 life-card 宿主生成，插件不负责恢复。",
          "info"
        )
      );
      return wrap;
    }

    if (key === "storage") {
      const paths = (payload && payload.paths) || {};
      const counts = (payload && payload.counts) || {};
      const migrated = Array.isArray(payload && payload.migrated) ? payload.migrated : [];
      const wrap = el("div", { class: "stack" });

      const pluginSet = payload && payload.plugin_set;
      if (pluginSet && pluginSet.enabled === false) {
        wrap.appendChild(
          notice(
            `AstrBot 的 plugin_set 白名单没有包含本插件（当前：${(pluginSet.values || []).join(", ") || "空"}）。这会导致插件显示已加载，但所有指令、记忆注入和开发者模式都被静默跳过。请把 plugin_set 改为 ["*"] 或加入 astrbot_plugin_persona_forge，然后重启 AstrBot。`,
            "warn",
            "alert"
          )
        );
      }

      const countsGrid = el("div", { class: "kv-grid" });
      countsGrid.append(
        kvCard("人格", formatNumber(counts.personas)),
        kvCard("当前人格记忆", formatNumber(counts.memory)),
        kvCard("共享知识", formatNumber(counts.wiki)),
        kvCard("待审草稿", formatNumber(counts.reviews)),
        kvCard("生命卡", formatNumber(counts.life_cards))
      );
      wrap.appendChild(countsGrid);

      const pathList = el("div", { class: "path-list" });
      const pathKeys = [
        ["人格资料目录", paths.profile_dir],
        ["记忆目录", paths.memory_dir],
        ["共享 wiki 文件", paths.wiki_file],
        ["生命卡备份目录", paths.backup_dir],
        ["宿主请求目录", paths.request_dir],
        ["宿主结果目录", paths.result_dir],
      ];
      for (const [label, value] of pathKeys) pathList.appendChild(pathRow(label, value));
      wrap.appendChild(pathList);

      wrap.appendChild(
        el(
          "div",
          { class: "stack-sm" },
          el("span", { class: "field-label" }, "旧数据自动迁移记录"),
          migrated.length > 0
            ? el(
                "div",
                { class: "row-wrap" },
                ...migrated.map((item) => el("span", { class: "chip" }, String(item)))
              )
            : el("p", { class: "drawer-note" }, "没有发生迁移。0.3.1 会把旧的 persona_profiles / persona_memory 等目录搬到 data/plugin_data/astrbot_plugin_persona_forge/ 下。")
        )
      );

      wrap.appendChild(
        notice(
          "修改数据目录请编辑插件配置（_conf_schema.json 对应的配置项）后重载插件；这里只做只读展示，避免路径不一致导致数据分裂。",
          "info"
        )
      );
      return wrap;
    }

    return null;
  }

  function pathRow(label, value) {
    return el(
      "div",
      { class: "path-item" },
      el("span", { class: "path-label" }, label),
      el("span", { class: "path-value" }, value || "—")
    );
  }

  function setSearch(value) {
    query = String(value ?? "");
    renderNav();
    if (payload) renderSection();
  }

  load();

  return {
    destroy() {
      destroyed = true;
    },
    setSearch,
    reload() {
      return load();
    },
  };
}

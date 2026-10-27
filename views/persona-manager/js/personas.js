/**
 * 人格页：卡片网格 + 搜索 + 当前人格 + 提炼入口
 * 右侧人格抽屉：资料 / 提示词 / 记忆 / 开发者模式 / 高级 / 版本
 *
 * 全部 DOM 由 createElement / <template>.cloneNode + textContent 构建，
 * 不注入任何 HTML 字符串。
 */

import { endpoints } from "./api.js";
import {
  badge,
  button,
  clear,
  cloneFirst,
  confirmDialog,
  diffView,
  el,
  emptyState,
  fillAvatar,
  icon,
  iconButton,
  input,
  isImageDataUrl,
  notice,
  pushEscapeHandler,
  qs,
  replaceChildren,
  runBusy,
  skeletonCards,
  textarea,
  toast,
  toggle,
  trapFocus,
} from "./dom.js";
import {
  formatNumber,
  formatTime,
  formatWhitelist,
  initials,
  matchesQuery,
  normalizePersona,
  splitList,
  timeAgo,
  toNumber,
  truncate,
  uniqueId,
} from "./util.js";

/* ============================== 人格页 ============================== */

let pageContext = null;

export function mount(app, root) {
  const page = el("section", { class: "page", id: "page-personas" });

  const sub = el("p", { class: "page-sub", id: "personas-sub" }, "正在加载…");
  const head = el(
    "div",
    { class: "page-head" },
    el(
      "div",
      { class: "grow" },
      el("h1", { class: "page-title" }, icon("users", { size: 24 }), "人格"),
      sub
    )
  );

  const actions = el("div", { class: "page-actions" });
  const refreshBtn = button({
    icon: "refresh",
    label: "刷新",
    onClick: () => load({ force: true }),
  });
  actions.appendChild(refreshBtn);
  head.appendChild(actions);

  const gridHost = el("div", { id: "personas-grid" });
  page.append(head, gridHost);
  root.appendChild(page);

  let destroyed = false;
  let renderedData = null;

  const unsubscribe = app.onPersonasChanged((data) => {
    if (destroyed || !data) return;
    if (data !== renderedData) renderGrid(data);
  });

  pageContext = {
    app,
    query: app.getSearch ? app.getSearch() : "",
    setSearch: (value) => setSearch(value),
    reload: () => load({ force: true }),
  };

  function setSearch(value) {
    pageContext.query = String(value ?? "");
    const data = renderedData || { personas: [], active: "", version: "" };
    renderGrid(data);
  }

  function renderGrid(data) {
    renderedData = data;
    const personas = Array.isArray(data.personas) ? data.personas : [];
    const activeId = String(data.active || "");
    const activePersona = personas.find((item) => item.id === activeId);
    const query = pageContext.query || "";

    const filtered = query
      ? personas.filter((item) => matchesQuery(`${item.nickname} ${item.id}`, query))
      : personas;

    clear(gridHost);
    sub.textContent = buildSubtitle(personas.length, activePersona, query, filtered.length);

    if (filtered.length === 0) {
      gridHost.appendChild(
        personas.length === 0
          ? emptyState({
              icon: "users",
              title: "还没有可管理的人格",
              hint: "在 AstrBot 的人格管理里创建人格后，回到这里点击刷新即可看到卡片。也可以先用指令 /persona list 检查当前状态。",
              actions: [
                button({
                  icon: "refresh",
                  label: "重新加载",
                  variant: "primary",
                  onClick: () => load({ force: true }),
                }),
              ],
            })
          : emptyState({
              icon: "search",
              title: "没有匹配的人格",
              hint: `没有找到包含「${query}」的人格，换个关键词试试。`,
              actions: [
                button({
                  icon: "x",
                  label: "清除搜索",
                  onClick: () => {
                    if (pageContext.app.clearSearch) pageContext.app.clearSearch();
                  },
                }),
              ],
            })
      );
      return;
    }

    const grid = el("div", { class: "grid-cards", id: "persona-grid" });
    for (const persona of filtered) {
      grid.appendChild(buildPersonaCard(app, persona, activeId));
    }
    gridHost.appendChild(grid);
  }

  async function load(options = {}) {
    clear(gridHost);
    gridHost.appendChild(skeletonCards(6));
    sub.textContent = "正在加载人格…";
    try {
      const data = await app.loadPersonas({ force: options.force === true });
      if (destroyed) return;
      hidePageError();
      if (data !== renderedData) renderGrid(data);
    } catch (err) {
      if (destroyed) return;
      renderError(err);
    }
  }

  function renderError(err) {
    renderedData = null;
    clear(gridHost);
    gridHost.appendChild(
      emptyState({
        icon: "alert",
        title: "人格列表加载失败",
        hint: err && err.message ? err.message : "请确认插件已加载，然后重试。",
        actions: [
          button({
            icon: "refresh",
            label: "重试",
            variant: "primary",
            onClick: () => load({ force: true }),
          }),
        ],
      })
    );
    sub.textContent = "加载失败";
    if (app.setError) app.setError(err && err.message ? err.message : "加载失败");
  }

  function hidePageError() {
    if (app.clearError) app.clearError();
  }

  load();

  return {
    destroy() {
      destroyed = true;
      unsubscribe();
      if (pageContext) pageContext = null;
    },
    setSearch,
    reload() {
      return load({ force: true });
    },
  };
}

function buildSubtitle(total, activePersona, query, filteredCount) {
  if (total === 0) return "共 0 张人格";
  const parts = [`共 ${formatNumber(total)} 张人格`];
  if (activePersona) parts.push(`当前：${activePersona.nickname}`);
  else parts.push("当前人格未设置");
  if (query) parts.push(`筛选出 ${filteredCount} 张`);
  return parts.join(" · ");
}

function buildPersonaCard(app, persona, activeId) {
  const node = cloneFirst("tpl-persona-card");
  const isActive = activeId ? persona.id === activeId : persona.active;
  node.classList.toggle("is-active", isActive);
  node.setAttribute("aria-label", `打开人格 ${persona.nickname}（${persona.id}）`);

  const img = qs(".avatar-img", node);
  const fallback = qs(".avatar-fallback", node);
  const activeDot = qs(".card-active-dot", node);
  if (isImageDataUrl(persona.avatarData)) {
    img.src = persona.avatarData;
    img.hidden = false;
    fallback.hidden = true;
  } else {
    img.hidden = true;
    fallback.hidden = false;
    fallback.textContent = initials(persona.nickname);
  }
  if (activeDot) activeDot.hidden = !isActive;

  qs(".card-name", node).textContent = persona.nickname;
  qs(".card-id", node).textContent = persona.id;

  const badges = qs(".card-badges", node);
  if (isActive) badges.appendChild(badge("当前人格"));
  if (persona.isDefault) badges.appendChild(badge("默认", "muted"));
  badges.appendChild(
    persona.ready ? badge("资料就绪", "success") : badge("待完善", "warn")
  );

  const meta = qs(".card-meta", node);
  const metaItems = [
    `版本 v${toNumber(persona.version, 0)}`,
    `记忆 ${formatNumber(persona.memoryCount)} 条`,
    persona.updatedAt ? `更新于 ${timeAgo(persona.updatedAt)}` : "尚未更新",
  ];
  metaItems.forEach((text, index) => {
    if (index > 0) meta.appendChild(el("span", { class: "sep", attrs: { "aria-hidden": "true" } }));
    meta.appendChild(el("span", null, text));
  });

  const foot = qs(".card-foot", node);
  if (isActive) {
    foot.appendChild(
      el(
        "span",
        { class: "chip is-active" },
        icon("review", { size: 14 }),
        el("span", null, "当前使用中")
      )
    );
  } else {
    const switchBtn = button({
      size: "sm",
      variant: "primary",
      icon: "arrow-right",
      label: "设为当前",
      onClick: async (event, btn) => {
        event.stopPropagation();
        await runBusy(
          btn,
          async () => {
            const result = await endpoints.switchPersona(persona.id);
            toast(result.message || `已切换到 ${persona.nickname}`, "success");
            await app.loadPersonas({ force: true });
          },
          "切换人格失败"
        );
      },
    });
    foot.appendChild(switchBtn);
  }
  foot.appendChild(
    button({
      size: "sm",
      variant: "ghost",
      icon: "pencil",
      label: "详情",
      onClick: (event) => {
        event.stopPropagation();
        openPersona(app, persona.id);
      },
    })
  );

  const open = () => openPersona(app, persona.id);
  node.addEventListener("click", open);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });

  return node;
}

/* ============================== 人格抽屉 ============================== */

const TABS = [
  { key: "profile", label: "资料", icon: "user" },
  { key: "prompt", label: "提示词", icon: "pencil" },
  { key: "memory", label: "记忆", icon: "layers" },
  { key: "dev", label: "开发者模式", icon: "terminal" },
  { key: "advanced", label: "高级", icon: "sliders" },
  { key: "versions", label: "版本", icon: "history" },
];

const KIND_LABELS = { knowledge: "知识", session: "会话", episodic: "片段" };
const SCOPE_LABELS = { global: "全局", chat: "本会话", persona: "人格私聊" };
const SCOPE_OPTIONS = [
  { value: "chat", label: "本会话（chat）" },
  { value: "persona", label: "人格私聊（persona）" },
];

let drawer = null;

function refs() {
  return {
    scrim: document.getElementById("drawer-scrim"),
    drawerNode: document.getElementById("drawer"),
    head: document.getElementById("drawer-head"),
    tabs: document.getElementById("drawer-tabs"),
    body: document.getElementById("drawer-body"),
  };
}

export function isDrawerOpen() {
  return Boolean(drawer);
}

/** 打开指定人格的详情抽屉。 */
export async function openPersona(app, pid) {
  const id = String(pid || "").trim();
  if (!id) return;
  if (drawer && drawer.pid === id) {
    focusActiveTab();
    return;
  }

  const { scrim, drawerNode, head, tabs, body } = refs();
  if (!drawerNode) return;

  if (drawer) closePersona({ restoreFocus: false });

  drawer = {
    app,
    pid: id,
    persona: null,
    tab: "profile",
    token: 0,
    trigger: document.activeElement,
    releases: [],
  };
  const current = drawer;

  current.releases.push(trapFocus(drawerNode));
  current.releases.push(pushEscapeHandler(() => closePersona()));

  ensureScrimHandler();
  document.body.style.setProperty("overflow", "hidden");
  if (scrim) scrim.hidden = false;
  drawerNode.hidden = false;

  replaceChildren(
    head,
    el(
      "div",
      { class: "drawer-ident" },
      el("span", { class: "spinner", attrs: { "aria-hidden": "true" } }),
      el("span", { class: "muted" }, "正在加载人格资料…")
    )
  );
  clear(tabs);
  replaceChildren(body, loadingPanel());
  body.scrollTop = 0;

  try {
    const detail = await endpoints.persona(id);
    if (drawer !== current) return;
    current.persona = normalizeDetail(detail);
    renderShell();
    await renderTab();
    focusActiveTab();
  } catch (err) {
    if (drawer !== current) return;
    renderDrawerError(err);
  }
}

/** 关闭抽屉。 */
export function closePersona(options = {}) {
  const current = drawer;
  if (!current) return;
  const { scrim, drawerNode, head, tabs, body } = refs();

  for (const release of current.releases) {
    try {
      release();
    } catch {
      /* ignore */
    }
  }
  current.releases = [];
  document.body.style.removeProperty("overflow");
  if (scrim) scrim.hidden = true;
  if (drawerNode) drawerNode.hidden = true;
  if (head) clear(head);
  if (tabs) clear(tabs);
  if (body) clear(body);
  drawer = null;

  if (options.restoreFocus !== false && current.trigger) {
    const trigger = current.trigger;
    if (trigger.isConnected && typeof trigger.focus === "function") {
      try {
        trigger.focus({ preventScroll: true });
      } catch {
        /* ignore */
      }
    }
  }
}

function ensureScrimHandler() {
  const { scrim } = refs();
  if (!scrim || scrim.dataset.bound === "1") return;
  scrim.dataset.bound = "1";
  scrim.addEventListener("click", () => closePersona());
}

function focusActiveTab() {
  const { tabs } = refs();
  if (!tabs) return;
  const active = tabs.querySelector('[aria-selected="true"]');
  if (active) {
    try {
      active.focus({ preventScroll: true });
    } catch {
      /* ignore */
    }
  }
}

function loadingPanel() {
  return el(
    "div",
    { class: "tab-panel" },
    el("div", { class: "skeleton-card" }, el("div", { class: "sk sk-avatar" }),
      el("div", { class: "sk-lines" },
        el("div", { class: "sk sk-line", style: { width: "58%" } }),
        el("div", { class: "sk sk-line", style: { width: "82%" } }),
        el("div", { class: "sk sk-line", style: { width: "44%" } })
      )
    )
  );
}

function normalizeDetail(raw) {
  const item = raw && typeof raw === "object" ? raw : {};
  const id = String(item.id ?? item.persona_id ?? "").trim();
  return {
    id,
    nickname: String(item.nickname ?? "").trim() || id || "未命名人格",
    avatarData: typeof item.avatar_data === "string" ? item.avatar_data : "",
    avatarSha: String(item.avatar_sha ?? item.avatar_sha256 ?? ""),
    avatarTooLarge: item.avatar_too_large === true,
    prompt: typeof item.prompt === "string" ? item.prompt : "",
    promptBase: typeof item.prompt_base === "string" ? item.prompt_base : "",
    promptVersion: toNumber(item.prompt_version ?? item.version, 0),
    updatedAt: item.updated_at ?? null,
    tools: normalizeListField(item.tools),
    skills: normalizeListField(item.skills),
    beginDialogs: Array.isArray(item.begin_dialogs) ? item.begin_dialogs : [],
    memoryCount: toNumber(item.memory_count, 0),
    versionCount: toNumber(item.version_count, 0),
    dev: item.dev && typeof item.dev === "object" ? item.dev : null,
    qq: item.qq && typeof item.qq === "object" ? item.qq : null,
  };
}

/** tools / skills 可能是 null（表示全部）、数组或逗号文本。 */
function normalizeListField(value) {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (typeof value === "string") return splitList(value);
  return null;
}

function listFieldText(value) {
  if (value === null) return "all";
  if (Array.isArray(value) && value.length === 0) return "none";
  return formatWhitelist(value || []);
}

function listFieldValue(text) {
  const raw = String(text ?? "").trim();
  if (!raw || /^(all|全部)$/i.test(raw)) return "all";
  if (/^(none|无)$/i.test(raw)) return "none";
  return splitList(raw);
}

/* ---------------------------- 抽屉外壳 ---------------------------- */

function renderShell() {
  const current = drawer;
  if (!current || !current.persona) return;
  const { head, tabs } = refs();
  if (!head || !tabs) return;

  const persona = current.persona;
  const activeId = current.app.getActivePid ? current.app.getActivePid() : "";
  const isActive = Boolean(activeId) && activeId === current.pid;

  const avatarHost = el("div", { class: "drawer-avatar" });
  fillAvatar(avatarHost, persona.avatarData, persona.nickname);

  const title = el("h2", { class: "drawer-title", id: "drawer-title" });
  title.appendChild(el("span", { class: "drawer-title-text" }, persona.nickname));
  if (isActive) title.appendChild(badge("当前人格"));
  if (persona.promptVersion > 0) title.appendChild(badge(`v${persona.promptVersion}`, "muted"));

  const subline = el(
    "div",
    { class: "drawer-sub" },
    el("span", { class: "mono" }, persona.id),
    el("span", { class: "sep" }, "·"),
    el("span", null, `资料更新于 ${formatTime(persona.updatedAt)}`)
  );

  const actions = el("div", { class: "drawer-head-actions" });
  if (!isActive) {
    actions.appendChild(
      button({
        size: "sm",
        variant: "primary",
        icon: "arrow-right",
        label: "设为当前",
        onClick: (event, btn) => switchPersona(current, btn),
      })
    );
  }
  actions.appendChild(
    iconButton({ icon: "x", title: "关闭人格详情", onClick: () => closePersona() })
  );

  replaceChildren(
    head,
    avatarHost,
    el("div", { class: "drawer-ident" }, title, subline),
    actions
  );

  replaceChildren(tabs);
  for (const tab of TABS) {
    const selected = tab.key === current.tab;
    const tabBtn = el(
      "button",
      {
        type: "button",
        class: "drawer-tab",
        id: `drawer-tab-${tab.key}`,
        attrs: {
          role: "tab",
          "aria-selected": String(selected),
          "aria-controls": `drawer-panel-${tab.key}`,
          tabindex: selected ? "0" : "-1",
        },
      },
      tab.label
    );
    tabBtn.addEventListener("click", () => switchTab(tab.key));
    tabBtn.addEventListener("keydown", (event) => onTabKeydown(event, tab.key));
    tabs.appendChild(tabBtn);
  }
}

function onTabKeydown(event, key) {
  const index = TABS.findIndex((tab) => tab.key === key);
  if (index < 0) return;
  let nextIndex = index;
  if (event.key === "ArrowRight") nextIndex = (index + 1) % TABS.length;
  else if (event.key === "ArrowLeft") nextIndex = (index - 1 + TABS.length) % TABS.length;
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = TABS.length - 1;
  else return;
  event.preventDefault();
  const next = TABS[nextIndex];
  switchTab(next.key);
  const { tabs } = refs();
  const node = tabs ? tabs.querySelector(`#drawer-tab-${next.key}`) : null;
  if (node) node.focus();
}

function switchTab(key) {
  const current = drawer;
  if (!current || !TABS.some((tab) => tab.key === key)) return;
  current.tab = key;
  const { tabs, body } = refs();
  if (tabs) {
    for (const tabBtn of Array.from(tabs.children)) {
      const selected = tabBtn.id === `drawer-tab-${key}`;
      tabBtn.setAttribute("aria-selected", String(selected));
      tabBtn.setAttribute("tabindex", selected ? "0" : "-1");
    }
  }
  if (body) body.scrollTop = 0;
  renderTab();
}

async function renderTab() {
  const current = drawer;
  if (!current || !current.persona) return;
  const { body } = refs();
  if (!body) return;

  const token = (current.token || 0) + 1;
  current.token = token;
  const isCurrent = () => drawer === current && current.token === token;

  const panel = el("div", {
    class: "tab-panel",
    id: `drawer-panel-${current.tab}`,
    attrs: { role: "tabpanel", "aria-labelledby": `drawer-tab-${current.tab}` },
  });
  replaceChildren(body, panel);

  try {
    if (current.tab === "profile") renderProfile(panel, current);
    else if (current.tab === "prompt") renderPrompt(panel, current);
    else if (current.tab === "memory") await renderMemory(panel, current, isCurrent);
    else if (current.tab === "dev") await renderDev(panel, current, isCurrent);
    else if (current.tab === "advanced") renderAdvanced(panel, current);
    else if (current.tab === "versions") await renderVersions(panel, current, isCurrent);
  } catch (err) {
    if (!isCurrent()) return;
    panel.appendChild(errorBlock(err, () => renderTab()));
  }
}

function errorBlock(err, onRetry) {
  const message = err && err.message ? err.message : "请求失败，请稍后重试";
  const actions = [];
  if (typeof onRetry === "function") {
    actions.push(
      button({ icon: "refresh", label: "重试", variant: "primary", onClick: () => onRetry() })
    );
  }
  return emptyState({ icon: "alert", title: "加载失败", hint: message, actions });
}

function drawerSection(titleText, iconName, extraNodes = []) {
  const header = el(
    "div",
    { class: "drawer-section-head" },
    el(
      "h3",
      { class: "drawer-section-title" },
      icon(iconName, { size: 16 }),
      el("span", null, titleText)
    ),
    el("span", { class: "grow" })
  );
  for (const node of extraNodes) {
    if (node) header.appendChild(node);
  }
  const section = el("section", { class: "drawer-section" }, header);
  return section;
}

async function refreshPersona(current) {
  try {
    const detail = await endpoints.persona(current.pid);
    if (drawer !== current) return;
    current.persona = normalizeDetail(detail);
    renderShell();
    await renderTab();
  } catch (err) {
    toast(`刷新人格资料失败：${err && err.message ? err.message : err}`, "error");
  }
}

async function switchPersona(current, btn) {
  await runBusy(
    btn,
    async () => {
      const result = await endpoints.switchPersona(current.pid);
      toast(result.message || "已切换当前人格", "success");
      await current.app.loadPersonas({ force: true });
      if (drawer === current) renderShell();
    },
    "切换人格失败"
  );
}

/* ---------------------------- 资料 ---------------------------- */

function renderProfile(panel, current) {
  const persona = current.persona;
  const isActive = current.app.getActivePid && current.app.getActivePid() === current.pid;

  /* 头像 */
  const avatarSection = drawerSection("头像", "image");
  const avatarHost = el("div", { class: "avatar-preview" });
  fillAvatar(avatarHost, persona.avatarData, persona.nickname);
  const actions = el("div", { class: "avatar-actions" });

  const fileInput = el("input", {
    type: "file",
    accept: "image/png,image/jpeg,image/webp",
    hidden: true,
    attrs: { "aria-label": "选择头像文件" },
  });
  const uploadBtn = button({
    icon: "upload",
    label: "上传头像",
    variant: "primary",
    onClick: () => fileInput.click(),
  });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!file) return;
    if (file.type && !file.type.startsWith("image/")) {
      toast("请选择图片文件（PNG / JPEG / WebP）", "warn");
      return;
    }
    await runBusy(
      uploadBtn,
      async () => {
        const result = await endpoints.uploadAvatar(current.pid, file);
        toast(result.message || "头像已更新", "success");
        await current.app.loadPersonas({ force: true });
        await refreshPersona(current);
      },
      "上传头像失败"
    );
  });

  const urlInput = input({
    placeholder: "https://example.com/avatar.png",
    ariaLabel: "头像图片链接",
  });
  const urlBtn = button({
    icon: "link",
    label: "应用链接",
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const url = urlInput.value.trim();
          if (!/^https?:\/\//i.test(url)) {
            toast("请输入以 http:// 或 https:// 开头的图片链接", "warn");
            return;
          }
          const result = await endpoints.avatarFromUrl({ pid: current.pid, url });
          toast(result.message || "头像已更新", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "设置头像失败"
      ),
    variant: "primary",
    size: "sm",
  });
  const qqBtn = button({
    icon: "user",
    label: "用机器人 QQ 头像",
    size: "sm",
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const result = await endpoints.avatarFromUrl({ pid: current.pid, source: "qq" });
          toast(result.message || "已使用机器人 QQ 头像", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "获取 QQ 头像失败"
      ),
  });
  const removeBtn = button({
    icon: "trash",
    label: "移除头像",
    variant: "danger",
    size: "sm",
    disabled: !persona.avatarData,
    onClick: async (event, btn) => {
      const ok = await confirmDialog({
        title: "移除头像",
        message: `确认移除人格「${persona.nickname}」的头像吗？此操作不会删除历史版本中的头像快照。`,
        confirmText: "移除",
        danger: true,
      });
      if (!ok) return;
      await runBusy(
        btn,
        async () => {
          const result = await endpoints.removeAvatar(current.pid);
          toast(result.message || "头像已移除", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "移除头像失败"
      );
    },
  });

  actions.append(
    el("div", { class: "row-wrap" }, uploadBtn, fileInput),
    el("div", { class: "field-row" }, urlInput, urlBtn),
    el("div", { class: "row-wrap" }, qqBtn, removeBtn)
  );
  avatarSection.append(el("div", { class: "avatar-editor" }, avatarHost, actions));
  if (persona.avatarTooLarge) {
    avatarSection.appendChild(
      notice(
        "头像文件超过内联上限（256 KB），页面不会加载缩略图；建议上传一张更小的头像，或在 AstrBot 端查看。",
        "warn",
        "alert"
      )
    );
  }
  avatarSection.appendChild(
    el(
      "p",
      { class: "drawer-note" },
      "头像以 data URL 形式内嵌在接口返回中，页面不会直接引用任何外部图片地址。支持 PNG / JPEG / WebP，大小受配置项 max_avatar_mb 限制。"
    )
  );
  panel.appendChild(avatarSection);

  /* 昵称 */
  const nickSection = drawerSection("昵称", "pencil");
  const nickInput = input({
    value: persona.nickname,
    placeholder: "人格昵称",
    id: `nickname-${uniqueId("n")}`,
    ariaLabel: "人格昵称",
  });
  const nickId = nickInput.id;
  const nickSave = button({
    icon: "save",
    label: "保存昵称",
    variant: "primary",
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const nickname = nickInput.value.trim();
          if (!nickname) {
            toast("昵称不能为空", "warn");
            return;
          }
          if (nickname === persona.nickname) {
            toast("昵称没有改动", "info");
            return;
          }
          const result = await endpoints.updatePersona({ pid: current.pid, nickname });
          toast(result.message || "昵称已保存", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "保存昵称失败"
      ),
  });
  const nickField = el(
    "div",
    { class: "field" },
    el("label", { class: "field-label", attrs: { for: nickId } }, "昵称"),
    el("div", { class: "field-row" }, nickInput, nickSave)
  );
  nickSection.appendChild(nickField);
  panel.appendChild(nickSection);

  /* QQ 资料同步 */
  const qq = persona.qq || {};
  const qqConfigured = qq.configured === true;
  const qqSyncedAt = toNumber(qq.synced_at, 0);
  const qqCheckedAt = toNumber(qq.checked_at, 0);
  const qqSection = drawerSection("QQ 资料", "user");
  const qqList = el("div", { class: "info-list" });
  const qqRows = [
    ["同步状态", qqConfigured ? "已配置 SnowLuma" : "未配置 SnowLuma"],
    ["同步模式", qq.mode || "—"],
    ["上次推送到 QQ", qqSyncedAt ? formatTime(qqSyncedAt) : "从未"],
    ["最近核对 QQ 资料", qqCheckedAt ? formatTime(qqCheckedAt) : "从未"],
  ];
  for (const [key, value] of qqRows) {
    qqList.appendChild(
      el(
        "div",
        { class: "info-row" },
        el("span", { class: "info-k" }, key),
        el("span", { class: "info-v" }, String(value))
      )
    );
  }
  qqSection.appendChild(qqList);
  const qqSyncBtn = button({
    icon: "refresh",
    label: "立即同步到 QQ",
    size: "sm",
    variant: "primary",
    disabled: !qqConfigured || !isActive,
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const result = await endpoints.syncQQ(current.pid);
          toast(result.message || "已同步到 QQ", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "同步到 QQ 失败"
      ),
  });
  qqSection.appendChild(
    el(
      "div",
      { class: "row-wrap" },
      qqSyncBtn,
      el(
        "span",
        { class: "muted", style: { "font-size": "11px" } },
        qqConfigured
          ? "仅在切换人格、点此按钮或 /persona sync 时推送；不会定时轮询。"
          : "配置 snowluma_http 后才能同步。"
      )
    )
  );
  qqSection.appendChild(
    notice(
      isActive
        ? "插件不会从 QQ 反向拉取昵称/头像；QQ 侧被改动后，可用 /persona check 查看差异，/persona sync 强制修复。"
        : "只有「当前人格」会同步到 QQ；切换为当前人格后再点同步。",
      "info"
    )
  );
  panel.appendChild(qqSection);

  /* 资料信息 */
  const infoSection = drawerSection("资料", "info");
  const infoList = el("div", { class: "info-list" });
  const infoRows = [
    ["人格 ID", persona.id],
    ["提示词版本", persona.promptVersion > 0 ? `v${persona.promptVersion}` : "—"],
    ["记忆条数", `${formatNumber(persona.memoryCount)} 条`],
    ["历史版本", `${formatNumber(persona.versionCount)} 个`],
    ["开场对话", `${formatNumber(persona.beginDialogs.length)} 条`],
    ["最近更新", formatTime(persona.updatedAt)],
    ["头像校验", persona.avatarSha ? truncate(persona.avatarSha, 16) : "—"],
  ];
  for (const [key, value] of infoRows) {
    infoList.appendChild(
      el(
        "div",
        { class: "info-row" },
        el("span", { class: "info-k" }, key),
        el("span", ["人格 ID", "头像校验"].includes(key) ? "info-v mono" : "info-v", String(value))
      )
    );
  }
  infoSection.appendChild(infoList);
  if (isActive) {
    infoSection.appendChild(
      notice("这是当前生效的人格。切换人格后，QQ 侧的人设会立即随之变化。")
    );
  }
  panel.appendChild(infoSection);
}

/* ---------------------------- 提示词 ---------------------------- */

function renderPrompt(panel, current) {
  const persona = current.persona;

  const editorSection = drawerSection("提示词", "pencil", [
    badge(persona.promptVersion > 0 ? `当前 v${persona.promptVersion}` : "未保存", "muted"),
  ]);

  const ta = textarea({
    value: persona.prompt,
    rows: 14,
    placeholder: "这里是人设提示词…",
    ariaLabel: "人格提示词",
    className: "prompt-textarea",
  });
  const count = el("span", null, "");
  const updateCount = () => {
    count.textContent = `${formatNumber(ta.value.length)} 字符`;
  };
  ta.addEventListener("input", updateCount);
  updateCount();

  const hint = el(
    "div",
    { class: "counter" },
    el("span", null, "字符数"),
    count
  );

  const saveBtn = button({
    icon: "save",
    label: "保存提示词",
    variant: "primary",
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const prompt = ta.value;
          if (prompt.trim() === persona.prompt.trim()) {
            toast("提示词没有改动", "info");
            return;
          }
          const result = await endpoints.updatePersona({ pid: current.pid, prompt });
          toast(result.message || "提示词已保存", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "保存提示词失败"
      ),
  });
  const resetBtn = button({
    icon: "refresh",
    label: "还原",
    variant: "ghost",
    onClick: () => {
      ta.value = persona.prompt;
      updateCount();
    },
  });

  const actions = el("div", { class: "row-wrap" }, saveBtn, resetBtn);

  const iterateRow = el("div", { class: "row-wrap" });
  iterateRow.appendChild(
    button({
      icon: "book",
      label: "用知识库提炼",
      size: "sm",
      onClick: (event, btn) => runIterate(current, btn, "knowledge"),
    })
  );
  iterateRow.appendChild(
    button({
      icon: "wand",
      label: "用模型提炼",
      size: "sm",
      onClick: (event, btn) => runIterate(current, btn, "llm"),
    })
  );

  editorSection.append(
    el("div", { class: "prompt-editor" }, ta, hint, actions),
    el("div", { class: "settings-divider" }),
    el(
      "div",
      { class: "stack-sm" },
      el("span", { class: "field-label" }, "提炼与重写"),
      iterateRow,
      el(
        "p",
        { class: "drawer-note" },
        "知识库提炼只使用共享 wiki 与人格记忆；用模型提炼会调用聊天模型，消耗额度。提炼结果是草稿，需要到「审查」页通过后才会生效。"
      )
    )
  );
  panel.appendChild(editorSection);
}

async function runIterate(current, btn, mode) {
  if (mode === "llm") {
    const ok = await confirmDialog({
      title: "用模型提炼提示词",
      message: "将调用聊天模型基于近期对话重写这个人格的提示词，会消耗模型额度。生成的草稿需要你在审查页确认后才会生效。",
      confirmText: "开始提炼",
    });
    if (!ok) return;
  }
  await runBusy(
    btn,
    async () => {
      const result = await endpoints.iterate(current.pid, mode);
      const message =
        (result && result.message) ||
        (mode === "llm" ? "模型提炼已提交" : "知识库提炼已提交");
      // 后端对「没有知识型记忆 / 没有基础提示词」也返回 200，用文案兜底成提醒色
      toast(message, /未|没有|失败|错误/.test(message) ? "warn" : "success");
      await current.app.loadPersonas({ force: true });
      await refreshPersona(current);
    },
    "提炼失败"
  );
}

/* ---------------------------- 记忆 ---------------------------- */

async function renderMemory(panel, current, isCurrent) {
  const listSection = drawerSection("记忆列表", "layers", [
    button({
      icon: "history",
      label: "抓取最近会话",
      size: "sm",
      variant: "primary",
      onClick: (event, btn) =>
        runBusy(
          btn,
          async () => {
            const result = await endpoints.captureMemory(current.pid);
            toast(result.message || "已抓取最近会话", "success");
            await refreshPersona(current);
          },
          "抓取会话失败"
        ),
    }),
    (() => {
      const refresh = button({
        icon: "refresh",
        label: "刷新",
        size: "sm",
        variant: "ghost",
        onClick: () => renderTab(),
      });
      return refresh;
    })(),
  ]);
  listSection.appendChild(
    notice(
      "人格记忆不会实时同步 QQ 聊天：默认只在切换人格时自动抓取一次；点「抓取最近会话」或发 /memory snapshot 可立即把最近聊天写入记忆。记忆保存在插件本地（data/plugin_data/astrbot_plugin_persona_forge/persona_memory），每条右侧是本地记录时间。",
      "info"
    )
  );

  /* 添加 */
  const addInput = input({ placeholder: "要记住的一句话…", ariaLabel: "记忆内容" });
  const addBtn = button({
    icon: "plus",
    label: "添加记忆",
    size: "sm",
    variant: "primary",
    onClick: (event, btn) => memoryAction(current, btn, { action: "add", text: addInput.value }),
  });
  const knowInput = input({ placeholder: "沉淀为共享知识…", ariaLabel: "知识内容" });
  const knowBtn = button({
    icon: "book",
    label: "记入知识库",
    size: "sm",
    onClick: (event, btn) => memoryAction(current, btn, { action: "know", text: knowInput.value }),
  });
  const addForm = el(
    "div",
    { class: "stack-sm" },
    el("div", { class: "field-row" }, addInput, addBtn),
    el("div", { class: "field-row" }, knowInput, knowBtn)
  );
  listSection.appendChild(addForm);

  /* 检索 / 作用域 / 清空 */
  const forgetInput = input({ placeholder: "按关键词遗忘，例如「喜欢猫」", ariaLabel: "遗忘关键词" });
  const forgetBtn = button({
    icon: "trash",
    label: "遗忘",
    size: "sm",
    variant: "danger",
    onClick: (event, btn) =>
      memoryAction(current, btn, { action: "forget", keyword: forgetInput.value.trim() }),
  });
  const scopeSelect = el("select", { class: "select", attrs: { "aria-label": "记忆作用域" } });
  for (const option of SCOPE_OPTIONS) {
    scopeSelect.appendChild(el("option", { value: option.value }, option.label));
  }
  scopeSelect.value = "chat";
  const scopeBtn = button({
    icon: "sliders",
    label: "应用到全部",
    size: "sm",
    onClick: async (event, btn) => {
      const ok = await confirmDialog({
        title: "修改全部记忆作用域",
        message: `把这个人格的全部记忆作用域改为「${
          SCOPE_OPTIONS.find((option) => option.value === scopeSelect.value)?.label ||
          scopeSelect.value
        }」？`,
        confirmText: "应用",
      });
      if (!ok) return;
      await memoryAction(current, btn, { action: "scope", scope: scopeSelect.value });
    },
  });
  const clearBtn = button({
    icon: "trash",
    label: "清空记忆",
    size: "sm",
    variant: "danger",
    onClick: async (event, btn) => {
      const ok = await confirmDialog({
        title: "清空记忆",
        message: `确认清空人格「${current.persona.nickname}」的全部记忆吗？该操作不可撤销，共享知识库不受影响。`,
        confirmText: "清空",
        danger: true,
      });
      if (!ok) return;
      await memoryAction(current, btn, { action: "clear" });
    },
  });

  listSection.append(
    el("div", { class: "settings-divider" }),
    el("div", { class: "field-row" }, forgetInput, forgetBtn),
    el("div", { class: "field-row" }, scopeSelect, scopeBtn, clearBtn),
    el(
      "p",
      { class: "drawer-note" },
      "作用域只影响经历/偏好型记忆：知识型记忆始终全局可见，会话记忆不可改作用域。留空关键词表示作用于全部记忆。"
    )
  );
  panel.appendChild(listSection);

  /* 列表 */
  const host = el("div", { class: "memory-list", attrs: { "aria-busy": "true" } });
  const listWrap = drawerSection("已记录", "database");
  listWrap.appendChild(host);
  panel.appendChild(listWrap);

  await loadMemory(current, host, isCurrent);
}

async function memoryAction(current, btn, payload) {
  const request = { pid: current.pid, ...payload };
  if (payload.action === "add" || payload.action === "know") {
    if (!String(payload.text || "").trim()) {
      toast(payload.action === "add" ? "请先填写记忆内容" : "请先填写知识内容", "warn");
      return;
    }
    request.text = String(payload.text).trim();
  }
  if (payload.action === "forget" && !String(payload.keyword || "").trim()) {
    toast("请填写要遗忘的关键词", "warn");
    return;
  }

  await runBusy(
    btn,
    async () => {
      const result = await endpoints.memoryAction(request);
      toast(result && result.message ? result.message : "操作已完成", "success");
      await current.app.loadPersonas({ force: true });
      await refreshPersona(current);
    },
    "记忆操作失败"
  );
}

async function loadMemory(current, host, isCurrent) {
  replaceChildren(host, el("div", { class: "muted" }, "加载中…"));
  try {
    const data = await endpoints.memory(current.pid);
    if (!isCurrent()) return;
    const items = Array.isArray(data && data.items) ? data.items : [];
    host.removeAttribute("aria-busy");
    replaceChildren(host);
    if (items.length === 0) {
      host.appendChild(
        emptyState({
          icon: "layers",
          title: "还没有记忆",
          hint: "在群聊或私聊中对话会自动沉淀片段；也可以在上方手动添加一条。",
        })
      );
      return;
    }
    for (const item of items) {
      host.appendChild(buildMemoryItem(current, item, host, isCurrent));
    }
  } catch (err) {
    if (!isCurrent()) return;
    host.removeAttribute("aria-busy");
    replaceChildren(host, errorBlock(err, () => renderTab()));
  }
}

function buildMemoryItem(current, item, host, isCurrent) {
  const kind = String((item && item.kind) || "episodic");
  const scope = String((item && item.scope) || "");
  const text = String((item && item.text) || "");
  const source = String((item && item.source) || "");
  const createdAt = item ? item.created_at : null;

  const top = el("div", { class: "memory-top" });
  top.appendChild(badge(KIND_LABELS[kind] || kind));
  if (scope) top.appendChild(badge(SCOPE_LABELS[scope] || scope, "muted"));
  if (source) top.appendChild(el("span", { class: "chip" }, source));
  top.appendChild(
    el("span", { class: "muted", style: { "font-size": "11px" } }, timeAgo(createdAt))
  );

  const actions = el("div", { class: "memory-actions" });
  if (kind === "knowledge") {
    const promoteBtn = button({
      icon: "book",
      label: "同步到共享库",
      variant: "ghost",
      size: "sm",
      onClick: async (event, btn) => {
        await runBusy(
          btn,
          async () => {
            const result = await endpoints.memoryAction({
              pid: current.pid,
              action: "promote",
              keyword: text,
            });
            toast(result && result.message ? result.message : "已同步到共享知识库", "success");
            await current.app.loadPersonas({ force: true });
            await refreshPersona(current);
          },
          "同步失败"
        );
      },
    });
    actions.appendChild(promoteBtn);
  }
  const forgetBtn = button({
    icon: "trash",
    label: "忘记",
    variant: "danger",
    size: "sm",
    onClick: async (event, btn) => {
      await runBusy(
        btn,
        async () => {
          const result = await endpoints.memoryAction({
            pid: current.pid,
            action: "forget",
            keyword: text,
          });
          toast(result && result.message ? result.message : "已遗忘", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "遗忘失败"
      );
    },
  });
  actions.appendChild(forgetBtn);
  top.appendChild(actions);

  return el(
    "article",
    { class: "memory-item" },
    top,
    el("p", { class: "memory-text" }, text),
    el("span", { class: "note-time" }, formatTime(createdAt, { seconds: true }))
  );
}

/* ---------------------------- 开发者模式 ---------------------------- */

async function renderDev(panel, current, isCurrent) {
  const statusSection = drawerSection("开发者模式", "terminal");
  const statusHost = el("div", { class: "dev-status" }, el("span", { class: "muted" }, "加载中…"));
  statusSection.appendChild(statusHost);
  const notesHost = el("div", { class: "note-list", attrs: { "aria-busy": "true" } });
  const notesSection = drawerSection("教学笔记", "layers");
  notesSection.appendChild(notesHost);
  panel.append(statusSection, notesSection);

  let data;
  try {
    data = await endpoints.dev(current.pid);
  } catch (err) {
    if (!isCurrent()) return;
    replaceChildren(statusHost, errorBlock(err, () => renderTab()));
    clear(notesHost);
    return;
  }
  if (!isCurrent()) return;

  const activePid = String((data && data.active_pid) || "");
  const enabled = activePid !== "" && activePid === current.pid;
  const otherActive = activePid !== "" && activePid !== current.pid;
  const notes = Array.isArray(data && data.notes) ? data.notes : [];
  const ttl = toNumber(data && data.ttl, 0);
  const expiresAt = data && data.expires_at;

  /* 状态与开关 */
  const toggleWrap = el("div", { class: "dev-status" });
  const checkbox = el("input", {
    type: "checkbox",
    id: "dev-toggle",
    checked: enabled,
  });
  checkbox.addEventListener("change", async () => {
    const next = checkbox.checked;
    checkbox.disabled = true;
    try {
      const result = await endpoints.setDev(current.pid, next);
      toast(
        (result && result.message) || (next ? "开发者模式已开启" : "开发者模式已关闭"),
        "success"
      );
      renderTab();
    } catch (err) {
      checkbox.checked = !next;
      checkbox.disabled = false;
      toast(`切换失败：${err && err.message ? err.message : err}`, "error");
    }
  });
  toggleWrap.appendChild(
    el(
      "label",
      { class: "toggle", attrs: { for: "dev-toggle" } },
      checkbox,
      el("span", { class: "toggle-track", attrs: { "aria-hidden": "true" } }),
      el("span", { class: "toggle-text" }, "启用教学重写")
    )
  );
  toggleWrap.appendChild(enabled ? badge("已开启", "success") : badge("未开启", "muted"));
  if (ttl > 0) {
    toggleWrap.appendChild(el("span", { class: "chip" }, `剩余 ${formatTtl(ttl)}`));
  }
  if (expiresAt) {
    toggleWrap.appendChild(
      el("span", { class: "muted", style: { "font-size": "11px" } }, `到期 ${formatTime(expiresAt)}`)
    );
  }
  replaceChildren(statusHost, toggleWrap);
  statusHost.appendChild(
    el(
      "p",
      { class: "drawer-note" },
      "开发者模式不是「扮演开发者」，而是把 owner 的教学说明临时合并进提示词：你在这里记下的每一条，都会在下一次重写时交给模型参考。"
    )
  );
  if (otherActive) {
    statusHost.appendChild(
      notice(`当前开发者模式属于人格 ${otherActive}，开启本项会自动接管。`, "warn", "alert")
    );
  }

  /* 笔记 */
  const noteInput = textarea({
    rows: 3,
    placeholder: "例如：回答时先给结论，再给两点理由；不要用表情包。",
    ariaLabel: "教学笔记内容",
  });
  const addBtn = button({
    icon: "plus",
    label: "记录笔记",
    variant: "primary",
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const text = noteInput.value.trim();
          if (!text) {
            toast("笔记内容不能为空", "warn");
            return;
          }
          const result = await endpoints.addDevNote(current.pid, text);
          toast((result && result.message) || "笔记已记录", "success");
          renderTab();
        },
        "记录笔记失败"
      ),
  });
  const compactBtn = button({
    icon: "wand",
    label: "生成教学草稿",
    size: "sm",
    variant: "ghost",
    onClick: async (event, btn) => {
      const ok = await confirmDialog({
        title: "生成教学草稿",
        message:
          "会调用聊天模型，把基础提示词、知识库与当前教学笔记合并成一份草稿。草稿不会直接生效，需要到「审查」页通过。",
        confirmText: "生成草稿",
      });
      if (!ok) return;
      await runBusy(
        btn,
        async () => {
          const result = await endpoints.compactDev(current.pid);
          toast((result && result.message) || "已生成教学草稿", "success");
          renderTab();
        },
        "生成草稿失败"
      );
    },
  });
  const clearBtn = button({
    icon: "trash",
    label: "清空笔记",
    size: "sm",
    variant: "danger",
    onClick: async (event, btn) => {
      const ok = await confirmDialog({
        title: "清空教学笔记",
        message: "确认清空这个人格的全部教学笔记吗？",
        confirmText: "清空",
        danger: true,
      });
      if (!ok) return;
      await runBusy(
        btn,
        async () => {
          const result = await endpoints.clearDev(current.pid);
          toast((result && result.message) || "笔记已清空", "success");
          renderTab();
        },
        "清空笔记失败"
      );
    },
  });

  statusSection.append(
    el("div", { class: "settings-divider" }),
    el("div", { class: "stack-sm" }, noteInput, el("div", { class: "row-wrap" }, addBtn, compactBtn, clearBtn))
  );

  /* 笔记列表 */
  notesHost.removeAttribute("aria-busy");
  replaceChildren(notesHost);
  if (notes.length === 0) {
    notesHost.appendChild(
      el("p", { class: "drawer-note" }, "还没有教学笔记。写下第一句偏好，下次重写提示词时会带上。")
    );
  } else {
    for (const note of notes) {
      notesHost.appendChild(
        el(
          "article",
          { class: "note-item" },
          el("span", null, String((note && note.text) || "")),
          el("span", { class: "note-time" }, formatTime(note && note.created_at, { seconds: true }))
        )
      );
    }
  }
}

function formatTtl(seconds) {
  const total = Math.max(0, Math.trunc(toNumber(seconds, 0)));
  if (total < 60) return `${total} 秒`;
  if (total < 3600) return `${Math.floor(total / 60)} 分钟`;
  if (total < 86400) {
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  }
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  return hours ? `${days} 天 ${hours} 小时` : `${days} 天`;
}

/* ---------------------------- 高级 ---------------------------- */

function renderAdvanced(panel, current) {
  const persona = current.persona;

  const section = drawerSection("高级字段", "sliders");
  section.appendChild(
    notice(
      "这些字段直接映射到 AstrBot 原生人格对象：tools / skills 填 all 表示全部、none 表示全部禁用，也可以写逗号分隔的名称；开场对话是 user / assistant 交替的偶数条列表。",
      "info"
    )
  );

  const toolsInput = input({
    value: listFieldText(persona.tools),
    placeholder: "all / none / web_search, weather",
    ariaLabel: "工具列表",
  });
  const skillsInput = input({
    value: listFieldText(persona.skills),
    placeholder: "all / none / writing, coding",
    ariaLabel: "Skills 列表",
  });
  const errorTa = textarea({
    rows: 3,
    placeholder: "自定义错误提示（留空表示不修改）",
    ariaLabel: "自定义错误提示",
  });
  const clearErrorToggle = toggle({
    id: "adv-clear-error",
    label: "清空当前自定义错误提示",
  });
  const dialogsInitial = persona.beginDialogs.length
    ? JSON.stringify(persona.beginDialogs, null, 2)
    : "";
  const dialogsTa = textarea({
    rows: 7,
    mono: true,
    value: dialogsInitial,
    placeholder: '开场对话 JSON，例如 ["你好", "你好呀"]（user / assistant 交替，偶数条）',
    ariaLabel: "开场对话 JSON",
  });

  section.append(
    el(
      "div",
      { class: "field-grid" },
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "工具（tools）"),
        toolsInput,
        el("p", { class: "field-hint" }, "all = 全部工具；none = 全部禁用；也可以写名称列表。")
      ),
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Skills"),
        skillsInput,
        el("p", { class: "field-hint" }, "规则同工具。")
      ),
      el(
        "div",
        { class: "field is-wide" },
        el("label", { class: "field-label" }, "自定义错误提示（error）"),
        errorTa,
        clearErrorToggle,
        el("p", { class: "field-hint" }, "接口返回里没有这个字段，所以留空表示「不修改」；勾选上面的开关可显式清空。")
      ),
      el(
        "div",
        { class: "field is-wide" },
        el("label", { class: "field-label" }, "开场对话（dialogs）"),
        dialogsTa
      )
    )
  );

  const saveBtn = button({
    icon: "save",
    label: "保存高级字段",
    variant: "primary",
    onClick: (event, btn) =>
      runBusy(
        btn,
        async () => {
          const payload = { pid: current.pid };

          const toolsValue = listFieldValue(toolsInput.value);
          const skillsValue = listFieldValue(skillsInput.value);
          if (JSON.stringify(toolsValue) !== JSON.stringify(listState(persona.tools))) {
            payload.tools = toolsValue;
          }
          if (JSON.stringify(skillsValue) !== JSON.stringify(listState(persona.skills))) {
            payload.skills = skillsValue;
          }

          const errorText = errorTa.value.trim();
          const clearErrorBox = clearErrorToggle.querySelector("input");
          if (clearErrorBox && clearErrorBox.checked) payload.error = "";
          else if (errorText) payload.error = errorText;

          const dialogsText = dialogsTa.value.trim();
          if (dialogsText !== dialogsInitial.trim()) {
            let parsed;
            try {
              parsed = JSON.parse(dialogsText);
            } catch (err) {
              toast("开场对话不是合法 JSON，请检查格式", "error");
              return;
            }
            if (!Array.isArray(parsed) || parsed.length % 2 !== 0) {
              toast("开场对话必须是 user / assistant 交替的偶数条数组", "error");
              return;
            }
            payload.dialogs = parsed;
          }

          if (Object.keys(payload).length === 1) {
            toast("没有需要保存的改动", "info");
            return;
          }

          const result = await endpoints.updatePersona(payload);
          toast((result && result.message) || "高级字段已保存", "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "保存高级字段失败"
      ),
  });

  section.appendChild(el("div", { class: "row-wrap" }, saveBtn));
  panel.appendChild(section);
}

/** 把 null（全部）/ 空数组（禁用）/ 名称数组统一成可比较的状态。 */
function listState(value) {
  if (value === null || value === undefined) return "all";
  if (Array.isArray(value) && value.length === 0) return "none";
  return Array.isArray(value) ? value : splitList(value);
}

/* ---------------------------- 版本 ---------------------------- */

async function renderVersions(panel, current, isCurrent) {
  const section = drawerSection("历史版本", "history", [
    button({
      icon: "refresh",
      label: "刷新",
      size: "sm",
      variant: "ghost",
      onClick: () => renderTab(),
    }),
  ]);
  const listHost = el("div", { class: "version-list", attrs: { "aria-busy": "true" } });
  const detailHost = el("div", { class: "version-preview" });
  section.append(listHost, detailHost);
  panel.appendChild(section);

  let versions = [];
  try {
    const data = await endpoints.versions(current.pid);
    if (!isCurrent()) return;
    versions = normalizeVersions(data && data.versions);
  } catch (err) {
    if (!isCurrent()) return;
    replaceChildren(listHost, errorBlock(err, () => renderTab()));
    return;
  }
  if (!isCurrent()) return;

  listHost.removeAttribute("aria-busy");
  replaceChildren(listHost);
  replaceChildren(detailHost);

  if (versions.length === 0) {
    listHost.appendChild(
      emptyState({
        icon: "history",
        title: "还没有历史版本",
        hint: "每次保存提示词或切换人格都会自动生成快照。",
      })
    );
    return;
  }

  let selectedN = null;
  const items = new Map();

  const selectVersion = async (n, btn) => {
    selectedN = n;
    for (const [itemN, node] of items) node.classList.toggle("is-active", itemN === n);
    replaceChildren(detailHost, el("div", { class: "muted" }, "加载版本内容…"));
    try {
      const detail = await endpoints.version(current.pid, n);
      if (!isCurrent() || selectedN !== n) return;
      renderVersionDetail(detailHost, current, versions, detail, selectVersion, isCurrent);
    } catch (err) {
      if (!isCurrent()) return;
      replaceChildren(detailHost, errorBlock(err, () => selectVersion(n, btn)));
    }
  };

  for (const version of versions) {
    const node = el(
      "button",
      { type: "button", class: "version-item" },
      el("span", { class: "version-no" }, `v${version.n}`),
      el("span", { class: "version-reason" }, version.reason || "自动快照"),
      el("span", { class: "version-time" }, formatTime(version.createdAt))
    );
    node.addEventListener("click", () => selectVersion(version.n, node));
    items.set(version.n, node);
    listHost.appendChild(node);
  }

  selectVersion(versions[0].n, items.get(versions[0].n));
}

function normalizeVersions(raw) {
  const list = Array.isArray(raw) ? raw : [];
  return list
    .map((item) => {
      if (item && typeof item === "object") {
        return {
          n: toNumber(item.n ?? item.version, 0),
          createdAt: item.created_at ?? item.createdAt ?? null,
          reason: String(item.reason ?? ""),
        };
      }
      return { n: toNumber(item, 0), createdAt: null, reason: "" };
    })
    .filter((item) => item.n > 0)
    .sort((a, b) => b.n - a.n);
}

function renderVersionDetail(host, current, versions, detail, selectVersion, isCurrent) {
  const n = toNumber(detail && detail.n, 0);
  const prompt = typeof (detail && detail.prompt) === "string" ? detail.prompt : "";
  const diff = typeof (detail && detail.diff) === "string" ? detail.diff : "";

  const head = el(
    "div",
    { class: "drawer-section-head" },
    el(
      "h4",
      { class: "drawer-section-title" },
      icon("history", { size: 16 }),
      el("span", null, `v${n} 内容`)
    ),
    el("span", { class: "grow" }),
    badge(String((detail && detail.reason) || "快照"), "muted"),
    el("span", { class: "muted", style: { "font-size": "11px" } }, formatTime(detail && detail.created_at))
  );

  const body = el("div", { class: "stack-sm" });
  if (diff.trim()) {
    body.appendChild(
      el(
        "div",
        { class: "diff-panel-head" },
        el("span", null, "与当前提示词的差异"),
        el("span", { class: "grow" }),
        el("span", { class: "muted" }, `v${n} vs 当前`)
      )
    );
    body.appendChild(diffView(diff));
  } else {
    body.appendChild(el("p", { class: "drawer-note" }, "该版本与当前提示词没有差异。"));
  }
  if (prompt) {
    body.appendChild(
      el(
        "details",
        { class: "notes-block" },
        el("summary", { class: "notes-block-title" }, "查看该版本完整提示词"),
        el("pre", { class: "notes-block-body" }, prompt)
      )
    );
  }

  const rollbackBtn = button({
    icon: "restore",
    label: "回滚到此版本",
    variant: "danger",
    size: "sm",
    onClick: async (event, btn) => {
      const ok = await confirmDialog({
        title: `回滚到 v${n}`,
        message: "回滚前会自动为当前提示词做一次快照，因此这个操作可以再回滚回来。确认继续吗？",
        confirmText: "回滚",
        danger: true,
      });
      if (!ok) return;
      await runBusy(
        btn,
        async () => {
          const result = await endpoints.rollbackVersion(current.pid, n);
          toast((result && result.message) || `已回滚到 v${n}`, "success");
          await current.app.loadPersonas({ force: true });
          await refreshPersona(current);
        },
        "回滚失败"
      );
    },
  });

  replaceChildren(
    host,
    head,
    body,
    el("div", { class: "row-wrap" }, rollbackBtn)
  );
  void versions;
  void selectVersion;
  void isCurrent;
}

/* ---------------------------- 抽屉错误态 ---------------------------- */

function renderDrawerError(err) {
  const current = drawer;
  if (!current) return;
  const { head, tabs, body } = refs();
  if (head) {
    replaceChildren(
      head,
      el(
        "div",
        { class: "drawer-ident" },
        el(
          "h2",
          { class: "drawer-title", id: "drawer-title" },
          el("span", { class: "drawer-title-text" }, current.pid)
        ),
        el("div", { class: "drawer-sub" }, el("span", null, "人格资料加载失败"))
      ),
      el(
        "div",
        { class: "drawer-head-actions" },
        iconButton({ icon: "x", title: "关闭人格详情", onClick: () => closePersona() })
      )
    );
  }
  if (tabs) clear(tabs);
  if (body) {
    replaceChildren(
      body,
      errorBlock(err, async () => {
        try {
          const detail = await endpoints.persona(current.pid);
          if (drawer !== current) return;
          current.persona = normalizeDetail(detail);
          renderShell();
          await renderTab();
          focusActiveTab();
        } catch (retryErr) {
          renderDrawerError(retryErr);
        }
      })
    );
  }
}

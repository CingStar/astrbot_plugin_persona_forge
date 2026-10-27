/**
 * 人格工坊 WebUI · 应用入口
 *
 * 职责：theme / i18n / hash 路由 / 顶栏（搜索、当前人格、提炼）/ 侧边栏 /
 *      toast 与错误横幅 / 与宿主 bridge 的连接状态。
 */

import {
  apiReady,
  endpoints,
  getContext,
  getLocale,
  hasBridge,
  onContext,
  t,
} from "./api.js";
import {
  button,
  choiceDialog,
  clear,
  emptyState,
  fillAvatar,
  hideBanner,
  hydrateIcons,
  icon,
  pushEscapeHandler,
  qs,
  qsa,
  replaceChildren,
  runBusy,
  showBanner,
  toast,
} from "./dom.js";
import * as lifePage from "./life.js";
import * as personasPage from "./personas.js";
import * as reviewsPage from "./reviews.js";
import * as settingsPage from "./settings.js";
import { debounce, normalizePersona, formatNumber } from "./util.js";

const ROUTES = {
  personas: personasPage,
  reviews: reviewsPage,
  life: lifePage,
  settings: settingsPage,
};

const PAGE_TITLES = {
  personas: "人格",
  reviews: "审查",
  life: "生命卡",
  settings: "设置中心",
};

const THEME_KEY = "persona-forge.theme";

const state = {
  route: "personas",
  search: "",
  personas: [],
  active: "",
  version: "",
  counts: { personas: 0, reviews: 0, life_cards: 0 },
  personasLoadedAt: 0,
};

let currentPage = null;
let navOpen = false;
let releaseNavEscape = null;
const personaListeners = new Set();

/* ============================== 应用对象 ============================== */

const app = {
  getPersonas: () => state.personas,
  getActivePid: () => state.active,
  getSearch: () => state.search,

  loadPersonas,
  refreshStatus,
  setVersion,
  setError(message) {
    if (message) showBanner(String(message), "error");
  },
  clearError() {
    hideBanner();
  },
  clearSearch() {
    const input = qs("#search-input");
    if (input) input.value = "";
    state.search = "";
    const clearBtn = qs("#search-clear");
    if (clearBtn) clearBtn.hidden = true;
    if (currentPage && currentPage.setSearch) currentPage.setSearch("");
  },
  onPersonasChanged(handler) {
    if (typeof handler !== "function") return () => {};
    personaListeners.add(handler);
    return () => personaListeners.delete(handler);
  },
  openPersona(pid) {
    return personasPage.openPersona(app, pid);
  },
  navigate(name) {
    if (!ROUTES[name]) return;
    if (state.route === name) {
      if (currentPage && currentPage.reload) currentPage.reload();
      return;
    }
    const next = `#/${name}`;
    if (window.location.hash === next) renderRoute(name);
    else window.location.hash = next;
  },
};

/* ============================== 主题 ============================== */

function storedTheme() {
  try {
    return window.localStorage.getItem(THEME_KEY) || "";
  } catch {
    return "";
  }
}

function applyTheme(theme, options = {}) {
  const dark = theme === "dark";
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  const btn = qs("#theme-btn");
  if (btn) {
    replaceChildren(btn, icon(dark ? "sun" : "moon"));
    const label = dark ? "切换到浅色主题" : "切换到深色主题";
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }
  if (options.persist !== false) {
    try {
      window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }
}

function initTheme() {
  const saved = storedTheme();
  if (saved) {
    applyTheme(saved, { persist: false });
    return;
  }
  const prefersDark =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(prefersDark ? "dark" : "light", { persist: false });
}

function toggleTheme() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  applyTheme(dark ? "light" : "dark");
}

/* ============================== 国际化 ============================== */

function applyI18n() {
  const navKeys = {
    personas: "nav.personas",
    reviews: "nav.reviews",
    life: "nav.life",
    settings: "nav.settings",
  };
  for (const btn of qsa("[data-route]")) {
    const key = navKeys[btn.dataset.route];
    if (!key) continue;
    const label = qs(".nav-label", btn);
    if (label) label.textContent = t(key, label.textContent);
  }
  const search = qs("#search-input");
  if (search) search.placeholder = t("app.search", search.placeholder);
  const pillLabel = qs(".pill-label");
  if (pillLabel) pillLabel.textContent = t("app.current", "当前人格");
  const iterateLabel = qs("#iterate-btn .btn-label");
  if (iterateLabel) iterateLabel.textContent = t("app.iterate", "提炼当前人格");
  const refreshBtn = qs("#refresh-btn");
  if (refreshBtn) refreshBtn.title = t("app.refresh", "刷新当前页");
  const locale = getLocale().toLowerCase();
  document.documentElement.lang = locale.startsWith("en") ? "en" : "zh-CN";
}

/* ============================== 状态加载 ============================== */

async function loadPersonas(options = {}) {
  const force = options.force === true;
  const stale = Date.now() - state.personasLoadedAt > 2500;
  if (!force && !stale && state.personas.length > 0) {
    return { personas: state.personas, active: state.active, version: state.version };
  }

  const data = await endpoints.personas();
  const rawList = Array.isArray(data && data.personas) ? data.personas : [];
  const personas = rawList.map(normalizePersona);
  const active = String(
    (data && data.active) || (personas.find((persona) => persona.active) || {}).id || ""
  );
  state.personas = personas;
  state.active = active;
  if (data && data.version) state.version = String(data.version);
  state.personasLoadedAt = Date.now();

  renderActivePill();
  renderVersion();
  updateNavCount({ personas: personas.length });

  const payload = { personas, active, version: state.version };
  for (const listener of Array.from(personaListeners)) {
    try {
      listener(payload);
    } catch {
      /* ignore */
    }
  }
  return payload;
}

async function refreshStatus() {
  try {
    const status = await endpoints.status();
    if (status && status.version) setVersion(String(status.version));
    if (status && status.counts) updateNavCount(status.counts);
    if (status && status.active) {
      state.active = String(status.active);
      renderActivePill();
    }
    return status;
  } catch {
    return null;
  }
}

function setVersion(version) {
  state.version = String(version || "");
  renderVersion();
}

function renderVersion() {
  const label = qs("#version-label");
  if (label) label.textContent = state.version ? `v${state.version}` : "v—";
}

function updateNavCount(counts) {
  if (!counts) return;
  const mapping = {
    personas: counts.personas,
    reviews: counts.reviews,
    life: counts.life_cards,
  };
  for (const [route, value] of Object.entries(mapping)) {
    const node = qs(`[data-count="${route}"]`);
    if (!node) continue;
    const count = Number(value);
    if (!Number.isFinite(count) || count <= 0) {
      node.hidden = true;
      node.textContent = "";
      continue;
    }
    node.hidden = false;
    node.textContent = count > 99 ? "99+" : formatNumber(count);
  }
}

function renderActivePill() {
  const pill = qs("#active-pill");
  const name = qs("#pill-name");
  const avatarHost = qs("#pill-avatar");
  if (!pill || !name) return;
  const persona = state.personas.find((item) => item.id === state.active);
  if (persona) {
    name.textContent = persona.nickname;
    pill.title = `当前人格：${persona.nickname}（${persona.id}）· 点击查看详情`;
    if (avatarHost) fillAvatar(avatarHost, persona.avatarData, persona.nickname);
    return;
  }
  name.textContent = t("app.noActive", "未选择");
  pill.title = "当前还没有设置人格，点击去人格页选择";
  if (avatarHost) fillAvatar(avatarHost, "", "?");
}

/* ============================== 路由 ============================== */

function parseRoute() {
  const raw = String(window.location.hash || "")
    .replace(/^#\/?/, "")
    .split("?")[0]
    .trim()
    .toLowerCase();
  return Object.prototype.hasOwnProperty.call(ROUTES, raw) ? raw : "personas";
}

function updateNavCurrent() {
  for (const btn of qsa("[data-route]")) {
    if (btn.dataset.route === state.route) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
}

function renderRoute(name) {
  const route = Object.prototype.hasOwnProperty.call(ROUTES, name) ? name : "personas";
  state.route = route;

  if (navOpen) closeNav();
  if (personasPage.isDrawerOpen()) personasPage.closePersona();

  if (currentPage) {
    try {
      currentPage.destroy();
    } catch {
      /* ignore */
    }
    currentPage = null;
  }

  const root = qs("#view-root");
  if (!root) return;
  clear(root);
  updateNavCurrent();
  document.title = `人格工坊 · ${PAGE_TITLES[route] || ""}`;

  try {
    currentPage = ROUTES[route].mount(app, root);
  } catch (err) {
    root.appendChild(
      emptyState({
        icon: "alert",
        title: "页面渲染失败",
        hint: err && err.message ? err.message : String(err),
        actions: [
          button({
            icon: "refresh",
            label: "重新加载",
            variant: "primary",
            onClick: () => renderRoute(route),
          }),
        ],
      })
    );
    toast(`页面渲染失败：${err && err.message ? err.message : err}`, "error");
    return;
  }

  if (state.search && currentPage && currentPage.setSearch) currentPage.setSearch(state.search);
}

/* ============================== 侧边栏（移动端） ============================== */

function scrimNode() {
  return qs("#drawer-scrim");
}

function openNav() {
  if (navOpen) return;
  const shell = qs("#app-shell");
  const menuBtn = qs("#menu-btn");
  navOpen = true;
  if (shell) shell.classList.add("sidebar-open");
  if (menuBtn) menuBtn.setAttribute("aria-expanded", "true");
  const scrim = scrimNode();
  if (scrim) scrim.hidden = false;
  releaseNavEscape = pushEscapeHandler(closeNav);
}

function closeNav() {
  if (!navOpen) return;
  const shell = qs("#app-shell");
  const menuBtn = qs("#menu-btn");
  navOpen = false;
  if (shell) shell.classList.remove("sidebar-open");
  if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
  const scrim = scrimNode();
  if (scrim && !personasPage.isDrawerOpen()) scrim.hidden = true;
  if (releaseNavEscape) {
    releaseNavEscape();
    releaseNavEscape = null;
  }
}

/* ============================== 顶栏交互 ============================== */

function bindShell() {
  for (const btn of qsa("[data-route]")) {
    btn.addEventListener("click", () => app.navigate(btn.dataset.route));
  }

  const menuBtn = qs("#menu-btn");
  if (menuBtn) {
    menuBtn.addEventListener("click", () => {
      if (navOpen) closeNav();
      else openNav();
    });
  }

  const scrim = scrimNode();
  if (scrim) {
    scrim.addEventListener("click", () => {
      if (navOpen) closeNav();
    });
  }

  const searchInput = qs("#search-input");
  const clearBtn = qs("#search-clear");
  const runSearch = debounce((value) => {
    state.search = value;
    if (clearBtn) clearBtn.hidden = value === "";
    if (currentPage && currentPage.setSearch) currentPage.setSearch(value);
  }, 140);

  if (searchInput) {
    searchInput.addEventListener("input", () => runSearch(searchInput.value));
    searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && searchInput.value) {
        searchInput.value = "";
        runSearch("");
      }
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      runSearch("");
      if (searchInput) searchInput.focus();
    });
  }

  const pill = qs("#active-pill");
  if (pill) {
    pill.addEventListener("click", () => {
      if (state.active) app.openPersona(state.active);
      else {
        app.navigate("personas");
        toast("还没有设置当前人格，先在人格页选择一个吧", "info");
      }
    });
  }

  const iterateBtn = qs("#iterate-btn");
  if (iterateBtn) {
    iterateBtn.addEventListener("click", () => iterateActive(iterateBtn));
  }

  const refreshBtn = qs("#refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => refreshCurrent(refreshBtn));
  }

  const themeBtn = qs("#theme-btn");
  if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

  const bannerClose = qs("#banner-close");
  if (bannerClose) bannerClose.addEventListener("click", hideBanner);

  window.addEventListener("hashchange", () => renderRoute(parseRoute()));

  window.addEventListener("resize", () => {
    if (window.innerWidth > 900 && navOpen) closeNav();
  });

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event && event.reason;
    const message =
      (reason && reason.message) || (typeof reason === "string" ? reason : "") || "请求异常";
    if (reason && reason.name === "ApiError") return; // 已由各视图提示
    toast(`请求异常：${message}`, "error");
  });
}

async function refreshCurrent(btn) {
  await runBusy(
    btn,
    async () => {
      if (currentPage && currentPage.reload) await currentPage.reload();
      else renderRoute(state.route);
      await loadPersonas({ force: true });
      await refreshStatus();
    },
    "刷新失败"
  );
}

async function iterateActive(btn) {
  const pid = state.active;
  if (!pid) {
    toast("当前还没有设置人格，先在人格页选择一个", "warn");
    app.navigate("personas");
    return;
  }
  const persona = state.personas.find((item) => item.id === pid);
  const nickname = persona ? persona.nickname : pid;
  const mode = await choiceDialog({
    title: "提炼当前人格",
    message: `用「${nickname}」现有的资料生成一版新的提示词草稿。草稿不会直接生效，需要到审查页确认。`,
    choices: [
      {
        id: "knowledge",
        icon: "book",
        title: "用知识库提炼",
        desc: "只使用共享知识库与人格记忆，不调用聊天模型，不消耗额度。",
      },
      {
        id: "llm",
        icon: "wand",
        title: "用模型提炼",
        desc: "调用聊天模型重写提示词，会消耗模型额度。",
      },
    ],
    confirmText: "开始提炼",
  });
  if (!mode) return;

  await runBusy(
    btn,
    async () => {
      const result = await endpoints.iterate(pid, mode);
      const message = (result && result.message) || "提炼已提交";
      toast(message, /未|没有|失败|错误/.test(message) ? "warn" : "success");
      await loadPersonas({ force: true });
      await refreshStatus();
      if (state.route === "reviews" && currentPage && currentPage.reload) {
        await currentPage.reload();
      }
    },
    "提炼失败"
  );
}

/* ============================== 连接状态 ============================== */

function setupConnection(connected) {
  const dot = qs("#conn-dot");
  if (dot) {
    dot.classList.toggle("is-offline", !connected);
    dot.title = connected ? "已连接 AstrBot 宿主" : "未检测到 AstrBot 宿主桥接";
  }
  const note = qs("#sidebar-note");
  if (note) {
    if (connected) {
      note.hidden = true;
    } else {
      note.hidden = false;
      note.textContent =
        "本地预览模式：未检测到 AstrBot 插件视图桥接，接口按同源路径请求，部分能力（文件上传）可能不可用。";
    }
  }
}

function initContext() {
  const apply = (context) => {
    if (!context || typeof context !== "object") return;
    const locale = context.locale || context.lang || context.language;
    if (locale) {
      document.documentElement.lang = String(locale).toLowerCase().startsWith("en")
        ? "en"
        : "zh-CN";
    }
    if (context.theme && !storedTheme()) {
      applyTheme(String(context.theme) === "dark" ? "dark" : "light", { persist: false });
    }
  };
  apply(getContext());
  onContext(apply);
}

/* ============================== 启动 ============================== */

async function boot() {
  hydrateIcons();
  initTheme();
  initContext();
  applyI18n();
  bindShell();

  let connected = false;
  try {
    connected = await apiReady();
  } catch (err) {
    connected = false;
    showBanner(
      `宿主桥接初始化失败：${err && err.message ? err.message : err}。页面仍可尝试通过同源接口加载。`,
      "error"
    );
  }
  setupConnection(connected || hasBridge());

  const results = await Promise.allSettled([loadPersonas({ force: true }), refreshStatus()]);
  const failed = results.find((item) => item.status === "rejected");
  if (failed) {
    const reason = failed.reason;
    showBanner(
      `初始化失败：${reason && reason.message ? reason.message : reason}。可以点击右上角刷新重试。`,
      "error"
    );
  }
  renderVersion();

  if (!window.location.hash || !/^#\//.test(window.location.hash)) {
    try {
      window.history.replaceState(null, "", "#/personas");
    } catch {
      window.location.hash = "#/personas";
    }
  }
  renderRoute(parseRoute());
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    boot().catch((err) => {
      showBanner(`页面初始化失败：${err && err.message ? err.message : err}`, "error");
    });
  });
} else {
  boot().catch((err) => {
    showBanner(`页面初始化失败：${err && err.message ? err.message : err}`, "error");
  });
}

export default app;

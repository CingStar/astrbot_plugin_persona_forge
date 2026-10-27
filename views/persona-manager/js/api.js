/**
 * 人格工坊 WebUI · 后端桥接
 *
 * 优先使用宿主注入的 window.AstrBotPluginView（历史别名 AstrBotPluginPage）：
 *   bridge.ready()           —— 等待宿主就绪
 *   bridge.apiGet(path, q)   —— GET
 *   bridge.apiPost(path, b)  —— POST JSON
 *   bridge.upload(path, f)   —— multipart 上传（字段名固定 file）
 *   bridge.onContext(cb) / bridge.getContext()
 *   bridge.t(key, vars) / bridge.getLocale()
 *
 * 同时提供 fetch 兜底，便于在浏览器里直接打开页面做本地联调；
 * 所有错误统一抛出 ApiError（含可直接展示的 message）。
 */

export const API_PREFIX = "/astrbot_plugin_persona_forge";

/** 统一错误类型：message 面向用户可直接展示。 */
export class ApiError extends Error {
  constructor(message, options = {}) {
    super(String(message || "请求失败"));
    this.name = "ApiError";
    this.status = Number(options.status) || 0;
    this.endpoint = String(options.endpoint || "");
    this.cause = options.cause ?? null;
  }
}

function getBridge() {
  if (typeof window === "undefined") return null;
  return window.AstrBotPluginView || window.AstrBotPluginPage || null;
}

/** 是否运行在 AstrBot 插件视图容器内。 */
export function hasBridge() {
  return Boolean(getBridge());
}

function messageFrom(payload) {
  if (!payload) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload === "object") {
    const raw = payload.message ?? payload.error ?? payload.msg ?? payload.detail ?? "";
    if (typeof raw === "string") return raw;
    if (raw && typeof raw === "object") return String(raw.message ?? raw.error ?? "");
    return "";
  }
  return String(payload);
}

function normalizeError(err, endpoint) {
  if (err instanceof ApiError) return err;

  let message = "";
  let status = 0;

  if (typeof err === "string") {
    message = err;
  } else if (err && typeof err === "object") {
    message = messageFrom(err);
    status = Number(err.status || err.statusCode || err.code || 0) || 0;
    if (!message && err.body) message = messageFrom(err.body);
  }

  if (!message) message = "请求失败，请稍后重试";
  message = String(message).replace(/^Error:\s*/, "").trim();

  // 部分宿主会把 JSON 字符串塞进 message
  const parsed = message.startsWith("{") ? safeParse(message) : null;
  if (parsed) {
    const inner = messageFrom(parsed);
    if (inner) message = inner;
  }

  return new ApiError(message, { status, endpoint, cause: err });
}

function safeParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function cleanParams(params) {
  const out = {};
  if (!params || typeof params !== "object") return out;
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    out[key] = value;
  }
  return out;
}

function endpointPath(endpoint) {
  return String(endpoint || "")
    .replace(/^\/+/, "")
    .replace(/^astrbot_plugin_persona_forge\//, "");
}

function buildUrl(endpoint, params) {
  const base =
    typeof window !== "undefined" && window.location && window.location.origin
      ? window.location.origin
      : "http://127.0.0.1";
  const url = new URL(`${API_PREFIX}/${endpointPath(endpoint)}`, base);
  for (const [key, value] of Object.entries(cleanParams(params))) {
    url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function readPayload(response) {
  const text = await response.text();
  if (!text) return {};
  const parsed = safeParse(text);
  if (parsed !== null) return parsed;
  return { message: text };
}

async function fetchJson(endpoint, options = {}) {
  if (typeof fetch !== "function") {
    throw new ApiError("当前宿主缺少 fetch，无法访问插件接口", { endpoint });
  }
  const method = options.method || "GET";
  const init = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    cache: "no-store",
  };

  if (options.formData) {
    init.body = options.formData;
  } else if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body ?? {});
  }

  let response;
  try {
    response = await fetch(buildUrl(endpoint, options.params), init);
  } catch (err) {
    throw new ApiError("无法连接插件后端，请确认插件已加载并刷新页面", {
      endpoint,
      cause: err,
    });
  }

  const payload = await readPayload(response);

  if (!response.ok) {
    throw new ApiError(messageFrom(payload) || `接口返回 ${response.status}`, {
      status: response.status,
      endpoint,
    });
  }

  if (payload && typeof payload === "object" && payload.ok === false) {
    throw new ApiError(messageFrom(payload) || "接口返回失败", {
      status: response.status,
      endpoint,
    });
  }

  return payload ?? {};
}

/** 等待宿主 bridge 就绪（无 bridge 时直接返回 false）。 */
let readyPromise = null;

export function apiReady() {
  if (readyPromise) return readyPromise;
  readyPromise = (async () => {
    const bridge = getBridge();
    if (!bridge) return false;
    if (typeof bridge.ready === "function") {
      try {
        await bridge.ready();
      } catch (err) {
        throw normalizeError(err, "ready");
      }
    }
    return true;
  })();
  return readyPromise;
}

/** GET 请求。 */
export async function apiGet(endpoint, params = {}) {
  const bridge = getBridge();
  if (bridge && typeof bridge.apiGet === "function") {
    try {
      return await bridge.apiGet(endpoint, cleanParams(params));
    } catch (err) {
      throw normalizeError(err, endpoint);
    }
  }
  return fetchJson(endpoint, { method: "GET", params });
}

/** POST JSON 请求。 */
export async function apiPost(endpoint, body = {}) {
  const bridge = getBridge();
  if (bridge && typeof bridge.apiPost === "function") {
    try {
      return await bridge.apiPost(endpoint, body ?? {});
    } catch (err) {
      throw normalizeError(err, endpoint);
    }
  }
  return fetchJson(endpoint, { method: "POST", body: body ?? {} });
}

/** multipart 上传（字段名固定 file）。 */
export async function upload(endpoint, file) {
  const bridge = getBridge();
  if (bridge && typeof bridge.upload === "function") {
    try {
      return await bridge.upload(endpoint, file);
    } catch (err) {
      throw normalizeError(err, endpoint);
    }
  }
  if (typeof FormData === "undefined") {
    throw new ApiError("当前宿主不支持文件上传", { endpoint });
  }
  const formData = new FormData();
  formData.append("file", file);
  return fetchJson(endpoint, { method: "POST", formData });
}

/* ============================== 上下文 / 文案 ============================== */

/** 订阅宿主上下文变化（主题、语言、locale 等），返回取消函数。 */
export function onContext(handler) {
  if (typeof handler !== "function") return () => {};
  const bridge = getBridge();
  if (bridge && typeof bridge.onContext === "function") {
    try {
      bridge.onContext(handler);
      return () => {};
    } catch {
      /* 宿主实现异常时退化为一次性回调 */
    }
  }
  const ctx = getContext();
  if (ctx) {
    try {
      handler(ctx);
    } catch {
      /* ignore */
    }
  }
  return () => {};
}

/** 当前宿主上下文。 */
export function getContext() {
  const bridge = getBridge();
  if (bridge && typeof bridge.getContext === "function") {
    try {
      return bridge.getContext() || null;
    } catch {
      return null;
    }
  }
  return null;
}

/** 当前语言代码（zh / en …）。 */
export function getLocale() {
  const bridge = getBridge();
  if (bridge && typeof bridge.getLocale === "function") {
    try {
      const value = bridge.getLocale();
      if (value) return String(value);
    } catch {
      /* ignore */
    }
  }
  const ctx = getContext();
  const fromCtx = ctx && (ctx.locale || ctx.lang || ctx.language);
  if (fromCtx) return String(fromCtx);
  if (typeof navigator !== "undefined" && navigator.language) return String(navigator.language);
  return "zh-CN";
}

const DICT = {
  zh: {
    "nav.personas": "人格",
    "nav.reviews": "审查",
    "nav.life": "生命卡",
    "nav.settings": "设置中心",
    "app.search": "搜索人格、审查或生命卡…",
    "app.current": "当前人格",
    "app.noActive": "未选择",
    "app.iterate": "提炼当前人格",
    "app.refresh": "刷新当前页",
    "app.theme": "切换深色主题",
    "page.personas.title": "人格",
    "page.personas.sub": "选择、切换与打磨每一张人格",
    "page.reviews.title": "审查",
    "page.reviews.sub": "提示词草稿在生效前都需要你确认",
    "page.life.title": "生命卡",
    "page.life.sub": "把人格、记忆与知识库打包成可迁移的卡片",
    "page.settings.title": "设置中心",
    "page.settings.sub": "通用、群聊、QQ 资料、知识库、生命卡与存储",
    "action.save": "保存",
    "action.cancel": "取消",
    "action.confirm": "确认",
    "action.close": "关闭",
    "action.retry": "重试",
    "action.refresh": "刷新",
    "action.approve": "通过",
    "action.reject": "拒绝",
    "state.loading": "加载中…",
    "state.empty": "暂无内容",
    "state.error": "加载失败",
  },
  en: {
    "nav.personas": "Personas",
    "nav.reviews": "Reviews",
    "nav.life": "Life Cards",
    "nav.settings": "Settings",
    "app.search": "Search personas, reviews or life cards…",
    "app.current": "Current",
    "app.noActive": "None",
    "app.iterate": "Refine active persona",
    "app.refresh": "Refresh",
    "app.theme": "Toggle dark theme",
    "page.personas.title": "Personas",
    "page.personas.sub": "Pick, switch and polish every persona",
    "page.reviews.title": "Reviews",
    "page.reviews.sub": "Draft prompts need your approval before going live",
    "page.life.title": "Life Cards",
    "page.life.sub": "Package persona, memory and wiki into portable cards",
    "page.settings.title": "Settings",
    "page.settings.sub": "General, group chat, QQ profile, wiki, life cards and storage",
    "action.save": "Save",
    "action.cancel": "Cancel",
    "action.confirm": "Confirm",
    "action.close": "Close",
    "action.retry": "Retry",
    "action.refresh": "Refresh",
    "action.approve": "Approve",
    "action.reject": "Reject",
    "state.loading": "Loading…",
    "state.empty": "Nothing here yet",
    "state.error": "Failed to load",
  },
};

function interpolate(template, vars) {
  if (!vars || typeof vars !== "object") return template;
  return String(template).replace(/\{(\w+)\}/g, (match, key) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : match
  );
}

/** 取宿主文案：bridge.t 优先，其次内建中英文，最后用 fallback。 */
export function t(key, fallback = "", vars = null) {
  const bridge = getBridge();
  if (bridge && typeof bridge.t === "function") {
    try {
      const value = bridge.t(key, vars);
      if (value && value !== key) return String(value);
    } catch {
      /* ignore */
    }
  }
  const locale = getLocale().toLowerCase().startsWith("en") ? "en" : "zh";
  const text = DICT[locale] && DICT[locale][key] ? DICT[locale][key] : fallback || key;
  return interpolate(text, vars);
}

/* ============================== 接口清单 ============================== */

/**
 * 后端 API 契约（0.3.1）。所有端点都是插件视图 API 的相对路径，
 * 由宿主 bridge 自动补 `/astrbot_plugin_persona_forge` 前缀。
 */
export const endpoints = {
  personas: () => apiGet("personas"),
  status: () => apiGet("status"),

  persona: (pid) => apiGet("persona", { pid }),
  updatePersona: (payload) => apiPost("persona/update", payload),
  uploadAvatar: (pid, file) => upload(`persona/avatar/upload/${encodeURIComponent(pid)}`, file),
  avatarFromUrl: (payload) => apiPost("persona/avatar/url", payload),
  removeAvatar: (pid) => apiPost("persona/avatar/remove", { pid }),
  syncQQ: (pid) => apiPost("persona/sync", { pid }),
  switchPersona: (pid) => apiPost("persona/switch", { pid }),
  iterate: (pid, mode) => apiPost("persona/iterate", { pid, mode }),

  versions: (pid) => apiGet("persona/versions", { pid }),
  version: (pid, n) => apiGet("persona/version", { pid, n }),
  rollbackVersion: (pid, n) => apiPost("persona/version/rollback", { pid, n }),

  dev: (pid) => apiGet("persona/dev", { pid }),
  setDev: (pid, enabled) => apiPost("persona/dev", { pid, enabled }),
  addDevNote: (pid, text) => apiPost("persona/dev/notes", { pid, text }),
  compactDev: (pid) => apiPost("persona/dev/compact", { pid }),
  clearDev: (pid) => apiPost("persona/dev/clear", { pid }),

  memory: (pid) => apiGet("memory", { pid }),
  memoryAction: (payload) => apiPost("memory", payload),
  captureMemory: (pid) => apiPost("memory/capture", { pid }),

  reviews: (status) => apiGet("reviews", { status }),
  review: (pid) => apiGet("review", { pid }),
  reviewAction: (pid, action) => apiPost("reviews/action", { pid, action }),

  settings: () => apiGet("settings"),
  saveSettings: (payload) => apiPost("settings", payload),

  life: () => apiGet("life"),
  lifeBackup: (target) => apiPost("life/backup", { target }),
  lifeRestore: (name) => apiPost("life/restore", { name, confirm: true }),
};

export default endpoints;

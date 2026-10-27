/**
 * 人格工坊 WebUI · DOM 工具
 *
 * 安全约定：本文件（以及全部视图模块）不使用任何 HTML 字符串注入 API
 * （html 赋值 / 插入标记接口）。所有文本一律走 textContent 或
 * createTextNode，结构一律 createElement 或 <template>.cloneNode。
 */

import { parseDiff } from "./util.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const XLINK_NS = "http://www.w3.org/1999/xlink";
const IMAGE_DATA_RE = /^data:image\/(?:png|jpe?g|gif|webp|bmp|avif|svg\+xml)[;,]/i;

/** 这些键按 DOM 属性赋值，其余走 setAttribute。 */
const PROP_KEYS = new Set([
  "value",
  "checked",
  "disabled",
  "selected",
  "hidden",
  "open",
  "type",
  "placeholder",
  "id",
  "name",
  "title",
  "rows",
  "cols",
  "maxLength",
  "min",
  "max",
  "step",
  "accept",
  "multiple",
  "readOnly",
  "required",
  "spellcheck",
  "autocomplete",
  "tabIndex",
  "textContent",
]);

const ATTR_ALIASES = {
  className: "class",
  htmlFor: "for",
  readOnly: "readonly",
  maxLength: "maxlength",
  tabIndex: "tabindex",
};

/* ============================== 查询 ============================== */

export function qs(selector, root = document) {
  return root ? root.querySelector(selector) : null;
}

export function qsa(selector, root = document) {
  return root ? Array.from(root.querySelectorAll(selector)) : [];
}

/* ============================== 元素创建 ============================== */

function appendChild(parent, child) {
  if (child === null || child === undefined || child === false || child === true) return;
  if (Array.isArray(child)) {
    for (const item of child) appendChild(parent, item);
    return;
  }
  if (typeof child === "string" || typeof child === "number") {
    parent.appendChild(document.createTextNode(String(child)));
    return;
  }
  if (child instanceof Node) {
    parent.appendChild(child);
  }
}

/**
 * 创建元素：el("div", { class: "card", dataset: { id } }, "文本", childNode)
 * 文本子节点只会成为 Text 节点，不会被当成 HTML 解析。
 */
export function el(tag, props = null, ...children) {
  const node = document.createElement(String(tag));
  if (props && typeof props === "object") {
    for (const [rawKey, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      const key = rawKey;

      if (key === "class" || key === "className") {
        node.className = String(value);
      } else if (key === "dataset" && typeof value === "object") {
        for (const [dataKey, dataValue] of Object.entries(value)) {
          if (dataValue === null || dataValue === undefined) continue;
          node.dataset[dataKey] = String(dataValue);
        }
      } else if (key === "style" && value && typeof value === "object") {
        for (const [styleKey, styleValue] of Object.entries(value)) {
          if (styleValue === null || styleValue === undefined) continue;
          node.style.setProperty(styleKey, String(styleValue));
        }
      } else if (key === "attrs" && value && typeof value === "object") {
        for (const [attrKey, attrValue] of Object.entries(value)) {
          if (attrValue === null || attrValue === undefined || attrValue === false) continue;
          node.setAttribute(attrKey, String(attrValue));
        }
      } else if (key === "on" && value && typeof value === "object") {
        for (const [eventName, handler] of Object.entries(value)) {
          if (typeof handler !== "function") continue;
          node.addEventListener(eventName, handler);
        }
      } else if (key.startsWith("on") && typeof value === "function" && key.length > 2) {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (PROP_KEYS.has(key)) {
        node[key] = value;
      } else {
        const attr = ATTR_ALIASES[key] || key;
        node.setAttribute(attr, String(value));
      }
    }
  }
  for (const child of children) appendChild(node, child);
  return node;
}

/** 文本节点（等价于 document.createTextNode）。 */
export function textNode(value) {
  return document.createTextNode(String(value ?? ""));
}

/** 向父节点追加若干子节点。 */
export function append(parent, ...children) {
  for (const child of children) appendChild(parent, child);
  return parent;
}

/** 清空子节点（不注入任何 HTML 字符串）。 */
export function clear(node) {
  if (!node) return node;
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** 用新内容替换节点全部子节点。 */
export function replaceChildren(node, ...children) {
  clear(node);
  return append(node, ...children);
}

/** 文档片段。 */
export function frag() {
  return document.createDocumentFragment();
}

/** 惰性获取指定 id 的容器，不存在则创建并挂到 body。 */
export function ensureContainer(id, tag = "div", className = "") {
  let node = document.getElementById(id);
  if (node) return node;
  node = el(tag, { id, class: className });
  document.body.appendChild(node);
  return node;
}

/* ============================== 图标 ============================== */

/** 内联 SVG 图标（引用 index.html 中的 sprite symbol）。 */
export function icon(name, options = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  const size = Number(options.size) > 0 ? Number(options.size) : 18;
  svg.setAttribute("class", `icon${options.className ? ` ${options.className}` : ""}`);
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", options.ariaHidden === false ? "false" : "true");
  svg.setAttribute("focusable", "false");
  if (options.title) {
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = String(options.title);
    svg.appendChild(title);
  }
  const use = document.createElementNS(SVG_NS, "use");
  const href = `#i-${String(name || "").replace(/^i-/, "")}`;
  use.setAttribute("href", href);
  use.setAttributeNS(XLINK_NS, "xlink:href", href);
  svg.appendChild(use);
  return svg;
}

/** 把 [data-icon] 占位元素替换成真实 SVG（HTML 里保持声明式写法）。 */
export function hydrateIcons(root = document) {
  const scope = root && root.querySelectorAll ? root : document;
  for (const holder of qsa("[data-icon]", scope)) {
    const name = holder.getAttribute("data-icon");
    if (!name) continue;
    holder.removeAttribute("data-icon");
    if (holder.childElementCount > 0) continue;
    holder.appendChild(icon(name));
  }
}

/* ============================== 常用控件 ============================== */

/**
 * 按钮：button({ label, icon: "save", variant: "primary", size: "sm",
 *               onClick, title, disabled, busyLabel })
 */
export function button(options = {}) {
  const btn = el("button", {
    type: options.type || "button",
    id: options.id,
    class: [
      "btn",
      options.variant ? `btn-${options.variant}` : "",
      options.size === "sm" ? "btn-sm" : "",
      options.className || "",
    ]
      .filter(Boolean)
      .join(" "),
    title: options.title,
    disabled: options.disabled === true,
    attrs: { "aria-label": options.ariaLabel },
  });
  if (options.icon) btn.appendChild(icon(options.icon, { size: options.size === "sm" ? 15 : 16 }));
  if (options.label !== undefined && options.label !== null && options.label !== "") {
    btn.appendChild(el("span", { class: "btn-label" }, String(options.label)));
  }
  if (typeof options.onClick === "function") {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      options.onClick(event, btn);
    });
  }
  return btn;
}

/** 纯图标按钮。 */
export function iconButton(options = {}) {
  const btn = el("button", {
    type: "button",
    class: `icon-btn${options.className ? ` ${options.className}` : ""}`,
    title: options.title,
    disabled: options.disabled === true,
    attrs: { "aria-label": options.ariaLabel || options.title || "操作" },
  });
  btn.appendChild(icon(options.icon || "dots"));
  if (typeof options.onClick === "function") {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      options.onClick(event, btn);
    });
  }
  return btn;
}

/** 徽标。 */
export function badge(label, variant = "") {
  const node = cloneTemplate("tpl-badge");
  const span = node.firstElementChild || node;
  span.className = `badge${variant ? ` is-${variant}` : ""}`;
  span.textContent = String(label ?? "");
  return span;
}

/** 输入框。 */
export function input(options = {}) {
  return el("input", {
    class: `input${options.className ? ` ${options.className}` : ""}`,
    type: options.type || "text",
    id: options.id,
    name: options.name,
    value: options.value ?? "",
    placeholder: options.placeholder,
    autocomplete: options.autocomplete || "off",
    spellcheck: options.spellcheck === true,
    disabled: options.disabled === true,
    min: options.min,
    max: options.max,
    step: options.step,
    maxLength: options.maxLength,
    accept: options.accept,
    attrs: { "aria-label": options.ariaLabel },
  });
}

/** 多行文本。 */
export function textarea(options = {}) {
  const node = el("textarea", {
    class: `textarea${options.mono ? " mono" : ""}${options.className ? ` ${options.className}` : ""}`,
    id: options.id,
    name: options.name,
    placeholder: options.placeholder,
    rows: options.rows,
    spellcheck: options.spellcheck === true,
    disabled: options.disabled === true,
    maxLength: options.maxLength,
    value: options.value ?? "",
    attrs: { "aria-label": options.ariaLabel },
  });
  return node;
}

/** 下拉选择：options = [{ value, label }] */
export function select(options = {}) {
  const node = el("select", {
    class: `select${options.className ? ` ${options.className}` : ""}`,
    id: options.id,
    name: options.name,
    disabled: options.disabled === true,
    attrs: { "aria-label": options.ariaLabel },
  });
  const current = options.value;
  for (const item of Array.isArray(options.options) ? options.options : []) {
    const opt = el("option", { value: item.value }, String(item.label ?? item.value));
    if (String(item.value) === String(current)) opt.selected = true;
    node.appendChild(opt);
  }
  if (typeof options.onChange === "function") node.addEventListener("change", options.onChange);
  return node;
}

/** 开关。 */
export function toggle(options = {}) {
  const id = options.id || `toggle-${Math.random().toString(36).slice(2, 9)}`;
  const checkbox = el("input", {
    type: "checkbox",
    id,
    name: options.name,
    checked: options.checked === true,
    disabled: options.disabled === true,
  });
  if (typeof options.onChange === "function") checkbox.addEventListener("change", options.onChange);
  const label = el(
    "label",
    { class: "toggle", attrs: { for: id } },
    checkbox,
    el("span", { class: "toggle-track", attrs: { "aria-hidden": "true" } }),
    options.label ? el("span", { class: "toggle-text" }, String(options.label)) : null
  );
  return label;
}

/** 分段控件按钮组。 */
export function segmented(items, activeValue, onChange) {
  const wrap = el("div", { class: "segmented", attrs: { role: "group" } });
  for (const item of Array.isArray(items) ? items : []) {
    const btn = el(
      "button",
      {
        type: "button",
        class: "seg-btn",
        attrs: { "aria-pressed": String(item.value === activeValue), "data-value": item.value },
      },
      String(item.label ?? item.value)
    );
    btn.addEventListener("click", () => {
      if (typeof onChange === "function") onChange(item.value);
    });
    wrap.appendChild(btn);
  }
  return wrap;
}

/** 表单字段容器（label + control + hint）。 */
export function field(options = {}) {
  const node = cloneTemplate("tpl-field");
  const root = node.firstElementChild || node;
  const label = qs(".field-label", root);
  const control = qs(".field-control", root);
  const hint = qs(".field-hint", root);
  const badgeEl = qs(".field-badge", root);

  if (options.wide) root.classList.add("is-wide");
  if (options.id && label) label.setAttribute("for", options.id);
  if (label) label.textContent = String(options.label ?? "");
  if (options.badge && badgeEl) {
    badgeEl.hidden = false;
    badgeEl.textContent = String(options.badge);
  }
  if (options.hint && hint) {
    hint.hidden = false;
    hint.textContent = String(options.hint);
  }
  if (control && options.control) append(control, options.control);
  return root;
}

/** 信息行（键值）。 */
export function infoRow(key, value) {
  return el(
    "div",
    { class: "info-row" },
    el("span", { class: "info-k" }, String(key ?? "")),
    el("span", { class: "info-v" }, String(value ?? "—"))
  );
}

/** 键值小卡片。 */
export function kvCard(key, value) {
  return el(
    "div",
    { class: "kv" },
    el("span", { class: "kv-k" }, String(key ?? "")),
    el("span", { class: "kv-v" }, String(value ?? "—"))
  );
}

/** 提示条（表单内的说明块）。 */
export function notice(message, variant = "info", iconName = "info") {
  const node = el("div", { class: `notice${variant && variant !== "info" ? ` is-${variant}` : ""}` });
  node.appendChild(el("span", { class: "notice-icon" }, icon(iconName, { size: 15 })));
  node.appendChild(el("div", { class: "grow" }, message));
  return node;
}

/* ============================== 模板 ============================== */

/** 克隆 <template> 内容，返回 DocumentFragment。 */
export function cloneTemplate(id) {
  const tpl = document.getElementById(id);
  if (!tpl || !tpl.content) {
    return frag();
  }
  return tpl.content.cloneNode(true);
}

/** 克隆模板并返回第一个元素节点。 */
export function cloneFirst(id) {
  const fragment = cloneTemplate(id);
  const holder = el("div");
  holder.appendChild(fragment);
  const node = holder.firstElementChild;
  return node || el("div");
}

/** 渲染 unified diff：逐行着色，非法内容按上下文行显示。 */
export function diffView(diffText) {
  const wrap = el("div", {
    class: "diff-view",
    attrs: { role: "region", "aria-label": "提示词差异" },
  });
  const lines = parseDiff(diffText);
  if (lines.length === 0) {
    wrap.appendChild(
      el(
        "div",
        { class: "diff-line is-meta" },
        el("span", { class: "diff-no" }, ""),
        el("span", { class: "diff-no" }, ""),
        el("span", { class: "diff-sign" }, ""),
        el("span", { class: "diff-text" }, "没有可显示的差异")
      )
    );
    return wrap;
  }
  for (const line of lines) {
    const fragment = cloneTemplate("tpl-diff-line");
    const row = Array.from(fragment.childNodes).find((node) => node.nodeType === 1);
    if (!row) continue;
    row.className = `diff-line is-${line.type}`;
    const numbers = row.querySelectorAll(".diff-no");
    if (numbers.length >= 2) {
      numbers[0].textContent =
        line.oldNo === null || line.oldNo === undefined ? "" : String(line.oldNo);
      numbers[1].textContent =
        line.newNo === null || line.newNo === undefined ? "" : String(line.newNo);
    }
    const sign = row.querySelector(".diff-sign");
    if (sign) sign.textContent = line.sign || "";
    const content = row.querySelector(".diff-text");
    if (content) content.textContent = line.text === "" ? "\u00a0" : line.text;
    wrap.appendChild(row);
  }
  return wrap;
}

/* ============================== 头像 ============================== */

/** 是否是合法的图片 data URL（头像只接受 data URL，避免外部引用）。 */
export function isImageDataUrl(value) {
  return typeof value === "string" && IMAGE_DATA_RE.test(value.trim());
}

/** 头像节点：有合法 data URL 时渲染 img，否则渲染首字占位。 */
export function avatar(avatarData, fallbackName, options = {}) {
  const wrap = el("span", { class: options.className || "avatar-preview" });
  const data = typeof avatarData === "string" ? avatarData.trim() : "";
  if (isImageDataUrl(data)) {
    const img = el("img", {
      class: "avatar-img",
      alt: options.alt || "",
      src: data,
      attrs: { loading: "lazy", decoding: "async" },
    });
    wrap.appendChild(img);
  } else {
    wrap.appendChild(el("span", { class: "avatar-fallback" }, initialsSafe(fallbackName)));
  }
  return wrap;
}

function initialsSafe(name) {
  const text = String(name ?? "").trim();
  if (!text) return "?";
  const isCjk = /[\u3400-\u4dbf\u4e00-\u9fff]/.test(text);
  if (isCjk) return text.slice(0, 1);
  const words = text.split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) return text.slice(0, 1).toUpperCase();
  return words
    .slice(0, 2)
    .map((word) => word[0] || "")
    .join("")
    .toUpperCase();
}

/** 把头像数据填进已有容器（清空后重新渲染，保留容器自身的 class）。 */
export function fillAvatar(container, avatarData, fallbackName) {
  if (!container) return;
  const rendered = avatar(avatarData, fallbackName, { className: "avatar-preview" });
  const children = Array.from(rendered.childNodes);
  replaceChildren(container, ...children);
}

/* ============================== 状态块 ============================== */

/** 空态：emptyState({ icon, title, hint, actions: [Node] }) */
export function emptyState(options = {}) {
  const node = cloneTemplate("tpl-empty");
  const root = node.firstElementChild || node;
  const iconHost = qs(".empty-icon", root);
  const title = qs(".empty-title", root);
  const hint = qs(".empty-hint", root);
  const actions = qs(".empty-actions", root);

  if (iconHost) iconHost.appendChild(icon(options.icon || "spark", { size: 26 }));
  if (title) title.textContent = String(options.title ?? "暂无内容");
  if (hint) hint.textContent = String(options.hint ?? "");
  for (const action of Array.isArray(options.actions) ? options.actions : []) {
    if (action) actions.appendChild(action);
  }
  return root;
}

/** 骨架卡片网格占位。 */
export function skeletonCards(count = 6) {
  const wrap = el("div", { class: "grid-cards", attrs: { "aria-hidden": "true" } });
  const n = Math.max(1, Math.trunc(Number(count) || 1));
  for (let i = 0; i < n; i += 1) {
    const node = cloneTemplate("tpl-skeleton-card");
    wrap.appendChild(node.firstElementChild || node);
  }
  return wrap;
}

/** 局部忙碌态容器（首次加载时显示骨架）。 */
export function skeletonPanel() {
  return el(
    "div",
    { class: "panel", style: { padding: "22px", display: "flex", "align-items": "center", gap: "12px" } },
    el("span", { class: "spinner", attrs: { "aria-hidden": "true" } }),
    el("span", { class: "muted" }, "加载中…")
  );
}

/* ============================== 忙碌态 ============================== */

/** 按钮/元素进入忙碌状态，返回恢复函数。 */
export function setBusy(node, busy) {
  if (!node) return () => {};
  if (busy) {
    node.classList.add("is-busy");
    node.setAttribute("aria-busy", "true");
    if ("disabled" in node) node.dataset.wasDisabled = node.disabled ? "1" : "0";
    if ("disabled" in node) node.disabled = true;
    return () => {
      node.classList.remove("is-busy");
      node.removeAttribute("aria-busy");
      if ("disabled" in node && node.dataset.wasDisabled !== "1") node.disabled = false;
      if ("disabled" in node) delete node.dataset.wasDisabled;
    };
  }
  node.classList.remove("is-busy");
  node.removeAttribute("aria-busy");
  if ("disabled" in node && node.dataset.wasDisabled !== "1") node.disabled = false;
  if ("disabled" in node) delete node.dataset.wasDisabled;
  return () => {};
}

/** 包裹一次异步操作：按钮忙 + 统一错误 toast。 */
export async function runBusy(node, fn, errorPrefix = "操作失败") {
  const restore = setBusy(node, true);
  try {
    return await fn();
  } catch (err) {
    toast(`${errorPrefix}：${err && err.message ? err.message : err}`, "error");
    return undefined;
  } finally {
    restore();
  }
}

/* ============================== toast / banner ============================== */

const TOAST_ICONS = {
  info: "info",
  success: "review",
  warn: "alert",
  error: "alert",
};

/** 右下角 toast。 */
export function toast(message, kind = "info", timeout = 3600) {
  const host = ensureContainer("toasts", "div", "toasts");
  const node = el("div", { class: `toast is-${kind}`, attrs: { role: "status" } });
  node.appendChild(el("span", { class: "toast-icon" }, icon(TOAST_ICONS[kind] || "info", { size: 16 })));
  node.appendChild(el("span", { class: "grow" }, String(message ?? "")));
  host.appendChild(node);

  const remove = () => {
    if (!node.isConnected) return;
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 200);
  };
  const timer = setTimeout(remove, Math.max(1200, Number(timeout) || 3600));
  node.addEventListener("click", () => {
    clearTimeout(timer);
    remove();
  });
  return node;
}

/** 顶部错误横幅。 */
export function showBanner(message, kind = "error") {
  const banner = document.getElementById("banner");
  const text = document.getElementById("banner-text");
  if (!banner || !text) return;
  banner.className = `banner${kind && kind !== "warn" ? ` is-${kind}` : ""}`;
  text.textContent = String(message ?? "");
  banner.hidden = false;
}

export function hideBanner() {
  const banner = document.getElementById("banner");
  if (banner) banner.hidden = true;
}

/* ============================== Esc / 焦点 ============================== */

const escapeHandlers = [];
let escapeBound = false;

function bindEscape() {
  if (escapeBound || typeof document === "undefined") return;
  escapeBound = true;
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const handler = escapeHandlers[escapeHandlers.length - 1];
    if (!handler) return;
    event.preventDefault();
    event.stopPropagation();
    handler();
  });
}

/** 压入一个 Esc 处理器，返回出栈函数。 */
export function pushEscapeHandler(handler) {
  if (typeof handler !== "function") return () => {};
  bindEscape();
  escapeHandlers.push(handler);
  return () => {
    const index = escapeHandlers.lastIndexOf(handler);
    if (index >= 0) escapeHandlers.splice(index, 1);
  };
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** 限制焦点在容器内，返回释放函数。 */
export function trapFocus(container) {
  if (!container) return () => {};
  const onKeydown = (event) => {
    if (event.key !== "Tab") return;
    const nodes = qsa(FOCUSABLE, container).filter(
      (node) => node.getClientRects().length > 0 || node === document.activeElement
    );
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  container.addEventListener("keydown", onKeydown);
  return () => container.removeEventListener("keydown", onKeydown);
}

/* ============================== 弹窗 ============================== */

/** 关闭弹窗时恢复页面滚动；抽屉仍打开时保持锁定。 */
function releaseBodyLock() {
  const drawer = document.getElementById("drawer");
  if (drawer && !drawer.hidden) return;
  document.body.style.removeProperty("overflow");
}

/**
 * 通用确认框：confirmDialog({ title, message, confirmText, cancelText, danger })
 * 返回 Promise<boolean>。
 */
export function confirmDialog(options = {}) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    if (!root) {
      resolve(false);
      return;
    }
    const previousFocus = document.activeElement;

    const onRootClick = (event) => {
      if (event.target === root) close(false);
    };

    const close = (result) => {
      root.removeEventListener("click", onRootClick);
      releaseFocus();
      releaseEscape();
      root.hidden = true;
      replaceChildren(root);
      releaseBodyLock();
      if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
      resolve(result);
    };

    const modal = el("div", {
      class: "modal",
      attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "modal-title" },
    });
    const head = el(
      "div",
      { class: "modal-head" },
      el(
        "h2",
        { class: "modal-title", id: "modal-title" },
        String(options.title || "请确认")
      )
    );
    head.appendChild(iconButton({ icon: "x", title: "关闭", onClick: () => close(false) }));
    modal.appendChild(head);

    const body = el("div", { class: "modal-body" });
    if (options.message) {
      body.appendChild(el("p", null, String(options.message)));
    }
    if (options.detail) {
      body.appendChild(el("p", { class: "mono", style: { "margin-top": "8px" } }, String(options.detail)));
    }
    modal.appendChild(body);

    const actions = el("div", { class: "modal-foot" });
    actions.appendChild(
      button({
        label: options.cancelText || "取消",
        variant: "ghost",
        onClick: () => close(false),
      })
    );
    const confirmBtn = button({
      label: options.confirmText || "确认",
      variant: options.danger ? "danger" : "primary",
      onClick: () => close(true),
    });
    actions.appendChild(confirmBtn);
    modal.appendChild(actions);

    replaceChildren(root, modal);
    root.hidden = false;
    document.body.style.setProperty("overflow", "hidden");

    const releaseFocus = trapFocus(modal);
    const releaseEscape = pushEscapeHandler(() => close(false));

    root.addEventListener("click", onRootClick);

    setTimeout(() => {
      if (options.danger) confirmBtn.focus();
      else actions.firstElementChild.focus();
    }, 0);
  });
}

/**
 * 选项弹窗：choiceDialog({ title, message, choices: [{ id, icon, title, desc }] })
 * 返回 Promise<string|null>（选中项 id，取消为 null）。
 */
export function choiceDialog(options = {}) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    if (!root) {
      resolve(null);
      return;
    }
    const previousFocus = document.activeElement;
    let selected = null;
    let confirmBtn = null;

    const onRootClick = (event) => {
      if (event.target === root) close(null);
    };

    const close = (result) => {
      root.removeEventListener("click", onRootClick);
      releaseFocus();
      releaseEscape();
      root.hidden = true;
      replaceChildren(root);
      releaseBodyLock();
      if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
      resolve(result);
    };

    const modal = el("div", {
      class: "modal",
      attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "modal-title" },
    });
    const head = el(
      "div",
      { class: "modal-head" },
      el("h2", { class: "modal-title", id: "modal-title" }, String(options.title || "选择操作"))
    );
    head.appendChild(iconButton({ icon: "x", title: "关闭", onClick: () => close(null) }));
    modal.appendChild(head);

    const body = el("div", { class: "modal-body" });
    if (options.message) body.appendChild(el("p", null, String(options.message)));
    const choices = el("div", { class: "modal-choices" });

    const choiceNodes = [];
    const sync = () => {
      for (const item of choiceNodes) {
        item.node.classList.toggle("is-selected", item.id === selected);
        item.node.setAttribute("aria-pressed", String(item.id === selected));
      }
      if (confirmBtn) confirmBtn.disabled = selected === null;
    };

    for (const choice of Array.isArray(options.choices) ? options.choices : []) {
      const node = el("button", {
        type: "button",
        class: "modal-choice",
        attrs: { "aria-pressed": "false" },
      });
      node.appendChild(
        el("span", { class: "choice-icon" }, icon(choice.icon || "spark", { size: 17 }))
      );
      node.appendChild(
        el(
          "span",
          { class: "choice-text" },
          el("span", { class: "choice-title" }, String(choice.title ?? choice.id)),
          choice.desc ? el("span", { class: "choice-desc" }, String(choice.desc)) : null
        )
      );
      node.addEventListener("click", () => {
        selected = choice.id;
        sync();
      });
      node.addEventListener("dblclick", () => {
        selected = choice.id;
        close(selected);
      });
      choiceNodes.push({ id: choice.id, node });
      choices.appendChild(node);
    }
    body.appendChild(choices);
    modal.appendChild(body);

    const actions = el("div", { class: "modal-foot" });
    actions.appendChild(button({ label: "取消", variant: "ghost", onClick: () => close(null) }));
    confirmBtn = button({
      label: options.confirmText || "继续",
      variant: "primary",
      disabled: true,
      onClick: () => close(selected),
    });
    actions.appendChild(confirmBtn);
    modal.appendChild(actions);

    replaceChildren(root, modal);
    root.hidden = false;
    document.body.style.setProperty("overflow", "hidden");

    const releaseFocus = trapFocus(modal);
    const releaseEscape = pushEscapeHandler(() => close(null));

    root.addEventListener("click", onRootClick);

    sync();
    setTimeout(() => {
      if (choiceNodes.length > 0) choiceNodes[0].node.focus();
    }, 0);
  });
}

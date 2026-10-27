/**
 * 人格工坊 WebUI · 纯函数工具集
 *
 * 本模块不依赖 DOM / window，可在 Node 中直接 import 做单元测试。
 * 所有函数都做空值兜底，界面上任何字段缺失都不应导致抛错。
 */

const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/* ============================== 数值 / 文本 ============================== */

/** 数字转 Number，非法返回 fallback。 */
export function toNumber(value, fallback = 0) {
  if (typeof value === "number") return Number.isFinite(value) ? value : fallback;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value.trim());
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

/** 数字 clamp（浮点）。 */
export function clampNumber(value, min, max, fallback = 0) {
  const n = toNumber(value, fallback);
  const lo = toNumber(min, -Infinity);
  const hi = toNumber(max, Infinity);
  if (lo > hi) return fallback;
  return Math.min(hi, Math.max(lo, n));
}

/** 整数 clamp，四舍五入。 */
export function clampInt(value, min, max, fallback = 0) {
  return Math.round(clampNumber(value, min, max, fallback));
}

/** 布尔转换，兼容 "true" / "1" / "on" 等字符串。 */
export function toBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    if (["1", "true", "yes", "on", "enabled"].includes(v)) return true;
    if (["0", "false", "no", "off", "disabled", ""].includes(v)) return false;
  }
  return fallback;
}

/** 是否空白（null / undefined / 纯空白字符串）。 */
export function isBlank(value) {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

/** 截断文本，超出补省略号。 */
export function truncate(value, max = 80) {
  const text = String(value ?? "");
  const limit = toNumber(max, 80);
  if (limit <= 0) return "";
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1))}…` : text;
}

/** 千分位数字。 */
export function formatNumber(value) {
  const n = toNumber(value, 0);
  return String(Math.trunc(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 字节数格式化：0 -> "0 B"，1536 -> "1.5 KB"。 */
export function formatBytes(value) {
  let n = toNumber(value, 0);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  const rounded = Math.round(n * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${text} ${units[i]}`;
}

/** 千字节格式化（接口里 size_kb 字段）。 */
export function formatKb(value) {
  const kb = toNumber(value, 0);
  if (kb <= 0) return "0 KB";
  return formatBytes(kb * 1024);
}

/** 密钥掩码：空串原样返回，非空统一显示圆点。 */
export function maskSecret(value, mask = "••••••") {
  const text = String(value ?? "");
  return text === "" ? "" : mask;
}

/** 头像占位文字：中文取首字，拉丁取前两个词首字母。 */
export function initials(name) {
  const text = String(name ?? "").trim();
  if (!text) return "?";
  if (CJK_RE.test(text)) return text.slice(0, 1);
  const words = text.split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) return text.slice(0, 1).toUpperCase();
  const letters = words.slice(0, 2).map((w) => w[0] || "").join("");
  return (letters || text[0]).toUpperCase();
}

/** 搜索归一化：去首尾空白、转小写。 */
export function normalizeSearch(value) {
  return String(value ?? "").trim().toLowerCase();
}

/** 宽松匹配：needle 为空返回 true；多关键词用空格分隔，需全部命中。 */
export function matchesQuery(haystack, needle) {
  const query = normalizeSearch(needle);
  if (!query) return true;
  const hay = String(haystack ?? "").toLowerCase();
  return query.split(/\s+/).every((part) => hay.includes(part));
}

/* ============================== 列表解析 ============================== */

/**
 * 解析群号/标签列表：支持英文逗号、中文逗号、顿号、分号、空格、换行混合分隔。
 * 返回去重后的字符串数组（保持原始顺序，保留前导 0）。
 */
export function parseWhitelist(value) {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    return dedupe(value.map((item) => String(item ?? "").trim()).filter(Boolean));
  }
  const text = String(value);
  if (!text.trim()) return [];
  return dedupe(
    text
      .split(/[\s,，、;；]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  );
}

/** 任意分隔列表（工具名 / Skills），规则同 parseWhitelist。 */
export function splitList(value) {
  return parseWhitelist(value);
}

/** 反向：列表 -> 逗号分隔字符串。 */
export function formatWhitelist(list, separator = ", ") {
  if (!Array.isArray(list)) return "";
  return list.map((item) => String(item ?? "").trim()).filter(Boolean).join(separator);
}

/** 数组去重（保持顺序）。 */
export function dedupe(list) {
  const seen = new Set();
  const out = [];
  for (const item of Array.isArray(list) ? list : []) {
    if (seen.has(item)) continue;
    seen.add(item);
    out.push(item);
  }
  return out;
}

/* ============================== 时间 ============================== */

/** 把 epoch 秒 / epoch 毫秒 / ISO 字符串 / Date 统一成 Date，非法返回 null。 */
export function toDate(value) {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;

  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0) return null;
    const ms = value < 1e11 ? value * 1000 : value;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  if (typeof value === "string") {
    const text = value.trim();
    if (!text) return null;
    if (/^\d+(\.\d+)?$/.test(text)) return toDate(Number(text));
    const normalized = text.includes("T") ? text : text.replace(" ", "T");
    const d = new Date(normalized);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  return null;
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

/** "YYYY-MM-DD HH:MM"，非法值返回 "—"。 */
export function formatTime(value, options = {}) {
  const date = toDate(value);
  if (!date) return "—";
  const withSeconds = options.seconds === true;
  const base = `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours()
  )}:${pad2(date.getMinutes())}`;
  return withSeconds ? `${base}:${pad2(date.getSeconds())}` : base;
}

/** 仅日期 "YYYY-MM-DD"。 */
export function formatDate(value) {
  const date = toDate(value);
  if (!date) return "—";
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

/** 相对时间："刚刚 / N 分钟前 / N 小时前 / N 天前"，超过 30 天返回日期。 */
export function timeAgo(value, now = Date.now()) {
  const date = toDate(value);
  if (!date) return "—";
  const diff = Math.floor((toNumber(now, Date.now()) - date.getTime()) / 1000);
  if (!Number.isFinite(diff)) return "—";
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return formatDate(date);
}

/** 秒数转可读时长。 */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.trunc(toNumber(seconds, 0)));
  if (total < 60) return `${total} 秒`;
  if (total < 3600) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s ? `${m} 分 ${s} 秒` : `${m} 分钟`;
  }
  if (total < 86400) {
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
  }
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  return h ? `${d} 天 ${h} 小时` : `${d} 天`;
}

/* ============================== diff 解析 ============================== */

/**
 * 解析 unified diff 文本，返回逐行对象：
 * { type: "add"|"del"|"ctx"|"hunk"|"meta", sign, text, oldNo, newNo }
 * 行号从 hunk 头推出的起始值开始自增，便于左右对齐显示。
 */
export function parseDiff(text) {
  const raw = String(text ?? "").replace(/\r\n?/g, "\n");
  if (!raw.trim()) return [];
  const lines = raw.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();

  const out = [];
  let oldNo = null;
  let newNo = null;
  let inHunk = false;

  for (const line of lines) {
    if (line.startsWith("@@")) {
      inHunk = true;
      const match = /^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/.exec(line);
      if (match) {
        oldNo = Number(match[1]);
        newNo = Number(match[2]);
      } else {
        oldNo = null;
        newNo = null;
      }
      out.push({ type: "hunk", sign: "", text: line, oldNo: null, newNo: null });
      continue;
    }

    const isMetaHeader =
      (!inHunk &&
        (line.startsWith("--- ") ||
          line.startsWith("+++ ") ||
          line.startsWith("diff --git ") ||
          line.startsWith("index "))) ||
      line.startsWith("diff --git ") ||
      line.startsWith("index ") ||
      line.startsWith("\\ No newline");

    if (isMetaHeader) {
      out.push({ type: "meta", sign: "", text: line, oldNo: null, newNo: null });
      continue;
    }

    if (line.startsWith("+")) {
      out.push({
        type: "add",
        sign: "+",
        text: line.slice(1),
        oldNo: null,
        newNo,
      });
      if (newNo !== null) newNo += 1;
      continue;
    }

    if (line.startsWith("-")) {
      out.push({
        type: "del",
        sign: "-",
        text: line.slice(1),
        oldNo,
        newNo: null,
      });
      if (oldNo !== null) oldNo += 1;
      continue;
    }

    const content = line.startsWith(" ") ? line.slice(1) : line;
    out.push({
      type: "ctx",
      sign: " ",
      text: content,
      oldNo,
      newNo,
    });
    if (oldNo !== null) oldNo += 1;
    if (newNo !== null) newNo += 1;
  }

  return out;
}

/** diff 增删统计。 */
export function diffStats(lines) {
  let added = 0;
  let removed = 0;
  for (const line of Array.isArray(lines) ? lines : []) {
    if (line && line.type === "add") added += 1;
    else if (line && line.type === "del") removed += 1;
  }
  return { added, removed };
}

/* ============================== JSON / 对象 ============================== */

/** 安全 JSON 解析：返回 { ok, value, error }。 */
export function safeJsonParse(text, fallback = null) {
  const raw = String(text ?? "").trim();
  if (!raw) return { ok: false, value: fallback, error: "空内容" };
  try {
    return { ok: true, value: JSON.parse(raw), error: null };
  } catch (err) {
    return { ok: false, value: fallback, error: err instanceof Error ? err.message : String(err) };
  }
}

/** 稳定序列化（键排序），用于比较表单是否有改动。 */
export function stableStringify(value) {
  const seen = new WeakSet();
  const walk = (node) => {
    if (node === null || typeof node !== "object") return node;
    if (seen.has(node)) return "[Circular]";
    seen.add(node);
    if (Array.isArray(node)) return node.map(walk);
    const out = {};
    for (const key of Object.keys(node).sort()) out[key] = walk(node[key]);
    return out;
  };
  try {
    return JSON.stringify(walk(value));
  } catch {
    return "";
  }
}

/** 去掉 undefined 的键，便于拼 POST 载荷。 */
export function pickDefined(source) {
  const out = {};
  if (!source || typeof source !== "object") return out;
  for (const [key, value] of Object.entries(source)) {
    if (value !== undefined) out[key] = value;
  }
  return out;
}

/* ============================== 其它 ============================== */

/** 简易 debounce，返回的函数带 cancel()。 */
export function debounce(fn, wait = 200) {
  let timer = null;
  const delay = Math.max(0, toNumber(wait, 200));
  const wrapped = (...args) => {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, delay);
  };
  wrapped.cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  return wrapped;
}

let idSeq = 0;

/** 生成 DOM id / 临时 key。 */
export function uniqueId(prefix = "pf") {
  idSeq += 1;
  return `${prefix}-${Date.now().toString(36)}-${idSeq.toString(36)}`;
}

/** 角色状态归一化，兼容接口字段缺失。 */
export function normalizePersona(raw) {
  const item = raw && typeof raw === "object" ? raw : {};
  const id = String(item.id ?? item.persona_id ?? "").trim();
  const nickname = String(item.nickname ?? "").trim() || id || "未命名人格";
  const version = toNumber(item.version ?? item.prompt_version, 0);
  return {
    id,
    nickname,
    avatarData: typeof item.avatar_data === "string" ? item.avatar_data : "",
    avatarSha: String(item.avatar_sha ?? item.avatar_sha256 ?? ""),
    avatarTooLarge: item.avatar_too_large === true,
    ready: toBool(item.ready, false),
    active: toBool(item.active, false),
    isDefault: toBool(item.default, false),
    version,
    updatedAt: item.updated_at ?? null,
    memoryCount: toNumber(item.memory_count, 0),
  };
}

/** 审查条目归一化（待审：带 diff；历史：带 action）。 */
export function normalizeReview(raw) {
  const item = raw && typeof raw === "object" ? raw : {};
  return {
    personaId: String(item.persona_id ?? item.pid ?? "").trim(),
    source: String(item.source ?? "").trim() || "未标注",
    action: String(item.action ?? "").trim(),
    createdAt: item.created_at ?? null,
    diff: typeof item.diff === "string" ? item.diff : "",
    basePrompt: typeof item.base_prompt === "string" ? item.base_prompt : "",
    draftPrompt: typeof item.draft_prompt === "string" ? item.draft_prompt : "",
    status: String(item.status ?? "pending"),
  };
}

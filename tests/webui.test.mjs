/**
 * 人格工坊 WebUI · 纯函数单测（node:test）
 *
 * 运行：node --test tests/webui.test.mjs
 * util.js 在 views/persona-manager/js/ 下，由 views/persona-manager/package.json
 * 的 "type": "module" 声明为 ES Module，因此可以直接 import。
 * 这里的用例只覆盖纯函数（无 DOM / 无网络），不启动浏览器。
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  toNumber,
  clampNumber,
  clampInt,
  toBool,
  isBlank,
  truncate,
  formatNumber,
  formatBytes,
  formatKb,
  maskSecret,
  initials,
  normalizeSearch,
  matchesQuery,
  parseWhitelist,
  splitList,
  formatWhitelist,
  dedupe,
  toDate,
  formatTime,
  formatDate,
  timeAgo,
  formatDuration,
  parseDiff,
  diffStats,
  safeJsonParse,
  stableStringify,
  pickDefined,
  debounce,
  uniqueId,
  normalizePersona,
  normalizeReview,
} from "../views/persona-manager/js/util.js";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("toNumber", async (t) => {
  await t.test("accepts numbers and numeric strings", () => {
    assert.equal(toNumber(12), 12);
    assert.equal(toNumber(1.5), 1.5);
    assert.equal(toNumber("12"), 12);
    assert.equal(toNumber(" 3.25 "), 3.25);
  });
  await t.test("falls back for empty / invalid / non-finite values", () => {
    assert.equal(toNumber("", 5), 5);
    assert.equal(toNumber("  ", 5), 5);
    assert.equal(toNumber("abc", 5), 5);
    assert.equal(toNumber(NaN, 5), 5);
    assert.equal(toNumber(Infinity, 5), 5);
    assert.equal(toNumber(null, 5), 5);
    assert.equal(toNumber(undefined, 5), 5);
    assert.equal(toNumber(true, 5), 5);
  });
});

test("clampNumber / clampInt", async (t) => {
  await t.test("clamps into the closed range", () => {
    assert.equal(clampNumber(50, 0, 100), 50);
    assert.equal(clampNumber(150, 0, 100), 100);
    assert.equal(clampNumber(-5, 0, 100), 0);
    assert.equal(clampNumber("42", 0, 100), 42);
  });
  await t.test("uses the fallback for invalid input and inverted ranges", () => {
    assert.equal(clampNumber("abc", 0, 100, 7), 7);
    assert.equal(clampNumber(5, 100, 0, 9), 9);
  });
  await t.test("clampInt rounds after clamping", () => {
    assert.equal(clampInt(1.6, 0, 10), 2);
    assert.equal(clampInt(-1.2, 0, 10), 0);
    assert.equal(clampInt(10.9, 0, 10), 10);
    assert.equal(clampInt("3.5", 0, 10), 4);
  });
});

test("toBool", async (t) => {
  await t.test("recognises truthy spellings", () => {
    for (const value of [true, 1, "1", "true", " TRUE ", "yes", "on", "enabled"]) {
      assert.equal(toBool(value), true, String(value));
    }
  });
  await t.test("recognises falsy spellings", () => {
    for (const value of [false, 0, "0", "false", "no", "off", "disabled", ""]) {
      assert.equal(toBool(value), false, String(value));
    }
  });
  await t.test("falls back for unknown values", () => {
    assert.equal(toBool("maybe"), false);
    assert.equal(toBool("maybe", true), true);
    assert.equal(toBool(null, true), true);
    assert.equal(toBool(undefined), false);
  });
});

test("isBlank", async (t) => {
  await t.test("blank values", () => {
    for (const value of [null, undefined, "", "   ", [], {}]) {
      assert.equal(isBlank(value), true);
    }
  });
  await t.test("non-blank values", () => {
    for (const value of ["x", " 0 ", [0], { a: 1 }, 0, false]) {
      assert.equal(isBlank(value), false);
    }
  });
});

test("truncate", async (t) => {
  assert.equal(truncate("abc", 5), "abc");
  assert.equal(truncate("abcde", 5), "abcde");
  assert.equal(truncate("abcdef", 4), "abc…");
  assert.equal(truncate("abcdef", 3), "ab…");
  assert.equal(truncate("x", 0), "");
  assert.equal(truncate(null), "");
  assert.equal(truncate(12345, 3), "12…");
});

test("formatNumber", async (t) => {
  assert.equal(formatNumber(0), "0");
  assert.equal(formatNumber(999), "999");
  assert.equal(formatNumber(1000), "1,000");
  assert.equal(formatNumber(1234567), "1,234,567");
  assert.equal(formatNumber("1234"), "1,234");
  assert.equal(formatNumber(-1234), "-1,234");
  assert.equal(formatNumber(1234.9), "1,234");
});

test("formatBytes / formatKb", async (t) => {
  await t.test("byte units", () => {
    assert.equal(formatBytes(0), "0 B");
    assert.equal(formatBytes(-5), "0 B");
    assert.equal(formatBytes(null), "0 B");
    assert.equal(formatBytes(1023), "1023 B");
    assert.equal(formatBytes(1024), "1 KB");
    assert.equal(formatBytes(1536), "1.5 KB");
    assert.equal(formatBytes("2048"), "2 KB");
    assert.equal(formatBytes(1024 * 1024), "1 MB");
    assert.equal(formatBytes(1024 ** 4), "1 TB");
  });
  await t.test("size_kb helper used by the life-card page", () => {
    assert.equal(formatKb(0), "0 KB");
    assert.equal(formatKb(512), "512 KB");
    assert.equal(formatKb(1536), "1.5 MB");
  });
});

test("maskSecret", async (t) => {
  assert.equal(maskSecret(""), "");
  assert.equal(maskSecret(null), "");
  assert.equal(maskSecret("token"), "••••••");
  assert.equal(maskSecret("token", "***"), "***");
  assert.ok(!maskSecret("token").includes("token"));
});

test("initials", async (t) => {
  assert.equal(initials("温言"), "温");
  assert.equal(initials("John Doe"), "JD");
  assert.equal(initials("alice"), "A");
  assert.equal(initials("a_b"), "AB");
  assert.equal(initials(""), "?");
  assert.equal(initials("   "), "?");
  assert.equal(initials(null), "?");
});

test("normalizeSearch / matchesQuery", async (t) => {
  assert.equal(normalizeSearch("  ABC "), "abc");
  assert.equal(normalizeSearch(null), "");
  assert.equal(matchesQuery("温言很温柔", "温言 温柔"), true);
  assert.equal(matchesQuery("ABC", "abc"), true);
  assert.equal(matchesQuery("abc", "xyz"), false);
  assert.equal(matchesQuery("abc", ""), true);
  assert.equal(matchesQuery(null, ""), true);
  assert.equal(matchesQuery(null, "x"), false);
});

test("parseWhitelist / splitList", async (t) => {
  await t.test("empty inputs", () => {
    assert.deepEqual(parseWhitelist(null), []);
    assert.deepEqual(parseWhitelist(undefined), []);
    assert.deepEqual(parseWhitelist("   "), []);
    assert.deepEqual(parseWhitelist([]), []);
  });
  await t.test("Chinese / English comma, ideographic comma, semicolon, space, newline, tab", () => {
    const raw = "123456，234567、345678;456789；567890 678901\n789012\t890123";
    assert.deepEqual(parseWhitelist(raw), [
      "123456",
      "234567",
      "345678",
      "456789",
      "567890",
      "678901",
      "789012",
      "890123",
    ]);
  });
  await t.test("trims, dedupes, keeps leading zeros and order", () => {
    assert.deepEqual(parseWhitelist("00123, 00123 , 456"), ["00123", "456"]);
    assert.deepEqual(parseWhitelist([" 123 ", "123", "", "456"]), ["123", "456"]);
    assert.deepEqual(parseWhitelist([123, 456]), ["123", "456"]);
  });
  await t.test("splitList mirrors parseWhitelist", () => {
    assert.deepEqual(splitList("a,b，c"), ["a", "b", "c"]);
    assert.deepEqual(splitList(null), []);
  });
});

test("formatWhitelist / dedupe", async (t) => {
  assert.equal(formatWhitelist(["1", "2"]), "1, 2");
  assert.equal(formatWhitelist(["1", "2"], "|"), "1|2");
  assert.equal(formatWhitelist(["a", "", null]), "a");
  assert.equal(formatWhitelist("nope"), "");
  assert.deepEqual(dedupe(["a", "b", "a"]), ["a", "b"]);
  assert.deepEqual(dedupe([]), []);
  assert.deepEqual(dedupe("nope"), []);
});

test("toDate", async (t) => {
  await t.test("epoch seconds and milliseconds", () => {
    assert.equal(toDate(1700000000).getTime(), 1700000000000);
    assert.equal(toDate(1700000000000).getTime(), 1700000000000);
    assert.equal(toDate("1700000000").getTime(), 1700000000000);
  });
  await t.test("ISO / space separated strings", () => {
    assert.equal(toDate("2024-01-02T03:04:05").getFullYear(), 2024);
    assert.equal(toDate("2024-01-02 03:04:05").getFullYear(), 2024);
  });
  await t.test("invalid inputs return null", () => {
    for (const value of [null, undefined, "", "   ", 0, -5, "not-a-date", new Date("nope")]) {
      assert.equal(toDate(value), null, String(value));
    }
  });
  await t.test("accepts a Date instance", () => {
    const date = new Date(2024, 0, 2, 3, 4, 5);
    assert.equal(toDate(date).getTime(), date.getTime());
  });
});

test("formatTime / formatDate", async (t) => {
  const date = new Date(2024, 0, 2, 3, 4, 5);
  assert.equal(formatTime(date), "2024-01-02 03:04");
  assert.equal(formatTime(date, { seconds: true }), "2024-01-02 03:04:05");
  assert.equal(formatTime("nope"), "—");
  assert.equal(formatDate(date), "2024-01-02");
  assert.equal(formatDate(null), "—");
});

test("timeAgo", async (t) => {
  const now = new Date(2024, 5, 1, 12, 0, 0);
  const nowMs = now.getTime();
  const at = (seconds) => nowMs - seconds * 1000;
  assert.equal(timeAgo(at(10), nowMs), "刚刚");
  assert.equal(timeAgo(at(90), nowMs), "1 分钟前");
  assert.equal(timeAgo(at(3700), nowMs), "1 小时前");
  assert.equal(timeAgo(at(90000), nowMs), "1 天前");
  assert.equal(timeAgo(at(86400 * 29), nowMs), "29 天前");
  assert.match(timeAgo(at(86400 * 40), nowMs), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(timeAgo("nope", nowMs), "—");
});

test("formatDuration", async (t) => {
  assert.equal(formatDuration(0), "0 秒");
  assert.equal(formatDuration(-3), "0 秒");
  assert.equal(formatDuration(59), "59 秒");
  assert.equal(formatDuration(60), "1 分钟");
  assert.equal(formatDuration(90), "1 分 30 秒");
  assert.equal(formatDuration(3600), "1 小时");
  assert.equal(formatDuration(3660), "1 小时 1 分");
  assert.equal(formatDuration(86400), "1 天");
  assert.equal(formatDuration(90000), "1 天 1 小时");
});

test("parseDiff", async (t) => {
  const diff = [
    "diff --git a/x b/x",
    "index 1234567..89abcde 100644",
    "--- a/x",
    "+++ b/x",
    "@@ -1,3 +1,4 @@",
    " line one",
    "-old two",
    "+new two",
    "+added",
    " line three",
    "\\ No newline at end of file",
  ].join("\n");

  await t.test("empty input", () => {
    assert.deepEqual(parseDiff(""), []);
    assert.deepEqual(parseDiff("  \n "), []);
    assert.deepEqual(parseDiff(null), []);
  });

  await t.test("classifies every line of the review diff", () => {
    const lines = parseDiff(diff);
    assert.deepEqual(
      lines.map((line) => line.type),
      ["meta", "meta", "meta", "meta", "hunk", "ctx", "del", "add", "add", "ctx", "meta"]
    );
    assert.equal(lines[6].text, "old two");
    assert.equal(lines[6].sign, "-");
    assert.equal(lines[7].text, "new two");
    assert.equal(lines[7].sign, "+");
    assert.equal(lines[5].text, "line one");
    assert.equal(lines[10].text, "\\ No newline at end of file");
  });

  await t.test("tracks old/new line numbers from the hunk header", () => {
    const lines = parseDiff(diff);
    const ctx1 = lines[5];
    const del = lines[6];
    const add1 = lines[7];
    const add2 = lines[8];
    const ctx2 = lines[9];
    assert.deepEqual([ctx1.oldNo, ctx1.newNo], [1, 1]);
    assert.deepEqual([del.oldNo, del.newNo], [2, null]);
    assert.deepEqual([add1.oldNo, add1.newNo], [null, 2]);
    assert.deepEqual([add2.oldNo, add2.newNo], [null, 3]);
    assert.deepEqual([ctx2.oldNo, ctx2.newNo], [3, 4]);
    assert.deepEqual([lines[4].oldNo, lines[4].newNo], [null, null]);
  });

  await t.test("diffStats counts additions and deletions", () => {
    assert.deepEqual(diffStats(parseDiff(diff)), { added: 2, removed: 1 });
    assert.deepEqual(diffStats(null), { added: 0, removed: 0 });
    assert.deepEqual(diffStats([{ type: "ctx" }, { type: "add" }]), { added: 1, removed: 0 });
  });
});

test("safeJsonParse", async (t) => {
  const ok = safeJsonParse('{"a":1}');
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.value, { a: 1 });
  assert.equal(ok.error, null);

  const empty = safeJsonParse("   ");
  assert.equal(empty.ok, false);
  assert.equal(empty.value, null);
  assert.equal(empty.error, "空内容");

  const bad = safeJsonParse("{oops", { fallback: true });
  assert.equal(bad.ok, false);
  assert.deepEqual(bad.value, { fallback: true });
  assert.equal(typeof bad.error, "string");
  assert.ok(bad.error.length > 0);
});

test("stableStringify / pickDefined", async (t) => {
  assert.equal(stableStringify({ b: 1, a: 2 }), stableStringify({ a: 2, b: 1 }));
  assert.equal(stableStringify({ b: 1, a: 2 }), '{"a":2,"b":1}');
  assert.notEqual(stableStringify([1, 2]), stableStringify([2, 1]));
  assert.equal(stableStringify("x"), '"x"');
  assert.equal(stableStringify(undefined), undefined);

  const circular = {};
  circular.self = circular;
  assert.ok(stableStringify(circular).includes("[Circular]"));

  assert.deepEqual(pickDefined({ a: 1, b: undefined, c: null, d: false }), {
    a: 1,
    c: null,
    d: false,
  });
  assert.deepEqual(pickDefined(null), {});
  assert.deepEqual(pickDefined("x"), {});
});

test("debounce", async (t) => {
  await t.test("collapses bursts and keeps the last arguments", async () => {
    const calls = [];
    const fn = debounce((value) => calls.push(value), 20);
    assert.equal(typeof fn.cancel, "function");
    fn(1);
    fn(2);
    assert.deepEqual(calls, []);
    await wait(60);
    assert.deepEqual(calls, [2]);
  });

  await t.test("cancel drops the pending call", async () => {
    const calls = [];
    const fn = debounce((value) => calls.push(value), 20);
    fn(1);
    fn.cancel();
    await wait(60);
    assert.deepEqual(calls, []);
  });
});

test("uniqueId", async (t) => {
  const a = uniqueId();
  const b = uniqueId();
  const prefixed = uniqueId("pf-card");
  assert.ok(a.startsWith("pf-"));
  assert.ok(prefixed.startsWith("pf-card-"));
  assert.notEqual(a, b);
});

test("normalizePersona", async (t) => {
  await t.test("defaults for empty input", () => {
    const persona = normalizePersona(null);
    assert.equal(persona.id, "");
    assert.equal(persona.nickname, "未命名人格");
    assert.equal(persona.avatarData, "");
    assert.equal(persona.avatarSha, "");
    assert.equal(persona.avatarTooLarge, false);
    assert.equal(persona.ready, false);
    assert.equal(persona.active, false);
    assert.equal(persona.isDefault, false);
    assert.equal(persona.version, 0);
    assert.equal(persona.updatedAt, null);
    assert.equal(persona.memoryCount, 0);
  });
  await t.test("maps the backend personas payload", () => {
    const persona = normalizePersona({
      persona_id: "p1",
      nickname: " 温言 ",
      avatar_data: "data:image/png;base64,AAA",
      avatar_sha256: "abc123",
      avatar_too_large: true,
      ready: 1,
      active: "true",
      default: "on",
      prompt_version: "3",
      updated_at: 1700000000,
      memory_count: "5",
    });
    assert.equal(persona.id, "p1");
    assert.equal(persona.nickname, "温言");
    assert.equal(persona.avatarData, "data:image/png;base64,AAA");
    assert.equal(persona.avatarSha, "abc123");
    assert.equal(persona.avatarTooLarge, true);
    assert.equal(persona.ready, true);
    assert.equal(persona.active, true);
    assert.equal(persona.isDefault, true);
    assert.equal(persona.version, 3);
    assert.equal(persona.updatedAt, 1700000000);
    assert.equal(persona.memoryCount, 5);
  });
  await t.test("prefers id over persona_id when both exist", () => {
    const persona = normalizePersona({ id: "a", persona_id: "b" });
    assert.equal(persona.id, "a");
    assert.equal(persona.nickname, "a");
  });
});

test("normalizeReview", async (t) => {
  await t.test("defaults for empty input", () => {
    const review = normalizeReview(undefined);
    assert.equal(review.personaId, "");
    assert.equal(review.source, "未标注");
    assert.equal(review.action, "");
    assert.equal(review.createdAt, null);
    assert.equal(review.diff, "");
    assert.equal(review.basePrompt, "");
    assert.equal(review.draftPrompt, "");
    assert.equal(review.status, "pending");
  });
  await t.test("maps the backend review payload", () => {
    const review = normalizeReview({
      persona_id: "p2",
      source: "dev",
      action: "approve",
      created_at: 1700000000,
      diff: "+a",
      base_prompt: "base",
      draft_prompt: "draft",
      status: "done",
    });
    assert.deepEqual(review, {
      personaId: "p2",
      source: "dev",
      action: "approve",
      createdAt: 1700000000,
      diff: "+a",
      basePrompt: "base",
      draftPrompt: "draft",
      status: "done",
    });
  });
  await t.test("falls back to pid when persona_id is missing", () => {
    assert.equal(normalizeReview({ pid: "p3" }).personaId, "p3");
  });
});

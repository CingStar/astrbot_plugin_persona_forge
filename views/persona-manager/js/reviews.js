/**
 * 审查页：待审草稿 / 历史记录 + diff 着色 + 说明侧栏
 *
 * 数据来源：
 *   GET reviews?status=pending  -> { reviews: [{ persona_id, source, created_at, diff, base_prompt, draft_prompt }] }
 *   GET reviews?status=history  -> { history: [{ persona_id, action, source, created_at }] }
 *   GET review?pid=             -> 单条草稿详情
 *   POST reviews/action {pid, action: approve|reject}
 */

import { endpoints } from "./api.js";
import {
  badge,
  button,
  clear,
  confirmDialog,
  diffView,
  el,
  emptyState,
  icon,
  notice,
  replaceChildren,
  runBusy,
  skeletonPanel,
  toast,
} from "./dom.js";
import {
  diffStats,
  formatNumber,
  formatTime,
  matchesQuery,
  normalizeReview,
  parseDiff,
  timeAgo,
} from "./util.js";

const ACTION_LABELS = { approve: "已通过", reject: "已拒绝" };

export function mount(app, root) {
  const page = el("section", { class: "page", id: "page-reviews" });

  const sub = el("p", { class: "page-sub" }, "正在加载审查队列…");
  const head = el(
    "div",
    { class: "page-head" },
    el(
      "div",
      { class: "grow" },
      el("h1", { class: "page-title" }, icon("review", { size: 24 }), "审查"),
      sub
    )
  );

  const actions = el("div", { class: "page-actions" });
  const segmentedHost = el("div", { class: "segmented", attrs: { role: "group", "aria-label": "审查视图" } });
  const refreshBtn = button({ icon: "refresh", label: "刷新", onClick: () => load({ force: true }) });
  actions.append(segmentedHost, refreshBtn);
  head.appendChild(actions);

  const listPanel = el(
    "div",
    { class: "review-list-panel" },
    el(
      "div",
      { class: "review-list-head" },
      el("span", null, "队列"),
      el("span", { class: "grow" }),
      el("span", { id: "review-count" }, "")
    ),
    el("div", { class: "review-list", id: "review-list" })
  );
  const detailHost = el("div", { class: "review-detail", id: "review-detail" });
  const layout = el("div", { class: "reviews-layout" }, listPanel, detailHost);
  page.append(head, layout);
  root.appendChild(page);

  let destroyed = false;
  let mode = "pending";
  let query = app.getSearch ? app.getSearch() : "";
  let items = [];
  let selectedPid = "";
  let detail = null;
  let loadToken = 0;

  const countEl = () => document.getElementById("review-count");
  const listEl = () => document.getElementById("review-list");

  const unsubscribe = app.onPersonasChanged(() => {
    if (!destroyed) renderList();
  });

  function nicknameOf(pid) {
    const personas = app.getPersonas ? app.getPersonas() : [];
    const found = personas.find((persona) => persona.id === pid);
    return found ? found.nickname : pid;
  }

  function renderSegmented() {
    replaceChildren(segmentedHost);
    for (const option of [
      { value: "pending", label: "待审" },
      { value: "history", label: "历史" },
    ]) {
      const btn = el(
        "button",
        {
          type: "button",
          class: "seg-btn",
          attrs: { "aria-pressed": String(option.value === mode) },
        },
        option.label
      );
      btn.addEventListener("click", () => {
        if (mode === option.value) return;
        mode = option.value;
        selectedPid = "";
        detail = null;
        renderSegmented();
        load();
      });
      segmentedHost.appendChild(btn);
    }
  }

  async function load() {
    const token = ++loadToken;
    clear(listEl());
    replaceChildren(detailHost, skeletonPanel());
    if (sub) sub.textContent = "正在加载审查队列…";
    try {
      const data = await endpoints.reviews(mode);
      if (destroyed || token !== loadToken) return;
      const raw = mode === "history"
        ? (Array.isArray(data && data.history) ? data.history : [])
        : (Array.isArray(data && data.reviews) ? data.reviews : []);
      items = raw.map(normalizeReview);
      if (app.clearError) app.clearError();
      renderList();
      const first = filteredItems()[0];
      if (first) select(first.personaId === selectedPid ? selectedPid : first.personaId);
      else if (mode === "pending") {
        selectedPid = "";
        renderEmptyDetail("pending");
      } else {
        selectedPid = "";
        renderEmptyDetail("history");
      }
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      items = [];
      renderList();
      replaceChildren(
        detailHost,
        emptyState({
          icon: "alert",
          title: "审查队列加载失败",
          hint: err && err.message ? err.message : "请稍后重试。",
          actions: [button({ icon: "refresh", label: "重试", variant: "primary", onClick: () => load() })],
        })
      );
      sub.textContent = "加载失败";
      if (app.setError) app.setError(err && err.message ? err.message : "加载失败");
    }
  }

  function filteredItems() {
    if (!query) return items;
    return items.filter((item) =>
      matchesQuery(`${item.personaId} ${nicknameOf(item.personaId)} ${item.source}`, query)
    );
  }

  function renderList() {
    if (destroyed) return;
    const host = listEl();
    if (!host) return;
    replaceChildren(host);
    const visible = filteredItems();
    const counter = countEl();
    if (counter) {
      counter.textContent = `${formatNumber(visible.length)}${query ? ` / ${formatNumber(items.length)}` : ""} 条`;
    }
    if (sub) {
      sub.textContent = mode === "pending"
        ? `待审 ${items.length} 条${query ? `（筛选中：${visible.length} 条）` : ""} · 通过后才会写回人格提示词`
        : `共 ${items.length} 条历史${query ? `（筛选中：${visible.length} 条）` : ""} · 记录最近 100 次审查动作`;
    }
    if (visible.length === 0) {
      host.appendChild(
        el(
          "p",
          { class: "drawer-note", style: { padding: "10px 6px" } },
          items.length === 0
            ? mode === "pending"
              ? "当前没有待审草稿。"
              : "还没有审查历史。"
            : "没有匹配的条目。"
        )
      );
      return;
    }
    for (const item of visible) host.appendChild(buildListItem(item));
  }

  function buildListItem(item) {
    const node = el(
      "button",
      {
        type: "button",
        class: `review-item${item.personaId === selectedPid ? " is-active" : ""}`,
        attrs: { "aria-current": item.personaId === selectedPid ? "true" : "false" },
      }
    );
    const top = el(
      "div",
      { class: "review-item-top" },
      el("span", { class: "review-item-id" }, item.personaId || "(未知人格)"),
      el("span", { class: "grow" })
    );
    if (mode === "history" && item.action) {
      top.appendChild(
        badge(
          ACTION_LABELS[item.action] || item.action,
          item.action === "approve" ? "success" : "danger"
        )
      );
    } else {
      top.appendChild(badge(item.source || "草稿", "muted"));
    }
    node.appendChild(top);

    const meta = el("div", { class: "review-item-meta" });
    meta.appendChild(el("span", null, nicknameOf(item.personaId) || "—"));
    meta.appendChild(el("span", null, "·"));
    meta.appendChild(el("span", null, timeAgo(item.createdAt)));
    if (mode === "pending" && item.diff) {
      const stats = diffStats(parseDiff(item.diff));
      meta.appendChild(el("span", { class: "badge is-success" }, `+${stats.added}`));
      meta.appendChild(el("span", { class: "badge is-danger" }, `-${stats.removed}`));
    }
    node.appendChild(meta);

    node.addEventListener("click", () => select(item.personaId));
    return node;
  }

  async function select(pid) {
    const personaId = String(pid || "");
    if (!personaId) return;
    selectedPid = personaId;
    detail = null;
    renderList();

    const item = items.find((entry) => entry.personaId === personaId) || null;
    if (mode === "history" || !item) {
      renderHistoryDetail(personaId, item);
      return;
    }

    if (item.diff || item.draftPrompt) {
      renderPendingDetail(item);
      return;
    }

    const token = ++loadToken;
    replaceChildren(detailHost, skeletonPanel());
    try {
      const data = await endpoints.review(personaId);
      if (destroyed || token !== loadToken) return;
      const fresh = normalizeReview({ ...data, persona_id: (data && data.persona_id) || personaId });
      detail = fresh;
      renderPendingDetail(fresh);
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      replaceChildren(
        detailHost,
        emptyState({
          icon: "alert",
          title: "草稿加载失败",
          hint: err && err.message ? err.message : "请稍后重试。",
          actions: [
            button({ icon: "refresh", label: "重试", variant: "primary", onClick: () => select(personaId) }),
          ],
        })
      );
    }
  }

  function renderEmptyDetail(kind) {
    replaceChildren(
      detailHost,
      emptyState({
        icon: kind === "pending" ? "review" : "history",
        title: kind === "pending" ? "没有待审草稿" : "还没有审查历史",
        hint:
          kind === "pending"
            ? "在人格抽屉里用「知识库提炼 / 模型提炼」，或在开发者模式里生成教学草稿，都会先落到这里等你确认。"
            : "每次通过或拒绝草稿都会留下一条记录，方便回溯。",
        actions: [button({ icon: "refresh", label: "刷新", onClick: () => load() })],
      })
    );
  }

  function renderPendingDetail(review) {
    const stats = diffStats(parseDiff(review.diff));
    const head = el("div", { class: "review-detail-head" });
    const title = el(
      "div",
      { class: "review-detail-title" },
      icon("pencil", { size: 18 }),
      el("span", null, `${nicknameOf(review.personaId) || review.personaId} 的提示词草稿`)
    );
    const meta = el(
      "div",
      { class: "review-detail-meta" },
      el("span", { class: "mono" }, review.personaId),
      el("span", null, "·"),
      el("span", null, `来源：${review.source}`),
      el("span", null, "·"),
      el("span", null, formatTime(review.createdAt)),
      el("span", { class: "badge is-success" }, `+${stats.added}`),
      el("span", { class: "badge is-danger" }, `-${stats.removed}`)
    );

    const approveBtn = button({
      icon: "review",
      label: "通过并应用",
      variant: "primary",
      onClick: async (event, btn) => {
        const ok = await confirmDialog({
          title: "通过这份草稿",
          message: `通过后会用草稿覆盖人格「${nicknameOf(review.personaId) || review.personaId}」的提示词，并自动生成版本快照。`,
          confirmText: "通过",
        });
        if (!ok) return;
        await runBusy(btn, () => act(review.personaId, "approve"), "通过失败");
      },
    });
    const rejectBtn = button({
      icon: "trash",
      label: "拒绝",
      variant: "danger",
      onClick: async (event, btn) => {
        const ok = await confirmDialog({
          title: "拒绝这份草稿",
          message: "拒绝会删除该草稿，人格提示词保持不变。确认拒绝吗？",
          confirmText: "拒绝",
          danger: true,
        });
        if (!ok) return;
        await runBusy(btn, () => act(review.personaId, "reject"), "拒绝失败");
      },
    });
    const detailActions = el("div", { class: "review-detail-actions" }, approveBtn, rejectBtn);
    head.append(title, meta, detailActions);

    const diffPanel = el("div", { class: "diff-panel" });
    diffPanel.appendChild(
      el(
        "div",
        { class: "diff-panel-head" },
        el("span", null, "提示词差异 diff"),
        el(
          "div",
          { class: "diff-legend" },
          el("span", { class: "legend-item" }, el("span", { class: "legend-dot is-add" }), "新增"),
          el("span", { class: "legend-item" }, el("span", { class: "legend-dot is-del" }), "删除")
        )
      )
    );
    if (review.diff && review.diff.trim()) {
      diffPanel.appendChild(diffView(review.diff));
    } else {
      diffPanel.appendChild(
        el("p", { class: "drawer-note" }, "接口没有返回 diff，可在右侧查看草稿全文。")
      );
    }

    const notes = el("div", { class: "review-notes" });
    notes.appendChild(
      noteBlock("草稿来源", `${review.source} · ${formatTime(review.createdAt)}`, false)
    );
    if (review.basePrompt) {
      notes.appendChild(noteBlock("当前提示词（base）", review.basePrompt, true));
    }
    if (review.draftPrompt) {
      notes.appendChild(noteBlock("草稿提示词（draft）", review.draftPrompt, true));
    }

    replaceChildren(detailHost, head, el("div", { class: "review-columns" }, diffPanel, notes));
  }

  function renderHistoryDetail(pid, item) {
    const head = el("div", { class: "review-detail-head" });
    const title = el(
      "div",
      { class: "review-detail-title" },
      icon("history", { size: 18 }),
      el("span", null, `${nicknameOf(pid) || pid} 的审查记录`)
    );
    const meta = el(
      "div",
      { class: "review-detail-meta" },
      el("span", { class: "mono" }, pid),
      el("span", null, "·"),
      el("span", null, item ? `来源：${item.source || "—"}` : "来源：—"),
      el("span", null, "·"),
      el("span", null, item ? formatTime(item.createdAt) : "—")
    );
    head.append(title, meta);

    const notes = el(
      "div",
      { class: "review-notes" },
      noteBlock(
        "动作",
        item && item.action ? ACTION_LABELS[item.action] || item.action : "—",
        false
      ),
      noteBlock("说明", "历史记录只保留动作、来源与时间，不保留草稿正文。", false)
    );

    replaceChildren(
      detailHost,
      head,
      notice("历史记录是审查动作的审计日志；草稿正文在通过或拒绝后即被清空。", "info"),
      notes
    );
  }

  function noteBlock(title, text, preformatted) {
    const block = el("div", { class: "notes-block" });
    block.appendChild(el("div", { class: "notes-block-title" }, title));
    block.appendChild(
      preformatted
        ? el("pre", { class: "notes-block-body" }, text)
        : el("p", { class: "notes-block-body" }, text)
    );
    return block;
  }

  async function act(pid, action) {
    const result = await endpoints.reviewAction(pid, action);
    toast((result && result.message) || (action === "approve" ? "草稿已通过" : "草稿已拒绝"), "success");
    if (app.loadPersonas) await app.loadPersonas({ force: true });
    if (app.refreshStatus) await app.refreshStatus();
    selectedPid = "";
    await load({ force: true });
  }

  function setSearch(value) {
    query = String(value ?? "");
    renderList();
    if (selectedPid && !filteredItems().some((item) => item.personaId === selectedPid)) {
      selectedPid = "";
      detail = null;
      if (mode === "pending") renderEmptyDetail("pending");
      else renderEmptyDetail("history");
    }
  }

  renderSegmented();
  load();

  return {
    destroy() {
      destroyed = true;
      unsubscribe();
    },
    setSearch,
    reload() {
      return load({ force: true });
    },
  };
}

/**
 * 生命卡页：按人格分组展示备份卡片，支持备份 / 恢复。
 *
 * 数据来源：
 *   GET  life              -> { cards, personas:[{id,nickname,latest}], tasks, allow_restore, max_card_mb }
 *   POST life/backup       -> { target: "plugin" | "persona:<id>" | "shared" }
 *   POST life/restore      -> { name, confirm: true }
 */

import { endpoints } from "./api.js";
import {
  badge,
  button,
  clear,
  confirmDialog,
  el,
  emptyState,
  fillAvatar,
  icon,
  notice,
  replaceChildren,
  runBusy,
  skeletonPanel,
  toast,
} from "./dom.js";
import {
  formatKb,
  formatNumber,
  formatTime,
  matchesQuery,
  timeAgo,
  toNumber,
  truncate,
} from "./util.js";

const TYPE_LABELS = { persona: "人格卡", shared: "共享卡", host: "宿主卡" };
const TYPE_VARIANTS = { persona: "", shared: "success", host: "warn" };

const STATUS_VARIANTS = {
  ok: "success",
  success: "success",
  done: "success",
  completed: "success",
  pending: "warn",
  running: "warn",
  error: "danger",
  failed: "danger",
};

export function mount(app, root) {
  const page = el("section", { class: "page", id: "page-life" });

  const sub = el("p", { class: "page-sub" }, "正在加载生命卡…");
  const head = el(
    "div",
    { class: "page-head" },
    el(
      "div",
      { class: "grow" },
      el("h1", { class: "page-title" }, icon("heart", { size: 24 }), "生命卡"),
      sub
    )
  );

  const actions = el("div", { class: "page-actions" });
  const backupAllBtn = button({
    icon: "download",
    label: "备份全部",
    variant: "primary",
    onClick: (event, btn) => doBackup(btn, "plugin"),
  });
  const backupSharedBtn = button({
    icon: "book",
    label: "备份共享卡",
    onClick: (event, btn) => doBackup(btn, "shared"),
  });
  const refreshBtn = button({ icon: "refresh", label: "刷新", onClick: () => load({ force: true }) });
  actions.append(backupAllBtn, backupSharedBtn, refreshBtn);
  head.appendChild(actions);

  const content = el("div", { class: "life-layout", id: "life-content" });
  page.append(head, content);
  root.appendChild(page);

  let destroyed = false;
  let data = null;
  let query = app.getSearch ? app.getSearch() : "";
  let allowRestore = false;
  let maxCardMb = 0;

  async function load() {
    replaceChildren(content, skeletonPanel());
    if (sub) sub.textContent = "正在加载生命卡…";
    try {
      const payload = await endpoints.life();
      if (destroyed) return;
      data = {
        cards: Array.isArray(payload && payload.cards) ? payload.cards : [],
        personas: Array.isArray(payload && payload.personas) ? payload.personas : [],
        tasks: Array.isArray(payload && payload.tasks) ? payload.tasks : [],
      };
      allowRestore = Boolean(payload && payload.allow_restore);
      maxCardMb = toNumber(payload && payload.max_card_mb, 0);
      if (app.clearError) app.clearError();
      render();
    } catch (err) {
      if (destroyed) return;
      data = null;
      if (sub) sub.textContent = "加载失败";
      clear(content);
      content.appendChild(
        emptyState({
          icon: "alert",
          title: "生命卡加载失败",
          hint: err && err.message ? err.message : "请稍后重试。",
          actions: [button({ icon: "refresh", label: "重试", variant: "primary", onClick: () => load() })],
        })
      );
      if (app.setError) app.setError(err && err.message ? err.message : "加载失败");
    }
  }

  function render() {
    if (!data) return;
    clear(content);

    const personaCards = data.cards.filter((card) => card.type === "persona");
    const sharedCards = data.cards.filter((card) => card.type === "shared");
    const hostCards = data.cards.filter((card) => card.type === "host");

    const total = data.cards.length;
    if (sub) {
      const parts = [`共 ${formatNumber(total)} 张卡`];
      if (maxCardMb > 0) parts.push(`单卡上限 ${formatNumber(maxCardMb)} MB`);
      if (!allowRestore) parts.push("恢复已关闭");
      if (query) parts.push(`筛选「${query}」`);
      sub.textContent = parts.join(" · ");
    }

    content.appendChild(
      el(
        "div",
        { class: "row-wrap" },
        el(
          "span",
          { class: "chip" },
          icon("card", { size: 14 }),
          el("span", null, `人格卡 ${personaCards.length}`)
        ),
        el(
          "span",
          { class: "chip" },
          icon("book", { size: 14 }),
          el("span", null, `共享卡 ${sharedCards.length}`)
        ),
        el(
          "span",
          { class: "chip" },
          icon("database", { size: 14 }),
          el("span", null, `宿主卡 ${hostCards.length}`)
        )
      )
    );

    if (!allowRestore) {
      content.appendChild(
        notice(
          "恢复功能当前是关闭的（allow_restore=false）。可以在「设置中心 → 生命卡」打开后再恢复；备份不受影响。",
          "warn",
          "alert"
        )
      );
    }

    let renderedGroups = 0;

    if (total === 0 && data.personas.length === 0) {
      content.appendChild(
        emptyState({
          icon: "heart",
          title: "还没有生命卡",
          hint: "备份会把人格资料、记忆、版本与共享知识库打包成 .pialife 卡片，方便迁移或留档。",
          actions: [
            button({
              icon: "download",
              label: "备份全部",
              variant: "primary",
              onClick: (event, btn) => doBackup(btn, "plugin"),
            }),
            button({
              icon: "refresh",
              label: "刷新",
              onClick: () => load(),
            }),
          ],
        })
      );
    }

    /* 人格卡分组 */
    for (const persona of data.personas) {
      const pid = String((persona && (persona.id ?? persona.persona_id)) ?? "");
      const nickname = String((persona && persona.nickname) || pid || "未命名人格");
      const cards = personaCards.filter((card) => String(card.persona_id || "") === pid);
      if (query && !groupMatches(persona, cards)) continue;
      content.appendChild(buildPersonaGroup(pid, nickname, persona && persona.latest, cards));
      renderedGroups += 1;
    }

    const orphanPersonaCards = personaCards.filter(
      (card) => !data.personas.some((persona) => String(persona && persona.id) === String(card.persona_id || ""))
    );
    if (orphanPersonaCards.length > 0) {
      const filtered = orphanPersonaCards.filter((card) =>
        matchesQuery(`${card.name} ${card.persona_id}`, query)
      );
      if (filtered.length > 0 || !query) {
        content.appendChild(
          buildGroup({
            title: "未在人格列表中的卡片",
            subtitle: `人格卡 ${orphanPersonaCards.length} 张`,
            iconName: "card",
            cards: filtered,
            emptyHint: "没有匹配的卡片。",
          })
        );
        renderedGroups += 1;
      }
    }

    /* 共享卡 */
    const sharedFiltered = sharedCards.filter((card) => matchesQuery(card.name, query));
    if (sharedFiltered.length > 0 || !query) {
      content.appendChild(
        buildGroup({
          title: "共享知识卡",
          subtitle: "所有人格共用",
          iconName: "book",
          cards: sharedFiltered,
          emptyHint: "还没有共享知识卡备份。",
          extraActions: [
            button({
              icon: "download",
              label: "备份共享卡",
              size: "sm",
              onClick: (event, btn) => doBackup(btn, "shared"),
            }),
          ],
        })
      );
      renderedGroups += 1;
    }

    /* 宿主卡 */
    const hostFiltered = hostCards.filter((card) => matchesQuery(card.name, query));
    if (hostFiltered.length > 0 || !query) {
      content.appendChild(
        buildGroup({
          title: "宿主卡",
          subtitle: "由宿主 life-card-host 生成的全量卡（含 AstrBot 配置/数据库），插件只负责发起与查看，恢复需在宿主侧进行",
          iconName: "database",
          cards: hostFiltered,
          emptyHint: "宿主还没有生成卡片。可在宿主运行 life-card-host 执行 all / agent / qq 备份。",
        })
      );
      renderedGroups += 1;
    }

    if (query && renderedGroups === 0) {
      content.appendChild(
        emptyState({
          icon: "search",
          title: "没有匹配的生命卡",
          hint: `没有找到包含「${query}」的人格或卡片，换个关键词试试。`,
          actions: [
            button({
              icon: "x",
              label: "清除搜索",
              onClick: () => {
                if (app.clearSearch) app.clearSearch();
                else setSearch("");
              },
            }),
          ],
        })
      );
    }

    /* 任务 */
    content.appendChild(buildTasks());
  }

  function groupMatches(persona, cards) {
    const pid = String(persona && persona.id || "");
    const nickname = String((persona && persona.nickname) || "");
    if (matchesQuery(`${nickname} ${pid}`, query)) return true;
    return cards.some((card) => matchesQuery(card.name, query));
  }

  function buildPersonaGroup(pid, nickname, latest, cards) {
    const personaMeta = (app.getPersonas ? app.getPersonas() : []).find(
      (persona) => persona.id === pid
    );
    const avatarHost = el("span", { class: "life-group-avatar" });
    fillAvatar(avatarHost, personaMeta ? personaMeta.avatarData : "", nickname);

    const group = buildGroup({
      title: nickname,
      subtitle: pid,
      iconName: "card",
      cards,
      emptyHint: "还没有备份过这张人格卡。",
      avatarNode: avatarHost,
      latest,
      extraActions: [
        button({
          icon: "download",
          label: "备份这张卡",
          size: "sm",
          variant: "primary",
          onClick: (event, btn) => doBackup(btn, `persona:${pid}`),
        }),
      ],
    });
    return group;
  }

  function buildGroup(options) {
    const group = el("section", { class: "life-group" });
    const headEl = el("div", { class: "life-group-head" });

    if (options.avatarNode) headEl.appendChild(options.avatarNode);
    else {
      headEl.appendChild(
        el(
          "span",
          { class: "life-group-avatar", title: options.title },
          icon(options.iconName || "card")
        )
      );
    }

    const ident = el(
      "div",
      { class: "life-group-name" },
      el("strong", null, options.title || ""),
      el("span", null, options.subtitle || "")
    );
    headEl.appendChild(ident);

    if (options.latest) {
      headEl.appendChild(
        el(
          "span",
          { class: "chip" },
          icon("clock", { size: 13 }),
          el("span", null, `最近备份 ${timeAgo(options.latest.modified_at || options.latest.created_at)}`)
        )
      );
    }

    const actionsWrap = el("div", { class: "life-group-actions" });
    for (const node of Array.isArray(options.extraActions) ? options.extraActions : []) {
      if (node) actionsWrap.appendChild(node);
    }
    headEl.appendChild(actionsWrap);
    group.appendChild(headEl);

    const list = el("div", { class: "life-cards" });
    if (!options.cards || options.cards.length === 0) {
      list.appendChild(el("div", { class: "life-empty" }, options.emptyHint || "暂无卡片。"));
    } else {
      for (const card of options.cards) list.appendChild(buildCard(card));
    }
    group.appendChild(list);
    return group;
  }

  function buildCard(card) {
    const type = String(card.type || "host");
    const name = String(card.name || "");
    const node = el("article", { class: "life-card" });

    const top = el(
      "div",
      { class: "life-card-top" },
      icon("card", { size: 15 }),
      el("span", { class: "life-card-name", title: name }, truncate(name, 34)),
      badge(TYPE_LABELS[type] || type, TYPE_VARIANTS[type] || "muted")
    );
    node.appendChild(top);

    const meta = el(
      "div",
      { class: "life-card-meta" },
      el("span", null, formatKb(card.size_kb)),
      el("span", null, "·"),
      el("span", null, timeAgo(card.created_at || card.modified_at)),
      el("span", null, "·"),
      el("span", null, formatTime(card.modified_at || card.created_at))
    );
    node.appendChild(meta);

    const actionsWrap = el("div", { class: "life-card-actions" });
    const restoreDisabled = type === "host" || !allowRestore;
    const restoreBtn = button({
      icon: "restore",
      label: "恢复",
      size: "sm",
      variant: "ghost",
      disabled: restoreDisabled,
      title:
        type === "host"
          ? "宿主卡请使用 life-card 宿主恢复"
          : allowRestore
            ? "恢复这张卡"
            : "恢复功能未开启（allow_restore=false）",
      onClick: (event, btn) => doRestore(btn, card),
    });
    actionsWrap.appendChild(restoreBtn);
    node.appendChild(actionsWrap);
    return node;
  }

  function buildTasks() {
    const section = el("section", { class: "task-list" });
    section.appendChild(
      el(
        "div",
        { class: "task-list-head" },
        icon("history", { size: 15 }),
        el("span", null, "最近宿主任务"),
        el("span", { class: "grow" }),
        button({ icon: "refresh", label: "刷新", size: "sm", variant: "ghost", onClick: () => load() })
      )
    );

    const tasks = data.tasks.length > 0 ? data.tasks : [];
    if (tasks.length === 0) {
      section.appendChild(
        el("p", { class: "drawer-note" }, "还没有宿主任务记录。宿主执行备份/恢复后会在这里显示。")
      );
      return section;
    }
    for (const task of tasks) {
      const status = String(task.status || "").toLowerCase();
      section.appendChild(
        el(
          "div",
          { class: "task-item" },
          badge(task.status || "unknown", STATUS_VARIANTS[status] || "muted"),
          el("span", { class: "task-action" }, String(task.action || "—")),
          el("span", { class: "task-message", title: String(task.message || "") },
            String(task.message || "—"))
        )
      );
    }
    return section;
  }

  async function doBackup(btn, target) {
    await runBusy(
      btn,
      async () => {
        const result = await endpoints.lifeBackup(target);
        toast((result && result.message) || "生命卡已生成", "success");
        if (app.refreshStatus) await app.refreshStatus();
        await load();
      },
      "备份失败"
    );
  }

  async function doRestore(btn, card) {
    const name = String(card.name || "");
    if (!name) return;
    const kind = TYPE_LABELS[card.type] || "生命卡";
    const ok = await confirmDialog({
      title: `恢复${kind}`,
      message:
        "恢复会用卡片内容覆盖当前的人格资料、记忆与版本（共享卡会覆盖共享知识库）。建议先做一次备份，确认继续吗？",
      detail: name,
      confirmText: "恢复",
      danger: true,
    });
    if (!ok) return;
    await runBusy(
      btn,
      async () => {
        const result = await endpoints.lifeRestore(name);
        toast((result && result.message) || "恢复完成", "success");
        if (app.loadPersonas) await app.loadPersonas({ force: true });
        if (app.refreshStatus) await app.refreshStatus();
        await load();
      },
      "恢复失败"
    );
  }

  function setSearch(value) {
    query = String(value ?? "");
    render();
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

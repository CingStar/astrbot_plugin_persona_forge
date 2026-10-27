const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const statusEl = document.getElementById("status");
const listEl = document.getElementById("persona-list");

function setStatus(text) { statusEl.textContent = text || ""; }

async function apiGet(path, params = {}) {
  return bridge.apiGet(path, params);
}
async function apiPost(path, body = {}) {
  if (typeof bridge.apiPost === "function") return bridge.apiPost(path, body);
  if (typeof bridge.apiRequest === "function") return bridge.apiRequest("POST", path, body);
  throw new Error("bridge apiPost unavailable");
}

async function loadPersonas() {
  setStatus("加载中…");
  try {
    const data = await apiGet("personas");
    const active = data.active;
    listEl.innerHTML = "";
    for (const p of data.personas || []) {
      const card = document.createElement("div");
      card.className = "card" + (p.active ? " active" : "");
      card.innerHTML = `
        <h3>${p.id}</h3>
        <div class="meta">昵称：${p.nickname || "-"}</div>
        <div class="meta">头像：${p.avatar ? "有" : "无"} · 版本：${p.version || 0}</div>
        <div class="actions">
          <span class="pill">${p.default ? "默认" : "角色"}</span>
          ${p.active ? '<span class="pill">当前</span>' : ""}
          <button data-action="switch" data-id="${p.id}">切换</button>
          <button data-action="versions" data-id="${p.id}">版本</button>
        </div>`;
      listEl.appendChild(card);
    }
    setStatus(`当前：${active}`);
  } catch (e) {
    setStatus("加载失败：" + e.message);
  }
}

listEl.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const pid = btn.dataset.id;
  const action = btn.dataset.action;
  try {
    if (action === "switch") {
      setStatus("切换中…");
      const r = await apiPost("switch", { pid });
      setStatus(r.message || "已切换");
      await loadPersonas();
    } else if (action === "versions") {
      const r = await apiGet("versions", { pid });
      alert(`${pid} 版本：${(r.versions || []).join(", ") || "无"}`);
    }
  } catch (e) {
    setStatus("操作失败：" + e.message);
  }
});

async function loadReviews() {
  const box = document.getElementById("review-list");
  box.innerHTML = "加载中…";
  try {
    const data = await apiGet("reviews");
    const items = data.reviews || [];
    if (!items.length) { box.textContent = "没有待审草稿。"; return; }
    box.innerHTML = "";
    for (const r of items) {
      const el = document.createElement("div");
      el.className = "review";
      el.innerHTML = `
        <strong>${r.persona_id}</strong> · ${r.source}
        <div class="actions">
          <button data-review="approve" data-pid="${r.persona_id}">批准</button>
          <button data-review="reject" data-pid="${r.persona_id}">拒绝</button>
          <button data-review="show" data-pid="${r.persona_id}">查看 diff</button>
        </div>`;
      box.appendChild(el);
    }
  } catch (e) {
    box.textContent = "加载失败：" + e.message;
  }
}

document.getElementById("review-list").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-review]");
  if (!btn) return;
  const pid = btn.dataset.pid;
  const action = btn.dataset.review;
  if (action === "show") {
    const data = await apiGet("reviews");
    const r = (data.reviews || []).find(x => x.persona_id === pid);
    document.getElementById("review-diff").textContent = r ? r.diff : "无";
    return;
  }
  await apiPost("reviews/action", { pid, action });
  await loadReviews();
});

async function loadGroup() {
  const data = await apiGet("group-mode");
  const form = document.getElementById("group-form");
  form.group_mode.value = data.group_mode || "off";
  form.group_whitelist.value = (data.group_whitelist || []).join(",");
  form.require_at.checked = !!data.require_at;
  form.guest_wiki.value = data.guest_wiki || "read";
  form.guest_rate_limit_per_minute.value = data.guest_rate_limit_per_minute || 5;
}

document.getElementById("group-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const payload = {
    group_mode: form.group_mode.value,
    group_whitelist: form.group_whitelist.value.split(",").map(x => x.trim()).filter(Boolean),
    require_at: form.require_at.checked,
    guest_wiki: form.guest_wiki.value,
    guest_rate_limit_per_minute: Number(form.guest_rate_limit_per_minute.value || 5),
  };
  const el = document.getElementById("group-status");
  try {
    await apiPost("group-mode", payload);
    el.textContent = "已保存";
  } catch (e) {
    el.textContent = "保存失败：" + e.message;
  }
});

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach(x => x.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    if (tab.dataset.tab === "reviews") loadReviews();
    if (tab.dataset.tab === "group") loadGroup();
  });
});

document.getElementById("refresh").addEventListener("click", loadPersonas);
document.getElementById("refresh-reviews").addEventListener("click", loadReviews);

await bridge.ready();
await loadPersonas();

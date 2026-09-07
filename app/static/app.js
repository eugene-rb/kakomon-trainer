"use strict";

/** API 呼び出しとページ共通のユーティリティ。 */

const QUESTION_COLORS = [
  "#1f5fa0", "#c2410c", "#2f7a3f", "#7c3aed", "#b45309",
  "#0e7490", "#be123c", "#4d7c0f", "#6d28d9", "#a16207",
];

function questionColor(index) {
  return QUESTION_COLORS[index % QUESTION_COLORS.length];
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    throw new Error("サーバーに接続できません。アプリが起動しているか確認し、もう一度お試しください。");
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = Array.isArray(body.detail)
          ? body.detail.map((item) => `${(item.loc || []).filter((part) => part !== "body").join(" → ")}: ${item.msg}`).join("\n")
          : typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (e) { /* JSON でないレスポンスはそのまま */ }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

function apiJson(path, method, payload) {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value !== null && value !== undefined) {
      node.setAttribute(key, value);
    }
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function showNotice(container, message, level = "info") {
  if (!container) return;
  container.setAttribute("role", level === "error" ? "alert" : "status");
  container.setAttribute("aria-live", level === "error" ? "assertive" : "polite");
  container.innerHTML = "";
  if (!message) return;
  container.appendChild(el("div", { class: `notice ${level}`, text: message }));
}

function setBusy(button, busy, label) {
  if (busy) {
    if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent;
    button.textContent = label;
  } else if (button.dataset.idleLabel) {
    button.textContent = button.dataset.idleLabel;
    delete button.dataset.idleLabel;
  }
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function queryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function formatDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** status に応じたバッジ要素を返す。 */
function statusBadge(status) {
  const map = {
    processing: ["処理中", ""],
    ready: ["転記確認待ち", "ok"],
    grading: ["採点中", ""],
    graded: ["採点済み", "ok"],
    failed: ["失敗", "error"],
  };
  const [label, cls] = map[status] || [status, ""];
  return el("span", { class: `badge ${cls}`, text: label });
}

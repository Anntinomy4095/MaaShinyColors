// 设置 → 存储: how much local data dev_assets/private holds per category, 清理 per category (confirm dialog)
// and 自动清理 (default off, keep 7 / 14 / 30 days). The page only asks (storage.status / storage.clean / storage.auto); the
// host decides which files belong to a category, never deletes settings, login, scan results or ledgers, and refuses while
// a run, scan or recording is active. A host build without status.update.storage shows 「需要更新程序」.
import { h } from "./dom.js";
import { formatBytes } from "./game-data.js";

export const STORAGE_CATEGORIES = [
  { key: "samples", label: "截图样本", what: "任务样本帧、扫描截图", keep: "保留扫描结果" },
  { key: "logs", label: "运行记录", what: "运行日志与历史", keep: "保留最近 2 天（今日进度要用）" },
  // 2026-10-29: W.I.N.G. hand portraits; deleting only means one look per card again; never auto-cleaned
  { key: "cache", label: "手牌识别", what: "Live 手牌头像与技能的对应", keep: "删除后每张卡重新点一次", auto: false },
];
const AUTO_CATEGORIES = STORAGE_CATEGORIES.filter((c) => c.auto !== false);
export const OTHER_HINT = "设置、登录、扫描结果与账本，不清理";
export const AUTO_HINT = "程序启动和每次运行结束后，删除超过保留天数的文件。";
export const RETENTION_DAYS = [7, 14, 30];

const BLOCKER_TEXT = {
  engine_active: "任务运行中",
  card_scan_active: "正在扫描持有卡",
  knowhow_scan_active: "正在扫描ノウハウ",
  external_run: "有任务正在操作游戏",
};

const num = (v) => (Number.isFinite(v) && v >= 0 ? v : null);

/** Normalised status.update.storage. */
export function storageModel(host) {
  const s = host?.storage;
  if (!s || typeof s !== "object") return { supported: false };
  const cats = Array.isArray(s.categories) ? s.categories : null;
  const byKey = Object.fromEntries((cats || []).filter((c) => c && typeof c.key === "string").map((c) => [c.key, c]));
  const days = RETENTION_DAYS.includes(s.auto?.days) ? s.auto.days : 14;
  return {
    supported: true,
    state: ["scanning", "cleaning"].includes(s.state) ? s.state : "idle",
    known: Boolean(cats),
    categories: STORAGE_CATEGORIES.map((c) => ({ ...c, bytes: num(byKey[c.key]?.bytes), files: num(byKey[c.key]?.files),
      cleanable: num(byKey[c.key]?.cleanable_bytes), cleanableFiles: num(byKey[c.key]?.cleanable_files) })),
    other: num(s.other_bytes),
    total: num(s.total_bytes),
    auto: { enabled: s.auto?.enabled === true, days },
    blocker: typeof s.blocker === "string" && s.blocker ? s.blocker : null,
    last: s.last && typeof s.last === "object" ? s.last : null,
  };
}

/** Why 清理 cannot be used now, or "" when it can. */
export function storageBlocker(state, model = storageModel(state?.host)) {
  if (!model.supported) return "需要更新程序";
  if (state?.run?.active) return "任务运行中";
  if (model.blocker) return BLOCKER_TEXT[model.blocker] || "现在不能清理";
  if (model.state === "cleaning") return "正在清理…";
  if (model.state === "scanning" || !model.known) return "计算中…";
  return "";
}

/** Fold-card summary: 「共 1.2 GB · 自动 14 天」 / 「计算中…」. */
export function storageSummary(model) {
  if (!model.supported) return "需要更新程序";
  const auto = model.auto.enabled ? ` · 自动 ${model.auto.days} 天` : "";
  if (model.state === "cleaning") return `正在清理…${auto}`;
  if (!model.known || model.total === null) return `计算中…${auto}`;
  return `共 ${formatBytes(model.total)}${auto}`;
}

/** 「上次清理：释放 35 MB（120 个文件）」 */
export function lastText(last) {
  if (!last) return "";
  const head = last.op === "prune" ? "自动清理" : "上次清理";
  if (last.ok === false && !Number.isFinite(last.files)) return `${head}失败`;
  const failed = Number(last.failed) > 0 ? `，${last.failed} 个未能删除` : "";
  return `${head}：释放 ${formatBytes(Number(last.freed_bytes) || 0)}（${Number(last.files) || 0} 个文件${failed}）`;
}

/** Confirmation for one category's 清理; onConfirm runs only from the red button. */
export function openStorageConfirm(category, { onConfirm } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const dialog = h("dialog", { class: "dialog game-data-dialog", "aria-labelledby": "storage-confirm-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  const files = category.cleanableFiles ?? 0;
  dialog.append(
    h("h2", { id: "storage-confirm-title", class: "dialog-title", text: `清理${category.label}` }),
    h("ul", { class: "card-scan-lines" },
      h("li", { class: "small", title: category.keep, text: `${category.what} · ${files} 个文件 · 约 ${formatBytes(category.cleanable ?? 0)}` }),
      h("li", { class: "small", text: "删除后不能恢复" })),
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { storageCancel: "1" }, onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-danger", dataset: { storageConfirm: category.key }, onclick: () => { close(); onConfirm?.(); } }, "清理")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/**
 * 自动清理 confirmation: turning it on, or keeping fewer days while it is on, deletes files at the next
 * start / run end. The counts are what the categories could free at most (the host keeps files newer than `days`).
 */
export function openAutoCleanConfirm(model, days, { onConfirm } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const dialog = h("dialog", { class: "dialog game-data-dialog", "aria-labelledby": "storage-auto-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  const auto = model.categories.filter((c) => c.auto !== false);
  const files = auto.reduce((sum, c) => sum + (c.cleanableFiles ?? 0), 0);
  const bytes = auto.reduce((sum, c) => sum + (c.cleanable ?? 0), 0);
  dialog.append(
    h("h2", { id: "storage-auto-title", class: "dialog-title", text: `自动清理：保留 ${days} 天` }),
    h("ul", { class: "card-scan-lines" },
      h("li", { class: "small", text: `删除超过 ${days} 天的${AUTO_CATEGORIES.map((c) => c.label).join("、")}` }),
      model.known ? h("li", { class: "small", text: `现在最多 ${files} 个文件，约 ${formatBytes(bytes)}` }) : null,
      h("li", { class: "small", text: "删除后不能恢复" })),
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { storageCancel: "1" }, onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-danger", dataset: { storageConfirm: "auto" }, onclick: () => { close(); onConfirm?.(); } }, "开启")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/** Body of the 存储 fold card. `actions`: storageStatus(refresh), storageClean(key), storageAuto(enabled, days). */
export function storageView(state, actions) {
  const model = storageModel(state.host);
  actions.storageStatus?.(false);   // throttled by the app; the host refreshes a stale cache in the background
  if (!model.supported) return h("p", { class: "muted small", text: "需要更新程序后才能查看存储。" });
  const blocker = storageBlocker(state, model);
  const rows = model.categories.map((c) => {
    const empty = !c.cleanable;
    const reason = blocker || (empty && model.known ? "没有可清理的文件" : "");
    return h("li", { class: "storage-row", dataset: { storage: c.key } },
      h("span", { class: "storage-name", title: c.what }, h("span", { text: c.label })),
      h("span", { class: "num muted small storage-size", text: c.bytes === null ? "—" : formatBytes(c.bytes) }),
      h("button", { type: "button", class: "btn btn-small", disabled: reason ? "" : null, title: reason || `释放约 ${formatBytes(c.cleanable ?? 0)}`,
        dataset: { focusKey: `storage-clean-${c.key}` }, onclick: () => openStorageConfirm(c, { onConfirm: () => actions.storageClean?.(c.key) }) }, "清理"));
  });
  rows.push(h("li", { class: "storage-row", dataset: { storage: "other" } },
    h("span", { class: "storage-name", title: OTHER_HINT }, h("span", { text: "其他" })),
    h("span", { class: "num muted small storage-size", text: model.other === null ? "—" : formatBytes(model.other) }),
    h("span", { class: "storage-gap" })));
  const daySeg = h("div", { class: "segmented text-seg storage-days", role: "radiogroup", "aria-label": "保留天数" }, RETENTION_DAYS.map((d) =>
    h("button", { class: `segment${model.auto.days === d ? " is-selected" : ""}`, type: "button", role: "radio", "aria-checked": String(model.auto.days === d),
      dataset: { focusKey: `storage-days-${d}` }, onclick: () => (model.auto.enabled && d < model.auto.days
        ? openAutoCleanConfirm(model, d, { onConfirm: () => actions.storageAuto?.(true, d) }) : actions.storageAuto?.(model.auto.enabled, d)) },
      h("span", { text: `${d} 天` }))));
  const status = blocker && blocker !== "计算中…" ? blocker : model.state === "scanning" ? "计算中…" : lastText(model.last);
  return h("div", { class: "storage" },
    h("ul", { class: "storage-list" }, rows),
    h("div", { class: "field form-row" }, h("div", { class: "field-control dev-tool-row" },
      h("button", { type: "button", class: "btn btn-small", disabled: model.state !== "idle" ? "" : null, dataset: { focusKey: "storage-refresh" },
        onclick: () => actions.storageStatus?.(true) }, "重新计算"),
      status ? h("span", { class: "muted small", text: status }) : null)),
    h("ul", { class: "toggle-list" },
      h("li", { title: AUTO_HINT },
        h("span", { class: "switch-text" }, h("span", { id: "storage-auto", text: "自动清理" })),
        h("button", { class: `toggle${model.auto.enabled ? " is-on" : ""}`, type: "button", role: "switch", "aria-checked": String(model.auto.enabled),
          "aria-labelledby": "storage-auto", dataset: { focusKey: "storage-auto" }, onclick: () => (model.auto.enabled ? actions.storageAuto?.(false, model.auto.days)
            : openAutoCleanConfirm(model, model.auto.days, { onConfirm: () => actions.storageAuto?.(true, model.auto.days) })) },
        h("span", { class: "toggle-track" }, h("span", { class: "toggle-thumb" }))))),
    h("div", { class: "field form-row" }, h("div", { class: "field-label-row" }, h("span", { class: "field-label", text: "保留天数" })),
      h("div", { class: "field-control" }, daySeg)));
}

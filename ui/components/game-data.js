// 设置 → 「关于数据」: what this app stores on the computer, plus 静音游戏 / 清理游戏缓存 / 清除登录信息.
// The page only asks (protocol commands game.audio.set / game.data.clear); the host does the work and reports back through
// status.update.game_data. Audio and clearing are separate capabilities; clearing requires clear_supported=true.
// Nothing here reads cookies or cache content: the page never sees them, and clearing only deletes.
import { h } from "./dom.js";

export const NEED_HOST = "需要更新程序";

/** "1.2 GB" / "35 MB" / "—" (unknown). */
export function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 ** 3) return `${Math.round(n / 1024 ** 2)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

/** Normalised audio support plus the separate clearSupported capability. */
export function gameDataModel(host) {
  const g = host?.game_data;
  if (!g || typeof g !== "object") return { supported: false, clearSupported: false, muted: false, busy: false, cacheBytes: null, profileBytes: null, last: null };
  const num = (v) => (Number.isFinite(v) && v >= 0 ? v : null);
  return { supported: true, clearSupported: g.clear_supported === true, muted: g.muted === true, busy: g.busy === true, cacheBytes: num(g.cache_bytes), profileBytes: num(g.profile_bytes),
    last: g.last && typeof g.last === "object" ? g.last : null };
}

/** Why clearing is unavailable, or "" when it can be used. Muting does not interrupt tasks. */
export function gameDataBlocker(state, model = gameDataModel(state?.host)) {
  if (!model.supported) return NEED_HOST;
  if (!model.clearSupported) return "缓存和登录清理暂未提供";
  if (state?.run?.active) return "任务运行中";
  if (model.busy) return "处理中";
  if (state?.host?.card_scan?.state === "running" || state?.host?.card_scan?.state === "stopping") return "正在扫描持有卡";
  if (state?.host?.knowhow_scan?.state === "running" || state?.host?.knowhow_scan?.state === "stopping") return "正在扫描ノウハウ";
  return "";
}

/** Acceptance only starts the job; show the host's actual completion instead of assuming it succeeded. */
export function gameDataResult(model) {
  if (model.busy) return "正在清理游戏数据…";
  const last = model.last;
  if (!last) return "";
  if (!last.ok) return last.reason || "游戏数据清理失败";
  return last.op === "login" ? "登录信息已清除，请在游戏重新载入后登录" : "游戏缓存已清理，游戏正在重新载入";
}

/** Confirmation dialog for 清除登录信息 (logs the game out). onConfirm runs only from the red button. */
export function openLoginClearConfirm({ onConfirm } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const dialog = h("dialog", { class: "dialog game-data-dialog", "aria-labelledby": "game-data-confirm-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.append(
    h("h2", { id: "game-data-confirm-title", class: "dialog-title", text: "清除登录信息" }),
    h("ul", { class: "card-scan-lines" },
      h("li", { class: "small", text: "游戏退出登录" })),
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { gameDataCancel: "1" }, onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-danger", dataset: { gameDataConfirm: "1" }, onclick: () => { close(); onConfirm?.(); } }, "清除")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/** 清理缓存 confirmation (the game page reloads, so it is asked first). */
export function openCacheClearConfirm({ onConfirm } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const dialog = h("dialog", { class: "dialog game-data-dialog", "aria-labelledby": "game-cache-confirm-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.append(
    h("h2", { id: "game-cache-confirm-title", class: "dialog-title", text: "清理缓存" }),
    h("ul", { class: "card-scan-lines" },
      h("li", { class: "small", text: "游戏页面重新载入" })),
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { gameDataCancel: "1" }, onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-primary", dataset: { gameDataConfirm: "cache" }, onclick: () => { close(); onConfirm?.(); } }, "清理")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/** Body of the 「关于数据」 fold card. `actions`: gameMute(muted), gameClearCache(), gameClearLogin(). */
export function dataView(state, actions) {
  const model = gameDataModel(state.host);
  const blocker = gameDataBlocker(state, model);
  const sizes = model.supported && (model.profileBytes !== null || model.cacheBytes !== null)
    ? `游戏数据 ${formatBytes(model.profileBytes)} · 缓存 ${formatBytes(model.cacheBytes)}` : null;
  const action = (label, hint, { focusKey, danger = false, onclick }) => h("div", { class: "field form-row game-data-action" },
    h("div", { class: "field-control dev-tool-row" },
      h("button", { type: "button", class: `btn${danger ? " btn-danger-quiet" : ""}`, disabled: blocker ? "" : null, title: blocker || hint,
        dataset: { focusKey, gameData: focusKey }, onclick },
      label)));
  return h("div", { class: "game-data", title: "Maa 不读取、不上传 cookies 与缓存内容" },
    sizes ? h("p", { class: "muted small num", text: sizes }) : null,
    action("清理缓存", "不退出登录；游戏页会重新载入", { focusKey: "game-clear-cache", onclick: () => openCacheClearConfirm({ onConfirm: () => actions.gameClearCache?.() }) }),
    action("清除登录信息", "清除 cookies；游戏退出登录", { focusKey: "game-clear-login", danger: true,
      onclick: () => openLoginClearConfirm({ onConfirm: () => actions.gameClearLogin?.() }) }),
    blocker ? h("p", { class: "muted small", text: blocker }) : null,   // one line for both buttons
    gameDataResult(model) ? h("p", { class: "small", role: "status", text: gameDataResult(model) }) : null);
}

/** 静音游戏 switch state for the 外观 card: {on, disabled, hint}. */
export function muteState(state) {
  const model = gameDataModel(state.host);
  return { on: model.muted, disabled: !model.supported, hint: model.supported ? null : NEED_HOST };
}

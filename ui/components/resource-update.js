// U18 (ANN-49): 「上次读取」 + 「更新资源」 on the home tab. The home counters are read passively while tasks run
// 「只读取资源」 (2026-10-26, role-play D6/A8) runs 启动到首页 + one home read (a normal
// supervised run, no click of its own); the 持有卡 are read by the supervised 扫描持有卡 on the 资源 tab. The dialog names
// the three different things. Pure helpers are unit-tested offline.
import { h, relativeTime, jaText } from "./dom.js";

const pad = (n) => String(n).padStart(2, "0");

/** "HH:MM" in local time, or "" for a missing / invalid timestamp. */
export function clockTime(at) {
  const t = Date.parse(at || "");
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Age text for one reading: "3 小时前" (relativeTime) with the clock time for same-day readings. */
export function readAge(at, now = Date.now()) {
  if (!at || Number.isNaN(Date.parse(at))) return "";
  const t = Date.parse(at);
  const sameDay = new Date(t).toDateString() === new Date(now).toDateString();
  return sameDay ? `${clockTime(at)} · ${relativeTime(at, now)}` : relativeTime(at, now);
}

/** Newest reading time among the home counters: { at, text, stale }. Nothing read yet: 尚未读取. */
export function lastReadSummary(counters, now = Date.now(), staleMs = 6 * 3600 * 1000) {
  const times = (counters || []).filter((c) => c.count !== null && c.count !== undefined && c.at).map((c) => Date.parse(c.at)).filter((t) => !Number.isNaN(t));
  if (!times.length) return { at: null, text: "未读取", stale: false, older: 0 };
  const newest = Math.max(...times);
  const iso = new Date(newest).toISOString();
  // 2026-10-12: the home page shows freshness only here (no per-resource line); name how many readings are older.
  const older = now - newest > staleMs ? 0 : times.filter((t) => now - t > staleMs).length;
  return { at: iso, text: readAge(iso, now), stale: now - newest > staleMs, older };
}

/** The three different "refresh" meanings, in the order a player meets them (pure data for the dialog and the tests). */
export function updateChoices() {
  return [
    { key: "page", title: "游戏页刷新",
      body: "游戏卡住或白屏时，点游戏窗口右上角的刷新图标。刷新只重新载入游戏页面。" },
    { key: "resources", title: "资源读取（マニー・体力・Fes 参加权・ジュエル）",
      body: "日常运行时也会读取。只读取：回到首页读一次，只关已知弹窗。",
      action: { label: "只读取资源", run: "resources" } },
    { key: "cards", title: "持有卡扫描",
      body: "在资源页点「扫描持有卡」，读取 P／S 卡、Lv 和凸数。扫描会自动翻页，期间请勿操作游戏。",
      action: { label: "去资源页", tab: "resources" } },
  ];
}

export function openResourceUpdate({ counters, now = Date.now(), onGo, onReload, onRead, readBlocked = "" }) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const dialog = h("dialog", { class: "dialog card-scan-dialog resource-update-dialog", "aria-labelledby": "resource-update-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  const summary = lastReadSummary(counters, now);
  const lines = (counters || []).map((c) => h("li", { class: "small" },
    h("span", { text: `${c.label}：` }),
    h("span", { class: "muted", text: c.count !== null && c.count !== undefined ? readAge(c.at, now) || "时间未知" : "尚未读取" })));
  dialog.append(
    h("h2", { id: "resource-update-title", class: "dialog-title", text: "更新资源" }),
    h("p", { class: "dialog-sub", text: summary.text + (summary.stale ? "（超过 6 小时，可能已变化）" : "") }),
    h("ul", { class: "card-scan-lines" }, lines),
    ...updateChoices().map((choice) => h("section", { class: "resource-update-choice" },
      h("h3", { class: "small resource-update-title" }, jaText(choice.title)),
      h("p", { class: "small muted" }, jaText(choice.body)),
      choice.action?.run ? h("button", { type: "button", class: "btn btn-small btn-primary", dataset: { updateRun: choice.action.run },
        disabled: !onRead || Boolean(readBlocked), title: readBlocked || "", onclick: () => { close(); onRead(); } }, choice.action.label)
      : choice.action ? h("button", { type: "button", class: "btn btn-small", dataset: { updateGo: choice.tab || choice.action.tab }, onclick: () => { close(); onGo?.(choice.action.tab); } }, choice.action.label) : null)),
    h("div", { class: "dialog-actions" },
      onReload ? h("button", { type: "button", class: "btn btn-text", title: "重新读取本机已保存的识别结果", onclick: () => { close(); onReload(); } }, "重新读取已有记录") : null,
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", onclick: close }, "关闭")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

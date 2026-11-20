// AppHeader: app logo + wordmark, host status (dot + text) and a quiet emergency stop.
// In a host window the brand is drawn by the host, so this element is the right end of one full-width header.
import { h, clear, focusAfterRender, icon } from "./dom.js";
import { windowLayout } from "./appearance-settings.js";
import { appLogo } from "./logo.js";
import { tabIcon } from "./task-icons.js";

export function hostSummary(host) {
  if (host?.host_guard_reason === "login_required") return { tone: "warning", text: "登录中" };
  if (!host || !host.connected) return { tone: "danger", text: "游戏窗口未连接" };
  if (host.desktop_locked) return { tone: "warning", text: "桌面已锁定" };
  if (host.host_guard_reason) return { tone: "warning", text: "暂不能操作" };
  if (host.hide_mode && host.hide_mode !== "none") return { tone: "primary", text: "后台运行" };
  return { tone: "success", text: "已连接" };
}

// Host status fields that move on every 1 s tick (frame age, capture counters, countdowns). A tick that changes only
// these must not rebuild the whole panel: that recreated every icon <img> once per second (visible flicker) and closed
// any open popover such as 「实机未就绪」.
const VOLATILE_HOST = { frame_age_ms: true, capture_seq: true,
  card_scan: { elapsed_s: true, frames: true, scrolls: true }, knowhow_scan: { elapsed_s: true, frames: true, scrolls: true, books: true },
  run_live: true,  // run_live: the 「运行中」 view refreshes it in place
  // Per-run click counters move every tick; leaving them stable avoids rebuilding the panel and interrupting scrolling.
  clicks_this_run: true, run_window: true };

/** Copy of a status object without the volatile keys. */
export function stableHost(host, volatile = VOLATILE_HOST) {
  if (!host || typeof host !== "object") return host;
  const out = {};
  for (const [key, value] of Object.entries(host)) {
    const rule = volatile[key];
    if (rule === true) continue;
    out[key] = rule && value && typeof value === "object" ? stableHost(value, rule) : value;
  }
  return out;
}

/** "same" (nothing changed), "tick" (only volatile fields moved) or "change". */
export function hostChange(prev, next) {
  if (JSON.stringify(prev ?? null) === JSON.stringify(next ?? null)) return "same";
  return JSON.stringify(stableHost(prev ?? {})) === JSON.stringify(stableHost(next ?? {})) ? "tick" : "change";
}

/** mm:ss for elapsed time. */
export function clock(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.floor(seconds);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Internal run modes; only shown with 开发者选项 (the main UI has just 开始 / 停止). */
export const MODE_LABEL = { read_only: "识别", offline_replay: "离线回放", live: "实机", supervised_daily: "实机日常", supervised_wing: "W.I.N.G." };

/** Small coloured dot followed by plain text (status shown without filled pills). */
export function dotText(tone, text, cls = "") {
  return h("span", { class: `dot-text tone-${tone} ${cls}`.trim() }, h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text }));
}

const logTime = (log) => (typeof log.ts === "number" ? log.ts : Date.parse(log.ts) || 0);

class AppHeader extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    clear(this);
    // Inside a host window the host paints the logo + name on the left half of the same header row.
    // The frameless game host paints 最小化 / 关闭 at the far right of this row; keep 紧急停止 clear of them.
    this.className = state.bridgeKind === "mock" ? "app-header"
      // 面板位置 left / 独立窗口: the buttons sit over the game header instead, so this row needs no gap.
      : state.hostKind !== "browser_host" ? "app-header is-hosted"
      : state.host?.window_layout?.panel_side === "left" ? "app-header is-hosted is-panel-left"
      : state.host?.window_layout?.panel_side === "detached" ? "app-header is-hosted is-panel-detached" : "app-header is-hosted has-window-controls";
    this.setAttribute("role", "banner");
    const host = hostSummary(state.host);
    // external_run: a Maa run started outside the panel (command line) is clicking.
    const running = state.run.active || state.host?.external_run === true
      || [state.host?.card_scan?.state, state.host?.knowhow_scan?.state].some((scan) => scan === "running" || scan === "stopping");
    const brand = h("div", { class: "brand" },
      appLogo(24),
      h("span", { class: "brand-name" }, "MaaShinyColors"),
      state.bridgeKind === "mock" ? h("span", { class: "brand-sub", text: "预览" }) : null);
    const hostItem = dotText(host.tone, host.text, "header-host");
    hostItem.title = "游戏窗口状态";
    const stop = h("button", {
      class: `btn btn-emergency${running ? " is-armed" : ""}`, type: "button", disabled: !running,
      title: running ? "立即停止后续操作" : null,
      onclick: () => actions.stopRun("emergency"),
    }, "紧急停止");
    // 静音 / 刷新 are host-painted icons on the game side of the header; 设置 → 外观 keeps the 静音 switch.
    // native append() would print a missing 拆分 button (null) as text
    // 2026-11-23 (user): a small 日志 button beside the status in every layout; the badge counts new 警告/错误
    const seen = state.logSeenTs || 0;
    const unseen = state.logs.filter((l) => (l.level === "warn" || l.level === "error") && logTime(l) > seen).length;
    const logBtn = h("button", { class: "icon-btn header-log-btn", type: "button", title: "日志", "aria-label": unseen ? `日志，${unseen} 条新警告` : "日志",
      dataset: { focusKey: "header-log" }, onclick: () => actions.openLogs() }, icon("list", { size: 16 }),
      unseen ? h("span", { class: "header-log-badge num", text: unseen > 99 ? "99+" : String(unseen) }) : null);
    this.append(...[brand, h("div", { class: "header-status" }, hostItem, running ? h("span", { class: "header-running", text: "运行中" }) : null, logBtn),
      panelWindowButton(state, actions), stop].filter(Boolean));
  }
}
customElements.define("sc-app-header", AppHeader);

/** one click splits the panel into its own window, or puts it back; only where the host supports it. */
export function panelWindowButton(state, actions) {
  const layout = windowLayout(state.host);
  if (!layout.supported) return null;
  const detached = layout.side === "detached";
  const label = detached ? "放回游戏窗口" : "拆分到独立窗口";
  return h("button", { class: "icon-btn header-window-btn", type: "button", title: label, "aria-label": label,
    dataset: { focusKey: "header-panel-window" }, onclick: () => actions.togglePanelWindow?.() }, icon(detached ? "dockin" : "popout", { size: 16 }));
}

// 主页 (was 日常) is the first tab. 2026-11-19: 日志 / 运行记录 live in the hidden 「运行中」 panel (no extra tab):
// it shows during a run, or after the 任务 tab's log strip opened it (then labelled 日志).
export const TABS = [
  { key: "live", label: "运行中", live: true },  // only while a run is live (or just finished); app.js sets state.liveTab
  { key: "home", label: "主页" },
  { key: "tasks", label: "任务" },
  { key: "produce", label: "Produce" },  // 2026-10-13: W.I.N.G. (later other Produce runs), apart from the daily queue
  { key: "resources", label: "资源" },
  { key: "knowhow", label: "ノウハウ" },
  // 2026-11-19 (user): the developer-only 安全 tab was removed; its status lives in 设置 → 开发者 and the logs.
  { key: "settings", label: "设置" },
];

/** The developer-only 安全 tab appears only with 开发者选项. */
export function visibleTabs(state) {
  return TABS.filter((t) => (!t.dev || state.prefs?.developer === true)
    && (!t.live || state.liveTab === true || state.activeTab === "live"))
    .map((t) => (t.live && state.liveTab !== true ? { ...t, label: "日志", icon: "logs" } : t));
}

class TabRail extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  render() {
    const { state, actions, badges } = this._data;
    clear(this);
    this.className = "tab-rail";
    this.setAttribute("role", "tablist");
    this.setAttribute("aria-label", "工具面板");
    const tabs = visibleTabs(state);
    tabs.forEach((tab, index) => {
      const selected = state.activeTab === tab.key;
      const badge = badges?.[tab.key];
      const button = h("button", {
        class: `tab${selected ? " is-active" : ""}`, type: "button", role: "tab", id: `tab-${tab.key}`,
        "aria-selected": String(selected), "aria-controls": "panel-body", tabindex: selected ? "0" : "-1",
        dataset: { focusKey: `tab-${tab.key}` },
        onclick: () => actions.setTab(tab.key),
        onkeydown: (event) => {
          const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          if (!delta) return;
          event.preventDefault();
          const next = tabs[(index + delta + tabs.length) % tabs.length].key;
          actions.setTab(next);
          focusAfterRender(`tab-${next}`);
        },
      }, tabIcon(tab.icon || tab.key), h("span", { class: "tab-top" }, h("span", { class: "tab-label", text: tab.label }),
        badge ? h("span", { class: `tab-badge tone-${badge.tone}`, title: badge.title, text: badge.text }) : null));
      this.append(button);
    });
  }
}
customElements.define("sc-tab-rail", TabRail);

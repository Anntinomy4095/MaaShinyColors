// 设置 tab (redesign 2026-10-08): fold cards 外观与声音 (open) / 窗口 / 快捷键 / 通知 /
// 实机与安全 (read-only) / 数据 / 存储 (2026-10-14, storage.js) / 问题反馈 / 更新 / 开发者 (closed) / 关于 (2026-10-15, support-settings.js). Every switch says what it does in its title; the live-input state is shown, never changed here.
import { h, clear, jaText } from "./dom.js";
import { foldSection, sectionDeck } from "./common.js";
import { liveConditions, liveProblems } from "./task-panel.js";
import { dataView, muteState, gameDataModel } from "./game-data.js";
import { storageView, storageModel, storageSummary } from "./storage.js";
import { appearanceSummary, appearanceView, windowLayout } from "./appearance-settings.js?v=6";
import { NOTIFY_GROUPS, SYSTEM_NOTIFY_HINT, supportModel, updateText, hotkeyView, feedbackView, updateView, aboutView } from "./support-settings.js";

let supportAsked = false;   // app.support op=info: asked once per page load, when 设置 first renders

export const NOTIFY_KEYS = [
  ["all_done", "全部结束"],
  ["failed", "任务失败"],
  ["uncertain", "结果不确定"],
  ["takeover", "需要接手"],
  ["guard_refused", "未执行（画面不符）"],
  ["free_draw", "免费ガシャ"],
];
// What each notification switch does (switch title; plain words).
export const NOTIFY_HINT = {
  all_done: "运行结束时提示",
  failed: "任务失败时提示",
  uncertain: "结果待核对时提示",
  takeover: "需在游戏里接手时提示",
  guard_refused: "画面与任务不符、未点击时提示",
  free_draw: "首页ガシャ显示「無料」时提醒",
};

export const TABLE_HINT = "重新读取并校验 assets/input/pipeline_actions.json，全部通过才替换（失败保留旧表）；帧许可作废。任务运行、扫描或监督运行中拒绝。";

export function devTools(state, actions) {
  const table = state.host?.action_table || null;
  const last = table?.last_reload;
  const tableStatus = table
    ? `动作表 ${String(table.sha256 || "").slice(0, 8)} · ${table.enabled ?? 0}/${table.actions ?? 0} 启用`
      + (last ? (last.ok ? " · 已重新载入" : ` · 上次失败：${last.reason}`) : "")
    : "当前宿主不支持";
  const tableRow = h("div", { class: "field form-row" },
    h("div", { class: "field-control dev-tool-row" },
      h("button", { class: "btn", type: "button", dataset: { focusKey: "dev-table-reload" }, disabled: !table || state.run.active,
        onclick: () => actions.devActionTable() }, "重载动作表"),
      h("span", { class: "num muted small", text: tableStatus })),
    h("details", { class: "dev-hint" }, h("summary", { text: "说明" }), h("p", { class: "muted small", text: TABLE_HINT })));
  return [tableRow];
}

export const BACKGROUND_HINT = "开启：最小化时隐藏到托盘，任务继续运行；点托盘图标还原。锁屏时无法截图和点击。";
export function windowModel(host) {
  const w = host?.window_background;
  if (!w || typeof w !== "object" || w.supported !== true) return { supported: false, enabled: false, forced: false, hidden: false };
  return { supported: true, enabled: w.enabled === true, forced: w.forced === true,
    hidden: typeof w.hide_mode === "string" && w.hide_mode !== "none" };
}

/** 设置 → 窗口: 后台运行 switch (host setting, default off) and 现在隐藏. Host-side; the page only asks. */
export function windowView(state, actions) {
  const m = windowModel(state.host);
  const note = !m.supported ? null : m.forced ? "由启动参数 --background-keepalive 开启"
    : m.hidden ? "窗口已隐藏，从托盘图标还原" : null;
  return h("div", {},
    h("ul", { class: "toggle-list" }, switchRow("window-background", "后台运行", m.enabled, () => actions.windowBackground?.(!m.enabled),
      { focusKey: "window-background", disabled: !m.supported || m.forced, hint: BACKGROUND_HINT })),
    h("div", { class: "field form-row" }, h("div", { class: "field-control dev-tool-row" },
      h("button", { class: "btn", type: "button", dataset: { focusKey: "window-hide" }, disabled: !m.supported || !m.enabled || m.hidden,
        onclick: () => actions.windowHide?.() }, "现在隐藏"),
      note ? h("span", { class: "muted small", text: note }) : null)));
}

export const DEV_HINT = "显示「安全」页、未完成的任务和全部选项；实机权限不变。";

function switchRow(id, label, on, onToggle, { focusKey, disabled = false, hint = null } = {}) {
  return h("li", { title: hint },
    h("span", { class: "switch-text" }, h("span", { id, text: label })),   // the hint is the row tooltip
    h("button", { class: `toggle${on ? " is-on" : ""}`, type: "button", role: "switch", "aria-checked": String(on), "aria-labelledby": id,
      title: hint, disabled, dataset: { focusKey: focusKey || id }, onclick: onToggle },
    h("span", { class: "toggle-track" }, h("span", { class: "toggle-thumb" }))));
}

/** Read-only 实机与安全 card: what live input would need, and where each item is changed (never here). */
export const LIVE_WHERE = "双击 MaaShinyColors.exe 打开，在任务页点「开始」运行。";

function liveCard(state) {
  // Players see what to do (liveProblems); 开发者选项 shows the raw policy / host conditions.
  const dev = state.prefs?.developer === true;
  const conds = dev ? liveConditions(state) : liveProblems(state).map((text) => ({ ok: false, text }));
  const unmet = conds.filter((c) => !c.ok).length;
  return foldSection({ key: "settings-live", title: "实机", defaultOpen: true,   // the first visit opens 实机 (is the game ready?), not 外观
    summary: h("span", { class: `dot-text small tone-${unmet ? "muted" : "success"}` }, h("span", { class: "dot", "aria-hidden": "true" }),
      h("span", { text: unmet ? `实机未就绪 · ${unmet} 项待处理` : "实机就绪" })),
    children: h("div", {},
      unmet || dev ? h("ul", { class: "cond-list settings-conds" }, conds.map((c) => h("li", { class: c.ok ? "ok" : "bad" },
        h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text: c.text }), dev ? h("span", { class: "cond-state", text: c.ok ? "满足" : "未满足" }) : null)))
        : null) });
}

class SettingsPanel extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    clear(this);
    this.className = "panel";
    const prefs = state.prefs;

    if (!state.supportInfo && !supportAsked) { supportAsked = true; actions.supportInfo?.(); }
    // 2026-10-15: grouped by what happened; 系统通知 switches the Windows notifications (the in-page reminders stay).
    const label = Object.fromEntries(NOTIFY_KEYS);
    const systemOn = prefs.notify?.system !== false;
    const notify = h("div", {},
      h("ul", { class: "toggle-list" }, switchRow("nt-system", "系统通知", systemOn, () => actions.setNotify("system", !systemOn),
        { focusKey: "notify-system", hint: SYSTEM_NOTIFY_HINT })),
      NOTIFY_GROUPS.map(([group, keys]) => [
        h("p", { class: "notify-group-label", text: group }),
        h("ul", { class: "toggle-list" }, keys.map((key) =>
          switchRow(`nt-${key}`, label[key], prefs.notify?.[key] !== false, () => actions.setNotify(key, prefs.notify?.[key] === false),
            { focusKey: `notify-${key}`, hint: NOTIFY_HINT[key] })))]),
      h("div", { class: "btn-row" }, h("button", { class: "btn btn-small", type: "button", disabled: state.bridgeKind === "mock" || !systemOn,
        dataset: { focusKey: "notify-test" }, title: state.bridgeKind === "mock" ? "界面预览不支持" : "显示一条系统通知",
        onclick: () => actions.support?.("notify_test") }, "发送测试")));
    const notifyOn = NOTIFY_KEYS.filter(([key]) => prefs.notify?.[key] !== false).length;
    const support = supportModel(state.host);

    const dev = prefs.developer === true;
    const replay = state.runMode === "offline_replay";
    const devList = h("ul", { class: "toggle-list" },
      switchRow("dev-on", "开发者选项", dev, () => actions.setPref("developer", !dev), { focusKey: "dev-on", hint: DEV_HINT }),
      dev ? switchRow("dev-replay", "离线回放", replay, () => actions.setRunMode(replay ? "supervised_daily" : "offline_replay"),
        { focusKey: "dev-replay", disabled: state.run.active, hint: "改为回放演示帧" }) : null,
      // ANN-17: the normal 开始 clicks the game; 只识别 keeps the old read-only run (no click, no spend) for checks.
      dev && !replay ? switchRow("dev-read-only", "只识别（不点击）", state.runMode === "read_only",
        () => actions.setRunMode(state.runMode === "read_only" ? "supervised_daily" : "read_only"),
        { focusKey: "dev-read-only", disabled: state.run.active, hint: "日常任务只读识别，不点击、不消耗" }) : null);
    const devBody = [devList];
    if (dev && replay) {
      // one frame set only: shown as a value, not a one-option dropdown
      devBody.push(h("div", { class: "field form-row" },
        h("div", { class: "field-label-row" }, h("span", { class: "field-label", text: "回放帧集" })),
        h("div", { class: "field-control" }, h("div", { class: "readonly-value", text: state.bridgeKind === "mock" ? "演示帧（预览）" : "宿主提供" }))));
    }
    if (dev) devBody.push(...devTools(state, actions));
    // What the switch unlocks (W.I.N.G. / 安全 tabs, roleplay test 2026-10-07) is its tooltip since 2026-10-24.

    const muted = muteState(state);
    const gd = gameDataModel(state.host);
    const lookLabel = appearanceSummary(prefs, windowLayout(state.host).side, state.layoutMode);
    const folds = [
      foldSection({ key: "settings-look", title: "外观", defaultOpen: false,
        summary: h("span", { class: "muted small", text: `${lookLabel}${muted.on ? " · 已静音" : ""}` }),
        children: h("div", {}, appearanceView(state, actions, switchRow),
          h("ul", { class: "toggle-list" }, switchRow("game-mute", "静音", muted.on, () => actions.gameMute?.(!muted.on),
            { focusKey: "game-mute", disabled: muted.disabled, hint: muted.hint }))) }),
      foldSection({ key: "settings-window", title: "窗口", defaultOpen: false,
        summary: h("span", { class: "muted small", text: windowModel(state.host).supported ? (windowModel(state.host).enabled ? "后台运行 开" : "后台运行 关") : "需要更新程序" }),
        children: windowView(state, actions) }),
      foldSection({ key: "settings-hotkey", title: "快捷键", defaultOpen: false,
        summary: h("span", { class: "muted small", text: support.hotkey.enabled ? (support.hotkey.registered ? "紧急停止 开" : "紧急停止 未生效") : "紧急停止 关" }),
        children: () => hotkeyView(state, actions, switchRow) }),
      foldSection({ key: "settings-notify", title: "通知", count: `${notifyOn}/${NOTIFY_KEYS.length}`, defaultOpen: false,
        summary: h("span", { class: "muted small", text: `${systemOn ? "系统通知" : "仅界面内"} · ${notifyOn === NOTIFY_KEYS.length ? "全部开启" : `关闭 ${NOTIFY_KEYS.length - notifyOn} 项`}` }),
        children: notify }),
      liveCard(state),
      foldSection({ key: "settings-data", title: "数据", defaultOpen: false,
        summary: h("span", { class: "muted small", text: gd.clearSupported ? "缓存 · 登录" : "本机数据" }),
        children: dataView(state, actions) }),
      foldSection({ key: "settings-storage", title: "存储", defaultOpen: false,
        summary: h("span", { class: "muted small num", text: storageSummary(storageModel(state.host)) }),
        children: () => storageView(state, actions) }),
      foldSection({ key: "settings-feedback", title: "问题反馈", defaultOpen: false,
        summary: h("span", { class: "muted small", text: support.lastReport ? "已导出" : "导出问题包 · 日志" }),
        children: () => feedbackView(state, actions) }),
      foldSection({ key: "settings-update", title: "更新", defaultOpen: false,
        summary: h("span", { class: "muted small", text: updateText(support.update, state.supportInfo) }),
        children: () => updateView(state, actions) }),
      foldSection({ key: "settings-dev", title: "开发者", defaultOpen: false,
        summary: h("span", { class: "muted small", text: dev ? `开${replay ? " · 离线回放" : ""}` : "关" }),
        children: h("div", {}, devBody) }),
      foldSection({ key: "settings-about", title: "关于", defaultOpen: false,
        summary: h("span", { class: "muted small num", text: state.schema.interface?.version || "—" }),
        children: () => aboutView(state, actions) }),
    ].filter(Boolean);
    // 双栏: the same folds as a section list beside the picked group (common.js sectionDeck)
    this.append(h("div", { class: "panel-scroll settings-scroll", dataset: { scrollKey: "settings" } },
      state.wide ? sectionDeck(folds, { key: "settings", label: "设置分组", defaultKey: "settings-live" }) : folds));
  }
}
customElements.define("sc-settings-panel", SettingsPanel);

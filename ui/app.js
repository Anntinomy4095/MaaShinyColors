// MaaShinyColors panel UI (U0.2). Native ES modules, no build step, no external resources.
// In the BrowserHost UI WebView2 the page talks to the host only through the ui/protocol.json whitelist.
// In an ordinary browser it runs in mock mode: a 1280x720 placeholder game area plus the panel, fed by
// synthetic fixtures, so the whole window can be previewed offline.
import { h, clear, jaText, setJaTerms, formatTime } from "./components/dom.js";
import { guardedRender, toast, openConfirm } from "./components/common.js";
import { cardDetailsStatus } from "./components/card-library.js";
import { HostBridge, MockBridge, inWebView, loadLocal, saveLocal } from "./components/bridge.js";
import { Store, gameDay, sanitizeProfile, profileFromPreset, profileToRunConfig, profileSpendFlags, PROFILE_VERSION, applyTaskPlacement, applyConsent, addTaskToPlan, removeTaskFromPlan, restoreTaskToPlan } from "./components/store.js";
import { hostSummary, visibleTabs, hostChange } from "./components/header.js";
import { renderQueue, scrollActivity, watchScrolling } from "./components/render-queue.js";
import { devMode, optionRows, startBlockers, addConflicts, wingOnly, singleTaskState, dailyRunState, wingBlockers, taskPlacement, consentOptions, taskInQueue, taskShown, taskName, WING_TASK } from "./components/display.js";
import { loadAcks, saveAcks, openLedger } from "./components/acks.js";
import { loadPassiveReadings, passiveReadingTiles, passiveGachaHint, gachaReminder, mergeTiles } from "./components/passive-readings.js";
import { loadEndResult, openProduceEnd } from "./components/produce-end.js";
import { loadTeamSnapshot } from "./components/team-snapshot.js";
import { loadTeamProposal } from "./components/team-proposal.js";
import { loadGameAssets } from "./components/game-assets.js";
import { openItemPicker } from "./components/card-picker.js";
import { openTrainingCardPicker } from "./components/team-picker.js";
import { openTaskPicker } from "./components/task-add.js";
import { loadPlanStore, savePlanStore, upsertPlan, removePlan, profileFromPlan, openSavePlanDialog } from "./components/plans.js";
import { openLevelImport } from "./components/team-levels-import.js";
import { ownedCards } from "./components/team-store.js";
import { readingTiles, refreshHoldings } from "./components/resource-panel.js";
import { reasonText, updateCardScanProgress } from "./components/card-scan.js";
import { knowhowScanGate, librarySync, openKnowhowScanConfirm, openLibrarySyncPrompt, updateKnowhowScanProgress } from "./components/knowhow-scan.js";
import { loadKnowhowData } from "./components/knowhow-data.js";
import { attentionItems, startNotice } from "./components/daily-panel.js";
import { liveModel, liveRunning, liveBanner, updateLiveTick } from "./components/live-panel.js";
import protocol from "./protocol.js";
import { applyAppearance, loadDockSide, panelSide, saveDockSide, VISITOR_LABEL, windowLayout } from "./components/appearance-settings.js?v=6";
import { loadLayout, saveLayout, watchLayout } from "./components/layout-mode.js";
import { liveProblems, dailyRunView } from "./components/task-panel.js";
import { wingRunView } from "./components/wing-panel.js";
import { logAside } from "./components/log-panel.js";
import { createChibiVisitor } from "./components/chibi-visitor.js?v=8";
import "./components/home-panel.js";
import "./components/resource-panel.js";
import "./components/wing-panel.js";
import "./components/knowhow-panel.js";
import "./components/settings-panel.js?v=6";

// 主页 first; the old 日志 tab is the log drawer inside 任务 (old ?tab=logs / ?tab=daily preview links still work).
const PANEL_TAGS = {
  live: "sc-live-panel",  // 「运行中」: shown while a run is live; takes over once per run
  home: "sc-home-panel",
  tasks: "sc-task-panel",
  produce: "sc-wing-panel",  // 2026-10-13: W.I.N.G. (and later other Produce runs) start here, never in the daily queue
  resources: "sc-resource-panel",
  knowhow: "sc-knowhow-panel",
  settings: "sc-settings-panel",
};
const PREFS_KEY = "sc-ui-prefs";
const DEFAULT_PREFS = { theme: "system", notify: {}, developer: false };
const TAB_ALIASES = { daily: "home", logs: "live", wing: "produce" };
const TERMINAL = new Set(["succeeded", "skipped", "failed", "uncertain", "guard_refused", "takeover"]);

async function createBridge() {
  if (inWebView()) return { bridge: new HostBridge(protocol, window.chrome.webview), demo: null };
  const fixtures = await import("./fixtures/mock-bundle.js");
  return { bridge: new MockBridge(protocol, fixtures), demo: fixtures.demo };
}

function emptyDemo() {
  return { host: { connected: false }, policy: {}, live_entry: {}, runs: [], logs: [], readings: [], ledger: [], audit: [], daily: { items: [] }, produce: null };
}

function applyPrefs(prefs) {
  applyAppearance(document.documentElement, prefs);
}

function buildLayout(layout) {
  const app = document.getElementById("app");
  clear(app);
  app.className = `app layout-${layout}`;
  app.removeAttribute("aria-busy");
  const header = document.createElement("sc-app-header");
  const rail = document.createElement("sc-tab-rail");
  const body = h("div", { id: "panel-body", class: "panel-body", role: "tabpanel" });
  // 宽屏工作台: the third column (开始/停止 + 日志) fills only at ≥ 1200 px (.app[data-aside]); empty otherwise
  const aside = h("div", { class: "run-aside", "aria-label": "运行与日志" });
  const panel = h("aside", { class: "side-panel", "aria-label": "工具面板" }, rail, body, aside);
  const parts = { header, rail, body, aside, statusbar: null };
  if (layout === "window") {
    const game = h("main", { class: "game-area", "aria-label": "游戏画面占位" },
      h("div", { class: "game-frame" },
        h("div", { class: "game-caption" },
          h("span", { class: "game-size num", text: "1280 × 720" }))));
    parts.statusbar = h("footer", { class: "status-bar", "aria-label": "状态栏" });
    app.append(header, game, panel, parts.statusbar);
  } else {
    app.append(header, panel);
  }
  return parts;
}

/** the 运行 column -- the current tab's start/stop (Produce → W.I.N.G., others → 日常) and the always-open log. */
function renderAside(el, state, actions) {
  if (state.wide !== "aside") { if (el.firstChild) clear(el); return; }
  guardedRender(el, (container) => {
    // The 「运行中」 view keeps one 停止 (its own) besides the header's 紧急停止
    if (state.activeTab !== "live") container.append((state.activeTab === "produce" ? wingRunView : dailyRunView)(state, actions));
    container.append(logAside(state, actions));
  });
}

function renderStatusBar(el, state) {
  if (!el) return;
  clear(el);
  const last = state.logs[state.logs.length - 1];
  const host = hostSummary(state.host);
  el.append(h("span", { class: "sb-item sb-log" }, last ? jaText(`${formatTime(last.ts)}  ${last.text}`) : "暂无日志"));
  if (devMode(state)) {
    el.append(
      h("span", { class: "sb-item num" }, `帧龄 ${state.host?.frame_age_ms ?? "—"} ms`),
      h("span", { class: "sb-item num" }, `点击 ${state.host?.clicks_this_run ?? 0}`));
  }
  el.append(h("span", { class: `sb-item tone-text-${host.tone}` }, host.text));
}

function tabBadges(state) {
  const attention = Object.entries(state.taskStatus).filter(([n, s]) => ["uncertain", "takeover"].includes(s.status) && taskInQueue(state, n));
  const wing = Object.entries(state.taskStatus).filter(([n, s]) => ["uncertain", "takeover"].includes(s.status) && n === WING_TASK);
  const home = attentionItems(state, gameDay(state.now ? new Date(state.now) : new Date())).filter((i) => i.key !== "host");  // host state is already in the header
  const ledger = openLedger(state);   // 已核对 entries drop out (acks.js)
  const badge = (list, title, sig) => (list.length ? { text: String(list.length), tone: "uncertain", title: `${list.length} ${title}`, sig: JSON.stringify(sig) } : null);
  return {
    home: badge(home, "项待处理", home.map((i) => i.key)),
    tasks: badge(attention, "个任务待核对", attention.map(([n, s]) => [n, s.status])),
    produce: badge(wing, "个结果待核对", wing.map(([n, s]) => [n, s.status])),
    resources: badge(ledger, "条消耗待核对", ledger.map((l) => l.id ?? l.ts ?? l.status)),
  };
}

/**
 * Red dots clear once the user has looked: the open tab never shows its dot, and the dot stays gone until the items
 * behind it change (a new or different item brings it back).
 */
function visibleBadges(store, state) {
  const all = tabBadges(state);
  const seen = { ...(state.badgeSeen || {}) };
  let moved = false;
  const out = {};
  for (const [key, badge] of Object.entries(all)) {
    if (!badge) { if (key in seen) { delete seen[key]; moved = true; } continue; }
    if (key === state.activeTab) { if (seen[key] !== badge.sig) { seen[key] = badge.sig; moved = true; } continue; }
    out[key] = seen[key] === badge.sig ? null : badge;
  }
  if (moved) store.state = { ...store.state, badgeSeen: seen };  // no re-render: nothing visible changes for this tab
  return out;
}

function captureView() {
  const active = document.activeElement;
  const focus = active?.dataset?.focusKey
    ? { key: active.dataset.focusKey, start: active.selectionStart ?? null, end: active.selectionEnd ?? null }
    : null;
  const scroll = {};
  document.querySelectorAll("[data-scroll-key]").forEach((el) => { scroll[el.dataset.scrollKey] = el.scrollTop; });
  // <details> the user opened or closed survive a re-render (keyed by data-open-key, else class + summary + occurrence).
  const open = new Map();
  detailsKeys().forEach(([key, el]) => open.set(key, el.open));
  return { focus, scroll, open };
}

function detailsKeys() {
  const seen = new Map();
  return [...document.querySelectorAll("#app details")].map((el) => {
    const base = el.dataset.openKey || `${el.className}|${el.querySelector(":scope > summary")?.textContent?.trim() || ""}`;
    const n = (seen.get(base) || 0) + 1;
    seen.set(base, n);
    return [`${base}#${n}`, el];
  });
}

function restoreView({ focus, scroll, open }) {
  detailsKeys().forEach(([key, el]) => {
    if (el.parentElement?.classList.contains("deck-view")) return;   // a section list opens its pick itself
    if (open?.has(key) && el.open !== open.get(key)) el.open = open.get(key);
  });
  document.querySelectorAll("[data-scroll-key]").forEach((el) => {
    if (el.dataset.scrollKey in scroll) el.scrollTop = scroll[el.dataset.scrollKey];
  });
  if (!focus) return;
  const el = document.querySelector(`[data-focus-key="${CSS.escape(focus.key)}"]`);
  if (!el || el.disabled) return;
  el.focus({ preventScroll: true });
  if (focus.start !== null && typeof el.setSelectionRange === "function") {
    try { el.setSelectionRange(focus.start, focus.end); } catch { /* not a text input */ }
  }
}

async function main() {
  const params = new URLSearchParams(window.location.search);
  const { bridge, demo: demoFixture } = await createBridge();
  await loadGameAssets();
  const layout = bridge.kind === "mock" && params.get("layout") !== "panel" ? "window" : "panel";
  const ready = await bridge.request("ui.ready", { protocol_version: 1 });
  const { schema, annotations } = await bridge.request("schema.get", {});
  const { profile: stored } = await bridge.request("profile.get", {});
  // 安全 tab: read-only policy summary from the host (display only; the mock preview uses its fixture).
  const hostPolicy = bridge.kind === "mock" ? null
    : await bridge.request("policy.get", {}).then((r) => r.policy || {}).catch(() => ({}));
  setJaTerms(annotations.ja_terms || []);
  // Preview-only: ?fresh=1 shows the first-run panel (no runs, readings or scans) for new-user review.
  const fresh = bridge.kind === "mock" && params.get("fresh") === "1";
  const demo = fresh ? { ...emptyDemo(), host: { ...demoFixture?.host, run_live: null } } : demoFixture || emptyDemo();
  const prefs = { ...DEFAULT_PREFS, ...(bridge.kind === "mock" ? loadLocal(PREFS_KEY) : stored?.prefs) };
  // Preview-only overrides (mock bridge): ?theme=light|dark for screenshots of both themes.
  if (bridge.kind === "mock" && ["light", "dark", "system"].includes(params.get("theme"))) prefs.theme = params.get("theme");
  // Preview-only: ?accent= / ?font= / ?density= for screenshots of the 外观 choices.
  if (bridge.kind === "mock") for (const [param, key] of [["accent", "accent"], ["font", "font_size"], ["density", "density"]]) {
    if (params.has(param)) prefs[key] = params.get(param);
  }
  // Preview-only: ?dev=1|0 forces 开发者选项 for screenshots.
  if (bridge.kind === "mock" && ["0", "1"].includes(params.get("dev"))) prefs.developer = params.get("dev") === "1";
  // Preview-only: ?winlayout=1 pretends the host can move the panel (面板位置 and the header 拆分 button; nothing moves).
  if (bridge.kind === "mock" && params.get("winlayout") === "1") demo.host = { ...demo.host, window_layout: { panel_side: "right", supported: true } };
  // Preview-only: ?gamedata=1 pretends the host reports game_data (to look at the enabled controls; nothing is cleared).
  if (bridge.kind === "mock" && params.get("gamedata") === "1") demo.host = { ...demo.host, game_data: { muted: false, clear_supported: true, cache_bytes: 412 * 1024 * 1024, profile_bytes: 538 * 1024 * 1024, busy: false } };
  const defaultPreset = schema.run_config?.default_preset || schema.presets[0]?.name || null;
  // 2026-10-13: MissionStatus is an automatic read of the daily run and W.I.N.G. lives on 「Produce」; saved queues and
  // 我的方案 that still list them are adjusted on load (options kept, nothing fails).
  const placement = taskPlacement(annotations, schema);
  const consent = consentOptions(annotations);  // 2026-10-26: ticking Grade Fes is its spend consent
  const place = (profile) => applyConsent(applyTaskPlacement(profile, placement), consent, { load: true });

  const planStore = loadPlanStore();  // U17: 我的方案 (panel storage; the host profile keeps only the current settings)
  const store = new Store({
    bridgeKind: bridge.kind,
    layoutMode: loadLayout(),  // 设置 → 外观 → 布局 (per device, localStorage)
    wide: null,  // 宽屏 tier from watchLayout: null (单栏) | "md" (分组列表 + 详情) | "aside" (+ 运行 column)
    taskSelected: null,  // 任务 list → detail pick in the wide layout
    // "browser_host" = the frameless game/demo host window: it paints 最小化 / 关闭 over the header's right end.
    hostKind: ready?.host || null,
    schema,
    annotations,
    host: demo.host,
    policy: hostPolicy ?? demo.policy,
    liveEntry: demo.live_entry,
    runs: demo.runs,
    logs: demo.logs,
    readings: demo.readings,
    runReadings: [],  // U3: filtered readings from engine run events (readings.update / run.history)
    ledger: demo.ledger,
    audit: demo.audit,
    daily: demo.daily,
    produce: demo.produce,
    profile: place(sanitizeProfile(schema, stored, defaultPreset)),
    prefs,
    dailyManual: stored?.daily_manual || {},
    taskStatus: {},
    supportInfo: null,  // 设置 → 关于 / 更新: app.support op=info (asked when the settings tab first renders)
    acks: loadAcks(),  // 已核对: {day, tasks, ledger}, kept across restarts (acks.js)
    run: { active: false, id: null, mode: null, tasks: [] },
    liveTakeover: null,  // run_live id the 「运行中」 view already took over for (or "pending" after 开始)
    // ANN-17: the normal 开始 runs the ticked daily tasks for real (supervised_daily); 只识别 / 离线回放 are developer modes.
    runMode: bridge.kind === "mock" && params.get("replay") === "1" ? "offline_replay" : "supervised_daily",  // preview-only ?replay=1
    activeTab: PANEL_TAGS[TAB_ALIASES[params.get("tab")] || params.get("tab")] ? TAB_ALIASES[params.get("tab")] || params.get("tab") : "home",
    logOpen: params.get("tab") === "logs" || params.get("log") === "1",  // log drawer in 任务 (preview: ?log=1)
    historyOpen: params.get("history") === "1",
    logView: params.get("history") === "1" ? "history" : params.get("tab") === "logs" ? "logs" : "run",
    expanded: params.get("expand") ? params.get("expand").split(",") : [],
    plans: planStore.plans,
    activePlan: planStore.active,
    saveStatus: { state: "saved", at: null },  // U17: 已自动保存 / 保存失败 + 重试
    planUndo: null,  // { name, index, enabled } of the last task taken out of the 方案 (inline 撤销)
    logFilter: { level: "", task: "", text: "" },
    now: params.get("now") || null,
  });

  const persist = () => {
    const s = store.get();
    const profile = { schema_version: PROFILE_VERSION, preset: s.profile.preset, tasks: s.profile.tasks, removed_tasks: s.profile.removed_tasks || [], daily_manual: s.dailyManual };
    if (bridge.kind === "mock") saveLocal(PREFS_KEY, s.prefs); else profile.prefs = s.prefs;
    return bridge.request("profile.save", { profile }).then(() => {
      const at = formatTime(new Date());
      if (store.get().saveStatus.state !== "saved") store.set({ saveStatus: { state: "saved", at } }, "save-status");
      else store.get().saveStatus.at = at;
      return true;
    }).catch((error) => {
      store.set({ saveStatus: { state: "failed", at: null, error: String(error?.message || error) } }, "save-status");
      toast(`保存失败：${error.message}`, "danger");
      return false;
    });
  };
  // U17: undoable replacement of the whole profile (重置 / 切换方案): the toast's 撤销 puts the previous one back.
  const replaceProfile = (profile, activePlan, message) => {
    const before = store.get();
    const previous = { profile: before.profile, activePlan: before.activePlan, taskStatus: before.taskStatus };
    store.set({ profile, activePlan, taskStatus: {}, planUndo: null });
    savePlanStore(store.get().plans, activePlan);
    persist();
    toast(message, "neutral", { action: { label: "撤销", onclick: () => {
      if (store.get().run.active) return;
      store.set(previous, "undo-replace");
      savePlanStore(store.get().plans, previous.activePlan);
      persist();
    } } });
  };
  const updateProfile = (mutate) => {
    const s = store.get();
    const tasks = s.profile.tasks.map((t) => ({ ...t, option: { ...t.option } }));
    mutate(tasks);
    store.set({ profile: applyConsent({ ...s.profile, preset: s.profile.preset, tasks }, consent) });
    persist();
  };

  let storageAskedAt = 0;
  const presetCache = new Map();
  let relayout = null;  // set once the layout is built (see watchLayout below)
  const actions = {
    setTab: (key) => store.set({ activeTab: key }),
    selectPreset: (name) => {
      if (!name) { store.set({ profile: { ...store.get().profile, preset: null }, activePlan: null }); savePlanStore(store.get().plans, null); persist(); return; }
      replaceProfile(place(profileFromPreset(schema, name)), null, "已恢复为预设方案");
    },
    // U17: 我的方案. Choosing one replaces the current settings (撤销 available); 另存为 snapshots the current settings.
    selectPlan: (name) => {
      const plan = store.get().plans.find((p) => p.name === name);
      if (!plan || store.get().run.active) return;
      replaceProfile(place(profileFromPlan(schema, plan)), plan.name, `已切换到方案「${plan.name}」`);
    },
    savePlanAs: (rawName) => {
      const s = store.get();
      const result = upsertPlan(s.plans, s.profile, rawName);
      if (result.error) return result;
      if (!savePlanStore(result.plans, result.name)) {
        const error = "方案保存失败，请检查本机存储后重试";
        toast(error, "danger");
        return { error };
      }
      store.set({ plans: result.plans, activePlan: result.name });
      toast(result.replaced ? `已更新方案「${result.name}」` : `已保存方案「${result.name}」`);
      return result;
    },
    updatePlan: () => {
      const s = store.get();
      if (s.activePlan) actions.savePlanAs(s.activePlan);
    },
    // the built-in 预设 as it would load now (placement and Grade consent applied), for 「与预设不同」
    presetProfile: (name) => {
      if (!presetCache.has(name)) presetCache.set(name, place(profileFromPreset(schema, name)));
      return presetCache.get(name);
    },
    deletePlan: (name) => {
      const s = store.get();
      const plan = s.plans.find((p) => p.name === name);
      if (!plan) return;
      const plans = removePlan(s.plans, name);
      const activePlan = s.activePlan === name ? null : s.activePlan;
      if (!savePlanStore(plans, activePlan)) { toast("方案删除失败，请重试", "danger"); return; }
      store.set({ plans, activePlan });
      toast(`已删除方案「${name}」`, "neutral", { action: { label: "撤销", onclick: () => {
        const now = store.get();
        const restored = [...now.plans.filter((p) => p.name !== name), plan].slice(0, 12);
        const restoredActive = s.activePlan === name ? name : now.activePlan;
        if (!savePlanStore(restored, restoredActive)) { toast("方案恢复失败，请重试", "danger"); return; }
        store.set({ plans: restored, activePlan: restoredActive });
      } } });
    },
    openSavePlan: () => openSavePlanDialog(store.get(), { onSave: (name) => actions.savePlanAs(name), onDelete: (name) => actions.deletePlan(name) }),
    retrySave: () => { persist(); },
    setEnabled: (name, enabled) => updateProfile((tasks) => { tasks.find((t) => t.name === name).enabled = enabled; }),
    // 2026-10-14 user: 全选 / 全取消 of the shown 任务 rows (one profile change)
    setEnabledMany: (names, enabled) => updateProfile((tasks) => { for (const t of tasks) if (names.includes(t.name)) t.enabled = enabled; }),
    // 只勾选此项 (row menu) ticks one 任务 row and unticks the other shown rows
    setOnly: (name) => {
      const s = store.get();
      updateProfile((tasks) => { for (const t of tasks) if (t.name === name || taskShown(s, t.name)) t.enabled = t.name === name; });
    },
    setOption: (name, key, value) => updateProfile((tasks) => { tasks.find((t) => t.name === name).option[key] = value; }),
    setOptions: (name, changes) => updateProfile((tasks) => { Object.assign(tasks.find((t) => t.name === name).option, changes); }),
    moveTask: (from, to) => {
      const n = store.get().profile.tasks.length;
      if (to < 0 || to >= n || from === to) return;
      updateProfile((tasks) => { const [item] = tasks.splice(from, 1); tasks.splice(to, 0, item); });
    },
    // 方案 membership: one instance per task (run configs reject repeated names); removal is undoable in place.
    addTask: (name) => {
      const s = store.get();
      if (s.run.active || !taskInQueue(s, name)) return;  // W.I.N.G. / automatic reads are not 任务 rows
      const conflict = addConflicts(s, name).length > 0;  // e.g. Match / Grade share 参加权: join unchecked
      let profile = addTaskToPlan(schema, s.profile, name);
      if (profile === s.profile) return;
      if (conflict) profile = { ...profile, tasks: profile.tasks.map((t) => (t.name === name ? { ...t, enabled: false } : t)) };
      store.set({ profile, planUndo: null });
      persist();
    },
    removeTask: (name) => {
      const s = store.get();
      if (s.run.active) return;
      const { profile, undo } = removeTaskFromPlan(s.profile, name);
      if (!undo) return;
      store.set({ profile, planUndo: undo, expanded: s.expanded.filter((n) => n !== name) });
      persist();
    },
    undoRemove: () => {
      const s = store.get();
      if (s.run.active || !s.planUndo) return;
      store.set({ profile: restoreTaskToPlan(s.profile, s.planUndo), planUndo: null });
      persist();
    },
    dismissUndo: () => store.set({ planUndo: null }),
    selectTask: (name) => store.set({ taskSelected: name }),
    toggleExpanded: (name) => {
      const list = store.get().expanded;
      store.set({ expanded: list.includes(name) ? list.filter((n) => n !== name) : [...list, name] });
    },
    ackStatus: (name) => {
      const s = store.get();
      const statuses = { ...s.taskStatus };
      delete statuses[name];
      // Also hide the same task's reminder from today's run history (主页 / 任务), kept after a restart.
      const today = loadAcks();
      const acks = { ...today, tasks: [...new Set([...(s.acks.day === today.day ? s.acks.tasks : []), name])], ledger: s.acks.ledger,
        notes: s.acks.day === today.day ? s.acks.notes || [] : [] };
      saveAcks(acks);
      store.set({ taskStatus: statuses, acks });
      toast("已核对");
    },
    // × on a 待处理 line: hidden for today while its text stays the same; nothing is retried.
    closeNote: (key) => {
      const s = store.get();
      const today = loadAcks();
      const notes = s.acks.day === today.day ? s.acks.notes || [] : [];
      const acks = { ...today, tasks: s.acks.day === today.day ? s.acks.tasks : [], ledger: s.acks.ledger, notes: [...new Set([...notes, key])] };
      saveAcks(acks);
      store.set({ acks });
    },
    ackLedger: (key) => {
      const s = store.get();
      const live = new Set((s.ledger || []).map((l) => `${l.ts || ""}|${l.task || ""}`));
      const acks = { ...s.acks, ledger: [...new Set([...s.acks.ledger.filter((k) => live.has(k)), key])] };
      saveAcks(acks);
      store.set({ acks });
      toast("已核对");
    },
    // A task's settings: picked in the wide list, expanded in one column (主页 「去任务」, 「去修改」, the row menu).
    openTask: (name) => {
      const s = store.get();
      store.set({ activeTab: "tasks", taskSelected: name, expanded: s.expanded.includes(name) ? s.expanded : [...s.expanded, name] });
    },
    // Offline replay is a developer option; the main UI only starts / stops.
    setRunMode: (mode) => { if (["supervised_daily", "read_only", "offline_replay"].includes(mode)) store.set({ runMode: mode }); },
    toggleDrawer: (key) => { if (["logOpen", "historyOpen"].includes(key)) store.set({ [key]: !store.get()[key] }); },
    setLogFilter: (patch) => store.set({ logFilter: { ...store.get().logFilter, ...patch } }),
    // 「运行中」 panel view: run (only during a run) | logs | history; the 任务 log strip opens it on logs.
    setLogView: (view) => store.set({ logView: ["run", "logs", "history"].includes(view) ? view : "logs" }),
    openLogs: () => store.set({ activeTab: "live", logView: "logs", logSeenTs: Date.now() }),
    setDailyManual: (day, key, value) => {
      const manual = { ...store.get().dailyManual };
      manual[day] = { ...(manual[day] || {}), [key]: value };
      store.set({ dailyManual: manual });
      persist();
    },
    setPref: (key, value) => {
      const next = { ...store.get().prefs, [key]: value };
      applyPrefs(next);
      const patch = { prefs: next };
      if (key === "developer" && !value) patch.runMode = "supervised_daily";
      store.set(patch);
      return persist();
    },
    setNotify: (key, value) => actions.setPref("notify", { ...store.get().prefs.notify, [key]: value }),
    setLayout: (mode) => { saveLayout(mode); store.set({ layoutMode: mode }); relayout?.(); },
    startRun: async (onlyTask = null) => {
      const current = store.get();
      // 2026-10-13: W.I.N.G. starts only from 「Produce」 (startRun(WING_TASK)); the daily 开始 runs the 任务 rows
      // (dailyRunState: hidden developer tasks and other tabs' tasks never run, the automatic MissionStatus read joins).
      const s = onlyTask ? singleTaskState(current, onlyTask) : dailyRunState(current);
      if (!s) return;
      if (s.run.active) return;
      const wingRun = onlyTask === WING_TASK;
      if ((wingRun ? wingBlockers(s) : startBlockers(s)).length) return;  // e.g. a team that repeats an idol; the button is disabled too
      const profile = s.profile;
      const devRun = devMode(s) && ["offline_replay", "read_only"].includes(s.runMode) ? s.runMode : null;
      const wing = wingRun && wingOnly(s) && !devRun;
      const mode = wing ? "supervised_wing" : devRun || "supervised_daily";
      const config = profileToRunConfig(schema, profile);
      const request = { mode, config, spend_flags: profileSpendFlags(schema, profile) };
      // The 开始 buttons are disabled while the host cannot operate the game (liveStartProblems); a stale click still
      // stops here before anything is saved or sent (the host re-checks everything).
      const blocked = bridge.kind === "mock" || devRun ? [] : liveProblems(s, { start: true });
      if (blocked.length) { toast(`无法开始：${blocked[0]}`, "danger"); return; }
      if (mode === "offline_replay") request.frames_id = "demo-frames";
      // No confirmation dialog: W.I.N.G.'s saved 「没有进行中就开新局」 switch is its stamina consent, and
      // each daily task shows its 「消耗：…」 in its settings, so pressing 开始 is the consent.
      // The host still requires confirmed:true and re-derives every spend from the saved profile.
      if (wing || mode === "supervised_daily") request.confirmed = true;
      // Tasks may run again on the same day, also after an uncertain result; one warning, never a
      // block (each task re-reads the game screen). W.I.N.G. may run many times a day: only its uncertain result warns.
      const notice = devRun ? { lines: [] } : startNotice(s, gameDay(s.now ? new Date(s.now) : new Date()),
        wingRun ? [WING_TASK] : config.tasks.filter((t) => t.enabled && taskInQueue(s, t.name)).map((t) => t.name),
        { done: !wingRun });
      if (notice.lines.length && !await openConfirm({ title: notice.title, lines: notice.lines, confirmText: "继续运行" })) return;
      try {
        // First use and migrated profiles can exist only in the page. Save the full plan (not this run's temporary
        // selection) before the host re-derives spending from it; do not start if saving failed.
        if (!await persist()) return;
        const { run_id: runId } = await bridge.request("run.start", request);
        const names = config.tasks.filter((t) => t.enabled).map((t) => t.name);
        const statuses = {};
        names.forEach((n) => { statuses[n] = { status: "pending", reason: null }; });
        const acks = { ...s.acks, tasks: (s.acks?.tasks || []).filter((n) => !names.includes(n)) };  // a new result needs a new 已核对
        saveAcks(acks);
        // the 「运行中」 view takes over at once; the first run_live of this run then only records its id.
        store.set({ run: { active: true, id: runId, mode, tasks: names, startedAt: new Date().toISOString() }, taskStatus: statuses, acks,
          activeTab: "live", logView: "run", liveTakeover: "pending" });
      } catch (error) {
        toast(`无法开始：${error.message}`, "danger");
      }
    },
    startWing: async () => {
      // 2026-11-23 (user): without the card details 开始 asks first; 「下载完后自动开始」 starts once they are complete
      const st = cardDetailsStatus(store.get().host);
      if (st.ready || !store.get().host?.card_library?.supported) return actions.startRun(WING_TASK);
      const ok = await openConfirm({ title: "开始自动挖矿？",
        lines: ["自动挖矿需要卡资料（技能、属性）。", st.busy && st.total ? `本机还没下载完：${st.updated} / ${st.total}，约 ${st.minutes} 分钟。` : `本机还没下载：约 ${st.minutes} 分钟。`],
        confirmText: "下载完后自动开始" });
      if (!ok) return;
      store.set({ wingAfterLibrary: true });
      if (!st.busy) await actions.cardLibraryUpdate();
    },
    devActionTable: async () => {
      try {
        await bridge.request("dev.action_table", { op: "reload" });
        toast("动作表已重新载入");
      } catch (error) { toast(`重新载入动作表：${error.message}`, "danger"); }
    },
    // 设置 → 静音游戏: availability is reported by status.update.game_data; clearing is a separate capability.
    gameMute: async (muted) => {
      try {
        await bridge.request("game.audio.set", { muted: Boolean(muted) });
        toast(muted ? "游戏已静音" : "游戏声音已打开");
      } catch (error) { toast(`静音游戏：${error.message}`, "danger"); }
    },
    // 设置 → 外观 → 面板位置 (2026-10-16; 独立窗口): host window preference; the host re-lays out and reports status.update.window_layout.
    windowLayout: async (side) => {
      const target = panelSide(side);
      try {
        await bridge.request("window.layout", { panel_side: target });
        saveDockSide(target);   // remembers right / left for 「放回游戏窗口」
      } catch (error) { toast(`面板位置：${error.message}`, "danger"); }
    },
    // header button: split the panel into its own window, or put it back where it was docked.
    togglePanelWindow: () => {
      const current = windowLayout(store.get().host).side;
      saveDockSide(current);
      return actions.windowLayout(current === "detached" ? loadDockSide() : "detached");
    },
    // U05: 「按当前 P 重新生成」 — the host reruns the offline recommender for this P card and stat; resolves
    // true once status.update.team_propose reports this run finished (the caller then reloads the proposal file).
    teamPropose: async ({ pCard, primary }) => {
      const before = store.get().host?.team_propose?.generation ?? 0;
      try { await bridge.request("team.propose", { p_card: pCard, primary }); }
      catch (error) { toast(`重新推荐：${error.message}`, "danger"); return false; }
      toast(pCard === "auto" ? "正在推荐编队（比较持有的 P 卡，约 2 分钟）…" : "正在按所选 P 偶像推荐编队…");
      for (let i = 0; i < 320; i += 1) {   // host timeout 300 s (推荐编队 compares up to 8 P cards)
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const tp = store.get().host?.team_propose;
        if (!tp || (tp.generation ?? 0) <= before || tp.state === "running") continue;
        if (tp.state === "done") { toast("推荐已更新"); return true; }
        toast(`重新推荐失败（${tp.error || "未知原因"}）`, "danger");
        return false;
      }
      toast("重新推荐超时", "danger");
      return false;
    },
    // 设置 → 窗口: 后台运行 (host setting, default off; minimize hides to the tray and keeps running) / 现在隐藏.
    windowBackground: async (enabled) => {
      try {
        await bridge.request("window.background", { op: "set", enabled: Boolean(enabled) });
        toast(enabled ? "后台运行已开启" : "后台运行已关闭");
      } catch (error) { toast(`后台运行：${error.message}`, "danger"); }
    },
    windowHide: async () => {
      try { await bridge.request("window.background", { op: "hide" }); }
      catch (error) { toast(`隐藏窗口：${error.message}`, "danger"); }
    },
    // 设置 → 关于 / 问题反馈 / 更新 / 通知测试 / 快捷键: the host owns every path, URL and file list.
    supportInfo: async () => {
      try { store.set({ supportInfo: await bridge.request("app.support", { op: "info" }) }, "support-info"); }
      catch { store.set({ supportInfo: { unavailable: true } }, "support-info"); }
    },
    support: async (op, extra = {}) => {
      try {
        const result = await bridge.request("app.support", { op, ...extra }, 30000);
        if (op === "issue_report") toast(`问题包已生成：${result.file}（${result.files} 个文件），已在资源管理器中选中`, "success");
        else if (op === "notify_test") toast("已发送测试通知，桌面右下角应弹出提示");
        else if (op === "update_check") toast("正在检查更新…");
        else if (op === "hotkey") toast(result.hotkey?.enabled ? (result.hotkey.registered ? "紧急停止快捷键已开启" : "快捷键被其他程序占用，未能开启") : "紧急停止快捷键已关闭",
          result.hotkey?.enabled && !result.hotkey.registered ? "warning" : undefined);
        return result;
      } catch (error) { toast(error.message, "danger"); return null; }
    },
    gameClearCache: async () => {
      try {
        await bridge.request("game.data.clear", { kind: "cache" });
        toast("正在清理游戏缓存…");
      } catch (error) { toast(`清理游戏缓存：${error.message}`, "danger"); }
    },
    gameClearLogin: async () => {
      try {
        // The panel has already shown its own confirmation (openLoginClearConfirm); the host re-checks `confirmed`.
        await bridge.request("game.data.clear", { kind: "login", confirmed: true }, 60000);
        toast("正在清除登录信息…");
      } catch (error) { toast(`清除登录信息：${error.message}`, "danger"); }
    },
    // 设置 → 存储: sizes (host caches 5 min; asked at most once a minute unless 重新计算), 清理 one category (the panel has already shown
    // its own confirmation; the host re-checks confirmed and what may be deleted), 自动清理 switch + retention days.
    storageStatus: async (refresh = false) => {
      const now = Date.now();
      if (!refresh && now - storageAskedAt < 60000) return;
      storageAskedAt = now;
      try { await bridge.request("storage.status", refresh ? { refresh: true } : {}); }
      catch (error) { if (refresh) toast(`存储：${error.message}`, "danger"); }
    },
    storageClean: async (category) => {
      try {
        await bridge.request("storage.clean", { category, confirmed: true });
        toast("正在清理…");
      } catch (error) { toast(`清理：${error.message}`, "danger"); }
    },
    storageAuto: async (enabled, days) => {
      try {
        await bridge.request("storage.auto", { enabled: Boolean(enabled), days });
        toast(enabled ? `自动清理已开启：保留 ${days} 天` : "自动清理已关闭");
      } catch (error) { toast(`自动清理：${error.message}`, "danger"); }
    },
    // 资源 → 扫描持有卡: the host launches the supervised scan (the panel has already shown its own confirmation).
    cardScanStart: async () => {
      try {
        await bridge.request("cards.scan.start", { confirmed: true });
        toast("扫描持有卡已开始：请勿触碰游戏画面", "warning");
      } catch (error) { toast(error.message, "danger"); }
    },
    cardLibraryUpdate: async () => {
      try { await bridge.request("cards.library.update", {}); }
      catch (error) { toast(`更新卡资料：${error.message}`, "danger"); }
    },
    cardLibraryStop: async () => {
      try { await bridge.request("cards.library.stop", {}); }
      catch (error) { toast(`停止更新：${error.message}`, "danger"); }
    },
    // ノウハウ → 扫描ノウハウ: the host launches the supervised KnowhowScan (mode chosen in the panel's own confirmation).
    knowhowScanStart: async (mode, count = 0) => {
      const params = mode === "newest" ? { confirmed: true, mode, count } : { confirmed: true, mode: mode === "scan" ? "scan" : "calibrate" };
      try {
        await bridge.request("knowhow.scan.start", params);
        toast(mode === "newest" ? "补读新书已开始：请勿触碰游戏画面" : mode === "scan" ? "扫描ノウハウ已开始：请勿触碰游戏画面"
          : "ノウハウ校准已开始：请勿触碰游戏画面", "warning");
      } catch (error) { toast(error.message, "danger"); }
    },
    // 自动卖书 turned on / strategy changed (task-panel): when the local library is not current, suggest reading it and go
    // to the ノウハウ page's own 读取书库 confirmation (补读新书 when only new books are missing and the host has it).
    knowhowSellCheck: async () => {
      const state = store.get();
      const data = await loadKnowhowData({ demo: state.bridgeKind === "mock" });
      const sync = librarySync(data.books, data.scan, data.pending.length);
      const newest = Array.isArray(state.host?.knowhow_scan?.modes) && state.host.knowhow_scan.modes.includes("newest");
      openLibrarySyncPrompt(sync, { newest, onRead: (mode, count) => {
        actions.setTab("knowhow");
        if (!knowhowScanGate(store.get().policy, store.get().host).ready) return;   // the page's button shows why
        openKnowhowScanConfirm({ mode, count, onConfirm: (chosen) => actions.knowhowScanStart(chosen, chosen === "newest" ? count : 0) });
      } });
    },
    knowhowScanStop: async () => {
      try {
        await bridge.request("knowhow.scan.stop", {});
        toast("正在停止扫描，已读书库资料会保留");
      } catch (error) { toast(`停止请求失败：${error.message}`, "danger"); }
    },
    cardScanStop: async () => {
      try {
        await bridge.request("cards.scan.stop", {});
        toast("正在停止扫描，回到首页后结束");
      } catch (error) { toast(`停止请求失败：${error.message}`, "danger"); }
    },
    stopRun: async (kind) => {
      try {
        const { accepted } = await bridge.request("run.stop", { kind, run_id: store.get().run.id || undefined });
        if (!accepted) { toast("当前没有运行中的任务"); return; }
        const run = store.get().run;
        if (run.active) {
          store.set({ run: { ...run, stopping: true } });  // the button shows 「停止中…」 until run.update finished
        }
        toast(kind === "emergency" ? "已发送停止请求" : "当前步骤结束后停止");
      } catch (error) {
        toast(`停止请求失败：${error.message}`, "danger");
      }
    },
  };

  const notify = (key, message, tone) => {
    if (store.get().prefs.notify?.[key] !== false) toast(message, tone);
  };

  bridge.addEventListener("log.append", (event) => store.set({ logs: [...store.get().logs, event.detail].slice(-5000) }, "log"));
  // U3: engine readings and recent run records (host mode). Tiles are derived only from observed readings.
  // ANN-17: run readings merged with the persisted passive readings (ui/game-assets/passive-readings.js, newest wins).
  const tilesFor = (runReadings) => {
    const passive = passiveReadingTiles();
    return passive.length ? mergeTiles(readingTiles(runReadings, []), passive) : readingTiles(runReadings, demo.readings);
  };
  // 2026-10-12: one toast per game day when the Home ガシャ 「無料」 tag was read (display only; the tool never draws).
  let gachaToastDay = null;
  const gachaToast = () => {
    const reminder = gachaReminder(passiveGachaHint(), new Date());
    if (!reminder || gachaToastDay === gameDay()) return;
    gachaToastDay = gameDay();
    notify("free_draw", `免费ガシャ「${reminder.tag}」`, "accent");
  };
  const refreshPassive = () => loadPassiveReadings({ force: true })
    .then(() => { store.set({ readings: tilesFor(store.get().runReadings) }, "readings"); gachaToast(); }).catch(() => {});
  bridge.addEventListener("readings.update", (event) => {
    const runReadings = [...store.get().runReadings, event.detail].slice(-500);
    store.set({ runReadings, readings: tilesFor(runReadings) }, "readings");
  });
  bridge.addEventListener("run.history", (event) => {
    const runs = Array.isArray(event.detail.runs) ? event.detail.runs : [];
    const runReadings = runs.slice().reverse().flatMap((r) => (Array.isArray(r.readings) ? r.readings : [])).slice(-500);
    store.set({ runs, runReadings, readings: tilesFor(runReadings) }, "history");
  });
  bridge.addEventListener("status.update", (event) => {
    const prev = store.get().host;
    const host = { ...prev, ...event.detail };
    const change = hostChange(prev, host);
    if (change === "same") return;
    store.set({ host }, change === "tick" ? "status-tick" : "status");
    // auto-takeover: a new live run (also one started outside the panel, e.g. a supervised W.I.N.G. segment) opens
    // 「运行中」 once; the user may switch tabs afterwards and is not pulled back for the same run.
    const model = liveModel(store.get());
    const seen = store.get().liveTakeover;
    if (liveRunning(model) && model.runId && seen !== model.runId) {
      store.set(seen === "pending" ? { liveTakeover: model.runId } : { liveTakeover: model.runId, activeTab: "live", logView: "run" });
    }
    // A scan the host finished since this page last looked: re-read the import files once (the baseline is the first status
    // seen, so reopening the page never replays an old result). Imported results follow the normal auto-accept flow.
    const before = prev?.card_scan?.finished_seq;
    const after = host.card_scan?.finished_seq;
    if (Number.isInteger(before) && Number.isInteger(after) && after > before) {
      const scan = host.card_scan;
      if (scan.outcome === "scanned" && scan.imported === true) {
        refreshHoldings(store.get(), actions, () => store.set({}, "scan")).catch(() => {});
        notify("all_done", "扫描持有卡完成，正在读入结果", "success");
      } else {
        notify("failed", `扫描持有卡未完成：${reasonText(scan.reason) || "原因未知"}`, "warning");
      }
    }
    const libraryBefore = prev?.card_library?.finished_seq;
    const libraryAfter = host.card_library?.finished_seq;
    if (Number.isInteger(libraryBefore) && Number.isInteger(libraryAfter) && libraryAfter > libraryBefore) {
      // Import the newly published public list once; keep all personal selections in the profile.
      loadGameAssets({ force: true }).then(() => store.set({}, "card-library")).catch(() => {});
      if (store.get().wingAfterLibrary) {
        store.set({ wingAfterLibrary: false });
        if (cardDetailsStatus(host).ready) actions.startRun(WING_TASK);
        else notify("failed", "卡资料没有下载完，自动挖矿未开始", "warning");
      }
    } else if (prev?.card_library?.phase === "icons" && host.card_library?.phase !== "icons") {
      // 2026-11-23: images come first and are published before the card details; show them right away
      loadGameAssets({ force: true }).then(() => store.set({}, "card-library")).catch(() => {});
    }
    // Modules the host copied after a write outside its own flows (2026-10-24: command-line ノウハウ scan segments):
    // re-read each once. knowhow.js follows knowhowReloadKey; the first status seen is the baseline.
    const assetsBefore = prev?.game_assets?.modules;
    const assetsAfter = host.game_assets?.modules;
    if (assetsBefore && assetsAfter && bridge.kind !== "mock") {
      const changed = new Set(Object.keys(assetsAfter).filter((name) => assetsAfter[name] !== assetsBefore[name]));
      if (["owned-levels.js", "inventory-scan.js", "item-scan.js"].some((name) => changed.has(name))) {
        refreshHoldings(store.get(), actions, () => store.set({}, "scan")).catch(() => {});
      }
      if (changed.has("passive-readings.js")) refreshPassive();
      if (changed.has("team-snapshot.js")) loadTeamSnapshot({ force: true }).then(() => store.set({}, "snapshot")).catch(() => {});
      if (changed.has("team-proposal.js")) loadTeamProposal(Date.now()).then(() => store.set({}, "proposal")).catch(() => {});
    }
  });
  bridge.addEventListener("run.update", (event) => {
    const data = event.detail;
    const s = store.get();
    if (s.run.id && data.run_id !== s.run.id) return;
    const statuses = { ...s.taskStatus };
    if (data.task && data.status) {
      statuses[data.task] = { status: data.status, reason: data.reason || null };
      if (data.status === "uncertain") notify("uncertain", "结果不确定，请在游戏中核对", "uncertain");
      if (data.status === "takeover") notify("takeover", "需在游戏里接手", "accent");
      if (data.status === "failed") notify("failed", "有任务失败，详情见「日志」", "danger");
      if (data.status === "guard_refused") notify("guard_refused", "未执行：画面与任务不符，未点击", "warning");
    }
    if (data.state === "stopping") {
      if (s.run.active && !s.run.stopping) store.set({ run: { ...s.run, stopping: true } });
      for (const name of s.run.tasks) {
        const cur = statuses[name]?.status;
        if (cur === "running") statuses[name] = { status: data.status || "uncertain", reason: data.reason === "emergency" ? "已紧急停止，请在游戏中核对" : "已停止" };
        else if (!cur || cur === "pending") statuses[name] = { status: "skipped", reason: "未开始" };
      }
    }
    if (data.state === "finished") {
      const record = {
        id: s.run.id, started_at: s.run.startedAt, mode: s.run.mode, outcome: data.outcome || null, reason: data.reason || null,
        tasks: s.run.tasks.map((n) => ({ name: n, status: TERMINAL.has(statuses[n]?.status) ? statuses[n].status : "skipped", reason: statuses[n]?.reason || null })),
      };
      store.set({ taskStatus: statuses, run: { active: false, id: null, mode: null, tasks: [] }, runs: [record, ...s.runs] });
      // A supervised W.I.N.G. run may have saved a new 编队快照 (the host copied team-snapshot.js); re-read it.
      if (s.run.mode === "supervised_wing") loadTeamSnapshot({ force: true }).then(() => store.set({}, "snapshot"));
      // ANN-17: the host published this run's passive readings (资源 page).
      if (bridge.kind !== "mock") refreshPassive();
      // ANN-29: the real Produce end summary of this W.I.N.G. run (reward handover / finish), in the end-result dialog.
      if (s.run.mode === "supervised_wing") {
        loadEndResult(s.run.startedAt).then((result) => {
          if (result) openProduceEnd({ payload: result.payload, notify: store.get().prefs.notify || {}, onNotify: actions.setNotify, runKey: s.run.id });
        }).catch(() => {});
      }
      // The host reports the outcome from run events + summary; never assume success from the process ending.
      const tone = { succeeded: "success", failed: "danger", uncertain: "uncertain", stopped: "warning", refused: "warning" }[data.outcome] || "success";
      notify("all_done", data.reason ? `运行结束：${data.reason}` : "运行结束", tone);
      return;
    }
    store.set({ taskStatus: statuses });
  });

  applyPrefs(prefs);
  // Popover-style <details> (实机未就绪 checklist) close on a click outside them or Esc, never on their own.
  document.addEventListener("pointerdown", (event) => {
    document.querySelectorAll("details.live-indicator[open]").forEach((el) => { if (!el.contains(event.target)) el.open = false; });
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") document.querySelectorAll("details.live-indicator[open]").forEach((el) => { el.open = false; });
  });
  const parts = buildLayout(layout);
  // 单栏 / 双栏 layout of the docked panel (the mock window keeps its fixed 400 px panel)
  relayout = layout === "panel"
    ? watchLayout(document.getElementById("app"), () => store.get().layoutMode, (tier) => { if (store.get().wide !== tier) store.set({ wide: tier }); })
    : null;
  const render = () => {
    const view = captureView();
    let state = store.get();
    state = { ...state, liveTab: Boolean(liveModel(state)) };
    if (!visibleTabs(state).some((t) => t.key === state.activeTab)) { store.state = { ...state, activeTab: "home" }; state = store.get(); }
    if (state.liveTab === undefined) state = { ...state, liveTab: Boolean(liveModel(state)) };
    parts.header.data = { state, actions };
    parts.rail.data = { state, actions, badges: visibleBadges(store, state) };
    guardedRender(parts.body, (container) => {
      const panel = document.createElement(PANEL_TAGS[state.activeTab]);
      container.setAttribute("aria-labelledby", `tab-${state.activeTab}`);
      const banner = liveBanner(state, actions);  // small 「运行中」 banner on the other tabs
      if (banner) container.append(banner);
      container.append(panel);
      panel.data = { state, actions };
    });
    renderAside(parts.aside, state, actions);
    renderStatusBar(parts.statusbar, state);
    restoreView(view);
  };
  // Update changing counters without rebuilding the panel.
  const renderTick = () => {
    const view = captureView();
    const state = store.get();
    if (state.activeTab === "resources") updateCardScanProgress(parts.body, state.host?.card_scan);
    if (state.activeTab === "knowhow") updateKnowhowScanProgress(parts.body, state.host?.knowhow_scan);
    // run_live is a volatile field -- refresh the 「运行中」 view in place, or just the banner on other tabs.
    if (state.activeTab === "live") {
      if (!updateLiveTick(parts.body, state)) guardedRender(parts.body, (container) => {
        const panel = document.createElement(PANEL_TAGS.live);
        container.append(panel);
        panel.data = { state, actions };
      });
    } else {
      const old = parts.body.querySelector(":scope > .live-banner");
      const next = liveBanner(state, actions);
      if (old && next) old.replaceWith(next);
      else if (old) old.remove();
      else if (next) parts.body.prepend(next);
    }
    if (state.activeTab === "settings" && devMode(state)) {
      guardedRender(parts.body, (container) => {
        const panel = document.createElement(PANEL_TAGS[state.activeTab]);
        container.append(panel);
        panel.data = { state, actions };
      });
    }
    renderStatusBar(parts.statusbar, state);
    restoreView(view);
  };
  // Host-driven changes wait while the user scrolls a panel (components/render-queue.js); the user's own render at once.
  const activity = scrollActivity();
  watchScrolling(document, activity);
  const enqueue = renderQueue({ render, renderTick, activity });
  store.addEventListener("change", (event) => enqueue(event.detail?.reason));
  render();
  // Preview-only: ?open=cards opens the training card picker (mock bridge, nothing is started).
  if (bridge.kind === "mock" && ["cards", "cards-all"].includes(params.get("open"))) {
    const entry = store.get().profile.tasks.find((t) => t.name === "Training");
    if (entry) openTrainingCardPicker(store.get(), { value: entry.option["训练卡"], onSelect: (v) => actions.setOption("Training", "训练卡", v) });
  }
  // Preview-only: ?open=live opens the 「实机未就绪」 checklist (read-only; with ?ticks=1 it must stay open).
  if (bridge.kind === "mock" && params.get("open") === "live") document.querySelector("details.live-indicator")?.setAttribute("open", "");
  // Preview-only: ?open=add-task opens the 「添加任务」 picker; ?remove=<Task> takes a task out first (shows 撤销).
  if (bridge.kind === "mock" && params.get("remove")) actions.removeTask(params.get("remove"));
  // Preview-only: ?add=WeeklyProduce,Diagnostics puts removed tasks back into the 方案 (mock bridge, nothing runs).
  if (bridge.kind === "mock" && params.get("add")) params.get("add").split(",").forEach((name) => actions.addTask(name));
  if (bridge.kind === "mock" && params.get("open") === "add-task") openTaskPicker(store.get(), { onAdd: (name) => actions.addTask(name) });
  // Preview-only: ?open=levels opens the reviewed Lv/凸 import (nothing is saved in the preview).
  if (bridge.kind === "mock" && params.get("open") === "levels") openLevelImport({ owned: ownedCards(store.get()), onApply: () => toast("预览：未保存") });
  // Preview-only: ?open=shop-items opens the shop 其他商品 picker.
  if (bridge.kind === "mock" && params.get("open") === "shop-items") {
    const entry = store.get().profile.tasks.find((t) => t.name === "Shop");
    const task = store.get().schema.tasks.find((t) => t.name === "Shop");
    const row = entry && task && optionRows(store.get(), task, entry).find((r) => r.group);
    if (row) {
      const values = Object.fromEntries(row.keys.map((k) => [k, entry.option[k]]));
      openItemPicker(store.get(), { group: row.group, keys: row.keys, values, onApply: (changes) => actions.setOptions("Shop", changes) });
    }
  }
  // Preview-only: ?live=wing plays a recorded W.I.N.G. run (the mock bridge emits run_live); the panel treats it as its own run.
  if (bridge.kind === "mock" && params.get("live") === "wing") {
    store.set({ run: { active: true, id: "preview-wing", mode: "read_only", tasks: ["Wing"], startedAt: new Date().toISOString() }, activeTab: "live", liveTakeover: "pending" });
  }
  if (bridge.kind !== "mock") refreshPassive();
  bridge.request("status.subscribe", { enabled: true }).catch(() => {});
  bridge.request("log.subscribe", { enabled: true }).catch(() => {});
  document.documentElement.dataset.ready = "true";
  const previewVisitor = bridge.kind === "mock" && params.get("open") === "chibi";
  const visitor = createChibiVisitor(parts.body, {
    onMotionChange: value => actions.setPref("reduce_chibi_motion", value),
    onHide: async () => {
      if (await actions.setPref("hide_chibi_visitor", true)) toast(`已关闭「${VISITOR_LABEL}」，可在「设置 → 外观」重新开启`);
    },
  });
  let visitorDisabled;
  function syncVisitor() {
    const prefs = store.get().prefs;
    visitor.setReducedMotion(prefs.reduce_chibi_motion === true);
    const disabled = prefs.hide_chibi_visitor === true;
    if (disabled === visitorDisabled) return;
    visitorDisabled = disabled;
    if (disabled) visitor.stop();
    else visitor.start({ preview: previewVisitor });
  }
  syncVisitor();
  store.addEventListener("change", syncVisitor);
  window.addEventListener("pagehide", () => { visitorDisabled = undefined; visitor.stop(); });
  window.addEventListener("pageshow", event => { if (event.persisted) syncVisitor(); });
}

main().catch((error) => {
  console.error(error);
  const app = document.getElementById("app");
  clear(app);
  app.append(h("div", { class: "fatal" }, h("h1", { text: "界面无法启动" }), h("p", { text: String(error && error.message || error) })));
});

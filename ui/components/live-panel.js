// 「运行中」 live view (「这个tab在后续wing任务的时候可以占据ui实时显示 其他任务也一样
// 实时显示队列操作之类的 当然保持直观美观面向用户而非开发者」). Data: status.update.run_live = the run's live status file
// (agent/core/live_status.py), forwarded by the host; without it (older host) the queue is derived from run.update.
// Compact on purpose (one page, no scrolling at the default 400 px panel; terse game-like labels, numbers
// over sentences). Generic part: state + elapsed + 停止, the current step, progress / clicks, the queue with short results.
// W.I.N.G. part: season/week, stat bars, SP / fans / stamina, this week's top options (score bars) and the last 4 events.
// Display only: nothing here decides, and the only control is the run's own cooperative stop.
import { h, clear } from "./dom.js";
import { taskIcon } from "./task-icons.js";
import { taskName } from "./display.js";
import { logViewSwitch, logViewBody, logList } from "./log-panel.js";

const STYLE_HREF = "styles/live.css";
const RUNNING = new Set(["starting", "running", "stopping"]);
const TASK_STATES = new Set(["pending", "running", "done", "failed", "skipped", "stopped"]);
const EVENT_KINDS = new Set(["week", "skill", "forget", "sp", "audition", "choice", "live", "fail", "stop", "info"]);
export const STALE_S = 120;          // a running snapshot older than this is a crashed / lost run: no takeover
export const KEEP_FINISHED_S = 1800; // a finished snapshot stays viewable for 30 min
const ATTRS = ["Vo", "Da", "Vi", "Me"];
const STATE_TEXT = { starting: "准备", running: "运行中", stopping: "停止中", finished: "已结束" };
const TASK_TONE = { running: "primary", done: "success", failed: "danger", stopped: "warning", skipped: "muted", pending: "muted" };
const EVENT_TEXT = { week: "日程", skill: "学会", forget: "忘记", sp: "SP", audition: "试镜", choice: "事件", live: "Live", fail: "失败", stop: "停止", info: "·" };
const EVENTS_SHOWN = 4;
const OPTIONS_SHOWN = 3;

function str(v, max = 80) { return typeof v === "string" ? v.slice(0, max) : null; }
function int(v) { return Number.isInteger(v) ? v : null; }
function num(v) { return typeof v === "number" && Number.isFinite(v) ? v : null; }

function cleanDecision(d) {
  if (!d || typeof d !== "object") return null;
  const options = (Array.isArray(d.options) ? d.options : []).slice(0, 4).filter((o) => o && typeof o.label === "string")
    .map((o) => ({ label: str(o.label, 40), score: num(o.score), why: str(o.why, 80), chosen: o.chosen === true, risk: num(o.risk) }));
  const excluded = (Array.isArray(d.excluded) ? d.excluded : []).slice(0, 8).filter((e) => e && typeof e.label === "string")
    .map((e) => ({ label: str(e.label, 40), reason: str(e.reason, 60) || "" }));
  return { season: int(d.season), weeksLeft: int(d.weeks_left), chosen: str(d.chosen, 40), chosenScore: num(d.chosen_score),
    why: str(d.why, 80), rule: str(d.rule, 40), options, excluded, more: int(d.more) || 0 };
}

function cleanWing(w) {
  if (!w || typeof w !== "object") return null;
  const stats = {};
  for (const k of ATTRS) if (Number.isInteger(w.stats?.[k])) stats[k] = w.stats[k];
  return {
    season: int(w.season), weeksLeft: int(w.weeks_left), stats: Object.keys(stats).length ? stats : null,
    sp: int(w.sp), fans: int(w.fans), fansLeft: int(w.fans_left), stamina: int(w.stamina),
    decision: cleanDecision(w.decision),
    events: (Array.isArray(w.events) ? w.events : []).slice(-12).filter((e) => e && typeof e.text === "string")
      .map((e) => ({ kind: EVENT_KINDS.has(e.kind) ? e.kind : "info", text: str(e.text), at: str(e.at, 40) })),
    live: w.live && typeof w.live.text === "string" ? { text: str(w.live.text, 60) } : null,
  };
}

/** Validated copy of status.update.run_live (agent/core/live_status.py snapshot), or null. */
export function cleanLive(raw) {
  if (!raw || typeof raw !== "object" || raw.version !== 1 || typeof raw.run_id !== "string") return null;
  const state = STATE_TEXT[raw.state] ? raw.state : "running";
  const tasks = (Array.isArray(raw.tasks) ? raw.tasks : []).slice(0, 24).filter((t) => t && typeof t.name === "string")
    .map((t) => ({ name: str(t.name, 40), label: str(t.label, 40) || t.name, state: TASK_STATES.has(t.state) ? t.state : "pending", text: str(t.text, 20) || "" }));
  return {
    runId: str(raw.run_id, 64), state, outcome: str(raw.outcome, 20), outcomeText: str(raw.outcome_text, 40),
    elapsed: int(raw.elapsed_s), age: num(raw.age_s), tasks, current: str(raw.current, 40), step: str(raw.step),
    progress: { done: int(raw.progress?.done) ?? tasks.filter((t) => !["pending", "running"].includes(t.state)).length, total: int(raw.progress?.total) ?? tasks.length },
    clicks: { used: int(raw.clicks?.used) ?? 0, cap: int(raw.clicks?.cap) },
    lastFrameAt: str(raw.last_frame_at, 40), updatedAt: str(raw.updated_at, 40),
    wing: cleanWing(raw.wing),
  };
}

/** The live model shown by the view: the host's run_live, else the queue derived from run.update. */
export function liveModel(state) {
  const live = cleanLive(state.host?.run_live);
  const run = state.run || {};
  if (live?.state === "finished" && !run.active) return null;
  // A W.I.N.G. segment can finish while the GUI run is waiting to start its next segment.
  // Keep the active run's derived queue visible until the whole run ends.
  if (live && live.state !== "finished") {
    const stale = live.age !== null && (RUNNING.has(live.state) ? live.age > STALE_S : live.age > KEEP_FINISHED_S);
    if (!stale || (run.active && run.id === live.runId)) return { ...live, stale: stale && RUNNING.has(live.state) };
    if (!run.active) return null;
  }
  if (!run.active) return null;
  const statuses = state.taskStatus || {};
  const map = { pending: "pending", running: "running", succeeded: "done", failed: "failed", skipped: "skipped", uncertain: "stopped", guard_refused: "failed", takeover: "stopped" };
  const tasks = (run.tasks || []).map((name) => {
    const s = statuses[name]?.status || "pending";
    return { name, label: taskName(state, name), state: map[s] || "pending", text: statuses[name]?.reason || ({ pending: "待办", running: "进行", succeeded: "完成" }[s] || "") };
  });
  const current = tasks.find((t) => t.state === "running") || null;
  return { runId: run.id, state: run.stopping ? "stopping" : "running", outcome: null, outcomeText: null, elapsed: null, age: null, tasks,
    current: current?.name || null, step: current ? current.label : "准备", progress: { done: tasks.filter((t) => !["pending", "running"].includes(t.state)).length, total: tasks.length },
    clicks: { used: null, cap: null }, lastFrameAt: null, updatedAt: null, wing: null, derived: true, stale: false };
}

export function liveRunning(model) {
  return Boolean(model && RUNNING.has(model.state) && !model.stale);
}

export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

function mmss(seconds) {
  if (!Number.isInteger(seconds) || seconds < 0) return null;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function clockText(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function ago(iso, ref) {
  const d = iso ? new Date(iso) : null;
  const r = ref ? new Date(ref) : new Date();
  if (!d || Number.isNaN(d.getTime()) || Number.isNaN(r.getTime())) return null;
  const s = Math.max(0, Math.round((r - d) / 1000));
  return s < 5 ? "刚刚" : s < 60 ? `${s}秒前` : `${Math.round(s / 60)}分前`;
}

function iconName(name) { return name === "Wing" ? "AutoProduce" : name; }

/** Small banner for the other tabs while a run is live: 「运行中 · <step>  查看 ›」. */
export function liveBanner(state, actions) {
  const model = liveModel(state);
  if (!liveRunning(model) || state.activeTab === "live") return null;
  ensureStyles();
  return h("button", { class: "live-banner", type: "button", dataset: { focusKey: "live-banner" }, title: "运行中",
    onclick: () => actions.setTab("live") },
  h("span", { class: "live-dot", "aria-hidden": "true" }),
  h("span", { class: "live-banner-text" }, h("b", { text: model.state === "stopping" ? "停止中" : "运行中" }), model.step ? ` · ${model.step}` : ""),
  h("span", { class: "live-banner-go", text: "›" }));
}

/**
 * The step line (2026-11-21: long waits were cut off). 「Collabo Fes · Vocal 满员，等重开 · 20:47 再看 · 已打 5/6 局」 →
 * a small task label, the status in bold, the remaining facts as chips; a step without 「 · 」 stays one bold line.
 */
function stepView(text) {
  const parts = String(text).split(" · ").map((s) => s.trim()).filter(Boolean);
  if (parts.length < 2) return h("div", { class: "live-step", "aria-live": "polite" }, h("span", { class: "live-step-main", text }));
  const [task, main, ...facts] = parts;
  return h("div", { class: "live-step", "aria-live": "polite", title: text },
    h("span", { class: "live-step-task", text: task }),
    h("span", { class: "live-step-main", text: main || "" }),
    facts.length ? h("span", { class: "live-step-facts" }, facts.map((f) => h("span", { class: "live-step-fact", text: f }))) : null);
}

function heroCard(model, state, actions) {
  const running = RUNNING.has(model.state);
  const stopping = model.state === "stopping" || state.run?.stopping;
  const elapsed = mmss(model.elapsed);
  const facts = factsText(model);
  const pill = h("span", { class: `live-state is-${model.stale ? "stale" : model.state}` },
    running && !model.stale ? h("span", { class: "live-dot", "aria-hidden": "true" }) : null,
    model.stale ? "无更新" : STATE_TEXT[model.state]);
  const stop = running && state.run?.active
    ? h("button", { class: "btn live-stop", type: "button", disabled: Boolean(stopping), dataset: { focusKey: "live-stop" },
      title: "当前步骤结束后停下", onclick: () => actions.stopRun("cooperative") }, stopping ? "停止中" : "停止")
    : null;
  const done = model.state === "finished" && model.outcomeText !== model.step
    ? h("span", { class: `live-outcome tone-text-${model.outcome === "succeeded" ? "success" : "warning"}` }, model.outcomeText || "已结束")
    : null;
  return h("section", { class: "live-hero", "aria-label": "当前运行" },
    h("div", { class: "live-hero-top" },
      pill,
      h("span", { class: "live-hero-title", text: model.wing ? "W.I.N.G." : "日常" }),
      elapsed ? h("span", { class: "live-elapsed num", title: "已运行", text: elapsed }) : null, stop),
    stepView(model.stale ? "状态未更新，见游戏画面" : model.step || "准备"),
    facts || done ? h("div", { class: "live-progress" }, done, h("span", { class: "live-facts muted small", text: facts })) : null);
}

const TASK_MARK = { done: "✓", failed: "✗", stopped: "■", skipped: "–", running: "●", pending: "" };

/** Queue as chips: icon + name + state mark (title carries the plain result). Only for runs with more than one task. */
function queueCard(model, state) {
  return h("ol", { class: "live-queue", "aria-label": "队列" }, model.tasks.map((t) => h("li", { class: `live-chip-task is-${t.state}`, title: t.text || STATE_TEXT[t.state] || null },
    taskIcon(state, iconName(t.name), { size: 16 }),
    h("span", { class: "live-task-name", text: t.label }),
    TASK_MARK[t.state] ? h("span", { class: "live-task-mark", "aria-hidden": "true", text: TASK_MARK[t.state] }) : null)));
}

function statBars(stats) {
  const max = Math.max(1, ...Object.values(stats)) * 1.1;
  return h("div", { class: "live-stats", role: "list", "aria-label": "属性" }, ATTRS.filter((k) => k in stats).map((k) => {
    const fill = h("span", { class: `live-bar-fill attr-${k.toLowerCase()}` });
    if (fill.style) fill.style.width = `${Math.round((stats[k] / max) * 100)}%`;
    return h("div", { class: "live-stat", role: "listitem", title: `${k} ${stats[k]}` },
      h("span", { class: "live-stat-key", text: k }), h("span", { class: "live-bar", "aria-hidden": "true" }, fill),
      h("span", { class: "live-stat-val num", text: String(stats[k]) }));
  }));
}

function weekText(season, weeksLeft) {
  return season !== null && weeksLeft !== null ? `第${season}季 · 剩${weeksLeft}周` : season !== null ? `第${season}季` : null;
}

function wingHeader(w) {
  const week = weekText(w.season, w.weeksLeft);
  const chip = (label, value, title = null) => h("div", { class: "live-chip", title }, h("span", { class: "live-chip-label", text: label }), h("b", { class: "num", text: value }));
  return h("section", { class: "live-card live-wing" },
    week ? h("div", { class: "live-week", text: week }) : null,
    w.stats ? statBars(w.stats) : h("p", { class: "muted small", text: "—" }),
    h("div", { class: "live-chips" },
      w.sp !== null ? chip("SP", String(w.sp)) : null,
      w.stamina !== null ? chip("体力", `${w.stamina}%`) : null,
      w.fans !== null ? chip("粉丝", w.fans.toLocaleString("ja-JP")) : null,
      w.fansLeft !== null ? chip("距目标", w.fansLeft.toLocaleString("ja-JP"), "粉丝目标差额") : null),
    w.live ? h("div", { class: "live-now-live" }, h("span", { class: "live-dot", "aria-hidden": "true" }), w.live.text) : null);
}

function scoreBar(score, max) {
  const fill = h("span", { class: "live-bar-fill" });
  if (fill.style) fill.style.width = `${score !== null && max > 0 ? Math.max(3, Math.round((Math.max(0, score) / max) * 100)) : 0}%`;
  return h("span", { class: "live-bar live-score-bar", "aria-hidden": "true" }, fill);
}

function decisionCard(d) {
  const shown = d.options.slice(0, OPTIONS_SHOWN);
  const max = Math.max(0, ...shown.map((o) => o.score ?? 0));
  const rows = shown.map((o) => h("li", { class: `live-option${o.chosen ? " is-chosen" : ""}`, title: o.why || null },
    h("span", { class: "live-option-mark", "aria-hidden": "true", text: o.chosen ? "✓" : "" }),
    h("span", { class: "live-option-label", text: o.label }),
    h("span", { class: "live-risk num", title: o.risk ? "失败风险" : null, text: o.risk ? `险${o.risk}%` : "" }),
    scoreBar(o.score, max),
    h("span", { class: "live-option-score num", title: "得分", text: o.score === null ? "—" : o.score.toFixed(1) })));
  return h("section", { class: "live-card live-decision", "aria-label": "本周行动" },
    h("div", { class: "live-h" }, "本周行动"), h("ol", { class: "live-options" }, rows));
}

function timeline(events) {
  if (!events.length) return null;
  return h("section", { class: "live-card live-timeline-card", "aria-label": "历史" },
    h("div", { class: "live-h" }, "历史"),
    h("ol", { class: "live-timeline" }, events.slice().reverse().slice(0, EVENTS_SHOWN).map((e) => h("li", { class: `live-event is-${e.kind}` },
      h("span", { class: "live-event-kind", text: EVENT_TEXT[e.kind] || "·" }),
      h("span", { class: "live-event-text", text: e.text }),
      h("span", { class: "live-event-at num muted", text: clockText(e.at) })))));
}

/** Everything that changes the picture except the clock fields (elapsed / updated / frame age). */
export function liveKey(state) {
  const raw = state.host?.run_live;
  const { age_s: _a, elapsed_s: _e, updated_at: _u, last_frame_at: _f, ...rest } = raw && typeof raw === "object" ? raw : {};
  return JSON.stringify([rest, state.run?.active, state.run?.stopping, state.taskStatus]);
}

/**
 * Status tick on the 「运行中」 tab: when only the clock fields moved, update the elapsed time and the facts line in place
 * (no icon <img> is recreated, so nothing flickers) and return true; otherwise return false and the caller re-renders.
 */
export function updateLiveTick(root, state) {
  const panel = root?.querySelector?.("sc-live-panel");
  if (panel && panel._view && panel._view !== "run") return true;   // 日志 / 运行记录: nothing ticks here
  if (!panel || panel._key !== liveKey(state)) return false;
  const model = liveModel(state);
  if (!model) return false;
  const elapsed = panel.querySelector(".live-elapsed");
  if (elapsed && mmss(model.elapsed)) elapsed.textContent = mmss(model.elapsed);
  const facts = panel.querySelector(".live-facts");
  if (facts) facts.textContent = factsText(model);
  return true;
}

function factsText(model) {
  return [
    model.clicks.used !== null ? `点击 ${model.clicks.used}` : null,
    model.lastFrameAt ? `帧 ${ago(model.lastFrameAt, model.updatedAt) || "—"}` : null,
  ].filter(Boolean).join(" · ");
}

class LivePanel extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    ensureStyles();
    clear(this);
    this._key = liveKey(state);
    this.className = "panel live-panel log-page";
    const model = liveModel(state);
    // 2026-11-19: 日志 / 运行记录 are views of this panel (no separate tab); 运行 exists only while a run is shown.
    const view = model && (state.logView || "run") === "run" ? "run" : state.logView === "history" ? "history" : "logs";
    this._view = view;
    this.append(logViewSwitch(state, actions, view, Boolean(model)));
    if (view !== "run") {
      this.append(...logViewBody(state, actions, view));
      return;
    }
    const w = model.wing;
    const queue = model.tasks.length <= 1 ? null : h("section", { class: "live-card live-queue-card" }, queueCard(model, state));
    this.append(h("div", { class: "panel-scroll live-scroll", dataset: { scrollKey: "live" } },
      heroCard(model, state, actions),
      w ? wingHeader(w) : null,
      w?.decision ? decisionCard(w.decision) : null,
      w ? timeline(w.events) : null,
      queue),
    // 2026-11-20 (user: the lower half stayed empty): the log, newest first, fills the rest of the run view.
    h("section", { class: "live-log", "aria-label": "日志" },
      h("button", { class: "live-log-head", type: "button", title: "日志全部与筛选", onclick: () => actions.setLogView?.("logs") },
        h("span", { class: "live-h", text: "日志" }), h("span", { class: "muted small num", text: String(state.logs.length) }),
        h("span", { class: "live-log-more muted small", text: "筛选 ›" })),
      h("div", { class: "log-body" }, logList(state))));
  }
}
customElements.define("sc-live-panel", LivePanel);

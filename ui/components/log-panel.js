// 日志 view (the drawer under 任务 left a few rows in a small window, and an extra tab crowded the rail):
// full height inside the hidden 「运行中」 panel (live-panel.js), the filter row and the windowed log list filling it;
// 运行记录 is the panel's third view. The 任务 tab keeps one strip (count + latest line) that opens it. The wide 工作台
// layout keeps its always-open log column.
import { h, icon, jaText, clear, formatTime } from "./dom.js";
import { statusPill, emptyState } from "./common.js";
import { choiceChips } from "./choice.js";
import { MODE_LABEL } from "./header.js";
import { taskName, devMode } from "./display.js";
import { runOutcome } from "./daily-panel.js";
import { outcomeText } from "./outcomes.js";

const ROW_H = 44;
const OVERSCAN = 6;
const LEVELS = [
  { key: "", label: "全部" },
  { key: "info", label: "信息" },
  { key: "warn", label: "警告" },
  { key: "error", label: "错误" },
  { key: "debug", label: "调试" },
];
const LEVEL_LABEL = { info: "信息", warn: "警告", error: "错误", debug: "调试" };

export function filterLogs(logs, filter) {
  const text = (filter.text || "").trim().toLowerCase();
  return logs.filter((log) => (!filter.level || log.level === filter.level)
    && (!filter.task || log.task === filter.task)
    && (!text || String(log.text).toLowerCase().includes(text)));
}

function filterRow(state, actions) {
  const filter = state.logFilter;
  const taskNames = [...new Set(state.logs.map((l) => l.task).filter(Boolean))];
  // 2026-10-24 (user: dropdowns are slow): level and task filters are pills; tasks appear only when the log has several.
  const level = choiceChips({ label: "按级别筛选", options: LEVELS.map((l) => [l.key, l.label]), value: filter.level || "", focusKey: "log-level",
    onPick: (v) => actions.setLogFilter({ level: v }) });
  const task = taskNames.length > 1 ? choiceChips({ label: "按任务筛选", focusKey: "log-task", value: filter.task || "", cls: "log-task-chips",
    options: [["", "全部任务"], ...taskNames.map((n) => [n, taskName(state, n)])], onPick: (v) => actions.setLogFilter({ task: v }) }) : null;
  const search = h("input", { class: "input input-compact", type: "search", placeholder: "搜索", "aria-label": "搜索日志", value: filter.text || "", dataset: { focusKey: "log-search" },
    oninput: (e) => actions.setLogFilter({ text: e.target.value }) });
  return h("div", { class: "log-toolbar" },
    h("div", { class: "log-toolbar-row" }, level, h("div", { class: "search-wrap grow" }, icon("search", { size: 16 }), search)),
    task);
}

export function logList(state) {
  const rows = filterLogs(state.logs, state.logFilter).slice().reverse();
  const viewport = h("div", { class: "log-viewport", tabindex: "0", "aria-label": `日志，共 ${rows.length} 条，最新在上`, role: "list", dataset: { scrollKey: "task-log" } });
  if (!rows.length) {
    viewport.append(emptyState({ title: state.logs.length ? "没有符合筛选的日志" : "还没有日志" }));
    return viewport;
  }
  const spacer = h("div", { class: "log-spacer" });
  spacer.style.height = `${rows.length * ROW_H}px`;
  viewport.append(spacer);
  const paint = () => {
    const start = Math.max(0, Math.floor(viewport.scrollTop / ROW_H) - OVERSCAN);
    const end = Math.min(rows.length, Math.ceil((viewport.scrollTop + (viewport.clientHeight || 240)) / ROW_H) + OVERSCAN);
    clear(spacer);
    for (let i = start; i < end; i += 1) {
      const log = rows[i];
      const row = h("div", { class: `log-row level-${log.level}`, role: "listitem", title: log.text },
        h("span", { class: "log-level" }, h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text: LEVEL_LABEL[log.level] || log.level })),
        h("div", { class: "log-main" },
          h("div", { class: "log-text" }, jaText(log.text)),
          h("div", { class: "log-meta" }, h("span", { class: "num", text: formatTime(log.ts) }), h("span", { class: "log-meta-sep", "aria-hidden": "true", text: "·" }),
            h("span", {}, jaText(log.task ? taskName(state, log.task) : "运行")))));
      row.style.top = `${i * ROW_H}px`;
      spacer.append(row);
    }
  };
  viewport.addEventListener("scroll", () => requestAnimationFrame(paint), { passive: true });
  requestAnimationFrame(paint);
  return viewport;
}

/** Where the host keeps full run records (UiEngineSupervisor runs root); shown so a stopped run can be looked up. */
export const RUNS_FOLDER = "dev_assets/private/ui/runs/";

// One run's tasks with their result text (expandable), so 「哪一步停了、为什么」 is readable after the fact.
function runTasks(state, run) {
  const tasks = (run.tasks || []).map((t) => (typeof t === "string" ? { name: t } : t || {}));
  if (!tasks.some((t) => t.status)) return h("span", { class: "muted small", text: ` ${tasks.length} 个任务` });
  return h("details", { class: "history-tasks" },
    h("summary", { class: "muted small", text: `${tasks.length} 个任务` }),
    h("ul", { class: "history-task-list small" }, tasks.map((t) => h("li", {},
      h("span", { text: `${taskName(state, t.name)}：` }), statusPill(t.status || "pending"),
      t.reason ? h("span", { class: "muted" }, jaText(` ${outcomeText(t.reason, t.name)}`)) : null))));
}

function historyTable(state) {
  if (!state.runs.length) return emptyState({ title: "还没有运行记录" });
  const dev = devMode(state);
  return h("div", {},
    h("table", { class: "table table-compact" },
      h("thead", {}, h("tr", {}, h("th", { text: "开始时间" }), dev ? h("th", { text: "模式" }) : null, h("th", { text: "结果" }))),
      h("tbody", {}, state.runs.slice(0, 20).map((run) => h("tr", {},
        h("td", { class: "num", text: formatTime(run.started_at) }), dev ? h("td", { text: MODE_LABEL[run.mode] || run.mode }) : null,
        h("td", {}, statusPill(runOutcome(run)), runTasks(state, run)))))),
    dev ? h("p", { class: "muted small history-foot", text: `完整记录在程序目录 ${RUNS_FOLDER}。` }) : null);
}

/**
 * The run column of the wide 工作台 layout: the same log list and filters, always open under 开始 /
 * 停止 on every tab; 运行记录 stays a collapsible block below it.
 */
export function logAside(state, actions) {
  const historyOpen = state.historyOpen === true;
  const hist = h("button", {
    class: "drawer-head", type: "button", "aria-expanded": String(historyOpen), dataset: { focusKey: "history-toggle" },
    onclick: () => actions.toggleDrawer("historyOpen"),
  }, icon("chevron", { size: 16 }), h("span", { class: "drawer-title", text: "运行记录" }),
  h("span", { class: "drawer-count num muted small", text: String(state.runs.length) }),
  state.runs[0] ? h("span", { class: "drawer-last" }, statusPill(runOutcome(state.runs[0]))) : null);
  return h("section", { class: "log-aside", "aria-label": "日志与运行记录" },
    h("div", { class: "log-aside-head" }, h("span", { class: "drawer-title", text: "日志" }),
      h("span", { class: "drawer-count num muted small", text: String(state.logs.length) })),
    filterRow(state, actions),
    h("div", { class: "log-body" }, logList(state)),
    hist,
    historyOpen ? h("div", { class: "drawer-body drawer-history", dataset: { scrollKey: "aside-history" } }, historyTable(state)) : null);
}

/** The 任务 tab's one-line log strip: count + latest line; opens the 日志 page. */
export function logStrip(state, actions) {
  const last = state.logs[state.logs.length - 1];
  const error = state.logs.some((l) => l.level === "error");
  return h("button", { class: "drawer-head log-strip", type: "button", title: "打开日志", dataset: { focusKey: "log-strip" },
    onclick: () => actions.openLogs() },
  h("span", { class: "drawer-title", text: "日志" }),
  h("span", { class: `drawer-count num small ${error ? "tone-text-danger" : "muted"}`, text: String(state.logs.length) }),
  h("span", { class: "drawer-last muted small" }, last ? jaText(`${formatTime(last.ts)} ${last.text}`) : "暂无日志"),
  h("span", { class: "card-button-chevron", "aria-hidden": "true" }, icon("chevron", { size: 16 })));
}

/** 「运行中」 panel switch: 运行 (only while a run is shown) | 日志 N | 运行记录 N. */
export function logViewSwitch(state, actions, view, withRun) {
  const options = [...(withRun ? [["run", "运行"]] : []), ["logs", `日志 ${state.logs.length}`], ["history", `运行记录 ${state.runs.length}`]];
  return h("div", { class: "log-page-head" }, choiceChips({ label: "运行、日志或运行记录", focusKey: "log-view", value: view, options,
    onPick: (v) => actions.setLogView?.(v) }));
}

/** The 日志 / 运行记录 body (full height). */
export function logViewBody(state, actions, view) {
  return view === "history"
    ? [h("div", { class: "log-page-history", dataset: { scrollKey: "page-history" } }, historyTable(state))]
    : [filterRow(state, actions), h("div", { class: "log-body" }, logList(state))];
}

/** Legacy drawer (no longer placed; kept for the wide layout's history toggle helpers). */
export function logDrawer(state, actions) {
  const last = state.logs[state.logs.length - 1];
  const open = state.logOpen === true;
  const historyOpen = state.historyOpen === true;
  const head = h("button", {
    class: "drawer-head", type: "button", "aria-expanded": String(open), dataset: { focusKey: "log-toggle" },
    onclick: () => actions.toggleDrawer("logOpen"),
  }, icon("chevron", { size: 16 }), h("span", { class: "drawer-title", text: "日志" }),
  h("span", { class: "drawer-count num muted small", text: String(state.logs.length) }),
  h("span", { class: "drawer-last muted small" }, last ? jaText(`${formatTime(last.ts)} ${last.text}`) : "暂无日志"));
  const hist = h("button", {
    class: "drawer-head", type: "button", "aria-expanded": String(historyOpen), dataset: { focusKey: "history-toggle" },
    onclick: () => actions.toggleDrawer("historyOpen"),
  }, icon("chevron", { size: 16 }), h("span", { class: "drawer-title", text: "运行记录" }),
  h("span", { class: "drawer-count num muted small", text: String(state.runs.length) }),
  state.runs[0] ? h("span", { class: "drawer-last" }, statusPill(runOutcome(state.runs[0]))) : null);
  return h("section", { class: `log-drawer${open ? " is-open" : ""}`, "aria-label": "日志与运行记录" },
    head,
    open ? h("div", { class: "drawer-body" }, filterRow(state, actions), h("div", { class: "log-body" }, logList(state))) : null,
    hist,
    historyOpen ? h("div", { class: "drawer-body drawer-history", dataset: { scrollKey: "task-history" } }, historyTable(state)) : null);
}

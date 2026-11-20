// 主页 data helpers (the page is home-panel.js, formerly the 日常 tab): today's checklist derived from run
// summaries of the current game day (JST 05:00 rollover), the next reset, the items that need the user and run
// outcomes. Pure functions only (no DOM), so they are node-tested.
import { gameDay } from "./store.js";
import { taskName, taskShown, startBlockers } from "./display.js";
import { outcomeText } from "./outcomes.js";
import { gradeDailyPlan } from "./fes-plan.js";
import { ackedTasks, openLedger } from "./acks.js";

// Host mode has no fixture checklist: the daily scope (Fes = Match or Grade). Rollover JST 05:00 (verified).
export const DEFAULT_DAILY = {
  rollover: { timezone: "Asia/Tokyo", hour: 5, verified: true },
  items: [
    { key: "startup", label: "启动", tasks: ["Startup"] },
    { key: "shop", label: "商店", tasks: ["Shop"] },
    { key: "training", label: "训练", tasks: ["Training"] },
    { key: "fes", label: "Fes（Match 或 Grade）", tasks: ["MatchSkip", "GradeFes"], any: true },
    { key: "missions", label: "每日任务", tasks: ["MissionStatus"] },
    { key: "business", label: "营业", tasks: ["Business"] },
  ],
};

const PRIORITY = ["uncertain", "takeover", "failed", "guard_refused", "succeeded", "skipped"];
const WORST = ["uncertain", "takeover", "failed", "guard_refused", "skipped", "succeeded"];
const ATTENTION = { uncertain: "uncertain", takeover: "accent", failed: "danger", guard_refused: "warning" };
// Same texts as common.js STATUS_HINT (kept here because this module stays DOM-free for node tests).
const HINT = { uncertain: "结果不确定，请在游戏中核对", guard_refused: "未执行：画面与任务不符，未点击",
  takeover: "未知画面（弹窗等），已停止：处理后回到首页再开始", failed: "失败：详情见「日志」" };
/** The current host was explicitly opened with game input disabled. */
export const INPUT_OFF_TEXT = "游戏操作未开启：重新打开 MaaShinyColors.exe";
/** Neither the packaged recognition crops nor local ones are installed: every page check would fail (UX review #4). */
export const RECOGNITION_MISSING_TEXT = "识别资源缺失：重新解压完整安装包";
export const OUTCOME_LABEL = { succeeded: "成功", uncertain: "不确定", takeover: "接管", failed: "失败", guard_refused: "未执行", skipped: "跳过" };

/** Latest status per task among runs that started on the given game day. */
export function todayStatuses(runs, day) {
  const result = {};
  const sorted = [...runs].sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)));
  for (const run of sorted) {
    if (gameDay(new Date(run.started_at)) !== day) continue;
    for (const t of run.tasks) result[t.name] = t.status;
  }
  return result;
}

/** Of ``names``, the tasks that already succeeded on that game day (warn 「已完成，是否继续」, never block). */
export function doneToday(runs, day, names) {
  const today = todayStatuses(runs, day);
  return names.filter((name) => today[name] === "succeeded");
}

/**
 * The start warning (已完成 / 结果不确定 only warn, never block; each task re-reads the game screen):
 * ``done`` succeeded today, ``uncertain`` uncertain today and not 已核对, ``ledger`` open spend-ledger entries.
 * ``lines`` is the dialog text, empty when there is nothing to say.
 */
export function startNotice(state, day, names, { done = true } = {}) {
  const today = todayStatuses(state.runs || [], day);
  const acked = ackedTasks(state, day);
  const label = (list) => list.map((name) => taskName(state, name)).join("、");
  const notice = {
    done: done ? names.filter((name) => today[name] === "succeeded") : [],
    uncertain: names.filter((name) => today[name] === "uncertain" && !acked.includes(name)),
    ledger: openLedger(state).length,
  };
  notice.lines = [notice.done.length && `已完成：${label(notice.done)}`,
    notice.uncertain.length && `结果不确定：${label(notice.uncertain)}`,
    notice.ledger && `${notice.ledger} 条消耗记录待核对`].filter(Boolean);
  notice.title = notice.uncertain.length || notice.ledger ? "继续运行？" : "今天已完成，继续运行？";
  return notice;
}

export function itemStatus(item, statuses) {
  const found = item.tasks.map((n) => statuses[n]).filter(Boolean);
  if (!found.length) return null;
  if (item.any && found.includes("succeeded")) return "succeeded";
  return PRIORITY.find((s) => found.includes(s)) || found[0];
}

/**
 * Whether any task of a checklist item is ticked in the current 方案. Lets 主页 say 「未勾选」 instead of 「未运行」 for
 * e.g. 营业 when it was never added (roleplay test 2026-10-07: users read 「未运行」 as a pending run).
 */
export function itemPlanned(item, profile) {
  const removed = new Set(profile?.removed_tasks || []);
  return item.tasks.some((n) => !removed.has(n) && (profile?.tasks || []).some((t) => t.name === n && t.enabled));
}

/** The checklist definition in use: the host/fixture list, else DEFAULT_DAILY (weekly_produce kept). */
export function dailyDefinition(state) {
  return state.daily?.items?.length ? state.daily : { ...DEFAULT_DAILY, weekly_produce: state.daily?.weekly_produce };
}

/** Today's selected tasks, separate from the game's mission achievement counters. */
export function dailyPlanProgress(state, now = new Date()) {
  const day = gameDay(now);
  const latest = {};
  const actual = (mode, name) => name === "AutoProduce" ? mode === "supervised_wing" : mode === "supervised_daily";
  const today = (at) => Number.isFinite(Date.parse(at || "")) && Date.parse(at) <= now.getTime()
    && gameDay(new Date(at)) === day;
  for (const run of [...(state.runs || [])].sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at))) {
    if (!today(run.started_at)) continue;
    for (const task of run.tasks || []) if (actual(run.mode, task.name)) latest[task.name] = task;
  }
  if (today(state.run?.startedAt)) {
    for (const name of state.run.tasks || []) {
      if (actual(state.run.mode, name) && state.taskStatus?.[name]) latest[name] = state.taskStatus[name];
    }
  }
  const items = (state.profile?.tasks || []).filter((entry) => entry.enabled && taskShown(state, entry.name)).map((entry) => {
    const result = latest[entry.name] || {};
    let status = result.status || null;
    let text = null;
    if (entry.name === "MissionStatus" && status === "succeeded") text = "已读取";
    if (entry.name === "PresentClaim" && result.reason === "received_partial") {
      status = status === "succeeded" ? "pending" : status;
      text = "仍有未领取礼物";
    }
    if (entry.name === "Shop" && ["skipped_rules", "skipped_inconsistent"].includes((result.reason || "").split(":")[0])) {
      status = status === "succeeded" ? "pending" : status;
      text = "未购买";
    }
    if (entry.name === "GradeFes") {
      const progress = gradeDailyPlan(state, entry, now);
      text = `${progress.known ? progress.completed : "—"} / ${progress.target} 局`;
      if (status === "succeeded" && (!progress.known || progress.completed < progress.target)) status = "pending";
    }
    return { key: entry.name, label: taskName(state, entry.name), tasks: [entry.name], status, text,
      done: status === "succeeded" };
  });
  return { items, done: items.filter((item) => item.done).length, total: items.length,
    statuses: Object.fromEntries(Object.entries(latest).map(([name, result]) => [name, result.status])) };
}

/** Worst task status of one run record (run history and the 主页 最近运行). */
export function runOutcome(run) {
  const statuses = new Set((run?.tasks || []).map((t) => t.status));
  if (["succeeded", "failed", "uncertain"].includes(run?.outcome)) return run.outcome;
  if (run?.outcome === "refused") return "guard_refused";
  if (run?.outcome === "stopped") return statuses.has("takeover") ? "takeover" : "stopped";
  return WORST.find((s) => statuses.has(s)) || "pending";
}

/** ["成功 2", "不确定 1", ...] for one run record, in display order. */
export function runCounts(run) {
  const counts = {};
  for (const t of run?.tasks || []) counts[t.status] = (counts[t.status] || 0) + 1;
  return Object.keys(OUTCOME_LABEL).filter((k) => counts[k]).map((k) => `${OUTCOME_LABEL[k]} ${counts[k]}`);
}

/** Next daily reset (JST `hour`:00, default 05:00) strictly after `now`. */
export function nextRollover(now = new Date(), hour = 5) {
  const day = 24 * 3600 * 1000;
  const t = now.getTime();
  let next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour - 9, 0, 0);
  while (next <= t) next += day;
  while (next - day > t) next -= day;
  return new Date(next);
}

/** "3 小时 12 分" until `next`. */
export function untilText(now, next) {
  const min = Math.max(0, Math.ceil((next.getTime() - now.getTime()) / 60000));
  const hours = Math.floor(min / 60);
  return hours ? `${hours} 小时 ${min % 60} 分` : `${min} 分`;
}

/** 主页 countdown beside the clock glyph: 「3 小时 12 分」; the tooltip names the reset time. */
export function resetText(now, next) {
  return untilText(now, next);
}

const STOP_STATUS = ["running", "stopped", "takeover", "failed", "uncertain", "guard_refused"];

/**
 * The task a run that did not finish got stuck at (主页 「上次运行」): the first task that stopped, failed or was
 * uncertain, else the first one that never started. `{ name, status }`, or null for a run that finished.
 */
export function stoppedAt(run) {
  if (!["uncertain", "takeover", "failed", "guard_refused", "stopped"].includes(runOutcome(run))) return null;
  const tasks = (run?.tasks || []).map((t) => (typeof t === "string" ? { name: t } : t || {})).filter((t) => t.name);
  const hit = tasks.find((t) => STOP_STATUS.includes(t.status)) || tasks.find((t) => !t.status || t.status === "pending");
  return hit ? { name: hit.name, status: hit.status || "pending" } : null;
}

/** Player-facing live problems (what to do), shared by the run bar, 设置 → 实机 and the 开始 pre-check. */
export function liveProblems(state, { start = false } = {}) {
  const { host = {}, policy = {} } = state;
  return !host.connected ? ["游戏窗口未连接"]
    : host.desktop_locked ? ["桌面已锁定"]
    : host.minimized ? ["游戏窗口已最小化"]
    : [
      !host.pipeline_input_enabled && INPUT_OFF_TEXT,
      host.recognition_ready === false && RECOGNITION_MISSING_TEXT,
      !policy.live_input_enabled && "此版本未开放游戏操作",
      !policy.risk_reference_resolvable && "实机配置不完整：需重新解压测试版",
      !start && host.host_guard_reason && (host.host_guard_reason === "login_required"
        ? "未登录" : "当前画面不能操作"),  // per frame; the host re-checks
    ].filter(Boolean);
}

/**
 * 实机未就绪 at 开始: the button is disabled with the reason beside it instead of a toast after the click.
 * The mock preview and developer runs (只识别 / 离线回放) need no game input.
 */
export function liveStartProblems(state, devRun = null) {
  return state.bridgeKind === "mock" || devRun ? [] : liveProblems(state, { start: true });
}

/**
 * Items that need the user: uncertain / takeover / failed / refused task results (this session, then today's runs),
 * spend-ledger entries to check, start blockers (e.g. an 自动编队 proposal not yet confirmed) and host problems.
 * Each: {key, tone, text, tab?}. Display only; never an authorisation.
 */
export function attentionItems(state, day) {
  const out = [];
  const seen = new Set();
  for (const [name, st] of Object.entries(state.taskStatus || {})) {
    if (!ATTENTION[st?.status]) continue;
    seen.add(name);
    out.push({ key: `task:${name}`, tone: ATTENTION[st.status], tab: "tasks", ack: ["uncertain", "takeover"].includes(st.status) ? name : null,
      text: `${taskName(state, name)}：${outcomeText(st.reason, name) || HINT[st.status]}` });
  }
  const acked = ackedTasks(state, day);
  for (const [name, status] of Object.entries(todayStatuses(state.runs || [], day))) {
    if (seen.has(name) || acked.includes(name) || !["uncertain", "takeover"].includes(status)) continue;
    out.push({ key: `run:${name}`, tone: ATTENTION[status], tab: "tasks", ack: name,
      text: `${taskName(state, name)}：${OUTCOME_LABEL[status]}，请在游戏中核对` });
  }
  const ledger = openLedger(state).length;
  if (ledger) out.push({ key: "ledger", tone: "uncertain", tab: "resources", text: `${ledger} 条消耗记录待核对` });
  // one line per problem: Match and Grade share the 参加权 conflict text
  const blockers = new Map();
  for (const blocker of startBlockers(state)) {
    const text = blocker.errors[0];
    blockers.set(text, [...(blockers.get(text) || []), blocker.name]);
  }
  for (const [text, names] of blockers) {
    out.push({ key: `block:${names[0]}`, tone: "warning", tab: "tasks", task: names[0], text: `${names.map((n) => taskName(state, n)).join("、")}：${text}` });
  }
  const host = state.host || {};
  if (host.host_guard_reason === "login_required") out.push({ key: "host", tone: "warning", text: "未登录：在游戏窗口完成登录" });
  else if (!host.connected) out.push({ key: "host", tone: "danger", text: "游戏窗口未连接" });
  else if (host.desktop_locked) out.push({ key: "host", tone: "warning", text: "桌面已锁定，无法操作游戏" });
  else if (host.host_guard_reason) out.push({ key: "host", tone: "warning", text: "游戏窗口暂时不能操作，未点击" });
  else if (host.pipeline_input_enabled === false) out.push({ key: "host", tone: "warning", text: INPUT_OFF_TEXT });
  if (host.connected && host.recognition_ready === false) out.push({ key: "recognition", tone: "danger", text: RECOGNITION_MISSING_TEXT });
  return out;
}

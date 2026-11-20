// Display layer (ui/annotations.json v2): short Chinese names, developer-only visibility, item icons.
// Never changes what a task or option means; it only decides how it is labelled and whether it is shown.
import { anyCardArt } from "./game-assets.js";
import { teamCheck } from "./team-rules.js";
import { fesPlan } from "./fes-plan.js";
import { matchProgressSummary } from "./match-progress.js";
import { gameDay } from "./store.js";

export function devMode(state) {
  return state.prefs?.developer === true;
}

export function taskNote(state, name) {
  return state.annotations.tasks?.[name] || {};
}

/** Short task name, e.g. 「商店」 instead of the interface label with development parentheses. */
export function taskName(state, name) {
  const note = taskNote(state, name);
  return note.name || state.schema.tasks.find((t) => t.name === name)?.label || name;
}

export function isDevTask(state, name) {
  const task = state.schema.tasks.find((t) => t.name === name);
  const note = taskNote(state, name);
  // settings_only: an unmigrated task whose settings the user edits now (Produce 编队); its run stays a no-click stub.
  // 2026-10-11: a settings_only task with nothing to set (WeeklyProduce) would only run as a failing stub: developer-only.
  if (note.settings_only === true) return note.dev === true || !(task?.options || []).length;
  return note.dev === true || note.migrated === false || task?.ui?.migrated === false;
}

export function taskVisible(state, name) {
  return devMode(state) || !isDevTask(state, name);
}

/** whether the task is in the current 方案 (not listed in profile.removed_tasks). */
export function taskInPlan(state, name) {
  return !(state.profile?.removed_tasks || []).includes(name);
}

/**
 * 2026-10-13: where a task lives. "queue" = a row of the 任务 tab (the daily run); "auto" = a hidden read the daily run
 * adds by itself (annotations `auto: true`, e.g. MissionStatus feeding 主页 「任务达成」); otherwise the tab key that owns
 * it (annotations `tab`, e.g. AutoProduce on 「Produce」: a W.I.N.G. run is never part of the daily queue).
 */
export function taskPlace(state, name) {
  const note = taskNote(state, name);
  return note.tab ? note.tab : note.auto === true ? "auto" : "queue";
}

export function taskInQueue(state, name) {
  return taskPlace(state, name) === "queue";
}

/** Task rows of the 任务 tab: visible (developer rule), in the 方案 and part of the daily queue. */
export function taskShown(state, name) {
  return taskVisible(state, name) && taskInPlan(state, name) && taskInQueue(state, name);
}

/** {task: option} for tasks whose tick is their spend consent (annotations `consent_option`; store.applyConsent input). */
export function consentOptions(annotations) {
  return Object.fromEntries(Object.entries(annotations?.tasks || {}).filter(([, note]) => note.consent_option).map(([name, note]) => [name, note.consent_option]));
}

// What never gets spent (project_policy.json); one list for 资源 and 设置 → 实机.
export const NEVER_USE = [["jewel", "ジュエル"], ["paid", "付费道具"], ["gasha", "ガシャ"]];

// Recovery items in the 资源 item grid that Maa never uses (stamina / Fes-ticket use needs its own setting, off by default):
// they carry the same 「不使用」 mark as ジュエル so their game icon is not read as something that gets spent.
export const NEVER_USE_ITEMS = new Set(["stamina_drink", "stamina_drink_60", "stamina_drink_100", "fes_ticket", "fes_ticket_5", "continue_ticket"]);

// 「消耗：…」 of a task that spends (shown in the task's settings; 资源 → 预算 removed 2026-10-28). `v(key)` is
// the shown value of an option; `on` the number of ticked goods (商店).
const COST = {
  Shop: (v, on) => `マニー · 今日限定 ${on} 件${v("商店余额保留") === "不保留" ? "" : ` · 余额保留 ${v("商店余额保留")}`}`,
  Training: (v) => `${v("训练券")} ×${v("训练数量")}/天`,
  MatchSkip: (v) => `Fes 参加权 · ${v("Match次数") === "全部" ? "用完剩余" : `${v("Match次数")} 次`} · 保留 ${v("Match参加权保留")} · 不用回复券`,
  GradeFes: (v) => `Fes 参加权 ${v("Grade一次消耗")} 个/局 · 每日 ${v("Grade每日次数")} 局 · 保留 ${v("Grade参加权保留")} · 不用回复券`,
  CollaboFes: (v) => `コラボフェスパス 1/局 · ${["全部用完", "全部"].includes(v("Collabo局数")) ? "用完（最多 6）" : `${v("Collabo局数")} 局`}`,
  AutoProduce: (v) => (wingRuns(v("Produce次数")) > 1 ? `体力 30/局 · 最多 ${wingRuns(v("Produce次数"))} 局` : "体力 30/局 · 每次 1 局"),
};

const RESERVE_YEN = { "1万": 10000, "10万": 100000, "100万": 1000000 };
const SHOP_PRICE = 10;   // the two usual goods cost 10 マニー each

/**
 * 商店 保留マニー (the reserve is visible): {reserve label, ticked goods, money (last reading or null),
 * short: true when the last reading leaves less than the reserve after one purchase, i.e. nothing would be bought}.
 */
export function shopReserve(state, entry, money = null) {
  const value = entry?.option?.["商店余额保留"] ?? state.schema.options["商店余额保留"]?.default_case;
  const reserve = RESERVE_YEN[value] ?? 0;
  const on = (taskNote(state, "Shop").require_any?.options || []).filter((k) => entry?.option?.[k] === "Yes").length;
  return { label: caseLabel(state, "商店余额保留", value), reserve, on, money, short: money != null && reserve > 0 && money - SHOP_PRICE < reserve };
}

/** Last マニー reading, the one 主页 / 资源 show (state.readings: run and passive readings, newest wins), or null. */
export function lastMoney(state) {
  const tile = (state.readings || []).find((t) => t?.item === "money" && Number.isInteger(t.value) && t.value >= 0);
  return tile ? tile.value : null;
}

/** 「消耗：…」 for a task that spends, else null. */
export function costLine(state, name, entry = state.profile?.tasks?.find((t) => t.name === name)) {
  const cost = COST[name];
  if (!cost || !entry) return null;
  const v = (key) => caseLabel(state, key, entry.option?.[key] ?? state.schema.options[key]?.default_case);
  const on = (taskNote(state, name).require_any?.options || []).filter((k) => entry.option?.[k] === "Yes").length;
  return `消耗：${cost(v, on)}`;
}

/** {auto, own} task names from the annotations (store.applyTaskPlacement input). */
export function taskPlacement(annotations, schema) {
  const names = (schema?.tasks || []).map((t) => t.name);
  const place = (name) => taskPlace({ annotations }, name);
  return { auto: names.filter((n) => place(n) === "auto"), own: names.filter((n) => !["queue", "auto"].includes(place(n))) };
}

// One-line picker descriptions (display only). annotations `tasks[name].blurb` overrides these defaults.
const TASK_BLURBS = {
  Startup: "从标题画面回到首页",
  Shop: "兑换当天「本日限定」里勾选的商品",
  Training: "用 1 张铜券训练所选 S 卡",
  MatchSkip: "跳过所选 Match Live 关卡",
  CollaboFes: "用所选编队打 Collabo Fes 会场",
  GradeFes: "按每日局数打 Grade Fes 或彩排",
  MissionStatus: "读取每日任务进度（日常运行时自动读取）",
  MissionsClaim: "领取每日和周常任务奖励",
  Business: "收取营业，再营业 12 小时",
  AutoProduce: "W.I.N.G. 培养（在「Produce」页开始）",
  Diagnostics: "截图并识别当前画面",
  ProduceDryRun: "用样例空跑一次决策",
};

export function taskBlurb(state, name) {
  return taskNote(state, name).blurb || TASK_BLURBS[name] || "";
}

/** Mutual-exclusion rules (annotations) that adding `name` would trigger against the enabled, shown tasks. */
export function addConflicts(state, name) {
  const enabled = new Set((state.profile?.tasks || []).filter((t) => t.enabled && t.name !== name && taskShown(state, t.name)).map((t) => t.name));
  return (state.annotations.mutual_exclusion || []).filter((rule) => rule.tasks.includes(name) && rule.tasks.some((n) => n !== name && enabled.has(n)));
}

export function optionNote(state, key) {
  return state.annotations.options?.[key] || {};
}

export function optionLabel(state, key) {
  return optionNote(state, key).label || state.schema.options[key]?.label || key;
}

export function caseLabel(state, key, value) {
  const spec = state.schema.options[key];
  if (spec?.type === "switch" && (value === "Yes" || value === "No")) return value === "Yes" ? "开" : "关";
  if (spec?.type === "input") {  // single-input option, e.g. a Produce card id
    if (!value) return "未登记";
    const art = anyCardArt(value);
    return art ? `${art.idol}${art.title ? `【${art.title}】` : ""}` : value;
  }
  return optionNote(state, key).cases?.[value] || value || "—";
}

/** Cases offered in the main UI; developer-only cases appear only with 开发者选项 (and the current value always). */
export function visibleCases(state, key, current) {
  const spec = state.schema.options[key];
  const hidden = new Set(devMode(state) ? [] : optionNote(state, key).dev_cases || []);
  return (spec?.cases || []).filter((c) => !hidden.has(c.name) || c.name === current);
}

/** Option rows shown for a task: developer-only and single-choice options are hidden unless 开发者选项 is on. */
/** Parent switch key of an option nested with annotations `under`, or null. */
export function optionParent(state, key) {
  return optionNote(state, key).under || null;
}

function parentOn(state, key, entry) {
  const parent = optionParent(state, key);
  if (!parent) return true;
  return (entry.option?.[parent] ?? state.schema.options[parent]?.default_case) === "Yes";
}

/** annotations `show_when: {optionKey: case}`: shown only while every listed option of the task holds that case. */
function showWhenOk(state, key, entry) {
  const rule = optionNote(state, key).show_when;
  if (!rule) return true;
  return Object.entries(rule).every(([other, want]) => (entry.option?.[other] ?? state.schema.options[other]?.default_case) === want);
}

/** annotations `active_dates: [[from, to], ...]` (game days, inclusive): an event setting shown only inside a window. */
export function activeDatesOk(state, key) {
  const windows = optionNote(state, key).active_dates;
  if (!Array.isArray(windows)) return true;
  const day = gameDay(state.now ? new Date(state.now) : new Date());
  return windows.some(([from, to]) => from <= day && day <= to);
}

/** Display note of an item_picker group (annotations `option_groups`). */
export function groupNote(state, group) {
  return state.annotations.option_groups?.[group] || {};
}

/** Group of an option shown through a multi-select picker (`control: "item_picker"` + `group`), or null. */
export function optionGroup(state, key) {
  const note = optionNote(state, key);
  return (note.control === "item_picker" || note.control === "team_slot") && note.group ? note.group : null;
}

/**
 * Rows for the task body: visible options in order, with the members of an item_picker group collapsed into ONE
 * row ({group, keys}) at the position of its first member. `keys` lists every member the task declares.
 */
export function optionRows(state, task, entry) {
  const rows = [];
  const seen = new Set();
  for (const key of visibleOptions(state, task, entry)) {
    const group = optionGroup(state, key);
    if (!group) { rows.push({ key }); continue; }
    if (seen.has(group)) continue;
    seen.add(group);
    rows.push({ group, keys: (task.options || []).filter((k) => state.schema.options[k] && optionGroup(state, k) === group) });
  }
  return rows;
}

/**
 * Warning text when an enabled task cannot do anything with its current settings: annotations `require_any` (none of those
 * switches is Yes) or `require_value` ({option, not, message}: the option still holds its "nothing chosen" value, e.g. 训练
 * with 未选择 -- roleplay 2026-10-10: a ticked 训练 card with no S card silently did nothing). Display only.
 */
export function requireAnyWarning(state, task, entry) {
  const note = taskNote(state, task.name);
  if (!entry?.enabled) return null;
  const rule = note.require_any;
  if (rule) {
    const keys = (rule.options || []).filter((k) => state.schema.options[k]);
    const on = keys.some((k) => (entry.option?.[k] ?? state.schema.options[k].default_case) === "Yes");
    if (keys.length && !on) return rule.message || "未选择任何项目";
  }
  const need = note.require_value;
  if (need && state.schema.options[need.option]) {
    const value = entry.option?.[need.option] ?? state.schema.options[need.option].default_case;
    if (value === need.not) return need.message || "未选择";
  }
  return null;
}

export function visibleOptions(state, task, entry) {
  const keys = (task.options || []).filter((key) => state.schema.options[key]);
  const shown = keys.filter((key) => {
    // `hidden`: a retired setting kept only so saved profiles stay valid (Produce可开新局, fixed to Yes since 2026-10-13).
    if (optionNote(state, key).hidden) return false;
    // `active_dates`: 強化週間 limited auditions (no permanent option for a few-week event).
    if (!activeDatesOk(state, key)) return false;
    if (devMode(state)) return true;
    if (optionNote(state, key).dev) return false;
    if (!parentOn(state, key, entry)) return false;
    if (!showWhenOk(state, key, entry)) return false;
    // always_show: a fixed setting the user should still see (e.g. 训练券 = 铜训练券, 数量 = 1), shown read-only.
    if (["card_picker", "team_slot"].includes(optionNote(state, key).control) || optionNote(state, key).always_show) return true;
    // 2026-10-11 (UI audit #7): a free input (e.g. Grade 参加编队评价值) has no cases but is a real setting.
    if (state.schema.options[key]?.type === "input") return true;
    return visibleCases(state, key, entry.option?.[key]).length > 1;
  });
  return [...shown.filter((k) => optionNote(state, k).first), ...shown.filter((k) => !optionNote(state, k).first)];
}

/** Whether the task's explicit spend option currently consents (same rule as tools/ui_schema.spend_flags_for_profile). */
export function spendOn(state, task, entry) {
  const spend = task?.ui?.spend_option;
  if (!spend || !entry?.enabled) return false;
  const spec = state.schema.options[spend.key];
  return (entry.option?.[spend.key] ?? spec?.default_case) === spend.when;
}

export function itemFor(state, name) {
  const key = taskNote(state, name).item;
  return key ? { key, ...(state.annotations.items?.[key] || {}) } : null;
}

/** Validation errors of a task's settings (today: the Produce 编队 rules in team-rules.js). Empty = OK. */
export function taskErrors(state, task, entry) {
  const errors = [...teamCheck(state, task.name, entry).errors];
  // Match + Grade may share フェス参加権 on one day; a refused split blocks 开始 on both rows.
  if (FES_TASKS.includes(task.name) && entry?.enabled) return [...errors, ...fesPlan(state).messages];
  return errors;
}

const FES_TASKS = ["MatchSkip", "GradeFes"];
export const WING_TASK = "AutoProduce";
/** 「只读取资源」 (2026-10-26, role-play D6/A8): started from 主页 → 更新资源, never a daily row (annotations tab: home). */
export const RESOURCE_TASK = "ResourceRead";

/** Enabled tasks that would run: visible and in the 方案 (any place). */
function enabledRunnable(state) {
  return (state.profile?.tasks || []).filter((t) => t.enabled && taskVisible(state, t.name) && taskInPlan(state, t.name)).map((t) => t.name);
}

/** The enabled tasks are exactly W.I.N.G. (see wingRunState): 开始 sends run.start supervised_wing with its own confirmation. */
export function wingOnly(state) {
  const names = enabledRunnable(state);
  return names.length === 1 && names[0] === WING_TASK;
}

/**
 * 2026-10-13: the run selection of the 「Produce」 tab's 开始: only W.I.N.G., whatever the daily 方案 holds (the saved
 * profile keeps its options; nothing else changes).
 */
export function wingRunState(state) {
  if (!state.profile?.tasks?.some((entry) => entry.name === WING_TASK)) return null;
  return { ...state, profile: { ...state.profile,
    removed_tasks: (state.profile.removed_tasks || []).filter((n) => n !== WING_TASK),
    tasks: state.profile.tasks.map((entry) => ({ ...entry, enabled: entry.name === WING_TASK })) } };
}

/**
 * The run selection of 「只读取资源」: 启动到首页 + ResourceRead only, whatever the daily 方案 holds (the saved profile is
 * unchanged). A profile saved before the task existed gets its entry here.
 */
export function resourceReadState(state) {
  if (!state.profile?.tasks || !state.schema?.tasks?.some((t) => t.name === RESOURCE_TASK)) return null;
  const names = ["Startup", RESOURCE_TASK];
  const tasks = state.profile.tasks.some((t) => t.name === RESOURCE_TASK) ? state.profile.tasks
    : [...state.profile.tasks, { name: RESOURCE_TASK, enabled: false, option: {} }];
  return { ...state, profile: { ...state.profile,
    removed_tasks: (state.profile.removed_tasks || []).filter((n) => !names.includes(n)),
    tasks: tasks.map((entry) => ({ ...entry, enabled: names.includes(entry.name) })) } };
}

/** Settings errors that keep the 「Produce」 tab's 开始 disabled (the W.I.N.G. 编队 rules). */
// W.I.N.G. 连跑 (tools/ui_engine.py readings kind "wing_loop"): why the loop ended (wing_segments.next_run_verdict).
export const WING_LOOP_END = {
  planned_runs: "已打完", target_hit: "补上目标，已停", run_not_home: "停在獲得報酬页，未继续",
  books_unread: "新书未读完，未继续", keep_uncertain: "锁书结果不确定，未继续", time_cap: "时间不够下一局",
  spend_pending: "开局结果未确认，未继续", stop_requested: "已停止", config_refused: "设置未通过，未继续",
};

/** The newest 连跑 reading of a run ({run, planned, finished} while running; {planned, finished, ended, hits} at the end). */
export function wingLoopReading(readings, runId = null) {
  const rows = (readings || []).filter((r) => r?.kind === "wing_loop" && r.readings && (!runId || r.run_id === runId));
  return rows.length ? rows[rows.length - 1].readings : null;
}

/** 「连跑 2/3 局 · 读到目标缺的条目…」 for a finished loop reading, else "". */
export function wingLoopText(loop) {
  if (!loop?.planned || loop.planned < 2 || !loop.ended) return "";
  return [`连跑 ${loop.finished ?? 0}/${loop.planned} 局`, WING_LOOP_END[loop.ended]].filter(Boolean).join(" · ");
}

/** The run's post-Produce new-book reads (one per W.I.N.G. run) added up: {read, new, hits, locked, lockUncertain}, or null. */
export function knowhowAfterReading(readings, runId = null) {
  const rows = (readings || []).filter((r) => r?.kind === "knowhow_after" && r.readings && (!runId || r.run_id === runId)).map((r) => r.readings);
  if (!rows.length) return null;
  const sum = (key) => rows.reduce((n, r) => n + (Number.isInteger(r[key]) && r[key] > 0 ? r[key] : 0), 0);
  return { read: rows.every((r) => r.read === true), new: sum("new"), hits: sum("hits"), locked: sum("locked"),
    lockUncertain: rows.some((r) => r.lock_uncertain === true) };
}

/** 「新书 2 本 · 补上目标 1 本 · 锁定 1 本」 (did a W.I.N.G. run bring a target book, was it locked). */
export function knowhowAfterText(k) {
  if (!k) return "";
  if (!k.read) return "新书未读完";
  return [`新书 ${k.new} 本`, k.hits ? `补上目标 ${k.hits} 本` : "未补上目标", k.locked ? `锁定 ${k.locked} 本` : "",
    k.lockUncertain ? "锁书结果不确定" : ""].filter(Boolean).join(" · ");
}

/** 「Produce次数」 as a number (1 when unset or unreadable). */
export function wingRuns(value) {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 ? n : 1;
}

// 剧本 of the 「Produce」 tab (H2c batch 5, 2026-11-04): the AutoProduce option 「Produce剧本」, switched by the scenario
// rail. say "Halo" keeps the P idol / team settings and runs one Produce per start.
export const SCENARIO_OPTION = "Produce剧本";
export const SCENARIO_CASES = { wing: "W.I.N.G.", halo: 'say "Halo"' };

/** "wing" | "halo" (pure): the chosen 剧本; "wing" when the schema has no 剧本 option. */
export function wingScenario(state, entry) {
  if (!state.schema.options?.[SCENARIO_OPTION]) return "wing";
  const value = entry?.option?.[SCENARIO_OPTION] ?? state.schema.options[SCENARIO_OPTION].default_case;
  return value === SCENARIO_CASES.halo ? "halo" : "wing";
}

export function wingBlockers(state) {
  const entry = state.profile?.tasks?.find((t) => t.name === WING_TASK);
  if (!entry) return [];
  const errors = taskErrors(state, state.schema.tasks.find((t) => t.name === WING_TASK) || { name: WING_TASK }, { ...entry, enabled: true });
  // 连跑: the next run starts only after Maa itself pressed 獲得報酬 OK (tools/ui_wing.py refuses the same)
  const opt = (key) => entry.option?.[key] ?? state.schema.options?.[key]?.default_case;
  if (wingScenario(state, entry) === "wing" && wingRuns(opt("Produce次数")) > 1 && opt("Produce报酬后OK") !== "Yes") {
    errors.push("连跑需开启「读取后自动确认」");
  }
  return errors.length ? [{ name: WING_TASK, errors }] : [];
}

/**
 * Run-bar model of the 「Produce」 tab (pure; unit-tested): what 开始 would do, or why it is disabled.
 * kind: "start" | "stop" (a W.I.N.G. run is active) | "busy" (another run is active).
 */
export function wingRunBar(state) {
  const run = state.run || {};
  const entry = state.profile?.tasks?.find((t) => t.name === WING_TASK) || null;
  const task = state.schema.tasks.find((t) => t.name === WING_TASK) || null;
  // A W.I.N.G. run: the supervised mode, or a developer run whose queue is W.I.N.G. (run_live names it "Wing").
  const wingRun = run.mode === "supervised_wing" || (run.tasks || []).some((n) => n === WING_TASK || n === "Wing");
  if (run.active && wingRun) {
    const loop = wingLoopReading(state.runReadings, run.run_id || run.id || null);
    const step = loop?.run && loop.planned > 1 && !loop.ended ? ` · 第 ${loop.run}/${loop.planned} 局` : "";
    return { kind: "stop", main: run.stopping ? "正在停止" : `W.I.N.G. 运行中${step}`, stopping: run.stopping === true };
  }
  if (run.active) return { kind: "busy", main: "日常运行中", title: "日常结束后再开始 W.I.N.G." };
  const blockers = entry ? wingBlockers(state) : [{ name: WING_TASK, errors: ["没有 W.I.N.G. 设置"] }];
  // Developer run modes (只识别 / 离线回放) apply here too, as they did when W.I.N.G. was a 任务 row.
  const devRun = devMode(state) && ["offline_replay", "read_only"].includes(state.runMode) ? state.runMode : null;
  const halo = wingScenario(state, entry) === "halo";
  const goal = entry && !halo ? caseLabel(state, "Produce目标", entry.option?.Produce目标 ?? state.schema.options.Produce目标?.default_case) : "";
  const runs = halo ? 1 : wingRuns(entry?.option?.Produce次数 ?? state.schema.options?.Produce次数?.default_case);
  return {
    kind: "start", disabled: blockers.length > 0, title: blockers.map((b) => b.errors[0]).join("\n") || null, devRun,
    main: devRun ? `W.I.N.G. · ${devRun === "offline_replay" ? "离线回放" : "只识别"}` : [halo ? SCENARIO_CASES.halo : "W.I.N.G.", goal].filter(Boolean).join(" · "),
    // 2026-10-13 (user): no 开局 choice — an in-progress Produce is checked (P 偶像/编队) and continued, otherwise a new one
    // starts. 2026-10-24: the cost stays visible, the resume rule is the button's tooltip.
    sub: devRun || !task ? null : runs > 1 ? `新局消耗：体力 30/局；最多 ${runs} 局` : "新局消耗：体力 30",
    tip: devRun || !task ? null : "有进行中的 Produce 时先核对 P 偶像与编队，一致才继续",
  };
}

/**
 * The daily run selection of 开始 / 开始日常: the ticked 任务 rows; tasks owned by another tab never run here; hidden
 * `auto` reads join whenever at least one row runs.
 */
export function dailyRunState(state) {
  const rows = (state.profile?.tasks || []).filter((t) => t.enabled && taskShown(state, t.name)).length > 0;
  // 2026-10-14 user: 任务奖励 reads the same daily page and claims what is there; the separate 每日任务进度 read
  // (home -> missions -> home) is skipped when it runs.
  const claims = (state.profile?.tasks || []).some((t) => t.name === "MissionsClaim" && t.enabled
    && taskPlace(state, t.name) === "queue" && taskVisible(state, t.name) && taskInPlan(state, t.name));
  const removed = (state.profile?.removed_tasks || []).filter((n) => taskPlace(state, n) !== "auto");
  return { ...state, profile: { ...state.profile, removed_tasks: removed,
    tasks: (state.profile?.tasks || []).map((entry) => {
      const place = taskPlace(state, entry.name);
      const enabled = place === "auto" ? rows && !(entry.name === "MissionStatus" && claims)
        : place === "queue" && entry.enabled && taskVisible(state, entry.name) && taskInPlan(state, entry.name);
      return { ...entry, enabled };
    }) } };
}

/** A temporary run selection; saved tasks, order and options remain unchanged. W.I.N.G. resolves to wingRunState. */
export function singleTaskState(state, name) {
  if (name === WING_TASK) return wingRunState(state);
  if (name === RESOURCE_TASK) return resourceReadState(state);
  if (!taskVisible(state, name) || !taskInPlan(state, name) || !taskInQueue(state, name)
      || !state.profile.tasks.some((entry) => entry.name === name)) return null;
  return { ...state, profile: { ...state.profile,
    tasks: state.profile.tasks.map((entry) => ({ ...entry, enabled: entry.name === name })) } };
}

/** One-line フェス参加権 split shown under the enabled Fes rows ("" when no Fes task consumes). */
export function fesSummary(state, task, entry) {
  if (!FES_TASKS.includes(task.name) || !entry?.enabled) return "";
  const progress = task.name === "MatchSkip" ? matchProgressSummary(state) : "";
  return [fesPlan(state).summary, progress].filter(Boolean).join("；");
}

/** [{name, errors}] for enabled, visible 任务 rows whose settings are invalid: 开始 stays disabled while non-empty. */
export function startBlockers(state) {
  return (state.profile?.tasks || [])
    .filter((entry) => entry.enabled && taskVisible(state, entry.name) && taskInQueue(state, entry.name))
    .map((entry) => ({ name: entry.name, errors: taskErrors(state, state.schema.tasks.find((t) => t.name === entry.name) || { name: entry.name }, entry) }))
    .filter((item) => item.errors.length);
}

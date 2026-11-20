// Application state and the UI profile <-> run config mapping (mirror of tools/ui_schema.profile_to_run_config and
// spend_flags_for_profile). The profile never holds live-input authorization. Spend flags are derived only from each
// task's explicit spend option (task.ui.spend_option) and travel separately in run.start; UI-only options are dropped.

export const PROFILE_VERSION = 1;

export class Store extends EventTarget {
  constructor(initial) {
    super();
    this.state = initial;
  }
  get() {
    return this.state;
  }
  set(patch, reason = "update") {
    this.state = { ...this.state, ...patch };
    this.dispatchEvent(new CustomEvent("change", { detail: { reason, keys: Object.keys(patch) } }));
  }
}

/** Single-input PI option (e.g. a Produce card id): the profile holds one string that must match `verify`. */
export function inputValueOk(spec, value) {
  const inputs = spec?.type === "input" ? spec.inputs || [] : [];
  if (inputs.length !== 1 || typeof value !== "string" || value.length > 256) return false;
  try {
    return !inputs[0].verify || new RegExp(`^(?:${inputs[0].verify})$`).test(value);
  } catch {
    return false;
  }
}

export function optionDefaults(schema, taskName) {
  const task = schema.tasks.find((t) => t.name === taskName);
  const out = {};
  for (const key of task?.options || []) {
    const spec = schema.options[key];
    if (spec && spec.default_case != null) out[key] = spec.default_case;
  }
  return out;
}

/**
 * Full task list from a preset: preset tasks first (preset order), then the remaining tasks unchecked.
 * A preset replaces the task list; tasks it does not list start outside the 方案
 * (`removed_tasks`) and are offered by 「添加任务」. Without a preset nothing is removed.
 */
export function profileFromPreset(schema, presetName) {
  const preset = schema.presets.find((p) => p.name === presetName) || null;
  const tasks = [];
  const seen = new Set();
  for (const item of preset?.task || []) {
    if (!schema.tasks.some((t) => t.name === item.name) || seen.has(item.name)) continue;
    seen.add(item.name);
    tasks.push({ name: item.name, enabled: item.enabled !== false, option: { ...optionDefaults(schema, item.name), ...(item.option || {}) } });
  }
  const removed = [];
  for (const task of schema.tasks) {
    if (seen.has(task.name)) continue;
    tasks.push({ name: task.name, enabled: preset ? false : task.default_check, option: optionDefaults(schema, task.name) });
    if (preset) removed.push(task.name);
  }
  return { schema_version: PROFILE_VERSION, preset: preset ? preset.name : null, tasks, removed_tasks: removed };
}

// ---------------------------------------------------------------- legacy profile migration
// Mirror of tools/ui_schema.migrate_legacy_profile. The W.I.N.G. task no longer offers 周常: a stored
// AutoProduce entry with 「Produce目标」=「周常」 moves its checkbox and 方案 membership to the separate WeeklyProduce
// task (Auto Produce·周常, a not-yet-migrated stub) and AutoProduce itself becomes unchecked with the default goal.
// Renamed cases keep the user's choice; 25% (no longer offered) becomes the new maximum 20%.
export const LEGACY_WEEKLY_GOAL = "周常";
export const LEGACY_CASES = {
  "Produce目标": { "粉丝": "只过季门槛" },
  // 2026-10-12 (U07/U09): P items not supported -> saved item plans fall back (saved 2/3 runs fell back to 1 until
  // 2026-10-26, when 连跑 made 1–5 runs real choices)
  "ProduceP道具": { "手帐+羁绊": "不带", "习惯（手帐+絆）": "不带", "开局强化": "不带", "决赛保险（后续）": "不带", "体力（后续）": "不带" },
  "Produce休息阈值": { "25%": "20%" },
  "Match关卡": { "最新": "最新（全部关卡）" },  // 2026-10-11: default = the latest stage of the observed progress
  // 2026-10-10: 训练卡 is a card-id input (any confirmed S card); "" = default (first trainable of the current sort)
  "训练卡": { "未选择": "", "【くっつく、くっつかない】芹沢あさひ": "2050170010" },
  // 2026-10-11: W.I.N.G. on/off settings became switches (Yes/No)
  "Produce终局清空SP": { "开": "Yes", "关": "No" },
  "Produce报酬读取": { "开": "Yes", "关": "No" },
  "Produce保护Lv5书": { "开": "Yes", "关": "No" },
  "Produce再抽选": { "开": "Yes", "关": "No" },
  "Produce报酬后OK": { "按 OK": "Yes", "交给我": "No" },
  // 2026-10-30 (user): Auto 育成方針 has no 「不改」 any more (オート1 is always set by Maa)
  "Auto育成方針": { "不改": "自动" },
  // 2026-11-19: Collabo 局数/等级 became steppers (「N 局」→「N」, 「用完」→「全部用完」, 「Lv.N」→「N」)
  "Collabo局数": { "用完": "全部用完", ...Object.fromEntries([1, 2, 3, 4, 5, 6].map((n) => [`${n} 局`, `${n}`])) },
  "Collabo等级": Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`Lv.${i + 1}`, `${i + 1}`])),
};
// 2026-10-09: settings removed from the UI (fixed in the backend: 商店 budget, 训练 once-per-day and list scroll steps);
// Grade 彩排次数 N > 0 becomes 模式 = フェスリハーサル with N lives. Mirror of tools/ui_schema._migrate_options.
export const REMOVED_OPTIONS = { Shop: ["商店预算"], Training: ["训练每日一次", "训练列表滚动"], GradeFes: ["Grade彩排次数"], AutoProduce: ["Produce开新一局"], WeeklyProduce: ["Auto核对P偶像", "Auto设定オート1", "Auto核对ユニット名"] };

function migrateOptions(name, stored) {
  const option = { ...stored };
  for (const [key, map] of Object.entries(LEGACY_CASES)) {
    if (typeof option[key] === "string" && option[key] in map) option[key] = map[option[key]];
  }
  const rehearsal = option["Grade彩排次数"];
  if (name === "GradeFes" && typeof rehearsal === "string" && /^[0-9]+$/.test(rehearsal) && Number(rehearsal) > 0) {
    option["Grade模式"] = "フェスリハーサル";
    option["Grade每日次数"] = rehearsal;
  }
  for (const key of REMOVED_OPTIONS[name] || []) delete option[key];
  return option;
}

export function migrateLegacyProfile(schema, stored) {
  if (!stored || !Array.isArray(stored.tasks)) return stored;
  const hasWeekly = schema.tasks.some((t) => t.name === "WeeklyProduce");
  let tasks = stored.tasks.map((item) => {
    if (!item || typeof item.option !== "object" || item.option === null) return item;
    return { ...item, option: migrateOptions(item.name, item.option) };
  });
  let removed = Array.isArray(stored.removed_tasks) ? [...stored.removed_tasks] : stored.removed_tasks;
  const index = tasks.findIndex((t) => t?.name === "AutoProduce" && t.option?.["Produce目标"] === LEGACY_WEEKLY_GOAL);
  if (hasWeekly && index >= 0) {
    const auto = tasks[index];
    const { ["Produce目标"]: _goal, ...option } = auto.option;
    const weekly = { name: "WeeklyProduce", enabled: auto.enabled === true, option: {} };
    tasks = tasks.filter((t) => t?.name !== "WeeklyProduce");
    const at = tasks.findIndex((t) => t?.name === "AutoProduce");
    tasks.splice(at, 1, { ...auto, enabled: false, option }, weekly);
    if (Array.isArray(removed)) {
      removed = removed.filter((n) => n !== "WeeklyProduce");
      if (removed.includes("AutoProduce")) removed.push("WeeklyProduce");
    }
  }
  return { ...stored, tasks, ...(Array.isArray(removed) ? { removed_tasks: removed } : {}) };
}

/**
 * Accept a stored profile only when it still matches the schema; unknown tasks/options/cases are dropped.
 * `removed_tasks`: a stored list is kept (known names only) and a task the stored profile never listed (new in the
 * schema) joins it; a legacy profile without the field keeps every task in the 方案, as before.
 */
export function sanitizeProfile(schema, stored, fallbackPreset) {
  const base = profileFromPreset(schema, stored?.preset ?? fallbackPreset);
  stored = migrateLegacyProfile(schema, stored);
  if (!stored || stored.schema_version !== PROFILE_VERSION || !Array.isArray(stored.tasks)) return base;
  const byName = new Map(base.tasks.map((t) => [t.name, t]));
  const legacy = !Array.isArray(stored.removed_tasks);
  const removed = new Set(legacy ? [] : stored.removed_tasks.filter((n) => byName.has(n)));
  const ordered = [];
  for (const item of stored.tasks) {
    const known = byName.get(item?.name);
    if (!known) continue;
    byName.delete(item.name);
    const option = { ...known.option };
    for (const [key, value] of Object.entries(item.option || {})) {
      const spec = schema.options[key];
      if (key in option && spec && spec.cases.some((c) => c.name === value)) option[key] = value;
      else if (key in option && inputValueOk(spec, value)) option[key] = value;
    }
    ordered.push({ name: item.name, enabled: item.enabled === true && !removed.has(item.name), option });
  }
  const rest = [...byName.values()];
  if (!legacy) rest.forEach((t) => { removed.add(t.name); t.enabled = false; });
  const tasks = [...ordered, ...rest];
  return { schema_version: PROFILE_VERSION, preset: base.preset, tasks, removed_tasks: tasks.map((t) => t.name).filter((n) => removed.has(n)) };
}

/**
 * 2026-10-26 (user: 勾选即同意): a task whose tick is its spend consent (display.consentOptions) keeps that switch equal to
 * the tick. `load`: a saved profile ticked without the consent is unticked (it never spent), never switched on.
 */
export function applyConsent(profile, consent = {}, { load = false } = {}) {
  if (!profile || !Array.isArray(profile.tasks) || !Object.keys(consent).length) return profile;
  const tasks = profile.tasks.map((t) => {
    const key = consent[t.name];
    if (!key) return t;
    const enabled = t.enabled && (!load || t.option?.[key] === "Yes");
    const value = enabled ? "Yes" : "No";
    return enabled === t.enabled && t.option?.[key] === value ? t : { ...t, enabled, option: { ...t.option, [key]: value } };
  });
  return { ...profile, tasks };
}

/**
 * 2026-10-13 (UI-only migration, display.taskPlacement): tasks that are not 任务 rows. `auto` reads (MissionStatus) stay in
 * the 方案 and ticked -- the daily run adds them itself; tasks owned by another tab (AutoProduce → 「Produce」) are unticked
 * so a saved queue or 我的方案 that still holds them never runs them with the daily tasks. Options are kept.
 */
export function applyTaskPlacement(profile, { auto = [], own = [] } = {}) {
  if (!profile || !Array.isArray(profile.tasks)) return profile;
  const hidden = new Set([...auto, ...own]);
  const tasks = profile.tasks.map((t) => (auto.includes(t.name) ? { ...t, enabled: true } : own.includes(t.name) ? { ...t, enabled: false } : t));
  const removed = (profile.removed_tasks || []).filter((n) => !hidden.has(n));
  return { ...profile, tasks, removed_tasks: removed };
}

// ---------------------------------------------------------------- 方案 membership (one instance per task)
// run_tasks / ui_schema.profile_to_run_config reject a repeated task name, so a task is either in the 方案 or not;
// removing keeps its entry (options, position) with enabled = false so 撤销 restores it exactly.

export function inPlan(profile, name) {
  return !(profile.removed_tasks || []).includes(name);
}

/** Tasks of the schema that 「添加任务」 may offer: not in the 方案 (visibility is the caller's display rule). */
export function addableTasks(schema, profile) {
  return schema.tasks.filter((t) => !inPlan(profile, t.name)).map((t) => t.name);
}

/** Add a removed task to the end of the 方案, checked. Unknown or already-present names leave the profile unchanged. */
export function addTaskToPlan(schema, profile, name) {
  if (!schema.tasks.some((t) => t.name === name) || inPlan(profile, name)) return profile;
  const tasks = profile.tasks.filter((t) => t.name !== name);
  const old = profile.tasks.find((t) => t.name === name);
  tasks.push({ name, enabled: true, option: { ...optionDefaults(schema, name), ...(old?.option || {}) } });
  return { ...profile, tasks, removed_tasks: profile.removed_tasks.filter((n) => n !== name) };
}

/** Remove a task from the 方案. Returns { profile, undo } (undo = null when nothing changed). */
export function removeTaskFromPlan(profile, name) {
  const index = profile.tasks.findIndex((t) => t.name === name);
  if (index < 0 || !inPlan(profile, name)) return { profile, undo: null };
  const entry = profile.tasks[index];
  const tasks = profile.tasks.map((t) => (t.name === name ? { ...t, enabled: false } : t));
  return {
    profile: { ...profile, tasks, removed_tasks: [...(profile.removed_tasks || []), name] },
    undo: { name, index, enabled: entry.enabled },
  };
}

/** Undo a removal: the task returns with its previous checkbox, options and position. */
export function restoreTaskToPlan(profile, undo) {
  if (!undo || inPlan(profile, undo.name)) return profile;
  const entry = profile.tasks.find((t) => t.name === undo.name);
  if (!entry) return profile;
  const tasks = profile.tasks.filter((t) => t.name !== undo.name);
  tasks.splice(Math.min(undo.index, tasks.length), 0, { ...entry, enabled: undo.enabled === true });
  return { ...profile, tasks, removed_tasks: profile.removed_tasks.filter((n) => n !== undo.name) };
}

/** Run config for tools/run_tasks.py: only name / enabled / option. No allow_spend, no input_enabled. */
export function profileToRunConfig(schema, profile) {
  const removed = new Set(profile.removed_tasks || []);
  return {
    tasks: profile.tasks.map((item) => {
      const task = schema.tasks.find((t) => t.name === item.name);
      const option = {};
      for (const key of task?.options || []) {
        const spec = schema.options[key];
        if (spec && !spec.ui_only && (spec.type === "select" || spec.type === "switch") && item.option?.[key] != null) option[key] = item.option[key];
        else if (spec && !spec.ui_only && inputValueOk(spec, item.option?.[key])) option[key] = item.option[key];
      }
      const entry = { name: item.name, enabled: item.enabled === true && !removed.has(item.name) };
      if (Object.keys(option).length) entry.option = option;
      return entry;
    }),
  };
}

/** run_tasks.SPEND_FLAGS keys for run.start: enabled tasks whose explicit spend option holds the consenting case. */
export function profileSpendFlags(schema, profile) {
  const flags = new Set();
  const removed = new Set(profile.removed_tasks || []);
  for (const item of profile.tasks || []) {
    const task = schema.tasks.find((t) => t.name === item.name);
    const spend = task?.ui?.spend_option;
    const flag = task?.ui?.spend_flag;
    if (item.enabled !== true || removed.has(item.name) || !spend || !flag || !(flag in (schema.run_config?.spend_flags || {}))) continue;
    if ((item.option?.[spend.key] ?? schema.options[spend.key]?.default_case) === spend.when) flags.add(flag);
  }
  return [...flags].sort();
}

/** Game day for the daily checklist: rollover at JST 05:00 (user-verified). */
export function gameDay(now = new Date()) {
  const jst = new Date(now.getTime() + 9 * 3600 * 1000 - 5 * 3600 * 1000);
  return jst.toISOString().slice(0, 10);
}

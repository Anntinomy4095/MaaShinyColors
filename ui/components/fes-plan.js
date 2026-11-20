// Shared フェス参加権 split between Match skip, Grade Fes and rehearsal: display mirror of agent/core/fes_allocation.py
// (tools/ui_schema.fes_preflight). Display only — the engine repeats the same preflight and each task re-reads its
// rights on its Fes page before spending. Unknown current rights are never guessed.
import { gameDay } from "./store.js";

const CAPACITY = 20;
const MESSAGES = {
  mixed_mode_not_supported: "正式 Fes 与彩排分开运行，每次选一种。",
  bulk_not_sampled: "每局只能消耗 1 个参加权：把「每局消耗参加权」设为 1。",
  recovery_policy_disabled: "回复券未开放：关闭「参加权不足时用回复券」",
  recovery_cap_exceeds_policy: "回复券上限超出允许范围",
  recovery_not_implemented: "暂不支持回复券：关闭「参加权不足时用回复券」",
  match_all_before_grade: "Match「全部」会用掉 Grade 的参加权：把 Grade 排在 Match 前面，或给 Match 指定次数。",
  over_allocated: "计划消耗超过当前参加权：减少 Match 次数或 Grade 局数。",
  over_allocated_capacity: "计划消耗超过上限 20 个参加权：减少 Match 次数或 Grade 局数。",
};

function value(state, entry, key) {
  return entry?.option?.[key] ?? state.schema.options[key]?.default_case;
}

function int(state, entry, key, fallback = 0) {
  const n = Number.parseInt(value(state, entry, key), 10);
  return Number.isInteger(n) ? n : fallback;
}

/** The selected mode's latest ledger reading today; recompute remaining when settings change. */
export function gradeDailyPlan(state, entry, now = new Date()) {
  const mode = value(state, entry, "Grade模式") === "フェスリハーサル" ? "rehearsal" : "grade";
  const target = int(state, entry, "Grade每日次数", 1);
  const cap = int(state, entry, "Grade采样每日上限", 0) || target;
  const candidates = (state.runReadings || []).filter((r) => {
    const p = r.readings?.daily_progress;
    const at = Date.parse(r.ts || "");
    const source = r.mode || (state.runs || []).find((run) => (run.run_id || run.id) === r.run_id)?.mode;
    return source === "supervised_daily" && r.kind === "grade" && p?.mode === mode && Number.isFinite(at) && at <= now.getTime()
      && gameDay(new Date(at)) === gameDay(now)
      && Number.isInteger(p.completed) && p.completed >= 0
      && Number.isInteger(p.attempts) && p.attempts >= p.completed;
  }).sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts));
  const progress = candidates[0]?.readings.daily_progress;
  const completed = progress?.completed ?? null;
  // The count is per run (grade_task.per_run_config); lives already played today are only shown.
  const remaining = target;
  let planned = Math.min(target, cap);
  const manual = value(state, entry, "Grade自动演出") === "关闭";
  if (manual) planned = Math.min(1, planned);
  return { mode, target, completed, remaining, planned_runs: planned, attempt_limit: cap,
    limited: cap < target, known: Boolean(progress), takeover: manual && planned > 0 };
}

/** Enabled Fes tasks that will consume, in profile order, with the option values the engine would merge. */
export function fesAllocation(state, now = new Date()) {
  const tasks = (state.profile?.tasks || []).filter((t) => t.enabled && !(state.profile.removed_tasks || []).includes(t.name));
  const order = [];
  const alloc = { match: null, grade: null };
  for (const entry of tasks) {
    if (entry.name === "MatchSkip" && state.schema.options["Match次数"] && value(state, entry, "Match只采样") === "No") {
      const count = value(state, entry, "Match次数");
      alloc.match = { mode: count === "全部用完" ? "all" : "fixed", count: count === "全部用完" ? CAPACITY : int(state, entry, "Match次数", CAPACITY),
        reserve: int(state, entry, "Match参加权保留"), recovery: value(state, entry, "Match回复券") === "Yes", cap: int(state, entry, "Match回复券上限", 1) };
      order.push("MatchSkip");
    }
    if (entry.name === "GradeFes" && state.schema.options["Grade每日次数"] && value(state, entry, "Grade使用参加权") === "Yes") {
      // 2026-10-09: one 模式 (グレードフェス / フェスリハーサル) + one count; まとめて消費 = rights per live (Grade一次消耗).
      const daily = gradeDailyPlan(state, entry, now);
      const lives = daily.planned_runs;
      const rehearsalMode = value(state, entry, "Grade模式") === "フェスリハーサル";
      alloc.grade = { daily, runs: rehearsalMode ? 0 : lives, rehearsal: rehearsalMode ? lives : 0, bulk: int(state, entry, "Grade一次消耗", 1),
        reserve: int(state, entry, "Grade参加权保留"), recovery: value(state, entry, "Grade回复券") === "Yes", cap: int(state, entry, "Grade回复券上限", 1) };
      order.push("GradeFes");
    }
  }
  return { order, ...alloc };
}

/** {status, match, grade, rehearsal, total, errors: [code], messages: [text], summary} — same rules as fes_allocation.plan. */
export function fesPlan(state, observed = null, now = new Date()) {
  const a = fesAllocation(state, now);
  if (!a.order.length) return { status: "idle", available: null, match: null, grade: 0, rehearsal: 0, total: null, errors: [], messages: [], summary: "" };
  const policy = state.schema.run_config?.fes_tickets || { enabled: false, max_per_run: 0 };
  const errors = [];
  if (a.grade && a.grade.bulk > 1) errors.push("bulk_not_sampled");
  for (const part of [a.match, a.grade]) {
    if (!part || !part.recovery) continue;
    const code = !policy.enabled ? "recovery_policy_disabled" : part.cap > policy.max_per_run ? "recovery_cap_exceeds_policy" : "recovery_not_implemented";
    if (!errors.includes(code)) errors.push(code);
  }
  const gradePart = a.grade ? (a.grade.runs + a.grade.rehearsal) * a.grade.bulk : 0;
  const matchAll = a.match && a.match.mode === "all";
  if (matchAll && gradePart && a.order.indexOf("MatchSkip") < a.order.indexOf("GradeFes")) errors.push("match_all_before_grade");
  const available = Number.isInteger(observed) ? observed : null;
  const bound = available ?? CAPACITY;
  const reserve = Math.max(0, ...[a.match, a.grade].filter(Boolean).map((p) => p.reserve));
  const fixedMatch = a.match && !matchAll ? a.match.count : 0;
  const need = gradePart + fixedMatch;
  let match = a.match ? (matchAll ? null : fixedMatch) : 0;
  if (matchAll && available !== null) match = Math.max(0, Math.min(a.match.count, available - gradePart - a.match.reserve));
  const shortfall = need + (need ? reserve : 0) - bound;
  const recoveryCap = [a.match, a.grade].filter((p) => p && p.recovery).reduce((sum, p) => sum + p.cap, 0);
  if (shortfall > 0 && recoveryCap < shortfall) errors.push(available !== null ? "over_allocated" : "over_allocated_capacity");
  const parts = [];
  const times = a.grade && a.grade.bulk > 1 ? `×${a.grade.bulk}` : "";
  if (a.grade) parts.push(...(a.grade.runs ? [`Grade ${a.grade.runs}${times}`] : []), ...(a.grade.rehearsal ? [`彩排 ${a.grade.rehearsal}${times}`] : []));
  if (a.match) parts.push(matchAll ? (gradePart ? "Match 用完剩余" : "Match 全部用完") : `Match ${a.match.count}`);
  const total = match === null ? null : need + (matchAll ? match : 0);
  const daily = a.grade?.daily;
  const dailyText = daily ? `；本轮 ${daily.planned_runs} 局${daily.completed ? `（今日已完成 ${daily.completed} 局）` : ""}${daily.limited ? `（尝试上限 ${daily.attempt_limit}，可在开发者设置调整）` : ""}${daily.takeover ? "；开演后转为手动，后续任务停止" : ""}` : "";
  const summary = `参加权分配：${parts.join(" + ")}${total !== null && !matchAll ? `（合计 ${total}${available === null ? " / 上限 20" : ` / 当前 ${available}`}）` : ""}${dailyText}`;
  return { status: errors.length ? "refused" : available === null ? "rights_unknown" : "ok", available, match,
    grade: a.grade ? a.grade.runs : 0, rehearsal: a.grade ? a.grade.rehearsal : 0, total, errors,
    daily_progress: daily || null, messages: errors.map((code) => MESSAGES[code] || code), summary };
}

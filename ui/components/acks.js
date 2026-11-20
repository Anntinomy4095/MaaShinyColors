// 已核对: one 已核对 for a result to check in the game, the same on 主页 / 任务 / 资源, remembered after the
// panel restarts. It only hides the reminder; nothing is retried. Task results count for the game day they happened on;
// a spend-ledger entry stays checked until the host drops it.
import { gameDay } from "./store.js";

const KEY = "sc-ui-acked";

// localStorage can be missing or throw (private window, node tests): the page still works, it just forgets
function remembered(key, fallback) {
  try { const raw = globalThis.localStorage?.getItem(key); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; }
}

function remember(key, value) {
  try { globalThis.localStorage?.setItem(key, JSON.stringify(value)); } catch { /* not kept */ }
}

/** {day, tasks, ledger, notes} for today; yesterday's task checks and closed notes are dropped. */
export function loadAcks(now = new Date()) {
  const saved = remembered(KEY, {}) || {};
  const day = gameDay(now);
  const today = saved.day === day;
  return { day, tasks: today && Array.isArray(saved.tasks) ? saved.tasks : [], ledger: Array.isArray(saved.ledger) ? saved.ledger : [],
    notes: today && Array.isArray(saved.notes) ? saved.notes : [] };
}

export function saveAcks(acks) {
  remember(KEY, { day: acks.day, tasks: acks.tasks, ledger: acks.ledger, notes: acks.notes || [] });
}

// 关闭 (「通知需要换成可以清除关闭的」): a closed 待处理 line stays hidden for the game day while its
// text is unchanged; a new or changed problem shows again.
export function noteKey(item) {
  return `${item?.key || ""}|${typeof item?.text === "string" ? item.text : ""}`;
}

/** 待处理 items not closed today. */
export function openNotes(state, items, day) {
  const closed = new Set(state.acks?.day === day ? state.acks.notes || [] : []);
  return items.filter((item) => !closed.has(noteKey(item)));
}

/** Stable key of a spend-ledger entry. */
export function ledgerKey(entry) {
  return `${entry?.ts || ""}|${entry?.task || ""}`;
}

/** Ledger entries that still need a check (pending / uncertain and not marked 已核对). */
export function openLedger(state) {
  const acked = new Set(state.acks?.ledger || []);
  return (state.ledger || []).filter((l) => (l.status === "pending" || l.status === "uncertain") && !acked.has(ledgerKey(l)));
}

/** Task names marked 已核对 for `day`. */
export function ackedTasks(state, day) {
  return state.acks?.day === day ? state.acks.tasks : [];
}

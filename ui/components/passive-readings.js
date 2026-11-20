// ANN-17: passive readings on the 资源 page. agent/core/reading_store.py keeps the newest observed value per
// key and writes ui/game-assets/passive-readings.js (local, gitignored); the host copies it into its own ui folder after
// each run. These pure helpers turn its `resource.*` entries into the same tiles as the run readings (readingTiles), and
// one cached loader reads the module. Display only: never input or spend authority; nothing is inferred or zero-filled.
import { scanSuffix } from "./scan-version.js";
import { gameDay } from "./store.js";

const KEY = /^resource\.([a-z][a-z0-9_]{0,40})$/;
const LABELS = { money: "マニー", fes: "Fes 参加权", stamina: "体力", bronze_ticket: "铜训练券", jewel: "ジュエル" };
let cached = null;
let cachedGacha = null;
let cachedMissions = null;
/** Tag text per kind (agent/core/gacha_hint.py); `free` = 「無料」 read without the rest. */
const GACHA_TAG = { free_daily: "1日1回無料", free_ten: "無料10連", free_single: "単発無料", free: "無料" };
const GACHA_KINDS = Object.keys(GACHA_TAG);

const count = (v) => (Number.isInteger(v) && v >= 0 && v <= 1e10 ? v : null);
const iso = (v) => (typeof v === "string" && v.length <= 40 && !Number.isNaN(Date.parse(v)) ? v : null);

/** Tiles from a reading_store snapshot ({readings: {"resource.money": {value: {count}, observed_at, source, stale}}}). */
export function passiveTiles(data) {
  const readings = data && typeof data === "object" && data.readings && typeof data.readings === "object" ? data.readings : {};
  const tiles = [];
  for (const [key, entry] of Object.entries(readings)) {
    const m = KEY.exec(key);
    if (!m || !entry || typeof entry !== "object") continue;
    const item = m[1] === "fes_rights" ? "fes" : m[1];
    const value = count(entry.value?.count);
    const observedAt = iso(entry.observed_at);
    // ジュエル is indicator-only (never spent) but still displayed (home strip and 资源 summary, 2026-10-12).
    if (value === null || !observedAt || (entry.value?.indicator_only === true && item !== "jewel")) continue;
    const tile = { key: item, label: LABELS[item] || item, item, unit: "", value, observed_at: observedAt,
      source: typeof entry.source === "string" ? `${entry.source.slice(0, 40)} · 被动读取` : "被动读取", stale: entry.stale === true };
    if (count(entry.value?.max) !== null) tile.max = entry.value.max;
    tiles.push(tile);
  }
  return tiles;
}

/** Newest tile per item: run readings and passive readings merged (an older passive value never hides a newer run one). */
export function mergeTiles(...lists) {
  const best = new Map();
  for (const tile of lists.flat()) {
    if (!tile || !tile.item) continue;
    const old = best.get(tile.item);
    if (!old || Date.parse(tile.observed_at || 0) > Date.parse(old.observed_at || 0)) best.set(tile.item, tile);
  }
  const extra = lists.flat().filter((t) => t && !t.item);
  return [...best.values(), ...extra];
}

/**
 * Home ガシャ tag read (`gacha.home`, agent/core/gacha_hint.py): {free, kind, observed_at, stale} or null. Only a definite
 * read is kept; `free: false` (plain plate) is kept too, so a later read after the user's draw clears the reminder.
 */
export function passiveGacha(data) {
  const entry = data?.readings?.["gacha.home"];
  const observedAt = iso(entry?.observed_at);
  const state = entry?.value?.state;
  if (!observedAt || (state !== "free" && state !== "no_tag")) return null;
  const kind = state === "free" && GACHA_KINDS.includes(entry.value.kind) ? entry.value.kind : null;
  if (state === "free" && !kind) return null;
  return { free: state === "free", kind, observed_at: observedAt, stale: entry.stale === true };
}

/**
 * 免费ガシャ reminder (pure): the newest Home ガシャ tag read of today's game day says 「無料」. Anything else -- no read,
 * an older day, a plain plate read later (the user drew) -- gives null. Display only.
 */
export function gachaReminder(hint, now = new Date()) {
  if (!hint || hint.free !== true || !GACHA_TAG[hint.kind]) return null;
  const at = Date.parse(hint.observed_at || "");
  if (!Number.isFinite(at) || gameDay(new Date(at)) !== gameDay(now)) return null;
  return { tag: GACHA_TAG[hint.kind], kind: hint.kind, at: hint.observed_at };
}

const MISSION_STATUS = ["claimable", "claimed", "incomplete", "unknown"];
/** Weekly Produce row of the weekly missions (2026-10-22 read: 「フェスアイドルを3人誕生させよう」 0/3). */
const PRODUCE_ROW = /^フェスアイドルを(\d{1,2})人誕生させよう$/;

/** Weekly cycle id: the Sunday (JST, 05:00 boundary) that started the week (agent/core/mission_bulk.weekly_cycle_id). */
export function weekCycle(now = new Date()) {
  const day = gameDay(now);
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - d.getUTCDay());
  return `W-${d.toISOString().slice(0, 10)}`;
}

function missionPage(entry) {
  const observedAt = iso(entry?.observed_at);
  const rows = Array.isArray(entry?.value?.rows) ? entry.value.rows : null;
  if (!observedAt || !rows) return null;
  const clean = rows.slice(0, 20).flatMap((row) => {
    if (!row || typeof row.title !== "string" || !MISSION_STATUS.includes(row.status)) return [];
    const pr = Array.isArray(row.progress) && row.progress.length === 2 && row.progress.every((v) => count(v) !== null)
      && row.progress[0] <= row.progress[1] ? row.progress : null;
    return [{ title: row.title.slice(0, 60), status: row.status, progress: pr }];
  });
  return { rows: clean, observed_at: observedAt, visibleOnly: entry.value.rows_visible_only !== false, stale: entry.stale === true };
}

/** Mission pages from a reading_store snapshot (`missions.mission_daily` / `missions.mission_weekly`); display only. */
export function passiveMissions(data) {
  const r = data?.readings || {};
  return { daily: missionPage(r["missions.mission_daily"]), weekly: missionPage(r["missions.mission_weekly"]) };
}

/**
 * Weekly Produce count from this week's weekly-mission read: {done, target, at} or null. A row read in an earlier week,
 * an unknown status or a missing progress gives null (never zero-filled); a claimed row is complete.
 */
export function weeklyProduceFromMissions(weekly, now = new Date()) {
  if (!weekly || weekCycle(new Date(weekly.observed_at)) !== weekCycle(now)) return null;
  for (const row of weekly.rows) {
    const m = PRODUCE_ROW.exec(row.title);
    if (!m) continue;
    const target = Number(m[1]);
    if (row.status === "claimed" || row.status === "claimable") return { done: target, target, at: weekly.observed_at };
    if (row.status === "incomplete" && row.progress && row.progress[1] === target) return { done: row.progress[0], target, at: weekly.observed_at };
    return null;
  }
  return null;
}

/**
 * 「需要处理」 nudge in the last two game days of the week (Fri/Sat; weekly reset Sunday 05:00 JST) while this week's
 * Produce count is known and short of the target; otherwise null.
 */
export function weeklyProduceNudge(produce, now = new Date()) {
  if (!produce || !Number.isInteger(produce.done) || !Number.isInteger(produce.target) || produce.done >= produce.target) return null;
  const weekday = new Date(`${gameDay(now)}T00:00:00Z`).getUTCDay();
  if (weekday < 5) return null;
  return `本周 Produce 周常还差 ${produce.target - produce.done} 次（周日 05:00 刷新）`;
}

/** Today's daily missions: {done, total, claimable, at, visibleOnly} from a read of the current game day, else null. */
export function dailyMissionSummary(daily, now = new Date()) {
  if (!daily || !daily.rows.length || gameDay(new Date(daily.observed_at)) !== gameDay(now)) return null;
  const done = daily.rows.filter((r) => r.status === "claimable" || r.status === "claimed").length;
  const claimable = daily.rows.filter((r) => r.status === "claimable").length;
  return { done, total: daily.rows.length, claimable, at: daily.observed_at, visibleOnly: daily.visibleOnly };
}

export function passiveMissionPages() {
  return cachedMissions || { daily: null, weekly: null };
}

export function passiveGachaHint() {
  return cachedGacha;
}

export function passiveReadingTiles() {
  return cached || [];
}

export async function loadPassiveReadings({ force = false } = {}) {
  if (cached && !force) return cached;
  try {
    const module = await import(`../game-assets/passive-readings.js${force ? `?t=${Date.now()}` : scanSuffix()}`);
    cached = passiveTiles(module.default);
    cachedGacha = passiveGacha(module.default);
    cachedMissions = passiveMissions(module.default);
  } catch {
    cached = [];
    cachedGacha = null;
    cachedMissions = null;
  }
  return cached;
}

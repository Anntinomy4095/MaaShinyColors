// 资源 tab 「持有卡牌」: one model per card the user owns or a scan recognised, a short
// 「需要你确认」 queue, status chips that double as filters, and a paged thumbnail grid (art, rarity frame, Lv, 凸 stars).
// Writes: (a) the user's own taps — 确认 / 保存 here, 确定 in the reviewed bulk dialogs; (b) since 2026-10-08b, at the user's
// request, high-confidence readings (confidence >= AUTO_MIN) are applied automatically once per scan file
// (autoApplyPlan), never over a value the user set, with per-card and per-batch undo. All go through the
// `onCommit(nextOwned, ids)` callback the panel passes in (its single save call site, resource-panel.js).
// Declined readings ("忽略此识别") and the auto-apply provenance (which values were auto-set, their confidence and scan,
// the previous values for undo) are per-viewer (common.js remember()): the profile protocol has no field for them, so
// when they are lost every saved value simply counts as user-set (the safe side: later scans never overwrite it).
import { h, icon, nextId, jaText, relativeTime, formatTime } from "./dom.js";
import { chip, emptyState, remembered, remember } from "./common.js";
import { choiceChips } from "./choice.js";
import { anyCardArt, idolNumber, badgeIcon, attrIconKey } from "./game-assets.js";
import { normalizeOwned, MAX_LEVEL, MAX_LIMIT_BREAK } from "./team-store.js";

const RARITY_ORDER = ["UR", "SSR", "SR", "R", "N"];
const ATTRS = [["vo", "Vo", "Vocal"], ["da", "Da", "Dance"], ["vi", "Vi", "Visual"]];
const PAGE = 30;
const VIEW_KEY = "sc-ui-resources-cards-v2";   // v2 (2026-10-08b): P 卡 first; the old key remembered S as the default
const DECLINED_KEY = "sc-ui-resources-declined";
const AUTO_KEY = "sc-ui-resources-auto";

/** Readings at or above this confidence are applied without per-card confirmation (= owned_levels.AUTO_ACCEPT_CONFIDENCE). */
export const AUTO_MIN = 0.7;
/** Risk added to a reading from an older recording than its scan (the card may have been trained since; ~1 card a day). */
export const STALE_RISK = 0.03;
export const AUTO_HINT = "识别清晰的卡自动确认，其余待确认";

export const STATUS_TEXT = {
  pending: "待确认", conflict: "与已填写不同", unknown: "未识别", confirmed: "已确认", auto: "已确认",
};

// ------------------------------------------------------------------------------------------------ pure model
const isP = (id) => id.startsWith("1");
const lvOk = (v) => Number.isInteger(v) && v >= 1 && v <= MAX_LEVEL;
const lbOk = (v) => Number.isInteger(v) && v >= 0 && v <= MAX_LIMIT_BREAK;

/** Signature of what a scan said about a card; a declined signature stays declined until a different reading arrives. */
export function readingSignature(entry) {
  const r = entry?.reading;
  if (r) return `lv:${r.level ?? "-"}/${r.limitBreak ?? "-"}@${r.frame || r.date || ""}`;
  return entry?.recognised ? "own" : "";
}

/** A reading's effective confidence: the scan's value (missing -> 0, i.e. low) minus STALE_RISK for an older recording. */
export function readingConfidence(confidence, stale = false) {
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) return 0;
  return Math.round(Math.max(0, Math.min(1, confidence - (stale ? STALE_RISK : 0))) * 1000) / 1000;
}

/** Whether every value the recommender needs is known: S = Lv + 凸, P = 凸. */
export function complete(id, values) {
  return lbOk(values?.limit_break) && (isP(id) || lvOk(values?.level));
}

/**
 * One entry per card in 持有卡牌 ∪ inventory scan ∪ Lv/凸 scan:
 * {id, kind: "p"|"s", owned, recognised, score, reading: {level, limitBreak, stale, date, frame, confidence}|null,
 *  confidence (effective, 0..1), high (>= AUTO_MIN), auto (auto-apply record still in force)|null,
 *  current: {level?, limit_break?}, shown: {level, limit_break}, fill: [], updates: [], conflicts: [], status, art fields…}.
 * status: pending (recognised but not owned, or a reading fills an empty value / updates an auto-set one), conflict
 * (reading ≠ a value the user entered), auto (owned, values set by auto-apply and unchanged since), unknown (owned,
 * values missing, nothing to suggest), confirmed (owned, values complete and no open suggestion).
 * `auto` = the provenance map ({id: record}) from loadAuto().cards: a value is auto-set only while it still equals its
 * record; anything else in the profile counts as user-set and is never overwritten automatically.
 * Declined suggestions (same signature) are ignored; a declined unowned card is left out entirely.
 */
export function cardEntries(owned, { cardScan = null, levelScan = null, declined = {}, auto = {} } = {}) {
  const have = new Set([...(owned?.p || []), ...(owned?.s || [])]);
  const recog = new Map((cardScan?.cards || []).map((c) => [c.id, c]));
  const levels = new Map((levelScan?.cards || []).map((c) => [c.id, c]));
  const ids = [...new Set([...have, ...recog.keys(), ...levels.keys()])].filter((id) => /^[12]\d{9}$/.test(id));
  const out = [];
  for (const id of ids) {
    const lv = levels.get(id) || null;
    const entry = {
      id, kind: isP(id) ? "p" : "s", owned: have.has(id), recognised: recog.has(id) || Boolean(lv),
      score: recog.get(id)?.score ?? null,
      reading: lv ? { level: isP(id) ? null : lv.level, limitBreak: lv.limitBreak, stale: Boolean(lv.stale), date: lv.date || "",
        frame: lv.frame || "", confidence: lv.confidence ?? null } : null,
      current: { ...(owned?.details?.[id] || {}) },
    };
    const isDeclined = Boolean(declined[id]) && declined[id] === readingSignature(entry);
    if (isDeclined) entry.reading = null;
    entry.declined = isDeclined;
    entry.confidence = entry.reading ? readingConfidence(lv.confidence, entry.reading.stale)
      : !isDeclined && recog.has(id) ? readingConfidence(recog.get(id).confidence) : 0;
    entry.high = entry.confidence >= AUTO_MIN;
    const record = entry.owned && autoInForce(auto?.[id], entry.current) ? auto[id] : null;
    entry.auto = record;
    const fill = [], updates = [], conflicts = [];
    if (entry.reading) {
      for (const [key, value] of [["level", entry.reading.level], ["limit_break", entry.reading.limitBreak]]) {
        if (value === null || value === undefined) continue;
        if (entry.current[key] === undefined) fill.push(key);
        else if (entry.current[key] === value) continue;
        else if (record && record[key] === entry.current[key]) updates.push(key);   // auto-set earlier: a later scan may update it
        else conflicts.push(key);
      }
    }
    entry.fill = fill;
    entry.updates = updates;
    entry.conflicts = conflicts;
    if (!entry.owned) {
      if (isDeclined || !entry.recognised) continue;
      entry.status = "pending";
    } else if (conflicts.length) entry.status = "conflict";
    else if (fill.length || updates.length) entry.status = "pending";
    else if (record) entry.status = "auto";
    else entry.status = complete(id, entry.current) ? "confirmed" : "unknown";
    entry.shown = suggested(entry);
    Object.assign(entry, artFields(id));
    out.push(entry);
  }
  return out;
}

/** Values a one-tap 确认 would save: the reading where it has one, else what is already entered. */
export function suggested(entry) {
  const r = entry.reading || {};
  const level = entry.kind === "p" ? null : (r.level ?? entry.current.level ?? null);
  const limitBreak = r.limitBreak ?? entry.current.limit_break ?? null;
  return { level, limit_break: limitBreak };
}

function artFields(id) {
  const art = anyCardArt(id);
  const attrText = art?.idea || art?.specialty || "";
  return {
    name: (art?.idol || art?.idolEn || "").replace(/\s+/g, "") || `卡 ${id}`,
    title: art?.title || "", rarity: art?.rarity || "", src: art?.src || "", idolNo: idolNumber(id),
    attrs: ATTRS.filter(([, , word]) => attrText.includes(word)).map(([key]) => key), attrText,
    search: [art?.idol, art?.idolEn, art?.title, art?.titleEn].filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, ""),
  };
}

/** Counts per status for chips: {all, pending (incl. conflict), unknown, confirmed, auto}. */
export function statusCounts(entries) {
  const c = { all: entries.length, pending: 0, unknown: 0, confirmed: 0, auto: 0 };
  for (const e of entries) {
    if (e.status === "pending" || e.status === "conflict") c.pending += 1;
    else c[e.status] += 1;
  }
  return c;
}

/** The 「需要你确认」 order: conflicts first, then fresh readings, then old recordings / ownership-only suggestions. */
export function queueOf(entries) {
  const rank = (e) => (e.status === "conflict" ? 0 : e.reading && !e.reading.stale ? 1 : e.reading ? 2 : 3);
  return entries.filter((e) => e.status === "pending" || e.status === "conflict")
    .sort((a, b) => rank(a) - rank(b) || a.id.localeCompare(b.id));
}

/** Filter + sort for the grid. view: {kind, status, rarity: [], attr: [], q, sort}. */
export function filterCards(entries, view) {
  const q = String(view.q || "").toLowerCase().replace(/\s+/g, "");
  const list = entries.filter((e) => e.kind === view.kind
    && (view.status === "all" || !view.status || (view.status === "pending" ? e.status === "pending" || e.status === "conflict" : view.status === "confirmed" ? e.status === "confirmed" || e.status === "auto" : e.status === view.status))
    && (!view.rarity?.length || view.rarity.includes(e.rarity))
    && (!view.attr?.length || view.attr.some((a) => e.attrs.includes(a)))
    && (!q || e.search.includes(q) || e.name.includes(q)));
  const rar = (e) => { const i = RARITY_ORDER.indexOf(e.rarity); return i < 0 ? 9 : i; };
  const lv = (e) => (e.shown.level ?? -1) * 10 + (e.shown.limit_break ?? -1);
  const byIdol = (a, b) => a.idolNo.localeCompare(b.idolNo) || a.id.localeCompare(b.id);
  const sorters = {
    idol: byIdol,
    rarity: (a, b) => rar(a) - rar(b) || byIdol(a, b),
    lv: (a, b) => lv(b) - lv(a) || rar(a) - rar(b) || byIdol(a, b),
  };
  return list.sort(sorters[view.sort] || byIdol);
}

/**
 * Owned list after the user's decision on one card: `own` false removes it (and its Lv/凸); otherwise the card is
 * added and its details become `level` / `limit_break` (null = unknown, P cards never get a level).
 */
export function applyCardDecision(owned, id, { own = true, level = null, limit_break: limitBreak = null } = {}) {
  const kind = isP(id) ? "p" : "s";
  const next = { p: [...(owned?.p || [])], s: [...(owned?.s || [])], details: { ...(owned?.details || {}) } };
  const set = new Set(next[kind]);
  if (own) set.add(id); else set.delete(id);
  next[kind] = [...set];
  delete next.details[id];
  if (own) {
    const item = {};
    if (kind === "s" && lvOk(level)) item.level = level;
    if (lbOk(limitBreak)) item.limit_break = limitBreak;
    if (Object.keys(item).length) next.details[id] = item;
  }
  const clean = normalizeOwned(next);
  // This is a full edit result: an empty object means the user explicitly cleared the last Lv/凸 entry.
  clean.details ??= {};
  return clean;
}

export function loadDeclined() {
  const value = remembered(DECLINED_KEY, {});
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function declineEntry(entry) {
  const map = loadDeclined();
  map[entry.id] = readingSignature(entry);
  remember(DECLINED_KEY, map);
}

// ------------------------------------------------------------------------------------------------ auto-apply
// Provenance store (per viewer, common.js remember): {v: 1, scans: [scanKey…], last: scanKey, cards: {id: record},
// undone: {id: readingSignature}}. record = {source: "auto", level?, limit_break?, added, prev: {owned, level?,
// limit_break?}, confidence, scan, at, sig}. A copy is kept in memory so a session without storage still has undo.
let autoMemory = null;
const emptyAuto = () => ({ v: 1, scans: [], last: "", cards: {}, undone: {} });
const isMap = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const CARD = /^[12]\d{9}$/;

export function loadAuto() {
  const raw = remembered(AUTO_KEY, null) || autoMemory;
  if (!isMap(raw) || raw.v !== 1) return emptyAuto();
  const cards = {};
  for (const [id, r] of Object.entries(isMap(raw.cards) ? raw.cards : {}).slice(0, 3000)) {
    if (!CARD.test(id) || !isMap(r) || r.source !== "auto") continue;
    const prev = isMap(r.prev) ? r.prev : {};
    const rec = { source: "auto", added: r.added === true, prev: { owned: prev.owned === true },
      confidence: typeof r.confidence === "number" ? r.confidence : null, scan: String(r.scan || "").slice(0, 120),
      at: String(r.at || "").slice(0, 40), sig: String(r.sig || "").slice(0, 300) };
    if (lvOk(r.level)) rec.level = r.level;
    if (lbOk(r.limit_break)) rec.limit_break = r.limit_break;
    if (lvOk(prev.level)) rec.prev.level = prev.level;
    if (lbOk(prev.limit_break)) rec.prev.limit_break = prev.limit_break;
    cards[id] = rec;
  }
  const undone = Object.fromEntries(Object.entries(isMap(raw.undone) ? raw.undone : {})
    .filter(([id, sig]) => CARD.test(id) && typeof sig === "string").slice(0, 3000));
  const scans = Array.isArray(raw.scans) ? raw.scans.filter((k) => typeof k === "string").slice(-20) : [];
  return { v: 1, scans, last: typeof raw.last === "string" ? raw.last : "", cards, undone };
}

export function saveAuto(store) {
  autoMemory = store;
  remember(AUTO_KEY, store);
}

/** An auto record is in force while every value it set is still what the profile holds. */
function autoInForce(record, current) {
  if (!record) return false;
  return ["level", "limit_break"].every((k) => record[k] === undefined || record[k] === current?.[k]);
}

/**
 * One key per pair of scan files AND threshold: auto-apply runs once per new scan (a re-render or reload of the same files
 * does nothing), but lowering/raising AUTO_MIN gives a new key, so readings the old threshold skipped are re-evaluated
 * (2026-10-10: 0.85 -> 0.70 never re-ran for files that had been handled with 0.85).
 */
export function scanKey({ cardScan = null, levelScan = null } = {}) {
  const parts = [levelScan?.generatedAt || "", cardScan?.generatedAt || ""];
  return parts.some(Boolean) ? `lv:${parts[0]}|own:${parts[1]}|t:${AUTO_MIN}` : "";
}

/** Tiles the scans could not tie to a card (no identity): shown as a small note, never as something to confirm. */
export function unrecognisedTiles({ cardScan = null, levelScan = null } = {}) {
  const n = [levelScan?.rejected, cardScan?.rejected].find((v) => Number.isInteger(v) && v > 0);
  return n || 0;
}

/**
 * Auto-apply plan for newly loaded scan files: every high-confidence suggestion (status pending: adds a recognised card,
 * fills an empty value or updates a value an earlier auto-apply set) that the user did not undo or decline. Conflicts with
 * user-set values, low-confidence and unrecognised cards are left for 「需要你确认」. Idempotent: a scan key that was
 * already handled returns `changed: false` and the same store. Returns {next, store, ids, changed}.
 */
export function autoApplyPlan(owned, { cardScan = null, levelScan = null, declined = {} } = {}, store = emptyAuto(), { now = Date.now() } = {}) {
  const key = scanKey({ cardScan, levelScan });
  if (!key || store.scans.includes(key)) return { next: owned, store, ids: [], changed: false };
  const entries = cardEntries(owned, { cardScan, levelScan, declined, auto: store.cards });
  let next = normalizeOwned(owned);
  const cards = { ...store.cards };
  const ids = [];
  const at = new Date(now).toISOString();
  for (const entry of entries) {
    if (entry.status !== "pending" || !entry.high || entry.declined) continue;
    const sig = readingSignature(entry);
    if (store.undone[entry.id] && store.undone[entry.id] === sig) continue;
    const after = applyCardDecision(next, entry.id, { own: true, ...suggested(entry) });
    const saved = after.details?.[entry.id] || {};
    // An update of an earlier auto value keeps that record's "before" so undo returns to what the user had.
    const before = entry.auto ? entry.auto.prev
      : { owned: entry.owned, ...(entry.current.level !== undefined ? { level: entry.current.level } : {}),
        ...(entry.current.limit_break !== undefined ? { limit_break: entry.current.limit_break } : {}) };
    cards[entry.id] = { source: "auto", ...saved, added: !before.owned, prev: before, confidence: entry.confidence, scan: key, at, sig };
    next = after;
    ids.push(entry.id);
  }
  const out = { v: 1, scans: [...store.scans, key].slice(-20), last: ids.length ? key : store.last, cards, undone: store.undone };
  return { next, store: out, ids: ids.sort(), changed: ids.length > 0 };
}

/** Ids whose auto values from the latest batch are still in force (the 「撤销本次自动采用」 set). */
export function lastBatch(owned, store) {
  if (!store?.last) return [];
  const details = owned?.details || {};
  const have = new Set([...(owned?.p || []), ...(owned?.s || [])]);
  return Object.entries(store.cards).filter(([id, r]) => r.scan === store.last && have.has(id) && autoInForce(r, details[id] || {}))
    .map(([id]) => id).sort();
}

/**
 * Undo auto-applied values: each card whose auto values are still in force gets its previous state back (a card the
 * auto-apply added is removed again). The reading is remembered as undone so the same reading is never re-applied; the
 * card then waits in 「需要你确认」. A card the user changed since is left alone (its record is just dropped).
 * Returns {next, store, ids (cards actually restored)}.
 */
export function undoAuto(owned, store, ids) {
  let next = normalizeOwned(owned);
  const cards = { ...store.cards };
  const undone = { ...store.undone };
  const done = [];
  for (const id of ids) {
    const r = cards[id];
    if (!r) continue;
    const have = new Set([...(next.p || []), ...(next.s || [])]);
    if (have.has(id) && autoInForce(r, next.details?.[id] || {})) {
      next = r.prev.owned
        ? applyCardDecision(next, id, { own: true, level: r.prev.level ?? null, limit_break: r.prev.limit_break ?? null })
        : applyCardDecision(next, id, { own: false });
      done.push(id);
    }
    if (r.sig) undone[id] = r.sig;
    delete cards[id];
  }
  return { next, store: { ...store, cards, undone }, ids: done };
}

/** The user decided these cards themselves (确认 / 保存 / 移出 / bulk 确定): their values count as user-set from now on. */
export function forgetAuto(store, ids) {
  const list = (ids || []).filter((id) => store.cards[id]);
  if (!list.length) return store;
  const cards = { ...store.cards };
  for (const id of list) delete cards[id];
  return { ...store, cards };
}

// ------------------------------------------------------------------------------------------------ view pieces
const fmtLv = (e, v = e.shown) => (e.kind === "p" ? "" : v.level != null ? `Lv${v.level}` : "Lv?");
const fmtLb = (v) => (v.limit_break != null ? `${v.limit_break}凸` : "凸?");
export const valuesText = (e, v = e.shown) => [fmtLv(e, v), fmtLb(v)].filter(Boolean).join(" · ");

/** Four stars, `n` filled: gold for 1–3, purple when fully broken (as in the game). */
export function stars(n) {
  if (!lbOk(n)) return null;
  return h("span", { class: `lb-stars${n === MAX_LIMIT_BREAK ? " is-max" : ""}`, role: "img", "aria-label": `${n}凸`, title: `${n}凸` },
    Array.from({ length: MAX_LIMIT_BREAK }, (_, i) => h("span", { class: `lb-star${i < n ? " is-on" : ""}`, "aria-hidden": "true", text: "★" })));
}

function thumb(entry, size) {
  if (entry.src) {
    return h("img", { class: "rc-img", src: entry.src, alt: "", width: String(size), height: String(size), draggable: "false",
      loading: "lazy", decoding: "async" });
  }
  return h("span", { class: "rc-img rc-placeholder", "aria-hidden": "true" }, icon("card", { size: 20 }));
}

function rarityClass(entry) {
  return entry.rarity ? ` rar-${entry.rarity.toLowerCase()}` : "";
}

/** Grid tile: art in a rarity frame, Lv + stars bar, status corner mark, name below. */
export function cardTile(entry, { onOpen } = {}) {
  const v = entry.shown;
  const bar = v.level != null || v.limit_break != null
    ? h("span", { class: "rc-bar" }, entry.kind === "s" ? h("span", { class: "rc-lv num", text: v.level != null ? `Lv${v.level}` : "Lv?" }) : h("span"),
      stars(v.limit_break) || h("span", { class: "rc-lv", text: "凸?" }))
    : null;
  const mark = entry.status === "confirmed" ? null
    : h("span", { class: `rc-mark is-${entry.status}`, "aria-hidden": "true",
      text: entry.status === "unknown" ? "?" : entry.status === "conflict" ? "!" : entry.status === "auto" ? "✓" : "" });
  const conf = entry.status === "auto" && entry.auto?.confidence != null ? `（可信度 ${Math.round(entry.auto.confidence * 100)}%）` : "";
  const label = `${entry.name}${entry.title ? `【${entry.title}】` : ""} · ${entry.rarity || "稀有度未知"} · ${valuesText(entry)} · ${STATUS_TEXT[entry.status]}${conf}`;
  return h("button", { type: "button", class: `rc-tile is-${entry.status}`, title: label, "aria-label": label,
    dataset: { card: entry.id, focusKey: `rc-${entry.id}` }, onclick: () => onOpen?.(entry) },
  h("span", { class: `rc-frame${rarityClass(entry)}` }, thumb(entry, 64), bar, mark),
  h("span", { class: "rc-name" }, jaText(entry.name)));
}

/** One 「需要你确认」 row: what was recognised (and what is entered, if different) + 确认 / 修改. */
export function queueRow(entry, { onConfirm, onEdit } = {}) {
  const r = entry.reading;
  const what = !entry.owned
    ? (r ? `识别到持有 · ${valuesText(entry)}` : `识别到持有 · 匹配 ${Math.round((entry.score || 0) * 100)}%`)
    : entry.status === "conflict" ? `已填 ${valuesText(entry, { level: entry.current.level ?? null, limit_break: entry.current.limit_break ?? null })} → 识别 ${valuesText(entry)}`
      : `识别 ${valuesText(entry)}`;
  const confirmLabel = entry.status === "conflict" ? "用识别值" : "确认";
  return h("li", { class: `rq-row is-${entry.status}`, dataset: { card: entry.id } },
    h("span", { class: `rc-frame rq-thumb${rarityClass(entry)}` }, thumb(entry, 40)),
    h("span", { class: "rq-main" },
      h("span", { class: "rq-name" }, jaText(entry.name), entry.title ? h("span", { class: "rq-title" }, jaText(` ${entry.title}`)) : null),
      h("span", { class: "rq-what" }, h("span", { text: what }),
        r?.stale ? h("span", { class: "rq-tag is-stale", title: "来自较早的扫描，Lv/凸 可能已变化" }, `旧扫描 ${r.date ? r.date.slice(5) : ""}`.trim()) : null,
        entry.status !== "conflict" && (r || entry.recognised) ? h("span", { class: "rq-tag is-conf", title: "识别可信度：90% 及以上的自动确认" },
          entry.confidence > 0 ? `可信度 ${Math.round(entry.confidence * 100)}%` : "可信度未知") : null)),
    h("span", { class: "rq-actions" },
      h("button", { type: "button", class: "btn btn-small btn-primary", dataset: { focusKey: `rq-ok-${entry.id}` },
        title: `${confirmLabel}：保存 ${entry.name} 的持有与 ${valuesText(entry)}`, onclick: () => onConfirm?.(entry) }, confirmLabel),
      h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: `rq-edit-${entry.id}` }, onclick: () => onEdit?.(entry) }, "修改")));
}

// ------------------------------------------------------------------------------------------------ section
function defaultView(entries) {
  const saved = remembered(VIEW_KEY, {}) || {};
  const hasP = entries.some((e) => e.kind === "p");
  return {
    kind: saved.kind === "p" || saved.kind === "s" ? saved.kind : hasP ? "p" : "s",
    sort: ["idol", "rarity", "lv"].includes(saved.sort) ? saved.sort : "idol",
    status: "all", rarity: [], attr: [], q: "", limit: PAGE,
  };
}
let view = null;  // kept across panel re-renders (module scope), like the store keeps open drawers

/**
 * 「持有卡牌」 body. `onCommit(nextOwned, ids)` saves the user's decision on `ids` (the panel's single save path);
 * `onDeclined()` re-renders after a reading was hidden for this viewer; `onBulkOwned` / `onBulkLevels` open the reviewed
 * bulk dialogs; `onUndoAuto(ids)` reverts auto-applied cards; `autoBatch` = ids of the latest auto-apply still in force;
 * `footer` holds the quiet scan/reload actions.
 */
export function cardsView(entries, { onCommit, onDeclined, onBulkOwned, onBulkLevels, onUndoAuto, autoBatch = [], owned, footer = null, rejected = 0, now = Date.now() } = {}) {
  if (!view && entries.length) view = defaultView(entries);   // not before the scan files have loaded (kind default)
  const root = h("div", { class: "rcards" });
  const draw = () => {
    root.replaceChildren(...build().filter(Boolean));
  };
  const set = (patch, keepLimit = false) => {
    Object.assign(view, patch);
    if (!keepLimit) view.limit = PAGE;
    remember(VIEW_KEY, { kind: view.kind, sort: view.sort });
    draw();
    if (patch.q !== undefined) {   // keep typing in the search box after the redraw
      const box = root.querySelector?.(".rc-search");
      if (box) { box.focus(); box.setSelectionRange?.(box.value.length, box.value.length); }
    }
  };
  const confirm = (entry) => onCommit?.(applyCardDecision(owned, entry.id, { own: true, ...suggested(entry) }), [entry.id]);
  const edit = (entry) => openCardSheet(entry, { owned, onCommit: (next) => onCommit?.(next, [entry.id]),
    onDecline: () => { declineEntry(entry); onDeclined?.(); }, onUndoAuto: () => onUndoAuto?.([entry.id]), now });

  function build() {
    if (!entries.length || !view) {
      return [emptyState({ title: "暂无卡牌" }), footer];
    }
    const mine = entries.filter((e) => e.kind === view.kind);
    const counts = statusCounts(mine);
    const kinds = ["p", "s"].map((k) => [k, entries.filter((e) => e.kind === k).length]);
    const queue = queueOf(mine);
    const list = filterCards(entries, view);
    const rarities = RARITY_ORDER.filter((r) => mine.some((e) => e.rarity === r));

    const kindSeg = h("div", { class: "segmented rc-kind", role: "group", "aria-label": "卡牌类型" },
      kinds.map(([k, n]) => h("button", { type: "button", class: `segment${view.kind === k ? " is-selected" : ""}`, "aria-pressed": String(view.kind === k),
        dataset: { focusKey: `rc-kind-${k}` }, onclick: () => set({ kind: k, status: "all", rarity: [], attr: [] }) },
      `${k === "s" ? "S 卡" : "P 卡"} ${n}`)));

    const statusRow = h("div", { class: "chip-row", role: "group", "aria-label": "按状态筛选" },
      chip("全部", { pressed: view.status === "all", count: counts.all, focusKey: "rc-st-all", onclick: () => set({ status: "all" }) }),
      chip("待确认", { pressed: view.status === "pending", count: counts.pending, dot: true, tone: "accent", focusKey: "rc-st-pending",
        title: "识别到但还没确认的持有或 Lv/凸", onclick: () => set({ status: view.status === "pending" ? "all" : "pending" }) }),
      chip("未识别", { pressed: view.status === "unknown", count: counts.unknown, dot: true, tone: "muted", focusKey: "rc-st-unknown",
        title: "已持有，但 Lv/凸 还没有识别或填写", onclick: () => set({ status: view.status === "unknown" ? "all" : "unknown" }) }),
      chip("已确认", { pressed: view.status === "confirmed", count: counts.confirmed + counts.auto, dot: true, tone: "success", focusKey: "rc-st-confirmed",
        onclick: () => set({ status: view.status === "confirmed" ? "all" : "confirmed" }) }),
      null);

    const kindBatch = autoBatch.filter((id) => (id.startsWith("1") ? "p" : "s") === view.kind);
    const autoBox = autoBatch.length
      ? h("div", { class: "rc-auto", role: "status" },
        h("span", { class: "rc-auto-mark", "aria-hidden": "true", text: "✓" }),
        h("span", { class: "rc-auto-text" }, `本次已确认 ${autoBatch.length} 张`,
          kindBatch.length !== autoBatch.length ? h("span", { class: "muted", text: `（${view.kind === "p" ? "P" : "S"} 卡 ${kindBatch.length}）` }) : null),
        h("span", { class: "grow" }),
        h("button", { type: "button", class: "btn btn-text btn-small", dataset: { focusKey: "rc-auto-undo" },
          title: `撤销这 ${autoBatch.length} 张的确认（回到「待确认」）`, onclick: () => onUndoAuto?.(autoBatch) }, "撤销本次确认"))
      : null;
    const hint = rejected ? h("p", { class: "rc-hint muted small", title: `${AUTO_HINT}；未识别的格可重新扫描` },
      h("span", { class: "rc-rejected", text: `${rejected} 格未识别` })) : null;

    const queueBox = queue.length && view.status !== "confirmed" && view.status !== "unknown" && view.status !== "auto"
      ? h("div", { class: "rq", role: "region", "aria-label": "待确认" },
        h("div", { class: "rq-head" }, h("span", { class: "rq-title-main", text: "待确认" }),
          h("span", { class: "rq-count num muted small", text: `${queue.length} 张` }),
          h("span", { class: "grow" }),
          queue.length > 3 && view.status !== "pending" ? h("button", { type: "button", class: "btn btn-text btn-small", dataset: { focusKey: "rq-all" },
            onclick: () => set({ status: "pending" }) }, "逐张查看") : null),
        h("ul", { class: "rq-list" }, queue.slice(0, 3).map((e) => queueRow(e, { onConfirm: confirm, onEdit: edit }))),
        h("div", { class: "rq-foot" },
          h("span", { class: "muted small", text: "批量：" }),
          h("button", { type: "button", class: "btn btn-text btn-small", dataset: { focusKey: "holdings-import" }, title: "打开识别结果清单，逐张勾选后按「确定」保存", onclick: () => onBulkOwned?.() }, "导入持有卡牌"),
          h("button", { type: "button", class: "btn btn-text btn-small", dataset: { focusKey: "holdings-import-levels" }, title: "打开 Lv/凸 识别清单，核对后按「确定」保存", onclick: () => onBulkLevels?.() }, "导入 Lv/凸"),
          h("span", { class: "grow" })))
      : null;

    const search = h("input", { type: "search", class: "input input-compact rc-search", placeholder: "搜索偶像或卡名", "aria-label": "搜索偶像或卡名",
      dataset: { focusKey: "rc-search" }, props: { value: view.q }, oninput: (e) => set({ q: e.target.value }) });
    const sort = choiceChips({ label: "排序", value: view.sort, focusKey: "rc-sort", cls: "rc-sort", onPick: (v) => set({ sort: v }),
      options: [["idol", "偶像", "按偶像排序"], ["rarity", "稀有度", "按稀有度排序"], ["lv", "Lv", "按 Lv/凸排序"]] });
    const toggle = (key, value) => set({ [key]: view[key].includes(value) ? view[key].filter((x) => x !== value) : [...view[key], value] });
    const facetRow = h("div", { class: "chip-row chip-row-quiet", role: "group", "aria-label": "按稀有度与属性筛选" },
      rarities.map((r) => chip(r, { pressed: view.rarity.includes(r), focusKey: `rc-rar-${r}`, lead: h("span", { class: `rar-swatch rar-${r.toLowerCase()}`, "aria-hidden": "true" }),
        onclick: () => toggle("rarity", r) })),
      rarities.length ? h("span", { class: "chip-sep", "aria-hidden": "true" }) : null,
      ATTRS.map(([key, short, word]) => chip(short, { pressed: view.attr.includes(key), focusKey: `rc-attr-${key}`, title: word,
        lead: badgeIcon(attrIconKey(key), { size: 14, decorative: true, cls: "chip-badge" }), onclick: () => toggle("attr", key) })));

    const shown = list.slice(0, view.limit);
    const grid = shown.length
      ? h("div", { class: "rc-grid", role: "list", "aria-label": `${view.kind === "p" ? "P" : "S"} 卡 ${list.length} 张` },
        shown.map((e) => cardTile(e, { onOpen: edit })))
      : emptyState({ title: "没有符合条件的卡", action: h("button", { type: "button", class: "btn btn-small", onclick: () => set({ status: "all", rarity: [], attr: [], q: "" }) }, "清除筛选") });
    const more = list.length > shown.length
      ? h("button", { type: "button", class: "btn rc-more", dataset: { focusKey: "rc-more" }, onclick: () => set({ limit: view.limit + PAGE * 2 }, true) },
        `再显示 ${Math.min(PAGE * 2, list.length - shown.length)} 张（共 ${list.length}）`)
      : null;

    return [kindSeg, statusRow, hint, autoBox, queueBox,
      h("div", { class: "rc-toolbar" }, h("div", { class: "search-wrap grow" }, icon("search", { size: 16 }), search), sort),
      facetRow, grid, more, footer];
  }
  draw();
  return root;
}

// ------------------------------------------------------------------------------------------------ card sheet
/**
 * Card sheet dialog: big art, what was recognised (source frame, date) vs what is saved, and an editor (Lv box for S,
 * 凸 0–4 segments). 保存 / 确认 writes through onCommit; 移出持有 removes; 忽略此识别 hides this reading for this viewer.
 */
export function openCardSheet(entry, { owned, onCommit, onDecline, onUndoAuto, now = Date.now() } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const titleId = nextId("sheet");
  const start = entry.status === "confirmed" || entry.status === "unknown" || entry.status === "auto"
    ? { level: entry.current.level ?? null, limit_break: entry.current.limit_break ?? null } : suggested(entry);
  let lb = start.limit_break;
  const dialog = h("dialog", { class: "dialog rc-sheet", "aria-labelledby": titleId });
  const close = () => { dialog.close(); dialog.remove(); };
  const err = h("span", { class: "rc-err small", role: "status" });
  const level = entry.kind === "s"
    ? h("input", { class: "input rc-lv-input num", type: "number", min: "1", max: String(MAX_LEVEL), inputmode: "numeric", placeholder: "未知",
      "aria-label": "Lv（空白=未知）", props: { value: start.level ?? "" } })
    : null;
  const segs = h("div", { class: "segmented rc-lb-seg", role: "radiogroup", "aria-label": "凸" });
  const paint = () => {
    segs.replaceChildren(...[null, 0, 1, 2, 3, 4].map((v) => h("button", { type: "button", role: "radio", class: `segment${lb === v ? " is-selected" : ""}`,
      "aria-checked": String(lb === v), onclick: () => { lb = v; paint(); } }, v === null ? "未知" : `${v}`)));
  };
  paint();
  const save = () => {
    const raw = level ? String(level.value).trim() : "";
    const lv = raw === "" ? null : Number(raw);
    if (level && raw !== "" && !lvOk(lv)) { err.textContent = `Lv 需为 1–${MAX_LEVEL} 的整数，或留空表示未知`; return; }
    onCommit?.(applyCardDecision(owned, entry.id, { own: true, level: lv, limit_break: lb }));
    close();
  };
  const r = entry.reading;
  const facts = [
    ["状态", entry.status === "auto" && entry.auto ? `${STATUS_TEXT.auto}${entry.auto.confidence != null ? `（可信度 ${Math.round(entry.auto.confidence * 100)}%）` : ""}${entry.auto.at ? ` · ${formatTime(entry.auto.at)}` : ""}` : STATUS_TEXT[entry.status]],
    r ? ["识别", `${valuesText(entry, { level: r.level, limit_break: r.limitBreak })}${r.stale ? "（旧扫描）" : ""}`] : entry.score != null ? ["识别", `持有 · 匹配 ${Math.round(entry.score * 100)}%`] : null,
    r ? ["来源", `${r.frame.split("/").pop() || "—"}${r.date ? ` · 扫描 ${r.date}` : ""} · 可信度 ${entry.confidence > 0 ? `${Math.round(entry.confidence * 100)}%` : "未知"}`] : null,
    entry.owned ? ["已保存", Object.keys(entry.current).length ? valuesText(entry, { level: entry.current.level ?? null, limit_break: entry.current.limit_break ?? null }) : "持有 · Lv/凸 未填"] : ["已保存", "未加入持有卡牌"],
  ].filter(Boolean);
  const primary = entry.owned && entry.status !== "pending" && entry.status !== "conflict" ? "保存" : "确认";
  const undo = entry.status === "auto" && onUndoAuto
    ? h("button", { type: "button", class: "btn btn-text", title: entry.auto?.added ? "撤销：从「持有卡牌」移除这张自动加入的卡" : "撤销：恢复确认前的 Lv/凸",
      onclick: () => { onUndoAuto(); close(); } }, "撤销确认")
    : null;
  dialog.append(
    h("div", { class: "rc-sheet-head" },
      h("span", { class: `rc-frame rc-sheet-art${rarityClass(entry)}` }, thumb(entry, 96)),
      h("div", { class: "rc-sheet-id" },
        h("h2", { id: titleId, class: "dialog-title" }, jaText(entry.name)),
        entry.title ? h("div", { class: "muted small" }, jaText(entry.title)) : null,
        h("div", { class: "rc-sheet-tags" },
          entry.rarity ? h("span", { class: `rc-tag rar-text rar-${entry.rarity.toLowerCase()}`, text: entry.rarity }) : null,
          h("span", { class: "rc-tag", text: entry.kind === "p" ? "P 卡" : "S 卡" }),
          entry.attrText ? h("span", { class: "rc-tag", text: entry.attrText }) : null)),
      h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", title: "关闭", onclick: close }, icon("close", { size: 16 }))),
    h("dl", { class: "kv rc-facts" }, facts.map(([k, v]) => [h("dt", { text: k }), h("dd", {}, jaText(v))])),
    h("div", { class: "rc-edit" },
      level ? h("label", { class: "rc-field" }, h("span", { class: "small muted", text: "Lv" }), level) : null,
      h("div", { class: "rc-field" }, h("span", { class: "small muted", text: "凸" }), segs)),
    err,
    h("div", { class: "dialog-actions rc-sheet-actions" },
      undo,
      entry.owned && !undo ? h("button", { type: "button", class: "btn btn-text", title: "从「持有卡牌」移除这张卡及其 Lv/凸",
        onclick: () => { onCommit?.(applyCardDecision(owned, entry.id, { own: false })); close(); } }, "移出持有") : null,
      (entry.status === "pending" || entry.status === "conflict") ? h("button", { type: "button", class: "btn btn-text",
        title: "不采用这次识别（有新结果时会再次出现）", onclick: () => { onDecline?.(); close(); } }, "忽略此识别") : null,
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-primary", onclick: save }, primary)));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  level?.focus?.();
  return dialog;
}

/** U18: counters like 体力 move within hours, so a reading is flagged after 6 h (was 1 day). */
export const STALE_MS = 6 * 3600 * 1000;
export const STALE_NOTE = "（超过 6 小时，可能已变化）";

/** Freshness of an observation: {text: "3 天前", stale: older than STALE_MS, title: exact time}. */
export function freshness(at, now = Date.now()) {
  if (!at) return { text: "", stale: false, title: "" };
  const t = Date.parse(at);
  if (Number.isNaN(t)) return { text: "", stale: false, title: "" };
  return { text: relativeTime(at, now), stale: now - t > STALE_MS, title: formatTime(at) };
}

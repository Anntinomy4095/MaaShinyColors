// 资源 tab (redesign 2026-10-08): summary first, details on demand.
//   1. 需要核对 — pending / uncertain spend ledger entries (only when there are any).
//   2. Summary strip — マニー / Fes 参加权 / 体力 / 铜训练券: value, max, freshness (stale > 1 day gets a warning dot).
//   3. Fold cards with counts in the header: 道具, プロデュースアイテム (icon tiles, count overlay; 未观测 never 0),
//      持有卡牌 (resource-cards.js: 需要你确认 queue, status chips, paged thumbnail grid), 最近运行读数.
// Every value shown was observed (engine readings or the offline scan files); nothing is inferred or zero-filled.
// Owned cards are saved after an explicit tap (确认 / 保存 here, 确定 in the reviewed bulk dialogs) or — at the user's
// request (2026-10-08b) — automatically for high-confidence readings, once per newly loaded scan file, never over a value
// the user set, with undo (resource-cards.js autoApplyPlan / undoAuto). All go through persistOwned() — the one save
// call site in this file. Icons: one look for the page — the pinned in-game tile wherever one exists, in the same square
// well (resIcon); the line / glyph plates remain only as the fallback for packaged builds without private assets.
import { h, jaText, clear, formatNumber, formatTime } from "./dom.js";
import { statusPill, toast, foldSection, sectionDeck } from "./common.js";
import { taskName, NEVER_USE_ITEMS } from "./display.js";
import { gameDay } from "./store.js";
import { outcomeText } from "./outcomes.js";
import { ledgerKey, openLedger } from "./acks.js";
import { itemIcon, itemSpec, anyCardArt, readingItemKey, gameAssets, P_ITEM_NAMES } from "./game-assets.js";
import { cleanScan, openInventoryImport } from "./team-import.js";
import { loadLevelScan, cleanLevelScan, openLevelImport } from "./team-levels-import.js";
import { ownedCards, saveOwnedCards, normalizeOwned } from "./team-store.js";
import { cardScanGate, gateTitle, openCardScanConfirm, cardScanProgress, cardScanResultNote } from "./card-scan.js";
import { scanSuffix, bumpScanVersion } from "./scan-version.js";
import { cardLibraryModel, cardLibrarySummary, cardLibraryView } from "./card-library.js";
import { cardEntries, cardsView, unrecognisedTiles, loadDeclined, freshness, STALE_NOTE, loadAuto, saveAuto, autoApplyPlan, undoAuto, forgetAuto, lastBatch } from "./resource-cards.js";

const KIND_LABEL = { shop: "商店", training: "训练", grade: "Grade", match: "Match", business: "营业", mission_status: "每日任务", diagnostics: "诊断" };
const STYLE_HREF = "styles/resource-inventory.css";

/**
 * U3: reading tiles from engine run readings (readings.update events / run.history records). Only values the engine
 * actually observed become tiles; nothing is inferred or zero-filled. Falls back to the given list (preview fixtures).
 */
export function readingTiles(runReadings, fallback = []) {
  let money = null;
  let rights = null;
  for (const entry of runReadings || []) {
    const r = entry?.readings || {};
    const source = `${KIND_LABEL[entry.kind] || entry.kind} · ${entry.run_id || ""}`.trim();
    for (const obs of Array.isArray(r.observations) ? r.observations : []) {
      const m = [obs.money_after, obs.money, obs.money_before].find((v) => Number.isInteger(v));
      if (m != null) money = { value: m, observed_at: entry.ts, source };
      const pair = [obs.rights_after, obs.rights, obs.rights_before].find((v) => Array.isArray(v) && v.length === 2);
      if (pair) rights = { value: pair[0], max: pair[1], observed_at: entry.ts, source };
    }
  }
  const tiles = [];
  if (money) tiles.push({ key: "money", label: "マニー", item: "money", unit: "", ...money });
  if (rights) tiles.push({ key: "fes_rights", label: "Fes 参加权", item: "fes", unit: "", ...rights });
  return tiles.length ? tiles : fallback || [];
}

// ------------------------------------------------------------------------------------------------ 识别持有 (holdings)
// Account items / counters always listed (未观测 until a reading exists). Labels for keys game-assets.js lacks.
export const HOLDING_ITEMS = ["money", "stamina", "fes", "bronze_ticket", "silver_ticket", "gold_ticket", "platinum_ticket",
  "lv50_ticket", "skip_ticket", "fes_ticket", "stamina_drink", "handmade_gift", "jewel"];
const EXTRA_LABELS = {
  stamina: { label: "体力（プロデュース）", icon: "dot" },
  lv50_ticket: { label: "Lv50 训练券", icon: "ticket" },
  fes_ticket: { label: "フェスエントリーチケット", icon: "ticket" },
  stamina_drink: { label: "リカバリーソーダ30（体力回复）", icon: "dot" },
  stamina_drink_60: { label: "リカバリーソーダ60", icon: "dot" },          // アイテム一覧 (agent/core/item_list.py)
  stamina_drink_100: { label: "リカバリーソーダ100", icon: "dot" },
  fes_ticket_5: { label: "フェスエントリーチケット5", icon: "ticket" },
  continue_ticket: { label: "コンティニューチケット", icon: "ticket" },
  handmade_gift: { label: "お手製差し入れ", icon: "dot" },
};
const ITEM_ID = /^[a-z][a-z0-9_]{0,40}$/;
const ICON_SRC = /^game-assets\/item-scan\/[a-z0-9_]{1,48}\.png$/;
const FRAME = /^[\w./-]{1,200}$/;
const CARD_ID = /^[12]\d{9}$/;

function iso(value) {
  return typeof value === "string" && value.length <= 40 && !Number.isNaN(Date.parse(value)) ? value : null;
}
function count(value) {
  return Number.isInteger(value) && value >= 0 && value <= 1e10 ? value : null;
}
function unit(value) {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
}
function text(value, max = 60) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

/** Validated tools/item_inventory_scan.py output: {items: [...], cards: {id: {frame, observedAt, score}}, generatedAt}. */
export function cleanItemScan(data) {
  const out = { items: [], cards: {}, generatedAt: "" };
  if (!data || typeof data !== "object" || data.version !== 1) return out;
  const seen = new Set();
  for (const item of Array.isArray(data.items) ? data.items.slice(0, 500) : []) {
    const id = item?.id;
    if (typeof id !== "string" || !ITEM_ID.test(id) || seen.has(id) || count(item.count) === null) continue;
    seen.add(id);
    out.items.push({
      id, count: item.count, max: count(item.max), plus: count(item.plus), confidence: unit(item.confidence),
      frame: typeof item.frame === "string" && FRAME.test(item.frame) ? item.frame : "", observedAt: iso(item.observed_at),
      name: text(item.name), look: text(item.look), icon: typeof item.icon === "string" && ICON_SRC.test(item.icon) ? item.icon : null,
      produce: item.kind === "produce_item", indicatorOnly: item.indicator_only === true,
    });
  }
  for (const card of Array.isArray(data.cards) ? data.cards.slice(0, 3000) : []) {
    if (typeof card?.id !== "string" || !CARD_ID.test(card.id)) continue;
    out.cards[card.id] = { frame: typeof card.frame === "string" && FRAME.test(card.frame) ? card.frame : "",
      observedAt: iso(card.observed_at), score: unit(card.score) };
  }
  out.generatedAt = iso(data.generated_at) || "";
  return out;
}

/** Item readings carried by engine runs: money / Fes rights observations and (future hook) `readings.items`. */
export function engineHoldings(runReadings) {
  const out = {};
  const put = (id, value) => {
    const prev = out[id];
    if (!prev || (Date.parse(value.observedAt || 0) || 0) >= (Date.parse(prev.observedAt || 0) || 0)) out[id] = value;
  };
  for (const entry of runReadings || []) {
    const r = entry?.readings || {};
    const source = `${KIND_LABEL[entry.kind] || entry.kind || "运行"} · ${entry.run_id || ""}`.replace(/ · $/, "");
    const at = iso(entry?.ts);
    for (const obs of Array.isArray(r.observations) ? r.observations : []) {
      const m = [obs.money_after, obs.money, obs.money_before].find((v) => count(v) !== null);
      if (m != null) put("money", { count: m, observedAt: at, source, confidence: null });
      const pair = [obs.rights_after, obs.rights, obs.rights_before].find((v) => Array.isArray(v) && v.length === 2 && v.every((x) => count(x) !== null));
      if (pair) put("fes", { count: pair[0], max: pair[1], observedAt: at, source, confidence: null });
    }
    const items = r.items && typeof r.items === "object" && !Array.isArray(r.items) ? r.items : {};
    for (const [id, value] of Object.entries(items).slice(0, 200)) {
      if (!ITEM_ID.test(id) || count(value?.count) === null) continue;
      put(id, { count: value.count, max: count(value.max), observedAt: at, source, confidence: unit(value.confidence) });
    }
  }
  return out;
}

/**
 * One row per holding: the fixed account list plus every scanned Produce item. Newest observation wins between the engine
 * and the scan file; a holding nobody observed has `count: null` (shown as 未观测, never 0).
 */
export function holdingRows(state, itemScan, runReadings = state?.runReadings) {
  const engine = engineHoldings(runReadings);
  const scanned = Object.fromEntries((itemScan?.items || []).map((i) => [i.id, i]));
  const ids = [...HOLDING_ITEMS, ...(itemScan?.items || []).filter((i) => !HOLDING_ITEMS.includes(i.id)).map((i) => i.id)];
  return [...new Set(ids)].map((id) => {
    const spec = { ...(EXTRA_LABELS[id] || {}), ...(itemSpec(state, id) || {}) };
    const fromScan = scanned[id] ? { ...scanned[id], source: scanned[id].frame ? `截图 ${scanned[id].frame.split("/").pop()}` : "截图扫描" } : null;
    const fromEngine = engine[id] || null;
    const pick = [fromScan, fromEngine].filter(Boolean)
      .sort((a, b) => (Date.parse(b.observedAt || 0) || 0) - (Date.parse(a.observedAt || 0) || 0))[0] || null;
    const label = scanned[id]?.produce ? produceName(id, scanned[id]) : (spec.label || id);
    return {
      id, label, spec: { ...spec, key: id, label }, produce: Boolean(scanned[id]?.produce), icon: scanned[id]?.icon || null,
      count: pick ? pick.count : null, max: pick?.max ?? null, plus: pick?.plus ?? null,
      observedAt: pick?.observedAt || null, source: pick?.source || "", frame: pick === fromScan ? fromScan?.frame || "" : "",
      confidence: pick?.confidence ?? null, indicatorOnly: id === "jewel" || NEVER_USE_ITEMS.has(id) || Boolean(scanned[id]?.indicatorOnly),
    };
  });
}

/** Produce item label: the scan's in-game name, else the アイテム一覧 name for the id (marked 推测 if not `sure`), else 未确认名称. */
export function produceName(id, scanned) {
  if (scanned?.name) return scanned.name;
  const known = P_ITEM_NAMES[id];
  if (known) return known.sure ? known.name : `${known.name}（推测）`;
  return `未确认名称（${scanned?.look || id}）`;
}

/** Card rows from the reviewed-import scan (inventory-scan.js), annotated with per-card frame/time when item-scan has it. */
export function cardRows(cardScan, itemScan) {
  return (cardScan?.cards || []).map((card) => {
    const seen = itemScan?.cards?.[card.id] || null;
    return { id: card.id, score: card.score, frames: card.frames, observedAt: seen?.observedAt || iso(cardScan.generatedAt) || null,
      frame: seen?.frame || "", source: seen?.frame ? `截图 ${seen.frame.split("/").pop()}` : `${card.frames} 帧扫描` };
  });
}

let holdingsCache = null;
/** The local scan modules (gitignored, same-origin imports); missing files give empty results, never errors. */
export async function loadHoldings() {
  const load = (path) => import(`${path}${scanSuffix()}`).then((m) => m.default).catch(() => null);
  const [items, cards, levelScan] = await Promise.all([load("../game-assets/item-scan.js"), load("../game-assets/inventory-scan.js"),
    loadLevelScan().catch(() => cleanLevelScan(null))]);
  holdingsCache = { itemScan: cleanItemScan(items), cardScan: cleanScan(cards), levelScan };
  return holdingsCache;
}

function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

/** "1,234" below 10 000, then "1.2万" / "3.4亿" (tile overlay; the exact value is in the tile title). */
export function compactNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const trim = (n) => String(Number(n.toFixed(n >= 100 ? 0 : 1)));
  if (Math.abs(value) < 10000) return formatNumber(value);
  if (Math.abs(value) < 1e8) return `${trim(value / 1e4)}万`;
  return `${trim(value / 1e8)}亿`;
}

// ------------------------------------------------------------------------------------------------ summary strip
/**
 * The four counters shown first. Order is fixed so they never jump around. Since 2026-10-08b (user: 「体力icon换成体力药，
 * 训练券换成钻石，fes参加权换成fes回复券」) the fourth card is ジュエル (display only: never spent) and the training tickets
 * live in the 道具 grid.
 */
export const SUMMARY_IDS = ["money", "fes", "stamina", "jewel"];
const SUMMARY_LABEL = { money: "マニー", fes: "Fes 参加权", stamina: "体力", jewel: "ジュエル" };
const SUMMARY_NOTE = { jewel: "不使用" };

/**
 * Icon key for a holding: counters without a tile of their own borrow the in-game item that refills them —
 * 体力 → リカバリーソーダ30, Fes 参加权 → フェスエントリーチケット — when the private assets have that tile.
 */
const ICON_STAND_IN = { stamina: "stamina_drink", fes: "fes_ticket", fes_rights: "fes_ticket" };
export function iconKeyFor(id) {
  const alt = ICON_STAND_IN[id];
  return alt && gameAssets().items[alt] ? alt : id;
}

/** The page's one icon look: a fixed square well holding the game tile (or, without private assets, the line plate). */
export function resIcon(state, id, { size = 32, spec = null } = {}) {
  const key = iconKeyFor(id);
  const use = key === id ? (spec || itemSpec(state, id) || { key: id }) : itemSpec(state, key);
  const inner = itemIcon(use, { size, label: "" });
  const kind = gameAssets().items[key] ? "is-tile" : "is-plate";
  const well = h("span", { class: `res-icon ${kind}`, "aria-hidden": "true", dataset: { icon: key } }, inner);
  if (well.style) { well.style.width = `${size}px`; well.style.height = `${size}px`; }
  return well;
}

/**
 * Summary values: the newest of the holding row (scan / engine) and the matching `state.readings` entry. A counter
 * nobody observed stays `count: null` (未观测).
 */
export function summaryValues(state, rows) {
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  return SUMMARY_IDS.map((id) => {
    const row = byId[id] || { id, count: null, max: null, observedAt: null, source: "" };
    const reading = (state?.readings || []).find((r) => readingItemKey(r) === id && r.value !== null && r.value !== undefined && r.observed_at);
    const fromReading = reading ? { count: reading.value, max: reading.max ?? null, observedAt: reading.observed_at, source: reading.source || "" } : null;
    const fromRow = row.count !== null && row.count !== undefined ? row : null;
    const pick = [fromRow, fromReading].filter(Boolean)
      .sort((a, b) => (Date.parse(b.observedAt || 0) || 0) - (Date.parse(a.observedAt || 0) || 0))[0] || null;
    return { id, label: SUMMARY_LABEL[id], note: SUMMARY_NOTE[id] || "", spec: row.spec || itemSpec(state, id), count: pick ? pick.count : null, max: pick?.max ?? null,
      observedAt: pick?.observedAt || null, source: pick?.source || "", frame: pick?.frame || "" };
  });
}

function freshLine(at, now) {
  const f = freshness(at, now);
  if (!f.text) return h("span", { class: "fresh", text: "—" });
  return h("span", { class: `fresh${f.stale ? " is-stale" : ""}`, title: `${f.title}${f.stale ? STALE_NOTE : ""}` }, f.text);
}

export function statCard(value, now, state = null) {
  const observed = value.count !== null && value.count !== undefined;
  const note = value.note ? `（${value.note}：只显示数量）` : "";
  const title = observed ? `${value.label} ${formatNumber(value.count)}${value.max != null ? ` / ${formatNumber(value.max)}` : ""}${note}\n${formatTime(value.observedAt)} · ${value.source || "来源未知"}`
    : `${value.label}：尚未读取${note}`;
  return h("div", { class: `stat-card${observed ? "" : " is-missing"}`, title, dataset: { item: value.id } },
    h("span", { class: "stat-icon" }, resIcon(state, value.id, { size: 32, spec: value.spec })),
    h("span", { class: "stat-main" },
      h("span", { class: "stat-label" }, jaText(value.label), value.note ? h("span", { class: "stat-note", text: value.note }) : null),
      observed
        ? h("span", { class: "stat-num num" }, formatNumber(value.count), value.max != null ? h("span", { class: "stat-max", text: ` / ${formatNumber(value.max)}` }) : null)
        : h("span", { class: "stat-num stat-none", text: "尚未读取" })),
    observed ? freshLine(value.observedAt, now) : null);
}

/**
 * Readings that are not holdings (e.g. 任务达成 3/7), as 资源 shows them (pure). 任务达成 is a daily value: it counts only on the
 * game day it was read, the same rule as 主页 (which shows 「—」 for an older one), so the two pages never disagree.
 */
export function visibleExtraReadings(state, now = Date.now()) {
  const today = gameDay(new Date(now));
  return (state?.readings || []).filter((r) => !SUMMARY_IDS.includes(readingItemKey(r) || r.key) && !HOLDING_ITEMS.includes(readingItemKey(r) || r.key)
    && r.value !== null && r.value !== undefined && r.observed_at
    && (readingItemKey(r) !== "mission" || gameDay(new Date(r.observed_at)) === today));
}

function extraReadings(state, now) {
  const list = visibleExtraReadings(state, now);
  if (!list.length) return null;
  return h("ul", { class: "extra-readings" }, list.slice(0, 4).map((r) => h("li", { title: `${formatTime(r.observed_at)} · ${r.source || ""}` },
    readingItemKey(r) && gameAssets().items[iconKeyFor(readingItemKey(r))] ? resIcon(state, readingItemKey(r), { size: 20 }) : null,   // game tiles only
    h("span", { class: "muted" }, jaText(r.label || r.key)),
    h("span", { class: "num" }, `${formatNumber(r.value)}${r.max != null ? ` / ${formatNumber(r.max)}` : ""}`),
    freshLine(r.observed_at, now))));
}

// ------------------------------------------------------------------------------------------------ item tiles
/**
 * Holding icon: a clean game icon from the manifest first (atlas / pinned tile / glyph; since 2026-10-08c every scanned
 * Produce item id has its tile), else — last resort, only when the private assets lack the id — the screenshot crop from
 * the scan, marked `is-crop` (dashed outline, title) so it reads as a stand-in, else the line plate.
 */
function holdingIcon(row, size = 32) {
  const clean = gameAssets().items[iconKeyFor(row.id)];
  if (!clean && row.icon) {
    return h("span", { class: "res-icon is-crop" }, h("img", { class: "holding-img is-crop", src: row.icon, alt: "", width: String(size), height: String(size), draggable: "false",
      loading: "lazy", title: "截图裁剪的图标", dataset: { iconSource: "crop" } }));
  }
  return resIcon(null, row.id, { size, spec: row.spec });
}

/** Full text for an item: name, exact value, when and where it was observed (tile title + the detail line). */
export function holdingDetail(row, now = Date.now()) {
  const observed = row.count !== null && row.count !== undefined;
  if (!observed) return `${row.label}：尚未读取${row.indicatorOnly ? "（不使用）" : ""}`;
  const f = freshness(row.observedAt, now);
  const conf = row.confidence != null ? ` · 置信 ${Math.round(row.confidence * 100)}%` : "";
  const value = `${formatNumber(row.count)}${row.max != null ? ` / ${formatNumber(row.max)}` : ""}${row.plus != null ? ` +${formatNumber(row.plus)}` : ""}`;
  return `${row.label}：${value}${row.indicatorOnly ? "（不使用）" : ""} · ${f.text || "时间未知"}${f.title ? `（${f.title}）` : ""} · ${row.frame ? row.frame.split("/").pop() : row.source || "来源未知"}${conf}`;
}

/** Icon tile: icon, count overlay (compact), name; 未观测 greyed (never 0). Click shows the source line. */
export function holdingTile(row, { onPick, now = Date.now() } = {}) {
  const observed = row.count !== null && row.count !== undefined;
  const mark = row.indicatorOnly && !row.label.includes("不使用");
  const label = mark ? (/）$/.test(row.label) ? row.label.replace(/）$/, " · 不使用）") : `${row.label}（不使用）`) : row.label;   // no 「（…）（不使用）」
  return h("button", { type: "button", class: `item-tile${observed ? "" : " is-missing"}${row.indicatorOnly ? " is-indicator" : ""}`,
    title: holdingDetail(row, now), dataset: { item: row.id, focusKey: `item-${row.id}` }, onclick: () => onPick?.(row) },
  h("span", { class: "item-tile-icon" }, holdingIcon(row)),
  h("span", { class: `item-tile-count num${observed ? "" : " is-none"}`, text: observed ? compactNumber(row.count) : "尚未读取" }),
  h("span", { class: "item-tile-name" }, jaText(label)));
}

/** Observed first, then 未观测, indicator-only (ジュエル, never used) last; stable within each group. */
export function tileOrder(rows) {
  const rank = (r) => (r.indicatorOnly ? 2 : r.count !== null && r.count !== undefined ? 0 : 1);
  return rows.map((r, i) => [r, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([r]) => r);
}

function itemGrid(rows, now) {
  const detail = h("p", { class: "item-detail muted small", role: "status" });   // filled when a tile is clicked
  const grid = h("div", { class: "item-grid", role: "list" },
    tileOrder(rows).map((row) => holdingTile(row, { now, onPick: (r) => { detail.replaceChildren(jaText(holdingDetail(r, now))); } })));
  return [grid, detail];
}

// 「扫描持有卡」: the self-scrolling アイドル一覧 scan, launched by the HOST (cards.scan.start) after the
// page's own confirmation dialog. Gate, confirmation, progress and result text live in card-scan.js; this file only places them.
// A finished scan's import files are re-read here (refreshHoldings) and then follow the normal auto-accept flow; the page
// never writes the owned list from the scan directly (the reviewed 「导入 Lv/凸」 dialogs and the auto-accept flow are the only savers).
export { cardScanGate };

function holdingsFooter(scan, hostScan, { onReload, onCardScan, onCardScanStop, dev = false } = {}) {
  const note = cardScanResultNote(hostScan);
  const control = scan.running
    ? cardScanProgress(hostScan, { onStop: onCardScanStop })
    : [h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: "holdings-card-scan", cardScan: scan.ready ? "ready" : scan.blocker ? "blocked" : "pending" },
        disabled: scan.ready ? null : "", title: gateTitle(scan, dev), "aria-label": `扫描持有卡：${scan.label}`,
        onclick: () => { if (scan.ready) onCardScan?.(); } }, scan.label)];
  return h("div", { class: "holdings-footer" },
    h("div", { class: "holdings-actions" },
      h("button", { type: "button", class: "btn btn-text btn-small", dataset: { focusKey: "holdings-reload" }, title: "重新读取本机的识别结果文件",
        onclick: () => onReload?.() }, "重新读取"),
      h("span", { class: "grow" }),
      control),
    note);
}

/**
 * Holdings body: summary strip, 道具 / プロデュースアイテム fold cards (icon tiles) and the 持有卡牌 fold card.
 * `onImport` / `onImportLevels` open the reviewed bulk dialogs (nothing saved without 确定); `onCommit(nextOwned)` saves
 * one card after the user's own 确认 / 保存 tap; `onDeclined` re-renders after 忽略此识别.
 */
export function holdingsView(state, holdings, { onImport, onImportLevels, onReload, onCardScan, onCardScanStop, onCommit, onDeclined, onUndoAuto, now = Date.now(), after = [] } = {}) {
  const rows = holdingRows(state, holdings?.itemScan);
  const summary = summaryValues(state, rows);
  const account = rows.filter((r) => !r.produce && !SUMMARY_IDS.includes(r.id));
  const produce = rows.filter((r) => r.produce);
  const owned = state ? ownedCards(state) : { p: [], s: [] };
  const auto = loadAuto();
  const entries = cardEntries(owned, { cardScan: holdings?.cardScan, levelScan: holdings?.levelScan, declined: loadDeclined(), auto: auto.cards });
  const autoBatch = lastBatch(owned, auto);
  const pending = entries.filter((e) => e.status === "pending" || e.status === "conflict").length;
  const observedCount = (list) => list.filter((r) => r.count !== null && r.count !== undefined).length;
  const scan = cardScanGate(state?.policy, state?.host);
  const kinds = { s: entries.filter((e) => e.kind === "s").length, p: entries.filter((e) => e.kind === "p").length };

  // readings, items, cards (+ `after`); the wide layout lists the folds beside a 概览 of the rest (render below)
  return h("div", { class: "holdings" },
    h("div", { class: "stat-strip" }, summary.map((v) => statCard(v, now, state))),
    extraReadings(state, now),
    foldSection({ key: "res-items", title: "道具", count: account.length, defaultOpen: true,
      summary: h("span", { class: "muted small", text: `已观测 ${observedCount(account)}` }),
      children: itemGrid(account, now) }),
    produce.length ? foldSection({ key: "res-produce-items", title: "プロデュースアイテム", count: produce.length, defaultOpen: false,
      summary: h("span", { class: "muted small", text: `已观测 ${observedCount(produce)}` }),
      children: itemGrid(produce, now) }) : null,
    foldSection({ key: "res-cards", title: "持有卡牌", count: entries.length || null, defaultOpen: true,
      summary: h("span", { class: "fold-summary-tags" },
        entries.length ? h("span", { class: "muted small", text: `P ${kinds.p} · S ${kinds.s}` }) : null,
        pending ? h("span", { class: "dot-text tone-accent small" }, h("span", { class: "dot", "aria-hidden": "true" }), `待确认 ${pending}`) : null),
      children: cardsView(entries, { owned, now, rejected: unrecognisedTiles({ cardScan: holdings?.cardScan, levelScan: holdings?.levelScan }), onCommit, onDeclined, onUndoAuto, autoBatch, onBulkOwned: onImport, onBulkLevels: onImportLevels,
        footer: holdingsFooter(scan, state?.host?.card_scan, { onReload, onCardScan, onCardScanStop, dev: state?.prefs?.developer === true }) }) }),
    after);
}

/**
 * The single owned-cards save path of this page: per-card 确认 / 保存, the reviewed bulk dialogs, auto-apply and its undo.
 * `userIds` = cards the user decided here (their values count as user-set from now on, so later scans never overwrite them).
 */
function persistOwned(state, actions, value, rerender, { userIds = null, autoStore = null } = {}) {
  // An auto-apply / undo brings its own provenance store; any other save marks the changed cards as user-set.
  saveAuto(autoStore || forgetAuto(loadAuto(), userIds || changedCards(ownedCards(state), normalizeOwned(value))));
  if (!saveOwnedCards(state, actions, value)) rerender?.();
}

/** Ids whose ownership or Lv/凸 differ between two owned lists. */
export function changedCards(a, b) {
  const own = (o) => new Set([...(o?.p || []), ...(o?.s || [])]);
  const ha = own(a), hb = own(b);
  const ids = new Set([...ha, ...hb]);
  return [...ids].filter((id) => ha.has(id) !== hb.has(id)
    || JSON.stringify(a?.details?.[id] || {}) !== JSON.stringify(b?.details?.[id] || {})).sort();
}

/** Holdings objects already offered to auto-apply (any page may load them first: 2026-10-10 the home tab did, so 资源 never ran it). */
const appliedHoldings = new WeakSet();
export function needsAutoApply(holdings) { return Boolean(holdings) && !appliedHoldings.has(holdings); }

/**
 * Auto-apply (b: 「识别准确率八九十基本就是了，不需要确认」): when scan files are loaded, readings
 * with confidence >= AUTO_MIN are saved without per-card confirmation — once per scan file, never over a user-set value.
 * Runs after loading, not while rendering. Returns the ids applied ([] when nothing new).
 */
export function autoApplyHoldings(state, actions, holdings, rerender, { now = Date.now(), notify = toast } = {}) {
  if (!state || !holdings) return [];
  appliedHoldings.add(holdings);
  const owned = ownedCards(state);
  const plan = autoApplyPlan(owned, { cardScan: holdings.cardScan, levelScan: holdings.levelScan, declined: loadDeclined() }, loadAuto(), { now });
  if (!plan.changed) { saveAuto(plan.store); return []; }
  persistOwned(state, actions, plan.next, rerender, { autoStore: plan.store });
  notify(`已自动确认 ${plan.ids.length} 张识别清晰的卡；可在资源页撤销`, "success");
  return plan.ids;
}

/** Undo auto-applied cards (one card from its sheet, or the whole latest batch). */
export function undoAutoApplied(state, actions, ids, rerender) {
  const result = undoAuto(ownedCards(state), loadAuto(), ids);
  persistOwned(state, actions, result.next, rerender, { autoStore: result.store });
  return result.ids;
}

/** Opens the reviewed import; the owned list is saved only through the dialog's 确定 (team-import.js onCommit). */
export function importOwnedCards(state, actions, rerender, open = openInventoryImport) {
  return open({
    owned: ownedCards(state),
    onApply: (merged) => persistOwned(state, actions, merged, rerender),
  });
}

/** Opens the reviewed Lv/凸 import (team-levels-import.js); details are saved only after its 确定. */
export function importOwnedLevels(state, actions, rerender, open = openLevelImport) {
  const owned = ownedCards(state);
  return open({
    owned,
    onApply: (details) => persistOwned(state, actions, { ...owned, details }, rerender),
  });
}

// 最近运行读数 (2026-10-26 role-play D12): Chinese names for the engine's reading fields (run_control.readings). A field
// without a name here is left out rather than shown as an English key; state words go through READING_STATE / outcomeText.
const READING_LABEL = {
  ok: "正常", home_clean: "首页无弹窗", available: "可用", capacity: "容量", quantity: "数量", price: "价格", runs_done: "已完成局数",
  runs_done_today: "今日已完成", remaining: "剩余", level: "等级", stage: "关卡", latest_stage: "最新关卡", total: "总数", count: "数量",
  succeeded: "成功数", counter: "计数", takeover_required: "需接管", status: "状态", outcome: "结果", conclusion: "结论", log_state: "日志状态",
};
const READING_STATE = {
  starting: "准备", succeeded: "成功", failed: "失败", error: "出错", timeout: "超时", interrupted: "中断", skipped: "跳过", sent: "已发送",
  refused: "已拒绝", denied: "已拒绝", unknown: "未知", intent: "已记录意图", not_sent: "未发送", uncertain: "不确定", verified: "已核实",
  takeover: "需接管", completed: "已完成", claimable: "可领取", incomplete: "未完成", nothing_to_do: "无事可做", stopped: "已停止",
  bought: "已购买", trained: "已训练", played: "已进行", claimed: "已领取", dispatched: "已派遣", sampled: "已采样",
};

/** One run reading as Chinese facts, 「状态 成功 · 数量 3」; fields without a Chinese name are omitted. */
export function readingFacts(readings) {
  const parts = [];
  for (const [key, value] of Object.entries(readings || {})) {
    const label = READING_LABEL[key];
    if (!label) continue;
    if (typeof value === "boolean") parts.push(`${label} ${value ? "是" : "否"}`);
    else if (Number.isInteger(value)) parts.push(`${label} ${value}`);
    else if (typeof value === "string") {
      const word = READING_STATE[value] || (outcomeText(value) !== value ? outcomeText(value) : null);
      if (word) parts.push(`${label} ${word}`);
    } else if (Array.isArray(value) && value.length && value.every(Number.isInteger)) parts.push(`${label} ${value.join("/")}`);
  }
  return parts.join(" · ") || "无数值";
}

// Indicator only: the tool never spends jewels or draws gacha; shown so the boundary is visible.
function ledgerCallout(state, actions) {
  const flagged = openLedger(state);
  if (!flagged.length) return null;
  return h("section", { class: "attention-card", "aria-label": "需要核对的消耗" },
    h("div", { class: "attention-card-head" }, h("span", { class: "dot", "aria-hidden": "true" }),
      h("span", { class: "attention-card-title", text: `消耗待核对 ${flagged.length} 条` })),
    h("ul", { class: "ledger" }, flagged.map((l) => h("li", { class: `ledger-item ledger-${l.status}` },
      statusPill(l.status === "uncertain" ? "uncertain" : "failed", l.status === "uncertain" ? "不确定" : "待核对"),
      h("div", { class: "grow" }, h("div", {}, jaText(taskName(state, l.task))),
        h("div", { class: "muted small" }, jaText(`${formatTime(l.ts)} · ${l.note || ""}`))),
      // the same 已核对 as 主页 / 任务 (acks.js): hides the entry, nothing is retried
      h("button", { class: "btn btn-small", type: "button", title: "已在游戏里核对：隐藏这条记录", dataset: { focusKey: `ack-ledger-${ledgerKey(l)}` },
        onclick: () => actions.ackLedger(ledgerKey(l)) }, "已核对")))));
}

/**
 * A finished host scan wrote new import modules: bump the module version (ES import cache), re-read them and let the normal
 * auto-accept flow apply high-confidence readings; the rest appear in 需要你确认. Returns the applied ids.
 */
export async function refreshHoldings(state, actions, rerender) {
  bumpScanVersion();
  const fresh = await loadHoldings();
  const ids = autoApplyHoldings(state, actions, fresh, rerender);
  rerender?.();
  return ids;
}

// Node unit tests import this module without a browser: define the element only where custom elements exist.
const Base = globalThis.HTMLElement || class {};

class ResourcePanel extends Base {
  set data(value) {
    this._data = value;
    if (holdingsCache && needsAutoApply(holdingsCache)) autoApplyHoldings(value.state, value.actions, holdingsCache, () => this.render());
    if (!holdingsCache && !this._loading) {
      this._loading = loadHoldings()
        .then((holdings) => { if (this._data) autoApplyHoldings(this._data.state, this._data.actions, holdings, () => this.render()); })
        .finally(() => { this._loading = null; if (this._data) this.render(); });
    }
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    ensureStyles();
    clear(this);
    this.className = "panel";
    const now = state.now && !Number.isNaN(Date.parse(state.now)) ? Date.parse(state.now) : Date.now();
    const rerender = () => this.render();

    const recent = (state.runReadings || []).slice(-8).reverse();
    // Card data and recent readings follow 持有卡牌 in its group (the right column when wide). 预算 removed 2026-10-28.
    const after = [
      foldSection({ key: "res-card-library", title: "卡资料", defaultOpen: !cardLibraryModel(state.host).last,
        summary: h("span", { class: "muted small", text: cardLibrarySummary(cardLibraryModel(state.host)) }),
        children: () => cardLibraryView(state, actions) }),
      recent.length ? foldSection({ key: "res-recent", title: "最近运行读数", count: recent.length, defaultOpen: false,
        children: h("ul", { class: "ledger" }, recent.map((r) => h("li", { class: "ledger-item" },
          h("div", {}, h("div", {}, jaText(`${r.task ? taskName(state, r.task) : "运行"} · ${KIND_LABEL[r.kind] || r.kind}`)),
            h("div", { class: "muted small" }, jaText(`${r.ts ? formatTime(r.ts) : "—"} · ${readingFacts(r.readings)}`)))))) }) : null,
    ];
    const holdings = holdingsView(state, holdingsCache || { itemScan: cleanItemScan(null), cardScan: cleanScan(null), levelScan: cleanLevelScan(null) }, {
      now, after,
      onImport: () => importOwnedCards(state, actions, rerender),
      onImportLevels: () => importOwnedLevels(state, actions, rerender),
      onCommit: (next, ids) => persistOwned(state, actions, next, rerender, { userIds: ids || null }),
      onDeclined: rerender,
      onUndoAuto: (ids) => undoAutoApplied(state, actions, ids, rerender),
      onReload: () => loadHoldings().then((fresh) => { autoApplyHoldings(this._data.state, this._data.actions, fresh, rerender); rerender(); }),
      onCardScan: () => openCardScanConfirm({ onConfirm: () => actions.cardScanStart?.(),
        libraryMissing: !gameAssets().cards.length && !gameAssets().pCards.length }),
      onCardScanStop: () => actions.cardScanStop?.(),
    });

    if (state.wide) {
      // 双栏: the readings become a 概览 section beside 道具 / 持有卡牌 / 卡资料 (same views, common.js sectionDeck);
      // 消耗待核对 stays above the list (what needs the player comes first).
      const parts = [...holdings.children];
      const overview = foldSection({ key: "res-overview", title: "概览", defaultOpen: true,
        children: h("div", { class: "res-overview" }, parts.filter((p) => !p.matches("details.fold"))) });
      this.append(h("div", { class: "panel-scroll res-scroll", dataset: { scrollKey: "resources" } },
        ledgerCallout(state, actions),
        sectionDeck([overview, ...parts.filter((p) => p.matches("details.fold"))], { key: "resources", label: "资源分组" })));
      return;
    }
    this.append(h("div", { class: "panel-scroll res-scroll", dataset: { scrollKey: "resources" } },
      ledgerCallout(state, actions),
      holdings));
  }
}
if (globalThis.customElements && !globalThis.customElements.get("sc-resource-panel")) {
  globalThis.customElements.define("sc-resource-panel", ResourcePanel);
}

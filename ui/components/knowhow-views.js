// ノウハウ page views (pure, node-tested by tests/test_knowhow_views.py), using the matcher in knowhow-data.js.
// Everything is advice/display only: Maa never sells, protects, marks or picks a book in the game.
// - teamFromSlots / bookUsability: the current Produce team (P + S + Guest) -> 可带 / 本队不可带（同偶像） / 需确认;
// - haloPicks: top single books for say "Halo" (default order = knowhow-data.analyze: compatibility, then wanted hits);
// - stepTrio: a 3-book candidate combination for S.T.E.P. (coverage of wanted items, not a global optimum or probability);
// - gridLocation / searchRecipe: where to find a book in-game. A page/row/column is only given when the scan proved the
//   whole 評価値 grid (scan.grid.complete); otherwise 「评价值 N 附近，偶像 X」. 詳細検索 is category → effect
//   group, never an arbitrary knowhow name, so the recipe does not claim more than that;
// - libraryRows / reviewFacts / pendingNotes / coverageMap / scanStatus: 书库, 整理复核 facts (never sale advice),
//   new produce-end books compared with the library, the wanted × idol coverage map and partial-scan honesty.
import { analyze, cleanWanted, isRosterIdol, nameMatches, normalizeName, GENERIC_CATALOG, IDOL_UNITS } from "./knowhow-data.js";

export { IDOL_UNITS };

export const GRID_COLS = 5;
export const GRID_ROWS = 3;
export const PER_PAGE = GRID_COLS * GRID_ROWS;  // ノウハウブック一覧 5×3 (native 1280×720 sample)
export const USABLE_LABEL = { ok: "可带入", blocked: "编队偶像，不可带入", unknown: "待确认" };
export const REVIEW_NOTE = "是否出售由你决定，Maa 不会出售";
const GENERAL_BOOK = /^283プロのノウハウブック/;
const HEAD = /^(Vocal|Dance|Visual|メンタル)(UP|上限UP)\+{0,3}$/;   // same as knowhow-rules.js (not G.R.A.D. 「Vocal上限+」)
const TEAM_SLOTS = ["P", "S1", "S2", "S3", "S4", "guest"];

// 社区常见 starting templates (community notes, dated; not current-meta weights). Editable by the user.
export const TEMPLATES = {
  halo: [
    ["パーフェクトリィ", "exact"], ["アピールUP(思い出高)", "exact"], ["アピールUP(思い出低)", "exact"],
    ["基礎能力値UP", "prefix"], ["オールラウンダー", "prefix"], ["思い出(ノウハウ)", "prefix"],
    ["エキシビションマッチ", "prefix"], ["ファン感謝祭クリア", "prefix"], ["Landing Point大成功", "prefix"],
  ].map(([name, match]) => ({ name, match, min_level: 1, purpose: "halo" })),
  step: [
    ["エキスパート目標達成", "exact", 5], ["Vocal上限", "prefix", 1], ["Dance上限", "prefix", 1], ["Visual上限", "prefix", 1],
    ["メンタル上限", "prefix", 1], ["思い出(ノウハウ)", "prefix", 1], ["エキシビションマッチ", "prefix", 1],
  ].map(([name, match, min]) => ({ name, match, min_level: min, purpose: "step" })),
};

/** Template list for "halo" | "step" | "both" (items present in both merged to purpose "any"). */
export function templateList(which = "both") {
  const parts = which === "halo" ? [TEMPLATES.halo] : which === "step" ? [TEMPLATES.step] : [TEMPLATES.halo, TEMPLATES.step];
  const out = new Map();
  for (const list of parts) {
    for (const item of list) {
      const key = `${normalizeName(item.name)}|${item.match}`;
      const prev = out.get(key);
      out.set(key, prev ? { ...prev, purpose: "any", min_level: Math.max(prev.min_level, item.min_level) } : { ...item });
    }
  }
  return cleanWanted([...out.values()]);
}

/** Wanted items for a scenario ("halo" | "step") and optional position (items without a position always count). */
export function itemsFor(wanted, scenario, position = "") {
  return cleanWanted(wanted).filter((w) => (w.purpose === scenario || w.purpose === "any")
    && (!position || !w.position || w.position === position));
}

/**
 * Team from the Produce 编队 slots: [{slot, label, id, idol}] (idol = resolved name or null). verified only when the P
 * slot is filled and every filled slot resolved to an idol name.
 */
export function teamFromSlots(slots = []) {
  const filled = slots.filter((s) => s && s.id);
  const unresolved = filled.filter((s) => !s.idol).map((s) => s.label || s.slot);
  const idols = [...new Set(filled.filter((s) => s.idol).map((s) => normalizeName(s.idol)))];
  const hasP = filled.some((s) => s.slot === "P");
  return { slots: filled, idols, unresolved, verified: hasP && !unresolved.length, source: "profile" };
}

/** Manual override: a list of idol names (the user's own pick). */
export function teamFromIdols(names = []) {
  const idols = [...new Set((Array.isArray(names) ? names : []).map(normalizeName).filter(Boolean))];
  return { slots: [], idols, unresolved: [], verified: idols.length > 0, source: "manual" };
}

/** Preview fixture team {P: idol, S1..: idol}. */
export function teamFromDemo(demo) {
  if (!demo || typeof demo !== "object") return null;
  const slots = TEAM_SLOTS.filter((k) => typeof demo[k] === "string" && demo[k])
    .map((k) => ({ slot: k, label: k === "guest" ? "Guest" : k, id: `demo-${k}`, idol: demo[k] }));
  return slots.length ? { ...teamFromSlots(slots), source: "demo" } : null;
}

export function isGeneralBook(book) {
  return !book?.idol && GENERAL_BOOK.test(normalizeName(book?.title));
}

/** "ok" | "blocked" | "unknown" (unknown idol, or no verified team). 283プロのノウハウブック: any idol may bring it. */
export function bookUsability(book, team) {
  if (isGeneralBook(book)) return "ok";
  if (!book?.idol) return "unknown";
  const idol = normalizeName(book.idol);
  if (team?.idols?.includes(idol)) return "blocked";
  // a 名义 the roster does not know (an unreadable name) cannot be checked against the team
  return team?.verified && isRosterIdol(idol) ? "ok" : "unknown";
}

const TIER = { ok: 0, unknown: 1, blocked: 2 };

function hitsOf(book, items) {
  const hits = [];
  items.forEach((item, index) => {
    const levels = book.rows.filter((r) => r.level !== null && nameMatches(item, r.name)).map((r) => r.level);
    if (levels.length && Math.max(...levels) >= item.min_level) {
      const row = book.rows.find((r) => r.level === Math.max(...levels) && nameMatches(item, r.name));
      hits.push({ index, item, name: row.name, level: row.level });
    }
  });
  return hits;
}

/**
 * say "Halo" single-book picks. Order = analyze() ranking (compatibility tier, then wanted hits), re-tiered with
 * bookUsability (283 book usable by anyone); blocked books and books without a hit are left out.
 */
export function haloPicks(books, wanted, team, { position = "", limit = 3 } = {}) {
  const items = itemsFor(wanted, "halo", position);
  const byFp = new Map(books.map((b) => [b.fingerprint, b]));
  const result = analyze(books, items, { excludeIdols: team?.verified ? team.idols : null });
  const order = result.ranking.map((fp) => byFp.get(fp));
  // Without a verified team every idol book is 需确认 (S cards unread until W.I.N.G. starts): ranking by that tier would
  // put a weak 283 book above every strong one (2026-10-24 role-play), so it only applies to a verified team.
  const tier = (r) => (team?.verified ? TIER[r.usability] : 0);
  const rows = order.map((book, i) => ({ book, i, usability: bookUsability(book, team), hits: hitsOf(book, items) }))
    .filter((r) => r.usability !== "blocked" && r.hits.length)
    .sort((a, b) => tier(a) - tier(b) || a.i - b.i);
  const picks = rows.slice(0, limit).map(({ book, usability, hits }) => ({
    book, usability, hits, gaps: items.filter((_, index) => !hits.some((h) => h.index === index)),
  }));
  const blocked = books.filter((b) => bookUsability(b, team) === "blocked");
  const reachable = new Set(rows.flatMap((r) => r.hits.map((h) => h.index)));
  return { items, picks, blocked, missing: items.filter((_, index) => !reachable.has(index)), candidates: rows.length };
}

/**
 * S.T.E.P. 3-book candidate combination. Units = usable books with ≥1 wanted hit (identical copies count up to their
 * physical count). Exhaustive over the best `maxUnits` units: fewer 需确认 books, then more wanted items covered, then
 * more total hits (several books holding the same item), then book order. Coverage means "brought in", never
 * "inherited" (STEP inheritance is random; no probability is computed).
 */
export function stepTrio(books, wanted, team, { position = "", maxUnits = 40 } = {}) {
  const items = itemsFor(wanted, "step", position);
  const units = [];
  books.forEach((book, order) => {
    const usability = bookUsability(book, team);
    if (usability === "blocked") return;
    const hits = hitsOf(book, items);
    if (!hits.length) return;
    for (let copy = 0; copy < Math.min(book.count || 1, 3); copy += 1) units.push({ book, usability, hits, copy, order });
  });
  units.sort((a, b) => TIER[a.usability] - TIER[b.usability] || b.hits.length - a.hits.length || a.order - b.order || a.copy - b.copy);
  const pool = units.slice(0, maxUnits);
  const evaluate = (combo) => {
    const covered = new Set(combo.flatMap((u) => u.hits.map((h) => h.index)));
    return { combo, covered, unknown: combo.filter((u) => u.usability !== "ok").length,
      hits: combo.reduce((sum, u) => sum + u.hits.length, 0) };
  };
  const better = (a, b) => !b || a.unknown < b.unknown || (a.unknown === b.unknown && (a.covered.size > b.covered.size
    || (a.covered.size === b.covered.size && a.hits > b.hits)));
  let best = null;
  const size = Math.min(3, pool.length);
  if (size === 3) {
    for (let i = 0; i < pool.length; i += 1) for (let j = i + 1; j < pool.length; j += 1) for (let k = j + 1; k < pool.length; k += 1) {
      const cand = evaluate([pool[i], pool[j], pool[k]]);
      if (better(cand, best)) best = cand;
    }
  } else if (size) {
    best = evaluate(pool.slice(0, size));
  }
  const combo = best ? best.combo : [];
  const covered = best ? best.covered : new Set();
  const slots = [0, 1, 2].map((i) => combo[i] ? { book: combo[i].book, usability: combo[i].usability, hits: combo[i].hits,
    copy: combo[i].copy } : null);
  return {
    items, slots,
    covered: items.filter((_, index) => covered.has(index)),
    gaps: items.filter((_, index) => !covered.has(index)),
    holders: items.map((_, index) => combo.filter((u) => u.hits.some((h) => h.index === index)).length),
    candidates: units.length, exhaustive: units.length <= maxUnits,
    blocked: books.filter((b) => bookUsability(b, team) === "blocked"),
  };
}

function cell(position) {
  const page = Math.floor((position - 1) / PER_PAGE) + 1;
  const inPage = (position - 1) % PER_PAGE;
  return { page, row: Math.floor(inPage / GRID_COLS) + 1, col: (inPage % GRID_COLS) + 1 };
}

const cellText = (c) => `第${c.page}页 第${c.row}行 第${c.col}列`;

function rangeText(a, b) {
  if (a.page === b.page && a.row === b.row) return `第${a.page}页 第${a.row}行 第${a.col}–${b.col}列`;
  if (a.page === b.page) return `第${a.page}页 第${a.row}行第${a.col}列 – 第${b.row}行第${b.col}列`;
  return `${cellText(a)} – ${cellText(b)}`;
}

/**
 * In-game location in ノウハウ評価値順 ▼降順 (ノウハウブック一覧, 5×3 per page). Exact only when the scan proved the whole
 * 評価値 grid (scan.grid.complete: consecutive screens overlap-checked and the tile count equals 所持数, recorded by
 * agent/core/knowhow_scan.py); the position is then counted on the recorded grid values, so books whose details were
 * not read still count. Equal 評価値 give a range (in-game tie order unknown); books received after the scan (pending)
 * may push it back. Otherwise approximate text only. `books` is kept for the signature (no longer used for counting).
 */
export function gridLocation(book, books, scan = null, pendingCount = 0) {
  const value = book?.eval_value;
  const idol = book?.idol || (isGeneralBook(book) ? "283プロ" : "名义未读");
  if (!Number.isInteger(value)) return { exact: false, text: `评价值未读取，偶像 ${idol}`, approx: true };
  const grid = scan?.grid;
  const values = Array.isArray(grid?.values) ? grid.values : [];
  const total = Number.isInteger(grid?.owned_total) ? grid.owned_total : null;
  const proven = grid?.complete === true && total !== null && values.length === total
    && values.every((v, i) => i === 0 || values[i - 1] >= v);
  if (!proven || !values.includes(value)) {
    const why = !grid ? "评价值网格未完整扫描，排序位置未知"
      : !proven ? `评价值网格只证明读到 ${values.length}${total !== null ? `/${total}` : ""} 本，排序位置未知`
        : "这本的评价值不在扫描记录的网格里（扫描后有变化），排序位置未知";
    return { exact: false, approx: true, text: `评价值 ${value} 附近，偶像 ${idol}`, why };
  }
  const higher = values.filter((v) => v > value).length;
  const ties = values.filter((v) => v === value).length;
  const first = cell(higher + 1);
  const last = cell(higher + ties + Math.max(0, pendingCount));
  const range = higher + ties + Math.max(0, pendingCount) > higher + 1;
  const notes = [];
  if (ties > 1) notes.push(`评价值 ${value} 的书共 ${ties} 本，游戏内同分顺序未知`);
  if (pendingCount > 0) notes.push(`扫描后新增 ${pendingCount} 本，位置可能后移`);
  return { exact: !range, approx: false, first, last, position: higher + 1,
    text: range ? rangeText(first, last) : cellText(first), notes };
}

const AUTO_TITLE = /^[^\u0000-\u001f/\\:<>"'`]{1,60}$/;  // ui/protocol.json prefs.auto_step_books title

/**
 * A book as one prefs.auto_step_books entry (Weekly Produce 带书「ノウハウ页指定」: Auto finds it on the picker's first
 * screen by 評価値, then checks title and Vo/Da/Vi/Me); null while its title, stats or 評価値 are unread.
 */
export function autoStepBook(book) {
  const title = String(book?.title || "").normalize("NFKC").replace(/\s+/g, "");
  const s = book?.stats || {};
  const values = [s.vo, s.da, s.vi, s.me, book?.eval_value];
  if (!AUTO_TITLE.test(title) || !values.every((v) => Number.isInteger(v) && v >= 0 && v <= 2000)) return null;
  return { title, vo: s.vo, da: s.da, vi: s.vi, me: s.me, eval: book.eval_value };
}

function statLine(book) {
  const s = book?.stats || {};
  return ["vo", "da", "vi", "me"].map((k) => (Number.isInteger(s[k]) ? s[k] : "?")).join("/");
}

/** Plain-text in-game search recipe for one book (copied to the clipboard by the panel). */
export function searchRecipe(book, location, hits = []) {
  const lines = [`书：${book.title || book.idol || "（名义未读）"}（Vo/Da/Vi/Me ${statLine(book)}${Number.isInteger(book.eval_value) ? `，評価値 ${book.eval_value}` : ""}）`];
  lines.push(`1. ノウハウブック一覧或选书页：並び替え → ノウハウ評価値順，▼降順；${location.text}`
    + (location.approx ? "（位置未知，仅供定位）" : "（按一覧 5列×3行 计算，选书页布局未校准）"));
  for (const note of location.notes || []) lines.push(`   注：${note}`);
  const filters = [];
  if (book.idol) filters.push(`アイドル：${book.idol}`);
  const heads = [...new Set(hits.map((h) => h.name).filter((n) => HEAD.test(n)))];
  if (heads.length) filters.push(`ノウハウ种别：勾选对应的 UP/上限UP 类及档位（${heads.join("、")}）`);
  const abilities = hits.map((h) => h.name).filter((n) => !HEAD.test(n));
  if (abilities.length) filters.push(`詳細検索 ノウハウ1：类别 → 效果组（只能选类别和效果组，组名以游戏内为准；本书含 ${abilities.slice(0, 3).join("、")}）`);
  if (filters.length) lines.push(`2. 或 絞り込み → ${filters.join("；")}`);
  lines.push(`3. 打开后核对四项能力 ${statLine(book)}${Number.isInteger(book.knowhow_count) ? ` 与 ノウハウ数 ${book.knowhow_count}` : ""}。`);
  lines.push("Maa 只提供查找方式，不会替你点击。");
  return lines.join("\n");
}

/** Chip colour class: vo / da / vi / me (head-type names), ab (ability / unit), in (instant), lo (often low), ot. */
export function knowhowKind(name) {
  const n = normalizeName(name);
  if (/^Vocal|^ボーカル/.test(n)) return "vo";
  if (/^Dance|^ダンス/.test(n)) return "da";
  if (/^Visual|^ビジュアル/.test(n)) return "vi";
  if (/^メンタル/.test(n)) return "me";
  const entry = GENERIC_CATALOG.find((e) => nameMatches(e, n));
  if (!entry) return "ot";
  return entry.group === "ability" || entry.group === "unit" ? "ab" : entry.group === "instant" ? "in" : entry.group === "low" ? "lo" : "ot";
}

/** Stable hue (0–359) per idol name, for the colour bar. */
export function idolHue(name) {
  let hash = 0;
  for (const ch of normalizeName(name)) hash = (hash * 31 + ch.codePointAt(0)) % 3600;
  return Math.round(hash / 10) % 360;
}

/**
 * Query tokens (space separated, all must match): idol / title / knowhow name substring, or 「Lv5 Vocal上限」 /
 * 「Lv5Vocal上限」 = a row whose name contains the text at Lv ≥ 5. "Lv5" alone = any row at Lv ≥ 5.
 */
export function parseQuery(query) {
  const raw = String(query || "").normalize("NFKC").trim().split(/\s+/).filter(Boolean);
  const terms = [];
  for (let i = 0; i < raw.length; i += 1) {
    const m = /^lv\.?([1-6])(.*)$/i.exec(raw[i]);
    if (m) {
      let text = m[2];
      if (!text && raw[i + 1] && !/^lv\.?[1-6]/i.test(raw[i + 1])) { text = raw[i + 1]; i += 1; }
      terms.push({ level: Number(m[1]), text: normalizeName(text).toLowerCase() });
    } else {
      terms.push({ level: null, text: normalizeName(raw[i]).toLowerCase() });
    }
  }
  return terms;
}

function termMatches(book, term) {
  const rows = book.rows.filter((r) => !term.text || r.name.toLowerCase().includes(term.text));
  if (term.level !== null) return rows.some((r) => r.level !== null && r.level >= term.level);
  if (!term.text) return true;
  return rows.length > 0 || normalizeName(book.idol).toLowerCase().includes(term.text)
    || normalizeName(book.title).toLowerCase().includes(term.text);
}

/** 书库 rows: filtered by query, sorted by "wanted" (hits; default), "eval" (official 評価値) or "idol". */
export function libraryRows(books, wanted, { query = "", sort = "wanted" } = {}) {
  const items = cleanWanted(wanted);
  const terms = parseQuery(query);
  const rows = books.map((book, order) => ({ book, order, hits: hitsOf(book, items) }))
    .filter((r) => terms.every((t) => termMatches(r.book, t)));
  const ev = (b) => (Number.isInteger(b.eval_value) ? b.eval_value : -1);
  const cmp = {
    wanted: (a, b) => b.hits.length - a.hits.length || ev(b.book) - ev(a.book) || a.order - b.order,
    eval: (a, b) => ev(b.book) - ev(a.book) || a.order - b.order,
    idol: (a, b) => (a.book.idol || "￿").localeCompare(b.book.idol || "￿", "ja") || ev(b.book) - ev(a.book) || a.order - b.order,
  }[sort] || ((a, b) => a.order - b.order);
  return rows.sort(cmp);
}

/** 书库 filter options: idols (with book counts) and ノウハウ names present in the read books, most common first. */
export function libraryFacets(books) {
  const idols = new Map();
  const entries = new Map();
  for (const book of books) {
    const idol = book.idol ? normalizeName(book.idol) : isGeneralBook(book) ? GENERAL_IDOL : "";
    if (idol) idols.set(idol, (idols.get(idol) || 0) + 1);
    for (const name of new Set(book.rows.map((r) => r.name).filter(Boolean))) entries.set(name, (entries.get(name) || 0) + 1);
  }
  const byCount = (a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ja");
  return { idols: [...idols].sort((a, b) => a[0].localeCompare(b[0], "ja")), entries: [...entries].sort(byCount) };
}

export const GENERAL_IDOL = "283プロ（通用）";
export const LIBRARY_STATUS = [["all", "全部"], ["wanted", "含目标条目"], ["protected", "保護"], ["unprotected", "未保護"], ["incomplete", "未读完"]];

/** 书库 filters on libraryRows output: status chip, idol, and one ノウハウ at ≥ min Lv. */
export function filterLibrary(rows, { status = "all", idol = "", entry = "", minLevel = 1 } = {}) {
  return rows.filter(({ book, hits }) => {
    if (status === "wanted" && !hits.length) return false;
    if (status === "protected" && book.protected !== true) return false;
    if (status === "unprotected" && book.protected !== false) return false;
    if (status === "incomplete" && book.complete) return false;
    if (idol) {
      const name = book.idol ? normalizeName(book.idol) : isGeneralBook(book) ? GENERAL_IDOL : "";
      if (name !== idol) return false;
    }
    if (entry && !book.rows.some((r) => r.name === entry && (r.level ?? 0) >= minLevel)) return false;
    return true;
  });
}

function levelMap(book) {
  const out = {};
  for (const r of book.rows) if (r.level !== null) out[r.name] = Math.max(out[r.name] || 0, r.level);
  return out;
}

/**
 * 整理复核 facts (never sale advice): complete, unprotected, unmarked books with a known idol, none of whose rows
 * matches any wanted item, and every row is held at ≥ the same Lv by one other complete same-idol book (or the book has
 * identical copies). Stats are reported side by side, not judged.
 */
export function reviewFacts(books, wanted) {
  const items = cleanWanted(wanted);
  const excluded = { protected: 0, marked: 0, incomplete: 0, wanted: 0 };
  const facts = [];
  for (const book of books) {
    if (book.protected === true) { excluded.protected += book.count || 1; continue; }
    if (book.marked === true) { excluded.marked += book.count || 1; continue; }
    if (!book.complete || !book.idol) { excluded.incomplete += book.count || 1; continue; }
    if (book.rows.some((r) => items.some((item) => nameMatches(item, r.name)))) { excluded.wanted += book.count || 1; continue; }
    const mine = levelMap(book);
    const by = books.filter((other) => other !== book && other.complete && other.fingerprint !== book.fingerprint
      && normalizeName(other.idol) === normalizeName(book.idol)
      && Object.entries(mine).every(([name, lv]) => (levelMap(other)[name] || 0) >= lv));
    const copies = (book.count || 1) - 1;
    if (by.length || copies > 0) facts.push({ book, by, copies });
  }
  return { facts, excluded, wanted_count: items.length };
}

/** New produce-end books vs the read library: 「Dance上限++ Lv5，书库最高 Lv4」 style notes per row. */
export function pendingNotes(pending, books, wanted) {
  const items = cleanWanted(wanted);
  const best = {};
  for (const book of books) for (const [name, lv] of Object.entries(levelMap(book))) best[name] = Math.max(best[name] || 0, lv);
  return (pending || []).map((book) => {
    const notes = [];
    let same = 0;
    for (const row of book.rows) {
      if (row.level === null) continue;
      const isWanted = items.some((item) => nameMatches(item, row.name));
      const have = best[row.name];
      if (!have) notes.push({ name: row.name, level: row.level, library: null, wanted: isWanted, text: `${row.name} Lv${row.level}，书库中没有` });
      else if (row.level > have) notes.push({ name: row.name, level: row.level, library: have, wanted: isWanted, text: `${row.name} Lv${row.level}，书库最高 Lv${have}` });
      else same += 1;
    }
    notes.sort((a, b) => Number(b.wanted) - Number(a.wanted));
    return { book, notes, notHigher: same };
  });
}

/**
 * Coverage map: rows = wanted items, columns = idols (most items met first), cell = highest owned Lv among that idol's
 * read books ({level, book, meets}); null = nothing read. Idol-less books form the column 「通用/未读名义」.
 */
export function coverageMap(books, wanted) {
  const items = cleanWanted(wanted);
  const idolOf = (b) => b.idol ? normalizeName(b.idol) : isGeneralBook(b) ? "283プロ" : "名义未读";
  const idols = [...new Set(books.map(idolOf))];
  const rows = items.map((item) => {
    const cells = {};
    for (const book of books) {
      const levels = book.rows.filter((r) => r.level !== null && nameMatches(item, r.name)).map((r) => r.level);
      if (!levels.length) continue;
      const level = Math.max(...levels);
      const key = idolOf(book);
      if (!cells[key] || level > cells[key].level) cells[key] = { level, book, meets: level >= item.min_level };
    }
    const met = Object.values(cells).filter((c) => c.meets);
    return { item, cells, met: met.length, best: Math.max(0, ...Object.values(cells).map((c) => c.level)) };
  });
  const score = (idol) => rows.filter((r) => r.cells[idol]?.meets).length;
  idols.sort((a, b) => score(b) - score(a) || a.localeCompare(b, "ja"));
  return { idols, rows };
}

/** 「已读 X/514 本详情，结果仅覆盖已读部分」 (physical counts; unread ≠ not owned). */
export function scanStatus(books, scan = null, pendingCount = 0) {
  const read = books.filter((b) => b.complete).reduce((s, b) => s + (b.count || 1), 0);
  const seen = books.reduce((s, b) => s + (b.count || 1), 0);
  const total = Number.isInteger(scan?.owned_total) ? scan.owned_total : null;
  const partial = total === null || read < total;
  const head = total === null ? `已读 ${read} 本详情（所持总数未读取）` : `已读 ${read}/${total} 本详情`;
  const extra = [];
  if (seen > read) extra.push(`另有 ${seen - read} 本只读到部分内容`);
  if (pendingCount > 0) extra.push(`Produce 结束新获得 ${pendingCount} 本待入库`);
  const grid = scan?.grid;
  const gridComplete = grid?.complete === true;
  if (grid) extra.push(gridComplete ? `评价值网格已完整读取 ${grid.values.length} 本，可给出游戏内位置` : "评价值网格未完整读取，位置只给近似");
  return { read, seen, total, partial, gridComplete, text: `${head}${partial ? "，结果仅覆盖已读部分" : ""}`, extra };
}

/** Preview-only extras (mock bridge): the synthetic team of the fixture. Pending books come from loadKnowhowData. */
export async function loadExtras({ demo = false } = {}) {
  if (!demo) return { demoTeam: null };
  try {
    const payload = (await import("../fixtures/knowhow-demo.js")).default || {};
    return { demoTeam: teamFromDemo(payload.demo_team) };
  } catch {
    return { demoTeam: null };
  }
}

/** 书库 group order (classification of knowhow-rules.classifyBook) and labels. */
// 2026-10-24 role-play: grouped by what the book does for the active Target (the STEP classes 浓缩组 / 分离素材 / 原料 put
// 406 of 498 real books under 分离素材, which told the player nothing). Second round: 「命中多项」 still held 383 of 498, so
// the books in between are grouped by progress (`p<n>` = n Target units met), best first.
export const LIBRARY_GROUPS = [["completed", "已达标"], ["intermediate", "主书与中间书"], ["many", "命中多项"], ["one", "命中 1 项"], ["other", "未命中"]];
const GROUP_ORDER = new Map(LIBRARY_GROUPS.map(([key], i) => [key, i]));
const groupRank = (key) => (/^p\d+$/.test(key) ? 1.5 - Number(key.slice(1)) / 100 : GROUP_ORDER.get(key));

/**
 * Group 书库 rows by classification (`classify(book)` → key: a LIBRARY_GROUPS key or `p<n>`; anything else = other).
 * Keeps the row order inside each group; drops empty groups. `label(key)` names the `p<n>` groups. Each group reports
 * kinds (rows = distinct contents) and copies (physical books, identical ones counted ×N).
 */
export function groupLibrary(rows, classify, label = (key) => `${key.slice(1)} 项`) {
  const groups = new Map();
  for (const row of rows) {
    const raw = classify(row.book);
    const key = GROUP_ORDER.has(raw) || /^p\d+$/.test(raw) ? raw : "other";
    if (!groups.has(key)) groups.set(key, { key, label: GROUP_ORDER.has(key) ? LIBRARY_GROUPS[GROUP_ORDER.get(key)][1] : label(key), rows: [] });
    groups.get(key).rows.push(row);
  }
  return [...groups.values()].sort((a, b) => groupRank(a.key) - groupRank(b.key))
    .map((g) => ({ ...g, kinds: g.rows.length, copies: g.rows.reduce((n, r) => n + (r.copies ?? (r.book.count || 1)), 0) }));
}

/** Which group opens by default: the one holding the main book, else the first of 成品 / 中间书 / 浓缩组 / 分离素材 / 原料. */
export function defaultOpenGroup(groups, mainFp = null) {
  const withMain = mainFp && groups.find((g) => g.rows.some((r) => r.book.fingerprint === mainFp));
  return withMain?.key || groups.find((g) => g.key !== "other")?.key || groups[0]?.key || null;
}

/**
 * Identical books (same idol, card name and every ノウハウ at the same Lv) shown as one row: the first keeps its place and
 * gets `copies` (sum of physical counts) and `same` (the other records). Different fingerprints can hold the same content
 * (e.g. a registered result that was scanned later); nothing is merged in the data.
 */
export function collapseIdentical(rows) {
  const key = (b) => `${normalizeName(b.idol)}|${b.title || ""}|${b.rows.map((r) => `${r.name}:${r.level}`).sort().join(",")}`;
  const out = new Map();
  for (const row of rows) {
    const k = row.book.complete ? key(row.book) : `fp:${row.book.fingerprint}`;
    const prev = out.get(k);
    if (prev) { prev.copies += row.book.count || 1; prev.same.push(row.book); } else out.set(k, { ...row, copies: row.book.count || 1, same: [] });
  }
  return [...out.values()];
}

const ATTR_SHORT = { Vocal: "Vo", Dance: "Da", Visual: "Vi", メンタル: "Me" };

/** Short chip text: drop a trailing scenario tag like 「(G.R.A.D.)」, cap the length (the full name goes in the title). */
export function shortName(name, max = 9) {
  const n = normalizeName(name).replace(/\((G\.R\.A\.D\.|W\.I\.N\.G\.|GRAD|WING|LP)\)$/, "")
    .replace(/^思い出\(ノウハウ\)/, "思い出").replace(/^基礎能力値UP/, "基礎UP").replace(/^アピールUP/, "アピUP")
    .replace(/\(思い出(高&低|高|低)\)/, "($1)")   // アピUP(高) / (低) stay distinct when shortened
    .replace(/^エキシビションマッチ/, "エキシビ").replace(/^パッシブスキル延長/, "パッシブ延長")
    .replace(/^(Vocal|Dance|Visual|メンタル)(上限)?UP(?=\+*$)/, (_, a, cap) => `${ATTR_SHORT[a]}${cap || ""}UP`);   // head: ViUP++ / Vi上限UP+
  if (n.length <= max) return n;
  const plus = /\++$/.exec(n)?.[0] || "";   // the +/++/+++ tells head books apart: never cut it
  return `${n.slice(0, max - 1 - plus.length)}…${plus}`;
}

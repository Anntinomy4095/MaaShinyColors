// ノウハウ page data helpers (pure, node-tested). Mirrors agent/core/knowhow_books.py: the committed generic catalogue,
// wanted-list sanitising (same rules as the host UiProfileStore) and the matcher. Advice only: Maa never sells,
// deletes or protects a book; K2 review facts never infer sale eligibility.
// Book data comes from ui/game-assets/knowhow.js (local, gitignored; written by `python -m agent.core.knowhow_books
// export-ui`). It may be missing: the page then shows an empty state.

export const MAX_WANTED = 64;   // ui/protocol.json prefs.knowhow_wanted maxItems
export const MAX_NAME = 40;
export const MATCH_KINDS = ["exact", "prefix", "contains"];
export const PURPOSES = ["halo", "step", "any"];
export const POSITIONS = ["center", "leader", "vocal", "dance", "visual"];
export const PURPOSE_LABEL = { halo: "say \"Halo\"", step: "S.T.E.P.", any: "两者" };
export const POSITION_LABEL = { center: "Center", leader: "Leader", vocal: "Vocal", dance: "Dance", visual: "Visual" };  // as in the item names (Center適正)
export const MATCH_LABEL = { exact: "完全一致", prefix: "前缀", contains: "包含" };
export const GROUP_LABEL = { head: "头（基礎）ノウハウ", instant: "即时能力类", ability: "アビリティ", unit: "ユニット依存",
  low: "常被视为低价值", private: "本地资料", seen: "库中出现过" };
// Default = one point per threshold-satisfied target; no Halo discount or STEP probability estimate.
export const DEFAULT_LEVEL_WEIGHT = { 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1 };

/** Re-read the library after a completed run or scan, without reloading it on ordinary status ticks. */
export function knowhowReloadKey(state) {
  const run = state.runs?.[0];
  return `${run?.id ?? run?.run_id ?? ""}|${state.host?.knowhow_scan?.finished_seq ?? ""}|${state.host?.game_assets?.modules?.["knowhow.js"] ?? ""}`;
}

const FORBIDDEN = /[\u0000-\u001f/\\:<>"'`]/;

function e(name, group, purposes = ["halo", "step"], match = "exact", note = "") {
  return { name, group, purposes, match, note, source: "doc" };
}

// Same list as agent/core/knowhow_books.py GENERIC_CATALOG (tests/test_knowhow_books.py checks parity). Names already
// documented; several exact spellings are unverified, hence prefix/contains.
export const GENERIC_CATALOG = [
  e("VocalUP", "head", undefined, "prefix", "头ノウハウ；+/++/+++ 档"),
  e("DanceUP", "head", undefined, "prefix", "头ノウハウ"),
  e("VisualUP", "head", undefined, "prefix", "头ノウハウ"),
  e("メンタルUP", "head", undefined, "prefix", "头ノウハウ（样本：メンタルUP+）"),
  e("Vocal上限", "head", undefined, "prefix", "头ノウハウ（样本：Vocal上限+/++/+++）"),
  e("Dance上限", "head", undefined, "prefix", "头ノウハウ"),
  e("Visual上限", "head", undefined, "prefix", "头ノウハウ"),
  e("メンタル上限", "head", undefined, "prefix", "头ノウハウ"),
  e("思い出(ノウハウ)", "instant", undefined, "prefix", "+/++"),
  e("エキスパート目標達成", "instant", ["step"]),
  e("エキシビションマッチ", "instant", undefined, "prefix", "++ 档常用"),
  e("ファン感謝祭クリア", "instant", undefined, "prefix"),
  e("Landing Point大成功", "instant", undefined, "prefix"),
  e("アピールUP(思い出高)", "ability", ["halo"]),
  e("アピールUP(思い出低)", "ability", ["halo"]),
  e("パーフェクトリィ", "ability", ["halo"]),
  e("スタートダッシュ", "ability", ["halo"]),
  e("スロースターター", "ability", ["halo"]),
  e("基礎能力値UP", "ability", ["halo"], "prefix", "(+3%)/(+6%)"),
  e("オールラウンダー", "ability", ["halo"], "prefix", "◯/◎"),
  e("適正", "ability", ["halo"], "contains", "◯◯適正◯/◎（按位置）"),
  e("スペシャリスト", "ability", ["halo"], "prefix", "按位置"),
  e("ロールモデル", "ability", ["halo"]),
  e("エース", "ability", ["halo"]),
  e("歌姫", "ability", ["halo"]),
  e("プロダンサー", "ability", ["halo"]),
  e("トップモデル", "ability", ["halo"]),
  e("施設効率相談", "ability", ["halo"], "prefix"),
  e("パッシブスキル延長", "ability", ["halo"], "prefix"),
  e("昂揚", "ability", ["halo"]),
  e("ファイナルライブ", "ability", ["halo"], "prefix"),
  e("越える絆", "unit", ["halo"], "exact", "两ユニット编队"),
  e("一致団結", "unit", ["halo"], "exact", "单ユニット编队"),
  e("WING優勝", "low", ["step"], "exact", "不可转记；Halo 价值低"),
  e("WING準決勝", "low", ["step"], "exact", "不可转记；Halo 价值低"),
  e("ファン感MVP", "low", ["step"]),
  e("GRAD予選", "low", ["step"]),
  e("GRAD決勝", "low", ["step"]),
  e("ファンの声援", "low", ["step"], "exact", "Halo 编书时常视为占格"),
  e("エントリー目標達成", "low", ["step"]),
];

export function normalizeName(text) {
  return String(text ?? "").normalize("NFKC").replace(/\s+/g, "").replace(/[〇○◯]/g, "◯");  // mirrors normalize_name
}

export function cleanName(text) {
  const name = normalizeName(text);
  return name && name.length <= MAX_NAME && !FORBIDDEN.test(name) ? name : null;
}

/** One wanted item {name, match, min_level, purpose, position?} or null (same rules as WantedItem.from_dict). */
export function cleanWantedItem(value) {
  const raw = typeof value === "string" ? { name: value } : value;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const name = cleanName(raw.name);
  if (!name) return null;
  const item = {
    name,
    match: MATCH_KINDS.includes(raw.match) ? raw.match : "exact",
    min_level: Number.isInteger(raw.min_level) && raw.min_level >= 1 && raw.min_level <= 6 ? raw.min_level : 1,
    purpose: PURPOSES.includes(raw.purpose) ? raw.purpose : "any",
  };
  if (POSITIONS.includes(raw.position)) item.position = raw.position;
  return item;
}

export function wantedKey(item) {
  return `${normalizeName(item.name)}|${item.match}|${item.purpose}`;
}

export function cleanWanted(values) {
  const out = [];
  const keys = new Set();
  for (const value of Array.isArray(values) ? values : []) {
    const item = cleanWantedItem(value);
    if (!item || keys.has(wantedKey(item)) || out.length >= MAX_WANTED) continue;
    keys.add(wantedKey(item));
    out.push(item);
  }
  return out;
}

export function nameMatches(item, rowName) {
  const want = normalizeName(item.name);
  const have = normalizeName(rowName);
  if (!want || !have) return false;
  if (item.match === "prefix") return have.startsWith(want);
  if (item.match === "contains") return have.includes(want);
  return have === want;
}

/**
 * In-game effect text per entry name from the book details read so far (2026-10-26 user: show what an entry does, not
 * guide advice). OCR varies between readings and a two-line effect is often read as its first line only, so the most
 * frequent complete reading (ending in % / ] / a number / UP) seen at least twice wins; otherwise the most frequent one,
 * and a lone reading only when it is the sole one. A next row's name bled onto the end is not complete.
 */
const EFFECT_END = /(?:[%％\])］）]|\d|UP)$/;
// OCR slips seen in every reading of a word (so the frequency vote cannot fix them): ボ read as ポ, べ as ベ, ゲ as グ.
const EFFECT_FIX = [[/ポーナス/g, "ボーナス"], [/すベて/g, "すべて"], [/グージ/g, "ゲージ"]];

export function effectIndex(books) {
  const seen = new Map();
  for (const book of books || []) {
    for (const row of book.rows || []) {
      const text = typeof row.effect === "string" ? EFFECT_FIX.reduce((s, [re, to]) => s.replace(re, to), row.effect.normalize("NFKC").trim()) : "";
      if (text.length < 4 || !row.name) continue;
      const counts = seen.get(row.name) || new Map();
      counts.set(text, (counts.get(text) || 0) + 1);
      seen.set(row.name, counts);
    }
  }
  const index = new Map();
  for (const [name, counts] of seen) {
    const ranked = [...counts].sort((a, b) => b[1] - a[1]);
    const complete = ranked.find(([text, n]) => n >= 2 && EFFECT_END.test(text));
    const [text, n] = complete || ranked[0];
    if (n >= 2 || counts.size === 1) index.set(name, text);
  }
  return index;
}

/** The effect text for an item ({name, match}): its exact name, else the first read name it matches. */
export function effectFor(index, item) {
  if (!index?.size || !item?.name) return null;
  const name = normalizeName(item.name);
  if (index.has(name)) return index.get(name);
  for (const [read, text] of index) if (nameMatches(item, read)) return text;
  return null;
}

/** Rows of `book` that satisfy a wanted item (name match AND Lv ≥ its min Lv): [{name, level, item}]. */
export function wantedHits(book, wanted) {
  const items = cleanWanted(wanted);
  const out = [];
  for (const row of book?.rows || []) {
    const item = items.find((w) => nameMatches(w, row.name) && Number.isInteger(row.level) && row.level >= w.min_level);
    if (item) out.push({ name: row.name, level: row.level, item });
  }
  return out;
}

/** Page summary (pure): books / W.I.N.G. new books, how many of them hit the wanted list, how many new ones are 保護. */
export function knowhowSummary(data, wanted) {
  const books = data?.books || [];
  const pending = data?.pending || [];
  const hit = (list) => list.filter((b) => wantedHits(b, wanted).length).length;
  return { books: books.length, pending: pending.length, wantedBooks: hit(books) + hit(pending), pendingWanted: hit(pending),
    pendingProtected: pending.filter((b) => b.protected === true).length };
}

const STATS = ["vo", "da", "vi", "me"];
const intIn = (v, lo, hi) => (Number.isInteger(v) && v >= lo && v <= hi ? v : null);

/** 283プロ units (for grouping idol pills and checking book names; a name not listed goes under 其他). */
export const IDOL_UNITS = [
  ["イルミネ", ["櫻木真乃", "風野灯織", "八宮めぐる"]],
  ["アンティーカ", ["月岡恋鐘", "田中摩美々", "白瀬咲耶", "三峰結華", "幽谷霧子"]],
  ["放クラ", ["小宮果穂", "園田智代子", "西城樹里", "杜野凛世", "有栖川夏葉"]],
  ["アルスト", ["大崎甘奈", "大崎甜花", "桑山千雪"]],
  ["ストレイ", ["芹沢あさひ", "黛冬優子", "和泉愛依"]],
  ["ノクチル", ["浅倉透", "樋口円香", "福丸小糸", "市川雛菜"]],
  ["シーズ", ["七草にちか", "緋田美琴"]],
  ["コメティック", ["斑鳩ルカ", "鈴木羽那", "郁田はるき"]],
];
const ROSTER = IDOL_UNITS.flatMap(([, list]) => list);

function editDistance(a, b) {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i += 1) {
    let prev = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const cur = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = cur;
    }
  }
  return row[b.length];
}

/**
 * The roster name an OCR-read 名义 stands for (2026-10-26: 篤冬優子 / 幽谷鼎子 / 斑ル力 … showed up as idols and slipped
 * past the same-idol rule). Exact names pass; otherwise the unique closest roster name within 2 edits, else the name as
 * read (a tie or a far name stays unknown, never guessed).
 */
export function snapIdol(name) {
  if (typeof name !== "string") return null;
  const n = name.normalize("NFKC").trim();
  if (!n) return null;
  if (ROSTER.includes(n)) return n;
  const scored = ROSTER.map((r) => [r, editDistance(n, r)]).sort((a, b) => a[1] - b[1]);
  const [best, second] = scored;
  return best[1] <= 2 && best[1] < n.length && (!second || second[1] > best[1]) ? best[0] : n;
}

export function isRosterIdol(name) {
  return ROSTER.includes(name);
}

/** Books from ui/game-assets/knowhow.js (already sanitised by Python; re-checked here because the file is local data). */
export function cleanBooks(payload) {
  const books = [];
  for (const raw of Array.isArray(payload?.books) ? payload.books : []) {
    if (!raw || typeof raw !== "object" || typeof raw.fingerprint !== "string") continue;
    const rows = (Array.isArray(raw.rows) ? raw.rows : [])
      .map((r) => ({ name: normalizeName(r?.name), level: intIn(r?.level, 1, 6), effect: typeof r?.effect === "string" ? r.effect : null }))
      .filter((r) => r.name).slice(0, 21);
    const stats = {};
    for (const k of STATS) { const v = intIn(raw.stats?.[k], 0, 450); if (v !== null) stats[k] = v; }
    const unknowns = (Array.isArray(raw.unknowns) ? raw.unknowns : []).filter((u) => typeof u === "string");
    if ((Array.isArray(raw.rows) ? raw.rows : []).some((r) => r?.clipped)) unknowns.push("rows.clipped");
    const knowhowCount = intIn(raw.knowhow_count, 0, 21);
    const complete = raw.complete !== false && !!raw.title && STATS.every((k) => k in stats)
      && rows.length > 0 && rows.length === knowhowCount && rows.every((r) => r.level !== null)
      && !unknowns.some((u) => u.startsWith("rows.") || u.startsWith("merge."));
    books.push({
      fingerprint: raw.fingerprint.slice(0, 40), title: typeof raw.title === "string" ? raw.title : null,
      idol: snapIdol(raw.idol), stats, rows,
      eval_value: intIn(raw.eval_value, 0, 99999), marked: typeof raw.marked === "boolean" ? raw.marked : null,
      protected: typeof raw.protected === "boolean" ? raw.protected : null, complete, knowhow_count: knowhowCount, unknowns,
      count: intIn(raw.count, 1, 999) || 1, source: typeof raw.source === "string" ? raw.source : "scan",
      seen: typeof raw.first_seen === "string" && raw.first_seen.length <= 40 && !Number.isNaN(Date.parse(raw.first_seen)) ? raw.first_seen : null,
    });
  }
  return books;
}

/** Generic catalogue + local extra names (private cache / names seen in the user's books), deduplicated. */
export function buildCatalog(extra = []) {
  const seen = new Map();
  for (const entry of [...GENERIC_CATALOG, ...(Array.isArray(extra) ? extra : [])]) {
    const name = cleanName(entry?.name);
    if (!name || seen.has(name)) continue;
    const purposes = (Array.isArray(entry.purposes) ? entry.purposes : []).filter((p) => p === "halo" || p === "step");
    seen.set(name, { name, group: GROUP_LABEL[entry.group] ? entry.group : "private", purposes: purposes.length ? purposes : ["halo", "step"],
      match: MATCH_KINDS.includes(entry.match) ? entry.match : "exact", note: typeof entry.note === "string" ? entry.note : "",
      source: entry.source || "doc" });
  }
  return [...seen.values()];
}

/** Catalogue entries for one purpose tab, filtered by a search string, grouped (group order as GROUP_LABEL). */
export function catalogGroups(catalog, purpose, query = "") {
  const q = normalizeName(query).toLowerCase();
  const groups = new Map(Object.keys(GROUP_LABEL).map((g) => [g, []]));
  for (const entry of catalog) {
    if (purpose && !entry.purposes.includes(purpose)) continue;
    if (q && !entry.name.toLowerCase().includes(q) && !entry.note.toLowerCase().includes(q)) continue;
    groups.get(entry.group)?.push(entry);
  }
  return [...groups].filter(([, list]) => list.length).map(([group, list]) => ({ group, label: GROUP_LABEL[group], entries: list }));
}

function levelsOf(book) {
  const out = {};
  for (const r of book.rows) if (r.level !== null) out[r.name] = Math.max(out[r.name] || 0, r.level);
  return out;
}

/** Same result shape and rules as knowhow_books.analyze (advice only). */
export function analyze(books, wanted, { purpose = null, excludeIdols = null, levelWeight = DEFAULT_LEVEL_WEIGHT, score = null } = {}) {
  const items = cleanWanted(wanted).filter((w) => !["halo", "step"].includes(purpose) || w.purpose === purpose || w.purpose === "any");
  const teamVerified = excludeIdols !== null;
  const excluded = new Set((excludeIdols || []).map(normalizeName));
  const usable = (book) => !teamVerified || !book.idol ? null : !excluded.has(normalizeName(book.idol));
  const best = {};
  for (const book of books) {
    for (const [name, level] of Object.entries(levelsOf(book))) {
      const cur = best[name];
      if (!cur || level > cur.level) best[name] = { level, books: [book.fingerprint] };
      else if (level === cur.level && !cur.books.includes(book.fingerprint)) cur.books.push(book.fingerprint);
    }
  }
  const counts = new Map(books.map((b) => [b.fingerprint, b.count]));
  for (const cur of Object.values(best)) {
    cur.physical_count = cur.books.reduce((sum, fp) => sum + counts.get(fp), 0);
    cur.unique = cur.physical_count === 1;
  }

  const perBook = new Map(books.map((b) => [b.fingerprint, []]));
  const uncertainHits = new Map(books.map((b) => [b.fingerprint, []]));
  const wantedOut = items.map((item, index) => {
    const holders = [];
    for (const book of books) {
      const levels = book.rows.filter((r) => nameMatches(item, r.name)).map((r) => r.level);
      const known = levels.filter((lv) => lv !== null);
      if (known.length && Math.max(...known) >= item.min_level) {
        const level = Math.max(...known);
        holders.push({ book: book.fingerprint, level, eval_value: book.eval_value, usable: usable(book), count: book.count });
        perBook.get(book.fingerprint).push([index, level]);
      } else if (levels.includes(null)) {
        uncertainHits.get(book.fingerprint).push(index);
      }
    }
    holders.sort((a, b) => b.level - a.level || (a.book < b.book ? -1 : a.book > b.book ? 1 : 0));
    const teamSatisfied = teamVerified ? holders.some((h) => h.usable === true) : null;
    return { item, holders, owned: holders.length > 0, satisfied: teamVerified ? teamSatisfied : holders.length > 0,
      team_satisfied: teamSatisfied, best_level: holders[0]?.level ?? null };
  });

  const outclassed = [];
  for (const a of books) {
    if (!a.complete || !a.idol) continue;
    const la = levelsOf(a);
    for (const b of books) {
      if (b === a || !b.complete || normalizeName(b.idol) !== normalizeName(a.idol) || b.fingerprint === a.fingerprint) continue;
      const lb = levelsOf(b);
      if (STATS.every((k) => (b.stats[k] ?? -1) >= (a.stats[k] ?? 1e6)) && Object.entries(la).every(([n, lv]) => (lb[n] || 0) >= lv)) {
        outclassed.push({ book: a.fingerprint, by: b.fingerprint });
      }
    }
  }

  const bookOut = books.map((book) => {
    const fp = book.fingerprint;
    const matched = perBook.get(fp).map(([index, level]) => ({ index, name: items[index].name, level }));
    return {
      book: fp, idol: book.idol, title: book.title, eval_value: book.eval_value, matched,
      score: score ? score(book, matched) : matched.reduce((sum, m) => sum + (levelWeight[m.level] || 0), 0),
      best_in: Object.keys(best).filter((n) => best[n].books.includes(fp)).sort(),
      usable: usable(book), count: book.count,
      complete: book.complete, marked: book.marked, uncertain_matches: uncertainHits.get(fp),
      outclassed_by: outclassed.filter((r) => r.book === fp).map((r) => r.by),
    };
  });
  const usabilityRank = (v) => v === true ? 0 : v === null ? 1 : 2;
  const ranking = [...bookOut].sort((a, b) => usabilityRank(a.usable) - usabilityRank(b.usable) || b.score - a.score
    || (a.book < b.book ? -1 : a.book > b.book ? 1 : 0)).map((b) => b.book);
  const uncertain = [];
  const review = [];
  books.forEach((book, i) => {
    const info = bookOut[i];
    if (items.length && !info.matched.length) {
      review.push({ book: book.fingerprint, reasons: ["已读取条目未命中当前目标"], count: book.count, complete: book.complete });
    }
    if (items.length && (!book.complete || info.uncertain_matches.length)) {
      uncertain.push({ book: book.fingerprint, reason: "详情未完整读取或存在未知项，目标缺口尚待复核" });
    } else if (items.length && info.usable === null) {
      uncertain.push({ book: book.fingerprint, reason: "未绑定队伍或书名义未知，兼容性未验证" });
    }
  });
  const notes = [
    "官方ノウハウ評価値仅为游戏元数据，不参与默认用途排序",
    "位置标签与完整扫描覆盖尚未绑定；已读取记录未发现不等于全库没有",
    "内容指纹不是物理唯一 ID；相同内容保留实体数量，最高 Lv 不推出可出售",
    "识别：ノウハウブック一覧 已用原生 1280×720 帧离线校准；编成选书页与扫描驱动未校准，未做全库扫描",
  ];
  if (!teamVerified) notes.push("未提供实际 P/S 队伍；仅报告库存持有，兼容性未验证");
  return { wanted: wantedOut, books: bookOut, ranking, best_holders: best, sell_candidates: [], review_facts: review,
    outclassed, uncertain, team_verified: teamVerified, notes, advice_only: true };

}

/**
 * Scan status of ui/game-assets/knowhow.js (knowhow_books.ui_payload): owned_total, pending count and the 評価値 grid
 * coverage. grid.complete is trusted only when the recorded values are non-increasing and their count equals
 * owned_total (same rule as knowhow_books.clean_grid); otherwise it is false and location hints stay approximate.
 */
export function cleanScan(scan) {
  if (!scan || typeof scan !== "object") return null;
  const int = (v, lo, hi) => (Number.isInteger(v) && v >= lo && v <= hi ? v : null);
  const raw = scan.grid && typeof scan.grid === "object" ? scan.grid : null;
  let grid = null;
  if (raw) {
    const values = (Array.isArray(raw.values) ? raw.values : []).filter((v) => int(v, 0, 99999) !== null).slice(0, 600);
    const owned = int(raw.owned_total, 0, 600);
    const ordered = values.every((v, i) => i === 0 || values[i - 1] >= v);
    grid = { complete: raw.complete === true && owned !== null && values.length === owned && ordered,
      basis: typeof raw.basis === "string" ? raw.basis.slice(0, 200) : "", values, owned_total: owned };
  }
  return { calibrated: scan.calibrated === true, scanned_at: typeof scan.scanned_at === "string" ? scan.scanned_at : null,
    owned_total: int(scan.owned_total, 0, 600), pending: int(scan.pending, 0, 999) ?? 0, grid, sync: cleanSync(scan.sync) };
}

export const SYNC_STATES = ["up_to_date", "new_books", "too_many", "fewer_owned", "needs_full_scan", "no_baseline", "unknown"];

/** scan.sync = knowhow_scan.library_sync(inv): 所持数 (owned_now) vs the books recorded (known = baseline + recent). */
export function cleanSync(sync) {
  if (!sync || typeof sync !== "object" || !SYNC_STATES.includes(sync.state)) return null;
  const int = (v, lo, hi) => (Number.isInteger(v) && v >= lo && v <= hi ? v : null);
  return { state: sync.state, owned_now: int(sync.owned_now, 0, 600), known: int(sync.known, 0, 600),
    new: int(sync.new, -600, 600), pending: int(sync.pending, 0, 999) ?? 0 };
}

/** books, pending produce-end books (pending_books), catalogue and scan status from the same data module. */
export async function loadKnowhowData({ demo = false, fresh = false } = {}) {
  try {
    // fresh: re-import after a W.I.N.G. run rewrote the module (the host copies it; the ES module cache keeps the old one).
    const module = demo ? await import("../fixtures/knowhow-demo.js") : await import(`../game-assets/knowhow.js${fresh ? `?t=${Date.now()}` : ""}`);
    const payload = module.default || {};
    return { books: cleanBooks(payload), pending: cleanBooks({ books: Array.isArray(payload.pending_books) ? payload.pending_books : [] }),
      catalog: buildCatalog(payload.catalog), scan: cleanScan(payload.scan), loaded: true, demo,
      demoWanted: demo ? cleanWanted(payload.demo_wanted) : [] };
  } catch {
    return { books: [], pending: [], catalog: buildCatalog(), scan: null, loaded: false, demo };
  }
}

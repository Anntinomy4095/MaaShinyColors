// ノウハウ 製本 plan state (pure, node-tested by tests/test_knowhow_rules.py): Targets, the active Target, main book per
// Target, STEP production runs (history, decisions, revert) and user-registered result books. Versioned and migrated:
// v0 = only the old wanted list (prefs.knowhow_wanted) -> one Target 「我的目标」 without losing any item. The panel stores
// the plan in localStorage (no profile field yet) and mirrors every Target (active
// first) to prefs.knowhow_wanted: the Python side keeps books by it (knowhow_keep) and older pages read it. Scanned
// books (game-assets/knowhow.js) are never modified here; user-registered books live in plan.userBooks and are merged
// into the inventory view.
import { cleanWanted, normalizeName, wantedKey } from "./knowhow-data.js";
import { cleanTarget, cleanTargetItem } from "./knowhow-rules.js";

export const PLAN_VERSION = 1;
export const PLAN_KEY = "sc-ui-knowhow-plan";
export const MAX_TARGETS = 12;
export const MAX_RUNS = 200;
export const MAX_USER_BOOKS = 120;
const DECISIONS = ["main", "backup", "stop", "failed"];

const tpl = (name, match = "prefix", role = "required", min = 1, ideal = 5, extra = {}) => ({ name, match, role, min_level: min, ideal_level: ideal, ...extra });
const alt = (group, role, ...names) => names.map(([name, match]) => tpl(name, match, role, 1, 5, { group }));
// ◎ / ◯ entries (2026-10-26 user: 单圈和双圈 must be told apart): two exact items in one substitute group, ◎ first; the
// group is met by either, as a bare prefix item was, but the page lists, marks and counts each ring on its own.
export const RINGED = ["Center適正", "Leader適正", "Vocal適正", "Dance適正", "Visual適正", "オールラウンダー"];
const ring = (base, role = "required", min = 1, ideal = 5) => ["◎", "◯"].map((r) => tpl(`${base}${r}`, "exact", role, min, ideal, { group: base }));

// Recommended entries (Wiki + 2024–2026 community consensus). Names are the
// canonical spellings seen in scanned books; say "Halo" entries are matched by prefix because their in-game suffix is not
// confirmed (アイドル( / 歌姫( keep 歌姫楽宴 and the audition songs out).
const HALO_BASE = [
  ...alt("思い出", "required", ["思い出(ノウハウ)++", "exact"], ["思い出(ノウハウ)+", "exact"]),
  ...alt("施設効率相談", "required", ["施設効率相談+", "prefix"], ["施設効率相談", "prefix"]),
  tpl("アイドル(", "prefix", "preferred", 1, 5, { group: "アイドル" }),
  tpl("アピールUP(思い出高)", "exact", "preferred"), tpl("スロースターター", "exact", "preferred"),
  tpl("メンタリスト", "prefix", "optional"), tpl("パッシブスキル延長", "prefix", "optional"),
  tpl("ファンの声援", "prefix", "excluded"), tpl("ファイナルライブ", "prefix", "excluded"),
];
const ATTR = {
  vo: { name: "Vocal", pos: "vocal", god: "歌姫(", godLabel: "歌姫", label: "Vo" },
  da: { name: "Dance", pos: "dance", god: "プロダンサー", godLabel: "プロダンサー", label: "Da" },
  vi: { name: "Visual", pos: "visual", god: "トップモデル", godLabel: "トップモデル", label: "Vi" },
};
const STEP_SEED = ["スタートダッシュ", "スロースターター", "アピールUP(思い出高)", "アピールUP(思い出低)"].map((n) => tpl(n, "exact", "required", 3, 5));

/** One-tap starting targets (推荐目标), editable after creation. */
export const TARGET_TEMPLATES = [
  { key: "base", name: "通用底本", scenario: "halo", items: HALO_BASE, note: "不含位置条目，之后按位置派生。" },
  { key: "center", name: "Center", scenario: "halo", position: "center",
    items: [...HALO_BASE, ...ring("Center適正"), ...ring("オールラウンダー", "preferred"),
      tpl("基礎能力値UP", "prefix", "preferred"), tpl("エキシビションマッチ++", "exact", "optional")] },
  { key: "leader", name: "Leader", scenario: "halo", position: "leader",
    items: [...HALO_BASE, ...ring("Leader適正"), tpl("ロールモデル", "prefix", "preferred"), tpl("交換数UP(Leader)", "prefix", "optional")] },
  ...Object.entries(ATTR).map(([a, x]) => ({ key: `attr-${a}`, name: `${x.label} 特化`, scenario: "halo", position: x.pos, attribute: a,
    items: [...HALO_BASE, ...ring(`${x.name}適正`), tpl("スペシャリスト", "prefix", "preferred"),
      tpl("基礎能力値UP", "prefix", "preferred"), tpl(x.god, "prefix", "preferred", 1, 5, { group: x.godLabel }), tpl("タフネス", "prefix", "optional")] })),
  { key: "support", name: "辅助位", scenario: "halo",
    items: [...HALO_BASE, ...ring("オールラウンダー"), tpl("スタートダッシュ", "exact", "preferred"), tpl("アピールUP(思い出低)", "exact", "preferred"),
      tpl("アピールUP(思い出高&低)", "prefix", "preferred"), tpl("パーフェクトリィ", "prefix", "optional"), tpl("一致団結", "prefix", "optional")] },
  { key: "mono", name: "单色队 昂揚", scenario: "halo",
    items: [...HALO_BASE, tpl("昂揚", "prefix", "required"), tpl("一致団結", "prefix", "optional")], note: "履歴只有 1 种ジャンル时生效。" },
  { key: "step", name: "S.T.E.P. 素材", scenario: "step",
    items: [...STEP_SEED, ...ring("オールラウンダー", "preferred", 3, 5), tpl("適正", "contains", "optional", 3, 5),
      tpl("スペシャリスト", "prefix", "optional", 3, 5), tpl("パーフェクトリィ", "prefix", "optional", 3, 5), tpl("ロマンチスト", "prefix", "optional", 3, 5),
      tpl("ファンの声援", "prefix", "excluded")], note: "素材书要干净：少带ファンの声援等杂项。" },
];

/**
 * 推荐条目 for 「添加条目」 (2026-10-24, replaces the old GENERIC_CATALOG list on the 目标 page): the entries of
 * grouped by use; `role` is what one tap adds. Low-value entries (ファンの
 * 声援 …) are not offered: 排除 is set per item in the editor (a one-tap 「通常排除」 group read as "remove these").
 */
export const RECOMMENDED_ITEMS = [
  { key: "common", label: "通用", items: [...alt("思い出", "required", ["思い出(ノウハウ)++", "exact"], ["思い出(ノウハウ)+", "exact"]),
    ...alt("施設効率相談", "required", ["施設効率相談+", "prefix"], ["施設効率相談", "prefix"]),
    tpl("アイドル(", "prefix", "preferred"), tpl("アピールUP(思い出高)", "exact", "preferred"), tpl("スロースターター", "exact", "preferred"),
    tpl("歌姫(", "prefix", "preferred"), tpl("プロダンサー", "prefix", "preferred"), tpl("トップモデル", "prefix", "preferred")] },
  { key: "center", label: "Center", items: [...ring("Center適正"), ...ring("オールラウンダー", "preferred"), tpl("基礎能力値UP", "prefix", "preferred"),
    tpl("エキシビションマッチ++", "exact", "optional")] },
  { key: "leader", label: "Leader", items: [...ring("Leader適正"), tpl("ロールモデル", "prefix", "preferred"), tpl("交換数UP(Leader)", "prefix", "optional")] },
  { key: "attr", label: "属性特化", items: [...ring("Vocal適正"), ...ring("Dance適正"), ...ring("Visual適正"), tpl("スペシャリスト", "prefix", "preferred"),
    tpl("タフネス", "prefix", "optional")] },
  { key: "support", label: "辅助位", items: [...ring("オールラウンダー"), tpl("スタートダッシュ", "exact", "preferred"), tpl("アピールUP(思い出低)", "exact", "preferred"),
    tpl("アピールUP(思い出高&低)", "prefix", "preferred"), tpl("パーフェクトリィ", "prefix", "optional"), tpl("一致団結", "prefix", "optional")] },
  { key: "mono", label: "单色队", items: [tpl("昂揚")] },
  { key: "step", label: "S.T.E.P. 素材", items: [...STEP_SEED, ...ring("オールラウンダー", "preferred", 3, 5), tpl("スペシャリスト", "prefix", "optional", 3, 5),
    tpl("パーフェクトリィ", "prefix", "optional", 3, 5), tpl("ロマンチスト", "prefix", "optional", 3, 5), tpl("基礎能力値UP(+6%)", "exact", "optional", 3, 5),
    tpl("メンタリスト", "prefix", "optional", 3, 5), tpl("越える絆", "prefix", "optional", 3, 5)] },
  { key: "situational", label: "其他", items: [tpl("パッシブスキル延長", "prefix", "optional"), tpl("メンタリスト", "prefix", "optional")] },
];

// G.R.A.D. abilities (Wiki「G.R.A.D.アビ4種」): their entries carry no source suffix; other sources come from
// knowhow-rules.js parseEntry. 2026-10-26 user: an entry shows only its in-game effect and where it is obtained.
const GRAD_ABILITIES = ["スタートダッシュ", "スロースターター", "アピールUP(思い出高)", "アピールUP(思い出低)"];

/** Where an entry is obtained (G.R.A.D. / W.I.N.G. / say "Halo" …), or null. */
export function entrySource(name, sourceOf = null) {
  const n = normalizeName(name);
  if (GRAD_ABILITIES.some((x) => n === x || n.startsWith(`${x}+`))) return "G.R.A.D.";
  return typeof sourceOf === "function" ? sourceOf(n) || null : null;
}

let seq = 0;
export function newId(prefix = "t", now = Date.now()) {
  seq = (seq + 1) % 1000;
  return `${prefix}${now.toString(36)}${seq.toString(36)}`;
}

export function targetFromTemplate(key, id = newId()) {
  const t = TARGET_TEMPLATES.find((x) => x.key === key);
  if (!t) return null;
  return cleanTarget({ ...t, id, source: `template:${key}`, note: t.note || "" });
}

/** The old wanted list -> one Target (every item kept: role required, min Lv kept, purpose -> scenario, position kept). */
export function targetFromWanted(wanted, id = "t-wanted") {
  const items = cleanWanted(wanted);
  const purposes = new Set(items.map((w) => w.purpose));
  const scenario = purposes.size === 1 ? [...purposes][0] : "any";
  return cleanTarget({
    id, name: "我的目标", scenario: scenario === "any" ? "any" : scenario, strategy: "balanced", source: "wanted",
    note: "由原「想要的ノウハウ」迁移。",
    items: items.map((w) => ({ name: w.name, match: w.match, role: "required", min_level: w.min_level, ideal_level: w.min_level,
      ...(w.position ? { position: w.position } : {}), ...(w.purpose !== "any" && scenario === "any" ? { purpose: w.purpose } : {}) })),
  });
}

/** Active Target -> wanted list for prefs.knowhow_wanted (wanted roles only; Lv = min Lv; purpose = scenario). */
export function targetToWanted(target) {
  if (!target) return [];
  const purpose = target.scenario === "halo" || target.scenario === "step" ? target.scenario : "any";
  return cleanWanted(target.items.filter((i) => i.role !== "excluded")
    .map((i) => ({ name: i.name, match: i.match, min_level: i.min_level, purpose: i.purpose || purpose, ...(i.position ? { position: i.position } : {}) })));
}

/**
 * Every Target -> one wanted list for prefs.knowhow_wanted: the active Target first (so the 64-item cap keeps it), an
 * item shared by several Targets keeps its lowest min Lv. agent/core/knowhow_keep.py treats these as the useful entries,
 * so a book one Target needs is never sold for another.
 */
export function plansToWanted(plan) {
  const active = activeTarget(plan);
  const targets = plan?.targets || [];
  const byKey = new Map();
  for (const t of active ? [active, ...targets.filter((x) => x.id !== active.id)] : targets) {
    for (const w of targetToWanted(t)) {
      const cur = byKey.get(wantedKey(w));
      if (!cur) byKey.set(wantedKey(w), w);
      else if (w.min_level < cur.min_level) byKey.set(wantedKey(w), { ...cur, min_level: w.min_level });
    }
  }
  return cleanWanted([...byKey.values()]);
}

const STATS = ["vo", "da", "vi", "me"];
const intIn = (v, lo, hi) => (Number.isInteger(v) && v >= lo && v <= hi ? v : null);

/** A user-registered book (manual entry of a STEP / Halo result). Same shape as cleanBooks output, source "user". */
export function cleanUserBook(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.fingerprint !== "string" || !/^u-[\w-]{1,40}$/.test(raw.fingerprint)) return null;
  const rows = (Array.isArray(raw.rows) ? raw.rows : []).map((r) => ({ name: normalizeName(r?.name).slice(0, 60), level: intIn(r?.level, 1, 6), effect: null }))
    .filter((r) => r.name).slice(0, 21);
  if (!rows.length) return null;
  const stats = {};
  for (const k of STATS) { const v = intIn(raw.stats?.[k], 0, 450); if (v !== null) stats[k] = v; }
  const text = (v, n) => (typeof v === "string" && v.trim() ? v.trim().slice(0, n) : null);
  return {
    fingerprint: raw.fingerprint, title: text(raw.title, 60), idol: text(raw.idol, 20) ? normalizeName(raw.idol) : null, stats, rows,
    eval_value: null, marked: null, protected: typeof raw.protected === "boolean" ? raw.protected : null,
    complete: rows.every((r) => r.level !== null), knowhow_count: rows.length, unknowns: [], count: 1, source: "user",
    seen: typeof raw.seen === "string" && !Number.isNaN(Date.parse(raw.seen)) ? raw.seen : null,
    run: typeof raw.run === "string" ? raw.run.slice(0, 40) : null,
  };
}

/**
 * 「名称 Lv」 lines -> rows. Accepts 「Vocal上限+ Lv5」「Vocal上限+ 5」「Vocal上限+\tLv.5」; a line without a level keeps
 * level null (shown as Lv?). Returns {rows, errors}.
 */
export function parseRowsText(text) {
  const rows = [];
  const errors = [];
  String(text || "").split(/\r?\n/).forEach((line, i) => {
    const raw = line.normalize("NFKC").trim();
    if (!raw) return;
    const m = /^(.*?)[\s,，、]*(?:lv\.?\s*)?([1-6])$/i.exec(raw);
    const name = normalizeName(m ? m[1] : raw);
    if (!name || name.length > 60) { errors.push(`第 ${i + 1} 行无法识别`); return; }
    rows.push({ name, level: m ? Number(m[2]) : null });
  });
  if (rows.length > 21) errors.push(`超过 21 条（${rows.length}）`);
  return { rows: rows.slice(0, 21), errors };
}

function cleanRun(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.id !== "string") return null;
  const fps = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string" && x.length <= 48).slice(0, 6) : []);
  return {
    id: raw.id.slice(0, 40), at: typeof raw.at === "string" ? raw.at : null, target: typeof raw.target === "string" ? raw.target : null,
    scenario: raw.scenario === "halo" ? "halo" : "step", inputs: fps(raw.inputs), outputs: fps(raw.outputs),
    mainBefore: typeof raw.mainBefore === "string" ? raw.mainBefore : null,
    decision: DECISIONS.includes(raw.decision) ? raw.decision : null, mode: typeof raw.mode === "string" ? raw.mode.slice(0, 12) : null,
    note: typeof raw.note === "string" ? raw.note.slice(0, 200) : "", reverted: raw.reverted === true,
  };
}

/** Clean / migrate a stored plan. `wanted` = the old wanted list (used only when there is no v1 plan yet). */
/** A saved Target's bare ringed item (「Center適正」, prefix or exact, no group) as the ◎/◯ pair it already matched. */
export function splitRinged(target) {
  if (!target?.items?.some((i) => !i.group && RINGED.includes(i.name) && i.match !== "contains")) return target;
  const names = new Set(target.items.map((i) => i.name));
  const items = target.items.flatMap((i) => (!i.group && RINGED.includes(i.name) && i.match !== "contains"
    ? ["◎", "◯"].filter((r) => !names.has(`${i.name}${r}`)).map((r) => ({ ...i, name: `${i.name}${r}`, match: "exact", group: i.name }))
    : [i]));
  return items.length <= 40 ? { ...target, items } : target;
}

export function migratePlan(raw, wanted = []) {
  const src = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const migrated = !Number.isInteger(src.version);
  let targets = (Array.isArray(src.targets) ? src.targets : []).map(cleanTarget).filter(Boolean).map(splitRinged).slice(0, MAX_TARGETS);
  const ids = new Set();
  targets = targets.filter((t) => (ids.has(t.id) ? false : ids.add(t.id)));
  if (!targets.length && cleanWanted(wanted).length) targets = [targetFromWanted(wanted)];
  const active = targets.some((t) => t.id === src.active) ? src.active : targets[0]?.id || null;
  const main = {};
  for (const [k, v] of Object.entries(src.main && typeof src.main === "object" ? src.main : {})) {
    if (ids.has(k) || targets.some((t) => t.id === k)) if (typeof v === "string" && v.length <= 48) main[k] = v;
  }
  const rules = {};
  for (const [k, v] of Object.entries(src.rules && typeof src.rules === "object" ? src.rules : {})) if (typeof v === "boolean" && /^\w{1,40}$/.test(k)) rules[k] = v;
  return {
    version: PLAN_VERSION, targets, active, main,
    runs: (Array.isArray(src.runs) ? src.runs : []).map(cleanRun).filter(Boolean).slice(-MAX_RUNS),
    userBooks: (Array.isArray(src.userBooks) ? src.userBooks : []).map(cleanUserBook).filter(Boolean).slice(-MAX_USER_BOOKS),
    rules, migrated,
  };
}

export function activeTarget(plan) {
  return plan?.targets?.find((t) => t.id === plan.active) || null;
}

/** Scanned books + user-registered books (user books after, tagged source "user"). */
export function mergeBooks(scanned, plan) {
  const fps = new Set(scanned.map((b) => b.fingerprint));
  return [...scanned, ...(plan?.userBooks || []).filter((b) => !fps.has(b.fingerprint))];
}

// ---- immutable updates (each returns a new plan) -------------------------------------------------------------------
export function upsertTarget(plan, target) {
  const clean = cleanTarget(target);
  if (!clean) return plan;
  const exists = plan.targets.some((t) => t.id === clean.id);
  if (!exists && plan.targets.length >= MAX_TARGETS) return plan;
  const targets = exists ? plan.targets.map((t) => (t.id === clean.id ? clean : t)) : [...plan.targets, clean];
  return { ...plan, targets, active: plan.active || clean.id };
}

export function removeTarget(plan, id) {
  const targets = plan.targets.filter((t) => t.id !== id);
  const main = { ...plan.main };
  delete main[id];
  return { ...plan, targets, main, active: plan.active === id ? targets[0]?.id || null : plan.active };
}

export function setActive(plan, id) {
  return plan.targets.some((t) => t.id === id) ? { ...plan, active: id } : plan;
}

export function setMain(plan, targetId, fingerprint) {
  const main = { ...plan.main };
  if (fingerprint) main[targetId] = fingerprint; else delete main[targetId];
  return { ...plan, main };
}

export function addTargetItem(target, item) {
  const clean = cleanTargetItem(item);
  if (!clean || target.items.some((i) => normalizeName(i.name) === clean.name && i.role === clean.role)) return target;
  return { ...target, items: [...target.items, clean] };
}

/** Register result books (manual entry) and a STEP run; the run waits for a decision. */
export function recordRun(plan, { target, inputs, results, mode = null, at = new Date().toISOString(), id = newId("r") }) {
  const books = results.filter((r) => r && typeof r === "object").map((r, i) => cleanUserBook({ ...r, fingerprint: r.fingerprint || `u-${id}-${i}`, seen: at, run: id })).filter(Boolean);
  const existing = results.filter((r) => typeof r === "string");
  const run = cleanRun({ id, at, target, scenario: "step", inputs, outputs: [...books.map((b) => b.fingerprint), ...existing],
    mainBefore: plan.main[target] || null, mode });
  return { ...plan, runs: [...plan.runs, run].slice(-MAX_RUNS), userBooks: [...plan.userBooks, ...books].slice(-MAX_USER_BOOKS) };
}

/**
 * Decide a run: "main" sets the chosen output as the Target's main book; "backup" keeps outputs without switching;
 * "stop" / "failed" keep everything (previous books are never removed). Returns the new plan.
 */
export function decideRun(plan, runId, decision, output = null) {
  if (!DECISIONS.includes(decision)) return plan;
  let next = { ...plan, runs: plan.runs.map((r) => (r.id === runId ? { ...r, decision } : r)) };
  const run = next.runs.find((r) => r.id === runId);
  if (run && decision === "main" && (output || run.outputs[0])) next = setMain(next, run.target, output || run.outputs[0]);
  return next;
}

/** Undo a run's decision: the main book goes back to what it was before the run; result books stay. */
export function revertRun(plan, runId) {
  const run = plan.runs.find((r) => r.id === runId);
  if (!run) return plan;
  let next = { ...plan, runs: plan.runs.map((r) => (r.id === runId ? { ...r, reverted: true } : r)) };
  if (run.decision === "main") next = setMain(next, run.target, run.mainBefore);
  return next;
}

export function removeUserBook(plan, fingerprint) {
  const main = Object.fromEntries(Object.entries(plan.main).filter(([, fp]) => fp !== fingerprint));
  return { ...plan, userBooks: plan.userBooks.filter((b) => b.fingerprint !== fingerprint), main };
}

/** Runs for a Target, newest first, and the count of trailing runs without progress (failed / stop). */
export function runsFor(plan, targetId) {
  const runs = plan.runs.filter((r) => r.target === targetId && !r.reverted).reverse();
  let failures = 0;
  for (const r of runs) { if (r.decision === "failed" || r.decision === "stop") failures += 1; else break; }
  return { runs, failures, last: runs[0] || null };
}

/** Production history of one book: runs that produced it (parents = their inputs) and runs that used it as input. */
export function bookHistory(plan, fingerprint) {
  const made = plan.runs.filter((r) => r.outputs.includes(fingerprint));
  const used = plan.runs.filter((r) => r.inputs.includes(fingerprint));
  return { made, used, parents: [...new Set(made.flatMap((r) => r.inputs))] };
}

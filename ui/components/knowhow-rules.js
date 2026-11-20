// ノウハウ rule engine (pure, node-tested by tests/test_knowhow_rules.py). One place for the STEP / say "Halo" book rules
// the 带书 / 书库 / 目标 pages use: entry parsing, Target evaluation and validation, inventory analysis, book
// classification, stage detection, explainable STEP 3-book recommendations, STEP result comparison and the next action.
// Advice only: Maa never sells, protects, marks, deletes or picks a book in the game. No probability is computed:
// scores are explainable heuristics (shown as reasons, never as a success rate). Every rule carries its evidence status and
// source; the rules are fixed, there are no user switches.
import { nameMatches, normalizeName } from "./knowhow-data.js";

export const MAX_ENTRIES = 21;          // 一本最多 21 个ノウハウ，含头（Wiki，原生详情「ノウハウ数 N/21」）
export const STEP_INPUTS = 3;
export const HALO_INPUTS = 1;
export const HALO_DISCOUNT = { 1: 5, 2: 10, 3: 15, 4: 20, 5: 30, 6: 50 };  // % OFF（Wiki Halo 页 + 英文 Wiki）
export const ROLES = ["required", "preferred", "optional", "excluded"];
export const ROLE_LABEL = { required: "必需", preferred: "优先", optional: "可选", excluded: "排除" };
export const ROLE_TIP = { required: "没有就不算完成", preferred: "尽量有；完成条件选「必需和优先都有」时也要有", optional: "有更好，不影响完成",
  excluded: "不想要的条目：占格，选书时避开含它的书" };
/** One-line meaning shown next to each role group (2026-10-26 user: 目标条目 should explain itself). */
export const ROLE_SHORT = { required: "没有不算完成", preferred: "尽量有", optional: "有更好", excluded: "选书时避开" };
export const STRATEGIES = ["quick", "balanced", "optimized"];
// 「完成条件」 (2026-10-26 user: 完成标准 read as jargon): plain options; 「达到」 = at or above the item's 最低 Lv.
export const STRATEGY_LABEL = { quick: "必需的都有", balanced: "必需和优先都有", optimized: "都有且到理想 Lv" };
export const MODES = ["safe", "fast", "preserve"];
export const MODE_LABEL = { safe: "稳妥", fast: "快速", preserve: "省素材" };
export const ATTRS = ["vo", "da", "vi", "me"];
export const ATTR_LABEL = { vo: "Vo", da: "Da", vi: "Vi", me: "Me" };
export const STAGES = ["collect", "separate", "group", "final", "halo", "done"];
export const STAGE_LABEL = {
  collect: "素材收集", separate: "剥离", group: "分组浓缩", final: "最终浓缩", halo: "Halo 复制/追い出し", done: "已达成",
};
export const CLASS_LABEL = {
  completed: "成品", intermediate: "中间书", group: "浓缩组", separation: "剥离素材", raw: "原料", other: "未命中",
};

/**
 * Rule table (2026-10-24: settled by research; no user switches). status: 确认 / 部分确认 / 否定 / 无资料. Sources:
 * W1 = シャニマス攻略 Wiki* ノウハウブック, W2 = S.T.E.P.編, W3 = say "Halo"編, E1/E2 = shinycolors.wiki,
 * N = dated community notes. Nothing here is a probability.
 */
export const RULES = [
  { key: "step_inputs", label: "S.T.E.P. 带入 3 本，Halo 带入 1 本", value: true, status: "确认", source: "W1 / 官方帮助" },
  { key: "max_entries", label: "一本最多 21 个ノウハウ（含头与目標達成）", value: MAX_ENTRIES, status: "确认", source: "W1（2024-03-11 起）；原生详情 N/21" },
  { key: "head_one", label: "头（基礎）ノウハウ每本 1 个：{Vocal/Dance/Visual/メンタル}UP 或 上限UP，可带 +/++/+++", value: true, status: "确认", source: "W1" },
  { key: "step_head_certain", label: "S.T.E.P.：头在 S2/S3/S4 末必定继承，可成长为 +/++/+++（成长时 Lv 在 1–5 重抽）", value: true, status: "确认", source: "W1" },
  { key: "step_random", label: "S.T.E.P.：普通条目随机继承，Lv 越高越容易继承；多本带同一条目提高机会（无公开概率）", value: true, status: "确认", source: "W1 / E1" },
  { key: "step_consumes", label: "带入 S.T.E.P. / Halo 的书不消耗，可反复使用", value: false, status: "否定", source: "W1「持ち込んだノウハウブックはなくならない」；E1" },
  { key: "halo_all", label: "Halo：带入书的全部条目在 S1 末必定继承", value: true, status: "确认", source: "W1 / E2" },
  { key: "halo_discount", label: "Halo：Lv1–6 习得折扣 5/10/15/20/30/50% OFF；仍需付成长点", value: HALO_DISCOUNT, status: "确认", source: "W3 / E2" },
  { key: "level_no_effect", label: "同名同效果档，Lv 不改变效果强弱；+/++/+++ 是不同效果档", value: true, status: "确认", source: "W1" },
  { key: "transfer", label: "继承成功且可转记的条目写入结束时的新书", value: true, status: "确认", source: "W1" },
  { key: "inherited_level_kept", label: "转记按原 Lv 写入；本局新発現的版本会覆盖带入的版本", value: true, status: "确认", source: "W1 / W4（2022–2023）" },
  { key: "exclusive_groups", label: "同组一本只留 1 个：思い出、ファンの声援、エキシビション、目標達成、LP大成功(SP)、同名 W.I.N.G. 试镜曲", value: true, status: "确认", source: "W1 / W2 / 拍悟 2025-04" },
  { key: "rediscovery_overrides", label: "目標達成 / エキシビション / ファンの声援 会被本局新発現的版本覆盖", value: true, status: "确认", source: "W1" },
  { key: "overflow_pushout", label: "超过 21 个时从头以外随机删除（目标条目也可能被删；ファンの声援不优先）", value: true, status: "部分确认", source: "W1；实测 2025-08（8 次）否定ファンの声援优先" },
  { key: "low_level_interference", label: "转记 Lv 取实际被继承的那一份：混带同名低 Lv 副本时结果可能是低 Lv", value: true, status: "部分确认", source: "W4 评论 2022-05" },
  { key: "non_transferable", label: "不可转记：WING優勝/準決勝、ファン感謝祭系、G.R.A.D. 予選/決勝等、LandingPoint(属性)、トワコレ、283 书的曲目（仍可直接带原书使用）", value: true, status: "确认", source: "W1；enza #5428（2025-04）" },
  { key: "idol_restriction", label: "不能带与编成 P/S 同偶像的书；283プロのノウハウブック 例外", value: true, status: "确认", source: "官方帮助 / W1" },
  { key: "lv6_items_only", label: "Lv6 只来自道具ノウハウ", value: true, status: "确认", source: "W1" },
  { key: "halo_copy_approx", label: "Halo 产书 ≈ 带入书的可转记部分 + 本局新発現条目（头可能成长或重抽）", value: true, status: "确认", source: "W1 / W3 / E2；すくあ 2025-06 等" },
  { key: "separation", label: "剥离（ふるい落し）：借 S.T.E.P. 随机继承去掉杂项，结果随机；Halo 无法剥离", value: true, status: "确认", source: "W1 / W2；綿貫狸 2025-04" },
  { key: "head_is_first_row", label: "头ノウハウ固定在第一行", value: true, status: "确认", source: "W1「必ず一番最初に記載される」；E1" },
  { key: "special_slots", label: "没有特殊格位限制，只有数量约束", value: false, status: "否定", source: "W1 / W2" },
  { key: "probability", label: "继承/発現/挤出概率", value: null, status: "无资料", source: "无官方或可靠数据，Maa 不计算概率" },
];

const RULE_VALUES = Object.fromEntries(RULES.map((r) => [r.key, r.value]));

/** Rule values (fixed). The argument is ignored: user switches were removed with the rule table. */
export function ruleValues() {
  return { ...RULE_VALUES };
}

// ----------------------------------------------------------------------------------------------- entries
// Head = {stat}UP or {stat}上限UP with 0–3 「+」 (W1). G.R.A.D. abilities 「Vocal上限+」 etc. are not heads.
const HEAD = /^(Vocal|Dance|Visual|メンタル)(UP|上限UP)\+{0,3}$/;
const HEAD_ATTR = { Vocal: "vo", Dance: "da", Visual: "vi", "メンタル": "me" };
const NON_TRANSFERABLE = [/^WING優勝/, /^WING準決勝/, /トワコレ|トヴコレ/, /^ファン感謝祭/, /^ファン感MVP/, /^G\.?R\.?A\.?D\.?(予選|敗者復活|決勝)/,
  /^G\.R\.A\.D\.\((Vo|Da|Vi|Le|Ce)\)$/, /^LandingPoint\((Vo|Da|Vi|Me)\)$/, /^LandingPoint大成功\(アビリティ\)/, /^W\.I\.N\.G\.\(/,
  /^(なないろ歌合戦|週刊プリズムメロディーズ|TOPSONGS|七彩メモリーズ)/];
// W.I.N.G. audition songs: the (Vo)/(Da)/(Vi) versions of one song share a group (W1 / W2).
const SONGS = /^(今がキラキラ!おもいッきりアイドル!|おはよう街角の朝ごはん|HOPPINJAM|エレぇベスト|踊っていいとも\?増刊号|歌姫楽宴)\((Vo|Da|Vi)\)/;
const EXCLUSIVE = [["exhibition", /^エキシビションマッチ/], ["memory", /^思い出\(ノウハウ\)/], ["goal", /目標達成/], ["fans", /^ファンの声援/],
  ["landing_sp", /^LandingPoint大成功\(SP\)/]];
const SOURCES = [
  [/\(G\.R\.A\.D\.\)|GRAD/, "G.R.A.D."], [/\(W\.I\.N\.G\.\)|^WING/, "W.I.N.G."], [/LandingPoint|Landing Point/, "Landing Point"],
  [/ファン感/, "ファン感謝祭"], [/目標達成/, "S.T.E.P."], [/トワコレ/, "トワコレ"],
  [/^歌姫(?!楽宴)|^プロダンサー|^トップモデル|^昂揚|^施設効率相談|^パッシブスキル延長|^ファイナルライブ|^アイドル\(|say"Halo"/, "say \"Halo\""],
];

/** Parsed facts about one entry name: kind (head/other), attribute, effect tier (+ count), transferable, exclusive group. */
export function parseEntry(name) {
  const n = normalizeName(name);
  const head = HEAD.exec(n);
  const attrMatch = /\((Vo|Da|Vi|Me)\)$/i.exec(n);
  const attr = head ? HEAD_ATTR[head[1]] : attrMatch ? attrMatch[1].toLowerCase() : null;
  const tierMatch = /(\+{1,3})(\([^)]*\))?$/.exec(n);
  const base = n.replace(/(\+{1,3})(\([^)]*\))?$/, "$2");
  const source = SOURCES.find(([re]) => re.test(n));
  return {
    name: n, base, kind: head ? "head" : "other", headType: head ? head[2] : null, attr,
    tier: tierMatch ? tierMatch[1].length : 0,
    transferable: NON_TRANSFERABLE.some((re) => re.test(n)) ? false : true,
    exclusive: EXCLUSIVE.find(([, re]) => re.test(n))?.[0] || (SONGS.exec(n) ? `song:${SONGS.exec(n)[1]}` : null),
    source: head ? "任意剧本（头）" : source ? source[1] : null,
  };
}

/** The book's header entry: the first row when it is a head type (rule head_is_first_row), else the first head row. */
export function headOf(book, rules = ruleValues()) {
  const rows = book?.rows || [];
  if (rules.head_is_first_row && rows[0] && HEAD.test(normalizeName(rows[0].name))) return rows[0];
  return rows.find((r) => HEAD.test(normalizeName(r.name))) || null;
}

// ----------------------------------------------------------------------------------------------- Target
const MAX_TARGET_ITEMS = 40;
const MAX_TEXT = 40;
const FORBIDDEN = /[\u0000-\u001f/\\:<>"'`]/;
const cleanText = (v, max = MAX_TEXT) => {
  const s = normalizeName(v);
  return s && s.length <= max && !FORBIDDEN.test(s) ? s : null;
};
/** Display names keep their inner spaces (normalizeName strips them; that is for ノウハウ matching only). */
const cleanLabel = (v, max) => {
  const s = String(v ?? "").normalize("NFKC").replace(/\s+/g, " ").trim();
  return s && s.length <= max && !FORBIDDEN.test(s) ? s : null;
};
const lvOf = (v, fallback) => (Number.isInteger(v) && v >= 1 && v <= 6 ? v : fallback);
const pick = (v, list, fallback) => (list.includes(v) ? v : fallback);

export function cleanTargetItem(raw) {
  if (!raw || typeof raw !== "object") return null;
  const name = cleanText(raw.name);
  if (!name) return null;
  const min = lvOf(raw.min_level, 1);
  const item = {
    name, match: pick(raw.match, ["exact", "prefix", "contains"], "exact"), role: pick(raw.role, ROLES, "required"),
    min_level: min, ideal_level: Math.max(min, lvOf(raw.ideal_level, min)),
  };
  const group = typeof raw.group === "string" ? raw.group.trim().slice(0, 20) : "";
  if (group && !FORBIDDEN.test(group)) item.group = group;
  if (["center", "leader", "vocal", "dance", "visual"].includes(raw.position)) item.position = raw.position;
  if (raw.purpose === "halo" || raw.purpose === "step") item.purpose = raw.purpose;  // kept from the migrated wanted list
  return item;
}

export function cleanTarget(raw, index = 0) {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" && /^[\w-]{1,40}$/.test(raw.id) ? raw.id : `t${index + 1}`;
  const items = [];
  const seen = new Set();
  for (const value of Array.isArray(raw.items) ? raw.items : []) {
    const item = cleanTargetItem(value);
    const key = item && `${item.name}|${item.match}|${item.role === "excluded" ? "x" : "w"}`;
    if (!item || seen.has(key) || items.length >= MAX_TARGET_ITEMS) continue;
    seen.add(key);
    items.push(item);
  }
  return {
    id, name: cleanLabel(raw.name, 30) || "未命名目标",
    scenario: pick(raw.scenario, ["halo", "step", "any"], "any"),
    position: pick(raw.position, ["", "center", "leader", "vocal", "dance", "visual"], ""),
    attribute: pick(raw.attribute, ["", ...ATTRS], ""),
    strategy: pick(raw.strategy, STRATEGIES, "balanced"),
    items, source: typeof raw.source === "string" ? raw.source.slice(0, 40) : "user",
    note: typeof raw.note === "string" ? raw.note.slice(0, 200) : "",
  };
}

/**
 * Requirement units: items sharing a `group` are substitutes (any one meeting its Lv satisfies the unit); others are
 * their own unit. Role of a group = strongest member role. Excluded items are separate units of role "excluded".
 */
export function targetUnits(target) {
  const rank = { required: 0, preferred: 1, optional: 2 };
  const units = new Map();
  for (const item of target?.items || []) {
    const key = item.role === "excluded" ? `x:${item.name}` : item.group ? `g:${item.group}` : `i:${item.name}|${item.match}`;
    const unit = units.get(key) || { key, label: item.group || item.name, role: item.role, items: [], group: item.group || null };
    unit.items.push(item);
    if (item.role !== "excluded" && rank[item.role] < rank[unit.role]) unit.role = item.role;
    units.set(key, unit);
  }
  return [...units.values()];
}

/** Which roles / Lv count as "done" under the Target strategy (Quick / Balanced / Optimized). */
export function strategyNeeds(strategy) {
  if (strategy === "quick") return { roles: ["required"], level: "min" };
  if (strategy === "optimized") return { roles: ["required", "preferred"], level: "ideal" };
  return { roles: ["required", "preferred"], level: "min" };
}

/** Best match of one unit on a set of rows: {level, name, meets (≥min), ideal (≥ideal), item} or null. */
export function unitHit(unit, rows) {
  let best = null;
  for (const item of unit.items) {
    for (const row of rows || []) {
      if (!Number.isInteger(row.level) || !nameMatches(item, row.name)) continue;
      const hit = { level: row.level, name: row.name, item, meets: row.level >= item.min_level, ideal: row.level >= item.ideal_level };
      if (!best || Number(hit.meets) - Number(best.meets) > 0 || (hit.meets === best.meets && hit.level > best.level)) best = hit;
    }
  }
  return best;
}

/**
 * Book (or any rows) against a Target: per-unit status, excluded present, noise (entries matching no unit), progress
 * per the strategy (needed units met), slots used and the head entry.
 */
export function evaluateBook(book, target, rules = ruleValues()) {
  const rows = book?.rows || [];
  const units = targetUnits(target);
  const needs = strategyNeeds(target?.strategy);
  const status = units.map((unit) => {
    const hit = unitHit(unit, rows);
    const state = unit.role === "excluded" ? (hit ? "excluded" : "clear")
      : !hit ? "missing" : !hit.meets ? "below" : hit.ideal ? "ideal" : "met";
    const done = unit.role !== "excluded" && (needs.level === "ideal" ? state === "ideal" : state === "met" || state === "ideal");
    return { unit, hit, state, done };
  });
  const matched = new Set(status.filter((s) => s.hit).map((s) => s.hit.name));
  const head = headOf(book, rules);
  const noise = rows.filter((r) => !matched.has(r.name) && r !== head).map((r) => r.name);
  const needed = status.filter((s) => needs.roles.includes(s.unit.role));
  const required = status.filter((s) => s.unit.role === "required");
  return {
    status, noise, head: head ? head.name : null, slots: rows.length,
    excluded: status.filter((s) => s.state === "excluded").map((s) => s.hit.name),
    progress: { done: needed.filter((s) => s.done).length, total: needed.length },
    required: { done: required.filter((s) => s.done).length, total: required.length },
    complete: needed.length > 0 && needed.every((s) => s.done) && !status.some((s) => s.state === "excluded"),
  };
}

/**
 * Target checks: slot limit (units + head ≤ 21), required/excluded conflicts, exclusive-group conflicts,
 * non-transferable required items for STEP, Lv6 on non-item entries, missing materials (with books given).
 * Returns [{level: "error"|"warning"|"info", code, text}].
 */
export function validateTarget(target, books = null, rules = ruleValues()) {
  const issues = [];
  const units = targetUnits(target);
  const want = units.filter((u) => u.role !== "excluded");
  const count = (roles) => want.filter((u) => roles.includes(u.role)).length;
  const slots = (rules.max_entries || MAX_ENTRIES) - 1;  // head occupies one of the 21
  if (!want.length) issues.push({ level: "info", code: "empty", text: "还没有条目：从推荐目标开始，或在下方添加条目。" });
  if (count(["required"]) > slots) issues.push({ level: "error", code: "slots_required", text: `必需 ${count(["required"])} 项，超过一本可放的 ${slots} 项（21 格含头）。` });
  else if (count(["required", "preferred"]) > slots) issues.push({ level: "warning", code: "slots_preferred", text: `必需+优先 ${count(["required", "preferred"])} 项，超过 ${slots} 项，放不进一本；S.T.E.P. 可用互补的三本。` });
  else if (want.length > slots) issues.push({ level: "info", code: "slots_optional", text: `全部 ${want.length} 项超过 ${slots} 项，可选项不一定放得下。` });
  const wanted = target?.items?.filter((i) => i.role !== "excluded") || [];
  for (const ex of target?.items?.filter((i) => i.role === "excluded") || []) {
    const clash = wanted.find((w) => normalizeName(w.name) === normalizeName(ex.name));
    if (clash) issues.push({ level: "error", code: "excluded_conflict", text: `「${ex.name}」同时是想要和排除。` });
  }
  if (rules.exclusive_groups) {
    const byGroup = new Map();
    for (const unit of want.filter((u) => u.role === "required")) {
      const groups = new Set(unit.items.map((i) => parseEntry(i.name).exclusive).filter(Boolean));
      for (const g of groups) byGroup.set(g, [...(byGroup.get(g) || []), unit.label]);
    }
    for (const [, labels] of byGroup) {
      if (labels.length > 1) issues.push({ level: "warning", code: "exclusive", text: `${labels.join("、")} 同组，一本只留 1 个。` });
    }
  }
  if (target?.scenario !== "halo") {
    for (const unit of want.filter((u) => u.role === "required")) {
      if (unit.items.every((i) => parseEntry(i.name).transferable === false)) {
        issues.push({ level: "warning", code: "non_transferable", text: `「${unit.label}」不可转记，S.T.E.P. 浓缩不进新书，只能直接带原书。` });
      }
    }
  }
  for (const item of wanted) {
    if (item.min_level === 6 || item.ideal_level === 6) issues.push({ level: "info", code: "lv6", text: `「${item.name}」Lv6 只来自道具ノウハウ。` });
  }
  if (Array.isArray(books)) {
    const missing = want.filter((u) => u.role === "required" && !books.some((b) => unitHit(u, b.rows)?.meets));
    if (missing.length) issues.push({ level: "warning", code: "missing", text: `书库中没有达到最低 Lv 的：${missing.map((u) => u.label).join("、")}。` });
  }
  return issues;
}

// ----------------------------------------------------------------------------------------------- inventory
const GENERAL = /^283プロのノウハウブック/;
export function usableFor(book, team) {
  if (!book?.idol && GENERAL.test(normalizeName(book?.title))) return "ok";
  if (!book?.idol) return "unknown";
  if (team?.idols?.includes(normalizeName(book.idol))) return "blocked";
  return team?.verified ? "ok" : "unknown";
}

/**
 * Inventory against a Target: per unit the holders (best Lv first), whether only one physical book holds it at ≥ min
 * (protection warning when that book is not 保護), and an acquisition hint from the entry name (heuristic).
 */
export function analyzeInventory(books, target, team = null) {
  const units = targetUnits(target).filter((u) => u.role !== "excluded");
  return units.map((unit) => {
    const holders = [];
    for (const book of books) {
      const hit = unitHit(unit, book.rows);
      if (hit) holders.push({ book, hit, usable: usableFor(book, team) });
    }
    holders.sort((a, b) => Number(b.hit.meets) - Number(a.hit.meets) || b.hit.level - a.hit.level);
    const meeting = holders.filter((h) => h.hit.meets);
    const physical = meeting.reduce((s, h) => s + (h.book.count || 1), 0);
    const source = unit.items.map((i) => parseEntry(i.name).source).find(Boolean) || null;
    return {
      unit, holders, meeting: meeting.length, best: holders[0]?.hit.level ?? null,
      usable: meeting.filter((h) => h.usable !== "blocked").length,
      unique: physical === 1 ? meeting[0].book : null,
      protectWarn: physical === 1 && meeting[0].book.protected !== true,
      source: source || "普通条目",
    };
  });
}

/**
 * say "Halo" single books for a Target (2026-10-24 role-play: ranking by the raw hit count put a book without the
 * required Center適正 first). Order: usable before unread idols (verified team only), more required and preferred units
 * met, fewer excluded entries (Halo inherits all of them: they only take slots), more optional units, higher Lv sum
 * (習得折扣), higher 評価値. A position keeps the items without one.
 * Returns {total (strategy units), candidates, picks: [{book, usability, done, hits (required first), gaps}]}.
 */
export function haloRank(books, target, team = null, { position = "", limit = 3, rules = ruleValues() } = {}) {
  const t = position ? { ...target, items: (target?.items || []).filter((i) => !i.position || i.position === position) } : target;
  const order = { required: 0, preferred: 1, optional: 2 };
  const rows = [];
  for (const book of books) {
    const usability = usableFor(book, team);
    if (usability === "blocked") continue;
    const ev = evaluateBook(book, t, rules);
    const met = ev.status.filter((s) => s.unit.role !== "excluded" && s.hit?.meets);
    if (!met.length) continue;
    const count = (role) => met.filter((s) => s.unit.role === role).length;
    rows.push({ book, usability, ev, met, key: [team?.verified && usability !== "ok" ? 1 : 0, -count("required"), -count("preferred"),
      ev.excluded.length, -count("optional"), -met.reduce((n, s) => n + s.hit.level, 0), -(book.eval_value || 0)] });
  }
  rows.sort((a, b) => { for (let i = 0; i < a.key.length; i += 1) if (a.key[i] !== b.key[i]) return a.key[i] - b.key[i]; return 0; });
  const needed = strategyNeeds(t?.strategy).roles;
  return {
    total: targetUnits(t).filter((u) => needed.includes(u.role)).length, candidates: rows.length,
    picks: rows.slice(0, limit).map(({ book, usability, ev, met }) => ({
      book, usability, done: ev.progress.done,
      hits: [...met].sort((a, b) => order[a.unit.role] - order[b.unit.role] || b.hit.level - a.hit.level)
        .map((s) => ({ name: s.hit.name, level: s.hit.level, item: s.hit.item, role: s.unit.role, label: s.unit.label })),
      gaps: ev.status.filter((s) => needed.includes(s.unit.role) && !s.hit?.meets).map((s) => ({ name: s.unit.label, role: s.unit.role })),
    })),
  };
}

/**
 * Duplicates / low-Lv conflicts / redundancy inside the library: entries a book holds that another same-content or
 * higher-Lv holder already covers. Facts only; never a sale verdict.
 */
export function libraryConflicts(book, books) {
  const out = { duplicateCopies: Math.max(0, (book.count || 1) - 1), lowerThan: [], sameNameLevels: [] };
  const names = new Map();
  for (const r of book.rows) names.set(r.name, [...(names.get(r.name) || []), r.level]);
  for (const [name, levels] of names) if (levels.length > 1) out.sameNameLevels.push({ name, levels });
  for (const r of book.rows) {
    if (!Number.isInteger(r.level)) continue;
    const better = books.find((o) => o !== book && o.rows.some((x) => x.name === r.name && x.level > r.level));
    if (better) out.lowerThan.push({ name: r.name, level: r.level, by: better });
  }
  out.redundant = book.rows.length > 0 && out.lowerThan.length === book.rows.length;
  return out;
}

/**
 * Automatic classification against the active Target (display label only; the user's own tags are stored apart):
 * completed (meets the Target), intermediate (registered STEP output / main book with progress), group (holds ≥2
 * wanted units, a concentrate member), separation (wanted entries mixed with excluded or mostly noise), raw (one
 * wanted unit), other (no wanted entry).
 */
export function classifyBook(book, target, { plan = null, rules = ruleValues() } = {}) {
  if (!target || !targetUnits(target).some((u) => u.role !== "excluded")) return "other";
  const ev = evaluateBook(book, target, rules);
  const hits = ev.status.filter((s) => s.unit.role !== "excluded" && s.hit?.meets).length;
  if (ev.complete) return "completed";
  const produced = plan?.runs?.some((r) => !r.reverted && r.outputs?.includes(book.fingerprint));
  if (produced || plan?.main?.[target.id] === book.fingerprint) return "intermediate";
  if (hits && (ev.excluded.length || ev.noise.length > hits * 2)) return "separation";
  if (hits >= 2) return "group";
  if (hits === 1) return "raw";
  return "other";
}

// ----------------------------------------------------------------------------------------------- stage
/**
 * Current stage of a Target given the main book and the inventory:
 * done/halo (main meets the Target: Halo copy / 追い出し next or nothing), separate (main holds excluded entries or
 * cannot take the missing units within 21), final (main + 2 usable books cover every missing needed unit), group
 * (missing units are held, but spread over more books), collect (some needed units are held by nobody).
 */
export function detectStage(target, books, mainBook, team = null, rules = ruleValues()) {
  const needs = strategyNeeds(target?.strategy);
  const units = targetUnits(target).filter((u) => needs.roles.includes(u.role));
  const inv = analyzeInventory(books, target, team);
  const absent = inv.filter((a) => needs.roles.includes(a.unit.role) && !a.holders.some((h) => h.hit.meets && h.usable !== "blocked"));
  if (!mainBook) {
    return { stage: absent.length === units.length && units.length ? "collect" : "group", missing: units, absent: absent.map((a) => a.unit), main: null,
      reason: units.length ? "未指定主书。" : "目标没有必需/优先条目。" };
  }
  const ev = evaluateBook(mainBook, target, rules);
  const missing = ev.status.filter((s) => needs.roles.includes(s.unit.role) && !s.done).map((s) => s.unit);
  const absentKeys = new Set(absent.map((a) => a.unit.key));
  const base = { missing, absent: absent.map((a) => a.unit), main: ev };
  if (!missing.length && !ev.excluded.length) {
    return { ...base, stage: target.scenario === "step" ? "done" : "halo", reason: "主书已满足目标。" };
  }
  if (ev.excluded.length || ev.slots + missing.length > (rules.max_entries || MAX_ENTRIES)) {
    return { ...base, stage: "separate", reason: ev.excluded.length ? `主书含排除条目：${ev.excluded.join("、")}。` : `主书 ${ev.slots} 格，再加 ${missing.length} 项会超过 21 格。` };
  }
  const reachable = missing.filter((u) => !absentKeys.has(u.key));
  if (reachable.length < missing.length) {
    return { ...base, stage: "collect", reason: reachable.length ? `${missing.length - reachable.length} 项书库中没有，需采集；其余 ${reachable.length} 项可同时浓缩。` : "缺少的条目书库中都没有，需采集。" };
  }
  const pairs = minCover(reachable, books.filter((b) => b.fingerprint !== mainBook.fingerprint && usableFor(b, team) !== "blocked"));
  if (pairs !== null && pairs <= STEP_INPUTS - 1) {
    return { ...base, stage: "final", reason: `主书 + ${pairs} 本可以覆盖全部缺项。` };
  }
  return { ...base, stage: "group", reason: "缺项分散在多本书里，先两两浓缩成中间书。" };
}

/** Smallest number of books (≤ 3) that together meet every unit, or null. */
function minCover(units, books) {
  const useful = books.map((b) => ({ b, keys: new Set(units.filter((u) => unitHit(u, b.rows)?.meets).map((u) => u.key)) }))
    .filter((x) => x.keys.size).sort((a, b) => b.keys.size - a.keys.size).slice(0, 30);
  const all = units.map((u) => u.key);
  const covers = (sets) => all.every((k) => sets.some((s) => s.has(k)));
  for (const x of useful) if (covers([x.keys])) return 1;
  for (let i = 0; i < useful.length; i += 1) for (let j = i + 1; j < useful.length; j += 1) if (covers([useful[i].keys, useful[j].keys])) return 2;
  for (let i = 0; i < useful.length; i += 1) for (let j = i + 1; j < useful.length; j += 1) for (let k = j + 1; k < useful.length; k += 1) {
    if (covers([useful[i].keys, useful[j].keys, useful[k].keys])) return 3;
  }
  return null;
}

// ----------------------------------------------------------------------------------------------- STEP trio
const WEIGHTS = {
  safe: { required: 10, preferred: 4, optional: 1, fresh: 2, redundant: 3, ideal: 1, excluded: 8, noise: 1.5, lowLevel: 2, loss: 3, unknown: 3, unique: 0, reuse: 0 },
  fast: { required: 10, preferred: 4, optional: 1, fresh: 5, redundant: 1, ideal: 1, excluded: 6, noise: 0.5, lowLevel: 1, loss: 1, unknown: 2, unique: 0, reuse: 0 },
  preserve: { required: 10, preferred: 4, optional: 1, fresh: 2, redundant: 2, ideal: 1, excluded: 8, noise: 1, lowLevel: 1, loss: 2, unknown: 3, unique: 4, reuse: 2 },
};

/**
 * Explain one 3-book selection (manual or recommended) for a Target: per-book contribution / new units / noise /
 * excluded; trio coverage, redundancy, low-Lv interference, possible loss (main-book units held by one book only),
 * slot overflow risk, team conflicts and over-used copies. `score` is a heuristic for ordering, never a probability.
 */
export function analyzeTrio(trio, target, { mainBook = null, team = null, mode = "safe", plan = null, books = [], rules = ruleValues() } = {}) {
  const w = WEIGHTS[mode] || WEIGHTS.safe;
  const units = targetUnits(target);
  const wantUnits = units.filter((u) => u.role !== "excluded");
  const mainEval = mainBook ? evaluateBook(mainBook, target, rules) : null;
  const onMain = new Set(mainEval ? mainEval.status.filter((s) => s.hit?.meets).map((s) => s.unit.key) : []);
  const picked = trio.filter(Boolean);
  const perBook = picked.map((book) => {
    const ev = evaluateBook(book, target, rules);
    const gives = ev.status.filter((s) => s.unit.role !== "excluded" && s.hit?.meets);
    return {
      book, usable: usableFor(book, team), eval: ev,
      gives: gives.map((s) => ({ key: s.unit.key, label: s.unit.label, role: s.unit.role, level: s.hit.level, name: s.hit.name,
        transferable: parseEntry(s.hit.name).transferable })),
      fresh: gives.filter((s) => !onMain.has(s.unit.key)).map((s) => s.unit.label),
      noise: ev.noise, excluded: ev.excluded,
    };
  });
  const conflicts = [];
  const counts = new Map();
  for (const b of picked) counts.set(b.fingerprint, (counts.get(b.fingerprint) || 0) + 1);
  for (const [fp, n] of counts) {
    const book = picked.find((b) => b.fingerprint === fp);
    if (n > (book.count || 1)) conflicts.push({ level: "error", code: "copies", text: `「${bookName(book)}」只有 ${book.count || 1} 本，选了 ${n} 次。` });
  }
  for (const p of perBook) {
    if (p.usable === "blocked") conflicts.push({ level: "error", code: "blocked", text: `「${bookName(p.book)}」与编队偶像同名，不可带入。` });
    else if (p.usable === "unknown" && team?.verified) conflicts.push({ level: "warning", code: "unknown", text: `「${bookName(p.book)}」名义未读，带入前确认。` });
    if (p.excluded.length) conflicts.push({ level: "warning", code: "excluded", text: `「${bookName(p.book)}」含排除条目：${p.excluded.join("、")}。` });
  }
  if (picked.length && picked.length < STEP_INPUTS) conflicts.push({ level: "error", code: "count", text: `S.T.E.P. 需要 ${STEP_INPUTS} 本，现在 ${picked.length} 本。` });
  // coverage
  const coverage = wantUnits.map((unit) => {
    const holders = perBook.filter((p) => p.gives.some((g) => g.key === unit.key));
    const levels = holders.map((p) => p.gives.find((g) => g.key === unit.key).level);
    const allLevels = perBook.map((p) => unitHit(unit, p.book.rows)?.level).filter(Number.isInteger);
    return { unit, holders: holders.length, best: levels.length ? Math.max(...levels) : null,
      ideal: holders.some((p) => p.gives.find((g) => g.key === unit.key && g.level >= unit.items[0].ideal_level)),
      // a copy below the unit's min Lv brought next to one that meets it: the result keeps the inherited copy's Lv
      lowCopy: rules.low_level_interference && holders.length > 0 && allLevels.length > 1 && Math.min(...allLevels) < unit.items[0].min_level,
      onMain: onMain.has(unit.key) };
  });
  const covered = coverage.filter((c) => c.holders);
  const roleCount = (role) => covered.filter((c) => c.unit.role === role).length;
  const fresh = covered.filter((c) => !c.onMain);
  const redundant = covered.filter((c) => c.holders >= 2 && c.unit.role !== "optional");
  const lowLevel = coverage.filter((c) => c.lowCopy);
  const loss = covered.filter((c) => c.holders === 1 && c.onMain && c.unit.role !== "optional");
  const excludedNames = [...new Set(perBook.flatMap((p) => p.excluded))];
  const noiseNames = [...new Set(perBook.flatMap((p) => p.noise.filter((n) => parseEntry(n).transferable)))];
  const transferUnion = new Set([...covered.filter((c) => c.unit.items.some((i) => parseEntry(i.name).transferable)).map((c) => c.unit.key), ...noiseNames]);
  const overflow = rules.overflow_pushout && transferUnion.size + 1 > (rules.max_entries || MAX_ENTRIES);
  if (overflow) conflicts.push({ level: "warning", code: "overflow", text: `三本可转记条目最多 ${transferUnion.size + 1} 个，可能超过 21 格：超出部分从头以外随机删除，目标条目也可能被删。` });
  if (lowLevel.length) conflicts.push({ level: "info", code: "low_level", text: `同一条目有高低 Lv 混带：${lowLevel.map((c) => c.unit.label).join("、")}，结果可能是低 Lv。` });
  const nonTransfer = covered.filter((c) => c.unit.items.every((i) => parseEntry(i.name).transferable === false));
  if (nonTransfer.length) conflicts.push({ level: "info", code: "non_transferable", text: `${nonTransfer.map((c) => c.unit.label).join("、")} 不可转记：本局可用，但不会写入新书。` });
  // material use
  const uniqueUsed = mode === "preserve" ? perBook.filter((p) => p.book !== mainBook && (p.book.count || 1) === 1
    && p.gives.some((g) => books.filter((b) => unitHit(wantUnits.find((u) => u.key === g.key), b.rows)?.meets).length === 1)) : [];
  const reused = plan ? perBook.filter((p) => plan.runs?.some((r) => !r.reverted && r.outputs?.includes(p.book.fingerprint))) : [];
  if (rules.step_consumes && uniqueUsed.length) conflicts.push({ level: "warning", code: "unique_consumed", text: `会用掉唯一素材：${uniqueUsed.map((p) => bookName(p.book)).join("、")}。` });
  const unknown = perBook.filter((p) => p.usable === "unknown").length;
  const parts = {
    required: roleCount("required") * w.required, preferred: roleCount("preferred") * w.preferred, optional: roleCount("optional") * w.optional,
    fresh: fresh.length * w.fresh, redundant: redundant.length * w.redundant, ideal: covered.filter((c) => c.ideal).length * w.ideal,
    excluded: -excludedNames.length * w.excluded, noise: -noiseNames.length * w.noise, lowLevel: -lowLevel.length * w.lowLevel,
    loss: -loss.length * w.loss, unknown: -unknown * w.unknown, unique: -uniqueUsed.length * w.unique, reuse: reused.length * w.reuse,
  };
  const score = Object.values(parts).reduce((s, v) => s + v, 0);
  const needs = strategyNeeds(target?.strategy);
  const neededUnits = wantUnits.filter((u) => needs.roles.includes(u.role));
  const reach = neededUnits.filter((u) => covered.some((c) => c.unit.key === u.key && c.unit.items.some((i) => parseEntry(i.name).transferable))).length;
  return {
    books: perBook, coverage, covered: covered.map((c) => c.unit), gaps: coverage.filter((c) => !c.holders && c.unit.role !== "optional").map((c) => c.unit),
    fresh: fresh.map((c) => c.unit.label), redundant: redundant.map((c) => c.unit.label), lowLevel: lowLevel.map((c) => c.unit.label),
    loss: loss.map((c) => c.unit.label), excluded: excludedNames, noise: noiseNames, conflicts, parts, score,
    reach: { done: reach, total: neededUnits.length },
    valid: picked.length === STEP_INPUTS && !conflicts.some((c) => c.level === "error"),
  };
}

function bookName(book) {
  return book?.idol || book?.title || "（名义未读）";
}

/**
 * Recommend STEP trios for a Target. Units = usable books with ≥1 wanted hit (or the main book), copies up to their
 * physical count; the main book is anchored when given. Candidates are ranked per mode with analyzeTrio's heuristic
 * (coverage + priorities + new units + Lv + duplicates/low-Lv + unwanted + loss + rarity/reuse), not coverage alone.
 * Returns {best, alternatives (one per mode), candidates, exhaustive}.
 */
export function recommendStep(books, target, { mainBook = null, team = null, mode = "safe", plan = null, rules = ruleValues(), maxUnits = 30, anchor = true } = {}) {
  const wantUnits = targetUnits(target).filter((u) => u.role !== "excluded");
  const pool = [];
  books.forEach((book, order) => {
    if (usableFor(book, team) === "blocked") return;
    const hits = wantUnits.filter((u) => unitHit(u, book.rows)?.meets).length;
    if (!hits && book !== mainBook) return;
    for (let copy = 0; copy < Math.min(book.count || 1, STEP_INPUTS); copy += 1) pool.push({ book, hits, order, copy });
  });
  const anchored = anchor && mainBook && pool.some((u) => u.book === mainBook);
  const rest = pool.filter((u) => !(anchored && u.book === mainBook && u.copy === 0))
    .sort((a, b) => b.hits - a.hits || a.order - b.order || a.copy - b.copy);
  const units = rest.slice(0, maxUnits);
  const evaluate = (m) => {
    let best = null;
    const consider = (trio) => {
      const res = analyzeTrio(trio, target, { mainBook, team, mode: m, plan, books, rules });
      const key = res.score + (res.valid ? 1000 : 0);
      if (!best || key > best.key) best = { key, res, trio };
    };
    if (anchored) {
      for (let i = 0; i < units.length; i += 1) for (let j = i + 1; j < units.length; j += 1) consider([mainBook, units[i].book, units[j].book]);
      if (units.length < 2) consider([mainBook, ...units.map((u) => u.book)]);
    } else if (units.length >= 3) {
      for (let i = 0; i < units.length; i += 1) for (let j = i + 1; j < units.length; j += 1) for (let k = j + 1; k < units.length; k += 1) {
        consider([units[i].book, units[j].book, units[k].book]);
      }
    } else if (units.length) {
      consider(units.map((u) => u.book));
    }
    return best ? { mode: m, trio: best.trio, ...best.res } : null;
  };
  const alternatives = MODES.map(evaluate).filter(Boolean);
  return { best: alternatives.find((a) => a.mode === mode) || null, alternatives, candidates: pool.length,
    exhaustive: rest.length <= maxUnits, anchored: Boolean(anchored) };
}

// ----------------------------------------------------------------------------------------------- STEP route
/**
 * Whole STEP route from the library to one book meeting the Target (advice only; no probability). Needed units (per
 * the strategy) that usable books meet with a transferable entry are covered greedily (main book first, then books
 * without excluded entries, most new units, less noise, higher Lv); the chosen books are merged three at a time into
 * intermediate books (two and a filler when one merge is enough), keeping the main book for the final run. Fillers
 * repeat the units only one input holds (more copies, more chances; no excluded entry when a clean book exists), else
 * add the fewest new entries. `absent` = needed units no usable book meets (collect first); `direct` = met only by
 * non-transferable entries (bring that book itself). Planned from the library on every call, so a registered result
 * shortens the route by itself.
 * Returns {units, absent, direct, runs: [{index, final, now, inputs: [{book, filler} | {run}], gives, risks}], done, book, base}.
 */
export function planRoute(books, target, { mainBook = null, team = null, rules = ruleValues() } = {}) {
  const needs = strategyNeeds(target?.strategy);
  const units = targetUnits(target).filter((u) => needs.roles.includes(u.role));
  const out = { units, absent: [], direct: [], runs: [], done: false, book: null, base: null };
  if (!units.length) return out;
  const info = books.filter((b) => b === mainBook || usableFor(b, team) !== "blocked").map((book) => {
    const ev = evaluateBook(book, target, rules);
    const held = ev.status.filter((s) => s.done && needs.roles.includes(s.unit.role));
    const head = headOf(book, rules);
    return { book, ev, lv: held.reduce((sum, s) => sum + s.hit.level, 0), usable: usableFor(book, team),
      keys: new Set(held.filter((s) => parseEntry(s.hit.name).transferable).map((s) => s.unit.key)),
      any: new Set(held.map((s) => s.unit.key)),
      rows: new Set(book.rows.filter((r) => r !== head && parseEntry(r.name).transferable).map((r) => normalizeName(r.name))) };
  });
  const complete = info.find((x) => x.book === mainBook && x.ev.complete) || info.find((x) => x.ev.complete);
  if (complete) return { ...out, done: true, book: complete.book };
  const reach = new Set(info.flatMap((x) => [...x.keys]));
  const met = new Set(info.flatMap((x) => [...x.any]));
  out.absent = units.filter((u) => !met.has(u.key));
  out.direct = units.filter((u) => met.has(u.key) && !reach.has(u.key));
  // leaves: greedy cover of the reachable units, then drop a leaf the others already cover
  const need = new Set(reach);
  const main = mainBook ? info.find((x) => x.book === mainBook) || null : null;
  const chosen = [];
  const take = (x) => { chosen.push(x); for (const k of x.keys) need.delete(k); };
  // a book with excluded entries only when no clean book adds the unit (shedding them needs extra random runs)
  const better = (a, b) => b.x.ev.excluded.length - a.x.ev.excluded.length || a.gain - b.gain
    || b.x.ev.noise.length - a.x.ev.noise.length || a.x.lv - b.x.lv;
  if (main) take(main);
  while (need.size) {
    let best = null;
    for (const x of info) {
      if (chosen.includes(x)) continue;
      const gain = [...x.keys].filter((k) => need.has(k)).length;
      if (gain && (!best || better({ x, gain }, best) > 0)) best = { x, gain };
    }
    if (!best) break;
    take(best.x);
  }
  for (let i = chosen.length - 1; i >= 0; i -= 1) {
    const others = chosen.filter((_, j) => j !== i);
    if (chosen[i] !== main && [...chosen[i].keys].every((k) => others.some((o) => o.keys.has(k)))) chosen.splice(i, 1);
  }
  if (chosen.length <= 1) return { ...out, base: chosen[0]?.book || null };   // one book already holds every reachable unit
  const node = (x) => ({ book: x.book, x, keys: x.keys, rows: x.rows, excluded: x.ev.excluded, usable: x.usable });
  const pickFillers = (inputs, keys) => {
    const fillers = [];
    while (inputs.length + fillers.length < STEP_INPUTS) {
      const all = [...inputs, ...fillers];
      const lonely = new Set([...keys].filter((k) => all.filter((n) => n.keys.has(k)).length === 1));
      const rowsNow = new Set(all.flatMap((n) => [...n.rows]));
      let best = null;
      for (const x of info) {
        if (all.filter((n) => n.book === x.book).length >= Math.min(x.book.count || 1, STEP_INPUTS)) continue;
        const cand = { x, excl: x.ev.excluded.length, extra: [...x.rows].filter((r) => !rowsNow.has(r)).length,
          score: 2 * [...x.keys].filter((k) => lonely.has(k)).length + [...x.keys].filter((k) => keys.has(k)).length };
        // a filler never brings excluded entries when a clean book exists; then most repeats, then fewest new entries
        if (!best || (cand.excl - best.excl || best.score - cand.score || cand.extra - best.extra) < 0) best = cand;
      }
      if (!best) break;
      fillers.push({ ...node(best.x), filler: true });
    }
    return fillers;
  };
  const runs = [];
  const makeRun = (inputs, final) => {
    const keys = new Set(inputs.flatMap((n) => [...n.keys]));
    const all = [...inputs, ...pickFillers(inputs, keys)];
    const rows = new Set(all.flatMap((n) => [...n.rows]));
    const excluded = [...new Set(all.flatMap((n) => n.excluded))];
    const single = units.filter((u) => keys.has(u.key) && all.filter((n) => n.keys.has(u.key)).length === 1);
    const risks = [];
    if (all.length < STEP_INPUTS) risks.push({ level: "warning", code: "count", text: `可带的书不够 ${STEP_INPUTS} 本（现在 ${all.length} 本）。` });
    if (all.some((n) => n.usable === "blocked")) risks.push({ level: "warning", code: "blocked", text: "主书与编队偶像同名，当前编队不可带入。" });
    if (rules.overflow_pushout && rows.size + 1 > (rules.max_entries || MAX_ENTRIES)) {
      risks.push({ level: "warning", code: "overflow", text: `可转记条目最多 ${rows.size + 1} 个，可能超过 21 格：多出的从头以外随机删除。` });
    }
    if (excluded.length) risks.push({ level: "warning", code: "excluded", text: `带入排除条目：${excluded.join("、")}，之后需要剥离（随机）。` });
    if (team?.verified && all.some((n) => n.usable === "unknown")) risks.push({ level: "info", code: "unknown", text: "有书名义未读，带入前确认。" });
    if (single.length) {
      const which = single.length === keys.size ? "每项都" : `${single.map((u) => u.label).join("、")} `;
      risks.push({ level: "info", code: "single", text: `${which}只有 1 本带：随机继承，可能要重跑。` });
    }
    const run = { index: runs.length + 1, final, now: all.every((n) => n.book), gives: units.filter((u) => keys.has(u.key)), risks,
      inputs: all.map((n) => (n.book ? { book: n.book, filler: Boolean(n.filler) } : { run: n.run })), keys, rows, excluded };
    runs.push(run);
    return run;
  };
  const nodes = chosen.filter((x) => x !== main).map(node);
  const slots = main ? STEP_INPUTS - 1 : STEP_INPUTS;
  while (nodes.length > slots) {
    nodes.sort((a, b) => Number(Boolean(a.run)) - Number(Boolean(b.run)) || a.keys.size - b.keys.size);
    const run = makeRun(nodes.splice(0, nodes.length - slots === 1 ? 2 : 3), false);
    nodes.push({ run: run.index, keys: run.keys, rows: run.rows, excluded: run.excluded, usable: "ok" });
  }
  makeRun(main ? [node(main), ...nodes] : nodes, true);
  for (const run of runs) { delete run.keys; delete run.rows; delete run.excluded; }
  return { ...out, runs };
}

// ----------------------------------------------------------------------------------------------- STEP results
/**
 * Compare a registered STEP result with the current main book under a Target (and other Targets): added / removed
 * entries, Lv up/down, required units kept, progress before/after, excluded gained, value for other Targets and the
 * recommendation: "main" (use as new main), "backup" (keep, do not switch), "stop" (no gain; review the route).
 * `intermediate` = the run did not bring the main book (a route's intermediate run): keep the result, keep the main.
 */
export function compareResult(result, mainBook, target, { otherTargets = [], intermediate = false, rules = ruleValues() } = {}) {
  const before = mainBook ? evaluateBook(mainBook, target, rules) : null;
  const after = evaluateBook(result, target, rules);
  const lv = (book) => new Map((book?.rows || []).filter((r) => Number.isInteger(r.level)).map((r) => [r.name, r.level]));
  const a = lv(mainBook);
  const b = lv(result);
  const added = [...b.keys()].filter((n) => !a.has(n)).map((n) => ({ name: n, level: b.get(n) }));
  const removed = [...a.keys()].filter((n) => !b.has(n)).map((n) => ({ name: n, level: a.get(n) }));
  const levelUp = [...b.keys()].filter((n) => a.has(n) && b.get(n) > a.get(n)).map((n) => ({ name: n, from: a.get(n), to: b.get(n) }));
  const levelDown = [...b.keys()].filter((n) => a.has(n) && b.get(n) < a.get(n)).map((n) => ({ name: n, from: a.get(n), to: b.get(n) }));
  const units = after.status.map((s, i) => ({ unit: s.unit, before: before ? before.status[i].state : "missing", after: s.state,
    beforeLv: before?.status[i].hit?.level ?? null, afterLv: s.hit?.level ?? null }));
  const rank = { missing: 0, below: 1, met: 2, ideal: 3, clear: 0, excluded: -1 };
  const lostRequired = units.filter((u) => u.unit.role === "required" && rank[u.before] >= 2 && rank[u.after] < 2).map((u) => u.unit.label);
  const gainedUnits = units.filter((u) => u.unit.role !== "excluded" && rank[u.after] > rank[u.before]).map((u) => u.unit.label);
  const lostUnits = units.filter((u) => u.unit.role !== "excluded" && rank[u.after] < rank[u.before]).map((u) => u.unit.label);
  const excludedGained = after.excluded.filter((n) => !(before?.excluded || []).includes(n));
  const p0 = before ? before.progress.done : 0;
  const p1 = after.progress.done;
  const others = otherTargets.filter((t) => t && t.id !== target.id).map((t) => {
    const ev = evaluateBook(result, t, rules);
    return { target: t, done: ev.progress.done, total: ev.progress.total };
  }).filter((x) => x.done > 0);
  let decision = "backup";
  let reason = "";
  if (!mainBook) { decision = p1 > 0 ? "main" : "stop"; reason = p1 > 0 ? `进度 0 → ${p1}。` : "没有目标条目。"; }
  else if (after.complete && !lostRequired.length) { decision = "main"; reason = "满足目标。"; }
  else if (lostRequired.length) { decision = p1 > p0 ? "backup" : "stop"; reason = `失去必需条目：${lostRequired.join("、")}。`; }
  else if (p1 > p0) { decision = "main"; reason = `进度 ${p0} → ${p1}。`; }
  else if (p1 === p0 && (levelUp.length > levelDown.length || after.noise.length < (before?.noise.length ?? 0)) && !excludedGained.length) {
    decision = "main"; reason = levelUp.length > levelDown.length ? "进度相同，Lv 提升。" : "进度相同，非目标条目更少。";
  } else if (p1 === p0) { decision = "backup"; reason = `进度 ${p0} → ${p1}。`; }
  else { decision = others.length ? "backup" : "stop"; reason = `进度 ${p0} → ${p1}。`; }
  if (decision === "stop" && others.length) decision = "backup";
  // a route run without the main book made an intermediate book: it waits for the next run, the main book stays
  if (intermediate && mainBook && !after.complete && !lostRequired.length) { decision = "backup"; reason = "中间书，留作下一轮材料。"; }
  if (excludedGained.length) reason += ` 新增排除条目：${excludedGained.join("、")}。`;
  return { before, after, added, removed, levelUp, levelDown, units, lostRequired, gainedUnits, lostUnits, excludedGained,
    progress: { before: p0, after: p1, total: after.progress.total }, others, decision, reason };
}

/**
 * What a newly obtained book is worth for the saved Targets (advice only; Maa never protects or sells): "main" = more
 * progress on a Target than its main book (or than every library book when it has none), "fills" = meets a Target
 * unit no library book meets, else null. Returns {key, text, target} or null.
 */
export function newBookValue(book, books, targets = [], { plan = null, rules = ruleValues() } = {}) {
  const others = books.filter((b) => b.fingerprint !== book.fingerprint);
  for (const t of targets) {
    const ev = evaluateBook(book, t, rules);
    if (!ev.progress.done) continue;
    const main = plan?.main?.[t.id] ? others.find((b) => b.fingerprint === plan.main[t.id]) || null : null;
    const bar = main ? evaluateBook(main, t, rules).progress.done : Math.max(0, ...others.map((b) => evaluateBook(b, t, rules).progress.done));
    if (ev.progress.done > bar) return { key: "main", text: `「${t.name}」${ev.progress.done}/${ev.progress.total}，多于${main ? "主书" : "书库中的书"}`, target: t };
  }
  for (const t of targets) {
    const unit = targetUnits(t).find((u) => u.role !== "excluded" && unitHit(u, book.rows)?.meets && !others.some((b) => unitHit(u, b.rows)?.meets));
    if (unit) return { key: "fills", text: `补足「${unit.label}」（书库中没有）`, target: t };
  }
  return null;
}

// ----------------------------------------------------------------------------------------------- next action
/**
 * One next action for the 带书 page from the stage, the last STEP run and the recommendation: {key, text}. key is
 * "pick_main" | "collect" | "step" | "record" | "decide" | "halo" | "done" | "rethink".
 */
export function nextAction({ target, stage, lastRun = null, recommendation = null, failures = 0 }) {
  if (!target || !targetUnits(target).some((u) => u.role !== "excluded")) return { key: "target", text: "先在「目标」里建一个目标。" };
  if (lastRun && !lastRun.decision) return { key: "decide", text: "上次 S.T.E.P. 的结果待处理：设为主书、留作备用或不采用。" };
  if (failures >= 3) return { key: "rethink", text: `连续 ${failures} 次没有进展，可换组合或降低目标 Lv。` };
  switch (stage?.stage) {
    case "done": return { key: "done", text: "主书已满足目标，可直接带入。" };
    case "halo": return { key: "halo", text: "主书已满足目标，可直接带入 say \"Halo\"；用 Halo 复制时结果会有变化。" };
    case "collect": return { key: "collect", text: `需采集：${stage.absent.map((u) => u.label).slice(0, 4).join("、")}${stage.absent.length > 4 ? " 等" : ""}。` };
    default: break;
  }
  if (!stage?.main) return { key: "pick_main", text: "按路线带入 3 本进行 S.T.E.P.。" };
  if (stage.stage === "separate") {
    return { key: "step", text: "主书含排除条目：带主书和 2 本不含排除条目的书进行 S.T.E.P. 剥离；继承随机，可能需要多次。" };
  }
  if (recommendation?.valid) return { key: "step", text: `${STAGE_LABEL[stage.stage]}：按路线带入 3 本，完成后登记结果。` };
  return { key: "step", text: `${STAGE_LABEL[stage.stage]}：选 3 本可带入的书，完成后登记结果。` };
}

// ----------------------------------------------------------------------------------------------- target change
/**
 * After the Target changed (or when switching Targets): units added / removed, whether the current main book is still
 * the best progress holder, and the books that already hold the newly needed units (reuse before collecting).
 */
export function reusePlan(oldTarget, newTarget, books, { mainBook = null, team = null, plan = null, rules = ruleValues() } = {}) {
  const sig = (u) => `${u.role}|${u.items.map((i) => `${i.min_level}-${i.ideal_level}`).join(",")}`;
  const old = new Map(targetUnits(oldTarget).map((u) => [u.label, u]));
  const after = targetUnits(newTarget);
  const added = after.filter((u) => !old.has(u.label));
  const removed = [...old.values()].filter((u) => !after.some((x) => x.label === u.label));
  const changed = after.filter((u) => old.has(u.label) && sig(old.get(u.label)) !== sig(u))
    .map((u) => ({ unit: u, from: old.get(u.label).role, to: u.role }));
  const ranked = books.filter((b) => usableFor(b, team) !== "blocked")
    .map((b) => ({ book: b, ev: evaluateBook(b, newTarget, rules) }))
    .sort((a, b) => b.ev.progress.done - a.ev.progress.done || a.ev.excluded.length - b.ev.excluded.length || a.ev.noise.length - b.ev.noise.length);
  const mainEv = mainBook ? evaluateBook(mainBook, newTarget, rules) : null;
  const best = ranked[0] || null;
  const keepMain = Boolean(mainEv && (!best || mainEv.progress.done >= best.ev.progress.done));
  const intermediates = plan ? books.filter((b) => plan.runs?.some((r) => !r.reverted && r.outputs?.includes(b.fingerprint))
    && evaluateBook(b, newTarget, rules).progress.done > 0) : [];
  const reuse = added.filter((u) => u.role !== "excluded").map((unit) => ({ unit,
    books: books.filter((b) => unitHit(unit, b.rows)?.meets).slice(0, 4) }));
  return { added, removed, changed, keepMain, suggestMain: keepMain ? mainBook : best?.book || null, mainProgress: mainEv?.progress || null,
    bestProgress: best?.ev.progress || null, reuse, intermediates };
}

/** Two Targets side by side: shared units, only-in-A / only-in-B, and books useful to both (≥1 needed unit each). */
export function compareTargets(a, b, books = []) {
  const label = (u) => `${u.label}`;
  const ua = targetUnits(a).filter((u) => u.role !== "excluded");
  const ub = targetUnits(b).filter((u) => u.role !== "excluded");
  const nb = new Set(ub.map(label));
  const na = new Set(ua.map(label));
  const both = books.filter((book) => ua.some((u) => unitHit(u, book.rows)?.meets) && ub.some((u) => unitHit(u, book.rows)?.meets));
  return { shared: ua.filter((u) => nb.has(label(u))).map(label), onlyA: ua.filter((u) => !nb.has(label(u))).map(label),
    onlyB: ub.filter((u) => !na.has(label(u))).map(label), both };
}

/** Halo single book: all entries inherited at S1 end; per needed unit the 习得折扣 from its Lv. */
export function haloView(book, target, rules = ruleValues()) {
  const ev = evaluateBook(book, target, rules);
  return { ...ev, discounts: ev.status.filter((s) => s.hit && s.unit.role !== "excluded")
    .map((s) => ({ label: s.unit.label, level: s.hit.level, off: HALO_DISCOUNT[s.hit.level] ?? null })) };
}

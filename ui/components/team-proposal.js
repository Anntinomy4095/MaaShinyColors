// 自动编队 proposal (tools/auto_team_propose.py -> ui/game-assets/team-proposal.js, local and gitignored): the team
// the True End recommender (or, when its data is missing, the wing_team fallback) proposed from 持有卡牌; `engine`,
// `fallback_reason` and the recommender summary (ProxyTERating ESTIMATED, assumptions) are shown with it. 2026-10-14
//: 推荐编队 (自动编队) asks for `--p-card auto` (Maa picks the P card too); the team field writes the
// proposal into the P/S/Guest keys and a new Produce edits the chosen unit into it (tools/ui_wing.py team_edit).
// Same-origin module import only (CSP). Pure helpers + one cached loader; no DOM.
import { idolNumber } from "./game-assets.js";

const P_ID = /^1\d{9}$/;
const S_ID = /^2\d{9}$/;
const STATUSES = ["ok", "needs_confirmation", "rejected"];
const ENGINES = { true_end: "True End 推荐器", wing_team: "旧评分（wing_team）", scorer: "自定义评分" };
const FALLBACKS = {
  card_db_empty: "本地没有已解析的卡数据", id_map_missing: "缺少卡 ID 映射", p_card_unavailable: "没有可计算的 P 卡",
  too_few_s_cards: "可计算的 S 卡不足 5 名偶像", true_end_blocked: "推荐器数据不完整", true_end_settings_invalid: "推荐器设置无效",
  true_end_error: "推荐器出错",
};
const AUDITIONS = ["s3_odotte", "legend", "semifinal", "final"];
const text = (v, n) => (typeof v === "string" ? v.slice(0, n) : "");
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
/** {S card id: ["Da 3.5倍刀", "SP", …]} from the recommender (display only; at most 4 short tags per card). */
export function cleanCardTags(v) {
  const out = {};
  for (const [id, tags] of Object.entries(v && typeof v === "object" ? v : {}).slice(0, 12)) {
    if (/^2\d{9}$/.test(id)) out[id] = (Array.isArray(tags) ? tags : []).filter((t) => typeof t === "string" && t).slice(0, 4).map((t) => t.slice(0, 12));
  }
  return out;
}
/** 流派 of a team (2026-11-01, optimizer.team_style): {id, label, detail} or null. */
export function cleanStyle(v) {
  if (!v || typeof v !== "object" || !["mono", "dual", "audition", "unit"].includes(v.id)) return null;
  const label = typeof v.label === "string" ? v.label.slice(0, 16) : "";
  return label ? { id: v.id, label, detail: typeof v.detail === "string" ? v.detail.slice(0, 60) : "" } : null;
}
const strings = (v, count, n) => (Array.isArray(v) ? v : []).filter((x) => typeof x === "string").slice(0, count).map((x) => x.slice(0, n));

/** Display label of the engine that produced a proposal, and the readable fallback reason (or ""). */
export function engineLabel(engine) { return ENGINES[engine] || ""; }
export function fallbackLabel(reason) { return reason ? FALLBACKS[reason] || reason : ""; }

/** Recommender summary (team_recommend._summary) reduced to display fields; true_end_probability is always null. */
export function cleanRecommender(data) {
  if (!data || typeof data !== "object") return null;
  const growth = data.growth && typeof data.growth === "object" ? data.growth : {};
  const stats = {};
  for (const k of ["Vo", "Da", "Vi", "Me"]) { const v = num(growth.effective_stats?.[k]); if (v !== null) stats[k] = v; }
  const battle = data.battle && typeof data.battle === "object" ? data.battle : {};
  const rows = (Array.isArray(battle.rows) ? battle.rows : []).filter((r) => AUDITIONS.includes(r?.audition)).slice(0, 4)
    .map((r) => ({ audition: r.audition, worst: num(r.worst_probability), lower: num(r.worst_lower_bound) }));
  return {
    rating: num(data.ProxyTERating), ratingLabel: data.rating_label === "ESTIMATED" ? "ESTIMATED" : "", trueEndProbability: null,
    strategy: text(data.strategy, 32), evaluation: text(data.evaluation, 32),
    growth: { stats, sp: num(growth.sp), spRequired: num(growth.sp_required), routeFeasible: growth.route_feasible === true,
      routeInfeasible: growth.route_feasible === false },
    battle: { status: text(battle.status, 40), policy: text(battle.policy, 40), rows },
    assumptions: strings(data.assumptions, 12, 200), unknowns: Array.isArray(data.unknowns) ? data.unknowns.length : 0,
  };
}

const ROLES = ["active", "guest", "p"];
const GAP = /^[\w.:()\- 　-鿿]{1,80}$/u;
const gapList = (v) => (Array.isArray(v) ? v : []).filter((g) => typeof g === "string" && GAP.test(g)).slice(0, 24);

/**
 * Per-card missing data (team_recommend.card_gaps): for every owned card the library source, the Lv/凸 basis
 * (OWNED / ASSUMED_LB4_CAP / UNKNOWN) and per role the gaps that are unknown (no data) or unresolved (DB/Wiki disagree).
 */
export function cleanCardGaps(rows) {
  return (Array.isArray(rows) ? rows : []).filter((r) => /^[12]\d{9}$/.test(r?.id || "")).slice(0, 2000).map((r) => {
    const out = { id: r.id, kind: r.kind === "P" ? "P" : "S", name: text(r.name, 80), source: text(r.source, 60),
      ownership: ["OWNED", "ASSUMED_LB4_CAP", "UNKNOWN"].includes(r.ownership) ? r.ownership : "", ready: {}, unknown: {}, unresolved: {},
      assumed: strings(r.assumed, 4, 24), rejected: text(r.rejected, 200) };
    for (const role of ROLES) {
      if (typeof r.ready?.[role] === "boolean") out.ready[role] = r.ready[role];
      if (r.unknown?.[role]) out.unknown[role] = gapList(r.unknown[role]);
      if (r.unresolved?.[role]) out.unresolved[role] = gapList(r.unresolved[role]);
    }
    return out;
  });
}

const ROLE_LABEL = { active: "Active", guest: "Guest", p: "P" };

/** Short text of one card's gaps per role: 「Active 缺 a、b；冲突 c」. Pure (node-tested). */
export function gapSummary(row) {
  const parts = [];
  for (const role of ["p", "active", "guest"]) {
    if (!(role in row.ready) && !(role in row.unknown)) continue;
    const unknown = row.unknown[role] || [];
    const unresolved = row.unresolved[role] || [];
    if (row.ready[role] === true && !unknown.length && !unresolved.length) { parts.push(`${ROLE_LABEL[role]} 就绪`); continue; }
    const bits = [];
    if (unknown.length) bits.push(`未知 ${unknown.join("、")}`);
    if (unresolved.length) bits.push(`冲突 ${unresolved.join("、")}`);
    parts.push(`${ROLE_LABEL[role]} ${bits.join("；") || "未就绪"}`);
  }
  if (row.source === "missing") parts.push("本地卡库没有这张卡");
  return parts.join(" · ");
}

/** Validated proposal: {status, reason, primary, confidence, reasons[], proposal: {p_card, s_cards[4], guest}|null}. */
export function cleanProposal(data) {
  const out = { status: "", reason: "", primary: "", confidence: null, reasons: [], proposal: null, generatedAt: "", owned: "",
    engine: "", fallbackReason: "", recommender: null, needsParsing: [], coverage: {}, cardGaps: [], gapCounts: null, assume: {},
    ownedCounts: null, ownedSource: "" };
  if (!data || typeof data !== "object" || data.version !== 1) return out;
  out.engine = ENGINES[data.engine] ? data.engine : "";
  out.fallbackReason = out.engine === "true_end" ? "" : text(data.fallback_reason, 48);
  out.recommender = out.engine === "true_end" ? cleanRecommender(data.recommender) : null;
  out.needsParsing = (Array.isArray(data.needs_parsing) ? data.needs_parsing : []).filter((n) => /^[12]\d{9}$/.test(n?.id || ""))
    .slice(0, 500).map((n) => ({ id: n.id, title: text(n.candidate_title, 80), reason: text(n.reason, 40) }));
  for (const [k, v] of Object.entries(data.coverage_counts && typeof data.coverage_counts === "object" ? data.coverage_counts : {}).slice(0, 12)) {
    if (/^[a-z_]{1,40}$/.test(k) && Number.isInteger(v) && v >= 0) out.coverage[k] = v;
  }
  out.cardGaps = cleanCardGaps(data.card_gaps);
  const gc = data.gap_counts;
  if (gc && typeof gc === "object") {
    out.gapCounts = {};
    for (const k of ["cards", "missing", "ready_active", "ready_guest", "ready_p", "with_unresolved"]) if (Number.isInteger(gc[k]) && gc[k] >= 0) out.gapCounts[k] = gc[k];
    out.gapCounts.top = Object.entries(gc.top_unknown && typeof gc.top_unknown === "object" ? gc.top_unknown : {})
      .filter(([k, v]) => GAP.test(k) && Number.isInteger(v)).slice(0, 8);
  }
  for (const k of ["lb4_cap", "p_te_eligible", "recognised_levels"]) if (data.assume?.[k] === true) out.assume[k] = true;
  // The owned list the proposal was computed from (the CLI may use a snapshot manifest, not the UI profile).
  const oc = data.owned_counts;
  if (oc && typeof oc === "object" && Number.isInteger(oc.p) && Number.isInteger(oc.s) && oc.p >= 0 && oc.s >= 0) {
    out.ownedCounts = { p: oc.p, s: oc.s };
    out.ownedSource = text(data.owned_source, 120);
  }
  out.status = STATUSES.includes(data.status) ? data.status : "";
  out.reason = typeof data.reason === "string" ? data.reason.slice(0, 64) : "";
  out.primary = ["Vo", "Da", "Vi"].includes(data.primary) ? data.primary : "";
  out.confidence = typeof data.confidence === "number" && Number.isFinite(data.confidence) ? Math.max(0, Math.min(1, data.confidence)) : null;
  out.reasons = (Array.isArray(data.reasons) ? data.reasons : []).filter((r) => typeof r === "string").slice(0, 20).map((r) => r.slice(0, 80));
  out.generatedAt = typeof data.generated_at === "string" ? data.generated_at.slice(0, 32) : "";
  out.requestedP = /^1\d{9}$/.test(data.requested_p_card || "") ? data.requested_p_card : "";
  // 推荐编队: Maa picked the P card (requested_p_card "auto"); requested_primary "auto" = each P card's specialty
  out.requestedAuto = data.requested_p_card === "auto";
  out.requestedPrimary = ["Vo", "Da", "Vi", "auto"].includes(data.requested_primary) ? data.requested_primary : "";
  const cands = data.recommender?.p_candidates;
  out.pCandidates = (Array.isArray(cands) ? cands : []).filter((c) => P_ID.test(c?.p_card || "") && typeof c.rating === "number")
    .slice(0, 12).map((c) => ({ p_card: c.p_card, primary: ["Vo", "Da", "Vi"].includes(c.primary) ? c.primary : "", rating: c.rating }));
  out.owned = typeof data.owned_digest === "string" ? data.owned_digest.slice(0, 64) : "";
  const p = data.proposal;
  if (p && typeof p === "object" && P_ID.test(p.p_card || "") && S_ID.test(p.guest || "") && Array.isArray(p.s_cards)
      && p.s_cards.length === 4 && p.s_cards.every((id) => S_ID.test(id || ""))) {
    const ids = [p.p_card, ...p.s_cards, p.guest];
    if (new Set(ids).size === ids.length) out.proposal = { p_card: p.p_card, s_cards: [...p.s_cards], guest: p.guest };
  }
  // Multi-route recommendation (backend fields, optional): a stable id and
  // a short feature / trade-off line for the main team, and at most two real alternatives. Absent -> one team, as before.
  out.candidateId = cleanId(data.candidate_id) || "main";
  out.feature = text(data.feature, 60);
  out.tradeoff = text(data.tradeoff, 60);
  out.cardTags = cleanCardTags(data.card_tags);
  out.style = cleanStyle(data.style);
  out.ownedFingerprint = text(data.owned_fingerprint, 64);
  out.alternatives = out.proposal ? cleanAlternatives(data.alternatives, out) : [];
  out.trainingHints = out.proposal ? cleanTrainingHints(data.training_hints) : [];
  return out;
}

/** 练级提示: at most three owned S cards that raise the main team once levelled to their 凸's cap. */
export function cleanTrainingHints(rows) {
  const out = [];
  for (const r of Array.isArray(rows) ? rows : []) {
    const level = r?.level, target = r?.target_level, lb = r?.limit_break, gain = num(r?.gain);
    if (!S_ID.test(r?.card_id || "") || !S_ID.test(r?.replaces || "") || !/^(S[1-4]|guest)$/.test(r?.slot || "")
        || !Number.isInteger(level) || !Number.isInteger(target) || level < 1 || target <= level || target > 120
        || !Number.isInteger(lb) || lb < 0 || lb > 4 || gain === null || gain <= 0
        || out.some((h) => h.cardId === r.card_id)) continue;
    out.push({ cardId: r.card_id, level, targetLevel: target, limitBreak: lb, slot: r.slot, replaces: r.replaces, gain });
    if (out.length === 3) break;
  }
  return out;
}

const cleanId = (v) => (typeof v === "string" && /^[\w.:-]{1,48}$/.test(v) ? v : "");

/** Six valid, distinct card ids {p_card, s_cards[4], guest} or null. */
function cleanTeam(p) {
  if (!p || typeof p !== "object" || !P_ID.test(p.p_card || "") || !S_ID.test(p.guest || "") || !Array.isArray(p.s_cards)
      || p.s_cards.length !== 4 || !p.s_cards.every((id) => S_ID.test(id || ""))) return null;
  const ids = [p.p_card, ...p.s_cards, p.guest];
  return new Set(ids).size === ids.length ? { p_card: p.p_card, s_cards: [...p.s_cards], guest: p.guest } : null;
}

const sameTeam = (a, b) => Boolean(a && b && a.p_card === b.p_card && a.guest === b.guest
  && a.s_cards.every((id, i) => id === b.s_cards?.[i]));

/** At most two alternatives with a stable id, six valid cards and a team different from the main one / each other. */
function cleanAlternatives(rows, main) {
  const out = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    const id = cleanId(row?.id);
    const team = cleanTeam(row?.proposal);
    if (!id || !team || id === main.candidateId || out.some((a) => a.id === id) || sameTeam(team, main.proposal)
        || out.some((a) => sameTeam(a.proposal, team))) continue;
    const rec = row.recommender && typeof row.recommender === "object" ? cleanRecommender(row.recommender) : null;
    out.push({ id, label: text(row.label, 24), feature: text(row.feature, 60), tradeoff: text(row.tradeoff, 60),
      reasons: strings(row.reasons, 8, 120), primary: ["Vo", "Da", "Vi"].includes(row.primary) ? row.primary : "",
      rating: num(row.rating) ?? rec?.rating ?? null,
      routeInfeasible: row.route_feasible === false || rec?.growth.routeInfeasible === true, proposal: team,
      cardTags: cleanCardTags(row.card_tags), style: cleanStyle(row.style) });
    if (out.length === 2) break;
  }
  return out;
}

/**
 * Every adoptable team of a 推荐编队 proposal, main first: [{id, label, feature, tradeoff, reasons, primary, rating,
 * routeInfeasible, proposal, main}]. Empty when the proposal has no usable team. Pure.
 */
export function proposalCandidates(proposal) {
  const pp = proposal?.proposal;
  if (!pp || proposal.status === "rejected") return [];
  const rec = proposal.recommender;
  return [{ id: proposal.candidateId || "main", label: "综合推荐", feature: proposal.feature || "", tradeoff: proposal.tradeoff || "",
    reasons: [], primary: proposal.primary || "", rating: rec?.rating ?? null, routeInfeasible: rec?.growth.routeInfeasible === true,
    proposal: pp, main: true, cardTags: proposal.cardTags || {}, style: proposal.style || null },
  ...(proposal.alternatives || []).map((a) => ({ ...a, main: false }))];
}

// 基本持平 (92.3 against 92.0 is within the estimate): a team whose rating is less than TIE_MARGIN below
// the best one is marked as a tie, so the player can pick either by taste.
export const TIE_MARGIN = 1.0;

/** True when `candidate` is not the best-rated one but rates within TIE_MARGIN of it. Pure. */
export function tiedWithBest(candidates, candidate) {
  const ratings = (candidates || []).map((c) => c.rating).filter(Number.isFinite);
  if (!ratings.length || !Number.isFinite(candidate?.rating)) return false;
  const best = Math.max(...ratings);
  return candidate.rating < best && best - candidate.rating < TIE_MARGIN;
}

/** The candidate whose six cards equal `team` ({p_card, s_cards, guest}), or null. Pure. */
export function matchingCandidate(proposal, team) {
  return proposalCandidates(proposal).find((c) => sameTeam(c.proposal, team)) || null;
}

/**
 * The candidate 推荐编队 should hold (pure): the one matching the adopted cards (a re-render never jumps back to the
 * first team), else the one the player picked last (`selectedId`, kept across 重新推荐 while that id still exists),
 * else the main recommendation.
 */
export function chosenCandidate(proposal, team, selectedId = "") {
  const all = proposalCandidates(proposal);
  return all.find((c) => sameTeam(c.proposal, team)) || all.find((c) => selectedId && c.id === selectedId) || all[0] || null;
}

const SELECTED_KEY = "sc-team-proposal-selected";
const BASIS_KEY = "sc-team-proposal-basis";
function storeGet(key) {
  try { return JSON.parse(globalThis.localStorage?.getItem(key) ?? "null"); } catch { return null; }
}
function storeSet(key, value) {
  try { globalThis.localStorage?.setItem(key, JSON.stringify(value)); } catch { /* preview or blocked storage */ }
}

/** Id of the candidate the player adopted last (「使用这队」), or "". */
export function selectedCandidateId() { const v = storeGet(SELECTED_KEY); return typeof v === "string" ? v : ""; }
export function setSelectedCandidateId(id) { storeSet(SELECTED_KEY, id || ""); }

/**
 * Fingerprint of the saved 持有卡牌 (pure): sorted P / S ids and every Lv/凸, hashed to a short string. A swap to
 * other cards with the same counts, or a Lv/凸 change, gives a different value (count comparison alone missed both).
 */
export function ownedFingerprint(owned) {
  const details = owned?.details || {};
  const parts = [...(owned?.p || [])].sort().concat("|", [...(owned?.s || [])].sort(), "|",
    Object.keys(details).sort().map((id) => `${id}:${details[id]?.level ?? ""}:${details[id]?.limit_break ?? ""}`));
  let a = 0x811c9dc5;
  let b = 0x9e3779b9;
  for (const ch of parts.join(",")) {
    const c = ch.codePointAt(0);
    a = Math.imul(a ^ c, 0x01000193) >>> 0;
    b = Math.imul(b ^ c, 0x5bd1e995) >>> 0;
  }
  return `${a.toString(16).padStart(8, "0")}${b.toString(16).padStart(8, "0")}`;
}

/** The owned fingerprint a proposal was requested with ({generatedAt, fp}), recorded by the team field after 推荐. */
export function proposalBasis() { const v = storeGet(BASIS_KEY); return v && typeof v === "object" ? v : null; }
export function recordProposalBasis(proposal, fp) {
  if (proposal?.generatedAt && fp) storeSet(BASIS_KEY, { generatedAt: proposal.generatedAt, fp });
}

let cachedProposal = null;   // last loaded cleanProposal() (null before the first load); team-rules.js reads it

// 「开局前大富豪」 自动: play when the adopted 推荐编队 scores below this ProxyTERating or its 50 万 fan
// route is not feasible. Same constant and rule as tools/ui_wing.py MINIGAME_AUTO_MIN_RATING / minigame_choice.
export const MINIGAME_AUTO_MIN_RATING = 75;
// The home Pデスク・ミニゲーム tap (wing_nav TARGETS home_minigame_menu) is not live-verified yet: 自动 does not play
// until it is (tools/ui_wing.minigame_entry_verified; a test keeps the two in step).
export const MINIGAME_ENTRY_VERIFIED = true;

/**
 * What 自动 decides for the next new Produce (pure): {play, rating}. `team` = the adopted {p_card, s_cards, guest} (the
 * option values), `recommend` = the 推荐编队 source. No usable score -> {play: false, rating: null}.
 */
export function minigameAuto(proposal, team, recommend) {
  // the adopted team's own score: the main recommendation or the alternative the player chose
  const found = recommend && proposal?.proposal && team ? proposalCandidates({ ...proposal, requestedAuto: true })
    .find((c) => sameTeam(c.proposal, team)) : null;
  if (found?.rating == null) return { play: false, rating: null };
  return { play: found.rating < MINIGAME_AUTO_MIN_RATING || found.routeInfeasible, rating: found.rating };
}

/** The proposal loaded last, or null before any load (used by the 用推荐 start check). */
export function teamProposal() { return cachedProposal; }

/** Test / preview hook. */
export function setTeamProposal(data) { cachedProposal = data == null ? null : cleanProposal(data); }

/**
 * Browser preview only (no host): ?proposal=auto shows the local proposal file as a 推荐编队 one; ?proposal=alts also
 * adds two made-up alternatives (other owned S cards) to look at the 「其他编队」 layout. Screenshots only.
 */
function previewProposal(data) {
  const mode = typeof window !== "undefined" && !window.chrome?.webview ? new URLSearchParams(window.location?.search || "").get("proposal") : null;
  if (!["auto", "alts"].includes(mode) || !data || typeof data !== "object") return data;
  const out = { ...data, requested_p_card: "auto", requested_primary: "auto" };
  const pp = data.proposal;
  if (mode === "alts" && pp) {
    const used = new Set([pp.p_card, ...pp.s_cards, pp.guest].map((id) => id.slice(3, 6)));
    const spare = (data.card_gaps || []).filter((r) => r.kind === "S" && !used.has(r.id.slice(3, 6))).map((r) => r.id);
    out.feature = "主属性技能齐全，试镜补 SP";
    out.tradeoff = "回体较少";
    out.alternatives = [
      { id: "preview-sp", label: "SP与回体", primary: pp.primary_stat || "Vo", rating: 78.2, feature: "回体多，少去话术房", tradeoff: "主属性攻击札较弱",
        reasons: ["预览数据：S4 换成回体卡"], proposal: { p_card: pp.p_card, s_cards: [...pp.s_cards.slice(0, 3), spare[0]], guest: pp.guest } },
      { id: "preview-aud", label: "试镜成长", primary: pp.primary_stat || "Vo", rating: 72.4, feature: "试镜奖励多", tradeoff: "SP 略少",
        reasons: ["预览数据：Guest 换成试镜卡"], proposal: { p_card: pp.p_card, s_cards: [...pp.s_cards], guest: spare[1] } },
    ];
  }
  return out;
}

export async function loadTeamProposal(version = 0) {
  try {
    // a regenerated file (U05) is a new module URL; the first load keeps the plain path
    const module = await import(version ? `../game-assets/team-proposal.js?v=${version}` : "../game-assets/team-proposal.js");
    cachedProposal = cleanProposal(previewProposal(module.default));
  } catch {
    cachedProposal = cleanProposal(null);
  }
  return cachedProposal;
}

export const SCAN_FIRST = "需扫描持有卡";

const NO_PROPOSAL = [
  [["selected_p_unavailable", "selected_p_not_recommended"], "P 卡缺少 Lv/凸 或卡牌资料：需扫描持有卡或更换 P 偶像", true],
  [["no_eligible_p_card", "p_card_unavailable"], "持有 P 卡缺少 Lv/凸 或卡牌资料：需扫描持有卡", true],
  [["owned_cards_unknown"], SCAN_FIRST, true],
  [["too_few_s_cards"], "可用 S 卡不足 5 名偶像：需扫描持有卡", true],
  [["card_db_empty", "id_map_missing"], "无本地卡资料：需下载卡资料", false],
];

function noProposalEntry(proposal) {
  const codes = [proposal?.fallbackReason, proposal?.reason, ...(proposal?.reasons || [])]
    .filter(Boolean).map((c) => String(c).replace(/^fallback:/, ""));
  return NO_PROPOSAL.find(([keys]) => codes.some((c) => keys.includes(c))) || null;
}

/** Short reason + next step when the recommender produced no team (rejected / no proposal). Pure. */
export function noProposalText(proposal) {
  const found = noProposalEntry(proposal);
  if (found) return found[1];
  const why = fallbackLabel(proposal?.fallbackReason);
  return why ? `暂无推荐：${why}` : "暂无推荐";
}

/** Whether the missing recommendation is fixed by scanning the owned cards (shows 「前往资源扫描」). Pure. */
export function noProposalNeedsScan(proposal) {
  return Boolean(noProposalEntry(proposal)?.[2]);
}

/**
 * Whether 持有卡牌 (the 资源 scan result in prefs.owned_cards) can feed the recommender: {ok, kind, text}. kind:
 * "never" (nothing scanned or imported) or "incomplete" (no P / no S cards, fewer than five S idols, the chosen P not
 * owned). Lv/凸 coverage is judged by the recommender itself (it also reads the recognised levels). Pure.
 */
export function recommendReadiness(owned, pCard = "") {
  const p = owned?.p || [];
  const s = owned?.s || [];
  if (!p.length && !s.length) return { ok: false, kind: "never", text: SCAN_FIRST };
  if (!p.length || !s.length) return { ok: false, kind: "incomplete", text: `持有卡只读到${p.length ? " P 卡" : " S 卡"}：${SCAN_FIRST}` };
  if (new Set(s.map(idolNumber).filter(Boolean)).size < 5) return { ok: false, kind: "incomplete", text: `持有的 S 卡不足 5 名偶像：${SCAN_FIRST}` };
  if (pCard && !p.includes(pCard)) return { ok: false, kind: "incomplete", text: "所选 P 偶像不在持有卡中：需扫描持有卡或更换 P 偶像" };
  return { ok: true, kind: "ready", text: "" };
}

/**
 * The owned list changed since the proposal was computed (pure). Uses, in order: the proposal's own
 * `owned_fingerprint`; the fingerprint recorded when this page requested it (`basis` with the same generatedAt); else
 * the P / S counts (a proposal made elsewhere, e.g. a snapshot run).
 */
export function proposalOutdated(proposal, owned, basis = null) {
  if (!proposal || !owned) return false;
  if (proposal.ownedFingerprint) return proposal.ownedFingerprint !== ownedFingerprint(owned);
  if (basis?.fp && basis.generatedAt && basis.generatedAt === proposal.generatedAt) return basis.fp !== ownedFingerprint(owned);
  const base = proposal.ownedCounts;
  return Boolean(base && (base.p !== (owned.p || []).length || base.s !== (owned.s || []).length));
}

/**
 * One player-facing line on why this team was recommended, from the recommender's own numbers (True End engine: the
 * 32-week growth projection behind ProxyTERating; fallback: the wing_team rubric). Pure; null without a proposal.
 */
export function proposalWhy(proposal) {
  if (!proposal?.proposal) return null;
  const rec = proposal.recommender;
  if (proposal.engine === "true_end" && rec) {
    const stat = proposal.primary;
    const parts = [];
    const main = stat ? rec.growth.stats[stat] : null;
    if (main != null) parts.push(`${stat} 约 ${Math.round(main)}`);
    if (rec.growth.sp != null) parts.push(`SP 约 ${Math.round(rec.growth.sp)}${rec.growth.spRequired != null ? `（技能计划需 ${Math.round(rec.growth.spRequired)}）` : ""}`);
    if (rec.growth.routeFeasible) parts.push("50 万粉丝路线可行");
    const final = rec.battle.rows.find((r) => r.audition === "final");
    if (final?.worst != null) parts.push(`决赛最低胜率 ${Math.round(final.worst * 100)}%`);
    const score = rec.rating == null ? "" : `评分 ${rec.rating.toFixed(1)}`;
    return `${[parts.join("，"), score].filter(Boolean).join("；")}（按 32 周成长估算）`;
  }
  if (proposal.engine === "wing_team") return "按旧评分选出：至少 3 张主属性 Live 技能 S 卡，S 卡、Guest 与 P 卡得分合计最高";
  return null;
}

/** Contextual help for the recommendation criteria (the 推荐编队 row's 「依据」). */
export const RECOMMEND_HELP = [
  "范围：已扫描的持有卡中，有卡牌资料且 Lv/凸 已知的卡；资料不全的卡不参与。",
  "以所选 P 偶像为目标，搜索 4 张 S 卡 + Guest，同一偶像只出现一次。",
  "主属性为「自动」时按 P 卡特长估算 32 周成长（主属性、Me、SP、可学技能、50 万粉丝路线），取评分最高的一队。",
  "数据相同时结果相同；「重新推荐」按当前持有卡与主属性重新计算。",
  "评分是估计值。开新局时 Maa 把所选ユニット改成这套编队。",
];

/** 「待确认」 tooltip: what the recommendation assumed (null when it assumed nothing). Pure. */
export function confirmHint(proposal) {
  if (proposal?.status !== "needs_confirmation") return null;
  const parts = [];
  if (proposal.assume?.lb4_cap) parts.push("未填凸数按4凸，未填等级按该凸上限；保留已填写值");
  if (proposal.assume?.p_te_eligible) parts.push("P 的 TE 资格未知，按有 TE 计算");
  return `推荐是估算${parts.length ? `（${parts.join("；")}）` : ""}；点开始即按这套编队改写所选ユニット`;
}

/**
 * The visible 「估算」 tag of a recommendation that assumed something (null when it assumed nothing): the same facts as
 * confirmHint, short enough to sit beside 「已采用」 instead of only in its tooltip. Pure.
 */
export function estimateTag(proposal) {
  if (proposal?.status !== "needs_confirmation") return null;
  const parts = [];
  if (proposal.assume?.lb4_cap) parts.push("未填凸数按 4 凸");
  if (proposal.assume?.p_te_eligible) parts.push("TE 资格按有");
  return ["估算", ...parts].join(" · ");
}

/** 推荐编队: the six cards of a proposal where Maa picked the P card (valid, not rejected), else null. */
export function autoProposal(proposal, pCard = "") {
  const pp = proposal?.proposal;
  if (!pp || !requestMatches(proposal, pCard) || proposal.status === "rejected" || (pCard && pp.p_card !== pCard)) return null;
  return pp;
}

/** Whether the loaded proposal answers this request: for the P card the player chose (推荐编队 since 2026-10-14, user:
 * 「需要用户选择P卡为目标再推荐编队」), else an older "auto" one (Maa picked the P). Pure. */
export function requestMatches(proposal, pCard = "") {
  if (!proposal) return false;
  return pCard ? proposal.requestedP === pCard : proposal.requestedAuto === true;
}

/** Whether a loaded 推荐编队 proposal no longer fits the wanted 主属性 ("auto" or Vo/Da/Vi) or owned list. Pure. */
export function autoProposalStale(proposal, stat, owned, pCard = "") {
  if (!requestMatches(proposal, pCard)) return true;
  if (stat === "auto" ? proposal.requestedPrimary !== "auto" : (proposal.requestedPrimary || proposal.primary) !== stat) return true;
  return proposalOutdated(proposal, owned, proposalBasis());
}

/** The proposal's six cards when it is usable for this P card (valid, not rejected, same P), else null. */
export function proposalFor(proposal, pCard) {
  const pp = proposal?.proposal;
  if (!pp || !pCard || pp.p_card !== pCard || proposal.status === "rejected") return null;
  return pp;
}

/** Option changes that accept a proposal: the six slot keys (by annotation `slot`) plus the mode option -> manual. */
export function proposalChanges(state, note, slots, proposal) {
  if (!proposal) return null;
  const bySlot = { P: proposal.p_card, S1: proposal.s_cards[0], S2: proposal.s_cards[1], S3: proposal.s_cards[2], S4: proposal.s_cards[3], guest: proposal.guest };
  const changes = {};
  for (const slot of slots) {
    if (!(slot.slot in bySlot)) continue;  // mode / ユニット / 自动找 members of the 编队 group
    changes[slot.key] = bySlot[slot.slot];
  }
  const cases = state.schema.options[note.mode_option]?.cases || [];
  const manual = cases.find((c) => c.name !== note.auto_case)?.name;
  if (!note.mode_option || !manual) return null;
  changes[note.mode_option] = manual;
  return changes;
}

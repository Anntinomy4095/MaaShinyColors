// W.I.N.G. 编队 (annotations `option_groups.<group>.control = "team"`; 2026-10-11 redesign):
// - 编队来源 (2026-10-14, user) has two values:
//   「指定ユニット」: the user picks the P idol (owned P cards, team-picker) and a ユニット (1–20, default ユニット1); the
//   switch 「自动查找编队」 below it lets Maa look through ユニット1–20 (wing_nav unit_search). No recommendation here.
//   「推荐编队」 (the 自动编队 case): Maa picks P + 4 S + Guest from 持有卡牌 (team-proposal.js, `--p-card auto`,
//   regenerated when 主属性 / the owned list change); the P is shown read-only; the proposal is written into the team
//   keys and a new Produce overwrites the chosen ユニット with it (「将覆盖ユニットN」; pressing 开始 is the consent).
// - 游戏内编队 (was 上次读取): the read-only 编队快照 Maa saved on ユニット編成 (team-snapshot.js); later runs verify P + this snapshot
//   unless 「核对上次读到的 S/Guest」 is off (then P only, and the unit is read again).
// - An inline pre-start checklist (team-rules.js). Research details (ProxyTERating, battle sim, gaps) and the manual
//   S/Guest registration (the old six-slot keys, kept for developers and migrated profiles) appear only with 开发者选项.
// 持有卡牌 counts / Lv imports live on the 资源 page. Nothing here authorises a run.
import { h, icon, jaText } from "./dom.js";
import { choiceChips } from "./choice.js";
import { groupNote, optionNote, devMode, caseLabel } from "./display.js";
import { anyCardArt } from "./game-assets.js";
import { ownedCards } from "./team-store.js";
import { teamCheck, teamRoles, S_SLOTS, unitNumber } from "./team-rules.js";
import { openTeamCardPicker, openOwnedPicker, specialtyShort, attrBadge } from "./team-picker.js";
import { autoProposal, autoProposalStale, requestMatches, chosenCandidate, confirmHint, engineLabel, estimateTag, fallbackLabel, gapSummary, loadTeamProposal,
  noProposalNeedsScan, noProposalText, ownedFingerprint, proposalCandidates, proposalOutdated, proposalBasis, proposalWhy, TIE_MARGIN, tiedWithBest,
  recommendReadiness, recordProposalBasis, RECOMMEND_HELP, selectedCandidateId, setSelectedCandidateId, teamProposal } from "./team-proposal.js";
import { resolvedPrimary } from "./wing-run.js";
import { ensureStyles as ensureDetailStyles } from "./team-details.js";
import { loadTeamSnapshot, snapshotWhen, teamSnapshot } from "./team-snapshot.js";
import { saveOwnedCards } from "./team-store.js";
import { openInventoryImport } from "./team-import.js";

function taskEntry(state, note) {
  return state.profile?.tasks?.find((t) => t.name === note.task) || null;
}

/** Rule check for the values shown in this field (team-rules.js): {auto, errors, notes, checks, team}. */
export function fieldCheck(state, note, values) {
  const entry = taskEntry(state, note);
  return teamCheck(state, note.task, { ...(entry || {}), option: { ...(entry?.option || {}), ...values } });
}

const AUDITION_LABELS = { s3_odotte: "S3", legend: "LEGEND", semifinal: "准决赛", final: "决赛" };
const pct = (v) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);

const GAP_FILTERS = [["blocked", "有缺口", "warning"], ["unresolved", "有冲突", "uncertain"], ["all", "全部", null]];
const SOURCE_LABEL = (s) => (s?.startsWith("merged:") ? "DB+Wiki" : s?.startsWith("db:") ? "DB" : s === "wiki_reviewed" ? "Wiki" : s === "missing" ? "库中没有" : s || "");

/** Owned cards with missing data, per role (unknown vs unresolved); developer view of the recommender. */
export function gapsBlock(proposal) {
  const rows = proposal?.cardGaps || [];
  if (!rows.length) return null;
  const c = proposal.gapCounts || {};
  const blocked = (r) => r.source === "missing" || Object.values(r.ready).some((v) => v === false);
  const unresolved = (r) => Object.values(r.unresolved).some((v) => v.length);
  let mode = "blocked";
  const list = h("ul", { class: "team-gap-list" });
  const paint = () => {
    const shown = rows.filter((r) => mode === "all" || (mode === "blocked" ? blocked(r) : unresolved(r)));
    list.replaceChildren(...shown.slice(0, 400).map((r) => h("li", { class: "team-gap-row", title: r.rejected ? `推荐器：${r.rejected}` : null },
      h("span", { class: "team-gap-name" }, jaText(r.name || r.id)),
      h("span", { class: "team-gap-meta muted" }, `${r.kind} · ${SOURCE_LABEL(r.source)}${r.ownership === "ASSUMED_LB4_CAP" ? " · 估计Lv/凸" : r.ownership === "OWNED" ? " · 实际Lv/凸" : ""}${r.assumed.includes("p_te_eligible") ? " · 假设有TE" : ""}`),
      h("span", { class: "team-gap-text" }, jaText(gapSummary(r))))));
    if (!shown.length) list.append(h("li", { class: "muted" }, "没有"));
    if (shown.length > 400) list.append(h("li", { class: "muted" }, `另 ${shown.length - 400} 张未列出`));
  };
  const counts = { blocked: rows.filter(blocked).length, unresolved: rows.filter(unresolved).length, all: rows.length };
  const filter = h("div", { class: "team-gap-filter chip-row", role: "group", "aria-label": "筛选" }, GAP_FILTERS.map(([key, label, tone]) => h("button", {
    class: `chip${key === mode ? " is-on" : ""}${tone ? ` tone-${tone}` : ""}`, type: "button", "aria-pressed": String(key === mode),
    onclick: (e) => { mode = key; e.currentTarget.parentElement.querySelectorAll("button").forEach((b) => { const on = b === e.currentTarget; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", String(on)); }); paint(); },
  }, tone ? h("span", { class: "dot", "aria-hidden": "true" }) : null, h("span", { text: label }), h("span", { class: "chip-count num", text: String(counts[key]) }))));
  paint();
  const top = (c.top || []).map(([k, v]) => `${k} ${v}`).join("、");
  const preview = !window.chrome?.webview && new URLSearchParams(window.location.search).get("gaps") === "1";
  return h("details", { class: "team-assumptions team-gaps", open: preview || null },
    h("summary", {}, jaText(`持有卡资料：${c.cards ?? rows.length} 张 · Active 就绪 ${c.ready_active ?? "—"} · Guest 就绪 ${c.ready_guest ?? "—"} · P 就绪 ${c.ready_p ?? "—"}${c.with_unresolved ? ` · 有冲突 ${c.with_unresolved}` : ""}${c.missing ? ` · 库中没有 ${c.missing}` : ""}`)),
    top ? h("p", { class: "team-gap-top" }, jaText(`最常见的未知资料：${top}`)) : null,
    filter, list);
}

/** Developer view: which engine produced the proposal, why it fell back, the recommender's numbers and assumptions. */
export function engineInfo(proposal) {
  if (!proposal?.engine) return null;
  ensureDetailStyles();
  const rec = proposal.recommender;
  const tags = [h("span", { class: `team-engine-tag${proposal.engine === "true_end" ? "" : " is-fallback"}`, text: engineLabel(proposal.engine) })];
  if (proposal.fallbackReason) tags.push(h("span", { class: "muted small" }, jaText(`回退原因：${fallbackLabel(proposal.fallbackReason)}`)));
  if (rec) {
    tags.push(h("span", { class: "muted small", title: "推荐器的整队估计分，不是 True End 概率" },
      `ProxyTERating ${rec.rating == null ? "—" : rec.rating.toFixed(1)}（${rec.ratingLabel || "ESTIMATED"}）· True End 概率：未建立`));
  }
  const missing = proposal.needsParsing.length;
  const lines = [];
  if (rec) {
    const stats = Object.entries(rec.growth.stats).map(([k, v]) => `${k} ${Math.round(v)}`).join(" / ");
    if (stats) lines.push(`32 周成长期望：${stats}；SP ${rec.growth.sp == null ? "—" : Math.round(rec.growth.sp)}${rec.growth.routeFeasible ? "" : "（粉丝路线未成立）"}`);
    lines.push(rec.battle.rows.length
      ? `条件战斗（${rec.battle.policy}）：${rec.battle.rows.map((r) => `${AUDITION_LABELS[r.audition]} 最差 ${pct(r.worst)} / 下界 ${pct(r.lower)}`).join("；")}`
      : "未做战斗模拟：没有显式战斗情景，只有成长推荐");
    lines.push(...rec.assumptions.slice(0, 6));
  }
  if (missing) lines.push(`${missing} 张持有卡还没有已解析的卡页数据或映射，未参与推荐（见 tools/auto_team_propose.py 输出的 needs_parsing）`);
  if (proposal.assume?.lb4_cap) tags.push(h("span", { class: "team-assume-tag", title: "未填凸数按4凸，未填等级按该凸上限；保留已填写值" }, "补全缺失Lv/凸"));
  if (proposal.assume?.p_te_eligible) tags.push(h("span", { class: "team-assume-tag", title: "TE 资格未知的 P 卡按有 TE 计算" }, "假设 P 有 TE"));
  return h("div", { class: "team-engine-block" },
    h("p", { class: "team-engine" }, tags),
    gapsBlock(proposal),
    lines.length ? h("details", { class: "team-assumptions" }, h("summary", {}, jaText("主要假设与数据缺口")),
      h("ul", {}, lines.map((line) => h("li", {}, jaText(line))))) : null);
}

/** Developer head line: the owned counts the proposal was computed from; flags a different owned source. */
export function proposalHead(proposal, owned) {
  const base = proposal?.ownedCounts;
  if (!base) return `Maa 从「持有卡牌」中评分提出一队（P ${owned.p.length} / S ${owned.s.length}），不使用游戏推荐编成。`;
  const same = base.p === owned.p.length && base.s === owned.s.length;
  const src = same ? "「持有卡牌」" : `提案生成时的持有清单${proposal.ownedSource ? `（${proposal.ownedSource}）` : ""}`;
  const note = same ? "" : `；与当前「持有卡牌」P ${owned.p.length} / S ${owned.s.length} 不同，采用前请核对`;
  return `Maa 从${src}中评分提出一队（P ${base.p} / S ${base.s}${note}），不使用游戏推荐编成。`;
}

const LB_MAX = 4;

/** Four 凸 stars (gold 1–3, purple when fully broken), or null when unknown — same look as the 资源 card grid. */
export function slotStars(n) {
  if (!Number.isInteger(n) || n < 0 || n > LB_MAX) return null;
  return h("span", { class: `lb-stars${n === LB_MAX ? " is-max" : ""}`, "aria-hidden": "true" },
    Array.from({ length: LB_MAX }, (_, i) => h("span", { class: `lb-star${i < n ? " is-on" : ""}`, text: "★" })));
}

/** Short Lv / 凸 text for a slot title ("Lv60 · 4凸", "Lv/凸 未填写"). P cards have no Lv. */
export function slotLevelText(kind, detail) {
  const lv = kind === "S" && Number.isInteger(detail?.level) ? `Lv${detail.level}` : null;
  const lb = Number.isInteger(detail?.limit_break) ? `${detail.limit_break}凸` : null;
  return [lv, lb].filter(Boolean).join(" · ") || "Lv/凸 未填写";
}

export function cardName(id) {
  const art = anyCardArt(id);
  return art ? `${art.idol || art.idolEn}${art.title ? `【${art.title}】` : ""}` : id || "";
}

function slotButton({ slot, value, disabled, focusKey, onclick, detail = null }) {
  const art = anyCardArt(value);
  const visual = art
    ? h("img", { class: "team-slot-img", src: art.src, alt: "", width: "56", height: "56", draggable: "false", decoding: "async" })
    : h("span", { class: "team-slot-img team-slot-empty", "aria-hidden": "true" }, icon(value ? "card" : "plus", { size: 18 }));
  const name = value ? cardName(value) : "未登记";
  const known = value && (Number.isInteger(detail?.level) || Number.isInteger(detail?.limit_break));
  const bar = known ? h("span", { class: "team-slot-bar", "aria-hidden": "true" }, slotStars(detail.limit_break) || h("span", { text: "凸?" })) : null;
  const rarity = art?.rarity ? ` rar-${String(art.rarity).toLowerCase()}` : "";
  const facts = value ? `${name}${art?.rarity ? ` · ${art.rarity}` : ""} · ${slotLevelText(slot.kind, detail)}` : "未登记";
  return h(onclick ? "button" : "span", {
    class: `team-slot${slot.kind === "P" ? " is-p" : ""}${slot.slot === "guest" ? " is-guest" : ""}${value ? "" : " is-empty"}`,
    type: onclick ? "button" : null, disabled: onclick ? disabled : null, "aria-haspopup": onclick ? "dialog" : null,
    "aria-label": `${slot.label}：${facts}`, title: `${slot.label}\n${facts}`, dataset: { focusKey, slot: slot.slot }, onclick,
  }, h("span", { class: `team-slot-frame${rarity}` }, visual, bar), h("span", { class: "team-slot-label" }, slot.label));
}

/** Read-only strip of six cards (P, S1–S4 | Guest). */
function readOnlyStrip(ids, owned, focusKey, label) {
  const slots = [{ slot: "P", kind: "P", label: "P" }, ...S_SLOTS.map((s) => ({ slot: s, kind: "S", label: s })), { slot: "guest", kind: "S", label: "Guest" }];
  return h("div", { class: "team-strip is-proposal", role: "group", "aria-label": label }, slots.map((slot, i) => {
    const button = slotButton({ slot, value: ids[i], disabled: true, focusKey: `${focusKey}:${i}`, detail: owned.details?.[ids[i]] });
    return slot.slot === "guest" ? [h("span", { class: "team-sep", "aria-hidden": "true" }), button] : button;
  }));
}

/** 推荐编队 (2026-10-14 user): S1–S4 | Guest only (the P is the player's choice), larger, each with its attribute and
 *  the recommender's short reasons (刀 / 课 / SP / 上限 / 回体). */
function proposalStrip(c, owned, focusKey, label) {
  const ids = [...c.proposal.s_cards, c.proposal.guest];
  const slots = [...S_SLOTS.map((s) => ({ slot: s, kind: "S", label: s })), { slot: "guest", kind: "S", label: "Guest" }];
  return h("div", { class: "team-strip is-proposal is-large", role: "group", "aria-label": label }, slots.map((slot, i) => {
    const id = ids[i];
    const tags = c.cardTags?.[id] || [];
    const pick = h("div", { class: `team-pick${slot.slot === "guest" ? " is-guest" : ""}` },
      slotButton({ slot, value: id, disabled: true, focusKey: `${focusKey}:${i}`, detail: owned.details?.[id] }),
      h("span", { class: "team-pick-tags", "aria-label": tags.length ? `选择理由：${tags.join("、")}` : null },
        attrBadge(anyCardArt(id)?.specialty, { size: 16 }),
        tags.map((t) => h("span", { class: `team-pick-tag${/倍刀$/.test(t) ? " is-live" : ""}`, text: t }))));
    return slot.slot === "guest" ? [h("span", { class: "team-sep", "aria-hidden": "true" }), pick] : pick;
  }));
}

const ALTS_OPEN = new Set();   // focus keys whose 「其他推荐」 list is open

const CONFIDENCE = (c) => (c == null ? "—" : c >= 0.7 ? "高" : c >= 0.4 ? "中" : "低");

/** ユニット1–20 (both pages verified 2026-10-13) as a grid of numbered pills (2026-10-24: no dropdown); the full name is the tooltip. */
function unitChips(state, key, value, { disabled, focusKey, label, onPick }) {
  const cases = state.schema.options[key]?.cases || [];
  return choiceChips({ options: cases.map((c) => [c.name, String(unitNumber(c.name) ?? caseLabel(state, key, c.name)), caseLabel(state, key, c.name)]),
    value: value ?? state.schema.options[key]?.default_case, onPick, focusKey, label, disabled, cls: "is-grid team-unit-chips" });
}

export const TEAM_MODES = [["unit", "指定ユニット"], ["recommend", "推荐编队"]];

/** 编队来源 segmented control (two values; 「自动查找编队」 is a switch under 指定ユニット); `why` = {mode: reason}. */
function modeControl({ mode, disabled, why, focusKey, onPick }) {
  mode = mode === "search" ? "unit" : mode;
  return h("div", { class: "segmented text-seg team-mode", role: "radiogroup", "aria-label": "编队来源" }, TEAM_MODES.map(([key, text]) => {
    const off = disabled || (Boolean(why[key]) && key !== mode);
    return h("button", { class: `segment${key === mode ? " is-selected" : ""}`, type: "button", role: "radio", "aria-checked": String(key === mode),
      disabled: off, title: why[key] || null, dataset: { focusKey: `${focusKey}:${key}` }, onclick: () => { if (key !== mode) onPick(key); } }, h("span", { text }));
  }));
}

/**
 * 「将核对」 row of 指定ユニット (pure): what 开始 compares with the game's ユニット (P + S1–S4 + Guest, or only the P when the
 * snapshot check is off) and where the S/Guest come from (the unit and the read date of the saved snapshot).
 */
export function verifyText(snapshot, { check = true } = {}) {
  const from = `ユニット${snapshot.unit} · ${snapshotWhen(snapshot) || "已读取"}`;
  return check ? `P + S1–S4 + Guest · ${from}` : `本次只核对 P · ${from}`;
}

/** One-line description of what 开始 will do with the team, per source. */
export function modeHint(mode, { unit = null, snapshot = false, check = true } = {}) {
  if (mode === "recommend") return `开新局时把ユニット${unit || "?"} 改成这套推荐编队（覆盖原编队），核对一致后开始。`;
  const full = snapshot && check;
  if (mode === "search") return `开始后在ユニット1–20 里找${full ? "这套游戏内编队" : "含此 P 偶像的编队"}，找不到就停止。`;
  return `开始后核对ユニット${unit || "?"} 的 ${full ? "P 和 S/Guest" : "P 偶像"}，不符就停止。`;
}

/** Labelled row: the label and the first line of the value share one row height (team.css); card strips go below. */
function line(label, ...children) {
  const items = children.flat().filter(Boolean);
  const strip = (c) => c.classList?.contains?.("team-strip");
  return h("div", { class: "team-line" }, h("span", { class: "team-line-label", text: label }),
    h("div", { class: "team-line-body" }, h("div", { class: "team-line-head" }, items.filter((c) => !strip(c))), items.filter(strip)));
}

/** Muted one-line value text of a row. */
const valueText = (text, cls = "") => h("span", { class: `team-line-text${cls ? ` ${cls}` : ""}` }, jaText(text));

// 推荐进度条: while the host's recommender runs it reports one step per compared P card plus 练级提示.
// The bar reuses common.js's .meter styles (common.js itself registers custom elements, so it is not imported here).
export function proposeProgress(teamPropose, text) {
  const step = teamPropose?.state === "running" ? teamPropose.progress : null;
  const total = Number.isInteger(step?.total) && step.total > 0 ? step.total : 0;
  const done = total && Number.isInteger(step.done) ? Math.max(0, Math.min(step.done, total)) : 0;
  const bar = total ? h("span", { class: "meter", "aria-hidden": "true" }, h("span", { class: "meter-fill" })) : null;
  if (bar?.firstChild.style) bar.firstChild.style.width = `${Math.round((done / total) * 100)}%`;
  return h("span", { class: "team-line-text muted team-propose-progress", role: "status", dataset: { proposeProgress: total ? `${done}/${total}` : "" } },
    text, bar, total ? h("span", { class: "num" }, `${done}/${total}`) : null);
}

// One automatic proposal attempt per P/主属性 pair per page load (module scope: the field is rebuilt on every change).
const autoTried = new Set();

export function teamField({ state, group, keys, values, disabled, onChange, focusKey, actions }) {
  const note = groupNote(state, group);
  const roles = teamRoles(state, keys);
  const field = h("div", { class: "field form-row kind-team" });
  let proposal = teamProposal();   // cleanProposal() of the last load (shared cache), refreshed below
  let regenerating = false;
  // 「其他推荐」 (2026-10-14 user: replaces 重新推荐; the list follows P / 主属性 by itself). Kept across store re-renders.
  const showAlts = () => ALTS_OPEN.has(focusKey);
  const toggleAlts = (open) => { if (open) ALTS_OPEN.add(focusKey); else ALTS_OPEN.delete(focusKey); };
  let failed = false;
  const modeCases = (state.schema.options[roles.mode]?.cases || []).map((c) => c.name);
  const manualCase = modeCases.find((n) => n !== note.auto_case);
  const cardRoles = [...S_SLOTS, "guest"].filter((s) => roles[s]);
  const clearCards = () => Object.fromEntries(cardRoles.map((s) => [roles[s], ""]));

  // 推荐编队: the P/S/Guest keys follow the usable recommendation (Maa picked the P card too).
  const adoptChanges = (pp) => Object.fromEntries([["P", pp.p_card], ...S_SLOTS.map((s, i) => [s, pp.s_cards[i]]), ["guest", pp.guest]]
    .filter(([s]) => roles[s]).map(([s, id]) => [roles[s], id]));
  function adoptIfNeeded(pp) {
    const changes = adoptChanges(pp);
    if (disabled || Object.entries(changes).every(([k, v]) => values[k] === v)) return;
    queueMicrotask(() => onChange(changes));
  }
  // the adopted six cards as a team (for matching them against the recommendation's candidates)
  const adoptedTeam = (vals = values) => ({ p_card: vals[roles.P] || "", s_cards: S_SLOTS.map((s) => vals[roles[s]] || ""),
    guest: vals[roles.guest] || "" });
  const chosen = () => chosenCandidate(proposal, adoptedTeam(), selectedCandidateId());
  // 「使用这队」: adopt that candidate at once (no confirm; 开始 still overwrites the unit as before)
  function useCandidate(candidate) {
    setSelectedCandidateId(candidate.id);
    onChange(adoptChanges(candidate.proposal));
  }

  // 编队来源 -> option values (mode option). Leaving 推荐编队 clears the adopted S/Guest (the P stays as a choice);
  // the 「自动查找编队」 switch keeps its value.
  function pickMode(next) {
    const was = fieldCheck(state, note, values).mode;
    const changes = {};
    if (roles.mode) changes[roles.mode] = next === "recommend" ? note.auto_case : manualCase;
    if (was === "recommend" && next !== "recommend") Object.assign(changes, clearCards());
    const pick = chosen();
    if (next === "recommend" && pick && !proposalStale()) Object.assign(changes, adoptChanges(pick.proposal));
    onChange(changes);
  }

  const render = () => {
    const owned = ownedCards(state);
    const check = fieldCheck(state, note, values);
    const dev = devMode(state);
    const mode = check.mode;
    const auto = mode === "recommend";
    // 推荐编队 (2026-10-14 user): the player picks the P card as the target; Maa recommends S/Guest for it
    const p = values[roles.P] || "";
    const pArt = anyCardArt(p);
    // P 偶像 (2026-10-13 polish: one highlighted card — thumbnail, idol, card title, specialty, 更换). Both sources: the
    // player's choice (推荐编队 recommends the other five cards for it).
    const pLabel = auto ? "P 偶像" : optionNote(state, roles.P).label || "P 偶像";
    const pRow = h("div", { class: `team-p-card${p ? "" : " is-empty"}${auto ? " is-auto" : ""}` },
      slotButton({ slot: { slot: "P", kind: "P", label: "P" }, value: p, disabled, focusKey: `${focusKey}:p`, onclick: () => pickP() }),
      h("div", { class: "team-p-info" },
        h("span", { class: "team-p-eyebrow", text: pLabel }),
        h("span", { class: "team-p-name" }, p ? jaText(pArt?.idol || pArt?.idolEn || cardName(p))
          : h("span", { class: "team-p-missing", text: "未选择" })),
        p && pArt?.title ? h("span", { class: "team-p-title" }, jaText(`【${pArt.title}】`)) : null,
        h("div", { class: "team-p-meta" },
          pArt?.specialty ? h("span", { class: "team-meta-note muted small", title: "P 偶像自身的特长（本次主属性见「培养目标」）" }, "特长", attrBadge(pArt.specialty, { size: 16, cls: "team-attr" }), specialtyShort(pArt.specialty)) : null,
          h("button", { class: `btn btn-small${p ? " btn-text" : " btn-primary"}`, type: "button", disabled, dataset: { focusKey: `${focusKey}:p-change` }, onclick: () => pickP() }, p ? "更换 P 偶像" : "选择 P 偶像"))));

    // 2026-10-24 (user: 一眼看明白): the fold title already names this group; what 开始 does with the team is the tooltip
    // of the 编队来源 / ユニット rows, and the checklist lists only what still blocks 开始.
    const parts = [];
    const ready = recommendReadiness(owned, p);
    const noRec = Boolean(p) && requestMatches(proposal, p) && !autoProposal(proposal, p) && !regenerating;
    const why = { recommend: !ready.ok ? ready.text : noRec ? noProposalText(proposal) : "" };
    const modeLine = line("编队来源", modeControl({ mode, disabled, why, focusKey: `${focusKey}:mode`, onPick: pickMode }));
    parts.push(modeLine, pRow);
    const unitValue = values[roles.unit] ?? state.schema.options[roles.unit]?.default_case;
    const unitNo = unitNumber(unitValue);
    const unitLine = line("ユニット", unitChips(state, roles.unit, values[roles.unit], { disabled, focusKey: `${focusKey}:unit`, label: "ユニット",
      onPick: (v) => onChange({ [roles.unit]: v }) }), auto && unitNo ? h("span", { class: "team-overwrite", role: "note" }, `将覆盖ユニット${unitNo}`) : null);
    parts.push(unitLine);
    if (!auto && roles.search) {
      const on = values[roles.search] === "Yes";
      parts.push(line("自动查找编队", h("button", {
        class: `toggle${on ? " is-on" : ""}`, type: "button", role: "switch", "aria-checked": String(on), "aria-label": "自动查找编队",
        disabled, dataset: { focusKey: `${focusKey}:search` }, title: "开新局时在ユニット1–20 里找含此 P 偶像的编队",
        onclick: () => onChange({ [roles.search]: on ? "No" : "Yes" }) }, h("span", { class: "toggle-track" }, h("span", { class: "toggle-thumb" }))),
      valueText(on ? "从ユニット1–20 里找" : "只用所选ユニット")));
    }
    const team = check.team;
    const checkSnap = values[roles.snapcheck] !== "No";
    if (auto) {
      parts.push(...recommendLines(owned, dev, ready, p));
    } else {
      if (team?.source === "snapshot" || (team?.snapshot && !checkSnap)) {
        const snap = team.snapshot;
        const toggleCheck = roles.snapcheck ? h("button", { class: "btn btn-small btn-text", type: "button", disabled, dataset: { focusKey: `${focusKey}:snapcheck` },
          title: checkSnap ? "在游戏里改过这个编队时用：本次只核对 P，并重新读取" : "开局前按这套编队核对 S/Guest",
          onclick: () => onChange({ [roles.snapcheck]: checkSnap ? "No" : "Yes" }) }, checkSnap ? "改为只核对 P" : "恢复核对 S/Guest") : null;
        parts.push(line("将核对",
          valueText(verifyText(snap, { check: checkSnap })), toggleCheck,
          readOnlyStrip([p, ...snap.s_cards, snap.guest], owned, `${focusKey}:snap`, "Maa 读到的游戏内编队（只读）")));
      } else if (team?.source === "registered") {
        parts.push(line("核对用卡",
          valueText("手动登记的 S/Guest（旧设置）"),
          h("button", { class: "btn btn-small btn-text", type: "button", disabled, dataset: { focusKey: `${focusKey}:reg-clear` },
            title: "清除后按开局时读到的编队核对", onclick: () => onChange(clearCards()) }, "清除"),
          readOnlyStrip([p, ...team.s, team.guest], owned, `${focusKey}:reg`, "手动登记的编队（只读）")));
      }
      // no snapshot yet: nothing to show (the S/Guest are read when the run opens the ユニット)
    }
    const snapUsed = Boolean(team?.snapshot) && (mode === "search" || team.source === "snapshot" || !checkSnap);
    const hint = modeHint(mode, { unit: unitNo, snapshot: snapUsed, check: checkSnap });
    modeLine.setAttribute("title", hint);
    unitLine.setAttribute("title", hint);
    if (auto) {
      const pick = chosen();
      if (pick && !proposalStale()) adoptIfNeeded(pick.proposal);
    }

    // 开始前检查: only the items that still block 开始 (the run bar turns ready when none is left); the empty P card
    // already says 「未选择（必选）」.
    const failing = check.checks.filter((c) => !c.ok && !c.pending && !(c.text === "未选择 P 偶像" && !p));
    if (failing.length) {
      parts.push(h("ul", { class: "team-checklist", "aria-label": "开始前检查" }, failing.map((c) =>
        h("li", { class: "is-bad" }, h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text: c.text })))));
    }
    // an error already shown as a red checklist item (or by the empty P card) is not repeated below it
    const shown = new Set([...failing.map((c) => c.text), ...(p ? [] : ["未选择 P 偶像"])]);
    const errors = check.errors.filter((n) => !shown.has(n));
    if (errors.length) parts.push(h("ul", { class: "team-notes is-error", role: "alert" }, errors.map((n) => h("li", {}, jaText(n)))));
    if (check.notes.length) parts.push(h("ul", { class: "team-notes", role: "status" }, check.notes.map((n) => h("li", {}, jaText(n)))));
    if (dev && !auto) {
      const slots = [...S_SLOTS, "guest"].filter((s) => roles[s]).map((s) => ({ key: roles[s], slot: s, kind: "S", label: s === "guest" ? "Guest" : s }));
      parts.push(h("details", { class: "team-dev" }, h("summary", { class: "small muted" }, "开发者：手动登记 S/Guest（有快照时以快照为准）"),
        h("div", { class: "team-strip", role: "group", "aria-label": "手动登记" }, slots.map((slot, i) => {
          const button = slotButton({ slot, value: values[slot.key], disabled, focusKey: `${focusKey}:dev-${i}`, onclick: () => pickS(slot), detail: owned.details?.[values[slot.key]] });
          return slot.slot === "guest" ? [h("span", { class: "team-sep", "aria-hidden": "true" }), button] : button;
        }))));
    }
    field.replaceChildren(h("div", { class: "field-control team-control" }, parts));
    queueMicrotask(maybeAutoPropose);
  };

  // U05 follow-up (2026-10-12, user): the proposal follows the selected P and 主属性 by itself; the button stays for a retry.
  // 推荐编队 (2026-10-14 user): the player's P card is the target ("" = not chosen yet: no recommendation); 主属性
  // 「自动」 = the P card's specialty ("auto").
  const wanted = () => {
    const entry = state.profile?.tasks?.find((t) => t.name === "AutoProduce");
    const primary = entry?.option?.["Produce主属性"] ?? state.schema.options["Produce主属性"]?.default_case;
    return { pCard: values[roles.P] || "", stat: !primary || primary === "自动" ? "auto" : resolvedPrimary(primary, "").stat };
  };
  const runPropose = async ({ pCard, stat }) => {
    const fp = ownedFingerprint(ownedCards(state));
    regenerating = true; failed = false; render();
    const ok = await actions.teamPropose({ pCard, primary: stat });
    regenerating = false;
    if (ok) {
      proposal = await loadTeamProposal(Date.now());
      recordProposalBasis(proposal, fp);
    } else failed = true;   // the previous result stays; 「本次未更新」
    render();
  };
  const proposalStale = () => autoProposalStale(proposal, wanted().stat, ownedCards(state), wanted().pCard);
  const maybeAutoPropose = () => {
    if (fieldCheck(state, note, values).mode !== "recommend") return;     // 指定ユニット: no recommendation at all
    const want = wanted();
    const owned = ownedCards(state);
    const key = `${want.pCard}:${want.stat}:${ownedFingerprint(owned)}`;
    if (!proposal || !proposalStale() || !want.pCard || !want.stat || !actions?.teamPropose || disabled || regenerating
      || !recommendReadiness(owned, want.pCard).ok
      || state.host?.team_propose?.state === "running" || autoTried.has(key)) return;
    autoTried.add(key);
    runPropose(want);
  };

  const regenerate = () => {
    const { pCard, stat } = wanted();
    const busy = regenerating || state.host?.team_propose?.state === "running";
    const ready = recommendReadiness(ownedCards(state), pCard);
    const why = !pCard ? "未选择 P 偶像" : !ready.ok ? ready.text : !actions?.teamPropose ? "当前版本不支持重新推荐" : "";
    return h("button", { class: "btn btn-small btn-text", type: "button", disabled: disabled || busy || Boolean(why), dataset: { focusKey: `${focusKey}:regen` },
      title: why || `按所选 P 偶像、主属性 ${stat === "auto" ? "自动（P 卡特长）" : stat} 和当前持有卡重新计算；数据没变时结果相同`,
      onclick: () => runPropose({ pCard, stat }) }, busy ? "推荐中…" : "推荐");
  };

  // Lv/凸 are imported on the 资源 page (持有卡牌); the recommendation reads them from there
  const levelsLink = () => (actions?.setTab ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `${focusKey}:levels` },
    title: "在「资源」页的「持有卡牌」导入 Lv/凸；没有 Lv/凸 的卡不参与推荐", onclick: () => actions.setTab("resources") }, "去资源页导入 Lv/凸") : null);

  const scanButton = () => h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: `${focusKey}:scan` },
    title: "打开「资源」页，扫描持有的偶像与支援卡", onclick: () => actions?.setTab?.("resources") }, "前往资源扫描");

  // 推荐编队 rows (only in that mode): the adopted team (main recommendation or a chosen alternative) with its
  // feature / trade-off line and reason, 重新推荐, and at most two folded alternatives; or why there is none. While a
  // new recommendation runs the old result stays visible (「重新推荐中…」); a failed run keeps it (「本次未更新」).
  const recommendLines = (owned, dev, ready, p) => {
    const busy = regenerating || state.host?.team_propose?.state === "running";
    const label = "推荐编队";
    if (!p) return [line(label, valueText("选择 P 偶像后推荐 S 卡与 Guest"))];
    if (!ready.ok) return [line(label, valueText(ready.text, "is-warn"), scanButton())];
    if (!proposal) return [line(label, valueText("读取中…"))];
    const pp = autoProposal(proposal, p);
    const out = [];
    if (!pp && busy) {
      out.push(line(label, proposeProgress(state.host?.team_propose, "推荐中…"), regenerate()));
    } else if (!pp && requestMatches(proposal, p)) {
      out.push(line(label, valueText(noProposalText(proposal), "is-warn"), noProposalNeedsScan(proposal) ? scanButton() : null, regenerate()));
    } else if (!pp || (proposalStale() && !busy)) {
      out.push(line(label, valueText(pp && proposalOutdated(proposal, owned, proposalBasis()) ? "持有卡已更新，需重新推荐" : pp ? "主属性已变，需重新推荐" : "暂无推荐"), regenerate()));
    } else {
      const all = proposalCandidates(proposal);
      const top = chosen() || all[0];
      const hint = confirmHint(proposal);
      const estimate = estimateTag(proposal);
      const compared = proposal.requestedAuto && proposal.pCandidates?.length ? ` · 比较了 ${proposal.pCandidates.length} 张 P 卡` : "";
      const name = all.length > 1 ? `${top.main ? "综合推荐" : top.label || "备选"} · ` : "";
      const status = busy ? proposeProgress(state.host?.team_propose, "重新推荐中…")
        : failed ? h("span", { class: "team-line-text is-warn", role: "status" }, "本次未更新") : null;
      const others = all.filter((c) => c.id !== top.id);
      const altButton = h("button", { class: "btn btn-small", type: "button", disabled: !others.length,
        "aria-expanded": others.length ? String(showAlts()) : null, dataset: { focusKey: `${focusKey}:alts` },
        title: others.length ? "查看与这套不同的推荐编队" : "没有不同卡组的推荐",
        onclick: () => { toggleAlts(!showAlts()); render(); } }, others.length ? `其他推荐（${others.length}）` : "无其他推荐");
      out.push(line(label,
        valueText(`${name}主属性 ${top.primary || proposal.primary || "—"}${compared}`),
        h("span", { class: "team-state-tag is-adopted", title: hint || "点开始即按这套编队改写所选ユニット" }, "已采用"),
        estimate ? h("span", { class: "team-state-tag is-estimate", title: hint }, estimate) : null,
        styleTag(top),
        tieTag(all, top),
        status, altButton, levelsLink(),
        proposalStrip(top, owned, `${focusKey}:prop`, "推荐编队（只读）")));
      if (showAlts() && others.length) out.push(alternativesBlock(others, owned, busy, all));
      const traits = traitLines(top);
      if (traits) out.push(traits);
      if (top.main && proposal.trainingHints?.length) out.push(trainingLine(proposal.trainingHints, owned));
      const why = top.main ? proposalWhy(proposal) : top.reasons.join("；");
      if (why) {
        out.push(h("details", { class: "team-why" },
          h("summary", { class: "small" }, jaText(`推荐理由：${why}`)),
          top.main ? h("ul", { class: "team-why-help" }, RECOMMEND_HELP.map((x) => h("li", {}, jaText(x)))) : null));
      }
    }
    if (dev) out.push(h("p", { class: "muted small" }, jaText(proposalHead(proposal, owned))), engineInfo(proposal));
    return out.filter(Boolean);
  };

  // 训练后可加入推荐 (2026-10-25 user; was 「练级」): owned S cards that would raise this team once trained to their 凸's
  // cap (never 凸): the card at its target level, the score gain and the slot it would take; details in the tooltip.
  const trainingLine = (hints, owned) => line("训练后可加入推荐", h("div", { class: "team-strip is-hints", role: "list", "aria-label": "训练后可加入推荐" },
    hints.map((t, i) => {
      const slot = t.slot === "guest" ? "Guest" : t.slot;
      return h("div", { class: "team-pick", role: "listitem",
        title: `${cardName(t.cardId)}：训练到 Lv${t.targetLevel} 后换下 ${slot} ${cardName(t.replaces)}，评分 +${t.gain.toFixed(1)}` },
        slotButton({ slot: { slot: t.slot === "guest" ? "guest" : "S", kind: "S", label: `Lv${t.level}→${t.targetLevel}` },
          value: t.cardId, disabled: true, focusKey: `${focusKey}:hint-${i}`, detail: { level: t.targetLevel, limit_break: t.limitBreak } }),
        h("span", { class: "team-pick-tags" },
          h("span", { class: "team-pick-tag is-gain", text: `+${t.gain.toFixed(1)}` }),
          h("span", { class: "team-pick-tag", text: `换 ${slot}` })));
    })));

  // 「特点 / 取舍」 of a candidate (only what the backend computed; nothing when it sent neither).
  const traitLines = (c) => (c.feature || c.tradeoff ? h("ul", { class: "team-traits" },
    c.feature ? h("li", {}, h("span", { class: "team-trait-key", text: "特点" }), jaText(c.feature)) : null,
    c.tradeoff ? h("li", {}, h("span", { class: "team-trait-key", text: "取舍" }), jaText(c.tradeoff)) : null) : null);

  // 流派: the build type the recommender computed; what it means in the tooltip.
  const styleTag = (c) => (c.style ? h("span", { class: "team-state-tag is-style", title: c.style.detail || null,
    dataset: { style: c.style.id } }, jaText(c.style.label)) : null);

  // 「其他编队」: vertical cards (fits the 360–600 px panel); 「使用这队」 adopts at once, the choice survives re-renders.
  const tieTag = (all, c) => (tiedWithBest(all, c) ? h("span", { class: "team-state-tag is-tie",
    title: `评分只差不到 ${TIE_MARGIN} 分，在估算误差内，可按喜好选` }, "基本持平") : null);
  const alternativesBlock = (others, owned, busy, all = others) => h("div", { class: "team-alts", role: "group", "aria-label": "其他推荐" },
    others.map((c, i) => h("div", { class: "team-alt", dataset: { candidate: c.id } },
      h("div", { class: "team-alt-head" },
        h("span", { class: "team-alt-name" }, jaText(c.main ? "综合推荐" : c.label || "备选")),
        c.primary ? h("span", { class: "muted small", text: `主属性 ${c.primary}` }) : null,
        styleTag(c),
        tieTag(all, c),
        h("button", { class: "btn btn-small", type: "button", disabled: disabled || busy, dataset: { focusKey: `${focusKey}:alt-${i}` },
          title: "改用这套编队；点开始时按它改写所选ユニット", onclick: () => { toggleAlts(false); useCandidate(c); } }, "使用这队")),
      proposalStrip(c, owned, `${focusKey}:alt-${i}`, `${c.label || "备选"}（只读）`),
      traitLines(c),
      c.reasons.length ? h("details", { class: "team-why" }, h("summary", { class: "small" }, "理由"),
        h("ul", { class: "team-why-help" }, c.reasons.map((x) => h("li", {}, jaText(x))))) : null)));

  /** 「去资源页」 for a picker without card data / owned cards (资源 → 卡资料, 扫描持有卡). */
  const goResources = () => (actions?.setTab ? () => actions.setTab("resources") : null);

  function pickP() {
    const team = fieldCheck(state, note, values).team;
    const used = {};
    (team?.s || []).forEach((id, i) => { if (id) used[id] = S_SLOTS[i]; });
    if (team?.guest) used[team.guest] = "Guest";
    const owned = ownedCards(state);
    openTeamCardPicker({ kind: "P", slotLabel: "P 偶像", value: values[roles.P] || "", used, owned: new Set(owned.p),
      editOwned: () => editOwned("P"), goResources: goResources(), onSelect: (cardId) => {
        // 推荐编队: a new target P drops the S/Guest recommended for the old one (a new recommendation follows)
        const recommend = fieldCheck(state, note, values).mode === "recommend";
        onChange(recommend && cardId !== values[roles.P] ? { [roles.P]: cardId, ...clearCards() } : { [roles.P]: cardId });
      } });
  }

  function pickS(slot) {
    const used = {};
    for (const role of ["P", ...S_SLOTS, "guest"]) if (roles[role] && roles[role] !== slot.key && values[roles[role]]) used[values[roles[role]]] = role;
    const owned = ownedCards(state);
    openTeamCardPicker({ kind: "S", slotLabel: slot.label, value: values[slot.key] || "", used, owned: new Set(owned.s),
      editOwned: () => editOwned("S"), goResources: goResources(), onSelect: (cardId) => onChange({ [slot.key]: cardId }) });
  }

  function editOwned(kind) {
    const owned = ownedCards(state);
    const apply = (next) => { if (!saveOwnedCards(state, actions, next)) render(); };
    openOwnedPicker({
      kind, owned: new Set(kind === "P" ? owned.p : owned.s), goResources: goResources(),
      onImport: () => openInventoryImport({ owned: ownedCards(state), onApply: apply }),
      onApply: (ids) => apply({ ...ownedCards(state), [kind === "P" ? "p" : "s"]: ids }),
    });
  }

  // The field is rebuilt on every store render: start from the newest loaded proposal (a regenerated one included) and
  // read the file only before the first load (2026-10-13 live: a re-import returned the page-load copy → 「P 或主属性已变」).
  proposal = teamProposal();
  render();
  const before = teamSnapshot();
  loadTeamSnapshot().then((loaded) => { if (loaded !== before) render(); });
  if (!proposal) loadTeamProposal().then((loaded) => { proposal = loaded; render(); });
  return field;
}

// W.I.N.G. 本周评分卡 (read-only, 2026-10-08). Shows every candidate of one week grouped into
// the scorer's display groups, the chosen action and the excluded options with their reasons. Data: the payload of
// agent/core/wing_score.score_card (plan.scores / excluded / context). The preview uses the synthetic offline
// score_week output in fixtures (state.produce.score_cards); live wiring comes later. Nothing here edits weights or
// changes a decision.
import { h, clear } from "./dom.js";

// Same markup as common.emptyState (common.js defines custom elements, so it is not imported here and this module
// stays loadable by the node tests).
function emptyState({ title, body }) {
  return h("div", { class: "empty-state" }, h("div", { class: "empty-title", text: title }),
    body ? h("p", { class: "empty-body", text: body }) : null);
}

const STYLE_HREF = "styles/wing-score-card.css";

// Mirror of wing_score.TERM_GROUPS (tests/test_wing_score_card.py keeps them equal).
export const TERM_GROUPS = [
  ["primary", "主属性成长", ["stat_gain", "cap_overflow"]],
  ["other", "其他属性/Me", ["off_stat", "mental"]],
  ["sp", "SP", ["sp"]],
  ["support", "支援（絆+事件）", ["bond", "events"]],
  ["fans", "粉丝/试镜进度", ["fans", "audition_progress", "fan_urgency", "audition_stats"]],
  ["risk", "体力与失败风险", ["stamina", "trouble_risk"]],
  ["misc", "约定/テンション", ["promise", "tension"]],
];

export const RULE_TEXT = {
  best: "取最高分",
  fallback_empty_room: "都不划算时 → 上主属性课（空房）",
  fallback_rest: "都不划算时 → 休息",
  no_candidate: "没有可打分的行动（交给规则表或停止）",
  no_reference_gain: "缺少空房课参考值（交给规则表）",
};

// The three plain controls the review proposes instead of raw weights; listed only, not implemented yet.
export const FUTURE_CONTROLS = ["风险厌恶（低/中/高）", "SP 重视", "试镜积极度"];

const KINDS = new Set(["lesson", "work", "rest", "audition"]);
const MAX_ROWS = 40;

function text(value, max = 80) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function cleanList(value, max = 8) {
  return Array.isArray(value) ? value.filter((v) => typeof v === "string").slice(0, max).map((v) => v.slice(0, 40)) : [];
}

/** Validated copy of a wing_score.score_card payload, or null. Unknown groups are ignored; missing ones read as 0. */
export function cleanScoreCard(raw) {
  if (!raw || typeof raw !== "object" || raw.version !== 1 || !Array.isArray(raw.rows)) return null;
  const rows = [];
  for (const r of raw.rows.slice(0, MAX_ROWS)) {
    if (!r || typeof r.id !== "string" || !KINDS.has(r.kind)) continue;
    const groups = {};
    for (const [key] of TERM_GROUPS) groups[key] = num(r.groups?.[key]) ?? 0;
    rows.push({ id: text(r.id, 40), kind: r.kind, label: text(r.label, 40) || r.kind, score: num(r.score), groups,
      trouble: num(r.trouble), unknown: cleanList(r.unknown), chosen: r.chosen === true });
  }
  const excluded = [];
  for (const e of (Array.isArray(raw.excluded) ? raw.excluded : []).slice(0, MAX_ROWS)) {
    if (!e || typeof e.id !== "string" || !KINDS.has(e.kind)) continue;
    excluded.push({ id: text(e.id, 40), label: text(e.label, 40) || e.kind, reason: text(e.reason, 40),
      reasonText: text(e.reason_text, 60) || text(e.reason, 40), trouble: num(e.trouble) });
  }
  const ctx = raw.context && typeof raw.context === "object" ? raw.context : {};
  return { rule: text(raw.rule, 40), summary: text(raw.summary, 200), rows, excluded,
    floor: num(ctx.floor), stamina: num(ctx.stamina), fallbackReason: text(ctx.fallback_reason, 120) };
}

function fmt(value) {
  if (value === null) return "—";
  const rounded = Math.round(value * 10) / 10;
  return (rounded > 0 ? "+" : "") + rounded.toFixed(1);
}

function tone(value) {
  if (value === null || Math.abs(value) < 0.05) return "wsc-zero";
  return value > 0 ? "wsc-pos" : "wsc-neg";
}

export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

const TOP_ROWS = 4;

/** One-line summary of a card for a closed fold: the chosen action and its score. */
export function scoreCardHeadline(card) {
  const chosen = card?.rows.find((r) => r.chosen) || null;
  if (!chosen) return card ? `${card.rows.length} 个候选 · 无已选` : "";
  return `选 ${chosen.label} · ${chosen.score === null ? "—" : chosen.score.toFixed(1)}`;
}

function scoreRow(r) {
  const chips = TERM_GROUPS.filter(([key]) => Math.abs(r.groups[key]) >= 0.05)
    .map(([key, label]) => h("span", { class: `wsc-chip ${tone(r.groups[key])}` },
      h("span", { class: "wsc-chip-label", text: label }), h("span", { class: "num", text: fmt(r.groups[key]) })));
  // Details on demand: the chosen row shows its groups, other rows open them with one click.
  const body = h("div", { class: "wsc-row-body" },
    h("div", { class: "wsc-meta muted" }, h("span", { text: r.id }),
      r.trouble ? h("span", { text: `トラブル ${r.trouble}%` }) : null,
      r.unknown.length ? h("span", { class: "wsc-unknown", title: r.unknown.join(", "), text: `未读 ${r.unknown.length}` }) : null),
    chips.length ? h("div", { class: "wsc-chips" }, chips) : h("div", { class: "wsc-meta muted", text: "各组均为 0" }));
  body.hidden = !r.chosen;
  const head = h("button", { class: "wsc-row-head", type: "button", "aria-expanded": String(r.chosen),
    title: r.chosen ? "打分器本周选择的行动" : "点开查看各组得分",
    onclick: () => { body.hidden = !body.hidden; head.setAttribute("aria-expanded", String(!body.hidden)); } },
  h("span", { class: "wsc-label", text: r.label }),
  r.chosen ? h("span", { class: "wsc-badge", text: "已选" }) : null,
  r.unknown.length && !r.chosen ? h("span", { class: "wsc-dot", title: `未读 ${r.unknown.length}`, "aria-hidden": "true" }) : null,
  h("span", { class: `wsc-score num ${tone(r.score)}`, text: r.score === null ? "—" : r.score.toFixed(1) }));
  return h("li", { class: r.chosen ? "wsc-row wsc-chosen" : "wsc-row" }, head, body);
}

/** DOM of one cleaned card: the top candidates (score, chosen badge; groups on click), the rest on demand, then exclusions. */
export function scoreCardView(card, { title = "", source = "" } = {}) {
  ensureStyles();
  const rows = card.rows.map(scoreRow);
  const facts = [
    card.stamina === null ? null : `体力 ${card.stamina}`,
    card.floor === null ? null : `兜底线 ${card.floor.toFixed(1)}`,
    `规则：${RULE_TEXT[card.rule] || card.rule || "—"}`,
  ].filter(Boolean).join(" · ");
  const list = h("ol", { class: "wsc-list", "aria-label": "候选行动评分（从高到低）" }, rows.slice(0, TOP_ROWS));
  const rest = rows.slice(TOP_ROWS);
  const more = rest.length ? h("button", { class: "btn btn-small btn-text wsc-more", type: "button",
    onclick: () => { list.append(...rest); more.remove(); } }, `显示其余 ${rest.length} 个候选`) : null;
  return h("div", { class: "wsc" },
    title ? h("div", { class: "wsc-title", text: title }) : null,
    source ? h("div", { class: "muted small", text: source }) : null,
    card.summary ? h("p", { class: "wsc-summary", text: card.summary }) : null,
    h("p", { class: "muted small", text: facts }),
    list, more,
    card.excluded.length
      ? h("details", { class: "wsc-excluded" }, h("summary", { class: "wsc-subtitle", text: `未参与比较（${card.excluded.length}）` }),
        h("ul", {}, card.excluded.map((e) => h("li", {},
          h("span", { class: "wsc-label", text: e.label }), h("span", { class: "wsc-id muted", text: e.id }),
          h("span", { text: `：${e.reasonText}${e.trouble !== null && e.reason === "trouble_hard_cap" ? `（${e.trouble}%）` : ""}` })))))
      : null);
}

/** Cleaned preview cards of state.produce.score_cards = [{title, source, card}]. */
export function scoreCardItems(state) {
  return (Array.isArray(state.produce?.score_cards) ? state.produce.score_cards : [])
    .map((item) => ({ title: text(item?.title, 60), source: text(item?.source, 60), card: cleanScoreCard(item?.card) }))
    .filter((item) => item.card);
}

export const SCORE_NOTE = "只读：显示每周各按钮的打分（单位：主属性 +1）；不改变决策，未读到的读数标「未读」。";

/** Body of the 「本周评分卡」 fold on the W.I.N.G. tab (the fold header is the panel's; this is its content). */
export function scoreCardSection(state) {
  const items = scoreCardItems(state);
  if (!items.length) return h("div", { class: "wsc-section" }, emptyState({ title: "还没有评分记录" }));
  const holder = h("div", {});
  let current = 0;
  const tabs = items.length > 1 ? h("div", { class: "segmented wsc-tabs", role: "group", "aria-label": "样例周" }) : null;
  const show = () => {
    clear(holder);
    holder.append(scoreCardView(items[current].card, items[current]));
    if (tabs) [...tabs.children].forEach((b, i) => { b.classList.toggle("is-selected", i === current); b.setAttribute("aria-pressed", String(i === current)); });
  };
  if (tabs) items.forEach((item, i) => tabs.append(h("button", { type: "button", class: "segment", text: item.title.replace(/^.*· /, "").replace(/（.*$/, ""),
    onclick: () => { current = i; show(); } })));
  show();
  return h("div", { class: "wsc-section", title: SCORE_NOTE }, tabs, holder);
}

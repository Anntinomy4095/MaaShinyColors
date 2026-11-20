// Produce 结束确认（摘要卡 + 对话框）。显示 agent/core/produce_end.py 的 summary_payload：総合評価、フェスアイドル数值、EX 技能
// 稀有度（铜/银/金/虹）、ノウハウブック、True End、保留建议与提醒。只显示与记录用户意向，不发任何宿主请求：
// - 只有「保留」（默认）与「稍后在游戏内处理」两个选择；没有任何会执行的删除/移籍/売却按钮。
// - 「之后打算移籍」只是本页 localStorage 里的意向记录，给将来另行评审的受监督功能参考，Maa 不执行。
// - EX 金/虹提醒开关存在 prefs.notify（协议已允许任意布尔键，无需改 protocol）。
// Pure helpers first (node-tested), then DOM. Styles: styles/produce-end.css, linked on first use (CSP 'self').
import { h, icon, nextId, jaText, formatNumber } from "./dom.js";
import { loadLocal, saveLocal, inWebView } from "./bridge.js";
import { badgeIcon, rankIconKey, attrIconKey } from "./game-assets.js";

const STYLE_HREF = "styles/produce-end.css";
const INTENT_KEY = "sc-ui-produce-end-intent";
const MAX_INTENTS = 50;

export const EX_TIERS = ["bronze", "silver", "gold", "rainbow"];
export const EX_TIER_LABEL = { bronze: "铜", silver: "银", gold: "金", rainbow: "虹" };
const RANKS = ["F", "F+", "E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+", "SS", "SS+"];
const STATS = [["vo", "Vo"], ["da", "Da"], ["vi", "Vi"], ["me", "Me"]];
const TONES = new Set(["info", "success", "warning", "accent", "danger"]);
export const CHOICES = { keep: "保留", later_in_game: "稍后在游戏内处理" };
/** prefs.notify keys (host: ^[a-z][a-z0-9_]{0,31}$, ≤16 keys in total). Absent = on, like the other notifications. */
export const END_NOTIFY_KEYS = [
  ["produce_end_ex_rainbow", "虹色 EX 技能提醒", "ex_rainbow"],
  ["produce_end_ex_gold", "金色 EX 技能提醒", "ex_gold"],
];
const SUGGESTION_LABEL = { keep: "建议保留", release_candidate: "符合移籍规则", undecided: "无法判断（总合评价未读）" };

const str = (v, max = 80) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const int = (v) => (Number.isInteger(v) && v >= 0 ? v : null);

/**
 * Whitelisted, safety-normalised view of a produce-end payload. Whatever the file says, the result never carries an
 * automatic action: `defaultChoice` is "keep", `autoAction` null, `requiresConfirmation` true. Unknown -> null.
 */
export function cleanEndPayload(raw) {
  if (!raw || typeof raw !== "object" || raw.version !== 1 || !raw.summary || typeof raw.summary !== "object") return null;
  const s = raw.summary;
  const stats = {};
  const caps = {};
  for (const [key] of STATS) {
    if (int(s.stats?.[key]) !== null) stats[key] = s.stats[key];
    if (int(s.stat_caps?.[key]) !== null) caps[key] = s.stat_caps[key];
  }
  const ex = (Array.isArray(s.ex_skills) ? s.ex_skills : []).slice(0, 20).filter((e) => e && typeof e === "object").map((e) => ({
    name: str(e.name), tier: EX_TIERS.includes(e.tier) ? e.tier : null,
    target: e.target === "produce" || e.target === "support" ? e.target : null, idol: str(e.idol, 40),
  }));
  const books = (Array.isArray(s.knowhow_books) ? s.knowhow_books : []).slice(0, 10).filter((b) => b && typeof b === "object").map((b) => ({
    name: str(b.name), level: Number.isInteger(b.level) && b.level >= 1 && b.level <= 6 ? b.level : null,
    count: Number.isInteger(b.count) && b.count >= 1 && b.count <= 9 ? b.count : 1,
  }));
  const rec = raw.recommendation || {};
  const notes = (Array.isArray(raw.notifications) ? raw.notifications : []).slice(0, 20)
    .filter((n) => n && typeof n.key === "string" && str(n.message, 160))
    .map((n) => ({ key: n.key.slice(0, 40), tone: TONES.has(n.tone) ? n.tone : "info", message: str(n.message, 160) }));
  const comm = Array.isArray(s.communication_level) && s.communication_level.length === 2 && s.communication_level.every((v) => int(v) !== null)
    ? s.communication_level : null;
  return {
    rank: RANKS.includes(s.rank) ? s.rank : null,
    finalFans: int(s.final_fans),
    stats, caps,
    spLost: int(s.sp_lost),
    liveSkills: (Array.isArray(s.live_skills) ? s.live_skills : []).map((v) => str(v, 40)).filter(Boolean).slice(0, 8),
    ex,
    bestTier: EX_TIERS.includes(s.best_ex_tier) ? s.best_ex_tier : null,
    books,
    trustLevel: int(s.trust_level),
    commLevel: comm,
    trueEnd: s.true_end === true ? true : s.true_end === false ? false : null,
    unknowns: (Array.isArray(s.unknowns) ? s.unknowns : []).filter((u) => typeof u === "string").slice(0, 20),
    suggestion: ["keep", "release_candidate", "undecided"].includes(rec.suggestion) ? rec.suggestion : "keep",
    reasons: (Array.isArray(rec.reasons) ? rec.reasons : []).filter((r) => typeof r === "string").slice(0, 8),
    notifications: notes,
    stopReason: str(raw.stop_reason, 60),
    // ANN-29: parts of 獲得報酬 Maa could not read; the run stayed on the page instead of pressing OK.
    rewardMissing: (Array.isArray(raw.reward_missing) ? raw.reward_missing : []).filter((m) => typeof m === "string" && /^[a-z0-9_]{1,40}$/.test(m)).slice(0, 12),
    // Safety invariants: never read from the payload.
    defaultChoice: "keep",
    autoAction: null,
    requiresConfirmation: true,
  };
}

/** Notifications after the user's prefs.notify switches (EX 金/虹 reminders can be turned off; safety notes cannot). */
export function visibleNotifications(clean, notify = {}) {
  if (!clean) return [];
  const off = new Set(END_NOTIFY_KEYS.filter(([pref]) => notify?.[pref] === false).map(([, , key]) => key));
  return clean.notifications.filter((n) => !off.has(n.key));
}

const MISSING_LABEL = { ex_skills: "Exスキル 列表", ex_rows_partial: "Exスキル 列表（未读全）", knowhow_books: "ノウハウブック 数量" };

/** "第 2 本书" / "第 3 本书的保護" ... for the reward parts Maa could not read (pure). */
export function missingText(code) {
  if (MISSING_LABEL[code]) return MISSING_LABEL[code];
  const m = /^book_(\d+)(?:_(rows_partial|protect))?$/.exec(code);
  if (!m) return code;
  return `第 ${m[1]} 本ノウハウブック${m[2] === "protect" ? "的「保護」" : m[2] === "rows_partial" ? "（内容未读全）" : ""}`;
}

/** Short headline for the summary card, e.g. "B+ · 虹 EX ×1 · True End". */
export function headline(clean) {
  if (!clean) return "";
  const parts = [clean.rank ? `总合评价 ${clean.rank}` : "总合评价 未读"];
  const counts = {};
  for (const e of clean.ex) if (e.tier === "gold" || e.tier === "rainbow") counts[e.tier] = (counts[e.tier] || 0) + 1;
  for (const tier of ["rainbow", "gold"]) if (counts[tier]) parts.push(`${EX_TIER_LABEL[tier]} EX ×${counts[tier]}`);
  if (clean.trueEnd === true) parts.push("True End");
  else if (clean.trueEnd === false) parts.push("未见 True End");
  return parts.join(" · ");
}

/** The user's later-release intent for one run (local record only; never sent, never executed). */
export function recordIntent(runKey, intent, store = { load: loadLocal, save: saveLocal }) {
  if (typeof runKey !== "string" || !runKey) return null;
  const saved = store.load(INTENT_KEY);
  const all = saved && typeof saved === "object" && !Array.isArray(saved) ? { ...saved } : {};
  if (intent === "release_later") all[runKey] = { intent, executed: false };
  else delete all[runKey];
  const keys = Object.keys(all);
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_INTENTS))) delete all[key];
  store.save(INTENT_KEY, all);
  return all[runKey] || null;
}

export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

// ---- DOM ---------------------------------------------------------------------------------------------------------
/** 総合評価: the in-game rank badge (local game-assets, optional) beside the rank text; text only when missing. */
function rankView(rank) {
  return h("span", { class: "pe-rank-wrap" }, badgeIcon(rankIconKey(rank), { size: 28, decorative: true, cls: "pe-rank-img" }),
    h("strong", { class: "pe-rank", text: rank }));
}

/** Vo/Da/Vi/Me label: the in-game attribute tile when available (alt = label), else the muted text. */
function statLabel(key, label) {
  return badgeIcon(attrIconKey(key), { size: 20, label, cls: "pe-stat-img" }) || h("span", { class: "muted", text: label });
}

function tierBadge(tier) {
  return h("span", { class: `pe-tier pe-tier-${tier || "unknown"}`, text: tier ? EX_TIER_LABEL[tier] : "未读" });
}

/** Summary block (used by the dialog and the Produce tab card). */
export function summaryView(clean, { compact = false } = {}) {
  if (!clean) return h("p", { class: "muted small", text: "没有 Produce 结束摘要" });
  const kv = (label, value) => [h("dt", { text: label }), h("dd", {}, value == null ? h("span", { class: "muted", text: "未读" }) : value)];
  const stats = STATS.map(([key, label]) => (key in clean.stats
    ? h("span", { class: "pe-stat" }, statLabel(key, label), ` ${clean.stats[key]}`, key in clean.caps ? h("span", { class: "muted", text: `/${clean.caps[key]}` }) : null)
    : h("span", { class: "pe-stat muted" }, statLabel(key, label), " 未读")));
  const exList = clean.ex.length
    ? h("ul", { class: "pe-ex-list" }, clean.ex.map((e) => h("li", { class: "pe-ex" }, tierBadge(e.tier),
      h("span", { class: "pe-ex-name" }, e.name ? jaText(e.name) : h("span", { class: "muted", text: "名称未读" })),
      e.target ? h("span", { class: "muted small", text: e.target === "produce" ? "P 用" : "S 用" }) : null)))
    : null;
  const books = clean.books.length
    ? clean.books.map((b) => `${b.name || "ノウハウブック"}${b.level ? ` Lv.${b.level}` : ""}${b.count > 1 ? ` ×${b.count}` : ""}`).join("、")
    : null;
  const te = clean.trueEnd === null ? null : h("span", { class: clean.trueEnd ? "pe-ok" : "pe-warn", text: clean.trueEnd ? "已看到" : "未确认" });
  return h("dl", { class: "kv pe-kv" },
    kv("总合评价", clean.rank ? rankView(clean.rank) : null),
    kv("True End", te),
    compact ? null : kv("能力", h("span", { class: "pe-stats" }, stats)),
    compact ? null : kv("粉丝", clean.finalFans == null ? null : formatNumber(clean.finalFans)),
    kv("EX 技能", exList),
    kv("ノウハウブック", books ? jaText(books) : null),
    compact ? null : kv("失去的 SP", clean.spLost == null ? null : String(clean.spLost)),
    compact || !clean.liveSkills.length ? null : kv("Live 技能", jaText(clean.liveSkills.join(" / "))),
    compact ? null : kv("信頼度", clean.trustLevel == null ? null : `Lv.${clean.trustLevel}`));
}

function noteList(notes) {
  if (!notes.length) return null;
  return h("ul", { class: "pe-notes", role: "list" }, notes.map((n) => h("li", { class: `pe-note tone-${n.tone}` }, jaText(n.message))));
}

function notifySwitch([pref, label], notify, onToggle) {
  const id = nextId("pe-nt");
  const on = notify?.[pref] !== false;
  return h("li", {}, h("span", { id, text: label }),
    h("button", { class: `toggle${on ? " is-on" : ""}`, type: "button", role: "switch", "aria-checked": String(on), "aria-labelledby": id,
      onclick: (e) => {
        const next = e.currentTarget.getAttribute("aria-checked") !== "true";
        e.currentTarget.setAttribute("aria-checked", String(next));
        e.currentTarget.classList.toggle("is-on", next);
        onToggle(pref, next);
      } },
    h("span", { class: "toggle-track" }, h("span", { class: "toggle-thumb" }))));
}

/** EX 金/虹 reminder switches (prefs.notify). `onNotify(key, value)` = actions.setNotify; null = preview only. */
export function notifySettings(notify, onNotify) {
  const local = { ...(notify || {}) };
  return h("ul", { class: "toggle-list pe-toggles" }, END_NOTIFY_KEYS.map((entry) => notifySwitch(entry, local, (key, value) => {
    local[key] = value;
    if (typeof onNotify === "function") onNotify(key, value);
  })));
}

/**
 * Dialog. `onChoice(choice)` receives "keep" | "later_in_game" (Esc / 关闭 = keep). Nothing here sends a request; the
 * caller decides what to do with the choice (today: record it). No button deletes, releases or sells.
 */
export function openProduceEnd({ payload, notify = {}, onNotify = null, onChoice = null, runKey = null, preview = false }) {
  ensureStyles();
  const clean = cleanEndPayload(payload);
  const titleId = nextId("pe");
  const dialog = h("dialog", { class: "dialog card-dialog pe-dialog", "aria-labelledby": titleId });
  let done = false;
  const finish = (choice) => {
    if (done) return;
    done = true;
    dialog.close();
    dialog.remove();
    if (typeof onChoice === "function") onChoice(CHOICES[choice] ? choice : "keep");
  };
  const intentBox = h("input", { type: "checkbox", id: nextId("pe-intent"),
    onchange: (e) => recordIntent(runKey || "preview", e.target.checked ? "release_later" : null) });
  const keep = h("button", { class: "btn btn-primary", type: "button", onclick: () => finish("keep") }, CHOICES.keep);
  const suggestion = clean ? clean.suggestion : "keep";
  dialog.append(
    h("div", { class: "card-dialog-head" },
      h("h2", { id: titleId, class: "dialog-title", text: "Produce 结束确认" }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "关闭（保留）", title: "关闭（保留）", onclick: () => finish("keep") }, icon("close", { size: 16 }))),
    preview ? h("p", { class: "pe-preview muted small", text: "预览：合成示例数据，不来自游戏" }) : null,
    h("p", { class: "dialog-sub", text: clean ? headline(clean) : "摘要无效" }),
    clean?.rewardMissing.length ? h("p", { class: "pe-note tone-warning", role: "note" },
      jaText(`獲得報酬有未读完的部分：${clean.rewardMissing.map(missingText).join("、")}。已停在此页，未按 OK；请在游戏里看完后自行按 OK。`)) : null,
    h("div", { class: "pe-body" },
      summaryView(clean),
      noteList(visibleNotifications(clean, notify)),
      h("div", { class: `pe-suggest pe-suggest-${suggestion}` },
        h("strong", { text: SUGGESTION_LABEL[suggestion] }),
        h("p", { class: "muted small" }, jaText("需在游戏内手动：フェスアイドル移籍、ノウハウブック売却/保護、EX 技能再抽選（体力 30）"))),
      h("label", { class: "pe-intent small", for: intentBox.id }, intentBox,
        h("span", { text: "标记：打算移籍这张フェスアイドル（仅本机记录，Maa 不执行）" })),
      h("div", { class: "pe-settings" }, h("div", { class: "muted small", text: "结束提醒" }), notifySettings(notify, onNotify))),
    h("div", { class: "dialog-actions" },
      h("button", { class: "btn", type: "button", onclick: () => finish("later_in_game") }, CHOICES.later_in_game),
      keep));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish("keep"); });
  document.body.append(dialog);
  dialog.showModal();
  keep.focus();
  return dialog;
}

// ---- real end result (ANN-29): tools/ui_engine.py writes ui/game-assets/produce-end-last.js after a W.I.N.G. run --
/** {writtenAt, payload} of the last W.I.N.G. end summary, or null (cleaned; display only). */
export function cleanEndResult(data) {
  if (!data || typeof data !== "object" || typeof data.written_at !== "string" || Number.isNaN(Date.parse(data.written_at))) return null;
  return cleanEndPayload(data.payload) ? { writtenAt: data.written_at, payload: data.payload } : null;
}

/** The end result written by the run that started at `since` (ISO), or null. Reads the module fresh each time. */
export async function loadEndResult(since) {
  try {
    const module = await import(`../game-assets/produce-end-last.js?t=${Date.now()}`);
    const result = cleanEndResult(module.default);
    if (!result) return null;
    return !since || Date.parse(result.writtenAt) >= Date.parse(since) - 5000 ? result : null;
  } catch {
    return null;
  }
}

// ---- preview (mock bridge only): ?open=produce-end -------------------------------------------------------------
function whenReady(fn) {
  const root = document.documentElement;
  if (root.dataset.ready === "true") { fn(); return; }
  const observer = new MutationObserver(() => {
    if (root.dataset.ready === "true") { observer.disconnect(); fn(); }
  });
  observer.observe(root, { attributes: true, attributeFilter: ["data-ready"] });
}

export function maybeOpenPreview() {
  if (typeof window === "undefined" || typeof MutationObserver === "undefined" || inWebView()) return;
  const open = new URLSearchParams(window.location.search).get("open");
  if (open !== "produce-end" && open !== "produce-end-plain") return;
  whenReady(async () => {
    const { SAMPLE_END_PAYLOAD, PLAIN_END_PAYLOAD } = await import("../fixtures/produce-end-sample.js");
    const notify = loadLocal("sc-ui-prefs")?.notify || {};
    openProduceEnd({ payload: open === "produce-end" ? SAMPLE_END_PAYLOAD : PLAIN_END_PAYLOAD, notify, preview: true, runKey: "preview" });
  });
}

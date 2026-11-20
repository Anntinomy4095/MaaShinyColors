// 「Produce」 tab: Produce runs that cannot share the daily queue. W.I.N.G. is the first (and today only)
// mode; a one-row scenario rail on top lists the game's scenarios (2026-10-25: the later ones as placeholders). A later
// mode joins PRODUCE_MODES and its rail item becomes a tab, so the narrow panel shows one mode's settings at a time.
// The W.I.N.G. settings are the AutoProduce options of the saved profile (same fields, same fold cards as before in
// 任务); 开始 calls actions.startWing() -> the supervised_wing start path in app.js. 2026-10-13 (user): no start dialog and
// no 开局 choice — pressing 开始 is the stamina consent (30, at most one Produce per run); an in-progress Produce is continued
// only after the Agent verified its P idol and team (else it stops). The request carries confirmed:true and the host
// re-derives the spend flag. 2026-10-13 visual polish: a summary card on top (P card + 目标 / 主属性 / P 偶像 / 编队, each
// opens its fold), per-fold value summaries, long notes behind an ⓘ toggle.
import { h, icon, jaText, clear } from "./dom.js";
import { attentionCard, foldSection, sectionDeck, STATUS_HINT, statusPill } from "./common.js";
import { optionBody, liveIndicator, liveStartProblems } from "./task-panel.js";
import { caseLabel, devMode, knowhowAfterReading, knowhowAfterText, SCENARIO_CASES, SCENARIO_OPTION, wingBlockers, wingLoopReading, wingLoopText, wingRunBar, wingScenario, WING_TASK } from "./display.js";
import { caseNote, haloGoalsLine, inactiveSettings, minigameLine, primaryLine, resolvedPrimary, scenarioItems, sectionSummary, switchNoteParts, wingSummary } from "./wing-run.js";

export { caseNote, inactiveSettings, wingSummary };
import { taskIcon } from "./task-icons.js";
import { MINIGAME_ENTRY_VERIFIED, loadTeamProposal, teamProposal } from "./team-proposal.js";
import { outcomeText } from "./outcomes.js";
import { anyCardArt } from "./game-assets.js";
import { cardDetailsStatus } from "./card-library.js";
import { scoreCardSection, ensureStyles as ensureScoreStyles } from "./wing-score-card.js";
import { loadEndResult, openProduceEnd } from "./produce-end.js";

const STYLE_HREF = "styles/wing-panel.css";

let stylesReady = false;
let proposalRequested = false;

/** Links styles/wing-panel.css once; `onReady` runs when it has loaded (the panel stays hidden until then, no flash). */
function ensureStyles(onReady) {
  if (typeof document === "undefined" || stylesReady) return true;
  let link = document.querySelector(`link[href="${STYLE_HREF}"]`);
  if (!link) {
    link = h("link", { rel: "stylesheet", href: STYLE_HREF });
    document.head.append(link);
  }
  if (link.sheet) { stylesReady = true; return true; }
  const done = () => { stylesReady = true; onReady?.(); };
  link.addEventListener("load", done, { once: true });
  link.addEventListener("error", done, { once: true });
  return false;
}

/** Produce modes of this tab, in order. `task` = the interface task holding the mode's settings. */
export const PRODUCE_MODES = [
  { key: "wing", label: "W.I.N.G.", task: WING_TASK },
];

/** Modes whose task the schema has (pure). */
export function produceModes(state) {
  return PRODUCE_MODES.filter((m) => state.schema.tasks.some((t) => t.name === m.task));
}

/** Latest finished W.I.N.G. run of the run history (mode supervised_wing), or null (pure). */
export function lastWingRun(state) {
  return (state.runs || []).find((r) => r.mode === "supervised_wing" && (r.tasks || []).some((t) => t.name === WING_TASK)) || null;
}

// ---- Option decoration (display only) ---------------------------------------------------------------------------------
// Notes per chosen case come from annotations `case_notes`; settings another choice makes irrelevant are shown inactive
// (their values are kept); long static notes sit behind an ⓘ toggle (kept open across re-renders for this page load).

const opt = (state, entry, key) => entry.option?.[key] ?? state.schema.options[key]?.default_case;
const foldKey = (heading) => `task-${WING_TASK}-h-${heading}`;
const openNotes = new Set();
const NOTE_INLINE_MAX = 40;   // shorter static notes stay visible

function fieldOf(root, key) {
  const id = `opt-${WING_TASK}-${key}`;
  const el = root.querySelector(`[data-focus-key="${id}"], [data-focus-key^="${id}:"]`);
  return el?.closest(".field") || null;
}

function addHint(field, text, cls = "") {
  if (!field || !text) return null;
  const p = h("p", { class: `field-hint wing-hint${cls ? ` ${cls}` : ""}` }, jaText(text));
  field.append(p);
  return p;
}

/** Per-choice note as the tooltip of the chosen control (2026-10-24: the layout carries the information, not lines). */
function caseTip(field, text) {
  if (!field || !text) return;
  const target = field.querySelector(".segment.is-selected, .toggle, select") || field.querySelector(".field-label-row");
  if (target) target.title = text;
}

function labelRow(field) {
  return field?.querySelector(".field-label-row") || null;
}

function tagOn(field, text, tone) {
  labelRow(field)?.append(h("span", { class: `wing-tag tone-${tone}`, text }));
}

function subhead(text) {
  return h("div", { class: "wing-subhead", role: "heading", "aria-level": "5", text });
}

/** SP 使用策略 order 「a → b → c」 as a compact numbered row (the text stays readable for screen readers). */
function orderHint(field, text) {
  const steps = text.replace(/。$/, "").split(/\s*→\s*/);
  if (!field || steps.length < 3) return caseTip(field, text);
  const list = h("ol", { class: "wing-order", "aria-label": "SP 使用顺序" }, steps.map((step) => h("li", {}, jaText(step))));
  field.append(list);
  return list;
}

/** Long static note -> hidden behind an ⓘ button on the label row (aria-expanded; stays open for this page load). */
function collapseNote(field, key, note) {
  const row = labelRow(field);
  if (!row || !note) return;
  const id = `wing-note-${key}`;
  note.id = id;
  note.classList.add("wing-note");
  const open = openNotes.has(key);
  note.hidden = !open;
  const button = h("button", { class: `wing-info${open ? " is-open" : ""}`, type: "button", "aria-expanded": String(open), "aria-controls": id,
    "aria-label": "说明", title: open ? "收起说明" : "说明", dataset: { focusKey: `wing-info-${key}` },
    onclick: (event) => {
      const now = !openNotes.has(key);
      if (now) openNotes.add(key); else openNotes.delete(key);
      note.hidden = !now;
      event.currentTarget.setAttribute("aria-expanded", String(now));
      event.currentTarget.classList.toggle("is-open", now);
      event.currentTarget.title = now ? "收起说明" : "说明";
    } }, icon("help", { size: 16 }));
  row.append(button);
}

/** Adds the per-choice notes, the inactive state and grouping to the fields built by optionBody. */
export function decorateWing(root, state, entry, actions = null, running = false) {
  const annotations = state.annotations?.options || {};
  // Static notes as built by optionField (captured before any hint is added below).
  const staticNotes = new Map();
  for (const key of Object.keys(annotations)) {
    if (!key.startsWith("Produce")) continue;
    const field = fieldOf(root, key);
    const note = field && !field.classList.contains("kind-team") ? field.querySelector(":scope > p.field-hint") : null;
    if (note) staticNotes.set(key, note);
  }
  const inactive = inactiveSettings(state, entry);
  for (const key of Object.keys(annotations)) {
    if (!key.startsWith("Produce")) continue;
    const field = fieldOf(root, key);
    if (!field) continue;
    const text = caseNote(state, entry, key);
    const hasCase = Boolean(text);
    if (key === "ProduceSP加点" && text) orderHint(field, text);
    else caseTip(field, text);
    const note = staticNotes.get(key);
    if (!note) continue;
    // A switch note 「…。关：…」 shows only the sentence for the current state.
    if (field.classList.contains("kind-switch")) {
      const parts = switchNoteParts(note.textContent || "");
      if (parts.off) note.replaceChildren(jaText(opt(state, entry, key) === "Yes" ? parts.on : parts.off));
    }
    const plain = note.textContent || "";
    const keep = key in inactive || /^(暂不可用|消耗：)/.test(plain);
    if (!keep && (hasCase || plain.length > NOTE_INLINE_MAX)) collapseNote(field, key, note);
  }
  // 主属性: the resolved attribute of 「自动选择」 — the hint plus a marked segment, so automatic ≠ manual.
  const primaryField = fieldOf(root, "Produce主属性");
  const pCard = opt(state, entry, "ProduceP卡") || "";
  const primary = resolvedPrimary(opt(state, entry, "Produce主属性"), pCard);
  caseTip(primaryField, primaryLine(opt(state, entry, "Produce主属性"), pCard));
  if (primaryField && primary.auto && primary.stat) {
    for (const seg of primaryField.querySelectorAll(".segment")) {
      if (seg.textContent.trim() === primary.stat) { seg.classList.add("is-implied"); seg.title = "自动选择的结果"; }
      if (seg.classList.contains("is-selected")) seg.append(h("span", { class: "seg-sub", text: primary.stat }));
    }
  }
  // a 均衡 P card has no single specialty: 自动 cannot decide (team-rules.js blocks 开始), the field says it needs a choice
  if (primaryField && primary.auto && pCard && !primary.stat && anyCardArt(pCard)) tagOn(primaryField, "请选择", "warning");
  for (const [key, why] of Object.entries(inactive)) {
    const field = fieldOf(root, key);
    if (!field) continue;
    field.classList.add("is-inactive");
    field.querySelectorAll("button:not(.wing-info), select, input").forEach((el) => { el.disabled = true; });
    tagOn(field, key === "Produce保护Lv5书" ? "暂不可用" : "不生效", key === "Produce保护Lv5书" ? "warning" : "muted");
    const row = labelRow(field);
    if (row) row.title = why;
  }
  // True End cannot be played on Beginner (team-rules.js): the segment says so instead of a note line.
  const difficultyField = fieldOf(root, "Produce难度");
  if (difficultyField && opt(state, entry, "Produce目标") === "True End") {
    for (const seg of difficultyField.querySelectorAll(".segment")) {
      if (seg.textContent.trim() === "Beginner" && !seg.classList.contains("is-selected")) { seg.disabled = true; seg.title = "True End 需要 Middle 或 Master"; }
    }
  }
  // say "Halo": the goals come from the ノウハウ wanted list (no option of their own); shown under 带书
  if (wingScenario(state, entry) === "halo") {
    const goals = haloGoalsLine(state.prefs?.knowhow_wanted);
    addHint(fieldOf(root, "Halo带书"), goals.text, goals.warn ? "is-warn" : "");
  }
  // 开局前大富豪: what 自动 decides for the next new Produce; developers also see that the home entry is unverified.
  const gameField = fieldOf(root, "Produce大富豪");
  caseTip(gameField, minigameLine(state, entry));
  if (devMode(state) && !MINIGAME_ENTRY_VERIFIED && opt(state, entry, "Produce大富豪") !== "关") {
    addHint(gameField, "开发者：首页「Pデスク・ミニゲーム」入口（home_minigame_menu）还没实机验证，目标禁用。自动暂不玩；每次会在首页停止，未开局。", "is-dev");
  }
  // choices with long labels take the full row width (label above) instead of wrapping inside the control
  for (const key of ["Produce低分兜底", "ProduceSP加点", "Produce出牌风格"]) fieldOf(root, key)?.classList.add("wing-wide");
  // 行动与 SP: 行动 (失败率 / 低收益 / 约定) then SP (策略 / 上限学习阈值, which only applies to that strategy).
  const rest = fieldOf(root, "Produce休息阈值");
  const promise = fieldOf(root, "Produce约定");
  const low = fieldOf(root, "Produce低分兜底");
  const sp = fieldOf(root, "ProduceSP加点");
  if (promise && low) low.after(promise);
  rest?.before(subhead("行动"));
  sp?.before(subhead("SP"));
  fieldOf(root, "Produce上限余量")?.classList.add("is-child");
  fieldOf(root, "Produce终局清空SP")?.before(subhead("结束前"));
  fieldOf(root, "Produce报酬读取")?.before(subhead("结算画面「獲得報酬」"));
}

// ---- Summary card ------------------------------------------------------------------------------------------------------

function openSection(root, heading) {
  const fold = root?.querySelector(`details[data-fold="${foldKey(heading)}"]`);
  if (!fold) return;
  fold.open = true;
  fold.scrollIntoView({ block: "start", behavior: "smooth" });
  fold.querySelector("summary")?.focus({ preventScroll: true });
}

function pThumb(pCard) {
  const art = anyCardArt(pCard);
  const rarity = art?.rarity ? ` rar-${String(art.rarity).toLowerCase()}` : "";
  return h("span", { class: `wing-hero-thumb${art ? rarity : " is-empty"}`, "aria-hidden": "true" },
    art ? h("img", { src: art.src, alt: "", width: "52", height: "52", draggable: "false", decoding: "async" }) : icon("plus", { size: 20 }));
}

/**
 * Summary card (2026-10-24, user: 一眼看明白做什么): who (P card; a tap opens the P picker), what (目标 · 难度) and one line
 * of the resolved 主属性 / 编队. Each value opens its settings fold; a value that still needs a choice is accented.
 */
export function summaryCard(state, entry, panel) {
  const facts = wingSummary(state, entry);
  const pCard = opt(state, entry, "ProduceP卡") || "";
  const art = anyCardArt(pCard);
  const by = Object.fromEntries(facts.map((f) => [f.key, f]));
  const open = (f) => () => openSection(panel, f.section);
  const pickP = () => {
    const button = panel.querySelector(".kind-team [data-focus-key$=':p-change']");
    if (button && !button.disabled) button.click(); else openSection(panel, by.p.section);
  };
  const halo = wingScenario(state, entry) === "halo";
  const difficulty = halo ? "" : caseLabel(state, "Produce难度", opt(state, entry, "Produce难度"));
  const fact = (f, text) => h("button", { class: `wing-fact${f.missing ? " is-missing" : ""}`, type: "button", title: `${f.label}：${f.value}`,
    dataset: { fact: f.key, focusKey: `wing-fact-${f.key}` }, onclick: open(f) }, jaText(text));
  return h("section", { class: "wing-hero", role: "group", "aria-label": "本次 Produce" },
    h("button", { class: "wing-hero-p", type: "button", title: pCard ? "更换 P 偶像" : "选择 P 偶像", dataset: { focusKey: "wing-fact-thumb" }, onclick: pickP },
      pThumb(pCard)),
    h("div", { class: "wing-hero-main" },
      h("div", { class: "wing-hero-title" },
        // the scenario name is the lit rail item above, so the card starts with 目标 · 难度
        taskIcon(state, WING_TASK, { size: 18, cls: "wing-hero-icon" }),
        // say "Halo" has no 目标 / 难度 (掘书线 only): the title names the route and opens the same fold
        h("button", { class: "wing-hero-goal", type: "button", title: "培养目标", dataset: { fact: "goal", focusKey: "wing-fact-goal" },
          onclick: open(by.goal || by.book) }, jaText(halo ? "掘书线" : [by.goal.value, difficulty].filter(Boolean).join(" · ")))),
      pCard
        ? h("div", { class: "wing-hero-idol" }, h("span", { class: "wing-hero-idol-name" }, jaText(by.p.value)),
          art?.title ? h("span", { class: "wing-hero-card" }, jaText(`【${art.title}】`)) : null)
        : h("button", { class: "wing-hero-pick", type: "button", dataset: { focusKey: "wing-fact-p" }, onclick: pickP }, "选择 P 偶像"),
      h("div", { class: "wing-hero-facts" },
        halo ? fact(by.book, `带书 ${by.book.value}`) : fact(by.stat, `主属性 ${by.stat.short || by.stat.value}${by.stat.auto ? "（自动）" : ""}`),
        h("span", { class: "wing-hero-sep", "aria-hidden": "true", text: "·" }),
        fact(by.team, by.team.value))));
}

/**
 * Scenario rail: the game's scenarios as two groups, 育成 and 特化, set apart by spacing (a narrow panel
 * wraps the 特化 group onto a second line). The chosen one is lit; the others are dimmed placeholders (「未开放」).
 * H2c batch 5: W.I.N.G. and say "Halo" are buttons that switch 「Produce剧本」 (same settings below).
 */
function scenarioRail(state, entry, actions, running) {
  const ready = state.schema.options[SCENARIO_OPTION] ? Object.keys(SCENARIO_CASES) : ["wing"];
  const items = scenarioItems(ready, wingScenario(state, entry));
  const chip = (s) => {
    const cls = `produce-rail-item${s.current ? " is-current" : ""}${s.ready ? "" : " is-soon"}`;
    if (!s.ready) {
      return h("span", { class: cls, role: "listitem", title: s.title, "aria-disabled": "true" }, jaText(s.label),
        h("span", { class: "produce-rail-soon", text: "未开放" }));   // 2026-10-26 C14: said on the chip, not only in the tooltip
    }
    return h("span", { role: "listitem", class: "produce-rail-cell" }, h("button", {
      class: cls, type: "button", title: s.title, "aria-pressed": s.current ? "true" : "false", disabled: running && !s.current,
      dataset: { focusKey: `produce-rail-${s.key}` },
      onclick: () => { if (!s.current && !running) actions.setOption(WING_TASK, SCENARIO_OPTION, SCENARIO_CASES[s.key]); } }, jaText(s.label)));
  };
  const group = (key, label) => h("div", { class: "produce-rail-group", role: "group", "aria-label": label },
    items.filter((s) => s.group === key).map(chip));
  return h("div", { class: "produce-rail", role: "list", "aria-label": "剧本" }, group("growth", "育成"), group("special", "特化"));
}

function attention(state, actions) {
  const items = [];
  const st = state.taskStatus?.[WING_TASK];
  if (st && ["uncertain", "guard_refused", "takeover", "failed"].includes(st.status)) {
    items.push({ text: outcomeText(st.reason, WING_TASK) || STATUS_HINT[st.status] || "", tone: { uncertain: "uncertain", guard_refused: "warning", takeover: "accent", failed: "danger" }[st.status],
      action: st.status === "uncertain" ? { label: "已核对", focusKey: "ack-wing", title: "已在游戏里核对（只隐藏提醒，不重试）", onclick: () => actions.ackStatus(WING_TASK) } : null });
  }
  return attentionCard({ title: `${items.length} 件事需要你处理`, label: "需要处理", items });
}

function lastRunLine(state) {
  const run = lastWingRun(state);
  if (!run) return null;
  const task = run.tasks.find((t) => t.name === WING_TASK);
  const when = run.started_at ? new Date(run.started_at) : null;
  const at = when && !Number.isNaN(when.getTime()) ? `${when.getMonth() + 1}/${when.getDate()} ${String(when.getHours()).padStart(2, "0")}:${String(when.getMinutes()).padStart(2, "0")}` : "";
  const loop = wingLoopText(wingLoopReading(run.readings));
  const books = knowhowAfterText(knowhowAfterReading(run.readings));
  const text = [loop, outcomeText(task?.reason, WING_TASK) || (loop ? "" : STATUS_HINT[task?.status]) || "", books].filter(Boolean).join(" · ");
  const end = h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "wing-end" }, title: "打开上次 Produce 的结算摘要",
    onclick: async (event) => {
      const button = event.currentTarget;
      const result = await loadEndResult(run.started_at);
      if (result) openProduceEnd({ payload: result.payload, notify: state.prefs?.notify || {}, runKey: run.id });
      else button.replaceWith(h("span", { class: "muted small", text: "没有结算摘要" }));
    } }, "结算");
  return h("div", { class: "wing-last" },
    h("span", { class: "wing-last-label", text: `上次${at ? ` ${at}` : ""}` }),
    task?.status ? statusPill(task.status) : null,
    text ? h("span", { class: "wing-last-text" }, jaText(text)) : null,
    end);
}

/** 2026-11-23 (user): until the card details are on this PC, the top of the Produce page says so; gone once complete. */
export function cardDetailsBanner(state, actions) {
  const st = cardDetailsStatus(state.host);
  if (st.ready || !state.host?.card_library?.supported) return null;
  const text = st.busy
    ? (st.phase === "cards" && st.total ? `卡资料下载中 ${st.updated} / ${st.total} · 约 ${st.minutes} 分钟` : "卡资料下载中…")
    : "自动挖矿需要卡资料（技能、属性），首次约 45 分钟";
  return h("div", { class: "wing-library-banner", role: "status", dataset: { cardDetails: st.busy ? "busy" : "missing" } },
    h("span", { class: "grow", text }),
    st.busy ? null : h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: "wing-library-download" },
      onclick: () => actions.cardLibraryUpdate?.() }, "下载"));
}

/** 开始 / 停止 for W.I.N.G.: under the Produce page, or at the top of the wide layout's run column. */
export function wingRunView(state, actions) {
  const model = wingRunBar(state);
  // 实机未就绪 first (disabled with the reason, not a toast after the click)
  const blockers = model.kind === "start" ? [...liveStartProblems(state, model.devRun), ...wingBlockers(state).flatMap((b) => b.errors)] : [];
  const tone = model.kind === "stop" ? "is-running" : model.kind === "busy" ? "is-busy" : blockers.length ? "is-blocked" : "is-ready";
  const button = model.kind === "stop"
    ? h("button", { class: "btn btn-run btn-stop", type: "button", disabled: model.stopping, "aria-busy": model.stopping ? "true" : null, dataset: { focusKey: "wing-run" },
      title: model.stopping ? "已请求停止：当前步骤结束后停止" : "当前步骤结束后停下", onclick: () => actions.stopRun("cooperative") }, model.stopping ? "停止中…" : "停止")
    : h("button", { class: "btn btn-run btn-primary", type: "button", disabled: model.kind === "busy" || model.disabled || blockers.length > 0,
      title: model.title || model.tip || null, dataset: { focusKey: "wing-run" }, onclick: () => actions.startWing() }, "开始");
  const sub = blockers.length
    ? h("span", { class: "wing-run-block", role: "status" }, jaText(blockers[0]), blockers.length > 1 ? ` 等 ${blockers.length} 项` : "")
    // clauses wrap as whole phrases (「新局消耗：体力 30」 never splits across lines in the narrow panel)
    : model.sub ? h("span", { class: "wing-cost" }, model.sub.split(/(?<=；)/).map((part) => h("span", { class: "wing-clause" }, jaText(part)))) : null;
  return h("div", { class: `run-bar wing-run-bar ${tone}` },
    h("div", { class: "run-main" },
      h("div", { class: "run-summary" },
        h("div", { class: "run-summary-main" }, h("span", { class: "wing-run-dot", "aria-hidden": "true" }), h("span", { text: model.main })),
        h("div", { class: "run-summary-sub" }, liveIndicator(state), sub)),
      button));
}

class WingPanel extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    clear(this);
    this.className = "panel wing-panel";
    // first paint waits for wing-panel.css (an unstyled summary card flashed when the page opened on this tab)
    const styled = ensureStyles(() => { if (this.style) this.style.visibility = ""; });
    if (this.style) this.style.visibility = styled ? "" : "hidden";
    const modes = produceModes(state);
    const task = state.schema.tasks.find((t) => t.name === WING_TASK);
    const entry = state.profile.tasks.find((t) => t.name === WING_TASK);
    if (!modes.length || !task || !entry) {
      this.append(h("div", { class: "panel-scroll" }, h("p", { class: "muted small", text: "当前版本没有可用的 Produce 任务" })));
      return;
    }
    const running = state.run.active;
    const card = attention(state, actions);
    // 培养目标 / P 偶像与编队 start open; the lower-frequency folds start closed and show their values in one line.
    const body = h("div", { class: "task-body wing-body" },
      optionBody(state, actions, task, entry, running, { openFirst: 2, summarize: (heading) => sectionSummary(state, entry, heading) }));
    decorateWing(body, state, entry, actions, running);
    // 双栏: the setting groups as a section list beside the picked group (same fields, common.js sectionDeck)
    if (state.wide && body.querySelector(":scope > details.fold")) {
      const kids = [...body.children];
      body.replaceChildren(...kids.filter((el) => !el.matches("details.fold")), sectionDeck(kids, { key: "produce", label: "Produce 设置" }));
    }
    // the 大富豪 自动 line reads the recommendation's score: repaint once the proposal file has loaded
    if (!teamProposal() && !proposalRequested) {
      proposalRequested = true;
      loadTeamProposal().then(() => { if (this.isConnected !== false && this._data) this.render(); });
    }
    const dev = devMode(state) ? foldSection({ key: "wing-score", title: "本周评分卡", summary: h("span", { class: "muted small", text: "只读" }),
      children: () => { ensureScoreStyles(); return scoreCardSection(state); } }) : null;
    const scroll = h("div", { class: "panel-scroll", dataset: { scrollKey: "produce" } },
      cardDetailsBanner(state, actions), scenarioRail(state, entry, actions, running), summaryCard(state, entry, this), lastRunLine(state), card ? h("div", { class: "task-attention" }, card) : null, body, dev);
    this.append(scroll);
    if (state.wide !== "aside") this.append(wingRunView(state, actions));   // 工作台: the run column has 开始
  }
}
customElements.define("sc-wing-panel", WingPanel);

// ノウハウ 製本 workflow views (STEP → Halo): the active Target bar shared by all sub-pages, the 带书 STEP loop (main
// book → route: the STEP runs to the finished book, the one to run now with its 3 books → record result → compare →
// decide), the Target list / settings / comparison / reuse, the recommended-target picker, and 书库 insights. Logic lives
// in knowhow-rules.js / knowhow-plan.js; this module only renders. Display/planning only: nothing here touches the game.
import { clear, h, icon, jaText } from "./dom.js";
import { chip as pickChip, details, emptyState, foldSection, meter, subhead, toast } from "./common.js";
import {
  MODE_LABEL, ROLE_LABEL, STAGE_LABEL, STRATEGIES, STRATEGY_LABEL, analyzeInventory, analyzeTrio,
  classifyBook, compareResult, compareTargets, detectStage, evaluateBook, libraryConflicts, nextAction, planRoute, reusePlan,
  ruleValues, targetUnits, unitHit, usableFor, validateTarget, ATTR_LABEL,
} from "./knowhow-rules.js";
import {
  TARGET_TEMPLATES, activeTarget, bookHistory, decideRun, newId, parseRowsText, recordRun, removeTarget, removeUserBook,
  revertRun, runsFor, setActive, setMain, targetFromTemplate, upsertTarget,
} from "./knowhow-plan.js";
import { POSITIONS, POSITION_LABEL, PURPOSE_LABEL } from "./knowhow-data.js";
import { LIBRARY_GROUPS, shortName } from "./knowhow-views.js";
import { bookLabel, chip, choiceChips, idolBar, itemLabel, microTag, miniChip, pill, segmented } from "./knowhow-ui.js";

const SHORT_PURPOSE = { halo: "Halo", step: "S.T.E.P.", any: "两者" };

const DECISION_LABEL = { main: "设为主书", backup: "留作备用", stop: "不采用", failed: "无可用书" };
const STATE_TONE = { missing: "missing", below: "warn", met: "done", ideal: "done", excluded: "conflict", clear: "done" };

/** Everything the workflow views need, computed once per render. */
export function workflowState({ plan, books, team }) {
  const rules = ruleValues();   // settled rules only (2026-10-24: no user switches)
  const target = activeTarget(plan);
  const byFp = new Map(books.map((b) => [b.fingerprint, b]));
  const mainFp = target ? plan.main[target.id] || null : null;
  const mainBook = mainFp ? byFp.get(mainFp) || null : null;
  const stage = target ? detectStage(target, books, mainBook, team, rules) : null;
  const runs = target ? runsFor(plan, target.id) : { runs: [], failures: 0, last: null };
  return { plan, books, team, rules, target, byFp, mainFp, mainBook, stage, runs };
}

/** 「偶像【卡名】」 (the card-name brackets tell same-idol books apart); user-registered books keep their own title. */
function bookName(book) {
  const card = /^【[^】]*】/.exec(book?.title || "")?.[0] || "";
  return `${bookLabel(book)}${card}${book?.source === "user" && book?.idol ? "（登记）" : ""}`;
}

function unitChip(s) {
  const lv = s.hit ? `Lv${s.hit.level}` : s.unit.role === "excluded" ? "" : `≥Lv${s.unit.items[0].min_level}`;
  const mark = { missing: "缺", below: "Lv低", met: "✓", ideal: "理想", excluded: "排除", clear: "" }[s.state];
  const tone = s.unit.role === "optional" && s.state === "missing" ? "optional" : STATE_TONE[s.state];
  return h("span", { class: `kh-chip kh-unit is-${tone}`, title: `${ROLE_LABEL[s.unit.role]}：${s.unit.label}${s.hit ? `（${s.hit.name} Lv${s.hit.level}）` : ""}` },
    h("span", { class: "kh-unit-mark", text: mark }), h("span", { lang: "ja", text: s.unit.label }), lv ? h("span", { class: "kh-lv", text: lv }) : null);
}

// ------------------------------------------------------------------------------------------------- target bar
/**
 * Shared context on every sub-page: the Targets as pills (one tap switches; 2026-10-24 no dropdown), 「＋」 for the
 * recommended ones and the main book's progress. Without a saved Target 带书 previews 通用底本 (ws.preview, second
 * role-play: an empty page first asked for an unexplained choice); one tap on a template saves it.
 */
export function targetBar(ws, api, { compact = false } = {}) {
  const { plan, target, stage, mainBook } = ws;
  const view = api.view;
  if (!target) {
    return compact
      ? h("div", { class: "kh-target-bar is-empty" }, h("span", { class: "kh-tb-label", text: "目标" }), h("span", { class: "muted small", text: "还没有" }),
        h("button", { class: "btn btn-small btn-primary", type: "button", dataset: { focusKey: "kh-tb-goals" }, onclick: () => api.go({ sub: "goals" }) }, "＋ 新建目标"))
      : h("div", { class: "kh-target-bar is-empty" }, h("div", { class: "kh-tb-row" }, h("span", { class: "kh-tb-label", text: "目标" }),
        h("span", { class: "muted small", text: "选一个" })), targetPicker(ws, api));
  }
  if (ws.preview) {
    // 2026-10-26 user: deleting the last Target left a silent preview that looked usable but saved nothing
    return h("div", { class: "kh-target-bar is-empty" },
      h("div", { class: "kh-tb-row" },
        h("span", { class: "kh-tb-label", text: "目标" }),
        h("span", { class: "kh-tb-name", text: target.name }),
        h("span", { class: "kh-pill is-warn", title: "还没有保存的目标：这里先用「通用底本」演示推荐", text: "预览·未保存" }),
        h("button", { class: "btn btn-small btn-primary", type: "button", dataset: { focusKey: "kh-tb-adopt" },
          onclick: () => adoptTemplate(ws, api, (target.source || "").replace(/^template:/, "") || "base") }, "用这个目标"),
        h("button", { class: `btn btn-small btn-text${view.addTarget ? " is-on" : ""}`, type: "button", "aria-expanded": String(Boolean(view.addTarget)),
          dataset: { focusKey: "kh-tb-other" }, onclick: () => api.go({ addTarget: !view.addTarget }) }, "换一个")),
      view.addTarget ? targetPicker(ws, api, { current: target.source, onDone: () => { view.addTarget = false; } }) : null);
  }
  const prog = stage?.main?.progress || { done: 0, total: targetUnits(target).filter((u) => u.role !== "excluded").length };
  return h("div", { class: "kh-target-bar" },
    h("div", { class: "kh-tb-row" },
      h("span", { class: "kh-tb-label", text: "目标" }),
      plan.targets.length > 1
        ? choiceChips("当前目标", plan.targets.map((t) => [t.id, t.name]), target.id, (id) => api.save(setActive(plan, id), { mirror: true }), "kh-tb-t", { cls: "kh-tb-targets" })
        : h("span", { class: "kh-tb-name", text: target.name }),
      h("button", { class: `btn btn-small${view.addTarget ? " is-on" : ""}`, type: "button", "aria-expanded": String(Boolean(view.addTarget)),
        dataset: { focusKey: "kh-tb-add" }, onclick: () => api.go({ addTarget: !view.addTarget }) }, "＋ 新建"),
      mainBook ? h("span", { class: "kh-tb-prog num", title: `主书：${bookName(mainBook)}` }, `主书 ${prog.done}/${prog.total}`, meter(prog.done, prog.total)) : null,
      h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-tb-edit" }, title: "编辑条目、复制或删除目标",
        onclick: () => api.go({ sub: "goals", addTarget: false }) }, "编辑")),
    view.addTarget ? targetPicker(ws, api, { onDone: () => { view.addTarget = false; } }) : null);
}

// ------------------------------------------------------------------------------------------------- book picker
/**
 * Inline book picker (2026-10-24, user: dropdowns are slow and ugly): the chosen book, or a placeholder, as a button;
 * opened = a filter field and the best candidates as rows, one tap picks. Typing refilters in place (focus stays).
 * candidates = [{ book, note }] best first.
 */
function bookPicker(api, { key, placeholder, current = null, candidates, onPick, onClear = null, clearLabel = "清除", limit = 8 }) {
  const view = api.view;
  const open = view.picker === key;
  const head = h("button", { class: `kh-bpick-head${current ? "" : " is-empty"}`, type: "button", "aria-expanded": String(open),
    dataset: { focusKey: `kh-bp-${key}` }, onclick: () => api.go({ picker: open ? null : key }) },
  current ? idolBar(current) : null,
  h("span", { class: "kh-bpick-name", lang: "ja", text: current ? bookName(current) : placeholder }),
  icon("chevron", { size: 16, cls: "kh-crow-chev" }));
  if (!open) return h("div", { class: "kh-bpick" }, head);
  const list = h("ul", { class: "kh-bpick-list" });
  const pick = (book) => { view.picker = null; onPick(book); };
  // same-idol books without a card name read alike: their 評価値 tells them apart
  const seen = new Map();
  for (const { book } of candidates) seen.set(bookName(book), (seen.get(bookName(book)) || 0) + 1);
  const label = (book) => (seen.get(bookName(book)) > 1 && book.eval_value ? `${bookName(book)} · 評価値 ${book.eval_value}` : bookName(book));
  const fill = (query) => {
    clear(list);
    const q = String(query || "").normalize("NFKC").trim().toLowerCase();
    const rows = q ? candidates.filter(({ book }) => [book.idol, book.title, ...book.rows.map((r) => r.name)].join(" ").normalize("NFKC").toLowerCase().includes(q))
      : candidates;
    for (const { book, note } of rows.slice(0, limit)) {
      list.append(h("li", {}, h("button", { class: "kh-bpick-row", type: "button", dataset: { focusKey: `kh-bp-${key}-${book.fingerprint}` }, onclick: () => pick(book) },
        idolBar(book), h("span", { class: "kh-bpick-name", lang: "ja", text: label(book) }), note ? h("span", { class: "kh-bpick-note num", text: note }) : null)));
    }
    if (rows.length > limit) list.append(h("li", { class: "muted small kh-bpick-more", text: `+${rows.length - limit}` }));
    if (!rows.length) list.append(h("li", { class: "muted small kh-bpick-more", text: "没有符合的书" }));
  };
  fill("");
  return h("div", { class: "kh-bpick is-open" }, head,
    candidates.length > limit ? h("input", { class: "input kh-bpick-q", type: "search", placeholder: "偶像、卡名或条目", "aria-label": "筛选书",
      dataset: { focusKey: `kh-bp-${key}-q` }, oninput: (e) => fill(e.target.value) }) : null,
    list,
    onClear && current ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-bp-${key}-clear` },
      onclick: () => { view.picker = null; onClear(); } }, clearLabel) : null);
}

// ------------------------------------------------------------------------------------------------- 带书 STEP
export function stepWorkflow(ws, api) {
  const { target, books } = ws;
  if (!target) return null;   // the target bar above offers the recommended targets
  const route = planRoute(books, target, { mainBook: ws.mainBook, team: ws.team, rules: ws.rules });
  const action = nextAction({ target, stage: ws.stage, lastRun: ws.runs.last, failures: ws.runs.failures });
  // 2026-10-24 (user: 整条浓缩路线): the route says what to run now and after; the box stays only for what it cannot say
  // (too many failures, separate the main book). A pending result is decided first, in its own card (resultSection).
  const box = ["rethink", "target"].includes(action.key) || ws.stage?.stage === "separate" ? nextActionBox(action, ws, api) : null;
  return h("div", { class: "kh-flow" },
    box,
    resultSection(ws, api),
    routeSection(ws, route, api),
    manualSection(ws, api),
    historySection(ws, api));
}

function nextActionBox(action, ws, api) {
  const jump = { target: ["去设置", () => api.go({ sub: "goals" })], rethink: ["调整目标", () => api.go({ sub: "goals" })] }[action.key];
  return h("div", { class: `kh-next is-${action.key}`, role: "status", title: ws.stage?.reason || null },
    h("span", { class: "kh-next-label", text: "提示" }),
    h("span", { class: "kh-next-text" }, jaText(action.text)),
    jump ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-next-jump" }, onclick: jump[1] }, jump[0]) : null);
}

/** Expandable one-line row used by the 带书 page (route books, Halo picks). */
export function compactRow({ key, open, onToggle, book, lead = null, leadMuted = false, chips = [], limit = 2, tags = [], right = null, sub = null, body = null }) {
  return h("div", { class: `kh-crow${open ? " is-open" : ""}` },
    h("button", { class: "kh-crow-head", type: "button", "aria-expanded": String(Boolean(open)), dataset: { focusKey: key }, onclick: onToggle },
      lead ? h("span", { class: `kh-crow-lead${leadMuted ? " is-muted" : ""}`, text: lead }) : null,
      idolBar(book),
      h("span", { class: "kh-row-idol", lang: "ja", text: bookLabel(book), title: book.title || "" }),
      h("span", { class: "kh-row-chips" }, chips.slice(0, tags.length ? 1 : limit)),
      chips.length > (tags.length ? 1 : limit) ? h("span", { class: "kh-row-more", text: `+${chips.length - (tags.length ? 1 : limit)}` }) : null,
      tags.length ? h("span", { class: "kh-row-tags" }, tags) : null,
      right !== null ? h("span", { class: "kh-crow-right num" }, right, sub !== null ? h("span", { class: "kh-crow-sub", title: "評価値", text: sub }) : null) : null,
      icon("chevron", { size: 16, cls: "kh-crow-chev" })),
    open && body ? h("div", { class: "kh-crow-body" }, body) : null);
}

function toggle(api, key) {
  return () => { api.view.open[key] = !api.view.open[key]; api.render(); };
}

/** 主书 line at the top of the route: optional (the route plans without one); the picker lists the best-progress books. */
function mainLine(ws, api) {
  const { target, mainBook, mainFp, books, plan, rules } = ws;
  const ranked = books.filter((b) => usableFor(b, ws.team) !== "blocked" || b === mainBook)
    .map((b) => ({ b, ev: evaluateBook(b, target, rules) }))
    .filter((x) => x.ev.progress.done > 0 || x.b === mainBook)
    .sort((a, b) => b.ev.progress.done - a.ev.progress.done || a.ev.excluded.length - b.ev.excluded.length || a.ev.noise.length - b.ev.noise.length)
    .slice(0, 40);
  return h("div", { class: "kh-main-line", title: "主书：正在培养的那本，路线的最后一次带入它。不指定时自动安排。" },
    h("span", { class: "kh-inline-label", text: "主书" }),
    mainFp && !mainBook ? h("span", { class: "kh-flag small", text: "原主书已不在书库" }) : null,
    bookPicker(api, { key: "main", placeholder: "不指定", current: mainBook,
      candidates: ranked.map(({ b, ev }) => ({ book: b, note: `${ev.progress.done}/${ev.progress.total}` })),
      onPick: (b) => api.save(setMain(plan, target.id, b.fingerprint)), onClear: () => api.save(setMain(plan, target.id, null)), clearLabel: "不指定主书" }));
}

// ---- 3-book rows and summary (route step, manual pick)
function trioRows(res, ws, api, prefix, { fillers = [], lonely = new Set() } = {}) {
  return res.books.map((p, i) => {
    const key = `${prefix}-${i}`;
    const isMain = p.book === ws.mainBook;
    const filler = Boolean(fillers[i]);
    return compactRow({ key: `kh-${key}`, open: api.view.open[key], onToggle: toggle(api, key), book: p.book, lead: filler ? "补位" : String(i + 1), leadMuted: filler,
      chips: p.gives.length ? p.gives.map((g) => miniChip(g.label, g.level, { cls: `is-hit${g.transferable ? "" : " is-nt"}` }))
        : [h("span", { class: "muted small", text: "无目标条目" })],
      // 「?」 only when the team is known but the book's idol is not: an unverified team is said once on the 编队 line
      tags: [isMain ? microTag("主书", "当前主书", "is-main") : null, p.usable === "unknown" && ws.team?.verified ? microTag("未读", "名义未读，带入前确认", "is-unsure") : null,
        p.usable === "blocked" ? microTag("不可带", "与编队偶像同名", "is-bad") : null, p.excluded.length ? microTag("排除", `含排除条目：${p.excluded.join("、")}`, "is-bad") : null,
        p.book.protected !== true && lonely.has(p.book.fingerprint) ? microTag("唯一", "唯一持有某个目标条目且未保護，建议在游戏中保護", "is-bad") : null].filter(Boolean),
      right: null,
      body: h("div", { class: "small" },
        h("div", { class: "muted", lang: "ja", text: `${p.book.title || ""} · ${USABLE_TEXT[p.usable]}` }),
        p.gives.length ? h("div", { class: "kh-chips" }, p.gives.map((g) => chip(g.label, g.level, { cls: `is-hit${g.transferable ? "" : " is-nt"}`,
          title: g.transferable ? ROLE_LABEL[g.role] : `${ROLE_LABEL[g.role]}；不可转记` }))) : null,
        p.fresh.length ? h("div", { class: "kh-ex is-good" }, jaText(`主书没有：${p.fresh.join("、")}`)) : null,
        p.excluded.length ? h("div", { class: "kh-ex is-bad" }, jaText(`排除条目：${p.excluded.join("、")}`)) : null,
        p.noise.length ? h("div", { class: "kh-ex is-warn" }, jaText(`非目标 ${p.noise.length} 条：${p.noise.slice(0, 5).join("、")}${p.noise.length > 5 ? "…" : ""}`)) : null) });
  });
}

const USABLE_TEXT = { ok: "可带入", blocked: "与编队偶像同名，不可带入", unknown: "待确认能否带入" };

/**
 * One summary line + errors always visible; the full reason list folds into 「详情」. The line keeps only what asks for
 * action (缺 / 排除 / 可能丢失 / 非目标 占格); `reach` adds 「完成 x/y」 where no step head already says it (自选 3 本).
 */
function trioSummary(res, extra = null, { reach = true } = {}) {
  const facts = [
    res.gaps.length ? ["is-bad", `缺 ${res.gaps.length}`, res.gaps.map((u) => u.label)] : null,
    res.excluded.length ? ["is-bad", `排除 ${res.excluded.length}`, res.excluded] : null,
    res.loss.length ? ["is-warn", `可能丢失 ${res.loss.length}`, res.loss] : null,
    res.noise.length ? ["is-warn", `非目标 ${res.noise.length} 条`, res.noise] : null,
  ].filter(Boolean);
  const errors = res.conflicts.filter((c) => c.level === "error");
  const rows = [
    res.fresh.length ? ["is-good", `新增：${res.fresh.join("、")}`] : null,
    res.redundant.length ? ["is-good", `多本带入（继承机会更高）：${res.redundant.join("、")}`] : null,
    res.loss.length ? ["is-warn", `可能丢失（只有一本带）：${res.loss.join("、")}`] : null,
    res.gaps.length ? ["is-bad", `缺：${res.gaps.map((u) => u.label).join("、")}`] : null,
    res.excluded.length ? ["is-bad", `带入排除条目：${res.excluded.join("、")}`] : null,
    res.noise.length ? ["is-warn", `非目标条目 ${res.noise.length} 条，可能占格`] : null,
  ].filter(Boolean);
  return h("div", { class: "kh-trio-summary" },
    reach || facts.length ? h("div", { class: "kh-trio-line small" },
      reach ? h("strong", { class: "num", title: "按带入条目全部转记计算；继承随机，不是概率", text: `完成 ${res.reach.done}/${res.reach.total}` }) : null,
      ...facts.map(([cls, text, list]) => h("span", { class: `kh-ex ${cls}`, title: list.join("、") }, text))) : null,
    errors.length ? conflictList(errors) : null,
    details("详情", h("ul", { class: "kh-explain-list small" }, rows.map(([cls, text]) => h("li", { class: cls }, jaText(text)))),
      conflictList(res.conflicts.filter((c) => c.level !== "error")), extra));
}

function conflictList(conflicts) {
  if (!conflicts.length) return null;
  return h("ul", { class: "kh-conflicts small" }, conflicts.map((c) => h("li", { class: `is-${c.level}` }, jaText(c.text))));
}

// ---- route
const LETTERS = "ABCDEFGHIJKL";
const SOURCE_SHORT = { "普通条目": "普通", "任意剧本（头）": "任意剧本" };

/**
 * S.T.E.P. 路线 (2026-10-24, user: 整条浓缩路线 / 路线要直观): numbered steps on a line, each saying what it makes
 * (中间书 A / 成品) from which 3 books; the next step is open with its books, 「登记结果」 and the search recipe.
 * Re-planned on every render, so a registered result shortens it. Advice only; no success rate.
 */
function routeSection(ws, route, api) {
  const view = api.view;
  const inv = analyzeInventory(ws.books, ws.target, ws.team);
  const byKey = new Map(inv.map((a) => [a.unit.key, a]));
  const head = h("div", { class: "kh-sec-head", title: "从现有书库到成品的 S.T.E.P. 次数，登记结果后重新安排。继承随机：次数是估计，不是成功率。" },
    h("span", { class: "kh-sec-title", text: "S.T.E.P. 路线" }),
    route.runs.length ? h("span", { class: "muted small kh-sec-right", text: `共 ${route.runs.length} 次` }) : null);
  // 需采集, grouped by where the entry comes from; a chip with a Lv = owned, but below the Target's minimum
  const groups = new Map();
  for (const unit of route.absent) {
    const a = byKey.get(unit.key);
    const src = SOURCE_SHORT[a?.source] || a?.source || "普通";
    groups.set(src, [...(groups.get(src) || []), { unit, best: a?.best ?? null }]);
  }
  const collect = route.absent.length ? h("div", { class: "kh-gaps kh-route-collect", title: "书库中没有达到最低 Lv 的可带入书，需在括号中的剧本取得" },
    h("span", { class: "kh-gaps-label is-strong", text: "需采集" }),
    ...[...groups].map(([src, list]) => h("span", { class: "kh-route-src" },
      ...list.map(({ unit, best }) => miniChip(unit.label, best, { cls: "is-gap" })),
      src === "普通" ? null : h("span", { class: "muted small", text: `（${src}）` })))) : null;
  const direct = route.direct.length ? h("p", { class: "muted small" }, jaText(`不可转记，直接带入原书：${route.direct.map((u) => u.label).join("、")}`)) : null;
  const top = [head, mainLine(ws, api), collect, direct];
  if (route.done) {
    return h("section", { class: "kh-block kh-route" }, ...top,
      h("div", { class: "kh-route-done" }, pill("已达成", "done"), h("span", { class: "small" }, jaText(`「${bookName(route.book)}」已满足目标。`)),
        route.book === ws.mainBook ? null : h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-route-main" },
          onclick: () => api.save(setMain(ws.plan, ws.target.id, route.book.fingerprint)) }, "设为主书")));
  }
  if (!route.runs.length) {
    if (route.base === ws.mainBook) return h("section", { class: "kh-block kh-route" }, ...top);
    if (route.base) {
      const p = evaluateBook(route.base, ws.target, ws.rules).progress;
      return h("section", { class: "kh-block kh-route" }, ...top,
        h("div", { class: "kh-route-done", title: "书库中的目标条目都已在这本书中；采集上面缺少的条目后再安排 S.T.E.P." },
          pill(`当前最佳 ${p.done}/${p.total}`, "info"), h("span", { class: "small", lang: "ja", text: bookName(route.base) }),
          route.base === ws.mainBook ? null : h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-route-main" },
            onclick: () => api.save(setMain(ws.plan, ws.target.id, route.base.fingerprint)) }, "设为主书")));
    }
    return h("section", { class: "kh-block kh-route" }, ...top,
      h("p", { class: "muted small", text: route.units.length ? "书库中没有命中目标的可带入书。" : "目标没有必需或优先条目。" }));
  }
  const letter = new Map(route.runs.filter((r) => !r.final).map((r, i) => [r.index, LETTERS[i] || String(i + 1)]));
  const lonely = new Set(inv.filter((a) => a.protectWarn).map((a) => a.unique.fingerprint));
  const nextIndex = route.runs.find((r) => r.now)?.index;
  const inputName = (input) => (input.run ? `中间书 ${letter.get(input.run)}` : input.book === ws.mainBook ? "主书" : shortName(bookLabel(input.book), 6));
  const steps = route.runs.map((run) => {
    const key = `route-${run.index}`;
    const isNext = run.index === nextIndex;
    const open = key in view.open ? Boolean(view.open[key]) : isNext;
    const title = run.final ? "成品" : `中间书 ${letter.get(run.index)}`;
    const tag = isNext ? pill("下一步", "info") : run.now ? pill("可同时进行", "neutral") : null;
    let body = null;
    if (open && run.now) {
      const trio = run.inputs.map((i) => i.book);
      const res = analyzeTrio(trio, ws.target, { mainBook: ws.mainBook, team: ws.team, mode: view.mode, plan: ws.plan, books: ws.books, rules: ws.rules });
      const single = run.risks.find((r) => r.code === "single");
      const recording = view.recordOpen && view.recordAt === key;
      // the final step is judged against the Target; an intermediate step only says what its book collects
      body = h("div", { class: "kh-step-body" },
        ...trioRows(res, ws, api, key, { fillers: run.inputs.map((i) => i.filler), lonely }),
        run.final ? trioSummary(res, single ? h("p", { class: "muted small" }, jaText(single.text)) : null, { reach: false }) : stepGives(run),
        recording ? recordForm(ws, api, { gives: run.gives }) : h("div", { class: "kh-row-actions" },
          api.copyTrio ? api.copyTrio(trio) : null,
          api.autoTrio ? api.autoTrio(trio) : null,
          h("button", { class: "btn btn-small btn-primary", type: "button", dataset: { focusKey: `kh-${key}-record` },
            title: "在游戏中带入这 3 本完成 S.T.E.P. 后，登记得到的书",
            onclick: () => api.go({ recordInputs: trio.map((b) => b.fingerprint), recordOpen: true, recordAt: key, draft: null }) }, "完成后登记")));
    } else if (open) {
      body = h("div", { class: "kh-step-body" }, stepGives(run));
    }
    return h("li", { class: `kh-step${isNext ? " is-next" : ""}${run.final ? " is-final" : ""}${open ? " is-open" : ""}` },
      h("span", { class: "kh-step-mark num", "aria-hidden": "true", text: String(run.index) }),
      h("div", { class: "kh-step-main" },
        h("button", { class: "kh-step-head", type: "button", "aria-expanded": String(open), dataset: { focusKey: `kh-${key}` },
          onclick: () => { view.open[key] = !open; api.render(); } },
        h("span", { class: "kh-step-title", text: title }), tag,
        h("span", { class: "kh-step-gives num", title: run.gives.map((u) => u.label).join("、"),
          text: run.final ? `${run.gives.length}/${route.units.length} 项` : `${run.gives.length} 项` }),
        icon("chevron", { size: 16, cls: "kh-crow-chev" })),
        open && run.now ? null : h("div", { class: "kh-step-inputs small", lang: "ja" }, run.inputs.map(inputName).join(" + ")),
        body));
  });
  return h("section", { class: "kh-block kh-route" }, ...top, h("ol", { class: "kh-steps" }, steps));
}

function stepGives(run) {
  return h("div", { class: "small" },
    h("div", { class: "kh-chips" }, h("span", { class: "muted", text: run.final ? "成品带有" : "带有" }),
      ...run.gives.map((u) => miniChip(u.label, null, { cls: "is-hit" }))),
    conflictList(run.risks));
}

// ---- manual selection
function manualSection(ws, api) {
  const view = api.view;
  const { books, target, team } = ws;
  const picked = [0, 1, 2].map((i) => view.picked?.[i] || "");
  const candidates = books.filter((b) => usableFor(b, team) !== "blocked")
    .map((b) => ({ b, n: evaluateBook(b, target, ws.rules).status.filter((s) => s.hit?.meets && s.unit.role !== "excluded").length }))
    .sort((a, b) => Number(b.b === ws.mainBook) - Number(a.b === ws.mainBook) || b.n - a.n).slice(0, 120)
    .map(({ b, n }) => ({ book: b, note: n ? `命中 ${n}` : "" }));
  const set = (i, fp) => { const next = [...picked]; next[i] = fp; api.go({ picked: next }); };
  const trio = picked.map((fp) => ws.byFp.get(fp)).filter(Boolean);
  const res = trio.length ? analyzeTrio(trio, target, { mainBook: ws.mainBook, team, mode: view.mode, plan: ws.plan, books, rules: ws.rules }) : null;
  const recording = view.recordOpen && view.recordAt === "manual";
  return foldSection({ key: "kh-manual", title: "自选 3 本", count: trio.length ? `${trio.length}/3` : null, defaultOpen: false,
    summary: res ? h("span", { class: `small ${res.valid ? "muted" : "tone-warning"}`, text: res.valid ? `完成 ${res.reach.done}/${res.reach.total}` : "未选满或有冲突" }) : null,
    children: h("div", { class: "kh-manual" },
      h("div", { class: "kh-slots" }, picked.map((fp, i) => bookPicker(api, { key: `slot-${i}`, placeholder: `第 ${i + 1} 本`, current: ws.byFp.get(fp) || null,
        candidates, onPick: (b) => set(i, b.fingerprint), onClear: () => set(i, ""), clearLabel: "移出" }))),
      res ? trioSummary(res) : null,
      recording ? recordForm(ws, api) : trio.length ? h("div", { class: "kh-row-actions" },
        res.valid ? h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-manual-record" },
          onclick: () => api.go({ recordOpen: true, recordAt: "manual", draft: null }) }, "完成后登记") : null,
        h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-pick-clear" }, onclick: () => api.go({ picked: null }) }, "清空")) : null) });
}

// ---- record & compare results
/** The registered result awaiting a decision (compared with the main book), else the last run in one line. */
function resultSection(ws, api) {
  const last = ws.runs.last;
  if (!last) return null;
  if (!last.decision) return h("section", { class: "kh-block kh-result" }, compareRun(last, ws, api));
  return h("section", { class: "kh-block kh-result" }, h("div", { class: "kh-row-actions kh-result-line" },
    h("span", { class: "kh-inline-label", text: "上次结果" }), lastRunLine(last, ws, api)));
}

function lastRunLine(run, ws, api) {
  const out = run.outputs.map((fp) => ws.byFp.get(fp)).filter(Boolean);
  return h("span", { class: "kh-last-run small" },
    h("span", { class: "muted", text: `${(run.at || "").slice(5, 10)} ` }),
    h("span", { class: "muted", text: `${DECISION_LABEL[run.decision] || "待处理"}` }),
    out.length ? h("span", { class: "muted" }, jaText(` · ${out.map(bookLabel).join("、")}`)) : null,
    h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-revert-${run.id}` },
      onclick: () => api.save(revertRun(ws.plan, run.id)) }, "撤销"));
}

function compareRun(run, ws, api) {
  const before = run.mainBefore ? ws.byFp.get(run.mainBefore) || null : null;
  const outputs = run.outputs.map((fp) => ws.byFp.get(fp)).filter(Boolean);
  if (!outputs.length) {
    return h("div", { class: "kh-compare-box" }, h("p", { class: "muted small", text: "这次没有登记结果书。" }),
      decisionButtons(run, null, "failed", ws, api));
  }
  return h("div", { class: "kh-compare-box" }, outputs.map((book) => {
    const c = compareResult(book, before, ws.target, { otherTargets: ws.plan.targets, rules: ws.rules,
      intermediate: Boolean(before) && run.inputs.length > 0 && !run.inputs.includes(before.fingerprint) });
    const delta = (list, sign) => list.map((x) => chip(x.name, x.level, { cls: sign === "+" ? "is-hit" : "is-gap" }));
    return h("article", { class: "kh-card kh-result-card" },
      h("div", { class: "kh-card-head" }, idolBar(book), h("div", { class: "kh-card-title" },
        h("div", {}, h("span", { class: "kh-idol", lang: "ja", text: bookLabel(book) }), " ", pill({ main: "建议设为主书", backup: "建议留作备用", stop: "建议不采用" }[c.decision], c.decision === "main" ? "done" : c.decision === "stop" ? "conflict" : "info")),
        h("div", { class: "muted small" }, jaText(c.reason))),
      h("div", { class: "kh-card-ev num" }, h("span", { class: "muted small", text: "进度" }), h("span", { text: `${c.progress.before}→${c.progress.after}` }))),
      c.lostRequired.length ? h("p", { class: "kh-flag small" }, jaText(`失去必需：${c.lostRequired.join("、")}`)) : null,
      c.gainedUnits.length ? h("div", { class: "small" }, h("span", { class: "muted", text: "改善：" }), jaText(c.gainedUnits.join("、"))) : null,
      c.added.length ? h("div", { class: "kh-chips" }, h("span", { class: "muted small", text: "新增" }), ...delta(c.added, "+")) : null,
      c.removed.length ? h("div", { class: "kh-chips" }, h("span", { class: "muted small", text: "减少" }), ...delta(c.removed, "-")) : null,
      c.levelUp.length || c.levelDown.length ? h("div", { class: "small" }, jaText([...c.levelUp.map((x) => `${x.name} Lv${x.from}→${x.to}↑`),
        ...c.levelDown.map((x) => `${x.name} Lv${x.from}→${x.to}↓`)].join("、"))) : null,
      c.excludedGained.length ? h("p", { class: "kh-flag small" }, jaText(`新增排除条目：${c.excludedGained.join("、")}`)) : null,
      c.others.length ? h("div", { class: "muted small" }, jaText(`其他目标：${c.others.map((o) => `${o.target.name} ${o.done}/${o.total}`).join("、")}`)) : null,
      decisionButtons(run, book, c.decision, ws, api));
  }));
}

function decisionButtons(run, book, suggested, ws, api) {
  const keys = book ? ["main", "backup", "stop"] : ["failed"];
  return h("div", { class: "kh-row-actions" }, keys.map((k) => h("button", { class: `btn btn-small${k === suggested ? " btn-primary" : ""}`, type: "button",
    dataset: { focusKey: `kh-decide-${run.id}-${k}` }, onclick: () => api.save(decideRun(ws.plan, run.id, k, book?.fingerprint || null)) }, DECISION_LABEL[k])),
  h("span", { class: "muted small", text: "可撤销" }));
}

/**
 * 登记结果 under the step (or 自选 3 本) it belongs to. 选择书: the only new book is preselected; new books, then books
 * holding what the step brings, come first. 手动录入: a Lv pill row per entry the step brings (no typing), other entries
 * as text. 无可用书 = the run gave nothing usable (counts as a failure).
 */
function recordForm(ws, api, { gives = null } = {}) {
  const view = api.view;
  // a route step keeps its own 3 books; 自选 3 本 uses its slots
  const inputs = ((view.recordAt === "manual" ? view.picked : view.recordInputs) || []).filter(Boolean);
  const pending = (api.pending || []).filter((b) => !inputs.includes(b.fingerprint));
  const draft = view.draft || { mode: "library", pick: pending.length === 1 ? pending[0].fingerprint : "", idol: "", title: "", text: "", lv: {} };
  const setDraft = (patch) => { view.draft = { ...(view.draft || draft), ...patch }; };
  const units = gives?.length ? gives : targetUnits(ws.target).filter((u) => u.role !== "excluded");
  const holds = (b) => units.filter((u) => unitHit(u, b.rows)).length;
  const candidates = [...pending.map((b) => ({ book: b, note: "新获得" })),
    ...ws.books.filter((b) => !inputs.includes(b.fingerprint)).map((b) => ({ b, n: holds(b) }))
      .sort((a, b) => Number(b.b.source === "user") - Number(a.b.source === "user") || b.n - a.n)
      .map(({ b, n }) => ({ book: b, note: b.source === "user" ? "登记" : n ? `${n} 项` : "" }))];
  const all = [...ws.books, ...pending];
  const chosen = all.find((b) => b.fingerprint === draft.pick) || null;
  const parsed = parseRowsText(draft.text);
  const close = (patch = {}) => { view.draft = null; api.go({ recordOpen: false, recordAt: null, ...patch }); };
  const lvRows = units.map((u) => {
    const name = u.items[0].name;
    return h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", lang: "ja", title: u.label, text: shortName(itemLabel(u.items[0]), 8) }),
      choiceChips(`${u.label} Lv`, [["", "无"], ...[1, 2, 3, 4, 5, 6].map((n) => [n, String(n)])], draft.lv?.[name] ?? "",
        (v) => { setDraft({ lv: { ...(view.draft || draft).lv, [name]: v } }); api.render(); }, `kh-rec-lv-${u.key}`));
  });
  const lvAll = units.map((u) => draft.lv?.[u.items[0].name] ?? "");
  const allRow = units.length > 1 ? h("div", { class: "kh-form-row is-all" }, h("span", { class: "kh-form-label", text: "全部" }),
    choiceChips("全部 Lv", [["", "无"], ...[1, 2, 3, 4, 5, 6].map((n) => [n, String(n)])], lvAll.every((v) => v === lvAll[0]) ? lvAll[0] : null,
      (v) => { setDraft({ lv: Object.fromEntries(units.map((u) => [u.items[0].name, v])) }); api.render(); }, "kh-rec-lv-all")) : null;
  const manual = h("div", { class: "kh-manual-entry" },
    h("input", { class: "input", type: "text", placeholder: "偶像名（可空）", "aria-label": "偶像名", value: draft.idol, dataset: { focusKey: "kh-rec-idol" }, oninput: (e) => setDraft({ idol: e.target.value }) }),
    h("div", { class: "kh-form" }, allRow, lvRows),
    details("其他条目", h("textarea", { class: "input kh-rows-input", rows: 4, placeholder: "一行一条：名称 Lv\n例：Vocal上限+ Lv5", "aria-label": "其他ノウハウ（一行一条）",
      dataset: { focusKey: "kh-rec-rows" }, oninput: (e) => setDraft({ text: e.target.value }) }, draft.text),
    parsed.errors.length ? h("p", { class: "kh-flag small", text: parsed.errors.join("；") }) : null));
  const submit = () => {
    const d = view.draft || draft;
    // a 新获得 book is not in the library until the next scan: register its content (a user book) so it can be compared
    const fresh = pending.find((b) => b.fingerprint === d.pick && !ws.byFp.has(b.fingerprint));
    const rows = [...Object.entries(d.lv || {}).filter(([, lv]) => lv).map(([name, level]) => ({ name, level })), ...parseRowsText(d.text).rows];
    const results = d.mode === "library" ? (fresh ? [{ idol: fresh.idol, title: fresh.title, rows: fresh.rows, stats: fresh.stats, protected: fresh.protected }]
      : d.pick ? [d.pick] : [])
      : rows.length ? [{ idol: d.idol, title: d.title, rows }] : [];
    if (!results.length) { toast(d.mode === "library" ? "请选择得到的书" : "请至少选择一条 Lv", "neutral"); return; }
    view.draft = null;
    api.save(recordRun(ws.plan, { target: ws.target.id, inputs, results, mode: view.mode }), { patch: { recordOpen: false, recordAt: null } });
  };
  const failed = () => {
    const id = newId("r");
    view.draft = null;
    api.save(decideRun(recordRun(ws.plan, { target: ws.target.id, inputs, results: [], id }), id, "failed"), { patch: { recordOpen: false, recordAt: null } });
  };
  return h("div", { class: "kh-record" },
    segmented("结果来源", [["library", "选择书"], ["manual", "手动录入"]], draft.mode, (m) => { setDraft({ mode: m }); api.render(); }, "kh-rec-mode"),
    draft.mode === "library" ? bookPicker(api, { key: "result", placeholder: "选择得到的书", current: chosen, candidates,
      onPick: (b) => { setDraft({ pick: b.fingerprint }); api.render(); } }) : manual,
    h("div", { class: "kh-row-actions" },
      h("button", { class: "btn btn-small btn-primary", type: "button", dataset: { focusKey: "kh-rec-save" }, onclick: submit }, "登记"),
      h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-rec-fail" }, title: "这次 S.T.E.P. 没有得到可用的书，记为一次未成功", onclick: failed }, "无可用书"),
      h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-rec-cancel" }, onclick: () => close() }, "取消")));
}

function historySection(ws, api) {
  const { runs } = ws;
  const all = ws.plan.runs.filter((r) => r.target === ws.target.id).reverse();
  if (!all.length) return null;
  return foldSection({ key: "kh-history", title: "S.T.E.P. 记录", count: runs.runs.length, defaultOpen: false,
    summary: runs.failures ? h("span", { class: "small tone-warning", text: `连续 ${runs.failures} 次没进展` }) : null,
    children: () => h("ul", { class: "kh-history" }, all.map((run) => h("li", { class: run.reverted ? "is-reverted" : "" },
      h("div", { class: "small" }, h("span", { class: "num", text: (run.at || "").slice(5, 16).replace("T", " ") }), " ",
        h("strong", { text: run.reverted ? "已撤销" : DECISION_LABEL[run.decision] || "未决定" }), run.mode ? h("span", { class: "muted", text: ` · ${MODE_LABEL[run.mode] || ""}` }) : null),
      h("div", { class: "muted small" }, jaText(`带入 ${run.inputs.map((fp) => bookLabel(ws.byFp.get(fp))).join(" / ") || "未记录"} → ${run.outputs.map((fp) => bookLabel(ws.byFp.get(fp))).join("、") || "无"}`)),
      run.reverted ? null : h("div", { class: "kh-row-actions" },
        h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-hist-revert-${run.id}` }, onclick: () => api.save(revertRun(ws.plan, run.id)) }, "撤销"),
        ...run.outputs.filter((fp) => fp.startsWith("u-") && ws.byFp.get(fp)).map((fp) => h("button", { class: "btn btn-small btn-text", type: "button",
          dataset: { focusKey: `kh-hist-del-${fp}` }, onclick: () => api.save(removeUserBook(ws.plan, fp)) }, `删除登记的 ${bookLabel(ws.byFp.get(fp))}`)))))) });
}

// ------------------------------------------------------------------------------------------------- 目标
// One line per template (2026-10-26 user: 新建 was not obvious; a bare name did not say what the template holds).
const TEMPLATE_BLURB = {
  base: "思い出、施設効率相談 等", center: "通用 + Center適正、オールラウンダー", leader: "通用 + Leader適正、ロールモデル",
  "attr-vo": "通用 + Vocal適正、スペシャリスト、歌姫", "attr-da": "通用 + Dance適正、スペシャリスト、プロダンサー",
  "attr-vi": "通用 + Visual適正、スペシャリスト、トップモデル", support: "通用 + オールラウンダー、スタートダッシュ",
  mono: "通用 + 昂揚", step: "スタートダッシュ 等 4 条 · Lv3+",
};

/** The base plan for a pick: a preview Target (ws.preview) is not saved, the pick replaces it. */
function pickBase(ws) {
  return ws.preview ? { ...ws.plan, targets: ws.plan.targets.filter((t) => t.id !== ws.target.id) } : ws.plan;
}

/** Save a template as a Target (or switch to the one made from it) and make it current. */
export function adoptTemplate(ws, api, key, onDone = null) {
  const have = ws.preview ? null : ws.plan.targets.find((t) => t.source === `template:${key}`) || null;
  onDone?.();
  if (have) { api.save(setActive(ws.plan, have.id), { mirror: true }); return; }
  if (ws.plan.targets.length >= 12) { toast("最多 12 个目标", "neutral"); return; }
  const t = targetFromTemplate(key);
  if (t) api.save(setActive(upsertTarget(pickBase(ws), t), t.id), { mirror: true });
}

/** Recommended targets as one-tap cards (name + what it holds; creates the Target and makes it active); 「自定义」 = empty Target. */
export function targetPicker(ws, api, { custom = false, current = null, onDone = null } = {}) {
  const { plan } = ws;
  const full = plan.targets.length >= 12;
  const existing = (key) => (ws.preview ? null : plan.targets.find((t) => t.source === `template:${key}`) || null);
  const card = (key, name, blurb, onclick, on = false) => h("button", { class: `kh-tpl${on ? " is-on" : ""}`, type: "button", "aria-pressed": String(on),
    dataset: { focusKey: `kh-pick-${key}` }, disabled: onclick ? null : "", onclick },
  h("span", { class: "kh-tpl-name", text: name }), h("span", { class: "kh-tpl-sub", text: blurb }));
  return h("div", { class: "kh-picker", role: "group", "aria-label": "推荐目标" },
    TARGET_TEMPLATES.map((t) => card(t.key, existing(t.key) ? `${t.name} ✓` : t.name, TEMPLATE_BLURB[t.key] || t.items.slice(0, 3).map(itemLabel).join("、"),
      full && !existing(t.key) ? null : () => adoptTemplate(ws, api, t.key, onDone), current === `template:${t.key}` || Boolean(existing(t.key)))),
    custom ? card("custom", "自定义", "空目标，自己加条目", full ? null : () => {
      onDone?.();
      const t = { id: newId(), name: "新目标", items: [] };
      api.save(setActive(upsertTarget(pickBase(ws), t), t.id), { mirror: true });
    }) : null);
}

export function targetsSection(ws, api) {
  const { plan, target } = ws;
  const view = api.view;
  const add = (t) => api.save(setActive(upsertTarget(plan, t), t.id), { mirror: true });
  const rows = plan.targets.map((t) => {
    // the same n/total (units the strategy needs) as 带书 and the 书库 groups
    const best = ws.books.map((b) => evaluateBook(b, t, ws.rules).progress).sort((a, b) => b.done - a.done)[0];
    const main = plan.main[t.id] ? ws.byFp.get(plan.main[t.id]) : null;
    const mp = main ? evaluateBook(main, t, ws.rules).progress : null;
    const meta = `${SHORT_PURPOSE[t.scenario]} · ${main ? `主书 ${bookLabel(main)} ${mp.done}/${mp.total}` : best?.done ? `最好的一本 ${best.done}/${best.total}` : "书库无命中"}`;
    return h("li", { class: `kh-target-row${t.id === plan.active ? " is-active" : ""}` },
      h("button", { class: "kh-target-pick", type: "button", "aria-pressed": String(t.id === plan.active), dataset: { focusKey: `kh-t-${t.id}` },
        onclick: () => api.save(setActive(plan, t.id), { mirror: true }) },
      h("span", { class: "kh-target-name", text: t.name }),
      h("span", { class: "kh-target-meta muted small" }, jaText(meta))),
      h("button", { class: "btn btn-small btn-text", type: "button", title: "复制", dataset: { focusKey: `kh-t-copy-${t.id}` },
        onclick: () => add({ ...t, id: newId(), name: `${t.name} 副本`.slice(0, 30) }) }, "复制"),
      view.confirmDelete === t.id
        ? h("button", { class: "btn btn-small btn-danger", type: "button", dataset: { focusKey: `kh-t-del2-${t.id}` }, onclick: () => api.save(removeTarget(plan, t.id), { mirror: true, patch: { confirmDelete: null } }) }, "确认")
        : h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-t-del-${t.id}` }, onclick: () => api.go({ confirmDelete: t.id }) }, "删除"));
  });
  return foldSection({ key: "kh-targets", title: "目标", count: plan.targets.length, defaultOpen: true,
    summary: null,
    children: h("div", {},
      rows.length ? h("ul", { class: "kh-targets" }, rows) : emptyState({ title: "还没有目标" }),
      // 2026-10-26 user: the list read too long with every template always open; they open behind 「＋ 新建目标」
      rows.length && !view.addTarget
        ? h("button", { class: "btn btn-small kh-new-open", type: "button", dataset: { focusKey: "kh-new-open" }, onclick: () => api.go({ addTarget: true }) }, "＋ 新建目标")
        : h("div", { class: "kh-new-target" },
          h("div", { class: "kh-new-title" }, h("span", { text: "＋ 新建目标" }),
            rows.length ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-new-close" }, onclick: () => api.go({ addTarget: false }) }, "收起") : null),
          targetPicker(ws, api, { custom: true, onDone: () => { view.addTarget = false; } }))) });
}

const STRATEGY_TIP = { quick: "必需条目都达到最低 Lv 就算完成", balanced: "必需和优先条目都达到最低 Lv 才算完成",
  optimized: "必需和优先条目都达到理想 Lv 才算完成" };

function formRow(label, control) {
  return h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: label }), control);
}

/** Settings of the active Target: name, scenario, position, attribute, strategy + validation. */
export function targetSettings(ws, api) {
  const { target, plan } = ws;
  if (!target) return null;
  const set = (patch) => api.save(upsertTarget(plan, { ...target, ...patch }), { mirror: true, edited: target });
  const issues = validateTarget(target, ws.books, ws.rules);
  // 「书库中没有…」 is already on the items (缺 marks), not repeated here
  const shown = issues.filter((i) => i.level !== "info" && i.code !== "missing");
  // a 特化 position (Vocal / Dance / Visual) already says the attribute
  const attrShown = target.attribute && !["vocal", "dance", "visual"].includes(target.position);
  const where = [SHORT_PURPOSE[target.scenario], STRATEGY_LABEL[target.strategy],
    target.position ? POSITION_LABEL[target.position] : null, attrShown ? ATTR_LABEL[target.attribute] : null].filter(Boolean).join(" · ");
  return h("div", { class: "kh-target-settings" },
    foldSection({ key: "kh-target-settings", title: "设置", count: null, defaultOpen: false,
      summary: h("span", { class: "muted small", text: where }),
      children: h("div", {},
        // 2026-10-24: every option visible as a pill (no dropdowns)
        h("div", { class: "kh-form" },
          formRow("名称", h("input", { class: "input", type: "text", value: target.name, "aria-label": "目标名称", maxlength: 30, dataset: { focusKey: "kh-target-name" },
            onchange: (e) => set({ name: e.target.value }) })),
          formRow("用途", choiceChips("用途", ["any", "step", "halo"].map((k) => [k, PURPOSE_LABEL[k]]), target.scenario, (v) => set({ scenario: v }), "kh-target-scn")),
          formRow("位置", choiceChips("位置", [["", "不限"], ...POSITIONS.map((x) => [x, POSITION_LABEL[x]])], target.position, (v) => set({ position: v }), "kh-target-pos")),
          formRow("主属性", choiceChips("主属性", [["", "不限"], ...Object.entries(ATTR_LABEL)], target.attribute, (v) => set({ attribute: v }), "kh-target-attr")),
          formRow("完成条件", choiceChips("完成条件", STRATEGIES.map((x) => [x, STRATEGY_LABEL[x], STRATEGY_TIP[x]]), target.strategy, (v) => set({ strategy: v }), "kh-strat"))),
        issues.filter((i) => i.level === "info").length ? h("ul", { class: "kh-conflicts small" }, issues.filter((i) => i.level === "info").map((i) => h("li", { class: "is-info" }, jaText(i.text)))) : null) }),
    shown.length ? h("ul", { class: "kh-conflicts small" }, shown.map((i) => h("li", { class: `is-${i.level}` }, jaText(i.text)))) : null);
}

/** After an edit: what changed and what can be reused (smallest change). */
export function reuseSection(ws, api) {
  const base = api.view.editBase;
  if (!ws.target || !base || base.id !== ws.target.id) return null;
  const r = reusePlan(base, ws.target, ws.books, { mainBook: ws.mainBook, team: ws.team, plan: ws.plan, rules: ws.rules });
  // only when items came or went and there is a main book to reconsider (a Lv tweak, or no main book, needs no box:
  // the route re-plans by itself)
  if (!ws.mainBook || (!r.added.length && !r.removed.length)) return null;
  const lines = [r.added.length ? `新增 ${r.added.map((u) => u.label).join("、")}` : null, r.removed.length ? `去掉 ${r.removed.map((u) => u.label).join("、")}` : null,
    r.changed.length ? `调整 ${r.changed.map((c) => (c.from === c.to ? `${c.unit.label} Lv` : `${c.unit.label} ${ROLE_LABEL[c.from]}→${ROLE_LABEL[c.to]}`)).join("、")}` : null].filter(Boolean);
  return h("div", { class: "kh-block kh-reuse" }, subhead("改动后可复用", h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-reuse-close" },
    onclick: () => api.go({ editBase: null }) }, "收起")),
    h("p", { class: "small" }, jaText(`${lines.join("；")}。`)),
    h("p", { class: "small" }, jaText(r.keepMain ? "主书进度仍最高。"
      : r.suggestMain ? `进度最高：「${bookName(r.suggestMain)}」${r.bestProgress.done}/${r.bestProgress.total}，可换为主书。` : "书库中没有命中新目标的书。")),
    r.reuse.length ? h("ul", { class: "kh-explain-list small" }, r.reuse.map((x) => h("li", {}, jaText(`${x.unit.label}：${x.books.length ? x.books.map(bookLabel).join("、") : "书库中没有，需采集"}`)))) : null,
    r.intermediates.length ? h("p", { class: "muted small" }, jaText(`可复用的中间书：${r.intermediates.map(bookLabel).join("、")}`)) : null,
    !r.keepMain && r.suggestMain ? h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-reuse-main" },
      onclick: () => api.save(setMain(ws.plan, ws.target.id, r.suggestMain.fingerprint)) }, "换成这本") : null);
}

export function compareSection(ws, api) {
  const others = ws.plan.targets.filter((t) => t.id !== ws.target?.id);
  if (!ws.target || !others.length) return null;
  const view = api.view;
  const other = others.find((t) => t.id === view.cmpTarget) || others[0];
  const c = compareTargets(ws.target, other, ws.books);
  return foldSection({ key: "kh-target-compare", title: "比较目标", count: null, defaultOpen: false,
    summary: h("span", { class: "muted small", text: `共用 ${c.shared.length} 项` }),
    children: h("div", {},
      choiceChips("比较对象", others.map((t) => [t.id, t.name]), other.id, (v) => api.go({ cmpTarget: v }), "kh-cmp-target"),
      h("dl", { class: "kh-facts" },
        h("dt", { text: "共用" }), h("dd", {}, jaText(c.shared.join("、") || "无")),
        h("dt", { text: `只在「${ws.target.name}」` }), h("dd", {}, jaText(c.onlyA.join("、") || "无")),
        h("dt", { text: `只在「${other.name}」` }), h("dd", {}, jaText(c.onlyB.join("、") || "无")),
        h("dt", { text: "两边都用得上的书" }), h("dd", {}, jaText(c.both.length ? `${c.both.length} 本：${[...new Set(c.both.map(bookLabel))].slice(0, 6).join("、")}${c.both.length > 6 ? " 等" : ""}` : "无")))) });
}

// ------------------------------------------------------------------------------------------------- 书库 insights
/**
 * 书库 group of a book for the active Target (knowhow-views groupLibrary): 已达标 / 主书与中间书 / `p<n>` (n of the
 * strategy's units met; the label says 「n/total 项」) / 未命中.
 */
export function classOf(book, ws) {
  if (!ws.target) return "other";
  const cls = classifyBook(book, ws.target, { plan: ws.plan, rules: ws.rules });
  if (cls === "completed" || cls === "intermediate") return cls;
  const done = evaluateBook(book, ws.target, ws.rules).progress.done;
  return done ? `p${done}` : "other";
}

const GROUP_LABEL = Object.fromEntries(LIBRARY_GROUPS);

/** Missing know-how for the active Target with acquisition hints and protection warnings for unique holders. */
export function missingSection(ws, api) {
  if (!ws.target) return null;
  const inv = analyzeInventory(ws.books, ws.target, ws.team);
  const missing = inv.filter((a) => !a.meeting && a.unit.role !== "optional");
  const warn = inv.filter((a) => a.protectWarn);
  return foldSection({ key: "kh-missing", title: "缺项与保护提醒", count: missing.length + warn.length, defaultOpen: api.view.libFocus === "missing",
    tone: missing.length ? "warning" : null,
    summary: h("span", { class: "muted small", text: `缺 ${missing.length} · 唯一未保護 ${warn.length}` }),
    children: h("div", {},
      missing.length ? h("ul", { class: "kh-explain-list small" }, missing.map((a) => h("li", { class: "is-bad" },
        jaText(`${a.unit.label}（${ROLE_LABEL[a.unit.role]}）：${a.holders.length ? `最高 Lv${a.best}，未达 Lv${a.unit.items[0].min_level}` : "书库中没有"} · 来源 ${a.source}`))))
        : h("p", { class: "muted small", text: "目标条目书库中都有。" }),
      warn.length ? h("ul", { class: "kh-explain-list small" }, warn.map((a) => h("li", { class: "is-warn" },
        jaText(`「${a.unit.label}」仅「${bookName(a.unique)}」持有且未保護，建议在游戏中保護。`)))) : null) });
}

/** Extra lines in an opened 书库 book: classification, Target evaluation, conflicts, history and 设为主书. */
export function bookInsights(book, ws, api) {
  const parts = [];
  if (ws.target) {
    const ev = evaluateBook(book, ws.target, ws.rules);
    const cls = classOf(book, ws);
    parts.push(h("div", { class: "kh-chips" }, GROUP_LABEL[cls] && cls !== "other" ? pill(GROUP_LABEL[cls], cls === "completed" ? "done" : "info") : null,
      h("span", { class: "muted small", text: `对「${ws.target.name}」${ev.progress.done}/${ev.progress.total}` }),
      ...ev.status.filter((s) => s.hit || s.unit.role === "required").filter((s) => s.state !== "clear").slice(0, 8).map(unitChip)));
  }
  const conflicts = libraryConflicts(book, ws.books);
  const facts = [];
  if (conflicts.duplicateCopies) facts.push(`相同内容另有 ${conflicts.duplicateCopies} 本`);
  if (conflicts.sameNameLevels.length) facts.push(`同名不同 Lv：${conflicts.sameNameLevels.map((x) => `${x.name} ${x.levels.join("/")}`).join("、")}`);
  if (conflicts.lowerThan.length) facts.push(`${conflicts.lowerThan.length} 条在别的书里有更高 Lv${conflicts.redundant ? "（全部条目都有更高 Lv 的）" : ""}`);
  if (facts.length) parts.push(h("p", { class: "muted small" }, jaText(facts.join("；"))));
  const hist = bookHistory(ws.plan, book.fingerprint);
  if (hist.made.length || hist.used.length) {
    parts.push(h("p", { class: "muted small" }, jaText([hist.parents.length ? `由 ${hist.parents.map((fp) => bookLabel(ws.byFp.get(fp))).join(" / ")} 做出` : null,
      hist.used.length ? `用于 ${hist.used.length} 次 S.T.E.P.` : null].filter(Boolean).join("；"))));
  }
  if (ws.target) {
    const isMain = ws.mainFp === book.fingerprint;
    parts.push(h("div", { class: "kh-row-actions" },
      isMain ? pill("当前主书", "done") : h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: `kh-set-main-${book.fingerprint}` },
        onclick: () => api.save(setMain(ws.plan, ws.target.id, book.fingerprint)) }, "设为主书"),
      book.source === "user" ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-del-user-${book.fingerprint}` },
        onclick: () => api.save(removeUserBook(ws.plan, book.fingerprint)) }, "删除这本登记") : null));
  }
  return parts;
}

/** Target item helpers for the 目标条目 editor (panel). */
export function itemStatus(item, ws) {
  const unit = { key: item.name, label: item.name, role: item.role, items: [item] };
  const holders = ws.books.filter((b) => unitHit(unit, b.rows)?.meets);
  return { holders: holders.length, best: Math.max(0, ...ws.books.map((b) => unitHit(unit, b.rows)?.level || 0)) };
}


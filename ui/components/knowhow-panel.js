// ノウハウ tab: three sub-pages, display only — no run, no bridge request, no game input.
//  ① 带书 (default): reads the current Produce 编队 (P + S + Guest, or a manual idol pick) and shows the say "Halo" top
//     single books or a S.T.E.P. 3-book candidate combination, with gaps, 可带/本队不可带/需确认 and an in-game search recipe;
//  ② 书库: sheet-like list (search, sort by wanted hits / official 評価値 / idol), full book on click, new produce-end
//     books on top, and 整理复核 facts — never sell advice (the matcher yields no sell candidates);
//  ③ 目标: the wanted list (starts from a 社区常见 template, editable) and the wanted × idol coverage map.
// 「扫描ノウハウ」 (knowhow-scan.js) asks the host for the supervised KnowhowScan (calibrate first, then
// scan), gated like 「扫描持有卡」; K4 (marking books in the game) is shown as a disabled future option. Pure logic lives in knowhow-views.js / knowhow-data.js.
// The wanted list is saved in prefs.knowhow_wanted (UI profile) when the protocol declares it, else in localStorage.
// 2026-10-12 redesign (user: 「ノウハウ页用处不大」): the page answers, in order, ① what W.I.N.G. just gave me and which
// of those Maa 保護-ed, ② did a wanted knowhow show up (目标命中), ③ which book to bring. Without a library scan the
// default sub-page is ② 书库 (W.I.N.G. 新获得 on top) or ③ 目标; the data module is re-read after each finished run.
// 2026-10-08: summary tiles on top, long explanations folded, 新获得 / 整理复核 / 名单 /
// 覆盖图 as fold cards with counts; book cards keep hits + 可带 state up front and move gaps into 「查找方式」.
// 2026-10-13 製本 workflow (STEP → Halo): the active Target (knowhow-plan.js, localStorage, migrated from the wanted list)
// is the shared context of all three sub-pages; 带书 runs the loop Target → inventory → missing → recommended 3 books →
// record STEP result → compare → update main book → next action (knowhow-workflow.js on the rule engine knowhow-rules.js).
import { h, clear, jaText, icon, formatTime } from "./dom.js";
import { chip as pickChip, details, emptyState, toast, foldSection, factLine, subhead } from "./common.js";
import { loadLocal, saveLocal } from "./bridge.js";
import { anyCardArt } from "./game-assets.js";
import { S_SLOTS, expectedTeam, teamGroup, teamRoles } from "./team-rules.js";
import protocol from "../protocol.js";
import { knowhowScanGate, knowhowScanProgress, knowhowScanResultNote, openKnowhowScanConfirm, gateTitle } from "./knowhow-scan.js";
import {
  MATCH_LABEL, POSITIONS, POSITION_LABEL, buildCatalog, cleanName,
  cleanWanted, effectFor, effectIndex, knowhowSummary, loadKnowhowData, normalizeName, wantedHits, knowhowReloadKey,
} from "./knowhow-data.js";
import {
  GENERAL_IDOL, IDOL_UNITS, LIBRARY_STATUS, PER_PAGE, REVIEW_NOTE, autoStepBook, bookUsability, coverageMap, filterLibrary, gridLocation,
  libraryFacets, libraryRows, loadExtras, pendingNotes, reviewFacts, scanStatus,
  searchRecipe, shortName, teamFromIdols, teamFromSlots, collapseIdentical, defaultOpenGroup, groupLibrary,
} from "./knowhow-views.js";
import {
  HALO_DISCOUNT, ROLES, ROLE_LABEL, ROLE_SHORT, ROLE_TIP, analyzeInventory, evaluateBook, haloRank, newBookValue, parseEntry, strategyNeeds, targetUnits,
} from "./knowhow-rules.js";
import {
  PLAN_KEY, RECOMMENDED_ITEMS, activeTarget, entrySource, mergeBooks, migratePlan, newId, plansToWanted, targetFromTemplate, targetToWanted, upsertTarget,
} from "./knowhow-plan.js";
import { bookLabel, chip, choiceChips, idolBar, itemLabel, microTag, miniChip, pill, segmented, statText, usableTag } from "./knowhow-ui.js";
import {
  bookInsights, classOf, compactRow, compareSection, itemStatus, missingSection, reuseSection, stepWorkflow,
  targetBar, targetSettings, targetsSection, workflowState,
} from "./knowhow-workflow.js";

const STYLE_HREF = "styles/knowhow.css";
const LOCAL_KEY = "sc-ui-knowhow-wanted";
export const SCAN_STATUS = "需要先读取书库";
// 2026-10-13 (user: 书库管理要清晰直观): 书库 first and default. 2026-10-26 user: tabs in the order of use (书库 → 目标 →
// 带书) at the top; until a library and a saved Target exist they carry the step number or 「✓」 (no separate guide).
export const SUBPAGES = [["library", "书库"], ["goals", "目标"], ["bring", "带书"]];
const LIBRARY_PAGE = 30;  // first screen, then LIBRARY_MORE per tap
const LIBRARY_MORE = 60;
const ROW_CHIPS = 2;  // closed library row: target hits first, at most this many chips, then 「+N」
// 新获得 verdicts (knowhow-rules.js newBookValue + pendingNotes), most useful first. With a Target only its items count
// (2026-10-24 role-play: a higher WING準決勝 Lv made an unrelated book 「Lv 更高」); 非目标 = no Target item at all.
const VERDICT_RANK = { main: 0, fills: 1, higher: 2, same: 3, none: 4 };
const VERDICT_LABEL = { main: "优于主书", fills: "补足缺项", higher: "Lv 更高", same: "书库已有", none: "非目标" };
const VERDICT_TIP = { higher: "有目标条目的 Lv 高于书库中所有书", same: "目标条目都有其他书以相同或更高 Lv 持有", none: "没有目标条目" };
const sourceOf = (name) => entrySource(name, (n) => parseEntry(n).source);

// Page-local view state (the panel element is recreated on every store render).
const view = {
  sub: "library", scenario: "halo", position: "", manual: null, editTeam: false,
  query: "", sort: "wanted", openBook: null, shown: LIBRARY_PAGE,
  status: "all", idol: "", entry: "", minLevel: 1,
  catPurpose: "halo", catQuery: "", catGroup: "common", data: null, extras: null, loading: null, scroll: {}, preview: false,
  subTouched: false, runKey: undefined, fresh: false, advanced: false,
  // 製本 workflow (knowhow-workflow.js)
  plan: null, mode: "safe", picked: null, recordInputs: null, recordOpen: false, draft: null, confirmDelete: null, newTpl: "", cmpTarget: null,
  editBase: null, libFocus: null, cls: "", src: "", scenarioSet: false, defaultChosen: false,
  groupOpen: {}, shownBy: {}, moreFilters: false, effects: false, open: {},
};

export function knowhowPrefSupported(state) {
  if (state?.bridgeKind === "mock") return true;
  return Boolean(protocol?.definitions?.profile?.properties?.prefs?.properties?.knowhow_wanted);
}

export function wantedList(state) {
  const fromPrefs = knowhowPrefSupported(state) ? state?.prefs?.knowhow_wanted : null;
  return cleanWanted(Array.isArray(fromPrefs) ? fromPrefs : loadLocal(LOCAL_KEY));
}

/** True when nothing was ever saved (the page then starts from the 社区常见 template as an unsaved draft). */
export function wantedNeverSaved(state) {
  if (knowhowPrefSupported(state) && Array.isArray(state?.prefs?.knowhow_wanted)) return false;
  return !Array.isArray(loadLocal(LOCAL_KEY));
}

/** Persist the cleaned list; returns true when it went through the store (which re-renders). */
export function saveWanted(state, actions, list) {
  const clean = cleanWanted(list);
  if (knowhowPrefSupported(state) && typeof actions?.setPref === "function") {
    actions.setPref("knowhow_wanted", clean);
    return true;
  }
  saveLocal(LOCAL_KEY, clean);
  return false;
}

/** The 製本 plan (Targets, main books, STEP runs, registered books): localStorage, migrated from the wanted list once. */
export function loadPlan(wanted, key = PLAN_KEY) {
  return migratePlan(loadLocal(key), wanted);
}

/** The preview's synthetic library keeps its own plan so demo books never mix with the real one. */
function planKey(state) {
  return state?.bridgeKind === "mock" && !view.localData ? `${PLAN_KEY}-demo` : PLAN_KEY;
}

/**
 * The W.I.N.G. team for 带书: P from the profile; S and Guest from the last in-game read (team-snapshot) or registered
 * cards — the same source the W.I.N.G. task checks. Mode / unit / search options are not cards (2026-10-13 fix).
 */
export function profileTeam(state) {
  const found = teamGroup(state, "AutoProduce");
  if (!found) return { ...teamFromSlots([]), reason: "编队未设置" };
  const entry = state.profile?.tasks?.find((t) => t.name === "AutoProduce");
  const roles = teamRoles(state, found.keys);
  const p = (roles.P && (entry?.option?.[roles.P] ?? state.schema.options[roles.P]?.default_case)) || "";
  const known = expectedTeam(state, entry, roles);
  const ids = [["P", p], ...S_SLOTS.map((slot, i) => [slot, known.s[i] || ""]), ["guest", known.guest || ""]];
  const slots = ids.filter(([, id]) => id).map(([slot, id]) => {
    const art = anyCardArt(id);
    return { slot, label: slot === "guest" ? "Guest" : slot, id, idol: art ? normalizeName(art.idol || "") || null : null };
  });
  const team = teamFromSlots(slots);
  if (!p) return { ...team, verified: false, reason: "未选择 P 卡" };
  if (team.unresolved.length) return { ...team, verified: false, reason: `${team.unresolved.join("、")} 的偶像未识别` };
  if (slots.length === 1) return { ...team, verified: false, reason: "S 卡未读取（W.I.N.G. 开局时读取）" };
  return team;
}

function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall back below */ }
  try {
    const area = h("textarea", { class: "kh-copy-buffer", readonly: true, "aria-hidden": "true" });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

function copyButton(label, text, focusKey) {
  return h("button", { class: "btn btn-small", type: "button", dataset: { focusKey },
    onclick: async () => toast(await copyText(text) ? "已复制查找方式" : "无法写入剪贴板，请展开「查找方式」手动复制", "neutral") }, label);
}

class KnowhowPanel extends HTMLElement {
  set data(value) {
    this._data = value;
    ensureStyles();
    if (!view.preview && value.state.bridgeKind === "mock" && typeof location !== "undefined") {
      view.preview = true;  // preview-only deep links for screenshots: ?kh=library|goals&kh_scn=step&kh_q=…
      const params = new URLSearchParams(location.search);
      if (SUBPAGES.some(([k]) => k === params.get("kh"))) { view.sub = params.get("kh"); view.subTouched = true; }
      if (["step", "halo"].includes(params.get("kh_scn"))) { view.scenario = params.get("kh_scn"); view.scenarioSet = true; }
      if (params.get("kh_target")) view.previewTemplate = params.get("kh_target");  // preview: start from a template Target
      if (params.get("kh_q")) view.query = params.get("kh_q");
      if (params.get("kh_open") === "first") view.openBook = "first";
      view.localData = params.get("kh_data") === "local";  // preview with the local game-assets/knowhow.js
    }
    // A finished run (e.g. W.I.N.G. with new 獲得報酬 books) or ノウハウ scan may have rewritten knowhow.js: read it again.
    const runKey = knowhowReloadKey(value.state);
    if (view.data && !view.loading && view.runKey !== runKey) { view.data = null; view.fresh = true; }
    if (!view.data && !view.loading) {
      const demo = value.state.bridgeKind === "mock" && !view.localData;
      view.runKey = runKey;
      view.loading = Promise.all([loadKnowhowData({ demo, fresh: view.fresh }), loadExtras({ demo })]).then(([data, extras]) => {
        view.data = data;
        view.extras = extras;
        view.loading = null;
        // No library read yet: open where there is something to see (W.I.N.G. 新获得), else the intro with 读取书库.
        if (this.isConnected) this.render();
      });
    }
    this.render();
  }

  /**
   * Save the plan (localStorage). mirror: copy every Target to prefs.knowhow_wanted (knowhow_keep, Python report, old pages);
   * edited: the Target before this edit (kept as the base of 「改动后可复用」); patch: view changes.
   */
  savePlan(plan, { mirror = false, edited = null, patch = null } = {}) {
    const { state, actions } = this._data;
    const clean = migratePlan({ ...plan, version: 1 }, []);
    clean.migrated = plan.migrated === true;
    view.plan = clean;
    if (edited && (!view.editBase || view.editBase.id !== edited.id)) view.editBase = edited;
    if (patch) Object.assign(view, patch);
    const { migrated, ...stored } = clean;
    saveLocal(planKey(state), stored);
    if (mirror && !(mirror === "if-target" && !activeTarget(clean))) {
      if (saveWanted(state, actions, plansToWanted(clean))) return;
    }
    this.render();
  }

  api(ws, ctx) {
    return {
      view, pending: ctx.data.pending, go: (patch) => this.go(patch), render: () => this.render(),
      save: (plan, opts) => this.savePlan(plan, opts),
      copyTrio: (trio) => copyButton("复制查找方式", trio.map((b, i) => `【第 ${i + 1} 本】
${searchRecipe(b, this.locate(b, ctx), [])}`).join("\n\n"), "kh-copy-trio"),
      autoTrio: (trio) => this.autoTrioButton(trio, ctx),
    };
  }

  /** 「设为 Auto 带书」: saves the trio to prefs.auto_step_books (Weekly Produce 带书「ノウハウ页指定」); a pill once saved. */
  autoTrioButton(trio, ctx) {
    const { state, actions } = this._data;
    const supported = state?.bridgeKind === "mock" || Boolean(protocol?.definitions?.profile?.properties?.prefs?.properties?.auto_step_books);
    if (!supported || typeof actions?.setPref !== "function") return null;
    const entries = trio.map(autoStepBook);
    const ready = entries.every(Boolean);
    const saved = ready && JSON.stringify(state?.prefs?.auto_step_books ?? null) === JSON.stringify(entries);
    // Auto only looks at the picker's first screen (15 books, 評価値 descending)
    const far = trio.filter((b) => { const loc = this.locate(b, ctx); return !loc.approx && loc.position > PER_PAGE; });
    return h("span", { class: "kh-auto-trio" },
      saved ? pill("Auto 带书", "done", "Weekly Produce 带书「ノウハウ页指定」带这 3 本")
        : h("button", { class: "btn btn-small", type: "button", disabled: !ready, dataset: { focusKey: "kh-auto-trio" },
          title: ready ? "Weekly Produce 带书「ノウハウ页指定」带这 3 本" : "书名、属性或評価値未读取",
          onclick: () => actions.setPref("auto_step_books", entries) }, "设为 Auto 带书"),
      far.length ? pill("第一屏外", "warn", `${far.map((b) => b.title || b.idol).join("、")}：評価値排在选书第一屏（前 ${PER_PAGE} 本）之后，Auto 会停下`) : null);
  }

  /** 「设为 Halo 带书」: saves one book to prefs.halo_book (say "Halo" 带书「ノウハウ页指定」); a pill once saved. */
  haloBookButton(book, loc) {
    const { state, actions } = this._data;
    const supported = state?.bridgeKind === "mock" || Boolean(protocol?.definitions?.profile?.properties?.prefs?.properties?.halo_book);
    if (!supported || typeof actions?.setPref !== "function") return null;
    const entry = autoStepBook(book);
    if (entry && JSON.stringify(state?.prefs?.halo_book ?? null) === JSON.stringify([entry])) {
      return pill("Halo 带书", "done", "say \"Halo\" 带书「ノウハウ页指定」带这本");
    }
    const far = !loc.approx && loc.position > PER_PAGE;
    return h("button", { class: "btn btn-small", type: "button", disabled: !entry || far, dataset: { focusKey: `kh-halo-book-${book.fingerprint}` },
      title: !entry ? "书名、属性或評価値未读取" : far ? `評価値排在选书第一屏（前 ${PER_PAGE} 本）之后，Maa 找不到` : "say \"Halo\" 带书「ノウハウ页指定」带这本",
      onclick: () => actions.setPref("halo_book", [entry]) }, "设为 Halo 带书");
  }

  go(patch) {
    if ("sub" in patch) view.subTouched = true;
    Object.assign(view, patch);
    this.render();
  }

  render() {
    const { state } = this._data;
    const old = this.querySelector(".panel-scroll");
    if (old) view.scroll[old.dataset.sub] = old.scrollTop;
    clear(this);
    this.className = "panel knowhow-panel";
    const data = view.data || { books: [], pending: [], catalog: buildCatalog(), scan: null, loaded: false };
    const extras = view.extras || { demoTeam: null };
    const draft = wantedNeverSaved(state);
    // U20: nothing saved yet = no target list (templates are an optional starting point on ③, not applied silently).
    const wanted = draft ? [] : wantedList(state);
    if (!view.plan) {
      view.plan = loadPlan(wanted, planKey(state));
      if (view.plan.migrated && view.plan.targets.length) saveLocal(planKey(state), { ...view.plan, migrated: undefined });
    }
    if (view.previewTemplate && !view.plan.targets.length) {
      const t = targetFromTemplate(view.previewTemplate);
      if (t) view.plan = upsertTarget(view.plan, t);
      view.previewTemplate = null;
    }
    // 带书 without a saved Target previews 通用底本 (not saved until the user picks or acts on it)
    const preview = view.sub === "bring" && !view.plan.targets.length && data.books.length ? targetFromTemplate("base", "t-preview") : null;
    const plan = preview ? { ...upsertTarget(view.plan, preview), active: preview.id } : view.plan;
    const goal = activeTarget(plan);
    // Library hits / coverage follow the active Target (its wanted roles); without one, the saved wanted list.
    const hitList = goal ? targetToWanted(goal) : wanted;
    const books = mergeBooks(data.books, plan);
    const team = this.team(state, data, extras);
    const ws = { ...workflowState({ plan, books, team }), preview: Boolean(preview) };
    const ctx = { state, data: { ...data, books }, scanned: data.books, extras, wanted: hitList, draft: draft && !goal, team, ws,
      effects: effectIndex(data.books) };
    ctx.api = this.api(ws, ctx);
    if (!view.subTouched && !view.defaultChosen && data.loaded) {
      view.defaultChosen = true;
      if (books.length && goal) view.sub = "bring";  // 带书 is the main page once there is a library and a Target
    }
    if (view.openBook === "first" && books.length) view.openBook = libraryRows(books, hitList, { sort: view.sort })[0]?.book.fingerprint;
    const body = view.sub === "library" ? this.libraryPage(ctx) : view.sub === "goals" ? this.goalsPage(ctx) : this.bringPage(ctx);
    const read = ctx.scanned.length > 0;
    const saved = view.plan.targets.length > 0;
    const steps = read && saved ? {} : { library: read ? "✓" : "1", goals: saved ? "✓" : "2", bring: "3" };
    const scroll = h("div", { class: "panel-scroll", dataset: { scrollKey: `knowhow-${view.sub}`, sub: view.sub } },
      h("nav", { class: "kh-subnav", "aria-label": "ノウハウ子页面" },
        segmented("ノウハウ子页面", SUBPAGES.map(([k, label]) => [k, label, steps[k]]), view.sub, (k) => this.go({ sub: k }), "kh-sub")),
      this.statusStrip(ctx),
      view.sub === "goals" ? null : targetBar(ws, ctx.api, { compact: view.sub === "library" }),   // 目标 lists and switches the Targets itself
      body);
    this.append(scroll);
    scroll.scrollTop = view.scroll[view.sub] || 0;
  }

  team(state, data, extras) {
    if (view.manual) return { ...teamFromIdols(view.manual), label: "手动指定" };
    const team = profileTeam(state);
    if (!team.slots.length && extras.demoTeam) return { ...extras.demoTeam, label: "预览示例队伍（合成）" };
    return { ...team, label: "来自 W.I.N.G. 编队" };
  }

  /** One line (2026-10-24, user: 一眼看明白): 书库 read count, 新获得 (a link to it) and 读取书库; details in tooltips. */
  statusStrip({ data, scanned, wanted }) {
    const status = scanStatus(scanned, data.scan, data.pending.length);
    const sum = knowhowSummary(data, wanted);
    const summary = view.loading ? "正在读取本地记录…" : data.books.length || data.pending.length ? status.text : "尚无本地记录";
    const empty = !data.books.length && !data.pending.length;
    const facts = [summary, ...status.extra, data.demo ? "预览示例数据（合成）" : null].filter(Boolean);
    return h("div", { class: "kh-status", role: "status" },
      factLine([
        { key: "kh-read", label: "书库", missing: empty,
          value: view.loading ? "读取中…" : empty ? "未读取" : status.total === null ? `${status.read} 本`
            : status.read < status.total ? `${status.total} 本 · ${status.total - status.read} 未读完` : `${status.total} 本`,
          tone: empty ? null : status.partial ? "warning" : "success", title: facts.join("\n") },
        data.pending.length ? { key: "kh-new", label: "新获得", value: `${data.pending.length} 本`, tone: "accent",
          title: `W.I.N.G. 结束时读到，其中已保護 ${sum.pendingProtected} 本`,
          onclick: () => this.go({ sub: "library" }), focusKey: "kh-goto-new" } : null,
      ], { label: "ノウハウ 概况" }),
      // The 带书 intro of an empty library carries its own 读取书库 button; do not show two.
      empty && view.sub === "bring" && !view.loading ? null : this.scanControl());
  }

  /** 「扫描ノウハウ」: button (or progress while the host runs it) and the last result, same gate as 「扫描持有卡」. */
  scanControl(focusKey = "knowhow-scan") {
    const { state, actions } = this._data;
    const hostScan = state?.host?.knowhow_scan;
    const gate = knowhowScanGate(state?.policy, state?.host);
    const lastMode = "scan";
    const control = gate.running
      ? knowhowScanProgress(hostScan, { onStop: () => actions.knowhowScanStop?.() })
      : h("button", { class: "btn btn-small", type: "button", disabled: gate.ready ? null : "", title: gateTitle(gate, state?.prefs?.developer === true),
        dataset: { focusKey, knowhowScan: gate.ready ? "ready" : gate.blocker ? "blocked" : "pending" },
        onclick: () => openKnowhowScanConfirm({ mode: lastMode, onConfirm: (mode) => actions.knowhowScanStart?.(mode) }) }, gate.label);
    const dev = state?.prefs?.developer === true && gate.detail ? h("p", { class: "muted small kh-scan-dev", text: gate.detail }) : null;
    return h("div", { class: "kh-scan-control" }, control, dev, knowhowScanResultNote(hostScan));
  }

  // ------------------------------------------------------------------------------------------- ① 带书
  bringPage(ctx) {
    const { data } = ctx;
    if (view.loading && !data.loaded) return this.loadingBlock();   // not 「书库还没有读取」 while the module loads
    if (!data.books.length) {
      return h("div", { class: "kh-block" }, subhead("带书"), emptyState({ title: "尚未读取书库",
        action: h("div", { class: "kh-intro-actions" },
          this.scanControl("kh-bring-scan"),
          data.pending.length ? h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-bring-new" }, onclick: () => this.go({ sub: "library" }) }, `新获得 ${data.pending.length} 本`) : null,
          h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-bring-goals" }, onclick: () => this.go({ sub: "goals" }) }, "设置目标")) }));
    }
    // without a saved Target the page previews 通用底本 (render), so there is always something to bring
    const body = this.scenario(ctx) === "step" ? stepWorkflow(ctx.ws, ctx.api) : this.haloSection(ctx);
    return h("div", {}, this.teamSection(ctx), body);
  }

  /** STEP / Halo on 带书: the user's pick, else the active Target's scenario (STEP for 两者). */
  scenario(ctx) {
    if (view.scenarioSet) return view.scenario;
    return ctx.ws.target?.scenario === "halo" ? "halo" : "step";
  }

  /**
   * 编队 (whose books cannot be brought): the idols, or why they are unknown; how many books that rules out; 更改 = a
   * manual pick for this page. 2026-10-24 copy: plain terms (编队 / 未选择 P 卡), no chatty wording.
   */
  teamSection(ctx) {
    const { data, team } = ctx;
    const names = team.slots.length ? team.slots.map((s) => s.idol || "?") : team.idols;
    const editor = view.editTeam ? this.teamEditor(data, team) : null;
    const blocked = data.books.filter((b) => bookUsability(b, team) === "blocked");
    const excluded = blocked.reduce((s, b) => s + (b.count || 1), 0);
    const reason = team.verified ? "" : (team.reason || "编队未确定").replace(/（.*$/, "");
    const halo = this.scenario(ctx) === "halo";
    // position pills only when the Target has items tied to a position (templates have none: the pills would do nothing)
    const byPosition = !ctx.ws.target?.position && (ctx.ws.target?.items || []).some((i) => i.position);
    const toProduce = /编队未设置|未选择 P 卡/.test(reason) && !view.manual && typeof this._data.actions?.setTab === "function";
    return h("div", { class: "kh-block kh-team-block" },
      h("div", { class: "kh-team-line", title: reason ? "编队未确定：带入前请确认书的名义不是编队中的偶像" : team.label },
        h("span", { class: "kh-inline-label", text: "编队" }),
        names.length ? h("span", { class: "kh-team-names small", lang: "ja", text: names.join("・") }) : null,
        reason ? h("span", { class: "kh-team-warn small" }, jaText(reason)) : null,
        toProduce ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-team-produce" }, title: "在 Produce 页设置 W.I.N.G. 编队",
          onclick: () => this._data.actions.setTab("produce") }, "去设置") : null,
        excluded ? h("span", { class: "muted small", title: [...new Set(blocked.map((b) => b.idol).filter(Boolean))].join("、"), text: `不可带入 ${excluded} 本` }) : null,
        h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-team-edit" }, title: "手动指定编队偶像（仅本页）",
          "aria-expanded": String(view.editTeam), onclick: () => this.go({ editTeam: !view.editTeam, manual: view.manual || [...team.idols] }) },
        view.editTeam ? "完成" : "手动指定"),
        view.manual ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-team-reset" },
          onclick: () => this.go({ manual: null, editTeam: false }) }, "还原") : null),
      editor,
      segmented("剧本", [["step", "S.T.E.P. · 3 本"], ["halo", "say \"Halo\" · 1 本"]], halo ? "halo" : "step", (k) => this.go({ scenario: k, scenarioSet: true }), "kh-scn"),
      // a Target with its own position (Center / Leader / 特化) decides it; the pills only for a Target without one
      halo && byPosition ? choiceChips("位置", [["", "不限位置"], ...POSITIONS.map((p) => [p, POSITION_LABEL[p]])], view.position,
        (v) => this.go({ position: v }), "kh-position", { cls: "kh-pos-chips" }) : null);
  }

  position(ctx) {
    return ctx.ws.target?.position || ((ctx.ws.target?.items || []).some((i) => i.position) ? view.position : "");
  }

  teamEditor(data, team) {
    const names = new Set(team.idols);
    const idols = [...new Set([...data.books.map((b) => b.idol).filter(Boolean).map(normalizeName), ...names])];
    const flip = (idol) => this.go({ manual: names.has(idol) ? (view.manual || []).filter((n) => n !== idol) : [...(view.manual || []), idol] });
    // grouped by unit (35 unsorted pills were hard to scan, 2026-10-24 role-play); names outside the list under 其他
    const listed = new Set(IDOL_UNITS.flatMap(([, list]) => list));
    const groups = [...IDOL_UNITS.map(([unit, list]) => [unit, list.filter((n) => idols.includes(n))]),
      ["其他", idols.filter((n) => !listed.has(n)).sort((a, b) => a.localeCompare(b, "ja"))]].filter(([, list]) => list.length);
    return h("div", { class: "kh-team-editor kh-form", role: "group", "aria-label": "编队偶像（手动指定）", title: "选中偶像名义的书不可带入；仅本页生效" },
      groups.map(([unit, list]) => h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", lang: "ja", text: unit }),
        h("div", { class: "chip-row" }, list.map((idol) => pickChip(idol, { pressed: names.has(idol), focusKey: `kh-team-${idol}`, onclick: () => flip(idol) }))))));
  }

  locate(book, ctx) {
    return gridLocation(book, ctx.data.books, ctx.data.scan, ctx.data.pending.length);
  }

  /** Halo pick as one expandable line: 推荐/备选 · idol · hit chips · hits/目标; location, discounts and gaps on expand. */
  bookCard(book, { usability, hits, gaps = [] }, ctx, key, { lead = null, right = null, sub = null, halo = false } = {}) {
    const loc = this.locate(book, ctx);
    const recipe = searchRecipe(book, loc, hits);
    const open = Boolean(view.open[key]);
    // say "Halo": the Lv only lowers the 習得 cost (HALO_DISCOUNT), shown on each chip
    const off = (m) => (HALO_DISCOUNT[m.level] ? `-${HALO_DISCOUNT[m.level]}%` : null);
    return compactRow({ key: `kh-${key}`, open, onToggle: () => { view.open[key] = !open; this.render(); }, book, lead, leadMuted: lead === "备选",
      chips: hits.map((m) => miniChip(m.name, m.level, { cls: "is-hit" })),
      // 「?」 only when the team is known but the book's idol is not (an unverified team is said once on the 编队 line)
      tags: [ctx.ws.mainFp === book.fingerprint ? microTag("主书", "当前主书", "is-main") : null,
        usability === "unknown" && ctx.team.verified ? microTag("未读", "名义未读，带入前确认", "is-unsure") : null,
        book.complete ? null : microTag("未读完", "详情未读完，可能还有其他目标条目", "is-unsure")].filter(Boolean),
      right: right ?? String(hits.length), sub,
      body: h("div", { class: "small" },
        h("div", { class: "kh-where", title: loc.notes?.join("\n") || null },
          h("span", { class: "kh-where-text" }, h("span", { class: "muted", text: loc.approx ? "一覧 约 " : "一覧 " }), jaText(loc.text)),
          copyButton("复制查找方式", recipe, `kh-copy-${key}`), halo ? this.haloBookButton(book, loc) : null),
        h("div", { class: "kh-chips" }, hits.map((m) => chip(m.name, m.level, { cls: "is-hit", suffix: off(m),
          title: `${ROLE_LABEL[m.role] || "目标"} ≥Lv${m.item.min_level}${off(m) ? `；習得 ${off(m)}` : ""}` }))),
        gaps.length ? h("div", { class: "kh-book-gaps" }, jaText(`缺少：${gaps.map((g) => g.name).join("、")}`)) : null,
        h("div", { class: "muted", lang: "ja", text: `${book.title || ""} · ${statText(book)} · 評価値 ${book.eval_value ?? "—"}` })) });
  }

  haloSection(ctx) {
    const target = ctx.ws.target;
    if (!target) return null;   // the target bar above offers the recommended targets
    if (!targetUnits(target).some((u) => u.role !== "excluded")) {
      return h("div", { class: "kh-block" }, emptyState({ title: "目标还没有条目",
        action: h("button", { class: "btn btn-small", type: "button", onclick: () => this.go({ sub: "goals" }) }, "添加条目") }));
    }
    // 2026-10-24 second role-play: ranked by the Target's roles (required first), not the raw hit count; the right
    // column is the same n/total (units the strategy needs) as the route, the 书库 groups and the 目标 card.
    const res = haloRank(ctx.data.books, target, ctx.team, { position: this.position(ctx), rules: ctx.ws.rules });
    const missing = analyzeInventory(ctx.data.books, target, ctx.team).filter((a) => !a.meeting && a.unit.role !== "optional");
    return h("div", { class: "kh-block" },
      h("div", { class: "kh-sec-head", title: "按目标条目排序：必需 > 优先 > 可选，再看 Lv 与評価値。say \"Halo\" 在 S1 末全部继承，Lv 只影响習得折扣。" },
        h("span", { class: "kh-sec-title", text: "推荐带入" }),
        ctx.team.verified ? null : h("span", { class: "kh-pill is-warn", title: "编队未确定：带入前确认书的名义不是编队中的偶像", text: "未排除编队偶像" }),
        h("span", { class: "kh-crow-right kh-sec-right muted small", title: "命中的目标条目 / 完成条件需要的条目；小字为評価値" },
          "命中", h("span", { class: "kh-crow-sub", text: "評価値" }))),
      res.picks.length ? res.picks.map((p, i) => this.bookCard(p.book, p, ctx, `halo-${i}`, { lead: i ? "备选" : "推荐", right: `${p.done}/${res.total}`, sub: p.book.eval_value ?? null, halo: true }))
        : h("p", { class: "muted small", text: "书库中没有命中目标的可带入书。" }),
      this.missingLine(missing.map((a) => ({ name: a.unit.label }))));
  }

  missingLine(missing) {
    if (!missing.length) return null;
    return h("div", { class: "kh-foot" }, h("div", { class: "kh-gaps", title: "书库中没有达到最低 Lv 的可带入书" },
      h("span", { class: "kh-gaps-label is-strong", text: "书库没有" }),
      h("div", { class: "kh-chips" }, missing.map((m) => miniChip(m.name, null, { cls: "is-gap" })))));
  }

  // ------------------------------------------------------------------------------------------- 书库
  loadingBlock() {
    return h("div", { class: "kh-block" }, h("p", { class: "muted small", role: "status", text: "正在读取本地记录…" }));
  }

  libraryPage(ctx) {
    const { data } = ctx;
    if (view.loading && !data.loaded) return this.loadingBlock();
    if (!data.books.length) {
      return h("div", {}, data.pending.length ? this.pendingSection(ctx) : null,
        h("div", { class: "kh-block" }, emptyState({ title: "还没有读取书库" })));
    }
    const facets = libraryFacets(data.books);
    const sources = [...new Set(data.books.flatMap((b) => b.rows.map((r) => parseEntry(r.name).source)).filter(Boolean))].sort();
    const search = h("input", { class: "input", type: "search", placeholder: "偶像、卡名或条目（如 Lv5 思い出）", "aria-label": "搜索书库",
      value: view.query, dataset: { focusKey: "kh-lib-search" }, oninput: (e) => { view.query = e.target.value; view.shownBy = {}; this.refreshSheet(ctx); } });
    const pick = (patch) => { Object.assign(view, patch, { shownBy: {} }); this.render(); };
    // 2026-10-24 (user: dropdowns are slow and ugly): sort / idol / source as pills under 「更多」; an entry is found by
    // typing it in the search field (「Lv5 思い出」 = that entry at Lv5 or higher). Without a Target the target filter and
    // sort are left out (they would show nothing / sort nothing).
    const targeted = ctx.wanted.length > 0;
    const sort = this.librarySort(ctx);
    const more = [view.idol, view.src, sort !== (targeted ? "wanted" : "eval")].filter(Boolean).length;
    const filters = h("div", { class: "kh-filters" },
      h("div", { class: "search-wrap" }, icon("search", { size: 16 }), search),
      h("div", { class: "kh-chip-filter", role: "radiogroup", "aria-label": "筛选" }, LIBRARY_STATUS.filter(([key]) => targeted || key !== "wanted").map(([key, text]) =>
        h("button", { type: "button", role: "radio", "aria-checked": String(view.status === key), class: `kh-filter-chip${view.status === key ? " is-on" : ""}`,
          dataset: { focusKey: `kh-st-${key}` }, onclick: () => pick({ status: key }) }, text)),
      h("button", { type: "button", class: `kh-filter-chip kh-more-filters${view.moreFilters || more ? " is-on" : ""}`, "aria-expanded": String(Boolean(view.moreFilters)),
        dataset: { focusKey: "kh-lib-more-filters" }, onclick: () => pick({ moreFilters: !view.moreFilters }) }, more ? `更多 ${more}` : "更多")),
      view.moreFilters ? h("div", { class: "kh-form kh-filter-more" },
        h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "排序" }),
          choiceChips("排序", [targeted ? ["wanted", "目标优先"] : null, ["eval", "評価値"], ["idol", "偶像"]].filter(Boolean), sort, (v) => pick({ sort: v }), "kh-lib-sort")),
        h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "偶像" }),
          choiceChips("偶像", [["", "全部"], ...facets.idols.map(([n, c]) => [n, `${n} ${c}`])], view.idol, (v) => pick({ idol: v }), "kh-lib-idol", { cls: "is-ja" })),
        sources.length ? h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "来源" }),
          choiceChips("来源剧本", [["", "全部"], ...sources.map((x) => [x, x])], view.src, (v) => pick({ src: v }), "kh-lib-src")) : null) : null);
    this.sheet = h("div", { class: "kh-sheet-wrap" });
    this.fillSheet(ctx);
    return h("div", {},
      data.pending.length ? this.pendingSection(ctx) : null,
      h("div", { class: "kh-block kh-lib" }, filters, this.sheet),
      missingSection(ctx.ws, ctx.api),
      this.reviewSection(ctx));
  }

  refreshSheet(ctx) {
    if (this.sheet) this.fillSheet(ctx);
  }

  librarySort(ctx) {
    return ctx.wanted.length || view.sort !== "wanted" ? view.sort : "eval";
  }

  filtersActive() {
    return Boolean(view.query || view.status !== "all" || view.idol || view.entry || view.src);
  }

  fillSheet(ctx) {
    const { data, wanted } = ctx;
    clear(this.sheet);
    const rows = collapseIdentical(filterLibrary(libraryRows(data.books, wanted, { query: view.query, sort: this.librarySort(ctx) }),
      { status: wanted.length || view.status !== "wanted" ? view.status : "all", idol: view.idol, entry: view.entry, minLevel: view.minLevel })
      .filter(({ book }) => !view.src || book.rows.some((r) => parseEntry(r.name).source === view.src)));
    const total = data.books.reduce((n, b) => n + (b.count || 1), 0);
    const reset = this.filtersActive() ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "kh-lib-reset" },
      onclick: () => { Object.assign(view, { query: "", status: "all", idol: "", entry: "", minLevel: 1, src: "", shownBy: {} }); this.render(); } }, "清除") : null;
    this.sheet.append(h("div", { class: "kh-result-line" },
      h("span", { class: "muted small", text: this.filtersActive() ? `${rows.length} 种 / 共 ${data.books.length} 种 ${total} 本` : `共 ${data.books.length} 种 ${total} 本` }), reset));
    if (!rows.length) {
      this.sheet.append(h("p", { class: "muted small", text: "没有符合条件的书。" }));
      return;
    }
    this.sheet.append(h("div", { class: "kh-sheet-head", "aria-hidden": "true" }, h("span", { text: "名义" }), h("span", { text: "ノウハウ" }), h("span", { text: "評価値" })));
    if (!ctx.ws.target) {
      this.sheet.append(this.sheetList(rows, ctx, "all"));
      return;
    }
    // Grouped by classification for the active Target; the relevant group opens, the others stay closed (remembered per session).
    const need = evaluateBook({ rows: [] }, ctx.ws.target, ctx.ws.rules).progress.total;
    const groups = groupLibrary(rows, (book) => classOf(book, ctx.ws), (key) => `命中 ${key.slice(1)}/${need} 项`);
    const auto = defaultOpenGroup(groups, ctx.ws.mainFp);
    for (const group of groups) {
      const open = this.filtersActive() ? view.groupOpen[group.key] !== false : view.groupOpen[group.key] ?? group.key === auto;
      const el = h("details", { class: `kh-lib-group is-${group.key}`, dataset: { group: group.key } },
        h("summary", { class: "kh-lib-group-head", dataset: { focusKey: `kh-group-${group.key}` } },
          icon("chevron", { size: 16, cls: "kh-lib-group-chev" }),
          h("span", { class: "kh-lib-group-title", text: group.label }),
          h("span", { class: "kh-lib-group-count num", text: group.copies > group.kinds ? `${group.kinds} 种 ${group.copies} 本` : `${group.kinds}` })));
      if (open) { el.setAttribute("open", ""); el.append(this.sheetList(group.rows, ctx, group.key)); }
      el.addEventListener("toggle", () => {
        view.groupOpen[group.key] = el.open;
        if (el.open && !el.querySelector(".kh-sheet")) el.append(this.sheetList(group.rows, ctx, group.key));
      });
      this.sheet.append(el);
    }
  }

  sheetList(rows, ctx, key) {
    const shown = view.shownBy[key] || LIBRARY_PAGE;
    const wrap = h("div", {});
    wrap.append(h("ul", { class: "kh-sheet", "aria-label": "书库" }, rows.slice(0, shown).map((r) => this.sheetRow(r, ctx))));
    if (rows.length > shown) {
      wrap.append(h("button", { class: "btn btn-small btn-text kh-more", type: "button", dataset: { focusKey: `kh-lib-more-${key}` },
        onclick: () => { view.shownBy[key] = shown + LIBRARY_MORE; this.fillSheet(ctx); } }, `再显示 ${Math.min(LIBRARY_MORE, rows.length - shown)} 种`));
    }
    return wrap;
  }

  /** One line per book: idol · ROW_CHIPS short chips (filtered / target hits first) and 「+N」 · ×N / 主 / 保護 · 評価値. */
  sheetRow({ book, hits, copies = book.count || 1, same = [] }, ctx) {
    const open = view.openBook === book.fingerprint;
    const hitNames = new Set(hits.map((m) => m.name));
    const focus = view.entry;
    const ranked = [...book.rows].sort((a, b) => Number(b.name === focus) - Number(a.name === focus)
      || Number(hitNames.has(b.name)) - Number(hitNames.has(a.name)) || (b.level ?? 0) - (a.level ?? 0));
    const shown = ranked.slice(0, ROW_CHIPS);
    const head = h("button", { class: "kh-sheet-row", type: "button", "aria-expanded": String(open), dataset: { focusKey: `kh-row-${book.fingerprint}` },
      onclick: () => { view.openBook = open ? null : book.fingerprint; this.fillSheet(ctx); } },
    idolBar(book),
    h("span", { class: "kh-row-idol", lang: "ja", text: bookLabel(book), title: book.title || "" }),
    h("span", { class: "kh-row-chips" }, shown.map((r) => miniChip(r.name, r.level, { cls: r.name === focus || hitNames.has(r.name) ? "is-hit" : "" }))),
    this.rowMore(book, shown, hitNames, ctx.wanted.length > 0),
    h("span", { class: "kh-row-tags" },
      copies > 1 ? microTag(`×${copies}`, `相同内容 ${copies} 本`) : null,
      ctx.ws.mainFp === book.fingerprint ? microTag("主书", "当前主书", "is-main") : null,
      book.protected ? microTag("保護", "已保護", "is-kept") : null,
      book.source === "user" ? microTag("登记", "手动登记") : null,
      book.complete ? null : microTag("未读完", "详情未读完", "is-unsure")),
    h("span", { class: "kh-sheet-ev num", title: "評価値", text: book.eval_value ?? "—" }));
    return h("li", { class: open ? "is-open" : "" }, head, open ? this.bookDetail(book, hits, ctx, same) : null);
  }

  rowMore(book, shown, hitNames, targeted) {
    const rest = book.rows.length - shown.length;
    if (!targeted) return rest > 0 ? h("span", { class: "kh-row-more", text: `+${rest}` }) : null;
    const hitsLeft = [...hitNames].filter((n) => !shown.some((r) => r.name === n)).length;
    const title = `${hitsLeft ? `另 ${hitsLeft} 条目标条目；` : ""}${rest - hitsLeft} 条非目标`;
    return hitsLeft ? h("span", { class: "kh-row-more is-hit", title, text: `+${hitsLeft}` }) : rest ? h("span", { class: "kh-row-more", title, text: "…" }) : null;
  }

  bookDetail(book, hits, ctx, same = []) {
    const loc = this.locate(book, ctx);
    const recipe = searchRecipe(book, loc, hits);
    const hitNames = new Set(hits.map((m) => m.name));
    const unread = Number.isInteger(book.knowhow_count) && book.knowhow_count > book.rows.length ? book.knowhow_count - book.rows.length : 0;
    const effects = book.rows.some((r) => r.effect);
    return h("div", { class: "kh-detail" },
      h("div", { class: "kh-detail-meta small" },
        h("span", { class: "muted", lang: "ja", text: book.title || "（卡名未读）" }),
        h("span", { class: "num", text: statText(book), title: "Vo/Da/Vi/Me" }),
        usableTag(bookUsability(book, ctx.team)),
        book.marked ? h("span", { class: "muted", text: "マーク" }) : null),
      h("ul", { class: "kh-entries", "aria-label": "ノウハウ" }, book.rows.map((r) => h("li", { class: hitNames.has(r.name) || r.name === view.entry ? "is-hit" : "" },
        h("span", { class: "kh-entry-lv num", text: r.level ? `Lv${r.level}` : "Lv?" }),
        h("span", { class: "kh-entry-name", lang: "ja", text: r.name }),
        r.effect && view.effects ? h("span", { class: "kh-entry-effect muted small", lang: "ja", text: r.effect }) : null))),
      effects ? h("button", { class: "btn btn-small btn-text kh-effects-toggle", type: "button", dataset: { focusKey: "kh-effects" },
        onclick: () => { view.effects = !view.effects; this.fillSheet(ctx); } }, view.effects ? "收起效果" : "显示效果") : null,
      unread ? h("p", { class: "kh-flag small", text: `还有 ${unread} 条没读到` }) : null,
      same.length ? h("p", { class: "muted small", text: `相同内容另有 ${same.length} 条记录` }) : null,
      ...bookInsights(book, ctx.ws, ctx.api),
      h("div", { class: "kh-where", title: loc.notes?.join("\n") || null }, h("span", { class: "kh-where-text small" }, h("span", { class: "muted", text: loc.approx ? "一覧 约 " : "一覧 " }), jaText(loc.text)),
        copyButton("复制查找方式", recipe, `kh-copy-lib-${book.fingerprint}`)));
  }

  pendingSection({ data, wanted, ws }) {
    // 2026-10-24 (user: 省时间): each new book says at a glance what it is worth, most useful first; worth keeping and not
    // 保護-ed = 「建议保護」 (Maa never protects or sells).
    const targeted = wanted.length > 0;
    const notes = pendingNotes(data.pending, data.books, wanted)
      .map((n) => {
        const hits = wantedHits(n.book, wanted);
        const value = data.books.length ? newBookValue(n.book, data.books, ws.plan.targets, { plan: ws.plan, rules: ws.rules }) : null;
        const higher = targeted ? n.notes.filter((x) => x.wanted) : n.notes;
        const verdict = value?.key || (!data.books.length ? null : higher.length ? "higher" : targeted && !hits.length ? "none" : "same");
        return { ...n, hits, value, verdict, shown: targeted ? higher : n.notes };
      })
      .sort((a, b) => (VERDICT_RANK[a.verdict] ?? 2) - (VERDICT_RANK[b.verdict] ?? 2) || Number(b.hits.length > 0) - Number(a.hits.length > 0)
        || String(b.book.seen || "").localeCompare(String(a.book.seen || "")));
    const useful = (n) => n.verdict && !["same", "none"].includes(n.verdict);
    const keep = notes.filter((n) => useful(n) && n.book.protected === false).length;
    const counts = Object.entries(VERDICT_LABEL).map(([k, label]) => [label, notes.filter((n) => n.verdict === k).length]).filter(([, n]) => n);
    // 「【黒百合で待ち合わせ】風野灯織」: the idol already leads the line
    const cardName = (book) => (book.idol ? String(book.title || "").replace(new RegExp(`${book.idol}$`), "") : book.title || "");
    return foldSection({ key: "kh-pending", title: "W.I.N.G. 新获得", count: notes.length, defaultOpen: true, tone: "accent",
      hint: "獲得報酬画面读到的书；四项能力与評価値在读取书库后补全",
      summary: h("span", { class: "muted small", text: [keep ? `建议保護 ${keep}` : null, ...counts.map(([label, n]) => `${label} ${n}`)].filter(Boolean).join(" · ") }),
      children: h("div", {},
      h("ul", { class: "kh-pending" }, notes.map(({ book, shown, hits, value, verdict }, i) => h("li", {
        class: `kh-card${hits.length ? " is-wanted" : ""}`, title: book.seen ? `读到：${formatTime(book.seen)}` : null },
        h("div", { class: "kh-sheet-name" }, idolBar(book), h("span", { class: "kh-new", text: "新" }),
          h("span", { class: "kh-idol", lang: "ja", text: book.idol || cardName(book) || "待读取" }),
          book.idol && cardName(book) ? h("span", { class: "muted small", lang: "ja", text: ` ${cardName(book)}` }) : null,
          verdict ? h("span", { class: `kh-tag kh-verdict is-${verdict}`, title: value?.text || VERDICT_TIP[verdict],
            text: hits.length && verdict !== "none" ? `目标 ${hits.length} · ${VERDICT_LABEL[verdict]}` : VERDICT_LABEL[verdict] }) : null,
          book.protected === true ? h("span", { class: "kh-tag is-kept", title: "读取时已保護（Maa 只读，不会解除）", text: "已保護" })
            : book.protected === false && useful({ verdict }) ? h("span", { class: "kh-tag is-gap", title: "Maa 不会保護，请在游戏中保護", text: "建议保護" }) : null),
        // The comparison with the library is the chip's mark and tooltip, not a sentence per row
        h("div", { class: "kh-chips" }, book.rows.map((r) => {
          const up = data.books.length ? shown.find((n) => n.name === r.name) : null;
          return chip(r.name, r.level, { cls: [hits.some((m) => m.name === r.name) ? "is-hit" : r.level >= 5 ? "is-lv5" : "", up ? "is-up" : ""].join(" ").trim(),
            title: up ? (up.library ? `书库最高 Lv${up.library}` : "书库没有") : null });
        })),
        h("span", { class: "kh-sr", text: `新书 ${i + 1}` }))))) });
  }

  reviewSection({ data, wanted }) {
    if (!data.books.length) return null;
    const res = reviewFacts(data.books, wanted);
    const byFp = (b) => `${b.title || bookLabel(b)}（${statText(b)}）`;
    const ex = res.excluded;
    return foldSection({ key: "kh-review", title: "整理复核", count: res.facts.length, defaultOpen: false,
      hint: `每条ノウハウ都被另一本同偶像的书以不低于的 Lv 持有。${REVIEW_NOTE}\n不列入（依次判断）：保護 ${ex.protected} · マーク ${ex.marked} · 未读完 ${ex.incomplete} · 含目标 ${ex.wanted}`,
      children: h("div", {},

      res.facts.length ? h("ul", { class: "kh-review" }, res.facts.map(({ book, by, copies }) => h("li", {},
        h("div", { class: "kh-sheet-name" }, idolBar(book), h("span", { class: "kh-idol", lang: "ja", text: bookLabel(book) }),
          h("span", { class: "muted small", lang: "ja", text: ` ${book.title || ""}` })),
        h("div", { class: "kh-chips" }, book.rows.map((r) => chip(r.name, r.level))),
        h("div", { class: "small num", title: "四项能力" }, statText(book)),
        by.length ? h("div", { class: "small" }, jaText(`已被 ${by.map(byFp).join("、")} 覆盖`)) : null,
        copies ? h("div", { class: "small", text: `相同 ×${copies + 1}` }) : null,
        h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-review-${book.fingerprint}` },
          onclick: () => this.go({ sub: "library", openBook: book.fingerprint, query: "" }) }, "在书库中查看"))))
        : h("p", { class: "muted small", text: "已读部分里没有符合条件的书。" })) });
  }

  // ------------------------------------------------------------------------------------------- ③ 目标
  goalsPage(ctx) {
    const { ws, api } = ctx;
    // without a Target only the recommended ones (2026-10-24 role-play: three empty cards and 40 pills that silently
    // created 「我的目标」)
    if (!ws.target) return h("div", {}, targetsSection(ws, api));
    return h("div", {}, targetsSection(ws, api), targetSettings(ws, api), reuseSection(ws, api), this.wantedSection(ctx),
      this.catalogSection(ctx), compareSection(ws, api), this.coverageSection(ctx));
  }

  /** Replace the active Target's items (creates 「我的目标」 when there is none); mirrors to prefs.knowhow_wanted. */
  updateItems(ctx, items) {
    const plan = ctx.ws.plan;
    const target = ctx.ws.target || { id: newId(), name: "我的目标", scenario: "any", strategy: "balanced", items: [] };
    this.savePlan(upsertTarget(plan, { ...target, items }), { mirror: true, edited: ctx.ws.target || null });
  }

  /**
   * 目标条目 (2026-10-24, user: dropdowns are slow and ugly): the items as chips grouped by role (the layout says 必需 /
   * 优先 / 可选 / 排除); each chip shows whether the library has it. One tap opens an editor under the group with every
   * choice as a pill; 「更多」 holds 理想 Lv, name matching and the substitute group.
   */
  wantedSection(ctx) {
    const target = ctx.ws.target;
    const items = target?.items || [];
    const set = (index, patch) => this.updateItems(ctx, items.map((w, i) => (i === index ? { ...w, ...patch } : w)));
    const status = items.map((item) => itemStatus(item, ctx.ws));
    // a substitute group (思い出++ / 思い出+) is met when any member is: the others say 「替」, not 「缺」
    const groupMet = (item) => Boolean(item.group) && items.some((o, i) => o !== item && o.group === item.group && o.role !== "excluded" && status[i].holders);
    const editing = Number.isInteger(view.editItem) && items[view.editItem] ? view.editItem : null;
    const mark = (item, st) => {
      if (item.role === "excluded") return null;
      if (st.holders) return h("span", { class: "kh-item-mark is-ok", title: `${st.holders} 本达到最低 Lv`, text: `${st.holders}本` });
      if (groupMet(item)) return h("span", { class: "kh-item-mark", title: `同组「${item.group}」的其他条目已有书`, text: "替代" });
      return h("span", { class: "kh-item-mark is-bad", title: st.best ? `最高 Lv${st.best}，未达到最低 Lv` : "书库中没有", text: st.best ? `Lv${st.best}` : "缺" });
    };
    const editor = (index) => {
      const item = items[index];
      const lv = (prefix) => [1, 2, 3, 4, 5, 6].map((n) => [n, `${prefix}${n}`, n === 6 ? "Lv6 只来自道具ノウハウ" : null]);
      const effect = effectFor(ctx.effects, item);
      const source = sourceOf(item.name);
      return h("div", { class: "kh-item-editor", role: "group", "aria-label": `编辑 ${item.name}` },
        h("div", { class: "kh-item-editor-name", lang: "ja" }, itemLabel(item), source ? h("span", { class: "kh-src", title: "获得剧本" }, jaText(source)) : null),
        effect ? h("div", { class: "kh-item-note small", lang: "ja", text: effect }) : null,
        h("div", { class: "kh-form" },
          h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "优先级" }),
            choiceChips("优先级", ROLES.map((r) => [r, ROLE_LABEL[r], ROLE_TIP[r]]), item.role, (v) => set(index, { role: v }), `kh-role-${index}`)),
          item.role === "excluded" ? null : h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "最低 Lv" }),
            choiceChips("最低 Lv", lv(""), item.min_level, (v) => set(index, { min_level: v, ideal_level: Math.max(v, item.ideal_level) }), `kh-lv-${index}`)),
          item.role === "excluded" ? null : details("更多",
            h("div", { class: "kh-form" },
              h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "理想 Lv" }),
                choiceChips("理想 Lv", lv(""), item.ideal_level, (v) => set(index, { ideal_level: Math.max(v, item.min_level) }), `kh-ideal-${index}`)),
              h("div", { class: "kh-form-row" }, h("span", { class: "kh-form-label", text: "名称" }),
                choiceChips("名称匹配方式", Object.entries(MATCH_LABEL), item.match, (v) => set(index, { match: v }), `kh-match-${index}`)),
              h("div", { class: "kh-form-row", title: "同组任一达标即算完成（如 思い出++ / 思い出+）" }, h("span", { class: "kh-form-label", text: "替代组" }),
                h("input", { class: "input kh-group-input", type: "text", value: item.group || "", placeholder: "不分组", maxlength: 20,
                  "aria-label": "替代组", dataset: { focusKey: `kh-group-${index}` }, onchange: (e) => set(index, { group: e.target.value }) }))))),
        h("div", { class: "kh-row-actions" },
          h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `kh-del-${index}` },
            onclick: () => { view.editItem = null; this.updateItems(ctx, items.filter((_, i) => i !== index)); } }, "移除"),
          h("button", { class: "btn btn-small", type: "button", dataset: { focusKey: "kh-item-done" }, onclick: () => this.go({ editItem: null }) }, "完成")));
    };
    const groups = ROLES.map((role) => [role, items.map((item, index) => ({ item, index })).filter(({ item }) => item.role === role)]).filter(([, list]) => list.length);
    const units = target ? targetUnits(target) : [];
    const counts = ROLES.map((r) => [r, units.filter((u) => u.role === r).length]).filter(([, n]) => n).map(([r, n]) => `${ROLE_LABEL[r]} ${n}`);
    return foldSection({ key: "kh-wanted", title: "目标条目", count: null, defaultOpen: true,
      hint: "点条目修改优先级和 Lv。n本 = 达到最低 Lv 的书数；Lv2 = 最高只到 Lv2；缺 = 书库没有；替代 = 同组其他条目已有",
      summary: h("span", { class: "muted small", text: target ? counts.join(" · ") || "空" : "未设置" }),
      children: h("div", {},
        items.length ? groups.map(([role, list]) => h("div", { class: `kh-role-group is-${role}` },
          h("span", { class: "kh-role-label", title: ROLE_TIP[role] }, ROLE_LABEL[role], h("span", { class: "kh-role-sub", text: ROLE_SHORT[role] })),
          h("div", { class: "kh-item-cloud" }, list.map(({ item, index }) => h("button", { type: "button", class: `kh-item${editing === index ? " is-on" : ""}`,
            "aria-expanded": String(editing === index), dataset: { focusKey: `kh-item-${index}` }, title: "点击编辑",
            onclick: () => this.go({ editItem: editing === index ? null : index }) },
          h("span", { class: "kh-item-name", lang: "ja", text: shortName(itemLabel(item), 12) }),
          item.role !== "excluded" && item.min_level > 1 ? h("span", { class: "kh-item-lv num", text: `≥${item.min_level}` }) : null,
          mark(item, status[index])))),
          list.some(({ index }) => index === editing) ? editor(editing) : null))
          : emptyState({ title: "还没有条目" })) });
  }

  /**
   * 添加条目 (2026-10-24, replaces 「从名单添加」): the recommended entries
   * grouped by use (knowhow-plan.js RECOMMENDED_ITEMS), one tap adds or removes; the search also finds names seen in the
   * library and adds any typed name.
   */
  catalogSection(ctx) {
    const { data } = ctx;
    const items = ctx.ws.target?.items || [];
    const names = new Set(items.map((w) => normalizeName(w.name)));
    const full = items.length >= 40;
    const toggleItem = (entry) => (names.has(normalizeName(entry.name))
      ? this.updateItems(ctx, items.filter((w) => normalizeName(w.name) !== normalizeName(entry.name)))
      : full ? toast("一个目标最多 40 个条目", "neutral")
        : this.updateItems(ctx, [...items, { name: entry.name, match: entry.match || "contains", role: entry.role || "required",
          min_level: entry.min_level || 1, ideal_level: entry.ideal_level || 5, ...(entry.group ? { group: entry.group } : {}) }]));
    const rowOf = (entry, key) => {
      const on = names.has(normalizeName(entry.name));
      const role = entry.role || "required";
      const effect = effectFor(ctx.effects, entry);
      const source = sourceOf(entry.name);
      const lv = (entry.min_level || 1) > 1 ? ` ≥Lv${entry.min_level}` : "";
      return h("button", { class: `kh-rec-row${on ? " is-on" : ""}`, type: "button", "aria-pressed": String(on), dataset: { focusKey: `kh-rec-${key}-${entry.name}` },
        title: [effect, on ? "点击移出目标" : `加入为「${ROLE_LABEL[role]}」：${ROLE_TIP[role]}`].filter(Boolean).join("\n"), onclick: () => toggleItem(entry) },
      h("span", { class: "kh-rec-mark", "aria-hidden": "true", text: on ? "✓" : "＋" }),
      h("span", { class: "kh-rec-name", lang: "ja", text: itemLabel(entry) }),
      h("span", { class: "kh-rec-tags" }, source ? h("span", { class: "kh-src" }, jaText(source)) : null,
        h("span", { class: `kh-rec-role is-${role}`, text: `${ROLE_LABEL[role]}${lv}` })),
      effect ? h("span", { class: "kh-rec-note", lang: "ja", text: effect }) : null);
    };
    const list = h("div", { class: "kh-rec-list" });
    const fill = () => {
      clear(list);
      const q = normalizeName(view.catQuery).toLowerCase();
      if (!q) {
        const group = RECOMMENDED_ITEMS.find((g) => g.key === view.catGroup) || RECOMMENDED_ITEMS[0];
        const picked = (g) => g.items.filter((e) => names.has(normalizeName(e.name))).length;
        list.append(choiceChips("分组", RECOMMENDED_ITEMS.map((g) => [g.key, picked(g) ? `${g.label} ${picked(g)}` : g.label]), group.key,
          (k) => { view.catGroup = k; fill(); }, "kh-catg", { cls: "kh-rec-groups" }),
        h("div", { class: "kh-rec-rows" }, group.items.map((entry) => rowOf(entry, group.key))));
        return;
      }
      const seen = new Map();
      for (const entry of [...RECOMMENDED_ITEMS.flatMap((g) => g.items), ...data.catalog]) {
        const key = normalizeName(entry.name);
        if (!seen.has(key) && key.toLowerCase().includes(q)) seen.set(key, entry);
      }
      const found = [...seen.values()].slice(0, 30);
      list.append(found.length ? h("div", { class: "kh-rec-rows" }, found.map((entry) => rowOf(entry, "q"))) : h("p", { class: "muted small", text: "没有匹配的名称" }));
      const custom = cleanName(view.catQuery);
      if (custom && !seen.has(normalizeName(custom)) && !names.has(normalizeName(custom))) {
        list.append(h("button", { class: "btn btn-small kh-custom", type: "button", dataset: { focusKey: "kh-custom" },
          onclick: () => toggleItem({ name: custom, match: "contains" }) }, `添加「${custom}」`));
      }
    };
    const search = h("input", { class: "input", type: "search", placeholder: "搜索或输入条目名", "aria-label": "搜索条目名",
      value: view.catQuery, dataset: { focusKey: "kh-search" }, oninput: (e) => { view.catQuery = e.target.value; fill(); } });
    fill();
    return foldSection({ key: "kh-catalog", title: "添加条目", count: null, defaultOpen: !items.length,
      summary: null,
      children: h("div", {}, h("div", { class: "search-wrap" }, icon("search", { size: 16 }), search), list) });
  }

  coverageSection({ data, wanted, team, ws }) {
    if (!data.books.length || !wanted.length) return null;
    const map = coverageMap(data.books, wanted);
    const table = h("table", { class: "kh-map" },
      h("thead", {}, h("tr", {}, h("th", { class: "kh-map-item", scope: "col", text: "目标" }),
        map.idols.map((idol) => h("th", { scope: "col", class: `kh-map-idol${team.idols.includes(idol) ? " is-team" : ""}`,
          title: team.idols.includes(idol) ? "本队偶像：这列的书本队不可带" : null }, h("span", { lang: "ja", text: idol }))))),
      h("tbody", {}, map.rows.map((row, r) => h("tr", { class: row.met ? "" : "is-gap" },
        h("th", { scope: "row", class: "kh-map-item" }, h("span", { lang: "ja", text: itemLabel(row.item) }),
          h("span", { class: "muted small", text: ` ≥Lv${row.item.min_level}` }), row.met ? null : h("span", { class: "kh-tag is-gap", text: "缺" })),
        map.idols.map((idol, c) => {
          const cell = row.cells[idol];
          if (!cell) return h("td", { class: "kh-map-cell is-empty" }, h("span", { class: "kh-sr", text: "无" }));
          return h("td", { class: `kh-map-cell lv-${cell.level}${cell.meets ? " is-meets" : " is-below"}` },
            h("button", { type: "button", class: "kh-map-btn", title: `${cell.book.title || idol}：${row.item.name} Lv${cell.level}${cell.meets ? "" : "（低于最低 Lv）"}`,
              dataset: { focusKey: `kh-map-${r}-${c}` }, onclick: () => this.go({ sub: "library", openBook: cell.book.fingerprint, query: "" }) },
            String(cell.level)));
        })))));
    // the count is the Target's n/total (units its strategy needs), as on 带书 and the 目标 card; the table lists every item
    const needed = strategyNeeds(ws.target?.strategy).roles;
    const units = ws.target ? analyzeInventory(data.books, ws.target, team).filter((a) => needed.includes(a.unit.role)) : [];
    const missing = units.length ? units.filter((a) => !a.meeting).length : map.rows.filter((row) => !row.met).length;
    const total = units.length || map.rows.length;
    return foldSection({ key: "kh-coverage", title: "覆盖图", count: `${total - missing}/${total}`, defaultOpen: false,
      hint: "行 = 目标，列 = 偶像，格子 = 最高 Lv（虚框低于最低 Lv）；点格子看书，灰色列 = 本队偶像",
      summary: missing ? h("span", { class: "dot-text tone-warning small" }, h("span", { class: "dot", "aria-hidden": "true" }), `缺 ${missing} 项`)
        : h("span", { class: "muted small", text: "目标都有书" }),
      children: h("div", {},

      h("div", { class: "kh-map-wrap", dataset: { scrollKey: "knowhow-map" } }, table)) });
  }
}
customElements.define("sc-knowhow-panel", KnowhowPanel);

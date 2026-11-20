// 主页 tab. 2026-10-09 redesign (user: 「主页这种tab最好一页获得所有信息，
// 日常周常等，从玩家用户角度分析需要看什么」): one screen, no 2 × 2 tile grid, read top to bottom like a player's morning check:
//   1. 今日 hero: done / total with a meter, the reset countdown, the last run, and the one primary action 「开始日常」.
//   2. 需要你处理 (only when something needs the user): uncertain / failed results, spends to check, low-confidence cards.
//   1b. 免费ガシャ (only when the passive observer read the 「無料」 tag on the Home ガシャ button today; read-only, the tool
//       never opens or draws the gacha; 2026-10-12 replaces the manual 免费十连 tick).
//   3. 今日清单: the daily checklist as a compact two-column grid.
//   4. 资源: Fes 参加权 · 体力 · マニー · ジュエル in one strip (same as the 资源 summary; 未观测 when unseen, never 0).
//   5. 本周与进度: Produce 周常 n / 3, 每日任务达成, 营业.
// Actions: switch tabs and 「开始日常」 = the 任务 tab's own 开始 (actions.startRun, same blockers,
// same read-only run; the host still enforces the input policy). Nothing here spends or sends input by itself.
import { h, glyph, jaText, clear, formatTime, formatNumber, relativeTime } from "./dom.js";
import { STATUS, attentionCard, ribbon, tag, meter } from "./common.js";
import { gameDay } from "./store.js";
import { linePlate, readingItemKey } from "./game-assets.js";
import { taskIcon } from "./task-icons.js";
import { taskName, taskShown, startBlockers, RESOURCE_TASK } from "./display.js";
import { attentionItems, liveStartProblems, dailyDefinition, dailyPlanProgress, itemPlanned, itemStatus, nextRollover, resetText, runCounts, runOutcome, stoppedAt } from "./daily-panel.js";
import { holdingRows, loadHoldings, resIcon, compactNumber, autoApplyHoldings, refreshHoldings } from "./resource-panel.js";
import { cardEntries, freshness, STALE_NOTE, loadAuto, loadDeclined } from "./resource-cards.js";
import { lastReadSummary, readAge, openResourceUpdate } from "./resource-update.js";
import { ownedCards } from "./team-store.js";
import { cardDetailsStatus } from "./card-library.js";
import { noteKey, openNotes } from "./acks.js";
import { passiveGachaHint, gachaReminder, passiveMissionPages, weeklyProduceFromMissions, dailyMissionSummary, weeklyProduceNudge } from "./passive-readings.js";

const RES_STYLE_HREF = "styles/resource-inventory.css";  // resIcon wells are the 资源 tab's own component
const TAB_LABEL = { tasks: "去任务", resources: "去资源", produce: "去 Produce" };
const WEEKDAY = ["日", "一", "二", "三", "四", "五", "六"];
/** The home strip: the 资源 summary's four counters (2026-10-12: ジュエル, display only, never spent, replaces 铜训练券). */
export const HOME_COUNTERS = [
  { id: "fes", label: "Fes 参加权" }, { id: "stamina", label: "体力" }, { id: "money", label: "マニー" }, { id: "jewel", label: "ジュエル" },
];

const STATUS_SHORT = { succeeded: "完成", uncertain: "待核对", takeover: "需接管", failed: "失败", guard_refused: "未点击", skipped: "跳过" };

function ensureResourceStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${RES_STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: RES_STYLE_HREF }));
}

// The local scan files (item counts, card readings) load once per page; the newest home panel re-renders when they arrive.
let holdings = null;
let holdingsLoading = null;
let lastPanel = null;

/**
 * Newest observed value per home counter (pure): the holding row (scan / engine) or the matching reading, whichever is newer.
 * Unseen counters stay `count: null` (未观测), never 0.
 */
export function homeCounters(state, rows) {
  const byId = Object.fromEntries((rows || []).map((r) => [r.id, r]));
  return HOME_COUNTERS.map(({ id, label }) => {
    const row = byId[id];
    const reading = (state?.readings || []).find((r) => readingItemKey(r) === id && r.value !== null && r.value !== undefined && r.observed_at);
    const picks = [
      row && row.count !== null && row.count !== undefined ? { count: row.count, max: row.max ?? null, at: row.observedAt || null, source: row.source || "" } : null,
      reading ? { count: reading.value, max: reading.max ?? null, at: reading.observed_at, source: reading.source || "" } : null,
    ].filter(Boolean).sort((a, b) => (Date.parse(b.at || 0) || 0) - (Date.parse(a.at || 0) || 0));
    const pick = picks[0] || null;
    return { id, label, count: pick ? pick.count : null, max: pick?.max ?? null, at: pick?.at || null, source: pick?.source || "" };
  });
}

/** Cards whose reading is too uncertain to auto-apply (or conflicts with what the user entered): the 资源 queue size. */
export function cardsToConfirm(entries) {
  return (entries || []).filter((e) => e.status === "conflict" || (e.status === "pending" && !e.high)).length;
}

/** Weekly facts (pure): Produce 周常 trusted count / target, daily-mission reading, 营业 today. Unknown stays null. */
export function weeklyFacts(state, statuses, day, now = new Date(), pages = passiveMissionPages()) {
  const weekly = dailyDefinition(state).weekly_produce || {};
  const business = (state?.readings || []).find((r) => /^business/.test(r.key || "") && r.value !== null && r.value !== undefined) || null;
  // 2026-10-23: the weekly/daily mission pages read by MissionStatus (passive readings) give this week's Produce count and
  // today's mission progress; a configured trusted count still wins. Older weeks/days are not shown.
  const read = weeklyProduceFromMissions(pages?.weekly, now);
  const trusted = weekly.trusted_count ?? null;
  const daily = dailyMissionSummary(pages?.daily, now);
  return {
    produce: { done: trusted ?? read?.done ?? null, target: trusted !== null ? weekly.target ?? 3 : read?.target ?? weekly.target ?? 3,
      remaining: state?.produce?.weekly_plan?.remaining_runs ?? null, at: trusted === null ? read?.at || null : null },
    missions: daily ? { done: daily.done, total: daily.total, claimable: daily.claimable, at: daily.at,
      source: `每日任务${daily.visibleOnly ? "（可见行）" : ""}` } : null,
    business: { reading: business ? { value: business.value, max: business.max ?? null, at: business.observed_at || null } : null, status: statuses?.Business || null, day },
  };
}

function hero(state, actions, { done, total, now, rollover }) {
  const next = nextRollover(now, rollover.hour ?? 5);
  const run = state.runs?.[0] || null;
  const outcome = run ? runOutcome(run) : null;
  const meta = outcome ? STATUS[outcome] || STATUS.pending : null;
  const enabled = state.profile.tasks.filter((t) => t.enabled && taskShown(state, t.name));
  const blockers = startBlockers(state);
  const live = liveStartProblems(state);
  const running = state.run?.active;
  const jst = new Date(now.getTime() + 9 * 3600 * 1000);
  const dateText = `${jst.getUTCMonth() + 1}/${jst.getUTCDate()}（${WEEKDAY[jst.getUTCDay()]}）`;
  const stopped = run && !running ? stoppedAt(run) : null;
  const start = running
    ? h("button", { class: "btn btn-primary home-start", type: "button", dataset: { focusKey: "home-start" }, onclick: () => actions.setTab("live") },  // 「运行中」
      glyph("play", { size: 16 }), "运行中 · 查看")
    : h("button", { class: "btn btn-primary home-start", type: "button", dataset: { focusKey: "home-start" },
      disabled: !enabled.length || blockers.length > 0 || live.length > 0,
      title: [...live, ...blockers.map((b) => `${taskName(state, b.name)}：${b.errors[0]}`)].join("\n") || (enabled.length ? `运行当前方案的 ${enabled.length} 个任务` : "未勾选任务"),
      onclick: () => { actions.startRun(); actions.setTab("tasks"); } },
    glyph("play", { size: 16 }), "开始日常");
  return h("section", { class: "home-hero", "aria-label": "今日" },
    h("div", { class: "home-hero-top" },
      linePlate("sparkle", { size: 22, tone: "violet", cls: "is-round" }),
      h("span", { class: "home-hero-title" }, "今日方案"),
      h("span", { class: "home-hero-date num", text: dateText, title: `游戏日 ${gameDay(now)}（日本时间 ${String(rollover.hour ?? 5).padStart(2, "0")}:00 换日）` }),
      // 「重置」 is the 任务 tab's restore-the-plan button: the daily reset is 刷新 here
      h("span", { class: "home-reset", title: `下次刷新：${formatTime(next.toISOString())}（本机时间）${rollover.verified ? "" : "（未核实）"}` },
        glyph("clock", { size: 14 }), h("span", { class: "num", text: resetText(now, next) }))),
    h("div", { class: "home-hero-progress" },
      h("span", { class: "home-count num" }, String(done), h("span", { class: "home-of", text: ` / ${total}` })),
      h("span", { class: "home-meter-col" }, meter(done, total))),
    h("div", { class: "home-hero-actions" },
      start,
      h("button", { class: "btn home-adjust", type: "button", dataset: { focusKey: "home-adjust" }, onclick: () => actions.setTab("tasks") }, "调整任务"),
      !running && live.length ? h("span", { class: "home-start-block", role: "status" }, jaText(live[0])) : null),
    run ? h("div", { class: "home-last", title: `上次运行 ${formatTime(run.started_at)} · ${runCounts(run).join(" · ") || "无任务"}` },
      h("span", { class: `dot-text tone-${meta.tone}` },
        h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text: `${meta.label}${stopped ? ` · 停在 ${taskName(state, stopped.name)}` : ""}` })),
      run.started_at ? h("span", { class: "muted", text: relativeTime(run.started_at, now.getTime()) }) : null) : null);
}

function attentionView(attention, actions) {
  return attentionCard({
    title: `待处理 ${attention.length} 项`, label: "待处理",
    // a result to check gets 已核对 right here (the same mark as 任务 / 资源); a setting opens that task. Every other
    // line has ×: a task result closes like 已核对, any other note until its text changes.
    items: attention.map((item) => ({ text: item.text, tone: item.tone,
      action: item.ack ? { label: "已核对", focusKey: `ack-${item.key}`, title: "已在游戏里核对：隐藏这条提醒", onclick: () => actions.ackStatus(item.ack) }
        : item.task ? { label: "去修改", focusKey: `goto-${item.key}`, onclick: () => actions.openTask(item.task) }
          : item.tab ? { label: TAB_LABEL[item.tab] || "查看", focusKey: `goto-${item.key}`, onclick: () => actions.setTab(item.tab) } : null,
      close: item.ack ? null : { focusKey: `close-${item.key}`, title: "关闭这条提醒",
        onclick: () => (item.key.startsWith("task:") ? actions.ackStatus(item.key.slice(5)) : actions.closeNote(noteKey(item))) } })),
    moreText: attention.length > 3 ? `+${attention.length - 3}` : null,
  });
}

// 启动 and 只读取资源 only reach the home page; a run with any other task means the daily tasks have run once.
const HOME_CHECK_TASKS = ["Startup", RESOURCE_TASK];

/**
 * 第一次使用 (until the daily tasks have run once): the few things a new player must do. First-visit help dialogs are
 * closed by Maa (SC.Popup.AnyHelp), so there is no tutorial step. `done` only where data
 * already says so: a run's 启动 succeeded, so Maa reached the home page; a connected game on the title page does not
 * count. The game must still be operable; a training card is chosen. 检查 runs 只读取资源 (启动 + one
 * home read).
 */
export function firstRunSteps(state) {
  const runs = state.runs || [];
  if (state.run?.active || runs.some((r) => (r.tasks || []).some((t) => !HOME_CHECK_TASKS.includes(t.name) && t.status !== "skipped"))) return [];
  const training = state.profile?.tasks?.find((t) => t.name === "Training" && t.enabled && !(state.profile.removed_tasks || []).includes("Training"));
  const host = state.host || {};
  const home = runs.some((r) => (r.tasks || []).some((t) => t.name === "Startup" && t.status === "succeeded"));
  return [
    { key: "login", text: "登录游戏，停在首页", run: RESOURCE_TASK, done: home && host.connected === true && !host.host_guard_reason },
    training ? { key: "training", text: "选择训练用 S 卡（默认列表第一张）", tab: "tasks", task: "Training", done: Boolean(training.option?.["训练卡"]) } : null,
    { key: "start", text: "点「开始日常」", done: false },
  ].filter(Boolean);
}

/**
 * The line under the steps: 自动挖矿 needs the card details; gone once they are on this PC, a progress
 * line while they download.
 */
export function firstRunTip(state) {
  const st = cardDetailsStatus(state.host);
  if (st.ready || !state.host?.card_library?.supported) return null;
  return st.busy
    ? { text: st.phase === "cards" && st.total ? `卡资料下载中 ${st.updated} / ${st.total} · 约 ${st.minutes} 分钟` : "卡资料下载中…", busy: true }
    : { text: `自动挖矿需要卡资料，首次约 ${st.minutes} 分钟`, busy: false };
}

/** Numbered steps in a neutral card (not a problem list): a ring with the number, a green check once the data says it is done. */
export function firstRunView(steps, actions, tip = null) {
  const finished = steps.filter((step) => step.done).length;
  return h("section", { class: "first-run", "aria-label": "第一次使用" },
    h("div", { class: "first-run-head" }, h("span", { class: "first-run-title", text: "第一次使用" }), tag(`${finished}/${steps.length}`)),
    h("ol", { class: "first-run-steps" }, steps.map((step, i) => h("li", { class: `first-run-step${step.done ? " is-done" : ""}`, dataset: { step: step.key } },
      h("span", { class: "first-run-num num", role: "img", "aria-label": step.done ? "已完成" : `第 ${i + 1} 步` }, step.done ? glyph("check", { size: 12 }) : String(i + 1)),
      h("span", { class: "first-run-text" }, jaText(step.text)),
      !step.done && step.run ? h("button", { class: "btn btn-small btn-text", type: "button", title: "回到首页读一次资源，不消耗",
        dataset: { focusKey: `first-${step.key}` }, onclick: () => actions.startRun(step.run) }, "检查")
        : !step.done && step.task ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `first-${step.key}` },
          onclick: () => actions.openTask(step.task) }, TAB_LABEL[step.tab]) : null))),
    tip ? h("div", { class: "first-run-tip", role: "status", dataset: { cardDetails: tip.busy ? "busy" : "missing" } },
      h("span", { class: "first-run-text muted", text: tip.text }),
      tip.busy ? null : h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "first-card-details" },
        onclick: () => actions.cardLibraryUpdate?.() }, "下载")) : null);
}

function checkCell(state, item, statuses) {
  const st = "status" in item ? item.status : itemStatus(item, statuses);
  const planned = itemPlanned(item, state.profile);
  const done = st === "succeeded";
  const tone = st ? STATUS[st]?.tone || "neutral" : null;
  const text = item.text || (st ? STATUS_SHORT[st] || STATUS[st]?.label || st : planned ? "未运行" : "未勾选");
  const idle = !st && planned;   // ticked, not run yet: an empty ring says it (7 × 「未运行」 was noise); 未勾选 keeps its word
  return h("li", { class: `home-check${done ? " is-done" : ""}${!st && !planned ? " is-off" : ""}`, title: `${item.label}：${st ? STATUS[st]?.label || st : text}` },
    item.tasks?.[0] ? taskIcon(state, item.tasks[0], { size: 24 }) : null,
    h("span", { class: "home-check-label" }, jaText(item.label.replace(/（.*?）/g, ""))),
    // The text (e.g. 已读取) sits beside the 18 px check, not inside it (it overflowed the circle).
    done ? h("span", { class: "home-check-state" }, h("span", { class: "home-check-done", "aria-label": "完成" }, glyph("check", { size: 12 })), item.text ? text : null)
      : idle && !item.text ? h("span", { class: "home-check-state" }, h("span", { class: "home-check-ring", role: "img", "aria-label": text }))
      : h("span", { class: `home-check-state${tone ? ` tone-${tone}` : ""}` }, tone && st !== "skipped" ? h("span", { class: "dot", "aria-hidden": "true" }) : null, text));
}

function gachaBanner(reminder) {
  return h("section", { class: "home-gacha", role: "status", "aria-label": "免费ガシャ", title: `${formatTime(reminder.at).slice(6, 11)}` },
  h("span", { class: "line-plate tone-pink home-gacha-badge", "aria-hidden": "true" }, glyph("capsule", { size: 18 })),
  h("span", { class: "home-gacha-title" }, "免费ガシャ", h("span", { class: "home-gacha-tag" }, jaText(reminder.tag))));
}

function counterCell(state, c, now) {
  const seen = c.count !== null && c.count !== undefined;
  const f = seen ? freshness(c.at, now.getTime()) : null;
  const value = seen ? (c.id === "money" ? compactNumber(c.count) : formatNumber(c.count)) : "—";   // the section head says 尚未读取 once
  return h("li", { class: `home-counter${seen ? "" : " is-missing"}`, dataset: { item: c.id },
    title: seen ? `${c.label} ${formatNumber(c.count)}${c.max != null ? ` / ${formatNumber(c.max)}` : ""}\n${formatTime(c.at)}（${readAge(c.at, now.getTime()) || "时间未知"}）· ${c.source || "来源未知"}${f?.stale ? STALE_NOTE : ""}`
      : `${c.label}：尚未读取，运行日常时读取` },
  resIcon(state, c.id, { size: 26 }),
  h("span", { class: "home-counter-main" },
    h("span", { class: "home-counter-label" }, jaText(c.label)),
    // Freshness is shown once above the section (home-lastread); each resource keeps its read time in the tooltip.
    h("span", { class: "home-counter-value num" }, value,
      seen && c.max != null ? h("span", { class: "home-of", text: `/${formatNumber(c.max)}` }) : null)));
}

function weekCell(lead, label, value, { done = null, total = null, title = null, missing = false, key = "" } = {}) {
  return h("li", { class: `home-week${missing ? " is-missing" : ""}`, title, dataset: { week: key } },
    h("span", { class: "home-week-head" }, lead, h("span", { class: "home-week-label" }, jaText(label))),
    h("span", { class: "home-week-value num" }, typeof value === "string" ? jaText(value) : value),
    done !== null && total ? meter(done, total) : h("span", { class: "meter is-empty", "aria-hidden": "true" }));
}

function weekSection(state, facts, now) {
  const p = facts.produce;
  const m = facts.missions;
  const b = facts.business;
  const bText = b.reading ? `${formatNumber(b.reading.value)}${b.reading.max != null ? ` / ${formatNumber(b.reading.max)}` : ""}`
    : b.status ? STATUS[b.status]?.label || b.status : "—";
  return h("ul", { class: "home-weeks" },
    weekCell(taskIcon(state, "AutoProduce", { size: 20 }), "Produce 周常", p.done === null ? "—" : `${p.done} / ${p.target}`,
      { key: "produce", done: p.done, total: p.target, missing: p.done === null,
        title: p.done === null ? "尚未读取：运行日常后读取本周完成数" : `本周还需 ${Math.max(0, p.target - p.done)} 次${p.at ? ` · 读取于 ${formatTime(p.at)}` : ""}` }),
    weekCell(taskIcon(state, "MissionStatus", { size: 20 }), "任务达成", m ? `${m.done}${m.total != null ? ` / ${m.total}` : ""}${m.claimable ? ` · ${m.claimable} 待领` : ""}` : "—",
      { key: "missions", done: m ? m.done : null, total: m?.total ?? null, missing: !m, title: m ? `${formatTime(m.at)} · ${m.source}（${relativeTime(m.at, now.getTime())}）` : "尚未读取：运行日常后显示" }),
    weekCell(taskIcon(state, "Business", { size: 20 }), "营业派遣", bText,
      { key: "business", missing: !b.reading && !b.status, title: b.reading ? `${formatTime(b.reading.at)}` : b.status ? "今日「营业」任务的结果；槽位详情见「日志」" : "尚未读取：运行「营业」后显示收取与 12 小时再营业" }));
}

class HomePanel extends HTMLElement {
  set data(value) {
    this._data = value;
    lastPanel = this;
    if (!holdings && !holdingsLoading) {
      holdingsLoading = loadHoldings().then((loaded) => {
        holdings = loaded;
        // High-confidence readings are applied wherever the files are first loaded (also here: the home tab is the default one).
        if (this._data) autoApplyHoldings(this._data.state, this._data.actions, loaded, () => lastPanel?.isConnected && lastPanel.render());
      }).catch(() => { holdings = { itemScan: null }; })
        .finally(() => { if (lastPanel?.isConnected) lastPanel.render(); });
    }
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    ensureResourceStyles();
    clear(this);
    this.className = "panel home-panel";
    const now = state.now ? new Date(state.now) : new Date();
    const day = gameDay(now);
    const daily = dailyDefinition(state);
    const progress = dailyPlanProgress(state, now);
    const { statuses, items, done } = progress;
    const attention = attentionItems(state, day);
    if (holdings?.cardScan || holdings?.levelScan) {
      let count = 0;
      try {
        count = cardsToConfirm(cardEntries(ownedCards(state), { cardScan: holdings.cardScan, levelScan: holdings.levelScan, declined: loadDeclined(), auto: loadAuto().cards }));
      } catch { count = 0; }
      if (count) attention.push({ key: "cards", tone: "accent", tab: "resources", text: `${count} 张卡待确认` });
    }
    const counters = homeCounters(state, holdingRows(state, holdings?.itemScan || null));
    const lastRead = lastReadSummary(counters, now.getTime());
    const facts = weeklyFacts(state, statuses, day, now);
    const nudge = weeklyProduceNudge(facts.produce, now);
    if (nudge) attention.push({ key: "weekly-produce", tone: "accent", tab: "produce", text: nudge });
    const shown = openNotes(state, attention, day);
    const gacha = gachaReminder(passiveGachaHint(), now);
    const firstRun = firstRunSteps(state);

    this.append(h("div", { class: "panel-scroll home-scroll", dataset: { scrollKey: "home" } },
      hero(state, actions, { done, total: items.length, now, rollover: daily.rollover || {} }),
      gacha ? gachaBanner(gacha) : null,
      shown.length ? attentionView(shown, actions) : null,
      firstRun.length ? firstRunView(firstRun, actions, firstRunTip(state)) : null,
      ribbon("已选任务", tag(`${done}/${items.length}`)),
      h("ul", { class: "home-checks" }, items.map((item) => checkCell(state, item, statuses))),
      ribbon("资源", h("span", { class: "home-res-tools" },
        h("span", { class: `home-lastread${lastRead.stale || lastRead.older ? " is-stale" : ""}${lastRead.at ? "" : " is-missing"}`,
          title: lastRead.at ? formatTime(lastRead.at) : null }, lastRead.text),
        h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "home-update-res" }, onclick: () => openResourceUpdate({ counters, now: now.getTime(), onGo: (tab) => actions.setTab(tab),
          onRead: () => actions.startRun(RESOURCE_TASK), readBlocked: state.run?.active ? "正在运行" : liveStartProblems(state)[0] || "",
          onReload: () => refreshHoldings(state, actions, () => lastPanel?.isConnected && lastPanel.render()).catch(() => {}) }) }, "更新资源"),
        h("button", { class: "btn btn-small btn-text", type: "button", title: "全部道具与卡牌", "aria-label": "全部道具与卡牌", dataset: { focusKey: "goto-resources" },
          onclick: () => actions.setTab("resources") }, "全部"))),
      h("ul", { class: "home-counters" }, counters.map((c) => counterCell(state, c, now))),
      ribbon("本周与进度"),
      weekSection(state, facts, now)));
  }
}
customElements.define("sc-home-panel", HomePanel);

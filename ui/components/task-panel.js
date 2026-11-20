// Task tab: preset, compact task rows (check, drag reorder, short name, run status),
// inline settings, and a run bar with a single 开始 / 停止. Developer-only tasks/options and offline replay appear
// only with 设置 → 开发者选项. No per-run spend dialog: spending follows each task's own spend option.
// the former 日志 tab lives here as a collapsible 日志 / 运行记录 drawer between the list and the run bar.
// 2026-10-08: a closed row says in one line what is set (changed options or 「默认设置」);
// an open row groups its options into fold cards (the option `heading`s, else 常用 / 消耗 / 高级; annotations
// `layout: "flat"` keeps one plain list in task order, e.g. Match / Grade, 2026-10-09); results that need
// the user (不确定, 守卫拒绝, 失败, 需接管, settings that block 开始) are collected in one attention card on top.
import { h, icon, jaText, clear } from "./dom.js";
import { statusPill, STATUS_HINT, details, foldSection, attentionCard } from "./common.js";
import { choiceChips } from "./choice.js";
import { optionField, groupField } from "./option-field.js";
import { groupSummary } from "./card-picker.js";
import { trainingCardWarnings } from "./team-picker.js";
import { cardArt } from "./game-assets.js";
import { ownedCards } from "./team-store.js";
import { profileToRunConfig, profileSpendFlags } from "./store.js";
import { devMode, taskName, taskVisible, taskShown, visibleOptions, optionRows, optionParent, optionNote, groupNote, requireAnyWarning, spendOn, optionLabel, caseLabel, taskErrors, startBlockers, fesSummary, taskNote, costLine, shopReserve, lastMoney } from "./display.js";
import { gameDay } from "./store.js";
import { ackedTasks } from "./acks.js";
import { taskIcon } from "./task-icons.js";
import { addTaskEntry, undoBar, openContextMenu, rowMenuItems } from "./task-add.js";
import { logStrip } from "./log-panel.js";
import { outcomeText } from "./outcomes.js";
import { todayStatuses, liveProblems, liveStartProblems } from "./daily-panel.js";

export { liveProblems, liveStartProblems };   // moved to daily-panel.js (主页 uses them without the task page element)
import { saveIndicator, ensureStyles as ensurePlanStyles } from "./plans.js";
import { SELL_SWITCH, sellPromptDue } from "./knowhow-scan.js";

// Only the card the user just expanded animates; later re-renders must not replay the reveal.
let revealOnce = null;

export function liveConditions(state) {
  const { policy = {}, host = {} } = state;
  return [
    { ok: policy.live_input_enabled === true, text: "项目策略允许实机输入" },
    { ok: policy.risk_reference_resolvable === true, text: "风险接受记录可解析" },
    { ok: host.pipeline_input_enabled === true, text: "宿主允许 Pipeline 输入" },
    { ok: !host.host_guard_reason, text: "宿主守卫无拒绝" },
    { ok: host.recognition_ready !== false, text: "识别资源已安装" },
  ];
}

function mutualConflicts(state) {
  const enabled = new Set(state.profile.tasks.filter((t) => t.enabled && taskShown(state, t.name)).map((t) => t.name));
  return (state.annotations.mutual_exclusion || []).filter((rule) => rule.tasks.every((n) => enabled.has(n)));
}

const isChanged = (state, entry, key) => (entry.option?.[key] ?? state.schema.options[key].default_case) !== state.schema.options[key].default_case;

const SUMMARY_OPTIONS = {
  Shop: ["商店跳过券", "商店金训练券", "商店余额保留"],
  Training: ["训练卡", "训练券", "训练数量"],
  MatchSkip: ["Match关卡", "Match次数", "Match参加权保留", "Match只采样"],
  GradeFes: ["Grade模式", "Grade难度", "Grade每日次数", "Grade自动演出"],
  MissionsClaim: ["任务奖励领取"],
  Business: ["营业收取后", "营业模式", "营业一括派出"],
  CollaboFes: ["Collabo会场", "Collabo局数", "Collabo编队", "Collabo等级"],
};

/**
 * Row summary (pure): key settings stay visible even at defaults. `title` contains the full summary,
 * `changed` counts changed visible options, and `spend` indicates consumption.
 */
export function rowSummary(state, task, entry) {
  const keys = visibleOptions(state, task, entry);
  const changed = keys.filter((key) => isChanged(state, entry, key));
  const essential = (SUMMARY_OPTIONS[task.name] || []).filter((key) => keys.includes(key));
  const summaryValue = (key) => {
    const value = entry.option?.[key] ?? state.schema.options[key].default_case;
    return key === "训练卡" && !value ? "S 卡未选" : caseLabel(state, key, value);
  };
  const title = [...new Set([...essential, ...changed])].map((key) => `${optionLabel(state, key)}：${summaryValue(key)}`).join("\n");
  const parts = [];
  for (const row of optionRows(state, task, entry)) {
    if (row.group) {
      if (!row.keys.some((k) => isChanged(state, entry, k))) continue;
      const note = groupNote(state, row.group);
      const values = Object.fromEntries(row.keys.map((k) => [k, entry.option?.[k] ?? state.schema.options[k].default_case]));
      parts.push(note.control === "team" ? `${note.label || row.group}已改` : `${note.label || row.group} ${groupSummary(state, row.group, row.keys, values)}`);
    } else if (essential.includes(row.key) || isChanged(state, entry, row.key)) {
      const part = compactPart(state, row.key, summaryValue(row.key), isChanged(state, entry, row.key));
      if (part) parts.push(part);
    }
  }
  // 商店: what it buys and the reserve, short enough for the narrow row (the tooltip keeps each good)
  if (task.name === "Shop") {
    const shop = shopReserve(state, entry);
    parts.splice(0, parts.length, `今日限定 ${shop.on} 件`, shop.reserve ? `保留 ${shop.label}` : "不保留余额");
  }
  // Values only; a task without settings shows nothing
  const text = parts.join(" · ");
  return { changed: changed.length, title, text, spend: task.ui.spend_flag ? spendOn(state, task, { ...entry, enabled: true }) : false };
}

const COUNT_KEYS = new Set(["训练数量", "Grade每日次数", "Match次数", "Collabo局数"]);
const ON = new Set(["开启", "开", "Yes"]);
const OFF = new Set(["关闭", "关", "No", "不保留", "0"]);

/** One summary value: counts as ×n, a switch by its label when on (nothing when off), a value alone otherwise. */
function compactPart(state, key, value, changed) {
  const label = optionLabel(state, key);
  if (COUNT_KEYS.has(key)) return /^\d+$/.test(value) ? `×${value}` : value;
  if (ON.has(value)) return label;
  if (OFF.has(value)) return changed ? `${label} ${value}` : null;
  if (/^\d+$/.test(value)) return `${label} ${value}`;
  return key === "营业收取后" && value === "再营业" ? "再营业 12 小时" : value;
}

const GROUP_LABEL = { common: "常用", spend: "消耗", advanced: "高级" };
const FES_COUNT_OPTION = { MatchSkip: "Match次数", GradeFes: "Grade每日次数" };

/**
 * Option rows grouped for fold cards (pure): by the annotation `heading`s when the task has any (W.I.N.G.: 编队 / 目标 /
 * 培养 / …), else 常用 / 消耗 (the task's spend `budget_options`) / 高级 (developer options). A child option stays with its
 * parent. Returns [] when one plain list is clearer (a single non-empty group and no headings).
 */
export function optionGroups(state, task, entry) {
  if (taskNote(state, task.name).layout === "flat") return [];
  const rows = optionRows(state, task, entry);
  const keyOf = (row) => row.key || row.keys[0];
  const headed = rows.some((row) => optionNote(state, keyOf(row)).heading);
  // 2026-10-11 (UI audit #17): one rule — only a task with annotation headings (W.I.N.G.) uses fold cards; every other
  // task lists its options flat in task order, without 常用 / 消耗 / 高级 subheads.
  if (!headed) return [];
  const groups = [];
  const byId = new Map();
  const put = (id, label, row) => {
    if (!byId.has(id)) { const g = { id, label, rows: [] }; byId.set(id, g); groups.push(g); }
    byId.get(id).rows.push(row);
  };
  if (headed) {
    let current = { id: "common", label: GROUP_LABEL.common };
    for (const row of rows) {
      const heading = optionNote(state, keyOf(row)).heading;
      if (heading) current = { id: `h-${heading}`, label: heading };
      put(current.id, current.label, row);
    }
    return groups;
  }
  const budget = new Set(taskNote(state, task.name).spend?.budget_options || []);
  const placed = new Map();
  for (const row of rows) {
    const key = keyOf(row);
    const parent = optionParent(state, key);
    const id = parent && placed.has(parent) ? placed.get(parent)
      : optionNote(state, key).dev ? "advanced" : (row.keys || [key]).some((k) => budget.has(k)) ? "spend" : "common";
    (row.keys || [key]).forEach((k) => placed.set(k, id));
    put(id, GROUP_LABEL[id], row);
  }
  const order = Object.keys(GROUP_LABEL);
  groups.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  return groups.length > 1 ? groups : [];
}

/** Items for the attention card on top of the list: results that need the user and settings that block 开始. */
export function taskAttention(state) {
  const out = [];
  // today's run history too (主页 sends 「今日结果不确定」 here), unless it was marked 已核对 (acks.js)
  const day = gameDay(state.now ? new Date(state.now) : new Date());
  const today = todayStatuses(state.runs || [], day);
  const acked = ackedTasks(state, day);
  for (const entry of state.profile.tasks) {
    if (!taskShown(state, entry.name)) continue;
    const st = state.taskStatus[entry.name];
    if (st && ["uncertain", "guard_refused", "takeover", "failed"].includes(st.status)) {
      out.push({ kind: "status", name: entry.name, status: st.status, text: `${taskName(state, entry.name)}：${outcomeText(st.reason, entry.name) || STATUS_HINT[st.status] || ""}` });
    } else if (!st && ["uncertain", "takeover"].includes(today[entry.name]) && !acked.includes(entry.name)) {
      out.push({ kind: "status", name: entry.name, status: today[entry.name], text: `${taskName(state, entry.name)}：今日结果${today[entry.name] === "uncertain" ? "不确定" : "需接管"}，需在游戏中核对` });
    }
  }
  for (const blocker of startBlockers(state)) {
    out.push({ kind: "blocker", name: blocker.name, text: `${taskName(state, blocker.name)}：${blocker.errors[0]}` });
  }
  return out;
}

const ATTENTION_TONE = { uncertain: "uncertain", guard_refused: "warning", takeover: "accent", failed: "danger" };

function attentionView(state, actions) {
  const items = taskAttention(state);
  return attentionCard({
    title: `${items.length} 件事需要你处理`, label: "需要处理",
    items: items.map((item) => ({
      text: item.text, tone: item.kind === "blocker" ? "danger" : ATTENTION_TONE[item.status],
      // 去修改 opens the task's settings: picked in the wide list, expanded in one column
      action: item.kind === "blocker"
        ? ((state.wide ? state.taskSelected === item.name : state.expanded.includes(item.name)) ? null
          : { label: "去修改", focusKey: `fix-${item.name}`, title: "打开这个任务的设置", onclick: () => actions.openTask(item.name) })
        : ["uncertain", "takeover"].includes(item.status)
          ? { label: "已核对", focusKey: `ack-${item.name}`, title: "已在游戏里核对：隐藏这条提醒", onclick: () => actions.ackStatus(item.name) }
          : state.wide ? null : { label: "看日志", focusKey: `log-${item.name}`, onclick: () => actions.openLogs() },
    })),
  });
}

/**
 * Option fields of one task (fold cards when the task has annotation headings); shared with the 「Produce」 tab.
 * `openFirst` = how many heading folds start open; `summarize(label)` gives a fold's one-line value summary (shown while
 * the fold is closed; the changed count shows while it is open). Without them the 任务 tab behaviour is unchanged.
 */
export function optionBody(state, actions, task, entry, running, { openFirst = 2, summarize = null } = {}) {
  const { schema } = state;
  const build = (row, stripHeading) => {
    if (row.group) {
      const values = Object.fromEntries(row.keys.map((k) => [k, entry.option?.[k] ?? schema.options[k].default_case]));
      return groupField({ state, group: row.group, keys: row.keys, values, disabled: running, focusKey: `grp-${task.name}-${row.group}`, actions,
        onChange: (changes) => actions.setOptions(task.name, changes) });
    }
    const spec = schema.options[row.key];
    const field = optionField({
      state, key: row.key, value: entry.option?.[row.key] ?? spec.default_case, disabled: running, focusKey: `opt-${task.name}-${row.key}`, actions,
      siblings: entry.option,
      onChange: (value) => {
        const due = sellPromptDue(entry.option, row.key, value, schema.options[SELL_SWITCH]?.default_case);
        actions.setOption(task.name, row.key, value);
        if (due) actions.knowhowSellCheck?.();      // 2026-10-25: 卖书 judges new books against the library
      },
    });
    if (optionParent(state, row.key)) field.classList.add("is-child");
    // 2026-10-11 (UI audit #17): the shared フェス参加権 split is a hint under the count, not a separate result-like row.
    if (FES_COUNT_OPTION[task.name] === row.key) {
      const fes = fesSummary(state, task, entry);
      if (fes) field.append(h("p", { class: "field-hint fes-hint" }, jaText(fes)));
    }
    if (stripHeading) { field.querySelector(".field-heading")?.remove(); field.classList.remove("has-heading"); }
    return field;
  };
  const groups = optionGroups(state, task, entry);
  if (!groups.length) return optionRows(state, task, entry).map((row) => build(row, false));
  const spendNote = taskNote(state, task.name).spend;
  return groups.map((g, i) => {
    const keys = g.rows.flatMap((r) => r.keys || [r.key]);
    // developer-only keys (e.g. the S/Guest a 推荐编队 adopted) are not the player's edits: not counted as 已改
    const changed = keys.filter((k) => isChanged(state, entry, k) && (devMode(state) || !optionNote(state, k)?.dev)).length;
    const values = summarize ? summarize(g.label) : "";
    // 2026-10-24: only a change is worth a word on the fold head (「默认」 on every closed fold was noise)
    const state_ = changed ? h("span", { class: "small changed fold-state", text: `已改 ${changed} 项` }) : null;
    const summary = g.id === "spend" && spendNote
      ? h("span", { class: "muted small", title: `消耗：${spendNote.resource}（以这些设置为上限）` }, jaText(`${spendNote.resource} · 上限`))
      : values
        ? [h("span", { class: "fold-values small", title: values }, jaText(values)),
          changed ? h("span", { class: "fold-changed", role: "img", title: `已改 ${changed} 项`, "aria-label": `已改 ${changed} 项` }) : null, state_]
        : state_;
    const open = g.id === "advanced" ? false : g.id.startsWith("h-") ? i < openFirst : true;
    return foldSection({ key: `task-${task.name}-${g.id}`, title: g.label, defaultOpen: open, summary,
      children: h("div", { class: "fold-fields" }, g.rows.map((row) => build(row, true))) });
  });
}

/**
 * One task row. `pick` (双栏 list + detail): the row selects the task for the detail column instead of
 * expanding its settings in place; the name no longer toggles the checkbox.
 */
function taskRow(state, actions, entry, index, neighbours, conflictNames, pick = null) {
  const { schema } = state;
  const task = schema.tasks.find((t) => t.name === entry.name);
  const name = taskName(state, task.name);
  const status = state.taskStatus[task.name];
  const running = state.run.active;
  const expanded = !pick && state.expanded.includes(task.name);
  const conflict = entry.enabled && conflictNames.has(task.name);
  const requireWarning = requireAnyWarning(state, task, entry);

  const row = h("li", {
    class: `task-row${entry.enabled ? " is-enabled" : ""}${expanded ? " is-expanded" : ""}${pick?.selected ? " is-selected" : ""}${conflict || requireWarning ? " has-conflict" : ""}`,
    dataset: { name: task.name, index: String(index) },
  });

  const grip = h("button", {
    class: "icon-btn grip", type: "button", "aria-label": `移动「${name}」`, title: "拖动排序",
    disabled: running, dataset: { focusKey: `grip-${task.name}` },
    onpointerdown: () => { if (!running) row.draggable = true; },
    onkeydown: (event) => {
      const target = event.key === "ArrowUp" ? neighbours.prev : event.key === "ArrowDown" ? neighbours.next : null;
      if (target === null) return;
      event.preventDefault();
      actions.moveTask(index, target);
    },
  }, icon("grip", { size: 16 }));
  row.addEventListener("dragstart", (event) => {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(index));
    row.classList.add("is-dragging");
  });
  row.addEventListener("dragend", () => { row.draggable = false; row.classList.remove("is-dragging"); });
  row.addEventListener("dragover", (event) => {
    if (running) return;
    event.preventDefault();
    const rect = row.getBoundingClientRect();
    const after = event.clientY > rect.top + rect.height / 2;
    row.classList.toggle("drop-before", !after);
    row.classList.toggle("drop-after", after);
  });
  row.addEventListener("dragleave", () => row.classList.remove("drop-before", "drop-after"));
  // row context menu (right-click or the menu key on a focused control): move / settings / 移出方案.
  row.addEventListener("contextmenu", (event) => {
    if (event.target.closest(".task-body")) return;  // keep the native menu inside settings fields
    event.preventDefault();
    const keyboard = event.button !== 2 && event.clientX === 0 && event.clientY === 0;
    openContextMenu({
      x: keyboard ? undefined : event.clientX, y: keyboard ? undefined : event.clientY, anchor: row.querySelector(".task-head"),
      label: `「${name}」操作`, items: rowMenuItems(state, actions, { name: task.name, index, prev: neighbours.prev, next: neighbours.next }),
    });
  });
  row.addEventListener("drop", (event) => {
    event.preventDefault();
    const after = row.classList.contains("drop-after");
    row.classList.remove("drop-before", "drop-after");
    const from = Number(event.dataTransfer.getData("text/plain"));
    if (Number.isNaN(from)) return;
    let to = index + (after ? 1 : 0);
    if (from < to) to -= 1;
    actions.moveTask(from, to);
  });

  const checkbox = h("input", {
    type: "checkbox", class: "checkbox", id: `chk-${task.name}`, disabled: running,
    "aria-label": name, dataset: { focusKey: `chk-${task.name}` },
    onchange: (e) => actions.setEnabled(task.name, e.target.checked),
  });
  checkbox.checked = entry.enabled;

  const title = pick
    ? h("button", { class: "task-title task-pick", type: "button", "aria-current": pick.selected ? "true" : null, "aria-controls": "task-detail",
      title: devMode(state) ? task.label : null, dataset: { focusKey: `pick-${task.name}` }, onclick: pick.onSelect,
      onkeydown: (event) => {   // up/down pick the neighbouring task (the grip moves rows)
        const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
        const buttons = step ? [...(event.currentTarget.closest(".task-list")?.querySelectorAll(".task-pick") || [])] : [];
        const next = buttons[buttons.indexOf(event.currentTarget) + step];
        if (!next) return;
        event.preventDefault();
        next.click();
      } }, jaText(name))
    : h("label", { class: "task-title", for: `chk-${task.name}`, title: devMode(state) ? task.label : null }, jaText(name));
  const hasSettings = optionRows(state, task, entry).length > 0 || devMode(state);
  // 2026-10-11 (UI audit #18): no chevron for a task without settings; none in the 双栏 list (the detail column shows them).
  const toggle = hasSettings && !pick ? h("button", {
    class: "icon-btn expand", type: "button", "aria-expanded": String(expanded),
    "aria-label": `${name}设置`, title: expanded ? "收起" : "设置",
    dataset: { focusKey: `exp-${task.name}` }, onclick: () => { revealOnce = expanded ? null : task.name; actions.toggleExpanded(task.name); },
  }, icon("chevron", { size: 16 })) : null;
  // UI audit #21: 「移出方案」 and the other row actions live in this menu (also right-click on the row).
  const more = h("button", {
    class: "icon-btn row-more", type: "button", "aria-haspopup": "menu", "aria-label": `「${name}」更多操作`, title: "更多",
    dataset: { focusKey: `more-${task.name}` },
    onclick: (event) => {
      const rect = event.currentTarget.getBoundingClientRect();
      openContextMenu({ x: rect.left, y: rect.bottom, anchor: row.querySelector(".task-head"), label: `「${name}」操作`,
        items: rowMenuItems(state, actions, { name: task.name, index, prev: neighbours.prev, next: neighbours.next }) });
    },
  }, icon("more", { size: 16 }));

  const summary = rowSummary(state, task, entry);
  // 2026-10-09 (user): no item / cost icon on the right of a row; only a run status takes that place.
  const tail = status?.status ? statusPill(status.status) : null;
  // One plain line under the name: what is set. A click toggles the settings (the chevron is the keyboard control).
  const sub = h("span", { class: `task-sub${summary.changed ? " is-changed" : ""}`, title: summary.title || null,
    onclick: !pick && hasSettings ? () => { revealOnce = expanded ? null : task.name; actions.toggleExpanded(task.name); } : null }, jaText(summary.text));
  row.append(h("div", { class: "task-head",
    // 双栏: a click anywhere on the row picks it (the checkbox, grip and menu keep their own actions)
    onclick: pick ? (event) => { if (!event.target.closest("input, button, .grip")) pick.onSelect(); } : null },
  grip, checkbox, taskIcon(state, task.name, { size: 24 }), h("div", { class: "task-name-col" }, title, sub), tail, more, toggle));

  if (status && status.status && !["pending", "running"].includes(status.status)) {
    row.append(h("div", { class: `task-result tone-${status.status}` },
      h("span", { class: "task-result-text" }, jaText(outcomeText(status.reason, task.name) || STATUS_HINT[status.status] || ""))));
  }
  row.append(...taskNotices(state, task, entry, conflict));

  if (expanded && hasSettings) {
    const body = taskBody(state, actions, task, entry, revealOnce === task.name);
    if (revealOnce === task.name) revealOnce = null;
    row.append(body);
  }
  return row;
}

/** Conflict / setting warnings of a ticked task: under its row, and again on top of its 双栏 settings. */
function taskNotices(state, task, entry, conflict) {
  const out = [];
  if (conflict) {
    const rule = (state.annotations.mutual_exclusion || []).find((r) => r.tasks.includes(task.name));
    out.push(h("div", { class: "task-warning" }, jaText(rule?.message || "与其他任务互斥")));
  }
  const requireWarning = requireAnyWarning(state, task, entry);
  if (requireWarning) out.push(h("div", { class: "task-warning", role: "status" }, h("span", { class: "task-result-text" }, jaText(requireWarning))));  // warns; never blocks 开始
  if (entry.enabled) for (const error of taskErrors(state, task, entry)) out.push(h("div", { class: "task-warning is-error", role: "alert" }, h("span", { class: "task-result-text" }, jaText(error))));  // blocks 开始 (startBlockers)
  // 训练卡 chosen earlier but no longer in 持有卡牌 (a re-scan or an edit removed it): said under the row; never blocks 开始
  if (task.name === "Training" && entry.enabled) {
    const value = entry.option?.["训练卡"] || "";
    for (const warning of trainingCardWarnings(value, { art: cardArt(value), owned: ownedCards(state).s || [] })) {
      if (warning.key === "unowned") out.push(h("div", { class: "task-warning", role: "status", dataset: { warning: warning.key } }, h("span", { class: "task-result-text" }, jaText(warning.text))));
    }
  }
  const shop = task.name === "Shop" && entry.enabled ? shopReserve(state, entry, lastMoney(state)) : null;
  if (shop?.short) out.push(h("div", { class: "task-warning", role: "status" }, h("span", { class: "task-result-text" }, jaText(`上次读到マニー ${shop.money.toLocaleString("ja-JP")}，低于保留 ${shop.label}：会跳过购买`))));
  return out;
}

/** A task's settings: in its row (one column) or in the 双栏 detail column. 「消耗：…」 first for a task that spends. */
function taskBody(state, actions, task, entry, reveal = false) {
  const body = h("div", { class: `task-body${reveal ? " reveal" : ""}` });
  const cost = costLine(state, task.name, entry);
  if (cost) body.append(h("p", { class: "task-cost" }, jaText(cost)));
  const shop = task.name === "Shop" ? shopReserve(state, entry, lastMoney(state)) : null;
  if (shop?.reserve && shop.money == null) body.append(h("p", { class: "task-cost-note muted small" }, jaText(`还没读到マニー余额：低于保留 ${shop.label} 时不买`)));
  const rows = optionRows(state, task, entry);
  if (!rows.length) body.append(h("p", { class: "muted small task-empty", text: "无设置" }));
  else body.append(...optionBody(state, actions, task, entry, state.run.active));
  if (devMode(state)) {
    body.append(h("p", { class: "task-facts muted small" }, jaText(`${task.name}${task.ui.spend_flag ? ` · ${task.ui.spend_flag}` : ""}`)));
  }
  return body;
}

/** 双栏 detail column: the picked task's name, what is set, and the same settings as its row would show. */
function taskDetail(state, actions, entry, conflictNames) {
  if (!entry) return h("p", { class: "muted small task-detail-empty", text: "选一个任务查看设置" });
  const task = state.schema.tasks.find((t) => t.name === entry.name);
  const summary = rowSummary(state, task, entry);
  const status = state.taskStatus[task.name];
  const notices = taskNotices(state, task, entry, entry.enabled && conflictNames.has(task.name));
  return h("section", { class: "task-detail", id: "task-detail", role: "region", "aria-label": `${taskName(state, task.name)}设置` },
    h("div", { class: "task-detail-head" }, taskIcon(state, task.name, { size: 24 }),
      h("div", { class: "task-name-col" }, h("span", { class: "task-detail-title" }, jaText(taskName(state, task.name))),
        h("span", { class: `task-sub${summary.changed ? " is-changed" : ""}`, title: summary.title || null }, jaText(summary.text))),
      status?.status ? statusPill(status.status) : null),
    notices.length ? h("div", { class: "task-detail-notices" }, notices) : null,
    taskBody(state, actions, task, entry));
}


export function liveIndicator(state) {
  const conds = liveConditions(state);
  const unmet = conds.filter((c) => !c.ok);
  const problems = liveProblems(state);
  const shown = devMode(state) ? conds : problems.map((text) => ({ ok: false, text }));
  const problemCount = devMode(state) ? unmet.length : problems.length;
  const list = h("ul", { class: "cond-list" }, shown.map((c) => h("li", { class: c.ok ? "ok" : "bad" },
    h("span", { class: "dot", "aria-hidden": "true" }), h("span", {}, jaText(c.text)))));
  // Read-only checklist of what live (实机) input would need; it never connects or enables anything. It stays open
  // across status ticks (app.js keeps <details> state by data-open-key) until the user clicks it, outside, or Esc.
  const box = h("details", { class: "live-indicator", dataset: { openKey: "live-indicator" } },
    h("summary", { title: problemCount ? `实机未就绪：${problemCount} 项待处理` : "实机就绪" },
      h("span", { class: `dot-text tone-${problemCount ? "muted" : "success"}` }, h("span", { class: "dot", "aria-hidden": "true" }),
        h("span", { text: problemCount ? "实机未就绪" : "实机就绪" }))),
    h("div", { class: "live-pop", role: "note" },
      h("p", { class: "live-pop-title", text: devMode(state) ? "实机输入条件" : problems.length ? "开始前需处理" : "游戏操作已就绪" }),
      list,
      devMode(state) ? h("p", { class: "live-pop-foot muted", text: "此处只显示状态。" }) : null));
  return box;
}

/** 开始 / 停止 for the ticked daily tasks: under the 任务 list, or at the top of the wide layout's run column. */
export function dailyRunView(state, actions) {
  const running = state.run.active;
  const visible = state.profile.tasks.filter((t) => t.enabled && taskShown(state, t.name));
  const current = running ? state.run.tasks.find((n) => state.taskStatus[n]?.status === "running") : null;
  // ANN-17: the normal 开始 runs the ticked tasks live (supervised_daily); 只识别 / 离线回放 are developer modes.
  const devRun = devMode(state) && ["offline_replay", "read_only"].includes(state.runMode) ? state.runMode : null;
  const main = running ? (state.run.stopping ? "正在停止" : current ? `运行中 · ${taskName(state, current)}` : "运行中")
    : visible.length ? `${visible.length} 个任务${devRun ? "" : " · 实机"}` : "未勾选任务";
  const replay = devRun === "offline_replay";
  // Batch 4: once a stop was accepted the button stays disabled as 「停止中…」 until the run reports finished.
  const stopping = running && state.run.stopping;
  // why 开始 is disabled, written beside it (the same on every tab of the wide layout's run column)
  const live = running ? [] : liveStartProblems(state, devRun);
  const blockers = running ? [] : startBlockers(state);
  const block = live.length ? live[0] : blockers.length ? `${blockers.length} 项设置待修改` : null;
  const primary = running
    ? h("button", { class: "btn btn-run btn-stop", type: "button", disabled: stopping, "aria-busy": stopping ? "true" : null,
      title: stopping ? "已请求停止：当前步骤结束后停止" : "当前步骤结束后停下",
      dataset: { focusKey: "run-main" }, onclick: () => actions.stopRun("cooperative") }, stopping ? "停止中…" : "停止")
    : h("button", { class: "btn btn-run btn-primary", type: "button", disabled: !visible.length || blockers.length > 0 || live.length > 0,
      title: [...live, ...blockers.map((b) => `${taskName(state, b.name)}：${b.errors[0]}`)].join("\n") || null, dataset: { focusKey: "run-main" }, onclick: () => actions.startRun() }, "开始");
  const bar = h("div", { class: "run-bar" },
    h("div", { class: "run-main" },
      h("div", { class: "run-summary" },
        h("div", { class: "run-summary-main", text: main }),
        h("div", { class: "run-summary-sub" }, liveIndicator(state), devRun ? h("span", { class: "run-tag", text: replay ? "离线回放" : "只识别" }) : null,
          block ? h("span", { class: "run-block", role: "status" }, jaText(block)) : null)),
      primary));
  if (devMode(state)) {
    const flags = profileSpendFlags(state.schema, state.profile);
    bar.append(h("div", { class: "run-advanced" },
      details("运行配置", h("pre", { class: "code", text: JSON.stringify({ config: profileToRunConfig(state.schema, state.profile), spend_flags: flags }, null, 2) }))));
  }
  return bar;
}

class TaskPanel extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  render() {
    const { state, actions } = this._data;
    clear(this);
    this.className = "panel task-panel";
    // U17: built-in 预设 first, then 我的方案 (value "plan:<name>"), then 自定义 (current settings, not a saved plan).
    // 2026-10-24 (user: dropdowns are slow): one pill each, the current one highlighted.
    const current = state.activePlan ? `plan:${state.activePlan}` : state.profile.preset || "";
    const presetChips = choiceChips({ label: "方案", value: current, focusKey: "preset", disabled: state.run.active, cls: "grow plan-chips",
      options: [...state.schema.presets.map((p) => [p.name, p.label.replace(/（.*?）/g, ""), "预设"]),
        ...(state.plans || []).map((plan) => [`plan:${plan.name}`, plan.name, "我的方案"]),
        ["", "自定义", "当前设置，未存为方案"]],
      onPick: (v) => (v.startsWith("plan:") ? actions.selectPlan(v.slice(5)) : actions.selectPreset(v)) });
    const head = h("div", { class: "panel-head" },
      h("span", { class: "eyebrow", text: "方案" }),
      presetChips,
      h("button", { class: "btn btn-text btn-small", type: "button", disabled: state.run.active, title: "把当前设置存为新方案", dataset: { focusKey: "plan-save-as" }, onclick: () => actions.openSavePlan() }, "另存为"),
      h("button", { class: "btn btn-text btn-small", type: "button", disabled: state.run.active, title: state.activePlan ? "恢复为方案已保存的设置；可撤销" : "恢复为预设方案；可撤销", onclick: () => (state.activePlan ? actions.selectPlan(state.activePlan) : actions.selectPreset(state.profile.preset || state.schema.presets[0]?.name)) }, "重置"));
    const planRow = h("div", { class: "plan-row" }, saveIndicator(state, actions));
    ensurePlanStyles();

    const conflicts = mutualConflicts(state);
    const conflictNames = new Set(conflicts.flatMap((c) => c.tasks));
    const shown = state.profile.tasks.map((entry, i) => ({ entry, i })).filter(({ entry }) => taskShown(state, entry.name));
    // 双栏: the list picks the task whose settings fill the detail column (kept per device in the store)
    const picked = state.wide ? (shown.find(({ entry }) => entry.name === state.taskSelected) || shown[0])?.entry || null : null;
    const list = h("ol", { class: "task-list", "aria-label": "任务" },
      shown.map(({ entry, i }, pos) => taskRow(state, actions, entry, i,
        { prev: pos > 0 ? shown[pos - 1].i : null, next: pos < shown.length - 1 ? shown[pos + 1].i : null }, conflictNames,
        picked ? { selected: entry.name === picked.name, onSelect: () => actions.selectTask(entry.name) } : null)));
    const empty = shown.length ? null : h("p", { class: "muted small task-list-empty", text: "方案里没有任务：点「添加任务」" });
    const names = shown.map(({ entry }) => entry.name);
    const ticked = shown.filter(({ entry }) => entry.enabled).length;
    const bulk = shown.length > 1 && actions.setEnabledMany ? h("div", { class: "task-bulk", role: "group", "aria-label": "批量勾选" },
      h("span", { class: "muted small num", text: `${ticked}/${shown.length}` }),
      h("button", { class: "btn btn-text btn-small", type: "button", disabled: state.run.active || ticked === shown.length,
        dataset: { focusKey: "tasks-all" }, onclick: () => actions.setEnabledMany(names, true) }, "全选"),
      h("button", { class: "btn btn-text btn-small", type: "button", disabled: state.run.active || ticked === 0,
        dataset: { focusKey: "tasks-none" }, onclick: () => actions.setEnabledMany(names, false) }, "全取消")) : null;
    const attention = attentionView(state, actions);
    const listParts = [bulk, list, empty, undoBar(state, actions), addTaskEntry(state, actions)];
    const scroll = h("div", { class: "panel-scroll", dataset: { scrollKey: "tasks" } },
      attention ? h("div", { class: "task-attention" }, attention) : null,
      state.wide
        ? h("div", { class: "task-md" },
          h("div", { class: "task-md-list", dataset: { scrollKey: "tasks-list" } }, listParts),
          h("div", { class: "task-md-detail", dataset: { scrollKey: `tasks-detail-${picked?.name || ""}` } }, taskDetail(state, actions, picked, conflictNames)))
        : listParts);
    // 工作台 (aside tier): 开始 and the log live in the run column (app.js), not under the list.
    this.append(head, planRow, scroll, ...(state.wide === "aside" ? [] : [logStrip(state, actions), dailyRunView(state, actions)]));
  }
}
customElements.define("sc-task-panel", TaskPanel);

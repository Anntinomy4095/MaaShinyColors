// 「添加任务」: an entry under the task list opens a list dialog of the schema tasks
// that are not in the current 方案; picking one appends it. A row context menu (right-click / menu key) moves a task
// or takes it out of the 方案, with an inline 撤销. One instance per task: run_tasks and ui_schema.profile_to_run_config
// reject a repeated task name, so each task can be added only once. Only PI tasks from the schema appear;
// developer-only tasks only with 开发者选项. Styles live in styles/task-add.css, linked on first use (CSP 'self').
import { h, icon, jaText, nextId } from "./dom.js";
import { taskName, taskBlurb, taskVisible, taskInPlan, taskInQueue, isDevTask, addConflicts, devMode, singleTaskState, startBlockers } from "./display.js";
import { taskIcon } from "./task-icons.js";

const STYLE_HREF = "styles/task-add.css";

export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

/** Task glyph: the task's in-game icon (task-icons.js), else its line icon on a tinted plate. */
export function taskGlyph(state, name, size = 32) {
  return taskIcon(state, name, { size, cls: "task-glyph is-item" });
}

/**
 * Picker rows (pure; unit-tested): addable tasks first, in schema order, then the visible tasks already in the 方案
 * (shown as 已添加, not selectable). Hidden developer tasks never appear without 开发者选项; 2026-10-13: only daily-queue
 * tasks are offered (W.I.N.G. starts on 「Produce」, the 每日任务进度 read runs automatically).
 */
export function pickerRows(state) {
  const rows = [];
  for (const task of state.schema.tasks) {
    if (!taskVisible(state, task.name) || !taskInQueue(state, task.name)) continue;
    const added = taskInPlan(state, task.name);
    const conflicts = added ? [] : addConflicts(state, task.name);
    rows.push({
      name: task.name, label: taskName(state, task.name), blurb: taskBlurb(state, task.name), added,
      dev: isDevTask(state, task.name), conflict: conflicts[0]?.message || null,
    });
  }
  return [...rows.filter((r) => !r.added), ...rows.filter((r) => r.added)];
}

/** Dialog: click (or Enter) on a row calls onAdd(name) and closes. */
export function openTaskPicker(state, { onAdd }) {
  ensureStyles();
  const titleId = nextId("addtask");
  const dialog = h("dialog", { class: "dialog task-add-dialog", "aria-labelledby": titleId });
  const close = () => { dialog.close(); dialog.remove(); };
  const rows = pickerRows(state);
  const addable = rows.filter((r) => !r.added);
  const list = h("ul", { class: "task-add-list", role: "listbox", "aria-labelledby": titleId });
  // 2026-10-08: tasks already in the 方案 are folded away under 「已在方案中 N」.
  const addedList = h("ul", { class: "task-add-list is-added-list" });
  rows.forEach((row) => {
    const sub = row.conflict ? `${row.conflict}；加入后不勾选` : row.blurb;
    const item = h("li", {
      class: `task-add-item${row.added ? " is-added" : ""}${row.conflict ? " has-conflict" : ""}`, role: "option",
      "aria-selected": "false", "aria-disabled": row.added ? "true" : null, tabindex: row === addable[0] ? "0" : "-1",
      dataset: { task: row.name }, title: devMode(state) ? row.name : null,
      onclick: () => { if (!row.added) { close(); onAdd(row.name); } },
      onkeydown: (event) => {
        const items = [...list.querySelectorAll(".task-add-item:not(.is-added)")];
        const at = items.indexOf(item);
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.click(); return; }
        const delta = { ArrowDown: 1, ArrowUp: -1 }[event.key];
        if (!delta || at < 0) return;
        event.preventDefault();
        items[(at + delta + items.length) % items.length]?.focus();
      },
    },
    taskGlyph(state, row.name),
    h("span", { class: "task-add-text" },
      h("span", { class: "task-add-name" }, jaText(row.label), row.dev ? h("span", { class: "task-add-tag", text: "开发" }) : null),
      sub ? h("span", { class: `task-add-blurb${row.conflict ? " is-warning" : ""}` }, jaText(sub)) : null),
    row.added ? h("span", { class: "task-add-state", text: "已添加" }) : icon("plus", { size: 16, cls: "task-add-plus" }));
    (row.added ? addedList : list).append(item);
  });
  const added = rows.length - addable.length;
  dialog.append(
    h("div", { class: "task-add-head" },
      h("h2", { id: titleId, class: "dialog-title", text: "添加任务" }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", title: "关闭", onclick: close }, icon("close", { size: 16 }))),
    // Native append would render null as text.
    ...(addable.length ? [h("p", { class: "task-add-group", text: `可添加 · ${addable.length}` })] : [h("p", { class: "muted small task-add-empty", text: "方案已包含全部任务" })]),
    list,
    ...(added ? [h("details", { class: "task-add-added", open: addable.length ? null : "" },
      h("summary", { class: "task-add-group", text: `已在方案中 · ${added}` }), addedList)] : []));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });  // backdrop
  document.body.append(dialog);
  dialog.showModal();
  (list.querySelector('[tabindex="0"]') || dialog.querySelector(".icon-btn")).focus();
  return dialog;
}

/** Entry under the task list. */
export function addTaskEntry(state, actions) {
  ensureStyles();
  const count = pickerRows(state).filter((r) => !r.added).length;
  return h("div", { class: "task-add-entry" },
    h("button", {
      class: "task-add-btn", type: "button", disabled: state.run.active, "aria-haspopup": "dialog",
      dataset: { focusKey: "task-add" }, title: count ? `可添加 ${count} 个任务` : "方案已包含全部任务",
      onclick: () => openTaskPicker(state, { onAdd: (name) => actions.addTask(name) }),
    }, icon("plus", { size: 16 }), h("span", { text: "添加任务" }), count ? h("span", { class: "task-add-count", text: `可添加 ${count}` }) : null));
}

/** Inline 撤销 bar after a removal (state.planUndo). */
export function undoBar(state, actions) {
  const undo = state.planUndo;
  if (!undo || taskInPlan(state, undo.name)) return null;
  return h("div", { class: "task-undo", role: "status" },
    h("span", { class: "task-undo-text" }, jaText(`已移出「${taskName(state, undo.name)}」`)),
    h("button", { class: "btn btn-small btn-text", type: "button", disabled: state.run.active, dataset: { focusKey: "task-undo" }, onclick: () => actions.undoRemove() }, "撤销"),
    h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", title: "关闭", onclick: () => actions.dismissUndo() }, icon("close", { size: 16 })));
}

// ---------------------------------------------------------------- row context menu
let openMenu = null;

function closeMenu() {
  if (!openMenu) return;
  const { menu, restore } = openMenu;
  openMenu = null;
  menu.remove();
  document.removeEventListener("pointerdown", onOutside, true);
  restore?.focus?.({ preventScroll: true });
}

function onOutside(event) {
  if (openMenu && !openMenu.menu.contains(event.target)) closeMenu();
}

/** Small menu at the pointer (or under `anchor` for the keyboard). items: [{ label, onSelect, disabled, danger }]. */
export function openContextMenu({ x, y, anchor, label, items }) {
  ensureStyles();
  closeMenu();
  const menu = h("div", { class: "task-menu", role: "menu", "aria-label": label });
  const buttons = items.map((item) => h("button", {
    class: `task-menu-item${item.danger ? " is-danger" : ""}`, type: "button", role: "menuitem", disabled: item.disabled || false,
    onclick: () => { closeMenu(); item.onSelect(); },
  }, item.label));
  menu.append(...buttons);
  menu.addEventListener("keydown", (event) => {
    const enabled = buttons.filter((b) => !b.disabled);
    const at = enabled.indexOf(document.activeElement);
    if (event.key === "Escape" || event.key === "Tab") { event.preventDefault(); closeMenu(); return; }
    const delta = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (!delta) return;
    event.preventDefault();
    enabled[(at + delta + enabled.length) % enabled.length]?.focus();
  });
  document.body.append(menu);
  const rect = anchor?.getBoundingClientRect();
  const left = x ?? (rect ? rect.left + 24 : 0);
  const top = y ?? (rect ? rect.bottom : 0);
  const width = menu.offsetWidth || 160;
  const height = menu.offsetHeight || 120;
  menu.style.left = `${Math.max(4, Math.min(left, window.innerWidth - width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(top, window.innerHeight - height - 4))}px`;
  openMenu = { menu, restore: document.activeElement };
  document.addEventListener("pointerdown", onOutside, true);
  buttons.find((b) => !b.disabled)?.focus();
  return menu;
}

/** Menu entries for one task row (pure; unit-tested). */
export function rowMenuItems(state, actions, { name, index, prev, next }) {
  const running = state.run.active;
  const selected = singleTaskState(state, name);
  return [
    { label: "仅运行此任务", disabled: running || !selected || startBlockers(selected).length > 0,
      onSelect: () => actions.startRun(name) },
    // pick a few tasks without 全取消 first: this one ticked, the other rows unticked
    { label: "只勾选此项", disabled: running, onSelect: () => actions.setOnly(name) },
    { label: "上移", disabled: running || prev === null, onSelect: () => actions.moveTask(index, prev) },
    { label: "下移", disabled: running || next === null, onSelect: () => actions.moveTask(index, next) },
    // the wide list shows the picked task's settings beside it; one column expands them in the row
    state.wide ? { label: "查看设置", disabled: state.taskSelected === name, onSelect: () => actions.selectTask(name) }
      : { label: state.expanded.includes(name) ? "收起设置" : "展开设置", onSelect: () => actions.toggleExpanded(name) },
    { label: "移出方案", danger: true, disabled: running, onSelect: () => actions.removeTask(name) },
  ];
}

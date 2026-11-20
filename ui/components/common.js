// Shared small components: StatusPill (dot + text), EmptyState, HelpTip, Toast, PanelErrorBoundary, Section.
import { h, icon, clear, nextId, append, jaText } from "./dom.js";

/** Task status semantics. Tone names map to design tokens in app.css. */
export const STATUS = {
  pending: { label: "待运行", tone: "neutral", icon: "dot" },
  running: { label: "运行中", tone: "primary", icon: "play" },
  succeeded: { label: "成功", tone: "success", icon: "check" },
  skipped: { label: "跳过", tone: "muted", icon: "skip" },
  stopped: { label: "已停止", tone: "warning", icon: "skip" },
  failed: { label: "失败", tone: "danger", icon: "cross" },
  uncertain: { label: "不确定", tone: "uncertain", icon: "help" },
  guard_refused: { label: "未执行", tone: "warning", icon: "shield" },
  takeover: { label: "需接管", tone: "accent", icon: "warn" },
  idle: { label: "未运行", tone: "neutral", icon: "dot" },
};

export const STATUS_HINT = {
  uncertain: "结果不确定，请在游戏中核对",
  guard_refused: "未执行：画面与任务不符，未点击",
  takeover: "未知画面（弹窗等），已停止：处理后回到首页再开始",
  failed: "失败：详情见「日志」",
};

class StatusPill extends HTMLElement {
  static get observedAttributes() { return ["status", "label"]; }
  connectedCallback() { this.render(); }
  attributeChangedCallback() { if (this.isConnected) this.render(); }
  render() {
    const key = this.getAttribute("status") || "pending";
    const meta = STATUS[key] || STATUS.pending;
    this.className = `dot-text tone-${meta.tone}${key === "running" ? " is-running" : ""}`;
    this.setAttribute("role", "status");
    clear(this);
    this.append(h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text: this.getAttribute("label") || meta.label }));
  }
}
customElements.define("sc-status-pill", StatusPill);

export function statusPill(status, label) {
  const el = document.createElement("sc-status-pill");
  el.setAttribute("status", status);
  if (label) el.setAttribute("label", label);
  return el;
}

/** EmptyState: tells the user what is missing and what to do next (quiet text, no illustration). */
export function emptyState({ title, body, action = null }) {
  return h("div", { class: "empty-state" },
    h("div", { class: "empty-title", text: title }),
    body ? h("p", { class: "empty-body", text: body }) : null,
    action);
}

/** "?" button that toggles an inline help paragraph (works with keyboard and touch, unlike a bare title). */
export function helpTip(text, content = null) {
  const id = nextId("help");
  const body = h("div", { class: "help-body", id, hidden: true });
  if (content) body.append(content); else body.textContent = text;
  const button = h("button", {
    class: "icon-btn help-btn", type: "button", "aria-expanded": "false", "aria-controls": id,
    "aria-label": "说明", title: "说明",
    onclick: (event) => {
      event.stopPropagation();
      const open = body.hidden;
      body.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
    },
  }, icon("help", { size: 16 }));
  return { button, body };
}

export function section(title, ...children) {
  return h("section", { class: "section" }, h("h3", { class: "section-title", text: title }), ...children);
}

/** Collapsed-by-default details block (advanced / technical information). */
export function details(summary, ...children) {
  return h("details", { class: "details" },
    h("summary", {}, icon("chevron", { size: 16, cls: "details-chevron" }), h("span", { text: summary })),
    h("div", { class: "details-body" }, ...children));
}

let toastHost = null;
export function toast(message, tone = "neutral", options = {}) {
  if (!toastHost) {
    toastHost = h("div", { class: "toast-host", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  // options.action = { label, onclick }: a 撤销-style button; such a toast stays longer (8 s) and closes once clicked.
  const action = options.action;
  const item = h("div", { class: `toast tone-${tone}${action ? " has-action" : ""}` }, h("span", { text: message }));
  const lifetime = action ? 8000 : 3200;
  let gone = false;
  const dismiss = () => {
    if (gone) return;
    gone = true;
    item.classList.add("leaving");
    setTimeout(() => item.remove(), 400);
  };
  if (action) {
    item.append(h("button", { class: "btn btn-small btn-text toast-action", type: "button", onclick: () => { dismiss(); action.onclick(); } }, action.label));
  }
  toastHost.append(item);
  setTimeout(dismiss, lifetime);
}

/**
 * Modal yes/no: resolves true only from the confirm button (Esc / 取消 resolve false). Without <dialog> support
 * (node tests) it resolves true, so a warning never blocks.
 */
export function openConfirm({ title, lines = [], confirmText = "继续", cancelText = "取消" }) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return Promise.resolve(true);
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: "dialog confirm-dialog", "aria-label": title });
    const close = (answer) => { dialog.close(); dialog.remove(); resolve(answer); };
    dialog.append(
      h("h2", { class: "dialog-title", text: title }),
      ...lines.map((line) => h("p", { class: "small" }, jaText(line))),
      h("div", { class: "dialog-actions" },
        h("span", { class: "grow" }),
        h("button", { type: "button", class: "btn", dataset: { confirmCancel: "1" }, onclick: () => close(false) }, cancelText),
        h("button", { type: "button", class: "btn btn-primary", dataset: { confirmOk: "1" }, onclick: () => close(true) }, confirmText)));
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(false); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

/**
 * PanelErrorBoundary (poi-style): a panel that throws while rendering shows an error card with a retry button;
 * other panels keep working.
 */
export function guardedRender(container, renderFn) {
  try {
    clear(container);
    renderFn(container);
  } catch (error) {
    console.error(error);
    clear(container);
    container.append(emptyState({
      title: "此面板出错了",
      body: String(error && error.message || error),
      action: h("button", { class: "btn", type: "button", onclick: () => guardedRender(container, renderFn) }, "重试"),
    }));
  }
}

// ---- shared building blocks for the whole UI --------------------------------------------
const FOLD_KEY = "sc-ui-fold-open";

/** Per-viewer remembered value (localStorage, JSON). Storage can be missing or throw (private window, node tests). */
export function remembered(key, fallback = null) {
  try {
    const raw = globalThis.localStorage?.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function remember(key, value) {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: the page still works, it just forgets */
  }
}

/**
 * FoldCard: a collapsible card whose header always shows the title, a count and an optional one-line summary, so a
 * closed card still answers "how many / anything wrong?". Open state is remembered per viewer under `key`.
 * `children` may be a function (built only when opened — keeps long grids out of the DOM while closed).
 */
export function foldSection({ key, title, count = null, summary = null, tone = null, defaultOpen = false, icon: lead = null, hint = null, children }) {
  const saved = remembered(FOLD_KEY, {}) || {};
  const open = typeof saved[key] === "boolean" ? saved[key] : defaultOpen;
  const body = h("div", { class: "fold-body" });
  let built = false;
  const build = () => {
    if (built) return;
    built = true;
    const content = typeof children === "function" ? children() : children;
    append(body, [content]);
  };
  const el = h("details", { class: `fold${tone ? ` tone-${tone}` : ""}`, dataset: { fold: key, openKey: `fold-${key}` } },
    h("summary", { class: "fold-head", title: hint },
      lead,
      h("span", { class: "fold-title", text: title }),
      count !== null && count !== undefined ? h("span", { class: "fold-count num", text: String(count) }) : null,
      summary ? h("span", { class: "fold-summary" }, summary) : h("span", { class: "fold-summary" }),
      icon("chevron", { size: 16, cls: "fold-chevron" })),
    body);
  if (open) el.setAttribute("open", "");
  if (open || typeof children !== "function") build();
  el.buildBody = build;   // sectionDeck builds the picked section before it is attached
  el.addEventListener("toggle", () => {
    const isOpen = el.open ?? el.getAttribute?.("open") !== null;
    if (isOpen) build();
    if (el.closest?.(".deck")) return;   // a section list opens its pick; that is not the one-column open state
    const map = remembered(FOLD_KEY, {}) || {};
    map[key] = Boolean(isOpen);
    remember(FOLD_KEY, map);
  });
  return el;
}

const DECK_KEY = "sc-ui-deck";

/**
 * Wide layout (双栏): the same fold cards as a section list beside the selected section. The list shows
 * each fold's title, count and one-line summary; picking one shows that fold (opened, its head as the section title).
 * Fields and controls are the fold's own: only the arrangement differs from the one-column page. The pick is kept per
 * deck (`key`) on this device; without one the section named `defaultKey` (else the first) is shown.
 */
/** A fold head's summary for the section list: its parts as they are (tones, separate tags), without controls. */
function deckSummary(source, text) {
  const copy = source.cloneNode(true);
  copy.querySelectorAll?.("button, input, select, a, .icon").forEach((el) => el.remove());
  const out = h("span", { class: "deck-item-summary", title: text });
  out.append(...(copy.childNodes?.length ? [...copy.childNodes] : [text]));
  return out;
}

export function sectionDeck(folds, { key, label = "分组", defaultKey = null }) {
  const items = folds.filter((fold) => fold?.matches?.("details.fold"));
  const saved = (remembered(DECK_KEY, {}) || {})[key];
  const nav = h("div", { class: "deck-nav", role: "tablist", "aria-orientation": "vertical", "aria-label": label });
  const view = h("div", { class: "deck-view" });
  const select = (fold, { save = true, focus = false } = {}) => {
    for (const button of nav.children) {
      const on = button.dataset.deckFold === fold.dataset.fold;
      button.classList.toggle("is-current", on);
      button.setAttribute("aria-selected", String(on));
      button.tabIndex = on ? 0 : -1;
      if (on && focus) button.focus();
    }
    for (const item of items) item.classList.toggle("is-current", item === fold);
    fold.buildBody?.();
    if (!fold.open) fold.open = true;   // the toggle handler skips the one-column open state inside a deck
    if (save) {
      const scroller = view.closest?.(".panel-scroll");
      if (scroller && scroller.scrollTop > 0) scroller.scrollTop = 0;   // a new section starts at its title
      const map = remembered(DECK_KEY, {}) || {};
      map[key] = fold.dataset.fold;
      remember(DECK_KEY, map);
    }
  };
  items.forEach((fold, index) => {
    const head = fold.querySelector(":scope > summary");
    const count = head?.querySelector(".fold-count")?.textContent;
    const summaryEl = head?.querySelector(".fold-summary");
    const summary = summaryEl?.textContent.trim();
    nav.append(h("button", {
      class: `deck-item${fold.classList.contains("tone-accent") ? " tone-accent" : ""}`, type: "button", role: "tab",
      dataset: { deckFold: fold.dataset.fold, focusKey: `deck-${key}-${fold.dataset.fold}` },
      onclick: () => select(fold),
      onkeydown: (event) => {
        const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        select(items[(index + step + items.length) % items.length], { focus: true });
      },
    }, h("span", { class: "deck-item-title" }, h("span", { text: head?.querySelector(".fold-title")?.textContent || "" }),
      count ? h("span", { class: "fold-count num", text: count }) : null),
    summary ? deckSummary(summaryEl, summary) : null));
    head?.setAttribute("tabindex", "-1");   // the list is the control; the head is the section title here
    head?.addEventListener("click", (event) => { if (!event.target.closest("a, button, input, select, label")) event.preventDefault(); });
    view.append(fold);
  });
  const first = items.find((fold) => fold.dataset.fold === saved) || items.find((fold) => fold.dataset.fold === defaultKey) || items[0];
  if (first) select(first, { save: false });
  return h("div", { class: "deck", dataset: { deck: key } }, nav, view);
}

/** Filter chip (toggle button). `pressed` drives aria-pressed and the selected look; `dot` adds a status dot. */
export function chip(label, { pressed = false, count = null, tone = null, dot = false, title = null, onclick = null, focusKey = null, lead = null } = {}) {
  return h("button", { type: "button", class: `chip${pressed ? " is-on" : ""}${tone ? ` tone-${tone}` : ""}`,
    "aria-pressed": pressed ? "true" : "false", title, dataset: focusKey ? { focusKey } : null, onclick },
  dot ? h("span", { class: "dot", "aria-hidden": "true" }) : null, lead,
  h("span", { text: label }), count !== null ? h("span", { class: "chip-count num", text: String(count) }) : null);
}

/**
 * FactLine: one compact wrapping row of facts
 * for the top of a page that truly needs it — `items` = [{ label, value, tone?, missing?, title?, onclick?, focusKey?, key? }].
 * Each fact is a small pill: quiet label + value (status dot when `tone`, dashed when `missing` = 尚未读取). With `onclick` the
 * pill is a button (jump to the page that explains it). Returns null for an empty list.
 */
export function factLine(items, { label = null } = {}) {
  const list = (items || []).filter(Boolean);
  if (!list.length) return null;
  return h("div", { class: "fact-line", role: "list", "aria-label": label },
    list.map((f) => h(f.onclick ? "button" : "span", {
      class: `fact${f.missing ? " is-missing" : ""}${f.onclick ? " is-link" : ""}${f.tone ? ` tone-${f.tone}` : ""}`,
      role: f.onclick ? null : "listitem", type: f.onclick ? "button" : null, title: f.title || null, onclick: f.onclick || null,
      dataset: { ...(f.key ? { fact: f.key } : {}), ...(f.focusKey ? { focusKey: f.focusKey } : {}) } },
    h("span", { class: "fact-label" }, typeof f.label === "string" ? jaText(f.label) : f.label),
    f.tone ? h("span", { class: "dot", "aria-hidden": "true" }) : null,
    h("span", { class: "fact-value num" }, typeof f.value === "string" ? jaText(f.value) : f.value))));
}

/** Section ribbon (the game's dark slate 「✦ アイドル」 label): a short heading inside a page, optional trailing node. */
export function ribbon(text, extra = null) {
  return h("div", { class: "ribbon-row" }, h("span", { class: "ribbon" }, h("span", { class: "ribbon-star", "aria-hidden": "true", text: "✦" }),
    h("span", { text })), extra ? h("span", { class: "ribbon-extra" }, extra) : null);
}

/** Small slate tag (the game's 「期限」「達成度」 pills). */
export function tag(text, { tone = null, title = null } = {}) {
  return h("span", { class: `tag${tone ? ` tone-${tone}` : ""}`, title, text });
}

/** Thin progress meter 0..1 (pink; green when complete). Decorative: the numbers sit next to it. */
export function meter(done, total) {
  const ratio = total ? Math.max(0, Math.min(1, done / total)) : 0;
  const bar = h("span", { class: `meter${total && done >= total ? " is-done" : ""}`, "aria-hidden": "true" }, h("span", { class: "meter-fill" }));
  if (bar.firstChild.style) bar.firstChild.style.width = `${Math.round(ratio * 100)}%`;
  return bar;
}

/**
 * AttentionCard with rows: `items` = [{ text, tone, action: { label, onclick, focusKey, title } }]. Shows at most `max`
 * rows and a quiet 「还有 N 条」 line; returns null when there is nothing (never an empty box).
 */
export function attentionCard({ title, items, max = 3, label = null, moreText = null }) {
  if (!items?.length) return null;
  const shown = items.slice(0, max);
  return h("section", { class: "attention-card", "aria-label": label || title },
    h("div", { class: "attention-card-head" }, h("span", { class: "dot", "aria-hidden": "true" }),
      h("span", { class: "attention-card-title", text: title })),
    h("ul", { class: "attention-rows" }, shown.map((item) => h("li", { class: `attention-row tone-${item.tone || "accent"}` },
      h("span", { class: "dot", "aria-hidden": "true" }),
      h("span", { class: "attention-row-text" }, typeof item.text === "string" ? jaText(item.text) : item.text),
      item.action ? h("button", { class: "btn btn-small btn-text", type: "button", title: item.action.title || null,
        dataset: item.action.focusKey ? { focusKey: item.action.focusKey } : null, onclick: item.action.onclick }, item.action.label) : null,
      item.close ? h("button", { class: "icon-btn attention-close", type: "button", "aria-label": "关闭", title: item.close.title || "关闭",
        dataset: item.close.focusKey ? { focusKey: item.close.focusKey } : null, onclick: item.close.onclick }, icon("close", { size: 16 })) : null))),
    items.length > max ? h("p", { class: "attention-more muted small", text: moreText || `还有 ${items.length - max} 条` }) : null);
}

/** Quiet heading for a group of rows inside a card (replaces the old ruled `.section` title). */
export function subhead(text, extra = null) {
  return h("div", { class: "subhead" }, h("span", { class: "subhead-text", text }), extra);
}

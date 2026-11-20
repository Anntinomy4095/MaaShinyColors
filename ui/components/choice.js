// One-tap choice controls (2026-10-24, user: dropdowns are slow to use and ugly): ChoiceChips for a short single choice,
// ListPicker for a long one. No DOM globals at module scope, so node tests can import the fields that use them.
import { h, icon, clear, jaText, focusAfterRender } from "./dom.js";

const textNode = (text) => (typeof text === "string" ? jaText(text) : text);

/**
 * ChoiceChips (2026-10-24, user: dropdowns are slow and ugly): a single choice as wrapping pills — every option visible,
 * one tap (radio group; arrow keys move the choice). options = [[value, text, title?]]; `text` may be a node.
 */
export function choiceChips({ options, value, onPick, focusKey, label = null, labelledBy = null, disabled = false, cls = "" }) {
  const list = options.filter(Boolean);
  const at = Math.max(0, list.findIndex(([v]) => v === value));
  return h("div", { class: `chip-row choice-chips${cls ? ` ${cls}` : ""}`, role: "radiogroup", "aria-label": label, "aria-labelledby": labelledBy },
    list.map(([v, text, title], i) => h("button", {
      type: "button", role: "radio", class: `chip${v === value ? " is-on" : ""}`, "aria-checked": String(v === value),
      tabindex: i === at ? "0" : "-1", disabled, title: title || null, dataset: { focusKey: `${focusKey}:${i}` },
      onclick: () => { if (v !== value) onPick(v); },
      onkeydown: (event) => {
        const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
        if (!delta) return;
        event.preventDefault();
        const next = (i + delta + list.length) % list.length;
        onPick(list[next][0]);
        focusAfterRender(`${focusKey}:${next}`);
      },
    }, h("span", {}, textNode(text)))));
}

const PICKER_OPEN = new Set();      // focus keys of open list pickers (survive re-renders)
const PICKER_QUERY = new Map();     // focus key -> filter text

/**
 * ListPicker: a long single choice (idols, cards, …) in place — a button shows the current value; opened,
 * a filter field (more than `limit` items) and the matching rows; one tap picks and closes. Typing refilters only the
 * rows (focus stays). items = [{ value, text, sub?, lead?, search? }].
 */
export function listPicker({ items, value, onPick, focusKey, label, placeholder = "未选择", disabled = false, limit = 8,
  filterPlaceholder = "筛选", cls = "" }) {
  const root = h("div", { class: `list-picker${cls ? ` ${cls}` : ""}` });
  const current = items.find((it) => it.value === value);
  const paint = () => {
    const open = PICKER_OPEN.has(focusKey) && !disabled;
    clear(root);
    root.classList.toggle("is-open", open);
    root.append(h("button", { class: `list-picker-head${current ? "" : " is-empty"}`, type: "button", disabled, "aria-expanded": String(open),
      "aria-label": label, dataset: { focusKey }, onclick: () => { if (open) PICKER_OPEN.delete(focusKey); else PICKER_OPEN.add(focusKey); paint(); } },
    current?.lead || null, h("span", { class: "list-picker-value" }, textNode(current ? current.text : placeholder)),
    icon("chevron", { size: 16, cls: "list-picker-chev" })));
    if (!open) return;
    const rows = h("ul", { class: "list-picker-rows", role: "listbox", "aria-label": label });
    const fill = (query) => {
      clear(rows);
      const q = String(query || "").normalize("NFKC").trim().toLowerCase();
      const hits = q ? items.filter((it) => `${it.search || ""} ${it.text}`.normalize("NFKC").toLowerCase().includes(q)) : items;
      for (const it of hits) {
        rows.append(h("li", { role: "option", "aria-selected": String(it.value === value) },
          h("button", { class: `list-picker-row${it.value === value ? " is-on" : ""}`, type: "button", dataset: { focusKey: `${focusKey}:${it.value}` },
            onclick: () => { PICKER_OPEN.delete(focusKey); PICKER_QUERY.delete(focusKey); if (it.value === value) paint(); else onPick(it.value); } },
          it.lead || null, h("span", { class: "list-picker-text" }, textNode(it.text)),
          it.sub ? h("span", { class: "list-picker-sub" }, textNode(it.sub)) : null)));
      }
      if (!hits.length) rows.append(h("li", { class: "list-picker-none muted small", text: "没有符合的项" }));
    };
    const query = PICKER_QUERY.get(focusKey) || "";
    if (items.length > limit) {
      root.append(h("input", { class: "input input-compact list-picker-q", type: "search", placeholder: filterPlaceholder, "aria-label": filterPlaceholder,
        value: query, dataset: { focusKey: `${focusKey}:q` }, oninput: (e) => { PICKER_QUERY.set(focusKey, e.target.value); fill(e.target.value); } }));
    }
    fill(query);
    root.append(rows);
  };
  paint();
  return root;
}


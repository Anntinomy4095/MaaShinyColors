// Small DOM helpers shared by the ノウハウ panel (knowhow-panel.js) and its 製本 workflow (knowhow-workflow.js).
import { h } from "./dom.js";
import { USABLE_LABEL, idolHue, isGeneralBook, knowhowKind, shortName } from "./knowhow-views.js";

export function bookLabel(book) {
  return book?.idol || (isGeneralBook(book) ? "283プロ（通用）" : book?.title || (book?.source === "user" ? "登记的书" : "（名义未读）"));
}

export function statText(book) {
  const s = book?.stats || {};
  return ["vo", "da", "vi", "me"].map((k) => (Number.isInteger(s[k]) ? s[k] : "?")).join("/");
}

export function idolBar(book) {
  const bar = h("span", { class: "kh-idol-bar", "aria-hidden": "true" });
  const name = book?.idol || (isGeneralBook(book) ? "" : null);
  if (name) bar.style.backgroundColor = `hsl(${idolHue(name)} 55% 55%)`;
  return bar;
}

export function segmented(label, options, current, onPick, focusPrefix) {
  // options = [[key, text, mark?]]; mark = a step number or 「✓」 (ノウハウ tabs before the first use)
  return h("div", { class: "segmented text-seg", role: "radiogroup", "aria-label": label }, options.map(([key, text, mark]) =>
    h("button", { class: `segment${current === key ? " is-selected" : ""}`, type: "button", role: "radio",
      "aria-checked": String(current === key), dataset: { focusKey: `${focusPrefix}-${key}` }, onclick: () => onPick(key) },
    h("span", { text }), mark ? h("span", { class: `kh-seg-mark${mark === "✓" ? " is-done" : ""}`, text: mark }) : null)));
}

/**
 * Single choice as wrapping pills (2026-10-24, user: dropdowns are slow and ugly): every option visible, one tap.
 * options = [[key, text, title?]]; the chosen one is highlighted.
 */
export function choiceChips(label, options, current, onPick, focusPrefix, { cls = "" } = {}) {
  return h("div", { class: `kh-choice${cls ? ` ${cls}` : ""}`, role: "radiogroup", "aria-label": label }, options.map(([key, text, title]) =>
    h("button", { type: "button", role: "radio", class: `kh-filter-chip${current === key ? " is-on" : ""}`, "aria-checked": String(current === key),
      title: title || null, dataset: { focusKey: `${focusPrefix}-${key || "any"}` }, onclick: () => onPick(key) }, text)));
}

export function selectEl(id, label, options, value, onChange) {
  return h("div", { class: "select-wrap" },
    h("select", { class: "select select-compact", id, "aria-label": label, dataset: { focusKey: id }, onchange: (e) => onChange(e.target.value) },
      options.map(([key, text]) => h("option", { value: key, text, selected: key === value }))));
}

export function usableTag(usability) {
  return h("span", { class: `kh-use is-${usability}`, text: USABLE_LABEL[usability] });
}

export function chip(name, level, { cls = "", title = null, suffix = null } = {}) {
  return h("span", { class: `kh-chip kind-${knowhowKind(name)}${cls ? ` ${cls}` : ""}`, title },
    h("span", { lang: "ja", text: name }), level ? h("span", { class: "kh-lv", text: `Lv${level}` }) : null,
    suffix ? h("span", { class: "kh-chip-suffix num", text: suffix }) : null);
}

export function gapChip(item) {
  return h("span", { class: "kh-chip is-gap", title: "缺：已读部分没有达到最低 Lv 的可带书" },
    h("span", { lang: "ja", text: item.name }), h("span", { class: "kh-lv", text: `≥Lv${item.min_level}` }));
}

/** Status pill: tone = missing | done | conflict | protected | info | warn. */
export function pill(text, tone = "info", title = null) {
  return h("span", { class: `kh-pill is-${tone}`, title, text });
}

/** Compact chip: short name + Lv as a small number; full name in the tooltip. */
export function miniChip(name, level, { cls = "" } = {}) {
  return h("span", { class: `kh-chip kh-mini kind-${knowhowKind(name)}${cls ? ` ${cls}` : ""}`, title: `${name}${level ? ` Lv${level}` : ""}` },
    h("span", { lang: "ja", text: shortName(name) }), level ? h("span", { class: "kh-lv", text: String(level) }) : null);
}

/** Target item name for display: a prefix item 「アイドル(」 reads 「アイドル(…)」 (its in-game suffix varies). */
export function itemLabel(item) {
  return item?.match === "prefix" && /[(（]$/.test(item.name || "") ? `${item.name}…)` : item?.name || "";
}

/** Small icon-like tag: ×N, 主, 護 (保護), ? (未读完). */
export function microTag(text, title, cls = "") {
  return h("span", { class: `kh-micro${cls ? ` ${cls}` : ""}`, title, text });
}

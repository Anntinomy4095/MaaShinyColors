// Grid pickers modelled on the in-game training selection. The Training task's S card picker lives in team-picker.js
// (openTrainingCardPicker: confirmed S cards in 「持有卡牌」 + the empty default choice).
// - openItemPicker: a multi-select group of Yes/No switch options (annotations `control: "item_picker"` + `group`),
//   e.g. the shop's 其他商品 (third daily offer). Checked = the switch becomes Yes. Display only: the options and
//   their run-config meaning are unchanged.
// Both share openGridPicker (dialog, search, keyboard navigation, tiles). The Produce team pickers (team-picker.js)
// use its optional filters (rarity / specialty pills, idol list), renamed scope labels (「持有卡牌 / 全部」), tile tags and,
// for multi-select, 「全选可见 / 清除可见」.
import { h, icon, jaText, nextId } from "./dom.js";
import { choiceChips, listPicker } from "./choice.js";
import { itemIcon, itemSpec } from "./game-assets.js";
import { optionLabel, optionNote, groupNote } from "./display.js";
import { fold, queryWords, hasWords } from "./name-fold.js";

/** Inline control shared by both pickers: a leading visual + text; opens the picker. */
export function pickerButton({ id, disabled, focusKey, labelledBy, onclick, lead, text, title }) {
  return h("button", {
    class: "card-button", type: "button", id, disabled, "aria-haspopup": "dialog",
    "aria-labelledby": `${labelledBy} ${id}-name`, dataset: { focusKey }, onclick, title: title || null,
  }, lead, h("span", { class: "card-button-name", id: `${id}-name` }, jaText(text)),
  icon("chevron", { size: 16, cls: "card-button-chevron" }));
}

// Folded searchable text per entry (name, sub, search, id), built once: entries are not edited after the picker opens.
const haystacks = new WeakMap();
function haystack(entry) {
  let text = haystacks.get(entry);
  if (text === undefined) {
    text = fold(`${entry.name} ${entry.sub || ""} ${entry.search || ""} ${entry.id}`);
    haystacks.set(entry, text);
  }
  return text;
}

/**
 * Entries matching a search box text: it is split on whitespace and every word must occur (AND) in the entry's text. Both
 * sides are folded (name-fold.js: Simplified names, 々, case, width), so 「樱木 SR」 finds 櫻木真乃's SR cards.
 */
export function searchEntries(entries, query) {
  const words = queryWords(query);
  return words.length ? entries.filter((entry) => hasWords(haystack(entry), words)) : entries;
}

/**
 * Generic modal grid. `entries` / `extra` items: { id, selectable, visual(): Node, name, sub, sub2, badge, tags, lock,
 * tip, search, section, flag }. `gridNote` is a quiet line under the tiles. `flag` is a short dashed tag beside the tags (e.g. 未校准). Single choice commits on 确定 / double-click (an entry id may be "" for 「不登记」); multi
 * toggles and commits the chosen id Set. Optional: `scopeLabels` ([entries only, entries + extra]); `filters`
 * [{ label, options: [[value, text]], value, test(entry, value) }] shown as pills, or an in-place list when long (value
 * "" = no filter);
 * `bulk` (multi only) adds 「全选可见 / 清除可见」 for the currently filtered selectable tiles.
 */
export function openGridPicker({ title, entries, extra = [], showExtra = false, multi = false, initial, searchPlaceholder,
  emptyText, gridNote = null, foot = null, countText, onCommit, className = "", scopeLabels = ["可选", "全部"], filters = [], bulk = false }) {
  let chosen = multi ? new Set(initial) : initial;
  let filter = "";
  let showAll = showExtra;
  const filterValues = filters.map((f) => f.value ?? "");
  const titleId = nextId("grid");
  const dialog = h("dialog", { class: `dialog card-dialog ${className}`.trim(), "aria-labelledby": titleId });
  const grid = h("div", { class: "card-grid", role: "listbox", "aria-labelledby": titleId, "aria-multiselectable": multi ? "true" : null });
  const confirm = h("button", { class: "btn btn-primary", type: "button", onclick: () => finish(true) }, "确定");
  const count = h("span", { class: "card-count muted small" });
  const isChosen = (id) => (multi ? chosen.has(id) : id === chosen);

  function finish(ok) {
    dialog.close();
    dialog.remove();
    if (!ok) return;
    if (multi) onCommit(new Set(chosen));
    else if (chosen != null && chosen !== initial) onCommit(chosen);
  }

  function pick(card) {
    if (!card.selectable) return;
    if (multi) { if (chosen.has(card.id)) chosen.delete(card.id); else chosen.add(card.id); }
    else chosen = card.id;
  }

  function tiles() {
    return searchEntries([...entries, ...(showAll ? extra : [])], filter)
      .filter((c) => filters.every((f, i) => !filterValues[i] || f.test(c, filterValues[i])));
  }

  function paint(focusId = null) {
    grid.replaceChildren();
    const list = tiles();
    let section = null;
    list.forEach((card, index) => {
      if (card.section && card.section !== section) {
        section = card.section;
        grid.append(h("div", { class: "card-section", role: "presentation", text: section }));
      }
      const selected = isChosen(card.id);
      const tile = h("button", {
        class: `card-tile${selected ? " is-selected" : ""}${card.selectable ? "" : " is-locked"}`, type: "button",
        role: "option", "aria-selected": String(selected), "aria-disabled": card.selectable ? null : "true",
        tabindex: (multi ? index === 0 : selected || (!list.some((c) => c.id === chosen) && index === 0)) ? "0" : "-1",
        title: card.tip || card.name, dataset: { cardId: card.id },
        // Update the selection in place: repainting here would replace the tile under the pointer, so the second click
        // of a double-click would land on a new element and never form a dblclick. event.detail >= 2 confirms too.
        onclick: (event) => {
          if (!card.selectable) return;
          pick(card);
          if (!multi && event.detail >= 2) { finish(true); return; }
          refreshSelection();
        },
        ondblclick: () => { if (card.selectable && !multi && dialog.open) { chosen = card.id; finish(true); } },
        onkeydown: (event) => {
          const tilesOnly = [...grid.querySelectorAll(".card-tile")];
          const at = tilesOnly.indexOf(tile);
          const cols = Math.max(1, Math.round(grid.clientWidth / (tile.offsetWidth || 1)));
          const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[event.key];
          if (!delta) return;
          event.preventDefault();
          tilesOnly[at + delta]?.focus();
        },
      },
      h("span", { class: "card-frame" }, card.visual(),
        card.badge ? h("span", { class: "card-rarity", text: card.badge }) : null,
        selected ? h("span", { class: "card-check", "aria-hidden": "true" }, icon("check", { size: 16 })) : null,
        card.selectable ? null : h("span", { class: "card-lock", "aria-hidden": "true", text: card.lock || "不可选" })),
      card.tags?.length || card.flag ? h("span", { class: "card-tags", "aria-hidden": "true" }, (card.tags || []).map((t) => h("span", { class: "card-tag", text: t })),
        card.flag ? h("span", { class: "card-tag is-flag", text: card.flag }) : null) : null,
      h("span", { class: "card-name" }, jaText(card.name)),
      card.sub ? h("span", { class: "card-title" }, jaText(card.sub)) : null,
      card.sub2 ? h("span", { class: "card-title" }, jaText(card.sub2)) : null);
      grid.append(tile);
    });
    if (!list.length) grid.append(h("p", { class: "muted small", text: emptyText || "没有匹配项" }));
    else if (gridNote) grid.append(h("p", { class: "muted small card-note", text: gridNote }));   // e.g. only the 「不登记」 tile is there
    count.textContent = countText(multi ? chosen : chosen);
    confirm.disabled = !multi && ![...entries, ...extra].some((c) => c.id === chosen && c.selectable);
    if (focusId) grid.querySelector(`[data-card-id="${CSS.escape(focusId)}"]`)?.focus();
  }

  /** Selection state only (classes, aria, check mark, count, 确定); keeps every tile element and focus in place. */
  function refreshSelection() {
    const all = [...grid.querySelectorAll(".card-tile")];
    const anyChosen = all.some((tile) => isChosen(tile.dataset.cardId));
    all.forEach((tile, index) => {
      const selected = isChosen(tile.dataset.cardId);
      tile.classList.toggle("is-selected", selected);
      tile.setAttribute("aria-selected", String(selected));
      if (!multi) tile.setAttribute("tabindex", selected || (!anyChosen && index === 0) ? "0" : "-1");
      const frame = tile.querySelector(".card-frame");
      const check = frame?.querySelector(".card-check");
      if (selected && frame && !check) frame.append(h("span", { class: "card-check", "aria-hidden": "true" }, icon("check", { size: 16 })));
      if (!selected && check) check.remove();
    });
    count.textContent = countText(chosen);
    confirm.disabled = !multi && ![...entries, ...extra].some((c) => c.id === chosen && c.selectable);
  }

  const search = h("input", { class: "input", type: "search", placeholder: searchPlaceholder, "aria-label": searchPlaceholder,
    oninput: (e) => { filter = e.target.value; paint(); } });
  const scope = extra.length ? h("div", { class: "segmented text-seg card-scope", role: "radiogroup", "aria-label": "显示范围" },
    [[scopeLabels[0], false], [scopeLabels[1], true]].map(([label, wantAll]) => {
      const button = h("button", { class: "segment", type: "button", role: "radio", text: label,
        onclick: () => { showAll = wantAll; scope.querySelectorAll(".segment").forEach((b, i) => { const on = (i === 1) === wantAll; b.classList.toggle("is-selected", on); b.setAttribute("aria-checked", String(on)); }); paint(); } });
      button.classList.toggle("is-selected", wantAll === showAll);
      button.setAttribute("aria-checked", String(wantAll === showAll));
      return button;
    })) : null;

  // 2026-10-24 (user: dropdowns are slow): a short filter (稀有度, 特长) is a row of pills, a long one (偶像) an in-place
  // filterable list. The dialog paints itself, so a control is rebuilt when its value changes.
  const controls = [];
  const filterControl = (f, i) => {
    const pick = (value) => {
      filterValues[i] = value;
      const next = filterControl(f, i);
      controls[i].replaceWith(next);
      controls[i] = next;
      paint();
    };
    const focusKey = `${titleId}-f${i}`;
    // 2026-10-26 role-play A12: each row names what it filters (three rows of 「全部」 told nothing)
    const group = (control, long = false) => h("div", { class: `card-filter-group${long ? " is-long" : ""}`, dataset: { filter: f.label } },
      h("span", { class: "card-filter-label", text: f.label }), control);
    if (f.options.length <= 8) {
      return group(choiceChips({ label: f.label, options: f.options.map(([v, t]) => [v, v ? t : "全部", v ? null : f.label]), value: filterValues[i],
        onPick: pick, focusKey, cls: "card-filter" }));
    }
    return group(listPicker({ label: f.label, items: f.options.map(([v, t]) => ({ value: v, text: t })), value: filterValues[i], onPick: pick,
      focusKey, cls: "card-filter card-filter-list", filterPlaceholder: `筛选${f.label}` }), true);
  };
  filters.forEach((f, i) => controls.push(filterControl(f, i)));
  const long = (i) => filters[i].options.length > 8;
  const selects = [...controls.filter((_, i) => !long(i)), ...controls.filter((_, i) => long(i))];   // pill rows first, the list last
  const bulkBar = multi && bulk ? [
    h("button", { class: "btn btn-small btn-text", type: "button", onclick: () => { tiles().filter((c) => c.selectable).forEach((c) => chosen.add(c.id)); paint(); } }, "全选可见"),
    h("button", { class: "btn btn-small btn-text", type: "button", onclick: () => { tiles().forEach((c) => chosen.delete(c.id)); paint(); } }, "清除可见"),
  ] : [];
  const filterRow = selects.length || bulkBar.length ? h("div", { class: "card-filters" }, selects, bulkBar) : null;

  dialog.append(
    h("div", { class: "card-dialog-head" },
      h("h2", { id: titleId, class: "dialog-title", text: title }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", title: "关闭", onclick: () => finish(false) }, icon("close", { size: 16 }))),
    h("div", { class: "card-toolbar" }, h("div", { class: "search-wrap grow" }, icon("search", { size: 16 }), search), scope),
    // Native append would render a null filter row as the text "null".
    ...(filterRow ? [filterRow] : []),
    grid,
    h("div", { class: "dialog-actions card-actions" }, h("div", { class: "card-foot" }, foot, count),
      h("button", { class: "btn", type: "button", onclick: () => finish(false) }, "取消"), confirm));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
  document.body.append(dialog);
  paint();
  dialog.showModal();
  (grid.querySelector('[tabindex="0"]') || search).focus();
  return dialog;
}

// ---------------------------------------------------------------- multi-select item picker (switch groups)
function daysText(days) {
  return Array.isArray(days) && days.length ? `每月 ${days.join("/")} 日` : "";
}

/** Tiles for an item_picker group: one per member switch, in annotation order (sections from `section`). */
export function pickerItems(state, keys) {
  return keys.map((key) => {
    const note = optionNote(state, key);
    const item = itemSpec(state, note.item);
    const price = Number.isInteger(note.price) ? `${note.price.toLocaleString("en-US")} マニー` : "";
    const label = optionLabel(state, key);
    const jp = (key.split("·")[1] || "").replace(/@\d+$/, "");
    // 2026-10-11: tiles show the game's own name + price (the short Chinese label stays for the summary row and search)
    const name = jp || label;
    return {
      id: key, selectable: true, name, section: note.section || null, badge: note.discount ? note.discount.replace("OFF", "") : "",
      sub: price, sub2: daysText(note.days), search: `${jp} ${label} ${note.section || ""}`,
      tip: [jp, label, price, note.discount, daysText(note.days)].filter(Boolean).join("\n"),
      visual: () => h("span", { class: "card-img item-tile-img", "aria-hidden": "true" }, itemIcon(item, { size: 48, label: "" })),
    };
  });
}

/** Summary for the inline row: 「未选」 / 「银训练券」 / 「银训练券 等 2 项」. */
export function groupSummary(state, group, keys, values) {
  const on = keys.filter((k) => values[k] === "Yes");
  if (!on.length) return groupNote(state, group).empty || "未选";
  const first = optionLabel(state, on[0]);
  return on.length === 1 ? first : `${first} 等 ${on.length} 项`;
}

/** Opens the multi-select picker; `onApply(changes)` receives {key: "Yes" | "No"} for the switches that changed. */
export function openItemPicker(state, { group, keys, values, onApply }) {
  const note = groupNote(state, group);
  const initial = keys.filter((k) => values[k] === "Yes");
  return openGridPicker({
    title: note.title || note.label || group, entries: pickerItems(state, keys), multi: true, initial,
    searchPlaceholder: "搜索商品", emptyText: "没有匹配的商品", className: "item-dialog",
    foot: note.note ? h("span", { class: "card-cost item-note" }, jaText(note.note)) : null,
    countText: (chosen) => `已选 ${chosen.size} / 共 ${keys.length}`,
    onCommit: (chosen) => {
      const changes = {};
      for (const key of keys) {
        const want = chosen.has(key) ? "Yes" : "No";
        if (want !== values[key]) changes[key] = want;
      }
      if (Object.keys(changes).length) onApply(changes);
    },
  });
}

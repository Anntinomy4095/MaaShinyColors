// 「持有卡牌」 Lv / 凸 editor (prefs.owned_cards.details): one row per owned card with a level box (blank = 未知) and
// 凸 pills 0–4 (none lit = 未知; tapping the lit one clears it; 2026-10-24 replaces the select). The values are only input for the 自动编队 recommender (agent/core/team_recommend.py), which
// leaves a card out of its search while either value is unknown; nothing here authorises a run. Pure helpers first
// (unit-tested with node), then one dialog. Styles live in styles/team-details.css, linked on first open (CSP 'self').
import { h, icon, nextId, jaText } from "./dom.js";
import { anyCardArt } from "./game-assets.js";
import { attrBadge } from "./team-picker.js";
import { cleanDetails, MAX_LEVEL, MAX_LIMIT_BREAK } from "./team-store.js";

const STYLE_HREF = "styles/team-details.css";

/** "" / spaces -> null (unknown); a whole number 1..MAX_LEVEL -> number; anything else -> undefined (invalid). */
export function parseLevel(text) {
  const raw = String(text ?? "").trim();
  if (!raw) return null;
  if (!/^\d{1,3}$/.test(raw)) return undefined;
  const value = Number(raw);
  return value >= 1 && value <= MAX_LEVEL ? value : undefined;
}

/** "" -> null (unknown); "0".."4" -> number; anything else -> undefined. */
export function parseLimitBreak(text) {
  const raw = String(text ?? "");
  if (raw === "") return null;
  if (!/^[0-9]$/.test(raw)) return undefined;
  const value = Number(raw);
  return value <= MAX_LIMIT_BREAK ? value : undefined;
}

/** Owned lists with `edits` ({id: {level, limit_break}} where null = unknown) applied; returns the new details. */
export function applyDetailEdits(owned, edits) {
  const ids = new Set([...(owned.p || []), ...(owned.s || [])]);
  const next = { ...(owned.details || {}) };
  for (const [id, edit] of Object.entries(edits || {})) {
    if (!ids.has(id)) continue;
    const item = { ...(next[id] || {}) };
    for (const key of ["level", "limit_break"]) {
      if (!(key in edit)) continue;
      if (edit[key] === null) delete item[key];
      else item[key] = edit[key];
    }
    next[id] = item;
  }
  return cleanDetails(next, ids);
}

/** {known, total}: owned cards whose Lv and 凸 are both filled in. */
export function detailsSummary(owned) {
  const ids = [...(owned.p || []), ...(owned.s || [])];
  const details = owned.details || {};
  const known = ids.filter((id) => Number.isInteger(details[id]?.level) && Number.isInteger(details[id]?.limit_break)).length;
  return { known, total: ids.length };
}

/** Link styles/team-details.css once (also used by the 自动编队 engine block in team-field.js). */
export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

function cardName(id) {
  const art = anyCardArt(id);
  return { art, name: (art?.idol || art?.idolEn || id).replace(/\s+/g, ""), title: art?.title || "" };
}

/** Dialog over the owned cards; `onApply(details)` receives the full cleaned details object. */
export function openDetailsEditor({ owned, onApply }) {
  ensureStyles();
  const titleId = nextId("details");
  const edits = {};
  const errors = new Set();
  const dialog = h("dialog", { class: "dialog card-dialog team-details-dialog", "aria-labelledby": titleId });
  const confirm = h("button", { class: "btn btn-primary", type: "button", onclick: () => finish(true) }, "确定");
  const status = h("span", { class: "card-count muted small", role: "status" });
  let filter = "";

  const refresh = () => {
    const merged = { ...owned, details: applyDetailEdits(owned, edits) };
    const { known, total } = detailsSummary(merged);
    status.textContent = errors.size ? `${errors.size} 项格式不对（Lv 1–${MAX_LEVEL}）` : `Lv/凸 已填写 ${known} / ${total}`;
    confirm.disabled = errors.size > 0;
  };

  function finish(ok) {
    dialog.close();
    dialog.remove();
    if (ok && !errors.size) onApply(applyDetailEdits(owned, edits));
  }

  const row = (id) => {
    const { art, name, title } = cardName(id);
    const current = owned.details?.[id] || {};
    const levelId = nextId("lv");
    const level = h("input", {
      class: "input team-details-level", id: levelId, type: "text", inputmode: "numeric", maxlength: "3",
      placeholder: "未知", "aria-label": `${name} Lv`, value: current.level ?? "",
      oninput: (e) => {
        const value = parseLevel(e.target.value);
        if (value === undefined) { errors.add(id); e.target.setAttribute("aria-invalid", "true"); }
        else { errors.delete(id); e.target.removeAttribute("aria-invalid"); edits[id] = { ...(edits[id] || {}), level: value }; }
        refresh();
      },
    });
    let lbValue = current.limit_break ?? null;
    const lb = h("span", { class: "chip-row team-details-lb", role: "group", "aria-label": `${name} 凸数（未点亮 = 未知）` },
      [0, 1, 2, 3, 4].map((n) => h("button", { type: "button", class: `chip${lbValue === n ? " is-on" : ""}`, "aria-pressed": String(lbValue === n),
        title: `${n} 凸`, onclick: (e) => {
          lbValue = lbValue === n ? null : n;
          edits[id] = { ...(edits[id] || {}), limit_break: parseLimitBreak(lbValue === null ? "" : String(lbValue)) };
          e.currentTarget.parentElement.querySelectorAll("button").forEach((b, i) => { b.classList.toggle("is-on", i === lbValue); b.setAttribute("aria-pressed", String(i === lbValue)); });
          refresh();
        } }, String(n))));
    const thumb = art?.src
      ? h("img", { class: "team-details-img", src: art.src, alt: "", width: "36", height: "36", loading: "lazy", decoding: "async" })
      : h("span", { class: "team-details-img card-placeholder", "aria-hidden": "true" }, icon("card", { size: 16 }));
    return h("li", { class: "team-details-row", dataset: { cardId: id, search: `${name} ${title} ${art?.idolEn || ""} ${id}`.toLowerCase() } },
      thumb,
      h("label", { class: "team-details-name", for: levelId, title: `${name}${title ? `【${title}】` : ""}\n${id}` },
        h("span", {}, attrBadge(art?.specialty, { size: 16, cls: "team-details-attr" }), jaText(name)), title ? h("span", { class: "muted small" }, jaText(title)) : null),
      h("span", { class: "team-details-inputs" }, h("span", { class: "muted small", "aria-hidden": "true", text: "Lv" }), level,
        h("span", { class: "muted small", "aria-hidden": "true", text: "凸" }), lb));
  };

  const section = (label, ids) => ids.length ? [h("li", { class: "card-section", role: "presentation", text: `${label}（${ids.length}）` }), ids.map(row)] : [];
  const list = h("ul", { class: "team-details-list" }, section("P 卡", owned.p || []), section("S 卡", owned.s || []));
  const search = h("input", { class: "input", type: "search", placeholder: "搜索偶像 / 卡名", "aria-label": "搜索偶像 / 卡名",
    oninput: (e) => {
      filter = e.target.value.trim().toLowerCase();
      list.querySelectorAll(".team-details-row").forEach((li) => { li.hidden = Boolean(filter) && !li.dataset.search.includes(filter); });
    } });
  const empty = !(owned.p?.length || owned.s?.length);

  dialog.append(
    h("div", { class: "card-dialog-head" },
      h("h2", { id: titleId, class: "dialog-title", text: "持有卡牌 Lv / 凸" }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", title: "关闭", onclick: () => finish(false) }, icon("close", { size: 16 }))),
    h("p", { class: "team-details-hint muted small" },
      jaText("Lv 或凸留空的卡不参与推荐编队")),
    h("div", { class: "card-toolbar" }, h("div", { class: "search-wrap grow" }, icon("search", { size: 16 }), search)),
    empty ? h("p", { class: "muted small", text: "持有卡牌为空：先标记持有的卡" }) : list,
    h("div", { class: "dialog-actions card-actions" }, h("div", { class: "card-foot" }, status),
      h("button", { class: "btn", type: "button", onclick: () => finish(false) }, "取消"), confirm));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
  document.body.append(dialog);
  refresh();
  dialog.showModal();
  search.focus();
  return dialog;
}

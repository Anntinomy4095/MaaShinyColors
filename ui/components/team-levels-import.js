// Reviewed import of recognised Lv / 凸 (tools/owned_levels_scan.py -> ui/game-assets/owned-levels.js, local and
// gitignored) into 「持有卡牌」 details (prefs.owned_cards.details). Every reading is shown with its value, source frame
// and scan date; readings from an older scan than the current scan are marked 旧扫描 and start unticked. A value the
// user already entered is never overwritten silently: the row shows 当前 vs 识别 and the user picks which to keep
// (default 保留当前). Nothing is saved until 确定. Only owned cards can receive details. Pure helpers first (node-tested).
import { h, icon, nextId, jaText } from "./dom.js";
import { scanSuffix } from "./scan-version.js";
import { anyCardArt } from "./game-assets.js";
import { cleanDetails, MAX_LEVEL, MAX_LIMIT_BREAK } from "./team-store.js";
import { ensureStyles } from "./team-details.js";

const ID = /^[12]\d{9}$/;
const FRAME = /^[\w./-]{1,200}$/;
const FIELDS = ["level", "limit_break"];

const lvOk = (v) => Number.isInteger(v) && v >= 1 && v <= MAX_LEVEL;
const lbOk = (v) => Number.isInteger(v) && v >= 0 && v <= MAX_LIMIT_BREAK;

/** "YYYY-MM-DD" of the recording a frame path names (…-20261006-… / …20261007/…), else "". */
export function frameDate(path) {
  const m = /(?:^|[^\d])(20\d{2})(\d{2})(\d{2})(?!\d)/.exec(path || "");
  return m ? `${m[1]}-${m[2]}-${m[3]}` : "";
}

/**
 * Validated scan: {cards: [{id, level, limitBreak, confidence, frame, frames, date, stale}], generatedAt, scanDate}.
 * `stale` = the reading's recording date is before the scan date (or unknown).
 */
export function cleanLevelScan(data) {
  const out = { cards: [], generatedAt: "", scanDate: "", rejected: 0 };
  if (!data || typeof data !== "object" || data.version !== 1) return out;
  out.generatedAt = typeof data.generated_at === "string" ? data.generated_at.slice(0, 32) : "";
  out.scanDate = /^\d{4}-\d{2}-\d{2}/.test(out.generatedAt) ? out.generatedAt.slice(0, 10) : "";
  out.rejected = Number.isInteger(data.rejected_tiles) && data.rejected_tiles > 0 ? Math.min(data.rejected_tiles, 99999) : 0;
  const cards = data.cards && typeof data.cards === "object" && !Array.isArray(data.cards) ? data.cards : {};
  const selfScan = data.generated_by === "agent/core/card_list_scan.py";
  for (const [id, c] of Object.entries(cards).slice(0, 3000)) {
    if (!ID.test(id) || !c || typeof c !== "object") continue;
    const level = id.startsWith("2") && lvOk(c.level) ? c.level : null;   // P cards have no level
    const limitBreak = lbOk(c.limit_break) ? c.limit_break : null;
    if (level === null && limitBreak === null) continue;
    const frame = typeof c.frame === "string" && FRAME.test(c.frame) ? c.frame : "";
    // Dated by the newest frame that read the same value; a self-scrolling scan's frames ("support#3") are as new as the scan.
    const latest = typeof c.latest_frame === "string" && FRAME.test(c.latest_frame) ? c.latest_frame : frame;
    const date = frameDate(latest) || frameDate(frame) || (selfScan ? out.scanDate : "");
    out.cards.push({ id, level, limitBreak, frame, date, frames: Number.isInteger(c.frames) ? c.frames : 0,
      confidence: typeof c.confidence === "number" && Number.isFinite(c.confidence) ? Math.max(0, Math.min(1, c.confidence)) : null,
      stale: !date || (out.scanDate && date < out.scanDate) });
  }
  out.cards.sort((a, b) => a.id.localeCompare(b.id));
  return out;
}

export async function loadLevelScan() {
  try {
    const module = await import(`../game-assets/owned-levels.js${scanSuffix()}`);
    return cleanLevelScan(module.default);
  } catch {
    return cleanLevelScan(null);
  }
}

/**
 * One row per reading: {id, owned, read: {level, limit_break}, current: {...}, fill: [fields], same: [fields],
 * conflicts: [fields], stale, frame, date, defaultOn}. `fill` = empty now, reading present; `conflicts` = both present and
 * different. Rows for cards not in 持有卡牌 are listed but cannot be imported.
 */
export function levelImportRows(owned, scan) {
  const have = new Set([...(owned.p || []), ...(owned.s || [])]);
  return scan.cards.map((c) => {
    const read = { level: c.level, limit_break: c.limitBreak };
    const current = owned.details?.[c.id] || {};
    const fill = [], same = [], conflicts = [];
    for (const key of FIELDS) {
      if (read[key] === null) continue;
      if (current[key] === undefined) fill.push(key);
      else if (current[key] === read[key]) same.push(key);
      else conflicts.push(key);
    }
    const isOwned = have.has(c.id);
    const changes = fill.length + conflicts.length > 0;
    return { id: c.id, owned: isOwned, read, current: { ...current }, fill, same, conflicts, stale: c.stale, frame: c.frame,
      date: c.date, confidence: c.confidence, defaultOn: isOwned && changes && !c.stale };
  });
}

/**
 * New details after the confirmed import. `selected` = ids the user ticked; `useScan` = ids whose conflicting fields
 * take the reading (others keep the current value). Never touches unticked or unowned cards.
 */
export function applyLevelImport(owned, rows, selected, useScan = new Set()) {
  const ids = new Set([...(owned.p || []), ...(owned.s || [])]);
  const next = { ...(owned.details || {}) };
  for (const row of rows) {
    if (!row.owned || !selected.has(row.id)) continue;
    const item = { ...(next[row.id] || {}) };
    for (const key of row.fill) item[key] = row.read[key];
    if (useScan.has(row.id)) for (const key of row.conflicts) item[key] = row.read[key];
    next[row.id] = item;
  }
  return cleanDetails(next, ids);
}

const fmt = (v, p = false) => `${p ? "" : `${v.level != null ? `Lv${v.level}` : "Lv—"} · `}${v.limit_break != null ? `${v.limit_break}凸` : "凸—"}`;

/**
 * Dialog intro. Rows outside 「持有卡牌」 cannot be imported (their checkbox is disabled), so say how many and what
 * to do first; otherwise a fresh profile shows every row as 「未持有」 with no explanation (roleplay test 2026-10-07).
 */
export function levelImportHint(rows, generatedAt) {
  const stale = rows.filter((r) => r.stale).length;
  const unowned = rows.filter((r) => !r.owned).length;
  const parts = [`识别于 ${String(generatedAt || "").slice(0, 10) || "未知日期"}，共 ${rows.length} 张${stale ? `；${stale} 张来自较早扫描，默认不勾选` : ""}。`];
  if (unowned) {
    parts.push(unowned === rows.length
      ? "「持有卡牌」为空：先导入或勾选持有卡，再导入 Lv/凸。"
      : `${unowned} 张不在「持有卡牌」中，无法导入；先补进持有卡牌。`);
  }
  parts.push("已填写的值不会被覆盖，不同处需选择；按「确定」才保存。");
  return parts.join("");
}

/** Dialog; `onApply(details)` receives the full cleaned details only after 确定. */
export async function openLevelImport({ owned, onApply }) {
  const scan = await loadLevelScan();
  if (!scan.cards.length) {
    import("./common.js").then((m) => m.toast("没有 Lv/凸 识别结果：先用 tools/owned_levels_scan.py 读取已保存的偶像一览截图", "warning")).catch(() => {});
    return null;
  }
  ensureStyles();
  const rows = levelImportRows(owned, scan);
  const selected = new Set(rows.filter((r) => r.defaultOn).map((r) => r.id));
  const useScan = new Set();
  const titleId = nextId("lvimp");
  const dialog = h("dialog", { class: "dialog card-dialog team-details-dialog", "aria-labelledby": titleId });
  const status = h("span", { class: "card-count muted small", role: "status" });
  const confirm = h("button", { class: "btn btn-primary", type: "button", onclick: () => finish(true) }, "确定");
  const refresh = () => {
    const conflicts = rows.filter((r) => selected.has(r.id) && r.conflicts.length).length;
    status.textContent = `导入 ${selected.size} 张${conflicts ? ` · ${conflicts} 张与已填写不同` : ""}`;
    confirm.disabled = selected.size === 0;
  };
  function finish(ok) {
    dialog.close();
    dialog.remove();
    if (ok && selected.size) onApply(applyLevelImport(owned, rows, selected, useScan));
  }

  const row = (r) => {
    const art = anyCardArt(r.id);
    const name = (art?.idol || r.id).replace(/\s+/g, "");
    const changes = r.fill.length + r.conflicts.length > 0;
    const isP = r.id.startsWith("1");
    const box = h("input", { type: "checkbox", class: "checkbox", "aria-label": `导入 ${name}`, disabled: !r.owned || !changes,
      onchange: (e) => { if (e.target.checked) selected.add(r.id); else selected.delete(r.id); refresh(); } });
    box.checked = selected.has(r.id);
    const tags = [
      r.stale ? h("span", { class: "lv-tag is-stale", title: "来自较早的扫描，Lv/凸 可能已变化" }, `旧扫描 ${r.date ? r.date.slice(5) : "日期未知"}`)
        : h("span", { class: "lv-tag" }, r.date ? r.date.slice(5) : ""),
      !r.owned ? h("span", { class: "lv-tag" }, "未持有") : null,
      r.owned && !changes ? h("span", { class: "lv-tag" }, "与已填写相同") : null,
    ];
    let choice = null;
    if (r.owned && r.conflicts.length) {
      const group = nextId("lvc");
      const radio = (value, label) => {
        const input = h("input", { type: "radio", name: group, value, onchange: () => { if (value === "scan") useScan.add(r.id); else useScan.delete(r.id); refresh(); } });
        input.checked = value === "keep";
        return h("label", { class: "lv-choice" }, input, h("span", {}, label));
      };
      choice = h("div", { class: "lv-diff", role: "radiogroup", "aria-label": `${name} 冲突处理` },
        h("span", { class: "small" }, `当前 ${fmt(r.current, isP)} ≠ 识别 ${fmt(r.read, isP)}`),
        radio("keep", "保留当前"), radio("scan", "用识别值"));
    }
    return h("li", { class: `team-details-row lv-row${r.stale ? " is-stale" : ""}`, title: `${r.frame}${r.confidence != null ? `\n置信度 ${Math.round(r.confidence * 100)}%` : ""}` },
      box,
      h("span", { class: "team-details-name" }, h("span", {}, jaText(name)), art?.title ? h("span", { class: "muted small" }, jaText(art.title)) : null),
      h("span", { class: "lv-read num" }, fmt(r.read, isP)),
      h("span", { class: "lv-meta muted small" }, tags, h("span", { class: "lv-frame" }, r.frame.split("/").pop() || "")),
      choice);
  };
  const section = (label, list) => list.length ? [h("li", { class: "card-section", role: "presentation", text: `${label}（${list.length}）` }), list.map(row)] : [];
  const list = h("ul", { class: "team-details-list" }, section("P 卡", rows.filter((r) => r.id.startsWith("1"))), section("S 卡", rows.filter((r) => r.id.startsWith("2"))));
  dialog.append(
    h("div", { class: "card-dialog-head" },
      h("h2", { id: titleId, class: "dialog-title", text: "导入识别的 Lv / 凸" }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", title: "关闭", onclick: () => finish(false) }, icon("close", { size: 16 }))),
    h("p", { class: "team-details-hint muted small" }, jaText(levelImportHint(rows, scan.generatedAt))),
    list,
    h("div", { class: "dialog-actions card-actions" }, h("div", { class: "card-foot" }, status),
      h("button", { class: "btn", type: "button", onclick: () => finish(false) }, "取消"), confirm));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
  document.body.append(dialog);
  refresh();
  dialog.showModal();
  return dialog;
}

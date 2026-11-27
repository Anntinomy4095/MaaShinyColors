// 「扫描ノウハウ」: button gate, mode choice + confirmation, progress and result text for the
// supervised ノウハウブック一覧 scan that the HOST launches (knowhow.scan.start / stop), wired like 「扫描持有卡」
// (card-scan.js): same host conditions, the page only asks (after its own confirmation), watches the host's status.update
// `knowhow_scan` object and can stop. It supplies only the mode (calibrate | scan | newest + count), never a path, option
// or budget. Calibration comes first: it only measures the wheel steps and never changes the sort.
// 补读新书 (newest, 2026-10-25): the newest 1–10 books in 獲得順; offered when the library only lacks new books.
import { h, jaText } from "./dom.js";
import { factLine } from "./common.js";
import { BLOCKER_TEXT as CARD_BLOCKER_TEXT, reasonText as cardReasonText, gateTitle } from "./card-scan.js";

const CODE = /^[a-z_]{1,40}(?::[a-z0-9,-]{1,200})?$/;
const SAFE = /^[A-Za-z0-9_:.,-]{1,80}$/;

export { gateTitle };
export const MODES = [["scan", "扫描书库"]];
export const NEWEST_MAX = 10;      // agent/core/knowhow_scan.NEWEST_MAX: one screen of 獲得順 tiles

export const BLOCKER_TEXT = {
  ...CARD_BLOCKER_TEXT,
  card_scan_active: "正在扫描持有卡，请等它结束",
  knowhow_scan_active: "已有ノウハウ扫描进行中",
  invalid_mode: "扫描模式无效",
};

export const REASON_TEXT = {
  book_budget_exhausted: "本段读取本数已达上限",
  input_budget_exhausted: "本段操作次数已达上限",
  manual_game_input: "游戏被手动操作",
  grid_scroll_budget_exhausted: "网格滚动次数用完",
  grid_wheel_budget_before_bottom: "校准滚到上限仍没到底（步长已记录）",
  grid_overlap_lost: "网格滚动后与上一屏对不上（滚动步长不符）",
  grid_overlap_ambiguous: "网格滚动后对不准位置",
  detail_binding_lost: "打开的书与格子对不上",
  detail_mismatch: "详情与格子内容不一致",
  detail_overlap_lost: "详情滚动后与上一页对不上",
  detail_not_at_top: "详情不是从顶部开始",
  sort_changed: "扫描中排序变了",
  owned_changed: "扫描中所持数变了",
  orig_sort_not_restorable: "当前排序不是「デフォルト」或「ノウハウ評価値順」，请在游戏里改回后再扫",
  direction_not_desc: "当前不是降序，请在游戏里改回降序",
  popup_sort: "出现了未预期的排序弹窗",
  mode_protect: "游戏开着「保護」选择模式",
  mode_sale: "游戏开着「売却」选择模式",
  destructive_marker: "画面上有売却/保護确认，已停止",
  unknown_screen: "不在ノウハウブック一覧",
  picker_uncalibrated: "进入了编成选书页（未校准）",
  config_invalid: "扫描选项无效",
  newest_beyond_screen: "新书超出第一屏，请读取书库",
  owned_below_inventory: "所持数比书库记录少",
  topup_too_many: "新书超过 10 本",
  topup_misaligned: "最新的书与上次补读的对不上",
  sync_broken: "上次补读没对上",
  halted: "扫描已停止（原因见运行记录）",
};

/** Reason code -> text (card-scan codes and engine_exit_N are understood too). */
export function reasonText(reason) {
  if (typeof reason !== "string" || !SAFE.test(reason)) return "";
  return REASON_TEXT[reason] || cardReasonText(reason);
}

/**
 * Button state, same rule as cardScanGate: `policy.knowhow_scan_ready` (every knowhow-scan action and the entry enabled in
 * the host's table) plus the host blockers; the host re-checks all of it on start.
 */
export function knowhowScanGate(policy, host) {
  const policyReady = policy?.knowhow_scan_ready === true;
  const raw = policy?.knowhow_scan_reason;
  const reason = typeof raw === "string" && CODE.test(raw) ? raw : "";
  const scan = host?.knowhow_scan;
  const running = scan?.state === "running" || scan?.state === "stopping";
  let blocker = "";
  if (policyReady && host) {
    if (host.pipeline_input_enabled === false) blocker = "pipeline_input_disabled";
    else if (host.engine_running === true) blocker = "engine_active";
    else if (host.card_scan?.state === "running" || host.card_scan?.state === "stopping") blocker = "card_scan_active";
    else if (typeof scan?.blocker === "string" && CODE.test(scan.blocker)) blocker = scan.blocker;
  }
  const ready = policyReady && !blocker && !running;
  const blockText = blocker ? (BLOCKER_TEXT[blocker] || `现在不能开始（${blocker}）`) : "";
  return {
    ready, running, reason, blocker,
    label: "读取书库",
    title: ready
      ? "自动打开「アイドル → アイドル一覧 → ノウハウブック」：按评价值顺读取书库；只读，开始前会再确认"
      : running ? "扫描进行中"
      : blocker ? `现在不能扫描：${blockText}`
      : "此功能暂不可用：需要更新程序",
    detail: policyReady ? "" : `宿主动作表还没有 knowhow-scan 条目，需切换后重启宿主${reason ? `（${reason}）` : ""}`,
  };
}

export const CONFIRM_LINES = {
  scan: [
    "读取整个书库，结束后恢复排序",
    "不修改书库",
    "扫描时勿触碰游戏；可随时停止",
  ],
  newest: [
    "读取新书（最多 10 本），结束后恢复排序",
    "不修改书库",
  ],
};

const newestCount = (count) => (Number.isInteger(count) && count >= 1 && count <= NEWEST_MAX ? count : 0);

/** Modal confirmation with the mode choice; onConfirm(mode) runs only from the 开始 button. `count` (1–10) adds 补读新书. */
export function openKnowhowScanConfirm({ onConfirm, mode = "scan", count = 0 } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const n = newestCount(count);
  const modes = n ? [...MODES, ["newest", `补读新书 ${n} 本`]] : MODES;
  let chosen = modes.some(([k]) => k === mode) ? mode : "scan";
  const dialog = h("dialog", { class: "dialog card-scan-dialog", "aria-labelledby": "knowhow-scan-confirm-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  const lines = h("ul", { class: "card-scan-lines" });
  const fill = () => { lines.replaceChildren(...CONFIRM_LINES[chosen].map((line) => h("li", { class: "small" }, jaText(line)))); };
  const radios = h("div", { class: "kh-scan-modes", role: "radiogroup", "aria-label": "扫描模式" },
    modes.map(([key, label]) => h("label", { class: "small" },
      h("input", { type: "radio", name: "knowhow-scan-mode", value: key, checked: key === chosen ? "" : null,
        dataset: { knowhowScanMode: key }, onchange: () => { chosen = key; fill(); } }), ` ${label}`)));
  fill();
  dialog.append(
    h("h2", { id: "knowhow-scan-confirm-title", class: "dialog-title", text: "开始扫描ノウハウ？" }),
    radios, lines,
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { knowhowScanCancel: "1" }, onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-primary", dataset: { knowhowScanConfirm: "1" },
        onclick: () => { close(); onConfirm?.(chosen); } }, "开始")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

const clock = (s) => (Number.isFinite(s) && s >= 0 ? `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s) % 60).padStart(2, "0")}` : "—");
const n = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);

export function progressText(scan) {
  const reading = scan?.mode === "newest" ? "补读新书" : "读取书库";
  const where = scan?.entered ? reading : "正在进入ノウハウブック一覧";
  return `${where} · 滚轮 ${n(scan?.scrolls)} 次 · 打开 ${n(scan?.books)} 本 · 已存 ${n(scan?.frames)} 帧 · 用时 ${clock(scan?.elapsed_s)}`;
}

export function knowhowScanProgress(scan, { onStop } = {}) {
  const stopping = scan?.state === "stopping";
  return h("div", { class: "card-scan-progress", role: "status", dataset: { knowhowScanProgress: scan?.state || "running" } },
    h("span", { class: "card-scan-title small", text: stopping ? "正在停止…" : scan?.mode === "newest" ? "补读新书中（只读，请勿触碰游戏画面）"
      : "扫描ノウハウ进行中（只读，请勿触碰游戏画面）" }),
    h("span", { class: "card-scan-line muted small num", dataset: { knowhowScanLine: "1" }, text: progressText(scan) }),
    h("button", { type: "button", class: "btn btn-small btn-emergency", disabled: stopping ? "" : null, dataset: { focusKey: "knowhow-scan-stop" },
      title: "停止扫描并保留已读资料；排序未恢复时会显示提示", onclick: () => onStop?.() }, stopping ? "停止中…" : "停止"));
}

export function updateKnowhowScanProgress(root, scan) {
  const line = root?.querySelector?.("[data-knowhow-scan-line]");
  if (line) line.textContent = progressText(scan);
  return Boolean(line);
}

/** Last-result note (plain text; calibration reports the measured steps, a scan its coverage and review count). */
export function knowhowScanResultNote(scan) {
  if (!scan || scan.state !== "finished") return null;
  const reason = reasonText(scan.reason);
  let text;
  let tone = "warning";
  // 2026-11-24: a stopped scan resumes while the game was not touched since (host `resumable`).
  const again = scan.resumable === true ? "再次扫描从断点接续" : "再次扫描将重新读取全库";
  if (scan.outcome === "scanned") {
    text = `上次扫描读完整个书库${Number.isFinite(scan.books_read) ? `（${scan.books_read} 本）` : ""}。`; tone = "success";
  } else if (scan.outcome === "newest_read") {
    text = `新书已读入书库${Number.isFinite(scan.books_read) ? `（${scan.books_read} 本）` : ""}。`; tone = "success";
  } else if (scan.outcome === "library_up_to_date") {
    text = "书库已是最新，没有新书。"; tone = "success";
  } else if (scan.outcome === "needs_full_scan") {
    text = `所持数与书库对不上${reason ? `（${reason}）` : ""}：请读取书库。`;
  } else if (scan.outcome === "partial") {
    text = `上次扫描读了一部分${Number.isFinite(scan.books_read) ? `（${scan.books_read} 本）` : ""}：${reason || "未知原因"}。已读资料已保存；${again}。`;
  } else if (scan.outcome === "stopped") text = `上次扫描已停止：${reason || "已停止"}。${scan.resumable === true ? `${again}。` : ""}`;
  else if (scan.outcome === "uncertain") text = `上次扫描结果不确定：${reason || "进程被终止"}。请在游戏中核对，可能需要手动改回排序。`;
  else text = `上次扫描失败：${reason || "未知原因"}。`;
  if (scan.sort_restore_needed === true) text += "排序仍为评价值顺，可在游戏内调整。";
  const review = Number.isFinite(scan.needs_review) && scan.needs_review > 0 ? `其中 ${scan.needs_review} 本需要你确认。` : "";
  const folder = typeof scan.frames_folder === "string" && SAFE.test(scan.frames_folder) ? `存图：dev_assets/private/knowhow-scan/${scan.frames_folder}` : "";
  return h("p", { class: `card-scan-note small tone-${tone}`, role: "status", dataset: { knowhowScanNote: scan.outcome || "" } },
    jaText(text), review ? jaText(review) : null, folder ? h("span", { class: "muted num" }, ` ${folder}`) : null);
}

// ----------------------------------------------------------------------------------------------- 自动卖书 → 书库
/**
 * Is the library current enough to judge new books against it (agent/core/knowhow_keep.py)? From the
 * local knowhow.js: books recorded (`have`), the 所持数 last read (`total`) and books a Produce end left unread
 * (`pending`). "ok" = nothing missing; "topup" = only new books missing (≤ NEWEST_MAX: 补读新书 `count`);
 * "scan" = never read, 所持数 unknown, fewer books in the game than recorded (sold) or too many missing (读取书库).
 * scan.sync (knowhow_scan.library_sync, the state the sell executor checks) decides when present: up_to_date → ok,
 * new_books → topup, every other state → scan. Without it: physical copies vs scan.owned_total.
 */
export function librarySync(books, scan = null, pendingCount = 0) {
  const sync = scan?.sync;
  if (sync?.state) {
    const out = { have: sync.known ?? 0, total: sync.owned_now, pending: sync.pending || 0, count: 0, sync: sync.state };
    if (sync.state === "up_to_date") return { ...out, state: "ok" };
    if (sync.state === "new_books" && sync.new > 0 && sync.new <= NEWEST_MAX) return { ...out, count: sync.new, state: "topup" };
    return { ...out, state: "scan" };
  }
  const have = (books || []).reduce((sum, b) => sum + (Number.isInteger(b?.count) && b.count > 0 ? b.count : 1), 0);
  const total = Number.isInteger(scan?.owned_total) ? scan.owned_total : null;
  const pending = Number.isInteger(pendingCount) && pendingCount > 0 ? pendingCount : 0;
  const out = { have, total, pending, count: 0 };
  if (!have || total === null || have > total) return { ...out, state: "scan" };
  const count = Math.max(total - have, pending);
  if (!count) return { ...out, state: "ok" };
  return { ...out, count, state: count <= NEWEST_MAX ? "topup" : "scan" };
}

export const SELL_SWITCH = "Produce自动卖书";
export const SELL_STRATEGY = "Produce卖书策略";

/** The user just turned 自动卖书 on, or changed its strategy while it is on: check the library. */
export function sellPromptDue(option, key, value, defaultSell = "No") {
  if (key === SELL_SWITCH) return value === "Yes";
  return key === SELL_STRATEGY && (option?.[SELL_SWITCH] ?? defaultSell) === "Yes";
}

/**
 * 「卖书前先读取书库」: the library numbers and one action (补读新书 N 本 when the host can and only new books are
 * missing, else 读取书库). onRead(mode, count) runs only from that button; 稍后 just closes.
 */
export function openLibrarySyncPrompt(sync, { onRead, newest = true } = {}) {
  if (!sync || sync.state === "ok") return null;
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const topup = sync.state === "topup" && newest;
  const dialog = h("dialog", { class: "dialog card-scan-dialog", "aria-labelledby": "knowhow-sync-title", dataset: { knowhowSync: sync.state, knowhowSyncState: sync.sync || "" } });
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.append(
    h("h2", { id: "knowhow-sync-title", class: "dialog-title", text: "卖书前先读取书库" }),
    factLine([
      { key: "kh-sync-have", label: "书库", value: sync.have ? `${sync.have} 本` : "未读取", missing: !sync.have },
      sync.total !== null ? { key: "kh-sync-total", label: "所持", value: `${sync.total} 本`, tone: sync.have === sync.total ? null : "warning" } : null,
      sync.pending ? { key: "kh-sync-pending", label: "待读", value: `${sync.pending} 本`, tone: "accent" } : null,
    ], { label: "书库与所持数" }),
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { knowhowSyncLater: "1" }, onclick: close }, "稍后"),
      h("button", { type: "button", class: "btn btn-primary", dataset: { knowhowSyncRead: topup ? "newest" : "scan" },
        onclick: () => { close(); onRead?.(topup ? "newest" : "scan", topup ? sync.count : 0); } },
      topup ? `补读新书 ${sync.count} 本` : "读取书库")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

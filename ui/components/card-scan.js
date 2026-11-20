// 「扫描持有卡」: button gate, confirmation, progress and result text for the self-scrolling
// アイドル一覧 scan that the HOST launches (cards.scan.start / stop). The page only asks (after its own confirmation), watches
// the host's status.update `card_scan` object and can stop; it supplies no path, option or budget and never writes the
// owned-card profile itself: once the host reports the import files written, resource-panel.js re-reads them and the normal
// auto-accept flow applies (high-confidence automatically, the rest to 需要你确认).
import { h, jaText } from "./dom.js";

const CODE = /^[a-z_]{1,40}(?::[a-z0-9,-]{1,200})?$/;
const SAFE = /^[A-Za-z0-9_:.-]{1,80}$/;

/** Host refusal / blocker codes -> plain Chinese (unknown codes are shown as the code itself). */
export const BLOCKER_TEXT = {
  pipeline_input_disabled: "程序未启用游戏操作，无法开始扫描",
  host_not_connected: "游戏窗口未连接",
  host_guard_active: "游戏窗口现在不能操作（先处理弹窗或解锁桌面）",
  engine_active: "有任务正在运行，结束后再扫描",
  other_input_activity: "检测到另一个程序正在操作游戏，请等它结束",
  already_running: "已有扫描进行中",
};

export const REASON_TEXT = {
  scroll_budget_exhausted: "滚动次数用完仍没有到底",
  frame_budget_exhausted: "观察帧数用完",
  hold_budget_exhausted: "页面一直在加载，等待超时",
  list_filter_active: "列表开着筛选（请在游戏里取消筛选后再扫描）",
  list_sort_unknown: "无法确认列表的排序",
  list_order_mismatch: "列表排序不是预期的（Lv 降序）",
  list_order_changed: "扫描中列表排序变了",
  tab_switch_failed: "切换 S/P 标签失败",
  overlap_lost: "滚动后与上一页对不上（页面可能还在加载或有弹窗）",
  abort: "中途离开了列表页面",
  card_library_missing: "本机还没有卡资料：先在设置里「下载卡资料」",
  user_stop: "你停止了扫描",
  deadline: "超过预定时长（50 分钟）",
  killed_after_stop: "停止后进程未退出，已被终止",
  summary_unreadable: "运行摘要读不出来",
  import_files_missing: "扫描完成但没有写出导入文件",
  import_copy_failed: "扫描完成，但导入文件没能放进界面目录",
  python_env_missing: "找不到项目 Python 环境（.venv）",
  gacha_announcement_unhandled: "卡池公告挡住首页：请在游戏里关闭后再开始",
};

const TAB_TEXT = { support: "S 卡", produce: "P 卡" };

/** Reason code -> text; engine_exit_N and unknown codes stay readable and are never HTML. */
export function reasonText(reason) {
  if (typeof reason !== "string" || !SAFE.test(reason)) return "";
  if (REASON_TEXT[reason]) return REASON_TEXT[reason];
  if (/^engine_exit_-?\d{1,6}$/.test(reason)) return `扫描程序异常退出（${reason.slice(12)}）`;
  return reason;
}

/**
 * Button state. `policy` = policy.get summary, `host` = status.update fields (may be missing in tests / preview).
 * ready: enabled. running: a scan is in progress (the button turns into the progress block). Anything the host
 * reports as blocking keeps the button disabled with the reason in its tooltip; the host re-checks all of it on start.
 */
export function cardScanGate(policy, host) {
  const policyReady = policy?.card_scan_ready === true;
  const raw = policy?.card_scan_reason;
  const reason = typeof raw === "string" && CODE.test(raw) ? raw : "";
  const scan = host?.card_scan;
  const running = scan?.state === "running" || scan?.state === "stopping";
  let blocker = "";
  if (policyReady && host) {
    if (host.pipeline_input_enabled === false) blocker = "pipeline_input_disabled";
    else if (host.engine_running === true) blocker = "engine_active";
    else if (typeof scan?.blocker === "string" && CODE.test(scan.blocker)) blocker = scan.blocker;
  }
  const ready = policyReady && !blocker && !running;
  const blockText = blocker ? (BLOCKER_TEXT[blocker] || `现在不能开始（${blocker}）`) : "";
  return {
    ready, running, reason, blocker,
    label: "扫描持有卡",
    title: ready
      ? "读取 S/P 卡的持有、Lv 和凸数 · 约 10–20 分钟"
      : running ? "扫描进行中"
      : blocker ? `现在不能扫描：${blockText}`
      : "此功能暂不可用：需要更新程序",
    // Developer view only (设置 → 开发者): the exact condition behind 暂不可用.
    detail: policyReady ? "" : `动作与入口仍禁用，需监督校准（单格滚轮位移与完整扫描）后开放${reason ? `（${reason}）` : ""}`,
  };
}

/** Button tooltip: the player sentence; the technical condition is appended only in the developer view. */
export function gateTitle(gate, developer = false) {
  return developer && gate?.detail ? `${gate.title}
${gate.detail}` : gate?.title || "";
}

export const CONFIRM_LINES = [
  "约 10–20 分钟，最长 50 分钟",
  "扫描时勿触碰游戏；可随时停止",
  "不确定的卡放入「需要你确认」",
];

// 2026-11-23 (user): the scan compares every tile with the local card images; without them it downloads them first.
export const LIBRARY_LINE = "本机还没有卡图：先自动下载（约 5 分钟），完成后开始扫描";

/** Modal confirmation. onConfirm runs only from the 开始扫描 button; Esc / 取消 / backdrop do nothing. */
export function openCardScanConfirm({ onConfirm, libraryMissing = false } = {}) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  const dialog = h("dialog", { class: "dialog card-scan-dialog", "aria-labelledby": "card-scan-confirm-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.append(
    h("h2", { id: "card-scan-confirm-title", class: "dialog-title", text: "开始扫描持有卡？" }),
    h("ul", { class: "card-scan-lines" }, [...(libraryMissing ? [LIBRARY_LINE] : []), ...CONFIRM_LINES]
      .map((line) => h("li", { class: "small" }, jaText(line)))),
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", dataset: { cardScanCancel: "1" }, onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-primary", dataset: { cardScanConfirm: "1" },
        onclick: () => { close(); onConfirm?.(); } }, "开始扫描")));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

const clock = (s) => (Number.isFinite(s) && s >= 0 ? `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s) % 60).padStart(2, "0")}` : "—");

/** One-line progress from the host's card_scan object (tab, wheel steps, saved frames, elapsed). */
export function progressText(scan) {
  const tab = TAB_TEXT[scan?.tab] ? `${TAB_TEXT[scan.tab]}标签` : "正在进入列表";
  const n = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);
  return `${tab} · 已滚动 ${n(scan?.scrolls)} 次 · 已读 ${n(scan?.frames)} 帧 · 用时 ${clock(scan?.elapsed_s)}`;
}

/** The progress block shown instead of the button while a scan runs; 停止 only writes the stop file (host side). */
export function cardScanProgress(scan, { onStop } = {}) {
  const stopping = scan?.state === "stopping";
  return h("div", { class: "card-scan-progress", role: "status", dataset: { cardScanProgress: scan?.state || "running" } },
    h("span", { class: "card-scan-title small", text: stopping ? "正在停止扫描…" : "扫描持有卡进行中（只读，请勿触碰游戏画面）" }),
    h("span", { class: "card-scan-line muted small num", dataset: { cardScanLine: "1" }, text: progressText(scan) }),
    h("button", { type: "button", class: "btn btn-small btn-emergency", disabled: stopping ? "" : null, dataset: { focusKey: "holdings-card-scan-stop" },
      title: "停止扫描：回到首页后结束，已读到的部分不会导入", onclick: () => onStop?.() }, stopping ? "停止中…" : "停止"));
}

/** Per-second tick: refresh only the progress line in place (no panel rebuild, so icons never flicker). */
export function updateCardScanProgress(root, scan) {
  const line = root?.querySelector?.("[data-card-scan-line]");
  if (line) line.textContent = progressText(scan);
  return Boolean(line);
}

/** Last-result note under the button (plain reason; never claims success without the host's `imported`). */
export function cardScanResultNote(scan) {
  if (!scan || scan.state !== "finished") return null;
  const cards = Number.isInteger(scan.cards) ? `读到 ${scan.cards} 张` : "";
  const reason = reasonText(scan.reason);
  let text;
  let tone = "warning";
  if (scan.outcome === "scanned" && scan.imported === true) { text = `上次扫描完成${cards ? `，${cards}` : ""}，清晰的结果已确认；其余请在「需要你确认」中核对。`; tone = "success"; }
  else if (scan.outcome === "scanned") text = `上次扫描已读完，但未导入：${reason || "导入文件不可用"}。`;
  else if (scan.outcome === "uncertain") text = `上次扫描结果不确定：${reason || "进程被终止"}。请在游戏中核对；结果未导入。`;
  else if (scan.outcome === "stopped") text = `上次扫描已停止：${reason || "已停止"}。已读部分未导入。`;
  else if (scan.outcome === "partial") text = `上次扫描未读完：${reason || "原因未知"}。已读部分未导入，可再扫一次。`;
  else text = `上次扫描失败：${reason || "原因未知"}。结果未导入。`;
  const tabs = Array.isArray(scan.tabs) && scan.outcome !== "scanned"
    ? scan.tabs.filter((t) => TAB_TEXT[t?.tab]).map((t) => `${TAB_TEXT[t.tab]}${t.complete ? "已读完" : `读到 ${Number.isInteger(t.accepted) ? t.accepted : 0} 格`}`).join("，") : "";
  return h("p", { class: `card-scan-note small tone-${tone}`, role: "status", dataset: { cardScanNote: scan.outcome || "" } },
    jaText(text), tabs ? jaText(`（${tabs}）`) : null);
}

// 设置 → 通知 / 快捷键 / 问题反馈 / 更新 / 关于 (2026-10-15, P0).
// The page only names an operation (app.support op=…); the host owns every path, URL, file list and the hotkey.
import { h } from "./dom.js";

// Notification switches grouped by what happened (all keys stay in prefs.notify; missing = on).
export const NOTIFY_GROUPS = [
  ["完成", ["all_done"]],
  ["出错", ["failed", "uncertain", "guard_refused"]],
  ["需接手", ["takeover"]],
  ["提醒", ["free_draw"]],
];
export const SYSTEM_NOTIFY_HINT = "运行结果同时显示为 Windows 系统通知（右下角），窗口最小化或隐藏到托盘时也能看到";
export const HOTKEY_HINT = "任何窗口中按下即停止运行中的任务";
export const REPORT_HINT = "打包最近的运行记录、输入日志、W.I.N.G. 决策、设置和最近 10 张失败截图。不含登录信息和游戏素材，本机路径与用户名已替换；只保存在本机，不上传。";
export const REPORT_SHORT = "含运行记录、日志、设置、失败截图 · 不含登录信息 · 不上传";

const UPDATE_REASON = { network: "网络不可用", timeout: "连接超时", http_403: "请求过于频繁，请稍后再试", http_404: "找不到发布页" };

/** Normalised status.update.support: {hotkey: {enabled, registered, supported}, update: {...}, lastReport}. */
export function supportModel(host) {
  const s = host?.support || {};
  const hotkey = s.hotkey || {};
  return {
    hotkey: { enabled: hotkey.enabled === true, registered: hotkey.registered === true, supported: hotkey.supported === true },
    update: s.update || { state: "idle" },
    lastReport: typeof s.last_report === "string" ? s.last_report : null,
  };
}

/** One line for the 更新 card: what the last check found. */
export function updateText(update, info) {
  if (!info?.repo) return "未设置发布地址";
  switch (update?.state) {
    case "checking": return "正在检查…";
    case "failed": return `检查失败：${UPDATE_REASON[update.reason] || "未知原因"}`;
    case "done":
      if (!update.latest) return "发布页暂无版本";
      return update.newer ? `有新版本 ${update.latest}${update.published_at ? `（${String(update.published_at).slice(0, 10)}）` : ""}` : `已是最新版本（${update.latest}）`;
    default: return "尚未检查";
  }
}

/** 构建 line: package commit + build time, or 源码运行 / 界面预览. */
export function buildText(build) {
  if (!build) return "—";
  if (build.kind === "package") {
    const at = typeof build.built_utc === "string" ? build.built_utc.slice(0, 16).replace("T", " ") + " UTC" : "";
    return `测试包 ${build.commit || "?"}${build.dirty ? "（含未提交改动）" : ""}${at ? ` · ${at}` : ""}`;
  }
  return build.kind === "preview" ? "界面预览" : "源码运行";
}

export function hotkeyView(state, actions, switchRow) {
  const m = supportModel(state.host);
  const keys = state.supportInfo?.hotkey_keys || "Ctrl + Alt + Shift + S";
  const hint = !m.hotkey.supported ? "需要在游戏窗口中使用" : m.hotkey.enabled && !m.hotkey.registered ? "快捷键被其他程序占用，未生效" : HOTKEY_HINT;
  return h("div", {},
    h("ul", { class: "toggle-list" }, switchRow("hotkey-stop", `紧急停止 ${keys}`, m.hotkey.enabled,
      () => actions.support?.("hotkey", { enabled: !m.hotkey.enabled }), { focusKey: "hotkey-stop", disabled: !m.hotkey.supported, hint })),
    m.hotkey.enabled && m.hotkey.supported && !m.hotkey.registered ? h("p", { class: "small tone-warning", title: "关闭占用它的程序后重新打开此开关", text: "快捷键被占用" }) : null);
}

export function feedbackView(state, actions) {
  const host = state.bridgeKind !== "mock";
  const m = supportModel(state.host);
  return h("div", { class: "support-block" },
    h("div", { class: "btn-row" },
      h("button", { class: "btn btn-small", type: "button", disabled: !host, title: `${REPORT_SHORT}
${REPORT_HINT}`, dataset: { focusKey: "support-report" },
        onclick: () => actions.support?.("issue_report") }, "导出问题包"),
      h("button", { class: "btn btn-small btn-text", type: "button", disabled: !host, dataset: { focusKey: "support-logs" },
        onclick: () => actions.support?.("open_logs") }, "打开日志目录")),
    m.lastReport ? h("p", { class: "muted small", title: "dev_assets\\private\\issue-reports", text: `上次：${m.lastReport}` }) : null);
}

export function updateView(state, actions) {
  const info = state.supportInfo;
  const m = supportModel(state.host);
  const channel = state.prefs.update_channel === "beta" ? "beta" : "stable";
  const update = m.update.channel && m.update.channel !== channel ? { state: "idle" } : m.update;
  const seg = h("div", { class: "segmented text-seg", role: "radiogroup", "aria-label": "更新通道" },
    [["stable", "正式版"], ["beta", "测试版"]].map(([key, label]) => h("button", {
      class: `segment${channel === key ? " is-selected" : ""}`, type: "button", role: "radio", "aria-checked": String(channel === key),
      dataset: { focusKey: `update-${key}` }, onclick: () => actions.setPref("update_channel", key) }, h("span", { text: label }))));
  const canCheck = Boolean(info?.repo) && state.bridgeKind !== "mock" && update.state !== "checking";
  return h("div", { class: "support-block" },
    h("dl", { class: "kv" },
      h("dt", { text: "当前版本" }), h("dd", { class: "num", text: info?.version || state.schema.interface?.version || "—" }),
      h("dt", { text: "构建" }), h("dd", { class: "num", text: buildText(info?.build) })),
    h("div", { class: "field form-row" }, h("div", { class: "field-label-row" }, h("span", { class: "field-label", text: "通道" })), h("div", { class: "field-control" }, seg)),
    h("p", { class: `small${update.newer ? " tone-accent" : " muted"}`, text: updateText(update, info) }),
    h("div", { class: "btn-row" },
      h("button", { class: "btn btn-small", type: "button", disabled: !canCheck, dataset: { focusKey: "update-check" },
        title: "只检查并提示，不自动下载或安装", onclick: () => actions.support?.("update_check", { channel }) }, "检查更新"),
      info?.repo ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "update-releases" },
        onclick: () => actions.support?.("open_link", { link: "releases" }) }, "打开发布页") : null));
}

export function aboutView(state, actions) {
  const info = state.supportInfo;
  const links = Array.isArray(info?.links) ? info.links : [];
  const label = { repo: "项目主页", issues: "问题反馈", releases: "发布页" };
  return h("div", { class: "support-block" },
    h("dl", { class: "kv" },
      h("dt", { text: "版本" }), h("dd", { class: "num", text: info?.version || state.schema.interface?.version || "—" }),
      h("dt", { text: "构建" }), h("dd", { class: "num", text: buildText(info?.build) }),
      h("dt", { text: "界面协议" }), h("dd", { class: "num", text: "v1" }),
      h("dt", { text: "保存位置" }), h("dd", { text: state.bridgeKind === "mock" ? "本浏览器" : "本机 dev_assets\\private" })),
    h("div", { class: "btn-row" },
      state.bridgeKind !== "mock" ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: "about-guide" },
        onclick: () => actions.support?.("open_guide") }, "使用说明") : null,
      links.map((key) => h("button", { class: "btn btn-small btn-text", type: "button",
        dataset: { focusKey: `about-${key}` }, onclick: () => actions.support?.("open_link", { link: key }) }, label[key] || key))),
    h("details", { class: "details about-licenses" },
      h("summary", { text: "开源与第三方许可" }),
      h("ul", { class: "plain-list small" }, (info?.licenses || []).map((item) =>
        h("li", {}, h("span", { text: item.name }), h("span", { class: "muted num", text: ` · ${item.license}` })))),
      h("p", { class: "muted small", text: "完整说明：LICENSE、README.md" })));
}

// 设置 → 外观: 主题 / 主题色 / 字号 / 紧凑度 / 动画 / 面板位置 / 布局. Page prefs live in the profile (prefs.*)
// and are applied as root attributes (tokens.css); the panel side is a host window preference (window.layout); 布局 is a
// per-device choice (layout-mode.js).
import { h } from "./dom.js";
import { LAYOUTS, layoutMode } from "./layout-mode.js";

export const THEMES = [["system", "跟随系统"], ["light", "浅色"], ["dark", "深色"]];
export const ACCENTS = [["pink", "粉"], ["sky", "蓝"], ["violet", "紫"], ["teal", "青"]];
export const FONT_SIZES = [["small", "小"], ["normal", "标准"], ["large", "大"], ["xlarge", "特大"]];
export const DENSITIES = [["normal", "标准"], ["compact", "紧凑"]];
export const PANEL_SIDES = [["right", "右侧"], ["left", "左侧"], ["detached", "独立窗口"]];
const DEFAULTS = { theme: "system", accent: "pink", font_size: "normal", density: "normal" };
const OPTIONS = { theme: THEMES, accent: ACCENTS, font_size: FONT_SIZES, density: DENSITIES };

/** A known value of an appearance pref, or its default. */
export function lookPref(prefs, key) {
  const value = prefs?.[key];
  return OPTIONS[key].some(([k]) => k === value) ? value : DEFAULTS[key];
}

/** Root attributes for the prefs (defaults remove the attribute). */
export function applyAppearance(root, prefs) {
  const set = (name, value, fallback) => { if (value === fallback) delete root.dataset[name]; else root.dataset[name] = value; };
  set("theme", lookPref(prefs, "theme"), "system");
  set("accent", lookPref(prefs, "accent"), "pink");
  set("font", lookPref(prefs, "font_size"), "normal");
  set("density", lookPref(prefs, "density"), "normal");
}

const label = (list, key) => list.find(([k]) => k === key)?.[1] || "";

/** Fold summary: only what differs from the defaults (plus the theme). */
export function appearanceSummary(prefs, panelSide, layout = "auto") {
  const parts = [label(THEMES, lookPref(prefs, "theme"))];
  if (lookPref(prefs, "accent") !== "pink") parts.push(`${label(ACCENTS, lookPref(prefs, "accent"))}色`);
  if (lookPref(prefs, "font_size") !== "normal") parts.push(`字号${label(FONT_SIZES, lookPref(prefs, "font_size"))}`);
  if (lookPref(prefs, "density") === "compact") parts.push("紧凑");
  if (panelSide === "left") parts.push("面板在左");
  if (panelSide === "detached") parts.push("独立窗口");
  if (layoutMode(layout) !== "auto") parts.push(label(LAYOUTS, layoutMode(layout)));
  return parts.join(" · ");
}

function segmented(name, list, current, onPick, { swatch = false, disabled = false } = {}) {
  return h("div", { class: `segmented text-seg${swatch ? " accent-seg" : ""}`, role: "radiogroup", "aria-label": name },
    list.map(([key, text]) => h("button", { class: `segment${current === key ? " is-selected" : ""}`, type: "button", role: "radio",
      "aria-checked": String(current === key), disabled, dataset: { focusKey: `${name}-${key}` }, onclick: () => onPick(key) },
      swatch ? h("span", { class: `accent-swatch swatch-${key}`, "aria-hidden": "true" }) : null, h("span", { text }))));
}

function row(title, control, hint = null) {
  return h("div", { class: "field form-row", title: hint },
    h("div", { class: "field-label-row" }, h("span", { class: "field-label", text: title })),
    h("div", { class: "field-control" }, control));
}

/** Host layout support (status.update.window): {panel_side, supported}; old hosts have none. */
export function windowLayout(host) {
  const w = host?.window_layout || {};
  return { side: panelSide(w.panel_side), supported: w.supported === true };
}

const DOCK_KEY = "sc-ui-dock-side";

/** The docked side 「放回游戏窗口」 returns to (per device; the host keeps its own copy for closing the panel window). */
export function loadDockSide() {
  try { return localStorage.getItem(DOCK_KEY) === "left" ? "left" : "right"; } catch { return "right"; }
}

export function saveDockSide(side) {
  try { if (side === "left" || side === "right") localStorage.setItem(DOCK_KEY, side); } catch { /* storage unavailable: right */ }
}

/** right / left / detached (独立窗口); anything else is the default right side. */
export function panelSide(side) {
  return PANEL_SIDES.some(([key]) => key === side) ? side : "right";
}

export const VISITOR_LABEL = "事务所访客";

export function appearanceView(state, actions, switchRow) {
  const prefs = state.prefs;
  const pick = (key) => (value) => actions.setPref(key, value);
  const layout = windowLayout(state.host);
  const visitsOn = prefs.hide_chibi_visitor !== true;
  return [
    row("主题", segmented("theme", THEMES, lookPref(prefs, "theme"), pick("theme"))),
    row("主题色", segmented("accent", ACCENTS, lookPref(prefs, "accent"), pick("accent"), { swatch: true })),
    row("字号", segmented("font", FONT_SIZES, lookPref(prefs, "font_size"), pick("font_size"))),
    row("间距", segmented("density", DENSITIES, lookPref(prefs, "density"), pick("density")), "紧凑：缩小行距和留白，一屏显示更多任务和日志"),
    row("面板位置", segmented("panel", PANEL_SIDES, layout.side, (side) => actions.windowLayout?.(side), { disabled: !layout.supported }),
      layout.supported ? "独立窗口：面板单独成窗，可拖到别处；关掉它就放回原来的一侧" : "需要在游戏窗口中使用"),
    row("布局", segmented("layout", LAYOUTS, layoutMode(state.layoutMode), (mode) => actions.setLayout?.(mode)),
      "双栏：标签栏在左，分组列表在内容旁，够宽时右侧多一列运行与日志；自动：面板够宽时用双栏"),
    h("ul", { class: "toggle-list" }, switchRow("look-chibi-visitor", VISITOR_LABEL, visitsOn,
      () => actions.setPref("hide_chibi_visitor", visitsOn), { focusKey: "look-chibi-visitor" })),
  ];
}

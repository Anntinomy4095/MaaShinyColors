// Task and tab icons. 2026-10-09 (user: 「自己画的icon和游戏美术不一致，任务页 主页看着就会不舒服」):
// every task icon is ONE treatment — the game's own header-badge look: a glossy pastel badge with a white outline carrying a
// white pictogram. The pictogram is the in-game glyph where one exists (lifted off the home / UI atlases, manifest ui_*,
// drawn as a white CSS mask), else a filled project glyph (glyphs.js) in the same chunky style. No coloured item tiles inside
// badges any more (they clashed); item tiles stay where an item itself is shown. Packaged builds without ui/game-assets/
// show the filled glyph on the same badge. Display only.
import { h, glyph } from "./dom.js";
import { gameAssets, glyphPlate, isGlyph, linePlate, uiIcon } from "./game-assets.js";
import { taskName } from "./display.js";

/** Task name -> {ui (game glyph), line (filled glyph name, also the fallback), tone}. Game glyphs: ホーム / オプション /
 * アイテム一覧 (enza-10), ショップ / ミッション / 営業 / プレゼント (enza-1 home buttons), アイドル mic (enza-1), ホームユニット /
 * camera (enza-10), W.I.N.G. swan (enza-2). Filled project glyphs where the game has none: ticket, crown, calendar, play. */
export const TASK_ICONS = {
  Startup: { ui: "ui_home", line: "home", tone: "sky" },
  Shop: { ui: "ui_shop", line: "shop", tone: "pink" },
  Training: { line: "ticket", tone: "amber" },
  MatchSkip: { ui: "ui_mic", line: "mic", tone: "violet" },
  GradeFes: { line: "crown", tone: "violet" },
  CollaboFes: { line: "mic", tone: "pink" },
  MissionStatus: { ui: "ui_mission", line: "mission", tone: "sky" },
  MissionsClaim: { ui: "ui_mission", line: "mission", tone: "sky" },
  ResourceRead: { line: "scan", tone: "amber" },
  Business: { ui: "ui_business", line: "business", tone: "mint" },
  PresentClaim: { ui: "ui_present", line: "present", tone: "pink" },
  WeeklyCheck: { line: "calendar", tone: "sky" },
  AutoProduce: { ui: "ui_wing_mark", line: "wing", tone: "mint" },
  WeeklyProduce: { line: "calendar", tone: "violet" },
  Diagnostics: { ui: "ui_camera", line: "scan", tone: "gray" },
  ProduceDryRun: { line: "play", tone: "gray" },
};

/** Tab key -> {ui (game glyph, tinted with the tab colour), line (filled glyph fallback)}. */
export const TAB_ICONS = {
  live: { line: "play" },
  home: { ui: "ui_home", line: "home" },
  tasks: { ui: "ui_mission", line: "list" },
  resources: { ui: "ui_items", line: "can" },
  knowhow: { line: "book" },
  logs: { line: "list" },
  produce: { ui: "ui_wing_mark", line: "wing" },
  settings: { ui: "ui_settings", line: "gear" },
};

/** Which source a task icon resolves to with the loaded manifest: "game" | "line" (pure; unit-tested). */
export function taskIconSource(name) {
  const spec = TASK_ICONS[name];
  if (!spec) return "line";
  return spec.ui && gameAssets().icons[spec.ui]?.glyph ? "game" : "line";
}

/**
 * Task icon at `size` px: the glossy badge with the game glyph (white mask) when the manifest has it, otherwise the filled
 * project glyph; an unknown task gets its first character. Decorative (the task name sits next to it).
 */
export function taskIcon(state, name, { size = 24, cls = "" } = {}) {
  const spec = TASK_ICONS[name];
  const wrap = (child) => h("span", { class: `task-icon${cls ? ` ${cls}` : ""}`, "aria-hidden": "true" }, child);
  if (!spec) {
    const plate = linePlate("dot", { size, tone: "gray" });
    plate.replaceChildren(document.createTextNode(Array.from(taskName(state, name))[0] || "?"));
    plate.classList.add("is-char");
    return wrap(plate);
  }
  const plate = () => linePlate(spec.line, { size, tone: spec.tone });
  if (spec.ui && gameAssets().icons[spec.ui]?.glyph) {
    // Inside a badge a failed image falls back to the filled glyph (the badge stays).
    const onFail = (bad) => (bad.parentNode ? bad.replaceWith(glyph(spec.line, { size: Math.round(size * 0.62) })) : null);
    const el = uiIcon(spec.ui, { size: Math.round(size * 0.64), onFail });
    if (isGlyph(el)) return wrap(glyphPlate(el, size, spec.tone));
  }
  return wrap(plate());
}

/** 16 px tab icon in the tab's text colour: the game glyph as a mask, or the filled glyph. */
export function tabIcon(key) {
  const spec = TAB_ICONS[key];
  if (!spec) return null;
  const fallback = () => glyph(spec.line, { size: 16, cls: "tab-icon" });
  const mask = spec.ui ? uiIcon(spec.ui, { size: 16, cls: "tab-icon", onFail: (bad) => bad.replaceWith(fallback()) }) : null;
  return isGlyph(mask) ? mask : fallback();
}

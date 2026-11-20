// W.I.N.G. start helpers (pure; unit-tested). 2026-10-13 (user): 开始 on the 「Produce」 tab starts the run directly and is
// the stamina consent (no 开局 choice any more: an in-progress Produce is continued after the Agent verified its P idol and
// team, otherwise one new Produce starts; Produce可开新局 is fixed to Yes and hidden), so there is no start dialog. The
// page still sends run.start { mode: "supervised_wing", confirmed: true } and the host re-checks everything (its readiness
// gate, the spend flag re-derived from the saved profile, at most one Produce per run).
import { anyCardArt } from "./game-assets.js";
import { caseLabel, WING_TASK, wingScenario } from "./display.js";
import { teamCheck } from "./team-rules.js";
import { MINIGAME_AUTO_MIN_RATING, MINIGAME_ENTRY_VERIFIED, minigameAuto, teamProposal } from "./team-proposal.js";

const SPECIALTY_STAT = { Vocal: "Vo", Dance: "Da", Visual: "Vi" };

/**
 * The game's Produce scenarios in its own order: 育成 (growth), then 特化 (special). 2026-10-25 (user): show the later
 * scenarios as placeholders on the 「Produce」 tab without adding sections; one becomes selectable once it has a mode.
 */
export const SCENARIOS = [
  { key: "wing", label: "W.I.N.G.", group: "growth" },
  { key: "fes", label: "感謝祭", name: "ファン感謝祭", group: "growth" },
  { key: "grad", label: "G.R.A.D.", group: "growth" },
  { key: "lp", label: "Landing Point", group: "growth" },
  { key: "step", label: "S.T.E.P.", group: "special" },
  { key: "halo", label: "say \"Halo\"", group: "special" },
];

/** Scenario rail items (pure): `ready` for scenarios with a Produce mode, `current` for the shown one, a tooltip each. */
export function scenarioItems(modeKeys = [], current = modeKeys[0] || null) {
  const ready = new Set(modeKeys);
  return SCENARIOS.map((s) => ({ ...s, ready: ready.has(s.key), current: s.key === current,
    title: `${s.name || s.label}${ready.has(s.key) ? "" : "：未开放"}` }));
}

/** The P card's own specialty: {stat: "Vo" | "Da" | "Vi" | "", name: "Visual" | …} (first listed attribute). */
export function pSpecialty(pCard = "") {
  const main = /^\s*(Vocal|Dance|Visual)\b/.exec(anyCardArt(pCard)?.specialty || "");
  return main ? { stat: SPECIALTY_STAT[main[1]], name: main[1] } : { stat: "", name: "" };
}

/** 「自动」主属性 = the P card's specialty (U02; same rule as agent/core/produce_config.card_specialty). */
export function resolvedPrimary(primary = "自动", pCard = "") {
  if (primary && primary !== "自动") return { stat: primary, auto: false };
  return { stat: pSpecialty(pCard).stat, auto: true };
}

/**
 * One line on the attribute chosen for this Produce vs the P idol's own specialty (the 主属性 field hint). Pure.
 * 自动选择 -> 「本次：Vi（跟随 P 偶像特长 Visual）」; a manual choice names the specialty only when it differs.
 */
export function primaryLine(primary = "自动", pCard = "") {
  const own = pSpecialty(pCard);
  const pick = resolvedPrimary(primary, pCard);
  if (!pCard) return pick.auto ? "选择 P 偶像后，跟随其特长决定。" : `本次：${pick.stat}`;
  if (pick.auto) return pick.stat ? `本次：${pick.stat}（跟随 P 偶像特长 ${own.name}）` : "P 卡特长无法判断：需选 Vo / Da / Vi";
  if (!own.stat) return `本次：${pick.stat}`;
  return own.stat === pick.stat ? `本次：${pick.stat}（与 P 偶像特长相同）` : `本次：${pick.stat} · P 偶像特长：${own.name}`;
}

const opt = (state, entry, key) => entry.option?.[key] ?? state.schema.options[key]?.default_case;

/**
 * say "Halo" goals from the ノウハウ wanted list (pure; same rules as agent/core/halo_policy.goals_from_wanted):
 * null when no item is for Halo, else {facility, facilityPlus, completion: 100 | 51 | null, fansCap: bool}.
 */
export function haloGoals(wanted) {
  const names = (Array.isArray(wanted) ? wanted : [])
    .filter((w) => w && (w.purpose ?? "any") !== "step").map((w) => String(w.name || "")).filter(Boolean);
  if (!names.length) return null;
  const has = (prefix) => names.some((n) => n.startsWith(prefix));
  const completion = names.some((n) => /^(パッシブスキル延長\+|昂揚)/.test(n)) ? 100 : has("パッシブスキル延長") ? 51 : null;
  return { facility: has("施設効率相談"), facilityPlus: has("施設効率相談+"), completion, fansCap: !has("ファンの声援") };
}

/**
 * One line for the 带书 field: what the dig route works toward this run, or why 开始 is refused. No Halo item in the
 * wanted list = the default goals (halo_policy.Goals(): both 施設効率相談, 完成度 100 %, no fan cap); Halo items none of
 * which the dig route can work toward = refused (tools/ui_wing).
 */
export function haloGoalsLine(wanted) {
  const g = haloGoals(wanted) || { facility: true, facilityPlus: true, completion: 100, fansCap: false, preset: true };
  const parts = [g.facility ? "施設効率相談" : "", g.facilityPlus ? "施設効率相談+" : "",
    g.completion ? `チーム完成度 ${g.completion}%` : ""].filter(Boolean);
  if (!parts.length) return { text: "目标：想要列表里的 Halo 条目没有 施設効率相談、パッシブスキル延長 或 昂揚，无法开始", warn: true };
  return { text: `目标${g.preset ? "（默认）" : ""}：${parts.join(" · ")}${g.fansCap ? " · 粉丝不超 10 万" : ""}`, warn: false };
}

/**
 * 「开局前大富豪」 自动: one line on what the next new Produce does (pure; "" for 关 / 每次, which have case notes).
 * `proposal` defaults to the loaded 推荐编队 proposal.
 */
export function minigameLine(state, entry, proposal = teamProposal()) {
  if (opt(state, entry, "Produce大富豪") !== "自动") return "";
  const recommend = teamCheck(state, WING_TASK, entry).mode === "recommend";
  const team = { p_card: opt(state, entry, "ProduceP卡"), guest: opt(state, entry, "Produce客串"),
    s_cards: [1, 2, 3, 4].map((i) => opt(state, entry, `ProduceS卡${i}`)) };
  const { play, rating } = minigameAuto(proposal, team, recommend);
  if (rating == null) return recommend ? "推荐编队无评分：本次不玩" : "指定ユニット没有推荐评分：本次不玩。";
  const score = `推荐评分 ${rating.toFixed(1)}`;
  if (!play) return `${score}：本次不玩。`;
  return MINIGAME_ENTRY_VERIFIED ? `${score}，偏低：本次开局前玩一局。` : `${score}，偏低；首页入口未验证，本次不玩`;
}

/** Top summary facts (pure): 目标, 主属性 (resolved), P 偶像, 编队来源. */
export function wingSummary(state, entry) {
  const p = opt(state, entry, "ProduceP卡") || "";
  const stat = resolvedPrimary(opt(state, entry, "Produce主属性"), p);
  const art = anyCardArt(p);
  const mode = teamCheck(state, WING_TASK, entry).mode;
  const unit = caseLabel(state, "Produce编队槽位", opt(state, entry, "Produce编队槽位"));
  const team = { unit: unit || "指定ユニット", search: "自动查找编队", recommend: `推荐编队 · 覆盖${unit || "ユニット"}` }[mode];
  const auto = mode === "recommend";
  const teamFacts = [
    { key: "p", label: "P 偶像", value: p ? (art?.idol || art?.idolEn || p) : auto ? "推荐后显示" : "未选择", missing: !p && !auto,
      auto: auto && Boolean(p), section: "P 偶像与编队" },
    { key: "team", label: "编队", value: team, section: "P 偶像与编队" },
  ];
  // say "Halo" (H2c batch 5): 带书 instead of 目标 and 主属性 (掘书线 only since 2026-11-07); same P idol and team.
  if (wingScenario(state, entry) === "halo") {
    const book = caseLabel(state, "Halo带书", opt(state, entry, "Halo带书"));
    return [
      { key: "book", label: "带书", value: book, short: book, section: "培养目标" },
      ...teamFacts,
    ];
  }
  // `section` = the settings fold the fact belongs to (the summary opens it); `auto` / `short` are display hints.
  return [
    { key: "goal", label: "目标", value: caseLabel(state, "Produce目标", opt(state, entry, "Produce目标")), section: "培养目标" },
    // 自动 without a P card yet is not something to fix here: it follows the P idol once one is chosen / recommended
    { key: "stat", label: "主属性", value: stat.stat ? `${stat.stat}${stat.auto ? "（自动）" : ""}` : stat.auto && !p ? "跟随 P 偶像" : "待选择",
      missing: !stat.stat && !(stat.auto && !p), short: stat.stat || (stat.auto && !p ? "自动" : "待选择"), auto: stat.auto && Boolean(stat.stat), section: "培养目标" },
    ...teamFacts,
  ];
}

/**
 * One-line summary of a settings fold while it is closed (pure; heading = annotation `heading`). "" = no summary.
 * Values only; the explanations stay in the open fold.
 */
export function sectionSummary(state, entry, heading) {
  const label = (key) => caseLabel(state, key, opt(state, entry, key));
  const on = (key) => opt(state, entry, key) === "Yes";
  switch (heading) {
    case "培养目标": {
      if (wingScenario(state, entry) === "halo") return label("Halo带书");
      const stat = resolvedPrimary(opt(state, entry, "Produce主属性"), opt(state, entry, "ProduceP卡") || "");
      return [label("Produce目标"), label("Produce难度"), stat.stat ? `${stat.stat}${stat.auto ? " 自动" : ""}` : stat.auto ? "主属性自动" : "主属性待定"].join(" · ");
    }
    case "P 偶像与编队": {
      const p = opt(state, entry, "ProduceP卡") || "";
      const fact = wingSummary(state, entry).find((f) => f.key === "team");
      return [p ? (anyCardArt(p)?.idol || p) : "未选 P 偶像", fact?.value].filter(Boolean).join(" · ");
    }
    case "行动与 SP": return [`失败率 ${label("Produce休息阈值")}`, `SP ${label("ProduceSP加点")}`].join(" · ");
    case "试镜": return [label("Produce试镜出牌"), label("Produce出牌风格")].join(" · ");
    case "事件与道具": {
      const game = opt(state, entry, "Produce大富豪");
      return [`事件选${label("Produce事件选项")}`, game ? `大富豪${label("Produce大富豪")}` : ""].filter(Boolean).join(" · ");
    }
    case "收尾与结算": {
      const parts = [on("Produce报酬读取") ? "读取奖励" : "", on("Produce报酬后OK") ? "自动确认" : "", on("Produce终局清空SP") ? "用完 SP" : ""].filter(Boolean);
      return parts.length ? parts.join(" · ") : "全部关闭";
    }
    default: return "";
  }
}

/**
 * A switch note of the form 「…（开的说明）。关：…」 split by state (pure): {on, off}; `off` is "" when the note has
 * no 「关：」 part. Lets a toggle show only the sentence for its current state.
 */
export function switchNoteParts(note = "") {
  const at = note.indexOf("关：");
  if (at < 0) return { on: note, off: "" };
  return { on: note.slice(0, at).replace(/^开：/, "").trim(), off: note.slice(at).trim() };
}

/** The case note of an option for its current value, or "" (annotations `case_notes`). */
export function caseNote(state, entry, key) {
  const notes = state.annotations?.options?.[key]?.case_notes;
  if (!notes) return "";
  let text = notes[opt(state, entry, key)] || "";
  if (key === "Produce试镜出牌" && text) {
    const gate = caseLabel(state, "Produce高级Auto门槛", opt(state, entry, "Produce高级Auto门槛"));
    if (gate) text = text.replace("（默认 97%）", `（当前 ${gate}）`);
  }
  return text;
}

const SP_OFF = ["不加点", "交给我"];
/** {key: reason} of settings the current choices make irrelevant (pure; values stay saved). */
export function inactiveSettings(state, entry) {
  const out = {};
  if (SP_OFF.includes(opt(state, entry, "ProduceSP加点"))) {
    const why = "SP 使用策略为「暂不使用」或「手动设置」时不生效。";
    out["Produce上限余量"] = why;
    out["Produce终局清空SP"] = why;
  }
  out["Produce保护Lv5书"] = "";   // the 保護 target is unverified: the switch never leads to a click
  return out;
}


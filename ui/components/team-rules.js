// W.I.N.G. 编队 rules shared by the team field, the pickers and the run bar (mirror of agent/core/produce_config.py and
// tools/ui_wing.py):
// - the P idol must be one of 持有卡牌 (指定ユニット: chosen by the user; 推荐编队: chosen by Maa);
// - two team sources (2026-10-14, user): 「指定ユニット」 = the user's in-game ユニット (1–20, default ユニット1) with the
//   switch 「自动查找编队」 (teamMode "search": Maa looks through ユニット1–20 for the P or its last 编队快照);
//   「推荐编队」 (the 自动编队 case) = Maa's recommendation (P + 4 S + Guest), adopted into the team keys; a new Produce
//   overwrites the chosen ユニット with it (tools/ui_wing.py team_edit);
// - the team Maa verifies = P + the last 编队快照 of the same P (unless 「核对上次读到的 S/Guest」 is off), or the adopted
//   recommendation, else S/Guest a developer registered; the same idol may appear only once (P, S1–S4, Guest; WING demo
//   #152); a recommended / registered Guest must be owned (a snapshot Guest was read from the game: only a note);
// - goal / difficulty combinations produce_config rejects (True End ≠ Beginner).
// Errors block 开始 while the task is enabled. Pure functions; no DOM.
import { anyCardArt, idolNumber } from "./game-assets.js";
import { ownedCards } from "./team-store.js";
import { teamSnapshot } from "./team-snapshot.js";
import { autoProposal, matchingCandidate, noProposalText, requestMatches, teamProposal } from "./team-proposal.js";

/** The team group of a task (annotations option_groups with control "team"): {group, note, keys} or null. */
export function teamGroup(state, taskName) {
  const groups = state.annotations?.option_groups || {};
  for (const [group, note] of Object.entries(groups)) {
    if (note.control !== "team" || note.task !== taskName) continue;
    const task = state.schema.tasks.find((t) => t.name === taskName);
    const keys = (task?.options || []).filter((k) => state.annotations.options?.[k]?.group === group && state.schema.options[k]);
    return { group, note, keys };
  }
  return null;
}

/** {role: key} of a team group (annotations option `slot`: mode / unit / search / snapcheck / P / S1–S4 / guest). */
export function teamRoles(state, keys) {
  const roles = {};
  for (const key of keys) {
    const slot = state.annotations.options?.[key]?.slot;
    if (slot) roles[slot] = key;
  }
  return roles;
}

function value(state, entry, key) {
  return key ? entry?.option?.[key] ?? state.schema.options[key]?.default_case : undefined;
}

function idolName(id) {
  const art = anyCardArt(id);
  return (art?.idol || art?.idolEn || `偶像 ${idolNumber(id)}`).replace(/\s+/g, "");
}

/** {idolNumber: [slot labels]} for idols placed more than once. */
export function duplicateIdols(slots) {
  const byIdol = new Map();
  for (const { id, label } of slots) {
    const idol = idolNumber(id);
    if (!idol) continue;
    byIdol.set(idol, [...(byIdol.get(idol) || []), label]);
  }
  return Object.fromEntries([...byIdol].filter(([, labels]) => labels.length > 1));
}

export const S_SLOTS = ["S1", "S2", "S3", "S4"];

/** Unit number of a case such as "unit7" (null when unknown). */
export function unitNumber(name) {
  const m = /^unit(\d{1,2})$/.exec(name || "");
  return m ? Number(m[1]) : null;
}

/** "unit" | "search" | "recommend": where the team comes from (mode option = auto case -> 推荐编队; "search" =
 * 指定ユニット with 「自动查找编队」 on). */
export function teamMode(state, entry, roles, note) {
  if (note?.mode_option && value(state, entry, note.mode_option) === note.auto_case) return "recommend";
  return value(state, entry, roles.search) === "Yes" ? "search" : "unit";
}

/**
 * The cards Maa will verify besides P: {source: "snapshot" | "recommend" | "registered" | null, s: [4], guest,
 * snapshot}. The snapshot counts only for the same P card, (unless 按 P 找) the same ユニット and while 「核对上次读到的
 * S/Guest」 is on, exactly as tools/ui_wing.py uses it; 用推荐 uses the adopted S/Guest keys and never the snapshot.
 */
export function expectedTeam(state, entry, roles, snapshot = teamSnapshot(), mode = null) {
  const p = value(state, entry, roles.P) || "";
  const unit = unitNumber(value(state, entry, roles.unit));
  const search = value(state, entry, roles.search) === "Yes";
  const check = value(state, entry, roles.snapcheck) !== "No";
  const s = S_SLOTS.map((slot) => value(state, entry, roles[slot]) || "");
  const guest = value(state, entry, roles.guest) || "";
  const own = snapshot && p && snapshot.p_card === p ? snapshot : null;
  if (mode === "recommend") return { source: "recommend", s, guest, snapshot: own };
  if (own && check && (search || own.unit === unit)) {
    return { source: "snapshot", s: [...own.s_cards], guest: own.guest, snapshot: own };
  }
  return { source: s.some(Boolean) || guest ? "registered" : null, s, guest, snapshot: own };
}

/**
 * {auto, mode, errors: [text], notes: [text], checks: [{ok, text, warn?}], team, proposal} for the task's current
 * options. `checks` is the inline pre-start checklist; `proposal` is the usable recommendation for this P (or null).
 */
export function teamCheck(state, taskName, entry) {
  const found = teamGroup(state, taskName);
  if (!found) return { auto: false, mode: "unit", errors: [], notes: [], checks: [], team: null, proposal: null };
  const { note, keys } = found;
  const roles = teamRoles(state, keys);
  const mode = teamMode(state, entry, roles, note);
  const auto = mode === "recommend";
  const errors = [];
  const notes = [];
  const owned = ownedCards(state);
  const have = new Set([...owned.p, ...owned.s]);
  const p = value(state, entry, roles.P) || "";
  const team = expectedTeam(state, entry, roles, teamSnapshot(), mode);
  const slots = [{ id: p, label: "P" }, ...team.s.map((id, i) => ({ id, label: S_SLOTS[i] })), { id: team.guest, label: "Guest" }].filter((s) => s.id);
  const pOwned = Boolean(p) && have.has(p);
  const dupes = Object.entries(duplicateIdols(slots));
  const fromGame = team.source === "snapshot";
  const guestOwned = !team.guest || have.has(team.guest);
  const sMissing = team.s.filter((id) => id && !have.has(id)).length;
  const unit = unitNumber(value(state, entry, roles.unit));
  const loaded = teamProposal();
  const proposal = auto && p ? autoProposal(loaded, p) : null;
  const adopted = Boolean(p) && team.s.every(Boolean) && Boolean(team.guest);
  // the adopted cards may be the main recommendation or an alternative the player chose (「使用这队」)
  const same = proposal && adopted && Boolean(matchingCandidate(loaded, { p_card: p, s_cards: team.s, guest: team.guest }));
  if (!p) errors.push("未选择 P 偶像");   // 推荐编队 too: the player picks the P, Maa recommends S/Guest for it
  else if (!owned.p.length) errors.push("持有卡牌为空：需在「资源」页扫描");
  else if (!pOwned) errors.push("P 卡不在持有卡牌中");
  for (const [idol, labels] of dupes) {
    const id = slots.find((s) => idolNumber(s.id) === idol).id;
    errors.push(`同一偶像出现两次：${idolName(id)}（${labels.join(" / ")}）`);
  }
  if (fromGame) {
    // the snapshot was read from the game: a card missing from 持有卡牌 means the list is out of date
    if (!guestOwned || sMissing) notes.push("游戏内编队有卡不在持有卡牌里：可在「资源」页更新");
  } else {
    if (!guestOwned) errors.push("Guest 不在持有卡牌中");
    if (sMissing) errors.push(`${sMissing} 张 S 卡不在持有卡牌中`);
  }
  const unitOk = unit !== null && unit >= 1 && unit <= 20;
  if (mode !== "search" && !unitOk) errors.push("请选择ユニット1–20");
  if (auto) {
    // loaded = a proposal file was read (null: the field has not loaded it yet; the run checks the adopted keys)
    if (!p) { /* 「未选择 P 偶像」 above */ }
    else if (loaded && !proposal) {
      const why = noProposalText(loaded);
      errors.push(!requestMatches(loaded, p) ? "推荐编队未生成" : why === "暂无推荐" ? "暂无推荐：改用指定ユニット" : why);
    }
    else if (proposal && !same) errors.push("推荐更新中");
    else if (!adopted) errors.push("推荐编队未生成");
  }
  // 推荐编队 overwrites the unit: said once by the 「将覆盖ユニットN」 tag and the mode hint (team-field.js), not here.
  const goal = value(state, entry, "Produce目标");
  const difficulty = value(state, entry, "Produce难度");
  if (goal === "True End" && difficulty === "Beginner") errors.push("True End 需要 Middle 或 Master");
  // produce_config: 主属性「自动」 follows the P card's one main specialty; a known card without one (均衡 / unread) needs Vo / Da / Vi
  const hasPrimary = state.schema.tasks.find((t) => t.name === taskName)?.options?.includes("Produce主属性");
  const primary = value(state, entry, "Produce主属性");
  const art = p ? anyCardArt(p) : null;
  if (hasPrimary && art && (!primary || primary === "自动") && !/^\s*(Vocal|Dance|Visual)\b/.test(art.specialty || "")) {
    errors.push("P 卡无单一特长：主属性需选 Vo / Da / Vi");
  }
  // Each item says its current state (a red 「P 卡已持有」 read as the opposite). 推荐编队 waits for Maa's P (neutral).
  const where = mode === "search" ? "在ユニット1–20 中查找" : !unitOk ? "选择ユニット" : `ユニット${unit}`;
  const teamOk = auto ? adopted && (!loaded || Boolean(same)) : fromGame || (guestOwned && sMissing === 0);
  const guestText = auto ? (teamOk ? "推荐编队已采用" : "推荐编队未就绪")
    : team.guest ? (fromGame ? "按游戏内编队核对" : guestOwned && !sMissing ? "S/Guest 已持有" : "S/Guest 未持有") : "S/Guest 开始后读取";
  const pText = pOwned ? "P 卡已持有" : !p ? "未选择 P 偶像" : "P 卡未持有";
  const checks = [
    { ok: pOwned, text: pText },
    { ok: dupes.length === 0, text: dupes.length ? "有重复偶像" : "无重复偶像" },
    { ok: teamOk, text: guestText, pending: !auto && !team.guest },
    { ok: mode === "search" || unitOk, text: where },
  ];
  return { auto, mode, errors, notes, checks, team, proposal };
}

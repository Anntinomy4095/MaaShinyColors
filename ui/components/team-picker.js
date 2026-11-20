// Produce team pickers on top of the generic openGridPicker (card-picker.js):
// - openTeamCardPicker: one slot (P / S1–S4 / Guest), single choice from 「持有卡牌」 only. 「全部」 shows the rest greyed
//   out (「未持有」). First tile 「不登记」 clears the slot. Cards already in another slot and every card of an idol that
//   is already in the team (P / S / Guest: one idol per unit) are greyed out with the reason.
// - openOwnedPicker: multi-select over the whole local wiki list to mark 「持有卡牌」.
// Rarity / idol / specialty filters and search (space-separated words; Simplified names and aliases via name-fold.js).
// Tiles show the specialty tag (Vo/Da/Vi/…) from the wiki list; support skill / stamina-recovery tags appear when the
// local manifest carries `tags` (not in the wiki list pages today).
import { h, icon } from "./dom.js";
import { gameAssets, idolNumber, badgeIcon, attrIconKey, cardArt } from "./game-assets.js";
import { openGridPicker, pickerButton } from "./card-picker.js";
import { idolWords, specialtyWords } from "./name-fold.js";
import { ownedCards } from "./team-store.js";

const RARITY_ORDER = ["UR", "SSR", "SR", "R", "N"];
const SPECIALTY_SHORT = { Vocal: "Vo", Dance: "Da", Visual: "Vi", Mental: "Me", Balanced: "均衡" };
// No local card list yet: the player's way out is in the app (资源 → 卡资料 → 下载卡资料), never a command line.
const NO_LIST = "还没有卡牌资料：先在「资源」页的「卡资料」下载";
const noOwned = (manual) => `持有卡牌为空：先在「资源」页扫描持有卡${manual ? "，或点「编辑持有卡牌」手动标记" : ""}`;

/** A footer button that closes the dialog first, then runs `go` (opening 资源 from inside a modal). */
function leaveButton(label, go) {
  return h("button", { class: "btn btn-small btn-text", type: "button", onclick: (e) => { e.target.closest("dialog")?.close(); e.target.closest("dialog")?.remove(); go(); } }, label);
}

/** "Dance (Vocal)" -> "Da(Vo)", "Vocal & Visual" -> "Vo&Vi", "Balanced" -> "均衡". */
export function specialtyShort(text) {
  if (!text) return "";
  return text.replace(/Vocal|Dance|Visual|Mental|Balanced/g, (w) => SPECIALTY_SHORT[w]).replace(/\s+/g, "");
}

/** First attribute named in a wiki specialty ("Dance (Vocal)" -> "Dance"), or "" (Balanced / missing). */
export function primaryAttr(text) {
  return (/Vocal|Dance|Visual|Mental/.exec(text || "") || [""])[0];
}

/** In-game attribute tile for a card's primary specialty (local game-assets; null when missing — the text tag stays). */
export function attrBadge(specialty, { size = 20, cls = "card-attr" } = {}) {
  const attr = primaryAttr(specialty);
  return attr ? badgeIcon(attrIconKey(attr), { size, decorative: true, cls }) : null;
}

export function cardsOf(kind) {
  return kind === "P" ? gameAssets().pCards : gameAssets().cards.filter((c) => /^2\d{9}$/.test(c.id));
}

function thumb(card, size = 96) {
  if (card?.src) return h("img", { class: "card-img", src: card.src, alt: "", width: String(size), height: String(size), draggable: "false", loading: "lazy", decoding: "async" });
  return h("span", { class: "card-img card-placeholder", "aria-hidden": "true" }, icon("card", { size: 20 }));
}

function idolLabel(card) {
  return (card.idol || card.idolEn || card.id).replace(/\s+/g, "");
}

/** Filter selects shared by every team picker: rarity, idol (by idol number), specialty. */
export function cardFilters(cards) {
  const rarities = RARITY_ORDER.filter((r) => cards.some((c) => c.rarity === r));
  const idols = new Map();
  for (const card of cards) {
    const num = idolNumber(card.id);
    if (num && !idols.has(num)) idols.set(num, idolLabel(card));
  }
  return [
    { label: "稀有度", value: "", options: [["", "全部稀有度"], ...rarities.map((r) => [r, r])], test: (e, v) => e.card?.rarity === v },
    { label: "偶像", value: "", options: [["", "全部偶像"], ...[...idols].sort(([a], [b]) => a.localeCompare(b)).map(([n, name]) => [n, name])],
      test: (e, v) => idolNumber(e.id) === v },
    { label: "特长", value: "", options: [["", "全部特长"], ["Vo", "Vo"], ["Da", "Da"], ["Vi", "Vi"], ["均衡", "均衡"]],
      test: (e, v) => specialtyShort(e.card?.specialty).includes(v) },
  ];
}

/** Picker tile for a wiki card; `search` holds the idol name, titles, rarity, attribute words and the idol / unit aliases. */
export function cardEntry(card, { selectable = true, lock = "", sub2 = "", tip = "", flag = "", flagTip = "" } = {}) {
  const tags = [specialtyShort(card.specialty), ...(card.tags || [])].filter(Boolean).slice(0, 3);
  const name = idolLabel(card);
  return {
    id: card.id, card, selectable, name, sub: card.title, sub2, badge: card.rarity, tags, flag, lock,
    search: [name, card.title, card.idolEn, card.titleEn, card.rarity, card.specialty, specialtyWords(card.specialty),
      idolWords(idolNumber(card.id))].filter(Boolean).join(" "),
    tip: [`${name}${card.title ? `【${card.title}】` : ""}`, card.specialty && `特长：${card.specialty}`, flagTip, tip].filter(Boolean).join("\n"),
    visual: () => h("span", { class: "card-visual" }, thumb(card), attrBadge(card.specialty)),
  };
}

const CLEAR_ENTRY = {
  id: "", selectable: true, name: "不登记", sub: "留空", search: "", tip: "清空此格（未登记的格子开局前无法核对）",
  visual: () => h("span", { class: "card-img card-placeholder team-clear", "aria-hidden": "true" }, icon("close", { size: 20 })),
};

/**
 * One team slot. `kind` "P" | "S"; `value` current id or ""; `used` {id: slotLabel} of the other slots;
 * `owned` Set of owned ids of this kind; `onSelect(id)` receives "" to clear.
 */
export function openTeamCardPicker({ kind, slotLabel, value, used = {}, owned = new Set(), onSelect, editOwned = null, goResources = null, mark = null, clear = null, title = null, foot = null }) {
  const all = cardsOf(kind);
  const clearEntry = clear ? { ...CLEAR_ENTRY, ...clear } : CLEAR_ENTRY;
  const usedIdols = new Map(Object.entries(used).map(([id, label]) => [idolNumber(id), label]));
  const toEntry = (card) => {
    if (used[card.id]) return cardEntry(card, { selectable: false, lock: "已在编队", tip: `已在 ${used[card.id]}` });
    const idolSlot = usedIdols.get(idolNumber(card.id));
    if (idolSlot) return cardEntry(card, { selectable: false, lock: "同偶像", sub2: `同偶像在 ${idolSlot}`, tip: `同一偶像已在 ${idolSlot}：同一编队不能重复偶像（P / S / Guest）` });
    if (!owned.has(card.id)) return cardEntry(card, { selectable: false, lock: "未持有", tip: "未持有" });
    return cardEntry(card, mark?.(card) || {});
  };
  const mine = all.filter((c) => owned.has(c.id)).map(toEntry);
  const rest = all.filter((c) => !owned.has(c.id)).map(toEntry);
  const current = value && !all.some((c) => c.id === value)
    ? [{ ...clearEntry, id: value, name: value, sub: "列表中没有此卡", tip: value, visual: () => thumb(null) }] : [];
  return openGridPicker({
    title: title || `${slotLabel}${kind === "P" ? "（P 卡）" : "（S 卡）"}`, entries: [clearEntry, ...current, ...mine], extra: rest,
    showExtra: false, initial: value || "", scopeLabels: ["持有卡牌", "全部"], filters: all.length ? cardFilters(all) : [],
    searchPlaceholder: "搜索偶像 / 卡名 / 特长", className: "team-dialog",
    emptyText: !all.length ? NO_LIST : mine.length ? "没有匹配的卡牌" : noOwned(Boolean(editOwned)),
    gridNote: !all.length ? NO_LIST : mine.length ? null : noOwned(Boolean(editOwned)),
    foot: h("span", { class: "card-cost" }, all.length ? `持有卡牌 ${mine.length} 张` : "没有卡牌资料", foot,
      all.length && editOwned ? leaveButton("编辑持有卡牌", editOwned) : null,
      goResources && (!all.length || !mine.length) ? leaveButton(all.length ? "去资源页扫描" : "去资源页下载", goResources) : null),
    countText: () => `共 ${all.length} 张`, onCommit: onSelect,
  });
}

/** Mark 「持有卡牌」 for one kind. `onApply(ids[])` receives the full new owned list of this kind. */
export function openOwnedPicker({ kind, owned = new Set(), onApply, onImport = null, goResources = null }) {
  const all = cardsOf(kind);
  return openGridPicker({
    title: kind === "P" ? "我的 P 卡" : "我的 S 卡", entries: all.map((c) => cardEntry(c)), multi: true, initial: [...owned],
    filters: all.length ? cardFilters(all) : [], bulk: all.length > 0, searchPlaceholder: "搜索偶像 / 卡名 / 特长", className: "team-dialog",
    emptyText: all.length ? "没有匹配的卡牌" : NO_LIST,
    foot: h("span", { class: "card-cost item-note" }, all.length ? "勾选持有的卡，或导入识别结果" : "没有卡牌资料",
      all.length && onImport ? leaveButton("导入识别结果", onImport) : null,
      !all.length && goResources ? leaveButton("去资源页下载", goResources) : null),
    countText: (chosen) => `已标记 ${chosen.size} / 共 ${all.length}`,
    onCommit: (chosen) => onApply([...chosen]),
  });
}

// ---------------------------------------------------------------- Training task: the S card to train
// Option 「训练卡」 (input, annotations control "card_picker"): a wiki id of an S card in 「持有卡牌」 (= confirmed: auto-accepted
// readings or the user's confirmation), or "" = default: the first trainable S card of the training list in its current sort.
export const TRAINING_DEFAULT = "列表第一张";
const UNCALIBRATED_TIP = "未校准：还没有这张卡的实机训练样本，运行时按卡名核对";
const TRAINING_NOTE = "标「未校准」的卡还没有实机训练样本，运行时按卡名核对";

/**
 * What to tell the player about the chosen training card on its task row (pure): 未校准 (the manifest's `calibrated` flag,
 * known only while the card list is installed) and 不在持有卡牌 (only when that list is not empty; an empty one has its own hint).
 */
export function trainingCardWarnings(value, { art = null, owned = [] } = {}) {
  if (!value) return [];
  const out = [];
  if (art && art.calibrated !== true) out.push({ key: "uncalibrated", text: UNCALIBRATED_TIP });
  if (owned.length && !owned.includes(value)) out.push({ key: "unowned", text: "所选卡不在持有卡牌中，请重新选择" });
  return out;
}

/** Inline control: thumbnail + idol name of the chosen card, or the default text; opens the picker. */
export function trainingCardButton(value, { id, disabled, focusKey, labelledBy, onclick }) {
  const art = value ? cardArt(value) : null;
  const text = !value ? TRAINING_DEFAULT : art ? `${art.idol || art.idolEn || value}${art.title ? ` 【${art.title}】` : ""}` : value;
  return pickerButton({ id, disabled, focusKey, labelledBy, onclick, lead: thumb(art, 32), text, title: value || TRAINING_DEFAULT });
}

/** Picker over the confirmed S cards; the first tile is the empty choice. `onSelect(id)` receives "" for the default. */
export function openTrainingCardPicker(state, { value, onSelect, goResources = null }) {
  const owned = ownedCards(state);
  return openTeamCardPicker({
    kind: "S", slotLabel: "训练卡", title: "训练卡牌", value: value || "", owned: new Set(owned.s), goResources, onSelect,
    mark: (card) => (card.calibrated ? null : { flag: "未校准", flagTip: UNCALIBRATED_TIP }),
    clear: { name: "默认", sub: "列表第一张可训练", tip: `${TRAINING_DEFAULT}：按当前排序，选择列表顶部第一张可训练的 S 卡` },
    foot: cardsOf("S").length ? h("span", { class: "item-note muted small", text: TRAINING_NOTE }) : null,
  });
}

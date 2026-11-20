// Optional in-game imagery. tools/ui_game_assets.py builds item icons (from the user-authorised enza atlases and pinned
// single item tiles) and S-card thumbnails (Shinycolors Wiki list) into ui/game-assets/ — gitignored, never
// committed — and writes manifest.js there. When the folder is missing the import fails and the UI falls back to the
// self-drawn badges (filled glyphs on a glossy pastel badge). Same-origin relative image paths only (CSP img-src 'self').
// `cards` are S cards (support idols, training + Produce pickers); `pCards` are P cards (Produce idols, team picker
// only). Card ids are the game's 10-digit card ids (ShinyColorsDB enzaId = wiki CardIcon number): 1… = P, 2… = S,
// digits 4–6 = idol number. Thumbnails: ShinyColorsDB first, wiki fallback (manifest `thumb_source`, not read here).
import { h, glyph } from "./dom.js";

const SRC = /^game-assets\/(items|cards|icons)\/[a-z0-9_-]{1,64}(@2x)?\.png$/;
const RARITIES = ["UR", "SSR", "SR", "R", "N"];
const EMPTY = () => ({ items: {}, icons: {}, cards: [], pCards: [], source: "", pSource: "" });
const SPECIALTY = /^[A-Za-z &()]{1,40}$/;
const IDEAS = ["Vocal", "Dance", "Visual", "Mental"];
let assets = EMPTY();
let manifestRevision = 0;

/**
 * Display defaults for items (labels are UI text). `icon` is the self-drawn line fallback and wins over the annotations
 * layer's older icon names; `tone` tints its plate (pastel game palette, tokens.css --tone-*).
 */
export const ITEM_DEFAULTS = {
  money: { label: "マニー", icon: "coin", tone: "amber" },
  jewel: { label: "ジュエル（不使用）", icon: "gem", tone: "sky" },
  bronze_ticket: { label: "铜训练券", icon: "ticket", tone: "amber" },
  silver_ticket: { label: "银训练券", icon: "ticket", tone: "gray" },
  gold_ticket: { label: "金训练券", icon: "ticket", tone: "amber" },
  platinum_ticket: { label: "白金训练券", icon: "ticket", tone: "violet" },
  lv50_ticket: { label: "Lv50 训练券", icon: "ticket", tone: "sky" },
  skip_ticket: { label: "跳过券", icon: "ticket", tone: "pink" },
  fes: { label: "Fes 参加权", icon: "fes", tone: "violet" },
  // Icons: pinned enza tiles (2026-10-08c). Names as shown in アイテム一覧 → 消費アイテム (2026-10-08d): the white VS FES
  // tile is フェスエントリーチケット (the pink one is 「…5」), the blue can is リカバリーソーダ30. `icon` / `ui` remain the
  // fallback when the private assets are missing (packaged builds).
  fes_ticket: { label: "フェスエントリーチケット", icon: "fes", tone: "pink" },
  stamina: { label: "体力（プロデュース）", icon: "stamina", tone: "mint" },
  stamina_drink: { label: "リカバリーソーダ30（体力回复）", icon: "drink", tone: "sky", ui: "ui_items" },
  // 営業 gift item; creating it is a separate opt-in feature (disabled by default). Display only.
  handmade_gift: { label: "お手製差し入れ", icon: "present", tone: "amber" },
  gasha: { label: "ガシャ（不使用）", icon: "capsule", tone: "pink", ui: "ui_gasha" },
  shop: { label: "ショップ", icon: "shop", tone: "pink", ui: "ui_shop" },
  mission: { label: "ミッション", icon: "mission", tone: "sky", ui: "ui_mission" },
  business: { label: "営業", icon: "business", tone: "mint", ui: "ui_business" },
  present: { label: "プレゼント", icon: "present", tone: "pink", ui: "ui_present" },
  notice: { label: "お知らせ", icon: "list", tone: "gray", ui: "ui_notice" },
  // 2026-10-09 商店「其他商品」(本日限定 day goods): game names; the pinned enza tiles come from tools/ui_game_assets.py
  // TILE_ITEMS (アイテム一覧 プロデュースアイテム). Keys without a pinned tile yet
  // (Vo/Da 基礎知識本, 始まりのメッセージカード, seven unit towels) keep this line icon until the user supplies their tiles.
  shop_message_card: { label: "始まりのメッセージカード", icon: "card", tone: "pink" },
  p_morning_bread: { label: "モーニングコミュパン", icon: "card", tone: "amber" },
  p_toilette: { label: "約束のトワレ", icon: "card", tone: "mint" },
  p_cologne: { label: "約束のコロン", icon: "card", tone: "sky" },
  p_book_vocal_basic: { label: "ボーカル基礎知識本", icon: "book", tone: "pink" },
  p_book_dance_basic: { label: "ダンス基礎知識本", icon: "book", tone: "sky" },
  p_book_visual_basic: { label: "ビジュアル基礎知識本", icon: "book", tone: "amber" },
  p_trouble_charm: { label: "トラブル守り", icon: "shield", tone: "pink" },
  p_aroma_2: { label: "テンションアロマ(2個入り)", icon: "card", tone: "mint" },
  p_tart_yellow_2: { label: "ヒーリングタルト(2個入り)", icon: "card", tone: "amber" },
  p_schedule_note: { label: "予定変更付箋", icon: "calendar", tone: "pink" },
  towel_illumination: { label: "イルミネーションスターズのタオル", icon: "card", tone: "pink" },
  towel_antica: { label: "アンティーカのタオル", icon: "card", tone: "violet" },
  towel_houkura: { label: "放課後クライマックスガールズのタオル", icon: "card", tone: "amber" },
  towel_alstroemeria: { label: "アルストロメリアのタオル", icon: "card", tone: "pink" },
  towel_straylight: { label: "ストレイライトのタオル", icon: "card", tone: "gray" },
  towel_noctchill: { label: "ノクチルのタオル", icon: "card", tone: "mint" },
  towel_shhis: { label: "シーズのタオル", icon: "card", tone: "gray" },
  towel_cometik: { label: "コメティックのタオル", icon: "card", tone: "gray" },
};

/**
 * Produce item (プロデュースアイテム) names for the item ids of tools/item_inventory_scan.py. Since 2026-10-08d every name is
 * the label the game itself shows above the tile in アイテム一覧 → プロデュースアイテム (read-only Chrome pass; tile matched
 * to the pinned enza tile), so all are `sure`. An id without an entry falls back to its look.
 * Display only.
 */
export const P_ITEM_NAMES = {
  p_tart_yellow_3: { name: "ヒーリングタルト(3個入り)", sure: true },
  p_tart_blue_2: { name: "ヒーリングフルーツタルト(2個入り)", sure: true },
  p_tart_blue_3: { name: "ヒーリングフルーツタルト(3個入り)", sure: true },
  p_tart_black_2: { name: "高級ヒーリングフルーツタルト(2個入り)", sure: true },
  p_tart_black_3: { name: "高級ヒーリングフルーツタルト(3個入り)", sure: true },
  p_candle_1: { name: "プチテンションアロマ", sure: true },
  p_candle_2: { name: "プチテンションアロマ(2個入り)", sure: true },
  p_candle_3: { name: "プチテンションアロマ(3個入り)", sure: true },
  p_magazine_1: { name: "流行雑誌", sure: true },
  p_magazine_2: { name: "流行雑誌(2冊入り)", sure: true },
  p_magazine_vocal: { name: "Vocal流行雑誌", sure: true },
  p_magazine_dance: { name: "Dance流行雑誌", sure: true },
  p_magazine_visual: { name: "Visual流行雑誌", sure: true },
  p_secret_memo: { name: "秘密のメモ帳", sure: true },
  p_book_magnifier: { name: "ノウハウ目録", sure: true },
  p_bookmark_orange: { name: "ノウハウのしおり(1葉)", sure: true },
  p_bookmark_rainbow: { name: "高レベルノウハウのしおり(1葉)", sure: true },
  p_charm_pouch: { name: "親愛のお守り", sure: true },
};

const TONES = ["pink", "sky", "violet", "mint", "amber", "gray"];

// Option keys / case names that name an item (shop goods, training ticket). Order matters: first match wins.
const ITEM_WORDS = [
  [/スキップチケット|跳过券/, "skip_ticket"],
  [/ゴールドトレーニングチケット|金训练券/, "gold_ticket"],
  [/シルバートレーニングチケット|银训练券/, "silver_ticket"],
  [/ブロンズトレーニングチケット|铜券|铜训练券/, "bronze_ticket"],
  [/プラチナトレーニングチケット|白金训练券/, "platinum_ticket"],
];

function text(value, max = 80) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function src(value) {
  return typeof value === "string" && SRC.test(value) ? value : null;
}

function clean(manifest) {
  const out = EMPTY();
  if (!manifest || typeof manifest !== "object") return out;
  for (const [key, item] of Object.entries(manifest.items || {})) {
    if (!/^[a-z][a-z0-9_]{0,31}$/.test(key) || !src(item?.src)) continue;
    out.items[key] = { src: item.src, src2x: src(item.src2x), indicatorOnly: item.indicator_only === true };
  }
  for (const [key, entry] of Object.entries(manifest.icons || {})) {
    if (!/^(rank|attr|ui)_[a-z_]{1,16}$/.test(key) || !src(entry?.src) || !entry.src.startsWith("game-assets/icons/")) continue;
    out.icons[key] = { src: entry.src, src2x: src(entry.src2x), glyph: entry.mode === "glyph" };
  }
  for (const card of Array.isArray(manifest.cards) ? manifest.cards.slice(0, 5000) : []) {
    const entry = cleanCard(card);
    if (entry) out.cards.push(entry);
  }
  for (const card of Array.isArray(manifest.p_cards) ? manifest.p_cards.slice(0, 5000) : []) {
    const entry = cleanCard(card);
    if (entry && /^1\d{9}$/.test(entry.id)) out.pCards.push(entry);  // P list: wiki P ids only
  }
  out.source = text(manifest.card_source?.kind, 16);
  out.pSource = text(manifest.p_card_source?.kind, 16);
  return out;
}

function cleanCard(card) {
  if (typeof card?.id !== "string") return null;
  const image = src(card.src);
  const missing = card.thumbnail_missing === true && card.src == null
    && /^[12]\d{9}$/.test(card.id) && RARITIES.includes(card.rarity);
  if (!image && !missing) return null;
  const tags = Array.isArray(card.tags) ? card.tags.filter((t) => typeof t === "string" && t.length <= 12).slice(0, 4) : [];
  return {
    id: text(card.id, 120), src: image, thumbnailMissing: missing, option: text(card.option, 120) || null, calibrated: card.calibrated === true,
    idol: text(card.idol), title: text(card.title), idolEn: text(card.idol_en), titleEn: text(card.title_en),
    rarity: RARITIES.includes(card.rarity) ? card.rarity : "", slot: text(card.slot, 8),
    specialty: typeof card.specialty === "string" && SPECIALTY.test(card.specialty) ? card.specialty : "",
    idea: IDEAS.includes(card.idea) ? card.idea : "", tags,
  };
}

/** Install a manifest object (validated; anything unexpected is dropped). Used by loadGameAssets and tests. */
export function useGameAssets(manifest) {
  assets = clean(manifest);
  return assets;
}

export async function loadGameAssets({ force = false } = {}) {
  try {
    const module = await import("../game-assets/manifest.js" + (force ? `?updated=${Date.now()}-${++manifestRevision}` : ""));
    return useGameAssets(module.default);
  } catch {
    return useGameAssets(null);
  }
}

export function gameAssets() {
  return assets;
}

/** Item key named by an option key or case (e.g. 「商店必购金训练券」 -> gold_ticket), or null. */
export function itemKeyFor(name) {
  const found = ITEM_WORDS.find(([pattern]) => pattern.test(name || ""));
  return found ? found[1] : null;
}

// Reading keys without an item (engine readings / fixtures) that still have a fitting icon.
const READING_ITEMS = { money: "money", fes_rights: "fes", missions: "mission", mission_progress: "mission", stamina: "stamina" };

/** Item key for a resource reading: its own `item`, else the icon that fits its `key`, else null. */
export function readingItemKey(reading) {
  return reading?.item || READING_ITEMS[reading?.key] || null;
}

/** {key, label, icon} for an item key, merging the annotations layer over the built-in defaults. */
export function itemSpec(state, key) {
  if (!key) return null;
  return { key, ...(ITEM_DEFAULTS[key] || {}), ...(state?.annotations?.items?.[key] || {}) };
}

/**
 * Game-style badge (2026-10-09): a rounded glossy pastel badge (`tone`: pink / sky /
 * violet / mint / amber / gray) with a white outline and a white filled glyph (glyphs.js `name`) — the stand-in wherever
 * no in-game icon fits or the private assets are missing. The class keeps its old name `line-plate` (shared selectors).
 */
export function linePlate(name, { size = 24, tone = "gray", label = null, cls = "" } = {}) {
  const plate = h("span", { class: `line-plate tone-${TONES.includes(tone) ? tone : "gray"}${cls ? ` ${cls}` : ""}`,
    role: label ? "img" : null, "aria-label": label || null, "aria-hidden": label ? null : "true", title: label || null },
  glyph(name, { size: Math.max(12, Math.round(size * 0.62)) }));
  boxSize(plate, size);
  return plate;
}

/** True for a uiIcon() glyph element (CSS mask), false for a colour image or null. */
export function isGlyph(el) {
  return /(^| )glyph-mask( |$)/.test(el?.getAttribute?.("class") || "");
}

/**
 * A single-colour game glyph (CSS mask, filled white by the badge) centred on the same glossy badge as the filled
 * glyphs, at 64 % of the badge: the one look shared by every task / UI-item icon. `label` makes the badge the image.
 */
export function glyphPlate(mask, size, tone, label = null) {
  const plate = linePlate("dot", { size, tone, label, cls: "glyph-plate" });
  plate.replaceChildren(mask);
  boxSize(mask, Math.round(size * 0.64));
  return plate;
}

/** Fixed square size via CSSOM (allowed under CSP style-src 'self'); minimal test DOMs without `style` are skipped. */
function boxSize(el, size, mask = null) {
  if (!el.style) return;
  el.style.width = `${size}px`;
  el.style.height = `${size}px`;
  if (mask) { el.style.webkitMaskImage = mask; el.style.maskImage = mask; }
}

/**
 * Item badge: the in-game icon when available, otherwise the line icon on a tinted plate. `label` is the accessible
 * name. Falls back to the plate if the image fails to load.
 */
export function itemIcon(item, { size = 20, label = null } = {}) {
  if (!item) return null;
  const name = label ?? item.label ?? "";
  const spec = ITEM_DEFAULTS[item.key] || {};
  const fallback = () => linePlate(spec.icon || item.icon || "dot", { size, tone: spec.tone, label: name || null, cls: "item-line" });
  const found = assets.items[item.key];
  if (!found) {
    const glyph = spec.ui ? uiIcon(spec.ui, { size, onFail: (bad) => bad.closest?.(".line-plate")?.replaceWith(fallback()) }) : null;
    return isGlyph(glyph) ? glyphPlate(glyph, size, spec.tone, name) : fallback();
  }
  const img = h("img", {
    class: "item-img", src: found.src, srcset: found.src2x ? `${found.src} 1x, ${found.src2x} 2x` : null, alt: name,
    width: String(size), height: String(size), draggable: "false", title: name || null,
  });
  img.addEventListener("error", () => img.replaceWith(fallback()), { once: true });
  return img;
}

// ---- badges (manifest `icons`): Produce rank badges, Vo/Da/Vi/Me attribute tiles ---------------------------------
const ATTR_KEYS = { vo: "attr_vo", vocal: "attr_vo", da: "attr_da", dance: "attr_da", vi: "attr_vi", visual: "attr_vi",
  me: "attr_me", mental: "attr_me" };

/** Badge key for a Produce rank: "S+" -> rank_s_plus, "SS"/"SS+" -> rank_ss, other "X+" -> rank_x; unknown -> null. */
export function rankIconKey(rank) {
  const m = /^(SS|S|A|B|C|D|E|F)(\+?)$/.exec(rank || "");
  if (!m) return null;
  if (m[1] === "S" && m[2]) return "rank_s_plus";
  return `rank_${m[1].toLowerCase()}`;
}

/** Badge key for an attribute word ("Vo" / "Vocal" / "da" …), or null (e.g. "Balanced"). */
export function attrIconKey(attr) {
  return ATTR_KEYS[String(attr || "").toLowerCase()] || null;
}

/**
 * In-game badge image for `key`, or null when the local manifest has none (callers keep their text). `label` is the
 * accessible name; `decorative` hides it from assistive tech when the same text sits next to it. Removes itself if the
 * image fails to load.
 */
export function badgeIcon(key, { size = 20, label = "", decorative = false, cls = "" } = {}) {
  const found = key ? assets.icons[key] : null;
  if (!found) return null;
  const img = h("img", {
    class: `badge-img${cls ? ` ${cls}` : ""}`, src: found.src, srcset: found.src2x ? `${found.src} 1x, ${found.src2x} 2x` : null,
    alt: decorative ? "" : label, "aria-hidden": decorative ? "true" : null, title: decorative ? null : label || null,
    width: String(size), height: String(size), draggable: "false", decoding: "async",
  });
  img.addEventListener("error", () => img.remove(), { once: true });
  return img;
}

/**
 * `ui_*` task / tab icon from the manifest, or null when missing (callers draw their line icon). Glyph icons (single-colour
 * game glyphs) become a CSS mask filled with currentColor, so they follow the text colour in both themes; colour icons are
 * plain images. `onFail` runs (and the element removes itself) if the file cannot be loaded.
 */
export function uiIcon(key, { size = 24, label = "", onFail = null, cls = "" } = {}) {
  const found = /^ui_/.test(key || "") ? assets.icons[key] : null;
  if (!found) return null;
  const a11y = { role: label ? "img" : null, "aria-label": label || null, "aria-hidden": label ? null : "true", title: label || null };
  let el;
  if (found.glyph) {
    el = h("span", { class: `glyph-mask${cls ? ` ${cls}` : ""}`, ...a11y });
    const url = `url("${found.src2x || found.src}")`;  // validated same-origin relative path (SRC)
    boxSize(el, size, url);
    if (typeof Image === "function") {
      const probe = new Image();
      probe.addEventListener("error", () => { if (onFail) onFail(el); else el.remove(); }, { once: true });
      probe.src = found.src2x || found.src;
    }
  } else {
    el = h("img", { class: `ui-img${cls ? ` ${cls}` : ""}`, src: found.src, srcset: found.src2x ? `${found.src} 1x, ${found.src2x} 2x` : null,
      alt: label, ...a11y, role: null, width: String(size), height: String(size), draggable: "false", decoding: "async" });
    el.addEventListener("error", () => { if (onFail) onFail(el); else el.remove(); }, { once: true });
  }
  return el;
}

/** P card (Produce idol) by wiki id, or null. */
export function pCardArt(id) {
  if (!id) return null;
  return assets.pCards.find((c) => c.id === id) || null;
}

/** Wiki card by id from either list (P ids start with 1, S ids with 2). */
export function anyCardArt(id) {
  if (!id) return null;
  return /^1/.test(id) ? pCardArt(id) : assets.cards.find((c) => c.id === id) || null;
}

/** Idol number encoded in a wiki card id (digits 4–6), or "" for other ids. */
export function idolNumber(id) {
  return /^[12]\d{9}$/.test(id || "") ? id.slice(3, 6) : "";
}

/** Card art for a training option case or card id, or null when no thumbnail exists. */
export function cardArt(id) {
  if (!id) return null;
  return assets.cards.find((c) => c.option === id) || assets.cards.find((c) => c.id === id) || null;
}

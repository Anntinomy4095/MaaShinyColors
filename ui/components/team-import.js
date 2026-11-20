// Reviewed import of the offline card scan (tools/card_inventory_scan.py -> ui/game-assets/inventory-scan.js, local and
// gitignored). The detected cards are shown pre-checked with their match score; nothing is added to 持有卡牌 until the
// user presses 确定. Detections never remove cards the user already marked. Same-origin module import only (CSP).
import { h } from "./dom.js";
import { scanSuffix } from "./scan-version.js";
import { anyCardArt } from "./game-assets.js";
import { openGridPicker } from "./card-picker.js";

const ID = /^[12]\d{9}$/;

/** Validated scan: {cards: [{id, score, verify, frames}], frames: n, generatedAt} (anything unexpected dropped). */
export function cleanScan(data) {
  const out = { cards: [], frames: 0, generatedAt: "", rejected: 0 };
  if (!data || typeof data !== "object" || data.version !== 1) return out;
  const seen = new Set();
  for (const card of Array.isArray(data.cards) ? data.cards.slice(0, 3000) : []) {
    if (typeof card?.id !== "string" || !ID.test(card.id) || seen.has(card.id)) continue;
    seen.add(card.id);
    const num = (v) => (typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0);
    out.cards.push({ id: card.id, score: num(card.score), verify: num(card.verify), frames: Number.isInteger(card.frames) ? card.frames : 0,
      // thumbnail-match confidence (tools/card_inventory_scan.py); older scans have none -> null (treated as low)
      confidence: typeof card.confidence === "number" && Number.isFinite(card.confidence) ? num(card.confidence) : null });
  }
  out.frames = Array.isArray(data.frames) ? data.frames.length : 0;
  out.rejected = Number.isInteger(data.rejected_tiles) && data.rejected_tiles > 0 ? Math.min(data.rejected_tiles, 99999) : 0;
  out.generatedAt = typeof data.generated_at === "string" ? data.generated_at.slice(0, 32) : "";
  return out;
}

export async function loadInventoryScan() {
  try {
    const module = await import(`../game-assets/inventory-scan.js${scanSuffix()}`);
    return cleanScan(module.default);
  } catch {
    return cleanScan(null);
  }
}

/** Owned lists after merging the confirmed ids (union; never removes). */
export function mergeOwned(owned, ids) {
  const p = new Set(owned.p || []);
  const s = new Set(owned.s || []);
  for (const id of ids) (id.startsWith("1") ? p : s).add(id);
  return { p: [...p].sort(), s: [...s].sort() };
}

export async function openInventoryImport({ owned, onApply }) {
  const scan = await loadInventoryScan();
  if (!scan.cards.length) {
    import("./common.js").then((m) => m.toast("没有识别结果：先用 tools/card_inventory_scan.py 扫描已保存的卡牌一览截图", "warning")).catch(() => {});
    return null;
  }
  const have = new Set([...(owned.p || []), ...(owned.s || [])]);
  const entries = scan.cards.map((card) => {
    const art = anyCardArt(card.id);
    const name = (art?.idol || card.id).replace(/\s+/g, "");
    return {
      id: card.id, selectable: true, name, sub: art?.title || "", badge: art?.rarity || "",
      sub2: have.has(card.id) ? "已持有" : `匹配 ${Math.round(card.score * 100)}%`,
      section: card.id.startsWith("1") ? "P 卡" : "S 卡",
      search: [art?.title, art?.idolEn, art?.titleEn].filter(Boolean).join(" "),
      tip: `${name}${art?.title ? `【${art.title}】` : ""}\n匹配 ${Math.round(card.score * 100)}% / 复核 ${Math.round(card.verify * 100)}% · ${card.frames} 帧`,
      visual: () => h("span", { class: "card-visual" }, art?.src
        ? h("img", { class: "card-img", src: art.src, alt: "", width: "96", height: "96", draggable: "false", loading: "lazy" })
        : h("span", { class: "card-img card-placeholder" })),
    };
  }).sort((a, b) => a.section.localeCompare(b.section) || a.id.localeCompare(b.id));
  return openGridPicker({
    title: "导入识别结果", entries, multi: true, initial: scan.cards.map((c) => c.id), bulk: true,
    searchPlaceholder: "搜索偶像 / 卡名", emptyText: "没有匹配的卡牌", className: "team-dialog",
    foot: h("span", { class: "card-cost item-note" }, `${scan.frames} 帧识别；只添加勾选的卡，不移除已持有的卡`),
    countText: (chosen) => `勾选 ${chosen.size} / 识别 ${scan.cards.length}`,
    onCommit: (chosen) => onApply(mergeOwned(owned, [...chosen])),
  });
}

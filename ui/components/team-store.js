// 「持有卡牌」: the cards the user marked as owned (wiki ids; P = 1…, S = 2…). Display preference only — it filters the
// Produce team pickers and is the pool for the future 自动编队 scorer; it never authorises anything.
// Stored in the UI profile prefs (`prefs.owned_cards`). The host protocol must declare that field before the panel
// sends it (unknown prefs are rejected); until then the host build keeps it in this page's localStorage only.
// A later read-only in-game scan of the idol list could fill it automatically.
// Optional `details` holds the user's Lv / 凸 per owned card for the 自动编队 recommender (unknown = absent).
import protocol from "../protocol.js";
import { loadLocal, saveLocal } from "./bridge.js";

const LOCAL_KEY = "sc-ui-owned-cards";
const MAX_IDS = 2000;  // same cap as ui/protocol.json prefs.owned_cards (the host rejects longer lists)
const MAX_DETAILS = 1000;  // UiProfileStore.MaxOwnedDetails
export const MAX_LEVEL = 100;  // ui/protocol.json prefs.owned_cards.details.*.level maximum
export const MAX_LIMIT_BREAK = 4;

/**
 * Per-card Lv / 凸 (prefs.owned_cards.details) for ids in `owned` only. A missing field is unknown; non-integers and
 * out-of-range values are dropped (the host protocol would refuse them), empty entries removed.
 */
export function cleanDetails(value, owned) {
  const out = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return out;
  for (const id of Object.keys(value).sort()) {
    if (Object.keys(out).length >= MAX_DETAILS) break;
    const entry = value[id];
    if (!owned.has(id) || !entry || typeof entry !== "object") continue;
    const item = {};
    if (Number.isInteger(entry.level) && entry.level >= 1 && entry.level <= MAX_LEVEL) item.level = entry.level;
    if (Number.isInteger(entry.limit_break) && entry.limit_break >= 0 && entry.limit_break <= MAX_LIMIT_BREAK) item.limit_break = entry.limit_break;
    if (Object.keys(item).length) out[id] = item;
  }
  return out;
}
const PATTERN = { p: /^1\d{9}$/, s: /^2\d{9}$/ };

/** {p: [...], s: [...], details?: {id: {level?, limit_break?}}} with unique, sorted, well-formed ids only. */
export function normalizeOwned(value) {
  const out = { p: [], s: [] };
  for (const kind of ["p", "s"]) {
    const list = Array.isArray(value?.[kind]) ? value[kind] : [];
    out[kind] = [...new Set(list.filter((id) => typeof id === "string" && PATTERN[kind].test(id)))].sort().slice(0, MAX_IDS);
  }
  const details = cleanDetails(value?.details, new Set([...out.p, ...out.s]));
  if (Object.keys(details).length) out.details = details;
  return out;
}

/** Whether `prefs.owned_cards` may travel in profile.save (always in the browser preview's mock bridge). */
export function ownedPrefSupported(state) {
  if (state?.bridgeKind === "mock") return true;
  return Boolean(protocol?.definitions?.profile?.properties?.prefs?.properties?.owned_cards);
}

/** Whether this build's protocol declares `prefs.owned_cards.details` (Lv/凸); older hosts would refuse the save. */
export function detailsPrefSupported(state) {
  if (state?.bridgeKind === "mock") return true;
  return Boolean(protocol?.definitions?.profile?.properties?.prefs?.properties?.owned_cards?.properties?.details);
}

export function ownedCards(state) {
  // Host builds now carry prefs.owned_cards; a list saved earlier only in localStorage is used until the first save.
  const fromPrefs = ownedPrefSupported(state) ? state?.prefs?.owned_cards : null;
  return normalizeOwned(fromPrefs || loadLocal(LOCAL_KEY));
}

/**
 * Save the owned list. Returns true when it went through actions.setPref (the store re-renders), false when it was
 * kept in localStorage only (the caller re-renders its own field).
 */
export function saveOwnedCards(state, actions, value) {
  // Callers that only change the lists ({p, s}) keep the Lv/凸 already entered; details of removed cards are dropped.
  const clean = normalizeOwned(value && !("details" in value) ? { ...value, details: ownedCards(state).details } : value);
  if (ownedPrefSupported(state) && typeof actions?.setPref === "function") {
    if (!detailsPrefSupported(state)) delete clean.details;
    actions.setPref("owned_cards", clean);
    return true;
  }
  saveLocal(LOCAL_KEY, clean);
  return false;
}

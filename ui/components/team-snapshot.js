// W.I.N.G. 编队快照 (tools/ui_wing.py -> ui/game-assets/team-snapshot.js, local and gitignored; the host copies it into
// its own ui folder after a supervised W.I.N.G. run): the six cards Maa last read on ユニット編成 for the chosen P card.
// Display only (read-only strip + the rule check). The run itself reads the private JSON next to it and verifies the
// P card (always) and these cards (when the snapshot belongs to the same P and unit) before any spend. Pure helpers +
// one cached loader; no DOM.
import { scanSuffix } from "./scan-version.js";

const P_ID = /^1\d{9}$/;
const S_ID = /^2\d{9}$/;
let cached = null;
let loaded = false;

/** {unit, p_card, s_cards[4], guest, read_at} or null (same rules as tools/ui_wing.clean_snapshot). */
export function cleanSnapshot(data) {
  if (!data || typeof data !== "object" || data.version !== 1) return null;
  const { unit, p_card: p, s_cards: s, guest } = data;
  if (!(Number.isInteger(unit) && unit >= 1 && unit <= 20 && P_ID.test(p || "") && Array.isArray(s) && s.length === 4
    && s.every((id) => S_ID.test(id || "")) && S_ID.test(guest || ""))) return null;
  if (new Set([...s, guest]).size !== 5) return null;
  return { unit, p_card: p, s_cards: [...s], guest, read_at: typeof data.read_at === "string" ? data.read_at.slice(0, 32) : "" };
}

/** The snapshot loaded last (null before the first load or when there is none). */
export function teamSnapshot() {
  return cached;
}

/** Test / preview hook. */
export function setTeamSnapshot(value) {
  cached = cleanSnapshot(value);
  loaded = true;
}

export async function loadTeamSnapshot({ force = false } = {}) {
  if (loaded && !force) return cached;
  try {
    const module = await import(`../game-assets/team-snapshot.js${force ? `?t=${Date.now()}` : scanSuffix()}`);
    cached = cleanSnapshot(module.default);
  } catch {
    cached = null;
  }
  loaded = true;
  return cached;
}

/** Short date of a snapshot ("10-10 读取") or "". */
export function snapshotWhen(snapshot) {
  const m = /^\d{4}-(\d{2})-(\d{2})/.exec(snapshot?.read_at || "");
  return m ? `${m[1]}-${m[2]} 读取` : "";
}

// 剧本「按目标词条」 preview (2026-11-01, pure, node-tested): the same plan as agent/core/knowhow_auto_plan.py from the
// wanted list and the scanned books -- which scenario Auto will run and which missing entries Auto cannot find. The run
// itself is decided by Python (tools/ui_engine.knowhow_target); tests keep the two in step.
import SOURCES from "./knowhow-sources.js";
import { nameMatches, normalizeName } from "./knowhow-data.js";

export const AUTO_SCENARIOS = ["WING", "FANS", "GRAD", "LP", "STEP"];
const ORDER = ["GRAD", "LP", "FANS", "STEP", "WING"];
export const SCENARIO_LABEL = { WING: "W.I.N.G.", FANS: "ファン感謝祭", GRAD: "G.R.A.D.", LP: "Landing Point", STEP: "S.T.E.P." };

const rows = SOURCES.map(([entry, match, scenarios, auto, transferable, prefer]) => ({ entry, match, scenarios, auto, transferable, prefer }));

function sourceFor(name) {
  const have = normalizeName(name);
  const hits = rows.filter((s) => have === normalizeName(s.entry) || (s.match === "prefix" && have.startsWith(normalizeName(s.entry))));
  return hits.find((s) => normalizeName(s.entry) === have) || hits.sort((a, b) => b.entry.length - a.entry.length)[0] || null;
}

function autoScenarios(src) {
  if (src.auto !== true) return [];
  const own = src.scenarios.filter((s) => AUTO_SCENARIOS.includes(s));
  const preferred = src.prefer.filter((s) => own.includes(s));
  return preferred.length ? preferred : own;
}

/** {name, status, scenarios}: owned / auto / auto_unknown / manual / not_on_books / unknown (knowhow_auto_plan.classify). */
export function classify(item, books) {
  const level = Math.max(0, ...books.flatMap((b) => (b.rows || []).filter((r) => nameMatches(item, r.name)).map((r) => r.level || 0)));
  const src = sourceFor(item.name);
  const out = { name: item.name, level, scenarios: src ? [...src.scenarios] : [] };
  if (level >= (item.min_level || 1)) return { ...out, status: "owned" };
  if (!src) return { ...out, status: "unknown" };
  if (src.transferable === false) return { ...out, status: "not_on_books" };
  const auto = autoScenarios(src);
  if (auto.length) return { ...out, status: "auto", scenarios: auto };
  if (src.auto === null && src.scenarios.some((s) => AUTO_SCENARIOS.includes(s))) return { ...out, status: "auto_unknown" };
  return { ...out, status: "manual" };
}

/** {scenario (null: none, the run falls back to W.I.N.G.), targets, entries} (knowhow_auto_plan.plan without colours). */
export function targetPlan(wanted, books) {
  const entries = wanted.map((w) => classify(w, books));
  const score = {};
  for (const e of entries.filter((x) => x.status === "auto")) {
    score[e.scenarios[0]] = (score[e.scenarios[0]] || 0) + 1;
    for (const s of e.scenarios.slice(1)) score[s] ??= 0;
  }
  const ranked = Object.keys(score).sort((a, b) => score[b] - score[a] || ORDER.indexOf(a) - ORDER.indexOf(b));
  const scenario = ranked.length && score[ranked[0]] > 0 ? ranked[0] : null;
  return { scenario, entries,
    targets: scenario ? entries.filter((e) => e.status === "auto" && e.scenarios.includes(scenario)).map((e) => e.name) : [] };
}

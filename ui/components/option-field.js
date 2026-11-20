// OptionField: form controls generated from the PI v2 option schema plus the ui/annotations.json display layer.
// select -> segmented text (<=3 short cases), else pills (<=8 cases) or an in-place filterable list (2026-10-24: no
// dropdowns); switch -> toggle; card picker -> thumbnail button + modal.
// 2026-10-09: `control: "stepper"` = compact − n + spinbutton over numeric cases (non-numeric cases such as 全部用完 sit
// after the largest number) with optional quick chips; `control: "stage_target"` = segmented quick cases + 自定义, which
// reveals the remaining cases as pills grouped by area. Explanations stay out of the layout (2026-10-24: the layout carries the
// information): the option description, an annotation `note` and `hints[case]` are tooltips; only a 「消耗：…」 statement
// stays visible under the control, and a note starting with 暂不可用 locks the control with a 「未开放」 tag.
import { h, icon, jaText, nextId, focusAfterRender } from "./dom.js";
import { choiceChips, listPicker } from "./choice.js";
import { optionLabel, caseLabel, visibleCases, optionNote, groupNote } from "./display.js";
import { cardArt, itemIcon, itemKeyFor, itemSpec } from "./game-assets.js";
import { pickerButton, openItemPicker, groupSummary } from "./card-picker.js";
import { trainingCardButton, openTrainingCardPicker, trainingCardWarnings } from "./team-picker.js";
import { teamField } from "./team-field.js";
import { ownedCards } from "./team-store.js";
import { cleanWanted, loadKnowhowData } from "./knowhow-data.js";
import { SCENARIO_LABEL, targetPlan } from "./knowhow-auto-plan.js";

const SEGMENT_MAX_CASES = 3;
const SEGMENT_MAX_CHARS = 6;
const CHIP_MAX_CASES = 8;

export function optionControlKind(spec, cases = spec.cases, note = {}) {
  if (note.control === "card_picker") return "card";
  if (note.control === "stepper" && cases.length >= 1) return "stepper";
  if (note.control === "stage_target" && (note.quick_cases || []).length) return "stage";
  if (note.control === "unit_grid" && cases.length > 1) return "units";
  const names = cases.map((c) => c.name);
  if (note.control === "segmented" && names.length > 1 && names.length <= 5) return "segmented";  // annotations override
  if (spec.type === "switch" && names.length === 2 && names.includes("Yes") && names.includes("No")) return "switch";
  // annotations `control: "switch"` + `switch_on`: a two-case on/off select shown as a toggle (case names unchanged)
  if (note.control === "switch" && names.length === 2 && names.includes(note.switch_on)) return "switch";
  if (spec.type === "select" || spec.type === "switch") {
    if (names.length <= 1) return "readonly";
    if (names.length <= SEGMENT_MAX_CASES && note.labels.every((n) => n.length <= SEGMENT_MAX_CHARS)) return "segmented";
    return names.length <= CHIP_MAX_CASES ? "chips" : "list";
  }
  if (spec.type === "input") return "input";
  return "readonly";
}

/** Short display text for a chosen case (kept for callers without state: Yes/No become 开/关). */
export function caseText(spec, value) {
  if (spec?.type === "switch" && (value === "Yes" || value === "No")) return value === "Yes" ? "开" : "关";
  return value ?? "—";
}

/** visible: the 「消耗：…」 part of a note (the only text kept under a control); tip: the whole note for the tooltip. */
export function splitNote(text) {
  if (!text) return { visible: null, tip: null, unavailable: false };
  const at = text.indexOf("消耗：");
  return { visible: at >= 0 ? text.slice(at) : null, tip: text, unavailable: text.startsWith("暂不可用") };
}

/** The note of an option for its current value (annotations `keep_cases`, else `keep`); shown in the label tooltip. */
export function keptNote(note, value) {
  return note?.keep_cases?.[value] || note?.keep || null;
}

export function optionField({ state, key, value, disabled, onChange, focusKey, actions = null, siblings = null }) {
  const spec = state.schema.options[key];
  const note = optionNote(state, key);
  const cases = visibleCases(state, key, value);
  const kind = optionControlKind(spec, cases, { ...note, labels: cases.map((c) => caseLabel(state, key, c.name)) });
  const id = nextId("opt");
  const isDefault = value === spec.default_case;
  const split = splitNote(note.note);
  if (split.unavailable && isDefault) disabled = true;   // a changed value can still be switched back
  const labelTag = ["segmented", "chips", "list", "readonly", "card", "stepper", "stage", "units"].includes(kind) ? "span" : "label";
  // Item badge: the annotation's item, else an item named by a switch's key (shop goods, training ticket).
  const item = itemSpec(state, note.item || (spec.type === "switch" ? itemKeyFor(key) : null));
  // The value's consequence (keep_cases / keep) joins the tooltip, no visible line
  const keepText = keptNote(note, value ?? spec.default_case);
  const tip = [keepText, split.tip, spec.description].filter(Boolean).join("\n") || null;
  const labelRow = h("div", { class: "field-label-row", title: tip },
    item ? itemIcon(item, { size: 20, label: "" }) : null,
    h(labelTag, { class: "field-label", for: labelTag === "label" ? id : null, id: `${id}-label` }, jaText(optionLabel(state, key))),
    split.unavailable ? h("span", { class: "field-tag", text: "未开放" }) : null,
    isDefault ? null : h("span", { class: "field-changed", title: `默认：${caseLabel(state, key, spec.default_case)}`, role: "img", "aria-label": "已修改" }));
  let control;
  let extra = null;
  if (kind === "stepper") {
    ({ control, extra } = stepperControl({ state, key, value, cases, note, disabled, onChange, focusKey, labelledBy: `${id}-label` }));
  } else if (kind === "stage") {
    ({ control, extra } = stageControl({ state, key, value, cases, note, disabled, onChange, focusKey, labelledBy: `${id}-label` }));
  } else if (kind === "card") {
    const goScan = actions?.setTab ? () => actions.setTab("resources") : null;
    control = trainingCardButton(value, {
      id, disabled, focusKey, labelledBy: `${id}-label`,
      onclick: () => openTrainingCardPicker(state, { value, onSelect: onChange, goResources: goScan }),
    });
    // Prerequisite at the point of use: the picker lists 持有卡牌 only, so an empty list needs a card scan first.
    const ownedS = ownedCards(state).s || [];
    const notes = [];
    if (!ownedS.length) {
      notes.push(h("p", { class: "field-hint field-prereq" }, jaText("持有卡牌为空"),
        goScan ? h("button", { class: "btn btn-small btn-text", type: "button", dataset: { focusKey: `${focusKey}:scan` }, onclick: goScan }, "去扫描") : null));
    }
    // 2026-10-26 role-play D11: the chosen card may lack a live training sample (a card that left 持有卡牌 is flagged under
    // the task row itself, task-panel.js taskNotices)
    for (const warning of trainingCardWarnings(value, { art: cardArt(value), owned: ownedS })) {
      if (warning.key === "uncalibrated") notes.push(h("p", { class: "field-hint is-warn", dataset: { warning: warning.key } }, jaText(warning.text)));
    }
    extra = notes.length ? notes : null;
  } else if (kind === "switch") {
    const onName = note.control === "switch" && note.switch_on ? note.switch_on : "Yes";
    const offName = onName === "Yes" ? "No" : cases.map((c) => c.name).find((n) => n !== onName);
    const on = value === onName;
    control = h("button", {
      class: `toggle${on ? " is-on" : ""}`, type: "button", role: "switch", id, "aria-checked": String(on),
      "aria-labelledby": `${id}-label`, disabled, dataset: { focusKey },
      onclick: () => onChange(on ? offName : onName),
    }, h("span", { class: "toggle-track" }, h("span", { class: "toggle-thumb" })));
  } else if (kind === "segmented") {
    control = h("div", { class: "segmented text-seg", role: "radiogroup", "aria-labelledby": `${id}-label` },
      cases.map((c, i) => {
        const selected = c.name === value;
        return h("button", {
          class: `segment${selected ? " is-selected" : ""}`, type: "button", role: "radio", "aria-checked": String(selected),
          tabindex: selected || (!cases.some((x) => x.name === value) && i === 0) ? "0" : "-1",
          disabled, dataset: { focusKey: `${focusKey}:${i}` },
          onclick: () => onChange(c.name),
          onkeydown: (event) => {
            const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
            if (!delta) return;
            event.preventDefault();
            const next = (i + delta + cases.length) % cases.length;
            onChange(cases[next].name);
            focusAfterRender(`${focusKey}:${next}`);
          },
        }, jaText(caseLabel(state, key, c.name)));
      }));
  } else if (kind === "units") {
    // annotations `control: "unit_grid"` (2026-10-30, Auto): ユニット1–20 as the numbered grid of the W.I.N.G. team bar;
    // `overwrite_when` ({sibling option: case}) adds the same 「将覆盖ユニットN」 badge.
    const shown = value ?? spec.default_case;
    const chips = choiceChips({ options: cases.map((c) => [c.name, unitDigits(c.name), caseLabel(state, key, c.name)]), value: shown,
      onPick: onChange, focusKey, labelledBy: `${id}-label`, disabled, cls: "is-grid team-unit-chips" });
    const rule = note.overwrite_when;
    const overwrite = rule && Object.entries(rule).every(([k, v]) => (siblings?.[k] ?? state.schema.options[k]?.default_case) === v);
    control = h("div", { class: "unit-grid" }, chips,
      overwrite ? h("span", { class: "team-overwrite", role: "note" }, `将覆盖ユニット${unitDigits(shown)}`) : null);
  } else if (kind === "chips") {
    control = choiceChips({ options: cases.map((c) => [c.name, caseLabel(state, key, c.name), (note.hints || {})[c.name]]), value,
      onPick: onChange, focusKey, labelledBy: `${id}-label`, disabled });
  } else if (kind === "list") {
    control = listPicker({ items: cases.map((c) => ({ value: c.name, text: caseLabel(state, key, c.name), search: c.name })), value,
      onPick: onChange, focusKey, label: optionLabel(state, key), disabled });
  } else if (kind === "input") {
    control = h("input", { class: "input", id, type: "text", value: value ?? "", disabled, dataset: { focusKey }, onchange: (e) => onChange(e.target.value) });
  } else {
    control = h("div", { class: "readonly-value" }, jaText(caseLabel(state, key, value ?? spec.default_case)));
  }
  // annotations `heading`: a small group title above this option; hidden with the option.
  const heading = note.heading ? h("div", { class: "field-heading", role: "heading", "aria-level": "4", text: note.heading }) : null;
  if (heading) ensureSectionStyles();
  const hint = kind !== "stage" && split.visible ? h("p", { class: "field-hint" }, jaText(split.visible)) : null;
  const target = key === TARGET_OPTION && (value ?? spec.default_case) === TARGET_CASE ? targetPreview(state) : null;
  return h("div", { class: `field form-row kind-${kind}${heading ? " has-heading" : ""}` }, heading, labelRow, h("div", { class: "field-control" }, control), extra, hint, target);
}

// 剧本「按目标词条」: the scenario the run will pick for the missing wanted entries, and how many of them
// Auto cannot find (names in the tooltip). The run decides again with the same rules (tools/ui_engine.knowhow_target).
const TARGET_OPTION = "Auto核对剧本";
const TARGET_CASE = "按目标词条";
function targetPreview(state) {
  const node = h("p", { class: "field-hint target-preview", role: "status" }, "读取ノウハウ…");
  // the host keeps the wanted list in prefs.knowhow_wanted (the ノウハウ page's store)
  loadKnowhowData({ demo: state.bridgeKind === "mock" }).then((data) => {
    if (!data.loaded) { node.replaceChildren("先在ノウハウ页扫描书库"); return; }
    const plan = targetPlan(cleanWanted(state.prefs?.knowhow_wanted), data.books);
    const manual = plan.entries.filter((e) => e.status === "manual" || e.status === "auto_unknown");
    const where = (e) => (e.status === "auto_unknown" ? "Auto 未确认" : e.scenarios.length === 1 && e.scenarios[0] === "HALO" ? "say \"Halo\"" : "手动 G.R.A.D.");
    node.replaceChildren(
      jaText(plan.scenario ? `${SCENARIO_LABEL[plan.scenario]}：${plan.targets.join("、")}` : "没有 Auto 能拿的缺少词条，跑 W.I.N.G."),
      manual.length ? h("span", { class: "muted", title: manual.map((e) => `${e.name}（${where(e)}）`).join("\n") },
        ` · Auto 拿不到 ${manual.length}`) : null);
  }).catch(() => node.replaceChildren("读不到ノウハウ资料"));
  return node;
}

const isNumber = (name) => /^[0-9]+$/.test(name || "");
const unitDigits = (name) => (/(\d+)$/.exec(name || "") || [])[1] || name;

/** Step order of a stepper option: numeric cases ascending, then the other cases (e.g. 全部用完) in schema order. */
export function stepSequence(cases) {
  const names = cases.map((c) => c.name);
  return [...names.filter(isNumber).sort((a, b) => Number(a) - Number(b)), ...names.filter((n) => !isNumber(n))];
}

/** Value `delta` steps away from `value` in `seq`, clamped to the ends (an unknown value starts at the first step). */
export function stepValue(seq, value, delta) {
  const at = Math.max(0, seq.indexOf(value));
  return seq[Math.min(seq.length - 1, Math.max(0, at + delta))];
}

const STEP_KEYS = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 5, PageDown: -5 };

/**
 * Compact − n + stepper (WAI-ARIA spinbutton: arrows ±1, PageUp/PageDown ±5, Home/End) with quick chips from the
 * annotation `chips` (only names that exist as cases). The −/+ buttons are pointer helpers outside the tab order.
 */
function stepperControl({ state, key, value, cases, note, disabled, onChange, focusKey, labelledBy }) {
  const seq = stepSequence(cases);
  const at = seq.indexOf(value);
  const numbers = seq.filter(isNumber).map(Number);
  const set = (next) => { if (next !== undefined && next !== value) onChange(next); };
  const label = caseLabel(state, key, value);
  const unit = note.unit && isNumber(value) ? note.unit : "";
  const single = seq.length <= 1;
  const spin = h("span", {
    class: "stepper-value num", role: "spinbutton", tabindex: disabled ? "-1" : "0", "aria-labelledby": labelledBy,
    "aria-valuenow": isNumber(value) ? value : null, "aria-valuemin": numbers.length ? String(numbers[0]) : null,
    "aria-valuemax": numbers.length ? String(numbers[numbers.length - 1]) : null, "aria-valuetext": `${label}${unit}`,
    "aria-disabled": disabled ? "true" : null, "aria-readonly": single ? "true" : null, dataset: { focusKey },
    onkeydown: (event) => {
      if (disabled) return;
      if (event.key in STEP_KEYS) { event.preventDefault(); set(stepValue(seq, value, STEP_KEYS[event.key])); }
      else if (event.key === "Home") { event.preventDefault(); set(seq[0]); }
      else if (event.key === "End") { event.preventDefault(); set(seq[seq.length - 1]); }
    },
  }, h("span", { text: label }), unit ? h("span", { class: "stepper-unit", text: unit }) : null);
  const button = (delta, text, name) => h("button", {
    class: "stepper-btn", type: "button", tabindex: "-1", "aria-label": name, title: name,
    disabled: disabled || (delta < 0 ? at <= 0 : at >= seq.length - 1), onclick: () => set(stepValue(seq, value, delta)),
  }, text);
  const control = h("div", { class: `stepper${single ? " is-single" : ""}` },
    single ? null : button(-1, "−", "减少"), spin, single ? null : button(1, "+", "增加"));
  const names = new Set(seq);
  const chips = (note.chips || []).filter((name) => names.has(name));
  const extra = chips.length ? h("div", { class: "chip-row stepper-chips field-extra", role: "group", "aria-label": "常用值" },
    chips.map((name) => h("button", {
      type: "button", class: `chip${name === value ? " is-on" : ""}`, "aria-pressed": name === value ? "true" : "false", disabled,
      dataset: { focusKey: `${focusKey}:chip:${name}` }, onclick: () => set(name),
    }, h("span", { text: caseLabel(state, key, name) })))) : null;
  return { control, extra };
}

/**
 * Stage target: a segmented strip of the annotation `quick_cases` plus 「自定义」; while a non-quick case is chosen a
 * dropdown of the other cases appears under it. `hints[case]` (or `hints.custom`) is the strip's tooltip.
 */
function stageControl({ state, key, value, cases, note, disabled, onChange, focusKey, labelledBy }) {
  const quick = (note.quick_cases || []).filter((n) => cases.some((c) => c.name === n));
  const others = cases.filter((c) => !quick.includes(c.name));
  const custom = !quick.includes(value);
  const customLabel = note.custom_label || "自定义";
  const items = [...quick.map((name) => ({ name, label: caseLabel(state, key, name) })), { name: null, label: customLabel }];
  const pick = (item) => {
    if (item.name) { if (item.name !== value) onChange(item.name); return; }
    if (custom || !others.length) return;
    onChange(others.some((c) => c.name === note.custom_default) ? note.custom_default : others[0].name);
  };
  const selectedIndex = custom ? items.length - 1 : quick.indexOf(value);
  const hint = (note.hints || {})[custom ? "custom" : value] || null;
  const control = h("div", { class: "segmented text-seg stage-seg", role: "radiogroup", "aria-labelledby": labelledBy, title: hint },
    items.map((item, i) => h("button", {
      class: `segment${i === selectedIndex ? " is-selected" : ""}`, type: "button", role: "radio", "aria-checked": String(i === selectedIndex),
      tabindex: i === selectedIndex ? "0" : "-1", disabled: disabled || (!item.name && !others.length), dataset: { focusKey: `${focusKey}:${i}` },
      onclick: () => pick(item),
      onkeydown: (event) => {
        const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
        if (!delta) return;
        event.preventDefault();
        const next = (i + delta + items.length) % items.length;
        pick(items[next]);
        focusAfterRender(`${focusKey}:${next}`);
      },
    }, jaText(item.label))));
  const extra = custom && others.length ? h("div", { class: "field-extra" }, stageGrid({ state, key, value, cases: others, disabled, onChange,
    focusKey: `${focusKey}:custom`, label: `${customLabel}关卡` })) : null;
  return { control, extra };
}

/** 「1-1 · Vo 道具」 -> ["1-1", "vo"]: the stage on the pill, its reward as a colour dot (full label in the tooltip). */
function stageParts(label) {
  const [stage, reward = ""] = String(label).split(" · ");
  const kind = /^(Vo|Da|Vi)\b/.exec(reward)?.[1]?.toLowerCase() || (reward.startsWith("Leader") ? "lc" : "");
  return [stage, kind];
}

/**
 * Custom stage (2026-10-24, replaces the 25-row dropdown): every stage as a pill, grouped by area (「1-1 1-2 1-3 | 2-4 …」),
 * one tap; a dot shows the reward (Vo pink / Da blue / Vi amber / Leader·Center violet), the full label is the tooltip.
 */
function stageGrid({ state, key, value, cases, disabled, onChange, focusKey, label }) {
  const areas = new Map();
  cases.forEach((c, i) => {
    const text = caseLabel(state, key, c.name);
    const area = stageParts(text)[0].split("-")[0];
    if (!areas.has(area)) areas.set(area, []);
    areas.get(area).push({ c, i, text });
  });
  return h("div", { class: "chip-row choice-chips is-grid stage-grid", role: "radiogroup", "aria-label": label }, [...areas.values()].map((group) =>
      h("span", { class: "chip-group" }, group.map(({ c, i, text }) => {
        const [stage, reward] = stageParts(text);
        const on = c.name === value;
        return h("button", { type: "button", role: "radio", class: `chip${on ? " is-on" : ""}`, "aria-checked": String(on), title: text,
          tabindex: on ? "0" : "-1", disabled, dataset: { focusKey: `${focusKey}:${i}` }, onclick: () => { if (!on) onChange(c.name); } },
        reward ? h("span", { class: `stage-dot is-${reward}`, "aria-hidden": "true" }) : null, h("span", { text: stage }));
      }))));
}

const SECTION_STYLE_HREF = "styles/option-sections.css";

/** Link styles/option-sections.css once (CSP 'self'; index.html and the shared css files stay untouched). */
function ensureSectionStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${SECTION_STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: SECTION_STYLE_HREF }));
}

/**
 * One row for an item_picker group of Yes/No switches: label, a summary button (「银训练券 等 2 项」 / 「未选」) that
 * opens the multi-select picker, and the changed-mark when any member differs from its default.
 */
export function groupField({ state, group, keys, values, disabled, onChange, focusKey, actions = null }) {
  const note = groupNote(state, group);
  if (note.control === "team") return teamField({ state, group, keys, values, disabled, onChange, focusKey, actions });
  const id = nextId("grp");
  const changed = keys.some((k) => values[k] !== state.schema.options[k].default_case);
  const on = keys.filter((k) => values[k] === "Yes");
  const lead = h("span", { class: "group-lead", "aria-hidden": "true" },
    on.length ? itemIcon(itemSpec(state, optionNote(state, on[0]).item), { size: 24, label: "" }) : icon("dot", { size: 16 }));
  const labelRow = h("div", { class: "field-label-row", title: note.note || null },
    h("span", { class: "field-label", id: `${id}-label` }, jaText(note.label || group)),
    changed ? h("span", { class: "field-changed", title: "已修改", role: "img", "aria-label": "已修改" }) : null);
  const control = pickerButton({
    id, disabled, focusKey, labelledBy: `${id}-label`, lead, text: groupSummary(state, group, keys, values),
    title: on.map((k) => optionLabel(state, k)).join("、") || null,
    onclick: () => openItemPicker(state, { group, keys, values, onApply: onChange }),
  });
  return h("div", { class: "field form-row kind-group" }, labelRow, h("div", { class: "field-control" }, control));
}

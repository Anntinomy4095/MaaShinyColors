// U17 (ANN-49): named plans ("我的方案"), 撤销 for 重置 / 切换方案, and the small saved / unsaved indicator.
// The host's profile.save whitelist only keeps the built-in profile, so named plans live in the panel's own storage
// (localStorage, like the ノウハウ wanted-list fallback). A plan is a snapshot of tasks + removed_tasks; it never holds
// live-input authorisation (applying one goes through sanitizeProfile like any stored profile). Pure helpers first
// (unit-tested offline), DOM below.
import { h, icon } from "./dom.js";
import { loadLocal, saveLocal } from "./bridge.js";
import { sanitizeProfile, PROFILE_VERSION } from "./store.js";

export const PLANS_KEY = "sc-ui-plans";
export const MAX_PLANS = 12;
export const MAX_NAME = 16;
const STYLE_HREF = "styles/plans.css";

/** Trimmed plan name, or null when empty / too long / containing control or path-like characters. */
export function cleanPlanName(raw) {
  const name = typeof raw === "string" ? raw.normalize("NFKC").trim().replace(/\s+/g, " ") : "";
  return name && name.length <= MAX_NAME && !/[\u0000-\u001f<>"'`\\/]/.test(name) ? name : null;
}

const taskKey = (profile) => JSON.stringify({
  t: (profile.tasks || []).map((t) => [t.name, t.enabled === true, Object.entries(t.option || {}).sort(([a], [b]) => (a < b ? -1 : 1))]),
  r: [...(profile.removed_tasks || [])].sort(),
});

export function snapshotOf(profile, name, now = Date.now()) {
  return {
    name,
    preset: profile.preset || null,
    tasks: (profile.tasks || []).map((t) => ({ name: t.name, enabled: t.enabled === true, option: { ...(t.option || {}) } })),
    removed_tasks: [...(profile.removed_tasks || [])],
    saved_at: now,
  };
}

/** Plans read from storage: well-formed, unique names, capped. */
export function cleanPlans(raw) {
  const out = [];
  const seen = new Set();
  for (const item of Array.isArray(raw) ? raw : []) {
    const name = cleanPlanName(item?.name);
    if (!name || seen.has(name) || !Array.isArray(item.tasks) || out.length >= MAX_PLANS) continue;
    seen.add(name);
    out.push({
      name, preset: typeof item.preset === "string" ? item.preset : null, tasks: item.tasks,
      removed_tasks: Array.isArray(item.removed_tasks) ? item.removed_tasks : [], saved_at: Number(item.saved_at) || 0,
    });
  }
  return out;
}

export function loadPlanStore() {
  const raw = loadLocal(PLANS_KEY);
  const plans = cleanPlans(raw?.plans);
  const active = typeof raw?.active === "string" && plans.some((p) => p.name === raw.active) ? raw.active : null;
  return { plans, active };
}

export function savePlanStore(plans, active) {
  return saveLocal(PLANS_KEY, { plans, active });
}

/** Save / overwrite a plan by name. Returns { plans, name, replaced } or { error }. */
export function upsertPlan(plans, profile, rawName, now = Date.now()) {
  const name = cleanPlanName(rawName);
  if (!name) return { error: `方案名需要 1–${MAX_NAME} 个字符，不含 / \\ < > 引号` };
  const exists = plans.some((p) => p.name === name);
  if (!exists && plans.length >= MAX_PLANS) return { error: `最多 ${MAX_PLANS} 个方案：需删除一个` };
  const next = snapshotOf(profile, name, now);
  return { plans: exists ? plans.map((p) => (p.name === name ? next : p)) : [...plans, next], name, replaced: exists };
}

export function removePlan(plans, name) {
  return plans.filter((p) => p.name !== name);
}

/** A saved plan applied to the schema: unknown tasks / options are dropped; spend and input never travel in it. */
export function profileFromPlan(schema, plan) {
  return sanitizeProfile(schema, { schema_version: PROFILE_VERSION, preset: plan.preset, tasks: plan.tasks, removed_tasks: plan.removed_tasks }, plan.preset);
}

/** The active plan differs from the live profile (the user changed something after choosing it). */
export function planDirty(profile, plan) {
  return Boolean(plan) && taskKey(profile) !== taskKey(plan);
}

// ---------------------------------------------------------------- save indicator
export const SAVE_TEXT = {
  saved: "已自动保存",
  failed: "保存失败，改动尚未保存",
};

/** Beside the 方案 selector: a failed save (with 重试), or the button that keeps a changed plan. */
export function saveIndicator(state, actions) {
  const status = state.saveStatus || { state: "saved" };
  const plan = (state.plans || []).find((p) => p.name === state.activePlan) || null;
  const items = [];
  if (status.state === "failed") {
    items.push(h("span", { class: "save-state is-failed", role: "status", title: status.error || null }, h("span", { class: "save-dot", "aria-hidden": "true" }), h("span", { text: SAVE_TEXT.failed })));
    items.push(h("button", { class: "btn btn-text btn-small", type: "button", dataset: { focusKey: "save-retry" }, onclick: () => actions.retrySave() }, "重试"));
  }
  // A saved state needs no line; a changed plan shows only its button (dot = changed)
  if (plan && planDirty(state.profile, plan)) {
    items.push(h("button", { class: "btn btn-text btn-small", type: "button", disabled: state.run.active, dataset: { focusKey: "plan-update" }, onclick: () => actions.updatePlan() }, "更新方案"));
  } else if (!plan && state.profile.preset) {
    // A changed built-in 预设 says so too; it cannot be updated, so 另存为 keeps the change as a plan
    const preset = state.schema.presets.find((p) => p.name === state.profile.preset);
    const base = actions.presetProfile?.(state.profile.preset);
    if (preset && base && planDirty(state.profile, base)) {
      items.push(h("button", { class: "btn btn-text btn-small", type: "button", disabled: state.run.active, dataset: { focusKey: "plan-save-dirty" }, onclick: () => actions.openSavePlan() }, "存为方案"));
    }
  }
  return items;
}

// ---------------------------------------------------------------- 另存为 dialog
export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

export function openSavePlanDialog(state, { onSave, onDelete }) {
  if (typeof document === "undefined" || typeof document.createElement("dialog").showModal !== "function") return null;
  ensureStyles();
  const dialog = h("dialog", { class: "dialog plan-dialog", "aria-labelledby": "plan-dialog-title" });
  const close = () => { dialog.close(); dialog.remove(); };
  const input = h("input", { class: "input", type: "text", maxlength: String(MAX_NAME), id: "plan-name", placeholder: "例如：只练彩排", value: state.activePlan || "", autocomplete: "off" });
  const message = h("p", { class: "small plan-message", role: "status" });
  const submit = () => {
    const result = onSave(input.value);
    if (result?.error) { message.textContent = result.error; input.focus(); return; }
    close();
  };
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } });
  const saved = (state.plans || []).map((plan) => h("li", { class: "plan-item" },
    h("button", { type: "button", class: "btn btn-text btn-small plan-pick", title: "用这个名字覆盖保存", onclick: () => { input.value = plan.name; input.focus(); } }, plan.name),
    h("button", { type: "button", class: "icon-btn", "aria-label": `删除方案「${plan.name}」`, title: "删除方案", onclick: () => { onDelete(plan.name); close(); } }, icon("close", { size: 16 }))));
  dialog.append(...[
    h("h2", { id: "plan-dialog-title", class: "dialog-title", text: "把当前设置保存为方案" }),
    h("label", { class: "eyebrow", for: "plan-name", text: "方案名" }), input, message,
    saved.length ? h("ul", { class: "plan-list", "aria-label": "已有方案" }, saved) : null,
    h("div", { class: "dialog-actions" },
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn", onclick: close }, "取消"),
      h("button", { type: "button", class: "btn btn-primary", dataset: { planSave: "1" }, onclick: submit }, "保存"))].filter(Boolean));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  document.body.append(dialog);
  dialog.showModal();
  input.focus();
  input.select();
  return dialog;
}

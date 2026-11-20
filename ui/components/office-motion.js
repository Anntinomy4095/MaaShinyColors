// Small additive tracks over an unchanged native clip.
export const MOTION_VERSION = 1;
export const MOTION_OVERRIDES_KEY = "sc-office-motion-overrides-v1";
export const NEUTRAL = Object.freeze({ x: 0, y: 0, rotation: 0 });
export const STAND_BONES = [
  ["control", "整体"], ["body", "身体"], ["chest", "胸部"], ["neck", "脖子"], ["head", "头"],
  ["arm_L_top_down", "左上臂"], ["arm_L_bottom", "左前臂"], ["Hand_L", "左手"],
  ["arm_R_top_down", "右上臂"], ["arm_R_bottom", "右前臂"], ["hand_R", "右手"],
  ["face_control", "脸"], ["eyes", "视线"], ["eye_L", "左眼"], ["eye_R", "右眼"],
  ["eyebrow", "眉毛"], ["mouse", "嘴"],
];
export const EDITABLE_BONES = [
  ["control", "整体"], ["body", "身体"], ["body2", "上身"], ["head", "头"], ["neck", "脖子"],
  ["arm_L_top", "左上臂"], ["arm_L_bottom", "左前臂"], ["hand_L", "左手"],
  ["arm_R_top", "右上臂"], ["arm_R_bottom", "右前臂"], ["hand_R", "右手"],
  ["Pchan", "鸽子"], ["wing_L", "左翅膀"], ["wing_R", "右翅膀"],
];
// Preserve each rig's names; the standing models use different casing and arm chains.
export function motionBoneList(names, modelKind = "cb") {
  const preferred = modelKind === "stand" ? STAND_BONES : EDITABLE_BONES;
  const byName = new Map(names.map(name => [name.toLowerCase(), name]));
  const aliases = modelKind === "stand" ? {
    control:["control","All"], body:["body","body_1","body1","body_under"],
    arm_L_top_down:["arm_L_top","arm_L_top_down","arm_top_L"],
    arm_R_top_down:["arm_R_top","arm_R_top_down","arm_top_R"],
    arm_L_bottom:["arm_L_bottom","arm_L_bottom_down","arm_bottom_L","arm_bottom_L_up"],
    arm_R_bottom:["arm_R_bottom","arm_R_bottom_down","arm_R_bottm_down","arm_bottom_R"],
    Hand_L:["hand_L","hand_L_down","hand_L_up"], hand_R:["hand_R","hand_R_down","hand_R_up"],
    mouse:["mouse","mouth"],
  } : {};
  const common = preferred.map(([name,label]) => {
    const actual=(aliases[name]||[name]).map(alias=>byName.get(alias.toLowerCase())).find(Boolean);
    return [actual,label];
  }).filter(([name])=>name);
  const included = new Set(common.map(([name]) => name));
  const bones = [...common, ...names.filter(name => !included.has(name)).map(name => [name, name])];
  const handles = new Set(["整体", "身体", "头", "左手", "右手", "鸽子", "左翅膀", "右翅膀"]);
  return { bones, commonBones: common, handles: common.filter(([, label]) => handles.has(label)).map(([name]) => name) };
}
export function newMotion(character = "mano", baseAnimation = "wait", duration = 10.5) {
  return { version: MOTION_VERSION, character, target: "character", baseAnimation, startTime: 0, duration, fps: 30, loop: true, tracks: {} };
}
export function sampleTrack(keys, time) {
  if (!keys?.length || time < keys[0].time) return { ...NEUTRAL };
  let left = keys[0], right = left;
  for (const key of keys) { if (key.time <= time) left = key; if (key.time >= time) { right = key; break; } right = key; }
  const span = right.time - left.time;
  let t = span > 0 ? Math.max(0, Math.min(1, (time - left.time) / span)) : 0;
  if (left.curve === "hold") t = 0;
  else if (left.curve !== "linear") t = t * t * (3 - 2 * t);
  return Object.fromEntries(Object.keys(NEUTRAL).map((key) => [key, (left[key] || 0) + ((right[key] || 0) - (left[key] || 0)) * t]));
}
export function sampleMotion(project, time) {
  return Object.fromEntries(Object.entries(project.tracks || {}).map(([bone, keys]) => [bone, sampleTrack(keys, time)]));
}
export function setMotionKey(project, bone, time, pose, curve = "smooth") {
  const result = structuredClone(project);
  const at = Math.min(result.duration, Math.max(0, Math.round(time * result.fps) / result.fps));
  const keys = result.tracks[bone] || [];
  const key = { time: at, ...NEUTRAL, ...pose, curve };
  const index = keys.findIndex((k) => Math.abs(k.time - at) < 1e-5);
  if (index >= 0) keys[index] = key; else keys.push(key);
  result.tracks[bone] = keys.sort((a, b) => a.time - b.time);
  return result;
}
export function removeMotionKey(project, bone, time) {
  const result = structuredClone(project);
  result.tracks[bone] = (result.tracks[bone] || []).filter((k) => Math.abs(k.time - time) > .5 / result.fps);
  if (!result.tracks[bone].length) delete result.tracks[bone];
  return result;
}
export function moveMotionKey(project, bone, from, to) {
  const result = structuredClone(project), keys = result.tracks[bone] || [];
  const index = keys.findIndex((key) => Math.abs(key.time - from) < .5 / result.fps);
  if (index < 0) return result;
  const moved = { ...keys[index], time: Math.max(0, Math.min(result.duration, Math.round(to * result.fps) / result.fps)) };
  result.tracks[bone] = keys.filter((key, i) => i !== index && Math.abs(key.time - moved.time) > 1e-5).concat(moved).sort((a, b) => a.time - b.time);
  return result;
}
export function sourceTimeAt(project, time) {
  const start = project.startTime || 0;
  if (!("sourceEnd" in project)) return start + time;
  const progress = Math.max(0, Math.min(1, time / project.duration));
  return start + (project.sourceEnd - start) * progress;
}
export function resizeMotion(project, duration) {
  if (!Number.isFinite(duration) || duration <= 0) throw new Error("动作时长必须大于零");
  const result = structuredClone(project), ratio = duration / project.duration;
  for (const [bone, keys] of Object.entries(result.tracks)) {
    const byTime = new Map();
    for (const key of keys) {
      const time = Math.max(0, Math.min(duration, Math.round(key.time * ratio * result.fps) / result.fps));
      byTime.set(time, { ...key, time });
    }
    result.tracks[bone] = [...byTime.values()].sort((a, b) => a.time - b.time);
  }
  result.duration = duration;
  return result;
}
export function clearBoneMotion(project, bone) {
  const result = structuredClone(project);
  delete result.tracks[bone];
  return result;
}
export function applyMotionPose(skeleton, project, time, workingPose = {}) {
  const sampled = { ...sampleMotion(project, time), ...workingPose };
  const pinned = [];
  if (project.worldPositionBones?.length) {
    skeleton.updateWorldTransform();
    for (const name of new Set(project.worldPositionBones)) {
      const bone = skeleton.findBone(name);
      if (!bone?.parent) continue;
      const parent = bone.parent, pose = { ...NEUTRAL, ...sampled[name] };
      let depth = 0;
      for (let ancestor = parent; ancestor; ancestor = ancestor.parent) depth++;
      pinned.push({ bone, depth,
        x: bone.worldX + parent.a * pose.x + parent.b * pose.y,
        y: bone.worldY + parent.c * pose.x + parent.d * pose.y });
    }
  }
  for (const [name, value] of Object.entries(sampled)) {
    const pose = { ...NEUTRAL, ...value };
    const bone = skeleton.findBone(name);
    if (bone) { bone.x += pose.x; bone.y += pose.y; bone.rotation += pose.rotation; }
  }
  // Keep the native world path plus author translation in the original parent axes.
  // Resolve parents first so a pinned descendant sees its compensated parent matrix.
  for (const { bone, x, y } of pinned.sort((a, b) => a.depth - b.depth)) {
    skeleton.updateWorldTransform();
    const parent = bone.parent, determinant = parent.a * parent.d - parent.b * parent.c;
    if (!determinant) continue;
    const dx = x - parent.worldX, dy = y - parent.worldY;
    bone.x = (dx * parent.d - dy * parent.b) / determinant;
    bone.y = (dy * parent.a - dx * parent.c) / determinant;
  }
  if (pinned.length) skeleton.updateWorldTransform();
}
export function parseMotion(value) {
  const project = typeof value === "string" ? JSON.parse(value) : structuredClone(value);
  if (project?.version !== MOTION_VERSION || typeof project.character !== "string" || typeof project.baseAnimation !== "string"
    || !Number.isFinite(project.duration) || project.duration <= 0 || !project.tracks || typeof project.tracks !== "object") {
    throw new Error("动作文件格式不正确");
  }
  if ("worldPositionBones" in project && (!Array.isArray(project.worldPositionBones)
    || project.worldPositionBones.some(name => typeof name !== "string"))) throw new Error("世界位置骨骼设置不正确");
  if ("modelKind" in project && !["cb", "stand"].includes(project.modelKind)) throw new Error("模型类型不正确");
  for (const field of ["expressionAnimation", "gazeAnimation"]) {
    if (field in project && typeof project[field] !== "string") throw new Error("表情或视线设置不正确");
  }
  project.fps = 30; project.target = project.target === "bird" ? "bird" : "character";
  project.startTime = Number.isFinite(project.startTime) ? Math.max(0, project.startTime) : 0;
  if ("sourceEnd" in project && (!Number.isFinite(project.sourceEnd) || project.sourceEnd < project.startTime)) {
    throw new Error("原生终点不能早于起点");
  }
  if ("sourceLoop" in project && typeof project.sourceLoop !== "boolean") throw new Error("原生循环设置不正确");
  for (const keys of Object.values(project.tracks)) {
    if (!Array.isArray(keys) || keys.some((k) => ![k.time, k.x, k.y, k.rotation].every(Number.isFinite)
      || k.time < 0 || k.time > project.duration)) throw new Error("关键帧数值不正确");
    keys.sort((a, b) => a.time - b.time);
  }
  return project;
}
export function readMotionOverrides(storage = globalThis.localStorage) {
  try { return JSON.parse(storage?.getItem(MOTION_OVERRIDES_KEY) || "{}"); } catch { return {}; }
}
export function saveMotionOverride(project, storage = globalThis.localStorage) {
  const all = readMotionOverrides(storage); all[project.target === "bird" ? "pigeon" : project.character] = parseMotion(project);
  storage.setItem(MOTION_OVERRIDES_KEY, JSON.stringify(all));
}
export function clearMotionOverride(character, storage = globalThis.localStorage) {
  const all = readMotionOverrides(storage); delete all[character]; storage.setItem(MOTION_OVERRIDES_KEY, JSON.stringify(all));
}

import { loadOfficeNativeRuntime, loadOfficeNativeModel, officeNativeBounds } from "./office-eggs-native.js?v=8";
import { motionBoneList, applyMotionPose, sourceTimeAt } from "./office-motion.js?v=6";
const BIRDS = new Set(["body_Pchan", "wing_L", "wing_R"]);

export async function createMotionPlayer(host, onFrame = () => {}) {
  const { PIXI } = await loadOfficeNativeRuntime(document);
  const canvas = document.createElement("canvas");
  canvas.className = "motion-canvas"; host.append(canvas);
  const app = new PIXI.Application();
  await app.init({ view: canvas, width: 800, height: 450, resolution: 2, backgroundAlpha: 0,
    antialias: true, preference: "webgl", autoStart: false, preserveDrawingBuffer: true });
  app.ticker.maxFPS = 30;
  let spine, project, time = 0, playing = false, speed = 1, clipKey = "", zoom = 1;
  let boneInfo = { bones: [], handles: [] }, selectedBone = "head", view = "full";
  let transition = null, nativeParents = {};
  let working = {}, original = false, birdOnly = false, fitted, baseAnchor, loadId = 0;

  function sample(at) {
    const loop = project.sourceLoop ?? (project.sourceEnd === undefined);
    const layers = original ? [] : [project.expressionAnimation, project.gazeAnimation];
    const from = transition?.elapsed < transition?.duration ? transition : null;
    const nextClip = JSON.stringify([project.baseAnimation, loop, ...layers, from?.motion.baseAnimation]);
    if (nextClip !== clipKey) {
      clipKey = nextClip; spine.state.clearTracks();
      if (from && from.motion.baseAnimation !== "__setup__") {
        const previous = spine.state.setAnimation(0, from.motion.baseAnimation, from.motion.sourceLoop ?? (from.motion.sourceEnd === undefined));
        previous.trackTime = sourceTimeAt(from.motion, from.motion.duration);
        spine.skeleton.setToSetupPose(); spine.state.apply(spine.skeleton);
      }
      if (project.baseAnimation !== "__setup__") {
        const entry = spine.state.setAnimation(0, project.baseAnimation, loop);
        entry.mixDuration = from?.duration || 0;
      }
      layers.forEach((name, index) => { if (name) spine.state.setAnimation(index + 1, name, false); });
    }
    spine.skeleton.setToSetupPose();
    const track = spine.state.getCurrent(0);
    if (track) {
      track.trackTime = sourceTimeAt(project, at);
      if (from && track.mixingFrom) {
        track.mixTime = from.elapsed;
        track.mixingFrom.trackTime = sourceTimeAt(from.motion, from.motion.duration);
      }
    }
    for (let index = 1; index <= 2; index++) {
      const layer = spine.state.getCurrent(index);
      if (layer) layer.trackTime = layer.animation.duration;
    }
    spine.update(0);
  }
  function place() {
    if (!fitted) return;
    let box = fitted;
    if (!birdOnly && view !== "full") {
      const height = fitted.height * (view === "face" ? .3 : .62);
      box = { ...fitted, height };
      if (view === "face") {
        const headX = fitted.headX;
        if (Number.isFinite(headX)) box = { x: headX - height * .65, y: fitted.y, width: height * 1.3, height };
      }
    }
    const scale = Math.min((birdOnly ? 230 : 700) / Math.max(1, box.width),
      (birdOnly ? 160 : 365) / Math.max(1, box.height)) * zoom;
    spine.scale.set(scale);
    const ax = birdOnly ? baseAnchor?.x || 0 : 0, ay = birdOnly ? baseAnchor?.y || 0 : 0;
    spine.position.set(400 - (ax + box.x + box.width / 2) * scale,
      225 - (ay + box.y + box.height / 2) * scale);
  }
  function fromMix() { return transition ? Math.min(1, transition.elapsed / transition.duration) : 1; }
  function draw(render = true) {
    if (!spine || !project) return;
    sample(time); place();
    const points = boneInfo.bones.filter(([name]) => boneInfo.handles.includes(name) || name === selectedBone).map(([name, label]) => {
      const bone = spine.skeleton.findBone(name);
      if (!bone || birdOnly && !["Pchan", "wing_L", "wing_R"].includes(name) || !birdOnly && project.baseAnimation==="__setup__" && ["Pchan", "wing_L", "wing_R"].includes(name)) return null;
      return { name, label, parent: bone.parent?.data.name,
        x: spine.x + bone.worldX * spine.scale.x, y: spine.y + bone.worldY * spine.scale.y,
        angle: Math.atan2(bone.c * spine.scale.y, bone.a * spine.scale.x) };
    }).filter(Boolean);
    canvas.dataset.time = time.toFixed(4); canvas.dataset.clip = project.baseAnimation;
    canvas.dataset.character = project.character; canvas.dataset.modelKind = project.modelKind || "cb";
    canvas.dataset.original = String(original); canvas.dataset.keyedBones = String(Object.keys(project.tracks).length);
    canvas.dataset.view = view; canvas.dataset.mix = (fromMix()).toFixed(3);
    onFrame({ time, playing, points, project, sourceTime: sourceTimeAt(project, time) });
    if (render) app.render();
  }
  function fit() {
    if (!spine) return;
    const savedTime = time;
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity, headX;
    const steps = Math.min(80, Math.max(1, Math.ceil(project.duration * 8)));
    for (let i = 0; i <= steps; i++) {
      time = project.duration * i / steps; sample(time);
      if (i === 0) headX = (spine.skeleton.findBone("head") || spine.skeleton.findBone("Head"))?.worldX;
      const box = officeNativeBounds(spine);
      const x = box.x - (birdOnly ? baseAnchor?.x || 0 : 0), y = box.y - (birdOnly ? baseAnchor?.y || 0 : 0);
      left = Math.min(left, x); top = Math.min(top, y);
      right = Math.max(right, x + box.width); bottom = Math.max(bottom, y + box.height);
    }
    fitted = { x: left, y: top, width: right - left, height: bottom - top, headX };
    time = savedTime; draw();
  }
  async function load(next) {
    const mine = ++loadId; playing = false; app.stop();
    const data = await loadOfficeNativeModel(next.character, document, next.modelKind || "cb");
    if (mine !== loadId) return;
    const animations = data.animations.map(a => ({ name: a.name, duration: a.duration }));
    if (next.baseAnimation !== "__setup__" && !animations.some(a => a.name === next.baseAnimation)) throw new Error("此模型没有该原生动作");
    for (const name of [next.expressionAnimation, next.gazeAnimation].filter(Boolean)) {
      if (!animations.some(a => a.name === name)) throw new Error("此模型没有选定的表情或视线");
    }
    if (spine) { app.stage.removeChild(spine); spine.destroy(); }
    project = next; boneInfo = motionBoneList(data.bones.map(b => b.name), next.modelKind); time = 0; clipKey = ""; working = {}; original = false; transition = null;
    birdOnly = next.target === "bird"; view = "full"; zoom = 1; fitted = null;
    spine = new PIXI.Spine37.Spine({ skeletonData: data, autoUpdate: false });
    const skin = data.findSkin("normal") || data.defaultSkin || data.skins[0];
    if (skin) spine.skeleton.setSkin(skin);
    spine.state.data.defaultMix = 0;
    spine.beforeUpdateWorldTransforms = () => {
      baseAnchor = null;
      if (birdOnly) {
        spine.skeleton.updateWorldTransform();
        const bone = spine.skeleton.findBone("Pchan");
        if (bone) baseAnchor = { x: bone.worldX, y: bone.worldY };
      }
      nativeParents = {};
      if (project.worldPositionBones?.length) {
        spine.skeleton.updateWorldTransform();
        for (const name of project.worldPositionBones) {
          const parent = spine.skeleton.findBone(name)?.parent;
          if (parent) nativeParents[name] = { a:parent.a, b:parent.b, c:parent.c, d:parent.d };
        }
      }
      if (!original) applyMotionPose(spine.skeleton, project, time, working);
    };
    spine.afterUpdateWorldTransforms = () => {
      if (birdOnly) for (const slot of spine.skeleton.slots) if (!BIRDS.has(slot.data.name)) slot.setAttachment(null);
    };
    app.stage.addChild(spine); fit();
    return { animations, ...boneInfo };
  }
  function play(value) {
    if (value && project && time >= project.duration - 1e-6) time = 0;
    playing = value;
    if (value && !document.hidden) app.start(); else app.stop();
    draw();
  }
  app.ticker.add(ticker => {
    if (!playing || !project) return;
    time += ticker.deltaMS / 1000 * speed;
    if (time >= project.duration) {
      if (project.loop) time %= project.duration;
      else { time = project.duration; playing = false; app.stop(); }
    }
    draw(false);
  });
  function localDelta(name, dx, dy) {
    const parent = nativeParents[name] || spine?.skeleton.findBone(name)?.parent;
    dx /= spine?.scale.x || 1; dy /= spine?.scale.y || 1;
    if (!parent) return { x: dx, y: dy };
    const det = parent.a * parent.d - parent.b * parent.c;
    if (Math.abs(det) < 1e-8) return { x: 0, y: 0 };
    return { x: (parent.d * dx - parent.b * dy) / det, y: (-parent.c * dx + parent.a * dy) / det };
  }
  function rotationSign(name) {
    const bone = spine?.skeleton.findBone(name);
    if (!bone) return 1;
    const from = Math.atan2(bone.c, bone.a), value = bone.rotation;
    bone.rotation = value + .1; spine.skeleton.updateWorldTransform();
    const to = Math.atan2(bone.c, bone.a);
    bone.rotation = value; spine.skeleton.updateWorldTransform();
    return Math.sin(to - from) < 0 ? -1 : 1;
  }
  const visibility = () => { if (document.hidden) app.stop(); else if (playing) app.start(); };
  document.addEventListener("visibilitychange", visibility);
  return { canvas, load, fit, play, localDelta, rotationSign,
    setProject(value, pose = working) { project = value; working = pose; time = Math.min(time, project.duration); draw(); },
    setTransition(value) { transition = value; },
    setWorkingPose(value) { working = value; draw(); },
    setOriginal(value) { original = value; draw(); },
    seek(value) { time = Math.max(0, Math.min(project.duration, value)); draw(); },
    setSpeed(value) { speed = value; },
    setSelectedBone(value) { selectedBone = value; draw(); },
    setView(value) { view = value; draw(); },
    setZoom(value) { zoom = value; place(); draw(); },
    get time() { return time; }, get playing() { return playing; },
    destroy() { ++loadId; document.removeEventListener("visibilitychange", visibility);
      app.destroy(true, { children: true, texture: false, textureSource: false }); },
  };
}

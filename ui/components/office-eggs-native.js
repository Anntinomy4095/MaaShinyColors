import { applyMotionPose, readMotionOverrides, MOTION_OVERRIDES_KEY } from "./office-motion.js?v=2";
// Play native local timelines, with optional authored offsets. Source assets remain unchanged.
const indexUrl = new URL("../game-assets/office-native/index.js?v=5", import.meta.url);
let runtimePromise;
const scripts = new Map();
function script(url, doc) {
  if (!scripts.has(url)) scripts.set(url, new Promise((resolve, reject) => {
    const element = doc.createElement("script");
    element.src = url; element.onload = resolve; element.onerror = () => reject(new Error("native_runtime_unavailable"));
    doc.head.append(element);
  }));
  return scripts.get(url);
}
async function runtime(doc) {
  if (!runtimePromise) runtimePromise = (async () => {
    const bundle = await import(indexUrl.href);
    await script(new URL(bundle.runtime.pixi, indexUrl).href, doc);
    await script(new URL(bundle.runtime.csp, indexUrl).href, doc);
    await script(new URL(bundle.runtime.spine, indexUrl).href, doc);
    return { PIXI: globalThis.PIXI, models: bundle.default };
  })();
  return runtimePromise;
}

// Visible attachment bounds, including meshes. A bird-only model keeps the original parent-bone timeline.
function bounds(spine, selection = null) {
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const slot of spine.skeleton.slots) {
    if (selection && !selection.has(slot.data.name)) continue;
    const part = slot.getAttachment();
    if (!part?.computeWorldVertices) continue;
    let vertices;
    if (part.worldVerticesLength) {
      vertices = new Float32Array(part.worldVerticesLength);
      part.computeWorldVertices(slot, 0, vertices.length, vertices, 0, 2);
    } else if (part.offset) {
      vertices = new Float32Array(8); part.computeWorldVertices(slot.bone, vertices, 0, 2);
    } else continue;
    for (let i = 0; i < vertices.length; i += 2) {
      left = Math.min(left, vertices[i]); right = Math.max(right, vertices[i]);
      top = Math.min(top, vertices[i + 1]); bottom = Math.max(bottom, vertices[i + 1]);
    }
  }
  return Number.isFinite(left) ? { x: left, y: top, width: right - left, height: bottom - top }
    : { x: -50, y: -450, width: 100, height: 450 };
}
const BIRD_SLOTS = new Set(["body_Pchan", "wing_L", "wing_R"]);

export { runtime as loadOfficeNativeRuntime, bounds as officeNativeBounds };
const sharedModels = new Map();
export async function loadOfficeNativeModel(key, doc = globalThis.document, modelKind = "cb") {
  const cacheKey = `${key}:${modelKind}`;
  if (!sharedModels.has(cacheKey)) sharedModels.set(cacheKey, (async () => {
    const { PIXI, models: catalog } = await runtime(doc);
    const model = catalog[key]?.models?.[modelKind] || (modelKind === "cb" ? catalog[key] : null);
    if (!model) throw new Error(modelKind === "stand" ? "没有该角色的本机立绘素材" : "没有该角色的本机 Q 版素材");
    const moduleUrl = new URL(model.module, indexUrl);
    const source = (await import(moduleUrl.href)).default;
    const atlas = new PIXI.Spine37.TextureAtlas(source.atlas);
    await Promise.all(atlas.pages.map(async (page) => {
      const file = source.pages.find((p) => p.name === page.name);
      const image = new Image();
      await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error("本机贴图未能加载")); image.src = new URL(file.src, indexUrl).href; });
      const texture = new PIXI.ImageSource({ resource: image, alphaMode: page.pma ? "premultiplied-alpha" : "premultiply-alpha-on-upload" });
      page.setTexture(PIXI.Spine37.SpineTexture.from(texture));
    }));
    return new PIXI.Spine37.SkeletonJson(new PIXI.Spine37.AtlasAttachmentLoader(atlas)).readSkeletonData(source.skeleton);
  })());
  return sharedModels.get(cacheKey);
}


export function createOfficeNativeStage({ stage, doc = globalThis.document, onReady = () => {}, width = 336, height = 189, birdWidth = 23 }) {
  const canvas = doc.createElement("canvas");
  if (typeof canvas.getContext !== "function") return null;
  canvas.className = "egg-native-canvas"; canvas.setAttribute("aria-hidden", "true");
  stage.append(canvas);
  const actors = new Map(), birds = new Map(), models = new Map(), pending = new Map();
  let app, PIXI, catalog, snapshot, initialized = false, disposed = false, draws = 0, speed = 1, seenFeeds = 0, seenFlights = 0;
  let hitBoxes = [];
  let overrides = readMotionOverrides();
  const authored = (item) => overrides[item.bird ? "pigeon" : item.key];
  const metrics = { loaded: 0, actors: 0, birds: 0, frames: 0, error: null };
  function reduced() { return doc.documentElement?.dataset?.motion === "reduce" || globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches; }
  function stripBird(spine, onlyBird) {
    for (const slot of spine.skeleton.slots) {
      const isBird = BIRD_SLOTS.has(slot.data.name);
      if ((onlyBird && !isBird) || (!onlyBird && snapshot?.flock?.mode === "flying" && isBird)) slot.setAttachment(null);
    }
  }
  function make(data, bird = false, key = null) {
    const spine = new PIXI.Spine37.Spine({ skeletonData: data, autoUpdate: false });
    try { spine.skeleton.setSkinByName("normal"); } catch { spine.skeleton.setSkinByName("default"); }
    spine.state.data.defaultMix = .24;
    spine.state.setAnimation(0, "wait", true);
    spine.afterUpdateWorldTransforms = () => { if (bird) stripBird(spine, true); else stripBird(spine, false); };
    spine.update(0);
    const box = bounds(spine);
    const item = { spine, box, animation: "wait", bird, key, motionTime: 0 };
    spine.beforeUpdateWorldTransforms = () => {
      const motion = authored(item); if (motion) applyMotionPose(spine.skeleton, motion, item.motionTime);
    };
    app.stage.addChild(spine);
    return item;
  }
  async function loadModel(key) {
    if (models.has(key) || !catalog[key] || disposed) return;
    if (!pending.has(key)) pending.set(key, (async () => {
      const data = await loadOfficeNativeModel(key, doc);
      if (!disposed) { models.set(key, data); metrics.loaded = models.size; onReady(); }
    })().catch((error) => { metrics.error = String(error?.message || error); canvas.dataset.error = metrics.error; }));
    return pending.get(key);
  }
  function setClip(item, desired) {
    if (desired === item.animation) return;
    const isAuthored = Boolean(authored(item));
    item.spine.state.data.defaultMix = isAuthored ? 0 : .24;
    if (isAuthored || desired === "__setup__") item.spine.state.clearTracks();
    if (desired !== "__setup__" && item.spine.skeleton.data.findAnimation(desired)) item.spine.state.setAnimation(0, desired, true);
    item.animation = desired;
  }
  function advance(item, delta) {
    const motion = authored(item);
    if (!motion) { item.spine.update(delta); return; }
    const elapsed = item.motionTime + delta;
    item.motionTime = motion.loop === false ? Math.min(motion.duration, elapsed) : elapsed % motion.duration;
    item.spine.skeleton.setToSetupPose();
    const track = item.spine.state.getCurrent(0);
    if (track) track.trackTime = (motion.startTime || 0) + item.motionTime;
    item.spine.update(0);
  }
  function positionActor(item, actor) {
    const { spine, box } = item;
    const tall = actor.height || .68;
    const scale = height * tall / Math.max(1, box.height);
    spine.scale.set(scale);
    spine.position.set(actor.x * width - (box.x + box.width / 2) * scale,
      actor.y * height - (box.y + box.height) * scale);
    spine.zIndex = actor.layer || 2;
    setClip(item, authored(item)?.baseAnimation || actor.animation || "wait");
  }
  function sync() {
    if (!initialized || !snapshot || disposed) return;
    const active = new Set();
    hitBoxes = [];
    for (const actor of snapshot.story.actors) {
      if (!models.has(actor.key)) { loadModel(actor.key); continue; }
      active.add(actor.key);
      let item = actors.get(actor.key);
      if (!item) { item = make(models.get(actor.key), false, actor.key); actors.set(actor.key, item); }
      item.spine.visible = true; positionActor(item, actor);
    }
    for (const [key, item] of actors) item.spine.visible = active.has(key);
    const mano = actors.get("mano");
    if (mano?.spine.visible) {
      if (!overrides.mano && snapshot.flock.feeds > seenFeeds) mano.spine.state.setAnimation(0, "wait", true);
      if (!overrides.mano && snapshot.flock.flights > seenFlights) {
        mano.spine.state.setAnimation(0, "ng", false); mano.spine.state.addAnimation(0, "wait", true, 0);
      }
      if (snapshot.flock.mode !== "flying") {
        const box = bounds(mano.spine, BIRD_SLOTS), scale = mano.spine.scale.x;
        hitBoxes.push({ x: mano.spine.x + box.x * scale, y: mano.spine.y + box.y * scale, w: box.width * scale, h: box.height * scale });
      }
    }
    seenFeeds = snapshot.flock.feeds; seenFlights = snapshot.flock.flights;
    if (!models.has("mano")) loadModel("mano");
    const activeBirds = new Set();
    const hasMano = active.has("mano");
    for (let i = 0; i < snapshot.birds.length; i += 1) {
      const bird = snapshot.birds[i];
      // Mano's original wait already contains the first pigeon.
      if (i === 0 && hasMano && snapshot.flock.mode !== "flying") continue;
      if (!models.has("mano")) continue;
      activeBirds.add(bird.id);
      let item = birds.get(bird.id);
      if (!item) { item = make(models.get("mano"), true); birds.set(bird.id, item); }
      const { spine, box } = item;
      spine.visible = true; spine.alpha = bird.opacity;
      const scale = birdWidth * (bird.scale || 1) / Math.max(1, box.width);
      // Use unchanged native bird animation; simulation controls only its location.
      spine.scale.set(scale * (bird.facing === 1 ? -1 : 1), scale);
      const flying = ["fly", "takeoff"].includes(bird.pose);
      const phase = flying ? "fly" : "ground";
      const motion = authored(item);
      if (motion) setClip(item, motion.baseAnimation);
      else {
        setClip(item, "wait");
        const track = spine.state.getCurrent(0);
        if (item.phase !== phase) { track.trackTime = flying ? 5.6667 : 0; item.phase = phase; spine.update(0); }
        if (flying && track.trackTime > 7.2667) track.trackTime = 5.6667 + (track.trackTime - 5.6667) % 1.6;
        if (!flying && track.trackTime > 2) track.trackTime %= 2;
      }
      const anchor = spine.skeleton.findBone("Pchan");
      const ax = motion ? box.x + box.width / 2 : anchor?.worldX || box.x + box.width / 2;
      const ay = motion ? box.y + box.height : anchor?.worldY || box.y + box.height;
      spine.rotation = (bird.rotation || 0) * Math.PI / 180;
      const cos = Math.cos(spine.rotation), sin = Math.sin(spine.rotation);
      const ox = ax * spine.scale.x, oy = ay * scale;
      spine.position.set(bird.x * width - cos * ox + sin * oy,
        (bird.y + (bird.bob || 0)) * height - sin * ox - cos * oy);
      spine.zIndex = 5;
      hitBoxes.push({ x: bird.x * width - birdWidth / 2, y: bird.y * height - birdWidth * .8, w: birdWidth, h: birdWidth });
    }
    for (const [id, item] of birds) {
      if (!activeBirds.has(id)) { app.stage.removeChild(item.spine); item.spine.destroy(); birds.delete(id); }
    }
    metrics.actors = active.size; metrics.birds = activeBirds.size;
    canvas.dataset.actors = String(active.size); canvas.dataset.birds = String(activeBirds.size);
    canvas.dataset.loaded = String(models.size);
  }
  function storage(event) {
    if (event.key !== MOTION_OVERRIDES_KEY && event.key !== null) return;
    overrides = readMotionOverrides();
    for (const item of [...actors.values(), ...birds.values()]) { item.spine.state.clearTracks();item.spine.skeleton.setToSetupPose();item.animation = ""; item.motionTime = 0; }
    sync(); onReady();
  }
  globalThis.addEventListener?.("storage", storage);
  function visibility() { if (!app) return; if (doc.hidden) app.stop(); else app.start(); }
  const ready = (async () => {
    const loaded = await runtime(doc); PIXI = loaded.PIXI; catalog = loaded.models;
    if (disposed) return;
    app = new PIXI.Application();
    await app.init({ view: canvas, width, height, resolution: 2,
      backgroundAlpha: 0, antialias: true, preference: "webgl", autoStart: false, preserveDrawingBuffer: true });
    if (disposed) { app.destroy(true, { children: true }); return; }
    initialized = true;
    app.stage.sortableChildren = true; app.ticker.maxFPS = 30;
    app.ticker.add((ticker) => {
      if (!snapshot) return;
      const delta = snapshot.story.playing !== false && !reduced() ? Math.min(ticker.deltaMS, 100) / 1000 * speed : 0;
      for (const item of actors.values()) if (item.spine.visible) advance(item, delta);
      for (const item of birds.values()) advance(item, delta);
      sync(); metrics.frames = ++draws; canvas.dataset.frames = String(draws);
      const first = [...actors.values()].find((a) => a.spine.visible);
      canvas.dataset.time = String(first?.spine.state.getCurrent(0)?.trackTime || 0);
    });
    doc.addEventListener("visibilitychange", visibility); visibility();
    sync(); onReady();
  })().catch((error) => { metrics.error = String(error?.message || error); canvas.dataset.error = metrics.error; });
  return { ready, metrics,
    has: (key) => initialized && Boolean(actors.get(key)?.spine.visible),
    hitBird: (x, y) => hitBoxes.some((b) => x * width >= b.x - 5 && x * width <= b.x + b.w + 5 && y * height >= b.y - 5 && y * height <= b.y + b.h + 5),
    get hasBirds() { return initialized && models.has("mano"); },
    draw(snap) { snapshot = snap; sync(); },
    setSpeed(value) { speed = value; },
    destroy() { disposed = true; globalThis.removeEventListener?.("storage", storage); doc.removeEventListener("visibilitychange", visibility);
      app?.destroy(true, { children: true, texture: false, textureSource: false }); canvas.remove(); },
  };
}

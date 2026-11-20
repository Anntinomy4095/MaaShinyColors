import { loadOfficeNativeRuntime, loadOfficeNativeModel, officeNativeBounds } from "./office-eggs-native.js?v=6";

// One original animation, including its pigeon. No editor offsets or flock simulation.
export async function createOfficeShortScene(stage, onFrame = () => {}) {
  const { PIXI } = await loadOfficeNativeRuntime(document);
  const data = await loadOfficeNativeModel("mano");
  const clip = data.findAnimation("wait");
  const duration = clip.duration;
  const canvas = document.createElement("canvas");
  canvas.className = "short-canvas";
  canvas.setAttribute("aria-label", "真乃与鸽子");
  stage.append(canvas);
  const app = new PIXI.Application();
  await app.init({ view: canvas, width: 640, height: 360, resolution: 2,
    backgroundAlpha: 0, antialias: true, preference: "webgl", autoStart: false, preserveDrawingBuffer: true });
  app.ticker.maxFPS = 30;
  const actor = new PIXI.Spine37.Spine({ skeletonData: data, autoUpdate: false });
  try { actor.skeleton.setSkinByName("normal"); }
  catch { actor.skeleton.setSkinByName("default"); }
  actor.state.data.defaultMix = 0;
  actor.state.setAnimation(0, "wait", false);
  app.stage.addChild(actor);
  let time = 0, playing = false, frames = 0;

  function sample(at) {
    actor.skeleton.setToSetupPose();
    actor.state.getCurrent(0).trackTime = at;
    actor.update(0);
  }
  // Fit the complete native motion once, so flying wings cannot move the camera or leave the frame.
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (let at = 0; at < duration + 1 / 12; at += 1 / 12) {
    sample(Math.min(duration, at));
    const box = officeNativeBounds(actor);
    left = Math.min(left, box.x); top = Math.min(top, box.y);
    right = Math.max(right, box.x + box.width); bottom = Math.max(bottom, box.y + box.height);
  }
  const scale = Math.min(560 / (right - left), 282 / (bottom - top));
  actor.scale.set(scale);
  actor.position.set(320 - (left + right) / 2 * scale, 326 - bottom * scale);

  function draw(render = true) {
    sample(time); if (render) app.render();
    canvas.dataset.time = time.toFixed(3);
    canvas.dataset.duration = duration.toFixed(3);
    canvas.dataset.frames = String(++frames);
    onFrame({ time, duration, playing });
  }
  function play(value) {
    if (value && time >= duration) time = 0;
    playing = value;
    if (playing && !document.hidden) app.start(); else app.stop();
    draw();
  }
  app.ticker.add(ticker => {
    time = Math.min(duration, time + ticker.deltaMS / 1000);
    if (time >= duration) { playing = false; app.stop(); }
    draw(false);
  });
  const visibility = () => { if (document.hidden) app.stop(); else if (playing) app.start(); };
  document.addEventListener("visibilitychange", visibility);
  draw();
  return {
    play,
    replay() { time = 0; play(true); },
    seek(value) { playing = false; app.stop(); time = Math.max(0, Math.min(duration, value)); draw(); },
    get playing() { return playing; },
    destroy() {
      document.removeEventListener("visibilitychange", visibility);
      app.destroy(true, { children: true, texture: false, textureSource: false });
    },
  };
}

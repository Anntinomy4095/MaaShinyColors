import { createMotionPlayer } from "./office-motion-player.js?v=10";

// Native clips overlap briefly at each edit; seeking produces the same blend as playback.
export function storyActorFrame(actor, time) {
  let index = 0;
  for (let i = 1; i < actor.segments.length; i++) if (actor.segments[i].at <= time) index = i;
  const segment = actor.segments[index], local = Math.max(0, Math.min(segment.motion.duration, time - segment.at));
  const mix = segment.mix || 0;
  return { index, motion: segment.motion, time: local,
    transition: index > 0 && mix > 0 && local < mix
      ? { motion: actor.segments[index - 1].motion, elapsed: local, duration: mix } : null };
}

export async function createOfficeStory(host, story, onFrame) {
  const actors = await Promise.all(story.actors.map(async actor => {
    const layer = document.createElement("div"); layer.className = "story-actor";
    layer.style.transform = `translateX(${(actor.x - .5) * 100}%) scaleX(${actor.flip ? -1 : 1})`;
    layer.dataset.actor = actor.id; host.append(layer);
    const player = await createMotionPlayer(layer);
    await player.load(actor.segments[0].motion); player.setView(actor.view || "upper"); player.setZoom(actor.zoom || 1);
    return { actor, player, active: -1 };
  }));
  const duration = story.duration;
  let time = 0, playing = false, raf = null, previous = 0, lastDraw = 0;
  function draw() {
    for (const item of actors) {
      const frame = storyActorFrame(item.actor, time);
      item.player.setTransition(frame.transition);
      if (frame.index !== item.active) { item.active = frame.index; item.player.setProject(frame.motion, {}); }
      item.player.seek(frame.time);
      item.player.canvas.dataset.storyTime = time.toFixed(3);
      item.player.canvas.dataset.segment = String(frame.index + 1);
    }
    host.dataset.storyTime = time.toFixed(3);
    onFrame({ time, duration, playing });
  }
  function cancel() { if (raf !== null) cancelAnimationFrame(raf); raf = null; }
  function tick(now) {
    if (!playing) return;
    time = Math.min(duration, time + (now - previous) / 1000); previous = now;
    if (now - lastDraw >= 1000 / 30 || time >= duration) { lastDraw = now; draw(); }
    if (time >= duration) { playing = false; draw(); raf = null; }
    else raf = requestAnimationFrame(tick);
  }
  function play(value) {
    cancel(); if (value && time >= duration) time = 0;
    playing = value; previous = performance.now(); lastDraw = 0; draw();
    if (playing && !document.hidden) raf = requestAnimationFrame(tick);
  }
  const visibility = () => { cancel(); if (playing && !document.hidden) { previous = performance.now(); raf = requestAnimationFrame(tick); } };
  document.addEventListener("visibilitychange", visibility); draw();
  return { play, setOriginal(value) { for (const item of actors) item.player.setOriginal(value); },
    seek(value) { cancel(); playing = false; time = Math.max(0, Math.min(duration, value)); draw(); },
    get playing() { return playing; },
    destroy() { cancel(); document.removeEventListener("visibilitychange", visibility); for (const item of actors) item.player.destroy(); },
  };
}

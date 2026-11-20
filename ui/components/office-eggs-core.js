// Local office toys; no game commands are sent. Positions use fractions of the scene.
import { OFFICE_STORIES } from "./office-eggs-stories.js?v=4";
export { OFFICE_STORIES } from "./office-eggs-stories.js?v=4";
export const OFFICE_EGGS_VERSION = 2;
export const OFFICE_EGGS_STORAGE_KEY = "sc-office-eggs-v1";
export const FLOCK_SIZE = 28;
export const STORY_BEAT_MS = 12500;
export const FOOD_MS = 9000;
const FLIGHT_MS = 2800;
const RETURN_MS = 4800;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const count = (v) => Number.isSafeInteger(v) && v >= 0 ? v : 0;
const ease = (t) => t * t * (3 - 2 * t);

export function cleanOfficeState(saved) {
  if (!saved || ![1, OFFICE_EGGS_VERSION].includes(saved.version)) saved = {};
  const storyIndex = saved.version === OFFICE_EGGS_VERSION ? count(saved.storyIndex) % OFFICE_STORIES.length : 0;
  return { version: OFFICE_EGGS_VERSION, visits: count(saved.visits), feeds: count(saved.feeds),
    flights: count(saved.flights), storyTaps: count(saved.storyTaps), largestFlock: clamp(count(saved.largestFlock) || 1, 1, FLOCK_SIZE),
    storyIndex, beat: saved.version === OFFICE_EGGS_VERSION ? count(saved.beat) % 4 : 0,
    elapsedMs: saved.version === OFFICE_EGGS_VERSION ? clamp(Number(saved.elapsedMs) || 0, 0, STORY_BEAT_MS - 1) : 0,
    round: count(saved.round) };
}

export class OfficeEggs {
  constructor({ saved, clock = Date.now, random = Math.random, onSave = () => {} } = {}) {
    this.clock = clock; this.random = random; this.onSave = onSave;
    this.saved = cleanOfficeState(saved);
    this.visible = false; this.active = true; this.storyPlaying = true;
    this.birds = []; this.mode = "resting"; this.flightAt = null; this.food = null;
    this.arrivals = []; this.reaction = null; this.serial = 0; this.lastAt = this.clock();
  }
  exportState() { return { ...this.saved }; }
  _save() { try { this.onSave(this.exportState()); } catch { /* Storage does not interrupt the toy. */ } }
  _bird(now, entering = false) {
    const seed = this.random();
    return { id: ++this.serial, seed, born: now, entering, fromX: seed < .5 ? -.12 : 1.12,
      fromY: .12 + this.random() * .28, x: .1 + this.random() * .8, y: .76 + this.random() * .16 };
  }
  open() {
    if (!this.visible) {
      this.visible = true; this.lastAt = this.clock(); this.saved.visits += 1;
      this.birds = [this._bird(this.lastAt, true)]; this.mode = "resting";
      this.flightAt = null; this.food = null; this.arrivals = []; this.reaction = null; this._save();
    }
    return this.snapshot();
  }
  close() {
    this._advance(this.clock()); this.visible = false; this.birds = []; this.food = null;
    this.arrivals = []; this.reaction = null; this._save(); return this.snapshot();
  }
  setActive(active) { this._advance(this.clock()); this.active = Boolean(active); }
  pauseStory(paused) { this._advance(this.clock()); this.storyPlaying = !paused; return this.snapshot(); }
  selectStory(id) {
    const index = OFFICE_STORIES.findIndex((s) => s.id === id);
    if (index < 0) return this.snapshot();
    this.saved.storyIndex = index; this.saved.beat = 0; this.saved.elapsedMs = 0;
    this.lastAt = this.clock(); this.reaction = null; this._save(); return this.snapshot();
  }
  stepStory(direction = 1) {
    const size = OFFICE_STORIES.length * 4;
    const value = (this.saved.storyIndex * 4 + this.saved.beat + direction + size) % size;
    this.saved.storyIndex = Math.floor(value / 4); this.saved.beat = value % 4; this.saved.elapsedMs = 0;
    this.lastAt = this.clock(); this.reaction = null; this._save(); return this.snapshot();
  }
  feed(x = .5, y = .86) {
    const now = this.clock(); this._advance(now);
    if (!this.visible || this.mode === "flying" || this.food) return this.snapshot(now);
    this.food = { x: clamp(Number(x) || .5, .08, .92), y: clamp(Number(y) || .86, .72, .94), at: now, until: now + FOOD_MS };
    this.saved.feeds += 1;
    this.arrivals = [950, 2200, 3650].map((delay) => now + delay);
    for (const bird of this.birds) this._towardFood(bird, now);
    this._save(); return this.snapshot(now);
  }
  _towardFood(bird, now) {
    const here = this._ground(bird, now);
    bird.startX = here.x; bird.startY = here.y; bird.moveAt = now;
    bird.targetX = clamp(this.food.x + (this.random() - .5) * .4, .07, .93);
    bird.targetY = clamp(this.food.y + (this.random() - .5) * .08, .73, .95);
  }
  scatter() {
    const now = this.clock(); this._advance(now);
    if (!this.visible || this.mode === "flying") return this.snapshot(now);
    for (const bird of this.birds) bird.departure = this._ground(bird, now);
    this.mode = "flying"; this.flightAt = now; this.saved.flights += 1;
    this.food = null; this.arrivals = []; this.reaction = { kind: "mun", until: now + 1100 };
    this._save(); return this.snapshot(now);
  }
  tapProp(key) {
    if (!this.visible) return this.snapshot();
    const scene = OFFICE_STORIES[this.saved.storyIndex].beats[this.saved.beat];
    const item = scene.props.find((p) => p.key === key);
    if (!item) return this.snapshot();
    const kinds = { parcel: "peek", controller: "play", tea: "snooze", cookie: "sparkle", camera: "sparkle", sleep: "snooze" };
    this.saved.storyTaps += 1;
    this.reaction = { kind: kinds[item.asset] || "wobble", target: key, until: this.clock() + 1400 };
    this._save(); return this.snapshot();
  }
  _advance(now) {
    const delta = Math.max(0, now - this.lastAt); this.lastAt = now;
    if (this.visible && this.active && this.storyPlaying) {
      this.saved.elapsedMs += delta;
      if (this.saved.elapsedMs >= STORY_BEAT_MS) {
        const advance = Math.floor(this.saved.elapsedMs / STORY_BEAT_MS);
        const absolute = this.saved.storyIndex * 4 + this.saved.beat + advance;
        this.saved.round += Math.floor(absolute / (OFFICE_STORIES.length * 4));
        this.saved.storyIndex = Math.floor(absolute / 4) % OFFICE_STORIES.length;
        this.saved.beat = absolute % 4; this.saved.elapsedMs %= STORY_BEAT_MS;
        this._save();
      }
    }
    if (this.food && now >= this.food.until) { this.food = null; this.arrivals = []; }
    while (this.food && this.arrivals.length && now >= this.arrivals[0]) {
      const at = this.arrivals.shift();
      if (this.birds.length < FLOCK_SIZE) {
        const bird = this._bird(at, true); this._towardFood(bird, at); this.birds.push(bird);
        this.saved.largestFlock = Math.max(this.saved.largestFlock, this.birds.length); this._save();
      }
    }
    if (this.visible && this.mode === "flying" && now - this.flightAt >= RETURN_MS) {
      this.mode = "resting"; this.birds = [this._bird(this.flightAt + RETURN_MS, true)]; this.flightAt = null;
    }
    if (this.reaction && now >= this.reaction.until) this.reaction = null;
  }
  _ground(bird, now) {
    let x = bird.x, y = bird.y, pose = "idle", rotation = 0;
    let facing = bird.seed < .5 ? 1 : -1;
    if (this.food && bird.moveAt !== undefined) {
      const t = clamp((now - bird.moveAt) / 1050, 0, 1);
      x = bird.startX + (bird.targetX - bird.startX) * ease(t);
      y = bird.startY + (bird.targetY - bird.startY) * ease(t) - Math.sin(t * Math.PI) * .045;
      pose = t < 1 ? "hop" : "peck"; facing = bird.targetX > bird.startX ? -1 : 1;
      if (t === 1) rotation = (Math.sin((now - bird.moveAt) / 120 + bird.seed * 9) > .25 ? -13 : 0) * facing;
    } else {
      const age = Math.max(0, now - bird.born) + bird.seed * 3000;
      const t = clamp(((age % 4000) / 4000 - .3) / .18, 0, 1);
      const forward = Math.floor(age / 4000) % 2 === 0;
      x += (forward ? t : 1 - t) * (bird.seed < .5 ? .07 : -.07);
      y -= Math.sin(t * Math.PI) * .045;
      if (t > 0 && t < 1) { pose = "hop"; facing = forward ? -1 : 1; }
      else if (age % 8000 > 6600) { pose = "preen"; rotation = 7 * Math.sin(age / 180); }
      else if (age % 5000 < 800) { pose = "look"; facing *= -1; }
    }
    return { x, y, pose, rotation, facing };
  }
  snapshot(now = this.clock()) {
    this._advance(now);
    const birds = this.birds.map((bird, index) => {
      let { x, y, pose, rotation, facing } = this._ground(bird, now);
      let opacity = 1, squashX = 1, squashY = 1, altitude = 0;
      const age = Math.max(0, now - bird.born);
      if (bird.entering && age < 1400) {
        const t = clamp(age / 1100, 0, 1), progress = 1 - (1 - t) ** 3;
        const targetX = x, targetY = y;
        x = bird.fromX + (targetX - bird.fromX) * progress;
        y = bird.fromY + (targetY - bird.fromY) * progress - Math.sin(t * Math.PI) * .18;
        facing = bird.fromX < .5 ? -1 : 1; pose = t < .78 ? "fly" : "land";
        altitude = 1 - t; rotation = t < .78 ? (facing === 1 ? 8 : -8) : 0;
        if (t === 1) { squashY = 1 - Math.sin((age - 1100) / 300 * Math.PI) * .17; squashX = 2 - squashY; }
      }
      if (this.mode === "flying") {
        const age = now - this.flightAt - bird.seed * 240;
        const t = clamp((age - 170) / FLIGHT_MS, 0, 1);
        const origin = bird.departure || { x, y };
        if (age < 170) { x = origin.x; y = origin.y; pose = "crouch"; squashX = 1.12; squashY = .8; }
        else {
          x = origin.x + (bird.seed < .5 ? -1 : 1) * t * 1.35;
          y = origin.y - (.38 * Math.sin(t * Math.PI / 2) + t * t * 1.35);
          pose = t < .12 ? "takeoff" : "fly"; altitude = t * 2;
          rotation = (bird.seed < .5 ? 1 : -1) * Math.min(18, t * 65);
          opacity = t > .72 ? Math.max(0, (1 - t) / .28) : 1;
          facing = bird.seed < .5 ? 1 : -1;
        }
      }
      return { id: bird.id, x, y, pose, opacity, rotation, facing, squashX, squashY,
        bob: pose === "fly" ? Math.sin(now / 85 + bird.seed * 17) * .01 : 0,
        scale: .88 + bird.seed * .24,
        shadow: { x, y: bird.y + .015, scale: Math.max(.15, 1 - altitude * .65), opacity: Math.max(0, .2 - altitude * .22) } };
    });
    const story = OFFICE_STORIES[this.saved.storyIndex], scene = story.beats[this.saved.beat];
    return { visible: this.visible, birds, flock: { mode: this.mode, count: birds.length, birds,
      food: this.food ? { ...this.food, remaining: clamp(1 - (now - this.food.at) / FOOD_MS, 0, 1) } : null,
      largest: this.saved.largestFlock, feeds: this.saved.feeds, flights: this.saved.flights },
      story: { id: story.id, label: story.label, short: story.short, title: story.title, caption: scene.caption,
        index: this.saved.beat, total: story.beats.length, episodeIndex: this.saved.storyIndex,
        visits: this.saved.visits, round: this.saved.round, elapsedMs: this.saved.elapsedMs, playing: this.storyPlaying,
        actors: scene.actors.map((i) => ({ ...i })), props: scene.props.map((i) => ({ ...i })) },
      reaction: this.reaction ? { ...this.reaction } : null };
  }
}

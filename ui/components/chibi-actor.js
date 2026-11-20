import { loadOfficeNativeRuntime, loadOfficeNativeModel, officeNativeBounds } from "./office-eggs-native.js?v=10";

const CHIBI_WIDTH = 148;
const CHIBI_HEIGHT = 168;
const PORTRAIT_SIZE = 64;
const COSTUMES = ["first-ssr-casual", "casual-extra-1", "casual-extra-2", "staff"];
const REPLACED_OUTFITS = new Set(["1040040010", "1040060010", "1040110010", "1040130010"]);
const CASUAL = { stand: "wait", phase: 0, loop: true, actions: ["talk_ok_02", "pr"] };
const STAFF = { stand: "vocal_wait", phase: 0, loop: true, actions: ["radio_ok", "pr"] };
const NAMES = {
  mano: "櫻木真乃", hiori: "風野灯織", meguru: "八宮めぐる",
  kogane: "月岡恋鐘", mamimi: "田中摩美々", sakuya: "白瀬咲耶", yuika: "三峰結華", kiriko: "幽谷霧子",
  kaho: "小宮果穂", chiyoko: "園田智代子", juri: "西城樹里", rinze: "杜野凛世", natsuha: "有栖川夏葉",
  amana: "大崎甘奈", tenka: "大崎甜花", chiyuki: "桑山千雪", asahi: "芹沢あさひ", fuyuko: "黛冬優子", mei: "和泉愛依",
  toru: "浅倉透", madoka: "樋口円香", koito: "福丸小糸", hinana: "市川雛菜", nichika: "七草にちか", mikoto: "緋田美琴",
  luca: "斑鳩ルカ", hana: "鈴木羽那", haruki: "郁田はるき", hazuki: "七草叶月",
};
const portraits = new Map();
const ACTION_BLEND = .3;
const MIN_IDLE_SECONDS = 12;
const MAX_IDLE_SECONDS = 20;
const pick = (items, random) => items[Math.floor(random() * items.length)];
const performance = (kind) => kind === "staff" ? STAFF : CASUAL;
function standingPose(data, kind) {
  const { stand, phase, loop } = performance(kind);
  const clip = data.findAnimation(stand);
  return clip && { clip, time: clip.duration * phase, loop };
}

function fit(actor, pose, moves) {
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const clip of [pose.clip, ...moves]) {
    actor.state.clearTracks();
    actor.skeleton.setToSetupPose();
    const track = actor.state.setAnimation(0, clip.name, false);
    const fixed = clip === pose.clip && !pose.loop;
    const start = fixed ? pose.time : 0;
    const end = fixed ? pose.time : clip.duration;
    const steps = Math.max(1, Math.ceil((end - start) * 12));
    for (let step = 0; step <= steps; step++) {
      track.trackTime = start + (end - start) * step / steps;
      actor.update(0);
      const box = officeNativeBounds(actor);
      left = Math.min(left, box.x);
      top = Math.min(top, box.y);
      right = Math.max(right, box.x + box.width);
      bottom = Math.max(bottom, box.y + box.height);
    }
  }
  const scale = Math.min((CHIBI_WIDTH - 12) / Math.max(1, right - left),
    (CHIBI_HEIGHT - 15) / Math.max(1, bottom - top));
  actor.scale.set(scale);
  actor.position.set(CHIBI_WIDTH / 2 - (left + right) * scale / 2, CHIBI_HEIGHT - 4 - bottom * scale);
}

function createSkeleton(PIXI, data) {
  const actor = new PIXI.Spine37.Spine({ skeletonData: data, autoUpdate: false });
  const skin = data.findSkin("normal") || data.defaultSkin || data.skins[0];
  if (skin) actor.skeleton.setSkin(skin);
  return actor;
}

export async function createChibiActor(doc, random, selectedKey) {
  const { PIXI, models } = await loadOfficeNativeRuntime(doc);
  const characters = Object.entries(models).map(([key, entry]) => ({
    key, name: NAMES[key] || key,
    costumes: COSTUMES.filter(kind => {
      const model = entry.models?.[kind];
      return model && !REPLACED_OUTFITS.has(model.cardId);
    }),
  })).filter(character => character.costumes.length);
  if (!characters.length) return null;

  const selected = selectedKey ? characters.find(character => character.key === selectedKey) : pick(characters, random);
  if (!selected) return null;
  const { key, costumes } = selected;
  const costume = costumes.length === 1 ? costumes[0] : pick(costumes, random);
  const data = await loadOfficeNativeModel(key, doc, costume);
  const pose = standingPose(data, costume);
  if (!pose) return null;
  const { clip: idle } = pose;
  const { actions } = performance(costume);
  const moves = actions.map(name => data.findAnimation(name)).filter(clip => clip?.duration > 0);
  const reaction = data.findAnimation("ok");
  const canvas = doc.createElement("canvas");
  canvas.className = "chibi-canvas";
  canvas.setAttribute("aria-hidden", "true");
  const app = new PIXI.Application();
  await app.init({ canvas, width: CHIBI_WIDTH, height: CHIBI_HEIGHT, resolution: 2,
    backgroundAlpha: 0, antialias: true, preference: "webgl", autoStart: false });
  const actor = createSkeleton(PIXI, data);
  app.stage.addChild(actor);
  fit(actor, pose, [reaction, ...moves].filter(clip => clip?.duration > 0));
  const state = actor.state;
  state.data.defaultMix = ACTION_BLEND;

  let visible = false, ambient = true, idleLeft = null, previousMove = null, destroyed = false;
  let spontaneous = false;
  const isIdle = () => state.getCurrent(0).animation === idle;
  function syncTicker() {
    if (visible && (ambient || !isIdle())) app.start();
    else app.stop();
  }
  function stand(immediate) {
    spontaneous = false;
    if (immediate) {
      state.clearTracks();
      actor.skeleton.setToSetupPose();
    }
    const track = state.setAnimation(0, idle.name, Boolean(pose.loop));
    track.timeScale = pose.loop && ambient ? 1 : 0;
    track.trackTime = pose.time;
  }
  function move(clip, automatic = false) {
    spontaneous = automatic;
    idleLeft = null;
    state.setAnimation(0, clip.name, false);
    syncTicker();
  }
  state.addListener({ start: track => { canvas.dataset.animation = track.animation.name; } });
  stand(true);
  canvas.dataset.character = key;
  canvas.dataset.cardId = models[key].models[costume].cardId;
  actor.update(0);
  app.render();
  app.ticker.maxFPS = 30;
  app.ticker.add(ticker => {
    const track = state.getCurrent(0);
    if (track.animation !== idle && track.trackTime >= track.animation.duration) {
      stand(!ambient);
      if (!ambient) {
        actor.update(0);
        app.render();
        syncTicker();
        return;
      }
    }
    const delta = Math.min(ticker.deltaMS, 100) / 1000;
    const current = state.getCurrent(0);
    const previousTime = current.trackTime;
    actor.update(delta);
    if (!ambient || !moves.length || !isIdle()) return;
    idleLeft ??= MIN_IDLE_SECONDS + random() * (MAX_IDLE_SECONDS - MIN_IDLE_SECONDS);
    idleLeft -= delta;
    if (idleLeft > 0) return;
    if (pose.loop && Math.floor(previousTime / idle.duration) === Math.floor(current.trackTime / idle.duration)) return;
    const choices = moves.length > 1 ? moves.filter(clip => clip !== previousMove) : moves;
    previousMove = pick(choices, random);
    move(previousMove, true);
  });
  async function portrait(character) {
    if (portraits.has(character.key)) return portraits.get(character.key);
    const kind = character.costumes[0];
    const model = await loadOfficeNativeModel(character.key, doc, kind);
    if (destroyed) return null;
    const photo = createSkeleton(PIXI, model);
    const portraitStage = new PIXI.Container();
    portraitStage.addChild(photo);
    try {
      const portraitPose = standingPose(model, kind);
      if (portraitPose) photo.state.setAnimation(0, portraitPose.clip.name, true).trackTime = portraitPose.time;
      photo.update(0);
      const { x, y, width, height } = officeNativeBounds(photo, new Set(["head"]));
      const scale = PORTRAIT_SIZE / (Math.max(width, height, 1) * 1.55);
      photo.scale.set(scale);
      photo.position.set(PORTRAIT_SIZE / 2 - (x + width / 2) * scale, PORTRAIT_SIZE / 2 - (y + height * .35) * scale);
      const frame = new PIXI.Rectangle(0, 0, PORTRAIT_SIZE, PORTRAIT_SIZE);
      const url = app.renderer.extract.canvas({ target: portraitStage, frame, resolution: 1 }).toDataURL();
      portraits.set(character.key, url);
      return url;
    } finally { portraitStage.destroy({ children: true }); }
  }
  return {
    canvas, key, characters, portrait, width: CHIBI_WIDTH, height: CHIBI_HEIGHT,
    play(value, animateIdle = true) {
      visible = value;
      ambient = animateIdle;
      if (!ambient && (spontaneous || isIdle())) {
        stand(true);
        actor.update(0);
        app.render();
      } else if (ambient && pose.loop && isIdle()) state.getCurrent(0).timeScale = 1;
      syncTicker();
    },
    react() { if (visible && reaction && isIdle()) move(reaction); },
    destroy() {
      destroyed = true;
      app.destroy(true, { children: true, texture: false, textureSource: false });
    },
  };
}

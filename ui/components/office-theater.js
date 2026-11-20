export const THEATER_IDOLS = [
  { key: "mano", name: "真乃" },
  { key: "hiori", name: "灯织" },
  { key: "meguru", name: "巡" },
  { key: "amana", name: "甘奈" },
  { key: "tenka", name: "甜花" },
  { key: "asahi", name: "旭" },
  { key: "fuyuko", name: "冬优子" },
  { key: "koito", name: "小糸" },
  { key: "hinana", name: "雏菜" },
];

export const THEATER_SCENES = [
  { id: "rehearsal", title: "这次一起", cast: ["hiori", "meguru"], module: "../assets/office-scenes/q-rehearsal.js?v=1", editActor: "hiori" },
  { id: "photo", title: "再靠近一点", cast: ["koito", "hinana"], module: "../assets/office-scenes/q-photo.js?v=1", editActor: "koito" },
  { id: "curiosity", title: "越看越近", cast: ["asahi", "fuyuko"], module: "../assets/office-scenes/q-curiosity.js?v=1", editActor: "fuyuko" },
  { id: "twins", title: "找到你了", cast: ["amana", "tenka"], module: "../assets/office-scenes/q-twins.js?v=1", editActor: "tenka" },
  { id: "mimic", title: "谁在学谁", cast: ["mano"], module: "../assets/office-scenes/q-mimic.js?v=1", editActor: "mano" },
  { id: "peek", title: "团扇后面", cast: ["mano", "tenka"], module: "../assets/office-scenes/q-peek.js?v=1", editActor: "tenka" },
  { id: "q-theater", title: "乘凉小插曲", cast: ["mano", "tenka"], module: "../assets/office-scenes/q-theater.js?v=2", editActor: "tenka" },
];

export function findTheaterScene(id) {
  return THEATER_SCENES.find(scene => scene.id === id);
}

export function scenesForIdol(idol = "") {
  return !idol || idol === "all" ? THEATER_SCENES : THEATER_SCENES.filter(scene => scene.cast.includes(idol));
}

export function theaterIdolName(key) {
  return THEATER_IDOLS.find(idol => idol.key === key)?.name ?? key;
}

export async function loadTheaterScene(id) {
  const scene = findTheaterScene(id);
  if (!scene) throw new Error(`找不到小剧场：${id}`);
  return (await import(new URL(scene.module, import.meta.url).href)).default;
}

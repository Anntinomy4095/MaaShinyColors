import { OFFICE_CHARACTER_KEYS } from "./office-eggs-stories.js?v=4";
const publicRoot = new URL("../assets/office/", import.meta.url);
const privateModule = new URL("../game-assets/office-eggs.js", import.meta.url);
const files = { pigeon: "dove-rest.svg", pigeonFly: "dove-flight.svg", parcel: "parcel.svg", controller: "controller.svg",
  gift: "gift.svg", sleep: "sleep.svg", camera: "camera.svg", castle: "castle.svg", flag: "flag.svg", cup: "cup.svg",
  tea: "tea.svg", cookie: "cookie.svg", rainbow: "rainbow.svg", paper: "paper.svg", book: "book.svg", leaf: "leaf.svg",
  music: "music.svg", stone: "stone.svg", sparkle: "sparkle.svg" };
export const OFFICE_PROP_KEYS = Object.keys(files);
export async function loadOfficeAssets({ load = () => import(privateModule.href) } = {}) {
  let local = {};
  try { local = (await load()).default || {}; } catch { /* Character artwork is optional. */ }
  const assets = Object.fromEntries(Object.entries(files).map(([key, name]) => [key,
    { src: new URL(name, publicRoot).href, source: "Noto Emoji", license: "Apache-2.0" }]));
  for (const key of OFFICE_CHARACTER_KEYS) {
    const item = local[key];
    if (item?.src && /^\.\/office\/[a-z]+\.png$/.test(item.src)) {
      assets[key] = { ...item, src: new URL(item.src, privateModule).href, local: true };
    }
  }
  return { assets, charactersReady: OFFICE_CHARACTER_KEYS.every((key) => Boolean(assets[key])),
    missing: OFFICE_CHARACTER_KEYS.filter((key) => !assets[key]) };
}

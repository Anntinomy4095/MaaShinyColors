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

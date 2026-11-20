// DOM helpers. Data text is only ever inserted with textContent / text nodes, never parsed as HTML.
import { ICONS } from "./icons.js?v=8";
import { GLYPHS } from "./glyphs.js?v=8";

const SVG_NS = "http://www.w3.org/2000/svg";

/** Move a keyboard selection's focus after the store's queued render has restored the previous focus. */
export function focusAfterRender(key) {
  queueMicrotask(() => {
    const element = document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
    if (element && !element.disabled) element.focus({ preventScroll: true });
  });
}

/**
 * h("div", {class: "x", onclick: fn, dataset: {...}}, child, "text", [more])
 * Strings become text nodes. Attributes are set with setAttribute; event handlers via addEventListener.
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

export function applyAttrs(el, attrs) {
  if (!attrs) return el;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (key === "dataset") {
      Object.assign(el.dataset, value);
    } else if (key === "text") {
      el.textContent = String(value);
    } else if (key === "props") {
      Object.assign(el, value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Remove all children (safe alternative to clearing markup). */
export function clear(el) {
  el.replaceChildren();
  return el;
}

/** Inline SVG icon from the self-drawn icon set (no external files, no game assets). */
export function icon(name, { size = 16, label = null, cls = "" } = {}) {
  const spec = ICONS[name] || ICONS.dot;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("class", `icon ${cls}`.trim());
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  for (const d of spec.paths || []) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  for (const [cx, cy, r] of spec.circles || []) {
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", String(cx));
    c.setAttribute("cy", String(cy));
    c.setAttribute("r", String(r));
    svg.append(c);
  }
  if (spec.fill) svg.setAttribute("fill", "currentColor");
  return svg;
}

/**
 * Filled glyph (glyphs.js): one colour (currentColor), even-odd cut-outs. Used on the glossy badges (white) and as the
 * no-asset fallback for tab / task / item icons. Unknown names draw the dot.
 */
export function glyph(name, { size = 16, label = null, cls = "" } = {}) {
  const paths = GLYPHS[name] || GLYPHS.dot;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("class", `glyph ${cls}`.trim());
  svg.setAttribute("fill", "currentColor");
  svg.setAttribute("fill-rule", "evenodd");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", paths.join(" "));
  svg.append(path);
  return svg;
}

const KANA_RUN = /[぀-ヿㇰ-ㇿｦ-ﾟ]+(?:[ー・][぀-ヿㇰ-ㇿｦ-ﾟ]*)*/g;

let jaTerms = [];
export function setJaTerms(terms) {
  jaTerms = [...new Set(terms || [])].filter(Boolean).sort((a, b) => b.length - a.length);
}

/**
 * Text with Japanese game terms wrapped in <span lang="ja"> (kana runs plus the annotation term list),
 * so the Japanese font stack applies. Returns a DocumentFragment of text nodes and spans.
 */
export function jaText(text) {
  const frag = document.createDocumentFragment();
  const source = String(text ?? "");
  const ranges = [];
  for (const m of source.matchAll(KANA_RUN)) ranges.push([m.index, m.index + m[0].length]);
  for (const term of jaTerms) {
    let from = 0;
    for (;;) {
      const i = source.indexOf(term, from);
      if (i < 0) break;
      ranges.push([i, i + term.length]);
      from = i + term.length;
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  let pos = 0;
  for (const [s, e] of merged) {
    if (s > pos) frag.append(document.createTextNode(source.slice(pos, s)));
    const span = document.createElement("span");
    span.setAttribute("lang", "ja");
    span.textContent = source.slice(s, e);
    frag.append(span);
    pos = e;
  }
  if (pos < source.length) frag.append(document.createTextNode(source.slice(pos)));
  return frag;
}

export function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatNumber(value) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "—";
}

export function relativeTime(iso, now = Date.now()) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const min = Math.round((now - t) / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr} 小时前`;
  return `${Math.round(hr / 24)} 天前`;
}

let uid = 0;
export function nextId(prefix = "sc") {
  uid += 1;
  return `${prefix}-${uid}`;
}

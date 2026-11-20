// Minimal DOM stand-in for node-run UI tests (no jsdom): enough for dom.js h()/jaText()/icon() to build a tree and
// read its textContent. Not a browser; layout, events and selectors beyond `link[href="..."]` are not modelled.
class FakeNode {
  constructor(name) { this.nodeName = name; this.childNodes = []; this.parentNode = null; }
  append(...nodes) {
    for (const node of nodes) {
      if (node instanceof FakeFragment) { for (const child of [...node.childNodes]) this.append(child); continue; }
      const child = node instanceof FakeNode ? node : new FakeText(String(node));
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(value) { this.childNodes = [new FakeText(String(value))]; }
}
class FakeText extends FakeNode {
  constructor(text) { super("#text"); this.data = text; }
  get textContent() { return this.data; }
  set textContent(value) { this.data = String(value); }
}
class FakeFragment extends FakeNode { constructor() { super("#fragment"); } }
class FakeElement extends FakeNode {
  constructor(tag) { super(tag.toUpperCase()); this.tagName = this.nodeName; this.attributes = {}; this.dataset = {}; this.listeners = {}; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  get classList() {
    const get = () => (this.attributes.class || "").split(/\s+/).filter(Boolean);
    const set = (list) => { this.attributes.class = list.join(" "); };
    return {
      add: (...names) => set([...new Set([...get(), ...names])]),
      remove: (...names) => set(get().filter((n) => !names.includes(n))),
      contains: (name) => get().includes(name),
      toggle: (name, on = !get().includes(name)) => { set(on ? [...new Set([...get(), name])] : get().filter((n) => n !== name)); return on; },
    };
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  querySelectorAll(selector) {
    const match = /^link\[href="(.+)"\]$/.exec(selector);
    const out = [];
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child instanceof FakeElement) {
          if (match && child.tagName === "LINK" && child.attributes.href === match[1]) out.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const root = new FakeElement("html");
const head = new FakeElement("head");
const body = new FakeElement("body");
root.append(head, body);
globalThis.Node = FakeNode;
globalThis.document = {
  head, body, documentElement: root,
  createElement: (tag) => new FakeElement(tag),
  createElementNS: (_ns, tag) => new FakeElement(tag),
  createTextNode: (text) => new FakeText(text),
  createDocumentFragment: () => new FakeFragment(),
  querySelector: (selector) => root.querySelector(selector),
  querySelectorAll: (selector) => root.querySelectorAll(selector),
};

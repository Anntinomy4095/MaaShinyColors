import { h, icon } from "./dom.js";

const PAGE_SIZE = 6;

export function createChibiPicker(select) {
  let actor, page;
  const grid = h("div", { class: "chibi-avatar-grid" });
  const count = h("span", { class: "muted small num", "aria-live": "polite" });
  const arrow = (label, direction, delta) => h("button", {
    class: `icon-btn chibi-page-${direction}`, type: "button", "aria-label": label,
    onclick: () => { page += delta; render(); },
  }, icon("chevron"));
  const previous = arrow("上一页", "previous", -1);
  const next = arrow("下一页", "next", 1);
  const panel = h("div", { class: "task-menu chibi-settings chibi-picker", id: "chibi-picker", popover: "auto",
    role: "group", "aria-label": "切换偶像" },
  grid, h("div", { class: "chibi-pager" }, previous, count, next));

  function render() {
    const { characters, key } = actor;
    const pages = Math.ceil(characters.length / PAGE_SIZE);
    previous.disabled = page === 0;
    next.disabled = page === pages - 1;
    count.textContent = `${page + 1} / ${pages}`;
    const items = characters.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    grid.replaceChildren(...items.map(character => {
      const image = h("img", { alt: "", width: 52, height: 52, hidden: true });
      actor.portrait(character).then(url => {
        if (!url) return;
        image.src = url;
        image.hidden = false;
      }).catch(() => {});
      return h("button", { class: "chibi-avatar", type: "button", "aria-pressed": String(key === character.key),
        dataset: { character: character.key }, onclick: () => { panel.hidePopover(); return select(character.key); } },
      h("span", { class: "chibi-avatar-art" }, image), h("span", { text: character.name }));
    }));
  }

  return {
    panel,
    show(nextActor) {
      actor = nextActor;
      const position = actor.characters.findIndex(character => character.key === actor.key);
      page = Math.floor(Math.max(0, position) / PAGE_SIZE);
      render();
    },
  };
}

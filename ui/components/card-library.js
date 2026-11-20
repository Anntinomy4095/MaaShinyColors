import { h, formatTime } from "./dom.js";

const count = (value) => Number.isInteger(value) && value >= 0 ? value : 0;

/** Public card data is separate from the player's inventory and levels. */
export function cardLibraryModel(host) {
  const raw = host?.card_library || {};
  const last = raw.last && typeof raw.last === "object" ? raw.last : null;
  const state = ["idle", "running", "done", "partial", "failed", "stopped"].includes(raw.state) ? raw.state : "idle";
  return {
    supported: raw.supported === true, state, busy: state === "running", phase: raw.phase, auto: raw.auto === true,
    updated: count(raw.updated_cards), total: count(raw.total_cards), error: raw.error,
    cards: count(last?.local_cards), last,
  };
}

/** 2026-11-23 (user): 自动挖矿 needs the card details (skills, stats). ready = complete on this PC; minutes is a rough
 * estimate from the remaining count (one detail every ~1.75 s, the polite source rate). */
export function cardDetailsStatus(host) {
  const model = cardLibraryModel(host);
  const ready = model.last?.cards_complete === true && model.cards > 0;
  const left = model.busy && model.phase === "cards" && model.total ? Math.max(0, model.total - model.updated) : null;
  const minutes = left !== null ? Math.max(1, Math.ceil((left * 1.75) / 60)) : 45;
  return { ready, busy: model.busy, updated: model.updated, total: model.total, minutes, phase: model.phase };
}

export function cardLibraryResult(model) {
  if (model.busy) {
    // 2026-10-31: the host also refreshes the library by itself when it is read (UiCardLibrary.StartIfStale).
    const lead = model.auto ? "自动" : "正在";
    if (model.phase === "icons") return `${lead}补卡图`;
    if (model.phase === "cards") return model.total ? `${lead}下载卡资料：${model.updated} / ${model.total}` : `${lead}更新卡资料`;
    return `${lead}检查卡目录`;
  }
  if (model.state === "stopped") return "已停止，已下载的卡资料保留";
  if (model.error === "publish_failed") return "卡资料已保存，卡选择列表未更新，请重试";
  if (model.state === "failed") {
    if (model.error === "python_env_missing") return "找不到更新程序，请重新解压完整应用";
    if (model.error === "timeout") return "下载超时，可再次更新继续";
    return "更新未完成，请重试";
  }
  const last = model.last;
  if (!last || !["done", "partial"].includes(model.state)) return "";
  if (last.cards_complete !== true) return `已更新 ${count(last.updated_cards)} 张，剩余 ${count(last.remaining_cards)} 张；再次更新继续`;
  const result = last.status === "unchanged" ? "卡资料已是最新" : `新增 ${count(last.added_cards)} 张 · 更新 ${count(last.changed_cards)} 张`;
  if (count(last.icons_failed) || last.icons_stopped === true) {
    return `${result}；${last.icons_stopped ? "部分图片未下载" : `${count(last.icons_failed)} 张图片未下载`}，可再次补图`;
  }
  return result;
}

export function cardLibrarySummary(model) {
  if (model.busy) return cardLibraryResult(model);
  if (model.state === "failed") return "更新失败";
  if (model.state === "stopped") return "已停止";
  if (!model.last) return "尚未下载";
  return `本地 ${model.cards} 张${model.state === "partial" ? " · 更新未完整结束" : ""}`;
}

export function cardLibraryView(state, actions) {
  const model = cardLibraryModel(state.host);
  const checkedAt = model.last?.checked_at;
  return h("div", { class: "card-library" },
    checkedAt ? h("p", { class: "small muted", text: `最近检查：${formatTime(checkedAt)}` }) : null,
    h("div", { class: "dev-tool-row" },
      h("button", { type: "button", class: "btn btn-small", disabled: !model.supported || model.busy ? "" : null,
        title: !model.supported ? "需要更新程序"
          : "下载公开卡资料与图片（不含持有卡、Lv、凸）；首次较慢，可停止后继续。读取卡库时每 12 小时自动检查一次",
        dataset: { focusKey: "card-library-update" }, onclick: () => actions.cardLibraryUpdate?.() },
      model.busy ? "正在更新" : model.last ? "更新卡资料" : "下载卡资料"),
      model.busy ? h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: "card-library-stop" },
        onclick: () => actions.cardLibraryStop?.() }, "停止") : null),
    cardLibraryResult(model) ? h("p", { class: "small", role: "status", text: cardLibraryResult(model) }) : null);
}

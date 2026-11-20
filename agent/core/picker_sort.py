"""Sort planning for the game's card pickers (pure, no Maa, no input).

User: speed up card picking with the game's own 並び替え. The wanted S cards' main attribute decides the
sort key: mostly Vo cards -> 「Vocal順」 ▼降順; when a card of another attribute remains (e.g. Vi) -> 「Visual順」 ▼降順,
so the target sits on the first page and the scan scrolls little. The same helper serves the W.I.N.G. team-edit
picker (サポートアイドル編成, ``wing_team_edit`` / ``wing_nav``) and the S-card training list (``training_custom``).

Evidence (recording ``captures-20261011-164549Z``, user-driven, native 1280x720):

- header of サポートアイドル編成: 「情報切替」 + label 「デフォルト(W.I.N.G.用)」, 「絞り込み」, sort key button (e.g.
  「デフォルト」 / 「Vocal順」) and 「▼降順」 -- the same 絞り込み / sort / direction boxes as the training list
  (``training_select.FILTER_BOX`` / ``SORT_BOX`` / ``DIRECTION_BOX``);
- the sort key button opens 「並び替え」 (#112 (1045,141) -> #113; also #118->#120, #123->#125, #141->#143): 3x3 keys
  デフォルト / Lv順 / 特訓回数順, Vocal順 / Dance順 / Visual順, メンタル順 / 加入順 / リリース順, and 「決定」 only (no
  close / cancel). The selected key is pink (``training_select.cell_selected``); a key tap only moves the pink mark
  (#120 r2c1 -> (637,355) -> #121 r2c2; #143 r3c2 -> (827,435) -> #145 r3c3); 「決定」 applies it and closes the
  popup (#122 (652,532) -> #123 Dance順; #145 (635,549) -> #147; #156 (654,556) -> #158; #161 (621,556) -> #162).
- under a stat key the tiles show that stat instead of Lv (#118: 342, 325, 313 ...), sorted high to low under ▼降順.
- the sort PERSISTS: the S2/S3/S4/guest pickers of the same recording reopened in Vocal順 (#170/#177/#204/#218), and
  the training list opened in Vocal順 the next day (train-list-20261012, same first row 313 as #162): the support
  picker and the training list share one saved sort. The P picker (プロデュースアイドル編成) keeps its own (デフォルト).
- 「デフォルト(W.I.N.G.用)」 stayed unchanged across all nine sort changes (#113–#162): it is not the sort setting and
  a sort change does not edit it. Maa never taps 「情報切替」, that label, 「絞り込み」 or the direction button.

Filters (絞り込み) are not planned: no filter popup of these lists is sampled in the recording, and an active filter
persists like the sort and makes the training list stop (``list_filter_active``).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Mapping

from agent.core import training_select as ts

STATS = ("Vo", "Da", "Vi", "Me")
ATTR_KEYS = {"Vo": "Vocal順", "Da": "Dance順", "Vi": "Visual順", "Me": "メンタル順"}
KEY_ATTR = {v: k for k, v in ATTR_KEYS.items()}
# 並び替え popup: key label -> reserved 3x3 cell (row, col) (#114 / #120 popup text, training sample-live-20261011/15-17).
KEY_CELLS = {"デフォルト": (1, 1), "Lv順": (1, 2), "特訓回数順": (1, 3), "Vocal順": (2, 1), "Dance順": (2, 2),
             "Visual順": (2, 3), "メンタル順": (3, 1), "加入順": (3, 2), "リリース順": (3, 3)}
LABEL_OF_CODE = {code: label for label, code in ts.SORT_KEYS.items()}
PAGE_TILES = 21                  # 3 fully visible rows x 7 columns at the list top
MAX_CHANGES_PER_VISIT = 1        # one key change per picker visit
MAX_CHANGES_PER_RUN = 5          # S1..S4 + guest at most
MIN_OWNED_FOR_ESTIMATE = 40      # training: owned S levels needed before a stat-rank estimate is trusted


def cell_rect(row: int, col: int) -> tuple[int, int, int, int]:
    """Tap rect of a 並び替え key cell: the reserved cell of the training list popup (= the button, #114) inset by
    20 / 12 px so a tap never lands on the button edge."""
    x, y, w, h = ts.SORT_KEY_ACTIONS[f"training-sort-key-r{row}c{col}"]
    return (x + 20, y + 12, w - 40, h - 24)


SORT_OPEN_RECT = ts.SORT_OPEN_RECT
SORT_APPLY_RECT = (578, 515, 124, 56)       # inside the 決定 button (x 558-722, y 505-581 on #114)


def main_attribute(card: Mapping[str, Any] | None) -> str | None:
    """The S card's main stat (Vo/Da/Vi/Me) from its highest stored level row; a tie or no row -> None."""
    rows = {}
    for key, row in ((card or {}).get("levels") or {}).items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(row, Mapping) and all(isinstance(row.get(s), (int, float)) for s in STATS):
            rows[level] = row
    if not rows:
        return None
    top = rows[max(rows)]
    best = max(top[s] for s in STATS)
    winners = [s for s in STATS if top[s] == best]
    return winners[0] if len(winners) == 1 else None


def card_attributes(card_ids: Iterable[str], library: Mapping[str, Any] | None = None) -> dict[str, str | None]:
    """{card id: main attribute or None}. ``library`` = ``team_recommend.load_library()["cards"]`` (loaded lazily;
    a missing local database gives None for every card, i.e. no sort plan)."""
    ids = [str(c) for c in card_ids if c]
    if library is None:
        try:
            from agent.core import team_recommend
            library = team_recommend.load_library().get("cards") or {}
        except Exception:      # noqa: BLE001 - optional local data; no data means no plan, never a guess
            library = {}
    return {c: main_attribute(library.get(c)) for c in ids}


@dataclasses.dataclass(frozen=True)
class Header:
    key: str | None          # popup label, e.g. "Vocal順" (None: unread / unknown)
    direction: str | None    # desc | asc
    filter_off: bool | None
    reasons: tuple = ()

    @property
    def ok(self) -> bool:
        return not self.reasons

    def to_dict(self):
        return {"key": self.key, "direction": self.direction, "filter_off": self.filter_off,
                "reasons": list(self.reasons)}


def header_from(value) -> Header | None:
    """Header from a reading (a Header or its ``to_dict``); None when absent."""
    if isinstance(value, Header):
        return value
    if isinstance(value, Mapping):
        return Header(value.get("key"), value.get("direction"), value.get("filter_off"),
                      tuple(value.get("reasons") or ()))
    return None


def _join(texts) -> str | None:
    if texts is None:
        return None
    parts = [t if isinstance(t, str) else (t[0] if isinstance(t, (list, tuple)) and t else "") for t in texts]
    joined = "".join(p for p in parts if p)
    return joined or None


def parse_header(sort_texts, direction_texts, filter_texts) -> Header:
    """Header OCR (lists of text or (text, box)) -> Header; uses training_select.parse_order's vocabulary."""
    order, reasons = ts.parse_order(_join(sort_texts), _join(direction_texts), _join(filter_texts))
    if order is None:
        flt = ts._clean(_join(filter_texts))
        return Header(None, None, None if flt is None else flt == ts.FILTER_OFF, tuple(reasons))
    return Header(LABEL_OF_CODE[order.key], order.direction, True)


@dataclasses.dataclass(frozen=True)
class SortPlan:
    key: str | None          # label to have (None: keep whatever is there)
    change: bool             # a key change is needed
    reason: str
    counts: tuple = ()       # ((attribute, n), ...) of the remaining targets

    def to_dict(self):
        return {"key": self.key, "change": self.change, "reason": self.reason, "counts": [list(c) for c in self.counts]}


def _counts(cards, attributes) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cards:
        a = attributes.get(c)
        if a in ATTR_KEYS:
            out[a] = out.get(a, 0) + 1
    return out


def plan_sort(header: Header | None, focus: str | None, remaining: Iterable[str], attributes: Mapping[str, Any],
              ranks: Mapping[str, Mapping[str, int]] | None = None) -> SortPlan:
    """Which sort key puts the most remaining targets on page 1 (▼降順 only).

    ``focus`` = the card picked on this visit (None for a survey of all ``remaining``). Without ``ranks``: a key is
    scored by (focus has its attribute, number of remaining targets with its attribute); the current key is kept on a
    tie (no change saves taps). With ``ranks`` ({key label: {card: estimated 0-based position under ▼降順}}): the
    score counts the targets estimated on page 1 instead. A header that is unread, filtered or ▲昇順 -> no plan (the
    direction button is never tapped); a focus whose attribute is unknown -> keep the current key."""
    remaining = [str(c) for c in remaining if c]
    counts = _counts(remaining, attributes)
    summary = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], STATS.index(kv[0]))))
    if header is None or not header.ok or header.key is None:
        return SortPlan(None, False, "header_unread", summary)
    if header.direction != "desc":
        return SortPlan(None, False, "direction_not_desc", summary)
    if focus is not None and attributes.get(focus) not in ATTR_KEYS and not (ranks and any(focus in r for r in ranks.values())):
        return SortPlan(header.key, False, "focus_attribute_unknown", summary)
    if not counts and not ranks:
        return SortPlan(header.key, False, "attributes_unknown", summary)

    def score(key: str):
        if ranks and key in ranks:
            r = ranks[key]
            on_page = [c for c in remaining if isinstance(r.get(c), int) and r[c] < PAGE_TILES]
            return (focus in on_page if focus else 0, len(on_page))
        attr = KEY_ATTR.get(key)
        return (1 if focus and attributes.get(focus) == attr else 0, counts.get(attr, 0))

    candidates = list(ATTR_KEYS.values())
    best = max(score(k) for k in candidates)
    if header.key in candidates and score(header.key) == best:
        return SortPlan(header.key, False, "current_key_best", summary)
    if best == (0, 0):
        return SortPlan(header.key, False, "no_key_helps", summary)
    # first best key in the order of the targets' own attributes (focus first), then Vo/Da/Vi/Me
    order = ([ATTR_KEYS[attributes[focus]]] if focus and attributes.get(focus) in ATTR_KEYS else []) + \
        [ATTR_KEYS[a] for a, _ in summary] + candidates
    key = next(k for k in order if score(k) == best)
    return SortPlan(key, True, "attribute_sort", summary)


def fixed_plan(header: Header | None, key: str) -> SortPlan:
    """A set key instead of the attribute plan (Auto 自动编队: 「Lv順」, its ノウハウ発現マスタリー cards are the user's
    highest levels). Same header rules as ``plan_sort``: unread, filtered or ▲昇順 -> no plan."""
    if key not in KEY_CELLS:
        raise ValueError(f"unknown sort key {key!r}")
    if header is None or not header.ok or header.key is None:
        return SortPlan(None, False, "header_unread")
    if header.direction != "desc":
        return SortPlan(None, False, "direction_not_desc")
    if header.key == key:
        return SortPlan(key, False, "current_key_fixed")
    return SortPlan(key, True, "fixed_key")


# ---- 並び替え popup ------------------------------------------------------------------------------------------------
def parse_popup(items) -> ts.SortPopup:
    """OCR items ({"text", "box"} or (text, box)) of the popup ROI -> training_select.SortPopup."""
    norm = []
    for item in items or ():
        if isinstance(item, Mapping):
            norm.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            norm.append({"text": item[0], "box": item[1]})
    return ts.parse_sort_popup(norm)


def popup_cell(popup: ts.SortPopup, key: str) -> tuple[int, int] | None:
    """The reserved cell bound to ``key`` by the popup's own OCR (exactly one text, inside exactly its expected cell);
    None when the popup is not the known 並び替え layout."""
    action = ts.sort_key_action(popup, key)
    if action is None:
        return None
    cell = (int(action[-3]), int(action[-1]))
    return cell if KEY_CELLS.get(key) == cell else None


def popup_known(popup: ts.SortPopup) -> bool:
    """The known 並び替え layout: title, 決定 in its rect and every one of the nine keys in its own cell."""
    return (popup.title and popup.apply is not None and ts._within(popup.apply, ts.SORT_APPLY_RECT)
            and all(popup_cell(popup, k) == cell for k, cell in KEY_CELLS.items()))


def selected_share(image_rgb, cell: tuple[int, int]) -> float:
    return ts.cell_selected(image_rgb, f"training-sort-key-r{cell[0]}c{cell[1]}")


SELECTED_MIN = ts.SORT_SELECTED_PINK_MIN


# ---- training list (S カード訓練) ------------------------------------------------------------------------------------
def training_key(card_id: str | None, levels: Mapping[str, int] | None,
                 library: Mapping[str, Any] | None) -> tuple[str, str]:
    """(sort key label, reason) for the training list. Default 「Lv順」 (the existing plan: the configured card is
    usually low level, ``training_select.wanted_direction``). The card's attribute key (▼降順) is chosen only when the
    owned levels and the local stats estimate its position there strictly better than under Lv順 and on page 1.
    The estimate covers only the cards with a known owned level (the real list may hold more): a miss only costs
    scrolling, the identity check of every tile is unchanged."""
    from agent.core import card_stats as cs

    levels = {k: v for k, v in (levels or {}).items() if type(v) is int}
    library = library or {}
    attr = main_attribute(library.get(card_id)) if card_id else None
    level = levels.get(card_id) if card_id else None
    if attr is None or level is None:
        return "Lv順", "attribute_or_level_unknown"

    def value(cid: str):
        card = library.get(cid) or {}
        st = cs.stats_at_level(card.get("levels"), levels[cid], rarity=card.get("rarity"),
                               max_levels=card.get("max_levels"))
        return None if st.stats is None else st.stats[attr]

    mine = value(card_id)
    if mine is None:
        return "Lv順", "target_stat_unknown"
    others = [cid for cid in levels if cid != card_id and str(cid).startswith("2")]
    if len(others) < MIN_OWNED_FOR_ESTIMATE:
        return "Lv順", f"owned_levels_sparse:{len(others)}"
    vals = [value(c) for c in others]
    if any(v is None for v in vals):
        return "Lv順", "owned_stat_unknown"
    attr_rank = sum(1 for v in vals if v > mine)
    below = sum(1 for c in others if levels[c] < level)
    above = sum(1 for c in others if levels[c] > level)
    lv_rank = min(below, above)
    if attr_rank < lv_rank and attr_rank < PAGE_TILES:
        return ATTR_KEYS[attr], f"{attr}_rank{attr_rank}<lv_rank{lv_rank}"
    return "Lv順", f"lv_rank{lv_rank}<={attr}_rank{attr_rank}"

"""Daily shop task (migration batch B4): pure rules, spend log and per-run state. No Maa import, no input.

The Pipeline (assets/resource/base/pipeline/shop.json) owns navigation; the Agent custom recognitions in
``agent.core.shop_custom`` read the screen and call the functions here. Design:

- Only the leading 「本日限定」 cards of the マニー page (ALL) are in scope; ranks are the ORIGINAL positions
  (1..3) of the day. A bought card disappears and later cards shift left, so the screen position of rank r is
  r minus the ranks bought before it.
- No ledger lock: today's purchases are inferred from the visible cards; the JSONL spend log
  (``dev_assets/private/run_logs/shop-<game day>.jsonl``) is evidence used only to map original ranks to
  current positions. If the visible cards cannot be explained by the log (manual purchase, unknown change)
  the task buys nothing and ends ``skipped_inconsistent`` (a success: the queue simply continues).
- Per-ITEM selection (MaaEnd CreditShopping style, user decisions 2026-10-04/05): every selected catalog item
  (default スキップチケット, ゴールドトレーニングチケット, シルバートレーニングチケット) is bought wherever it appears
  among the day's daily offers, only at or below its FIXED catalog price. The card name OCR must equal a selected
  name exactly, otherwise that offer is skipped (never substituted); the 交換確認 is checked against the same rule.
- A rank whose spend result was never observed is ``uncertain``: the run that sent it stops. A later run (user
  2026-10-28: 结果不确定只提醒) decides it from the screen: gone from the list = bought; still offered at 0/1 = not
  bought, so it may be bought again through the usual 交換確認 checks.
- Every spend needs a verified 交換確認 reading and a one-shot grant bound to that frame; the result must
  be read back from the 交換結果 dialog.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import itertools
import json
import os
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "dev_assets" / "private"
LOG_ROOT = PRIVATE / "run_logs"
RANKS = (1, 2, 3)
CARD_SPACING = 270
CURRENCY_PROMPT = "マニーを消費して"
RESULT_SUFFIX = "と交換しました"
SPEND_ACTION = "shop-exchange-confirm"
APPROVER = "SC.Shop.Check.Confirm"
PURCHASE_SCOPE = "daily_discount_only"
CURRENCY = "money"
MAX_WAIT_FRAMES = 6
MAX_UNREADABLE_FRAMES = 3

# Outcomes. Success outcomes end the task as succeeded; every other outcome fails it (the queue continues).
SUCCESS_OUTCOMES = frozenset({"bought", "nothing_to_do", "skipped_rules", "skipped_inconsistent"})
FAILURE_OUTCOMES = frozenset({"confirm_mismatch", "result_mismatch", "spend_uncertain", "spend_refused",
                              "card_not_purchasable", "list_unreadable", "spend_not_enabled",
                              "unexpected_confirm", "unexpected_result_dialog", "config_invalid", "timeout",
                              "log_write_failed"})


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def card_roi(roi, position):
    x, y, w, h = roi
    return [x + CARD_SPACING * (position - 1), y, w, h]


# Fixed geometry on the canonical 1280x720 canvas (native profile shop-native-v1, 2026-10-02).
# 2026-10-24: two-line names (「ヒーリングフルーツタルト / (2個入り)」, first line from y 270) need the box between the
# 「本日限定」 banner (to y ~255) and the item frame (from y ~333); was [41, 274, 250, 50] (one-line names only).
LIST_NAME_ROI = [38, 262, 256, 66]
CONFIRM_NAME_ROI = [515, 228, 300, 62]
CURRENCY_PROMPT_ROI = [410, 132, 460, 56]
RESULT_SENTENCE_ROI = [400, 258, 480, 40]
_COUNT = r"[0-9]{1,3}(?:,[0-9]{3})*"
# name -> (crop, preprocessing kind, full-match pattern). Card fields are for position 1 and shift by 270 px.
DIGIT_FIELDS = {
    "list.money": ([1000, 28, 170, 30], "light", "^" + _COUNT + "$"),
    "card.limit": ([225, 516, 52, 28], "dark", r"^[0-9]{1,2}/[0-9]{1,2}$"),
    "card.price": ([130, 590, 100, 34], "blue_inv", "^" + _COUNT + "$"),
    "confirm.quantity": ([690, 346, 70, 30], "dark", r"^[0-9]{1,2}$"),
    "confirm.limit_after": ([686, 388, 32, 30], "dark", r"^[0-9]{1,2}$"),
    "confirm.limit_max": ([731, 388, 32, 30], "dark", r"^[0-9]{1,2}$"),
    "confirm.required": ([660, 428, 160, 30], "dark", "^" + _COUNT + "$"),
    "confirm.money_before": ([583, 466, 130, 32], "dark", "^" + _COUNT + "$"),
    "confirm.money_after": ([745, 466, 135, 32], "dark", "^" + _COUNT + "$"),
    "confirm.item_before": ([585, 504, 115, 32], "dark", "^" + _COUNT + "$"),
    "confirm.item_after": ([745, 504, 135, 32], "dark", "^" + _COUNT + r"\(\+1\)$"),
    # The result dialog prints numbers WITHOUT thousands separators (e.g. 17678482).
    "result.money": ([600, 342, 240, 26], "dark", r"^[0-9]{1,10}$"),
    "result.items": ([600, 380, 240, 26], "dark", r"^(?:[0-9]{1,7}|" + _COUNT + ")$"),
}


def digit_field(name, position=None):
    roi, kind, pattern = DIGIT_FIELDS[name]
    if name.startswith("card."):
        _require(position in RANKS, "卡片位置无效")
        roi = card_roi(roi, position)
    return list(roi), kind, pattern


def normalize_name(text):
    """NFKC, then drop all whitespace/line breaks (card names may wrap to two lines)."""
    _require(isinstance(text, str), "名称必须为字符串")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def to_int(text):
    """'17,678,492' / '17678482' / '610(+1)' -> int; None when absent or malformed."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\(\+1\))?", text)
    return int(match.group(1).replace(",", "")) if match else None


def game_day(now):
    """JST game day with the 05:00 boundary (same as the host's GameDayOf)."""
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=4)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- configuration
# THE fixed catalog of the マニー page's 「本日限定」 offers -- the single place to update when the rotation changes.
# Source: wikiwiki「ショップ」「本日限定」割引商品 (daily rotation by day of month, switches at 05:00 JST; the page
# keeps 「以前の日替わりラインナップ」), cross-checked with our native samples of game days 2026-10-01..03
#.
# Entry key = (exact Japanese name after normalize_name, price in マニー): ゴールドトレーニングチケット is offer 2 at 10
# every day AND offer 3 at 283 on days 9/14/25 -- two different entries. An offer is bought only when its (name,
# price) is a selected entry; the 交換確認 必要マニー must equal the same price. Anything else is never bought.
# Columns: id, Japanese name, short Chinese label, price, discount badge, kind (main | ticket | item | towel),
# days of month for slot 3 (() = every day).
SHOP_CATALOG = (
    ("skip_ticket", "スキップチケット", "跳过券", 10, "", "main", ()),
    ("gold_training_ticket", "ゴールドトレーニングチケット", "金训练券", 10, "98%OFF", "main", ()),
    ("gold_training_ticket_283", "ゴールドトレーニングチケット", "金训练券 283", 283, "71%OFF", "ticket", (9, 14, 25)),
    ("silver_training_ticket_283", "シルバートレーニングチケット", "银训练券", 283, "43%OFF", "ticket", (2, 12, 21)),
    ("message_card_283", "始まりのメッセージカード", "开端留言卡", 283, "97%OFF", "item", (3,)),
    ("morning_bread_283", "モーニングコミュパン", "早安交流面包", 283, "98%OFF", "item", (15,)),
    ("promise_toilette_283", "約束のトワレ", "约定的淡香水", 283, "97%OFF", "item", (29,)),
    ("secret_memo_50", "秘密のメモ帳", "秘密笔记本", 50, "50%OFF", "item", (31,)),
    ("petit_tension_aroma_500", "プチテンションアロマ(2個入り)", "小干劲香薰×2", 500, "50%OFF", "item", (6,)),
    ("trend_magazine_500", "流行雑誌", "流行杂志", 500, "50%OFF", "item", (8,)),
    ("vocal_book_500", "ボーカル基礎知識本", "Vo 基础知识书", 500, "50%OFF", "item", (26,)),
    ("dance_book_500", "ダンス基礎知識本", "Da 基础知识书", 500, "50%OFF", "item", (27,)),
    ("visual_book_500", "ビジュアル基礎知識本", "Vi 基础知识书", 500, "50%OFF", "item", (28,)),
    ("trouble_charm_1500", "トラブル守り", "麻烦护身符", 1500, "50%OFF", "item", (5,)),
    ("promise_cologne_1500", "約束のコロン", "约定的古龙水", 1500, "50%OFF", "item", (11,)),
    ("tension_aroma_1500", "テンションアロマ(2個入り)", "干劲香薰×2", 1500, "50%OFF", "item", (18,)),
    ("healing_tart_1500", "ヒーリングタルト(2個入り)", "治愈挞×2", 1500, "50%OFF", "item", (20,)),
    ("healing_fruit_tart_5000", "ヒーリングフルーツタルト(2個入り)", "治愈水果挞×2", 5000, "50%OFF", "item", (17, 24, 30)),
    ("schedule_sticky_5000", "予定変更付箋", "日程变更便签", 5000, "50%OFF", "item", (23,)),
    ("towel_illumination", "イルミネーションスターズのタオル", "イルミネ毛巾", 5000, "50%OFF", "towel", (1,)),
    ("towel_antica", "アンティーカのタオル", "アンティーカ毛巾", 5000, "50%OFF", "towel", (4,)),
    ("towel_hokago", "放課後クライマックスガールズのタオル", "放クラ毛巾", 5000, "50%OFF", "towel", (7,)),
    ("towel_alstroemeria", "アルストロメリアのタオル", "アルスト毛巾", 5000, "50%OFF", "towel", (10,)),
    ("towel_straylight", "ストレイライトのタオル", "ストレイ毛巾", 5000, "50%OFF", "towel", (13,)),
    ("towel_noctchill", "ノクチルのタオル", "ノクチル毛巾", 5000, "50%OFF", "towel", (16,)),
    ("towel_shhis", "シーズのタオル", "シーズ毛巾", 5000, "50%OFF", "towel", (19,)),
    ("towel_cometik", "コメティックのタオル", "コメティック毛巾", 5000, "50%OFF", "towel", (22,)),
)
SHOP_ITEMS = {row[0]: row[1:] for row in SHOP_CATALOG}   # id -> (ja, zh, price, discount, kind, days)
MAIN_ITEMS = tuple(i for i, row in SHOP_ITEMS.items() if row[4] == "main")
OTHER_ITEMS = tuple(i for i in SHOP_ITEMS if i not in MAIN_ITEMS)
# By default buy only the two everyday offers (スキップチケット 10 and the 98%OFF
# ゴールドトレーニングチケット 10); every third-offer catalog entry (incl. the 283 gold/silver tickets) is opt-in.
DEFAULT_SELECTED = frozenset({"skip_ticket", "gold_training_ticket"})


def item_name(item_id):
    _require(item_id in SHOP_ITEMS, f"未知商店商品: {item_id}")
    return normalize_name(SHOP_ITEMS[item_id][0])


def catalog_price(item_id):
    return SHOP_ITEMS[item_id][2]


def option_key(item_id):
    """Attach field of the item's switch (``buy_<id>``)."""
    _require(item_id in SHOP_ITEMS, f"未知商店商品: {item_id}")
    return f"buy_{item_id}"


def config_node(item_id):
    camel = "".join(part.capitalize() for part in item_id.split("_"))
    return f"SC.Shop.Config.{camel}" if item_id in MAIN_ITEMS else f"SC.Shop.Config.Other.{camel}"


def config_nodes():
    """Every SC.Shop.Config.* carrier node, in the order SC.Shop.Begin merges their ``attach``."""
    return tuple([config_node(i) for i in SHOP_ITEMS] + ["SC.Shop.Config.Budget"])


def _param_keys():
    return {option_key(i) for i in SHOP_ITEMS} | {"coin_budget", "coin_reserve", "purchase_scope", "currency"}


def _entry(name, price):
    return (normalize_name(name), price)


@dataclasses.dataclass(frozen=True)
class ShopConfig:
    """Per-ITEM selection (user decisions 2026-10-04/05). ``entries`` holds the selected catalog keys
    (normalized Japanese name, fixed price). A daily offer is bought wherever it appears only when its (name, price)
    is selected; anything else is skipped, never substituted. Ranks only serve navigation and log reconciliation.
    No spend budget (移除所有预算); the visible 购买后保留マニー (``coin_reserve``) stays."""
    entries: frozenset = frozenset((item_name(i), catalog_price(i)) for i in DEFAULT_SELECTED)
    coin_reserve: int = 100000
    selected: frozenset = DEFAULT_SELECTED

    @classmethod
    def from_params(cls, params):
        """Merge the option nodes' ``attach`` dicts. Scope and currency are fixed; anything else is refused."""
        _require(isinstance(params, dict), "商店配置必须为对象")
        unknown = set(params) - _param_keys()
        _require(not unknown, "商店配置含未知字段: " + ",".join(sorted(unknown)))
        _require(params.get("purchase_scope", PURCHASE_SCOPE) == PURCHASE_SCOPE, "商店范围只能是 daily_discount_only")
        _require(params.get("currency", CURRENCY) == CURRENCY, "商店货币只能是マニー（money）")
        selected = set()
        for item in SHOP_ITEMS:
            value = params.get(option_key(item), item in DEFAULT_SELECTED)
            _require(type(value) is bool, f"{option_key(item)} 必须为布尔值")
            if value:
                selected.add(item)
        # ``coin_budget`` (removed 2026-10-28) is still accepted from older overrides and ignored.
        reserve = params.get("coin_reserve", 100000)
        _require(type(reserve) is int and reserve >= 0, "coin_reserve 必须为非负整数")
        entries = frozenset((item_name(i), catalog_price(i)) for i in selected)
        return cls(entries, reserve, frozenset(selected))

    def wants_name(self, name):
        return isinstance(name, str) and any(n == normalize_name(name) for n, _ in self.entries)

    def allows(self, name, price):
        """True when (name, price) is a selected catalog entry (exact price, never a cheaper/dearer variant)."""
        return isinstance(name, str) and type(price) is int and _entry(name, price) in self.entries

    def to_dict(self):
        return {"entries": sorted([n, p] for n, p in self.entries), "selected": sorted(self.selected),
                "coin_reserve": self.coin_reserve,
                "purchase_scope": PURCHASE_SCOPE, "currency": CURRENCY}


# ---------------------------------------------------------------- spend log (evidence, not a lock)
class SpendLog:
    """Append-only JSONL per JST game day; read back only to map original ranks to screen positions."""

    def __init__(self, root=LOG_ROOT, now=lambda: _dt.datetime.now(_dt.timezone.utc)):
        root = Path(root).resolve()
        _require(root.is_relative_to(PRIVATE.resolve()), "商店消费日志必须位于 dev_assets/private")
        self.root, self.now = root, now

    def path(self, day=None):
        return self.root / f"shop-{day or game_day(self.now())}.jsonl"

    def append(self, record):
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"time": self.now().isoformat(), "game_day": game_day(self.now()), **record},
                          ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def events(self):
        path = self.path()
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


@dataclasses.dataclass(frozen=True)
class LogState:
    snapshot: tuple = None        # ((name, price), ...) of ranks 1..3, from the day's first full observation
    verified: frozenset = frozenset()
    refused: frozenset = frozenset()
    uncertain: frozenset = frozenset()
    spent: int = 0                # verified + uncertain prices (report only; no budget since 2026-10-28)


def log_state(events):
    snapshot, intents, verified, refused = None, {}, set(), set()
    for event in events:
        kind = event.get("event")
        if kind == "snapshot" and snapshot is None:
            snapshot = tuple((c["name"], c["price"]) for c in event["cards"])
        elif kind == "intent":
            intents[event["rank"]] = event
            refused.discard(event["rank"])  # a previous attempt was not sent; this new attempt needs its own result
        elif kind == "verified":
            verified.add(event["rank"])
        elif kind == "refused":
            refused.add(event["rank"])
    uncertain = {r for r in intents if r not in verified and r not in refused}
    spent = sum(intents[r]["price"] for r in verified | uncertain if r in intents)
    return LogState(snapshot, frozenset(verified), frozenset(refused), frozenset(uncertain), spent)


# ---------------------------------------------------------------- list reading -> decision
@dataclasses.dataclass(frozen=True)
class Card:
    position: int
    name: str = None
    price: int = None
    limit: str = None
    enabled: bool = False


@dataclasses.dataclass(frozen=True)
class ListView:
    """Leading daily cards in screen order. ``layout`` is False when a position is neither a daily card nor a
    monthly card (covered, animating, unknown); ``money`` is the top-bar balance."""
    layout: bool
    cards: tuple = ()
    money: int = None

    def key(self):
        return tuple((c.name, c.price) for c in self.cards)


def same_cards(expected, visible):
    """Equal (name, price) sequences; an unread price (None, an unselected offer) matches any price of that name."""
    return len(expected) == len(visible) and all(
        en == vn and (ep is None or vp is None or ep == vp) for (en, ep), (vn, vp) in zip(expected, visible))


def reconcile(view, state):
    """Explain the visible daily cards with the log. Returns (mapping {rank: position}, bought ranks, reason).

    ``mapping`` is None when the cards cannot be explained (inconsistent). Uncertain ranks are tried both ways;
    exactly one hypothesis must match the visible (name, price) sequence.
    """
    visible = view.key()
    if state.snapshot is None:
        if len(visible) == 3:
            return {r: r for r in RANKS}, frozenset(), "first_observation"
        return None, frozenset(), "daily_cards_missing_without_log"
    matches = []
    uncertain = sorted(state.uncertain)
    for size in range(len(uncertain) + 1):
        for extra in itertools.combinations(uncertain, size):
            bought = set(state.verified) | set(extra)
            remaining = [r for r in RANKS if r not in bought]
            if same_cards(tuple(state.snapshot[r - 1] for r in remaining), visible):
                matches.append((frozenset(bought), {r: i + 1 for i, r in enumerate(remaining)}))
    if len(matches) != 1:
        return None, frozenset(), "visible_cards_do_not_match_log" if not matches else "ambiguous_log_hypotheses"
    bought, mapping = matches[0]
    return mapping, bought, "matches_log"


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                 # open | done | inconsistent | fail | wait
    position: int = None
    rank: int = None
    outcome: str = None       # for done/inconsistent/fail
    notes: tuple = ()
    plan: dict = None

    def to_dict(self):
        return {k: v for k, v in dataclasses.asdict(self).items() if v not in (None, ())}


def decide(config, view, state, run_bought=frozenset(), can_spend=True):
    """Next step on the マニー list. Pure; see module docstring for the rules."""
    # Every card needs its name (what it is); only an offer Maa may buy needs its price and limit. 2026-10-27 daily:
    # the unselected third offer's price (「ダンス基礎知識本 500」) was unread live and the whole shop stopped.
    if not view.layout or view.money is None or any(c.name is None for c in view.cards):
        return Decision("wait", outcome="list_unreadable", notes=("layout_or_ocr_unreadable",))
    mapping, bought, reason = reconcile(view, state)
    if mapping is None:
        return Decision("inconsistent", outcome="skipped_inconsistent", notes=(reason,))
    bought = set(bought) | set(run_bought)
    notes = []
    for rank in RANKS:
        if rank in bought:
            notes.append(f"rank{rank}:already_bought")
            continue
        if rank in state.uncertain:
            notes.append(f"rank{rank}:uncertain_still_offered")   # reconcile: still on the list, so not bought
        if rank in state.refused:
            notes.append(f"rank{rank}:refused_earlier_today")
        card = view.cards[mapping[rank] - 1]
        if not config.wants_name(card.name):
            # Decided by item identity: not a selected catalog item -> never bought, never substituted. Checked before
            # the button/limit so an unwanted (or unknown) card cannot fail the run.
            notes.append(f"rank{rank}:not_selected")
            continue
        if card.enabled and (card.price is None or card.limit is None):
            return Decision("wait", outcome="list_unreadable", notes=tuple(notes + [f"rank{rank}:price_or_limit_unread"]))
        if card.price is not None and not config.allows(card.name, card.price):
            # A selected name at a price that is not one of its selected catalog entries (e.g. ゴールド 283 while only
            # the 10 offer is selected, or a changed price): skipped, not an error. A grey card's unreadable price
            # cannot be matched, so it still falls through to card_not_purchasable below.
            notes.append(f"rank{rank}:price_{card.price}_not_selected")
            continue
        if not card.enabled:
            return Decision("fail", outcome="card_not_purchasable", notes=tuple(notes + [f"rank{rank}:button_disabled"]))
        if card.limit != "0/1":
            return Decision("fail", outcome="card_not_purchasable", notes=tuple(notes + [f"rank{rank}:limit_{card.limit}"]))
        if view.money - card.price < config.coin_reserve:
            notes.append(f"rank{rank}:coin_reserve")
            continue
        if not can_spend:
            return Decision("fail", outcome="spend_not_enabled", notes=tuple(notes + [f"rank{rank}:spend_not_enabled"]))
        plan = {"rank": rank, "position": mapping[rank], "name": card.name, "price": card.price,
                "money": view.money, "visible": [list(k) for k in view.key()]}
        return Decision("open", position=mapping[rank], rank=rank, notes=tuple(notes), plan=plan)
    seen = {_entry(n, p) for n, p in (state.snapshot or view.key()) if isinstance(n, str)}
    notes += [f"selected_absent:{i}" for i in MAIN_ITEMS                       # slot-3 entries rotate daily
              if i in config.selected and (item_name(i), catalog_price(i)) not in seen]
    skipped = any(":price_" in n or n.endswith("coin_reserve") for n in notes)
    return Decision("done", outcome="skipped_rules" if skipped else "nothing_to_do", notes=tuple(notes))


# ---------------------------------------------------------------- dialogs
def check_confirm(config, plan, fields):
    """Reasons the 交換確認 dialog must NOT be confirmed (empty list = approve). ``fields`` holds OCR texts."""
    reasons = []
    if plan is None:
        return ["no_open_plan"]
    name, prompt = fields.get("name"), fields.get("prompt")
    if name != plan["name"]:
        reasons.append("name_mismatch")
    if not config.allows(name, plan["price"]):
        reasons.append("item_not_allowed")
    if not (isinstance(prompt, str) and prompt.startswith(CURRENCY_PROMPT)):
        reasons.append("currency_prompt_not_money")
    numbers = {k: to_int(fields.get(k)) for k in ("quantity", "limit_after", "limit_max", "required", "money_before",
                                                  "money_after", "item_before", "item_after")}
    if numbers["quantity"] != 1:
        reasons.append("quantity_not_1")
    if (numbers["limit_after"], numbers["limit_max"]) != (1, 1):
        reasons.append("limit_preview_not_1_of_1")
    required = numbers["required"]
    if required is None or required != plan["price"] or not config.allows(name, required):
        reasons.append("required_money_mismatch")
    if numbers["money_before"] is None or numbers["money_before"] != plan["money"]:
        reasons.append("money_before_mismatch")
    if None in (numbers["money_before"], numbers["money_after"], required) \
            or numbers["money_after"] != numbers["money_before"] - required:
        reasons.append("money_after_mismatch")
    elif numbers["money_after"] < config.coin_reserve:
        reasons.append("coin_reserve")
    item_after = fields.get("item_after")
    if numbers["item_before"] is None or numbers["item_after"] is None or not str(item_after).endswith("(+1)") \
            or numbers["item_after"] != numbers["item_before"] + 1:
        reasons.append("item_count_not_plus_1")
    return reasons


def check_result(intent, fields):
    """Reasons the 交換結果 dialog does not prove the intended exchange (empty list = verified)."""
    if intent is None:
        return ["no_pending_intent"]
    reasons = []
    if fields.get("sentence") != intent["name"] + RESULT_SUFFIX:
        reasons.append("result_sentence_mismatch")
    if to_int(fields.get("money")) != intent["money_before"] - intent["price"]:
        reasons.append("result_money_mismatch")
    if to_int(fields.get("items")) != intent["item_before"] + 1:
        reasons.append("result_items_mismatch")
    return reasons


# ---------------------------------------------------------------- per-run state
class ShopRun:
    """State of one Shop task run (reset by SC.Shop.Begin). Holds no authority of its own."""

    def __init__(self, config, log):
        self.config, self.log = config, log
        self.phase = "active"           # active | awaiting_result | finished
        self.outcome = None
        self.notes = []
        self.plan = None                # set when a card is opened
        self.intent = None              # set when the exchange grant was issued
        self.confirm_rejected = None
        self.bought = []                # ranks verified in this run
        self.pre_purchase_key = None    # visible list before the last verified purchase (refresh wait)
        self.waits = 0
        self.unreadable = 0
        self.decisions = []
        self.reported = False

    @property
    def active(self):
        return self.phase == "active"

    def finish(self, outcome, *notes):
        if self.phase != "finished":
            self.phase, self.outcome = "finished", outcome
        self.notes.extend(n for n in notes if n)

    @property
    def succeeded(self):
        return self.phase == "finished" and self.outcome in SUCCESS_OUTCOMES

    def summary(self):
        return {"outcome": self.outcome, "phase": self.phase, "succeeded": self.succeeded,
                "bought_ranks": list(self.bought), "notes": list(self.notes), "config": self.config.to_dict(),
                "decisions": self.decisions[-12:], "log": str(self.log.path())}

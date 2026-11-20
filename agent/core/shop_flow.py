"""每日商店（マニー页「本日限定」）合同：纯函数，不连接设备，也不授予实时输入权限。

几何来自本机原生 1280x720 样本（2026-10-02 manual-capture-kept-20261002/shop-grade）。
阶段：Home → Premium → List → Confirm →（Result 未知）→ After。只有三个
``shop-exchange-confirm-<rank>`` 消耗マニー；交換数的 −/+/>> 控件永远不是目标。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "SC.Shop."
STAGES = ("Home", "Premium", "List", "Confirm", "Result", "After")
GUARD_STAGES = ("Home", "Premium", "List", "Confirm", "Result")
FIXTURE = ROOT / "tests/fixtures/shop_flow/pipeline.json"
CARD_SPACING = 270
OFFER_RANKS = (1, 2, 3)
PRESENCE_SCORE = 0.80  # Exclusion only; positive anchors keep 0.95.
THRESHOLD = 0.95


def _card(roi, rank):
    x, y, w, h = roi
    return [x + CARD_SPACING * (rank - 1), y, w, h]


ANCHORS = {
    "Home": {"ShopLabel": [35, 276, 56, 17], "MenuLabel": [33, 348, 60, 24],
             "PresentLabel": [30, 193, 66, 18], "FesText": [1150, 645, 80, 25]},
    "Premium": {"Title": [6, 16, 150, 40], "PremiumSelected": [20, 92, 128, 40],
                "MoneyIdle": [440, 90, 112, 42], "NewbieSubtab": [40, 165, 172, 44]},
    "List": {"Title": [6, 16, 150, 40], "MoneySelected": [440, 90, 112, 42], "MoneyIcon": [958, 22, 40, 40],
             "AllSelected": [40, 165, 172, 44], "Back": [20, 628, 74, 66],
             **{f"Banner{r}": _card([118, 228, 102, 30], r) for r in OFFER_RANKS},
             **{f"LimitLabel{r}": _card([60, 516, 126, 28], r) for r in OFFER_RANKS},
             **{f"Coin{r}": _card([94, 598, 20, 20], r) for r in OFFER_RANKS}},
    "Confirm": {"Title": [360, 40, 162, 36], "Prompt": [410, 132, 460, 56], "Labels": [412, 352, 170, 182],
                "Controls": [640, 340, 190, 40], "Cancel": [457, 587, 167, 79], "Exchange": [657, 587, 167, 79]},
    # 交換結果 dialog after 交換する (live 2026-10-02, shop-20261002-04-confirm1-frames/frame-024.png).
    "Result": {"Title": [360, 170, 162, 36], "Labels": [405, 343, 170, 72], "Close": [557, 457, 167, 80]},
}
# 2026-10-02 live: a bought 本日限定 card is REMOVED from the list and later cards shift left; the
# leading 1–3 positions carry the 本日限定 banner, then monthly items follow. The List guard therefore
# proves only the common page anchors; each daily slot (banner + 交換上限/日 label + enabled coin
# button) is evaluated separately, and the original rank is resolved from the day's own ledger.
LIST_COMMON = ("Title", "MoneySelected", "MoneyIcon", "AllSelected", "Back")
SLOT_ANCHORS = ("Banner", "LimitLabel", "Coin")
# A disabled (grey) price button keeps its coin icon; only the orange button colour proves it enabled.
# Measured: enabled 478–480 of 480 pixels, grey 0 (frame shop-after-two-bought-20261002).
BUTTON_COLOR = {"roi": [140, 634, 60, 8], "lower": [230, 120, 0], "upper": [255, 235, 90], "count": 400}


# Grey header of a non-daily (monthly) card; every position after the daily slots must show it, so a
# card covered by an opening dialog or overlay makes the layout inconsistent (stop).
MONTHLY_HEADER_ROI = [118, 228, 102, 30]


def monthly_scene(position):
    _require(position in OFFER_RANKS, "卡片位置无效")
    return PREFIX + "List.MonthlyHeader" + str(position)


def monthly_roi(position):
    return _card(MONTHLY_HEADER_ROI, position)


def button_scene(position):
    _require(position in OFFER_RANKS, "卡片位置无效")
    return PREFIX + "List.ButtonEnabled" + str(position)


def button_roi(position):
    return _card(BUTTON_COLOR["roi"], position)
# Distinctive anchors used to refuse a frame where another known stage is (partly) visible.
DISTINCTIVE = {"Home": ("ShopLabel", "FesText"), "Premium": ("PremiumSelected", "NewbieSubtab"),
               "List": ("MoneySelected", "AllSelected"), "Confirm": ("Title", "Prompt"), "Result": ("Title", "Close")}
# The exchange dialog is drawn over the undimmed List.
UNDERLYING = {"Confirm": ("List",), "Result": ("List",)}
EXCLUSIONS = {"Loading": [837, 662, 294, 47]}

# Known 本日限定 items; identity is a template match of the name, never a position.
ITEMS = {"skip_ticket": "スキップチケット", "gold_training_ticket": "ゴールドトレーニングチケット",
         "illumination_stars_towel": "イルミネーションスターズのタオル"}
ITEM_TYPE = "ordinary_item"
# Rank mode: "buy offer rank r whatever it is today" (本日限定 items change daily). Identity is then the
# OCR'd item name, which must be readable and identical on the card and in the 交換確認 dialog.
ANY_ITEM = "any_daily_discount"
CURRENCY_PROMPT = "マニーを消費して"
CURRENCY_PROMPT_ROI = [410, 132, 460, 56]
CONFIRM_NAME_ROI = [515, 228, 300, 62]
# 「<name>と交換しました」 sentence of the result dialog (Japanese text OCR, fixed box).
RESULT_SENTENCE_ROI = [400, 258, 480, 40]
RESULT_SUFFIX = "と交換しました"


def list_name_roi(rank):
    _require(rank in OFFER_RANKS, "商品位次无效")
    return _card([41, 274, 250, 50], rank)


ACTIONS = {"home-shop": ("Home", (62, 282)), "shop-money-tab": ("Premium", (495, 110)),
           "shop-back": ("List", (57, 662)), "shop-exchange-cancel": ("Confirm", (540, 626)),
           **{f"shop-offer-open-{r}": ("List", (166 + CARD_SPACING * (r - 1), 607)) for r in OFFER_RANKS},
           **{f"shop-exchange-confirm-{r}": ("Confirm", (740, 626)) for r in OFFER_RANKS},
           "shop-result-close": ("Result", (640, 497))}
CONSUMING = tuple(f"shop-exchange-confirm-{r}" for r in OFFER_RANKS)
# Non-consuming. Offer-open is repeatable (≤3/day each) because after a purchase the next rank sits
# at the same screen position; the exchange confirms remain one-shot per rank and game day.
REPEATABLE = ("home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel",
              "shop-offer-open-1", "shop-offer-open-2", "shop-offer-open-3", "shop-result-close")
POST_STAGES = {"home-shop": ("Premium", "List"), "shop-money-tab": ("List",), "shop-back": ("Home",),
               "shop-exchange-cancel": ("List",),
               **{f"shop-offer-open-{r}": ("Confirm",) for r in OFFER_RANKS},
               **{f"shop-exchange-confirm-{r}": ("Result", "After") for r in OFFER_RANKS},
               "shop-result-close": ("After",)}

_COUNT = r"[0-9]{1,3}(?:,[0-9]{3})*"
# Fixed numeric crops (x, y, w, h) and preprocessing kind; only_rec on the crop.
OCR_FIELDS = {
    "List.money": ([1000, 28, 170, 30], "light", "^" + _COUNT + "$"),
    **{f"List.limit{r}": (_card([225, 516, 52, 28], r), "dark", r"^[0-9]{1,2}/[0-9]{1,2}$") for r in OFFER_RANKS},
    **{f"List.price{r}": (_card([130, 590, 100, 34], r), "blue_inv", "^" + _COUNT + "$") for r in OFFER_RANKS},
    "Confirm.quantity": ([690, 346, 70, 30], "dark", r"^[0-9]{1,2}$"),
    "Confirm.limit_after": ([686, 388, 32, 30], "dark", r"^[0-9]{1,2}$"),
    "Confirm.limit_max": ([731, 388, 32, 30], "dark", r"^[0-9]{1,2}$"),
    "Confirm.required": ([660, 428, 160, 30], "dark", "^" + _COUNT + "$"),
    "Confirm.money_before": ([583, 466, 130, 32], "dark", "^" + _COUNT + "$"),
    "Confirm.money_after": ([745, 466, 135, 32], "dark", "^" + _COUNT + "$"),
    "Confirm.item_before": ([585, 504, 115, 32], "dark", "^" + _COUNT + "$"),
    "Confirm.item_after": ([745, 504, 135, 32], "dark", "^" + _COUNT + r"\(\+1\)$"),
    # The result dialog prints numbers WITHOUT thousands separators (e.g. 17678482).
    "Result.money": ([600, 342, 240, 26], "dark", r"^[0-9]{1,10}$"),
    "Result.items": ([600, 380, 240, 26], "dark", r"^(?:[0-9]{1,7}|" + _COUNT + ")$"),
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


class AlreadyComplete(ValueError):
    """Today's ledger already confirms this rank (or the card shows its limit reached); skip, never buy."""


def scene_name(stage, anchor):
    return PREFIX + stage + "." + anchor


def anchor_stage(stage):
    return "List" if stage == "After" else stage


def guard_anchors(stage):
    base = anchor_stage(stage)
    return {a: roi for a, roi in ANCHORS[base].items() if base != "List" or a in LIST_COMMON}


def anchor_names(stage):
    base = anchor_stage(stage)
    return [scene_name(base, anchor) for anchor in guard_anchors(stage)]


def slot_names(position):
    _require(position in OFFER_RANKS, "卡片位置无效")
    return [scene_name("List", f"{anchor}{position}") for anchor in SLOT_ANCHORS] + [button_scene(position)]


def action_anchor_names(action):
    """Opening position p additionally requires that slot's banner, daily label and enabled coin button."""
    stage = ACTIONS[action][0]
    names = anchor_names(stage)
    if action.startswith("shop-offer-open-"):
        names = names + slot_names(int(action.rsplit("-", 1)[1]))
    return names


def item_scene(item_id, where):
    _require(item_id in ITEMS and where in ("List", "Confirm"), "商品身份场景无效")
    return PREFIX + "Item." + item_id + "." + where


def exclusion_scene(name):
    _require(name in EXCLUSIONS, "排除场景无效")
    return PREFIX + "Exclude." + name


# ---------------------------------------------------------------- Pipeline

def _expected_pipeline():
    pipeline = {}
    for stage in GUARD_STAGES:
        base = PREFIX + stage + "."
        names = anchor_names(stage)
        for anchor, roi in ANCHORS[stage].items():
            pipeline[base + anchor] = {"recognition": "TemplateMatch", "template": stage + "-" + anchor + ".png",
                "roi": roi, "threshold": THRESHOLD, "method": 5, "action": "DoNothing", "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Guard"] = {"recognition": "And", "all_of": names, "action": "DoNothing",
                                    "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Observe"] = {"recognition": "DirectHit", "action": "DoNothing",
            "next": [base + "Guard", base + "Unknown"], "on_error": [PREFIX + "Error"],
            "timeout": 1000, "rate_limit": 0, "pre_delay": 0, "post_delay": 0}
        pipeline[base + "Unknown"] = {"recognition": "Or", "any_of": [base + "Guard"], "inverse": True,
                                      "action": "StopTask", "pre_delay": 0, "post_delay": 0}
    for position in OFFER_RANKS:
        pipeline[button_scene(position)] = {"recognition": "ColorMatch", "roi": button_roi(position),
            "lower": BUTTON_COLOR["lower"], "upper": BUTTON_COLOR["upper"], "count": BUTTON_COLOR["count"],
            "method": 4, "connected": False, "action": "DoNothing", "pre_delay": 0, "post_delay": 0}
    for action, (stage, (x, y)) in ACTIONS.items():
        pipeline[PREFIX + "Action." + action] = {"enabled": False, "recognition": "And", "all_of": anchor_names(stage),
            "action": "Click", "target": [x, y, 1, 1], "on_error": [PREFIX + "Error"], "pre_delay": 0, "post_delay": 0}
        pipeline[PREFIX + "Action." + action]["all_of"] = action_anchor_names(action)
    pipeline[PREFIX + "Error"] = {"recognition": "DirectHit", "action": "StopTask", "pre_delay": 0, "post_delay": 0}
    return pipeline


def fixed_pipeline(action=None):
    """Validate the immutable on-disk flow; ``action`` enables exactly one fixed Click.

    Each Click ends the task; the caller must re-capture and classify the result before
    any further task. No edge repeats a consuming action.
    """
    pipeline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _require(pipeline == _expected_pipeline(), "商店Pipeline固定节点、锚点或默认禁用合同变化")
    if action is not None:
        _require(action in ACTIONS, "未知商店动作")
        stage = ACTIONS[action][0]
        pipeline[PREFIX + "Action." + action]["enabled"] = True
        pipeline[PREFIX + stage + ".Observe"]["next"][0] = PREFIX + "Action." + action
    return pipeline


# ---------------------------------------------------------------- recognition decisions

def _score(item):
    score = item.get("score") if type(item) is dict else None
    return score if type(score) in (int, float) and math.isfinite(score) else None


def exact_match(item, roi):
    score = _score(item)
    matches = item.get("raw_detail", {}).get("filtered") if type(item) is dict else None
    return (item.get("hit") is True and score is not None and THRESHOLD <= score <= 1
            and item.get("roi") == roi and type(matches) is list and len(matches) == 1
            and type(matches[0]) is dict and matches[0].get("box") == roi)


def present(item):
    score = _score(item)
    return type(item) is dict and (item.get("hit") is True or score is not None and score >= PRESENCE_SCORE)


def stage_matches(stage, recognitions):
    """All fixed anchors of the stage match uniquely at their exact ROI (≥0.95)."""
    if stage not in STAGES or type(recognitions) is not list:
        return False
    base = anchor_stage(stage)
    expected = {scene_name(base, a): roi for a, roi in guard_anchors(stage).items()}
    by_name = {r.get("scene"): r for r in recognitions if type(r) is dict and r.get("scene") in expected}
    if len(by_name) != len(expected) or sum(1 for r in recognitions if type(r) is dict and r.get("scene") in expected) != len(expected):
        return False
    return all(exact_match(by_name[name], roi) for name, roi in expected.items())


def exclusions_clear(stage, recognitions):
    """Refuse loading overlays and any other known stage's distinctive anchors."""
    if stage not in STAGES or type(recognitions) is not list:
        return False
    base = anchor_stage(stage)
    by_name = {r.get("scene"): r for r in recognitions if type(r) is dict}
    for name in EXCLUSIONS:
        item = by_name.get(exclusion_scene(name))
        if item is None or present(item):
            return False
    for other in GUARD_STAGES:
        if other == base or other in UNDERLYING.get(base, ()):
            continue
        for anchor in DISTINCTIVE[other]:
            item = by_name.get(scene_name(other, anchor))
            if item is None or present(item):
                return False
    return True


def slot_full(recognitions, position):
    """Banner + 交換上限 label + coin match exactly and the price button is orange (enabled)."""
    by_name = {r.get("scene"): r for r in recognitions if type(r) is dict} if type(recognitions) is list else {}
    names = slot_names(position)
    if any(n not in by_name for n in names):
        return False
    button = by_name[names[-1]]
    enabled = (button.get("hit") is True and button.get("roi") == button_roi(position)
               and type(button.get("matched_pixels")) is int and button["matched_pixels"] >= BUTTON_COLOR["count"])
    return enabled and all(exact_match(by_name[n], ANCHORS["List"][n.rsplit(".", 1)[1]]) for n in names[:-1])


def daily_slots(recognitions):
    """Number n of leading 本日限定 positions (0..3), or None if the layout is inconsistent.

    Positions 1..n must each be a full, enabled daily slot; every later position must show the grey
    monthly-card header and no banner. A banner with a grey/disabled button, a gap, or a covered card
    (opening dialog, overlay) is None: the List guard refuses.
    """
    if type(recognitions) is not list:
        return None
    by_name = {r.get("scene"): r for r in recognitions if type(r) is dict}
    count, ended = 0, False
    for position in OFFER_RANKS:
        banner = by_name.get(slot_names(position)[0])
        monthly = by_name.get(monthly_scene(position))
        if banner is None or monthly is None:
            return None
        if not ended and slot_full(recognitions, position):
            count += 1
        elif not present(banner) and exact_match(monthly, monthly_roi(position)):
            ended = True
        else:
            return None
    return count


def position_of(rank, confirmed):
    """Screen position of original rank r after today's confirmed purchases (bought cards are removed)."""
    _require(rank in OFFER_RANKS and type(confirmed) in (set, frozenset, list, tuple), "位次或账本无效")
    confirmed = set(confirmed)
    _require(confirmed <= set(OFFER_RANKS) and rank not in confirmed, "该位次今日已确认购买")
    return rank - sum(1 for q in confirmed if q < rank)


def identify_item(results, roi):
    """``results`` maps item_id → recognition at ``roi``; exactly one item may match."""
    _require(type(results) is dict and set(results) == set(ITEMS), "商品身份识别必须覆盖全部已知商品")
    matched = [item for item, rec in results.items() if exact_match(rec, roi)]
    others_present = [item for item, rec in results.items() if item not in matched and _score(rec) is not None
                      and _score(rec) >= THRESHOLD]
    return matched[0] if len(matched) == 1 and not others_present else None


# ---------------------------------------------------------------- numbers

def normalize_name(text):
    """NFKC, then drop all whitespace/line breaks (card names may wrap to two lines)."""
    import unicodedata
    _require(isinstance(text, str), "名称必须为字符串")
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _identity(identity):
    """(catalog item id or None, normalised OCR name or None). A bare string is a catalog id."""
    if identity is None or isinstance(identity, str):
        return identity, None
    _require(type(identity) is dict and set(identity) <= {"catalog", "name"}, "商品身份证据格式无效")
    name = identity.get("name")
    name = normalize_name(name) if isinstance(name, str) and name.strip() else None
    return identity.get("catalog"), name


def parse_count(text):
    _require(isinstance(text, str) and re.fullmatch(_COUNT, text) is not None, "数字格式无效")
    return int(text.replace(",", ""))


def parse_limit(text):
    _require(isinstance(text, str), "上限格式无效")
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", text)
    _require(match is not None, "上限格式无效")
    current, limit = int(match.group(1)), int(match.group(2))
    _require(limit > 0 and 0 <= current <= limit, "上限数值无效")
    return current, limit


def list_evidence(texts):
    """texts: accepted OCR text per List field (None if refused)."""
    _require(type(texts) is dict, "列表OCR证据无效")
    evidence = {"money": parse_count(texts.get("List.money")), "offers": {}}
    for rank in OFFER_RANKS:
        offer = {}
        try:
            offer["limit_current"], offer["limit_max"] = parse_limit(texts.get(f"List.limit{rank}"))
        except ValueError:
            pass
        try:
            offer["price"] = parse_count(texts.get(f"List.price{rank}"))
        except ValueError:
            pass
        evidence["offers"][rank] = offer
    return evidence


def confirm_evidence(texts):
    _require(type(texts) is dict, "弹窗OCR证据无效")
    after = texts.get("Confirm.item_after")
    _require(isinstance(after, str) and after.endswith("(+1)"), "所持数预览必须为(+1)")
    return {"quantity": int(texts["Confirm.quantity"]) if isinstance(texts.get("Confirm.quantity"), str)
                        and texts["Confirm.quantity"].isdigit() else None,
            "limit_after": int(texts["Confirm.limit_after"]) if isinstance(texts.get("Confirm.limit_after"), str)
                           and texts["Confirm.limit_after"].isdigit() else None,
            "limit_max": int(texts["Confirm.limit_max"]) if isinstance(texts.get("Confirm.limit_max"), str)
                         and texts["Confirm.limit_max"].isdigit() else None,
            "required": parse_count(texts.get("Confirm.required")),
            "money_before": parse_count(texts.get("Confirm.money_before")),
            "money_after": parse_count(texts.get("Confirm.money_after")),
            "item_before": parse_count(texts.get("Confirm.item_before")),
            "item_after": parse_count(after[:-4])}


# ---------------------------------------------------------------- configuration and budgets

def offer_config(config, rank):
    """The user's explicit rule for this rank; rank 3 only with include_third. Quantity is exactly 1."""
    from agent.core.daily_config import DailyConfig
    data = DailyConfig.from_dict(config.data if isinstance(config, DailyConfig) else config).data
    shop = data["shop"]
    _require(data["premium_currency_allowed"] is False, "付费货币始终禁止")
    _require(shop["enabled"] is True and shop["purchase_scope"] == "daily_discount_only", "商店未启用或范围不是本日限定")
    _require(rank in OFFER_RANKS and (rank != 3 or shop["include_third"] is True), "第三项需显式开启 include_third")
    items = [item for item in shop["items"] if item["offer_rank"] == rank]
    _require(len(items) == 1, "该位次缺少明确商品规则")
    item = dict(items[0])
    _require((item["item_id"] in ITEMS or item["item_id"] == ANY_ITEM) and item["item_type"] == ITEM_TYPE,
             "商品身份不在已核实目录（或 any_daily_discount）或类型不符")
    if item["item_id"] == ANY_ITEM:
        _require(item.get("currency") == "money", "any_daily_discount 必须声明 currency=money（マニー）")
    item["identity_mode"] = "rank" if item["item_id"] == ANY_ITEM else "catalog"
    _require(item["quantity"] == 1, "每个本日限定商品每次恰好交换1个")
    return item, {"coin_budget": min(shop["coin_budget"], data["budgets"]["coins"]),
                  "coin_reserve": shop["coin_reserve"]}


def _check_spend(item, limits, price, money, spent_today):
    _require(type(price) is int and price > 0, "价格OCR缺失")
    _require(type(money) is int and money >= 0, "マニー余额OCR缺失")
    _require(price <= item["max_unit_price"], "价格超过该商品配置的单价上限")
    _require(type(spent_today) is int and spent_today >= 0, "当日已花费账本无效")
    _require(spent_today + price <= limits["coin_budget"], "超出当日マニー总预算（含已发送/已确认）")
    _require(money - price >= limits["coin_reserve"], "交换后余额低于保留额度")


def _slot_identity(slots, position):
    _require(type(slots) is dict and type(slots.get("cards")) is dict, "卡片槽位证据无效")
    return _identity(slots["cards"].get(position, slots["cards"].get(str(position))))


def _snapshot(slots, count):
    rows = []
    for position in range(1, count + 1):
        catalog, name = _slot_identity(slots, position)
        rows.append({"catalog": catalog, "name": name})
    return rows


def validate_offer_open(config, rank, list_ev, slots, confirmed, ledger_state, spent_today):
    """Before opening the dialog. ``rank`` is the original 本日限定 rank; the screen position is resolved
    from today's confirmed ledger (bought cards disappear). Any mismatch between the visible banner
    count and the ledger means an unknown (e.g. manual) purchase: stop, never infer."""
    item, limits = offer_config(config, rank)
    confirmed = set(confirmed)
    if rank in confirmed:
        raise AlreadyComplete("今日账本已确认该位次购买，跳过")
    _require(ledger_state == "ready", "该商品今日已有pending记录，禁止再次交换")
    count = slots.get("count") if type(slots) is dict else None
    _require(type(count) is int and count == len(OFFER_RANKS) - len(confirmed),
             "可见本日限定数量与今日账本不符（可能有手动或未知购买），停止")
    position = position_of(rank, confirmed)
    _require(1 <= position <= count, "目标位置没有本日限定卡")
    catalog, name = _slot_identity(slots, position)
    if item["identity_mode"] == "rank":
        _require(name is not None, "本日限定卡片名称OCR不可读，停止")
    else:
        _require(catalog == item["item_id"], "卡片商品身份与配置不符，不按位置猜测")
    offer = list_ev["offers"].get(position, {})
    _require(type(offer.get("limit_current")) is int and type(offer.get("limit_max")) is int, "交换上限OCR缺失")
    if offer["limit_current"] >= offer["limit_max"]:
        raise AlreadyComplete("该商品今日已达交换上限，跳过")
    _require(offer["limit_current"] == 0 and offer["limit_max"] == 1, "仅接受 0/1 的每日限定状态")
    _check_spend(item, limits, offer.get("price"), list_ev.get("money"), spent_today)
    return {"rank": rank, "position": position, "banner_count": count, "confirmed_before": sorted(confirmed),
            "slots": _snapshot(slots, count), "item_id": item["item_id"], "identity_mode": item["identity_mode"],
            "card_name": name, "catalog_match": catalog, "price": offer["price"], "money": list_ev["money"],
            "limit_current": offer["limit_current"], "limit_max": offer["limit_max"]}


def validate_confirm(config, rank, baseline, confirm_ev, identity, ledger_state, spent_today):
    """Before 交換する: the dialog preview must agree with the opened card and the budgets."""
    item, limits = offer_config(config, rank)
    _require(type(baseline) is dict and baseline.get("rank") == rank and baseline.get("item_id") == item["item_id"],
             "缺少本位次打开弹窗前的列表基线")
    catalog, name = _identity(identity)
    if item["identity_mode"] == "rank":
        _require(name is not None and baseline.get("card_name") is not None, "弹窗或卡片商品名称OCR不可读，停止")
        _require(name == baseline["card_name"], "弹窗商品名称与打开的卡片名称不一致，停止")
    else:
        _require(catalog == item["item_id"], "弹窗商品身份与配置不符")
    _require(ledger_state == "ready", "该商品今日已有pending或确认记录，禁止再次交换")
    ev = confirm_ev
    prompt = ev.get("currency_prompt")
    _require(isinstance(prompt, str) and normalize_name(prompt).startswith(CURRENCY_PROMPT),
             "弹窗未以OCR证明「マニー を消費して」，货币不明停止")
    _require(ev.get("quantity") == 1, "交換数必须恰好为1；禁止操作 −/+/>>")
    _require(ev.get("limit_max") == baseline["limit_max"] == 1, "弹窗交换上限与列表不符")
    _require(ev.get("limit_after") == baseline["limit_current"] + 1 <= ev["limit_max"], "弹窗交换次数预览与列表不符")
    _require(ev.get("required") == baseline["price"], "必要マニー与列表价格不符")
    _require(ev.get("money_before") == baseline["money"], "所持マニー与列表余额不符")
    _require(ev.get("money_after") == ev["money_before"] - ev["required"], "所持マニー预览不等于余额减必要マニー")
    _require(ev.get("item_after") == ev.get("item_before", -2) + 1, "アイテム所持数预览必须恰好+1")
    _check_spend(item, limits, ev["required"], ev["money_before"], spent_today)
    return {"rank": rank, "item_id": item["item_id"], "identity_mode": item["identity_mode"],
            "card_name": baseline.get("card_name"), "dialog_name": name, "catalog_match": catalog,
            "required": ev["required"], "money_before": ev["money_before"],
            "money_after": ev["money_after"], "item_before": ev["item_before"], "item_after": ev["item_after"],
            "limit_current_before": baseline["limit_current"], "limit_max": baseline["limit_max"]}


def result_evidence(texts, sentence):
    """Result dialog numbers (no separators) and the OCR'd 「<name>と交換しました」 sentence."""
    _require(type(texts) is dict, "结果OCR证据无效")
    money, items = texts.get("Result.money"), texts.get("Result.items")
    _require(isinstance(money, str) and re.fullmatch(r"[0-9]{1,10}", money) is not None, "结果所持マニーOCR缺失")
    _require(isinstance(items, str), "结果アイテム所持数OCR缺失")
    sentence = normalize_name(sentence) if isinstance(sentence, str) else ""
    _require(sentence.endswith(RESULT_SUFFIX) and len(sentence) > len(RESULT_SUFFIX), "结果文案不是「…と交換しました」")
    return {"money": int(money), "items": int(items) if re.fullmatch(r"[0-9]{1,7}", items) else parse_count(items),
            "name": sentence[:-len(RESULT_SUFFIX)]}


def validate_result(witness, result_ev):
    """Read-only proof from the 交換結果 dialog: the exchanged name equals the snapshotted dialog name,
    マニー equals before − required and the item count equals before + 1."""
    spend = witness.get("spend") if type(witness) is dict else None
    _require(type(spend) is dict, "pending见证缺少交换预览")
    expected = spend.get("dialog_name") or spend.get("card_name")
    _require(isinstance(expected, str) and expected and result_ev.get("name") == normalize_name(expected),
             "结果中的商品名称与交换确认时不一致")
    _require(result_ev.get("money") == spend["money_before"] - spend["required"], "结果所持マニー不等于余额减必要マニー")
    _require(result_ev.get("items") == spend["item_before"] + 1, "结果アイテム所持数不等于交换前+1")
    return {"rank": spend["rank"], "item_id": spend["item_id"], "name": result_ev["name"],
            "money": result_ev["money"], "items": result_ev["items"]}


def _same_card(before, after):
    if before["name"] is not None:
        return after["name"] == before["name"]
    return before["catalog"] is not None and after["catalog"] == before["catalog"]


def validate_after(witness, list_ev, slots):
    """Read-only resolution: the bought card disappeared, later 本日限定 cards shifted left by one,
    the banner count fell by exactly one and マニー fell by exactly the required amount."""
    spend = witness.get("spend") if type(witness) is dict else None
    baseline = witness.get("offer_baseline") if type(witness) is dict else None
    _require(type(spend) is dict and type(baseline) is dict and baseline.get("rank") == spend.get("rank"),
             "pending见证缺少交换预览或打开时的列表快照")
    count = slots.get("count") if type(slots) is dict else None
    _require(type(count) is int and count == baseline["banner_count"] - 1, "本日限定数量未恰好减少1")
    expected = [row for index, row in enumerate(baseline["slots"], 1) if index != baseline["position"]]
    current = _snapshot(slots, count)
    _require(len(expected) == len(current) and all(_same_card(b, a) for b, a in zip(expected, current)),
             "剩余本日限定卡未按预期左移（身份不可读或不一致）")
    _require(list_ev.get("money") == spend["money_before"] - spend["required"], "交换后マニー未恰好减少必要マニー")
    return {"rank": spend["rank"], "item_id": spend["item_id"], "card_name": spend.get("card_name"),
            "money": list_ev["money"], "banner_count": count, "remaining": current}


# ---------------------------------------------------------------- game day and ledger

def game_day(now=None):
    """JST (UTC+9) calendar date with the 05:00 rollover (same as RepeatableNavigationBudget.GameDayOf).

    User-confirmed 2026-10-02: 本日限定 offers refresh at 05:00 JST together with daily missions.
    """
    now = _dt.datetime.now(_dt.timezone.utc) if now is None else now
    _require(isinstance(now, _dt.datetime) and now.tzinfo is not None, "时间必须带时区")
    return (now.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=9 - 5)).strftime("%Y-%m-%d")


def _sha(blob):
    return hashlib.sha256(blob).hexdigest()


class ShopLedger:
    """Per-game-day, per-offer one-shot consumption ledger under dev_assets/private/daily_shop.

    ``offer-<rank>.pending.json`` is written (exclusive create) before 交換する is sent and is
    never deleted. ``offer-<rank>.confirmed.json`` is written only by read-only re-observation.
    An unresolved pending on any day blocks every further exchange.
    """
    def __init__(self, root=None):
        private = (ROOT / "dev_assets/private").resolve()
        self.root = Path(root or private / "daily_shop").resolve()
        _require(self.root.is_relative_to(private), "商店账本必须位于private")

    def pending_path(self, day, rank):
        _require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") is not None and rank in OFFER_RANKS, "账本日或位次无效")
        return self.root / day / f"offer-{rank}.pending.json"

    def confirmed_path(self, day, rank):
        return self.pending_path(day, rank).with_name(f"offer-{rank}.confirmed.json")

    def _load(self, path):
        data = json.loads(path.read_bytes().decode("utf-8"))
        _require(type(data) is dict and data.get("version") == 1 and data.get("runner") == "SC.DailyShop", "商店账本文件损坏")
        return data

    def after_path(self, day, rank):
        return self.pending_path(day, rank).with_name(f"offer-{rank}.after.json")

    def write_after(self, day, rank, record):
        """Optional read-only record that the bought card vanished after the result was closed."""
        _require(self.state(day, rank) == "confirmed", "只有已确认的交换才能登记交换后列表")
        path = self.after_path(day, rank)
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"version": 1, "runner": "SC.DailyShop", **record}, stream, ensure_ascii=False, indent=2,
                      allow_nan=False)
        return {"path": str(path), "sha256": _sha(path.read_bytes())}

    def confirmed_record(self, day, rank):
        return self._load(self.confirmed_path(day, rank))

    def confirmed_ranks(self, day):
        return {rank for rank in OFFER_RANKS if self.state(day, rank) == "confirmed"}

    def state(self, day, rank):
        pending, confirmed = self.pending_path(day, rank), self.confirmed_path(day, rank)
        if confirmed.exists():
            _require(pending.exists(), "确认记录缺少pending见证；账本不一致")
            return "confirmed"
        return "pending" if pending.exists() else "ready"

    def unresolved(self):
        rows = []
        if self.root.exists():
            for path in sorted(self.root.glob("*/offer-*.pending.json")):
                rank = int(path.name.split("-")[1].split(".")[0])
                if not self.confirmed_path(path.parent.name, rank).exists():
                    rows.append({"day": path.parent.name, "rank": rank, "path": str(path)})
        return rows

    def spent(self, day):
        total = 0
        for rank in OFFER_RANKS:
            path = self.pending_path(day, rank)
            if path.exists():
                spend = self._load(path).get("spend", {})
                _require(type(spend.get("required")) is int and spend["required"] > 0, "pending见证缺少必要マニー")
                total += spend["required"]
        return total

    def write_pending(self, day, rank, witness):
        _require(type(witness) is dict and witness.get("runner") == "SC.DailyShop" and witness.get("version") == 1
                 and witness.get("no_retry") is True and type(witness.get("spend")) is dict
                 and witness["spend"].get("rank") == rank, "pending见证字段无效")
        path = self.pending_path(day, rank)
        path.parent.mkdir(parents=True, exist_ok=True)
        import os
        with path.open("x", encoding="utf-8") as stream:
            json.dump(witness, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": str(path), "sha256": _sha(path.read_bytes())}

    def read_pending(self, day, rank):
        return self._load(self.pending_path(day, rank))

    def write_confirmed(self, day, rank, resolution):
        pending = self.pending_path(day, rank)
        _require(pending.exists(), "无pending见证，不能登记确认")
        _require(type(resolution) is dict and resolution.get("pending_sha256") == _sha(pending.read_bytes()),
                 "确认记录必须绑定原pending见证")
        path = self.confirmed_path(day, rank)
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"version": 1, "runner": "SC.DailyShop", **resolution}, stream, ensure_ascii=False, indent=2,
                      allow_nan=False)
        return {"path": str(path), "sha256": _sha(path.read_bytes())}

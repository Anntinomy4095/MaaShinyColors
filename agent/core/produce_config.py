"""Produce 用户偏好合同（P0–P2 离线基础）：严格校验、默认值与周常预算计划；不连接设备、不授权输入。

两条路线必须分开：

* ``weekly_count``：周常「フェスアイドル誕生×3」。默认 = 官方 Auto + スキップチケット（用户确认跳过的 Auto 也计入周常）。
  W.I.N.G. 的 Auto 只有 Middle；Auto 不会产生 True End。2026-10-08 起它只属于独立任务「Auto Produce（周常）」
  （``assets/tasks/weekly_produce.json``，后续任务、尚无点击）；W.I.N.G. T.E. 任务的「目标」不再提供周常，
  旧 UI 方案里的「周常」由 ``ui/components/store.js`` 的 ``migrateLegacyProfile`` 迁到该任务。
* ``true_end`` / ``fans``：Maa 逐周手动驱动 W.I.N.G.；默认 unit1（2026-10-14 用户改默认；原 unit7 为 TE Vo 队）+ 打分器 ``vo_score``（``wing_policy.STRATEGIES``，
  2026-10-05 集成起为默认；规则表 ``vo_highest`` 仍可选，作兜底与回归）。

编队（``team``，可选，缺省 = 指定编队 unit1、未登记卡）：``mode`` 为 ``manual``（指定编队：用户在游戏里保存的
``unit_preset``，可登记 P 卡 + 4 张上场 S 卡 + 1 张 guest，用于开局前核对）或 ``auto``（自动编队：Maa 用
``team_recommend.recommend``（True End 推荐器优先，数据不足时回退 ``wing_team.select_team``）从「持有卡牌」提出一队，见 ``auto_team_plan``；提案必须由用户在 UI 采用到卡位（变为指定编队）
后才能开始，``run_preflight`` 在此之前以 ``team_needs_confirmation`` 拒绝运行）。**从不使用游戏自带的推荐编成**。卡 ID 为 Wiki 的 10 位 CardIcon 编号：
P 卡以 1 开头、S 卡以 2 开头；第 4–6 位为偶像编号（2026-10-05 对 Wiki 1354 张卡核对）。
**同一偶像在编队中只能出现一次**（P、4 张 S、Guest 之间都不行）：游戏换入同一偶像时弹出
「ユニット内の同一のアイドルが外れてしまいますが 本当によろしいですか？」（WING 演示 #152）。
``duplicate_idols`` 是供评分器（``agent/core/wing_team.py``）复用的唯一实现。游戏内编队为 ユニット1–20（两页各 10 个）。

``enabled`` 默认 false。配置只是偏好与上限，不是运行时授权：live 输入仍受 ``project_policy.json`` 与动作表约束。
付费货币、回复苏打永远禁止；コンティニューチケット、P 道具、多倍体力都需显式开启并受上限约束。
"""
from __future__ import annotations

import copy
import dataclasses
import re
from typing import Any, Mapping

from agent.core import wing_score as wsc
from agent.core import wing_sp as wsp
from agent.core.daily_config import _bool, _enum, _int, _object, _str, load_json
from agent.core.wing_policy import LIVE_MODES, STRATEGIES

GOALS = ("weekly_count", "true_end", "fans")
SCENARIOS = ("wing",)
DIFFICULTIES = ("beginner", "middle", "master")
RISK_LEVELS = ("safe", "balanced", "aggressive")
PRIMARY_STATS = ("Vo", "Da", "Vi")
STAMINA_PER_RUN = 30            # per run at 1× (wiki; 2×/3× multiply cost, not the weekly count)
SKIP_TICKETS_PER_DAY = 5        # daily use limit (wiki, 05:00 JST reset)
AUTO_RUN_LIMIT = 25             # Auto 1–5 slots, ≤25 runs in total (wiki)
AUTO_RUN_MINUTES = 60           # one Auto run without a skip ticket takes about an hour
MAX_P_ITEMS = 2                 # W.I.N.G. allows two item kinds; unused items are lost
MAX_EVENT_CHOICES = 3           # event (コミュ) option index upper bound (0-based, ≤3 options observed)
MANUAL_KEYS = {"strategy", "primary_stat", "use_p_items", "p_items", "skip_communications", "risk_level",
               "use_continue_ticket"}
# Team formation (2026-10-05, UI 编队). W.I.N.G. unit = 1 P idol + 5 support idols, the rightmost being the guest, which
# does not take part in auditions (shinycolors.wiki/wiki/Produce_Info). "game_recommended" is never a valid mode.
TEAM_MODES = ("manual", "auto")
UNIT_PRESETS = tuple(f"unit{i}" for i in range(1, 21))   # ユニット1–20, two pages of 10 in the game
TEAM_PRIMARY = PRIMARY_STATS + ("auto",)
S_SLOTS = 4
P_CARD_ID = re.compile(r"^1\d{9}$")
S_CARD_ID = re.compile(r"^2\d{9}$")
TEAM_KEYS = {"mode", "p_card", "s_cards", "guest", "primary_stat"}
DEFAULT_TEAM = {"mode": "manual", "p_card": None, "s_cards": [None] * S_SLOTS, "guest": None, "primary_stat": "auto"}
OPTIONAL_TOP = {"team": DEFAULT_TEAM}
# Optional since 2026-10-04 (first TE run); missing -> default below. W.I.N.G. T.E. settings (2026-10-06):
# every default reproduces the behaviour before the setting existed (``wing_params`` of a default config yields the
# library defaults; ``rest_trouble_rate`` None = the strategy's own rest threshold).
AUDITION_STYLES = ("first", "attribute")       # 稳拿第一 (trend-#1 focus, LiveParams default) / 按属性 (no focus bonus)
PROMISE_MODES = ("neutral", "keep")            # 中立 (promise_weight 1) / 优先守约 (PROMISE_KEEP_WEIGHT)
PROMISE_KEEP_WEIGHT = 4.0                      # opt-in 优先守约; default 中立 = small bonus (2026-10-14 user)
FALLBACKS = wsc.FALLBACKS                      # 低分兜底: empty primary lesson / rest / keep the best
SP_ORDERS = tuple(wsp.TIER_PRESETS)            # skills_first (user default) / caps_first
# 自动卖书策略 (; agent/core/knowhow_keep.STRATEGIES): 只卖没用的书 / 库存够 3 本 / 库存有 1 本.
KNOWHOW_SELL_STRATEGIES = ("useless_only", "keep3", "keep1")
# % trouble rate at which the rule path rests and the scorer excludes stamina-consuming actions. UI options 5/10/15/20,
# default 10; above 20 everything is excluded anyway (wing_score.TROUBLE_HARD_CAP_MAX).
REST_TROUBLE_RANGE = (5, 20)
CAP_MARGIN_RANGE = (0, 300)
# 高级 (developer) scorer / live weights: whitelisted keys and their allowed ranges. Missing = library default.
TUNING_RANGES = {"w_sp": (0.3, 2.0), "urgency_max": (0.5, 6.0), "floor_ratio": (0.0, 1.0), "bond_beta": (0.0, 2.0),
                 "auto_min_pass": (0.9, 1.0)}
TUNING_LIVE_KEYS = {"auto_min_pass"}           # LiveParams; the rest are ScoreParams
# P items (W.I.N.G. ItemSelect). Only free, non-paid items may be named. Bringing an item consumes it (unused items
# are lost), so it stays off by default and needs project_policy ``resource_usage.other_inventory_allowed`` (see
# ``resource_reasons``). The user's usual bond item (demo
# 2026-10-05 #229–#239) is a 支援絆 item, not a 親愛度 item; its icon looks like 283プロのキーホルダー but only the icon
# was recorded, so the label stays generic until the item name is read. Primary-stat items are resolved from the
# inventory reading at the item screen (never substituted).
BOND_ITEM_LOWEST = "支援絆アイテム（最低ランク）"
PRIMARY_BOOK_LOWEST = "主属性の基礎知識本（最低ランク）"
PRIMARY_TREND_MAGAZINE = "主属性の流行雑誌"
P_ITEM_PRESETS = {
    "none": (),
    "memo_bond": ("秘密のメモ帳", BOND_ITEM_LOWEST),                       # 用户习惯
    "start_boost": (PRIMARY_BOOK_LOWEST, BOND_ITEM_LOWEST),                # 开局强化：开局自动生效，无局中操作
    "final_insurance": (PRIMARY_TREND_MAGAZINE, PRIMARY_BOOK_LOWEST),      # 决赛保险：需局中使用（后续）
    "healing": ("ヒーリングタルト", "トラブル守り"),                           # 体力：タルト需局中使用（后续）
}
# Items that only help when used during the run (ItemPopup「使う」); Maa never presses it yet (ItemUsed is a fixed
# stop), so bringing them would waste them: ``resource_reasons`` refuses them independently of the policy.
P_ITEMS_NEED_MIDRUN_USE = frozenset({PRIMARY_TREND_MAGAZINE, "ヒーリングタルト"})
PAID_ITEM_MARKERS = ("ジュエル", "jewel", "有償", "課金", "283パス", "プレミアム", "スタミナドリンク", "回復ソーダ")
MANUAL_OPTIONAL = {"live_mode": "policy", "event_default_choice": 0, "accept_promises": True,
                   "audition_style": "first", "rest_trouble_rate": None, "fallback": "empty_room",
                   "sp_order": "skills_first", "cap_margin": wsp.DEFAULT_SP_PARAMS["cap_margin"], "promise": "neutral",
                   "tuning": {}, "sp_spend_down": False, "reward_read": True, "reward_protect_lv5": False,
                   "reward_reroll": False, "reward_ok": False, "knowhow_lock": False, "knowhow_sell": False,
                   "knowhow_sell_strategy": "useless_only",
                   "special_s1": True, "special_s2": True, "special_s3": True, "special_s4": True}
# 強化週間 limited auditions per season (「默认自动 按季度分开设」, agent/core/wing_special.py): on = enter
# the season's limited audition when its checks hold (a bonus only), off = always the ordinary route.
SPECIAL_KEYS = ("special_s1", "special_s2", "special_s3", "special_s4")
# W.I.N.G. end / SP settings: 终局清空 SP (off: leftover SP is fine), 獲得報酬 读取 (read-only taps:
# Exスキル tab + each knowhow book's detail), Lv5 书自动保護 (protective, allowed), 再抽選 (stamina 30, off; bounded
# by the shown remaining count, never with a Lv5 book), 读完按 OK (off: hand over on 獲得報酬). Never 売却 there.
# New books (knowhow_keep): 自动锁定 (保護 a needed book the library holds < 3 of) and 自动卖书 with
# its strategy, both off; acted on later in ノウハウブック一覧, not on 獲得報酬 (executor not built: nothing clicks yet).
END_BOOL_KEYS = ("sp_spend_down", "reward_read", "reward_protect_lv5", "reward_reroll", "reward_ok", "knowhow_lock",
                 "knowhow_sell")
# Screens that always stop for the user, whatever the settings (wing_nav.RULES; never configurable). 使用確認
# (StartConfirm) is a gated decision since 2026-10-09: it still stops unless the supervised launcher runs mode=start
# with --allow-produce-spend (wing_nav._decide_start_confirm).
# 2026-10-09c: ProduceSelectOther (特化 tab etc.) left this list: a start run taps 育成プロデュース (user: 「maa要自己选择到这」);
# attach runs still stop there (wing_nav._decide_setup_tab).
ALWAYS_USER_STOPS = ("IdolPickerConfirm", "IdolPicker", "Options", "ItemUsed")

DEFAULT_WEEKLY = {
    "version": 1,
    "enabled": False,
    "goal": "weekly_count",
    "scenario": "wing",
    "unit_preset": "unit1",
    "difficulty": "middle",
    "allow_master": False,
    "stamina_multiplier": 1,
    "premium_currency_allowed": False,
    "recovery_soda_allowed": False,
    "weekly": {"auto_produce": True, "use_skip_tickets": True, "target_count": 3},
    "manual": None,
    "time_budget": {"max_minutes_per_run": 10, "max_total_minutes": 30},
    "caps": {"max_runs": 3, "max_stamina": 90, "max_skip_tickets": 3, "max_p_items": 0},
}

DEFAULT_TRUE_END = {
    "version": 1,
    "enabled": False,
    "goal": "true_end",
    "scenario": "wing",
    "unit_preset": "unit1",
    "difficulty": "middle",
    "allow_master": False,
    "stamina_multiplier": 1,
    "premium_currency_allowed": False,
    "recovery_soda_allowed": False,
    "weekly": None,
    "manual": {"strategy": "vo_score", "primary_stat": "Vo", "use_p_items": False, "p_items": [],
               "skip_communications": True, "risk_level": "safe", "use_continue_ticket": False,
               "live_mode": "policy", "event_default_choice": 0, "accept_promises": True,
               "audition_style": "first", "rest_trouble_rate": None, "fallback": "empty_room",
               "sp_order": "skills_first", "cap_margin": 100, "promise": "neutral", "tuning": {},
               "sp_spend_down": False, "reward_read": True, "reward_protect_lv5": False, "reward_reroll": False,
               "reward_ok": False, "knowhow_lock": False, "knowhow_sell": False, "knowhow_sell_strategy": "useless_only",
               "special_s1": True, "special_s2": True, "special_s3": True, "special_s4": True},
    "time_budget": {"max_minutes_per_run": 90, "max_total_minutes": 90},
    "caps": {"max_runs": 1, "max_stamina": 30, "max_skip_tickets": 0, "max_p_items": 0},
}

TOP_KEYS = set(DEFAULT_WEEKLY)


def idol_of(card_id: str | None) -> str | None:
    """Idol number encoded in a wiki card id (digits 4–6), or None."""
    return card_id[3:6] if isinstance(card_id, str) and len(card_id) == 10 else None


def duplicate_idols(card_ids) -> dict[str, list[str]]:
    """{idol number: [card ids]} for every idol that appears more than once among ``card_ids`` (None / "" ignored).
    A W.I.N.G. unit (P + 4 S + guest) must return {} — the game removes the other card of the same idol."""
    seen: dict[str, list[str]] = {}
    for card in card_ids:
        idol = idol_of(card) if card else None
        if idol:
            seen.setdefault(idol, []).append(card)
    return {idol: cards for idol, cards in seen.items() if len(cards) > 1}


def team_cards(team: Mapping[str, Any]) -> list:
    """[p_card, s1..s4, guest] (None for empty slots)."""
    return [team.get("p_card"), *(team.get("s_cards") or []), team.get("guest")]


def owned_missing(team: Mapping[str, Any], owned: Mapping[str, Any]) -> list[str]:
    """Registered team cards that are not in 持有卡牌 ({"p": [...], "s": [...]}); every slot incl. guest must be owned."""
    have = set(owned.get("p") or []) | set(owned.get("s") or [])
    return [card for card in team_cards(team) if card and card not in have]


def validate_team(value: Any) -> dict:
    """Strict check of the ``team`` section; returns a normalised copy. Duplicate card ids and the same idol twice
    (P / S / guest) are rejected."""
    team = _object(value, TEAM_KEYS, "team")
    _enum(team["mode"], TEAM_MODES, "team.mode")
    _enum(team["primary_stat"], TEAM_PRIMARY, "team.primary_stat")
    p_card = team["p_card"]
    if p_card is not None and not (isinstance(p_card, str) and P_CARD_ID.match(p_card)):
        raise ValueError("team.p_card 必须是 1 开头的 10 位卡 ID 或 null")
    slots = team["s_cards"]
    if not isinstance(slots, list) or len(slots) != S_SLOTS:
        raise ValueError(f"team.s_cards 必须是 {S_SLOTS} 项数组（空位为 null）")
    for card in [*slots, team["guest"]]:
        if card is not None and not (isinstance(card, str) and S_CARD_ID.match(card)):
            raise ValueError("team.s_cards / team.guest 必须是 2 开头的 10 位卡 ID 或 null")
    used = [c for c in [*slots, team["guest"]] if c]
    if len(set(used)) != len(used):
        raise ValueError("同一张 S 卡不能放在两个位置")
    clash = duplicate_idols([p_card, *slots, team["guest"]])
    if clash:
        raise ValueError(f"同一偶像不能在编队中出现两次（P/S/Guest）：{sorted(clash)}")
    return copy.deepcopy(dict(team))


def team_warnings(team: Mapping[str, Any]) -> list[str]:
    """Display-only notes about a team (never a runtime authorisation)."""
    notes = []
    if duplicate_idols(team_cards(team)):
        notes.append("same_idol_twice")
    if team.get("mode") == "manual" and not team.get("p_card"):
        notes.append("p_card_not_registered")
    if team.get("mode") == "manual" and not all(team.get("s_cards") or []):
        notes.append("s_cards_not_registered")
    return notes


# CardRecord fields a per-card feature source may supply (wing_team.CardRecord); ``id`` / ``kind`` come from the
# owned list. ``idol`` is deliberately not taken from feature sources: wing_team derives it from the card id for every
# card alike, so a name for some cards and a number for others can never hide a duplicate idol.
CARD_FEATURE_KEYS = ("name", "unit", "rarity", "level", "uncap", "te_flag", "live_skill_attr", "live_skill_mult",
                     "live_skill_name", "live_skill_extra", "passive_attr", "passive_pct", "passive_rate", "passive_cond",
                     "mastery_stamina", "mastery_sp", "mastery_cap", "mastery_stats", "audition_mastery", "events_value",
                     "stats", "p_cap_total", "locked")


def owned_card_records(owned: Mapping[str, Any], features: Mapping[str, Mapping[str, Any]] | None = None) -> list:
    """wing_team card mappings for 持有卡牌 ({"p": [...], "s": [...]}) plus whatever per-card features are known.
    Unknown features stay absent (wing_team treats them as unknown and lowers confidence; nothing is invented)."""
    features = features or {}
    out = []
    for kind, pattern in (("P", P_CARD_ID), ("S", S_CARD_ID)):
        for card in owned.get(kind.lower()) or []:
            if not (isinstance(card, str) and pattern.match(card)):
                raise ValueError(f"持有卡牌含无效 {kind} 卡 ID：{card!r}")
            extra = features.get(card) if isinstance(features.get(card), Mapping) else {}
            out.append({"id": card, "kind": kind, **{k: extra[k] for k in CARD_FEATURE_KEYS if k in extra}})
    return out


def auto_team_plan(team: Mapping[str, Any], owned: Mapping[str, Any] | None = None, scorer=None,
                   features: Mapping[str, Mapping[str, Any]] | None = None, primary: str | None = None,
                   recommender: Mapping[str, Any] | None = None) -> dict:
    """自动编队: propose P card + 4 starters + guest from 持有卡牌.

    Default engine: ``team_recommend.recommend`` — the True End recommender over the owned cards with
    known Lv/凸 (``owned["details"]``), falling back to ``wing_team.select_team(cards, primary)`` with a
    ``fallback_reason``. ``recommender`` passes ``cards`` / ``id_map`` / ``settings`` / ``fallback`` through (tests).
    The plan carries ``engine``, ``fallback_reason``, ``recommender`` (summary or None), ``coverage`` and
    ``needs_parsing``.

    Never a runtime authorisation and never the game's recommended team: in ``auto`` mode the result always stops the
    run. A proposal is shown to the user, who accepts it into the slots (the team becomes ``manual``); only then can a
    run start. ``scorer(cards, primary)`` defaults to ``wing_team.select_team`` (returns a TeamSelection or a dict
    with ``status`` / ``p_card`` / ``starters`` / ``guest``). Reasons:

    * ``manual_team`` (stop False): nothing to propose;
    * ``owned_cards_unknown``: 持有卡牌 has no S cards (or no P cards);
    * ``team_needs_confirmation``: a full, rule-valid proposal exists (``status`` ok or needs_confirmation);
    * ``auto_team_rejected``: no team satisfies the hard rules with the known features (e.g. levels / TE flags unread);
    * ``auto_team_invalid_proposal``: the scorer's team breaks the unit rules (same idol, not owned, incomplete)."""
    base = {"stop": True, "status": None, "proposal": None, "confidence": None, "reasons": [], "candidates": [],
            "engine": None, "fallback_reason": None, "recommender": None}
    if team.get("mode") != "auto":
        return {**base, "stop": False, "reason": "manual_team"}
    if not owned or not owned.get("s") or not owned.get("p"):
        return {**base, "reason": "owned_cards_unknown"}
    stat = team.get("primary_stat")
    # 推荐编队: ``p_card == "auto"`` (or ``pick_p``) = Maa picks the P card too; with 主属性 「自动」 each P
    # card is scored with its own specialty and the plan's ``primary`` is the winning team's.
    pick = team.get("pick_p") is True or team.get("p_card") == "auto"
    if pick and stat == "auto":
        stat_arg = "auto"
        stat = "Vo"
    elif stat == "auto" and team.get("p_card"):
        # 推荐编队 with the player's P (2026-10-14 user): 主属性 「自动」 = that P card's specialty
        stat = card_specialty(team["p_card"]) or (primary if primary in PRIMARY_STATS else "Vo")
        stat_arg = stat
    else:
        stat = stat if stat in PRIMARY_STATS else (primary if primary in PRIMARY_STATS else "Vo")
        stat_arg = stat
    if scorer is None:
        # 2026-10-06: the True End recommender is the preferred engine; team_recommend falls back to
        # wing_team.select_team (with ``fallback_reason``) when its data is missing.
        from agent.core.team_recommend import recommend
        fixed = None if pick else (team.get("p_card") or None)
        info = recommend(owned, stat_arg, features=features,
                         **{**({"p_card": fixed} if fixed else {}), **({"pick_p": True} if pick else {}),
                            **(recommender or {})})
        if info.get("primary") in PRIMARY_STATS:
            stat = info["primary"]
    else:
        result = scorer(owned_card_records(owned, features), stat)
        info = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        info.setdefault("engine", "scorer")
    status = info.get("status")
    out = {**base, "status": status, "primary": stat, "confidence": info.get("confidence"),
           "reasons": list(info.get("reasons") or []), "rejected_cards": dict(info.get("rejected_cards") or {}),
           "engine": info.get("engine"), "fallback_reason": info.get("fallback_reason"),
           "recommender": info.get("summary"), "candidates": list(info.get("candidates") or []),
           "coverage": dict(info.get("coverage") or {}),
           "needs_parsing": list(info.get("needs_parsing") or []), "notes": list(info.get("notes") or []),
           "card_gaps": list(info.get("card_gaps") or []), "gap_counts": dict(info.get("gap_counts") or {}),
           "library": info.get("library"), "assume": dict(info.get("assume") or {}),
           "training_hints": list(info.get("training_hints") or [])}
    starters = list(info.get("starters") or [])
    if status not in ("ok", "needs_confirmation") or len(starters) != S_SLOTS or not info.get("p_card") \
            or not info.get("guest"):
        return {**out, "reason": "auto_team_rejected"}
    if team.get("p_card") and not pick and info["p_card"] != team["p_card"]:
        # U05: a recommendation never replaces the P card the user chose (the fallback engine may pick another P)
        return {**out, "reason": "auto_team_rejected", "reasons": out["reasons"] + ["selected_p_not_recommended"]}
    proposal = {"mode": "manual", "p_card": info["p_card"], "s_cards": starters, "guest": info["guest"],
                "primary_stat": stat}
    try:
        validate_team(proposal)
    except ValueError as exc:
        return {**out, "reason": "auto_team_invalid_proposal", "reasons": out["reasons"] + [str(exc)]}
    if owned_missing(proposal, owned):
        return {**out, "reason": "auto_team_invalid_proposal", "reasons": out["reasons"] + ["not_owned"]}
    return {**out, "reason": "team_needs_confirmation", "proposal": proposal,
            "candidates": out["candidates"] or [proposal]}


def run_preflight(config: "ProduceConfig", owned: Mapping[str, Any] | None = None,
                  features: Mapping[str, Mapping[str, Any]] | None = None, scorer=None,
                  policy: Mapping[str, Any] | None = None) -> dict:
    """Checks before a Produce run may start (pure; no device). ``ok`` False = the run is refused with ``reasons``.

    自动编队 is always refused with ``team_needs_confirmation`` (or the auto plan's own reason) until the user accepts
    the proposal into the slots; a registered manual team must not leave 持有卡牌 when the list is known; resource
    settings the project policy does not permit refuse the run (``resource_reasons``; ``policy`` None = read
    project_policy.json). This adds to, and never replaces, project_policy / SpendGate / the action table."""
    reasons = []
    team = config.team
    plan = auto_team_plan(team, owned, scorer=scorer, features=features,
                          primary=(config.manual or {}).get("primary_stat"))
    if plan["stop"]:
        reasons.append(plan["reason"])
    if team.get("mode") == "manual" and owned and owned_missing(team, owned):
        reasons.append("team_card_not_owned")
    reasons += resource_reasons(config, policy)
    return {"ok": not reasons, "reasons": reasons, "team_plan": plan}


def load_project_policy(path=None) -> dict:
    """``project_policy.json`` of this repository (read-only)."""
    from pathlib import Path
    return load_json(path or Path(__file__).resolve().parents[2] / "project_policy.json")


def resource_reasons(config: "ProduceConfig", policy: Mapping[str, Any] | None = None) -> list[str]:
    """Resource uses the config asks for that ``project_policy.json`` does not permit (each one refuses the run).

    Jewels / paid items / recovery soda are already rejected by ``from_dict``; P items and the continue ticket are
    inventory items (``resource_usage.other_inventory_allowed``). No W.I.N.G. setting uses stamina items or Fes
    tickets. A setting is never authorisation."""
    usage = (policy if policy is not None else load_project_policy()).get("resource_usage") or {}
    manual = config.manual or {}
    out = []
    if manual.get("use_p_items") and usage.get("other_inventory_allowed") is not True:
        out.append("p_items_blocked_by_policy")
    if manual.get("use_p_items") and P_ITEMS_NEED_MIDRUN_USE & set(manual.get("p_items") or ()):
        out.append("p_items_midrun_use_unsupported")
    if manual.get("use_continue_ticket") and usage.get("other_inventory_allowed") is not True:
        out.append("continue_ticket_blocked_by_policy")
    if usage.get("premium_currency_allowed") is not False or usage.get("paid_items_allowed") is not False:
        out.append("policy_paid_resources_not_false")   # the baseline must keep both false; never run otherwise
    return out


def _number(value: Any, lo: float, hi: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ValueError(f"{field} 必须是 {lo}..{hi} 之间的数")
    return float(value)


def _validate_wing_settings(manual: Mapping[str, Any]) -> None:
    """W.I.N.G. T.E. settings in ``manual`` (all optional; MANUAL_OPTIONAL gives the defaults)."""
    get = lambda key: manual.get(key, MANUAL_OPTIONAL[key])  # noqa: E731
    _enum(get("audition_style"), AUDITION_STYLES, "manual.audition_style")
    _enum(get("fallback"), FALLBACKS, "manual.fallback")
    _enum(get("sp_order"), SP_ORDERS, "manual.sp_order")
    _enum(get("promise"), PROMISE_MODES, "manual.promise")
    _enum(get("knowhow_sell_strategy"), KNOWHOW_SELL_STRATEGIES, "manual.knowhow_sell_strategy")
    rate = get("rest_trouble_rate")
    if rate is not None:
        _number(rate, *REST_TROUBLE_RANGE, "manual.rest_trouble_rate")
    margin = _int(get("cap_margin"), "manual.cap_margin")
    if not CAP_MARGIN_RANGE[0] <= margin <= CAP_MARGIN_RANGE[1]:
        raise ValueError(f"manual.cap_margin 必须在 {CAP_MARGIN_RANGE[0]}..{CAP_MARGIN_RANGE[1]}")
    for key in END_BOOL_KEYS + SPECIAL_KEYS:
        if type(get(key)) is not bool:
            raise ValueError(f"manual.{key} 必须为布尔值")
    tuning = get("tuning")
    if not isinstance(tuning, Mapping):
        raise ValueError("manual.tuning 必须是对象")
    unknown = set(tuning) - set(TUNING_RANGES)
    if unknown:
        raise ValueError(f"manual.tuning 含未知参数：{sorted(unknown)}")
    for key, value in tuning.items():
        _number(value, *TUNING_RANGES[key], f"manual.tuning.{key}")


# Carrier attach keys of the W.I.N.G. T.E. options (assets/tasks/auto_produce.json -> SC.Produce.Config.Wing.*).
# They only apply to the manual goals (true_end / fans); the official-Auto weekly goal ignores them.
WING_OPTION_KEYS = ("live_mode", "audition_style", "rest_trouble_rate", "fallback", "sp_order", "cap_margin", "promise",
                    "event_default_choice", "p_items", "knowhow_sell_strategy") + END_BOOL_KEYS + SPECIAL_KEYS


def _apply_wing_options(data: dict, attach: Mapping[str, Any]) -> None:
    """Copy the W.I.N.G. T.E. option attach into ``data["manual"]`` (validated later by ``from_dict``)."""
    manual = data["manual"]
    for key in WING_OPTION_KEYS:
        if key in attach and key != "p_items":
            manual[key] = attach[key]
    preset = attach.get("p_items", "none")
    if not isinstance(preset, str) or preset not in P_ITEM_PRESETS:
        raise ValueError(f"p_items 必须为 {tuple(P_ITEM_PRESETS)} 之一")
    items = list(P_ITEM_PRESETS[preset])
    manual.update(use_p_items=bool(items), p_items=items)
    data["caps"]["max_p_items"] = len(items)
    tuning = {}
    for key in TUNING_RANGES:
        if f"tune_{key}" in attach:
            tuning[key] = attach[f"tune_{key}"]
    manual["tuning"] = tuning


def wing_params(config: "ProduceConfig") -> dict:
    """The manual W.I.N.G. settings as the parameters of each pure decision module (no input, no authorisation):

    * ``policy``: ``wing_policy.decide(..., params=...)`` / ``WingNavRuntime(policy_params=...)`` — ``live_mode``,
      the rest threshold, ``score_params`` (``wing_score.ScoreParams`` overrides) and ``special_seasons``;
    * ``live``: ``wing_audition.live_plan(..., params=...)`` / ``should_use_game_auto`` (``LiveParams`` overrides);
    * ``sp``: ``wing_sp.plan_sp(..., params=...)``;
    * ``event``: ``wing_events.choose_event_option(..., default_index=..., accept_promises=...)``;
    * ``items``: P items to bring (empty unless opted in; ``resource_reasons`` still refuses them under the policy).

    A default config yields exactly the library defaults (tests/test_wing_settings.py)."""
    manual = config.manual
    if not isinstance(manual, Mapping):
        raise ValueError("weekly_count 使用官方 Auto，没有 W.I.N.G. 参数")
    get = lambda key: manual.get(key, MANUAL_OPTIONAL[key])  # noqa: E731
    tuning = dict(get("tuning"))
    score = {k: float(v) for k, v in tuning.items() if k not in TUNING_LIVE_KEYS}
    score.update(fallback=get("fallback"), promise_weight=PROMISE_KEEP_WEIGHT if get("promise") == "keep" else 1.0)
    policy = {"live_mode": get("live_mode"), "score_params": score,
              "special_seasons": tuple(i + 1 for i, key in enumerate(SPECIAL_KEYS) if get(key))}
    rate = get("rest_trouble_rate")
    if rate is not None:
        policy["trouble_rest_rate"] = score["trouble_rest_rate"] = float(rate)
    live = {k: float(v) for k, v in tuning.items() if k in TUNING_LIVE_KEYS}
    if get("audition_style") == "attribute":
        live["focus_bonus"] = 0.0
    return {
        "policy": policy,
        "live": live,
        "sp": {"cap_margin": get("cap_margin"), "tier_order": wsp.TIER_PRESETS[get("sp_order")],
               "spend_down_last_chance": get("sp_spend_down")},
        # 獲得報酬 handling (wing_nav reward review; produce_end.EndGateConfig.pause_at_reward = not reward_ok).
        "end": {"read": get("reward_read"), "protect_lv5": get("reward_protect_lv5"), "reroll": get("reward_reroll"),
                "press_ok": get("reward_ok"),
                # new books after the run (knowhow_keep.KeepSettings); no executor yet
                "keep": {"lock": get("knowhow_lock"), "sell": get("knowhow_sell"),
                         "strategy": get("knowhow_sell_strategy")}},
        "event": {"default_index": get("event_default_choice"), "accept_promises": get("accept_promises")},
        "items": list(manual.get("p_items") or []) if manual.get("use_p_items") else [],
    }


def default_config(goal: str = "weekly_count") -> dict:
    """Deep copy of the default for ``goal`` (``fans`` reuses the True End manual defaults)."""
    if goal == "weekly_count":
        return copy.deepcopy(DEFAULT_WEEKLY)
    data = copy.deepcopy(DEFAULT_TRUE_END)
    data["goal"] = _enum(goal, GOALS, "goal")
    return data


# 2026-10-11: carrier keys that are launcher run options, not ProduceConfig fields (``launch_options``):
# ``unit_search`` = 「自动找含此 P 的编队」 (wing_nav.RunOptions.unit_search, start mode, ユニット1–20; 用推荐 forces it, tools/ui_wing.py).
# ``sp_mode`` = 「SP 加点」 不加点/交给我 (wing_nav.RunOptions.sp_mode skip/handover; auto otherwise), 2026-10-11 review P2-5.
# ``minigame`` = 「开局前大富豪」 关/自动/每次 (off/auto/always; 2026-10-14): tools/ui_wing.py turns it into
# wing_nav.RunOptions.minigame="before_start" for a new Produce (auto = only when the adopted recommendation scores low).
# ``settings_fix`` = 「自动改游戏设置」 (2026-10-25, default off): wing_nav.RunOptions.settings_fix, any mode.
MINIGAME_PREFS = ("off", "auto", "always")
LAUNCH_OPTION_KEYS = ("unit_search", "sp_mode", "minigame", "settings_fix", "scenario", "halo_book")
# 剧本 on the 「Produce」 tab (H2c batch 5, 2026-11-04): W.I.N.G. or say "Halo" (book; same P idol and team). say "Halo"
# runs the dig route only (「默认不要强fes线 只做掘书线」).
SCENARIOS = ("wing", "halo")
HALO_ROUTE = "dig"
HALO_BOOKS = ("auto", "history", "preset1", "preset2", "target")   # = agent/core/wing_nav.HALO_BOOKS
SP_MODES = ("auto", "skip", "handover")


def launch_options(attach: Mapping[str, Any]) -> dict:
    """Launcher options carried by the AutoProduce task options (see ``LAUNCH_OPTION_KEYS``); validated, never a grant."""
    value = attach.get("unit_search", False)
    if type(value) is not bool:
        raise ValueError("unit_search 必须为布尔值")
    sp_mode = attach.get("sp_mode", "auto")
    if sp_mode not in SP_MODES:
        raise ValueError("sp_mode 只能为 auto/skip/handover")
    minigame = attach.get("minigame", "off")
    if minigame not in MINIGAME_PREFS:
        raise ValueError("minigame 只能为 off/auto/always")
    settings_fix = attach.get("settings_fix", False)
    if type(settings_fix) is not bool:
        raise ValueError("settings_fix 必须为布尔值")
    scenario = attach.get("scenario", "wing")
    if scenario not in SCENARIOS:
        raise ValueError("scenario 只能为 wing/halo")
    halo_book = attach.get("halo_book", "auto")
    if halo_book not in HALO_BOOKS:
        raise ValueError("halo_book 只能为 " + "/".join(HALO_BOOKS))
    return {"unit_search": value, "sp_mode": sp_mode, "minigame": minigame, "settings_fix": settings_fix,
            "scenario": scenario, "halo_route": HALO_ROUTE, "halo_book": halo_book}


SPECIALTY_STATS = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi"}


def card_specialty(card_id) -> str | None:
    """Main Vo/Da/Vi specialty of a P card from the local card manifest (``ui/game-assets/manifest.json``): the
    first listed attribute (「Visual & Dance」 -> Vi). None for 「Balanced」 or an unknown card/specialty."""
    if not card_id:
        return None
    from agent.core.wing_nav import card_info
    m = re.match(r"\s*(Vocal|Dance|Visual)\b", str(card_info(str(card_id)).get("specialty") or ""))
    return SPECIALTY_STATS[m.group(1)] if m else None


def card_info_known(card_id) -> bool:
    if not card_id:
        return False
    from agent.core.wing_nav import card_info
    return bool(card_info(str(card_id)))


@dataclasses.dataclass(frozen=True)
class ProduceConfig:
    data: Mapping[str, Any]

    @property
    def goal(self) -> str:
        return self.data["goal"]

    @property
    def manual(self) -> Mapping[str, Any] | None:
        return self.data["manual"]

    @property
    def weekly(self) -> Mapping[str, Any] | None:
        return self.data["weekly"]

    @property
    def caps(self) -> Mapping[str, Any]:
        return self.data["caps"]

    @property
    def stamina_per_run(self) -> int:
        return STAMINA_PER_RUN * self.data["stamina_multiplier"]

    @property
    def team(self) -> Mapping[str, Any]:
        return self.data["team"]

    @classmethod
    def from_task_options(cls, attach: Mapping[str, Any]) -> "ProduceConfig":
        """Config from the merged ``attach`` of the ``SC.Produce.Config.*`` carrier nodes (PI options of the
        AutoProduce task). Card fields use "" for an empty slot. The result is validated by
        ``from_dict``; it stays ``enabled=False`` — selecting options never authorises a run."""
        known = {"goal", "difficulty", "max_runs", "team_mode", "unit_preset", "primary_stat", "p_card", "guest",
                 *(f"s{i}" for i in range(1, S_SLOTS + 1)), *WING_OPTION_KEYS, *(f"tune_{k}" for k in TUNING_RANGES),
                 *LAUNCH_OPTION_KEYS}
        unknown = set(attach) - known
        if unknown:
            raise ValueError(f"Produce 选项载体含未知字段：{sorted(unknown)}")
        # 2026-10-08: the AutoProduce task defaults to True End (周常 moved to the WeeklyProduce task, which passes
        # goal="weekly_count" explicitly).
        goal = _enum(attach.get("goal", "true_end"), GOALS, "goal")
        data = default_config(goal)
        runs = _int(attach.get("max_runs", data["caps"]["max_runs"]), "max_runs", positive=True)
        data["difficulty"] = _enum(attach.get("difficulty", data["difficulty"]), DIFFICULTIES, "difficulty")
        data["allow_master"] = data["difficulty"] == "master"  # choosing Master in the UI is the explicit opt-in
        data["unit_preset"] = _str(attach.get("unit_preset", data["unit_preset"]), "unit_preset")
        stat = _enum(attach.get("primary_stat", "auto"), TEAM_PRIMARY, "primary_stat")
        # U02: 「自动」 = the selected P card's specialty (card manifest), not a silent Vo.
        resolved = stat if stat != "auto" else card_specialty(attach.get("p_card") or None)
        # A card known to the local manifest without a single main specialty (Balanced) needs an explicit choice; an
        # unknown card (no local manifest, e.g. a clean install) keeps the Vo default and the UI says so.
        if resolved is None and goal != "weekly_count" and card_info_known(attach.get("p_card")):
            raise ValueError("主属性「自动」无法从这张 P 卡判断（特长为均衡或未知），请在主属性里选 Vo/Da/Vi")
        data["caps"].update(max_runs=runs, max_stamina=STAMINA_PER_RUN * runs)
        if goal == "weekly_count":
            data["caps"]["max_skip_tickets"] = min(runs, SKIP_TICKETS_PER_DAY)
        else:
            data["time_budget"]["max_total_minutes"] = data["time_budget"]["max_minutes_per_run"] * runs
            if resolved is not None:
                data["manual"].update(strategy=f"{resolved.lower()}_score", primary_stat=resolved)
            _apply_wing_options(data, attach)

        def card(field):
            value = attach.get(field, "")
            if value in ("", None):
                return None
            return _str(value, field)

        data["team"] = {"mode": _enum(attach.get("team_mode", "manual"), TEAM_MODES, "team_mode"),
                        "p_card": card("p_card"), "s_cards": [card(f"s{i}") for i in range(1, S_SLOTS + 1)],
                        "guest": card("guest"), "primary_stat": stat}
        return cls.from_dict(data)

    @classmethod
    def load(cls, path) -> "ProduceConfig":
        return cls.from_dict(load_json(path))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProduceConfig":
        data = dict(_object(value, TOP_KEYS | set(OPTIONAL_TOP), "produce", required=False))
        missing = TOP_KEYS - set(data)
        if missing:
            raise ValueError(f"produce 缺少字段：{sorted(missing)}")
        data["team"] = validate_team(data.get("team", OPTIONAL_TOP["team"]))
        if _int(data["version"], "version") != 1:
            raise ValueError("仅支持 Produce 配置 version=1")
        _bool(data["enabled"], "enabled")
        goal = _enum(data["goal"], GOALS, "goal")
        _enum(data["scenario"], SCENARIOS, "scenario")
        _enum(data["unit_preset"], UNIT_PRESETS, "unit_preset")
        difficulty = _enum(data["difficulty"], DIFFICULTIES, "difficulty")
        allow_master = _bool(data["allow_master"], "allow_master")
        if difficulty == "master" and not allow_master:
            raise ValueError("Master 难度需显式 allow_master=true")
        multiplier = _int(data["stamina_multiplier"], "stamina_multiplier", positive=True)
        if multiplier not in (1, 2, 3):
            raise ValueError("stamina_multiplier 只能为 1/2/3")
        if _bool(data["premium_currency_allowed"], "premium_currency_allowed"):
            raise ValueError("付费货币（ジュエル）始终禁止")
        if _bool(data["recovery_soda_allowed"], "recovery_soda_allowed"):
            raise ValueError("回复苏打始终禁止")

        budget = _object(data["time_budget"], {"max_minutes_per_run", "max_total_minutes"}, "time_budget")
        per_run = _int(budget["max_minutes_per_run"], "time_budget.max_minutes_per_run", positive=True)
        if _int(budget["max_total_minutes"], "time_budget.max_total_minutes", positive=True) < per_run:
            raise ValueError("time_budget.max_total_minutes 不得小于单局上限")
        caps = _object(data["caps"], {"max_runs", "max_stamina", "max_skip_tickets", "max_p_items"}, "caps")
        max_runs = _int(caps["max_runs"], "caps.max_runs", positive=True)
        if max_runs > AUTO_RUN_LIMIT:
            raise ValueError(f"caps.max_runs 不得超过 {AUTO_RUN_LIMIT}")
        if _int(caps["max_stamina"], "caps.max_stamina") < STAMINA_PER_RUN * multiplier:
            raise ValueError("caps.max_stamina 不足一局的体力（30×倍率）")
        tickets = _int(caps["max_skip_tickets"], "caps.max_skip_tickets")
        if tickets > SKIP_TICKETS_PER_DAY:
            raise ValueError(f"caps.max_skip_tickets 不得超过每日上限 {SKIP_TICKETS_PER_DAY}")
        max_items = _int(caps["max_p_items"], "caps.max_p_items")
        if max_items > MAX_P_ITEMS:
            raise ValueError(f"caps.max_p_items 不得超过 {MAX_P_ITEMS}")

        if goal == "weekly_count":
            if data["manual"] is not None:
                raise ValueError("weekly_count 使用官方 Auto，manual 必须为 null")
            weekly = _object(data["weekly"], {"auto_produce", "use_skip_tickets", "target_count"}, "weekly")
            if not _bool(weekly["auto_produce"], "weekly.auto_produce"):
                raise ValueError("周常目前只支持官方 Auto（諦める 路线尚未实现）")
            if difficulty != "middle":
                raise ValueError("W.I.N.G. 的 Auto 仅支持 Middle")
            target = _int(weekly["target_count"], "weekly.target_count", positive=True)
            if target > 3:
                raise ValueError("weekly.target_count 周常目标为 3")
            if max_runs > target:
                raise ValueError("caps.max_runs 不得超过周常目标次数")
            if _bool(weekly["use_skip_tickets"], "weekly.use_skip_tickets"):
                if tickets < 1:
                    raise ValueError("use_skip_tickets=true 时 caps.max_skip_tickets 至少为 1")
            elif tickets:
                raise ValueError("未开启跳过券时 caps.max_skip_tickets 必须为 0")
            if max_items:
                raise ValueError("Auto 周常不带 P 道具：caps.max_p_items 必须为 0")
        else:
            if data["weekly"] is not None:
                raise ValueError(f"{goal} 为手动 W.I.N.G.，weekly 必须为 null")
            if tickets:
                raise ValueError("手动 W.I.N.G. 不使用跳过券：caps.max_skip_tickets 必须为 0")
            if goal == "true_end":
                if difficulty == "beginner":
                    raise ValueError("True End 需要 Middle 或 Master")
                if multiplier != 1:
                    raise ValueError("True End 只用 1 倍体力")
            manual = _object(data["manual"], MANUAL_KEYS | set(MANUAL_OPTIONAL), "manual", required=False)
            missing = MANUAL_KEYS - set(manual)
            if missing:
                raise ValueError(f"manual 缺少字段：{sorted(missing)}")
            _enum(manual.get("live_mode", "policy"), LIVE_MODES, "manual.live_mode")
            choice = _int(manual.get("event_default_choice", 0), "manual.event_default_choice")
            if choice > MAX_EVENT_CHOICES - 1:
                raise ValueError(f"manual.event_default_choice 必须在 0..{MAX_EVENT_CHOICES - 1}")
            _bool(manual.get("accept_promises", True), "manual.accept_promises")
            _validate_wing_settings(manual)
            strategy = _enum(manual["strategy"], tuple(STRATEGIES), "manual.strategy")
            expected = STRATEGIES[strategy].primary
            primary = manual["primary_stat"]
            if expected is None:
                if primary is not None:
                    raise ValueError("balanced 策略的 manual.primary_stat 必须为 null")
            elif _enum(primary, PRIMARY_STATS, "manual.primary_stat") != expected:
                raise ValueError(f"manual.primary_stat 与策略 {strategy} 的主属性 {expected} 不一致")
            items = manual["p_items"]
            if not isinstance(items, list):
                raise ValueError("manual.p_items 必须是数组")
            names = [_str(item, "manual.p_items[]") for item in items]
            if len(set(names)) != len(names):
                raise ValueError("manual.p_items 不得重复")
            paid = [n for n in names if any(m.lower() in n.lower() for m in PAID_ITEM_MARKERS)]
            if paid:
                raise ValueError(f"付费道具/货币始终禁止：{paid}")
            if _bool(manual["use_p_items"], "manual.use_p_items"):
                if not names or len(names) > max_items:
                    raise ValueError("use_p_items=true 时须列出 1..caps.max_p_items 个道具")
            elif names or max_items:
                raise ValueError("use_p_items=false 时 p_items 必须为空且 caps.max_p_items=0")
            _bool(manual["skip_communications"], "manual.skip_communications")
            _enum(manual["risk_level"], RISK_LEVELS, "manual.risk_level")
            _bool(manual["use_continue_ticket"], "manual.use_continue_ticket")
            stat = data["team"]["primary_stat"]
            if stat != "auto" and primary is not None and stat != primary:
                raise ValueError(f"team.primary_stat {stat} 与 manual.primary_stat {primary} 不一致")
        return cls(copy.deepcopy(dict(data)))


@dataclasses.dataclass(frozen=True)
class WeeklyPlan:
    runs: int
    stamina: int
    skip_tickets: int
    minutes: int
    stop: bool
    reasons: tuple

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"reasons": list(self.reasons)}


def plan_weekly(config: ProduceConfig, *, completed: int | None, target_on_screen: int | None,
                stamina: int | None, tickets_held: int | None, tickets_used_today: int | None) -> WeeklyPlan:
    """How many Auto runs to start now for the weekly mission (pure; every input is a fresh screen reading).

    Any unknown input stops. Runs are limited by the remaining count, ``caps.max_runs``, ``caps.max_stamina``, the
    current stamina, and (with skip tickets) min(held, daily remaining, ``caps.max_skip_tickets``). Without skip
    tickets each run takes ~1 hour and must fit ``time_budget.max_total_minutes``."""
    def stop(*reasons):
        return WeeklyPlan(0, 0, 0, 0, True, reasons)

    if config.goal != "weekly_count":
        return stop("not_weekly_goal")
    weekly, caps = config.weekly, config.caps
    if target_on_screen is None or completed is None:
        return stop("mission_progress_unread")
    if target_on_screen != weekly["target_count"]:
        return stop(f"mission_target_mismatch_{target_on_screen}")
    if not 0 <= completed <= target_on_screen:
        return stop("mission_progress_out_of_range")
    if completed == target_on_screen:
        return WeeklyPlan(0, 0, 0, 0, False, ("already_complete",))
    if stamina is None:
        return stop("stamina_unread")
    reasons = []
    runs = min(target_on_screen - completed, caps["max_runs"])
    per_run = config.stamina_per_run
    by_stamina = min(stamina, caps["max_stamina"]) // per_run
    if by_stamina < runs:
        reasons.append(f"limited_by_stamina_{stamina}")
        runs = by_stamina
    tickets = 0
    if weekly["use_skip_tickets"]:
        if tickets_held is None or tickets_used_today is None:
            return stop("skip_tickets_unread")
        available = min(tickets_held, max(0, SKIP_TICKETS_PER_DAY - tickets_used_today), caps["max_skip_tickets"])
        if available < runs:
            reasons.append(f"limited_by_skip_tickets_{available}")
            runs = available
        tickets = runs
        minutes = runs * config.data["time_budget"]["max_minutes_per_run"]
    else:
        minutes = runs * AUTO_RUN_MINUTES
        budget = config.data["time_budget"]["max_total_minutes"]
        if minutes > budget:
            runs = budget // AUTO_RUN_MINUTES
            minutes = runs * AUTO_RUN_MINUTES
            reasons.append(f"limited_by_time_budget_{budget}")
    if runs <= 0:
        return stop(*(reasons or ["no_runs_possible"]))
    return WeeklyPlan(runs, runs * per_run, tickets, minutes, False, tuple(reasons) or ("ok",))

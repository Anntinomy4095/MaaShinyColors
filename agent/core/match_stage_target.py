"""Match Live stage targets by reward attribute (2026-10-09, user: 「最新 Vo / Da / Vi 道具关卡 + 自定义」). Pure, no input.

The skip dialog lists stages as 「エリアN ステージM」 where M is the global stage number (エリア2 ステージ6 is stage 6).
Each stage's clear reward is fixed. Reward table: wikiwiki.jp/shinycolors 「MATCH LIVE」
(“2023/09-” clear-reward table, read 2026-10-09), **not yet confirmed on native frames**:

* ボーカル用 / ダンス用 / ビジュアル用 ペンライト【銅】 (+ 手紙【銅】 from area 2) rotate Vo → Da → Vi over stages
  1–6 and 13–21 (1/4/13/16/19 = Vo, 2/5/14/17/20 = Da, 3/6/15/18/21 = Vi);
* stages 7–12 and 22–25 give Leader用 / Center用 items (no attribute).

A target ``latest_vo`` / ``latest_da`` / ``latest_vi`` means: among the player's trusted complete progress, the stage
with the highest ``stage_order`` whose reward attribute is that one. It is the user's explicit choice of an attribute,
not a fallback: when that stage is not skippable (or not seen in the complete list) the task STOPS — it never takes an
older stage of the same attribute and never another attribute.

Because the table is research, not native evidence, ``REWARD_TABLE_VERIFIED`` is False and a live resolution stops
with ``stage_reward_unverified`` until the dialog's 獲得報酬 icons (or the stage detail) have been sampled and the table
confirmed. Tests pass ``verified=True`` explicitly.
"""
from __future__ import annotations

ATTRIBUTES = ("vo", "da", "vi")
TARGETS = {f"latest_{a}": a for a in ATTRIBUTES}
REWARD_TABLE_SOURCE = "wikiwiki.jp/shinycolors MATCH LIVE (2023/09- clear rewards), read 2026-10-09"
REWARD_TABLE_VERIFIED = False   # flip only with native evidence (skip-dialog reward icons) and a replay test

# Global stage number -> reward attribute ("vo" / "da" / "vi"), or "role" (Leader用 / Center用 items, no attribute).
STAGE_REWARD = {
    1: "vo", 2: "da", 3: "vi", 4: "vo", 5: "da", 6: "vi",
    **{n: "role" for n in range(7, 13)},
    13: "vo", 14: "da", 15: "vi", 16: "vo", 17: "da", 18: "vi", 19: "vo", 20: "da", 21: "vi",
    **{n: "role" for n in range(22, 26)},
}
# Area of each global stage number (3 per area, EX = area 8 with 22–25).
STAGE_AREA = {n: min(8, (n - 1) // 3 + 1) for n in STAGE_REWARD}


def attribute(stage):
    """Reward attribute of an ``(area, stage)`` pair; None when the pair is unknown or inconsistent."""
    area, number = stage
    if STAGE_AREA.get(number) != area:
        return None
    return STAGE_REWARD.get(number)


def resolve(target, progress_stages, seen, verified=REWARD_TABLE_VERIFIED):
    """``(stage, None)`` or ``(None, outcome)`` for a ``latest_<attr>`` target.

    ``progress_stages``: trusted complete progress records (``stage``, ``stage_order``, ``skip_available``);
    ``seen``: ``{(area, stage): plus_enabled}`` from the complete scan of the skip dialog.
    """
    if target not in TARGETS:
        raise ValueError(f"未知 Match 目标: {target}")
    if not verified:
        return None, "stage_reward_unverified"
    want = TARGETS[target]
    records = [r for r in progress_stages if attribute(r.stage) == want]
    if not records:
        return None, "attribute_stage_unavailable"
    latest = max(records, key=lambda r: r.stage_order)
    if not latest.skip_available or not seen.get(latest.stage, False):
        return None, "latest_attribute_not_skippable"
    return latest.stage, None

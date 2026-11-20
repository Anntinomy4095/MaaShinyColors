"""W.I.N.G. Live card play: the user's trend-#1 focus habit and a secured-1st estimate (pure, no input).

User habit (2026-10-05, confirmed by every own-play turn of the second demo): attack the
trend-#1 judge (★2倍) to take its Top Appeal and secure 1st place when the stats suffice; when that judge has left, the
next best trend. The numeric expected-star scorer (``wing_audition.decide``) needs each card's multiplier and every
judge's interest, which the Live screen does not show before a card is selected, so it reports low confidence on
almost every turn. This module decides from what the screen does show:

* judges present / left and their trend rank (★2倍 / ★1.5倍 badges);
* the hand's attribute labels; a card's multiplier and appeal value come from the tooltip shown after selecting it
  (「Vocal3.5倍アピール…」, 「1315+80%」). Multipliers are cached per card picture (``card_signature``), so a turn
  needs at most ``MAX_SWITCHES`` extra selections and usually none once the deck is known;
* the pre-audition estimate (``wing_audition.audition_win_estimate`` via the week decision): its ``plan`` says
  whether the trend-#1 focus or the own-attribute plan is the better bet ("stats suffice").

Card value = multiplier × appeal (or our stat of the card's attribute) × (2 when the card attribute matches the
target judge, the Excellent bonus; an 「Excellent」 card, attribute "All", always gets it and uses the target judge's
stat like ``wing_audition``). Memory appeal (思い出) is never chosen: its 「Good/Bad」 strip flow is not sampled.

``secured_first``: 1st place is certain when our stars exceed every visible rival's stars plus everything that rival
could still win (each judge still present: Top + Last Appeal + all three bonus thresholds). Only then may the game's
Auto finish the Live (and only with the user's ``game_auto`` opt-in, enforced by the caller).

Limited auditions (``special_rule`` = ``wing_special.Special.live``): "trend2" (S3 めざせアイドルNo.1, judges +100 %
every 2 turns; guide: attack trend 1 only when it is the primary's own trend, else spear trend 2 for its Top
Appeal). The caller hands "game_auto" rules to the game's Auto before any turn is planned here.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

ATTRIBUTES = ("Vo", "Da", "Vi")
OTHER = "Other"                     # green-sparkle starter card (メンタル 5% 回復): read, never played
ALL = "All"                         # 「Excellent」 appeal card (wing_screen.live_hand): Excellent on any judge
CARD_ATTRIBUTES = ATTRIBUTES + (ALL,)
STARS = {1: {"ta": 20, "la": 8, "bonus": 4}, 2: {"ta": 15, "la": 6, "bonus": 3}, 3: {"ta": 10, "la": 4, "bonus": 2}}
EXCELLENT = 2.0
MAX_SWITCHES = 2                    # extra card selections per turn to read unknown tooltips
OPTIMISTIC_MULTIPLIER = 5.0         # an unread card might be this strong (observed maximum in the demos)
SIGNATURE_TOLERANCE = 9.0           # mean |Δgrey| between two pictures of the same card
SECURED_MIN_MENTAL = 0.5            # our mental / max must stay above this before Auto takes over

HAND_X = ((310, 220), (540, 220), (770, 220))
CARD_ART = (12, 618, 104, 72)       # portrait part of a lowered hand card relative to its slot x (y absolute)


def card_signature(image, slot: int, lift: int = 0) -> list[int] | None:
    """Coarse picture of hand card ``slot`` (1..3): 12x6 mean grey cells of the portrait (``lift`` px higher for
    the raised selected card). None without pixels."""
    if image is None or not 1 <= slot <= 3:
        return None
    import numpy as np
    x0 = HAND_X[slot - 1][0] + CARD_ART[0]
    y0 = CARD_ART[1] - lift
    w, h = CARD_ART[2], CARD_ART[3]
    a = np.asarray(image)[y0:y0 + h, x0:x0 + w, :3].astype(float).mean(axis=2)
    if a.shape != (h, w):
        return None
    cells = a[: h - h % 6, : w - w % 12].reshape(6, h // 6, 12, w // 12).mean(axis=(1, 3))
    return [int(v) for v in cells.flatten()]


def same_card(a: Sequence[int] | None, b: Sequence[int] | None) -> bool:
    if not a or not b or len(a) != len(b):
        return False
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= SIGNATURE_TOLERANCE


def lookup(cards: Mapping[str, Mapping], signature) -> Mapping | None:
    """Known card info for a picture (``cards`` = RunMemory.live_cards: key -> {"sig", "multiplier", ...}); a picture
    read under two names (``shared``) is not a card identity and is inspected again."""
    for info in cards.values():
        if same_card(info.get("sig"), signature):
            return None if info.get("shared") else info
    return None


def remember(cards: dict, signature, info: Mapping, shared: bool = False) -> None:
    """Cache a read tooltip by picture. 2026-10-29 wing6: the P idol's portrait carries most skills (GAP+, かわいいひと,
    HoPPoPらびっつ…), so a picture seen with another name, or on two cards of one hand (``shared``), is marked shared."""
    if signature is None or info.get("multiplier") is None:
        return
    static = {k: info[k] for k in ("name", "attribute", "multiplier") if info.get(k) is not None}
    for key, known in cards.items():
        if same_card(known.get("sig"), signature):
            known.pop("appeal", None)
            known.pop("buff", None)
            if shared or (known.get("name") and static.get("name") and known["name"] != static["name"]):
                known["shared"] = True
            known.update(static)
            return
    cards[f"card{len(cards) + 1}"] = {"sig": list(signature), **static, **({"shared": True} if shared else {})}


def parse_tooltip(texts) -> dict | None:
    """Selected card tooltip (x 485–800, y 400–610): name, attribute, multiplier, appeal value and buff %."""
    from agent.core.wing_skill_panel import classify_skill, normalise
    texts = [(t, tuple(b)) for t, b in texts]
    # LINK cards (live 2026-10-14): the skill box moves left (x 310–636: name x 325, 「Vo.」 x 416) and a red
    # 「リンクアピール」 box takes x 642–968.
    link = any(re.search(r"リンクアピ", normalise(t)) and 640 <= b[0] <= 980 and 400 <= b[1] <= 610 for t, b in texts)
    x_lo, x_hi, name_max_x, attr_min_x = (300, 640, 345, 380) if link else (480, 800, 500, 560)
    rows = sorted(((t, b) for t, b in texts if x_lo <= b[0] <= x_hi and 400 <= b[1] <= 610),
                  key=lambda tb: (tb[1][1], tb[1][0]))
    if not rows:
        return None
    appeal = buff = attr = None
    desc = []
    name = None
    for text, box in rows:
        t = normalise(text)
        m = re.fullmatch(r"(\d{2,5})\+(\d{1,3})%", t)
        if m:
            appeal, buff = int(m.group(1)), int(m.group(2))
            continue
        m = re.fullmatch(r"(Vo|Da|Vi)\.?", t)
        if m and box[0] >= attr_min_x:
            attr = m.group(1)
            continue
        if re.fullmatch(r"LINK\+?", t):
            continue
        if name is None and box[0] <= name_max_x and not re.search(r"倍アピール", t):
            name = t
            continue
        desc.append(t)
    info = classify_skill(name, "".join(desc))
    if info["kind"] != "live_skill" or info["multiplier"] is None:
        return {"name": name, "multiplier": None, "attribute": attr, "appeal": appeal, "buff": buff}
    if re.search(r"Excellent\d", "".join(desc)):
        card_attr = ALL
    else:
        card_attr = info["attributes"][0] if len(info["attributes"]) == 1 else attr
    return {"name": name, "multiplier": info["multiplier"], "attribute": card_attr, "appeal": appeal, "buff": buff}


def target_judge(judges: Sequence[Mapping], *, primary: str | None, estimate: Mapping | None,
                 special_rule: str | None = None) -> dict:
    """The judge to attack: trend #1 while present (user habit), unless the pre-audition estimate says the
    own-attribute plan is clearly better; when trend #1 has left, the best remaining trend. ``reason`` explains.
    ``special_rule`` "trend2" (S3 limited audition): trend 1 only when it is the primary's trend, else trend 2."""
    present = [j for j in judges if j.get("present", not j.get("left")) and j.get("attribute") in ATTRIBUTES]
    if not present:
        return {"attribute": None, "reason": "no_judge_present"}
    ranks = {j["attribute"]: j.get("trend_rank") for j in judges if j.get("attribute") in ATTRIBUTES}
    if any(not isinstance(ranks.get(j["attribute"]), int) for j in present):
        return {"attribute": None, "reason": "trend_unread"}
    best = min(present, key=lambda j: ranks[j["attribute"]])
    if special_rule == "trend2" and ranks.get(primary) != 1:
        second = next((j for j in present if ranks[j["attribute"]] == 2), None)
        if second is not None:
            return {"attribute": second["attribute"], "reason": "special_spear_trend2"}
    plan = (estimate or {}).get("plan") if isinstance(estimate, Mapping) else None
    own = next((j for j in present if j["attribute"] == primary), None)
    if ranks[best["attribute"]] == 1 and plan == "own_attribute" and own is not None and own is not best:
        return {"attribute": own["attribute"], "reason": "estimate_prefers_own_attribute"}
    if ranks[best["attribute"]] == 1:
        return {"attribute": best["attribute"], "reason": "focus_trend1"}
    return {"attribute": best["attribute"], "reason": f"trend1_left_focus_trend{ranks[best['attribute']]}"}


def matches(attribute: str | None, target: str | None) -> bool:
    """The card gets the Excellent bonus on the target judge (same attribute, or an 「Excellent」/All card)."""
    return attribute is not None and (attribute == target or attribute == ALL)


def card_value(card: Mapping, target: str, stats: Mapping | None) -> float | None:
    mult = card.get("multiplier")
    if mult is None:
        return None
    attr = card.get("attribute")
    power = card.get("appeal")
    if power is None:
        stat_attr = target if attr == ALL else attr
        power = (stats or {}).get(stat_attr) if stat_attr in ATTRIBUTES else None
    power = float(power) if isinstance(power, (int, float)) and power > 0 else 1.0
    buff = 1.0 + (card.get("buff") or 0) / 100.0
    return mult * power * buff * (EXCELLENT if matches(attr, target) else 1.0)


def choose_card(hand: Sequence[Mapping], target: str, stats: Mapping | None, *, selected: int | None = None,
                inspected: Sequence[int] = (), switches: int = 0) -> dict:
    """Next card action for the turn. ``hand``: [{"slot", "attribute", "multiplier"?, "appeal"?, "buff"?}].
    -> {"action": "select"|"play"|None, "slot", "reason"}: ``select`` taps a card (inspect an unread card that could
    still be the best, or switch to the best known one), ``play`` = the selected card is the one to use."""
    def optimistic(card):
        # An unread card of the same attribute shares the known cards' appeal base and buff this turn.
        peers = [c for c in hand if c.get("attribute") == card.get("attribute") and c.get("multiplier") is not None]
        guess = dict(card, multiplier=OPTIMISTIC_MULTIPLIER,
                     appeal=max((p.get("appeal") or 0 for p in peers), default=0) or None,
                     buff=max((p.get("buff") or 0 for p in peers), default=0))
        return card_value(guess, target, stats)

    known = sorted(((card_value(c, target, stats), -c["slot"], c["slot"]) for c in hand
                    if card_value(c, target, stats) is not None), reverse=True)
    best_value, best_slot = (known[0][0], known[0][2]) if known else (None, None)
    unknown = [c for c in hand if card_value(c, target, stats) is None and c["slot"] not in inspected
               and c["slot"] != selected]
    hopeful = [c for c in unknown if best_value is None or optimistic(c) > best_value * 1.0001]
    hopeful.sort(key=lambda c: (not matches(c.get("attribute"), target), c["slot"]))
    if selected is None:
        if hopeful:
            return {"action": "select", "slot": hopeful[0]["slot"], "reason": "inspect_unknown_card"}
        if best_slot is None:
            return {"action": None, "slot": None, "reason": "no_card"}
        return {"action": "select", "slot": best_slot, "reason": "best_known_card"}
    current = next((v for v, _, s in known if s == selected), None)
    if current is None:
        return {"action": None, "slot": selected, "reason": "selected_card_unread"}
    if switches < MAX_SWITCHES and hopeful:
        return {"action": "select", "slot": hopeful[0]["slot"], "reason": "inspect_unknown_card"}
    # The switch back to the best read card is always allowed once inspections are spent (live 2026-10-10: three
    # unread cards used both switches and the last inspected, weaker card was played).
    if switches <= MAX_SWITCHES and best_slot != selected and best_value > current * 1.0001:
        return {"action": "select", "slot": best_slot, "reason": "switch_to_best_card"}
    return {"action": "play", "slot": selected, "reason": "selected_is_best"}


def secured_first(live_state: Mapping) -> dict:
    """Mathematical 1st-place check from the visible stars. -> {"secured", "margin", "rival_max_gain", "reason"}."""
    ours = live_state.get("our_stars")
    rivals = live_state.get("rival_stars")
    judges = live_state.get("judges") or ()
    if not isinstance(ours, int) or not rivals:
        return {"secured": False, "reason": "stars_unread"}
    ranks = [j.get("trend_rank") for j in judges if not j.get("left")]
    if any(not isinstance(r, int) or r not in STARS for r in ranks):
        return {"secured": False, "reason": "trend_unread"}
    gain = sum(STARS[r]["ta"] + STARS[r]["la"] + 3 * STARS[r]["bonus"] for r in ranks)
    margin = ours - max(rivals)
    mental, mental_max = live_state.get("mental"), live_state.get("mental_max")
    if not (isinstance(mental, (int, float)) and isinstance(mental_max, (int, float)) and mental_max > 0):
        return {"secured": False, "margin": margin, "rival_max_gain": gain, "reason": "mental_unread"}
    if mental / mental_max < SECURED_MIN_MENTAL:
        return {"secured": False, "margin": margin, "rival_max_gain": gain, "reason": "mental_low"}
    secured = margin > gain
    return {"secured": secured, "margin": margin, "rival_max_gain": gain,
            "reason": "first_secured" if secured else "first_not_secured"}


def first_place_estimate(live_state: Mapping) -> float | None:
    """A rough P(1st) for reports: 0.5 at a star tie, 1.0 once ``secured_first``; None when stars are unread."""
    info = secured_first(live_state)
    if "margin" not in info:
        return None
    gain = max(info["rival_max_gain"], 1)
    return round(max(0.0, min(1.0, 0.5 + info["margin"] / (2.0 * gain))), 3)


def catalog_tooltip(tip: Mapping[str, Any], catalog: Mapping[str, dict] | None) -> dict:
    """The tooltip with its multiplier from the team's Live-skill catalog (wing_skill_catalog) when its name line is
    known there (2026-10-29 user: no reliance on the small description lines). A description multiplier is kept only
    when it is one of that skill's own versions (「かわいいひと」 2.5 / 「かわいいひと+」 3.5: the plus may be misread)."""
    from agent.core import wing_skill_catalog as wsk
    tip = dict(tip)
    entry = wsk.lookup(catalog, tip.get("name"))
    if entry is None:
        return tip
    base = entry["name"].rstrip("+")
    versions = {e["multiplier"] for name, e in (catalog or {}).items() if name.rstrip("+") == base}
    if tip.get("multiplier") not in versions:
        tip["multiplier"] = entry["multiplier"]
        tip["multiplier_source"] = entry["source"]
    if tip.get("attribute") not in CARD_ATTRIBUTES and len(entry["attributes"]) == 1:
        tip["attribute"] = entry["attributes"][0]
    return tip


def portrait_record(readings: Mapping[str, Any], memory: Any, catalog: Mapping[str, dict] | None = None) -> dict | None:
    """select_judge: the selected card's lowered portrait (from the turn's card step), attribute icon and tooltip
    name / multiplier, for wing_hand_cache.record; None when any part is unread."""
    selected = readings.get("selected_card")
    if readings.get("live_phase") != "select_judge" or selected not in (1, 2, 3):
        return None
    step = getattr(memory, "live_step", None) or {}
    sig = (step.get("sigs") or [None] * 3)[selected - 1]
    live = readings.get("live_state") if isinstance(readings.get("live_state"), Mapping) else {}
    attr = {o.get("id"): o.get("attribute") for o in live.get("options") or ()}.get(f"card{selected}")
    tip = catalog_tooltip(readings.get("tooltip") if isinstance(readings.get("tooltip"), Mapping) else {}, catalog)
    if not sig or attr in (None, OTHER) or not tip.get("name") or tip.get("multiplier") is None:
        return None
    card = {"sig": list(sig), "attribute": attr, "name": tip["name"], "multiplier": tip["multiplier"]}
    if selected - 1 in shared_portraits(step.get("sigs")):
        card["shared"] = True        # the P idol's picture on several cards of this hand: not a card identity
    return card


def shared_portraits(sigs) -> set[int]:
    """Indexes of hand pictures that look like another card of the same hand (2026-10-29 wing6: the P idol's
    portrait sits on most skills, so it never identifies one)."""
    sigs = list(sigs or ())
    return {i for i, a in enumerate(sigs) for j, b in enumerate(sigs) if i != j and same_card(a, b)}


def plan_turn(readings: Mapping[str, Any], memory: Any, *, primary: str | None, estimate: Mapping | None,
              stats: Mapping | None, catalog: Mapping[str, dict] | None = None,
              portraits: Sequence[Mapping] | None = None, special_rule: str | None = None) -> dict:
    """One Live step from the readings (phase, live_state, hand signatures, tooltip) and the run memory's card
    cache / current step. -> {"action": "card"|"judge"|"timing"|"stop", "slot"|"attribute", "reason", ...}.
    The caller maps it to a click intent and keeps ``memory.live_step``."""
    phase = readings.get("live_phase")
    live = readings.get("live_state") if isinstance(readings.get("live_state"), Mapping) else {}
    step = getattr(memory, "live_step", None) or {}
    cards = getattr(memory, "live_cards", {})
    judges = [{"attribute": j.get("attribute"), "trend_rank": j.get("trend_rank"), "left": j.get("left"),
               "present": not j.get("left")} for j in live.get("judges") or ()]
    if phase == "timing":
        return {"action": "timing", "reason": "appeal_timing"}
    target = target_judge(judges, primary=primary, estimate=estimate, special_rule=special_rule)
    if target["attribute"] is None:
        return {"action": "stop", "reason": "live_" + target["reason"]}
    sigs = readings.get("hand_sigs") or [None, None, None]
    labels = {o.get("id"): o.get("attribute") for o in live.get("options") or ()}
    shared = shared_portraits(step.get("sigs") if phase == "select_judge" else sigs)
    # This turn's tooltips by slot: a shared picture is not in the card cache, so a card inspected and then switched
    # away from keeps its reading here.
    reads = dict(step.get("reads") or {}) if phase == "select_judge" else {}
    hand = []
    for slot in (1, 2, 3):
        attr = labels.get(f"card{slot}")
        if attr is None:
            continue
        sig = (step.get("sigs") or [None] * 3)[slot - 1] if phase == "select_judge" else sigs[slot - 1]
        info = reads.get(str(slot)) or lookup(cards, sig) or {}
        if not info and portraits and slot - 1 not in shared:
            # 2026-10-29: a card seen in an earlier Produce (portrait + attribute icon, wing_hand_cache): no inspect tap
            from agent.core import wing_hand_cache as whc
            info = whc.match(portraits, sig, attr) or {}
        card_attr = attr if attr == ALL else (info.get("attribute") or attr)
        hand.append({"slot": slot, "attribute": card_attr, "multiplier": info.get("multiplier"),
                     "appeal": None, "buff": None})
    if len(hand) != 3:
        return {"action": "stop", "reason": "live_hand_unread", "hand": hand}
    # 2026-10-15 live: the green-sparkle starter card (OTHER: restores 5 % mental, no appeal; user: the first card to
    # be replaced) is read so the phase is known, but never played.
    hand = [c for c in hand if c["attribute"] != OTHER]
    if not hand:
        return {"action": "stop", "reason": "live_no_playable_card"}
    if phase == "select_card":
        choice = choose_card(hand, target["attribute"], stats)
        if choice["action"] != "select":
            return {"action": "stop", "reason": "live_" + choice["reason"]}
        return {"action": "card", "slot": choice["slot"], "target": target, "reason": choice["reason"],
                "sigs": list(sigs), "switches": 0, "inspected": [choice["slot"]]}
    if phase == "select_judge":
        selected = readings.get("selected_card")
        if selected not in (1, 2, 3):
            return {"action": "stop", "reason": "live_selected_card_unread"}
        tip = readings.get("tooltip") if isinstance(readings.get("tooltip"), Mapping) else {}
        tip = catalog_tooltip(tip, catalog)
        if tip.get("multiplier") is not None:
            card = next(c for c in hand if c["slot"] == selected)
            card.update({k: tip[k] for k in ("multiplier", "appeal", "buff") if tip.get(k) is not None})
            if tip.get("attribute") in CARD_ATTRIBUTES and card["attribute"] != ALL:
                card["attribute"] = tip["attribute"]        # the hand's 「Excellent」 icon outranks a tooltip label
            # All cards of this attribute use the same base and active buff in this frame. Share that fresh reading,
            # not a tooltip cached in an earlier turn or audition.
            for peer in hand:
                if peer["attribute"] == card["attribute"]:
                    peer.update({k: tip[k] for k in ("appeal", "buff") if tip.get(k) is not None})
            if isinstance(cards, dict):
                remember(cards, (step.get("sigs") or [None] * 3)[selected - 1], tip, shared=selected - 1 in shared)
            reads[str(selected)] = {k: tip[k] for k in ("name", "attribute", "multiplier") if tip.get(k) is not None}
        choice = choose_card(hand, target["attribute"], stats, selected=selected,
                             inspected=step.get("inspected") or (), switches=step.get("switches") or 0)
        if choice["action"] == "select":
            return {"action": "card", "slot": choice["slot"], "target": target, "reason": choice["reason"],
                    "switch": True, "reads": reads}
        if choice["action"] != "play":
            return {"action": "stop", "reason": "live_" + choice["reason"]}
        index = next((i for i, j in enumerate(live.get("judges") or (), 1)
                      if j.get("attribute") == target["attribute"] and not j.get("left")), None)
        if index is None:
            return {"action": "stop", "reason": "live_target_judge_absent", "target": target}
        return {"action": "judge", "index": index, "target": target, "reason": target["reason"],
                "card": selected}
    return {"action": "stop", "reason": "live_phase_unread"}

"""W.I.N.G. skill-panel (振り返り) SP allocation planner: pure, deterministic, no input, no screen reading.

Rules from the first supervised True End run (unit7, Middle, 2026-10-04) and the user's
tier order of 2026-10-05:

* Before every audition and before W.I.N.G. (semifinal/final) spend all SP; do not hoard. After W.I.N.G. is won,
  spend nothing (the run ends; leftover SP is lost anyway, so there is no reason to touch the panel).
* Tiers, strictly in this order (a lower tier is bought only when no useful higher-tier node is affordable):

  1. ``p_card``: the P card's primary Live skills and the P card's primary cap raises;
  2. ``main_skill``: Live skills of the two main S cards (``source="s_main"``) and Live skills of unknown source
     (legacy behaviour: every Live skill was ranked before caps);
  3. ``cap``: other primary cap raises, valued by projected overflow ``min(amount, projected − cap_now)``. The
     projection is the primary stat at the end of the run's growth (``final_stat``, a 40 SP
     Vocal7%UP was bought over a 40 SP Vo cap before an S3 audition because only the audition's zero growth was
     looked at); else the next action's preview; without either the legacy gate applies (``cap − stat <= cap_margin``).
     With ``final_stat`` a passive waits while such a cap is still learnable, and a cap needed only by later growth
     waits while a planned Live target is still unaffordable;
  4. ``other_skill``: Live skills of other S cards (``source="s_other"``);
  5. ``passive``.

  Within a tier, value per SP decides (useful value / total path cost, then lower cost, then id). Useful value is the
  marginal multiplier for Live skills (new − replaced) and the useful cap amount for caps. Per-SP value is never
  compared across tiers: the user's lexicographic order wins (a pure PEP
  conversion would put caps before Live skills, which contradicts both the community and the observed TE runs).
  ``params["tier_order"]`` may reorder the tiers (W.I.N.G. T.E. setting 「SP 加点」, ``TIER_PRESETS``); default = above.
  With ``caps_first_near_cap``, all primary caps share the same urgent priority when the next known growth would
  overflow; buying enough capacity clears that priority. Without a preview the near-cap rule remains the fallback.
  Stage visits have no further growth, so normal cap value is zero there; explicit spend-down remains optional.
* A Live skill with the same known name and complete effect identity as one already acquired costs 0 SP at another
  panel position. That position can open a path without occupying another skill slot, even after the skill is forgotten.
* At most 4 Live skills. A 「+」 skill such as ``DIVE IN!+`` takes its own slot like any other. A full
  set forgets a recovery skill first (the initial メンタルキュアI), then the weakest appeal skill (multiplier × attribute
  weight, ``wing_skill_panel.skill_strength``), and only for a skill stronger by ``forget_margin``.
  When no learnable Live skill beats the weakest held one, no Live skill is planned and the SP goes to caps/passives
  (reason ``live_skills_not_stronger``). The game cannot undo a forget, so the plan lists it explicitly for the
  executor to confirm on screen.

* Lookahead (``future_sp``, 2026-10-10): Live skills follow a run plan over whole
  paths (deep nodes included) within SP now + safe future income; SP beyond what that income covers is kept for
  the plan's targets; nothing is kept at the last chance (W.I.N.G. stage).

Values (skill multiplier, cap amount, passive score) are inputs read from the panel; nothing here is a verified game
formula.
"""
from __future__ import annotations

import dataclasses
import itertools
import math
from typing import Iterable

ATTRIBUTES = ("Vo", "Da", "Vi")
NODE_KINDS = ("live_skill", "cap_up", "passive")
NODE_SOURCES = ("p_card", "s_main", "s_other", "unknown")
TIERS = ("p_card", "main_skill", "cap", "other_skill", "passive")
LIVE_SKILL_SLOTS = 4

DEFAULT_SP_PARAMS = {
    "cap_margin": 100,          # legacy cap gate without a projection: cap - primary stat <= this
    "reserve_sp": 0,            # SP kept back (user rule: spend everything before auditions / W.I.N.G.)
    "allow_offattr_skills": False,   # learn off-attribute Live skills (default: no, they take a slot)
    # User setting 「SP 加点」: the tier order. Default = the user's order (TIERS); "上限优先" puts ``cap``
    # right after ``p_card``. Must be a permutation of TIERS.
    "tier_order": TIERS,
    # P card Live skills are tier 1 whatever their attribute (third demo 2026-10-06: in a Vo run the user learned the
    # P card's Vi 3× skill first; its 「+」 node on the same petal is Vo 4.5×). False = the attribute filter applies.
    "p_card_any_attribute": True,
    # Forget rule: the new skill must beat the weakest held one by this relative margin; an off-attribute
    # skill counts with this weight when the stats are unknown (else its stat / the primary stat).
    "forget_margin": 0.10,
    "offattr_weight": 0.5,
    # Imminent primary overflow puts caps first; without a projection use the near-cap fallback.
    "caps_first_near_cap": True,
    # An SR P card's skills are weak; its Live skills rank as other S cards' skills and its caps as
    # ordinary caps (the caller sets this from the P card's rarity).
    "p_card_weak": False,
    # Lookahead (``future_sp`` given, 2026-10-10): share of the estimated future SP income counted as certain when
    # choosing the run's Live-skill targets and the SP to keep for them; Live candidates kept for the subset search.
    "income_safety": 0.7,
    "lookahead_candidates": 10,
    # W.I.N.G. setting 「终局清空 SP」 (leftover SP at the end is fine -> default off). True: at the
    # last chance (``last_chance=True``, before the W.I.N.G. semifinal/final) SP the normal tiers leave is spent down
    # on any learnable node of positive value: caps of every Live stat (primary first, then Vo/Da/Vi, Me last) and
    # passives of any attribute whose condition is not known to fail. The subset that spends the most SP wins.
    "spend_down_last_chance": False,
    "spend_down_candidates": 14,
}
# Weight of a Live-skill gain by tier in the run plan (P card skills first, user order 2026-10-05).
LIVE_TIER_WEIGHT = {"p_card": 2.0, "main_skill": 1.0, "other_skill": 0.6}
TIER_PRESETS = {
    "skills_first": TIERS,
    "caps_first": ("p_card", "cap", "main_skill", "other_skill", "passive"),
}


@dataclasses.dataclass(frozen=True)
class SPNode:
    """One panel node. ``requires`` lists unlearned-or-learned node ids on the adjacency path (inner to outer)."""
    id: str
    kind: str
    cost: int
    value: float = 0.0                 # live_skill: appeal multiplier; cap_up: cap amount; passive: score
    attributes: tuple = ()             # stats the node applies to, e.g. ("Vo",) or ("Vo", "Da"); () = any / none
    learned: bool = False
    available: bool = True             # False: locked by something other than the path (e.g. not yet shown)
    requires: tuple = ()
    upgrades: str | None = None        # legacy base-skill relation; 「+」 still uses its own slot
    source: str = "unknown"            # panel owner: p_card | s_main (the two main S cards) | s_other | unknown
    skill_identity: str | None = None  # complete effect identity; never inferred from multiplier alone
    duplicate_skill: bool = False     # already obtained elsewhere; opens a path without equipping a new skill

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(f"node.kind 必须为 {NODE_KINDS} 之一")
        if type(self.cost) is not int or self.cost < 0:
            raise ValueError("node.cost 必须是非负整数")
        if set(self.attributes) - set(ATTRIBUTES + ("Me",)):
            raise ValueError("node.attributes 只能包含 Vo/Da/Vi/Me")
        if self.source not in NODE_SOURCES:
            raise ValueError(f"node.source 必须为 {NODE_SOURCES} 之一")


@dataclasses.dataclass(frozen=True)
class LiveSkill:
    """A Live skill already held (所持スキル)."""
    id: str
    attribute: str | None
    multiplier: float
    skill_identity: str | None = None


@dataclasses.dataclass(frozen=True)
class SPStep:
    node_id: str
    cost: int
    category: str                       # live_skill | cap_up | passive | path
    forget: str | None = None           # Live skill to forget when learning this one (irreversible)
    reason: str = ""
    tier: str = ""                      # TIERS entry that selected the purchase ("" for path steps)


@dataclasses.dataclass(frozen=True)
class SPPlan:
    steps: tuple
    sp_before: int
    sp_left: int
    skills_after: tuple
    reasons: tuple
    target: tuple = ()                  # lookahead: Live-skill node ids the SP is kept for (not affordable yet)
    reserve: int = 0                    # SP kept back for ``target`` (beyond the expected future income)

    @property
    def spent(self) -> int:
        return self.sp_before - self.sp_left

    def to_dict(self) -> dict:
        return {"steps": [dataclasses.asdict(s) for s in self.steps], "sp_before": self.sp_before,
                "sp_left": self.sp_left, "skills_after": [dataclasses.asdict(s) for s in self.skills_after],
                "reasons": list(self.reasons), "target": list(self.target), "reserve": self.reserve}


def _strength(multiplier: float, attributes, primary: str, stats, params) -> float:
    from agent.core.wing_skill_panel import skill_strength
    return skill_strength({"kind": "live_skill", "multiplier": multiplier, "attributes": attributes}, primary, stats,
                          params.get("offattr_weight", 0.5)) or 0.0


def _forget_choice(skills: list[LiveSkill], new: SPNode, primary: str, stats=None,
                   params: dict | None = None) -> tuple[bool, str | None, float]:
    """(can_learn, skill_to_forget, strength gain) for a new Live skill under the 4-slot limit."""
    params = params or DEFAULT_SP_PARAMS
    new_strength = _strength(new.value, new.attributes, primary, stats, params)
    # wiki よくある質問#Unique_LS: a base skill and its 「+」 can be equipped together, so a
    # 「+」 uses the ordinary four-slot rule -- the same one the forget page executes (_decide_sp_forget).
    if len(skills) < LIVE_SKILL_SLOTS:
        return True, None, new_strength
    # recovery skills (multiplier 0) first, then the weakest appeal skill by strength
    scored = sorted(((0 if s.multiplier == 0 else 1,
                      _strength(s.multiplier, (s.attribute,) if s.attribute else (), primary, stats, params), s.id)
                     for s in skills))
    _, weakest, victim = scored[0]
    if new_strength <= 0 or new_strength <= weakest * (1 + params.get("forget_margin", 0.10)):
        return False, None, 0.0
    return True, victim, new_strength - weakest


def plan_sp(nodes: Iterable[SPNode], *, sp: int | None, primary: str, stat: int | None, cap: int | None,
            skills: Iterable[LiveSkill] = (), wing_won: bool = False, params: dict | None = None,
            projected_stat: int | None = None, stats: dict | None = None,
            future_sp: int | None = None, last_chance: bool = False, final_stat: int | None = None,
            mastered_skills: Iterable[str] = ()) -> SPPlan:
    """Spend ``sp`` on the panel by tier. Unknown SP/stat/cap never guess: an unknown SP stops; an unknown stat or
    cap disables only cap valuation (skills and passives are still planned).

    ``projected_stat``: the primary stat after the action that prompted this session; its overflow makes caps urgent
    (bought before Live skills). ``final_stat``: the primary stat expected when the run's growth ends; a cap is worth
    only the part of its amount that stat would overflow (0 -> not bought). Without ``final_stat`` the next action's
    projection values caps (legacy). ``stats``: Vo/Da/Vi of the idol, used to weigh
    off-attribute Live skills against the primary (unknown -> ``offattr_weight``).

    ``future_sp`` (lookahead, 2026-10-10; None = legacy greedy): SP expected before the last spending chance of the
    run (0 = this is the last one, e.g. before the W.I.N.G. final). The Live skills are then chosen as a **run plan**:
    the subset of at most four Live targets (whole paths, shared path nodes counted once, the 4-slot forget rule
    simulated in order) with the largest tier-weighted strength gain whose cost fits ``sp + income_safety ×
    future_sp``. Only Live skills of that plan are bought; the part of their cost the safe future income will not
    cover is kept back (``SPPlan.reserve``) and caps/passives use only the SP above it -- except cap raises while the
    primary sits within ``cap_margin`` of its cap (lessons would be wasted). With ``future_sp == 0`` nothing is kept.

    ``last_chance`` (the W.I.N.G. semifinal/final hub): with ``params["spend_down_last_chance"]`` the SP left after the
    tiers goes to the spend-down set (see DEFAULT_SP_PARAMS); off by default, the tiers alone decide."""
    params = {**DEFAULT_SP_PARAMS, **(params or {})}
    skills = list(skills)
    if primary not in ATTRIBUTES:
        raise ValueError("primary 必须是 Vo/Da/Vi")
    tier_order = tuple(params["tier_order"])
    if sorted(tier_order) != sorted(TIERS):
        raise ValueError(f"tier_order 必须是 {TIERS} 的一个排列")
    if future_sp is not None and (type(future_sp) is not int or future_sp < 0):
        raise ValueError("future_sp 必须是非负整数")
    if sp is None:
        return SPPlan((), 0, 0, tuple(skills), ("sp_unread",))
    if wing_won:
        return SPPlan((), sp, sp, tuple(skills), ("wing_won_no_spend",))
    nodes = list(nodes)
    by_id = {n.id: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("node id 重复")
    learned = {n.id for n in by_id.values() if n.learned}
    mastered = set(mastered_skills) | {n.skill_identity for n in nodes if n.skill_identity
                                      and (n.learned or n.duplicate_skill)}
    mastered.update(s.skill_identity for s in skills if s.skill_identity)
    budget = sp - params["reserve_sp"]
    steps: list[SPStep] = []
    reasons: list[str] = []
    blocked_live: set = set()
    cap_now = cap
    lookahead = future_sp is not None
    future_safe = int(math.floor((future_sp or 0) * float(params["income_safety"])))
    reserve, target, pending_cost = 0, (), 0

    def path_of(node: SPNode) -> list[SPNode] | None:
        out = []
        seen, visiting = set(learned), {node.id}

        def visit(rid: str) -> bool:
            if rid in seen:
                return True
            req = by_id.get(rid)
            if rid in visiting or req is None or not req.available:
                return False                                 # unknown, locked or cyclic path: skip
            visiting.add(rid)
            for parent in req.requires:
                if not visit(parent):
                    return False
            visiting.remove(rid)
            seen.add(rid)
            out.append(req)
            return True

        if not all(visit(rid) for rid in node.requires):
            return None
        return out

    def price(node: SPNode, identities: set) -> int:
        if node.kind == "live_skill" and node.skill_identity:
            duplicate = node.skill_identity in identities
            identities.add(node.skill_identity)
            if duplicate:
                return 0
        return node.cost

    def total_cost(node: SPNode) -> int | None:
        path = path_of(node)
        if path is None:
            return None
        seen = set(mastered)
        return sum(price(n, seen) for n in [*path, node])

    def union_cost(targets) -> int:
        ids, seen, cost = set(learned), set(mastered), 0
        for t in sorted(targets, key=lambda n: (tier_order.index(tier_of(n)) if tier_of(n) in tier_order else 99,
                                                -n.value, n.id)):
            for node in [*(path_of(t) or ()), t]:
                if node.id not in ids:
                    cost += price(node, seen)
                    ids.add(node.id)
        return cost

    def tier_of(node: SPNode) -> str | None:
        weak_p = node.source == "p_card" and params.get("p_card_weak")
        if node.kind == "live_skill" and weak_p:
            if node.attributes and primary not in node.attributes and not params["allow_offattr_skills"]:
                return None
            return "other_skill"
        if node.kind == "cap_up" and weak_p:
            return "cap" if primary in node.attributes else None
        if node.kind == "live_skill":
            if node.source == "p_card" and params["p_card_any_attribute"]:
                return "p_card"
            if node.attributes and primary not in node.attributes and not params["allow_offattr_skills"]:
                return None
            if node.source == "p_card":
                return "p_card"
            return "other_skill" if node.source == "s_other" else "main_skill"
        if node.kind == "cap_up":
            if primary not in node.attributes:
                return None
            return "p_card" if node.source == "p_card" else "cap"
        return "passive" if (not node.attributes or primary in node.attributes) else None

    def near_cap() -> bool:
        if last_chance or stat is None or cap_now is None:
            return False
        return projected_stat > cap_now if projected_stat is not None else cap_now - stat <= params["cap_margin"]

    def path_cap_value(node: SPNode) -> float:
        """Cap raise of the whole path (2026-10-14 user: finish a line to its +100 instead of picking +20 nodes; a
        path's own primary caps count too, so a deep +100 is not out-ranked by a lone +25 next to the centre)."""
        if last_chance or stat is None or cap_now is None:
            return 0.0
        total = sum(float(p.value) for p in [*(path_of(node) or ()), node]
                    if p.kind == "cap_up" and primary in (p.attributes or ()))
        horizon = final_stat if final_stat is not None else projected_stat
        if horizon is not None:
            return float(min(total, max(0, horizon - cap_now)))
        return total if near_cap() else 0.0

    def value_of(node: SPNode) -> tuple[float, str | None] | None:
        """(useful value, skill to forget) or None when the node cannot be learned now."""
        held = list(skills)
        seen = set(mastered)
        for req in path_of(node) or ():
            if req.kind != "live_skill":
                continue
            if req.skill_identity and req.skill_identity in seen:
                continue
            ok, forget, _ = _forget_choice(held, req, primary, stats, params)
            if not ok:
                blocked_live.add(req.id)
                return None
            if forget:
                held = [s for s in held if s.id != forget]
            held.append(as_skill(req))
            if req.skill_identity:
                seen.add(req.skill_identity)
        if node.kind == "live_skill":
            if node.skill_identity and node.skill_identity in seen:
                return 0.0, None
            ok, forget, gain = _forget_choice(held, node, primary, stats, params)
            if not ok:
                blocked_live.add(node.id)
                return None
            return gain, forget
        if node.kind == "cap_up":
            return path_cap_value(node), None
        return float(node.value), None

    def as_skill(node: SPNode) -> LiveSkill:
        return LiveSkill(node.id, node.attributes[0] if len(node.attributes) == 1 else None, node.value, node.skill_identity)

    def simulate(subset) -> tuple | None:
        """(tier-weighted strength gain, P-card gain) of learning ``subset`` (Live skills on the paths included) in tier order;
        None when the 4-slot rule would refuse one of them."""
        held, done, total = list(skills), set(learned), 0.0
        seen = set(mastered)
        p_total = 0.0
        order = sorted(subset, key=lambda n: (tier_order.index(tier_of(n)), -n.value, n.id))
        for t in order:
            weight = LIVE_TIER_WEIGHT[tier_of(t)]
            for node in [p for p in path_of(t) or () if p.kind == "live_skill"] + [t]:
                if node.id in done:
                    continue
                if node.skill_identity and node.skill_identity in seen:
                    done.add(node.id)
                    continue
                ok, forget, gain = _forget_choice(held, node, primary, stats, params)
                if not ok:
                    return None
                if forget:
                    held = [s for s in held if s.id != forget]
                held.append(as_skill(node))
                done.add(node.id)
                if node.skill_identity:
                    seen.add(node.skill_identity)
                total += gain * weight
                if tier_of(t) == "p_card":
                    p_total += gain
        return total, p_total

    def live_plan(limit: int) -> list[SPNode]:
        """Best Live-skill target set within ``limit`` SP (empty when no Live skill adds strength)."""
        cands = []
        for node in by_id.values():
            if node.kind != "live_skill" or node.id in learned or not node.available:
                continue
            tier = tier_of(node)
            cost = total_cost(node)
            if tier not in LIVE_TIER_WEIGHT or cost is None or cost > limit:
                continue
            sim = simulate([node])
            gain = None if sim is None else sim[0]
            if gain is None or gain <= 0:
                blocked_live.add(node.id)
                continue
            # Pruning must preserve the user's tier order too: a cheap P skill must not disappear just because
            # enough S-card targets have a higher weighted gain.
            cands.append((tier_order.index(tier), -gain, cost, node.id, node))
        cands = [c[4] for c in sorted(cands)[:int(params["lookahead_candidates"])]]
        best, best_key = [], (0.0, 0.0, 0)
        for size in range(1, min(LIVE_SKILL_SLOTS, len(cands)) + 1):
            for subset in itertools.combinations(cands, size):
                cost = union_cost(subset)
                if cost > limit:
                    continue
                sim = simulate(subset)
                if sim is None:
                    continue
                key = (round(sim[1], 6), round(sim[0], 6), -cost)     # P card skills first (user order)
                if key > best_key:
                    best, best_key = list(subset), key
        return best

    def candidates(tier: str, planned: set) -> list[tuple]:
        out = []
        for node in by_id.values():
            if node.id in learned or not node.available or tier_of(node) != tier:
                continue
            cost = total_cost(node)
            if cost is None or cost > budget:
                continue
            if lookahead:
                if node.kind == "live_skill" and node.id not in planned:
                    continue
                # Ordinary caps/passives respect the Live reserve; imminent overflow may use it.
                urgent = node.kind == "cap_up" and params.get("caps_first_near_cap", True) and near_cap()
                if node.kind != "live_skill" and cost > budget - reserve and not urgent:
                    continue
                # A cap needed only by later growth waits for a pending Live target (user order: skills > caps).
                if node.kind == "cap_up" and not urgent and cost > budget - pending_cost:
                    continue
            if node.kind == "passive" and caps_needed():
                continue
            valued = value_of(node)
            if valued is None or valued[0] <= 0:
                continue
            per_sp = valued[0] / cost if cost else math.inf
            out.append((-per_sp, cost, node.id, node, valued[1]))
        return sorted(out, key=lambda c: c[:3])

    def caps_needed() -> bool:
        """With ``final_stat``: a learnable primary cap the run's growth still needs (passives wait for it, user
        2026-10-25). Its cost does not matter: the SP is kept for it rather than spent on a passive."""
        if final_stat is None:
            return False
        for node in by_id.values():
            if (node.kind == "cap_up" and node.id not in learned and node.available
                    and tier_of(node) in ("p_card", "cap") and total_cost(node) is not None):
                valued = value_of(node)
                if valued is not None and valued[0] > 0:
                    return True
        return False

    def spend_down_rank(node: SPNode) -> int | None:
        """Spend-down priority (lower first) or None: caps of a Live stat (primary, Vo/Da/Vi, Me), then passives."""
        if node.kind == "cap_up" and node.value > 0:
            attrs = node.attributes or ()
            if primary in attrs:
                return 0
            if any(a in ATTRIBUTES for a in attrs):
                return 1
            if "Me" in attrs:
                return 2
            return None
        if node.kind == "passive" and node.value > 0:
            return 3
        return None

    def spend_down(limit: int) -> list[SPNode]:
        """Subset of spend-down nodes (whole paths, shared nodes once) spending the most SP within ``limit``; ties ->
        better priority sum, then fewer nodes. Returned in priority order (paths are bought with their node)."""
        cands = []
        for node in by_id.values():
            if node.id in learned or not node.available:
                continue
            rank = spend_down_rank(node)
            cost = total_cost(node)
            if rank is None or cost is None or cost > limit:
                continue
            if any(p.kind == "live_skill" for p in path_of(node) or ()):
                continue                                  # never learn a Live skill as a side effect
            cands.append((rank, cost, node.id, node))
        cands = [c[3] for c in sorted(cands)[:int(params["spend_down_candidates"])]]
        best, best_key = [], (0, 0, 0)
        for size in range(1, len(cands) + 1):
            for subset in itertools.combinations(cands, size):
                cost = union_cost(subset)
                if cost > limit:
                    continue
                key = (cost, -sum(spend_down_rank(n) + 1 for n in subset), -size)
                if key > best_key:
                    best, best_key = list(subset), key
            if size >= 6:
                break                                     # bounded search (a session learns at most 12 nodes)
        return sorted(best, key=lambda n: (spend_down_rank(n), n.id))

    def learn_skill(node: SPNode) -> str | None:
        nonlocal skills
        if node.skill_identity and node.skill_identity in mastered:
            return None
        _, forget, _ = _forget_choice(skills, node, primary, stats, params)
        if forget:
            skills = [s for s in skills if s.id != forget]
        skills.append(as_skill(node))
        if node.skill_identity:
            mastered.add(node.skill_identity)
        return forget

    def buy(node: SPNode, tier: str) -> None:
        nonlocal budget, cap_now
        for req in path_of(node) or []:
            cost = price(req, set(mastered))
            req_forget = learn_skill(req) if req.kind == "live_skill" else None
            steps.append(SPStep(req.id, cost, "path", forget=req_forget, reason=f"path_to_{node.id}"))
            learned.add(req.id)
            budget -= cost
            if req.kind == "cap_up" and primary in req.attributes and cap_now is not None:
                cap_now += int(req.value)
        cost = price(node, set(mastered))
        forget = learn_skill(node) if node.kind == "live_skill" else None     # after the path's own skills
        steps.append(SPStep(node.id, cost, node.kind, forget, f"{tier}_value_{node.value:g}", tier))
        learned.add(node.id)
        budget -= cost
        if node.kind == "cap_up" and cap_now is not None and primary in node.attributes:
            cap_now += int(node.value)

    while True:
        planned: set = set()
        if lookahead:
            plan_set = live_plan(budget + future_safe)
            planned = {n.id for n in plan_set}
            pending = [n for n in plan_set if (total_cost(n) or 0) > budget]
            pending_cost = union_cost(pending) if pending else 0
            reserve = min(budget, max(0, pending_cost - future_safe))
            target = tuple(sorted(n.id for n in pending))
        # One choice entry for caps and Live skills, including lookahead targets. An imminent overflow
        # may use the reserved budget; after enough cap is bought it no longer overrides the normal tiers.
        options = []
        for tier in tier_order:
            for per_sp, cost, nid, node, forget in candidates(tier, planned):
                urgent = node.kind == "cap_up" and params.get("caps_first_near_cap", True) and near_cap()
                within = -node.value if lookahead and node.kind == "live_skill" else per_sp
                options.append(((0 if urgent else 1, 0 if urgent else tier_order.index(tier), within, cost, nid), node, tier))
        if not options:
            break
        _, best, tier = min(options, key=lambda v: v[0])
        buy(best, tier)

    if last_chance and params.get("spend_down_last_chance") and budget > 0:
        for node in spend_down(budget):
            buy(node, "spend_down")
        if any(st.tier == "spend_down" for st in steps):
            reasons.append("spend_down_last_chance")

    if stat is None or cap is None:
        reasons.append("cap_rule_disabled_stat_or_cap_unread")
    if blocked_live and not any(st.category == "live_skill" for st in steps) and not target:
        reasons.append("live_skills_not_stronger")
    if target:
        reasons.append("reserve_for_live_target")
    if not steps:
        reasons.append("nothing_affordable")
    return SPPlan(tuple(steps), sp, budget + params["reserve_sp"], tuple(skills), tuple(reasons) or ("ok",),
                  target, reserve)

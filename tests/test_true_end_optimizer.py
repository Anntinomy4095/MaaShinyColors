from dataclasses import asdict, replace
from itertools import permutations
import unittest

from agent.core.true_end.growth import GrowthConfig, estimate_team_growth
from agent.core.true_end.battle import BattleSkill, BattleSupport, ProducedIdol
from agent.core.true_end.models import ATTRIBUTES, Ownership, Panel, SupportSkill, extract_card_features
from unittest.mock import patch
from agent.core.true_end.optimizer import (CRITICAL, SearchConfig, TeamCandidate, beam_search, calculate_marginal_card_value,
    evaluate_battle_profile, find_best_teams, prune_candidates, scenario_from_dict, select_strategy, _stage, _bad_hand_ids, _passive,
    _battle_supports)
from agent.core.true_end.optimizer import (_distinct_alternatives, audition_hand, best_slotting, card_tags, growth_readiness,
                                           team_style)
from tools.true_end_recommend import render_report, run_input
from true_end_fixtures import card, feature, growth_input


def scenario():
    return scenario_from_dict({"name": "synthetic-assumption", "player_judgments": {"Perfect": 1},
        "npc_judgments": {"Good": 1}, "npc_late_multiplier": 1.25, "reaction_mode": "uniform",
        "fourth_turn_multiplier": 2, "hand_recycle": "discard_shuffle", "ta_tie": "player_loses",
        "overkill_credit": False, "assumptions": ["Synthetic judgment/reaction assumptions for tests, not calibrated"]})


def config(**kwargs):
    return SearchConfig.from_dict({"strategies": ["VO"], "candidate_per_role": 3, "beam_width": 12,
        "projection_budget": 300, "evaluate_top": 1, "screening_trials": 1,
        "confirmation_trials": 1, **kwargs})


def force_profile(lower=.98):
    rows = [{"audition": a, "trend": list(t), "variant": v, "worst_lower_bound": lower}
            for a in CRITICAL for t in permutations(ATTRIBUTES)
            for v in ("base", "lower_stats", "weak_buff", "bad_hand")]
    return {"status": "CONDITIONAL_SIMULATION", "policies": {
        "VO_FORCE": {"rows": rows, "mean_probability": 1, "worst_probability": 1, "worst_lower_bound": lower, "mean_turns": 4},
        "VO_SAFE": {"rows": rows, "mean_probability": .8, "worst_probability": .6, "worst_lower_bound": .5, "mean_turns": 8}}}


class OptimizerTests(unittest.TestCase):
    def test_main_recommendation_includes_stronger_evaluated_swap(self):
        mechanics = [(20, 0, 0, 20, 5), (1, 20, 0, 50, 1), (1, 0, 15, 50, 2),
                     (1, 2, 0, 400, 3), (8, 4, 3, 200, 3), (10, 5, 5, 100, 3), (15, 8, 5, 200, 3)]
        library = {"p": card("p", kind="P")}
        for i, (mastery, sp, stamina, cap, multiplier) in enumerate(mechanics):
            library[f"s{i}"] = card(f"s{i}", mastery=mastery, sp=sp, stamina=stamina, cap=cap, multiplier=multiplier)
        owned = {key: Ownership(key, 4, 80) for key in library}
        cfg = config(beam_width=1, projection_budget=4, evaluate_top=1, sensitivity=False)
        result = find_best_teams(library, owned, "p", growth=GrowthConfig.from_dict(growth_input()), config=cfg)
        best = result["recommendation"]["ProxyTERating"]
        # without the swap pool the main recommendation stays at 64.7181 (2026-10-27 weights: skills 15 + passives 5)
        self.assertGreater(best, 65.5)
        self.assertTrue(all(best >= other["ProxyTERating"] for other in result["alternatives"]))

    def test_support_reasons_exclude_zero_unlocked_values(self):
        support = feature("s")
        inactive = replace(support, support_effects=tuple((skill, 0.0) for skill, _ in support.support_effects))
        self.assertNotIn("SP", card_tags(inactive))
        self.assertNotIn("回体", card_tags(inactive))
        self.assertNotIn("Vo课", card_tags(inactive))
        self.assertIn("SP", card_tags(support))
        self.assertIn("回体", card_tags(support))


    def test_stamina_tags_follow_the_build_room(self):
        # 回体 = the build room's マスタリー体力 (or unit stamina); 体力サポート = 省体; おやすみブースト none.
        def skills(*rows):
            return replace(feature("s"), owned_live=None, support_effects=tuple(
                (SupportSkill(kind, kind, {1: 5.0}, room=room), 5.0) for kind, room in rows))
        self.assertEqual(card_tags(skills(("mastery_stamina", "Vo")), attrs=("Vo",)), ["回体"])
        self.assertEqual(card_tags(skills(("mastery_stamina", "Da")), attrs=("Vo",)), [])
        self.assertEqual(card_tags(skills(("unit_mastery_stamina", None)), attrs=("Vo",)), ["回体"])
        self.assertEqual(card_tags(skills(("stamina_support", None), ("rest_support", None)), attrs=("Vo",)), ["省体"])
        self.assertEqual(card_tags(skills(("rest_support", None)), attrs=("Vo",)), [])
        self.assertEqual(card_tags(skills(("stamina_support", None)), attrs=("Vo",), unit=True), ["组合", "省体"])

    def test_unit_tag_marks_a_card_that_triggers_a_teammates_unit_mastery(self):
        from agent.core.true_end.optimizer import _feeds_unit
        def member(key, unit, kind=None, value=5.0):
            f = feature(key)
            effects = ((SupportSkill(kind, kind, {1: value}), value),) if kind else ()
            return replace(f, card=replace(f.card, unit=unit), support_effects=effects)
        holder, mate, other = member("a", "u", "unit_mastery_sp"), member("b", "u"), member("c", "v")
        self.assertTrue(_feeds_unit(mate, (holder, mate, other)))
        self.assertFalse(_feeds_unit(other, (holder, mate, other)))
        self.assertFalse(_feeds_unit(holder, (holder, other)))   # its own skill does not count
        locked = member("d", "u", "unit_mastery_sp", 0.0)
        self.assertFalse(_feeds_unit(mate, (locked, mate)))

    def test_alternatives_skip_equivalent_swaps_but_keep_active_combat_changes(self):
        p, guest = feature("p", kind="P"), feature("g")
        active = tuple(feature("s" + str(i)) for i in range(4))
        projection = estimate_team_growth(p, active, guest, "VO", GrowthConfig.from_dict(growth_input()))
        baseline = TeamCandidate(p, active, guest, "VO", projection)
        equivalent = replace(baseline, active=(guest, *active[1:]), guest=active[0])
        hitter = replace(guest, owned_live=replace(guest.owned_live, components={"Vo": 6}))
        combat_change = replace(equivalent, active=(hitter, *active[1:]))
        def value(candidate):
            return {"candidate": candidate, "strategy": {"strategy": "VO_SAFE"}, "rating": 50,
                    "readiness": 50, "battle": {}}
        alternatives = _distinct_alternatives(value(baseline), [value(baseline), value(equivalent), value(combat_change)])
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(alternatives[0]["guest"], "s0")
        self.assertIn("tradeoffs", alternatives[0])
        # growth-only (no battle): a Guest/Active swap of the same five S cards is the same team
        self.assertEqual(_distinct_alternatives(value(baseline), [value(baseline), value(equivalent), value(combat_change)],
                                                battle=False), [])

    def test_team_style_names_the_build_and_alternatives_show_another(self):
        # 2026-11-01 (user: 流派): 单属性 by default, オデマス with two audition-mastery cards, 组合 when a unit performs
        # in full and a 「<unit>全員」 passive uses it; another 流派 takes the second alternative place.
        p, guest = feature("p", kind="P"), feature("g")
        active = tuple(feature("s" + str(i)) for i in range(4))
        projection = estimate_team_growth(p, active, guest, "VO", GrowthConfig.from_dict(growth_input()))
        mono = TeamCandidate(p, active, guest, "VO", projection)
        self.assertEqual(team_style(mono)["id"], "mono")
        self.assertEqual(team_style(replace(mono, strategy="VO_DA"))["label"], "双属性")
        audition = SupportSkill("audition_mastery_sp", "audition_mastery_sp", {1: 2.0})
        def auditioner(f):
            return replace(f, support_effects=((audition, 2.0),))
        odemas = replace(mono, active=(auditioner(active[0]), *active[1:]), guest=auditioner(feature("y")))
        self.assertEqual(team_style(odemas)["id"], "audition")
        def idol(f, name, panels=()):
            return replace(f, card=replace(f.card, idol=name, panels=panels))
        cond = "passive requires battle interpreter: Vocal20%UP / [条件:シーズ全員がライブに参加している場合] [確率:40%] [最大:2回]"
        passive = Panel("seeds", "passive", 20, 0, (), True, {}, unsupported=(cond,))
        seeds = replace(mono, p=idol(p, "七草にちか", (passive,)), active=(idol(active[0], "緋田美琴"), *active[1:]))
        self.assertEqual(team_style(seeds), {"id": "unit", "label": "组合 シーズ",
                                             "detail": "シーズ全员上场，「シーズ全員」被动生效"})
        self.assertEqual(team_style(replace(seeds, active=active, guest=idol(guest, "緋田美琴")))["id"], "mono")
        def value(candidate, rating):
            return {"candidate": candidate, "strategy": {"strategy": "VO_SAFE"}, "rating": rating,
                    "readiness": rating, "battle": {}}
        others = [replace(mono, guest=feature(f"x{i}")) for i in range(3)]
        pool = [value(mono, 60), *(value(c, 59 - i) for i, c in enumerate(others)), value(odemas, 50)]
        alternatives = _distinct_alternatives(pool[0], pool, battle=False)
        self.assertEqual([a["style"]["id"] for a in alternatives], ["mono", "audition"])
        self.assertEqual(alternatives[0]["guest"], "x0")

    def test_force_needs_all_trends_variants_and_confidence(self):
        cfg = config()
        self.assertEqual(select_strategy(force_profile(), "VO", cfg=cfg)["strategy"], "VO_FORCE")
        self.assertNotEqual(select_strategy(force_profile(.96), "VO", cfg=cfg)["strategy"], "VO_FORCE")
        profile = force_profile()
        profile["policies"]["VO_FORCE"]["rows"].pop()
        self.assertNotEqual(select_strategy(profile, "VO", cfg=cfg)["strategy"], "VO_FORCE")
        profile = force_profile()
        rows = profile["policies"]["VO_FORCE"]["rows"]
        rows[1] = dict(rows[0])
        self.assertNotEqual(select_strategy(profile, "VO", cfg=cfg)["strategy"], "VO_FORCE")

    def test_unknown_or_missing_sensitivity_blocks_force(self):
        self.assertNotEqual(select_strategy(force_profile(), "VO", cfg=config(), force_blockers=("unknown mastery",))["strategy"], "VO_FORCE")
        self.assertNotEqual(select_strategy(force_profile(), "VO", cfg=config(sensitivity=False))["strategy"], "VO_FORCE")

    def test_candidates_preserve_hybrid_and_guest_mechanics(self):
        cards = tuple(feature("s" + str(i), sp=2, stamina=2) for i in range(4))
        hybrid = feature("hybrid", mastery=30, sp=30, stamina=30, cap=400)
        for slot in ("active", "guest"):
            pool = prune_candidates((*cards, hybrid), "VO", slot, config(candidate_per_role=1))
            self.assertIn("hybrid", [c.card.card_id for c in pool])

    def test_zero_prior_never_fills_a_role(self):
        """2026-10-07 audit: zero-score ties sorted by ID admitted low-ID R cards to AUDITION_MASTERY."""
        blanks = tuple(replace(feature("a" + str(i), mastery=0, sp=0, stamina=0, cap=0, multiplier=0),
                               stats={}, panels=(), support_effects=(), owned_live=None) for i in range(3))
        blanks = tuple(replace(f, card=replace(f.card, events=())) for f in blanks)
        strong = feature("z-strong")
        pool = prune_candidates((*blanks, strong), "VO", "guest", config(candidate_per_role=3))
        self.assertEqual([c.card.card_id for c in pool], ["z-strong"])

    def test_guest_only_card_fills_guest_never_active(self):
        p = feature("p", kind="P")
        actives = tuple(feature("s" + str(i)) for i in range(4))
        guest_only = replace(feature("g", mastery=40, sp=40, stamina=40),
                             owned_live=None, unknowns=("owned_live_skills",))
        self.assertTrue(guest_only.ready_as_guest and not guest_only.ready)
        teams, _ = beam_search(p, (*actives, guest_only), "VO", GrowthConfig.from_dict(growth_input()), config())
        self.assertTrue(teams)
        self.assertTrue(all("g" not in [c.card.card_id for c in t.active] for t in teams))
        self.assertTrue(any(t.guest.card.card_id == "g" for t in teams))
        team = next(t for t in teams if t.guest.card.card_id == "g")
        self.assertFalse(any(u.startswith("g:owned_live") for u in team.projection.unknowns))
        guest_support = next(s for s in _battle_supports(team) if s.guest)
        self.assertIsNone(guest_support.owned_skill)
        cards = {c.card_id: c for c in [card("p", kind="P"), *[card("s" + str(i)) for i in range(4)]]}
        cards["g"] = replace(card("g", mastery=40), owned_live_skills=())
        inventory = {k: Ownership(k, 4, 80) for k in cards}
        result = find_best_teams(cards, inventory, "p", growth=GrowthConfig.from_dict(growth_input()), config=config())
        self.assertEqual(result["guest_only_cards"], {"g": ["owned_live_skills"]})
        self.assertNotIn("g", result["rejected_cards"])
        self.assertEqual(result["recommendation"]["guest"], "g")

    def test_beam_legal_no_card_sum_final_and_metadata_invariant(self):
        p = feature("p", kind="P")
        supports = tuple(feature("s" + str(i)) for i in range(5))
        growth = GrowthConfig.from_dict(growth_input())
        teams, report = beam_search(p, supports, "VO", growth, config())
        self.assertTrue(teams)
        self.assertLessEqual(report["projections"], 300)
        for c in teams:
            self.assertEqual(len({x.card.idol for x in (c.p, *c.active, c.guest)}), 6)
        changed = tuple(replace(c, card=replace(c.card, rarity="Twilight")) for c in supports)
        other, _ = beam_search(p, changed, "VO", growth, config())
        self.assertEqual(teams[0].key, other[0].key)
        self.assertEqual(teams[0].projection.effective_stats, other[0].projection.effective_stats)

    def test_guest_is_the_card_with_the_least_audition_value(self):
        # 2026-10-31 (user: a UR sat in the Guest while a 2.5x card played): growth counts all five supports alike,
        # only the Active four enter the audition deck, so the weakest own Live sits in the Guest.
        p = feature("p", kind="P", idol="p")
        strong = feature("ur", idol="u", multiplier=4.0)
        others = tuple(feature(f"s{i}", idol=f"i{i}", multiplier=3.5) for i in range(3))
        weak = feature("weak", idol="w", multiplier=2.5)
        growth = GrowthConfig.from_dict(growth_input())
        best = best_slotting(p, (*others, weak), strong, "VO", growth)
        self.assertEqual(best.guest.card.card_id, "weak")
        self.assertIn("ur", {c.card.card_id for c in best.active})
        projection = best.projection
        self.assertGreater(audition_hand(projection, best.active, "VO"),
                           audition_hand(projection, (*others, weak), "VO"))
        self.assertGreater(growth_readiness(projection, "VO", best.active),
                           growth_readiness(projection, "VO", (*others, weak)))
        # Without the team the old skill-only term is unchanged.
        self.assertEqual(growth_readiness(projection, "VO"), growth_readiness(projection, "VO"))

    def test_capped_hand_still_puts_the_stronger_card_on_stage(self):
        # 2026-11-01 (user's Vo team: UR and a 3.5x SSR tied at 92.86 because the hand term reached its cap): equal
        # readiness goes to the stronger uncapped deck.
        p = feature("p", kind="P", idol="p")
        growth = GrowthConfig.from_dict(growth_input())
        base = tuple(feature(f"s{i}", idol=f"i{i}", multiplier=50) for i in range(3))
        lo = feature("lo", idol="l", multiplier=50)
        hi = replace(feature("hi", idol="h", multiplier=50), stats={**lo.stats, "Vo": lo.stats["Vo"] + 200})
        for active, guest in (((*base, lo), hi), ((*base, hi), lo)):
            best = best_slotting(p, active, guest, "VO", growth)
            self.assertEqual(audition_hand(best.projection, best.active, "VO"), 1)
            self.assertGreater(audition_hand(best.projection, best.active, "VO", cap=False), 1)
            self.assertIn("hi", {c.card.card_id for c in best.active})   # any of the four equal cards may sit out

    def test_hand_counts_the_supports_stats_and_needs_three_cards(self):
        p = feature("p", kind="P", idol="p")
        growth = GrowthConfig.from_dict(growth_input())
        team = tuple(feature(f"s{i}", idol=f"i{i}") for i in range(4))
        projection = estimate_team_growth(p, team, feature("g", idol="g"), "VO", growth)
        self.assertTrue(0 < audition_hand(projection, team, "VO") <= 1)
        stronger = tuple(feature(f"t{i}", idol=f"j{i}", multiplier=5) for i in range(4))
        self.assertGreater(audition_hand(projection, stronger, "VO"), audition_hand(projection, team, "VO"))
        empty = replace(projection, skill_plan=replace(projection.skill_plan, learned_skills=()))
        self.assertEqual(audition_hand(empty, team[:2], "VO"), 0)

    def test_readiness_does_not_punish_a_team_with_more_panels_to_buy(self):
        # 2026-10-25: SP counts against a fixed line, not the team's own planned panel cost.
        p, guest = feature("p", kind="P"), feature("g")
        active = tuple(feature("s" + str(i)) for i in range(4))
        projection = estimate_team_growth(p, active, guest, "VO", GrowthConfig.from_dict(growth_input()))
        richer = replace(projection, sp_required=projection.sp_required * 3, sp_readiness=projection.sp_readiness / 3)
        self.assertEqual(growth_readiness(richer, "VO"), growth_readiness(projection, "VO"))
        more_sp = replace(projection, sp=projection.sp + 50)
        self.assertGreater(growth_readiness(more_sp, "VO"), growth_readiness(projection, "VO"))

    def test_readiness_counts_learned_passives_as_their_own_term(self):
        # 2026-10-27 (user): passives count by chance and count, 5 of 100 points, without moving the stat term.
        from agent.core.true_end.growth import passive_turn_buff
        from agent.core.true_end.optimizer import PASSIVE_LINE
        p, guest = feature("p", kind="P"), feature("g")
        projection = estimate_team_growth(p, tuple(feature("s" + str(i)) for i in range(4)), guest, "VO",
                                          GrowthConfig.from_dict(growth_input()))
        self.assertEqual(projection.skill_plan.passives, ())
        passive = {"id": "x", "attribute": "Vo", "buff": .2, "probability": .4, "max_activations": 2}
        plan = replace(projection.skill_plan, passives=(passive,))
        gain = growth_readiness(replace(projection, skill_plan=plan), "VO") - growth_readiness(projection, "VO")
        self.assertAlmostEqual(gain, 5 * passive_turn_buff(passive) / PASSIVE_LINE)
        da = replace(projection.skill_plan, passives=({**passive, "attribute": "Da"},))
        self.assertEqual(growth_readiness(replace(projection, skill_plan=da), "VO"), growth_readiness(projection, "VO"))
        full = replace(projection.skill_plan, passives=({**passive, "buff": 5.0, "probability": 1, "max_activations": 6},))
        self.assertAlmostEqual(growth_readiness(replace(projection, skill_plan=full), "VO") - growth_readiness(projection, "VO"), 5)

    def test_live_reserve_keeps_a_card_with_strong_live_passives(self):
        # 2026-10-27 (user): 吼・吼・熱・々 ranked 11-27 in every role and never reached the beam.
        passive = {"id": "pass", "kind": "passive", "sp_cost": 30, "required_lb": 0, "prerequisites": ["root"],
                   "prerequisites_known": True,
                   "effects": {"id": "pass", "attribute": "Vo", "buff": .5, "probability": .4, "max_activations": 2}}
        plain = tuple(feature("s" + str(i), mastery=20 - i) for i in range(6))
        data = card("zlive", mastery=1)   # loses every tie on card id
        live = extract_card_features(replace(data, panels=(*data.panels, Panel.from_dict(passive))), Ownership("zlive", 4, 80))
        cfg = config(candidate_per_role=1)
        kept = {c.card.card_id for c in prune_candidates((*plain, live), "VO", "active", cfg)}
        self.assertIn("zlive", kept)
        with patch("agent.core.true_end.optimizer.LIVE_RESERVE", 0):
            self.assertNotIn("zlive", {c.card.card_id for c in prune_candidates((*plain, live), "VO", "active", cfg)})

    def test_beam_does_not_charge_for_an_unmodelled_extra_skill(self):
        # 2026-10-25: an unmodelled support skill only adds value (counted as zero), so a card that is otherwise better
        # must not lose its beam place to the points once charged per gap.
        perfect = {"id": "パーフェクトマスタリー", "kind": "unmodeled_perfect_mastery", "levels": {"1": 8},
                   "unsupported": ["mechanic not modeled: パーフェクトが発生"]}
        supports = (*(feature("s" + str(i)) for i in range(5)), feature("strong", mastery=11, extra=(perfect,)))
        growth = GrowthConfig.from_dict(growth_input(starting_caps={"Vo": 2000, "Da": 500, "Vi": 500, "Me": 500}))
        teams, _ = beam_search(feature("p", kind="P"), supports, "VO", growth, config(beam_width=1, candidate_per_role=10))
        self.assertIn("strong", {c.card.card_id for c in (*teams[0].active, teams[0].guest)})
        self.assertTrue(any("unmodeled_perfect_mastery" in u for u in teams[0].projection.unknowns))

    def test_minimal_budget_reaches_full_team(self):
        teams, report = beam_search(feature("p", kind="P"), tuple(feature("s" + str(i)) for i in range(5)),
                                    "VO", GrowthConfig.from_dict(growth_input()), config(projection_budget=4))
        self.assertTrue(teams)
        self.assertEqual(report["projections"], 4)

    def test_unknown_owned_card_rejected_and_guest_must_be_owned(self):
        cards = {c.card_id: c for c in [card("p", kind="P"), *[card("s" + str(i)) for i in range(4)]]}
        inventory = {k: Ownership(k, 4, 80) for k in cards} | {"missing": Ownership("missing", 0, 60)}
        result = find_best_teams(cards, inventory, "p", growth=GrowthConfig.from_dict(growth_input()), config=config())
        self.assertEqual(result["status"], "BLOCKED_DATA")
        self.assertIn("missing", result["rejected_cards"])
        self.assertIsNone(result["true_end_probability"])

    def test_stage_uses_before_state_and_guest_panel_as_p_skill(self):
        p = feature("p", kind="P")
        active = tuple(feature("s" + str(i)) for i in range(4))
        guest = feature("g", multiplier=5)
        growth = GrowthConfig.from_dict(growth_input(starting_sp=1000))
        projection = estimate_team_growth(p, active, guest, "VO", growth)
        candidate = TeamCandidate(p, active, guest, "VO", projection)
        idol = _stage(candidate, "s3_odotte")
        self.assertLessEqual(idol.stats["Vo"], projection.effective_stats["Vo"])
        self.assertLess(idol.week, 24)
        self.assertTrue(any("g-live" in s.id and s.source == "produce" for s in idol.learned_skills))

    def test_actual_simulation_conditional_and_small_sample_no_force(self):
        p = feature("p", kind="P")
        active, guest = tuple(feature("s" + str(i)) for i in range(4)), feature("g")
        growth = GrowthConfig.from_dict(growth_input())
        candidate = TeamCandidate(p, active, guest, "VO", estimate_team_growth(p, active, guest, "VO", growth))
        profile = evaluate_battle_profile(candidate, (scenario(),), config(sensitivity=False), trials=2, policies=("VO_FORCE",))
        self.assertEqual(len(profile["policies"]["VO_FORCE"]["rows"]), 24)
        self.assertIsNone(profile["true_end_probability"])
        self.assertEqual(profile["status"], "CONDITIONAL_SIMULATION")
        self.assertLess(profile["policies"]["VO_FORCE"]["worst_lower_bound"], .97)

    def test_bad_hand_uses_attribute_and_p_support_damage_not_multiplier(self):
        skills = tuple(BattleSkill(key, "produce", ((attr, mult),)) for key, attr, mult in
                       (("p-main", "Vo", 5), ("p-off1", "Da", 4), ("p-off2", "Da", 4), ("p-weak", "Vi", 1)))
        idol = ProducedIdol({"Vo": 1200, "Da": 50, "Vi": 30}, 350, skills, 32)
        supports = tuple(BattleSupport(f"s{i}", {"Vo": 260, "Da": 240, "Vi": 220},
                            BattleSkill(f"s{i}-live", "support", (("Vo", 2.5),), f"s{i}")) for i in range(4))
        self.assertEqual(set(_bad_hand_ids(idol, supports, "Vo", scenario())), {"p-off1", "p-off2", "p-weak"})

    def test_unmapped_passive_condition_never_becomes_always_on(self):
        p = _passive({"attribute": "Vo", "buff": 1, "probability": 1, "max_activations": 5,
                      "condition": "requires three named idols"}, 0)
        self.assertTrue(p.unsupported)
        with self.assertRaises(ValueError):
            _passive({"buff": 1, "probability": 1, "max_activations": 1}, 0)

    def test_legend_binds_named_audition_in_custom_route(self):
        p, guest = feature("p", kind="P"), feature("g")
        active = tuple(feature("s" + str(i)) for i in range(4))
        growth = GrowthConfig.from_dict(growth_input())
        projection = estimate_team_growth(p, active, guest, "VO", growth)
        timeline = [dict(row) for row in projection.timeline]
        for row in timeline[24:]:
            row["audition_id"] = None
        timeline[24].update(planned_action="audition", audition_id="s4_festival")
        timeline[29].update(planned_action="audition", audition_id="legend")
        before = dict(timeline[28]["stats"])
        before["Vo"] = 432
        timeline[28]["stats"] = before
        c = TeamCandidate(p, active, guest, "VO", replace(projection, timeline=tuple(timeline)))
        self.assertEqual(_stage(c, "legend").stats["Vo"], 432)
        self.assertEqual(_stage(c, "legend").week, 29)

    def test_marginal_recomputes_whole_team_with_same_slots(self):
        p, guest = feature("p", kind="P"), feature("g")
        active = tuple(feature("s" + str(i), mastery=2, sp=1, stamina=0) for i in range(4))
        # Vo below its cap, so the stronger card's growth is visible (the flexible plan reaches a 500 cap).
        growth = GrowthConfig.from_dict(growth_input(starting_caps={"Vo": 2000, "Da": 500, "Vi": 500, "Me": 500}))
        candidate = TeamCandidate(p, active, guest, "VO", estimate_team_growth(p, active, guest, "VO", growth))
        result = calculate_marginal_card_value(candidate, feature("upgrade", mastery=25, sp=15, stamina=10),
                                               0, growth, config(), ())
        self.assertEqual(result["removed"], ["s0"])
        self.assertEqual(result["added"], ["upgrade"])
        self.assertGreater(result["stat_delta"]["Vo"], 0)

    def test_end_to_end_without_network_or_input_reports_estimated(self):
        definitions = [card("p", kind="P"), *[card("s" + str(i)) for i in range(5)]]
        value = {"schema_version": 1, "p_card": "p", "cards": [asdict(c) for c in definitions],
                 "inventory": [asdict(Ownership(c.card_id, 4, 80)) for c in definitions],
                 "growth": growth_input(), "search": asdict(config()), "battle_scenarios": []}
        result = run_input(value)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["recommendation"]["rating_label"], "ESTIMATED")
        self.assertEqual(result["input_budget"], 0)
        self.assertEqual(result["resource_budget"], 0)
        self.assertEqual(len(result["data_snapshot_sha256"]), 64)
        self.assertIn("ESTIMATED", render_report(result))
        value["cards"].append(asdict(card("other-p", kind="P")))
        value["inventory"].append(asdict(Ownership("other-p", 4, 80)))
        first = run_input(value)
        value["p_card"] = "other-p"
        second = run_input(value)
        self.assertNotEqual(first["data_snapshot_sha256"], second["data_snapshot_sha256"])
        value["search"]["difficulty"] = "Master"
        with self.assertRaises(ValueError):
            run_input(value)

    def test_invalid_limits_and_scenario_rejected(self):
        for bad in ({"beam_width": 1001}, {"seed": True}, {"force_threshold": float("nan")},
                    {"confirmation_trials": 0}, {"unknown_search_option": 1}):
            with self.assertRaises(ValueError):
                config(**bad)
        with self.assertRaises(ValueError):
            scenario_from_dict({"overkill_credit": "false", "assumptions": ["explicit"]})


if __name__ == "__main__":
    unittest.main()

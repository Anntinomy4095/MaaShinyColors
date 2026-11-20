"""Offline mechanics contracts; no runtime/game integration."""
from dataclasses import replace
from random import Random
from itertools import permutations
import unittest
from unittest.mock import patch

from agent.core.true_end.battle import (
    ATTRIBUTES, Audition, BattleSkill, BattleSupport, Npc, Passive, ProducedIdol,
    Scenario, award_bonus, award_ta, award_la, build_deck, calculate_p_appeal,
    calculate_support_appeal, evaluate_scenarios, excellent_components, middle_audition, npc_target,
    placement, simulate_audition, wilson_interval,
)


def scenario(**changes):
    base = Scenario("fixture", (("Perfect", 1.),), (("Normal", 1.),), 1., "uniform",
                    1., "fifo", "player_loses", False,
                    ("确定性离线夹具；判定/回收/反击仅为指定情景",))
    return replace(base, **changes)


def team(p=1000, s=0, mental=500):
    skills = tuple(BattleSkill(f"p{i}", "produce", (("Vo", 2.),)) for i in range(4))
    idol = ProducedIdol({"Vo": p, "Da": 0, "Vi": 0}, mental, skills, 33)
    supports = tuple(BattleSupport(f"s{i}", dict.fromkeys(ATTRIBUTES, s),
        BattleSkill(f"owned{i}", "support", (("Vo", 2.),), f"s{i}"), i == 4) for i in range(5))
    return idol, supports


def arena(hp=100, *, damage=0, npcs=()):
    return Audition("fixture", dict.fromkeys(ATTRIBUTES, hp), ATTRIBUTES, npcs, 1,
                    damage, 1 if damage else 0)


class AppealFormulaTests(unittest.TestCase):
    def test_wiki_final_example_exact(self):
        self.assertEqual(calculate_p_appeal(600, 800, 33, 2.5, "Perfect", excellent=True), 14160)

    def test_int_order_keeps_base_fraction_then_truncates(self):
        self.assertEqual(calculate_p_appeal(111, 333, 17, 2.3, "Good", buff=.17,
                                          excellent=True, interest=.93), 2211)
        self.assertEqual(calculate_support_appeal(111, 333, 99, 17, 2.3, "Good", buff=.17,
                                                excellent=True, interest=.93), 2176)

    def test_support_uses_own_stat_and_distinct_formula(self):
        weak = calculate_support_appeal(600, 800, 100, 33, 2.5, excellent=True)
        strong = calculate_support_appeal(600, 800, 400, 33, 2.5, excellent=True)
        self.assertLess(weak, strong)
        self.assertNotEqual(strong, calculate_p_appeal(600, 800, 33, 2.5, excellent=True))

    def test_judgment_excellent_and_interest(self):
        normal = calculate_p_appeal(100, 0, 0, 1., "Normal")
        self.assertEqual(calculate_p_appeal(100, 0, 0, 1., "Perfect", excellent=True), normal * 3)
        self.assertEqual(calculate_p_appeal(100, 0, 0, 1., "Bad", interest=.5), 50)


class StarTests(unittest.TestCase):
    def test_bonus_is_personal_not_global_crossing(self):
        self.assertEqual(award_bonus(29, 100, 1), 0)
        self.assertEqual(award_bonus(30, 100, 1), 4)
        self.assertEqual(award_bonus(60, 100, 1), 8)
        self.assertEqual(award_bonus(90, 100, 1), 12)
        self.assertEqual(sum(award_bonus(c, 100, 1) for c in (25, 25, 25, 25)), 0)
        self.assertEqual(sum(award_bonus(c, 100, 1) for c in (55, 25, 20)), 4)

    def test_ta_la_and_unknown_ta_tie_are_explicit(self):
        self.assertEqual(award_ta({"player": 55, "A": 45}, 2, tie="all"), {"player": 15})
        self.assertEqual(award_la("A", 3), {"A": 4})
        self.assertEqual(award_ta({"player": 50, "A": 50}, 1, tie="player_loses"), {"A": 20})
        self.assertEqual(award_ta({"player": 50, "A": 50}, 1, tie="all"), {"player": 20, "A": 20})

    def test_wing_equal_stars_equal_rank(self):
        self.assertEqual(placement({"player": 24, "A": 24}), 1)
        self.assertEqual(placement({"player": 18, "A": 24, "B": 18}), 2)


class BattleTests(unittest.TestCase):
    def test_safe_middle_wing_target_table_and_force_remains_rank_one(self):
        # Low-power baseline: not a damage/stat threshold and never lone rank-3 TA.
        expected_by_main_rank = {
            "semifinal": {"Vo": (1, 2, 2), "Da": (2, 2, 2), "Vi": (2, 2, 2)},
            "final": {"Vo": (2, 2, 2), "Da": (1, 2, 2), "Vi": (1, 2, 2)},
        }
        idol, supports = team(p=500, s=200)
        for audition_id, by_attr in expected_by_main_rank.items():
            for main, targets in by_attr.items():
                for trend in permutations(ATTRIBUTES):
                    with self.subTest(audition=audition_id, main=main, trend=trend):
                        battle = middle_audition(audition_id, trend)
                        run = simulate_audition(idol, supports, battle, f"{main.upper()}_SAFE", Random(7),
                                                 scenario(max_turns=1), keep_trace=True)
                        pick = next(e for e in run.trace if e["event"] == "selection")
                        self.assertEqual(pick["target"], trend[targets[trend.index(main)] - 1])
        run = simulate_audition(idol, supports, middle_audition("final", ("Da", "Vi", "Vo")),
                                 "VO_FORCE", Random(7), scenario(max_turns=1), keep_trace=True)
        self.assertEqual(next(e["target"] for e in run.trace if e["event"] == "selection"), "Da")

    def test_low_vo_third_trend_replay_builds_second_trend_contribution(self):
        idol, supports = team(p=500, s=100)
        battle = middle_audition("final", ("Da", "Vi", "Vo"))
        run = simulate_audition(idol, supports, battle, "VO_SAFE", Random(10),
                                 scenario(max_turns=2), keep_trace=True)
        picks = [e for e in run.trace if e["event"] == "selection"]
        self.assertEqual([p["target"] for p in picks], ["Vi", "Vi"])
        self.assertGreater(run.contributions["Vi"]["player"], 0)
        self.assertEqual(run.contributions["Vo"]["player"], 0)

    def test_safe_and_aware_alias_is_disclosed(self):
        idol, supports = team(p=500)
        battle = middle_audition("final", ("Da", "Vi", "Vo"))
        safe = simulate_audition(idol, supports, battle, "VO_SAFE", Random(3), scenario())
        aware = simulate_audition(idol, supports, battle, "VO_TREND_AWARE", Random(3), scenario())
        self.assertEqual(safe.stars, aware.stars)
        self.assertEqual(safe.contributions, aware.contributions)
        self.assertTrue(any("别名" in a for a in aware.assumptions))

    def test_target_and_card_cannot_see_future_random_judgment(self):
        idol, supports = team(p=100)
        # Vo can be finished on Perfect, not Bad. Both must commit the same
        # preview-based LA choice before the timing result is known.
        battle = replace(arena(), id="final", hp={"Vo": 1000, "Da": 999999, "Vi": 999999})
        picks = []
        for judgment in ("Perfect", "Bad"):
            with patch("agent.core.true_end.battle._sample", return_value=judgment):
                run = simulate_audition(idol, supports, battle, "VO_SAFE", Random(9),
                    scenario(max_turns=1, initial_hand_ids=("p0", "p1", "p2")), keep_trace=True)
            pick = next(e for e in run.trace if e["event"] == "selection")
            picks.append((pick["target"], pick["skill"]))
        self.assertEqual(picks, [("Vo", "p0"), ("Vo", "p0")])

    def test_all_npcs_retiring_ends_battle_before_judges_are_satisfied(self):
        idol, supports = team(p=100)
        npc = Npc("A", "Vo", 1, (1,), False, 1)
        battle = replace(arena(999999, damage=10, npcs=(npc,)), reaction_targets=2)
        run = simulate_audition(idol, supports, battle, "VO_FORCE", Random(5), scenario(), keep_trace=True)
        self.assertTrue(run.complete)
        self.assertTrue(run.won)
        self.assertEqual(run.turns, 1)
        self.assertEqual(run.ta, ())
        self.assertFalse(any(e["event"] == "appeal" and e["actor"] == "A" for e in run.trace))

    def test_guest_neither_stats_nor_owned_card_but_learned_panel_kept(self):
        idol, supports = team()
        learned = replace(idol.learned_skills[0], id="learned_from_guest")
        idol = replace(idol, learned_skills=(learned,) + idol.learned_skills[1:])
        deck = build_deck(idol, supports)
        self.assertIn("learned_from_guest", [s.id for s in deck])
        self.assertNotIn("owned4", [s.id for s in deck])
        stronger_guest = supports[:-1] + (replace(supports[-1], stats=dict.fromkeys(ATTRIBUTES, 99999)),)
        first = simulate_audition(idol, supports, arena(), "VO_FORCE", Random(4), scenario())
        second = simulate_audition(idol, stronger_guest, arena(), "VO_FORCE", Random(4), scenario())
        self.assertEqual(first, second)
        swapped = tuple(replace(s, guest=(s.id == "s0")) for s in stronger_guest)
        self.assertIn("owned4", [s.id for s in build_deck(idol, swapped)])
        self.assertNotIn("owned0", [s.id for s in build_deck(idol, swapped)])

    def test_excellent_appeal_hits_any_judge_as_excellent(self):
        """「Excellent3倍アピール」: target attribute stats, always Excellent (ASSUMPTION on the base attribute)."""
        idol, supports = team(p=1000, s=100)
        idol = replace(idol, stats={"Vo": 1000, "Da": 400, "Vi": 200})
        exc = BattleSkill("exc", "support", (), "s0", excellent=3.)
        for target in ATTRIBUTES:
            parts = excellent_components(exc, target)
            self.assertEqual(parts, ((target, 3., True),))
        vo3 = BattleSkill("vo3", "support", (("Vo", 3.),), "s0")
        self.assertEqual(excellent_components(vo3, "Da"), (("Vo", 3., False),))
        supports = (replace(supports[0], owned_skill=exc),) + supports[1:]
        totals = {a: sum(s.stats[a] for s in supports if not s.guest) for a in ATTRIBUTES}
        expected = calculate_support_appeal(idol.stats["Da"], totals["Da"], 100, idol.week, 3., excellent=True)
        hand = ("exc", "p0", "p1")
        da_only = Audition("fixture", {"Vo": 10 ** 9, "Da": 10 ** 9, "Vi": 10 ** 9}, ("Da", "Vo", "Vi"), (), 1, 0, 0)
        result = simulate_audition(idol, supports, da_only, "VO_FORCE", Random(1),
                                   scenario(initial_hand_ids=hand, max_turns=1), keep_trace=True)
        hits = [e for e in result.trace if e["event"] == "appeal"]
        self.assertEqual(hits[0]["target"], "Da")
        # FORCE on Da: the Vo card is not Excellent there; the Excellent card is (3.0 > 2.0 * 0.5 stat ratio).
        self.assertEqual(hits[0]["value"], expected)
        self.assertTrue(any("Excellentアピール" in a for a in result.assumptions))
        self.assertFalse(any(a.startswith("unsupported:s0:exc") for a in result.assumptions))
        with self.assertRaises(ValueError):
            simulate_audition(idol, (replace(supports[0], owned_skill=replace(exc, excellent=-1.)),) + supports[1:],
                              arena(), "VO_FORCE", Random(1), scenario())

    def test_complete_battle_credits_clipped_hp_and_awards_all_stars(self):
        idol, supports = team()
        result = simulate_audition(idol, supports, arena(), "VO_FORCE", Random(3), scenario(), keep_trace=True)
        self.assertTrue(result.won)
        self.assertEqual(result.stars["player"], 90)
        self.assertEqual(set(result.ta), set(ATTRIBUTES))
        self.assertEqual(set(result.la), set(ATTRIBUTES))
        self.assertTrue(all(result.contributions[a]["player"] == 100 for a in ATTRIBUTES))
        self.assertEqual(arena().hp["Vo"], 100)  # inputs not mutated

    def test_good_before_reaction_normal_after_reaction(self):
        idol, supports = team(p=1000, mental=1)
        normal = scenario(player_judgments=(("Normal", 1.),), reaction_mode="player_always")
        result = simulate_audition(idol, supports, arena(damage=10), "VO_FORCE", Random(1), normal, keep_trace=True)
        self.assertIn("mental_failure", result.failure_reasons)
        self.assertFalse(any(e["event"] == "appeal" and e["actor"] == "player" for e in result.trace))
        good = replace(normal, player_judgments=(("Good", 1.),))
        result = simulate_audition(idol, supports, arena(damage=10), "VO_FORCE", Random(1), good, keep_trace=True)
        events = [e["event"] for e in result.trace]
        self.assertLess(events.index("appeal"), events.index("reaction"))

    def test_equal_judgment_player_before_e_before_a(self):
        idol, supports = team(p=10)
        npcs = tuple(Npc(n, "Vo", 10, (1,), False, 100) for n in ("A", "E"))
        result = simulate_audition(idol, supports, arena(10000, npcs=npcs), "VO_FORCE", Random(5),
                                  scenario(npc_judgments=(("Perfect", 1.),), max_turns=1), keep_trace=True)
        self.assertEqual([e["actor"] for e in result.trace if e["event"] == "appeal"], ["player", "E", "A"])

    def test_real_hand_retains_unplayed_cards_and_delays_reuse(self):
        idol, supports = team(p=100)
        result = simulate_audition(idol, supports, arena(999999), "VO_FORCE", Random(6),
                                  scenario(max_turns=9), keep_trace=True)
        picks = [e for e in result.trace if e["event"] == "selection"]
        self.assertTrue(all(len(set(e["hand"])) == 3 and e["skill"] in e["hand"] for e in picks))
        self.assertEqual(len({e["skill"] for e in picks[:6]}), 6)
        self.assertEqual(set(picks[0]["hand"]) - {picks[0]["skill"]}, set(picks[1]["hand"]) & set(picks[0]["hand"]))

    def test_passive_addition_condition_and_activation_budget(self):
        idol, supports = team(p=100)
        idol = replace(idol, passives=(Passive("a", "Vo", .5, 1., 1), Passive("b", "Vo", .5, 1., 1),
            Passive("unmet", "Vo", 9., 1., 3, mental_ratio_max=.1)))
        result = simulate_audition(idol, supports, arena(999999), "VO_FORCE", Random(2),
                                  scenario(max_turns=2, initial_hand_ids=("p0", "p1", "p2")), keep_trace=True)
        appeals = [e["value"] for e in result.trace if e["event"] == "appeal"]
        self.assertEqual(appeals, [2400, 1200])
        self.assertEqual([e["id"] for e in result.trace if e["event"] == "passive"], ["a", "b"])

    def test_skill_buff_starts_next_action_and_duration_counts_cast_turn(self):
        idol, supports = team(p=100)
        skills = tuple(replace(s, buffs=(("Vo", .5, 2),)) for s in idol.learned_skills)
        idol = replace(idol, learned_skills=skills)
        result = simulate_audition(idol, supports, arena(999999), "VO_FORCE", Random(2),
                                  scenario(max_turns=3, initial_hand_ids=("p0", "p1", "p2")), keep_trace=True)
        appeals = [e["value"] for e in result.trace if e["event"] == "appeal"]
        self.assertEqual(appeals, [1200, 1800, 1800])

    def test_memory_hits_all_judges_and_does_not_consume_hand(self):
        idol, supports = team(p=100)
        idol = replace(idol, memory_level=2)
        result = simulate_audition(idol, supports, arena(999999), "VO_FORCE", Random(2),
                                  scenario(max_turns=2, player_memory_turns=(1,)), keep_trace=True)
        picks = [e for e in result.trace if e["event"] == "selection"]
        self.assertEqual(picks[0]["skill"], "memory")
        self.assertEqual(picks[0]["hand"], picks[1]["hand"])
        self.assertEqual(len([e for e in result.trace if e["turn"] == 1 and e["event"] == "appeal"]), 3)
        self.assertTrue(any("脚本" in a for a in result.assumptions))

    def test_unsupported_effects_are_visible_and_passive_not_assumed_active(self):
        idol, supports = team(p=100)
        idol = replace(idol, passives=(Passive("unknown", "Vo", 9., 1., 1, unsupported=("unit condition",)),))
        result = simulate_audition(idol, supports, arena(), "VO_FORCE", Random(2), scenario(), keep_trace=True)
        self.assertIn("unsupported:unknown:unit condition", result.assumptions)
        self.assertFalse(any(e["event"] == "passive" for e in result.trace))

    def test_cycle_and_spear_targets_use_trend_rank(self):
        hp = dict.fromkeys(ATTRIBUTES, 100)
        npc = Npc("A", "Vo", 1, (3, 1, 2), True, 1)
        self.assertEqual(npc_target(npc, 2, ("Da", "Vi", "Vo"), hp), "Da")
        hp["Da"] = 0
        self.assertEqual(npc_target(npc, 2, ("Da", "Vi", "Vo"), hp), "Vi")
        spear = replace(npc, cycle=False, target_ranks=(1, 3, 2))
        self.assertEqual(npc_target(spear, 7, ("Da", "Vi", "Vo"), hp), "Vo")

    def test_middle_database_source_and_critical_values(self):
        final = middle_audition("final", ATTRIBUTES)
        self.assertEqual(final.hp["Vo"], 72000)
        self.assertEqual(final.npcs[2].base_appeal, 4636)
        self.assertEqual(final.npcs[2].attribute, "Vo")
        self.assertFalse(final.npcs[2].cycle)
        self.assertEqual(final.npcs[2].target_ranks[:2], (1, 2))
        self.assertEqual(middle_audition("legend", ATTRIBUTES).pass_rank, 2)
        self.assertEqual(middle_audition("s3_odotte", ATTRIBUTES).hp["Vo"], 52000)
        self.assertIn("wikiwiki.jp", final.source)
        with self.assertRaises(ValueError):
            middle_audition("Master", ATTRIBUTES)


class ConditionalProbabilityTests(unittest.TestCase):
    def test_nonfinite_formula_parameters_rejected(self):
        for invalid in (float("nan"), float("inf"), float("-inf")):
            for kwargs in ({"p": invalid}, {"support_total": invalid}, {"skill": invalid},
                           {"buff": invalid}, {"interest": invalid}, {"week": invalid}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    calculate_p_appeal(**({"p": 100, "support_total": 800, "week": 33, "skill": 2.5} | kwargs))
            with self.assertRaises(ValueError):
                calculate_support_appeal(100, 800, invalid, 33, 2.5)
            with self.assertRaises(ValueError):
                wilson_interval(1, 2, z=invalid)

    def test_nonfinite_battle_inputs_cannot_produce_probability(self):
        idol, supports = team()
        for invalid in (float("nan"), float("inf"), float("-inf")):
            malformed = (
                (replace(idol, stats={"Vo": invalid, "Da": 0, "Vi": 0}), supports, arena(), scenario()),
                (idol, supports, replace(arena(), hp={"Vo": invalid, "Da": 100, "Vi": 100}), scenario()),
                (idol, supports, arena(), scenario(npc_late_multiplier=invalid)),
                (idol, supports, arena(), scenario(fourth_turn_multiplier=invalid)),
                (idol, supports, arena(), scenario(player_judgments=(("Perfect", invalid),))),
                (idol, supports, arena(), scenario(player_buffs=(("Vo", invalid),))),
                (idol, supports, arena(), scenario(interest=(("Vo", invalid),))),
                (replace(idol, passives=(Passive("p", "Vo", invalid, 1., 1),)), supports, arena(), scenario()),
                (replace(idol, learned_skills=(replace(idol.learned_skills[0], components=(("Vo", invalid),)),)
                         + idol.learned_skills[1:]), supports, arena(), scenario()),
            )
            for args in malformed:
                with self.subTest(invalid=invalid, args=args), self.assertRaises(ValueError):
                    evaluate_scenarios(args[0], args[1], args[2], "VO_FORCE", (args[3],), trials=2, seed=1)
            with self.assertRaises(ValueError):
                evaluate_scenarios(idol, supports, arena(), "VO_FORCE", (scenario(),), trials=2, seed=1,
                                   confidence_z=invalid)

    def test_small_all_win_sample_does_not_prove_97_percent(self):
        self.assertLess(wilson_interval(10, 10)[0], .97)
        self.assertGreater(wilson_interval(500, 500)[0], .97)

    def test_incomplete_runs_do_not_become_wins_or_losses(self):
        idol, supports = team(p=1)
        result = evaluate_scenarios(idol, supports, arena(999999), "VO_FORCE",
                                    (scenario(max_turns=1),), trials=3, seed=42)
        row = result["scenarios"][0]
        self.assertEqual(row["completed"], 0)
        self.assertEqual(row["incomplete"], 3)
        self.assertIsNone(row["win_probability"])
        self.assertEqual(result["worst_lower_bound"], 0.)
        self.assertIsNone(result["true_end_probability"])

    def test_reproducible_conditional_statistics(self):
        idol, supports = team()
        first = evaluate_scenarios(idol, supports, arena(), "VO_FORCE", (scenario(),), trials=8, seed=42)
        second = evaluate_scenarios(idol, supports, arena(), "VO_FORCE", (scenario(),), trials=8, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(first["label"], "CONDITIONAL_SIMULATION")
        self.assertEqual(first["scenarios"][0]["win_probability"], 1.)
        self.assertLess(first["worst_lower_bound"], .97)


if __name__ == "__main__":
    unittest.main()

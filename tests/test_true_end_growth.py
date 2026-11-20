"""Synthetic mechanics fixtures: no game samples or live input."""
import unittest
from dataclasses import replace

from agent.core.true_end.features import mechanical_vector
from agent.core.true_end.growth import (GrowthConfig, estimate_team_growth,
                                       estimate_partial_growth, flex_me_target, panel_closure, passive_turn_buff)
from agent.core.true_end.models import CardData, Ownership, SupportSkill, extract_card_features, STATS
from agent.core.true_end.wiki import CardPageParser, MechanicsParser
from agent.core.support_effects import support_skill_expectation, expected_stamina_multiplier
from test_true_end_wiki import URL, small_card


def live(key, multiplier=1):
    return {"id": key, "components": {"Vo": multiplier}}


def panel(key, kind, effects, cost=10, prerequisites=(), **extra):
    return {"id": key, "kind": kind, "effects": effects, "sp_cost": cost,
            "prerequisites": list(prerequisites), "prerequisites_known": True, **extra}


def card(key, *, kind="S", skills=(), panels=(), events=(), stat=100, lb=4, unit=None):
    # Deliberately synthetic, reproducible mechanics; not asserted real-card values.
    data = CardData.from_dict({"card_id": key, "name": key, "idol": key, "kind": kind,
        "status": "OK", "source_url": "synthetic://growth-tests", "unit": unit,
        "te_eligible": True if kind == "P" else None,
        "levels": {"80": dict.fromkeys(STATS, stat)}, "max_levels": {"4": 80, "0": 80},
        "owned_live_skills": [live(key + "-own")], "support_skills": list(skills),
        "panels": list(panels), "events": list(events)})
    return extract_card_features(data, Ownership(key, lb, 80))


def stage_panel(idol, extra="", key="stage"):
    """ShinyColorsDB keeps 「<idol>がライブに参加している場合」 as unparsed text (shinycolorsdb_cards._passive_effects)."""
    desc = f"[条件:{idol}がライブに参加している場合] [確率:40%] [最大:2回]{extra}"
    return panel(key, "passive", {"buffs": {"Vo": .2}, "condition": f"{idol}がライブに参加している場合",
                                  "probability": .4, "max_activations": 2},
                 unsupported=[f"passive requires battle interpreter: Vocal20%UP / {desc}"])


def mastery(kind, level=10, attribute="Vo", room="Vo"):
    return {"id": kind, "kind": kind, "levels": {"1": level}, "attribute": attribute, "room": room}


def config(**kwargs):
    return GrowthConfig.from_dict({"starting_stats": dict.fromkeys(STATS, 100),
        "starting_live_skills": [live("start-" + str(n)) for n in range(4)],
        "starting_fans": 500000, **kwargs})


def project(cfg=None, *, p=None, supports=None, guest=None, strategy="VO"):
    return estimate_team_growth(p or card("p", kind="P"),
        supports or tuple(card("s" + str(i)) for i in range(4)),
        guest or card("guest"), strategy, cfg or config())


class GrowthTests(unittest.TestCase):
    def test_db_and_wiki_rarity_spellings_give_identical_growth(self):
        for rarity, kind, text, cfg in (
            ("SSR", "stamina_support", "レッスンかお仕事を選択する時に15%の確率で体力消費量-50%",
             config(actions=["Vo"] * 32, rest_threshold=0)),
            ("UR", "stamina_support", "レッスンかお仕事を選択する時に15%の確率で体力消費量-50%",
             config(actions=["Vo"] * 32, rest_threshold=0)),
        ):
            with self.subTest(rarity=rarity, kind=kind):
                raw = card("guest", skills=[{**mastery(kind, 5, None, None), "unsupported": [text]}])
                wiki = extract_card_features(replace(raw.card, rarity=rarity), raw.ownership)
                db = extract_card_features(replace(raw.card, rarity="S_" + rarity), raw.ownership)
                baseline, expected, actual = project(cfg), project(cfg, guest=wiki), project(cfg, guest=db)
                self.assertEqual(actual.timeline[0]["stamina"], expected.timeline[0]["stamina"])
                self.assertGreater(actual.timeline[0]["stamina"], baseline.timeline[0]["stamina"])
                self.assertEqual(actual.effective_stats, expected.effective_stats)
                self.assertFalse(any("unsupported support" in u for u in actual.unknowns))

    def test_standard_stamina_and_rest_support_use_real_rarity_and_level(self):
        def supporter(kind, text, rarity="SSR"):
            feature = card("guest", skills=[{**mastery(kind, 5, None, None), "unsupported": [text]}])
            data = replace(feature.card, rarity=rarity)
            return extract_card_features(data, feature.ownership)
        stamina = supporter("stamina_support", "レッスンかお仕事を選択する時に15%の確率で体力消費量-50%")
        effect = support_skill_expectation(*stamina.support_effects[0], stamina.card.rarity)
        self.assertAlmostEqual(effect["expected_stamina_reduction"], .075)
        cfg = config(actions=["Vo"] * 32, rest_threshold=0)
        baseline, saved = project(cfg), project(cfg, guest=stamina)
        self.assertGreater(saved.timeline[0]["stamina"], baseline.timeline[0]["stamina"])
        self.assertFalse(any("unsupported support" in u for u in saved.unknowns))
        # おやすみブースト is worth nothing and is no data gap, whatever its text or rarity.
        cfg = config(actions=["rest"] * 32, starting_stamina=0)
        for rarity in ("SR", "UR", None):
            rest = supporter("rest_support", "「休む」を選択時に15%の確率で体力回復量+10", rarity)
            result = project(cfg, guest=rest)
            self.assertEqual(result.timeline[0]["stamina"], 50)
            self.assertFalse(any("unsupported support" in u or "休む" in u for u in result.unknowns))
        unknown = supporter("stamina_support", "レッスンかお仕事を選択する時に15%の確率で体力消費量-50%", None)
        conflict = supporter("stamina_support", "レッスンかお仕事を選択する時に15%の確率で体力消費量-30%")
        for guest in (unknown, conflict):
            self.assertIsNone(support_skill_expectation(*guest.support_effects[0], guest.card.rarity))
            result = project(config(actions=["Vo"] * 32, rest_threshold=0), guest=guest)
            self.assertEqual(result.timeline[0]["stamina"], baseline.timeline[0]["stamina"])
            self.assertTrue(any("unsupported support" in u for u in result.unknowns))

    def test_stamina_reduction_stacking_has_zero_floor_per_outcome(self):
        effects = [{"activation_probability": .5, "stamina_reduction": .5}] * 3
        # 0/1/2/3 activations yield remaining costs 1/.5/0/0.
        self.assertAlmostEqual(expected_stamina_multiplier(effects), .3125)
        self.assertEqual(expected_stamina_multiplier([]), 1)
        self.assertEqual(expected_stamina_multiplier([
            {"activation_probability": 1, "stamina_reduction": .5}] * 3), 0)

    def test_fresh_p_data_binds_stats_and_standard_hand_independent_of_card_live(self):
        p = card("p", kind="P")
        p = replace(p, card=replace(p.card, initial_stats=dict.fromkeys(STATS, 75)))
        cfg = GrowthConfig.from_dict({"actions": ["rest"] * 32, "starting_fans": 500000})
        result = project(cfg, p=p)
        self.assertEqual(result.raw_stats, dict.fromkeys(STATS, 75))
        skills = result.skill_plan.learned_skills
        self.assertEqual([s.components for s in skills], [{"Vo": 1}, {"Da": 1}, {"Vi": 1}, {}])
        self.assertEqual([s.heal_fraction for s in skills], [0, 0, 0, .05])
        self.assertEqual(len({s.id for s in skills}), 4)
        self.assertTrue(all(row["skill_slots_verified"] for row in result.timeline))
        self.assertFalse(any("starting_live_skills" in u for u in result.unknowns))
        other = replace(p, card=replace(p.card, initial_stats=dict.fromkeys(STATS, 120)),
                        owned_live=replace(p.owned_live, id="other-own", components={"Vo": 3}))
        changed = project(cfg, p=other)
        self.assertEqual(changed.raw_stats["Vo"], 120)
        self.assertEqual(changed.skill_plan.learned_skills, skills)
        missing = project(cfg, p=replace(p, owned_live=None))
        self.assertEqual(missing.skill_plan.learned_skills, skills)

    def test_p_initialization_preserves_explicit_and_resume_snapshots(self):
        p = card("p", kind="P")
        p = replace(p, card=replace(p.card, initial_stats=dict.fromkeys(STATS, 200)))
        explicit = config(actions=["rest"] * 32)
        result = project(explicit, p=p)
        self.assertEqual(result.raw_stats, dict.fromkeys(STATS, 100))
        self.assertEqual(result.skill_plan.learned_skills, explicit.starting_live_skills)
        resumed = replace(explicit, start_week=32, starting_live_skills=(),
                          starting_bonds={**dict.fromkeys(["s0", "s1", "s2", "s3"], 20), "guest": 20})
        result = project(resumed, p=p)
        self.assertEqual(result.raw_stats, dict.fromkeys(STATS, 100))
        self.assertEqual(result.skill_plan.learned_skills, ())

    def test_unit_mastery_includes_p_excludes_holder_and_other_units(self):
        guest = card("guest", unit="a", skills=[mastery("unit_mastery_sp", 10, "SP")])
        cfg = config(actions=["Vo"] * 32, placement_probability=.25, rest_threshold=0)
        plain = project(cfg, guest=guest)
        same_p = project(cfg, guest=guest, p=card("p", kind="P", unit="a"))
        different_p = project(cfg, guest=guest, p=card("p", kind="P", unit="b"))
        self.assertEqual(plain.sp, different_p.sp)
        self.assertGreater(same_p.sp, plain.sp)
        same_support = project(cfg, guest=guest, supports=(card("s0", unit="a"),
                              *(card("s" + str(i)) for i in range(1, 4))))
        self.assertGreater(same_p.sp, same_support.sp)

    def test_unit_stamina_changes_recovery_only_with_same_unit(self):
        guest = card("guest", unit="a", skills=[mastery("unit_mastery_stamina", 20, None, None)])
        cfg = config(actions=["Vo"] * 32, placement_probability=1)
        unrelated = project(cfg, guest=guest)
        same = project(cfg, guest=guest, p=card("p", kind="P", unit="a"))
        self.assertLess(sum(r["action"] == "rest" for r in same.timeline),
                        sum(r["action"] == "rest" for r in unrelated.timeline))
        self.assertEqual(mechanical_vector(guest, "Vo")["stamina"], 20)

    def test_stamina_prior_counts_only_the_build_room(self):
        # A Vo build gains from Vo-room マスタリー体力; おやすみブースト adds nothing.
        vo = card("vo", skills=[mastery("mastery_stamina", 6, None, "Vo")])
        da = card("da", skills=[mastery("mastery_stamina", 6, None, "Da")])
        rest = card("rest", skills=[{**mastery("rest_support", 3, None, None),
                                     "unsupported": ["「休む」を選択時に9%の確率で体力回復量+15"]}])
        self.assertEqual(mechanical_vector(vo, "Vo")["stamina"], 6)
        self.assertEqual(mechanical_vector(da, "Vo")["stamina"], 0)
        self.assertEqual(mechanical_vector(da, "Da")["stamina"], 6)
        self.assertNotIn("rest_recovery", mechanical_vector(rest, "Vo"))
        self.assertEqual(mechanical_vector(rest, "Vo")["stamina"], 0)

    def test_libraries_converted_before_the_unit_stamina_fix_read_as_unit_stamina(self):
        flagged = [f"description amount differs from skillLevel x1: 行動した場所に自分以外のユニットメンバーがいると1人につき体力{n}回復"
                   for n in (3, 9)]
        legacy = {"id": "ユニットマスタリー体力", "kind": "unit_mastery_stat", "levels": {"1": 1, "50": 3},
                  "attribute": None, "room": None, "unsupported": flagged}
        skill = SupportSkill.from_dict(legacy)
        self.assertEqual((skill.kind, skill.unsupported), ("unit_mastery_stamina", ()))
        # Any other mismatch keeps the old reading and its flag.
        odd = SupportSkill.from_dict({**legacy, "unsupported": [flagged[0].replace("体力3", "体力10")]})
        self.assertEqual(odd.kind, "unit_mastery_stat")
        self.assertTrue(odd.unsupported)
        self.assertEqual(SupportSkill.from_dict({**legacy, "id": "ユニットマスタリーVo", "attribute": "Vo"}).kind,
                         "unit_mastery_stat")
        self.assertEqual(MechanicsParser.support("ユニットマスタリー体力"), ("unit_mastery_stamina", None, None))

    def test_parsed_support_event_reaches_features_and_rest_projection(self):
        # Reuse the synthetic public-page shape, with nonzero gains in all four stats.
        html = small_card().replace(
            '<td>+10</td><td></td><td></td><td>+5</td><td>+10</td>',
            '<td>+10</td><td>+11</td><td>+12</td><td>+5</td><td>+10</td>')
        parsed = CardPageParser().parse(html, source_url=URL)
        guest = extract_card_features(parsed, Ownership(parsed.card_id, 0, 60))
        expected = {"Vo": 10, "Da": 11, "Vi": 12, "Me": 5, "SP": 10}
        self.assertEqual(parsed.events, ({"id": "Event", "stats": expected},))
        for attr in ("Vo", "Da", "Vi"):
            self.assertEqual(mechanical_vector(guest, attr)["event_growth"], expected[attr])
        cfg = config(actions=["rest"] * 32, event_completion_probability=1)
        result = project(cfg, guest=guest)
        self.assertEqual(len(result.timeline), 32)
        self.assertTrue(all(row["action"] == "rest" for row in result.timeline))
        self.assertEqual(result.raw_stats, {a: 100 + expected[a] for a in STATS})
        self.assertEqual(result.effective_stats, result.raw_stats)
        self.assertEqual(result.sp, expected["SP"])

    def test_event_gains_precedence_includes_explicit_empty_gains(self):
        expected = {"Vo": 10, "Da": 11, "Vi": 12, "Me": 5, "SP": 10}
        cfg = config(actions=["rest"] * 32, event_completion_probability=1)
        for event, gains in (
            ({"id": "e", "gains": expected}, expected),
            ({"id": "e", "gains": expected, "stats": dict.fromkeys((*STATS, "SP"), 200)}, expected),
            ({"id": "e", "gains": {}, "stats": expected}, dict.fromkeys((*STATS, "SP"), 0)),
        ):
            with self.subTest(event=event):
                guest = card("guest", events=[event])
                self.assertEqual(mechanical_vector(guest, "Vo")["event_growth"], gains["Vo"])
                result = project(cfg, guest=guest)
                self.assertEqual(result.raw_stats, {a: 100 + gains[a] for a in STATS})
                self.assertEqual(result.sp, gains["SP"])

    def test_event_options_accept_stats_and_prioritize_gains(self):
        cfg = config(actions=["rest"] * 32, event_completion_probability=1)
        options = [
            {"gains": {"Vo": 9, "SP": 3}},
            {"stats": {"Vo": 10, "Da": 11, "Vi": 12, "Me": 5, "SP": 10}},
            {"gains": {"Vo": 1}, "stats": {"Vo": 200, "SP": 200}},
            {"gains": {}, "stats": {"Vo": 300, "SP": 300}},
        ]
        guest = card("guest", events=[{"id": "e", "options": options}])
        self.assertEqual(mechanical_vector(guest, "Vo")["event_growth"], 10)
        result = project(cfg, guest=guest)
        self.assertEqual(result.raw_stats, {"Vo": 110, "Da": 111, "Vi": 112, "Me": 105})
        self.assertEqual(result.sp, 10)

    def test_guest_growth_and_guest_panel_retained(self):
        cfg = config(actions=["Vo"] * 32, rest_threshold=0)
        baseline = project(cfg)
        guest = card("guest", skills=[mastery("mastery_stat", 30)],
                     panels=[panel("guest-live", "live", live("guest-panel", 4))])
        stronger = project(cfg, guest=guest)
        self.assertGreater(stronger.raw_stats["Vo"], baseline.raw_stats["Vo"])
        self.assertIn("guest-panel", [s.id for s in stronger.skill_plan.learned_skills])
        self.assertNotIn("guest-own", [s.id for s in stronger.skill_plan.learned_skills])

    def test_closure_deduplicates_shared_path_and_cost(self):
        p = card("p", kind="P", panels=[panel("root", "stat", {"Vo": 1}, cost=20),
            panel("left", "stat", {"Vo": 1}, cost=30, prerequisites=["root"]),
            panel("right", "stat", {"Vo": 1}, cost=30, prerequisites=["root"]),
            panel("skill", "live", live("strong", 4), cost=50, prerequisites=["left", "right"])])
        closure = panel_closure(p, "skill")
        self.assertEqual([x.id for x in closure], ["root", "left", "right", "skill"])
        result = project(config(starting_sp=500), p=p)
        self.assertEqual(result.sp_required, 130)
        self.assertEqual(result.skill_plan.cost, 130)

    def test_cycle_unknown_topology_lb_and_event_reject(self):
        p = card("p", kind="P", lb=0, panels=[
            panel("a", "live", live("a", 4), prerequisites=["b"]),
            panel("b", "stat", {"Vo": 1}, prerequisites=["a"]),
            panel("unknown", "live", live("u", 5), prerequisites_known=False),
            panel("locked", "live", live("l", 6), required_lb=4),
            panel("event", "live", live("e", 7), event="E4")])
        for key in ("a", "unknown", "locked", "event"):
            self.assertIsNone(panel_closure(p, key))
        self.assertIsNotNone(panel_closure(p, "event", ("p:E4",)))
        result = project(config(starting_sp=1000), p=p)
        self.assertEqual(result.skill_plan.cost, 0)

    def test_sp_readiness_saturates_at_selected_path(self):
        p = card("p", kind="P", panels=[panel("skill", "live", live("new", 4), cost=50)])
        small = project(config(starting_sp=200), p=p)
        large = project(config(starting_sp=2000), p=p)
        self.assertEqual(small.sp_readiness, 1)
        self.assertEqual(large.sp_readiness, 1)
        self.assertEqual(small.skill_plan.learned_skills, large.skill_plan.learned_skills)

    def test_nominal_1180_cap_no_benefit_when_growth_under_720(self):
        cfg = config(actions=["rest"] * 32, starting_stats=dict.fromkeys(STATS, 600),
                     starting_caps=dict.fromkeys(STATS, 720), starting_sp=1000)
        guest = card("guest", panels=[panel("huge", "cap", {"Vo": 460}, cost=50)])
        result = project(cfg, guest=guest)
        self.assertEqual(result.caps["Vo"], 720)
        self.assertEqual(result.sp_required, 0)
        self.assertEqual(result.effective_stats["Vo"], 600)

    def test_cap_useful_with_real_growth_but_not_rest_only(self):
        guest = card("guest", panels=[panel("cap", "cap", {"Vo": 460}, cost=50)])
        cfg = config(actions=["Vo"] * 32, starting_stats=dict.fromkeys(STATS, 710),
                     starting_caps=dict.fromkeys(STATS, 720), starting_sp=500, rest_threshold=0)
        result = project(cfg, guest=guest)
        self.assertGreater(result.effective_stats["Vo"], 720)
        self.assertEqual(result.caps["Vo"], 1180)
        self.assertEqual(result.sp_required, 50)

    def test_late_cap_cannot_restore_wasted_previous_growth(self):
        guest = card("guest", panels=[panel("cap", "cap", {"Vo": 500}, cost=60)])
        cfg = config(actions=["Vo"] * 8 + ["talk"] * 24, starting_stats=dict.fromkeys(STATS, 495),
                     rest_threshold=0, starting_sp=0, placement_probability=0)
        result = project(cfg, guest=guest)
        self.assertGreater(result.raw_stats["Vo"], result.effective_stats["Vo"])
        self.assertEqual(result.effective_stats["Vo"], 500)
        self.assertEqual(result.caps["Vo"], 1000)
        first_purchase = next(row for row in result.timeline if row["unlocked_panel_ids"])
        self.assertGreaterEqual(first_purchase["week"], 8)

    def test_exactly_32_weeks_rest_replaces_and_route_gates(self):
        result = project(config(starting_fans=0))
        self.assertEqual(len(result.timeline), 32)
        self.assertEqual(len(result.fans_by_season), 4)
        self.assertTrue(result.route_feasible)
        self.assertGreaterEqual(result.fans_by_season[-1], 500000)
        low = project(config(starting_stamina=0, actions=["Vo"] * 32, starting_fans=0))
        self.assertEqual(low.timeline[0]["action"], "rest")
        self.assertEqual(len(low.timeline), 32)
        self.assertFalse(low.route_feasible)
        self.assertEqual(low.timeline[8]["action"], "blocked")

    def test_default_s3_unlock_order_and_low_level_mental_growth(self):
        result = project(config(starting_fans=0))
        spot, special = result.timeline[22:24]
        self.assertEqual(spot["action"], "audition")
        self.assertGreaterEqual(spot["fans"], 50000)
        self.assertEqual(special["fans"] - spot["fans"], 50000)
        self.assertEqual(special["raw_stats"]["Me"] - spot["raw_stats"]["Me"], 0)
        self.assertEqual(spot["raw_stats"]["Me"] - result.timeline[21]["raw_stats"]["Me"], 0)
        self.assertEqual(result.timeline[15]["raw_stats"]["Me"] - result.timeline[14]["raw_stats"]["Me"], 0)
        self.assertEqual(special["raw_stats"]["Vo"] - spot["raw_stats"]["Vo"], 7)
        self.assertEqual(spot["raw_stats"]["Vo"] - result.timeline[21]["raw_stats"]["Vo"], 6)
        self.assertGreater(result.effective_stats["Me"], 150)
        # S1: four magazines for fans, then flexible weeks that train メンタル on ラジオ below the line.
        self.assertEqual([row["planned_action"] for row in result.timeline[:8]], ["magazine"] * 4 + ["flex"] * 4)
        self.assertGreater(sum(row["action"] == "radio" for row in result.timeline[:8]), 0)
        self.assertEqual(spot["reward_bound"], "conservative_lower_bound")
        self.assertEqual(spot["unbound_attribute_bonus"], 6)
        self.assertTrue(any("conservative lower bound" in w for w in result.warnings))

    def test_flexible_week_trains_mental_below_the_line_and_lessons_above_it(self):
        self.assertEqual([flex_me_target(w) for w in (0, 7, 15, 31)], [18.75, 150, 220, 330])
        stats = lambda me: {**dict.fromkeys(STATS, 100), "Me": me}
        ahead = project(config(actions=["flex"] * 32, starting_stats=stats(400), rest_threshold=0))
        self.assertEqual({row["action"] for row in ahead.timeline} - {"rest"}, {"Vo"})
        behind = project(config(actions=["flex"] * 32, starting_stats=stats(0), rest_threshold=0))
        self.assertEqual(behind.timeline[0]["action"], "radio")
        self.assertIn("Vo", {row["action"] for row in behind.timeline})
        # A dual build alternates its lessons; a rest week does not use up a lesson turn.
        dual = project(config(actions=["flex"] * 32, starting_stats=stats(400)), strategy="VO_DA")
        lessons = [row["action"] for row in dual.timeline if row["action"] in ("Vo", "Da")]
        self.assertTrue(any(row["action"] == "rest" for row in dual.timeline))
        self.assertEqual(lessons, ["Vo", "Da"] * (len(lessons) // 2) + ["Vo"] * (len(lessons) % 2))

    def test_known_event_lock_is_not_a_gap_but_unknown_paths_remain_visible(self):
        p = card("p", kind="P", panels=[
            panel("event", "live", live("e", 4), event="E4"),
            panel("path", "live", live("x", 4), prerequisites_known=False),
            panel("unsupported", "live", {**live("u", 5), "unsupported": ["LINK"]}, unsupported=["LINK"])])
        result = project(config(starting_sp=1000), p=p)
        self.assertTrue(all(any(key in w for w in result.warnings) for key in ("event", "path")))
        self.assertTrue(any("unmodeled panel path/effect path" in u for u in result.unknowns))
        self.assertFalse(any("unmodeled panel path/effect event" in u for u in result.unknowns))
        # 2026-10-14: an unparsed side effect (LINK) no longer drops the Live skill: its 5x appeal is planned
        self.assertEqual(result.skill_plan.unlocked_panel_ids, ("p:unsupported",))
        self.assertFalse(any("unmodeled panel path/effect unsupported" in u for u in result.unknowns))

    def test_unknown_passive_prerequisite_is_a_bridge_but_never_applied(self):
        """2026-10-14: the game lets you learn an unmodelled conditional passive; it is a path step to the strong
        skill behind it, but its effect is never applied."""
        p = card("p", kind="P", panels=[
            panel("conditional", "passive", {"attribute": "Vo", "buff": 3, "probability": 1,
                  "max_activations": 5, "condition": {"unit": "unknown"}}),
            panel("skill", "live", live("strong", 5), prerequisites=["conditional"])])
        result = project(config(starting_sp=1000), p=p)
        self.assertEqual(result.skill_plan.unlocked_panel_ids, ("p:conditional", "p:skill"))
        self.assertEqual(result.skill_plan.passives, ())
        self.assertTrue(any("conditional" in w for w in result.warnings))   # its own value stays unmodelled

    def test_explicit_supported_passive_can_be_bought(self):
        effects = {"id": "buff", "attribute": "Vo", "buff": .3,
                   "probability": .5, "max_activations": 2, "end_turn": 3}
        p = card("p", kind="P", panels=[panel("passive", "passive", effects)])
        result = project(config(starting_sp=100), p=p)
        self.assertEqual(result.skill_plan.passives, (effects,))

    def test_idol_in_the_live_passive_applies_when_that_idol_is_on_stage(self):
        # 2026-10-27 (user): P and the four active supports perform; the guest does not. Names match without spaces.
        resolved = {"id": "stage", "attribute": "Vo", "buff": .2, "probability": .4, "max_activations": 2}
        on = project(config(starting_sp=100), supports=(card("s0", panels=[stage_panel("s 0")]),
                                                         *(card("s" + str(i)) for i in range(1, 4))))
        self.assertEqual(on.skill_plan.passives, (resolved,))
        self.assertFalse(any("unmodeled panel path/effect stage" in u for u in on.unknowns))
        as_guest = project(config(starting_sp=100), guest=card("guest", panels=[stage_panel("guest")]))
        self.assertEqual(as_guest.skill_plan.passives, ())
        absent = project(config(starting_sp=100), supports=(card("s0", panels=[stage_panel("someone")]),
                                                             *(card("s" + str(i)) for i in range(1, 4))))
        self.assertEqual(absent.skill_plan.passives, ())

    def test_unit_all_members_passive_counts_the_p_idol(self):
        # 2026-11-01 (R26): 「<unit>全員がライブに参加」 needs every member on stage, P included; the guest does not perform.
        resolved = {"id": "stage", "attribute": "Vo", "buff": .2, "probability": .4, "max_activations": 2}
        p = card("櫻木真乃", kind="P")
        hiori = card("風野灯織", panels=[stage_panel("イルミネ全員")])
        meguru = card("八宮めぐる")
        others = (card("s2"), card("s3"))
        on = project(config(starting_sp=100), p=p, supports=(hiori, meguru, *others))
        self.assertEqual(on.skill_plan.passives, (resolved,))
        as_guest = project(config(starting_sp=100), p=p, supports=(hiori, card("s1"), *others), guest=meguru)
        self.assertEqual(as_guest.skill_plan.passives, ())
        other_p = project(config(starting_sp=100), supports=(hiori, meguru, *others))
        self.assertEqual(other_p.skill_plan.passives, ())
        late = card("風野灯織", panels=[stage_panel("2ターン以降イルミネ全員")])
        self.assertEqual(project(config(starting_sp=100), p=p, supports=(late, meguru, *others)).skill_plan.passives,
                         ({**resolved, "start_turn": 2},))

    def test_single_genre_history_passive_holds_in_a_single_attribute_build(self):
        # 2026-10-31 (research R8): 「履歴に1ジャンル以下」 holds when the build appeals in one genre; any slot's panel.
        def history(cond, extra=""):
            desc = f"[条件:{cond}] [確率:20%] [最大:2回]{extra}"
            return panel("hist", "passive", {"buffs": {"Vo": 1.5}, "condition": cond, "probability": .2,
                                             "max_activations": 2},
                         unsupported=[f"passive requires battle interpreter: Vocal150%UP / {desc}"])
        resolved = {"id": "hist", "attribute": "Vo", "buff": 1.5, "probability": .2, "max_activations": 2,
                    "start_turn": 3}
        guest = card("guest", panels=[history("3ターン以降履歴に1ジャンル以下")])
        self.assertEqual(project(config(starting_sp=100), guest=guest).skill_plan.passives, (resolved,))
        either = card("guest", panels=[history("履歴に1ジャンル以下又は3ジャンル以上")])
        self.assertEqual(project(config(starting_sp=100), guest=either).skill_plan.passives[0]["buff"], 1.5)
        dual = project(config(starting_sp=100), guest=guest, strategy="VO_DA")
        self.assertEqual(dual.skill_plan.passives, ())
        limited = card("guest", panels=[history("4ターン以降履歴に1ジャンル以下", " [発動制限]")])
        self.assertEqual(project(config(starting_sp=100), guest=limited).skill_plan.passives, ())

    def test_idol_in_the_live_passive_with_more_terms_stays_unmodeled(self):
        for extra in (" [発動制限]", ""):
            bad = stage_panel("s0", extra=extra)
            if not extra:
                bad["effects"] = {**bad["effects"], "probability": .3}   # text and effects disagree
            result = project(config(starting_sp=100), supports=(card("s0", panels=[bad]),
                                                                 *(card("s" + str(i)) for i in range(1, 4))))
            self.assertEqual(result.skill_plan.passives, ())

    def test_passive_turn_buff_counts_chance_count_and_window(self):
        # 20% at 40% up to twice over 6 turns: E[min(2, Binomial(6, .4))] = 1.720064
        self.assertAlmostEqual(passive_turn_buff({"buff": .2, "probability": .4, "max_activations": 2}), .2 * 1.720064 / 6)
        self.assertAlmostEqual(passive_turn_buff({"buff": .6, "probability": 1, "max_activations": 5, "end_turn": 2}), .2)
        self.assertAlmostEqual(passive_turn_buff({"buff": .6, "probability": 1, "max_activations": 1, "start_turn": 7}), 0)
        low = passive_turn_buff({"buff": .3, "probability": .4, "max_activations": 1, "mental_ratio_max": .74})
        self.assertAlmostEqual(low, passive_turn_buff({"buff": .3, "probability": .2, "max_activations": 1}))

    def test_custom_audition_values_are_explicit_unverified_assumptions(self):
        result = project(config(actions=[{"action": "audition", "fans": 1000000000}] * 32))
        self.assertTrue(any("custom audition rewards/unlock lack source" in u for u in result.unknowns))

    def test_audition_no_stamina_or_ordinary_sp_and_first_place_only(self):
        actions = [{"action": "audition", "fans": 0}] * 32
        guest = card("guest", skills=[mastery("audition_mastery_sp", 10, "SP", None)])
        zero = project(config(actions=actions, starting_stamina=25, first_place_probability=0), guest=guest)
        full = project(config(actions=actions, starting_stamina=25, first_place_probability=1), guest=guest)
        self.assertEqual(zero.sp, 0)
        self.assertEqual(full.sp, 320)
        self.assertEqual(full.stamina, 25)

    def test_unavailable_audition_stops_later_reward(self):
        cfg = config(starting_fans=0, actions=[{"action": "audition", "fans": 500000,
                    "required_fans": 10000}] + [{"action": "audition", "fans": 500000}] * 31)
        result = project(cfg)
        self.assertFalse(result.route_feasible)
        self.assertEqual(result.fans_by_season[-1], 0)
        self.assertTrue(all(row["action"] == "blocked" for row in result.timeline))

    def test_unsupported_effect_retained_without_fabricated_growth(self):
        cfg = config(actions=["Vo"] * 32)
        effect = {**mastery("mastery_stat", 100), "unsupported": ["conditional link"]}
        result = project(cfg, guest=card("guest", skills=[effect]))
        baseline = project(cfg)
        self.assertEqual(result.raw_stats, baseline.raw_stats)
        self.assertTrue(any("conditional link" in u for u in result.unknowns))

    def test_unit_mastery_needs_real_same_unit(self):
        guest = card("guest", unit="unit-a", skills=[mastery("unit_mastery_stat", 50)])
        cfg = config(actions=["Vo"] * 32, placement_probability=1, rest_threshold=0)
        alone = project(cfg, guest=guest)
        same = project(cfg, guest=guest, supports=(card("s0", unit="unit-a"), *tuple(card("s" + str(i)) for i in range(1, 4))))
        self.assertGreater(same.raw_stats["Vo"], alone.raw_stats["Vo"])

    def test_hybrid_stamina_value_low_sp_without_double_counting(self):
        cfg = config(actions=["Vo"] * 32, starting_stamina=60)
        sp_only = card("guest", skills=[mastery("mastery_sp", 5, "SP")])
        hybrid = card("guest", skills=[mastery("mastery_sp", 5, "SP"), mastery("mastery_stamina", 20)])
        plain, helped = project(cfg, guest=sp_only), project(cfg, guest=hybrid)
        self.assertGreater(helped.raw_stats["Vo"], plain.raw_stats["Vo"])
        self.assertEqual(helped.sp_readiness, plain.sp_readiness)
        self.assertLess(sum(x["action"] == "rest" for x in helped.timeline),
                        sum(x["action"] == "rest" for x in plain.timeline))

    def test_unknown_support_preserved_with_standard_fresh_hand(self):
        guest = card("guest", skills=[mastery("facility", 5)])
        result = project(GrowthConfig(), guest=guest)
        self.assertEqual(len(result.skill_plan.learned_skills), 4)
        self.assertFalse(any("starting_live_skills" in x for x in result.unknowns))
        self.assertTrue(any("facility" in x for x in result.unknowns))
        self.assertEqual(result.range["kind"], "sensitivity")

    def test_partial_guide_explicit_and_duplicate_rejected(self):
        p = card("p", kind="P")
        result = estimate_partial_growth(p, (card("s"),), None, "VO", config())
        self.assertTrue(any("partial beam guide" in x for x in result.unknowns))
        self.assertEqual(result.range["kind"], "partial_not_evaluated")
        with self.assertRaises(ValueError):
            estimate_partial_growth(p, (card("p"),), None, "VO", config())

    def test_stage_snapshot_cannot_use_future_learned_skill(self):
        p = card("p", kind="P", panels=[panel("late", "live", live("late-skill", 5), cost=150)])
        result = project(config(actions=["talk"] * 32), p=p)
        acquired = next(row for row in result.timeline if "p:late" in row["unlocked_panel_ids"])
        self.assertGreater(acquired["week"], 0)
        before = result.timeline[acquired["week"] - 1]
        self.assertNotIn("late-skill", [s.id for s in before["learned_skills"]])
        self.assertIn("late-skill", [s.id for s in acquired["learned_skills"]])
        self.assertEqual(acquired["skill_slots"], 4)
        self.assertTrue(acquired["skill_slots_verified"])

    def test_validation_and_four_distinct_skill_slots(self):
        for value in ({"starting_stats": {"Vo": 100}}, {"actions": ["rest"] * 33},
                      {"starting_live_skills": [live("same")] * 4},
                      {"actions": [{"action": "audition", "sp": 40}] * 32}):
            with self.assertRaises(ValueError):
                GrowthConfig.from_dict(value)
        p = card("p", kind="P", panels=[panel("a", "live", live("same", 5)),
                                         panel("b", "live", live("same", 5))])
        result = project(config(starting_sp=500), p=p)
        self.assertEqual(sum(s.id == "same" for s in result.skill_plan.learned_skills), 1)
        self.assertEqual(result.sp_required, 10)

    def test_excellent_live_skill_is_ranked_and_not_deduplicated_by_components(self):
        # Synthetic: an Excellent-only skill (no attribute components) must outrank a weak Vo skill when the four
        # slots are full, and two skills differing only in the Excellent multiplier are distinct identities.
        weak = {"starting_live_skills": [live("start-" + str(n), 1) for n in range(4)], "starting_sp": 500}
        p = card("p", kind="P", panels=[panel("x", "live", {"id": "exc", "components": {}, "excellent": 3})])
        learned = [s.id for s in project(config(**weak), p=p).skill_plan.learned_skills]
        self.assertIn("exc", learned)
        self.assertEqual(len(learned), 4)
        p = card("p", kind="P", panels=[panel("a", "live", {"id": "e", "components": {"Vo": 1}, "excellent": 2}),
                                         panel("b", "live", {"id": "e", "components": {"Vo": 1}, "excellent": 4})])
        result = project(config(**weak), p=p)
        self.assertEqual(sum(s.id == "e" for s in result.skill_plan.learned_skills), 2)
        self.assertEqual(sorted(s.excellent for s in result.skill_plan.learned_skills if s.id == "e"), [2, 4])


if __name__ == "__main__":
    unittest.main()

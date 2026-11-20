"""Actual resume contracts, synthetic cards only; no game input."""
from dataclasses import replace
import json
import unittest

from agent.core import wing_state
from agent.core.true_end.growth import GrowthConfig, estimate_team_growth
from agent.core.true_end.models import CardData, LiveSkill, Ownership, STATS, extract_card_features
from agent.core.true_end.run_state import RunProfile, RunState
from agent.core.true_end.sp_value import PanelValueState


def panel(key, kind, effects, cost=20, prerequisites=(), **extra):
    return {"id": key, "kind": kind, "sp_cost": cost, "required_lb": 0,
            "prerequisites": list(prerequisites), "prerequisites_known": True, "effects": effects, **extra}


def feature(key, *, kind="S", panels=(), events=()):
    data = CardData.from_dict({"card_id": key, "name": key, "idol": key, "kind": kind,
        "status": "OK", "source_url": "synthetic://resume-tests", "te_eligible": kind == "P",
        "levels": {"80": dict.fromkeys(STATS, 100)}, "max_levels": {str(n): 80 for n in range(5)},
        "owned_live_skills": [{"id": "own", "components": {"Vo": 2}}],
        "panels": list(panels), "events": list(events)})
    return extract_card_features(data, Ownership(key, 4, 80))


def profile(**kwargs):
    return RunProfile(feature("p", kind="P", **kwargs), tuple(feature(f"s{i}") for i in range(4)),
                      feature("guest"), "VO_SAFE")


def state(team, **kwargs):
    snapshot = PanelValueState(dict.fromkeys(STATS, 100), dict.fromkeys(STATS, 500),
                              tuple(LiveSkill(f"basic{i}", {"Vo": 1}) for i in range(4)))
    return RunState(team.id, 24, snapshot, 0, 100, 500000,
                    dict.fromkeys((c.card.card_id for c in team.cards[1:]), 49), **kwargs)


def assumptions(**kwargs):
    return GrowthConfig.from_dict({"actions": ["rest"] * 32, "perfect_probability": 0,
                                   "event_completion_probability": 1, **kwargs})


def project(team, snapshot, cfg=None, purchases=()):
    team.bind(snapshot)
    return estimate_team_growth(team.p, team.active, team.guest, team.strategy,
                               snapshot.growth_config(cfg or assumptions()), replay_panel_ids=purchases)


class RunResumeTests(unittest.TestCase):
    def test_state_json_roundtrip_and_profile_roles_level_lb_bind(self):
        team = profile()
        snapshot = state(team)
        restored = RunState.from_dict(json.loads(json.dumps(snapshot.to_dict())))
        self.assertEqual(restored.id, snapshot.id)
        for changed in (replace(team, active=tuple(reversed(team.active))),
                        replace(team, guest=replace(team.guest, ownership=replace(team.guest.ownership, limit_break=3))),
                        replace(team, guest=replace(team.guest, ownership=replace(team.guest.ownership, level=79)))):
            with self.assertRaisesRegex(ValueError, "different"):
                changed.bind(snapshot)
        altered = replace(snapshot, panels=replace(snapshot.panels, live_skills=tuple(reversed(snapshot.panels.live_skills))))
        self.assertNotEqual(altered.id, snapshot.id)

    def test_resume_absolute_weeks_facility_and_remaining_length(self):
        team = profile()
        actions = ["rest"] * 32
        actions[24] = "Vo"
        result = project(team, state(team), assumptions(actions=actions, placement_probability=0))
        self.assertEqual([r["week"] for r in result.timeline], list(range(24, 32)))
        self.assertEqual(result.timeline[0]["stats"]["Vo"], 132)  # Lv4 30, tension1.05 -> ceil32
        self.assertEqual(len(result.fans_by_season), 1)
        self.assertEqual(result.final_fans, result.timeline[-1]["fans"])

    def test_resume_week_multiplier_not_reset_to_zero(self):
        team = profile()
        actions = ["Vo"] * 32
        cfg = assumptions(actions=actions, facility_levels=[1] * 4, placement_probability=1,
                          excellent_probability=0, rest_threshold=0)
        first = project(team, replace(state(team), next_week=0), cfg)
        late = project(team, state(team), cfg)
        self.assertGreater(late.timeline[0]["stats"]["Vo"], first.timeline[0]["stats"]["Vo"])

    def test_actual_bond_excellent_boundary_is_preserved(self):
        team = profile()
        cfg = assumptions(actions=["Vo"] * 32, placement_probability=1, excellent_probability=1)
        low = project(team, state(team), cfg)
        snapshot = replace(state(team), bonds=dict.fromkeys(state(team).bonds, 50))
        high = project(team, snapshot, cfg)
        self.assertGreater(high.timeline[0]["stats"]["Vo"], low.timeline[0]["stats"]["Vo"])
        self.assertEqual(low.timeline[0]["bonds"]["guest"], 57)

    def test_paid_bridge_effect_not_reapplied_downstream_only_cost(self):
        team = profile(panels=[panel("bridge", "stat", {"Vo": 10}),
                               panel("target", "stat", {"Vo": 50}, prerequisites=("bridge",))])
        snap = state(team)
        snap = replace(snap, sp=20, panels=replace(snap.panels, stats={**snap.panels.stats, "Vo": 110},
                                                learned_panel_ids=("p:bridge",)))
        result = project(team, snap, purchases=("p:target",))
        self.assertEqual(result.effective_stats["Vo"], 160)
        self.assertEqual(result.skill_plan.cost, 20)
        self.assertEqual(result.remaining_sp, 0)
        self.assertEqual(result.skill_plan.unlocked_panel_ids, ("p:bridge", "p:target"))
        with self.assertRaisesRegex(ValueError, "already purchased"):
            project(team, snap, purchases=("p:bridge", "p:target"))

    def test_actual_passive_not_lost_or_duplicated(self):
        passive = {"id": "p:passive", "attribute": "Vo", "buff": .2, "probability": 1, "max_activations": 1}
        team = profile(panels=[panel("passive", "passive", passive)])
        snap = state(team)
        snap = replace(snap, panels=replace(snap.panels, passives=(passive,), learned_panel_ids=("p:passive",)))
        result = project(team, snap, purchases=None)
        self.assertEqual(result.skill_plan.passives, (passive,))
        self.assertEqual(result.skill_plan.cost, 0)

    def test_cap_does_not_recover_historical_overflow(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        snap = state(team)
        snap = replace(snap, next_week=32, phase="semifinal", sp=20,
                       panels=replace(snap.panels, stats={**snap.panels.stats, "Vo": 500}))
        result = project(team, snap, purchases=("p:cap",))
        self.assertEqual(result.effective_stats["Vo"], 500)
        self.assertEqual(result.caps["Vo"], 600)
        self.assertEqual(result.timeline, ())
        self.assertEqual(result.final_fans, 500000)

    def test_completed_event_skips_reward_but_keeps_unlock(self):
        team = profile(events=[{"id": "event", "week": 25, "gains": {"Vo": 100, "SP": 100}}],
                       panels=[panel("target", "stat", {"Vo": 20}, event="event")])
        snap = replace(state(team), sp=20, events_completed=("p:event",))
        result = project(team, snap, purchases=("p:target",))
        self.assertEqual(result.effective_stats["Vo"], 120)
        self.assertEqual(result.sp, 20)

    def test_expected_future_event_does_not_unlock_locked_panel(self):
        team = profile(events=[{"id": "event", "week": 25, "gains": {"SP": 100}}],
                       panels=[panel("target", "stat", {"Vo": 20}, event="event")])
        result = project(team, state(team), purchases=None)
        self.assertEqual(result.sp, 100)
        self.assertEqual(result.skill_plan.unlocked_panel_ids, ())

    def test_eliminated_snapshot_never_resumes_actions_or_purchases(self):
        team = profile(panels=[panel("gain", "stat", {"Vo": 50})])
        snap = replace(state(team), route_feasible=False, fans=100, sp=20)
        result = project(team, snap, assumptions(actions=["Vo"] * 32), purchases=None)
        self.assertFalse(result.route_feasible)
        self.assertEqual(result.effective_stats, snap.panels.stats)
        self.assertTrue(all(r["action"] == "blocked" for r in result.timeline))
        self.assertEqual(result.skill_plan.cost, 0)

    def test_season_gate_failure_consumes_last_week_without_extra_retry(self):
        team = profile()
        snap = replace(state(team), next_week=31, fans=400000, stamina=0)
        result = project(team, snap)
        self.assertFalse(result.route_feasible)
        self.assertEqual(len(result.timeline), 1)
        self.assertEqual(result.final_fans, 400000)
        actions = ["rest"] * 32
        actions[31] = {"action": "audition", "fans": 100000, "required_fans": 100000, "source": "synthetic"}
        passed = project(team, snap, assumptions(actions=actions))
        self.assertTrue(passed.route_feasible)
        self.assertEqual(passed.timeline[0]["action"], "audition")  # stamina0 does not create rest

    def test_old_season_gates_not_replayed_and_bad_snapshot_rejected(self):
        team = profile()
        result = project(team, state(team))
        self.assertEqual(len(result.fans_by_season), 1)
        with self.assertRaisesRegex(ValueError, "completed season"):
            team.bind(replace(state(team), fans=49999))
        with self.assertRaisesRegex(ValueError, "all five"):
            team.bind(replace(state(team), bonds={}))

    def test_week_reader_adapter_requires_observed_facts(self):
        team = profile()
        snap = state(team)
        observed = wing_state.WingState("week", 4, 4, snap.panels.stats, snap.fans,
                                      stamina=80, sp=15, stat_caps=snap.panels.caps)
        result = RunState.from_week_state(team, observed, panels=snap.panels, bonds=snap.bonds, memory_level=0)
        self.assertEqual(result.next_week, 27)
        self.assertEqual((result.sp, result.stamina), (15, 80))
        with self.assertRaises(ValueError):
            RunState.from_week_state(team, replace(observed, sp=None), panels=snap.panels, bonds=snap.bonds, memory_level=0)

    def test_snapshot_rejects_incomplete_equipment_phase_and_nonfinite_budget(self):
        team = profile()
        for changes in ({"next_week": 33}, {"next_week": 24.0}, {"memory_level": 1.0},
                        {"phase": "final"}, {"sp": float("nan")}, {"stamina": 101},
                        {"panels": replace(state(team).panels, live_skills=())}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(state(team), **changes)


if __name__ == "__main__":
    unittest.main()

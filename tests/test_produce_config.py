from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.core import produce_config as pc


def weekly(**caps) -> pc.ProduceConfig:
    data = pc.default_config("weekly_count")
    data["caps"].update(caps)
    return pc.ProduceConfig.from_dict(data)


class DefaultTests(unittest.TestCase):
    def test_defaults_validate_and_are_disabled(self) -> None:
        for goal in pc.GOALS:
            with self.subTest(goal):
                cfg = pc.ProduceConfig.from_dict(pc.default_config(goal))
                self.assertFalse(cfg.data["enabled"])
                self.assertEqual(cfg.data["unit_preset"], "unit1")
        w = pc.ProduceConfig.from_dict(pc.default_config())
        self.assertTrue(w.weekly["auto_produce"] and w.weekly["use_skip_tickets"])
        te = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        self.assertEqual((te.manual["strategy"], te.manual["primary_stat"]), ("vo_score", "Vo"))   # scorer default
        self.assertEqual((te.manual["live_mode"], te.manual["event_default_choice"], te.manual["accept_promises"]),
                         ("policy", 0, True))       # 2026-10-05: own live policy by default; Auto is opt-in

    def test_default_copies_are_independent(self) -> None:
        a = pc.default_config()
        a["caps"]["max_runs"] = 99
        self.assertEqual(pc.DEFAULT_WEEKLY["caps"]["max_runs"], 3)

    def test_load_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(pc.default_config()), encoding="utf-8")
            self.assertEqual(pc.ProduceConfig.load(path).goal, "weekly_count")
            path.write_text('{"version": 1, "version": 1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                pc.ProduceConfig.load(path)


class RejectionTests(unittest.TestCase):
    def check(self, goal, mutate):
        data = copy.deepcopy(pc.default_config(goal))
        mutate(data)
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_dict(data)

    def test_forbidden_resources(self) -> None:
        self.check("weekly_count", lambda d: d.update(premium_currency_allowed=True))
        self.check("true_end", lambda d: d.update(recovery_soda_allowed=True))

    def test_weekly_rules(self) -> None:
        cases = {
            "manual weekly route": lambda d: d["weekly"].update(auto_produce=False),
            "auto only middle": lambda d: d.update(difficulty="beginner"),
            "tickets off but cap": lambda d: d["weekly"].update(use_skip_tickets=False),
            "tickets on no cap": lambda d: d["caps"].update(max_skip_tickets=0),
            "daily ticket limit": lambda d: d["caps"].update(max_skip_tickets=6),
            "runs over target": lambda d: d["caps"].update(max_runs=4),
            "target over 3": lambda d: d["weekly"].update(target_count=4),
            "stamina below one run": lambda d: d["caps"].update(max_stamina=29),
            "manual present": lambda d: d.update(manual=pc.default_config("true_end")["manual"]),
            "p items in auto": lambda d: d["caps"].update(max_p_items=1),
            "unknown field": lambda d: d.update(extra=1),
            "time budget": lambda d: d["time_budget"].update(max_total_minutes=5),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.check("weekly_count", mutate)

    def test_true_end_rules(self) -> None:
        cases = {
            "beginner": lambda d: d.update(difficulty="beginner"),
            "master without opt-in": lambda d: d.update(difficulty="master"),
            "multiplier": lambda d: d.update(stamina_multiplier=2, caps=dict(d["caps"], max_stamina=60)),
            "primary mismatch": lambda d: d["manual"].update(primary_stat="Da"),
            "unknown strategy": lambda d: d["manual"].update(strategy="random"),
            "items without opt-in": lambda d: d["manual"].update(p_items=["流行雑誌"]),
            "items over cap": lambda d: d["manual"].update(use_p_items=True, p_items=["a", "b"]),
            "duplicate items": lambda d: (d["manual"].update(use_p_items=True, p_items=["a", "a"]),
                                          d["caps"].update(max_p_items=2)),
            "skip tickets": lambda d: d["caps"].update(max_skip_tickets=1),
            "weekly present": lambda d: d.update(weekly=pc.default_config()["weekly"]),
            "balanced with primary": lambda d: d["manual"].update(strategy="balanced"),
            "bad live mode": lambda d: d["manual"].update(live_mode="turbo"),
            "event choice range": lambda d: d["manual"].update(event_default_choice=3),
            "event choice bool": lambda d: d["manual"].update(event_default_choice=True),
            "accept promises int": lambda d: d["manual"].update(accept_promises=1),
            "missing required manual key": lambda d: d["manual"].pop("risk_level"),
            "unknown manual key": lambda d: d["manual"].update(auto_fallback=True),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.check("true_end", mutate)

    def test_true_end_accepts_opt_ins(self) -> None:
        data = pc.default_config("true_end")
        data.update(difficulty="master", allow_master=True)
        data["manual"].update(use_p_items=True, p_items=["流行雑誌", "ヒーリングタルト"])
        data["caps"]["max_p_items"] = 2
        self.assertEqual(pc.ProduceConfig.from_dict(data).data["difficulty"], "master")
        data = pc.default_config("fans")
        data.update(stamina_multiplier=3)
        data["caps"]["max_stamina"] = 90
        data["manual"].update(strategy="balanced", primary_stat=None)
        self.assertEqual(pc.ProduceConfig.from_dict(data).stamina_per_run, 90)


    def test_manual_optional_keys_may_be_omitted(self) -> None:
        data = pc.default_config("true_end")
        for key in pc.MANUAL_OPTIONAL:
            data["manual"].pop(key)
        self.assertEqual(pc.ProduceConfig.from_dict(data).manual["strategy"], "vo_score")
        data["manual"]["strategy"] = "vo_highest"      # the rule table stays selectable as the fallback
        self.assertEqual(pc.ProduceConfig.from_dict(data).manual["strategy"], "vo_highest")
        data = pc.default_config("true_end")
        data["manual"].update(strategy="vo_single", live_mode="agent", event_default_choice=2, accept_promises=False)
        self.assertEqual(pc.ProduceConfig.from_dict(data).manual["live_mode"], "agent")


class AutoPrimaryTests(unittest.TestCase):
    """U02: 「自动」主属性 = the P card's first listed specialty, never a silent Vo."""

    def build(self, specialty, stat="auto"):
        from unittest import mock
        info = {"id": "1040020010", "specialty": specialty} if specialty is not None else {}
        with mock.patch("agent.core.wing_nav.card_info", return_value=info):
            return pc.ProduceConfig.from_task_options({"p_card": "1040020010", "primary_stat": stat})

    def test_specialty_decides_the_auto_stat(self):
        for specialty, stat in (("Visual & Dance", "Vi"), ("Dance", "Da"), ("Vocal & Balanced", "Vo")):
            self.assertEqual(self.build(specialty).data["manual"]["primary_stat"], stat, specialty)
        self.assertEqual(self.build("Visual & Dance", "Da").data["manual"]["primary_stat"], "Da")

    def test_balanced_needs_an_explicit_choice_and_unknown_cards_keep_the_default(self):
        with self.assertRaisesRegex(ValueError, "主属性"):
            self.build("Balanced")
        self.assertEqual(self.build(None).data["manual"]["primary_stat"], "Vo")


class WeeklyPlanTests(unittest.TestCase):
    def plan(self, cfg=None, **kw):
        args = dict(completed=0, target_on_screen=3, stamina=200, tickets_held=609, tickets_used_today=0)
        args.update(kw)
        return pc.plan_weekly(cfg or weekly(), **args)

    def test_full_plan_with_tickets(self) -> None:
        p = self.plan()
        self.assertEqual((p.runs, p.stamina, p.skip_tickets, p.stop), (3, 90, 3, False))

    def test_partial_and_complete(self) -> None:
        self.assertEqual(self.plan(completed=2).runs, 1)
        done = self.plan(completed=3)
        self.assertEqual((done.runs, done.stop, done.reasons), (0, False, ("already_complete",)))

    def test_limits(self) -> None:
        p = self.plan(stamina=65)
        self.assertEqual(p.runs, 2)
        self.assertIn("limited_by_stamina_65", p.reasons)
        p = self.plan(tickets_used_today=4)
        self.assertEqual((p.runs, p.skip_tickets), (1, 1))
        self.assertTrue(self.plan(tickets_held=0).stop)
        self.assertTrue(self.plan(stamina=29).stop)

    def test_unknowns_stop(self) -> None:
        for key in ("completed", "target_on_screen", "stamina", "tickets_held", "tickets_used_today"):
            with self.subTest(key):
                self.assertTrue(self.plan(**{key: None}).stop)
        self.assertTrue(self.plan(target_on_screen=5).stop)
        self.assertTrue(self.plan(completed=4).stop)

    def test_without_tickets_uses_time_budget(self) -> None:
        data = pc.default_config()
        data["weekly"]["use_skip_tickets"] = False
        data["caps"]["max_skip_tickets"] = 0
        data["time_budget"] = {"max_minutes_per_run": 60, "max_total_minutes": 120}
        p = self.plan(pc.ProduceConfig.from_dict(data), tickets_held=None, tickets_used_today=None)
        self.assertEqual((p.runs, p.minutes, p.skip_tickets), (2, 120, 0))
        self.assertIn("limited_by_time_budget_120", p.reasons)

    def test_not_weekly_goal(self) -> None:
        te = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        self.assertEqual(self.plan(te).reasons, ("not_weekly_goal",))


if __name__ == "__main__":
    unittest.main()

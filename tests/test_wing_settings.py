"""W.I.N.G. T.E. settings: PI v2 options -> run config -> produce_config validation -> scorer / SP /
audition / event parameters. Offline; no game, no host, no input."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import types
import unittest
from pathlib import Path

from agent.core import produce_config as pc
from agent.core import wing_audition as wa
from agent.core import wing_events as we
from agent.core import wing_nav as nav
from agent.core import wing_policy as wp
from agent.core import wing_score as sc
from agent.core import wing_sp as sp
from agent.core import wing_state as ws
from tests.test_wing_score import rooms
from tests.test_wing_te_rules import run_state
from tools import run_tasks, ui_schema

ROOT = Path(__file__).resolve().parent.parent
TASK = "AutoProduce"
WING_OPTIONS = ["Produce休息阈值", "Produce低分兜底", "ProduceSP加点", "Produce上限余量", "Produce约定", "Produce试镜出牌",
                "Produce出牌风格", "Produce事件选项", "ProduceP道具", "Produce终局清空SP", "Produce报酬读取", "Produce保护Lv5书",
                "Produce再抽选", "Produce报酬后OK", "Produce自动锁书", "Produce自动卖书", "Produce卖书策略"]
ADVANCED = ["Produce高级SP权重", "Produce高级紧迫度", "Produce高级兜底比例", "Produce高级羁绊权重", "Produce高级Auto门槛"]
POLICY = {"resource_usage": {"premium_currency_allowed": False, "paid_items_allowed": False,
                             "other_inventory_allowed": False}}


def options_config(interface, option=None, goal="True End"):
    """ProduceConfig from the AutoProduce task options, exactly as run_tasks resolves them."""
    plan, = run_tasks.plan_tasks(interface, {"tasks": [{"name": TASK, "option": {"Produce目标": goal, **(option or {})}}]})
    attach = {k: v for node, fields in plan.pipeline_override.items() if node.startswith("SC.Produce.Config.")
              for k, v in fields["attach"].items()}
    return pc.ProduceConfig.from_task_options(attach)


def states():
    """A spread of synthetic weeks (score path, fallback, promise, deadline)."""
    promise = {"season": 2, "week": 3, "attribute": "Da"}
    return [run_state(2, 3, 3_000, 220, trend_vo=2),
            run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=90, trouble_rate=None),
            run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=40, trouble_rate=None),
            run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(vo=(26, 0)), stamina=90, trouble_rate=None,
                      promise=promise),
            run_state(3, 7, 13_852, 500, rank="D"),
            run_state(4, 2, 300_000, 900, trend_vo=3, rank="B", options=rooms(vo=(90, 0)), stamina=90,
                      trouble_rate=None, auditions=())]


class DefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interface = run_tasks.load_interface()

    def assert_library_defaults(self, params, press_ok=False):
        self.assertEqual(sc.ScoreParams.from_overrides(params["policy"]["score_params"]), sc.ScoreParams())
        self.assertEqual(wa.LiveParams.from_overrides(params["live"]), wa.LiveParams())
        self.assertEqual({**sp.DEFAULT_SP_PARAMS, **params["sp"]}, sp.DEFAULT_SP_PARAMS)
        self.assertEqual(params["event"], {"default_index": 0, "accept_promises": True})
        self.assertEqual(params["items"], [])
        self.assertEqual(params["end"], {"read": True, "protect_lv5": False, "reroll": False, "press_ok": press_ok,
                                         "keep": {"lock": False, "sell": False, "strategy": "useless_only"}})
        self.assertEqual(params["policy"]["live_mode"], "policy")
        merged = {**wp.DEFAULT_PARAMS, **wp.STRATEGIES["vo_score"].params}
        self.assertEqual({**merged, **params["policy"]}["trouble_rest_rate"], merged["trouble_rest_rate"])

    def test_default_config_yields_library_defaults(self):
        config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        self.assert_library_defaults(pc.wing_params(config))
        self.assertEqual(pc.run_preflight(config, policy=POLICY)["reasons"], [])

    def test_default_ui_options_yield_library_defaults(self):
        config = options_config(self.interface)
        self.assertEqual(config.manual["strategy"], "vo_score")
        # 2026-11-16 (user): the UI's 读取后自动确认 defaults on; the library default stays off
        self.assert_library_defaults(pc.wing_params(config), press_ok=True)
        self.assertEqual((config.manual["use_p_items"], config.caps["max_p_items"]), (False, 0))
        self.assertEqual(pc.run_preflight(config, policy=POLICY)["reasons"], [])

    def test_task_defaults_to_true_end_and_weekly_is_a_separate_task(self):
        # 2026-10-08: 周常 left the W.I.N.G. T.E. goal options; the bare task resolves to the True End defaults
        plan, = run_tasks.plan_tasks(self.interface, {"tasks": [{"name": TASK}]})
        attach = {k: v for node, fields in plan.pipeline_override.items() if node.startswith("SC.Produce.Config.")
                  for k, v in fields["attach"].items()}
        config = pc.ProduceConfig.from_task_options(attach)
        self.assertEqual((config.goal, config.caps["max_runs"]), ("true_end", 1))
        self.assert_library_defaults(pc.wing_params(config), press_ok=True)
        goals = [c["name"] for c in self.interface.options["Produce目标"]["cases"]]
        self.assertEqual(goals, ["True End", "只过季门槛"])
        weekly = self.interface.tasks["WeeklyProduce"]
        self.assertFalse(weekly.get("default_check", False))
        self.assertIn("オート1", weekly["description"])          # the game's official Auto, not W.I.N.G.
        # W.I.N.G. T.E. options never apply to it; 2026-10-22 it has its own 次数 / スキップ / 核对 options.
        self.assertFalse(set(weekly.get("option") or []) & set(self.interface.tasks["AutoProduce"].get("option") or []))
        self.assertTrue(all(key.startswith("Auto") for key in weekly.get("option") or []))
        with self.assertRaises(ValueError):                        # official Auto has no W.I.N.G. params
            pc.wing_params(pc.ProduceConfig.from_task_options({"goal": "weekly_count"}))

    def test_decisions_identical_with_default_settings(self):
        params = pc.wing_params(options_config(self.interface))["policy"]
        for state in states():
            with self.subTest(state=(state.season, state.week)):
                self.assertEqual(wp.decide(state, "vo_score", "true_end", copy.deepcopy(params)).to_dict(),
                                 wp.decide(state, "vo_score", "true_end").to_dict())

    def test_old_configs_without_the_new_keys_still_load(self):
        data = pc.default_config("true_end")
        for key in ("audition_style", "rest_trouble_rate", "fallback", "sp_order", "cap_margin", "promise", "tuning"):
            del data["manual"][key]
        self.assert_library_defaults(pc.wing_params(pc.ProduceConfig.from_dict(data)))


class ValidationTests(unittest.TestCase):
    def manual(self, **changes):
        data = pc.default_config("true_end")
        data["manual"].update(changes)
        return data

    def test_bad_values_rejected(self):
        bad = [{"audition_style": "x"}, {"fallback": "x"}, {"sp_order": "x"}, {"promise": "always"},
               {"rest_trouble_rate": 2}, {"rest_trouble_rate": 60}, {"rest_trouble_rate": True},
               {"rest_trouble_rate": "15"}, {"cap_margin": -1}, {"cap_margin": 301}, {"cap_margin": 1.5},
               {"cap_margin": True}, {"tuning": []}, {"tuning": {"w_sp": 9}}, {"tuning": {"w_sp": True}},
               {"tuning": {"perfect_rate": 0.5}}, {"tuning": {"auto_min_pass": 0.5}}, {"live_mode": "auto"},
               {"event_default_choice": 3}]
        for changes in bad:
            with self.subTest(changes), self.assertRaises(ValueError):
                pc.ProduceConfig.from_dict(self.manual(**changes))

    def test_bad_option_attach_rejected(self):
        for attach in ({"goal": "true_end", "p_items": "jewel_pack"}, {"goal": "true_end", "tune_perfect_rate": 1},
                       {"goal": "true_end", "premium_currency_allowed": True}, {"goal": "true_end", "fallback": "x"},
                       {"goal": "true_end", "rest_trouble_rate": 99}, {"goal": "true_end", "p_items": ["秘密のメモ帳"]}):
            with self.subTest(attach), self.assertRaises(ValueError):
                pc.ProduceConfig.from_task_options(attach)

    def test_library_parameter_guards(self):
        with self.assertRaises(ValueError):
            sc.ScoreParams.from_overrides({"fallback": "nap"})
        with self.assertRaises(ValueError):
            sc.ScoreParams.from_overrides({"promise_weight": -1})
        with self.assertRaises(ValueError):
            sp.plan_sp([], sp=10, primary="Vo", stat=1, cap=1, params={"tier_order": ("p_card", "cap")})


class OptionMappingTests(unittest.TestCase):
    """Every case of every new option reaches its parameter, and the parameter changes the decision."""

    @classmethod
    def setUpClass(cls):
        cls.interface = run_tasks.load_interface()

    def params(self, **option):
        return pc.wing_params(options_config(self.interface, option))

    def test_every_case_maps(self):
        expect = {
            "Produce休息阈值": lambda p, v: p["policy"]["trouble_rest_rate"] == p["policy"]["score_params"][
                "trouble_rest_rate"] == float(v.rstrip("%")),
            "Produce低分兜底": lambda p, v: p["policy"]["score_params"]["fallback"]
            == {"主属性课": "empty_room", "休息": "rest", "不兜底": "none"}[v],
            "ProduceSP加点": lambda p, v: p["sp"]["tier_order"]
            == sp.TIER_PRESETS[{"技能优先": "skills_first", "上限优先": "caps_first", "不加点": "skills_first",
                                "交给我": "skills_first"}[v]],   # 2026-10-11: + sp_mode skip / handover (launch option)
            "Produce上限余量": lambda p, v: p["sp"]["cap_margin"] == int(v),
            "Produce约定": lambda p, v: p["policy"]["score_params"]["promise_weight"] == {"中立": 1.0, "优先守约": 4.0}[v],
            "Produce试镜出牌": lambda p, v: p["policy"]["live_mode"] == {"自研策略": "policy", "游戏 Auto": "game_auto"}[v],
            "Produce出牌风格": lambda p, v: p["live"].get("focus_bonus", wa.LiveParams().focus_bonus)
            == {"稳拿第一": 3.0, "按属性": 0.0}[v],
            "Produce事件选项": lambda p, v: p["event"]["default_index"] == int(v.split()[1]) - 1,
            "ProduceP道具": lambda p, v: p["items"] == list(pc.P_ITEM_PRESETS[{"不带": "none"}[v]]),
            "Produce高级SP权重": lambda p, v: p["policy"]["score_params"]["w_sp"] == float(v),
            "Produce高级紧迫度": lambda p, v: p["policy"]["score_params"]["urgency_max"] == float(v),
            "Produce高级兜底比例": lambda p, v: p["policy"]["score_params"]["floor_ratio"] == float(v),
            "Produce高级羁绊权重": lambda p, v: p["policy"]["score_params"]["bond_beta"] == float(v),
            "Produce高级Auto门槛": lambda p, v: p["live"]["auto_min_pass"] == float(v.rstrip("%")) / 100,
            # 2026-10-10 W.I.N.G. end / SP options (defaults: no spend-down, read, protect Lv5, no reroll, handover)
            "Produce终局清空SP": lambda p, v: p["sp"]["spend_down_last_chance"] is (v == "Yes"),
            "Produce报酬读取": lambda p, v: p["end"]["read"] is (v == "Yes"),
            "Produce保护Lv5书": lambda p, v: p["end"]["protect_lv5"] is (v == "Yes"),
            "Produce再抽选": lambda p, v: p["end"]["reroll"] is (v == "Yes"),
            "Produce报酬后OK": lambda p, v: p["end"]["press_ok"] is (v == "Yes"),
            # 2026-10-25 new books (knowhow_keep): lock / sell switches and the sell strategy, all off by default
            "Produce自动锁书": lambda p, v: p["end"]["keep"]["lock"] is (v == "Yes"),
            "Produce自动卖书": lambda p, v: p["end"]["keep"]["sell"] is (v == "Yes"),
            "Produce卖书策略": lambda p, v: p["end"]["keep"]["strategy"]
            == {"只卖没用的书": "useless_only", "库存够3本就卖": "keep3", "库存有1本就卖": "keep1"}[v],
        }
        self.assertEqual(set(expect), set(WING_OPTIONS + ADVANCED))
        for key, check in expect.items():
            for case in self.interface.options[key]["cases"]:
                with self.subTest(key=key, case=case["name"]):
                    params = self.params(**{key: case["name"]})
                    self.assertTrue(check(params, case["name"]), params)
                    sc.ScoreParams.from_overrides(params["policy"]["score_params"])   # always valid
                    wa.LiveParams.from_overrides(params["live"])

    def test_rest_threshold_reaches_the_rules_and_the_scorer(self):
        params = self.params(Produce休息阈值="20%")
        merged = {**wp.DEFAULT_PARAMS, **wp.STRATEGIES["vo_score"].params, **params["policy"]}
        self.assertEqual(merged["trouble_rest_rate"], 20.0)
        self.assertEqual(sc.ScoreParams.from_overrides(params["policy"]["score_params"]).trouble_rest_rate, 20.0)
        cases = [c["name"] for c in self.interface.options["Produce休息阈值"]["cases"]]
        self.assertEqual((cases, self.interface.options["Produce休息阈值"]["default_case"]),
                         (["5%", "10%", "15%", "20%"], "10%"))
        with self.assertRaises(ValueError):                         # 25 % is no longer accepted anywhere
            pc.ProduceConfig.from_task_options({"goal": "true_end", "rest_trouble_rate": 25})

    def test_fallback_changes_the_weak_week(self):
        # no audition: since 2026-10-08 the fallback only replaces a weak lesson / work best
        week = run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=90, trouble_rate=None, auditions=())
        rules = {}
        for case in ("主属性课", "休息", "不兜底"):
            score = dict(self.params(Produce低分兜底=case)["policy"]["score_params"], floor_ratio=10.0)
            rules[case] = sc.score_week(week, "Vo", params=score).rule
        self.assertEqual(rules, {"主属性课": "fallback_empty_room", "休息": "fallback_rest", "不兜底": "best"})

    def test_promise_preference_scales_the_promise_term(self):
        week = run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(vo=(26, 0)), stamina=90, trouble_rate=None,
                         promise={"season": 2, "week": 3, "attribute": "Da"})
        neutral = sc.score_week(week, "Vo", params=self.params()["policy"]["score_params"])   # 中立 is the default
        keep = sc.score_week(week, "Vo", params=self.params(Produce约定="优先守约")["policy"]["score_params"])
        self.assertAlmostEqual(keep.context["promise_keep_value"], 4 * neutral.context["promise_keep_value"], places=2)
        da = lambda r: next(c for c in r.ranked if c.id == "lesson_da").terms["promise"]  # noqa: E731
        self.assertGreater(da(keep), da(neutral) > 0)

    def test_sp_order_and_cap_margin(self):
        nodes = [sp.SPNode("skill", "live_skill", 30, 3.0, ("Vo",), source="s_main"),
                 sp.SPNode("cap", "cap_up", 30, 50.0, ("Vo",))]
        # stat 150 below the cap (> margin 100): the tier order decides; near the cap caps come first
        first = lambda params, stat=350: sp.plan_sp(nodes, sp=30, primary="Vo", stat=stat, cap=500,  # noqa: E731
                                                    params=params["sp"]).steps[0].node_id
        self.assertEqual(first(self.params()), "skill")
        self.assertEqual(first(self.params(), stat=450), "cap")
        self.assertEqual(first(self.params(ProduceSP加点="上限优先"), stat=450), "cap")
        cap_only = [nodes[1]]
        far = lambda params: sp.plan_sp(cap_only, sp=30, primary="Vo", stat=300, cap=450,  # noqa: E731
                                        params=params["sp"]).steps
        self.assertEqual(far(self.params()), ())                                  # 150 from the cap > margin 100
        self.assertEqual([s.node_id for s in far(self.params(Produce上限余量="200"))], ["cap"])

    def test_audition_style(self):
        live_state = wa.LiveState(turn=1, judges=tuple(wa.JudgeView(a, r, 1.0, 75_000) for a, r in
                                                       (("Vo", 2), ("Da", 3), ("Vi", 1))),
                                  options=(wa.AppealOption("c1", "Vo", 2.5), wa.AppealOption("c2", "Vo", 1.5)),
                                  stats={"Vo": 2400, "Da": 300, "Vi": 250, "Me": 500}, memory_gauge=30,
                                  memory_level=3, mental=500, mental_max=500, need_first=True)
        first = wa.decide(live_state, self.params()["live"])
        self.assertGreater(max(r.terms["focus"] for r in first.rows), 0)
        by_attr = wa.decide(live_state, self.params(Produce出牌风格="按属性")["live"])
        self.assertEqual(max(r.terms["focus"] for r in by_attr.rows), 0)

    def test_live_mode_and_auto_threshold(self):
        readings = {"need_first": False, "estimate": {"p_pass": 0.98}, "auto": "OFF"}

        def plan(**option):
            config = options_config(self.interface, option)
            memory = nav.RunMemory.from_produce_config(config)
            return wa.live_plan(readings, memory, pc.wing_params(config)["live"])["action"]

        self.assertEqual(plan(), "stop")                                               # own policy, no live state
        self.assertEqual(plan(Produce试镜出牌="游戏 Auto"), "live_auto")                  # 0.98 ≥ 0.97
        self.assertEqual(plan(Produce试镜出牌="游戏 Auto", Produce高级Auto门槛="99%"), "stop")
        final = dict(readings, need_first=True)                                        # semifinal / final
        memory = nav.RunMemory.from_produce_config(options_config(self.interface, {"Produce试镜出牌": "游戏 Auto"}))
        self.assertEqual(wa.live_plan(final, memory)["reason"], "live_state_unread")    # never the game's Auto

    def test_event_default(self):
        options = [we.EventOption("a"), we.EventOption("b"), we.EventOption("c")]
        self.assertEqual(we.choose_event_option(options, **self.params()["event"]).index, 0)
        self.assertEqual(we.choose_event_option(options, **self.params(Produce事件选项="第 3 项")["event"]).index, 2)
        promise = [we.EventOption("a"), we.EventOption("二週間後", True)]
        self.assertEqual(we.choose_event_option(promise, **self.params(Produce事件选项="第 1 项")["event"]).index, 1)

    def test_items_need_the_policy(self):
        # 2026-10-12 (U09): the UI offers only 「不带」; the backend presets stay refused before any start
        config = pc.ProduceConfig.from_task_options({"p_items": "memo_bond"})
        self.assertEqual((config.manual["use_p_items"], config.caps["max_p_items"]), (True, 2))
        self.assertEqual(pc.run_preflight(config, policy=POLICY)["reasons"], ["p_items_blocked_by_policy"])
        self.assertIn("p_items_blocked_by_policy", pc.run_preflight(config)["reasons"])   # the repository policy
        allowed = {"resource_usage": dict(POLICY["resource_usage"], other_inventory_allowed=True)}
        self.assertEqual(pc.run_preflight(config, policy=allowed)["reasons"], [])


    def test_midrun_presets_are_refused_even_when_the_policy_allows(self):
        allowed = {"resource_usage": dict(POLICY["resource_usage"], other_inventory_allowed=True)}
        for case, midrun in (("start_boost", False), ("final_insurance", True), ("healing", True)):
            with self.subTest(case):
                config = pc.ProduceConfig.from_task_options({"p_items": case})
                self.assertEqual(config.caps["max_p_items"], 2)
                reasons = pc.run_preflight(config, policy=allowed)["reasons"]
                self.assertEqual("p_items_midrun_use_unsupported" in reasons, midrun, reasons)
                self.assertIn("p_items_blocked_by_policy", pc.run_preflight(config, policy=POLICY)["reasons"])
        self.assertNotIn("親愛度アイテム（最低ランク）", {i for items in pc.P_ITEM_PRESETS.values() for i in items})


class SafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interface = run_tasks.load_interface()

    def test_no_option_can_enable_paid_items_or_jewels(self):
        task = self.interface.tasks[TASK]
        free = {item for items in pc.P_ITEM_PRESETS.values() for item in items}
        for key in task["option"]:
            option = self.interface.options[key]
            for case in option.get("cases", []):
                text = json.dumps(case.get("pipeline_override", {}), ensure_ascii=False)
                for word in ("premium", "jewel", "ジュエル", "soda", "recovery", "paid", "continue", "stamina"):
                    self.assertNotIn(word, text.lower(), (key, case["name"]))
                for goal in ("True End", "只过季门槛"):
                    with self.subTest(key=key, case=case["name"], goal=goal):
                        try:
                            config = options_config(self.interface, {key: case["name"]}, goal=goal)
                        except ValueError:
                            self.assertEqual(key, "Produce难度")     # e.g. Master for the weekly Auto: refused
                            continue
                        self.assertFalse(config.data["premium_currency_allowed"])
                        self.assertFalse(config.data["recovery_soda_allowed"])
                        manual = config.manual or {}
                        self.assertFalse(manual.get("use_continue_ticket", False))
                        self.assertLessEqual(set(manual.get("p_items") or []), free)
                        self.assertEqual(config.data["stamina_multiplier"], 1)
        for item in free:
            self.assertFalse(any(m.lower() in item.lower() for m in pc.PAID_ITEM_MARKERS), item)

    def test_paid_item_names_rejected(self):
        for name in ("ジュエル", "有償ジュエルパック", "283パス特典", "回復ソーダ"):
            data = pc.default_config("true_end")
            data["manual"].update(use_p_items=True, p_items=[name])
            data["caps"]["max_p_items"] = 1
            with self.subTest(name), self.assertRaises(ValueError):
                pc.ProduceConfig.from_dict(data)

    def test_repository_policy_keeps_paid_resources_off(self):
        usage = pc.load_project_policy()["resource_usage"]
        self.assertIs(usage["premium_currency_allowed"], False)
        self.assertIs(usage["paid_items_allowed"], False)
        broken = {"resource_usage": {"premium_currency_allowed": True, "paid_items_allowed": False}}
        config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        self.assertIn("policy_paid_resources_not_false", pc.resource_reasons(config, broken))

    def test_user_stop_points_stay_stops(self):
        for screen in pc.ALWAYS_USER_STOPS:
            if screen in ("IdolPicker", "IdolPickerConfirm"):   # decide only for the opt-in team edit, else stop
                self.assertEqual(nav.WingNavRuntime().decide(screen, {}).reason, "team_edit_not_automated")
                continue
            if screen == "Options":
                # 2026-10-14: only Maa's own read pass wheels the list / taps ← (verified live); never 初期設定に戻す;
                # a page the user opened is read and always a stop. 2026-10-25: the opt-in settings fix adds the two
                # OFF taps, 保存 and 閉じる of its 「設定を保存しました。」 (tests/test_wing_settings_fix.py); a read
                # pass never returns them.
                self.assertEqual(nav.screen_intents(screen), ("options_scroll_down", "options_back", "options_unread_off",
                                                              "options_popup_off", "options_save", "options_saved_close"))
                self.assertEqual(nav.WingNavRuntime().decide(screen, {}).action, "stop")
                continue
            self.assertEqual(nav.RULES[screen].kind, "stop", screen)
        # 使用確認 (stamina 30) stops under every default; only start mode + the launcher's spend flag may press it.
        rt = nav.WingNavRuntime()
        rt.begin()
        plain = {"start_confirm": {"layout": "plain", "stamina_before": 300, "stamina_after": 270, "multiplier": 1}}
        self.assertEqual(rt.decide("StartConfirm", plain).reason, "spend_not_enabled")

    def test_carriers_are_attach_only(self):
        from tools.check_project import load_jsonc
        pipeline = load_jsonc(ROOT / "assets" / "resource" / "base" / "pipeline" / "produce.json")
        for key in WING_OPTIONS + ADVANCED:
            option = self.interface.options[key]
            self.assertIn(key, self.interface.tasks[TASK]["option"])
            self.assertIn(option["default_case"], [c["name"] for c in option["cases"]])
            for case in option["cases"]:
                for node, body in case["pipeline_override"].items():
                    self.assertTrue(node.startswith("SC.Produce.Config.Wing."), node)
                    self.assertEqual(set(pipeline[node]), {"recognition", "attach"})
                    self.assertEqual(set(body), {"attach"})


class UiVisibilityTests(unittest.TestCase):
    def test_annotations(self):
        annotations = json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))
        notes = annotations["options"]
        for key in WING_OPTIONS:
            # 2026-10-11: 重新抽选 (stamina 30, launcher authority not wired) is developer-only until it is wired;
            # 2026-10-25: the Lv5 lock gave way to 自动锁定新书.
            self.assertEqual(bool(notes[key].get("dev")), key in ("Produce再抽选", "Produce保护Lv5书"), key)
        self.assertEqual(notes["Produce卖书策略"]["under"], "Produce自动卖书")
        for key in ADVANCED:
            self.assertTrue(notes[key].get("dev"), key)
        self.assertEqual(notes[ADVANCED[0]]["heading"], "高级")
        task = next(t for t in ui_schema.export_schema()["tasks"] if t["name"] == TASK)
        # 2026-11-04: an option shown for one scenario repeats its group's heading (same heading = same fold group)
        headings = list(dict.fromkeys(notes[k]["heading"] for k in task["options"] if notes.get(k, {}).get("heading")))
        # 2026-10-13 (user): no 「开始」 section -- the UI-only 开新一局 switch is fixed to Yes and hidden
        self.assertEqual(headings, ["培养目标", "P 偶像与编队", "行动与 SP", "试镜", "事件与道具", "收尾与结算", "高级"])
        self.assertTrue(notes["Produce可开新局"].get("hidden"))
        self.assertFalse(notes["Produce可开新局"].get("first"))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_advanced_hidden_unless_developer(self):
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { visibleOptions } from './ui/components/display.js';"
            "import { optionField } from './ui/components/option-field.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const task = schema.tasks.find((t) => t.name === 'AutoProduce');"
            "const entry = { name: 'AutoProduce', enabled: true, option: {} };"
            "const view = (developer, option = {}) => visibleOptions({ schema, annotations, prefs: { developer } }, task,"
            " { ...entry, option });"
            "const state = { schema, annotations, prefs: {} };"
            "const field = optionField({ state, key: 'Produce休息阈值', value: '15%', disabled: false, onChange() {}, focusKey: 'k' });"
            "const plain = optionField({ state, key: 'Produce低分兜底', value: '主属性课', disabled: false, onChange() {}, focusKey: 'k2' });"
            "console.log(JSON.stringify({ user: view(false), dev: view(true), selling: view(false, { 'Produce自动卖书': 'Yes' }),"
            " text: field.textContent,"
            " cls: field.attributes.class, plainCls: plain.attributes.class,"
            " css: document.querySelectorAll('link[href=\"styles/option-sections.css\"]').length }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        for key in ADVANCED:
            self.assertNotIn(key, out["user"])
            self.assertIn(key, out["dev"])
        for key in WING_OPTIONS:
            if key == "ProduceP道具":      # U09: single case 「不带」 -> not shown as a choice
                self.assertNotIn(key, out["user"])
                continue
            if key == "Produce卖书策略":   # 2026-10-25: shown only under 自动卖书 = Yes
                self.assertNotIn(key, out["user"])
                self.assertIn(key, out["selling"])
                continue
            self.assertIn(key, out["dev"] if key in ("Produce再抽选", "Produce保护Lv5书") else out["user"])
        self.assertTrue(out["text"].startswith("行动与 SP"))
        self.assertIn("has-heading", out["cls"])
        self.assertNotIn("has-heading", out["plainCls"])
        self.assertEqual(out["css"], 1)


if __name__ == "__main__":
    unittest.main()

import contextlib
import copy
import datetime as dt
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from agent.core import grade_flow as grade
from tools import grade_protocol as proto
from tools import repeatable_navigation_protocol as rep

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc

CONFIG = {
    "version": 1, "premium_currency_allowed": False,
    "budgets": {"coins": 0, "basic_training_tickets": 0, "fes_participation_rights": 1},
    "shop": {"enabled": False, "purchase_scope": "daily_discount_only", "include_third": False, "coin_budget": 0,
             "coin_reserve": 0, "items": []},
    "training": {"enabled": False, "card_id": None, "candidate_card_ids": [], "ticket_type": "basic_training_ticket",
                 "ticket_quantity": 1, "ticket_budget": 0},
    "match_fes": {"enabled": False, "mode": "skip", "count": 20, "participation_budget": 0, "stage_id": None,
                  "stage_selection": "latest"},
    "grade_fes": {"enabled": True, "difficulty_id": "Normal", "count": 1, "participation_budget": 1},
}
EVIDENCE = {"rights": {"current": 6, "max": 20}, "title": grade.EXPECTED_TITLE, "prompt": grade.EXPECTED_PROMPT}
EMPTY = {"attempts": 0, "rights_used": 0, "unresolved": []}


def config(**changes):
    data = copy.deepcopy(CONFIG)
    data["grade_fes"].update(changes)
    return data


def hit(scene, roi, score=0.99):
    return {"scene": scene, "hit": score >= 0.95, "score": score, "roi": list(roi), "raw_detail": {"filtered": [{"box": list(roi)}]}}


def recognitions(stage, *, low=None, dim=False, present=()):
    rows = []
    groups = grade.STAGE_GROUPS[stage]
    for group, anchors in grade.ANCHORS.items():
        for anchor, roi in anchors.items():
            name = grade.scene_name(group, anchor)
            score = 0.99 if group in groups else 0.2
            if name == low:
                score = 0.9
            if name in present:
                score = 0.85
            rows.append(hit(name, roi, score))
    for group, checks in grade.COLOR_CHECKS.items():
        for name, rule in checks.items():
            ok = group in groups and not dim
            rows.append({"scene": grade.scene_name(group, name), "hit": ok, "roi": rule["roi"],
                         "matched_pixels": rule["count"] + 10 if ok else 0})
    for name, roi in grade.EXCLUSIONS.items():
        rows.append(hit(grade.exclusion_scene(name), roi, 0.1))
    return rows


class PipelineTest(unittest.TestCase):
    def test_fixture_is_fixed_and_disabled(self):
        pipeline = grade.fixed_pipeline()
        clicks = {k: v for k, v in pipeline.items() if v.get("action") == "Click"}
        expected = {grade.node_name(a, s) for a, (stages, _) in grade.ACTIONS.items() for s in stages}
        self.assertEqual(set(clicks), expected)
        self.assertTrue(all(v["enabled"] is False for v in clicks.values()))
        for action, (stages, point) in grade.ACTIONS.items():
            for stage in stages:
                node = clicks[grade.node_name(action, stage)]
                self.assertEqual(node["target"], [*point, 1, 1])
                self.assertEqual(node["all_of"], grade.anchor_names(stage))
                self.assertNotIn("next", node)

    def test_one_action_enabled(self):
        pipeline = grade.fixed_pipeline("grade-difficulty-normal", "Difficulty")
        enabled = [k for k, v in pipeline.items() if v.get("enabled") is True]
        self.assertEqual(enabled, [grade.node_name("grade-difficulty-normal", "Difficulty")])
        self.assertEqual(pipeline[grade.PREFIX + "Difficulty.Observe"]["next"][0], enabled[0])
        with self.assertRaises(ValueError):
            grade.fixed_pipeline("grade-difficulty-normal", "FesMain")
        with self.assertRaises(ValueError):
            grade.fixed_pipeline("grade-bulk-toggle", "Difficulty")

    def test_difficulty_guard_requires_cost_bubble_checkbox_and_score_labels(self):
        names = grade.anchor_names("Difficulty")
        for anchor in ("Prompt", "CheckboxOff", "BulkLabel", "ScoreEasy", "ScoreNormal", "ScoreHard", "Title", "BubbleBright"):
            self.assertIn(grade.scene_name("Difficulty", anchor), names)

    def test_points_match_host_and_protocol(self):
        source = (ROOT / "browser_host/DailyNavigationPolicy.cs").read_text(encoding="utf-8")
        self.assertEqual(list(proto.GRADE_ACTION_POINTS), re.findall(r'"([a-z-]+)"', source.split("GradeActions = {")[1].split("};")[0]))
        for action, (_, (x, y)) in grade.ACTIONS.items():
            self.assertEqual(proto.GRADE_ACTION_POINTS[action], (x, y))
            self.assertRegex(source, re.escape(f'"{action}"') + r"[^\n]*=> new Point\(" + f"{x}, {y}" + r"\)")
        self.assertEqual(set(grade.CONSUMING), set(proto.CONSUMING))
        self.assertEqual(set(grade.REPEATABLE), set(rep.GRADE_ACTIONS))
        self.assertFalse(set(grade.CONSUMING) & set(rep.ACTIONS))
        budget = (ROOT / "browser_host/RepeatableNavigationBudget.cs").read_text(encoding="utf-8")
        for action in grade.REPEATABLE:
            self.assertIn(f'"{action}"', budget)
        self.assertIn('"run_daily_grade.py"', budget)
        self.assertEqual(rep.producer_for("grade-back"), rep.GRADE_PRODUCER)


class GuardTest(unittest.TestCase):
    def test_complete_stages(self):
        for stage in grade.GUARD_STAGES:
            rows = recognitions(stage)
            self.assertTrue(grade.stage_matches(stage, rows), stage)
            self.assertTrue(grade.exclusions_clear(stage, rows), stage)

    def test_low_anchor_dim_and_foreign_refused(self):
        self.assertFalse(grade.stage_matches("FesMain", recognitions("FesMain", low="SC.Grade.FesMain.JoinLabel")))
        self.assertFalse(grade.stage_matches("FesMain", recognitions("FesMain", dim=True)))
        self.assertFalse(grade.stage_matches("Difficulty", recognitions("Difficulty", dim=True)))
        # Mode select buttons visible: FesMain refuses (ModeSelect is not underlying of FesMain).
        self.assertFalse(grade.exclusions_clear("FesMain", recognitions("FesMain", present=("SC.Grade.ModeSelect.GradeButton",))))
        # Help popup present: FesMain refuses; difficulty title present: ModeSelect refuses.
        self.assertFalse(grade.exclusions_clear("FesMain", recognitions("FesMain", present=("SC.Grade.FesHelp.Title",))))
        self.assertFalse(grade.exclusions_clear("ModeSelect", recognitions("ModeSelect", present=("SC.Grade.Difficulty.Title",))))
        # The dimmed grade button under the difficulty dialog is allowed.
        self.assertTrue(grade.exclusions_clear("Difficulty", recognitions("Difficulty", present=("SC.Grade.ModeSelect.GradeButton",))))
        rows = recognitions("Difficulty")
        rows = [dict(r, score=0.9, hit=False) if r["scene"] == grade.exclusion_scene("RehearsalTitle") else r for r in rows]
        self.assertFalse(grade.exclusions_clear("Difficulty", rows))
        self.assertFalse(grade.exclusions_clear("Difficulty", [r for r in recognitions("Difficulty")
                                                             if r["scene"] != grade.exclusion_scene("Loading")]))

    def test_navigation_overlay_exclusion(self):
        from agent.core.daily_navigation import GROUPS
        mission = [{"scene": n, "hit": False, "score": 0.86} for n in GROUPS["MissionCompleted"]]
        fes = [{"scene": n, "hit": True, "score": 0.99} for n in GROUPS["FesMenu"]]
        home = [{"scene": n, "hit": True, "score": 0.99} for n in GROUPS["Home"]]
        self.assertTrue(grade.navigation_clear("FesMenu", home + fes))
        self.assertFalse(grade.navigation_clear("Home", home + fes))
        self.assertFalse(grade.navigation_clear("FesMenu", home + fes + mission))
        self.assertFalse(grade.navigation_clear("FesMain", home))
        self.assertFalse(grade.navigation_clear("FesMain", []))
        self.assertEqual(grade.navigation_blockers("FesMenu", home + fes + mission), ["MissionCompleted"])


class RuleTest(unittest.TestCase):
    def test_allowed_default_normal(self):
        tap = grade.validate_difficulty_tap(config(), "grade-difficulty-normal", EVIDENCE, EMPTY)
        self.assertEqual((tap["difficulty_id"], tap["cost"], tap["bulk"], tap["attempt_index"]), ("Normal", 1, False, 1))

    def test_refusals(self):
        cases = [
            (config(), "grade-difficulty-easy", EVIDENCE, EMPTY),
            (config(enabled=False), "grade-difficulty-normal", EVIDENCE, EMPTY),
            (config(difficulty_id="normal"), "grade-difficulty-normal", EVIDENCE, EMPTY),
            (config(difficulty_id="Expert"), "grade-difficulty-normal", EVIDENCE, EMPTY),
            (config(participation_budget=0), "grade-difficulty-normal", EVIDENCE, EMPTY),
            (config(), "grade-difficulty-normal", dict(EVIDENCE, rights={"current": 0, "max": 20}), EMPTY),
            (config(), "grade-difficulty-normal", dict(EVIDENCE, prompt="フェス参加権を5消費します"), EMPTY),
            (config(), "grade-difficulty-normal", dict(EVIDENCE, title="フェスリハーサルの難易度を選択してください"), EMPTY),
            (config(), "grade-difficulty-normal", dict(EVIDENCE, rights=None), EMPTY),
            (config(), "grade-difficulty-normal", EVIDENCE, {"attempts": 1, "rights_used": 1, "unresolved": []}),
            (config(count=3), "grade-difficulty-normal", EVIDENCE, {"attempts": 1, "rights_used": 1, "unresolved": []}),
            (config(), "grade-difficulty-normal", EVIDENCE, {"attempts": 0, "rights_used": 0, "unresolved": [{"day": "x"}]}),
            (config(), "grade-back", EVIDENCE, EMPTY),
        ]
        for args in cases:
            with self.assertRaises(ValueError, msg=str(args[1:])):
                grade.validate_difficulty_tap(*args)
        premium = config()
        premium["premium_currency_allowed"] = True
        with self.assertRaises(ValueError):
            grade.validate_difficulty_tap(premium, "grade-difficulty-normal", EVIDENCE, EMPTY)
        no_global = config()
        no_global["budgets"]["fes_participation_rights"] = 0
        with self.assertRaises(ValueError):
            grade.validate_difficulty_tap(no_global, "grade-difficulty-normal", EVIDENCE, EMPTY)

    def test_parse_rights(self):
        self.assertEqual(grade.parse_rights("6/20"), {"current": 6, "max": 20})
        for text in ("21/20", "6/0", "6-20", None, "6/20+0"):
            with self.assertRaises(ValueError):
                grade.parse_rights(text)

    def test_evidence_normalises_ocr(self):
        ev = grade.difficulty_evidence({"rights": "6/20"}, {"Title": grade.EXPECTED_TITLE, "Prompt": "フェス参加権を 1 消費します"})
        self.assertEqual(ev["prompt"], grade.EXPECTED_PROMPT)
        self.assertIsNone(grade.difficulty_evidence({"rights": "6/20"}, {"Title": None, "Prompt": None})["title"])


class LedgerTest(unittest.TestCase):
    def test_game_day_matches_host(self):
        self.assertEqual(grade.game_day(dt.datetime(2026, 10, 1, 19, 59, 59, tzinfo=UTC)), "2026-10-01")
        self.assertEqual(grade.game_day(dt.datetime(2026, 10, 1, 20, 0, 0, tzinfo=UTC)), "2026-10-02")
        with self.assertRaises(ValueError):
            grade.game_day(dt.datetime(2026, 10, 2))

    def test_pending_blocks_and_never_resets(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as temporary:
            ledger = grade.GradeLedger(Path(temporary) / "daily_grade")
            day = "2026-10-02"
            self.assertEqual(ledger.summary(day), EMPTY)
            tap = grade.validate_difficulty_tap(config(), "grade-difficulty-normal", EVIDENCE, ledger.summary(day))
            witness = {"version": 1, "runner": grade.RUNNER, "no_retry": True, "tap": tap}
            proof = ledger.write_pending(day, witness)
            self.assertEqual(proof["index"], 1)
            summary = ledger.summary(day)
            self.assertEqual((summary["attempts"], summary["rights_used"], len(summary["unresolved"])), (1, 1, 1))
            with self.assertRaises(ValueError):
                ledger.write_pending(day, witness)  # stale attempt index
            with self.assertRaises(ValueError):
                grade.validate_difficulty_tap(config(count=5, participation_budget=5), "grade-difficulty-normal", EVIDENCE,
                                              ledger.summary(day))
            # A pending from an earlier game day still blocks today.
            self.assertTrue(ledger.summary("2026-10-03")["unresolved"])
            with self.assertRaises(ValueError):
                ledger.write_pending(day, dict(witness, no_retry=False))
        with self.assertRaises(ValueError):
            grade.GradeLedger(ROOT / "tools")


class ProtocolTest(unittest.TestCase):
    def session(self, day="2026-10-02"):
        actions = list(proto.GRADE_ACTION_POINTS)
        return {"version": 1, "host": "127.0.0.1", "port": 1234, "token": "A" * 64, "pid": 7, "input_enabled": True,
                "browser_demo_only": False, "transport": proto.TRANSPORT, "actions": actions,
                "budget_paths": {a: str(proto.budget_path(a, day)) for a in actions}}

    def status(self, **changes):
        base = {"ready": True, "browser_demo": False, "input_enabled": False, "composition_game": True, "minimized": False,
                "window_visible": True, "webview_visible": True, "supervised_daily": True, "supervised_navigation": None,
                "supervised_grade": True, "frame_sha256": "b" * 64, "frame_age_ms": 10, "width": 1280, "height": 720,
                "source_capture_width": 1280, "source_capture_height": 720}
        base.update(changes)
        return base

    def test_session(self):
        session = proto.validate_session(self.session(), 7)
        self.assertEqual(proto.session_day(session), "2026-10-02")
        for mutate in (lambda s: s.update(transport="webview2-composition-daily"),
                       lambda s: s.update(actions=s["actions"][:-1]),
                       lambda s: s["budget_paths"].update({"grade-back": str(proto.budget_path("grade-back", "2026-10-03"))}),
                       lambda s: s["budget_paths"].update({"grade-difficulty-normal": str(ROOT / "dev_assets/private/daily_navigation/x.json")}),
                       lambda s: s.update(pid=8)):
            bad = self.session()
            mutate(bad)
            with self.assertRaises(ValueError):
                proto.validate_session(bad, 7)

    def test_request_requires_switch_and_fresh_frame(self):
        session = self.session()
        request = proto.navigate_request(session, self.status(), "b" * 64, "grade-difficulty-normal")
        self.assertEqual((request["command"], request["action"]), ("daily-navigate", "grade-difficulty-normal"))
        for status in (self.status(supervised_grade=False), self.status(frame_sha256="c" * 64), self.status(frame_age_ms=6000),
                       self.status(minimized=True), self.status(supervised_navigation="announcement-back")):
            with self.assertRaises(ValueError):
                proto.navigate_request(session, status, "b" * 64, "grade-difficulty-normal")
        with self.assertRaises(ValueError):
            proto.navigate_request(session, self.status(), "b" * 64, "grade-bulk-toggle")

    def test_repeatable_scopes(self):
        self.assertTrue(all(a not in rep.allowed_actions(True, True) for a in rep.GRADE_ACTIONS))
        self.assertTrue(all(a in rep.allowed_actions(False, False, True) for a in rep.GRADE_ACTIONS))
        status = self.status(repeatable_navigation=True)
        session = {"version": 1, "host": "127.0.0.1", "port": 1, "token": "a" * 64, "pid": 7, "input_enabled": True,
                   "browser_demo_only": False, "transport": rep.TRANSPORT, "ledger_root": str(rep.LEDGER_ROOT),
                   "game_day": "2026-10-02", "per_action_max": 3, "day_max": 60, "actions": rep.allowed_actions(False, False, True)}
        rep.validate_session(session, 7)
        rep.navigate_request(session, status, "b" * 64, "grade-back")
        with self.assertRaises(ValueError):
            rep.navigate_request(session, dict(status, supervised_grade=False), "b" * 64, "grade-back")


class RunnerArgumentTest(unittest.TestCase):
    def run_main(self, *extra):
        from tools import run_daily_grade
        base = ["--foreground-manifest", "x.json", "--foreground-contracts", "y.json", "--session", "s.json",
                "--report", str(ROOT / "dev_assets/private/grade-test-never-written.json")]
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_daily_grade.main(base + list(extra))

    def test_refusals(self):
        self.run_main("--stage", "AfterStart", "--execute", "--action", "grade-back")
        self.run_main("--stage", "FesMain", "--execute")
        self.run_main("--stage", "FesMain", "--execute", "--action", "grade-difficulty-normal")
        self.run_main("--stage", "Difficulty", "--execute", "--repeatable", "--action", "grade-difficulty-normal")
        self.run_main("--stage", "FesMain", "--repeatable", "--action", "grade-join-open")
        self.run_main("--stage", "Difficulty", "--action", "grade-mode-grade")
        self.assertFalse((ROOT / "dev_assets/private/grade-test-never-written.json").exists())

    def test_default_config_disabled(self):
        data = json.loads((ROOT / "daily_tasks.json").read_text(encoding="utf-8"))
        self.assertFalse(data["grade_fes"]["enabled"])
        with self.assertRaises(ValueError):
            grade.grade_config(data)


if __name__ == "__main__":
    unittest.main()

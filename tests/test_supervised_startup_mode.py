"""The sole production frame-permit exception, exercised only with a fake host and native Maa replay."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import run_tasks as launcher


def startup_config():
    return {"tasks": ["Startup"], "max_clicks": 4, "task_max_clicks": {"Startup": 4}, "task_timeout_s": 8}


class SupervisedStartupConfigurationTests(unittest.TestCase):
    def validate(self, config, **options):
        return launcher.supervised_startup_config(config, live=options.get("live", True),
            offline=options.get("offline", False), simulate_input=options.get("simulate_input", False))

    def test_scopes_families_without_mutating_config(self):
        config = startup_config()
        scoped = self.validate(config)
        self.assertEqual(set(scoped["families"]), {"startup", "popup", "nav"})
        self.assertNotIn("families", config)
        self.assertEqual(self.validate({**config, "families": ["startup"]})["families"], ["startup"])
        self.assertEqual(launcher.task_click_cap(self.validate({**config, "task_max_clicks": 1}), "Startup"), 1)

    def test_rejects_other_modes_tasks_spend_and_budgets(self):
        config = startup_config()
        invalid = [{"tasks": None}, {"tasks": ["Startup", "MissionStatus"]}, {"tasks": ["AutoProduce"]},
            {"tasks": [{"name": "Startup"}]}, {"tasks": ["Startup", "Startup"]},
            {"max_clicks": 0}, {"max_clicks": 5}, {"max_clicks": True},
            {"task_max_clicks": 0}, {"task_max_clicks": {"Startup": 5}},
            {"task_max_clicks": {}}, {"allow_spend": ["shop-exchange-confirm"]},
            {"families": []}, {"families": ["popup"]}, {"families": ["startup", "mission"]},
            {"families": ["startup", "startup"]}]
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.validate({**config, **update})
        for missing in ("max_clicks", "task_max_clicks"):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                self.validate({key: value for key, value in config.items() if key != missing})
        for options in ({"live": False}, {"offline": True}, {"simulate_input": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.validate(config, **options)

    def test_config_cannot_persist_the_switch(self):
        with self.assertRaises(ValueError):
            launcher.validate_run_config({**startup_config(), "supervised_startup": True})

    def test_title_entry_is_runtime_only_and_the_cut_in_skip_stays(self):
        override = launcher.supervised_startup_override()
        entry = override["SC.Startup.Entry"]
        self.assertEqual(entry["recognition"], "And")
        self.assertEqual(entry["all_of"], ["SC.Startup.Anchor.TouchToStart", "SC.Startup.Anchor.Publisher"])
        self.assertEqual(entry["next"], ["SC.Startup.TouchToStart"])
        # 2026-10-27: not on the title -> a full-screen gacha announcement gets its own stop reason, else the unknown one
        self.assertEqual(entry["on_error"], ["SC.Common.Stop.GachaAnnouncement", "SC.Common.FailTask"])
        self.assertNotIn("any_of", override["SC.Startup.Started"])     # shipped list, incl. the cut-in guard
        self.assertEqual(override["SC.Startup.Started"]["next"], ["SC.Common.GoHome"])
        # The 新ガシャ/新アイドル登場 cut-in's right SKIP is ordinary navigation (as in a sequence)
        self.assertNotIn("SC.Popup.NewGacha.Close", override)
        self.assertNotIn("SC.Popup.CloseKnown", override)
        self.assertEqual(launcher.load_jsonc(launcher.PIPELINE_DIR / "startup.json")
                         ["SC.Startup.Entry"]["recognition"], "DirectHit")

    def test_cli_binds_only_the_explicit_per_run_switch(self):
        summary = {"order": ["Startup"], "input_mode": launcher.LIVE_INPUT, "tasks": [], "failed": 0,
                   "fes_allocation": {"status": "idle"},
                   "run_control": {"stop_requested": False, "events_complete": True}}
        with tempfile.TemporaryDirectory(dir=launcher.PRIVATE) as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(startup_config()), encoding="utf-8")
            for enabled in (False, True):
                args = ["--config", str(path), "--live", "--session", "unused-fake-session.json"]
                if enabled:
                    args.append("--supervised-startup")
                with patch.object(launcher, "run", return_value=summary) as run:
                    self.assertEqual(launcher.main(args), 0)
                    self.assertIs(run.call_args.kwargs["supervised_startup"], enabled)
                    self.assertNotIn("frame_guard_enabled", run.call_args.kwargs)


from tests.test_task_launcher import FakePipelineHost, HOME, PRIVATE, ROOT, RISK_POLICY, V24, private_ready


@unittest.skipUnless(private_ready(), "requires Maa v5.12.3, native private templates and screenshot fixtures")
class SupervisedStartupNativeReplayTests(unittest.TestCase):
    def run_fake(self, steps, *, enabled=True, config=None):
        from agent.core import webview_controller as wc
        from tests.test_pipeline_frame_guard import MemoryAudit
        from tests.test_webview_controller import pipeline_session

        host = FakePipelineHost(steps)
        session = pipeline_session()
        session["pid"] = host.PID
        config = startup_config() if config is None else config
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, launcher.SUPERVISED_STARTUP_FAMILIES),
            {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(), MemoryAudit(), host.send,
            policy=RISK_POLICY, limits=wc.ClickLimits(run_max=config["max_clicks"], task_max=4))
        with patch.object(launcher, "build_live_gate", return_value=gate) as builder:
            summary = launcher.run(config, live=True, session="fake-unused", policy=RISK_POLICY,
                summary_path=False, save_diagnostics=False, supervised_startup=enabled)
        return host, summary, builder.call_args

    def test_title_to_clean_home_uses_native_permit_and_v2_binding(self):
        host, summary, args = self.run_fake([(V24 + "frame-001.png", "click"), (HOME, "click")])
        self.assertEqual(summary["failed"], 0, summary["tasks"])
        self.assertEqual([r["action"] for r in host.requests], ["game-start"])
        self.assertTrue(all(r["protocol_version"] == 2 and r["capture_seq"] > 0 and
                            r["frame_binding_seq"] > 0 for r in host.requests))
        self.assertTrue(summary["live"]["frame_guard_enabled"])
        self.assertTrue(summary["live"]["supervised_startup"])
        self.assertEqual(summary["tasks"][0]["node_trace"][-1], "SC.Common.HomeClean")
        self.assertEqual(set(args.args[0]["families"]), launcher.SUPERVISED_STARTUP_FAMILIES)
        self.assertEqual(summary["allow_spend"], [])

    def test_current_read_only_title_observation_replays_to_home(self):
        source = "dev_assets/private/browser_tasks/20261005-222803-058763/frame-001.png"
        if not (ROOT / source).is_file():
            self.skipTest("missing current private title observation")
        host, summary, _ = self.run_fake([(source, "click"), (HOME, "click")])
        self.assertEqual(summary["failed"], 0, summary["tasks"])
        self.assertEqual([r["action"] for r in host.requests], ["game-start"])
        self.assertEqual(summary["tasks"][0]["node_trace"][-1], "SC.Common.HomeClean")

    def test_home_first_and_missing_publisher_never_click_or_succeed(self):
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix="startup-title-negative-", dir=PRIVATE) as directory:
            path = Path(directory) / "missing-publisher.png"
            image = Image.open(ROOT / (V24 + "frame-001.png")).convert("RGB")
            image.paste((0, 0, 0), (1043, 18, 1261, 76))
            image.save(path)
            for source in (HOME, str(path)):
                with self.subTest(source=source):
                    host, summary, _ = self.run_fake([(source, "click")])
                    self.assertEqual(host.requests, [])
                    self.assertEqual(summary["failed"], 1)

    def test_one_click_budget_stops_before_reward_without_retry(self):
        config = {**startup_config(), "max_clicks": 1}
        host, summary, _ = self.run_fake([(V24 + "frame-001.png", "click"),
            (V24 + "frame-016.png", "click"), (HOME, "click")], config=config)
        self.assertEqual([r["action"] for r in host.requests], ["game-start"])
        self.assertEqual(summary["real_input_attempts"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertIn("run_click_cap_reached", [row["reason"] for row in summary["tasks"][0]["live_clicks"]])

    def test_new_gacha_after_title_is_skipped_once(self):
        """The cut-in's right SKIP is pressed (was left for the user)."""
        source = "dev_assets/private/manual-capture-kept-20261002/new-gacha-cutin-20261003.png"
        if not (ROOT / source).is_file():
            self.skipTest("missing native new-gacha fixture")
        host, summary, _ = self.run_fake([(V24 + "frame-001.png", "click"), (source, "click"), (HOME, "click")])
        self.assertEqual([r["action"] for r in host.requests], ["game-start", "popup-new-gacha-skip"])
        self.assertEqual(summary["failed"], 0)

    def test_ordinary_live_still_refuses_without_frame_permits(self):
        host, summary, _ = self.run_fake([(V24 + "frame-001.png", "click"), (HOME, "click")], enabled=False)
        self.assertFalse(summary["live"]["frame_guard_enabled"])
        self.assertFalse(summary["live"]["supervised_startup"])
        self.assertEqual(host.requests, [])
        self.assertEqual(summary["failed"], 1)


if __name__ == "__main__":
    unittest.main()

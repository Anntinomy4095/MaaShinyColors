"""Offline replay of the observed free Grade season reward; only the left OK is an input."""
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import pipeline_actions as pa
from tools import run_tasks
from tests.test_task_launcher import HOME, private_ready, pipeline_nodes

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / "dev_assets/private/live-20261020/fes-period-reward.png"
FES_MAIN = ROOT / "dev_assets/private/grade-run-03c-entry-frames/frame-008.png"
ACTION = "popup-fes-period-reward-ok"
PREFIX = "SC.Popup.FesPeriodReward."


class FesPeriodRewardContractTests(unittest.TestCase):
    def test_only_left_ok_is_bound_and_pipeline_input_is_disabled_by_default(self):
        action = pa.default_table().get(ACTION)
        nodes = pipeline_nodes()
        self.assertEqual((action.family, action.consuming, action.rect, action.nodes),
                         ("popup", False, (470, 627, 65, 18), (PREFIX + "Close",)))
        close = nodes[PREFIX + "Close"]
        self.assertIs(close["enabled"], False)
        # Common contract (test_common_pipeline): a click node repeats its guard's evidence literally (>= 3 parts).
        self.assertEqual(close["all_of"], nodes[PREFIX + "Guard"]["all_of"])
        self.assertEqual(close["target"], list(action.rect))
        self.assertEqual(close["on_error"], ["SC.Common.FailTask"])
        self.assertEqual(nodes[PREFIX + "Guard"]["all_of"],
                         [PREFIX + suffix for suffix in ("Title", "Body", "Ok", "FesTitle")])
        self.assertIn(PREFIX + "Guard", nodes["SC.Popup.CloseKnown"]["any_of"])
        self.assertIn(PREFIX + "Close", nodes["SC.Popup.CloseKnown"]["next"])
        self.assertEqual(nodes[PREFIX + "Settled"]["any_of"], ["SC.Nav.FesMain.Guard", "SC.Loading.Present"])
        self.assertFalse(any("shop" in a.id and PREFIX + "Close" in a.nodes for a in pa.default_table().actions))


@unittest.skipUnless(private_ready() and FRAME.is_file() and FES_MAIN.is_file(), "需要本地赛季奖励截图与 Maa 资源")
class FesPeriodRewardReplayTests(unittest.TestCase):
    def replay(self, first, after=(), task_timeout_s=12):
        # Permit this new action only inside the simulated replay while its initial table entry is disabled.
        table = pa.default_table()
        staged = dataclasses.replace(table, actions=tuple(dataclasses.replace(a, enabled=True) if a.id == ACTION else a
                                                         for a in table.actions))
        with mock.patch.object(pa, "default_table", return_value=staged):
            return run_tasks.run({"tasks": ["Startup"], "task_timeout_s": task_timeout_s},
                                 offline_steps=[(p, "click") for p in (first, *after)], simulate_input=True,
                                 policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False)

    def test_observed_reward_closes_left_ok_and_returns_from_fes(self):
        result = self.replay(FRAME, (FES_MAIN, ROOT / HOME))
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], [ACTION, "nav-back"])
        self.assertIn(PREFIX + "Settled", task["node_trace"])
        self.assertTrue(pa.default_table().get(ACTION).contains(task["simulated_clicks"][0]))
        self.assertEqual(result["real_input_attempts"], 0)

    def test_missing_reward_body_or_left_ok_never_clicks(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as directory:
            for suffix in ("Body", "Ok"):
                with self.subTest(missing=suffix):
                    image = Image.open(FRAME).convert("RGB")
                    x, y, w, h = pipeline_nodes()[PREFIX + suffix]["roi"]
                    ImageDraw.Draw(image).rectangle((x, y, x + w, y + h), fill="white")
                    path = Path(directory) / (suffix + ".png")
                    image.save(path)
                    # An unrecognised frame fails only when GoHome's timeout (90 s since 6d8f6ea) expires; the task
                    # budget must outlast it, otherwise the run ends as a task timeout instead of the guard failure.
                    result = self.replay(path, task_timeout_s=120)
                    task, = result["tasks"]
                    self.assertEqual(task["status"], "failed")
                    self.assertEqual(task["simulated_actions"], [])
                    self.assertEqual(result["real_input_attempts"], 0)


if __name__ == "__main__":
    unittest.main()

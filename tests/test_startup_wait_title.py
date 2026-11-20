"""Startup begun before the title shows (live 2026-10-31, private native frames; skipped if absent).

Right after a host restart the first frame was neither loading nor the title; Startup.Entry fell through to
SC.Common.GoHome, which has no title step, waited out its 30 s and failed while TOUCH TO START was already showing.
SC.Startup.GoHome waits (up to 90 s) and still presses TOUCH TO START once.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.maa_probe import maa_ready
from tools import run_tasks as launcher

ROOT = Path(__file__).resolve().parent.parent
UNKNOWN = "dev_assets/private/wing_samples/2026-10-31/20261031T035530840961Z-capture.png"   # not a Startup page
TITLE = "dev_assets/private/startup-game-readonly-20261001-frames/frame-002.png"
HOME = "dev_assets/private/shop-20261002-01-home-frames/frame-001.png"


@unittest.skipUnless(maa_ready(UNKNOWN, TITLE, HOME), "需要 MaaFramework 与私有标题/首页原生帧")
class StartupWaitsForTitleTests(unittest.TestCase):
    def test_title_after_an_unknown_first_frame_is_pressed_once(self):
        summary = launcher.run({"preset": None, "tasks": ["Startup"]},
                               offline_steps=[(ROOT / UNKNOWN, 4), (ROOT / TITLE, "click"), (ROOT / HOME, "click")],
                               simulate_input=True, policy={"live_input_enabled": False}, summary_path=False,
                               save_diagnostics=False)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task.get("fail_reasons"))
        self.assertEqual(task["simulated_actions"], ["game-start"])
        self.assertIn("SC.Startup.GoHome", task["node_trace"])
        self.assertNotIn("SC.Common.GoHome", task["node_trace"][:task["node_trace"].index("SC.Startup.TouchToStart")])


if __name__ == "__main__":
    unittest.main()

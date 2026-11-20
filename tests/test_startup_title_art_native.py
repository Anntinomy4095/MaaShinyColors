"""Title screen guard is independent of the rotating title illustration (private native frames; skipped if absent).

2026-10-09: the Publisher anchor used to be a 218x58 crop that included the title art around the BANDAI NAMCO bubble,
so the new 85th Anniversary art dropped it below 0.95 and Startup failed with unknown_or_unhandled_state. The template
is now only the opaque white bubble interior.
"""
from __future__ import annotations

import unittest

from tests.maa_probe import NodeProbe, maa_ready

REVIEW = "dev_assets/private/cardscan-review-20261009/now.png"
OLD_TITLE = ("dev_assets/private/manual-capture-kept-20261002/touch-to-start-native-20261002.png",
             "dev_assets/private/startup-game-readonly-20261001-frames/frame-001.png",
             "dev_assets/private/startup-game-visible-execute-20261001-frames/frame-001.png",
             "dev_assets/private/startup-game-visible-execute-20261001-frames/frame-003.png")
LOADING_OVER_BUTTON = "dev_assets/private/startup-game-visible-execute-20261001-frames/frame-004.png"
HOME = "dev_assets/private/supervised-tasks-20261007/after_startup.png"
GUARD = "SC.Startup.StartScreen.Guard"
ANCHORS = ("SC.Startup.Anchor.TouchToStart", "SC.Startup.Anchor.Publisher")


@unittest.skipUnless(maa_ready(REVIEW, *OLD_TITLE, LOADING_OVER_BUTTON, HOME), "需要 MaaFramework 与私有标题画面原生帧")
class StartupTitleArtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = NodeProbe()

    def test_guard_hits_on_old_and_new_title_art(self):
        for frame in (REVIEW,) + OLD_TITLE:
            with self.subTest(frame=frame):
                self.assertTrue(all(self.probe.hits(frame, n) for n in ANCHORS + (GUARD,)))

    def test_guard_misses_off_the_title_screen(self):
        self.assertFalse(self.probe.hits(HOME, GUARD))
        # The Now Loading band covers the button after the tap: the guard must not hit again (max_hit 1 re-tap guard).
        self.assertFalse(self.probe.hits(LOADING_OVER_BUTTON, GUARD))


if __name__ == "__main__":
    unittest.main()

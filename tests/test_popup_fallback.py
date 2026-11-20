"""Every waiting node of a daily task ends with the shared popup fallback (2026-11-23 new-PC help dialogs)."""
import json
import unittest

from agent.core import popup_fallback as pf


class PopupFallbackTests(unittest.TestCase):
    def test_waiting_nodes_get_the_fallback_last(self):
        nodes = pf.override()
        self.assertIn("SC.Shop.Premium.MoneyTab", nodes)       # 2026-11-23: shop help after the tab tap
        self.assertIn("SC.Missions.Bulk.Read", nodes)           # 2026-11-23: mission help over the list
        for name, spec in nodes.items():
            self.assertEqual(spec["next"][-1], pf.FALLBACK, name)
            self.assertEqual(spec["next"].count(pf.FALLBACK), 1, name)
        self.assertFalse(any(n.startswith("SC.Popup.") for n in nodes))
        self.assertNotIn("SC.Shop.Main", nodes)                 # already handles popups first

    def test_endpoints_are_left_alone(self):
        self.assertFalse(pf.eligible("SC.X.Stop", {"next": []}))
        self.assertFalse(pf.eligible("SC.X.Fail", {"next": ["SC.Common.FailTask"]}))
        self.assertTrue(pf.eligible("SC.X.Wait", {"next": ["SC.X.Page", "SC.Common.FailTask"]}))

    def test_launcher_installs_it(self):
        from pathlib import Path
        source = (Path(pf.__file__).resolve().parents[2] / "tools" / "run_tasks.py").read_text(encoding="utf-8")
        self.assertLess(source.index("popup_fallback.install(resource)"), source.index("install(resource, frame_gate)"))

    def test_fallback_checks_the_avatar_first_and_closes_with_anyhelp(self):
        popups = json.loads((pf.PIPELINE / "common" / "popups.json").read_text(encoding="utf-8"))
        node = popups[pf.HELP_FALLBACK]
        self.assertEqual(node["all_of"][0], "SC.Common.Anchor.AnyHelp.Icon")   # cheap template first; fade-in frames miss
        self.assertNotIn("action", node)
        self.assertEqual(node["next"], ["SC.Popup.AnyHelp.Close", "SC.Popup.NotHandled"])

if __name__ == "__main__":
    unittest.main()

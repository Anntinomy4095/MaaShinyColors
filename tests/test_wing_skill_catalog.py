"""Live-skill catalog (2026-10-29 user: 「不用读技能说明 … 已经有S卡库了」): the tooltip name line + the team's card library."""
from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agent.core import wing_live_policy as wlp
from agent.core import wing_nav as nav
from agent.core import wing_skill_catalog as wsk


def panel(title, desc, kind="active"):
    return {"skillType": kind, "skillTitle": title, "skillDesc": desc}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        old, new = root / "snapshot-20261007-all", root / "snapshot-20261020-update"
        old.mkdir(), new.mkdir()
        (old / "s-2040060080-aa.json").write_text(json.dumps({"cardPanels": [
            panel("かわいいひと", "Vocal2.5倍アピール[興味無視]/興味0.7倍[1ターン]"),
            panel("かわいいひと＋", "Vocal3.5倍アピール[興味無視]/興味0.5倍[1ターン]"),
            panel("Vo上限+20", "Vocal上限+20", "limit_break")]}, ensure_ascii=False), encoding="utf-8")
        (old / "p-1040010090-bb.json").write_text(json.dumps({"cardPanels": [
            panel("きみと巡り行く", "Visual2.5倍アピール/全観客にVocal1倍アピール")]}, ensure_ascii=False),
            encoding="utf-8")
        (new / "p-1040010090-cc.json").write_text(json.dumps({"cardPanels": [
            panel("きみと巡り行く＋", "Vocal4倍アピール/全観客にVisual2倍アピール")]}, ensure_ascii=False),
            encoding="utf-8")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_team_library_and_panel_skills(self):
        cat = wsk.build_catalog(["2040060080", "1040010090", "9999"], [{"name": "HoPPoPらびっつ", "multiplier": 4.0,
                                                                         "attributes": ["Vo"]}], root=self.root)
        self.assertEqual(cat["かわいいひと"]["multiplier"], 2.5)
        self.assertEqual(cat["かわいいひと+"]["multiplier"], 3.5)
        self.assertNotIn("Vo上限+20", cat)
        self.assertIn("きみと巡り行く+", cat)            # the newest snapshot that has the card wins
        self.assertNotIn("きみと巡り行く", cat)
        self.assertEqual(cat["HoPPoPらびっつ"]["source"], "panel")

    def test_lookup_exact_near_and_ambiguous(self):
        cat = wsk.build_catalog(["2040060080"], root=self.root)
        self.assertEqual(wsk.lookup(cat, "かわいいひと")["multiplier"], 2.5)
        self.assertEqual(wsk.lookup(cat, "かわいいひと+")["multiplier"], 3.5)
        self.assertIsNone(wsk.lookup(cat, "かわいいひと十"))     # one slip from both versions: ambiguous
        self.assertIsNone(wsk.lookup(cat, "まったく別"))
        self.assertIsNone(wsk.lookup({}, "かわいいひと"))

    def test_tooltip_takes_the_catalog_multiplier(self):
        cat = wsk.build_catalog(["2040060080"], root=self.root)
        # wing4b: the two description lines read as garbage -> no multiplier; the name line is enough now
        tip = wlp.catalog_tooltip({"name": "かわいいひと", "multiplier": None, "attribute": None}, cat)
        self.assertEqual((tip["multiplier"], tip["attribute"]), (2.5, "Vo"))
        # a misread description (25) is replaced; a description naming the other version (plus misread) is kept
        self.assertEqual(wlp.catalog_tooltip({"name": "かわいいひと", "multiplier": 25.0}, cat)["multiplier"], 2.5)
        self.assertEqual(wlp.catalog_tooltip({"name": "かわいいひと", "multiplier": 3.5}, cat)["multiplier"], 3.5)
        unknown = {"name": "知らない", "multiplier": 1.5}
        self.assertEqual(wlp.catalog_tooltip(unknown, cat), unknown)

    def test_navigator_builds_from_the_produce_team(self):
        rt = nav.WingNavRuntime()
        rt.memory.produce = {"verified_context": {"team": {"P": "1040010090", "S": ["2040060080"], "guest": None}}}
        with unittest.mock.patch.object(wsk, "SNAPSHOTS", self.root):
            cat = rt._skill_catalog()
        self.assertEqual(sorted(cat), ["かわいいひと", "かわいいひと+", "きみと巡り行く+"])
        self.assertIs(rt._skill_catalog(), cat)                  # cached until the team or panel skills change


if __name__ == "__main__":
    unittest.main()

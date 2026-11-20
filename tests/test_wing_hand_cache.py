"""Hand-card portrait cache (2026-10-29 user: 按头像认手牌，免去查看点击；设置 → 存储「手牌识别」可清理)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.core import wing_hand_cache as whc
from agent.core import wing_live_policy as wlp
from agent.core import wing_nav as nav

CATALOG = {"かわいいひと": {"name": "かわいいひと", "multiplier": 2.5, "attributes": ["Vo"], "source": "library:s"},
           "かわいいひと+": {"name": "かわいいひと+", "multiplier": 3.5, "attributes": ["Vo"], "source": "library:s"},
           "GAP": {"name": "GAP", "multiplier": 2.5, "attributes": ["Vo"], "source": "library:t"}}


def sig(v):
    return [v] * 72


class CacheFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wing-cache" / "hand-cards.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_resolve_and_match(self):
        self.assertTrue(whc.record({"sig": sig(40), "attribute": "Vo", "name": "かわいいひと", "multiplier": 2.5},
                                   CATALOG, self.path, today="2026-10-29"))
        self.assertFalse(whc.record({"sig": sig(41), "attribute": "Vo", "name": "かわいいひと", "multiplier": 2.5},
                                    CATALOG, self.path, today="2026-10-29"))      # same card, same day: no write
        portraits = whc.resolve(whc.load(self.path), CATALOG)
        self.assertEqual(whc.match(portraits, sig(43), "Vo")["multiplier"], 2.5)
        self.assertIsNone(whc.match(portraits, sig(43), "Da"))                     # attribute icon must agree too
        self.assertIsNone(whc.match(portraits, sig(90), "Vo"))
        self.assertEqual(whc.resolve(whc.load(self.path), {}), [])                 # another team: not used

    def test_misread_name_is_canonical_and_unknown_is_not_saved(self):
        whc.record({"sig": sig(40), "attribute": "Vo", "name": "GAR", "multiplier": None}, CATALOG, self.path)
        self.assertEqual([e["name"] for e in whc.load(self.path)], ["GAP"])
        self.assertFalse(whc.record({"sig": sig(60), "attribute": "Vo", "name": "知らない", "multiplier": 2.0},
                                    CATALOG, self.path))
        self.assertEqual(len(whc.load(self.path)), 1)

    def test_an_unsaved_name_on_a_known_portrait_makes_it_ambiguous(self):
        # the P idol's portrait: first read as the generic appeal, later as an S-card skill missing from the catalog
        whc.record({"sig": sig(40), "attribute": "Vo", "name": "ボーカルアピール", "multiplier": 1.0}, CATALOG, self.path)
        self.assertTrue(whc.record({"sig": sig(41), "attribute": "Vo", "name": "murmurmaid", "multiplier": 2.8},
                                   CATALOG, self.path))
        self.assertEqual([e["name"] for e in whc.load(self.path)], ["ボーカルアピール"])
        self.assertEqual(whc.resolve(whc.load(self.path), CATALOG), [])

    def test_look_alikes_with_two_names_are_ambiguous(self):
        # base and ＋ share the portrait: both are inspected again instead of guessing
        whc.record({"sig": sig(40), "attribute": "Vo", "name": "かわいいひと"}, CATALOG, self.path, today="2026-10-29")
        whc.record({"sig": sig(42), "attribute": "Vo", "name": "かわいいひと+"}, CATALOG, self.path, today="2026-10-29")
        self.assertTrue(all(e.get("ambiguous") for e in whc.load(self.path)))
        self.assertEqual(whc.resolve(whc.load(self.path), CATALOG), [])

    def test_generic_starting_appeal_keeps_its_read_multiplier(self):
        whc.record({"sig": sig(20), "attribute": "Da", "name": "ダンスアピール", "multiplier": 1.0}, CATALOG, self.path)
        self.assertEqual(whc.match(whc.resolve(whc.load(self.path), CATALOG), sig(20), "Da")["multiplier"], 1.0)

    def test_support_portrait_appeals_are_kept_and_the_p_portrait_stays_ambiguous(self):
        # 2026-10-29 wing6: one P-idol picture named GAP+ / かわいいひと, a support idol's picture always V+++++ 3.5x
        whc.record({"sig": sig(40), "attribute": "Vo", "name": "GAP", "multiplier": 2.5}, CATALOG, self.path)
        whc.record({"sig": sig(41), "attribute": "Vo", "name": "かわいいひと", "multiplier": 2.5}, CATALOG, self.path)
        whc.record({"sig": sig(90), "attribute": "Vo", "name": "ボーカルアピールV+++++", "multiplier": None},
                   CATALOG, self.path, today="2026-10-29")
        self.assertEqual(whc.resolve(whc.load(self.path), CATALOG), [])          # no read multiplier yet
        self.assertTrue(whc.record({"sig": sig(91), "attribute": "Vo", "name": "ボーカルアピールV+++++",
                                    "multiplier": 3.5}, CATALOG, self.path, today="2026-10-29"))
        portraits = whc.resolve(whc.load(self.path), CATALOG)
        self.assertEqual([e["name"] for e in portraits], ["ボーカルアピールV+++++"])
        self.assertEqual(whc.match(portraits, sig(92), "Vo")["multiplier"], 3.5)
        self.assertIsNone(whc.match(portraits, sig(40), "Vo"))
        for name in ("ボーカルアピールVI+++++", "ダンスアピールIV", "ビジュアルアピールVII"):
            self.assertEqual(whc.canonical(name, CATALOG), name)
        self.assertIsNone(whc.canonical("エクセレントアピールVナ+", CATALOG))     # misread: not saved

    def test_unreadable_file_is_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{", encoding="utf-8")
        self.assertEqual(whc.load(self.path), [])


def readings(phase="select_card", selected=None, tooltip=None, sigs=(sig(40), sig(20), sig(80))):
    judges = [{"attribute": a, "trend_rank": t, "left": False} for a, t in (("Vo", 1), ("Da", 2), ("Vi", 3))]
    return {"live_phase": phase, "selected_card": selected, "tooltip": tooltip, "hand_sigs": list(sigs),
            "live_state": {"turn": 1, "judges": judges,
                           "options": [{"id": f"card{i}", "attribute": "Vo"} for i in (1, 2, 3)]}}


class LivePlanTests(unittest.TestCase):
    def test_known_portraits_skip_the_inspect_tap(self):
        memory = SimpleNamespace(live_step=None, live_cards={})
        plain = wlp.plan_turn(readings(), memory, primary="Vo", estimate=None, stats=None)
        self.assertEqual(plain["reason"], "inspect_unknown_card")
        portraits = [{"sig": sig(40), "attribute": "Vo", "name": "かわいいひと", "multiplier": 2.5},
                     {"sig": sig(20), "attribute": "Vo", "name": "GAP", "multiplier": 3.5},
                     {"sig": sig(80), "attribute": "Vo", "name": "ボーカルアピールI", "multiplier": 1.0}]
        known = wlp.plan_turn(readings(), memory, primary="Vo", estimate=None, stats=None, portraits=portraits)
        self.assertEqual((known["action"], known["slot"], known["reason"]), ("card", 2, "best_known_card"))

    def test_portrait_record_needs_the_lowered_portrait_and_a_name(self):
        memory = SimpleNamespace(live_step={"step": "card", "slot": 1, "sigs": [sig(40), sig(20), sig(80)]},
                                 live_cards={})
        tip = {"name": "かわいいひと", "multiplier": None, "attribute": None}
        got = wlp.portrait_record(readings("select_judge", 1, tip), memory, CATALOG)
        self.assertEqual((got["sig"], got["attribute"], got["name"], got["multiplier"]), (sig(40), "Vo", "かわいいひと", 2.5))
        self.assertIsNone(wlp.portrait_record(readings("select_judge", 1, {"name": None}), memory, CATALOG))
        self.assertIsNone(wlp.portrait_record(readings("select_card", 1, tip), memory, CATALOG))


LOGS = Path(__file__).resolve().parents[1] / "dev_assets" / "private" / "wing-ui" / "decision-log"


@unittest.skipUnless(LOGS.is_dir() and any(LOGS.glob("2026102*.jsonl")), "private W.I.N.G. decision logs not present")
class DecisionLogReplayTests(unittest.TestCase):
    def test_cache_built_over_past_produces_never_names_a_card_wrong(self):
        """Every tooltip read in the private decision logs, in order: the cache's guess before the read must never be
        another name (2026-10-29 replay of 19 Produces: 0 wrong, 128 right, ~46 % of hand cards known up front)."""
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path, right, wrong = Path(tmp) / "hand-cards.json", 0, []
            for log in sorted(LOGS.glob("2026102*.jsonl")):
                today, sigs = f"{log.name[:4]}-{log.name[4:6]}-{log.name[6:8]}", None
                for line in log.read_text(encoding="utf-8").splitlines():
                    e = json.loads(line)
                    r = e.get("readings") or {}
                    if e.get("event") != "decide" or e.get("screen") != "Live":
                        continue
                    if r.get("live_phase") == "select_card":
                        sigs = r.get("hand_sigs")
                        continue
                    sel, tip = r.get("selected_card"), r.get("tooltip") or {}
                    if r.get("live_phase") != "select_judge" or not sigs or sel not in (1, 2, 3) or not sigs[sel - 1]                             or not tip.get("name"):
                        continue
                    attr = {o["id"]: o["attribute"] for o in r["live_state"]["options"]}.get(f"card{sel}")
                    shared = sel - 1 in wlp.shared_portraits(sigs)
                    got = None if shared else whc.match(whc.resolve(whc.load(path), {}), sigs[sel - 1], attr)
                    if got is not None:
                        if got["name"] == whc.canonical(tip["name"], {}):
                            right += 1
                        else:
                            wrong.append((log.name, got["name"], tip["name"]))
                    whc.record({"sig": sigs[sel - 1], "attribute": attr, "name": tip["name"],
                                "multiplier": tip.get("multiplier"), "shared": shared}, {}, path, today=today)
        self.assertEqual(wrong, [])
        self.assertGreater(right, 0)


class NavigatorTests(unittest.TestCase):
    def test_no_cache_file_unless_a_live_run_sets_it(self):
        rt = nav.WingNavRuntime()
        self.assertIsNone(rt.hand_cache_path)
        self.assertEqual(rt._hand_portraits(CATALOG), [])
        with tempfile.TemporaryDirectory() as tmp:
            rt.hand_cache_path = Path(tmp) / "hand-cards.json"
            rt.memory.live_step = {"step": "card", "slot": 1, "sigs": [sig(40), sig(20), sig(80)]}
            rt._catalog_key = ("team",)
            rt._save_portrait(readings("select_judge", 1, {"name": "かわいいひと", "multiplier": 2.5}), CATALOG)
            self.assertEqual(rt._hand_portraits(CATALOG)[0]["name"], "かわいいひと")


if __name__ == "__main__":
    unittest.main()

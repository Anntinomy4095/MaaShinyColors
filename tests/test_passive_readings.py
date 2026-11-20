"""Passive readings: the private store, report -> reading extraction, the training-list
owned-level observer and the pipeline contract. Offline; native-frame tests skip when the private frames/Maa are absent."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from agent.core import reading_store as rs
from agent.core import report_readings as rr
from agent.core import resource_observer as ro
from agent.core.task_actions import ReportSink
from tests.test_resource_observer import PIPELINE, ROOT, Clock, FakeVision, _maa_ready, frame
from tools.check_project import load_jsonc

T0 = dt.datetime(2026, 10, 10, 3, 0, tzinfo=dt.timezone.utc)


def stamp(hours=0.0):
    return (T0 + dt.timedelta(hours=hours)).isoformat()


class StoreTests(unittest.TestCase):
    def test_present_reading_uses_the_observed_after_page(self):
        for left in (0, 12):
            with self.subTest(left=left):
                report = {"outcome": "received", "page": {"tab": "unclaimed", "count": 112, "rows": []},
                          "page_after": {"tab": "unclaimed", "count": left, "rows": []}}
                key, value, _ = rr.present(report)[0]
                self.assertEqual((key, value["count"]), ("present.box", left))
        self.assertEqual(rr.present({"page": {"tab": "unclaimed", "count": 2, "rows": []}})[0][1]["count"], 2)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.now = [T0]
        self.path = self.dir / "readings.json"
        self.store = rs.ReadingStore(self.path, now=lambda: self.now[0], flush_interval_s=0.0)

    def test_latest_wins_older_is_refused_and_history_is_bounded(self):
        self.assertTrue(self.store.record("resource.money", {"count": 5}, source="a", observed_at=stamp(1)))
        self.assertFalse(self.store.record("resource.money", {"count": 4}, source="b", observed_at=stamp(0)))
        self.assertTrue(self.store.record("resource.money", {"count": 6}, source="c", observed_at=stamp(2), confidence_pm=900))
        item = self.store.latest("resource.money")
        self.assertEqual((item["value"], item["source"], item["confidence_pm"]), ({"count": 6}, "c", 900))
        self.assertEqual([h["value"]["count"] for h in item["history"]], [5])
        for n in range(30):
            self.store.record("resource.money", {"count": n}, source="x", observed_at=stamp(3 + n))
        self.assertLessEqual(len(self.store.latest("resource.money")["history"]), rs.HISTORY)

    def test_invalid_input_is_refused_not_guessed(self):
        bad = [("Bad Key", {"count": 1}, "s", None), ("resource.x", [1], "s", None), ("resource.x", {"count": float("nan")}, "s", None),
               ("resource.x", {"count": 1}, "", None), ("resource.x", {"count": 1}, "s", 1001),
               ("resource.x", {"blob": "x" * 9000}, "s", None)]
        for key, value, source, conf in bad:
            self.assertFalse(self.store.record(key, value, source=source, confidence_pm=conf), key)
        self.assertFalse(self.store.record("resource.x", {"count": 1}, source="s", observed_at="not-a-date"))
        self.assertEqual(self.store.readings, {})

    def test_persists_across_instances_and_survives_a_corrupt_file(self):
        self.store.record("resource.stamina", {"count": 280, "max": 300}, source="p", observed_at=stamp())
        self.store.record_card("2050070010", limit_break=0, level=66, confidence=0.97, source="p", observed_at=stamp())
        again = rs.ReadingStore(self.path, now=lambda: self.now[0])
        self.assertEqual(again.latest("resource.stamina")["value"], {"count": 280, "max": 300})
        self.assertEqual(again.cards["2050070010"]["level"], 66)
        self.path.write_text("{not json", encoding="utf-8")
        broken = rs.ReadingStore(self.path, now=lambda: self.now[0])
        self.assertEqual((broken.readings, broken.cards), ({}, {}))
        self.assertEqual(broken.errors, 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")   # never replaced by load

    def test_stale_flag_by_age_and_per_key_window(self):
        self.store.record("resource.stamina", {"count": 1}, source="p", observed_at=stamp())
        self.store.record("present.box", {"rows": []}, source="p", observed_at=stamp())
        self.store.record("knowhow.scan", {"outcome": "x"}, source="p", observed_at=stamp())
        fresh = self.store.snapshot(T0 + dt.timedelta(hours=1))["readings"]
        self.assertFalse(any(v["stale"] for v in fresh.values()))
        later = self.store.snapshot(T0 + dt.timedelta(hours=7))["readings"]
        self.assertEqual({k: v["stale"] for k, v in later.items()},
                         {"knowhow.scan": False, "present.box": False, "resource.stamina": True})
        day3 = self.store.snapshot(T0 + dt.timedelta(days=3, hours=1))["readings"]
        self.assertTrue(all(v["stale"] for v in day3.values()))
        self.assertEqual(day3["resource.stamina"]["age_s"], 3 * 86400 + 3600)

    def test_manual_card_values_are_never_overwritten_and_auto_accept_follows_confidence(self):
        self.assertTrue(self.store.record_card("2040010020", limit_break=4, level=80, confidence=1.0, source="manual", observed_at=stamp()))
        self.assertFalse(self.store.record_card("2040010020", limit_break=3, level=70, confidence=0.99, source="p", observed_at=stamp(5)))
        self.assertEqual(self.store.cards["2040010020"]["level"], 80)
        self.assertTrue(self.store.record_card("2040010030", limit_break=4, level=60, confidence=0.60, source="p", observed_at=stamp()))
        self.assertTrue(self.store.record_card("2040010010", limit_break=0, level=1, confidence=0.956, source="p", observed_at=stamp()))
        # a clearly worse, newer reading never replaces a good one; a better one does
        self.assertFalse(self.store.record_card("2040010010", limit_break=1, level=9, confidence=0.55, source="p", observed_at=stamp(1)))
        self.assertTrue(self.store.record_card("2040010010", limit_break=0, level=2, confidence=0.90, source="p", observed_at=stamp(2)))
        self.assertTrue(self.store.record_card("2040010030", limit_break=4, level=61, confidence=0.80, source="p", observed_at=stamp(1)))
        snap = self.store.snapshot(T0)["owned_levels"]
        self.assertEqual(snap["details"], {"2040010010": {"limit_break": 0, "level": 2}, "2040010020": {"limit_break": 4, "level": 80},
                                           "2040010030": {"limit_break": 4, "level": 61}})
        self.assertTrue(snap["cards"]["2040010010"]["auto_accept"])
        low = self.store.record_card("2050070010", limit_break=0, level=9, confidence=0.55, source="p", observed_at=stamp())
        self.assertTrue(low)
        self.assertFalse(self.store.snapshot(T0)["owned_levels"]["cards"]["2050070010"]["auto_accept"])
        self.assertNotIn("2050070010", self.store.snapshot(T0)["owned_levels"]["details"])
        for kwargs in ({"limit_break": 5}, {"limit_break": 1, "level": 0}, {"limit_break": 1, "confidence": 1.5}):
            args = {"limit_break": 1, "level": 3, "confidence": 0.9, **kwargs}
            self.assertFalse(self.store.record_card("2040010010", source="p", **args))
        self.assertFalse(self.store.record_card("abc", limit_break=1, confidence=0.9, source="p"))

    def test_export_module_and_throttled_flush(self):
        export = self.dir / "passive-readings.js"
        clock = Clock()
        store = rs.ReadingStore(self.dir / "s.json", export_path=export, now=lambda: T0, clock=clock, flush_interval_s=5.0)
        store.record("resource.money", {"count": 1}, source="a", observed_at=stamp())
        self.assertTrue(export.is_file())
        first = export.read_text(encoding="utf-8")
        store.record("resource.money", {"count": 2}, source="a", observed_at=stamp(1))
        self.assertEqual(export.read_text(encoding="utf-8"), first)          # throttled
        self.assertTrue(store.flush(force=True))
        text = export.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// Generated by") and "export default" in text)
        data = json.loads(text[text.index("export default") + 15:].rsplit(";", 1)[0])
        self.assertEqual(data["readings"]["resource.money"]["value"], {"count": 2})
        self.assertEqual(data["readings"]["resource.money"]["source"], "a")
        self.assertFalse((self.dir / "s.json.tmp").exists())

    def test_write_failure_never_raises(self):
        store = rs.ReadingStore(self.dir / "blocked" / "x.json", flush_interval_s=0.0)
        (self.dir / "blocked").write_text("a file, not a directory", encoding="utf-8")
        self.assertTrue(store.record("resource.money", {"count": 1}, source="a"))
        self.assertGreaterEqual(store.errors, 1)
        self.assertEqual(store.latest("resource.money")["value"], {"count": 1})


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.store = rs.ReadingStore(self.dir / "r.json", now=lambda: T0, flush_interval_s=0.0)
        self.tap = rr.ReportTap(self.store)

    def keys(self):
        return {k: v["value"] for k, v in self.store.readings.items()}

    def test_shop_report_money_and_offers(self):
        self.tap({"kind": "shop", "outcome": "bought", "bought_ranks": [2, 1], "decisions": [
            {"decision": {"kind": "open", "plan": {"money": 17709869}}, "visible": [["スキップチケット", 10], ["ゴールドトレーニングチケット", 10]],
             "money": 17709869, "layout": True},
            {"money_before": 17709869, "money_after": 17709859}]})
        keys = self.keys()
        self.assertEqual(keys["resource.money"], {"count": 17709859})
        self.assertEqual(keys["shop.offers"]["bought_ranks"], [1, 2])
        self.assertEqual(keys["shop.offers"]["visible"][0], ["スキップチケット", 10])
        self.assertEqual(self.store.latest("resource.money")["source"], "task_report:shop")

    def test_training_ticket_count_is_the_last_ocr_inventory(self):
        self.tap({"kind": "training", "outcome": "trained", "decisions": [
            {"stage": "Zero", "ocr": {"inventory": "22673", "level": "4"}}, {"stage": "Confirm", "ocr": {"level": "4"}},
            {"stage": "After", "ocr": {"inventory": "22672", "level": "5"}}]})
        self.assertEqual(self.keys(), {"resource.bronze_ticket": {"count": 22672}})
        self.tap({"kind": "training", "decisions": [{"ocr": {"inventory": "x1"}}]})
        self.assertEqual(self.keys()["resource.bronze_ticket"]["count"], 22672)

    def test_match_and_grade_rights_and_match_main(self):
        match = {"kind": "match", "decisions": [{"stage": "MatchMain", "rights": "20/20"}, {"main_observation": {
            "rights": [19, 20], "ranking_deadline": {"raw": "x", "month": 10, "day": 30, "hour": 13, "minute": 59},
            "areas": [{"row": 1, "area_id": 1, "difficulty": "Normal", "achievement": 100, "header_raw": "エリア1Normal"}]}}]}
        self.tap(match)
        keys = self.keys()
        self.assertEqual(keys["resource.fes_rights"], {"count": 19, "max": 20})
        self.assertEqual(keys["match.main"]["areas_visible"], [{"area_id": 1, "difficulty": "Normal", "achievement": 100}])
        self.assertEqual(keys["match.main"]["ranking_deadline"], {"month": 10, "day": 30, "hour": 13, "minute": 59})
        self.assertFalse(keys["match.main"]["stage_list_complete"])
        self.assertNotIn("raw", json.dumps(keys["match.main"]))
        self.tap({"kind": "grade", "decisions": [{"rights_before": [20, 20]}, {"rights_after": "19/20"}]})
        self.assertEqual(self.store.latest("resource.fes_rights")["source"], "task_report:grade")
        self.tap({"kind": "grade", "decisions": [{"rights": "99/20"}]})
        self.assertEqual(self.keys()["resource.fes_rights"], {"count": 19, "max": 20})   # impossible pair: kept old

    def test_mission_status_only_when_ok(self):
        report = {"kind": "mission_status", "page": "mission_daily", "counter": [1, 5], "ok": True,
                  "rows": [{"title": "ゲームにログインしよう", "progress": [1, 1], "status": "claimable"},
                           {"title": "x", "progress": None, "status": "unknown"}],
                  "claimable": ["a"], "claimed": [], "incomplete": [], "unknown": ["x"]}
        self.tap({**report, "ok": False})
        self.assertEqual(self.keys(), {})
        self.tap(report)
        value = self.keys()["missions.mission_daily"]
        self.assertEqual((value["counter"], value["claimable"], value["unknown"]), ([1, 5], 1, 1))
        self.assertTrue(value["rows_visible_only"])

    def test_home_gacha_hint_only_definite_states(self):
        base = {"kind": "resources", "ok": True, "source": "passive_observer", "observed_at": stamp(), "items": {}}
        self.tap({**base, "gacha": {"state": "free", "kind": "free_daily", "fill_pm": 760}})
        self.assertEqual(self.keys()["gacha.home"], {"state": "free", "kind": "free_daily"})
        for bad in ({"state": "unread", "kind": None}, {"state": "free", "kind": None}, {"state": "no_tag", "kind": "free"},
                    {"state": "free", "kind": "paid"}, "free"):
            self.assertIsNone(rr.gacha_hint(bad), bad)
        self.assertEqual(rr.gacha_hint({"state": "no_tag", "kind": None}), {"state": "no_tag", "kind": None})
        self.assertEqual(rs.stale_after("gacha.home"), rs.DAY)

    def test_present_rows_resources_and_wing_hub(self):
        self.tap({"kind": "present", "outcome": "box_empty", "page": {"tab": "unclaimed", "count": 2, "rows": [
            {"name": "マニー", "quantity": 50000, "kind": "money", "button": "受け取る", "deadline": None, "y": 100}]}})
        self.assertEqual(self.keys()["present.box"], {"tab": "unclaimed", "count": 2, "outcome": "box_empty",
                                                      "rows": [{"name": "マニー", "quantity": 50000, "kind": "money"}]})
        self.tap({"kind": "resources", "ok": True, "source": "passive_observer", "observed_at": stamp(), "items": {
            "money": {"count": 17678700, "confidence_pm": 1000}, "jewel": {"count": 48157, "confidence_pm": 1000, "indicator_only": True},
            "stamina": {"count": 300, "max": 300, "confidence_pm": 1000}, "fes": {"count": 20, "max": 20, "plus": 0, "confidence_pm": 1000}}})
        keys = self.keys()
        self.assertEqual(keys["resource.fes_rights"], {"count": 20, "max": 20, "plus": 0})
        self.assertTrue(keys["resource.jewel"]["indicator_only"])
        self.assertEqual(self.store.latest("resource.money")["source"], "passive_observer")
        self.tap({"kind": "wing", "log": [{"event": "decide", "screen": "Hub", "readings": {
            "season": 1, "weeks_left": 4, "fans_left": 615, "stamina_pct": 95, "hub_speaker": "灯織", "hub_cheer": None}},
            {"event": "decide", "screen": "UnitSelect", "readings": {"unit_index": 17, "unit_total": 20}}]})
        self.assertEqual(self.keys()["wing.hub"], {"season": 1, "weeks_left": 4, "fans_left": 615, "stamina_pct": 95,
                                                   "hub_speaker": "灯織", "unit_index": 17, "unit_total": 20})

    def test_owned_levels_report_feeds_cards_and_unknown_kinds_are_ignored(self):
        self.tap({"kind": "owned_levels", "ok": True, "source": "passive_observer:training_list", "observed_at": stamp(),
                  "cards": {"2050070010": {"limit_break": 0, "level": 66, "confidence_pm": 973}, "x": {}, "2040010020": {"limit_break": 4}}})
        self.assertEqual(self.store.cards["2050070010"]["level"], 66)
        self.assertEqual(self.store.cards["2050070010"]["confidence"], 0.973)
        self.assertEqual(len(self.store.cards), 1)
        before = self.store.accepted
        for kind in ("fail", "diagnostics", "card_scan", "mystery"):
            self.tap({"kind": kind, "ok": True})
        self.assertEqual(self.store.accepted, before)
        self.tap({"kind": "shop", "decisions": "garbage", "bought_ranks": None})   # malformed: no raise
        self.assertEqual(self.tap.errors, 0)

    def test_sink_listener_failure_never_affects_the_task_report(self):
        sink = ReportSink()
        sink.add_listener(lambda report: 1 / 0)
        seen = []
        sink.add_listener(seen.append)
        sink.add("shop", {"outcome": "x"})
        self.assertEqual([r["kind"] for r in sink.drain()], ["shop"])
        self.assertEqual(len(seen), 1)


class InstallWiringTests(unittest.TestCase):
    def test_install_persists_reports_and_store_none_disables_it(self):
        from tests.test_resource_observer import ObserverRuleTests  # noqa: F401  (module import keeps fixtures shared)

        class Resource:
            def register_custom_recognition(self, name, rec):
                return True

            def override_pipeline(self, nodes):
                self.nodes = nodes
                return True

        with tempfile.TemporaryDirectory() as tmp:
            store = rs.ReadingStore(Path(tmp) / "r.json", flush_interval_s=0.0)
            sink = ReportSink()
            res = Resource()
            observer = ro.install(res, sink, store=store)
            self.assertEqual(set(res.nodes), set(ro.NODES))
            sink.add("training", {"decisions": [{"ocr": {"inventory": "5"}}]})
            self.assertEqual(store.latest("resource.bronze_ticket")["value"], {"count": 5})
            self.assertIs(observer.store, store)
            plain = ReportSink()
            ro.install(Resource(), plain, store=None)
            self.assertEqual(plain._listeners, [])

    def test_default_store_is_opt_in_through_the_environment(self):
        class Resource:
            def register_custom_recognition(self, name, rec):
                return True

            def override_pipeline(self, nodes):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            env = {"SC_READINGS_DIR": tmp}
            with mock.patch.dict("os.environ", env, clear=False):
                __import__("os").environ.pop("SC_READINGS_PERSIST", None)
                self.assertIsNone(ro.install(Resource(), ReportSink()).store)                 # tests/imports: no persistence
                self.assertIsNone(ro.install(Resource(), ReportSink(), store=None).store)
            with mock.patch.dict("os.environ", {**env, "SC_READINGS_PERSIST": "1"}):
                sink = ReportSink()
                observer = ro.install(Resource(), sink)
                self.assertEqual(observer.store.path, Path(tmp) / "readings.json")
                sink.add("training", {"decisions": [{"ocr": {"inventory": "7"}}]})
                self.assertEqual(json.loads((Path(tmp) / "readings.json").read_text(encoding="utf-8"))
                                 ["readings"]["resource.bronze_ticket"]["value"], {"count": 7})
        source = (ROOT / "tools/run_tasks.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.setdefault("SC_READINGS_PERSIST", "1")', source.split('if __name__ == "__main__":')[1])
        self.assertNotIn("SC_READINGS_PERSIST", source.split('if __name__ == "__main__":')[0])

    def test_default_store_honours_the_redirect_directory(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"SC_READINGS_DIR": tmp}):
            store = rs.ReadingStore.default()
            self.assertEqual(store.path, Path(tmp) / "readings.json")
            self.assertEqual(store.export_path, Path(tmp) / "passive-readings.js")


class TrainingListObserverTests(unittest.TestCase):
    """Rules of the training_list layout with a fake vision and a fake card index (no Maa, no private data)."""

    RECORDS = [
        {"status": "accepted", "card_id": "2050070010", "limit_break": 0, "level": 66, "confidence": 0.973},
        {"status": "accepted", "card_id": "2040010020", "limit_break": 4, "level": 80, "confidence": 0.934},
        {"status": "accepted", "card_id": "2040010030", "limit_break": 4, "level": 60, "confidence": 0.40},   # below floor
        {"status": "rejected", "card_id": None, "reasons": ["tile:no_match"]},
    ]

    class V(FakeVision):
        def __init__(self, guard=True, popup=False):
            super().__init__()
            self.guard, self.popup = guard, popup

        def hit(self, node, image):
            return {ro.TRAINING_GUARD: self.guard, "SC.Popup.TrainingHelp.Guard": self.popup}[node]

    def make(self, vision=None, **options):
        self.sink, self.clock = ReportSink(), Clock()
        obs = ro.ResourceObserver(self.sink, clock=self.clock, now=lambda: T0, vision=vision or self.V(), **options)
        obs.index, obs._index_state = object(), "ready"
        return obs

    def observe(self, obs, image=None):
        with mock.patch("agent.core.owned_levels.read_frame", return_value=self.RECORDS) as read:
            result = obs.observe(None, frame() if image is None else image, "training_list")
        return result, read

    def test_emits_confident_tiles_only_and_hits_after_a_read(self):
        obs = self.make()
        result, read = self.observe(obs)
        self.assertIs(result, True)          # 2026-11-19: a read hits so the task decides on a fresh frame
        read.assert_called_once()
        report, = self.sink.drain()
        self.assertEqual(report["kind"], "owned_levels")
        self.assertEqual(report["source"], "passive_observer:training_list")
        self.assertEqual(report["observed_at"], T0.isoformat())
        self.assertEqual(report["cards"], {"2050070010": {"limit_break": 0, "level": 66, "confidence_pm": 973},
                                           "2040010020": {"limit_break": 4, "level": 80, "confidence_pm": 934}})
        self.assertEqual((report["tiles"], report["rejected_tiles"]), (4, 2))
        self.assertEqual(obs.summary()["training_list"]["emitted"], 1)

    def test_throttles_guard_misses_overlay_canvas_and_unchanged(self):
        obs = self.make(self.V(guard=False))
        for _ in range(3):
            self.observe(obs)
        self.assertEqual(obs.refusals, {"not_training_list": 1})                    # misses throttled to 1.5 s
        self.clock.t += 2
        self.observe(obs)
        self.assertEqual(obs.refusals, {"not_training_list": 2})
        self.assertEqual(self.sink.drain(), [])
        obs = self.make(self.V(popup=True))
        self.observe(obs)
        self.assertEqual(obs.refusals, {"list_overlay": 1})
        obs = self.make()
        self.observe(obs, np.zeros((600, 800, 3), dtype=np.uint8))
        self.assertEqual(obs.refusals, {"frame_not_1280x720": 1})
        obs = self.make()
        self.observe(obs)
        self.clock.t += 5          # inside the 15 s read interval: nothing happens
        _, read = self.observe(obs)
        read.assert_not_called()
        self.clock.t += 11
        self.observe(obs)          # due again, same tiles within refresh_s: suppressed
        self.assertEqual(len(self.sink.drain()), 1)
        self.assertEqual(obs.refusals["unchanged"], 1)

    def test_read_cap_slow_read_and_errors_disable_only_this_layout(self):
        obs = self.make(list_max_reads=2)
        for _ in range(4):
            self.clock.t += 20
            self.observe(obs)
        self.assertEqual(obs.list["reads"], 2)
        self.assertEqual(obs.list["disabled"], "read_cap_reached")
        obs = self.make()
        with mock.patch("agent.core.owned_levels.read_frame", side_effect=lambda *a, **k: setattr(self.clock, "t", self.clock.t + 9) or self.RECORDS):
            obs.observe(None, frame(), "training_list")
        self.assertEqual(obs.list["disabled"], "read_too_slow")
        self.assertIsNone(obs.disabled)                      # home layout unaffected
        obs = self.make(max_errors=1)
        with mock.patch("agent.core.owned_levels.read_frame", side_effect=RuntimeError("boom")):
            self.assertIs(obs.observe(None, frame(), "training_list"), True)   # the attempt took time: fresh frame
        self.assertEqual((obs.errors, obs.list["disabled"]), (1, "reader_errors"))
        self.assertIsNone(obs.observe(None, frame(), "training_list"))          # disabled: no read, no hit

    def test_index_loads_in_the_background_and_the_frame_is_skipped(self):
        obs = self.make()
        obs.index, obs._index_state = None, None
        started = []
        with mock.patch("agent.core.resource_observer.threading.Thread") as thread:
            thread.side_effect = lambda **kw: started.append(kw) or mock.Mock()
            self.observe(obs)
        self.assertEqual(obs._index_state, "loading")
        self.assertEqual(obs.refusals, {"index_loading": 1})
        self.assertEqual(len(started), 1)
        self.assertEqual(self.sink.drain(), [])
        obs._index_state = "failed"
        self.clock.t += 2
        self.observe(obs)
        self.assertEqual(obs.list["disabled"], "index_unavailable")

    def test_end_to_end_into_the_store_never_overwrites_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = rs.ReadingStore(Path(tmp) / "r.json", now=lambda: T0, flush_interval_s=0.0)
            store.record_card("2040010020", limit_break=3, level=70, confidence=1.0, source="manual", observed_at=stamp(-1))
            obs = self.make()
            self.sink.add_listener(rr.ReportTap(store))
            self.observe(obs)
        self.assertEqual(store.cards["2040010020"]["level"], 70)
        self.assertEqual(store.cards["2050070010"]["source"], "passive_observer:training_list")


class TrainingListContractTests(unittest.TestCase):
    def nodes(self):
        out = {}
        for path in PIPELINE.rglob("*.json"):
            out.update({k: v for k, v in load_jsonc(path).items() if isinstance(v, dict)})
        return out

    def test_node_is_disabled_custom_do_nothing_and_hooked_after_loading_wait(self):
        nodes = self.nodes()
        node = nodes["SC.Resource.Observe.TrainingList"]
        self.assertIs(node["enabled"], False)
        self.assertEqual((node["recognition"], node["action"], node["custom_recognition"]), ("Custom", "DoNothing", ro.NAME))
        self.assertEqual(node["custom_recognition_param"], {"layout": "training_list"})
        hook = "[JumpBack]SC.Resource.Observe.TrainingList"   # 2026-11-19: hits after a read -> fresh frame
        users = {n: v["next"] for n, v in nodes.items() if hook in (v.get("next") or [])}
        self.assertEqual(set(users), {"SC.Training.Main"})
        entries = users["SC.Training.Main"]
        index = entries.index(hook)
        # 2026-10-24: moved with the list nodes ahead of the shared popup close; the observer checks the list guard
        # (undimmed) and the training help pop-up itself (resource_observer.TRAINING_GUARD / TRAINING_BLOCKERS).
        self.assertEqual(entries[index - 1], "[JumpBack]SC.Training.SortPopup.Apply")
        self.assertLess(index, entries.index("[JumpBack]SC.Training.List.Select"))
        self.assertEqual((ro.TRAINING_GUARD, ro.TRAINING_BLOCKERS), ("SC.Training.List.Guard", ("SC.Popup.TrainingHelp.Guard",)))
        self.assertLess(entries.index("SC.Training.Halt"), index)
        self.assertEqual(entries.count(hook), 1)
        self.assertNotIn("SC.Resource.Observe.TrainingList", entries)
        table = json.loads((ROOT / "assets/input/pipeline_actions.json").read_text(encoding="utf-8"))
        self.assertNotIn("SC.Resource", json.dumps(table))

    def test_new_modules_have_no_input_capture_or_network(self):
        for name in ("reading_store", "report_readings", "resource_observer"):
            source = (ROOT / f"agent/core/{name}.py").read_text(encoding="utf-8")
            for forbidden in ("post_click", "post_screencap", "cached_image", "run_task", "run_action", "SpendGate",
                              "authorize", "requests", "urllib", "socket", "subprocess", "webview_controller"):
                self.assertNotIn(forbidden, source, f"{name}: {forbidden}")
        self.assertEqual((ROOT / "agent/core/resource_observer.py").read_text(encoding="utf-8").count("override_pipeline("), 1)


TRAINING_LIST = ROOT / "dev_assets/private/fes-match-reader-20261007/training_list_descending.png"
HOME_NOBADGE = ROOT / "dev_assets/private/manual-capture-kept-20261002/home-20261003-nobadge.png"


def _native_ready():
    if not (TRAINING_LIST.is_file() and HOME_NOBADGE.is_file() and _maa_ready()):
        return False
    try:
        from agent.core import card_inventory as ci

        return any(ci.ASSET_DIR.glob("*")) if hasattr(ci, "ASSET_DIR") else True
    except Exception:
        return False


@unittest.skipUnless(_native_ready(), "需要 MaaFramework、私有模板包、OCR 模型、卡面缩略图与原生训练列表帧")
class TrainingListNativeTests(unittest.TestCase):
    """Real Maa: the hook reads the visible tiles of the native training list on the frame the task polled, with no
    extra capture, matches the offline card-scan reader, and a clean home frame emits nothing for this layout."""

    @classmethod
    def setUpClass(cls):
        from maa.resource import Resource

        from agent.core import card_inventory as ci
        from agent.core.resource_bundles import bundle_paths, ocr_model_dir

        cls.sink = ReportSink()
        cls.resource = Resource()
        for bundle in bundle_paths():
            assert cls.resource.post_bundle(bundle).wait().succeeded
        model = ocr_model_dir()
        if model is not None:
            assert cls.resource.post_ocr_model(model).wait().succeeded
        cls.observer = ro.install(cls.resource, cls.sink, store=None, list_min_interval_s=0.0, list_miss_interval_s=0.0)
        cls.index = ci.CardIndex.from_assets()
        cls.observer.index, cls.observer._index_state = cls.index, "ready"

    def run_once(self, path, next_list):
        from maa.tasker import Tasker

        from tools import run_tasks as launcher

        images, advances = launcher.load_frames([(path, 1)])
        scenario = launcher.ReplayScenario(images, advances, allow_input=False)
        controller = launcher.create_replay_controller(scenario)
        self.assertTrue(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(self.resource, controller))
        job = tasker.post_task("SC.Test.Observe", {
            "SC.Test.Observe": {"recognition": "DirectHit", "next": next_list},
            "SC.Test.Done": {"recognition": "DirectHit"}}).wait()
        self.assertTrue(job.succeeded)
        self.names = [n.name for n in tasker.get_task_detail(job.job_id).nodes if n is not None]
        self.assertEqual((scenario.clicks, scenario.other_calls), ([], 0))
        return scenario.captures

    def test_native_training_list_levels_match_the_offline_reader(self):
        from PIL import Image

        from agent.core import owned_levels as ol

        self.sink.drain()
        self.observer.refusals.clear()
        self.observer.list["last_cards"] = None
        hooked = self.run_once(TRAINING_LIST, ["SC.Resource.Observe.TrainingList", "SC.Test.Done"])
        self.assertIn("SC.Resource.Observe.TrainingList", self.names)     # a read hits (DoNothing, no input)
        plain = self.run_once(TRAINING_LIST, ["SC.Test.Done"])
        self.assertEqual(hooked, plain)                      # no capture beyond the polling batch
        reports = self.sink.drain()
        self.assertEqual(self.observer.refusals.get("not_training_list", 0), 0, self.observer.summary())
        report, = reports
        offline = ol.read_frame(Image.open(TRAINING_LIST).convert("RGB"), self.index, ol.maa_ocr(__import__(
            "agent.core.resource_bundles", fromlist=["ocr_model_dir"]).ocr_model_dir()))
        want = {r["card_id"]: (r["limit_break"], r.get("level")) for r in offline if r["status"] == "accepted"
                and r["confidence"] >= self.observer.options["list_min_confidence"]}
        got = {cid: (c["limit_break"], c.get("level")) for cid, c in report["cards"].items()}
        self.assertGreaterEqual(len(got), 10)
        self.assertEqual(got, want)
        self.assertEqual(got["2050070010"], (0, 66))
        self.assertTrue(all(c["confidence_pm"] >= 500 for c in report["cards"].values()))

    def test_clean_home_emits_nothing_for_the_training_layout(self):
        self.sink.drain()
        self.run_once(HOME_NOBADGE, ["SC.Resource.Observe.TrainingList", "SC.Test.Done"])
        self.assertNotIn("SC.Resource.Observe.TrainingList", self.names)
        self.assertEqual(self.sink.drain(), [])


if __name__ == "__main__":
    unittest.main()

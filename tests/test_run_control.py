from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.core import run_control as rc


class ControlTests(unittest.TestCase):
    def setUp(self):
        rc.PRIVATE.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="_run_control_", dir=rc.PRIVATE)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def control(self, **kwargs):
        control = rc.RunControl(**kwargs)
        self.addCleanup(control.close)
        return control

    def rows(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_preexisting_stop_uses_presence_only_and_latches_after_removal(self):
        path = self.root / "run.stop"
        path.write_bytes(b"private-untrusted-contents")
        control = self.control(stop_file=path)
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read")):
            self.assertTrue(control.poll())
        path.unlink()
        self.assertTrue(control.poll())
        self.assertEqual(control.reason, "stop_file_requested")

    def test_stop_path_directory_and_inspection_error_stop_conservatively(self):
        path = self.root / "run.stop"
        control = self.control(stop_file=path)
        path.mkdir()
        self.assertTrue(control.poll())
        self.assertEqual(control.reason, "stop_file_unavailable")
        other = self.control(stop_file=self.root / "other.stop")
        with patch.object(rc, "private_path", side_effect=PermissionError("secret")):
            self.assertTrue(other.poll())
        self.assertEqual(other.reason, "stop_file_unavailable")

    def test_private_escape_suffix_collision_and_existing_event_file_rejected(self):
        path = self.root / "events.jsonl"
        path.write_bytes(b"original")
        for kwargs in ({"events_path": path}, {"events_path": self.root / "events.json"},
                       {"events_path": path, "stop_file": path},
                       {"stop_file": rc.PRIVATE.parent / "outside.stop"}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, FileExistsError)):
                rc.RunControl(**kwargs)
        self.assertEqual(path.read_bytes(), b"original")

    def test_reparse_ancestor_refused(self):
        original = Path.lstat
        def lstat(path):
            result = original(path)
            if path == self.root:
                return type("Info", (), {"st_mode": result.st_mode, "st_file_attributes": 1024})()
            return result
        with patch.object(Path, "lstat", lstat), self.assertRaises(ValueError):
            rc.RunControl(events_path=self.root / "events.jsonl")

    def test_events_are_sequenced_and_concurrent_whole_lines(self):
        path = self.root / "events.jsonl"
        control = self.control(events_path=path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(lambda n: control.emit("node", node="SC.Test.Node", status="starting"), range(40))))
        rows = self.rows(path)
        self.assertEqual([row["sequence"] for row in rows], list(range(1, 41)))
        self.assertEqual(len({row["run_id"] for row in rows}), 1)
        self.assertFalse(control.poll())

    def test_event_limit_and_short_write_latch_stop_without_raw_exception(self):
        control = self.control(events_path=self.root / "count.jsonl", max_events=1)
        self.assertTrue(control.emit("run.started"))
        self.assertFalse(control.emit("run.finished"))
        self.assertEqual(control.reason, "event_log_unavailable")
        other = self.control(events_path=self.root / "short.jsonl")
        other._stream.close()
        class Short(io.BytesIO):
            def write(self, blob):
                super().write(blob[:10])
                return 10
        other._stream = Short()
        self.assertFalse(other.emit("run.started"))
        self.assertTrue(other.poll())
        self.assertFalse(other.summary()["events_complete"])

    def test_recognition_timings_are_aggregated_into_bounded_rows(self):
        """2026-10-25 W.I.N.G. speed audit: per-node recognition time without a row per attempt."""
        now = [0.0]
        path = self.root / "reco.jsonl"
        control = self.control(events_path=path, clock=lambda: now[0])
        for k in range(300):
            for name, took, hit in (("SC.Wing.On.Hub", 0.4, k % 3 == 0), ("SC.Wing.Is.Hub", 0.05, True)):
                control.recognition("Node.Recognition.Starting", name)
                now[0] += took
                control.recognition("Node.Recognition.Succeeded" if hit else "Node.Recognition.Failed", name)
        control.recognition("Node.Recognition.Failed", "SC.Wing.Unopened")      # no Starting: ignored
        control.close()
        rows = [r for r in self.rows(path) if r["event"] == "recognition_stats"]
        self.assertLessEqual(len(rows), 300 * 0.45 / rc.RECO_WINDOW_S + 2)
        totals = {}
        for row in rows:
            for name, n, hits, total, longest in row["nodes"]:
                agg = totals.setdefault(name, [0, 0, 0.0])
                agg[0], agg[1], agg[2] = agg[0] + n, agg[1] + hits, agg[2] + total
                self.assertLessEqual(longest, 0.4 + 1e-9)
        self.assertEqual(totals["SC.Wing.On.Hub"][:2], [300, 100])
        self.assertAlmostEqual(totals["SC.Wing.On.Hub"][2], 120.0, places=2)
        self.assertEqual(totals["SC.Wing.Is.Hub"][:2], [300, 300])
        self.assertNotIn("SC.Wing.Unopened", totals)
        self.assertTrue(rows[0]["nodes"][0][0] == "SC.Wing.On.Hub")            # slowest first

    def test_event_bytes_are_bounded(self):
        path = self.root / "bytes.jsonl"
        control = self.control(events_path=path, max_bytes=1024)
        for _ in range(20):
            control.emit("node", node="SC.Test.Node", status="starting")
        self.assertLessEqual(path.stat().st_size, 1024)
        self.assertEqual(control.reason, "event_log_unavailable")

    def test_report_and_click_filter_drop_free_text_paths_and_credentials(self):
        path = self.root / "events.jsonl"
        control = self.control(events_path=path)
        secret = "DO-NOT-EXPORT-THIS"
        control.task = "MissionStatus"
        control.report({"kind": "mission_status", "ok": True, "counter": [1, 5],
                        "rows": [{"title": secret, "status": "claimable"}, {"status": [secret]}],
                        "token": secret, "path": secret, "notes": [secret]})
        control.report({"kind": "grade", "decisions": [{"rights": "19/20", "money": "12000", "notes": secret}]})
        control.input_event({"kind": "click", "action": "probe-click", "result": "unknown",
                             "spend": True, "spend_token": secret, "nonce": secret, "reason": secret,
                             "response": {"attempted": True, "raw": secret}})
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        rows = self.rows(path)
        self.assertEqual(rows[0]["readings"]["counter"], [1, 5])
        self.assertEqual(rows[0]["readings"]["row_states"], ["claimable", "unknown"])
        self.assertEqual(rows[1]["readings"]["observations"], [{"money": 12000, "rights": [19, 20]}])
        self.assertEqual(rows[2]["result"], "unknown")

    def test_grade_daily_progress_and_shop_skip_reasons_reach_ui(self):
        control = self.control()
        grade = control.readings({"kind": "grade", "status": "takeover", "takeover_required": True,
            "daily_progress": {"mode": "grade", "target": 3, "completed": 1, "remaining": 2,
                               "planned_runs": 2, "path": "private"}})
        self.assertEqual(grade["readings"]["daily_progress"],
                         {"mode": "grade", "target": 3, "completed": 1, "remaining": 2, "planned_runs": 2})
        self.assertEqual((grade["readings"]["status"], grade["readings"]["takeover_required"]), ("takeover", True))
        shop = control.readings({"kind": "shop", "outcome": "skipped_rules", "notes": ["rank1:coin_reserve"]})
        self.assertEqual(shop["readings"]["skip_reasons"], ["coin_reserve"])
        self.assertEqual(shop["readings"]["outcome"], "skipped_rules")

    def test_stop_event_written_only_once(self):
        path = self.root / "events.jsonl"
        control = self.control(events_path=path)
        for _ in range(10):
            control.request_stop()
            self.assertTrue(control.poll())
        self.assertEqual([r["event"] for r in self.rows(path)], ["run.stop_requested"])

    def test_match_boundaries_and_failure_reason_reach_ui_without_raw_evidence(self):
        control = self.control()
        result = control.readings({'kind': 'match', 'outcome': 'latest_not_skippable',
            'progress': {'latest_open_stage': 'A7-S20', 'latest_skippable_stage': 'A5-S15',
                         'selected_stage': 'A7-S20', 'skip_list_complete': True,
                         'frames': ['private-frame'], 'path': 'private'}})['readings']
        self.assertEqual(result['outcome'], 'latest_not_skippable')
        self.assertEqual(result['progress'], {'latest_open_stage': 'A7-S20',
            'latest_skippable_stage': 'A5-S15', 'selected_stage': 'A7-S20', 'skip_list_complete': True})
        bad = control.readings({'kind': 'match', 'outcome': ['invalid'], 'progress':
            {'latest_open_stage': 'private', 'latest_skippable_stage': 'A9-S99', 'skip_list_complete': 'yes'}})
        self.assertEqual(bad['readings']['progress'], {'skip_list_complete': False})
        self.assertNotIn('outcome', bad['readings'])

    def test_each_task_forwards_its_own_outcomes(self):
        control = self.control()
        for kind, code in (('training', 'list_sort_unknown'), ('training', 'insufficient_tickets'),
                           ('shop', 'confirm_mismatch'), ('grade', 'manual_takeover'),
                           ('business', 'slots_unverified'), ('match', 'latest_not_skippable')):
            with self.subTest(kind=kind, code=code):
                result = control.readings({'kind': kind, 'outcome': code})
                self.assertEqual(result['readings']['outcome'], code)
        for code in ('private-card-name', 'slots_unverified', ['invalid']):
            self.assertNotIn('outcome', control.readings({'kind':'training','outcome':code})['readings'])

    def test_claim_outcomes_reach_ui_without_private_report_details(self):
        for kind, code in (('missions_claim', 'already_claimed'), ('missions_claim', 'bulk_received'),
                           ('present', 'box_empty'), ('present', 'received_partial'),
                           ('present', 'result_overflow')):
            with self.subTest(kind=kind, code=code):
                self.assertEqual(rc.RunControl.readings({'kind': kind, 'outcome': code,
                    'reason': 'private', 'record': {'id': 'private'}}),
                    {'kind': kind, 'readings': {'outcome': code}})
        stopped = rc.RunControl.readings({'kind': 'present', 'outcome': 'stopped:private/path'})
        self.assertEqual(stopped['readings'], {'outcome': 'stopped'})
        self.assertEqual(rc.RunControl.readings({'kind': 'present', 'outcome': ['invalid']})['readings'], {})

    def test_native_node_sink_filters_focus_and_nonproject_nodes(self):
        path = self.root / "events.jsonl"
        control = self.control(events_path=path)
        class Tasker:
            def add_context_sink(self, sink):
                self.sink = sink
                return 1
        tasker = Tasker()
        control.attach(tasker)
        tasker.sink.on_raw_notification(None, "Node.PipelineNode.Starting", {"name": "SC.Test.Node", "focus": "secret"})
        tasker.sink.on_raw_notification(None, "Node.PipelineNode.Starting", {"name": "Other.Node"})
        tasker.sink.on_raw_notification(None, "Node.Recognition.Succeeded", {"name": "SC.Test.Node"})
        self.assertEqual([(r["node"], r["status"]) for r in self.rows(path)], [("SC.Test.Node", "starting")])
        self.assertNotIn("secret", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

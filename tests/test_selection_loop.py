import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import selection_loop_protocol as protocol
from tools import resolve_training_selection as resolver


class SelectionLoopProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = {"version": 1, "host": "127.0.0.1", "port": 3456, "token": "a" * 64, "pid": 123,
            "input_enabled": True, "browser_demo_only": False, "transport": protocol.TRANSPORT,
            "action": protocol.ACTION, "budget_path": str(protocol.BUDGET), "resolution_path": str(protocol.RESOLUTION),
            "resolution_sha256": "b" * 64, "max_attempts": 4}
        self.status = {"ready": True, "browser_demo": False, "input_enabled": False, "composition_game": True,
            "width": 1280, "height": 720, "training_selection_loop": True, "minimized": False,
            "window_visible": True, "webview_visible": True, "source_capture_width": 1280, "source_capture_height": 720,
            "frame_sha256": "c" * 64, "frame_age_ms": 50}

    def test_session_requires_independent_fixed_scope_reviewed_digest_and_budget(self):
        self.assertEqual(protocol.validate_session(self.session, 123, "b" * 64), self.session)
        for change in ({"action": "training-confirm-one-bronze"}, {"budget_path": str(protocol.BUDGET.with_name("new.json"))},
                       {"resolution_path": str(protocol.RESOLUTION.with_name("new.json"))}, {"max_attempts": 5},
                       {"pid": True}, {"host": "localhost"}, {"input_enabled": False}, {"coordinate": [461, 236]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.validate_session({**self.session, **change}, 123, "b" * 64)
        with self.assertRaises(ValueError):
            protocol.validate_session(self.session, 123, "d" * 64)

    def test_select_is_one_closed_action_without_coordinates(self):
        request = protocol.select_request(self.session, self.status, "c" * 64, "d" * 32)
        self.assertEqual(set(request), {"token", "command", "action", "nonce", "frame_sha256", "source_width", "source_height"})
        self.assertEqual(request["action"], "training-select-asahi")
        self.assertEqual(request["command"], "selection-loop-select")
        self.assertEqual(protocol.POINT, (461, 236))

    def test_changed_stale_hidden_or_geometry_frame_rejected(self):
        for change in ({"frame_sha256": "d" * 64}, {"frame_age_ms": 5001}, {"frame_age_ms": True},
                       {"frame_age_ms": float("nan")}, {"training_selection_loop": False}, {"minimized": True},
                       {"window_visible": False}, {"source_capture_width": 1920}, {"input_enabled": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.select_request(self.session, {**self.status, **change}, "c" * 64, "d" * 32)
        with self.assertRaises(ValueError):
            protocol.select_request(self.session, self.status, "c" * 64, "bad")

    def test_status_has_no_input_and_confirmation_cannot_use_public_file(self):
        self.assertEqual(set(protocol.status_request(self.session)), {"command", "token"})
        with self.assertRaises(ValueError):
            protocol.confirm_request(self.session, self.status, "c" * 64, "d" * 32,
                                     protocol.ROOT / "README.md", "e" * 64)

    def test_legacy_workspace_paths_rebase_only_into_private(self):
        moved = protocol.legacy_private_path(r"D:\Machine Learning\maa\dev_assets\private\x\frame-001.png")
        self.assertEqual(moved, (protocol.PRIVATE / "x/frame-001.png").resolve())
        self.assertEqual(protocol.legacy_private_path(r"d:\machine learning\MAA\dev_assets\private\a.png"),
                         (protocol.PRIVATE / "a.png").resolve())
        for value in (r"D:\Machine Learning\maa\README.md", r"D:\Machine Learning\maa\dev_assets\private\..\..\x.png",
                      r"E:\Machine Learning\maa\dev_assets\private\a.png", r"D:\Machine Learning\maaX\dev_assets\private\a.png", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.legacy_private_path(value)

    def test_native_sent_pending_is_not_postcondition_success(self):
        result = {"ok": True, "attempted": True, "reason": "native_selection_sent_postcondition_required",
                  "state": "pending", "attempts": 2, "max_attempts": 4}
        self.assertEqual(protocol.validate_response(result)["state"], "pending")
        for change in ({"state": "success"}, {"attempts": True}, {"attempts": 5}, {"max_attempts": 99}, {"passed": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.validate_response({**result, **change})


class SelectionLegacyResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        frames = []
        for index in range(4):
            path = self.base / f"frame-{index}.png"
            blob = str(index).encode()
            path.write_bytes(blob)
            frames.append({"image": str(path), "sha256": hashlib.sha256(blob).hexdigest()})
        self.ledger = {"Version": 1, "Navigation": protocol.ACTION, "MaxAttempts": 1, "Attempts": 1,
            "LastPid": 123, "LastFrameSha256": frames[2]["sha256"], "LastNonce": "a" * 32}
        self.report = {"passed": False, "action": protocol.ACTION, "input_enabled": True, "offline_only": False,
            "resource_consumption": False, "input_attempts": 1, "response": {"ok": True, "attempted": True},
            "node_trace": [{"name": "SC.DailyNavigation.training-select-asahi." + name,
                            "completed": True, "hit": True, "action_success": True} for name in ("Observe", "Click")],
            "frames": frames, "postcondition_checks": [{}]}

    def validate(self, report=None, ledger=None):
        with patch("tools.resolve_training_selection.legacy_private_path", side_effect=lambda p: Path(p)):
            return resolver.validate_legacy(report or self.report, ledger or self.ledger)

    def test_re_resolution_preserves_failed_report_and_binds_original_pre_post_images(self):
        before = copy.deepcopy(self.report)
        pre, post = self.validate()
        self.assertEqual(pre["sha256"], self.ledger["LastFrameSha256"])
        self.assertEqual(post, {**self.report["frames"][-1], "image": str(Path(self.report["frames"][-1]["image"]))})
        self.assertEqual(self.report, before)
        self.assertNotIn("session_pid", self.report)
        self.assertNotIn("nonce", self.report)

    def test_paid_or_unattempted_or_forged_success_reports_cannot_resolve(self):
        for change in ({"resource_consumption": True}, {"input_attempts": 0}, {"input_attempts": True},
                       {"passed": True}, {"response": {"ok": True, "attempted": False}}, {"node_trace": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate({**self.report, **change})

    def test_ledger_mismatch_or_image_changes_cannot_resolve(self):
        for change in ({"LastFrameSha256": "f" * 64}, {"Attempts": 0}, {"Navigation": "home-idol"}, {"MaxAttempts": 4}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(ledger={**self.ledger, **change})
        Path(self.report["frames"][-1]["image"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.validate()

    def test_confirm_pending_and_consumed_native_are_never_cleared(self):
        pending = self.base / "pending.json"
        native = self.base / "native.json"
        with patch("tools.resolve_training_selection.PENDING", pending), patch("tools.resolve_training_selection.CONFIRM_LEDGER", native):
            resolver.consumption_unused()
            pending.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolver.consumption_unused()
            self.assertEqual(pending.read_text(), "{}")
            pending.unlink()
            data = {"Version": 1, "Navigation": "training-confirm-one-bronze", "MaxAttempts": 1, "Attempts": 0,
                    "LastPid": None, "LastFrameSha256": None, "LastNonce": None}
            native.write_text(json.dumps(data), encoding="utf-8")
            resolver.consumption_unused()
            data.update(Attempts=1, LastPid=123, LastFrameSha256="a" * 64, LastNonce="b" * 32)
            native.write_text(json.dumps(data), encoding="utf-8")
            before = native.read_bytes()
            with self.assertRaises(ValueError):
                resolver.consumption_unused()
            self.assertEqual(native.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

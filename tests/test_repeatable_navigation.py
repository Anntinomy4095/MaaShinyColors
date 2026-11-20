import hashlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from agent.core.daily_navigation import ACTION_GROUPS, POST_GROUPS
from tools import repeatable_navigation_protocol as protocol
from tools import run_daily_navigation as runner
from tools.navigation_protocol import ALL_DAILY_ACTION_POINTS, DAILY_SCROLL_ACTION_POINTS


class RepeatableProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = {"version": 1, "host": "127.0.0.1", "port": 3456, "token": "a" * 64, "pid": 123,
            "input_enabled": True, "browser_demo_only": False, "transport": protocol.TRANSPORT,
            "ledger_root": str(protocol.LEDGER_ROOT), "game_day": "2026-10-01", "per_action_max": 3, "day_max": 60,
            "actions": protocol.allowed_actions(False)}
        self.status = {"ready": True, "browser_demo": False, "input_enabled": False, "composition_game": True,
            "width": 1280, "height": 720, "supervised_daily": True, "repeatable_navigation": True,
            "supervised_navigation": None, "supervised_training": False, "minimized": False, "window_visible": True,
            "webview_visible": True, "source_capture_width": 1280, "source_capture_height": 720,
            "frame_sha256": "c" * 64, "frame_age_ms": 50}

    def test_whitelist_is_closed_and_excludes_consumption_selection_scroll_and_startup(self):
        self.assertEqual(len(protocol.ACTIONS), 35)
        # The daily game-start (same point as the one-shot touch-to-start) is repeatable and guarded.
        self.assertIn("game-start", protocol.allowed_actions(False))
        from tools.navigation_protocol import DAILY_SHOP_ACTION_POINTS
        from tools.grade_protocol import CONSUMING as GRADE_CONSUMING, GRADE_ACTION_POINTS
        for action in protocol.ACTIONS:
            self.assertIn(action, ALL_DAILY_ACTION_POINTS | DAILY_SHOP_ACTION_POINTS | GRADE_ACTION_POINTS)
        # Grade: only non-consuming navigation; the difficulty circles are never repeatable.
        self.assertEqual(set(protocol.ACTIONS) & set(GRADE_ACTION_POINTS), set(protocol.GRADE_ACTIONS))
        self.assertFalse(set(protocol.ACTIONS) & set(GRADE_CONSUMING))
        # Shop: only non-consuming navigation, never offer-open or the exchange confirms.
        self.assertEqual(set(protocol.ACTIONS) & set(DAILY_SHOP_ACTION_POINTS), set(protocol.SHOP_ACTIONS))
        for action in ("training-select-asahi", "training-bronze-plus", "training-open-confirm",
                       "training-confirm-one-bronze", "training-result-close", "touch-to-start", *DAILY_SCROLL_ACTION_POINTS):
            self.assertNotIn(action, protocol.ACTIONS)
        # Every runner-reachable repeatable action has a read-only postcondition contract.
        for action in set(protocol.ACTIONS) & set(ACTION_GROUPS):
            self.assertIn(action, POST_GROUPS)

    def test_session_binds_transport_ledger_caps_and_exact_scope(self):
        self.assertEqual(protocol.validate_session(self.session, 123), self.session)
        self.assertEqual(protocol.validate_session({**self.session, "actions": list(protocol.ACTIONS)}, 123)["pid"], 123)
        for change in ({"transport": "webview2-composition-daily"}, {"ledger_root": str(protocol.PRIVATE / "daily_navigation")},
                       {"ledger_root": str(protocol.ROOT)}, {"per_action_max": 4}, {"day_max": 61}, {"per_action_max": True},
                       {"actions": [*protocol.allowed_actions(False), "training-confirm-one-bronze"]},
                       {"actions": protocol.allowed_actions(False)[1:]}, {"game_day": "today"}, {"pid": True},
                       {"host": "localhost"}, {"input_enabled": False}, {"token": "x"}, {"coordinate": [1, 2]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.validate_session({**self.session, **change}, 123)
        with self.assertRaises(ValueError):
            protocol.validate_session(self.session, 124)

    def test_navigate_request_is_closed_and_frame_bound(self):
        request = protocol.navigate_request(self.session, self.status, "c" * 64, "home-mission", "d" * 32)
        self.assertEqual(set(request), {"token", "command", "action", "nonce", "frame_sha256", "source_width", "source_height"})
        self.assertEqual(request["command"], "repeatable-navigate")
        for action in ("training-confirm-one-bronze", "home-idol", "mission-scroll-down", "touch-to-start"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                protocol.navigate_request(self.session, self.status, "c" * 64, action, "d" * 32)
        for change in ({"frame_sha256": "d" * 64}, {"frame_age_ms": 5001}, {"frame_age_ms": float("nan")},
                       {"repeatable_navigation": False}, {"supervised_daily": False}, {"supervised_navigation": "announcement-back"},
                       {"minimized": True}, {"source_capture_width": 1920}, {"input_enabled": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.navigate_request(self.session, {**self.status, **change}, "c" * 64, "home-mission", "d" * 32)
        with self.assertRaises(ValueError):
            protocol.navigate_request(self.session, self.status, "c" * 64, "home-mission", "bad")

    def test_training_portal_requires_both_session_scope_and_training_switch(self):
        session = {**self.session, "actions": list(protocol.ACTIONS)}
        with self.assertRaises(ValueError):
            protocol.navigate_request(session, self.status, "c" * 64, "home-idol", "d" * 32)
        request = protocol.navigate_request(session, {**self.status, "supervised_training": True}, "c" * 64, "home-idol", "d" * 32)
        self.assertEqual(request["action"], "home-idol")
        for action in ("training-list-back", "idol-back"):
            with self.subTest(action=action):
                self.assertNotIn(action, protocol.allowed_actions(False))
                with self.assertRaises(ValueError):
                    protocol.navigate_request(session, self.status, "c" * 64, action, "d" * 32)
                request = protocol.navigate_request(session, {**self.status, "supervised_training": True}, "c" * 64, action, "d" * 32)
                self.assertEqual(request["action"], action)

    def test_status_and_confirm_requests(self):
        self.assertEqual(protocol.status_request(self.session, "home-fes"),
                         {"token": "a" * 64, "command": "repeatable-status", "action": "home-fes"})
        with self.assertRaises(ValueError):
            protocol.confirm_request(self.session, self.status, "c" * 64, "home-fes", "d" * 32, protocol.ROOT / "README.md", "e" * 64)

    def test_response_counts_and_attempt_permission(self):
        base = {"ok": True, "attempted": False, "reason": "readonly_ledger_status", "state": "ready", "action_count": 0, "day_count": 0}
        self.assertTrue(protocol.status_allows_attempt(base))
        self.assertTrue(protocol.status_allows_attempt({**base, "state": "confirmed", "action_count": 2, "day_count": 9}))
        for change in ({"state": "pending"}, {"state": "blocked"}, {"action_count": 3, "day_count": 3},
                       {"ok": False}, {"attempted": True}):
            with self.subTest(change=change):
                self.assertFalse(protocol.status_allows_attempt({**base, **change}))
        for change in ({"state": "success"}, {"action_count": 4, "day_count": 4}, {"day_count": 61},
                       {"action_count": True}, {"action_count": 2, "day_count": 1}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.validate_response({**base, **change})
        pending = {**base, "attempted": True, "reason": "native_gesture_sent_postcondition_required", "state": "pending",
                   "action_count": 1, "day_count": 1}
        self.assertEqual(runner.validate_repeatable_navigate_response(pending), pending)
        with self.assertRaises(ValueError):
            runner.validate_repeatable_navigate_response({**pending, "state": "confirmed"})

    def test_postcondition_proof_shape_matches_host_whitelist(self):
        post = {"image": str(protocol.PRIVATE / "x/frame-002.png"), "sha256": "e" * 64}
        proof = protocol.postcondition_proof("home-mission", "d" * 32, 123, "c" * 64, post, "1" * 64, "2" * 64, [], {}, "3" * 64)
        self.assertEqual(set(proof), {"version", "kind", "action", "passed", "resource_consumption", "input_attempts", "nonce",
            "session_pid", "pre_frame_sha256", "post_frame", "configuration_sha256", "foreground_sha256",
            "post_recognitions", "foreground", "producer_path", "producer_sha256"})
        self.assertEqual(proof["kind"], "SC.RepeatableNavigation.Postcondition")
        self.assertIs(proof["resource_consumption"], False)
        self.assertEqual(Path(proof["producer_path"]), (protocol.ROOT / "tools/run_daily_navigation.py").resolve())
        for args in (("training-confirm-one-bronze", "d" * 32, 123, "c" * 64, post),
                     ("home-mission", "d" * 32, 123, "e" * 64, post),
                     ("home-mission", "d" * 32, 123, "c" * 64, {**post, "image": str(protocol.ROOT / "README.md")}),
                     ("home-mission", "d" * 32, True, "c" * 64, post)):
            with self.subTest(args=args[0]), self.assertRaises(ValueError):
                protocol.postcondition_proof(*args, "1" * 64, "2" * 64, [], {}, "3" * 64)


class RepeatableRunnerTests(unittest.TestCase):
    def test_one_shot_guarded_send_unchanged_and_repeatable_records_identity_before_send(self):
        report = {'input_attempts': 0}
        send = Mock(side_effect=TimeoutError('lost'))
        request = {'nonce': 'd' * 32, 'frame_sha256': 'c' * 64}
        self.assertFalse(runner.guarded_send(report, True, 'home-mission', (62, 429), Mock(return_value=request), send,
                                             runner.validate_repeatable_navigate_response))
        self.assertEqual((report['nonce'], report['pre_frame_sha256'], report['input_attempts']), ('d' * 32, 'c' * 64, 1))
        self.assertFalse(runner.guarded_send(report, True, 'home-mission', (62, 429), Mock(return_value=request), send))
        self.assertEqual(send.call_count, 1)
        legacy = {'input_attempts': 0}
        runner.guarded_send(legacy, True, 'home-mission', (62, 429), Mock(return_value=request), Mock(side_effect=OSError()))
        self.assertNotIn('nonce', legacy)

    def test_repeatable_cli_refuses_offline_or_non_whitelisted_actions(self):
        for extra in (['--offline-only'], ['--action', 'training-select-asahi', '--session', 'x']):
            argv = ['run_daily_navigation.py', '--repeatable', '--manifest', 'a', '--sources', 'b', '--foreground-manifest', 'c',
                    '--foreground-contracts', 'd', '--report', 'e.json']
            if '--action' not in extra:
                argv += ['--action', 'home-mission']
            with self.subTest(extra=extra), patch('sys.argv', argv + extra), \
                    patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                runner.main()

    def test_confirmation_writes_exclusive_proof_and_requires_confirmed_state(self):
        with tempfile.TemporaryDirectory(dir=protocol.PRIVATE) as directory:
            base = Path(directory)
            frame = base / 'frame-002.png'
            frame.write_bytes(b'post')
            entry = {'image': str(frame), 'sha256': hashlib.sha256(b'post').hexdigest()}
            args = types.SimpleNamespace(report=base / 'report.json', action='home-mission')
            session = {'pid': 123}
            bridge = types.SimpleNamespace(session={'pid': 123}, status=lambda: {})
            producer = hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest()
            report = {'nonce': 'd' * 32, 'pre_frame_sha256': 'c' * 64,
                      'response': {'ok': True, 'attempted': True, 'state': 'pending'}}
            confirmed = {"ok": True, "attempted": False, "reason": "repeatable_postcondition_confirmed",
                         "state": "confirmed", "action_count": 1, "day_count": 1}
            sent = []
            def send(_, request):
                sent.append(request)
                return confirmed
            with patch.object(runner.repeatable_protocol, 'confirm_request', side_effect=lambda *a: {'proof': str(a[5]), 'sha': a[6]}):
                runner.confirm_repeatable(report, args, session, bridge, entry, [], {'state': 'no_known_popup'},
                                          '1' * 64, '2' * 64, producer, send)
                proof_path = base / 'report-postcondition.json'
                proof = json.loads(proof_path.read_text(encoding='utf-8'))
                self.assertEqual(proof['post_frame'], {'image': str(frame.resolve()), 'sha256': entry['sha256']})
                self.assertEqual(sent[0]['sha'], hashlib.sha256(proof_path.read_bytes()).hexdigest())
                # A second registration never overwrites the existing proof.
                with self.assertRaises(ValueError):
                    runner.confirm_repeatable(report, args, session, bridge, entry, [], {}, '1' * 64, '2' * 64, producer, send)
                proof_path.unlink()
                with self.assertRaises(ValueError):
                    runner.confirm_repeatable(report, args, session, bridge, entry, [], {}, '1' * 64, '2' * 64, producer,
                                              lambda *_: {**confirmed, "ok": False, "state": "pending"})
                proof_path.unlink()
                for response in ({'ok': False, 'attempted': True, 'state': 'pending'}, {'ok': True, 'attempted': True, 'state': 'blocked'}):
                    with self.subTest(response=response), self.assertRaises(ValueError):
                        runner.confirm_repeatable({**report, 'response': response}, args, session, bridge, entry, [], {},
                                                  '1' * 64, '2' * 64, producer, send)
                    self.assertFalse(proof_path.exists())
                with self.assertRaises(ValueError):
                    runner.confirm_repeatable(report, args, session, bridge, entry, [], {}, '1' * 64, '2' * 64, 'f' * 64, send)
                self.assertEqual(len(sent), 1)

class ResolvePendingReportTests(unittest.TestCase):
    def setUp(self):
        from tools.run_daily_navigation import validate_pending_report
        self.validate = validate_pending_report
        self.prior = {"repeatable": True, "action": "mission-complete-to-mission", "input_enabled": True,
                      "input_attempts": 1, "passed": False, "reason": "postcondition_unverified_no_retry",
                      "resource_consumption": False, "session_pid": 77, "nonce": "a" * 32, "pre_frame_sha256": "b" * 64,
                      "response": {"ok": True, "attempted": True, "state": "pending", "reason": "x",
                                   "action_count": 1, "day_count": 1}}

    def test_sent_unverified_repeatable_report_binds_nonce_and_pre_frame(self):
        result = self.validate(self.prior, "mission-complete-to-mission", 77)
        self.assertEqual((result["nonce"], result["pre_frame_sha256"]), ("a" * 32, "b" * 64))

    def test_other_action_pid_unsent_one_shot_or_verified_reports_refused(self):
        cases = [("action", "home-fes"), ("session_pid", 78), ("input_attempts", 0), ("repeatable", False),
                 ("passed", True), ("reason", "repeatable_postcondition_confirmed"), ("resource_consumption", True),
                 ("nonce", "x"), ("response", {"ok": False, "attempted": True, "state": "pending"})]
        for key, value in cases:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate({**self.prior, key: value}, "mission-complete-to-mission", 77)
        with self.assertRaises(ValueError):
            self.validate(self.prior, "home-fes", 77)

if __name__ == "__main__":
    unittest.main()

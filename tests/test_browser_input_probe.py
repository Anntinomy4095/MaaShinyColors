from __future__ import annotations

import contextlib
import io
import json
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tools import run_browser_input_probe as probe

STATUS = {"ready": True, "browser_demo": True, "input_enabled": False, "width": 1280, "height": 720, "source_capture_width": 1282, "source_capture_height": 721}
TOP = probe.NativeWindow(10, 123, 0, "WindowsForms10.Window", probe.TITLE, True, False, (30, 40), (1280, 788), 144)
VIEWPORT = probe.NativeWindow(11, 123, 10, "WindowsForms10.Child", "", True, False, (30, 84), (1280, 720), 144)
WIDGET = probe.NativeWindow(12, 234, 11, "Chrome_WidgetWin_1", "", True, False, (30, 84), (1280, 720), 144)
RENDERER = probe.NativeWindow(13, 234, 12, "Chrome_RenderWidgetHostHWND", "", True, False, (30, 84), (1282, 721), 144)
TARGET = probe.InputTarget(TOP, VIEWPORT, RENDERER)
PROCESS = {123: (1, "host.exe"), 234: (123, "msedgewebview2.exe")}
DESKTOP = {"foreground_hwnd": 999, "cursor": [200, 300]}


def result(state, passed=True):
    return {"state": state, "passed": passed, "node_trace": [], "framework_succeeded": passed}


class BrowserInputIdentityTests(unittest.TestCase):
    def test_exact_title_pid_visibility_and_layout_are_required(self):
        self.assertEqual(probe.choose_top([TOP], 123), TOP)
        for updates in ({"title": "MaaShinyColors BrowserDemo game"}, {"pid": 124}, {"visible": False}, {"minimized": True}, {"class_name": "Chrome_WidgetWin_1"}, {"size": (1280, 720)}, {"size": (0, 0)}, {"dpi": 0}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                probe.choose_top([replace(TOP, **updates)], 123)
        with self.assertRaises(ValueError):
            probe.choose_top([TOP, replace(TOP, hwnd=99)], 123)

    def test_renderer_requires_unique_owned_viewport_and_physical_calibration(self):
        children = [VIEWPORT, WIDGET, RENDERER]
        self.assertEqual(probe.choose_input_target(TOP, children, PROCESS, STATUS), TARGET)
        self.assertEqual(TARGET.point(), (641, 461))
        for bad in (replace(RENDERER, pid=666), replace(RENDERER, size=(1920, 1080)), replace(RENDERER, origin=(30, 128)), replace(RENDERER, parent=10), replace(RENDERER, dpi=96), replace(RENDERER, visible=False)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe.choose_input_target(TOP, [VIEWPORT, WIDGET, bad], PROCESS, STATUS)
        with self.assertRaises(ValueError):
            probe.choose_input_target(TOP, [*children, replace(RENDERER, hwnd=14)], PROCESS, STATUS)
        with self.assertRaises(ValueError):
            probe.choose_input_target(TOP, [*children, replace(VIEWPORT, hwnd=99)], PROCESS, STATUS)

    def test_capture_source_dimensions_are_mandatory_and_exactly_calibrated(self):
        for changes in ({"source_capture_width": None}, {"source_capture_height": 900}, {"width": 1920}, {"browser_demo": False}, {"input_enabled": True}, {"ready": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                probe.choose_input_target(TOP, [VIEWPORT, WIDGET, RENDERER], PROCESS, {**STATUS, **changes})

    def test_renderer_parent_process_chain_must_be_webview_only(self):
        self.assertTrue(probe.owned_pid(234, 123, PROCESS))
        self.assertTrue(probe.owned_pid(345, 123, {**PROCESS, 345: (234, "msedgewebview2.exe")}))
        for processes in ({234: (999, "msedgewebview2.exe")}, {234: (123, "game.exe")}, {234: (234, "msedgewebview2.exe")}):
            self.assertFalse(probe.owned_pid(234, 123, processes))


class BrowserInputPipelineTests(unittest.TestCase):
    def test_click_has_actual_recognition_and_explicit_error_stop(self):
        pipeline = probe.fixed_pipeline(True)
        click = pipeline[probe.CLICK_NODE]
        self.assertEqual(click["action"], "Click")
        self.assertEqual(click["recognition"], "ColorMatch")
        self.assertEqual(click["roi"], [430, 380, 420, 160])
        self.assertTrue(click["enabled"])
        self.assertFalse(probe.fixed_pipeline()[probe.CLICK_NODE]["enabled"])
        self.assertEqual(click["target"], [640, 460, 1, 1])
        self.assertEqual(pipeline[click["on_error"][0]]["action"], "StopTask")
        self.assertEqual(pipeline[probe.ENTRY]["next"][0], probe.PREFIX + "Ambiguous")
        self.assertNotIn(probe.CLICK_NODE, probe.fixed_pipeline()[probe.ENTRY]["next"])
        self.assertEqual([name for name, node in pipeline.items() if node["action"] == "Click"], [probe.CLICK_NODE])

    def test_only_actual_successful_trace_can_pass(self):
        def node(name, action="DoNothing"):
            return {"name": name, "completed": True, "recognition_hit": True, "action_success": True, "action": action}
        trace = [node(probe.ENTRY), node(probe.CLICK_NODE, "Click")]
        self.assertTrue(probe.interpret_trace(trace, True, True)["passed"])
        self.assertFalse(probe.interpret_trace(trace, False, True)["passed"])
        self.assertFalse(probe.interpret_trace([trace[0], {**trace[1], "recognition_hit": False}], True, True)["passed"])
        for suffix in ("Unknown", "Ambiguous", "Error"):
            self.assertFalse(probe.interpret_trace([trace[0], node(probe.PREFIX + suffix, "StopTask")], True, True)["passed"])


class BrowserDirectInputTests(unittest.TestCase):
    def test_exact_mouse_messages_only_and_one_attempt(self):
        report = {"input_attempts": 0, "transport": "direct-postmessage"}
        with patch.object(probe, "native_post_message", return_value=True) as sender:
            self.assertTrue(probe.inject_background_click(TARGET, report))
        self.assertEqual(report["input_attempts"], 1)
        self.assertTrue(report["input_queue_succeeded"])
        self.assertFalse(report["input_partial"])
        self.assertEqual([call.args for call in sender.call_args_list], [(13, 0x200, 0, 641 | (461 << 16)), (13, 0x201, 1, 641 | (461 << 16)), (13, 0x202, 0, 641 | (461 << 16))])
        self.assertNotIn("postcondition", report)

    def test_move_or_down_failure_stops_without_retry_or_partial_press(self):
        for responses in ([False], [True, False]):
            report = {"input_attempts": 0}
            with self.subTest(responses=responses), patch.object(probe, "native_post_message", side_effect=responses) as sender:
                self.assertFalse(probe.inject_direct_postmessage(TARGET, report))
                self.assertEqual(sender.call_count, len(responses))
            self.assertEqual(report["input_attempts"], 1)
            self.assertFalse(report["input_partial"])
            self.assertFalse(report["input_succeeded"])

    def test_release_failure_records_partial_without_repeating_any_message(self):
        report = {"input_attempts": 0}
        with patch.object(probe, "native_post_message", side_effect=[True, True, False]) as sender:
            self.assertFalse(probe.inject_direct_postmessage(TARGET, report))
        self.assertEqual(sender.call_count, 3)
        self.assertEqual(report["input_attempts"], 1)
        self.assertTrue(report["input_partial"])
        self.assertFalse(report["input_queue_succeeded"])

    def test_release_exception_records_partial_and_stops(self):
        report = {"input_attempts": 0}
        with patch.object(probe, "native_post_message", side_effect=[True, True, OSError("denied")]) as sender:
            self.assertFalse(probe.inject_direct_postmessage(TARGET, report))
        self.assertTrue(report["input_partial"])
        self.assertEqual(report["input_error"], "denied")
        self.assertEqual(sender.call_count, 3)


class BrowserCompositionInputTests(unittest.TestCase):
    def session(self):
        return {"version": 1, "host": "127.0.0.1", "port": 1234, "token": "a" * 64,
                "pid": 123, "input_enabled": True, "browser_demo_only": True,
                "transport": "webview2-composition"}

    def test_input_session_requires_owned_pid_local_endpoint_and_explicit_enable(self):
        session = self.session()
        self.assertEqual(probe.validate_local_input_session(session, 123), session)
        for update in ({"pid": 124}, {"pid": True}, {"host": "example.com"}, {"port": True},
                       {"port": 0}, {"input_enabled": False}, {"browser_demo_only": False},
                       {"transport": "direct-postmessage"}, {"token": "short"}, {"extra": "click"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                probe.validate_local_input_session({**session, **update}, 123)

    def test_visual_target_requires_same_process_calibrated_viewport_and_enable(self):
        status = {**STATUS, "composition_demo": True, "local_input_enabled": True}
        target = probe.choose_composition_target(TOP, [VIEWPORT], status)
        self.assertEqual(target.viewport, VIEWPORT)
        for change in ({"local_input_enabled": False}, {"composition_demo": False}, {"source_capture_width": None}, {"source_capture_width": 1920}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                probe.choose_composition_target(TOP, [VIEWPORT], {**status, **change})
        for viewport in (replace(VIEWPORT, pid=124), replace(VIEWPORT, origin=(30, 90)), replace(VIEWPORT, visible=False)):
            with self.subTest(viewport=viewport), self.assertRaises(ValueError):
                probe.choose_composition_target(TOP, [viewport], status)

    def test_fixed_authenticated_command_is_sent_once_and_response_is_not_a_postcondition(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(self.session()), encoding="utf-8")
            report = {"input_attempts": 0, "input_session_path": str(path), "transport": "webview2-composition"}
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            stream = io.BytesIO(b'{"ok":true}\n')
            connection.makefile.return_value = stream
            with patch.object(probe.socket, "create_connection", return_value=connection):
                self.assertTrue(probe.inject_background_click(TARGET, report))
            connection.sendall.assert_called_once()
            request = json.loads(connection.sendall.call_args.args[0])
            self.assertEqual(set(request), {"token", "command"})
            self.assertEqual(request["command"], "claim-demo")
            self.assertEqual(report["input_attempts"], 1)
            self.assertNotIn("postcondition", report)


class BrowserPostconditionTests(unittest.TestCase):
    def test_ready_then_complete_uses_only_fresh_readonly_observations(self):
        controller = object()
        with patch.object(probe, "run_pipeline", side_effect=[result("ready"), result("complete")]) as pipeline, patch.object(probe.time, "sleep"):
            outcome = probe.verify_postcondition(controller)
        self.assertTrue(outcome["passed"])
        self.assertEqual(len(outcome["observations"]), 2)
        self.assertEqual([call.args for call in pipeline.call_args_list], [(controller,), (controller,)])

    def test_unknown_or_ambiguous_stops_immediately(self):
        for state in ("unknown", "ambiguous", "error"):
            with self.subTest(state=state), patch.object(probe, "run_pipeline", return_value=result(state, False)) as pipeline, patch.object(probe.time, "sleep") as sleep:
                outcome = probe.verify_postcondition(object())
            self.assertFalse(outcome["passed"])
            self.assertEqual(pipeline.call_count, 1)
            sleep.assert_not_called()

    def test_ready_is_limited_to_three_observations(self):
        with patch.object(probe, "run_pipeline", return_value=result("ready")) as pipeline, patch.object(probe.time, "sleep"):
            outcome = probe.verify_postcondition(object())
        self.assertFalse(outcome["passed"])
        self.assertEqual(len(outcome["observations"]), 3)
        self.assertEqual(pipeline.call_count, 3)

    def test_overrun_rejects_even_late_complete_without_another_call(self):
        overdue = probe.POSTCONDITION_SECONDS + 0.1
        with patch.object(probe, "run_pipeline", return_value=result("complete")) as pipeline, patch.object(probe.time, "monotonic", side_effect=[0, 0, overdue, overdue]), patch.object(probe.time, "sleep") as sleep:
            outcome = probe.verify_postcondition(object())
        self.assertFalse(outcome["passed"])
        self.assertEqual(outcome["reason"], "postcondition_time_budget_exceeded")
        self.assertEqual(pipeline.call_count, 1)
        sleep.assert_not_called()


class BrowserInputExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.bridge = Mock(session={"pid": 123})
        self.bridge.status.return_value = STATUS
        self.inspector = Mock()
        self.inspector.desktop_state.return_value = dict(DESKTOP)
        self.controller = types.SimpleNamespace(frame_paths=[], click=None)
        self.report = {"input_attempts": 0, "pipeline_click_attempts": 0, "input_succeeded": False}

    @contextlib.contextmanager
    def setup_runtime(self, pipeline):
        with patch.object(probe, "discover_identity", return_value=TOP), patch.object(probe, "resolve_target", return_value=TARGET), patch.object(probe, "create_screenshot_controller", return_value=self.controller), patch.object(probe, "initialize"), patch.object(probe, "offline_replay", return_value=[{"passed": True}]), patch.object(probe, "run_pipeline", side_effect=pipeline), patch.object(probe, "inject_background_click") as inject:
            yield inject

    def test_default_ready_observes_without_input_or_child_selection(self):
        with self.setup_runtime(lambda *args, **kwargs: result("ready")) as inject, patch.object(probe, "resolve_target") as resolve:
            self.assertTrue(probe.execute_probe(self.bridge, self.inspector, self.path, self.report))
            inject.assert_not_called()
            resolve.assert_not_called()
        self.assertEqual(self.report["input_attempts"], 0)
        self.assertTrue(self.report["focus_unchanged"])
        self.assertTrue(self.report["cursor_unchanged"])

    def test_unknown_ambiguous_complete_refuse_execution(self):
        for state in ("unknown", "ambiguous", "complete"):
            with self.subTest(state=state), self.setup_runtime(lambda *args, **kwargs: result(state, state == "complete")) as inject:
                self.assertFalse(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
                inject.assert_not_called()

    def test_non_demo_refused_before_window_discovery(self):
        self.bridge.status.return_value = {**STATUS, "browser_demo": False}
        with patch.object(probe, "discover_identity") as discovery, self.assertRaises(ValueError):
            probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True)
        discovery.assert_not_called()

    def test_bad_capture_dimensions_stop_before_any_click(self):
        def pipeline(*args, **kwargs):
            raise ValueError("截图尺寸必须为1280x720")
        with self.setup_runtime(pipeline) as inject, self.assertRaises(ValueError):
            probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True)
        inject.assert_not_called()

    def test_success_requires_real_attempt_and_verified_postcondition(self):
        def pipeline(controller, execute=False):
            if execute:
                self.assertTrue(controller.click(640, 460))
                return result("clicked")
            return result("complete" if self.report["input_attempts"] else "ready")
        def inject_real(target, report):
            report.update(input_attempts=report["input_attempts"] + 1, input_succeeded=True)
            return True
        with self.setup_runtime(pipeline) as inject:
            inject.side_effect = inject_real
            self.assertTrue(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
        self.assertEqual(self.report["input_attempts"], 1)
        self.assertEqual(self.report["pipeline_click_attempts"], 1)

    def test_pending_postcondition_observations_never_repeat_click(self):
        states = iter(("ready", "ready", "ready"))
        def pipeline(controller, execute=False):
            if execute:
                self.assertTrue(controller.click(640, 460))
                return result("clicked")
            return result(next(states) if self.report["input_attempts"] else "ready")
        def click(target, report):
            report.update(input_attempts=report["input_attempts"] + 1, input_succeeded=True)
            return True
        with self.setup_runtime(pipeline) as inject, patch.object(probe.time, "sleep"):
            inject.side_effect = click
            self.assertFalse(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
            inject.assert_called_once()
        self.assertEqual(self.report["input_attempts"], 1)
        self.assertEqual(self.report["pipeline_click_attempts"], 1)
        self.assertEqual(len(self.report["postcondition_observations"]), 3)

    def test_click_failure_cannot_retry_or_pass(self):
        def pipeline(controller, execute=False):
            if execute:
                self.assertFalse(controller.click(640, 460))
                self.assertFalse(controller.click(640, 460))
                return result("error", False)
            return result("ready")
        def failed_click(target, report):
            report["input_attempts"] += 1
            return False
        with self.setup_runtime(pipeline) as inject:
            inject.side_effect = failed_click
            self.assertFalse(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
            inject.assert_called_once()
        self.assertEqual(self.report["input_attempts"], 1)

    def test_external_desktop_change_before_input_does_not_block_click(self):
        changed = {**DESKTOP, "cursor": [201, 300]}
        self.inspector.desktop_state.side_effect = [DESKTOP, changed, changed, changed]
        def pipeline(controller, execute=False):
            if execute:
                self.assertTrue(controller.click(640, 460))
                return result("clicked")
            return result("complete" if self.report["input_attempts"] else "ready")
        def click(target, report):
            report.update(input_attempts=report["input_attempts"] + 1, input_succeeded=True)
            return True
        with self.setup_runtime(pipeline) as inject:
            inject.side_effect = click
            self.assertTrue(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
            inject.assert_called_once()
        self.assertTrue(self.report["input_cursor_unchanged"])
        self.assertTrue(self.report["desktop_changed"])

    def test_readonly_external_desktop_change_is_information_only(self):
        self.inspector.desktop_state.side_effect = [DESKTOP, {**DESKTOP, "foreground_hwnd": 1000}]
        with self.setup_runtime(lambda *args, **kwargs: result("ready")) as inject:
            self.assertTrue(probe.execute_probe(self.bridge, self.inspector, self.path, self.report))
        inject.assert_not_called()
        self.assertFalse(self.report["focus_unchanged"])
        self.assertEqual(self.report["input_attempts"], 0)

    def test_geometry_change_still_refuses_input(self):
        changed = replace(TARGET, renderer=replace(RENDERER, origin=(640, 500)))
        def pipeline(controller, execute=False):
            if execute:
                self.assertFalse(controller.click(640, 460))
                return result("error", False)
            return result("ready")
        with self.setup_runtime(pipeline) as inject, patch.object(probe, "resolve_target", side_effect=[TARGET, changed]), patch.object(probe.time, "sleep"):
            self.assertFalse(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
        inject.assert_not_called()
        self.assertEqual(self.report["input_attempts"], 0)

    def test_desktop_change_during_injection_fails_without_repeat_click(self):
        changed = {**DESKTOP, "cursor": [201, 300], "foreground_hwnd": 1000}
        self.inspector.desktop_state.side_effect = [DESKTOP, DESKTOP, changed, changed]
        def pipeline(controller, execute=False):
            if execute:
                self.assertFalse(controller.click(640, 460))
                return result("error", False)
            return result("complete" if self.report["input_attempts"] else "ready")
        def click(target, report):
            report.update(input_attempts=report["input_attempts"] + 1, input_succeeded=True)
            return True
        with self.setup_runtime(pipeline) as inject:
            inject.side_effect = click
            self.assertFalse(probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True))
            inject.assert_called_once()
        self.assertEqual(self.report["input_attempts"], 1)
        self.assertFalse(self.report["input_focus_unchanged"])
        self.assertFalse(self.report["input_cursor_unchanged"])
        self.assertEqual(self.report["reason"], "input_compatibility_uncertain")

    def test_input_desktop_read_failure_cannot_replace_primary_injection_error(self):
        report = {"input_attempts": 0}
        self.inspector.desktop_state.side_effect = [DESKTOP, OSError("desktop metadata failure")]
        with patch.object(probe, "inject_background_click", side_effect=RuntimeError("primary injection failure")), self.assertRaisesRegex(RuntimeError, "primary injection failure"):
            probe.inject_with_desktop_observation(TARGET, self.inspector, report)
        self.assertEqual(report["input_desktop_after_error"], "desktop metadata failure")

    def test_desktop_change_cannot_replace_primary_identity_error(self):
        self.inspector.desktop_state.side_effect = [DESKTOP, {**DESKTOP, "foreground_hwnd": 1000}]
        with self.setup_runtime(lambda *args, **kwargs: result("ready")), patch.object(probe, "resolve_target", side_effect=ValueError("BrowserDemo标题与会话PID绑定失败")), self.assertRaisesRegex(ValueError, "会话PID绑定失败"):
            probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True)
        self.assertTrue(self.report["desktop_changed"])
        self.assertIn("不能归因为工具造成", self.report["desktop_changes"])
        self.assertEqual(self.report["input_attempts"], 0)

    def test_offline_replay_failure_prevents_input(self):
        with self.setup_runtime(lambda *args, **kwargs: result("ready")) as inject, patch.object(probe, "offline_replay", side_effect=RuntimeError("离线回放失败")), self.assertRaises(RuntimeError):
            probe.execute_probe(self.bridge, self.inspector, self.path, self.report, True)
        inject.assert_not_called()

    def test_cli_has_no_arbitrary_window_coordinate_or_pipeline_option(self):
        for option in ("--title", "--hwnd", "--pid", "--x", "--template", "--pipeline"):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                probe.main(["--session", "unused.json", option, "anything"])

    def test_wait_cannot_hide_invalid_session_or_non_demo(self):
        with patch.object(probe.BrowserBridge, "from_file", side_effect=ValueError("invalid")), patch.object(probe.time, "sleep") as sleep, self.assertRaises(ValueError):
            probe.wait_for_demo(self.path / "unused.json", 20)
        sleep.assert_not_called()
        self.bridge.status.return_value = {**STATUS, "browser_demo": False}
        with patch.object(probe.BrowserBridge, "from_file", return_value=self.bridge), patch.object(probe.time, "sleep") as sleep, self.assertRaises(ValueError):
            probe.wait_for_demo(self.path / "unused.json", 20)
        sleep.assert_not_called()

    def test_report_cannot_escape_private_root_or_overwrite_session(self):
        private = self.path / "dev_assets/private"
        private.mkdir(parents=True)
        session = private / "session.json"
        session.write_text("{}", encoding="utf-8")
        with patch.object(probe, "ROOT", self.path):
            self.assertEqual(probe.private_report_path(private / "report.json", session), (private / "report.json").resolve())
            for target in (session, private / "../../outside.json", private / "report.txt"):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    probe.private_report_path(target, session)

    def test_wait_time_is_bounded_and_finite(self):
        for value in ("-1", "21", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                probe.main(["--session", "unused.json", "--wait-seconds", value])


if __name__ == "__main__":
    unittest.main()

import io
import contextlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.core.run_control import PRIVATE, RunControl
from agent.core import webview_controller as wc
from agent.core.task_actions import ReportSink
from tools import run_tasks as launcher
from tests.test_webview_controller import FakeBridge, FakeImage, POLICY_ON, TABLE, pipeline_session, daily_session


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Job:
    job_id = 1
    def __init__(self, done=False): self.done, self.succeeded = done, True
    def get(self): return None


class Tasker:
    def __init__(self, *, on_post=None, on_stop=None, completed=False):
        self.on_post, self.on_stop, self.completed = on_post, on_stop, completed
        self.posted, self.stop_calls = [], 0
        self.running = self.stopping = False
        self.job = None
    def post_task(self, entry, override):
        self.posted.append(entry)
        self.job = Job(self.completed)
        self.running = not self.completed
        if self.on_post: self.on_post()
        return self.job
    def post_stop(self):
        self.stop_calls += 1
        if self.on_stop: return self.on_stop()
        self.running = self.stopping = False
        self.job.done = True
        return Job(True)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        PRIVATE.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="_runner_control_", dir=PRIVATE)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.control = RunControl(events_path=self.root / "events.jsonl", stop_file=self.root / "run.stop")
        self.addCleanup(self.control.close)
        self.clock = FakeClock()
        self.plans = [launcher.TaskPlan(name, "SC." + name + ".Entry", {}) for name in ("A", "B")]

    def execute(self, tasker, **kwargs):
        return launcher.execute_plan(self.plans, tasker, control=self.control,
                                     clock=self.clock, sleep=self.clock.sleep, stop_timeout_s=.1, **kwargs)

    def test_preexisting_stop_skips_all_without_before_task_or_submit(self):
        self.control.stop_file.touch()
        tasker = Tasker()
        before = []
        results = self.execute(tasker, before_task=lambda p: before.append(p.name))
        self.assertEqual([r["status"] for r in results], ["skipped", "skipped"])
        self.assertEqual((tasker.posted, tasker.stop_calls, before), ([], 0, []))

    def test_before_task_creates_stop_and_cannot_reenable_by_removal(self):
        tasker = Tasker()
        def before(plan): self.control.stop_file.touch()
        results = self.execute(tasker, before_task=before)
        self.control.stop_file.unlink()
        self.assertEqual(tasker.posted, [])
        self.assertEqual([r["status"] for r in results], ["skipped", "skipped"])
        self.assertTrue(self.control.poll())

    def test_post_task_stop_calls_post_stop_once_and_preserves_uncertain_report(self):
        sink = ReportSink(on_report=self.control.report)
        tasker = Tasker(on_post=self.control.stop_file.touch)
        def after(plan): sink.add("training", {"outcome": "unknown", "log_state": "uncertain"})
        results = self.execute(tasker, sink=sink, after_task=after)
        self.assertEqual([r["status"] for r in results], ["interrupted", "skipped"])
        self.assertEqual((tasker.posted, tasker.stop_calls), (["SC.A.Entry"], 1))
        self.assertEqual(results[0]["reports"][0]["log_state"], "uncertain")
        self.assertTrue(self.control.stop_verified)

    def test_stop_job_done_requires_native_idle_and_callback_idle(self):
        tasker = Tasker(on_post=self.control.request_stop)
        tasker.on_stop = lambda: Job(True)
        sleeps = []
        def sleep(seconds):
            self.clock.sleep(seconds)
            sleeps.append(seconds)
            if len(sleeps) == 2:
                tasker.running = False
                tasker.job.done = True
        idle = lambda: len(sleeps) >= 3
        results = launcher.execute_plan(self.plans, tasker, control=self.control, clock=self.clock,
                                       sleep=sleep, stop_timeout_s=.1, input_idle=idle)
        self.assertGreaterEqual(len(sleeps), 3)
        self.assertTrue(self.control.stop_verified)
        self.assertEqual(results[1]["status"], "skipped")

    def test_stop_timeout_preserves_incomplete_reports_and_never_flushes_runtime(self):
        tasker = Tasker(on_post=self.control.request_stop, on_stop=lambda: Job(True))
        flushed = []
        results = self.execute(tasker, after_task=lambda p: flushed.append(p.name))
        self.assertFalse(self.control.stop_verified)
        self.assertTrue(results[0]["reports_incomplete"])
        self.assertEqual((flushed, tasker.stop_calls, tasker.posted), ([], 1, ["SC.A.Entry"]))
        self.assertLessEqual(self.clock.now, .12)

    def test_invalid_stop_handle_and_status_exception_never_retry(self):
        for stop in (lambda: SimpleNamespace(job_id=0), lambda: (_ for _ in ()).throw(RuntimeError("secret"))):
            with self.subTest(stop=stop):
                control = RunControl()
                tasker = Tasker(on_post=control.request_stop, on_stop=stop)
                results = launcher.execute_plan(self.plans, tasker, control=control, clock=self.clock,
                                               sleep=self.clock.sleep, stop_timeout_s=.1)
                self.assertFalse(control.stop_verified)
                self.assertEqual((tasker.stop_calls, results[1]["status"]), (1, "skipped"))

    def test_timeout_unconfirmed_does_not_claim_stopped_or_continue(self):
        tasker = Tasker(on_stop=lambda: Job(True))
        results = self.execute(tasker, timeout_s=.01)
        self.assertEqual([r["status"] for r in results], ["timeout", "skipped"])
        self.assertIn("停止未确认", results[0]["error"])
        self.assertEqual(tasker.stop_calls, 1)

    def test_ordinary_failure_still_continues_with_control(self):
        tasker = Tasker(completed=True)
        def post(): tasker.job.succeeded = False
        tasker.on_post = post
        results = self.execute(tasker)
        self.assertEqual([r["status"] for r in results], ["failed", "failed"])
        self.assertEqual(len(tasker.posted), 2)
        self.assertFalse(self.control.poll())

    def test_stop_during_final_report_and_final_event_cleans_up(self):
        for phase in ("readings", "task.finished"):
            with self.subTest(phase=phase):
                control = RunControl()
                old = control.emit
                def emit(event, **fields):
                    if event == phase: control.request_stop("event_log_unavailable")
                    return old(event, **fields)
                control.emit = emit
                cleaned = []
                tasker = Tasker(completed=True)
                sink = ReportSink(on_report=control.report)
                results = launcher.execute_plan(self.plans[:1], tasker, control=control, sink=sink,
                    after_task=lambda p: sink.add("training", {"log_state": "uncertain"}),
                    on_stop=lambda: cleaned.append(True), clock=self.clock, sleep=self.clock.sleep)
                self.assertTrue(cleaned)
                self.assertEqual(tasker.stop_calls, 1)
                self.assertEqual(results[0]["reports"][0]["log_state"], "uncertain")

    def test_run_preexisting_stop_does_not_construct_controller_or_load_frames(self):
        self.control.stop_file.touch()
        with patch.object(launcher, "build_tasker", side_effect=AssertionError("no tasker")), \
             patch.object(launcher, "load_frames", side_effect=AssertionError("no frames")), \
             patch.object(launcher, "create_replay_controller", side_effect=AssertionError("no controller")):
            summary = launcher.run({"tasks": ["Startup", "MissionStatus"]}, offline_steps=[],
                policy={"live_input_enabled": False}, summary_path=False, control=self.control)
        self.assertEqual(summary["controller"], "not_connected")
        self.assertTrue(summary["run_control"]["stop_verified"])
        self.assertEqual(summary["real_input_attempts"], 0)

    def test_cli_reports_final_event_failure_even_after_all_tasks_succeeded(self):
        summary = {"order": ["Startup"], "input_mode": "read_only", "tasks": [], "failed": 0,
                   "fes_allocation": {"status": "idle"},
                   "run_control": {"stop_requested": True, "events_complete": False}}
        output = io.StringIO()
        with patch.object(launcher, "run", return_value=summary), contextlib.redirect_stdout(output):
            status = launcher.main(["--tasks", "Startup"])
        self.assertEqual(status, 1)
        self.assertTrue(json.loads(output.getvalue())["run_control"]["stop_requested"])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.control = RunControl()
        self.sent, self.audit = [], []
        self.clock = FakeClock()

    def gate(self, pipeline=False):
        config = wc.RunConfig(True, frozenset(TABLE.families if pipeline else {"daily"}))
        endpoints = {"pipeline": wc.Endpoint("pipeline", pipeline_session())} if pipeline else \
                    {"daily": wc.Endpoint("daily", daily_session())}
        gate = wc.WebViewInputGate(FakeBridge(), config, endpoints=endpoints,
            audit=SimpleNamespace(append=self.audit.append), send=self.send, policy=POLICY_ON,
            clock=self.clock, decode=lambda blob: FakeImage(), frame_guard_enabled=pipeline,
            run_control=self.control)
        gate.begin_task("Startup")
        gate.screencap()
        return gate

    def send(self, session, request):
        self.sent.append(request)
        return {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}

    def test_stop_before_click_and_during_intent_send_nothing(self):
        for moment in ("before", "intent"):
            with self.subTest(moment=moment):
                self.control = RunControl()
                gate = self.gate()
                old = gate.audit.append
                def append(row):
                    old(row)
                    if row.get("result") == "intent": self.control.request_stop()
                if moment == "intent": gate.audit.append = append
                else: self.control.request_stop()
                self.assertFalse(gate.click(62, 429))
                self.assertEqual((self.sent, gate.run_clicks, gate.task_clicks), ([], 0, 0))
                self.assertIsNone(gate._frame)
                self.assertEqual(gate.history[-1]["reason"], "run_stopped")

    def test_stop_in_final_binding_check_is_caught_before_send(self):
        gate = self.gate(pipeline=True)
        action = TABLE.at((62, 429), gate.config.families)[0]
        token = gate.frame_permits.issue(action.nodes[0], action.id, 1, 2, FakeImage(), gate._frame)
        self.assertTrue(gate.frame_permits.arm(token, 1, 2, action.nodes[0]))
        checks = []
        class Binding:
            def allows_click(binding, permit):
                checks.append(True)
                if len(checks) == 2: self.control.request_stop()
                return True
            def reject_click(binding, reason): pass
            def discard_click(binding, reason): pass
        gate.frame_binding = Binding()
        self.assertFalse(gate.click(62, 429))
        self.assertEqual((len(checks), self.sent, gate.run_clicks), (2, [], 0))

    def test_stop_inside_send_retains_sent_or_unknown_and_attempt_count(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                self.control = RunControl()
                gate = self.gate()
                def send(session, request):
                    self.sent.append(request)
                    self.control.request_stop()
                    if fails: raise TimeoutError("unknown effect")
                    return {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}
                gate.send = send
                self.assertEqual(gate.click(62, 429), not fails)
                self.assertEqual((gate.run_clicks, gate.history[-1]["result"]), (1, "unknown" if fails else "sent"))
                self.assertFalse(gate.click(62, 429))
                self.assertEqual(gate.run_clicks, 1)
        self.assertEqual(len(self.sent), 2)

    def test_event_failure_before_intent_blocks_send_after_send_preserves_outcome(self):
        for phase in ("intent", "sent"):
            with self.subTest(phase=phase):
                self.control = RunControl()
                def event(record):
                    if record.get("result") == phase:
                        self.control.request_stop("event_log_unavailable")
                        return False
                    return True
                self.control.input_event = event
                gate = self.gate()
                self.assertEqual(gate.click(62, 429), phase == "sent")
                self.assertEqual(gate.run_clicks, 0 if phase == "intent" else 1)
                if phase == "sent": self.assertEqual(gate.history[-1]["result"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_stop_revokes_pending_but_never_refunds_issued_grant(self):
        gate = self.gate(pipeline=True)
        action = next(iter(wc.PIPELINE_SPENDING_ACTIONS))
        gate.spend_gate = wc.SpendGate([action], clock=self.clock)
        gate.spend_gate.authorize(action, gate.latest_frame_sha256, "SC.Test.Authorize", capture_seq=1, frame_binding_seq=2)
        self.control.request_stop()
        self.assertTrue(gate.stop_input())
        self.assertIsNone(gate.spend_gate._pending)
        self.assertEqual(gate.spend_gate._issued[action], 1)
        self.assertFalse(wc.LiveSpendAuthority(gate).can_spend(action))

    def test_stop_during_authorize_revokes_new_grant_without_refunding(self):
        gate = self.gate(pipeline=True)
        action = next(iter(wc.PIPELINE_SPENDING_ACTIONS))
        gate.spend_gate = wc.SpendGate([action], clock=self.clock)
        original = gate.spend_gate.authorize
        def authorize(*args, **kwargs):
            result = original(*args, **kwargs)
            self.control.request_stop()
            return result
        gate.spend_gate.authorize = authorize
        self.assertEqual(wc.LiveSpendAuthority(gate).authorize(action, FakeImage(), "SC.Test.Authorize"), "run_stopped")
        self.assertIsNone(gate.spend_gate._pending)
        self.assertEqual(gate.spend_gate._issued[action], 1)

    def test_final_run_event_failure_cleans_permissions_and_reports_unconfirmed_stop(self):
        gate = self.gate()
        root = tempfile.TemporaryDirectory(prefix="_event_footer_", dir=PRIVATE)
        self.addCleanup(root.cleanup)
        control = RunControl(events_path=Path(root.name) / "events.jsonl", max_events=1)
        control.stop_cleanup = gate.stop_input
        summary = {"started_at": "2026-10-04T12:00:00+00:00", "tasks": [], "succeeded": 0, "failed": 0}
        with patch.object(launcher, "_run", return_value=summary):
            result = launcher.run({}, control=control, summary_path=False)
        self.assertIsNone(gate._frame)
        self.assertTrue(result["run_control"]["stop_requested"])
        self.assertFalse(result["run_control"]["stop_verified"])
        self.assertFalse(result["run_control"]["events_complete"])

    def test_nonblocking_cleanup_does_not_wait_on_input_callback(self):
        gate = self.gate()
        held, release = threading.Event(), threading.Event()
        def worker():
            with gate._input_lock:
                held.set()
                release.wait(2)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(held.wait(1))
            self.control.request_stop()
            self.assertFalse(gate.stop_input())
            self.assertFalse(gate.input_idle())
        finally:
            release.set()
            thread.join(2)
        self.assertTrue(gate.stop_input())
        self.assertTrue(gate.input_idle())

    def test_initial_stop_attaches_to_existing_gate_even_after_recapture(self):
        gate = self.gate()
        gate.run_control = None
        self.control.request_stop()
        summary = launcher.run({"tasks": ["Startup"]}, gate=gate, control=self.control,
                               policy={"live_input_enabled": False}, summary_path=False)
        self.assertIs(gate.run_control, self.control)
        gate.screencap()
        self.assertFalse(gate.click(62, 429))
        self.assertEqual((self.sent, summary["real_input_attempts"]), ([], 0))


try:
    from tests.test_pipeline_frame_guard import Harness, HAVE_MAA, NODE
except (ImportError, OSError):
    HAVE_MAA = False


@unittest.skipUnless(HAVE_MAA, "requires the installed MaaFramework v5.12.3 SDK")
class NativeRunStopTests(unittest.TestCase):
    def harness(self):
        from maa.define import LoggingLevelEnum
        from maa.tasker import Tasker as NativeTasker
        NativeTasker.set_stdout_level(LoggingLevelEnum.Off)
        NativeTasker.set_debug_mode(False)
        NativeTasker.set_save_on_error(False)
        NativeTasker.set_log_dir(PRIVATE / "sample-b6m-run-control-sdk-log")
        temp = tempfile.TemporaryDirectory(prefix="_native_run_stop_", dir=PRIVATE)
        self.addCleanup(temp.cleanup)
        harness = Harness(Path(temp.name))
        self.addCleanup(harness.tasker.clear_sinks)
        self.addCleanup(harness.tasker.clear_context_sinks)
        control = RunControl(events_path=Path(temp.name) / "events.jsonl")
        self.addCleanup(control.close)
        harness.gate.run_control = control
        control.attach(harness.tasker)
        return harness, control

    def test_actual_maa_intent_stop_skips_next_task_without_a_send(self):
        h, control = self.harness()
        h.on_intent(control.request_stop)
        plans = [launcher.TaskPlan("A", NODE, {}), launcher.TaskPlan("B", NODE, {})]
        results = launcher.execute_plan(plans, h.tasker, control=control, on_stop=h.gate.stop_input,
                                       input_idle=h.gate.input_idle, stop_timeout_s=2)
        self.assertTrue(h.injected)
        self.assertEqual([r["status"] for r in results], ["interrupted", "skipped"])
        self.assertTrue(control.stop_verified, results)
        self.assertEqual((h.sent, h.gate.run_clicks), ([], 0))
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertFalse(h.tasker.running or h.tasker.stopping)
        events = [json.loads(line) for line in control.events_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(row["event"] == "node" for row in events))
        self.assertEqual([row["task"] for row in events if row["event"] == "task.started"], ["A"])

    def test_actual_maa_run_waits_for_blocked_final_audit(self):
        from concurrent.futures import ThreadPoolExecutor
        h, control = self.harness()
        entered, release = threading.Event(), threading.Event()
        original = h.audit.append
        def audit(row):
            original(row)
            if row.get("result") == "intent": control.request_stop()
            if row.get("result") == "denied" and row.get("reason") == "run_stopped":
                entered.set()
                if not release.wait(3): raise AssertionError("blocked audit was not released")
        h.audit.append = audit
        plans = [launcher.TaskPlan("A", NODE, {}), launcher.TaskPlan("B", NODE, {})]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(launcher.execute_plan, plans, h.tasker, control=control,
                                 on_stop=h.gate.stop_input, input_idle=h.gate.input_idle, stop_timeout_s=2)
            try:
                self.assertTrue(entered.wait(2), h.audit.rows)
                self.assertFalse(future.done(), "native terminal cannot bypass final input audit")
            finally:
                release.set()
            results = future.result(timeout=5)
        self.assertTrue(control.stop_verified)
        self.assertEqual((h.sent, h.gate.run_clicks, results[1]["status"]), ([], 0, "skipped"))
        self.assertEqual(h.gate.history[-1]["reason"], "run_stopped")


if __name__ == "__main__":
    unittest.main()

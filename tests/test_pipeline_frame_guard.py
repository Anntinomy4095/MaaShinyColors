"""Real Maa 5.12.3 recognition/action lifecycle, with synthetic frames and a fake host.

No token is issued/armed by tests, no host is opened and no network/input API is used.
After each negative, Maa evaluates the click node again in the SAME task: restoring
the original definition or removing an interference must never produce a late send.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from agent.core import pipeline_actions as pa, pipeline_frame_guard as fg, webview_controller as wc
from tests.test_webview_controller import FakeBridge, POLICY_ON, pipeline_session

try:
    from maa.context import ContextEventSink
    from maa.define import LoggingLevelEnum
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker
    HAVE_MAA = Library.version() == "v5.12.3"
except (ImportError, OSError, RuntimeError):
    HAVE_MAA = False

PRIVATE = Path(__file__).resolve().parents[1] / "dev_assets/private"
NODE, GUARD, RETRY, END = "SC.Probe.Click", "SC.Probe.Guard", "SC.Probe.Retry", "SC.Probe.End"
RECT = [100, 100, 40, 40]
TABLE = pa.parse_table(json.dumps({
    "version": 1, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4,
    "families": ["popup"], "actions": [{"id": "probe-click", "family": "popup", "consuming": False,
        "rect": RECT, "source": "synthetic lifecycle test", "nodes": [NODE]}],
}).encode())
PIXELS = np.full((720, 1280, 3), [15, 10, 5], dtype=np.uint8)
_buffer = io.BytesIO()
Image.new("RGB", (1280, 720), (5, 10, 15)).save(_buffer, format="PNG")
PNG = _buffer.getvalue()
SHA = hashlib.sha256(PNG).hexdigest()


class MemoryAudit:
    def __init__(self): self.rows = []
    def append(self, row): self.rows.append(copy.deepcopy(row)); return True


def input_recognition(spec):
    """Translate an SDK normalized snapshot back into the public input spelling."""
    spec = copy.deepcopy(spec)
    spec.pop("sub_name", None)
    for key in ("all_of", "any_of"):
        if key in spec.get("param", {}):
            spec["param"][key] = [part if isinstance(part, str) else
                {"recognition": input_recognition(part)} for part in spec["param"][key]]
    return spec


class Harness:
    def __init__(self, directory, *, algorithm="And", fail=False, repoll=False, pre_wait=False):
        self.sent, self.events, self.errors = [], [], []
        self.hook = None
        self.injected = False
        self.stop_job = None
        self.intent_done = None
        self.audit = MemoryAudit()
        self.bridge = FakeBridge(png=PNG)
        config = wc.RunConfig(True, frozenset({"popup"}))
        endpoint = wc.Endpoint("pipeline", pipeline_session(TABLE))
        self.gate = wc.WebViewInputGate(self.bridge, config, {"pipeline": endpoint}, audit=self.audit,
            send=self.send, policy=POLICY_ON, decode=lambda blob: PIXELS.copy(), action_table=TABLE,
            frame_guard_enabled=True)
        self.gate.begin_task("synthetic lifecycle")
        nodes = {
            GUARD: {"recognition": "ColorMatch", "roi": [0, 0, 10, 10], "count": 100,
                "method": 4,
                "lower": [250, 250, 250] if fail else [5, 10, 15],
                "upper": [255, 255, 255] if fail else [5, 10, 15]},
            NODE: {"recognition": algorithm, "all_of" if algorithm == "And" else "any_of":
                [GUARD] if algorithm == "And" else ["SC.Probe.False", GUARD],
                "action": "Click", "target": RECT, "next": [RETRY if repoll else END],
                "on_error": [RETRY], "timeout": 160, "rate_limit": 10,
                "pre_delay": 0, "post_delay": 0, "focus": False},
            "SC.Probe.False": {"recognition": "ColorMatch", "roi": [0, 0, 10, 10], "count": 100,
                "method": 4,
                "lower": [250, 250, 250], "upper": [255, 255, 255]},
            RETRY: {"action": "DoNothing", "max_hit": 1, "next": [NODE, END], "on_error": [END],
                "timeout": 160, "rate_limit": 10, "pre_delay": 0, "post_delay": 0},
            END: {"action": "DoNothing", "pre_delay": 0, "post_delay": 0},
        }
        if pre_wait:
            nodes[NODE]["pre_wait_freezes"] = {"time": 20, "timeout": 100, "rate_limit": 10}
        pipeline = directory / "pipeline"
        pipeline.mkdir()
        (pipeline / "probe.json").write_text(json.dumps(nodes), encoding="utf-8")
        self.resource = Resource()
        assert self.resource.post_bundle(directory).wait().succeeded
        self.binder = fg.install(self.resource, self.gate)
        self.terminal = threading.Condition()
        self.finished = set()
        task_event = self.binder.task_event

        def observed_task_event(message, details):
            task_event(message, details)
            if message in ("Tasker.Task.Succeeded", "Tasker.Task.Failed"):
                with self.terminal:
                    self.finished.add(details["task_id"])
                    self.terminal.notify_all()

        self.binder.task_event = observed_task_event
        self.original = self.resource.get_node_data(NODE)
        self.original_guard = self.resource.get_node_data(GUARD)
        self.controller = wc.create_webview_controller(self.gate)
        assert self.controller.set_screenshot_use_raw_size(True)
        assert self.controller.post_connection().wait().succeeded
        self.tasker = Tasker()
        assert self.tasker.bind(self.resource, self.controller)
        self.binder.attach(self.tasker)
        harness = self

        class Observer(ContextEventSink):
            def on_raw_notification(self, context, message, details):
                harness.events.append((message, copy.deepcopy(details)))
                try:
                    if harness.hook:
                        harness.hook(context, message, details)
                except Exception as exc:
                    harness.errors.append(exc)
                    harness.stop_job = context.tasker.post_stop()

        self.observer = Observer()
        assert self.tasker.add_context_sink(self.observer) is not None

    def send(self, session, request):
        self.sent.append(copy.deepcopy(request))
        return {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}

    def run(self):
        job = self.tasker.post_task(NODE)
        if job.job_id <= 0:
            raise AssertionError(f"Maa task submission refused: id={job.job_id}, stopping={self.tasker.stopping}")
        job.wait()
        # Native Job.wait may return just before the terminal sink; await that
        # actual callback rather than relying on sleeps or changing binder state.
        self.await_terminal(job)
        # A native stop can finish before the Python audit/controller callback.
        # Wait for the installed intent effect before reading stop_job or errors,
        # then for the whole gated attempt (including its final audit/history).
        if self.intent_done is not None and not self.intent_done.wait(5):
            raise AssertionError("Maa intent audit callback did not finish")
        if not self.gate._input_lock.acquire(timeout=2):
            raise AssertionError("Maa controller input callback did not finish")
        self.gate._input_lock.release()
        if self.stop_job is not None:
            self.stop_job.wait()
            self.await_terminal(self.stop_job)
            # SDK per-job completion is published before the runner clears its
            # running flag. A new post during that gap is rejected with id 0.
            self.await_stopped()
            self.stop_job = None
        if self.errors:
            raise self.errors[0]
        return job

    def await_terminal(self, job):
        with self.terminal:
            if not self.terminal.wait_for(lambda: job.job_id in self.finished, timeout=2):
                raise AssertionError(f"Maa task terminal callback missing: id={job.job_id}, "
                                     f"status={job.status}, stopping={self.tasker.stopping}, "
                                     f"finished={sorted(self.finished)}")

    def await_stopped(self):
        deadline = time.monotonic() + 2
        pause = threading.Event()
        while self.tasker.running or self.tasker.stopping:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("Maa stop job finished but native runner remained active")
            # The SDK exposes no idle event. Poll its actual flags with a bound;
            # do not retry a rejected post, sleep unconditionally or reset state.
            pause.wait(min(0.005, remaining))

    def starts(self):
        return sum(msg == "Node.Recognition.Starting" and detail.get("name") == NODE
                   for msg, detail in self.events)

    def restore(self, context):
        original = self.original
        assert context.override_pipeline({NODE: {"recognition": input_recognition(original["recognition"]),
            "action": original["action"], "repeat": original["repeat"], "inverse": original["inverse"]},
            GUARD: {"recognition": self.original_guard["recognition"]}})

    def once(self, event, effect):
        def hook(context, message, details):
            if message == event and details.get("name") == NODE and not self.injected:
                self.injected = True
                effect(context)
            elif message == "Node.Recognition.Starting" and details.get("name") == NODE and self.injected:
                self.restore(context)
        self.hook = hook

    def on_intent(self, effect):
        append = self.audit.append
        self.intent_done = threading.Event()
        def hooked_append(row):
            result = append(row)
            if row.get("result") == "intent" and not self.injected:
                self.injected = True
                try:
                    effect()
                except Exception as exc:
                    self.errors.append(exc)
                    raise
                finally:
                    self.intent_done.set()
            return result
        self.audit.append = hooked_append


@unittest.skipUnless(HAVE_MAA, "requires the installed MaaFramework v5.12.3 SDK")
class PipelineFrameGuardIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PRIVATE.mkdir(exist_ok=True)
        Tasker.set_debug_mode(False)
        Tasker.set_save_on_error(False)
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        Tasker.set_log_dir(PRIVATE / "sample-b6i-frameguard-sdk-log")

    def build(self, **options):
        temp = tempfile.TemporaryDirectory(prefix="sample-b6i-frameguard-", dir=PRIVATE)
        self.addCleanup(temp.cleanup)
        harness = Harness(Path(temp.name), **options)
        self.addCleanup(harness.tasker.clear_context_sinks)
        self.addCleanup(harness.tasker.clear_sinks)
        return harness

    def assert_blocked_with_repoll(self, harness):
        harness.run()
        self.assertEqual(harness.sent, [], harness.audit.rows)
        self.assertGreaterEqual(harness.starts(), 2, "the negative must include the next Maa evaluation")
        self.assertIsNone(harness.gate.frame_permits.take())
        self.assertIsNone(harness.binder.task_id)

    def assert_not_sent_then_fresh_retry(self, harness):
        """ANN-17: a click proven not sent (frame race before send) only drops that batch. The stale permit is never
        used; the repoll captures a fresh frame, re-runs the full parent recognition and may send exactly once."""
        harness.run()
        clicks = [e for e in harness.gate.history if e.get("kind") == "click"]
        self.assertEqual(clicks[0]["result"], "denied", harness.gate.history)
        intents = [r for r in harness.audit.rows if r.get("result") == "intent"]
        self.assertEqual(len(harness.sent), 1, harness.audit.rows)
        self.assertEqual(harness.sent[0]["nonce"], intents[-1]["nonce"])
        guards = [r["recognition_guard"]["reco_id"] for r in harness.audit.rows if r.get("recognition_guard")]
        self.assertGreaterEqual(len(set(guards)), 2, "the retry must carry a new recognition")
        self.assertGreaterEqual(harness.starts(), 2)
        self.assertTrue(any(r.get("kind") == "frame_guard" and r.get("result") == "retry" for r in harness.audit.rows))
        self.assertIsNone(harness.gate.frame_permits.take())

    def test_and_complete_parent_sends_once_and_task_end_clears_permit(self):
        h = self.build()
        self.assertTrue(h.run().succeeded)
        self.assertEqual([r["action"] for r in h.sent], ["probe-click"])
        proof = [r["recognition_guard"] for r in h.audit.rows if r.get("result") == "intent"]
        self.assertEqual(len(proof), 1)
        self.assertEqual(proof[0]["node"], NODE)
        self.assertGreater(proof[0]["reco_id"], 0)
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertIsNone(h.binder.task_id)
        h.controller.post_screencap().wait()
        self.assertFalse(h.controller.post_click(120, 120).wait().succeeded)
        self.assertEqual(len(h.sent), 1)

    def test_or_complete_parent_preserves_branches_and_sends_once(self):
        h = self.build(algorithm="Or")
        self.assertTrue(h.run().succeeded)
        self.assertEqual(len(h.sent), 1)
        parent = h.resource.get_node_data(NODE)["recognition"]
        self.assertEqual(parent["type"], "And")
        self.assertEqual(parent["param"]["all_of"][0]["type"], "Or")

    def test_native_parent_guard_failure_never_sends(self):
        self.assert_blocked_with_repoll(self.build(fail=True, repoll=True))

    def test_stop_during_intent_audit_never_sends(self):
        h = self.build()
        states = []
        def stop():
            owner_id = h.audit.rows[-1]["recognition_guard"]["task_id"]
            owner = h.tasker.get_task_detail(owner_id)
            self.assertIsNotNone(owner)
            self.assertTrue(owner.status.running)
            h.stop_job = h.tasker.post_stop()
            states.append(h.stop_job)
        h.on_intent(stop)
        h.run()
        self.assertEqual(len(states), 1, {"audit": h.audit.rows, "events": h.events})
        self.assertTrue(states[0].wait().succeeded)
        self.assertEqual(h.sent, [], h.audit.rows)
        self.assertEqual(h.gate.run_clicks, 0)
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertIsNone(h.binder.task_id)

    def test_stop_terminal_waits_for_intent_and_final_controller_audit(self):
        h = self.build()
        entered, release = threading.Event(), threading.Event()
        final_entered, final_release = threading.Event(), threading.Event()
        owner_ids = []

        def stop():
            owner_ids.append(h.audit.rows[-1]["recognition_guard"]["task_id"])
            h.stop_job = h.tasker.post_stop()
            entered.set()
            if not release.wait(3):
                raise AssertionError("controlled intent audit release missing")

        h.on_intent(stop)
        append = h.audit.append

        def held_final_audit(row):
            result = append(row)
            if row.get("kind") == "click" and row.get("result") == "denied":
                final_entered.set()
                if not final_release.wait(3):
                    raise AssertionError("controlled final audit release missing")
            return result

        h.audit.append = held_final_audit
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(h.run)
            completed = threading.Event()
            future.add_done_callback(lambda _: completed.set())
            try:
                self.assertTrue(entered.wait(2), h.audit.rows)
                with h.terminal:
                    self.assertTrue(h.terminal.wait_for(lambda: owner_ids[0] in h.finished, timeout=2))
                self.assertFalse(completed.wait(0.05), "native terminal must not bypass intent callback")
                release.set()
                self.assertTrue(final_entered.wait(2), h.audit.rows)
                self.assertTrue(h.intent_done.is_set())
                self.assertFalse(completed.wait(0.05), "intent completion must not bypass controller final audit")
            finally:
                release.set()
                final_release.set()
            future.result(timeout=5)
        self.assertEqual(h.sent, [], h.audit.rows)
        self.assertEqual(h.gate.run_clicks, 0)
        self.assertTrue(any(e.get("kind") == "click" and e.get("result") == "denied"
                            for e in h.gate.history), h.gate.history)
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertIsNone(h.binder.task_id)

    def test_same_pixel_capture_during_intent_audit_is_not_sent_and_retried_on_a_fresh_frame(self):
        h = self.build(repoll=True)
        h.on_intent(lambda: h.gate.screencap())
        self.assert_not_sent_then_fresh_retry(h)
        self.assertTrue(h.injected)
        self.assertEqual(h.gate.run_clicks, 1)

    def test_permit_expiring_during_intent_audit_is_not_sent_and_retried_on_a_fresh_frame(self):
        h = self.build(repoll=True)
        clock = [h.gate.clock()]
        h.gate.clock = h.gate.frame_permits.clock = lambda: clock[0]
        def expire():
            clock[0] += 6.0
        h.on_intent(expire)
        self.assert_not_sent_then_fresh_retry(h)
        self.assertTrue(h.injected)
        self.assertEqual(h.gate.run_clicks, 1)

    def test_slow_recognition_outliving_the_permit_ttl_retries_with_a_fresh_capture(self):
        """Regression (CardScan 9b): a slow custom recognition ahead of the guard must not poison the task."""
        h = self.build(repoll=True)
        clock = [h.gate.clock()]
        h.gate.clock = h.gate.frame_permits.clock = lambda: clock[0]
        h.once("Node.Recognition.Starting", lambda c: clock.__setitem__(0, clock[0] + 6.0))
        h.run()
        self.assertTrue(h.injected)
        reasons = [r.get("reason") for r in h.audit.rows if r.get("kind") == "frame_guard"]
        self.assertEqual(reasons, ["frame_stale_before_permit"])    # a retry, never a poison denial
        self.assertGreaterEqual(len(h.sent), 1)   # the retry on a fresh frame is permitted (repoll may send again)

    def test_same_pixel_capture_before_recognition_poison_survives_next_poll(self):
        h = self.build(repoll=True)
        h.once("Node.NextList.Starting", lambda c: c.tasker.controller.post_screencap().wait())
        self.assert_blocked_with_repoll(h)
        self.assertTrue(h.injected)

    def test_same_pixel_capture_after_full_parent_poison_survives_next_poll(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.tasker.controller.post_screencap().wait())
        self.assert_blocked_with_repoll(h)

    def test_host_same_sha_changed_sequences_before_status_is_not_sent_and_retried_on_a_fresh_frame(self):
        h = self.build(repoll=True)
        def external(context):
            h.bridge.status_value["capture_seq"] += 1
            h.bridge.status_value["frame_binding_seq"] += 1
            self.assertEqual(h.bridge.status_value["frame_sha256"], SHA)
        h.once("Node.Recognition.Succeeded", external)
        self.assert_not_sent_then_fresh_retry(h)

    def test_same_png_rebound_refusal_is_resent_only_with_a_fresh_capture(self):
        h = self.build(repoll=True)
        original_sequences = []
        def host(session, request):
            original_sequences.append((request["capture_seq"], request["frame_binding_seq"]))
            h.bridge.screencap()  # Another client races after Python's last status; PNG is byte-identical.
            status = h.bridge.status()
            self.assertEqual(request["frame_sha256"], status["frame_sha256"])
            self.assertNotEqual(original_sequences[-1], (status["capture_seq"], status["frame_binding_seq"]))
            h.sent.append(copy.deepcopy(request))
            return {"ok": False, "attempted": False, "reason": "frame_binding_guard_failed"}
        h.gate.send = host
        h.run()
        # ANN-17: the host refusal before any gesture is an explicit not_sent; the bounded repoll (RETRY max_hit 1)
        # re-recognises on a fresh capture and its request carries that capture's own sequences, never the first.
        self.assertEqual(len(h.sent), 2)
        self.assertEqual(h.sent[0]["protocol_version"], 2)
        captured = [row for row in h.audit.rows if row.get("result") == "captured"][:1][0]
        self.assertEqual(original_sequences[0], (captured["host_after_capture"]["capture_seq"],
                                                captured["host_after_capture"]["frame_binding_seq"]))
        self.assertNotEqual(original_sequences[0], original_sequences[1])
        refused = [entry for entry in h.gate.history if entry.get("result") == "refused"]
        self.assertEqual([entry["attempted"] for entry in refused], [False, False])
        self.assertEqual(h.gate.run_clicks, 2)

    def test_dynamic_weakened_click_guard_never_sends_after_restore(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.override_pipeline({NODE: {"recognition": "DirectHit"}}))
        self.assert_blocked_with_repoll(h)

    def test_dynamic_weakened_referenced_guard_never_sends_after_restore(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.override_pipeline({GUARD: {"recognition": "DirectHit"}}))
        self.assert_blocked_with_repoll(h)

    def test_dynamic_disabled_referenced_guard_never_sends_after_restore(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.override_pipeline({GUARD: {"enabled": False}}))
        self.assert_blocked_with_repoll(h)

    def test_dynamic_inverse_never_sends_after_restore(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.override_pipeline({NODE: {"inverse": True}}))
        self.assert_blocked_with_repoll(h)

    def test_dynamic_action_or_target_never_sends_after_restore(self):
        for override in ({"action": "DoNothing"}, {"action": "Click", "target": [110, 110, 10, 10]}):
            with self.subTest(override=override):
                h = self.build(repoll=True)
                h.once("Node.Recognition.Succeeded", lambda c: c.override_pipeline({NODE: override}))
                self.assert_blocked_with_repoll(h)

    def test_dynamic_repeat_never_sends_after_restore(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.override_pipeline({NODE: {"repeat": 2}}))
        self.assert_blocked_with_repoll(h)

    def test_nested_known_click_recognition_cannot_reuse_parent_or_next_poll(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Starting", lambda c: c.run_recognition(NODE, PIXELS.copy()))
        self.assert_blocked_with_repoll(h)

    def test_nested_read_only_recognition_does_not_break_a_complete_parent(self):
        h = self.build()
        h.once("Node.Recognition.Starting", lambda c: c.run_recognition(GUARD, PIXELS.copy()))
        self.assertTrue(h.run().succeeded)
        self.assertTrue(h.injected)
        self.assertEqual(len(h.sent), 1)

    def test_nested_action_cannot_reuse_parent_or_next_poll(self):
        h = self.build(repoll=True)
        h.once("Node.Recognition.Succeeded", lambda c: c.run_action(NODE, box=RECT))
        self.assert_blocked_with_repoll(h)

    def test_stop_clears_parent_and_new_task_requires_new_real_recognition(self):
        h = self.build()
        def stop(context): h.stop_job = context.tasker.post_stop()
        h.once("Node.Recognition.Succeeded", stop)
        h.run()
        self.assertEqual(h.sent, [])
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertIsNone(h.binder.task_id)
        h.hook = None
        self.assertTrue(h.run().succeeded)
        self.assertEqual(len(h.sent), 1)

    def test_stop_job_completion_waits_for_native_idle_before_a_second_task(self):
        h = self.build()
        terminal, idle_checked, release = threading.Event(), threading.Event(), threading.Event()
        task_event = h.binder.task_event
        native_running = Tasker.running.fget

        def observed_terminal(message, details):
            task_event(message, details)
            if details.get("entry") == "MaaTaskerPostStop" and message in ("Tasker.Task.Succeeded", "Tasker.Task.Failed"):
                terminal.set()

        def held_idle_flag(tasker):
            value = native_running(tasker)
            # Deterministically hold the SDK's real completion-to-idle gap.
            if tasker is h.tasker and terminal.is_set() and not release.is_set():
                idle_checked.set()
                return True
            return value

        def stop(context):
            h.stop_job = context.tasker.post_stop()

        h.binder.task_event = observed_terminal
        h.once("Node.Recognition.Succeeded", stop)
        with patch.object(Tasker, "running", property(held_idle_flag)):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(h.run)
                completed = threading.Event()
                future.add_done_callback(lambda _: completed.set())
                try:
                    self.assertTrue(idle_checked.wait(2), "stop completion must await the native idle flags")
                    self.assertFalse(completed.wait(0.05), "a completed stop job cannot bypass a busy runner")
                finally:
                    release.set()
                future.result(timeout=5)
        self.assertEqual(h.sent, [], h.audit.rows)
        self.assertEqual(h.gate.run_clicks, 0)
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertIsNone(h.binder.task_id)
        ended_tasks = set(h.finished)
        h.hook = None
        self.assertTrue(h.run().succeeded)
        self.assertEqual(len(h.sent), 1)
        intent, = [r for r in h.audit.rows if r.get("result") == "intent"]
        self.assertNotIn(intent["recognition_guard"]["task_id"], ended_tasks)
        self.assertIsNone(h.gate.frame_permits.take())
        self.assertIsNone(h.binder.task_id)

    def test_pre_wait_freezes_capture_invalidates_original_guard_and_next_poll(self):
        h = self.build(repoll=True, pre_wait=True)
        self.assert_blocked_with_repoll(h)
        self.assertTrue(any(msg == "Node.WaitFreezes.Starting" for msg, _ in h.events))


if __name__ == "__main__":
    unittest.main()

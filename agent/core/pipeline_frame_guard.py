"""Opt-in Maa adapter for exact Pipeline recognition-to-click permits.

Only the first controller capture of a Pipeline polling batch is a candidate. Any
additional capture invalidates the batch, including identical pixels. The final
inline recognition proves that image, but a permit is armed only after the complete
parent recognition succeeds and its matching action starts. No debug images,
Controller screenshot notifications, coordinates or input transports are used here.
"""
from __future__ import annotations

import copy
import json
import threading

from agent.core.frame_permit import MAX_AGE_S

NAME = "SC.Input.FrameGuard"
GUARD_KEYS = ("recognition", "inverse")
ACTION_KEYS = ("action", "repeat", "repeat_delay", "pre_wait_freezes", "repeat_wait_freezes")


def _references(recognition):
    if not isinstance(recognition, dict):
        return
    param = recognition.get("param", {})
    for part in param.get("all_of", param.get("any_of", [])):
        if isinstance(part, str):
            yield part
        elif isinstance(part, dict):
            yield from _references(part)


def _signature(node, action=False):
    # The launcher enables the root Click, but referenced guards must keep their
    # original enabled state as well as their recognition and inverse semantics.
    keys = GUARD_KEYS + ACTION_KEYS if action else GUARD_KEYS + ("enabled",)
    return {key: node.get(key) for key in keys}


def _inline(recognition):
    """Native normalized sub-results use type/param; input JSON needs recognition."""
    spec = copy.deepcopy(recognition)
    sub_name = spec.pop("sub_name", None)
    for key in ("all_of", "any_of"):
        if key in spec.get("param", {}):
            spec["param"][key] = [part if isinstance(part, str) else _inline(part)
                                  for part in spec["param"][key]]
    result = {"recognition": spec}
    if sub_name is not None:
        result["sub_name"] = sub_name
    return result


class PipelineFrameBinder:
    def __init__(self, resource, gate):
        self.gate, self.resource = gate, resource
        self.nodes = gate.table.node_actions()
        self.lock = threading.RLock()
        self.task_id = self.task_thread = None
        self.tasker = None
        self.generation = 0
        self.poisoned = False
        self._reset_batch(False)

        # Native normalized definitions make the trusted snapshot independent of
        # v1/v2 JSON spelling. Ordinary page guards remain native Pipeline guards.
        override = {}
        originals = {}
        for name in self.nodes:
            node = resource.get_node_data(name)
            drag = gate.table.get(self.nodes[name]).drag_rect is not None
            if (not node or node["action"]["type"] != ("Swipe" if drag else "Click") or node.get("inverse") is not False
                    or node["recognition"]["type"] not in ("And", "Or")):
                raise ValueError("帧许可缺少可信的正向点击守卫：" + name)
            originals[name] = copy.deepcopy(node["recognition"])
            override[name] = {"recognition": {"type": "And", "param": {
                "all_of": [_inline(node["recognition"]), _inline({"type": "Custom", "param": {
                    "custom_recognition": NAME, "custom_recognition_param": {"node": name},
                    "roi": [0, 0, 1280, 720]}})], "box_index": 0}}}
        if not resource.override_pipeline(override):
            raise ValueError("帧许可 Pipeline 接入失败")
        self.expected = {name: resource.get_node_data(name) for name in resource.node_list}
        for name, original in originals.items():
            parts = self.expected[name]["recognition"]["param"]["all_of"]
            if (len(parts) != 2 or {key: parts[0].get(key) for key in ("type", "param")} != original
                    or parts[1].get("type") != "Custom"
                    or parts[1].get("param", {}).get("custom_recognition") != NAME):
                raise ValueError("帧许可包装改变了原始识别守卫：" + name)
        self.dependencies = {name: self._dependency_names(name) for name in self.nodes}

    def _dependency_names(self, name, seen=None):
        seen = set() if seen is None else seen
        if name in seen:
            return seen
        node = self.expected.get(name)
        if not node:
            raise ValueError("帧许可引用了未知守卫：" + name)
        seen.add(name)
        for child in _references(node["recognition"]):
            self._dependency_names(child, seen)
        return seen

    def _definition_matches(self, context, name):
        if name not in self.nodes:
            return False
        for child in self.dependencies[name]:
            current = context.get_node_data(child)
            if not current or _signature(current, child == name) != _signature(self.expected[child], child == name):
                return False
        return context.get_node_data(name).get("enabled") is True

    def _reset_batch(self, await_capture):
        self.generation += 1
        self.await_capture = bool(await_capture) and not self.poisoned
        self.batch_valid = self.await_capture
        self.batch_frame, self.recognition, self.candidate, self.completed = None, None, None, None
        self.next_list_open = False
        self.gate.frame_permits.invalidate()

    def _poison(self, reason):
        # Maa may poll again after a custom guard misses. A broken identity proof
        # must stop permission issuance for this task, rather than silently retry.
        self.poisoned = True
        self._reset_batch(False)
        self.gate._log({"kind": "frame_guard", "result": "denied", "reason": reason,
                        "task_id": self.task_id})

    def reset(self):
        with self.lock:
            self.task_id = self.task_thread = None
            self.poisoned = False
            self._reset_batch(False)

    def reject_click(self, reason):
        with self.lock:
            if self.task_id is not None:
                self._poison(reason)
            else:
                self.gate.frame_permits.invalidate()

    def discard_click(self, reason):
        """An explicit ``not_sent`` (denied, or refused before any gesture) proves nothing reached the game: drop only
        this frame's permit so the task may re-recognise on a fresh capture. Identity errors still use ``reject_click``."""
        with self.lock:
            if self.poisoned or self.task_id is None:
                self.gate.frame_permits.invalidate()
                return
            self._reset_batch(False)
            self.gate._log({"kind": "frame_guard", "result": "retry", "reason": reason, "task_id": self.task_id})

    def allows_click(self, permit):
        """Native task state closes the gap before its terminal callback arrives."""
        with self.lock:
            try:
                detail = self.tasker.get_task_detail(self.task_id) if self.tasker and self.task_id else None
                valid = (not self.poisoned and permit is not None and permit.task_id == self.task_id
                         and self.tasker.stopping is False and detail is not None and detail.status.running)
            except Exception:
                valid = False
            if not valid:
                self.reject_click("native_task_not_running")
            return valid

    def capture_started(self, source):
        with self.lock:
            eligible = (self.task_id is not None and self.await_capture and self.batch_valid
                        and self.batch_frame is None and source == "maa_custom_controller"
                        and threading.get_ident() == self.gate.controller_worker_thread)
            self.await_capture = False
            self.candidate = self.completed = None
            if not eligible:
                self.batch_valid = False
                return None
            return self.task_id, self.generation

    def capture_finished(self, cookie, frame):
        with self.lock:
            if (cookie is not None and cookie == (self.task_id, self.generation)
                    and self.batch_valid and frame is not None):
                self.batch_frame = frame
            else:
                self.batch_valid = False

    def _current_batch(self):
        return (not self.poisoned and self.batch_valid and self.next_list_open and self.batch_frame is not None
                and self.batch_frame == self.gate._frame)

    def task_event(self, message, details):
        with self.lock:
            if message == "Tasker.Task.Starting":
                self.reset()
                if details.get("entry") == "MaaTaskerPostStop":
                    return  # Maa emits a separate stop job; it is never an owner task.
                self.task_id, self.task_thread = details.get("task_id"), threading.get_ident()
                self.gate.frame_permits.reset_task()
            elif message.startswith("Tasker.Task.") or "PostStop" in message:
                self.reset()

    def context_event(self, context, message, details):
        with self.lock:
            if details.get("task_id") != self.task_id:
                # Nested read-only recognition is not the parent polling batch.
                if details.get("name") in self.nodes:
                    self._reset_batch(False)
                return
            if threading.get_ident() != self.task_thread:
                self._poison("task_thread_mismatch")
                return
            name = details.get("name")
            if message == "Node.PipelineNode.Starting":
                self._reset_batch(True)
            elif message == "Node.NextList.Starting":
                self.next_list_open = True
            elif message == "Node.NextList.Failed":
                self._reset_batch(True)
            elif message.startswith("Node.Recognition.") and name in self.nodes:
                self._recognition_event(context, message, details)
            elif message == "Node.Action.Starting":
                completed = self.completed
                self.completed = None
                if name not in self.nodes:
                    self.gate.frame_permits.invalidate()
                elif (completed is None or completed[:2] != (self.task_id, name)
                        or not self._current_batch() or not self._definition_matches(context, name)
                        or not self.gate.frame_permits.arm(completed[3], self.task_id, completed[2], name)):
                    self._poison("action_parent_mismatch")
            elif message == "Node.Action.Failed" and name in self.nodes:
                # The controller already poisoned an uncertain send or identity error (reject_click); a failed click
                # that reached here was explicitly not sent, so only this batch is dropped (ANN-17).
                self._reset_batch(False)
            elif message in ("Node.Action.Succeeded", "Node.Action.Failed", "Node.PipelineNode.Succeeded",
                             "Node.PipelineNode.Failed", "Node.ActionNode.Starting"):
                self._reset_batch(False)

    def _recognition_event(self, context, message, details):
        name, reco_id = details["name"], details["reco_id"]
        if message == "Node.Recognition.Starting":
            self.candidate = self.completed = None
            self.gate.frame_permits.invalidate()
            self.recognition = (self.task_id, name, reco_id) if self._current_batch() else None
        elif message == "Node.Recognition.Failed":
            self.recognition = self.candidate = self.completed = None
            self.gate.frame_permits.invalidate()
        elif message == "Node.Recognition.Succeeded":
            reco = context.tasker.get_recognition_detail(reco_id)
            parts = getattr(getattr(reco, "best_result", None), "sub_results", [])
            token = None
            if (len(parts) == 2 and all(part.hit for part in parts)
                    and getattr(parts[1].algorithm, "value", parts[1].algorithm) == "Custom"
                    and isinstance(parts[1].raw_detail, dict)):
                token = parts[1].raw_detail.get("best", {}).get("detail", {}).get("token")
            if (self.recognition == (self.task_id, name, reco_id) and reco is not None and reco.hit
                    and getattr(reco.algorithm, "value", reco.algorithm) == "And" and token == self.candidate
                    and token is not None and self._current_batch() and self._definition_matches(context, name)):
                self.completed = self.task_id, name, reco_id, token
            else:
                self._poison("complete_parent_mismatch")
            self.recognition = None

    def _batch_frame_stale(self):
        frame = self.batch_frame
        try:
            return not 0 <= self.gate.frame_permits.clock() - frame.captured_at <= MAX_AGE_S
        except Exception:
            return False

    def analyze(self, context, argv):
        with self.lock:
            params = json.loads(argv.custom_recognition_param)
            name = params.get("node") if isinstance(params, dict) and set(params) == {"node"} else None
            recognition = self.recognition
            if (recognition is None or name != recognition[1] or not self._current_batch()
                    or threading.get_ident() != self.task_thread or context.get_task_job().job_id != self.task_id
                    or not context.get_task_job().running or context.tasker.stopping
                    or not self._definition_matches(context, name)):
                if self.task_id is not None:
                    self._poison("recognition_frame_mismatch")
                return None
            if self._batch_frame_stale():
                # Age alone is not an identity break: a slow earlier recognition in the same And (for example the
                # CardScan tile OCR) may outlive the permit TTL. No permit is issued, so nothing can be sent; the next
                # poll captures a fresh batch instead of poisoning the whole task.
                self.recognition = None
                self.gate.frame_permits.invalidate()
                self.gate._log({"kind": "frame_guard", "result": "retry", "reason": "frame_stale_before_permit",
                                "task_id": self.task_id})
                return None
            token = self.gate.frame_permits.issue(name, self.nodes[name], self.task_id, recognition[2],
                                                 argv.image, self.batch_frame)
            self.candidate = token
            if token is None:
                self._poison("recognition_frame_mismatch")
            return token

    def attach(self, tasker):
        from maa.context import ContextEventSink
        from maa.tasker import TaskerEventSink

        binder = self
        self.tasker = tasker

        class TaskSink(TaskerEventSink):
            def on_raw_notification(self, tasker, message, details):
                try:
                    binder.task_event(message, details)
                except Exception:
                    binder.reset()

        class ContextSink(ContextEventSink):
            def on_raw_notification(self, context, message, details):
                try:
                    binder.context_event(context, message, details)
                except Exception:
                    binder.reset()  # callback exceptions must never preserve authority

        from agent.core import maa_sinks

        self.task_sink, self.context_sink = TaskSink(), ContextSink()
        task_id, context_id = tasker.add_sink(self.task_sink), tasker.add_context_sink(self.context_sink)
        for remove, sink_id in (("remove_sink", task_id), ("remove_context_sink", context_id)):
            if sink_id is not None:
                maa_sinks.track(tasker, remove, sink_id)   # released when the run ends (maa_sinks.release)
        if task_id is None or context_id is None:
            raise ValueError("帧许可生命周期监听注册失败")


def install(resource, gate):
    """Private development integration; callers must explicitly enable it on the gate."""
    from maa.custom_recognition import CustomRecognition

    if not gate.frame_guard_enabled:
        return None
    binder = PipelineFrameBinder(resource, gate)

    class FrameRecognition(CustomRecognition):
        def analyze(self, context, argv):
            try:
                token = binder.analyze(context, argv)
                return self.AnalyzeResult((0, 0, 1280, 720), {"token": token}) if token else None
            except Exception:
                binder.reset()
                return None

    binder.custom_recognition = FrameRecognition()
    if not resource.register_custom_recognition(NAME, binder.custom_recognition):
        raise ValueError("帧许可自定义识别注册失败")
    gate.frame_binding = binder
    return binder

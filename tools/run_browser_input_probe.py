#!/usr/bin/env python3
"""Explicit, single-click background experiment for the owned BrowserDemo only."""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.browser_bridge import BrowserBridge, create_screenshot_controller, validate_status
from tools.probe_browser import discover_with_maa

TITLE = "MaaShinyColors BrowserDemo"
PREFIX = "SC.BrowserLab."
ENTRY = PREFIX + "Observe"
CLICK_NODE = PREFIX + "Click.ReadyGuard"
PRIVATE_ROOT = ROOT / "dev_assets/private/browser_input_probe"
MAX_SECONDS = 10
POSTCONDITION_SECONDS = 12.0
DIRECT_BUTTON_HOLD_SECONDS = 0.05


def require_demo(status):
    validate_status(status)
    if status["browser_demo"] is not True or status["ready"] is not True:
        raise ValueError("仅允许已就绪的仓库自有BrowserDemo页面")
    if status.get("minimized", False) or status.get("window_visible", True) is not True or status.get("webview_visible", True) is not True:
        raise ValueError("BrowserDemo必须可见且非最小化")
    return status


def fixed_pipeline(execute=False):
    """Load the repository-owned graph; CLI cannot supply recognition or targets."""
    pipeline = json.loads((ROOT / "tests/fixtures/browser_input_probe/pipeline.json").read_text(encoding="utf-8"))
    pipeline[CLICK_NODE]["enabled"] = bool(execute)
    pipeline[ENTRY]["next"][2] = CLICK_NODE if execute else PREFIX + "Ready"
    return pipeline


@dataclass(frozen=True)
class NativeWindow:
    hwnd: int
    pid: int
    parent: int
    class_name: str
    title: str
    visible: bool
    minimized: bool
    origin: tuple[int, int]
    size: tuple[int, int]
    dpi: int


@dataclass(frozen=True)
class InputTarget:
    top: NativeWindow
    viewport: NativeWindow
    renderer: NativeWindow

    def point(self):
        width, height = self.renderer.size
        return round(640 * width / 1280), round(460 * height / 720)


class NativeInspector:
    """Read metadata in physical pixels without changing focus or the cursor."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("后台输入实验仅支持Windows")
        from ctypes import wintypes as w
        self.w = w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetParent": ([w.HWND], w.HWND),
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "GetClassNameW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "IsWindowVisible": ([w.HWND], w.BOOL), "IsIconic": ([w.HWND], w.BOOL),
            "GetDpiForWindow": ([w.HWND], w.UINT),
            "GetForegroundWindow": ([], w.HWND),
            "GetCursorPos": ([ctypes.POINTER(w.POINT)], w.BOOL),
            "SetThreadDpiAwarenessContext": ([ctypes.c_void_p], ctypes.c_void_p),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user.EnumChildWindows.argtypes = [w.HWND, self.callback_type, w.LPARAM]
        self.user.EnumChildWindows.restype = w.BOOL

    @contextmanager
    def physical(self):
        previous = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise RuntimeError("无法取得物理像素DPI上下文")
        try:
            yield
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)

    def window(self, hwnd):
        with self.physical():
            pid, rect, origin = self.w.DWORD(), self.w.RECT(), self.w.POINT(0, 0)
            if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not self.user.GetClientRect(hwnd, ctypes.byref(rect)) or not self.user.ClientToScreen(hwnd, ctypes.byref(origin)):
                raise RuntimeError("窗口已消失或客户区元数据读取失败")
            class_name, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, class_name, len(class_name))
            self.user.GetWindowTextW(hwnd, title, len(title))
            return NativeWindow(int(hwnd), int(pid.value), int(self.user.GetParent(hwnd) or 0), class_name.value, title.value, bool(self.user.IsWindowVisible(hwnd)), bool(self.user.IsIconic(hwnd)), (origin.x, origin.y), (rect.right, rect.bottom), int(self.user.GetDpiForWindow(hwnd)))

    def children(self, hwnd):
        handles = []
        callback = self.callback_type(lambda child, _: handles.append(int(child)) or True)
        self.user.EnumChildWindows(hwnd, callback, 0)
        return [self.window(handle) for handle in handles]

    def desktop_state(self):
        point = self.w.POINT()
        with self.physical():
            if not self.user.GetCursorPos(ctypes.byref(point)):
                raise RuntimeError("无法只读获取游标位置")
            return {"foreground_hwnd": int(self.user.GetForegroundWindow() or 0), "cursor": [point.x, point.y]}

    def processes(self):
        """Toolhelp parent relationships only; no command line or credentials."""
        w = self.w
        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("pid", w.DWORD), ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD), ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD), ("exe", w.WCHAR * 260)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
        kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32FirstW.restype = kernel.Process32NextW.restype = w.BOOL
        kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
        handle = kernel.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise RuntimeError("无法验证WebView2进程归属")
        result, entry = {}, ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            valid = kernel.Process32FirstW(handle, ctypes.byref(entry))
            while valid:
                result[int(entry.pid)] = (int(entry.parent), entry.exe.casefold())
                valid = kernel.Process32NextW(handle, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(handle)
        return result


def owned_pid(pid, session_pid, processes):
    seen = set()
    while pid != session_pid:
        if pid in seen or pid not in processes or len(seen) >= 12:
            return False
        seen.add(pid)
        parent, image = processes[pid]
        if image != "msedgewebview2.exe":
            return False
        pid = parent
    return True


def choose_top(windows, session_pid):
    candidates = [item for item in windows if item.title == TITLE and item.pid == session_pid]
    if len(candidates) != 1:
        raise ValueError("BrowserDemo标题与会话PID绑定失败或存在多个窗口")
    top = candidates[0]
    if not top.visible or top.minimized or not top.class_name.startswith("WindowsForms10.") or top.dpi <= 0:
        raise ValueError("BrowserDemo顶层窗口身份或可见性不符合要求")
    width, height = top.size
    if not 640 <= width <= 5120 or abs(height - width * 788 / 1280) > 2:
        raise ValueError("BrowserDemo客户区布局或DPI比例不符合固定布局")
    return top


def choose_input_target(top, children, processes, status):
    require_demo(status)
    scale = top.size[0] / 1280
    expected_origin = (top.origin[0], top.origin[1] + round(44 * scale))
    expected_size = (round(1280 * scale), round(720 * scale))
    close = lambda a, b: all(abs(x - y) <= 2 for x, y in zip(a, b))
    viewports = [item for item in children if item.pid == top.pid and item.parent == top.hwnd and item.class_name.startswith("WindowsForms10.") and close(item.origin, expected_origin) and close(item.size, expected_size) and item.visible]
    if len(viewports) != 1:
        raise ValueError("无法唯一校准BrowserDemo的WebView客户区")
    viewport = viewports[0]
    index = {item.hwnd: item for item in children}
    def in_viewport(item):
        seen = set()
        while item.parent != viewport.hwnd:
            if item.parent not in index or item.parent in seen or len(seen) >= 12:
                return False
            seen.add(item.parent)
            item = index[item.parent]
            if not owned_pid(item.pid, top.pid, processes):
                return False
        return True
    renderers = [item for item in children if item.class_name == "Chrome_RenderWidgetHostHWND"]
    # Reject multiple renderer targets even if only one seems visible.
    if len(renderers) != 1:
        raise ValueError("WebView2输入子窗口不存在或存在歧义")
    renderer = renderers[0]
    if not renderer.visible or renderer.minimized or renderer.dpi != top.dpi or not owned_pid(renderer.pid, top.pid, processes) or not in_viewport(renderer) or not close(renderer.origin, viewport.origin) or not close(renderer.size, viewport.size):
        raise ValueError("WebView2子窗口PID、类、父链或物理矩形校准失败")
    source_size = (status.get("source_capture_width"), status.get("source_capture_height"))
    if any(type(value) is not int for value in source_size) or not close(source_size, renderer.size) or not close(source_size, (1280, 720)):
        raise ValueError("CapturePreview源像素与WebView2输入客户区无法一一校准")
    return InputTarget(top, viewport, renderer)


def discover_identity(inspector, session_pid):
    _, windows = discover_with_maa()
    candidates = [inspector.window(item.hwnd) for item in windows if item.title == TITLE]
    return choose_top(candidates, session_pid)


def resolve_target(inspector, session_pid, status):
    top = discover_identity(inspector, session_pid)
    if status.get("composition_demo") is True:
        return choose_composition_target(top, inspector.children(top.hwnd), status)
    return choose_input_target(top, inspector.children(top.hwnd), inspector.processes(), status)


def choose_composition_target(top, children, status):
    """A visual host receives native input itself; it has no renderer HWND route."""
    require_demo(status)
    if status.get("composition_demo") is not True or status.get("local_input_enabled") is not True:
        raise ValueError("Composition 本地输入未显式启用")
    close = lambda a, b: all(abs(x - y) <= 2 for x, y in zip(a, b))
    expected = (top.origin[0], top.origin[1] + 44)
    candidates = [item for item in children if item.parent == top.hwnd and item.pid == top.pid
                  and item.class_name.startswith("WindowsForms10.") and item.visible
                  and not item.minimized and item.dpi == top.dpi
                  and close(item.origin, expected) and close(item.size, (1280, 720))]
    if len(candidates) != 1 or not close(top.size, (1280, 788)):
        raise ValueError("Composition 自有画布身份或尺寸校准失败")
    source = (status.get("source_capture_width"), status.get("source_capture_height"))
    if any(type(value) is not int for value in source) or not close(source, (1280, 720)):
        raise ValueError("Composition 截图源尺寸未校准")
    viewport = candidates[0]
    return InputTarget(top, viewport, viewport)


def validate_local_input_session(data, pid):
    expected = {"version", "host", "port", "token", "pid", "input_enabled", "browser_demo_only", "transport"}
    if not isinstance(data, dict) or set(data) != expected or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("独立本地输入会话字段无效")
    if data["host"] != "127.0.0.1" or type(data["port"]) is not int or not 1 <= data["port"] <= 65535:
        raise ValueError("本地输入只允许回环地址与有效端口")
    if type(data["pid"]) is not int or data["pid"] != pid or data["input_enabled"] is not True or data["browser_demo_only"] is not True or data["transport"] != "webview2-composition":
        raise ValueError("本地输入会话与自有 Composition 宿主不匹配")
    if not isinstance(data["token"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", data["token"]):
        raise ValueError("本地输入会话认证字段无效")
    return data


def inject_composition_click(target, report):
    """One authenticated fixed gesture; coordinates and page are not selectable."""
    session = validate_local_input_session(json.loads(Path(report["input_session_path"]).read_text(encoding="utf-8")), target.top.pid)
    with socket.create_connection((session["host"], session["port"]), timeout=5) as connection:
        connection.settimeout(10)
        report["input_attempts"] += 1
        connection.sendall((json.dumps({"token": session["token"], "command": "claim-demo"}) + "\n").encode("utf-8"))
        with connection.makefile("rb") as stream:
            line = stream.readline(4097)
        if len(line) > 4096 or not line.endswith(b"\n"):
            raise ValueError("本地输入响应过长或被截断")
        result = json.loads(line.decode("utf-8"))
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise ValueError("本地输入响应无效")
        report["input_succeeded"] = result["ok"]
        if not result["ok"]:
            report["input_error"] = str(result.get("error", "宿主边界拒绝输入"))
        return result["ok"]


def inject_background_click(target, report):
    """The only real input boundary; directly mockable in all unit tests."""
    transport = report.get("transport", "maa-postmessage")
    if transport == "webview2-composition":
        return inject_composition_click(target, report)
    if transport == "direct-postmessage":
        return inject_direct_postmessage(target, report)
    if transport != "maa-postmessage":
        raise ValueError("未知本地输入实验传输方式")
    from maa.controller import Win32Controller
    from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
    controller = Win32Controller(target.renderer.hwnd, screencap_method=MaaWin32ScreencapMethodEnum.Null, mouse_method=MaaWin32InputMethodEnum.PostMessage, keyboard_method=MaaWin32InputMethodEnum.Null)
    try:
        if not controller.set_screenshot_use_raw_size(True) or not controller.post_connection().wait().succeeded:
            raise RuntimeError("纯PostMessage输入控制器连接失败")
        x, y = target.point()
        # Increment at the actual post_click boundary, including failed calls.
        report["input_attempts"] += 1
        job = controller.post_click(x, y).wait()
        report["input_succeeded"] = bool(job.succeeded)
        return bool(job.succeeded)
    finally:
        controller.post_inactive().wait()


def native_post_message(hwnd, message, wparam, lparam):
    """Queue one mouse message; deliberately no activation or focus messages."""
    from ctypes import wintypes as w
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.PostMessageW.restype = w.BOOL
    return bool(user.PostMessageW(hwnd, message, wparam, lparam))


def inject_direct_postmessage(target, report):
    """One fixed gesture, with observable queue results and no automatic retry."""
    x, y = target.point()
    lparam = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
    report["input_attempts"] += 1
    report["message_results"] = []
    report["input_succeeded"] = False
    report["input_queue_succeeded"] = False
    report["input_partial"] = False
    report["button_hold_seconds"] = DIRECT_BUTTON_HOLD_SECONDS
    down_queued = False
    for name, message, flags in (("WM_MOUSEMOVE", 0x0200, 0), ("WM_LBUTTONDOWN", 0x0201, 1), ("WM_LBUTTONUP", 0x0202, 0)):
        try:
            queued = native_post_message(target.renderer.hwnd, message, flags, lparam)
        except Exception as exc:
            report["input_error"] = str(exc)
            queued = False
        report["message_results"].append({"message": name, "message_id": message, "flags": flags, "queued": bool(queued)})
        if not queued:
            report["input_partial"] = down_queued
            report["input_error"] = report.get("input_error") or "原生鼠标消息投递失败，已停止；不补发或重试"
            return False
        if name == "WM_LBUTTONDOWN":
            down_queued = True
            # Match Maa's documented down/up spacing; no new input message.
            time.sleep(DIRECT_BUTTON_HOLD_SECONDS)
    # Queue acceptance is a transport result, never proof of page completion.
    report["input_succeeded"] = True
    report["input_queue_succeeded"] = True
    return True


def run_pipeline(controller, execute=False):
    from maa.resource import Resource
    from maa.tasker import Tasker
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix="sc-browserlab-") as directory:
        path = Path(directory) / "pipeline.json"
        path.write_text(json.dumps(fixed_pipeline(execute)), encoding="utf-8")
        if not resource.post_pipeline(path).wait().succeeded or not tasker.bind(resource, controller) or not tasker.inited:
            raise RuntimeError("BrowserLab固定Pipeline初始化失败")
        job = tasker.post_task(ENTRY)
        deadline = time.monotonic() + MAX_SECONDS
        while not job.done:
            if time.monotonic() >= deadline:
                tasker.post_stop().wait()
                raise RuntimeError("BrowserLab有界Pipeline超时并安全停止")
            time.sleep(0.01)
        detail = job.get()
        trace = [] if detail is None else [{"name": node.name, "completed": node.completed, "recognition_hit": node.recognition.hit if node.recognition else None, "action": node.action.action.value if node.action else None, "action_success": node.action.success if node.action else None} for node in detail.nodes]
        return interpret_trace(trace, bool(job.succeeded), execute)


def interpret_trace(trace, succeeded, execute=False):
    state = "error"
    if len(trace) == 2 and trace[0]["name"] == ENTRY:
        state = {PREFIX + "Ready": "ready", PREFIX + "Complete": "complete", PREFIX + "Ambiguous": "ambiguous", PREFIX + "Unknown": "unknown", CLICK_NODE: "clicked"}.get(trace[-1]["name"], "error")
    expected = "Click" if state == "clicked" and execute else "DoNothing"
    valid = succeeded and all(item.get("completed") is True and item.get("recognition_hit") is True and item.get("action_success") is True for item in trace) and bool(trace) and trace[-1].get("action") == expected
    return {"state": state, "passed": bool(valid and state in {"ready", "complete", "clicked"}), "node_trace": trace, "framework_succeeded": succeeded}


def initialize(controller):
    # CustomController otherwise advertises split mouse-down/up input; the
    # experiment exposes only the auditable single Click callback.
    controller.get_features = lambda: 0
    if not controller.set_screenshot_use_raw_size(True) or not controller.post_connection().wait().succeeded:
        raise RuntimeError("只读截图控制器初始化失败")


def offline_replay(evidence_dir):
    """Actual Maa recognition/action against owned fixtures, no desktop APIs."""
    results = []
    for state in ("ready", "complete", "native_complete", "unknown", "ambiguous", "native_ambiguous", "partial_complete", "dimmed_complete", "dimmed_overlay", "opaque_overlay"):
        class FixtureBridge:
            def status(self):
                return {"ready": True, "browser_demo": True, "input_enabled": False, "width": 1280, "height": 720}
            def screencap(self):
                if state in {"native_complete", "native_ambiguous", "partial_complete", "dimmed_complete"}:
                    from tools.replay_screenshots import read_png
                    source = "ambiguous" if state == "native_ambiguous" else "unknown" if state == "partial_complete" else "complete"
                    image = read_png(ROOT / "tests/fixtures/replay" / (source + ".png")).copy()
                    # Measured owned Composition screenshot RGB=(56,99,233).
                    mask = (image[:, :, 0] == 235) & (image[:, :, 1] == 100) & (image[:, :, 2] == 36)
                    image[mask] = (233, 99, 56)
                    if state == "partial_complete":
                        image[260:360, 330:600] = (233, 99, 56)
                    if state == "dimmed_complete":
                        image = (image * 0.4).astype("uint8")
                    path = evidence_dir / state / "color-variant.png"
                    write_png(path, image)
                    return path.read_bytes()
                if state.endswith("overlay"):
                    import numpy as np
                    from tools.replay_screenshots import read_png
                    source = read_png(ROOT / "tests/fixtures/replay/ready.png")
                    image = (source * 0.4).astype(np.uint8) if state == "dimmed_overlay" else source.copy()
                    if state == "opaque_overlay":
                        image[380:540, 430:850] = (30, 30, 30)
                    path = evidence_dir / state / "overlay-source.png"
                    write_png(path, image)
                    return path.read_bytes()
                return (ROOT / "tests/fixtures/replay" / (state + ".png")).read_bytes()
        controller = create_screenshot_controller(FixtureBridge(), evidence_dir / state)
        calls = []
        controller.click = lambda *args: calls.append(args) or False
        initialize(controller)
        result = run_pipeline(controller)
        executable = run_pipeline(controller, execute=True)
        expected = "unknown" if state.endswith("overlay") or state in {"partial_complete", "dimmed_complete"} else "complete" if state == "native_complete" else "ambiguous" if state == "native_ambiguous" else state
        expected_action_calls = 1 if state == "ready" else 0
        good = result["state"] == expected and result["passed"] == (expected in {"ready", "complete"}) and len(calls) == expected_action_calls
        if state == "ready":
            good = good and not executable["passed"] and executable["state"] == "error"
        else:
            good = good and executable["state"] == expected
        results.append({"fixture": state, "passed": bool(good), "mock_click_attempts": len(calls), "real_input_attempts": 0, "observation": result, "execution": executable})
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "replay-report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(item["passed"] for item in results):
        raise RuntimeError("BrowserLab离线截图回放未通过，拒绝创建输入控制器")
    return results


def write_png(path, image):
    """Write synthetic overlay evidence using existing NumPy and the stdlib."""
    import struct
    import zlib
    height, width = image.shape[:2]
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(b"".join(b"\0" + image[row, :, :3][:, ::-1].tobytes() for row in range(height)))) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def wait_for_demo(session, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            bridge = BrowserBridge.from_file(session)
            status = bridge.status()
            if not status["browser_demo"]:
                raise ValueError("会话不是自有BrowserDemo")
            if not status["ready"]:
                raise RuntimeError("BrowserDemo页面尚未就绪")
            require_demo(status)
            return bridge
        except (OSError, RuntimeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def private_report_path(path, session):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / "dev_assets/private").resolve()) or path.suffix.casefold() != ".json" or path == Path(session).resolve() or path.exists():
        raise ValueError("报告必须为dev_assets/private内尚不存在的JSON，不能覆盖会话或已有文件")
    return path


def verify_postcondition(controller):
    """Three fresh observations, allowing measured 3.7-second Maa latency."""
    started = time.monotonic()
    deadline = started + POSTCONDITION_SECONDS
    observations = []
    outcome = {"state": "error", "passed": False, "reason": "postcondition_not_observed"}
    for _ in range(3):
        if time.monotonic() >= deadline:
            outcome = {**outcome, "passed": False, "reason": "postcondition_time_budget_exceeded"}
            break
        try:
            observation = run_pipeline(controller)
        except Exception as exc:
            observation = {"state": "error", "passed": False, "error": str(exc), "reason": "postcondition_recognition_failed"}
        elapsed = time.monotonic() - started
        observations.append({**observation, "elapsed_seconds": elapsed})
        outcome = dict(observation)
        # Native Maa callbacks cannot be preempted with a hard real-time promise.
        # A late Complete does not convert an exceeded budget into a pass.
        if elapsed > POSTCONDITION_SECONDS:
            outcome.update(passed=False, reason="postcondition_time_budget_exceeded")
            break
        if observation["state"] == "complete" and observation["passed"]:
            outcome["reason"] = "postcondition_verified"
            break
        if observation["state"] != "ready" or not observation["passed"]:
            outcome.update(passed=False, reason="postcondition_unsafe_state")
            break
        outcome.update(passed=False, reason="postcondition_ready_after_attempt_limit")
        if len(observations) < 3:
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return {**outcome, "observations": observations, "elapsed_seconds": time.monotonic() - started, "max_observations": 3, "budget_seconds": POSTCONDITION_SECONDS, "budget_enforcement": "每轮Maa调用前后检查预算；回调不是硬实时，单次调用可能越界；越界拒绝成功且不再开始新轮次"}


def inject_with_desktop_observation(target, inspector, report):
    """Measure desktop compatibility only across the actual input boundary."""
    before = inspector.desktop_state()
    report["input_desktop_before"] = before
    report["input_desktop_compatible"] = False
    try:
        queued = inject_background_click(target, report)
    finally:
        primary_exception = sys.exc_info()[0] is not None
        try:
            after = inspector.desktop_state()
        except Exception as exc:
            report["input_desktop_after_error"] = str(exc)
            report["input_compatibility"] = "输入期间桌面状态读取失败，兼容性无法确定"
            if not primary_exception:
                raise
        else:
            report["input_desktop_after"] = after
            report["input_focus_unchanged"] = before["foreground_hwnd"] == after["foreground_hwnd"]
            report["input_cursor_unchanged"] = before["cursor"] == after["cursor"]
            report["input_desktop_compatible"] = before == after
            if before != after:
                report["input_compatibility"] = "输入期间焦点或游标变化，兼容性无法确定；不能归因为工具造成，不重复点击"
    return bool(queued and report["input_desktop_compatible"])


def execute_probe(bridge, inspector, evidence_dir, report, execute=False):
    session_pid = bridge.session["pid"]
    require_demo(bridge.status())
    # Complete the slower synthetic replay before binding any live-frame geometry.
    # A window may legitimately move while offline work is running; the later
    # fresh observation and all input-boundary checks still reject any new change.
    if execute:
        report["offline_replay"] = offline_replay(evidence_dir / "offline_replay")
        require_demo(bridge.status())
    top = discover_identity(inspector, session_pid)
    report["top_window"] = asdict(top)
    before = None
    try:
        before = inspector.desktop_state()
        report["desktop_before"] = before
    except Exception as exc:
        report["desktop_before_error"] = str(exc)
    try:
        class DemoBridge:
            def status(self):
                return require_demo(bridge.status())
            def screencap(self):
                self.status()
                return bridge.screencap()
        controller = create_screenshot_controller(DemoBridge(), evidence_dir)
        initialize(controller)
        observation = run_pipeline(controller)
        report["observation"] = observation
        report["capture_error"] = getattr(controller, "capture_error", None)
        report["status_after_observation"] = bridge.status()
        report["frames"] = [str(path) for path in controller.frame_paths]
        if not observation["passed"] or observation["state"] != "ready":
            report["reason"] = observation["state"]
            return False
        if not execute:
            report["reason"] = "recognition_only"
            return True
        require_demo(bridge.status())
        composition = bridge.status().get("composition_demo") is True
        if composition != (report.get("transport") == "webview2-composition"):
            raise ValueError("输入传输必须与自有宿主类型一致，拒绝自动切换")
        target = resolve_target(inspector, session_pid, bridge.status())
        if target.top != top:
            raise ValueError("BrowserDemo窗口身份或几何在识别后发生变化")
        report["input_target"] = asdict(target)
        used = False
        def click(x, y, contact=0, pressure=1):
            nonlocal used
            report["pipeline_click_attempts"] += 1
            try:
                if used or (x, y, contact, pressure) != (640, 460, 0, 1):
                    raise ValueError("仅允许固定单次BrowserLab点击")
                used = True
                latest = resolve_target(inspector, session_pid, bridge.status())
                if latest != target:
                    raise ValueError("窗口归属、DPI或几何在输入前发生变化")
                return inject_with_desktop_observation(target, inspector, report)
            except Exception as exc:
                report["input_error"] = str(exc)
                return False
        controller.click = click
        execution = run_pipeline(controller, execute=True)
        report["execution"] = execution
        # Fresh CapturePreview after Click, interpreted by actual Maa recognition.
        postcondition = verify_postcondition(controller)
        report["postcondition"] = postcondition
        report["postcondition_observations"] = postcondition["observations"]
        report["postcondition_elapsed_seconds"] = postcondition["elapsed_seconds"]
        report["frames"] = [str(path) for path in controller.frame_paths]
        passed = execution["passed"] and execution["state"] == "clicked" and report["input_attempts"] == 1 and report["input_succeeded"] and report.get("input_desktop_compatible") is True and postcondition["passed"] and postcondition["state"] == "complete"
        report["reason"] = "postcondition_verified" if passed else "input_compatibility_uncertain" if report.get("input_desktop_compatible") is False else "input_or_postcondition_failed"
        return bool(passed)
    finally:
        try:
            after = inspector.desktop_state()
        except Exception as exc:
            report["desktop_after_error"] = str(exc)
        else:
            report["desktop_after"] = after
            if before is not None:
                report["focus_unchanged"] = before["foreground_hwnd"] == after["foreground_hwnd"]
                report["cursor_unchanged"] = before["cursor"] == after["cursor"]
                report["desktop_changed"] = after != before
                if after != before:
                    report["desktop_changes"] = "观察到全流程期间焦点或游标变化；仅记录信息，不能归因为工具造成"


def main(argv=None):
    parser = argparse.ArgumentParser(description="只对自有BrowserDemo做单次后台输入实验；默认仅识别截图。")
    parser.add_argument("--session", type=Path, required=True, help="BrowserDemo只读截图桥会话文件。")
    parser.add_argument("--execute-local-probe", action="store_true", help="显式允许一次本地PostMessage实验。")
    parser.add_argument("--transport", choices=("maa-postmessage", "direct-postmessage", "webview2-composition"), default="maa-postmessage", help="固定本地实验后端；不会自动切换或重试点击。")
    parser.add_argument("--wait-seconds", type=float, default=0, help="等待本地会话就绪，范围0到20秒。")
    parser.add_argument("--report", type=Path, help="dev_assets/private内尚不存在的报告JSON。")
    args = parser.parse_args(argv)
    if not math.isfinite(args.wait_seconds) or not 0 <= args.wait_seconds <= 20:
        parser.error("等待时间必须在0到20秒之间")
    directory = PRIVATE_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        report_path = private_report_path(args.report or directory / "report.json", args.session)
    except ValueError as exc:
        parser.error(str(exc))
    directory.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "reason": "not_started", "browser_demo_only": True, "execute_requested": args.execute_local_probe, "live_input_enabled": False, "local_input_enabled": args.execute_local_probe, "readonly": not args.execute_local_probe, "evidence_dir": str(directory), "bridge_input_enabled": False, "input_channel": "独立Maa Win32Controller；截图桥始终只读", "recognition_limit": "固定本地颜色守卫不能证明任意弹窗缺席", "click_node": CLICK_NODE, "input_method": "PostMessage", "input_attempts": 0, "pipeline_click_attempts": 0, "input_succeeded": False}
    report["transport"] = args.transport
    if args.transport == "webview2-composition":
        report["input_session_path"] = str(args.session) + ".local-input.json"
        report["input_method"] = "CoreWebView2CompositionController.SendMouseInput"
        report["input_channel"] = "独立认证自有测试页输入端点；Maa Pipeline Ready守卫；截图桥保持只读"
    if args.transport == "direct-postmessage":
        report["input_method"] = "Win32 PostMessageW (WM_MOUSEMOVE/WM_LBUTTONDOWN/WM_LBUTTONUP)"
        report["input_channel"] = "独立原生Win32鼠标消息；截图桥始终只读；不发送激活或焦点消息"
    try:
        from maa.define import LoggingLevelEnum
        from maa.library import Library
        from maa.tasker import Tasker
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        if Library.version() != "v5.12.3":
            raise ValueError("BrowserLab实验要求MaaFramework v5.12.3")
        report["framework_version"] = Library.version()
        bridge = wait_for_demo(args.session, args.wait_seconds)
        report["passed"] = execute_probe(bridge, NativeInspector(), directory, report, args.execute_local_probe)
        code = 0 if report["passed"] else 1
    except Exception as exc:
        report.update(passed=False, reason="safely_stopped", error=str(exc))
        code = 2
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": report["passed"], "reason": report["reason"], "input_attempts": report["input_attempts"], "report": str(report_path)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

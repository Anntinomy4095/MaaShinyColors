#!/usr/bin/env python3
"""Run a guarded MaaFramework click against the local offline GDI harness."""

from __future__ import annotations

import argparse
import ctypes
import json
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from probe_browser import WindowRecord, discover_with_maa


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "project_policy.json"
OFFLINE_RESOURCE = ROOT / "assets" / "resource" / "offline"
CLICK_NODE = "SC.Offline.Click.ReadyGuard"

READY_ROI = (430, 380, 420, 160)
READY_LOWER_RGB = np.array((10, 145, 55), dtype=np.uint8)
READY_UPPER_RGB = np.array((35, 180, 95), dtype=np.uint8)
READY_COUNT = 30000

SUCCESS_ROI = (320, 250, 640, 130)
SUCCESS_LOWER_RGB = np.array((25, 85, 215), dtype=np.uint8)
SUCCESS_UPPER_RGB = np.array((50, 115, 255), dtype=np.uint8)
SUCCESS_COUNT = 45000


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def wait_for_offline_window(
    title: str,
    timeout_seconds: float,
) -> tuple[str, WindowRecord]:
    deadline = time.monotonic() + timeout_seconds
    framework_version = ""
    while time.monotonic() < deadline:
        framework_version, windows = discover_with_maa()
        matches = [
            window
            for window in windows
            if window.title == title
            and window.class_name.startswith("WindowsForms10.")
            and window.visible
            and not window.minimized
        ]
        if len(matches) == 1:
            return framework_version, matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"发现多个离线靶场窗口，拒绝选择：{len(matches)}"
            )
        time.sleep(0.25)
    raise RuntimeError(f"等待离线靶场窗口超时：{title}")


def focus_offline_window(hwnd: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_bool
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_bool
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_bool
    user32.ShowWindow(hwnd, 9)
    hwnd_topmost = ctypes.c_void_p(-1)
    swp_nosize = 0x0001
    swp_showwindow = 0x0040
    if not user32.SetWindowPos(
        hwnd,
        hwnd_topmost,
        40,
        40,
        0,
        0,
        swp_nosize | swp_showwindow,
    ):
        raise RuntimeError("无法把离线靶场恢复到屏幕内。")
    user32.SetForegroundWindow(hwnd)


def capture(controller: Any) -> np.ndarray:
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("MaaFramework 截图失败。")
    image = job.get()
    if image.ndim != 3 or image.shape[0:2] != (720, 1280):
        raise RuntimeError(f"截图尺寸异常：{image.shape}")
    return image


def color_count(
    image: np.ndarray,
    roi: tuple[int, int, int, int],
    lower_rgb: np.ndarray,
    upper_rgb: np.ndarray,
) -> int:
    x, y, width, height = roi
    bgr = image[y : y + height, x : x + width, :3]
    rgb = bgr[:, :, ::-1]
    mask = np.logical_and(rgb >= lower_rgb, rgb <= upper_rgb).all(axis=2)
    return int(mask.sum())


def wait_for_color_capture(
    controller: Any,
    roi: tuple[int, int, int, int],
    lower_rgb: np.ndarray,
    upper_rgb: np.ndarray,
    minimum_count: int,
    timeout_seconds: float,
) -> tuple[np.ndarray, int]:
    deadline = time.monotonic() + timeout_seconds
    last_image = capture(controller)
    last_count = color_count(last_image, roi, lower_rgb, upper_rgb)
    while last_count < minimum_count and time.monotonic() < deadline:
        time.sleep(0.25)
        last_image = capture(controller)
        last_count = color_count(last_image, roi, lower_rgb, upper_rgb)
    return last_image, last_count


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = np.ascontiguousarray(image[:, :, :3][:, :, ::-1])
    height, width = rgb.shape[:2]
    scanlines = b"".join(
        b"\x00" + rgb[row].tobytes()
        for row in range(height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum)
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", checksum & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(scanlines, level=6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "仅对固定标题的本地 WebView2 离线靶场运行截图、守卫、点击和结果校验。"
        )
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=20.0,
        help="等待离线靶场启动的秒数。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = load_policy()
    offline_policy = policy.get("offline_harness", {})
    if not offline_policy.get("enabled", False):
        print("ERROR: project_policy.json 未启用离线靶场。", file=sys.stderr)
        return 2
    title = str(offline_policy.get("window_title", ""))
    allowed_nodes = set(offline_policy.get("allowed_input_nodes", []))
    if CLICK_NODE not in allowed_nodes:
        print("ERROR: 离线点击节点未被策略精确允许。", file=sys.stderr)
        return 2

    evidence_root = ROOT / str(offline_policy["evidence_dir"])
    run_dir = evidence_root / datetime.now().strftime("%Y%m%d-%H%M%S")

    controller = None
    report: dict[str, Any] = {
        "offline_only": True,
        "window_title_required": title,
        "click_node": CLICK_NODE,
        "input_method": "PostMessageWithCursorPos",
        "screencap_method": "Background",
    }
    try:
        framework_version, window = wait_for_offline_window(
            title,
            args.wait_seconds,
        )
        if (window.client_width, window.client_height) != (1280, 720):
            raise RuntimeError(
                "离线靶场客户区不是 1280x720："
                f"{window.client_width}x{window.client_height}"
            )

        focus_offline_window(window.hwnd)
        time.sleep(0.4)

        from maa.controller import Win32Controller
        from maa.define import (
            MaaWin32InputMethodEnum,
            MaaWin32ScreencapMethodEnum,
        )
        from maa.resource import Resource
        from maa.tasker import Tasker

        controller = Win32Controller(
            window.hwnd,
            screencap_method=MaaWin32ScreencapMethodEnum.Background,
            mouse_method=MaaWin32InputMethodEnum.PostMessageWithCursorPos,
            keyboard_method=MaaWin32InputMethodEnum.PostMessageWithCursorPos,
        )
        if not controller.set_screenshot_use_raw_size(True):
            raise RuntimeError("无法设置原始截图尺寸。")
        connection = controller.post_connection().wait()
        if not connection.succeeded or not controller.connected:
            raise RuntimeError("Win32Controller 连接离线靶场失败。")
        focus_offline_window(window.hwnd)
        time.sleep(0.4)

        before, ready_count = wait_for_color_capture(
            controller,
            READY_ROI,
            READY_LOWER_RGB,
            READY_UPPER_RGB,
            READY_COUNT,
            timeout_seconds=10.0,
        )
        before_path = run_dir / "before.png"
        write_image(before_path, before)
        if ready_count < READY_COUNT:
            raise RuntimeError(
                f"离线绿色状态守卫未通过：{ready_count} < {READY_COUNT}"
            )

        resource = Resource()
        resource_job = resource.post_bundle(OFFLINE_RESOURCE).wait()
        if not resource_job.succeeded or not resource.loaded:
            raise RuntimeError("离线 Pipeline 资源加载失败。")

        tasker = Tasker()
        if not tasker.bind(resource, controller) or not tasker.inited:
            raise RuntimeError("Tasker 绑定离线资源或控制器失败。")

        task_job = tasker.post_task(
            "SC.Offline.Click.Root",
            pipeline_override={
                CLICK_NODE: {
                    "enabled": True,
                }
            },
        ).wait()

        after, success_count = wait_for_color_capture(
            controller,
            SUCCESS_ROI,
            SUCCESS_LOWER_RGB,
            SUCCESS_UPPER_RGB,
            SUCCESS_COUNT,
            timeout_seconds=3.0,
        )
        after_path = run_dir / "after.png"
        write_image(after_path, after)

        report.update(
            {
                "framework_version": framework_version,
                "window_class": window.class_name,
                "hwnd_hex": window.hwnd_hex,
                "resolution": [window.client_width, window.client_height],
                "ready_color_count": ready_count,
                "success_color_count": success_count,
                "task_succeeded": task_job.succeeded,
                "before_image": str(before_path),
                "after_image": str(after_path),
            }
        )
        experiment_succeeded = (
            task_job.succeeded and success_count >= SUCCESS_COUNT
        )
        report["experiment_succeeded"] = experiment_succeeded
        report_path = run_dir / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if experiment_succeeded else 1
    except Exception as exc:
        report["experiment_succeeded"] = False
        report["error"] = str(exc)
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"ERROR: 离线点击实验失败：{exc}", file=sys.stderr)
        print(f"报告：{report_path}", file=sys.stderr)
        return 2
    finally:
        if controller is not None:
            inactive = controller.post_inactive().wait()
            if not inactive.succeeded:
                print(
                    "WARNING: Win32Controller 未能正常进入 inactive 状态。",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())

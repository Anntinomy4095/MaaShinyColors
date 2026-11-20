#!/usr/bin/env python3
"""Capture the uniquely identified Shiny Colors window with input methods Null."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path
from uuid import uuid4

try:
    from tools.manage_capture_session import PRIVATE_ROOT, private_path, require
    from tools.probe_browser import compile_patterns, discover_with_maa, select_browser_windows
except ModuleNotFoundError:
    from manage_capture_session import PRIVATE_ROOT, private_path, require
    from probe_browser import compile_patterns, discover_with_maa, select_browser_windows

# Verified against the pinned Maa 5.12.3 Python definitions. No input backend exists.
METHODS = {"framepool": 2, "screen": 32}


def select_target(windows):
    patterns = compile_patterns()
    matches = [item.window for item in select_browser_windows(windows, *patterns) if item.connection_ready]
    require(len(matches) == 1, "必须唯一识别到可见且未最小化的游戏浏览器窗口")
    return matches[0]


def encode_png(image):
    require(image.ndim == 3 and image.shape[2] == 3 and str(image.dtype) == "uint8", "截图格式无效")
    height, width = image.shape[:2]
    require(0 < width <= 8192 and 0 < height <= 8192, "截图尺寸超出支持范围")
    rgb = image[:, :, ::-1].tobytes()
    stride = width * 3
    rows = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def capture(output_dir, method="framepool", frames=1, discover=None, controller_factory=None):
    require(method in METHODS and type(frames) is int and 1 <= frames <= 3, "仅允许已知截图方式及1至3帧")
    output_dir = private_path(output_dir)
    require(not output_dir.exists(), "采集目录已存在，拒绝覆盖")
    discover = discover or discover_with_maa
    version, windows = discover()
    require(version == "v5.12.3", "采集要求MaaFramework v5.12.3")
    target = select_target(windows)
    if controller_factory is None:
        from maa.controller import Win32Controller
        from maa.define import LoggingLevelEnum
        from maa.tasker import Tasker
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        controller_factory = Win32Controller
    controller = controller_factory(target.hwnd, screencap_method=METHODS[method], mouse_method=0, keyboard_method=0)
    require(controller.set_screenshot_use_raw_size(True), "无法开启原尺寸截图")
    require(controller.post_connection().wait().succeeded, "只读截图连接失败")
    output_dir.mkdir(parents=True)
    report = {"version": 1, "capture_complete": False, "readonly": True, "input_enabled": False,
              "mouse_method": "Null", "keyboard_method": "Null", "framework_version": version,
              "screencap_method": method, "source": "browser_client_area_not_game_canvas",
              "pipeline_eligible": False, "frames": []}
    try:
        for index in range(frames):
            _, current_windows = discover()
            current = select_target(current_windows)
            require((current.hwnd, current.title, current.client_width, current.client_height)
                    == (target.hwnd, target.title, target.client_width, target.client_height), "窗口或页面标识发生变化")
            job = controller.post_screencap().wait()
            require(job.succeeded, "原尺寸截图失败")
            image = job.get()
            _, after_windows = discover()
            require(select_target(after_windows) == current, "截图期间窗口或页面标识发生变化")
            blob = encode_png(image)
            filename = f"frame-{index + 1:04d}.png"
            (output_dir / filename).write_bytes(blob)
            report["frames"].append({"file": filename, "sha256": hashlib.sha256(blob).hexdigest(),
                                     "dimensions": [image.shape[1], image.shape[0]],
                                     "pixel_range": int(image.max()) - int(image.min())})
        report["capture_complete"] = True
    except Exception as exc:
        report["error"] = str(exc)
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="仅截图唯一游戏浏览器窗口；不聚焦、不输入、不访问网页结构。")
    parser.add_argument("--method", choices=tuple(METHODS), default="framepool")
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=PRIVATE_ROOT / "window_captures" / uuid4().hex)
    args = parser.parse_args(argv)
    try:
        report = capture(args.output_dir, args.method, args.frames)
        print(json.dumps({"capture_complete": report["capture_complete"], "report": str(args.output_dir / "report.json"),
                          "dimensions": [frame["dimensions"] for frame in report["frames"]]}, ensure_ascii=False))
        return 0 if report["capture_complete"] else 2
    except Exception as exc:
        print(json.dumps({"capture_complete": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

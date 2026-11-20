"""Bounded, read-only client for the local browser screenshot bridge."""
from __future__ import annotations

import json
import re
import socket
import struct
from pathlib import Path

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_HEADER_BYTES = 16384


def validate_session(data):
    if not isinstance(data, dict) or set(data) != {"version", "host", "port", "token", "pid", "input_enabled"}:
        raise ValueError("截图桥会话字段无效")
    if type(data["version"]) is not int or data["version"] != 1 or data["host"] != "127.0.0.1":
        raise ValueError("截图桥只允许版本1和127.0.0.1")
    if type(data["port"]) is not int or not 1 <= data["port"] <= 65535:
        raise ValueError("截图桥端口无效")
    if type(data["pid"]) is not int or data["pid"] <= 0:
        raise ValueError("截图桥进程无效")
    if not isinstance(data["token"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", data["token"]):
        raise ValueError("截图桥会话令牌无效")
    if data["input_enabled"] is not False:
        raise ValueError("拒绝允许输入的截图桥")
    return dict(data)


def validate_status(status):
    if not isinstance(status, dict) or any(type(status.get(key)) is not bool for key in ("ready", "browser_demo", "input_enabled")):
        raise ValueError("截图桥状态字段无效")
    if status["input_enabled"] is not False:
        raise ValueError("截图桥输入必须关闭")
    if type(status.get("width")) is not int or type(status.get("height")) is not int or (status["width"], status["height"]) != (1280, 720):
        raise ValueError("截图桥画布必须为1280x720")
    return status


def validate_png(data):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("截图桥未返回PNG")
    if struct.unpack(">II", data[16:24]) != (1280, 720):
        raise ValueError("截图尺寸必须为1280x720")
    return data


class BrowserBridge:
    def __init__(self, session, timeout=5.0):
        self.session = validate_session(session)
        if not 0 < timeout <= 10:
            raise ValueError("截图桥超时须在0到10秒之间")
        self.timeout = timeout

    @classmethod
    def from_file(cls, path, **kwargs):
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")), **kwargs)

    def request(self, command):
        if command not in {"status", "screencap"}:
            raise ValueError("截图桥仅允许status和screencap，只读模式拒绝输入")
        with socket.create_connection((self.session["host"], self.session["port"]), timeout=self.timeout) as connection:
            connection.settimeout(self.timeout)
            payload = {"token": self.session["token"], "command": command}
            connection.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            with connection.makefile("rb") as stream:
                line = stream.readline(MAX_HEADER_BYTES + 1)
                if len(line) > MAX_HEADER_BYTES or not line.endswith(b"\n"):
                    raise ValueError("截图桥响应头过长或被截断")
                header = json.loads(line.decode("utf-8"))
                if not isinstance(header, dict) or type(header.get("ok")) is not bool:
                    raise ValueError("截图桥响应无效")
                if not header["ok"]:
                    raise RuntimeError("截图桥拒绝请求：" + str(header.get("error", "未知错误")))
                length = header.get("image_length")
                if type(length) is not int or not 0 <= length <= MAX_IMAGE_BYTES:
                    raise ValueError("截图桥图像长度无效")
                if command == "status":
                    if length != 0:
                        raise ValueError("状态响应不允许包含图像")
                    return validate_status(header.get("status"))
                if length < 24:
                    raise ValueError("截图桥图像为空或过短")
                image = stream.read(length)
                if len(image) != length:
                    raise RuntimeError("截图桥PNG传输被截断")
                return validate_png(image)

    def status(self):
        return self.request("status")

    def screencap(self):
        return self.request("screencap")


def create_screenshot_controller(bridge, evidence_dir):
    """Lazy Maa import preserves pure protocol unit testing."""
    import numpy as np
    from maa.controller import CustomController
    from tools.replay_screenshots import read_png

    class ReadOnlyBrowserController(CustomController):
        def __init__(self):
            self.frame_paths = []
            self.capture_error = None
            super().__init__()

        def connect(self): return True
        def request_uuid(self): return "SC.Browser.ReadOnlyCapture"
        def screencap(self):
            try:
                status = bridge.status()
                if not status["ready"]:
                    raise RuntimeError("浏览器截图尚未就绪")
                data = bridge.screencap()
                path = Path(evidence_dir) / f"frame-{len(self.frame_paths) + 1:03d}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                image = read_png(path)
                self.frame_paths.append(path)
                return image
            except Exception as exc:
                self.capture_error = str(exc)
                return np.empty((0, 0, 3), dtype=np.uint8)
        def deny(self, *args): return False
        start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args): return None

    return ReadOnlyBrowserController()

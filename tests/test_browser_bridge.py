from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core.browser_bridge import BrowserBridge, MAX_IMAGE_BYTES, create_screenshot_controller, validate_session
from tools.run_browser_task import main, wait_for_bridge

FIXTURE = Path(__file__).parent / "fixtures/replay/ready.png"
STATUS = {"ready": True, "browser_demo": True, "input_enabled": False, "width": 1280, "height": 720}


@contextlib.contextmanager
def bridge_server(header, image=b""):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    requests = []
    errors = []
    def serve():
        try:
            server.settimeout(2)
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2)
                with connection.makefile("rb") as stream:
                    requests.append(json.loads(stream.readline()))
                connection.sendall((json.dumps(header) + "\n").encode() + image)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    session = {"version": 1, "host": "127.0.0.1", "port": server.getsockname()[1], "token": "a" * 64, "pid": 123, "input_enabled": False}
    try:
        yield BrowserBridge(session, timeout=1), requests
    finally:
        thread.join(3)
        server.close()
        if errors:
            raise errors[0]


class BrowserBridgeTests(unittest.TestCase):
    def test_failed_capture_returns_empty_image_and_preserves_error(self):
        class FailedBridge:
            def status(self):
                raise RuntimeError("截图桥拒绝截图")
        empty_image = object()
        fake_numpy = types.SimpleNamespace(empty=lambda shape, dtype: empty_image if shape == (0, 0, 3) and dtype == "uint8" else None, uint8="uint8")
        fake_controller = types.SimpleNamespace(CustomController=type("CustomController", (), {}))
        with patch.dict("sys.modules", {"numpy": fake_numpy, "maa": types.ModuleType("maa"), "maa.controller": fake_controller}):
            controller = create_screenshot_controller(FailedBridge(), Path("unused"))
            self.assertIs(controller.screencap(), empty_image)
            self.assertEqual(controller.capture_error, "截图桥拒绝截图")
            self.assertEqual(controller.frame_paths, [])

    def test_loopback_and_disabled_input_are_required(self):
        session = {"version": 1, "host": "127.0.0.1", "port": 1234, "token": "a" * 64, "pid": 123, "input_enabled": False}
        for key, value in [("host", "example.com"), ("host", "localhost"), ("input_enabled", True), ("port", True), ("port", 65536), ("token", "secret"), ("pid", 0)]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_session({**session, key: value})

    def test_commands_cannot_send_input(self):
        bridge = BrowserBridge({"version": 1, "host": "127.0.0.1", "port": 1234, "token": "a" * 64, "pid": 123, "input_enabled": False})
        with patch("socket.create_connection") as connection:
            for command in ("click", "input_text", "shell", "custom", "start_app"):
                with self.assertRaises(ValueError):
                    bridge.request(command)
            connection.assert_not_called()

    def test_status_protocol_sends_only_readonly_command(self):
        with bridge_server({"ok": True, "image_length": 0, "status": STATUS}) as (bridge, requests):
            self.assertEqual(bridge.status(), STATUS)
        self.assertEqual(requests, [{"token": "a" * 64, "command": "status"}])

    def test_screencap_returns_exact_png_bytes(self):
        image = FIXTURE.read_bytes()
        with bridge_server({"ok": True, "image_length": len(image)}, image) as (bridge, requests):
            self.assertEqual(bridge.screencap(), image)
        self.assertEqual(requests[0]["command"], "screencap")

    def test_remote_error_is_reported(self):
        with bridge_server({"ok": False, "image_length": 0, "error": "not ready"}) as (bridge, _):
            with self.assertRaisesRegex(RuntimeError, "not ready"):
                bridge.screencap()

    def test_truncated_image_is_rejected(self):
        with bridge_server({"ok": True, "image_length": 100}, b"short") as (bridge, _):
            with self.assertRaises(RuntimeError):
                bridge.screencap()

    def test_oversized_image_is_rejected_before_reading(self):
        with bridge_server({"ok": True, "image_length": MAX_IMAGE_BYTES + 1}) as (bridge, _):
            with self.assertRaises(ValueError):
                bridge.screencap()

    def test_wrong_dimensions_are_rejected(self):
        image = (FIXTURE.parent / "invalid_size.png").read_bytes()
        with bridge_server({"ok": True, "image_length": len(image)}, image) as (bridge, _):
            with self.assertRaises(ValueError):
                bridge.screencap()

    def test_status_must_confirm_canonical_canvas_and_input_disabled(self):
        for update in ({"input_enabled": True}, {"width": 640}, {"ready": "true"}):
            with self.subTest(update=update):
                with bridge_server({"ok": True, "image_length": 0, "status": {**STATUS, **update}}) as (bridge, _):
                    with self.assertRaises(ValueError):
                        bridge.status()

    def test_wait_does_not_hide_invalid_configuration(self):
        with patch("tools.run_browser_task.BrowserBridge.from_file", side_effect=ValueError("invalid")), patch("tools.run_browser_task.time.sleep") as sleep:
            with self.assertRaises(ValueError):
                wait_for_bridge(Path("unused"), 20)
            sleep.assert_not_called()

    def test_sample_requires_neither_maa_nor_session_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            with patch("tools.run_browser_task.BrowserBridge.from_file") as bridge, contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["--session", "missing.json", "--task", "produce-sample", "--report", str(report)])
            bridge.assert_not_called()
            self.assertEqual(code, 0)
            self.assertIn("样例", json.loads(output.getvalue())["summary"])
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["sample_only"])


if __name__ == "__main__":
    unittest.main()

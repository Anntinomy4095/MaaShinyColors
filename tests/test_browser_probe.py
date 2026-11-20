from __future__ import annotations

import unittest

from tools.probe_browser import (
    WindowRecord,
    compile_patterns,
    redact_title,
    report_payload,
    select_browser_windows,
)


class BrowserProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.class_pattern, self.title_pattern = compile_patterns()

    def test_selects_ready_shiny_colors_chrome_window(self) -> None:
        windows = [
            WindowRecord(
                hwnd=42,
                class_name="Chrome_WidgetWin_1",
                title="アイドルマスター シャイニーカラーズ",
                client_width=1280,
                client_height=720,
            )
        ]

        matches = select_browser_windows(
            windows,
            self.class_pattern,
            self.title_pattern,
        )

        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].target_match)
        self.assertTrue(matches[0].connection_ready)

    def test_browser_candidate_is_not_target_without_title_match(self) -> None:
        windows = [
            WindowRecord(
                hwnd=7,
                class_name="MozillaWindowClass",
                title="Example",
                client_width=1280,
                client_height=720,
            )
        ]

        matches = select_browser_windows(
            windows,
            self.class_pattern,
            self.title_pattern,
        )

        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0].target_match)
        self.assertFalse(matches[0].connection_ready)

    def test_minimized_target_is_not_connection_ready(self) -> None:
        windows = [
            WindowRecord(
                hwnd=8,
                class_name="Chrome_WidgetWin_1",
                title="Shiny Colors",
                client_width=1280,
                client_height=720,
                minimized=True,
            )
        ]

        matches = select_browser_windows(
            windows,
            self.class_pattern,
            self.title_pattern,
        )

        self.assertTrue(matches[0].target_match)
        self.assertFalse(matches[0].connection_ready)

    def test_japanese_short_title_is_target(self) -> None:
        windows = [
            WindowRecord(
                hwnd=10,
                class_name="Chrome_WidgetWin_1",
                title="シャニマス",
                client_width=1280,
                client_height=720,
            )
        ]

        matches = select_browser_windows(
            windows,
            self.class_pattern,
            self.title_pattern,
        )

        self.assertTrue(matches[0].target_match)
        self.assertTrue(matches[0].connection_ready)

    def test_browser_host_window_is_target(self) -> None:
        windows = [
            WindowRecord(
                hwnd=11,
                class_name="WindowsForms10.Window.8.app.0.2bf8098_r3_ad1",
                title="MaaShinyColors BrowserHost",
                client_width=1280,
                client_height=720,
            )
        ]

        matches = select_browser_windows(
            windows,
            self.class_pattern,
            self.title_pattern,
        )

        self.assertTrue(matches[0].target_match)
        self.assertTrue(matches[0].connection_ready)

    def test_non_browser_window_is_ignored(self) -> None:
        windows = [
            WindowRecord(
                hwnd=9,
                class_name="Notepad",
                title="Shiny Colors notes",
                client_width=1280,
                client_height=720,
            )
        ]

        matches = select_browser_windows(
            windows,
            self.class_pattern,
            self.title_pattern,
        )

        self.assertEqual(matches, [])

    def test_non_target_title_is_redacted_by_default(self) -> None:
        self.assertEqual(
            redact_title("Private tab", target_match=False, show_titles=False),
            "<已脱敏：11 字符>",
        )
        self.assertEqual(
            redact_title("Shiny Colors", target_match=True, show_titles=False),
            "Shiny Colors",
        )

    def test_report_keeps_enumerated_window_count(self) -> None:
        payload = report_payload(
            "v5.12.3",
            [],
            show_titles=False,
            enumerated_window_count=12,
        )

        self.assertEqual(payload["enumerated_window_count"], 12)
        self.assertFalse(payload["safety"]["controller_created"])


if __name__ == "__main__":
    unittest.main()

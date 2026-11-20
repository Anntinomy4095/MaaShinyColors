"""Static contracts for the default-off BrowserHost background keepalive."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "browser_host"


def _read(name: str) -> str:
    return (HOST / name).read_text(encoding="utf-8")


class BackgroundKeepaliveContractTests(unittest.TestCase):
    def test_window_state_is_sampled_only_by_the_unified_guard(self) -> None:
        form = _read("CompositionDemoForm.cs")
        self.assertEqual(form.count("FormWindowState.Minimized"), 3)
        self.assertIn("window.WindowState == FormWindowState.Minimized) { return; }", form)
        snapshot = form[form.index("private HostSnapshot Snapshot()"):]
        self.assertIn("FormWindowState.Minimized", snapshot[: snapshot.index(";")])
        # No guard may re-introduce the scattered visible/minimized checks.
        self.assertNotRegex(form, r"!Visible\s*\|\|")
        self.assertGreaterEqual(form.count("HostGuardReason is not null"), 5)

    def test_controller_is_never_hidden_or_suspended(self) -> None:
        for path in HOST.glob("*.cs"):
            source = path.read_text(encoding="utf-8")
            for pattern in (r"\.TrySuspendAsync\(", r"IsVisible\s*=\s*false", r"MemoryUsageTargetLevel\s*="):
                self.assertIsNone(re.search(pattern, source), f"{path.name}: {pattern}")

    def test_docked_game_browser_arguments_allow_live_toggling(self) -> None:
        options = _read("BrowserHostOptions.cs")
        self.assertIn("bool BackgroundKeepalive = false", options)
        self.assertIn('case "--background-keepalive":', options)
        form = _read("CompositionDemoForm.cs")
        self.assertIn("_keepaliveArguments = (_options.BackgroundKeepalive || (_options.CompositionGame && _uiDock is not null))", form)
        self.assertIn("_browserArguments = HostGuard.GameArguments(_keepaliveArguments);", form)
        # 2026-11-04: every game environment pins the locale and sRGB output, whatever the OS language or monitor.
        guard = _read("HostGuard.cs")
        self.assertIn('GameLanguage = "en-US"', guard)
        self.assertIn('RenderArguments = "--force-color-profile=srgb"', guard)
        self.assertIn("Language = HostGuard.GameLanguage", form)
        self.assertIn("Language = HostGuard.GameLanguage", _read("BrowserHostForm.cs"))
        launcher = (ROOT / "tools/packaging/launcher/Launcher.cs").read_text(encoding="utf-8")
        self.assertNotIn("--background-keepalive", launcher)
        # 2026-10-13: 后台运行 is back to default off (UiHostPrefs.BackgroundRun = false); the CLI flag stays an override.
        self.assertIn(
            "_options.BackgroundKeepalive || (_options.CompositionGame && _uiDock is not null && _prefs.BackgroundRun)", form)
        prefs = (HOST / "Ui" / "UiHostPrefs.cs").read_text(encoding="utf-8")
        self.assertIn("bool BackgroundRun = false", prefs)
        self.assertIn('["background_run"] = BackgroundRun', prefs)

    def test_window_options_live_in_settings_not_on_the_main_window(self) -> None:
        form = _read("CompositionDemoForm.cs")
        for banned in ("最小化改为隐藏到托盘", "隐藏到后台", "new CheckBox", "_keepIntercept", "_chromeControls"):
            self.assertNotIn(banned, form)
        router = (HOST / "Ui" / "UiMessageRouter.cs").read_text(encoding="utf-8")
        self.assertIn('case "window.background":', router)
        self.assertIn("_uiDock.Router.BackgroundRun =", form)
        settings = (ROOT / "ui" / "components" / "settings-panel.js").read_text(encoding="utf-8")
        for snippet in ('title: "窗口"', '"后台运行"', '"现在隐藏"', "actions.windowBackground", "actions.windowHide"):
            self.assertIn(snippet, settings)
        for path in ("header.js", "home-panel.js", "daily-panel.js"):
            text = (ROOT / "ui" / "components" / path).read_text(encoding="utf-8")
            self.assertNotIn("window.background", text)
            self.assertNotIn("windowBackground", text)
        keeper = _read("BackgroundKeeper.cs")
        self.assertIn("public bool Enabled", keeper)
        # Restoring: tray left click and a restore command while hidden both bring the window back.
        self.assertIn("mouse.Button == MouseButtons.Left", keeper)
        self.assertIn("ScRestore", keeper)
        # Hiding by minimize or 「现在隐藏」 shows a tray notification; the tray menu's 隐藏 does not.
        self.assertIn('ShowNotification("MaaShinyColors 已隐藏到托盘"', keeper)
        self.assertIn("notify && only is null && _tray is not null", keeper)
        self.assertIn('menu.Items.Add("隐藏", null, (_, _) => { if (Enabled) Hide(notify: false); });', keeper)
        guard = _read("HostGuard.cs")
        for flag in (
            "--disable-features=CalculateNativeWinOcclusion",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-background-timer-throttling",
        ):
            self.assertIn(flag, guard)

    def test_hide_never_minimizes_or_recreates_the_window(self) -> None:
        keeper = _read("BackgroundKeeper.cs")
        self.assertIn("DwmwaCloak = 13", keeper)
        self.assertNotRegex(keeper, r"ShowInTaskbar\s*=")
        self.assertNotRegex(keeper, r"WindowState\s*=")
        self.assertNotIn("SendInput", keeper)
        self.assertNotIn("SetCursorPos", keeper)
        # A hidden window must not take the foreground on an automated gesture.
        self.assertIn("NoActivate(handle, true)", keeper)
        self.assertRegex(keeper, re.escape('DefaultOrder = { Cloaked, Offscreen }'))

    def test_status_contract_fields(self) -> None:
        form = _read("CompositionDemoForm.cs")
        for field in ("hide_mode =", "frame_age_ms =", "frame_changing =", "host_guard_reason =", "desktop_locked ="):
            self.assertIn(field, form)

    def test_heartbeat_page_is_local_and_packaged(self) -> None:
        page = (HOST / "demo" / "heartbeat.html").read_text(encoding="utf-8")
        self.assertIn("default-src 'none'", page)
        self.assertNotRegex(page, r"https?://")
        self.assertIn("requestAnimationFrame", page)
        self.assertIn("setInterval", page)
        self.assertIn(r"demo\heartbeat.html", _read("MaaShinyColors.BrowserHost.csproj"))


if __name__ == "__main__":
    unittest.main()

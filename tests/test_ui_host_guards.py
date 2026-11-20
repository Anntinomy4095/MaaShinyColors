"""Static guards for the U1 UI panel host (U1).

The UI panel WebView is isolated from the game WebView: the host never injects script, never calls the DevTools
Protocol, never touches cookies, and only the UI core (inside browser_host/Ui/) may post web messages. The offline
UI preview can never be combined with the game host modes.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "browser_host"
UI_HOST = HOST / "Ui"

END_OF_METHOD = "\n    }\n"

FORBIDDEN_EVERYWHERE = (
    "ExecuteScriptAsync",
    "AddScriptToExecuteOnDocumentCreated",
    "CallDevToolsProtocolMethod",
    "CookieManager",
    "GetDevToolsProtocolEventReceiver",
)


def _sources() -> list[Path]:
    return sorted(
        path for path in HOST.rglob("*.cs")
        if not any(part in {"bin", "obj"} for part in path.relative_to(HOST).parts)
    )


def _code(path: Path) -> str:
    """Source without // comments and string literals, so prose cannot trip or hide a match."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'"""[\s\S]*?"""', '""', text)
    text = re.sub(r'@?\$?"(?:[^"\\\n]|\\.)*"', '""', text)
    return re.sub(r"//[^\n]*", "", text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class UiHostGuardTests(unittest.TestCase):
    def test_ui_files_exist(self) -> None:
        for name in ("UiShellForm.cs", "UiPanelHost.cs", "UiMessageRouter.cs", "UiProtocol.cs", "UiSelfTest.cs", "UiProfileStore.cs",
                     "UiEngineSupervisor.cs", "UiEngineSelfTest.cs", "UiGameDock.cs"):
            self.assertTrue((UI_HOST / name).is_file(), name)

    def test_no_script_injection_devtools_protocol_or_cookies_anywhere(self) -> None:
        sources = _sources()
        self.assertGreater(len(sources), 30)
        for path in sources:
            code = _code(path)
            for api in FORBIDDEN_EVERYWHERE:
                self.assertNotIn(api, code, f"{path.relative_to(ROOT)} uses {api}")

    def test_post_web_message_only_on_the_ui_core(self) -> None:
        for path in _sources():
            if "PostWebMessage" not in _code(path):
                continue
            self.assertEqual(path.parent, UI_HOST, f"{path.relative_to(ROOT)} posts web messages outside browser_host/Ui")
        users = [path.name for path in UI_HOST.glob("*.cs") if "PostWebMessage" in _code(path)]
        self.assertEqual(users, ["UiPanelHost.cs"])

    def test_ui_code_never_touches_game_capture_or_input(self) -> None:
        banned = ("CaptureBridge", "CompositionDemoForm", "BrowserHostOptions", "PipelineInput", "SendMouseInput",
                  "CompositionController", "AdditionalBrowserArguments", "BackgroundArguments", "HostGuard",
                  "SelectionLoop", "RepeatableNavigation", "SupervisedDaily", "Process.Start")
        for path in UI_HOST.glob("*.cs"):
            code = _code(path)
            for name in banned:
                self.assertNotIn(name, code, f"{path.name} references {name}")
        # The only screenshot is of the UI panel itself, taken by the panel host.
        capture_users = [path.name for path in UI_HOST.glob("*.cs") if "CapturePreviewAsync" in _code(path)]
        self.assertEqual(capture_users, ["UiPanelHost.cs"])

    def test_panel_isolation_settings(self) -> None:
        panel = _read(UI_HOST / "UiPanelHost.cs")
        for snippet in (
            "settings.AreHostObjectsAllowed = false;",
            "settings.AreDefaultContextMenusEnabled = false;",
            "settings.IsPasswordAutosaveEnabled = false;",
            "settings.IsGeneralAutofillEnabled = false;",
            "settings.IsStatusBarEnabled = false;",
            "settings.AreDevToolsEnabled = _devTools;",
            "CoreWebView2HostResourceAccessKind.Deny",
            'AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All)',
            "args.Handled = true;",
            "CoreWebView2PermissionState.Deny",
            "core.DownloadStarting",
            "core.FrameNavigationStarting",
            "CoreWebView2Environment.CreateAsync(null, _userDataFolder",
        ):
            self.assertIn(snippet, panel)
        self.assertEqual(panel.count("SetVirtualHostNameToFolderMapping("), 1)
        self.assertIn("SetVirtualHostNameToFolderMapping(UiProtocol.VirtualHost, _uiFolder, CoreWebView2HostResourceAccessKind.Deny)", panel)
        self.assertIn('internal const string VirtualHost = "maa-ui.test";', _read(UI_HOST / "UiProtocol.cs"))

    def test_router_accepts_only_the_exact_ui_page(self) -> None:
        protocol = json.loads((ROOT / "ui" / "protocol.json").read_text(encoding="utf-8"))
        source = protocol["source"]
        self.assertEqual(f"{source['scheme']}://{source['host']}{source['path']}", "https://maa-ui.test/index.html")
        router = _read(UI_HOST / "UiMessageRouter.cs")
        self.assertIn("string.Equals(source, _protocol.SourceUri, StringComparison.Ordinal)", router)
        self.assertIn("_protocol.Commands.Contains(command)", router)
        self.assertIn("_protocol.Validate(parameters, _protocol.CommandParams(command)", router)
        self.assertIn("MaxMessageBytes", router)
        self.assertIn("MaxMessagesPerSecond", router)

    def test_page_spend_flags_are_never_trusted(self) -> None:
        # spend_flags from the page are read once, only to be recorded next to the host-derived set.
        text = _read(UI_HOST / "UiMessageRouter.cs")
        self.assertEqual(text.count('"spend_flags"'), 1)
        # 2026-10-15: a daily single-task run passes savedEnabledRequired; the flags are still host-derived.
        self.assertIn("_schema.DeriveSpendFlags(_profiles.Load(), requested", text)
        store = _read(UI_HOST / "UiProfileStore.cs")
        derive = store[store.index("internal IReadOnlyList<string> DeriveSpendFlags"):]
        derive = derive[: derive.index("\n    }\n")]
        self.assertNotIn("spend_flags\"", derive.replace('["spend_flags"]', ""))
        self.assertIn("SpendWhen", derive)

    def test_run_commands_do_not_launch_anything_in_u1(self) -> None:
        router = _code(UI_HOST / "UiMessageRouter.cs")
        for name in ("Process", "run_tasks", "File.Delete"):
            self.assertNotIn(name, router)

    def test_only_the_engine_supervisor_starts_processes(self) -> None:
        # U2: the supervisor is the single place in the host that creates a child process.
        users = sorted(path.name for path in _sources() if re.search(r"new Process|ProcessStartInfo|Process\.Start", _code(path)))
        # BrowserHostForm.cs is the pre-existing legacy demo host (its own daily button), unchanged by U2.
        # UiCardScan.cs: the 「扫描持有卡」 supervisor, the only place that starts the supervised --live CardScan;
        # UiCardScanSelfTest.cs only starts a trivial PowerShell child to exercise it.
        # UiKnowhowScan.cs: the 「扫描ノウハウ」 supervisor (same mechanics, supervised --live KnowhowScan only);
        # UiKnowhowScanSelfTest.cs only starts a trivial PowerShell child to exercise it.
        # UiTeamPropose.cs (U05, 2026-10-12): 「重新生成推荐」 runs only the offline tools/auto_team_propose.py (local files,
        # no game frame, no input); one process at a time with a timeout.
        # ShellOpen.cs (2026-10-15 设置 → 问题反馈 / 关于 / 更新): Explorer at a host-chosen dev_assets/private folder or file and
        # the default browser at https://github.com/<interface.json github> pages; the page never supplies a path or URL.
        # UiCardLibrary: fixed public-data updater, independent of game input; its self-test uses local fixture children.
        self.assertEqual(users, ["BrowserHostForm.cs", "ShellOpen.cs", "UiCardLibrary.cs", "UiCardLibrarySelfTest.cs", "UiCardScan.cs", "UiCardScanSelfTest.cs",
                                 "UiEngineSupervisor.cs", "UiKnowhowScan.cs", "UiKnowhowScanSelfTest.cs", "UiTeamPropose.cs"])

    def test_card_scan_supervisor_only_launches_the_fixed_supervised_scan(self) -> None:
        text = re.sub(r"//[^\n]*", "", _read(UI_HOST / "UiCardScan.cs"))
        self.assertIn('"--live", "--supervised-task"', text)
        for banned in ("--allow", "--simulate-input", "--offline-frames", "cmd.exe", "UseShellExecute = true"):
            self.assertNotIn(banned, text)
        self.assertEqual(text.count("--live"), 1)
        for snippet in ('["allow_spend"] = new JsonArray()', "FileMode.CreateNew", "Kill(entireProcessTree: true)", "dev_assets/private",
                        "CooperativeGraceMs", '"stop.flag"'):
            self.assertIn(snippet, text)
        # No other host source names the live scan command line.
        others = [path.name for path in _sources() if path.name not in ("UiCardScan.cs",) and "supervised-task" in _code(path)]
        self.assertEqual(others, [])

    def test_python_children_use_utf8_mode_on_any_code_page(self) -> None:
        # 2026-11-04: a Chinese Windows (code page 936) must read and write the same UTF-8 files as en-US.
        text = _read(UI_HOST / "UiEngineSupervisor.cs")
        start = text[text.index("internal static ProcessStartInfo NewStartInfo"):]
        self.assertIn('info.Environment["PYTHONUTF8"] = "1";', start[:start.index("return info;")])

    def test_engine_supervisor_never_passes_live_or_spend_flags(self) -> None:
        text = re.sub(r"//[^\n]*", "", _read(UI_HOST / "UiEngineSupervisor.cs"))  # comments may name the banned flags
        # 2026-10-11: the only exception is W.I.N.G. — `--wing` (the engine itself adds --live --supervised-task under the
        # CLI's validation) and `--allow-produce-spend`, passed only for a W.I.N.G. launch the page confirmed.
        self.assertEqual(text.count('"--allow-produce-spend"'), 1)
        self.assertIn('if (launch.AllowProduceSpend) { info.ArgumentList.Add("--allow-produce-spend"); }', text)
        self.assertIn("wing && allowProduceSpend", text)
        text = text.replace('"--allow-produce-spend"', "")
        # UiWingGate reads the host's own pipeline-input state as a precondition; it never sets it.
        self.assertIn('host["pipeline_input_enabled"]?.GetValue<bool>() != true', text)
        text = text.replace('host["pipeline_input_enabled"]', "")
        for banned in ("--live", "--allow", "--simulate-input", "run_tasks.py", "input_enabled", "allow_spend"):
            self.assertNotIn(banned, text)
        self.assertIn('internal const string EngineScript = "tools/ui_engine.py";', text)
        for snippet in ('"--profile"', '"--events"', '"--stop-file"', '"--summary"', '"--session"', '"--offline-frames"',
                        "Kill(entireProcessTree: true)", "FileMode.CreateNew", '"stop.flag"', '"events.jsonl"',
                        "CooperativeGraceMs", "EmergencyGraceMs", "StartupTimeoutMs", "MaxEventBytes", "MaxLineBytes"):
            self.assertIn(snippet, text)
        # Success needs both the event and the summary; a dead or killed child is uncertain.
        self.assertIn("A success needs both the event and the summary.", _read(UI_HOST / "UiEngineSupervisor.cs"))
        self.assertIn('"uncertain", "引擎被终止，结果不确定"', text)
        self.assertIn('"interrupted" => ("uncertain"', text)
        self.assertIn('"timeout" => ("uncertain"', text)
        self.assertIn("dev_assets/private", text)

    def test_router_run_start_uses_host_sanitised_tasks_and_opaque_frames(self) -> None:
        router = _read(UI_HOST / "UiMessageRouter.cs")
        self.assertIn("_profiles.Sanitize(", router)
        self.assertIn("SameTasks(requested, clean)", router)
        self.assertIn("_engine.Start(", router)
        start = router[router.index("private string StartRun"):]
        start = start[: start.index(END_OF_METHOD)]
        self.assertNotIn("spend_flags", start)
        self.assertIn("char.IsAsciiLetterOrDigit(c) || c is '_' or '-'", router)

    def test_game_host_docks_new_panel_and_keeps_canvas(self) -> None:
        form = _read(HOST / "CompositionDemoForm.cs")
        self.assertIn("new Ui.UiGameDock(UiStatus, () => _bridgeSessionPath, new Ui.HostDevTools(", form)
        self.assertIn("new Panel { Location = new Point(0, 44), Size = new Size(1280, 720)", form)
        self.assertIn("_webView.Bounds = new Rectangle(0, 0, 1280, 720);", form)
        self.assertIn("new Panel { Location = new Point(1280, 0)", form)
        legacy = form.index("else if (options.CompositionGame)")
        self.assertGreater(form.index("new DailySettingsPanel("), legacy)
        status = form[form.index("private System.Text.Json.Nodes.JsonObject UiStatus()"):]
        status = status[: status.index(END_OF_METHOD)]
        for banned in ("CaptureImage", "CapturePreviewAsync", "SendMouseInput", "InvalidateFrame"):
            self.assertNotIn(banned, status)
        options = _read(HOST / "BrowserHostOptions.cs")
        self.assertIn('case "--legacy-settings-panel":', options)
        self.assertIn("legacySettingsPanel && !compositionGame", options)
        self.assertIn("uiPanel && !compositionDemo", options)
        self.assertIn("public bool DockedUiPanel => (CompositionGame && !LegacySettingsPanel) || (CompositionDemo && UiPanel);", options)
        dock = _code(UI_HOST / "UiGameDock.cs")
        self.assertIn("ui-webview2-profile", _read(UI_HOST / "UiGameDock.cs"))
        self.assertNotIn("AdditionalBrowserArguments", dock)
        program = _read(HOST / "Program.cs")
        self.assertLess(program.index('"--self-test-ui-dock"'), program.index("BrowserHostOptions.Parse(args)"))

    def test_program_branches_precede_options_and_reject_combinations(self) -> None:
        program = _read(HOST / "Program.cs")
        parse = program.index("BrowserHostOptions.Parse(args)")
        preview = program.index("args[0] == Ui.UiShellOptions.PreviewSwitch")
        self_test = program.index('args.SequenceEqual(new[] { "--self-test-ui" })')
        self.assertLess(preview, parse)
        self.assertLess(self_test, parse)
        branch = program[preview: program.index("return;", preview)]
        self.assertNotIn("BrowserHostOptions", branch)
        self.assertNotIn("CompositionDemoForm", branch)
        self.assertIn("UiShellOptions.ForPreview(args.Skip(1).ToArray())", branch)
        shell = _read(UI_HOST / "UiShellForm.cs")
        self.assertIn('internal const string PreviewSwitch = "--ui-preview";', shell)
        self.assertIn('internal const string DevToolsSwitch = "--ui-devtools";', shell)
        parser = shell[shell.index("internal static UiShellOptions ForPreview"):]
        parser = parser[: parser.index("var root")]
        self.assertIn("argument == DevToolsSwitch && !devTools", parser)
        self.assertIn("throw new ArgumentException", parser)
        self.assertNotIn("--ui-preview", _read(HOST / "BrowserHostOptions.cs"))
        self.assertNotIn("--composition-game", re.sub(r"//[^\n]*", "", shell))

    def test_csproj_embeds_protocol_and_copies_ui(self) -> None:
        project = _read(HOST / "MaaShinyColors.BrowserHost.csproj")
        self.assertIn('<EmbeddedResource Include="..\\ui\\protocol.json" LogicalName="SC.UiProtocol.json" />', project)
        self.assertRegex(project, r'<Content Include="\.\.\\ui\\\*\*\\\*" Link="ui\\%\(RecursiveDir\)%\(Filename\)%\(Extension\)"')
        self.assertIn('"SC.UiProtocol.json"', _read(UI_HOST / "UiProtocol.cs"))

    def test_profile_writes_are_atomic_and_private(self) -> None:
        store = _read(UI_HOST / "UiProfileStore.cs")
        self.assertIn("File.Move(temp, Path, overwrite: true);", store)
        shell = _read(UI_HOST / "UiShellForm.cs")
        self.assertIn('Path.Combine(data, "ui", "profile.json")', shell)
        self.assertIn('Path.Combine(data, "ui-webview2-profile")', shell)
        self.assertIn('var data = Path.Combine(root, "dev_assets", "private");', shell)

    def test_shell_palette_mirrors_ui_tokens(self) -> None:
        # the host chrome (header/footer beside the game) uses exactly the panel's colour tokens in both themes.
        shell = _read(UI_HOST / "UiShellForm.cs")
        body = shell[shell.index("internal static ShellPalette For(bool dark)"):]
        body = body[: body.index("}")]
        dark = re.findall(r'C\("(#[0-9A-Fa-f]{6})"\)', body[: body.index(": new(")])
        light = re.findall(r'C\("(#[0-9A-Fa-f]{6})"\)', body[body.index(": new("):])
        order = ("--sc-bg", "--sc-canvas", "--sc-border", "--sc-text", "--sc-text-2", "--sc-warning", "--sc-surface",
                 "--sc-umbrella-fill", "--sc-umbrella-line", "--sc-umbrella-rib", "--sc-umbrella-handle", "--sc-umbrella-shine")
        tokens = _read(ROOT / "ui" / "styles" / "tokens.css")
        def block(selector: str) -> dict[str, str]:
            start = tokens.index(selector + " {")
            text = tokens[start: tokens.index("}", start)]
            return {k: v.upper() for k, v in re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", text)}
        light_tokens, dark_tokens = block(":root"), block(':root[data-theme="dark"]')
        self.assertEqual([c.upper() for c in light], [light_tokens[t] for t in order])
        self.assertEqual([c.upper() for c in dark], [dark_tokens[t] for t in order])

    def test_docked_host_paints_one_header_beside_the_unchanged_viewport(self) -> None:
        form = _read(HOST / "CompositionDemoForm.cs")
        # The old grey text strip only remains for the legacy/demo layouts without the docked panel.
        strip = form.index('"MaaShinyColors · 游戏画布 1280×720 · 后台输入实验需单独启用"')
        self.assertGreater(strip, form.index("if (!options.DockedUiPanel)"))
        self.assertIn("new Ui.UiHostChrome(Ui.UiHostChrome.Part.Header", form)
        self.assertIn("{ Bounds = new Rectangle(0, 0, 1280, 44) }", form)
        self.assertIn("{ Bounds = new Rectangle(0, 764, 1280, 24) }", form)
        dock = _read(UI_HOST / "UiGameDock.cs")
        chrome = dock[dock.index("internal sealed class UiHostChrome"):]
        for banned in ("CaptureImage", "CapturePreviewAsync", "SendMouseInput", "CoreWebView2", "PostWebMessage"):
            self.assertNotIn(banned, chrome)
        self.assertIn("Router.ProfileSaved +=", dock)
        header = _read(ROOT / "ui" / "components" / "header.js")
        self.assertIn('state.bridgeKind === "mock" ? "app-header"', header)
        # 2026-10-16 面板位置: the window-button gap only when the panel is on the right (default); on the left the host
        # draws the buttons over the game header instead.
        self.assertIn('state.hostKind !== "browser_host" ? "app-header is-hosted"', header)
        self.assertIn('panel_side === "left" ? "app-header is-hosted is-panel-left"', header)
        self.assertIn('panel_side === "detached" ? "app-header is-hosted is-panel-detached" : "app-header is-hosted has-window-controls"', header)
        css = _read(ROOT / "ui" / "styles" / "app.css")
        self.assertIn(".app-header.is-hosted .brand { display: none; }", css)

    def test_dock_self_test_never_uses_the_user_profile(self) -> None:
        selftest = _read(HOST / "UiDockSelfTest.cs")
        self.assertIn('foreach (var theme in new[] { "light", "dark" })', selftest)
        self.assertIn("Ui.UiGameDock.SelfTestPaths = (uiProfile,", selftest)
        self.assertIn("Ui.UiGameDock.SelfTestPaths = null;", selftest)
        self.assertNotIn("SelfTestPaths", _read(HOST / "CompositionDemoForm.cs"))

    def test_docked_host_is_frameless_resizable_and_keeps_the_game_fixed(self) -> None:
        # no system title bar/frame in the docked layout; the header is the caption. The title string
        # stays (tools find the host by MainWindowTitle), minimize/system menu stay. The window is
        # resizable and maximizable. The whole dock scales for display only; the game WebView keeps
        # rendering, capturing and receiving program input at exactly 1280x720.
        form = _read(HOST / "CompositionDemoForm.cs")
        self.assertIn("FormBorderStyle = options.DockedUiPanel ? FormBorderStyle.None : FormBorderStyle.FixedSingle;", form)
        self.assertIn("Text = options.WindowTitle;", form)
        self.assertIn('CompositionGameTitle = "MaaShinyColors"', _read(HOST / "BrowserHostOptions.cs"))
        # WS_THICKFRAME so the system runs the edge-drag sizing loop; WM_NCCALCSIZE removes the frame again.
        self.assertIn("parameters.Style |= WsMinimizeBox | WsMaximizeBox | WsSysMenu | WsThickFrame;", form)
        self.assertIn("if (message.Msg == WmNcCalcSize && Frameless) { message.Result = IntPtr.Zero; return; }", form)
        # host-20261011d: after a system minimize/restore DefWindowProc painted the classic caption over the header;
        # the frameless window never lets it paint the non-client area (activation state still changes).
        self.assertIn("if (Frameless && SuppressClassicFrame(ref message)) { return; }", form)
        suppress = form[form.index("private bool SuppressClassicFrame(ref Message message)"):]
        suppress = suppress[: suppress.index(END_OF_METHOD)]
        for message in ("case WmNcActivate:", "case WmNcPaint:", "case WmNcUahDrawCaption:", "case WmNcUahDrawFrame:",
                        "case WmSetText:", "case WmSetIcon:", "message.LParam = (IntPtr)(-1);"):
            self.assertIn(message, suppress)
        for banned in ("_webView", "CaptureImage", "SendMouseInput", "InvalidateFrame"):
            self.assertNotIn(banned, suppress)
        self.assertIn("protected override Size SizeFromClientSize(Size clientSize) => Frameless ? clientSize : base.SizeFromClientSize(clientSize);", form)
        self.assertIn("parameters.ClassStyle |= CsDropShadow;", form)
        self.assertNotIn("((long)message.WParam & 0xFFF0) == ScMaximize", form)   # maximize is no longer swallowed
        layout = form[form.index("private void ApplyDockLayout()"):]
        layout = layout[: layout.index(END_OF_METHOD)]
        for banned in ("_webView", "CaptureImage", "SendMouseInput", "InvalidateFrame"):
            self.assertNotIn(banned, layout)
        self.assertIn("GameDisplay.Compute(ClientSize, border, MinPanelPx,", layout)
        self.assertIn("ChromeScale, PanelLeft, PanelDetached);", layout)
        self.assertIn("SetGameDisplay(layout.Game)", layout)
        # the panel keeps its own size (zoom 1); only the game column scales. A detached panel's
        # holder belongs to its own window, so the dock layout leaves its bounds alone.
        self.assertIn("_uiDock.Panel.SetZoom(1);", layout)
        self.assertNotIn("SetZoom(layout.Scale)", layout)
        self.assertIn("if (_panelWindow is null) { _panelHolder.Bounds = layout.Panel; }", layout)
        # Only the viewport HWND and the composition visual transform follow the display size; the WebView bounds are
        # set once to 1280x720 and never changed.
        display = form[form.index("private bool SetGameDisplay(Rectangle display)"):]
        display = display[: display.index(END_OF_METHOD)]
        self.assertLess(display.index("_visual.SetScale(display.Width / 1280f, display.Height / 720f)"), display.rindex("_viewport.Bounds = display;"))
        for banned in ("_webView", "CaptureImage", "SendMouseInput"):
            self.assertNotIn(banned, display)
        self.assertEqual(form.count("_webView.Bounds = "), 1)
        self.assertIn("_webView.Bounds = new Rectangle(0, 0, 1280, 720);", form)
        # 2026-10-16: the page is pinned to DPR 1 on every monitor (150 % devices rendered at DPR 1.5 and missed the 100 %
        # templates); the only RasterizationScale write is that pin, after monitor-scale detection is turned off.
        self.assertEqual(form.count("RasterizationScale = "), 1)
        self.assertLess(form.index("_webView.ShouldDetectMonitorScaleChanges = false;"), form.index("_webView.RasterizationScale = 1.0;"))
        self.assertIn("_webView.ZoomFactor = 1;", form)
        # Captures stay CapturePreviewAsync of the page (independent of the visual transform) with the 1280x720 check.
        capture = form[form.index("private async Task<byte[]> CaptureImage(string captureSource)"):]
        capture = capture[: capture.index(END_OF_METHOD)]
        self.assertIn("CapturePreviewAsync", capture)
        self.assertIn("Math.Abs(source.Width - 1280) > 2 || Math.Abs(source.Height - 720) > 2", capture)
        # Program input stays in canvas coordinates; only the user's own mouse goes through the inverse display transform.
        self.assertIn("canonical.X * frame.SourceWidth / 1280.0", form)
        manual = form[form.index("private void ForwardManualMouse("):]
        manual = manual[: manual.index(END_OF_METHOD)]
        self.assertIn("GameDisplay.ToCanvas(new Point(mouse.X, mouse.Y), _displaySize)", manual)
        self.assertIn("_webView.SendMouseInput(kind, flags, 0, canvas);", manual)
        self.assertNotIn("new Point(mouse.X, mouse.Y));", manual)
        self.assertIn("if (message.Msg == WmGetMinMaxInfo && Frameless && IsHandleCreated)", form)
        self.assertIn("if (ResizeHit(client) is int edge) { message.Result = (IntPtr)edge; }", form)
        self.assertIn("if (message.Msg == WmSizing && Frameless && _uiDock is not null && _displayScaling && IsHandleCreated)", form)
        # The host guard still requires the exact 1280x720 WebView bounds, and a viewport equal to the committed transform.
        guard = _read(HOST / "HostGuard.cs")
        self.assertIn("state.WebViewBounds != new Rectangle(Point.Empty, Canvas)", guard)
        self.assertIn("state.ViewportSize != state.DisplaySize || !GameDisplay.DisplayAllowed(state.DisplaySize)", guard)
        self.assertIn("_desktopLocked, _displaySize);", form)
        dcomp = _read(HOST / "DirectCompositionTarget.cs")
        self.assertIn('Guid("4D93059D-097B-4651-9A60-F0F25116E2F3")', dcomp)
        self.assertIn("void SetTransform(ref System.Numerics.Matrix3x2 matrix);", dcomp)
        prefs = _read(UI_HOST / "UiHostPrefs.cs")
        self.assertIn("File.Move(temp, path, overwrite: true);", prefs)
        self.assertIn('Path.Combine(data, "ui", "host-window.json")', _read(UI_HOST / "UiGameDock.cs"))
        # Caption only over the host-painted header strip, never over the game viewport.
        self.assertIn("Frameless && _chromeHeader is { } header && header.Bounds.Contains(client)", form)
        # 最小化 goes through WM_SYSCOMMAND (background keeper intercept still applies); 关闭 through Form.Close().
        self.assertIn("PostMessage(Handle, WmSysCommand, (IntPtr)ScMinimize, IntPtr.Zero)", form)
        self.assertIn("_windowButtons.CloseRequested += Close;", form)
        # Base geometry at construction (scale 1).
        self.assertIn("_viewport = new Panel { Location = new Point(0, 44), Size = new Size(1280, 720)", form)
        # The self-test capture hook exists only for the self-owned demo page.
        hook = form[form.index("internal Task<byte[]> SelfTestCaptureAsync()"):]
        hook = hook[: hook.index(END_OF_METHOD)]
        self.assertIn("if (!_options.CompositionDemo || _options.CompositionGame) { throw", hook)

    def test_window_buttons_leave_room_for_emergency_stop(self) -> None:
        dock = _read(UI_HOST / "UiGameDock.cs")
        buttons = dock[dock.index("internal sealed class UiWindowButtons"):]
        width = int(re.search(r"internal const int ButtonWidth = (\d+);", buttons).group(1))
        count = int(re.search(r"internal const int Count = (\d+);", buttons).group(1))
        self.assertEqual(count, 3)   # 最小化 / 最大化(还原) / 关闭
        for banned in ("CaptureImage", "CapturePreviewAsync", "SendMouseInput", "CoreWebView2", "PostWebMessage"):
            self.assertNotIn(banned, buttons)
        css = _read(ROOT / "ui" / "styles" / "app.css")
        match = re.search(r"\.app-header\.has-window-controls \{ --window-controls-w: (\d+)px; padding-right: calc\(var\(--window-controls-w\)", css)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), count * width)
        app = _read(ROOT / "ui" / "app.js")
        self.assertIn('hostKind: ready?.host || null,', app)
        router = _read(UI_HOST / "UiMessageRouter.cs")
        self.assertIn('GameHostKind = "browser_host"', router)

    def test_refresh_icon_keeps_the_user_only_confirmed_reload(self) -> None:
        dock = _read(UI_HOST / "UiGameDock.cs")
        self.assertIn('internal const string RefreshTip = "刷新游戏";', dock)
        self.assertEqual(dock.count("RefreshRequested?.Invoke()"), 1)
        self.assertEqual(dock.count("MuteRequested?.Invoke()"), 1)
        form = _read(HOST / "CompositionDemoForm.cs")
        # 2026-10-11: no 「游戏画面」 caption on the game host; 静音 is a host icon (game view audio only).
        self.assertNotIn('"游戏画面"', form)
        self.assertNotIn('"游戏画面"', _read(UI_HOST / "UiShellForm.cs"))
        mute = form[form.index("private bool? SetGameMuted(bool muted)"):]
        mute = mute[: mute.index(END_OF_METHOD)]
        self.assertIn("core.IsMuted = muted;", mute)
        for banned in ("SendMouseInput", "ExecuteScript", "PostWebMessage", "CaptureImage", "InvalidateFrame", "Reload"):
            self.assertNotIn(banned, mute)
        self.assertEqual(form.count("_chromeHeader.MuteRequested += () => SetGameMuted(!_prefs.Muted);"), 1)
        self.assertIn('case "game.audio.set":', _read(UI_HOST / "UiMessageRouter.cs"))
        header = _read(ROOT / "ui" / "components" / "header.js")
        self.assertNotIn("header-mute", header)
        self.assertNotIn("gameMute", header)
        self.assertEqual(form.count("RefreshRequested += RequestManualReload"), 1)
        reload = form[form.index("private void RequestManualReload()"):]
        reload = reload[:reload.index(END_OF_METHOD)]
        self.assertLess(reload.index("_uiDock?.Engine.Active == true"), reload.index("MessageBox.Show(this, \"重新载入游戏页面？"))
        self.assertLess(reload.index("MessageBoxDefaultButton.Button2"), reload.index('InvalidateFrame("manual_reload");'))
        self.assertLess(reload.index('InvalidateFrame("manual_reload");'), reload.index("_webView.CoreWebView2.Reload();"))
        self.assertEqual(reload.count(".Reload()"), 1)  # game-data clearing has its own completion reload

    def test_no_safety_tab_and_produce_renamed(self) -> None:
        header = _read(ROOT / "ui" / "components" / "header.js")
        self.assertNotIn('key: "safety"', header)   # 2026-11-19 user: removed
        self.assertFalse((ROOT / "ui" / "components" / "safety-panel.js").exists())
        # 2026-10-13: W.I.N.G. is back on its own 「Produce」 tab (not part of the daily queue); 「运行中」 shows the live run.
        self.assertIn('{ key: "produce", label: "Produce" },', header)
        # the 「运行中」 tab is additionally shown only while a run is live (state.liveTab, set by app.js).
        self.assertIn("return TABS.filter((t) => (!t.dev || state.prefs?.developer === true)\n"
                      "    && (!t.live || state.liveTab === true || state.activeTab === \"live\"))", header)
        annotations = json.loads(_read(ROOT / "ui" / "annotations.json"))
        self.assertEqual(annotations["tasks"]["AutoProduce"]["name"], "W.I.N.G.")
        task = json.loads(_read(ROOT / "assets" / "tasks" / "auto_produce.json"))["task"][0]
        self.assertEqual((task["name"], task["label"]), ("AutoProduce", "W.I.N.G."))


if __name__ == "__main__":
    unittest.main()

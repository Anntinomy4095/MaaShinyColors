using System.Diagnostics;
using System.Drawing;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// --self-test-ui-dock (U4): opens the Composition host on its own local demo page (never the game) with the new UI
/// panel docked (--composition-demo --ui-panel), then checks that the game viewport and WebView bounds stay 1280x720,
/// the guard is clear, the panel sits at x=1280, the page handshake runs against the in-process status source, and
/// capture_seq does not move while the panel streams status for several seconds (the UI never captures). Runs once in
/// light and once in dark (each with its own self-test profile and UI WebView2 folder, never the user's profile) and
/// saves window and panel screenshots under dev_assets/private/ui-dock-selftest/.
/// </summary>
internal static class UiDockSelfTest
{
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern int GetWindowLong(IntPtr window, int index);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr window);
    private const int WmNcHitTest = 0x0084, WmNcLButtonDblClk = 0x00A3, WmMouseMove = 0x0200, WmLButtonDown = 0x0201, WmLButtonUp = 0x0202;
    private const int WmMouseLeave = 0x02A3;
    private const int HtTransparent = -1, HtClient = 1, HtCaption = 2;
    private const int HtLeft = 10, HtRight = 11, HtTop = 12, HtTopLeft = 13, HtTopRight = 14, HtBottom = 15, HtBottomRight = 17;
    private static IntPtr Lp(Point p) => (IntPtr)((p.Y << 16) | (p.X & 0xFFFF));
    private static int HitTest(Control target, Point client) => (int)SendMessage(target.Handle, WmNcHitTest, IntPtr.Zero, Lp(target.PointToScreen(client)));

    internal static void Run()
    {
        var root = Ui.UiPaths.RepoRoot();
        var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss", System.Globalization.CultureInfo.InvariantCulture);
        var output = Path.Combine(root, "dev_assets", "private", "ui-dock-selftest", stamp);
        var checks = new List<object>();
        // Light and dark: each pass uses its own profile file and UI WebView2 folder under the output, never
        // the user's dev_assets/private/ui/profile.json.
        Directory.CreateDirectory(output);
        PrefsChecks(output, (name, pass, detail) => checks.Add(new { check = "prefs:" + name, pass, detail }));
        foreach (var theme in new[] { "light", "dark" }) { RunTheme(output, theme, checks); }
        RunBackgroundSwitch(output, checks);
        Ui.UiGameDock.SelfTestPaths = null;
        var passed = checks.Count > 0 && checks.All(item => (bool)item.GetType().GetProperty("pass")!.GetValue(item)!);
        var json = JsonSerializer.Serialize(new { self_test = "ui-dock", real_game_contact = false, passed, checks },
            new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
        File.WriteAllText(Path.Combine(output, "selftest-ui-dock.json"), json);
        Console.WriteLine(json);
        Console.WriteLine($"ui dock self-test: passed={passed}; report: {output}");
        Environment.ExitCode = passed ? 0 : 1;
    }

    private static void RunTheme(string output, string theme, List<object> checks)
    {
        var profile = Path.Combine(output, "webview2-game-demo-" + theme);
        Directory.CreateDirectory(profile);
        var uiProfile = Path.Combine(output, $"profile-{theme}.json");
        File.WriteAllText(uiProfile, $"{{\"schema_version\":1,\"preset\":\"daily\",\"prefs\":{{\"theme\":\"{theme}\"}}}}\n");
        Ui.UiGameDock.SelfTestPaths = (uiProfile, Path.Combine(output, "ui-webview2-" + theme));
        var options = new BrowserHostOptions(new Uri("https://sc-local.test/index.html"), profile,
            OfflineDemo: false, BrowserDemo: true, BridgeSession: Path.Combine(profile, "bridge-selftest.json"),
            CompositionDemo: true, UiPanel: true);
        void Check(string name, bool pass, object? detail = null) => checks.Add(new { check = theme + ":" + name, pass, detail });
        var form = new CompositionDemoForm(options) { StartPosition = FormStartPosition.Manual, Location = new Point(0, 0) };
        uint gameBrowser = 0, uiBrowser = 0;
        var closingSeen = false;
        form.FormClosing += (_, _) => closingSeen = true;
        form.Shown += async (_, _) =>
        {
            try
            {
                // A launcher's STARTUPINFO show state (for example a hidden/minimised console start) applies to the
                // process's first window; restore it so the window checks and screenshots see the normal layout.
                var ready = await form.UiPanelReady.Task.WaitAsync(TimeSpan.FromSeconds(30));
                var restored = form.WindowState == FormWindowState.Minimized;
                if (restored) { form.WindowState = FormWindowState.Normal; await Task.Delay(500); }
                checks.Add(new { check = theme + ":launcher_show_state", pass = true, detail = (object)(restored ? "restored_from_minimized" : "normal") });
                Check("ui_panel_initialized", ready, ready ? null : form.ChromeHeader?.Notice ?? form.HostGuardReason);
                if (!ready || form.UiDock is not { } dock) { return; }
                gameBrowser = form.BrowserProcessId;
                uiBrowser = dock.Panel.BrowserProcessId;
                var router = dock.Router;
                var statuses = new List<JsonObject>();
                dock.Router.EventReady += json =>
                {
                    var node = JsonNode.Parse(json)!.AsObject();
                    if ((string?)node["event"] == "status.update") { statuses.Add(node["data"]!.AsObject()); }
                };
                var watch = Stopwatch.StartNew();
                while (watch.ElapsedMilliseconds < 30_000 && !(router.StatusSubscribed && router.LogSubscribed && form.HostGuardReason is null)) { await Task.Delay(100); }
                Check("handshake", router.Decisions.Any(d => d.Command == "ui.ready" && d.Accepted) && router.StatusSubscribed && router.LogSubscribed,
                    router.Decisions.Select(d => $"{d.Command}:{d.Accepted}").ToList());
                Check("all_page_messages_from_home", router.Decisions.All(d => d.Source == Ui.UiPanelHost.HomeUri && d.Accepted));
                var captureBefore = form.CaptureSequence;
                await Task.Delay(5_000);
                Check("status_streamed", statuses.Count >= 4, statuses.Count);
                Check("status_is_in_process_host_fields", statuses.Count > 0 && statuses[^1]["connected"]?.GetValue<bool>() == true
                    && statuses[^1].ContainsKey("capture_seq") && statuses[^1]["pipeline_input_enabled"]?.GetValue<bool>() == false, statuses.LastOrDefault()?.ToJsonString());
                Check("capture_seq_unchanged_by_ui", form.CaptureSequence == captureBefore, new { before = captureBefore, after = form.CaptureSequence });
                Check("viewport_1280x720", form.GameViewport.Size == new Size(1280, 720) && form.GameViewport.Location == new Point(0, GameDisplay.HeaderPx(1, form.ChromeScale)),
                    $"{form.GameViewport.Bounds}");
                Check("webview_bounds_1280x720", form.GameBounds == new Rectangle(0, 0, 1280, 720), $"{form.GameBounds}");
                Check("game_raster_scale_1", form.GameRasterScale == 1.0, $"{form.GameRasterScale} dpi={form.DeviceDpi}");
                Check("host_guard_clear", form.HostGuardReason is null, form.HostGuardReason);
                var panel = dock.View.Parent!;
                // the default window shows the game 1:1; the panel fills the rest, trimmed by the resize border.
                Check("panel_docked_right", panel.Left == 1280 && panel.Top == form.ResizeBorder && panel.Right == form.ClientSize.Width - form.ResizeBorder
                    && panel.Bottom == form.ClientSize.Height - form.ResizeBorder && form.ClientSize.Width >= form.MinimumClient.Width
                    && form.ClientSize.Height == 788 && form.DisplayScale == 1, $"{panel.Bounds} in {form.ClientSize} border {form.ResizeBorder}");
                Check("default_window_prefs", form.WindowPrefs == Ui.UiHostPrefs.Default && !File.Exists(dock.HostPrefsPath), dock.HostPrefsPath);
                Check("demo_has_no_mute", !form.ChromeHeader!.MuteVisible && dock.Router.GameAudio is null);
                // 后台运行 lives in 设置 → 窗口 (game host only); no keep-alive checkbox or button on the main window any more.
                Check("no_background_controls_on_window", dock.Router.BackgroundRun is null && form.Keeper is null
                    && !form.Controls.OfType<CheckBox>().Any() && !form.Controls.OfType<Button>().Any(button => button.Text.Contains("隐藏")));
                Check("ui_browser_separate", dock.Panel.BrowserProcessId != 0 && dock.Panel.BrowserProcessId != form.BrowserProcessId,
                    new { ui = dock.Panel.BrowserProcessId, game = form.BrowserProcessId });
                Check("no_blocked_ui_navigation", dock.Panel.BlockedNavigations.Count == 0 && dock.Panel.BlockedRequests.Count == 0);
                var png = Path.Combine(output, $"window-dock-{theme}.png");
                Check("window_screenshot", Ui.UiSelfTest.SaveWindowPng(form, png), png);
                var frame = Path.Combine(output, $"window-frame-{theme}.png");
                Check("frame_screenshot", Ui.UiSelfTest.SaveFramePng(form, frame), frame);
                await dock.Panel.SavePanelPngAsync(Path.Combine(output, $"panel-dock-{theme}.png"));
                await FramelessChecks(form, output, Check);
            }
            catch (Exception exception) { Check("no_exception", false, exception.ToString()); }
            finally
            {
                // close through the host-painted 关闭 button (a left-click message on it), i.e. the normal FormClosing path.
                var buttons = form.WindowButtons;
                if (buttons is not null && !form.IsDisposed)
                {
                    var center = Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Close));
                    SendMessage(buttons.Handle, WmLButtonDown, (IntPtr)1, Lp(center));
                    SendMessage(buttons.Handle, WmLButtonUp, IntPtr.Zero, Lp(center));
                }
                if (!form.IsDisposed && form.Visible) { form.Close(); }
            }
        };
        Application.Run(form);
        checks.Add(new { check = theme + ":close_button_uses_form_closing", pass = closingSeen, detail = (object?)null });
        // The window size left by the resize checks was saved on close (host-owned prefs next to the self-test profile).
        var saved = Ui.UiHostPrefs.Load(Path.ChangeExtension(uiProfile, ".host-window.json"));
        checks.Add(new { check = theme + ":window_prefs_saved_on_close", pass = saved.Width == LastClient.Width
            && saved.Height == LastClient.Height && !saved.Maximized,
            detail = (object)saved.ToString() });
        foreach (var id in new[] { gameBrowser, uiBrowser }.Where(id => id != 0))
        {
            try { using var browser = Process.GetProcessById((int)id); browser.WaitForExit(15_000); }
            catch (ArgumentException) { }
        }
    }

    private static void RunBackgroundSwitch(string output, List<object> checks)
    {
        var profile = Path.Combine(output, "webview2-background-switch");
        Ui.UiGameDock.SelfTestPaths = (Path.Combine(output, "profile-background-switch.json"),
            Path.Combine(output, "ui-webview2-background-switch"));
        // Construct the docked game window with its usual controls and route navigation to the local demo page.
        var options = new BrowserHostOptions(new Uri("https://sc-local.test/" + CompositionDemoForm.HeartbeatPage), profile,
            OfflineDemo: false, BrowserDemo: true, BridgeSession: Path.Combine(profile, "bridge-selftest.json"),
            CompositionDemo: true, CompositionGame: true, DemoPage: CompositionDemoForm.HeartbeatPage);
        using var form = new CompositionDemoForm(options);
        void Check(string name, bool pass, object? detail = null) => checks.Add(new { check = "background:" + name, pass, detail });
        form.Shown += async (_, _) =>
        {
            try
            {
                var ready = await form.UiPanelReady.Task.WaitAsync(TimeSpan.FromSeconds(30));
                await form.HeartbeatSeen.WaitAsync(TimeSpan.FromSeconds(30));
                Check("ready", ready);
                if (!ready || form.UiDock is not { } dock || form.Keeper is not { } keeper) { return; }
                var browser = form.BrowserProcessId;
                var messageId = 0;
                bool Command(string op, bool? enabled = null)
                {
                    var data = new JsonObject { ["op"] = op };
                    if (enabled is bool value) { data["enabled"] = value; }
                    var request = new JsonObject { ["v"] = 1, ["id"] = "background-" + ++messageId,
                        ["cmd"] = "window.background", ["params"] = data };
                    var response = dock.Router.Handle(Ui.UiPanelHost.HomeUri, request.ToJsonString());
                    return response is not null && JsonNode.Parse(response)?["ok"]?.GetValue<bool>() == true;
                }
                Check("starts_off", !keeper.Enabled && form.BackgroundArgumentsActive && !Command("hide"));
                Check("enable", Command("set", true) && keeper.Enabled && Ui.UiHostPrefs.Load(dock.HostPrefsPath).BackgroundRun);
                // Use the same system command as clicking minimize in the frameless title bar.
                SendMessage(form.Handle, 0x0112, (IntPtr)0xf020, IntPtr.Zero);
                Check("minimize_hides", keeper.HideMode != BackgroundKeeper.None);
                Check("disable_restores", Command("set", false) && !keeper.Enabled && keeper.HideMode == BackgroundKeeper.None
                    && !Ui.UiHostPrefs.Load(dock.HostPrefsPath).BackgroundRun);
                Check("off_refuses_hide", !Command("hide"));
                SendMessage(form.Handle, 0x0112, (IntPtr)0xf020, IntPtr.Zero);
                await Task.Yield();
                Check("normal_minimize", IsIconic(form.Handle) && keeper.HideMode == BackgroundKeeper.None);
                form.WindowState = FormWindowState.Normal;
                Check("reenable_without_restart", Command("set", true) && keeper.Enabled && Command("hide")
                    && keeper.HideMode != BackgroundKeeper.None && form.BrowserProcessId == browser && browser != 0);
                Command("set", false);
            }
            catch (Exception exception) { Check("exception", false, exception.ToString()); }
            finally { form.Close(); }
        };
        Application.Run(form);
    }

    private delegate void CheckFn(string name, bool pass, object? detail = null);

    /// <summary>UiHostPrefs round trip and fallbacks (pure file I/O under the self-test output).</summary>
    internal static void PrefsChecks(string output, Action<string, bool, object?> check)
    {
        var path = Path.Combine(output, "prefs-roundtrip.host-window.json");
        var prefs = new Ui.UiHostPrefs(1900, 900, true, true, false);
        check("prefs_round_trip", prefs.Save(path) && Ui.UiHostPrefs.Load(path) == prefs, File.ReadAllText(path));
        // 后台运行 defaults to off (also for an old file without the key) and survives being turned on.
        check("background_run_default_off", !Ui.UiHostPrefs.Default.BackgroundRun, null);
        File.WriteAllText(path, "{\"width\": 1700, \"height\": 800, \"maximized\": false, \"muted\": false}");
        check("background_run_missing_key_off", !Ui.UiHostPrefs.Load(path).BackgroundRun, File.ReadAllText(path));
        File.WriteAllText(path, "{\"width\": 1700, \"height\": 800, \"background_run\": true}");
        check("background_run_on_kept", Ui.UiHostPrefs.Load(path).BackgroundRun, File.ReadAllText(path));
        File.WriteAllText(path, "{\"width\": 10, \"height\": 99999, \"maximized\": \"yes\", \"muted\": 1}");
        check("prefs_out_of_range_fall_back", Ui.UiHostPrefs.Load(path) == Ui.UiHostPrefs.Default, File.ReadAllText(path));
        File.WriteAllText(path, "not json");
        check("prefs_invalid_json_default", Ui.UiHostPrefs.Load(path) == Ui.UiHostPrefs.Default, null);
        check("prefs_missing_default", Ui.UiHostPrefs.Load(path + ".missing") == Ui.UiHostPrefs.Default, null);
    }
    private static Point Center(Rectangle r) => new(r.X + r.Width / 2, r.Y + r.Height / 2);
    private const int WmSizing = 0x0214;
    /// <summary>Demo page 「领取测试奖励」 button (canvas px 430..850 x 380..540), grown by <paramref name="margin"/> when negative.</summary>
    private static bool InButton(Point canvas, int margin) => canvas.X >= 430 + margin && canvas.X < 850 - margin && canvas.Y >= 380 + margin && canvas.Y < 540 - margin;
    /// <summary>The button's rgb(20,160,70) (with resampling tolerance), never the #111c30 background.</summary>
    private static bool IsGreen(Color c) => c.G > 120 && c.R < 70 && c.B < 120;
    /// <summary>Client size the checks leave before closing; the close path must save exactly it.</summary>
    private static Size LastClient;
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr window, out NativeRect rect);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out NativeRect rect);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern IntPtr GetSystemMenu(IntPtr window, bool revert);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern int GetMenuState(IntPtr menu, int id, int flags);
    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct NativeRect { public int Left, Top, Right, Bottom; }

    /// <summary>
    /// frameless window contract (2026-10-11: resizable + maximizable). No system frame or thick frame, title text kept
    /// for process lookup, minimize / maximize boxes + system menu; header empty area drags (HTCAPTION), 静音 / 刷新游戏 /
    /// window buttons / game / panel stay client areas; window-edge strips answer resize codes, never the game viewport;
    /// resizing, a caption double-click and the 最大化 button only change the panel width while the game viewport and
    /// WebView bounds stay 0,44 1280x720 with the guard clear; minimize (the 最小化 button path) is refused by HostGuard
    /// and a restore returns to the same 1280x720 view.
    /// </summary>
    private static async Task FramelessChecks(CompositionDemoForm form, string output, CheckFn check)
    {
        check("frameless_border_none", form.FormBorderStyle == FormBorderStyle.None && form.Size == form.ClientSize, $"{form.Size} / {form.ClientSize}");
        check("window_title_kept", form.Text == BrowserHostOptions.BrowserDemoTitle && form.ShowInTaskbar, form.Text);
        var style = GetWindowLong(form.Handle, -16);
        check("minimize_maximize_box_and_sysmenu", (style & 0x00020000) != 0 && (style & 0x00080000) != 0 && (style & 0x00010000) != 0
            && (style & 0x00C00000) == 0, $"0x{style:X8}");
        // WS_THICKFRAME is what lets the system run the edge-drag sizing loop; WM_NCCALCSIZE keeps it frameless
        // (real client rect == real window rect). The system menu's 大小 (SC_SIZE) state is recorded for information only.
        GetClientRect(form.Handle, out var clientRect);
        GetWindowRect(form.Handle, out var windowRect);
        var menu = GetSystemMenu(form.Handle, false);
        SendMessage(form.Handle, 0x0116, menu, IntPtr.Zero);   // WM_INITMENU: the system updates the item states
        var sizeState = GetMenuState(menu, 0xF000, 0);           // SC_SIZE, MF_BYCOMMAND
        check("thick_frame_sizing_enabled", (style & 0x00040000) != 0
            && clientRect.Right - clientRect.Left == windowRect.Right - windowRect.Left && clientRect.Bottom - clientRect.Top == windowRect.Bottom - windowRect.Top,
            new { style = $"0x{style:X8}", sizeState, client = $"{clientRect.Right - clientRect.Left}x{clientRect.Bottom - clientRect.Top}",
                window = $"{windowRect.Right - windowRect.Left}x{windowRect.Bottom - windowRect.Top}" });
        var header = form.ChromeHeader!;
        var buttons = form.WindowButtons!;
        // 静音 / 刷新游戏 are --composition-game features; shown here (no handler attached) only to test placement and hit-testing.
        header.RefreshVisible = true;
        header.MuteVisible = true;
        header.Refresh();
        var refresh = header.RefreshBounds();
        var mute = header.ToolBounds(Ui.UiHostChrome.Tool.Mute);
        // 2026-10-11 (host-20261011d): 刷新游戏 right after the wordmark, 静音 at the right end of the strip.
        var wordmarkEnd = Ui.UiChrome.WordmarkEnd(form.DeviceDpi / 96f);
        check("header_icons_placed", refresh.Width > 0 && refresh.Left >= wordmarkEnd && refresh.Left <= wordmarkEnd + 16 && refresh.Right < 400
            && refresh.Top >= 0 && refresh.Bottom <= header.Height && mute.Width == refresh.Width && mute.Right <= 1280 - 8 && mute.Left > 1180
            && mute.Top == refresh.Top, $"wordmark {wordmarkEnd:0.#} refresh {refresh} mute {mute}");
        check("header_empty_area_transparent", HitTest(header, new Point(900, 22)) == HtTransparent);
        check("header_refresh_is_client", HitTest(header, Center(refresh)) == HtClient && HitTest(header, Center(mute)) == HtClient);
        check("form_caption_over_header", HitTest(form, new Point(900, 22)) == HtCaption);
        check("game_viewport_not_caption", HitTest(form.GameViewport, new Point(640, 360)) == HtClient && !form.IsCaptionPoint(new Point(640, 44 + 360)));
        check("window_buttons_far_right", buttons.Right == form.ClientSize.Width && buttons.Top == 0 && buttons.Height == header.Height
            && buttons.Left >= 1280 && !buttons.Bounds.IntersectsWith(form.GameViewport.Bounds), $"{buttons.Bounds}");
        check("window_buttons_client", HitTest(buttons, Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Minimize))) == HtClient);
        // Hover states (synthetic mouse moves) for one screenshot: 刷新游戏 and 关闭.
        SendMessage(header.Handle, WmMouseMove, IntPtr.Zero, Lp(Center(mute)));
        SendMessage(buttons.Handle, WmMouseMove, IntPtr.Zero, Lp(Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Close))));
        // Paint now and capture before the message loop delivers the WM_MOUSELEAVE that the real (elsewhere) cursor causes.
        header.Update();
        buttons.Update();
        var hover = Path.Combine(output, $"window-hover-{(form.UiDock!.Palette.Background.GetBrightness() < 0.5f ? "dark" : "light")}.png");
        check("hover_screenshot", Ui.UiSelfTest.SaveWindowPng(form, hover), hover);
        SendMessage(header.Handle, WmMouseLeave, IntPtr.Zero, IntPtr.Zero);
        SendMessage(buttons.Handle, WmMouseLeave, IntPtr.Zero, IntPtr.Zero);
        var size = form.Size;
        var dock = form.UiDock!;
        var panel = dock.View.Parent!;
        var panelPx = (int)Math.Round(GameDisplay.MinPanelCss * form.DeviceDpi / 96.0);
        // every part sits where the pure layout puts it for the current client size, the game WebView itself stays
        // 0,0 1280x720, the viewport equals the committed display transform, and the panel zoom stays 1.
        DockLayout Expected() => GameDisplay.Compute(form.ClientSize, form.ResizeBorder, panelPx, Ui.UiWindowButtons.ScaledWidth(form.DeviceDpi), true, form.ChromeScale, form.PanelLeft, form.PanelDetached);
        bool Consistent()
        {
            var l = Expected();
            return form.DisplayScalingAvailable && form.GameViewport.Bounds == l.Game && form.DisplaySize == l.Game.Size
                && form.GameBounds == new Rectangle(0, 0, 1280, 720) && Math.Abs(form.DisplayScale - l.Scale) < 1e-9
                && form.ChromeHeader!.Bounds == l.Header && form.ChromeFooter!.Bounds == l.Footer
                && (form.PanelDetached || panel.Bounds == l.Panel) && buttons.Bounds == l.Buttons && Math.Abs(dock.Panel.Zoom - 1) < 1e-3;
        }
        string Describe() => $"client {form.ClientSize} scale {form.DisplayScale:0.###} viewport {form.GameViewport.Bounds} display {form.DisplaySize} "
            + $"webview {form.GameBounds} panel {panel.Bounds} zoom {dock.Panel.Zoom:0.###} buttons {buttons.Bounds} expected {Expected()}";
        async Task<string?> GuardSettled()
        {
            var watch = Stopwatch.StartNew();
            while (watch.ElapsedMilliseconds < 10_000 && form.HostGuardReason is not null) { await Task.Delay(100); }
            return form.HostGuardReason;
        }
        var theme = dock.Palette.Background.GetBrightness() < 0.5f ? "dark" : "light";
        check("default_scale_is_1_to_1", form.DisplayScale == 1 && form.GameViewport.Bounds == new Rectangle(0, GameDisplay.HeaderPx(1, form.ChromeScale), 1280, 720) && Consistent(), Describe());
        // Resize hit-testing: right / bottom strips and the corner belong to the form; header and footer pass edges through;
        // the game viewport's left edge stays a client area (it is never a resize handle).
        var client = form.ClientSize;
        check("resize_edges", HitTest(form, new Point(client.Width - 2, 400)) == HtRight
            && HitTest(form, new Point(client.Width - 2, client.Height - 2)) == HtBottomRight
            && HitTest(header, new Point(600, 1)) == HtTransparent && HitTest(form, new Point(600, 1)) == HtTop
            && HitTest(form, new Point(1, 1)) == HtTopLeft && HitTest(form, new Point(1, 22)) == HtLeft
            && HitTest(form, new Point(600, client.Height - 2)) == HtBottom
            && HitTest(buttons, new Point(buttons.Width - 2, 22)) == HtTransparent
            && HitTest(form.GameViewport, new Point(1, 360)) == HtClient && HitTest(form, new Point(900, 22)) == HtCaption
            && HitTest(form, new Point(1500, 1)) == HtTop && HitTest(form, new Point(client.Width - 2, 8)) == HtTopRight,
            new { right = HitTest(form, new Point(client.Width - 2, 400)), top = HitTest(form, new Point(600, 1)),
                bottom = HitTest(form, new Point(600, client.Height - 2)), left = HitTest(form, new Point(1, 22)) });
        var work = Screen.FromHandle(form.Handle).WorkingArea;
        // a lower window of the same width: the game follows the height (80 %), the panel gets the freed width.
        var defaultPanelWidth = panel.Width;
        var testClient = new Size(client.Width, (int)Math.Round(GameDisplay.BaseHeightFor(form.ChromeScale) * 0.8));
        form.ClientSize = testClient;
        await Task.Delay(800);
        var guardScaled = await GuardSettled();
        var testScale = form.DisplayScale;
        check("resize_scales_together", Consistent() && Math.Abs(testScale - 0.8) < 0.002 && guardScaled is null
            && form.DisplaySize != new Size(1280, 720) && panel.Width > defaultPanelWidth && panel.Right == form.ClientSize.Width - form.ResizeBorder,
            Describe() + " guard " + guardScaled);
        // the panel page keeps zoom 1 while the game is scaled; it reflows to the wider holder instead.
        var panelScaledPng = Path.Combine(output, $"panel-scaled-{theme}.png");
        await dock.Panel.SavePanelPngAsync(panelScaledPng);
        Size panelShot;
        using (var image = Image.FromFile(panelScaledPng)) { panelShot = image.Size; }
        check("panel_not_zoomed_with_window", Math.Abs(dock.Panel.Zoom - 1) < 1e-3 && form.DisplayScale != 1,
            new { shot = panelShot.ToString(), holder = panel.Size.ToString(), zoom = dock.Panel.Zoom, scale = form.DisplayScale });
        // Display scaled while the capture stays the 1280x720 page content (real capture path: guard + CapturePreviewAsync).
        byte[]? png = null; string? captureError = null;
        try { png = await form.SelfTestCaptureAsync(); } catch (Exception exception) { captureError = exception.Message; }
        Size captured = Size.Empty; Color capturedGreen = Color.Empty;
        if (png is not null)
        {
            using var stream = new MemoryStream(png);
            using var bitmap = new Bitmap(stream);
            captured = bitmap.Size; capturedGreen = bitmap.GetPixel(640, 460);
            File.WriteAllBytes(Path.Combine(output, $"capture-scaled-{theme}.png"), png);
        }
        check("capture_stays_1280x720_while_scaled", captured == new Size(1280, 720) && IsGreen(capturedGreen) && form.DisplaySize != captured,
            new { captured = captured.ToString(), display = form.DisplaySize.ToString(), pixel = capturedGreen.ToString(), captureError });
        // The window shows the picture scaled: the canvas button centre is green at its scaled position, and a viewport
        // point that is inside the button only in unscaled coordinates shows background.
        var scaledPng = Path.Combine(output, $"window-scaled-{theme}.png");
        var shot = Ui.UiSelfTest.SaveWindowPng(form, scaledPng);
        check("scaled_screenshot", shot, scaledPng);
        if (shot)
        {
            using var window = new Bitmap(scaledPng);
            var display = form.DisplaySize;
            var origin = form.GameViewport.Location;
            var shown = GameDisplay.ToDisplay(new Point(640, 460), display);
            var centre = window.GetPixel(origin.X + shown.X, origin.Y + shown.Y);
            Point? probe = null;
            for (var y = 384; y < 536 && probe is null; y += 4)
            {
                for (var x = 434; x < 846 && probe is null; x += 4)
                { if (!InButton(GameDisplay.ToCanvas(new Point(x, y), display), -6)) { probe = new Point(x, y); } }
            }
            var outside = probe is { } p ? window.GetPixel(origin.X + p.X, origin.Y + p.Y) : Color.Empty;
            check("display_scaled_in_window", IsGreen(centre) && probe is not null && !IsGreen(outside),
                new { centre = centre.ToString(), probe = probe?.ToString(), outside = outside.ToString(), display = display.ToString() });
            // Manual mouse mapping: the display point of a canvas pixel maps back to it (inverse transform).
            var back = GameDisplay.ToCanvas(shown, display);
            var tolerance = form.DisplayScale >= 1 ? 0 : 2;
            check("manual_input_round_trip", Math.Abs(back.X - 640) <= tolerance && Math.Abs(back.Y - 460) <= tolerance, new { back = back.ToString() });
        }
        // User drag (WM_SIZING): a width-only drag is left alone; a taller drag on this monitor widens up to the work area
        // and otherwise limits the height, so no empty band appears under the game.
        Rectangle Sizing(Rectangle dragged, int edge)
        {
            var rectPtr = System.Runtime.InteropServices.Marshal.AllocHGlobal(16);
            try
            {
                System.Runtime.InteropServices.Marshal.WriteInt32(rectPtr, 0, dragged.Left);
                System.Runtime.InteropServices.Marshal.WriteInt32(rectPtr, 4, dragged.Top);
                System.Runtime.InteropServices.Marshal.WriteInt32(rectPtr, 8, dragged.Right);
                System.Runtime.InteropServices.Marshal.WriteInt32(rectPtr, 12, dragged.Bottom);
                SendMessage(form.Handle, WmSizing, (IntPtr)edge, rectPtr);
                return Rectangle.FromLTRB(System.Runtime.InteropServices.Marshal.ReadInt32(rectPtr, 0), System.Runtime.InteropServices.Marshal.ReadInt32(rectPtr, 4),
                    System.Runtime.InteropServices.Marshal.ReadInt32(rectPtr, 8), System.Runtime.InteropServices.Marshal.ReadInt32(rectPtr, 12));
            }
            finally { System.Runtime.InteropServices.Marshal.FreeHGlobal(rectPtr); }
        }
        var before = form.Bounds;
        var narrower = Rectangle.FromLTRB(before.Left, before.Top, before.Right - 100, before.Bottom);
        var widthOnly = Sizing(narrower, GameDisplay.EdgeRight);
        var taller = Rectangle.FromLTRB(before.Left, before.Top, before.Right, before.Bottom + 400);
        var tallAnswer = Sizing(taller, GameDisplay.EdgeBottom);
        check("drag_sizing", widthOnly == narrower
            && tallAnswer == GameDisplay.DragSizing(taller, GameDisplay.EdgeBottom, panelPx, work.Width, form.MinimumClient, form.ChromeScale)
            && tallAnswer.Location == before.Location && tallAnswer.Width <= work.Width
            && GameDisplay.Compute(tallAnswer.Size, form.ResizeBorder, panelPx, 0, true, form.ChromeScale) is var tall
            && tallAnswer.Height - tall.Footer.Bottom <= 2,
            new { before = before.ToString(), widthOnly = widthOnly.ToString(), taller = taller.ToString(), answered = tallAnswer.ToString() });
        form.ClientSize = new Size(900, 500);
        await Task.Delay(300);
        check("minimum_size", form.ClientSize.Width >= form.MinimumClient.Width && form.ClientSize.Height >= form.MinimumClient.Height
            && Math.Abs(form.DisplayScale - GameDisplay.MinScale) < 0.01 && Consistent() && await GuardSettled() is null,
            $"{form.ClientSize} min {form.MinimumClient} " + Describe());
        var smallPng = Path.Combine(output, $"window-minimum-{theme}.png");
        check("minimum_screenshot", Ui.UiSelfTest.SaveWindowPng(form, smallPng), smallPng);
        form.ClientSize = GameDisplay.ClientFor(1, (int)Math.Round(400 * form.ChromeScale), form.ChromeScale);
        await Task.Delay(300);
        size = form.Size;
        check("back_to_1_to_1", form.DisplayScale == 1 && form.GameViewport.Bounds == new Rectangle(0, GameDisplay.HeaderPx(1, form.ChromeScale), 1280, 720) && Consistent(), Describe());
        // 2026-10-16 面板位置 left: the dock mirrors (game column at the right, panel on the left), the guard still accepts
        // the game view, the header keeps its icons left of the window buttons, the left edge is a resize handle; then back.
        var leftApplied = form.SetPanelLeft(true);
        await Task.Delay(300);
        var muteRect = header.ToolBounds(Ui.UiHostChrome.Tool.Mute);
        var muteRight = muteRect.IsEmpty ? 0 : header.Left + muteRect.Right;
        check("panel_left_mirrors", leftApplied == true && form.PanelLeft && Consistent() && panel.Left < form.GameViewport.Left
            && form.GameViewport.Right == form.ClientSize.Width && buttons.Right == form.ClientSize.Width && header.RightInset == buttons.Width
            && muteRight <= buttons.Left && HitTest(form, new Point(1, 400)) == HtLeft
            && dock.Router.PanelSide?.Invoke() == "left" && await GuardSettled() is null,
            new { layout = Describe(), muteRight, buttonsLeft = buttons.Left, inset = header.RightInset, guard = form.HostGuardReason,
                consistent = Consistent(), left = HitTest(form, new Point(1, 400)), status = dock.Router.PanelSide?.Invoke() });
        var leftPng = Path.Combine(output, $"window-panel-left-{theme}.png");
        check("panel_left_screenshot", Ui.UiSelfTest.SaveWindowPng(form, leftPng), leftPng);
        check("panel_left_saved", Ui.UiHostPrefs.Load(dock.HostPrefsPath).PanelLeft, dock.HostPrefsPath);
        form.SetPanelLeft(false);
        await Task.Delay(300);
        check("panel_right_restored", !form.PanelLeft && Consistent() && header.RightInset == 0 && form.GameViewport.Left == 0
            && !Ui.UiHostPrefs.Load(dock.HostPrefsPath).PanelLeft && await GuardSettled() is null, Describe());
        // 面板位置「独立窗口」: the panel moves into its own window (same WebView, still answering), this window keeps only
        // the game column, the guard still accepts the game view; closing the panel window docks it back on the right.
        var sizeBeforeDetach = form.ClientSize;
        var detachedApplied = form.SetPanelSide("detached");
        await Task.Delay(500);
        var panelWindow = form.PanelWindow;
        check("panel_detached", detachedApplied == "detached" && panelWindow is { Visible: true } && panel.Parent == panelWindow
            && Consistent() && form.ClientSize.Width < sizeBeforeDetach.Width && buttons.Right == form.GameViewport.Right
            && dock.Router.PanelSide?.Invoke() == "detached" && Ui.UiHostPrefs.Load(dock.HostPrefsPath).PanelDetached
            && await GuardSettled() is null, new { layout = Describe(), window = panelWindow?.Bounds.ToString(), parent = panel.Parent?.GetType().Name });
        var detachedPng = Path.Combine(output, $"window-panel-detached-{theme}.png");
        check("panel_detached_screenshot", Ui.UiSelfTest.SaveWindowPng(form, detachedPng), detachedPng);
        panelWindow?.Close();   // the user closing the panel window docks it back
        await Task.Delay(500);
        check("panel_redocked_on_close", form.PanelWindow is null && panel.Parent == form && !form.PanelDetached && !form.PanelLeft
            && Consistent() && !Ui.UiHostPrefs.Load(dock.HostPrefsPath).PanelDetached && await GuardSettled() is null, Describe());
        // Detached from the left side, it docks back on the left (the side is kept in the prefs while detached).
        form.SetPanelSide("left");
        form.SetPanelSide("detached");
        await Task.Delay(500);
        var keptLeft = Ui.UiHostPrefs.Load(dock.HostPrefsPath);
        form.PanelWindow?.Close();
        await Task.Delay(500);
        check("panel_redocked_on_its_side", keptLeft is { PanelDetached: true, PanelLeft: true } && form.PanelWindow is null && form.PanelLeft
            && Consistent() && await GuardSettled() is null, new { layout = Describe(), keptLeft });
        form.SetPanelSide("right");
        await Task.Delay(300);
        // Caption double-click maximizes (to the monitor work area, letterboxed) and restores; the 最大化 button does the same.
        SendMessage(form.Handle, WmNcLButtonDblClk, (IntPtr)HtCaption, Lp(form.PointToScreen(new Point(900, 22))));
        await Task.Delay(500);
        check("caption_double_click_maximizes", form.WindowState == FormWindowState.Maximized && form.Bounds == work && Consistent()
            && form.ResizeBorder == 0 && buttons.Maximized && panel.Right == work.Width && panel.Bottom == work.Height && panel.Top == 0
            && Expected().Footer.Bottom <= work.Height
            && await GuardSettled() is null,
            new { state = form.WindowState.ToString(), bounds = form.Bounds.ToString(), work = work.ToString(), guard = form.HostGuardReason, layout = Describe(),
                band_under_game = work.Height - Expected().Footer.Bottom });
        var maximizedPng = Path.Combine(output, $"window-maximized-{theme}.png");
        check("maximized_screenshot", Ui.UiSelfTest.SaveWindowPng(form, maximizedPng), maximizedPng);
        var maximizeEvents = 0;
        buttons.MaximizeRequested += () => maximizeEvents++;
        var restoreButton = Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Maximize));
        SendMessage(buttons.Handle, WmLButtonDown, (IntPtr)1, Lp(restoreButton));
        SendMessage(buttons.Handle, WmLButtonUp, IntPtr.Zero, Lp(restoreButton));
        await Task.Delay(500);
        check("restore_button_restores", maximizeEvents == 1 && form.WindowState == FormWindowState.Normal && form.Size == size && Consistent()
            && !buttons.Maximized && await GuardSettled() is null, $"{form.WindowState} {form.Size}");
        SendMessage(buttons.Handle, WmLButtonDown, (IntPtr)1, Lp(Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Maximize))));
        SendMessage(buttons.Handle, WmLButtonUp, IntPtr.Zero, Lp(Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Maximize))));
        await Task.Delay(500);
        check("maximize_button_maximizes", form.WindowState == FormWindowState.Maximized && Consistent(), $"{form.WindowState}");
        SendMessage(form.Handle, WmNcLButtonDblClk, (IntPtr)HtCaption, Lp(form.PointToScreen(new Point(Expected().Header.Left + 200, 10))));
        await Task.Delay(500);
        check("caption_double_click_restores", form.WindowState == FormWindowState.Normal && form.Size == size, $"{form.WindowState} {form.Size}");
        // 最小化 button path: a left click on the button sends WM_SYSCOMMAND SC_MINIMIZE.
        var minimizeEvents = 0;
        buttons.MinimizeRequested += () => minimizeEvents++;
        var minimize = Center(buttons.BoundsOf(Ui.UiWindowButtons.Kind.Minimize));
        SendMessage(buttons.Handle, WmLButtonDown, (IntPtr)1, Lp(minimize));
        SendMessage(buttons.Handle, WmLButtonUp, IntPtr.Zero, Lp(minimize));
        await Task.Delay(500);
        var minimized = form.WindowState == FormWindowState.Minimized;
        var guard = form.HostGuardReason;
        check("minimize_button_minimizes_and_guard_refuses", minimized && guard is not null, new { state = form.WindowState.ToString(), guard, minimizeEvents, iconic = IsIconic(form.Handle) });
        form.WindowState = FormWindowState.Normal;
        var restoreWatch = Stopwatch.StartNew();
        while (restoreWatch.ElapsedMilliseconds < 10_000 && form.HostGuardReason is not null) { await Task.Delay(100); }
        check("restore_keeps_view", form.WindowState == FormWindowState.Normal && form.HostGuardReason is null && form.Size == size
            && Consistent(), form.HostGuardReason);
        await RestorePathChecks(form, output, check, size, Consistent, GuardSettled);
        // Leave the test size for the close path: it must save exactly this restored size.
        form.ClientSize = testClient;
        await Task.Delay(300);
        LastClient = form.ClientSize;
        header.RefreshVisible = false;
        header.MuteVisible = false;
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool PostMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool RedrawWindow(IntPtr window, IntPtr update, IntPtr region, int flags);
    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern IntPtr SendMessage(IntPtr window, int message, IntPtr wParam, string lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(Point point);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern IntPtr GetAncestor(IntPtr window, int flags);
    private const int WmSysCommand = 0x0112, ScMinimize = 0xF020, ScMaximize = 0xF030, ScRestore = 0xF120;
    private const int WmNcActivate = 0x0086, WmSetText = 0x000C;
    private const int RdwInvalidate = 0x0001, RdwFrame = 0x0400, RdwUpdateNow = 0x0100;

    private readonly record struct FrameInfo(int Style, Size Client, Rectangle Window, bool Iconic);

    /// <summary>Frame state read from the system: style, real client vs window rect, iconic.</summary>
    private static FrameInfo FrameState(Form form)
    {
        GetClientRect(form.Handle, out var c);
        GetWindowRect(form.Handle, out var w);
        return new FrameInfo(GetWindowLong(form.Handle, -16), new Size(c.Right - c.Left, c.Bottom - c.Top),
            Rectangle.FromLTRB(w.Left, w.Top, w.Right, w.Bottom), IsIconic(form.Handle));
    }

    /// <summary>
    /// 2026-10-11 (host-20261011d) minimize / restore through the system paths (WM_SYSCOMMAND SC_MINIMIZE / SC_RESTORE as
    /// the 最小化 button and a taskbar click use, ShowWindow SW_MINIMIZE / SW_RESTORE), from normal and from maximized. The
    /// window must come back frameless (no WS_CAPTION, real client rect == window rect) in the same state and size with
    /// the dock layout consistent; then activation, deactivation, a title WM_SETTEXT and a frame redraw must not paint
    /// the classic caption over the header (checked on screen: the system paints it into the window DC, PrintWindow
    /// re-renders and would not show it).
    /// </summary>
    private static async Task RestorePathChecks(CompositionDemoForm form, string output, CheckFn check, Size size,
        Func<bool> consistent, Func<Task<string?>> guardSettled)
    {
        var h = form.Handle;
        var theme = form.UiDock!.Palette.Background.GetBrightness() < 0.5f ? "dark" : "light";
        async Task<bool> WaitFor(Func<bool> condition)
        {
            var watch = Stopwatch.StartNew();
            while (watch.ElapsedMilliseconds < 5_000 && !condition()) { await Task.Delay(100); }
            await Task.Delay(400);
            return condition();
        }
        static bool Frameless(FrameInfo f) => (f.Style & 0x00C00000) == 0 && f.Client == f.Window.Size && !f.Iconic;
        object Detail(FrameInfo f) => new { style = $"0x{f.Style:X8}", client = f.Client.ToString(), window = f.Window.ToString(),
            iconic = f.Iconic, state = form.WindowState.ToString(), formSize = form.Size.ToString() };
        async Task<(bool Clean, object Detail)> NoClassicCaption(string name)
        {
            // Frame redraw first (it also repaints the child controls), then the messages whose default handling paints
            // the caption; each screen shot is taken before anything repaints the header over it.
            RedrawWindow(h, IntPtr.Zero, IntPtr.Zero, RdwInvalidate | RdwFrame | RdwUpdateNow);
            await Task.Delay(300);
            var window = FrameState(form).Window;
            var header = form.ChromeHeader!.Bounds;
            // Empty middle of the header strip (brand + 刷新 on the left, 静音 on the right), above its bottom hairline.
            var probe = Rectangle.FromLTRB(header.Left + header.Width * 3 / 10, header.Top + 4, header.Left + header.Width * 7 / 10, header.Bottom - 6);
            var centre = new Point(window.Left + probe.Left + probe.Width / 2, window.Top + probe.Top + probe.Height / 2);
            var onTop = GetAncestor(WindowFromPoint(centre), 2) == h;   // GA_ROOT: nothing else covers the probe
            var background = form.UiDock!.Palette.Background;
            int? Off(string step)
            {
                var png = Path.Combine(output, $"restore-{name}-{step}-{theme}.png");
                if (!ScreenPng(window, png)) { return null; }
                using var shot = new Bitmap(png);
                var off = 0;
                for (var y = probe.Top; y < probe.Bottom; y += 2)
                {
                    for (var x = probe.Left; x < probe.Right; x += 4)
                    {
                        var c = shot.GetPixel(x, y);
                        if (Math.Abs(c.R - background.R) > 24 || Math.Abs(c.G - background.G) > 24 || Math.Abs(c.B - background.B) > 24) { off++; }
                    }
                }
                return off;
            }
            var redrawn = Off("redraw");
            SendMessage(h, WmNcActivate, (IntPtr)1, IntPtr.Zero);
            var active = Off("ncactivate");
            SendMessage(h, WmNcActivate, IntPtr.Zero, IntPtr.Zero);
            SendMessage(h, WmSetText, IntPtr.Zero, form.Text);
            var inactive = Off("settext");
            return (onTop && redrawn == 0 && active == 0 && inactive == 0 && form.Visible && IsWindowVisible(h)
                && form.Text == BrowserHostOptions.BrowserDemoTitle,
                new { onTop, redrawn, active, inactive, probe = probe.ToString(), title = form.Text });
        }
        async Task Cycle(string name, Action minimize, Action restore, FormWindowState expected)
        {
            minimize();
            var minimized = await WaitFor(() => IsIconic(h));
            restore();
            await WaitFor(() => !IsIconic(h));
            var f = FrameState(form);
            var guard = await guardSettled();
            check(name, minimized && Frameless(f) && form.WindowState == expected
                && (expected != FormWindowState.Normal || form.Size == size) && consistent() && guard is null, Detail(f));
            var (clean, detail) = await NoClassicCaption(name);
            check(name + "_no_classic_caption", clean, detail);
        }
        await Cycle("restore_sc_restore", () => PostMessage(h, WmSysCommand, (IntPtr)ScMinimize, IntPtr.Zero),
            () => PostMessage(h, WmSysCommand, (IntPtr)ScRestore, IntPtr.Zero), FormWindowState.Normal);
        await Cycle("restore_show_window", () => ShowWindow(h, 6), () => ShowWindow(h, 9), FormWindowState.Normal);   // SW_MINIMIZE, SW_RESTORE
        PostMessage(h, WmSysCommand, (IntPtr)ScMaximize, IntPtr.Zero);
        await WaitFor(() => form.WindowState == FormWindowState.Maximized);
        await Cycle("restore_to_maximized", () => PostMessage(h, WmSysCommand, (IntPtr)ScMinimize, IntPtr.Zero),
            () => PostMessage(h, WmSysCommand, (IntPtr)ScRestore, IntPtr.Zero), FormWindowState.Maximized);
        PostMessage(h, WmSysCommand, (IntPtr)ScRestore, IntPtr.Zero);
        await WaitFor(() => form.WindowState == FormWindowState.Normal);
        var back = FrameState(form);
        check("maximized_back_to_normal", Frameless(back) && form.WindowState == FormWindowState.Normal && form.Size == size && consistent(), Detail(back));
        var (clean, detail) = await NoClassicCaption("maximized_back_to_normal");
        check("maximized_back_to_normal_no_classic_caption", clean, detail);
    }

    /// <summary>What is on screen over the window rect (the system paints a classic caption there; PrintWindow would not show it).</summary>
    private static bool ScreenPng(Rectangle bounds, string path)
    {
        try
        {
            using var bitmap = new Bitmap(bounds.Width, bounds.Height);
            using (var g = Graphics.FromImage(bitmap)) { g.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size); }
            bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
            return true;
        }
        catch (Exception) { return false; }
    }
}

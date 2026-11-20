using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// --self-test-background: opens its own window on the self-owned heartbeat page (never the game)
/// and measures, per window state, whether CapturePreviewAsync frames keep changing, whether rAF and
/// timers keep running, whether a SendMouseInput click reaches the page, and what the HostGuard says.
/// Options: --no-flags (omit Chromium switches, for comparison), --seconds N (per case, default 6).
/// </summary>
internal static class BackgroundSelfTest
{
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr window, int attribute, out int value, int size);
    private const int SwShowNoActivate = 4;
    private const int SwShowMinNoActive = 7;

    private sealed class CoverForm : Form
    {
        protected override bool ShowWithoutActivation => true;
        protected override CreateParams CreateParams
        {
            get { var parameters = base.CreateParams; parameters.ExStyle |= 0x08000000 | 0x00000008; return parameters; } // NOACTIVATE | TOPMOST
        }
    }

    internal static void Run(string[] args)
    {
        var flags = true;
        var seconds = 6.0;
        for (var index = 0; index < args.Length; index++)
        {
            if (args[index] == "--no-flags") { flags = false; }
            else if (args[index] == "--seconds" && index + 1 < args.Length) { seconds = Math.Clamp(double.Parse(args[++index], System.Globalization.CultureInfo.InvariantCulture), 2, 60); }
            else { throw new ArgumentException("未知后台自测参数：" + args[index]); }
        }
        var root = Path.Combine(Directory.GetCurrentDirectory(), "dev_assets", "private", "host-debug-bg");
        // Fresh profile per run: AdditionalBrowserArguments must match within one user-data folder,
        // and a browser process still shutting down from an earlier run must not be reused.
        var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss");
        var profile = Path.Combine(root, "selftest-profiles", $"{stamp}-{(flags ? "flags" : "noflags")}");
        Directory.CreateDirectory(profile);
        var options = new BrowserHostOptions(new Uri("https://sc-local.test/" + CompositionDemoForm.HeartbeatPage), profile,
            OfflineDemo: false, BrowserDemo: true, BridgeSession: Path.Combine(profile, "bridge-selftest.json"),
            CompositionDemo: true, BackgroundKeepalive: true, DemoPage: CompositionDemoForm.HeartbeatPage,
            OmitBackgroundArguments: !flags);
        var form = new CompositionDemoForm(options);
        object? report = null;
        var passed = false;
        uint browserProcess = 0;
        form.Shown += async (_, _) =>
        {
            try { (report, passed) = await RunCases(form, flags, seconds); browserProcess = form.BrowserProcessId; }
            catch (Exception exception) { report = new { error = exception.ToString() }; }
            finally { form.Close(); }
        };
        Application.Run(form);
        // Wait for the self-test's own browser process so no orphan keeps the profile locked.
        if (browserProcess != 0)
        {
            try { using var browser = Process.GetProcessById((int)browserProcess); browser.WaitForExit(15000); }
            catch (ArgumentException) { }
        }
        var json = JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true });
        var output = Path.Combine(root, $"selftest-background-{stamp}-{(flags ? "flags" : "noflags")}.json");
        File.WriteAllText(output, json);
        Console.WriteLine(json);
        Console.WriteLine("report: " + output);
        Environment.ExitCode = passed ? 0 : 1;
    }

    private static async Task<(object report, bool passed)> RunCases(CompositionDemoForm form, bool flags, double seconds)
    {
        if (await Task.WhenAny(form.HeartbeatSeen, Task.Delay(30000)) != form.HeartbeatSeen)
        { throw new TimeoutException("心跳页 30 秒内未回报。"); }
        var keeper = form.Keeper ?? throw new InvalidOperationException("后台保活未启用。");
        var handle = form.Handle;
        var cases = new List<Dictionary<string, object?>>();
        CoverForm? cover = null;

        async Task Case(string id, string description, Func<Task> setup, bool expectRender, Func<Task>? teardown = null,
            Func<bool>? extra = null)
        {
            bool IsTestWindow(IntPtr window) => window == handle || (cover is not null && window == cover.Handle);
            var foregroundBefore = IsTestWindow(GetForegroundWindow());
            await setup();
            await Task.Delay(1500);
            var foregroundAfterSetup = IsTestWindow(GetForegroundWindow());
            var start = form.LatestHeartbeat!;
            var clickError = (string?)null;
            try { await form.ProbeClickAsync(new Point(640, 600)); }
            catch (Exception exception) { clickError = exception.Message; }
            await Task.Delay(300);
            var foregroundAfterClick = IsTestWindow(GetForegroundWindow());
            var watch = Stopwatch.StartNew();
            var shas = new List<string>();
            var failures = 0; string? error = null; var hiddenSeen = false; long lastOk = 0; string? size = null;
            while (watch.Elapsed.TotalSeconds < seconds)
            {
                var (ok, sha, captureError, captureSize) = await form.ProbeCaptureAsync(TimeSpan.FromSeconds(3));
                if (ok) { shas.Add(sha!); lastOk = Stopwatch.GetTimestamp(); size = captureSize; } else { failures++; error = captureError; }
                hiddenSeen |= form.LatestHeartbeat?.Hidden == true;
                await Task.Delay(250);
            }
            var elapsed = watch.Elapsed.TotalSeconds;
            var end = form.LatestHeartbeat!;
            var changes = shas.Zip(shas.Skip(1)).Count(pair => pair.First != pair.Second);
            var rafRate = (end.Raf - start.Raf) / elapsed;
            var timerRate = (end.Timer - start.Timer) / elapsed;
            var guard = form.HostGuardReason;
            var fresh = HostGuard.FrameFresh(lastOk == 0 ? null : Stopwatch.GetElapsedTime(lastOk).TotalMilliseconds,
                form.FrameChangeAgeMs, requireChange: true);
            var foreground = GetForegroundWindow();
            _ = DwmGetWindowAttribute(handle, 14, out var cloaked, sizeof(int));
            var rendering = shas.Count > 0 && changes >= Math.Max(1, shas.Count / 2) && rafRate >= 20 && timerRate >= 5;
            // Only a change caused by this case counts: the launching shell may hand the new process foreground.
            var stoleFocus = !foregroundBefore && IsTestWindow(foreground);
            var pass = expectRender
                ? rendering && guard is null && fresh is null && end.Clicks > start.Clicks && !stoleFocus && (extra?.Invoke() ?? true)
                : guard == "window_minimized" && !stoleFocus;
            cases.Add(new()
            {
                ["case"] = id, ["description"] = description, ["expect_render"] = expectRender, ["pass"] = pass,
                ["captures_ok"] = shas.Count, ["captures_failed"] = failures, ["capture_error"] = error,
                ["frame_changes"] = changes, ["distinct_frames"] = shas.Distinct().Count(),
                ["raf_per_s"] = Math.Round(rafRate, 1), ["timer_per_s"] = Math.Round(timerRate, 1),
                ["messages"] = end.Messages - start.Messages, ["page_visibility"] = end.Visibility, ["page_hidden_seen"] = hiddenSeen,
                ["click_reached_page"] = end.Clicks > start.Clicks, ["click_error"] = clickError,
                ["host_guard"] = guard, ["frame_fresh"] = fresh,
                ["hide_mode"] = keeper.HideMode, ["iconic"] = IsIconic(handle), ["dwm_cloaked"] = cloaked,
                ["window_location"] = $"{form.Location.X},{form.Location.Y}", ["client"] = $"{form.ClientSize.Width}x{form.ClientSize.Height}",
                ["foreground_was_test_window"] = foregroundBefore, ["foreground_after_setup"] = foregroundAfterSetup,
                ["foreground_after_click"] = foregroundAfterClick, ["capture_size"] = size, ["stole_focus"] = stoleFocus, ["keeper_error"] = keeper.LastError,
            });
            if (teardown is not null) { await teardown(); await Task.Delay(800); }
        }

        // F0 runs first, while the fresh process still holds foreground rights: worst case for focus theft.
        await Case("F0", "first gesture while cloaked (focus-theft check)", () => { keeper.Hide(BackgroundKeeper.Cloaked); return Task.CompletedTask; },
            true, () => { keeper.Show(activate: false); return Task.CompletedTask; }, () => keeper.HideMode == BackgroundKeeper.Cloaked);
        await Case("T0", "baseline, window shown (not activated)", () => Task.CompletedTask, true);
        await Case("T1", "fully covered by another topmost window", () =>
        {
            cover = new CoverForm
            {
                FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.Manual, ShowInTaskbar = false,
                BackColor = Color.FromArgb(40, 44, 52), Bounds = Rectangle.Inflate(form.Bounds, 40, 40), Text = "cover",
            };
            cover.Show();
            return Task.CompletedTask;
        }, true, () => { cover?.Close(); cover?.Dispose(); cover = null; return Task.CompletedTask; });
        await Case("T2", "real minimize (interception off) - must be refused", () =>
        {
            keeper.Enabled = false;   // the 后台运行 setting off: minimize is a real minimize
            ShowWindow(handle, SwShowMinNoActive);
            return Task.CompletedTask;
        }, false, () => { ShowWindow(handle, SwShowNoActivate); keeper.Enabled = true; return Task.CompletedTask; });
        // The setting API: --background-keepalive (forced here) overrides an off setting.
        var forcedOverride = form.SetBackgroundRun(false) == true && keeper.Enabled;
        form.SetBackgroundRun(true);
        await Case("T8", "setting turned off while hidden -> window comes back, rendering continues", () =>
        {
            keeper.Hide(BackgroundKeeper.Cloaked);
            keeper.Enabled = false;
            return Task.CompletedTask;
        }, true, () => { keeper.Enabled = true; return Task.CompletedTask; },
            () => keeper.HideMode == BackgroundKeeper.None && !keeper.InterceptMinimize && !IsIconic(handle));
        await Case("T3", "DWM cloak (hide to tray)", () => { keeper.Hide(BackgroundKeeper.Cloaked); return Task.CompletedTask; },
            true, () => { keeper.Show(activate: false); return Task.CompletedTask; }, () => keeper.HideMode == BackgroundKeeper.Cloaked);
        await Case("T4", "moved off-screen", () => { keeper.Hide(BackgroundKeeper.Offscreen); return Task.CompletedTask; },
            true, () => { keeper.Show(activate: false); return Task.CompletedTask; }, () => keeper.HideMode == BackgroundKeeper.Offscreen);
        await Case("T2i", "WM_SYSCOMMAND SC_MINIMIZE intercepted -> hide", () =>
        {
            SendMessage(handle, 0x0112, (IntPtr)0xF020, IntPtr.Zero);
            return Task.CompletedTask;
        }, true, () => { keeper.Show(activate: false); return Task.CompletedTask; },
            () => keeper.HideMode != BackgroundKeeper.None && !IsIconic(handle));
        await Case("T2r", "minimize bypassing WM_SYSCOMMAND (Win+D path) -> resize fallback hides", () =>
        {
            ShowWindow(handle, SwShowMinNoActive);
            return Task.CompletedTask;
        }, true, null, () => keeper.HideMode != BackgroundKeeper.None && !IsIconic(handle));
        await Case("T7", "restore from hidden", () => { keeper.Show(activate: false); return Task.CompletedTask; },
            true, null, () => keeper.HideMode == BackgroundKeeper.None && form.HostGuardReason is null);

        var passed = cases.All(item => (bool)item["pass"]!) && forcedOverride;
        return (new
        {
            self_test = "background", passed, browser_flags = flags, arguments = HostGuard.GameArguments(flags),
            runtime = CoreWebView2Environment.GetAvailableBrowserVersionString(), os = Environment.OSVersion.VersionString,
            seconds_per_case = seconds, real_game_contact = false, setting_forced_override = forcedOverride, cases,
        }, passed);
    }
}

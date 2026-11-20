using System.Diagnostics;
using System.Drawing;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// Window/controller state sampled on the UI thread; the guard itself is pure. <see cref="HostSnapshot.DisplaySize"/>
/// is the size the committed display transform maps the 1280x720 canvas onto (display-only scaling; the canvas size
/// for hosts that never scale).
/// </summary>
internal readonly record struct HostSnapshot(
    bool PageReady,
    bool WindowVisible,
    bool Minimized,
    bool ViewportVisible,
    Size ViewportSize,
    Rectangle WebViewBounds,
    bool ControllerVisible,
    bool DesktopLocked,
    Size DisplaySize);

/// <summary>
/// Single host guard for capture and input. Hide-to-tray (cloak/off-screen) is not a refusal
/// reason: the window stays WS_VISIBLE, non-minimized and 1280x720. A true minimize, a locked
/// session, a hidden controller or changed geometry always refuse; callers never retry.
/// </summary>
internal static class HostGuard
{
    /// <summary>Chromium switches for --background-keepalive. Experimental and unsupported by Microsoft.</summary>
    public const string BackgroundArguments =
        "--disable-features=CalculateNativeWinOcclusion " +
        "--disable-renderer-backgrounding " +
        "--disable-backgrounding-occluded-windows " +
        "--disable-background-timer-throttling";

    /// <summary>
    /// 2026-11-04: on a Chinese Windows every Maa task timed out at its first recognition while this en-US PC worked.
    /// The WebView2 locale follows the OS display language and picks the CJK fallback fonts for text without a lang
    /// tag, so the game locale is pinned to the one the recognition templates were captured with; sRGB output keeps a
    /// wide-gamut or HDR monitor's colour profile out of the captures.
    /// </summary>
    public const string GameLanguage = "en-US";

    /// <summary>Chromium switches for every game WebView (the keepalive switches are added on top).</summary>
    public const string RenderArguments = "--force-color-profile=srgb";

    public static string GameArguments(bool keepalive) => keepalive ? RenderArguments + " " + BackgroundArguments : RenderArguments;

    public static readonly Size Canvas = new(BrowserHostOptions.ViewportWidth, BrowserHostOptions.ViewportHeight);

    /// <summary>Returns null when the host can render and accept bound input, else a named reason.</summary>
    public static string? Renderable(HostSnapshot state) =>
        !state.PageReady ? "page_not_ready" :
        state.Minimized ? "window_minimized" :
        !state.WindowVisible ? "window_hidden" :
        state.DesktopLocked ? "desktop_locked" :
        !state.ControllerVisible ? "webview_not_visible" :
        // The WebView itself (render, capture and program-input geometry) must stay exactly 1280x720; only the displayed
        // viewport may be scaled, and only to the size the committed visual transform shows (16:9, in range).
        !state.ViewportVisible || state.WebViewBounds != new Rectangle(Point.Empty, Canvas) ||
        state.ViewportSize != state.DisplaySize || !GameDisplay.DisplayAllowed(state.DisplaySize) ? "viewport_geometry_changed" : null;

    /// <summary>
    /// Age-only freshness for the game (its idle screens may legitimately be static). The
    /// heartbeat test page sets <paramref name="requireChange"/> so a renderer that stopped
    /// producing new frames but still returns the old one is detected.
    /// </summary>
    public static string? FrameFresh(double? frameAgeMs, double? changeAgeMs, bool requireChange,
        double maxAgeMs = 5000, double maxChangeAgeMs = 3000) =>
        frameAgeMs is null ? "no_frame" :
        frameAgeMs > maxAgeMs ? "frame_stale" :
        requireChange && (changeAgeMs is null || changeAgeMs > maxChangeAgeMs) ? "frame_not_changing" : null;

    /// <summary>Offline truth table; never creates a window or browser.</summary>
    internal static void SelfCheck(Action<bool> check)
    {
        var good = new HostSnapshot(true, true, false, true, Canvas, new Rectangle(0, 0, 1280, 720), true, false, Canvas);
        check(Renderable(good) is null);
        check(Renderable(good with { PageReady = false }) == "page_not_ready");
        check(Renderable(good with { Minimized = true }) == "window_minimized");
        check(Renderable(good with { WindowVisible = false }) == "window_hidden");
        check(Renderable(good with { DesktopLocked = true }) == "desktop_locked");
        check(Renderable(good with { ControllerVisible = false }) == "webview_not_visible");
        check(Renderable(good with { ViewportSize = new Size(0, 0) }) == "viewport_geometry_changed");
        check(Renderable(good with { ViewportVisible = false }) == "viewport_geometry_changed");
        check(Renderable(good with { WebViewBounds = new Rectangle(0, 0, 1282, 721) }) == "viewport_geometry_changed");
        // display-only scaling: a scaled viewport is fine while it equals the committed transform and the WebView
        // stays 1280x720; any mismatch, distortion, out-of-range scale or scaled WebView bounds refuses.
        var scaled = new Size(1463, 823);
        check(Renderable(good with { ViewportSize = scaled, DisplaySize = scaled }) is null);
        check(Renderable(good with { ViewportSize = new Size(768, 432), DisplaySize = new Size(768, 432) }) is null);
        check(Renderable(good with { ViewportSize = scaled }) == "viewport_geometry_changed");
        check(Renderable(good with { DisplaySize = scaled }) == "viewport_geometry_changed");
        check(Renderable(good with { ViewportSize = Size.Empty, DisplaySize = Size.Empty }) == "viewport_geometry_changed");
        check(Renderable(good with { ViewportSize = new Size(1463, 700), DisplaySize = new Size(1463, 700) }) == "viewport_geometry_changed");
        check(Renderable(good with { ViewportSize = new Size(500, 281), DisplaySize = new Size(500, 281) }) == "viewport_geometry_changed");
        check(Renderable(good with { ViewportSize = scaled, DisplaySize = scaled, WebViewBounds = new Rectangle(Point.Empty, scaled) }) == "viewport_geometry_changed");
        // Minimize wins over every other reason so the report names the real cause.
        check(Renderable(good with { Minimized = true, ViewportSize = Size.Empty }) == "window_minimized");
        check(FrameFresh(null, null, false) == "no_frame");
        check(FrameFresh(6000, 10, false) == "frame_stale");
        check(FrameFresh(100, null, false) is null);
        check(FrameFresh(100, 60000, false) is null);
        check(FrameFresh(100, null, true) == "frame_not_changing");
        check(FrameFresh(100, 4000, true) == "frame_not_changing");
        check(FrameFresh(100, 200, true) is null);
        var tracker = new FrameChangeTracker();
        check(!tracker.Changing() && tracker.ChangeAgeMs is null);
        tracker.Observe("a"); tracker.Observe("a");
        check(!tracker.Changing());
        tracker.Observe("b");
        check(tracker.Changing() && tracker.ChangeAgeMs is >= 0);
        check(BackgroundArguments.Contains("CalculateNativeWinOcclusion") && !BackgroundArguments.Contains("  "));
        check(GameArguments(false) == RenderArguments && GameArguments(true).StartsWith(RenderArguments + " --", StringComparison.Ordinal)
            && GameArguments(true).EndsWith(BackgroundArguments, StringComparison.Ordinal));
    }
}

/// <summary>Records when consecutive captures last differed. Informational for the game.</summary>
internal sealed class FrameChangeTracker
{
    private string? _last;
    private long _lastChange;
    private bool _hasChange;

    public void Observe(string sha256)
    {
        if (_last is not null && sha256 != _last) { _lastChange = Stopwatch.GetTimestamp(); _hasChange = true; }
        _last = sha256;
    }

    public double? ChangeAgeMs => _hasChange ? Stopwatch.GetElapsedTime(_lastChange).TotalMilliseconds : null;

    public bool Changing(double windowMs = 10000) => ChangeAgeMs is double age && age <= windowMs;
}

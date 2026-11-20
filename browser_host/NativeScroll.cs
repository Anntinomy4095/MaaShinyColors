using System.Runtime.InteropServices;
using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Owned WebView2 wheel only; no desktop cursor, buttons, or fallback.</summary>
internal static class NativeScroll
{
    /// <summary>Lines per notch the wheel deltas were calibrated with (the Windows default).</summary>
    internal const uint CalibratedLines = 3;
    private const uint WheelPageScroll = uint.MaxValue;
    private const uint SpiGetWheelScrollLines = 0x0068;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SystemParametersInfo(uint action, uint param, out uint value, uint winIni);

    /// <summary>Windows 「一次滚动的行数」. Chromium scales every wheel delta by it, so the same delta scrolls
    /// further on a PC with a larger setting.</summary>
    internal static uint SystemWheelLines() =>
        SystemParametersInfo(SpiGetWheelScrollLines, 0, out var lines, 0) ? lines : CalibratedLines;

    /// <summary>Delta that scrolls the calibrated distance under <paramref name="lines"/>; null when the
    /// distance cannot be matched (page-at-a-time scrolling or 0 lines).</summary>
    internal static int? Normalize(int delta, uint lines)
    {
        if (lines == CalibratedLines) { return delta; }
        if (lines == 0 || lines == WheelPageScroll) { return null; }
        var scaled = (int)Math.Round(delta * (double)CalibratedLines / lines, MidpointRounding.AwayFromZero);
        return scaled == 0 ? Math.Sign(delta) : scaled;
    }

    internal static void Send(string action, Point point, Action<CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys, uint, Point> send,
        uint? lines = null) =>
        SendDelta(DailyNavigationPolicy.FixedWheelDelta(action), point, send, lines);

    /// <summary>Pipeline wheel actions: fixed delta from the embedded table (±60/±120/±240), scaled to the
    /// system wheel setting so one action scrolls the same distance on every PC.</summary>
    internal static void SendDelta(int delta, Point point, Action<CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys, uint, Point> send,
        uint? lines = null)
    {
        var systemLines = lines ?? SystemWheelLines();
        var wheel = Normalize(delta, systemLines) ?? throw new InvalidOperationException(
            "Windows 鼠标设置为「一次滚动一个屏幕」，无法控制滚动距离；请在 设置 → 蓝牙和其他设备 → 鼠标 中改为按行滚动。");
        send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None, 0, point);
        // WebView2's uint mouseData carries the signed WHEEL_DELTA bit pattern.
        send(CoreWebView2MouseEventKind.Wheel, CoreWebView2MouseEventVirtualKeys.None, unchecked((uint)wheel), point);
    }
}

using System.Diagnostics;

namespace MaaShinyColors.BrowserHost;

internal static class HostMonotonic
{
    internal static double NowMs() => Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency;
}

/// <summary>One ordinary APPEAL bar tap (W.I.N.G. or Grade Fes). These are host monotonic times, not render timestamps.</summary>
internal sealed record AppealTiming(double TargetDownMs, double PeriodMs, double LatestDownMs)
{
    // v1 took the W.I.N.G. bar only; v2 adds the Grade Fes bar, same geometry and plan.
    internal const string Protocol = "SC.AppealTiming.v2";
    internal const string Action = "wing-live-appeal-timing";
    internal const string Node = "SC.Wing.Click.Live.live_appeal_timing";
    internal const string GradeAction = "live-appeal-timing";
    internal const string GradeNode = "SC.Grade.Live.Timing";
    internal const double MaxHorizonMs = 5000;
    // Scheduling tolerance only; this does not promise the game's Perfect window or judgment event.
    internal const double MaxSchedulingLatenessMs = 15;

    internal static bool Allowed(string action, string? node) =>
        (action == Action && node == Node) || (action == GradeAction && node == GradeNode);

    internal string? Refusal(string action, string? node) => !Allowed(action, node) ? "appeal_timing_action_not_allowed"
        : !double.IsFinite(TargetDownMs) || !double.IsFinite(PeriodMs) || !double.IsFinite(LatestDownMs) ||
          TargetDownMs < 0 || PeriodMs is < 900 or > 1050 || LatestDownMs < TargetDownMs ||
          LatestDownMs - TargetDownMs > MaxHorizonMs ? "appeal_timing_invalid" : null;

    internal double? Select(double now)
    {
        if (!double.IsFinite(now) || LatestDownMs <= now || LatestDownMs - now > MaxHorizonMs) { return null; }
        var cycles = Math.Max(0, Math.Floor((now - TargetDownMs) / PeriodMs) + 1);
        var selected = TargetDownMs + cycles * PeriodMs;
        return double.IsFinite(selected) && selected > now && selected <= LatestDownMs ? selected : null;
    }
}

/// <summary>Event-call observations only; Down versus Up as the game's judgment event remains unverified.</summary>
internal sealed class AppealTimingObservation(AppealTiming plan)
{
    public double requested_down_ms { get; } = plan.TargetDownMs;
    public double period_ms { get; } = plan.PeriodMs;
    public double latest_down_ms { get; } = plan.LatestDownMs;
    public double? selected_down_ms { get; internal set; }
    public double? actual_down_ms { get; internal set; }
    public double? actual_up_ms { get; internal set; }
    public int rollovers { get; internal set; }
    public double? lateness_ms => actual_down_ms - selected_down_ms;
    public string judgment_event => "unverified_down_or_up";
}

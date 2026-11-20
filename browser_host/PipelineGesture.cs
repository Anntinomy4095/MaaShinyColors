using System.Diagnostics;
using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Pipeline-only gesture. The guard, frame consumption and Down/Wheel run synchronously on the host UI thread.</summary>
internal static class PipelineGesture
{
    internal const double SpinLeadMs = 20;

    /// <summary>
    /// Task.Delay wakes on the ~15.6 ms system tick, so a timed Down landed 0–15 ms late and any wake past
    /// <see cref="AppealTiming.MaxSchedulingLatenessMs"/> rolled it a whole period (10-26/27: 73 taps spread evenly
    /// over 0–15 ms; four rollovers used up the 5 s frame deadline twice). Sleep until one tick before the target,
    /// then spin the rest on the calling (UI) thread; never returns early.
    /// </summary>
    internal static async Task PreciseDelay(double ms, CancellationToken token)
    {
        var deadline = Stopwatch.GetTimestamp() + (long)(Math.Max(0, ms) * Stopwatch.Frequency / 1000.0);
        if (ms > SpinLeadMs) { await Task.Delay(TimeSpan.FromMilliseconds(ms - SpinLeadMs), token); }
        while (Stopwatch.GetTimestamp() < deadline)
        {
            token.ThrowIfCancellationRequested();
            Thread.SpinWait(64);
        }
    }

    internal static async Task<NavigationResult> Click(Func<string?> guard, Action consumeFrame,
        Action<CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys> send, Func<int, Task>? delay = null,
        AppealTiming? timing = null, AppealTimingObservation? observation = null, Action? timedDown = null,
        CancellationToken cancellation = default, Func<double>? monotonicMs = null,
        Func<double, CancellationToken, Task>? timingDelay = null, int? holdMs = null)
    {
        delay ??= ms => Task.Delay(ms);
        monotonicMs ??= HostMonotonic.NowMs;
        timingDelay ??= PreciseDelay;
        var attempted = false;
        try
        {
            if (guard() is string refusal) { return new(false, false, refusal); }
            attempted = true; // A native call may take effect before it throws.
            send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None);
            await delay(50);
            if (guard() is string afterHover) { return new(false, attempted, afterHover); }
            send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None);
            await delay(30);
            if (timing is not null)
            {
                // Cooldown, admission, audit and both hover windows have already completed. Roll a missed base
                // target forward by whole periods; the request still binds the same guarded capture and one nonce.
                if (guard() is string beforeWait) { return new(false, attempted, beforeWait); }
                var selected = timing.Select(monotonicMs());
                if (selected is not double target) { return new(false, attempted, "appeal_timing_no_future_target"); }
                try
                {
                    while (true)
                    {
                        if (observation is not null)
                        {
                            observation.selected_down_ms = target;
                            observation.rollovers = (int)Math.Round((target - timing.TargetDownMs) / timing.PeriodMs);
                        }
                        var remaining = target - monotonicMs();
                        while (remaining > 0)
                        {
                            await timingDelay(remaining, cancellation);
                            remaining = target - monotonicMs();
                        }
                        if (cancellation.IsCancellationRequested) { return new(false, attempted, "cancelled_before_input"); }
                        if (guard() is string afterWait) { return new(false, attempted, afterWait); }
                        var now = monotonicMs();
                        if (now > timing.LatestDownMs) { return new(false, attempted, "appeal_timing_deadline_missed_no_input"); }
                        if (now - target <= AppealTiming.MaxSchedulingLatenessMs) { break; }
                        // A delayed UI continuation missed this phase. No Down yet: use the next guarded period,
                        // or stop if this frame's bounded deadline has no future center left.
                        var next = timing.Select(now);
                        if (next is not double future) { return new(false, attempted, "appeal_timing_no_future_target"); }
                        target = future;
                    }
                }
                catch (OperationCanceledException) { return new(false, attempted, "cancelled_before_input"); }
            }
            else if (guard() is string beforeDown) { return new(false, attempted, beforeDown); }
            consumeFrame(); // No await between the final guard, invalidation and the first Down.
            Exception? primary = null;
            try
            {
                if (timing is not null)
                {
                    timedDown?.Invoke();
                    if (observation is not null) { observation.actual_down_ms = monotonicMs(); }
                }
                send(CoreWebView2MouseEventKind.LeftButtonDown, CoreWebView2MouseEventVirtualKeys.LeftButton);
                await delay(holdMs ?? 120); // Always release an attempted Down, even if cancellation arrives now.
            }
            catch (Exception exception) { primary = exception; throw; }
            finally
            {
                try
                {
                    if (timing is not null && observation is not null) { observation.actual_up_ms = monotonicMs(); }
                    send(CoreWebView2MouseEventKind.LeftButtonUp, CoreWebView2MouseEventVirtualKeys.None);
                }
                catch (Exception release) when (primary is not null)
                { throw new AggregateException("按下与释放均失败，不重试。", primary, release); }
            }
            return new(true, attempted, "native_gesture_sent_postcondition_required");
        }
        catch (Exception exception) { return new(false, attempted, "native_input_failed_no_retry: " + exception.Message); }
    }

    /// <summary>Interpolated pointer path from <paramref name="start"/> to <paramref name="end"/>: one point per step,
    /// the last one exactly <paramref name="end"/>.</summary>
    internal static Point[] DragPath(Point start, Point end, int steps)
    {
        steps = Math.Max(1, steps);
        return Enumerable.Range(1, steps).Select(i => new Point(
            start.X + (int)Math.Round((end.X - start.X) * (double)i / steps),
            start.Y + (int)Math.Round((end.Y - start.Y) * (double)i / steps))).ToArray();
    }

    internal const int DragStepMs = 16;
    internal const int DragPressMs = 80;
    internal const int DragSettleMs = 80;

    /// <summary>
    /// SC.PipelineDrag.v1: hover at the start, guard, consume the frame, press, move along <see cref="DragPath"/> with the
    /// button held for <paramref name="durationMs"/>, settle, release at the end. Every attempted press is released
    /// (at the last point the pointer reached) even when a move or the cancellation fails; never retried.
    /// </summary>
    internal static async Task<NavigationResult> Drag(Func<string?> guard, Action consumeFrame, Point start, Point end, int durationMs,
        Action<CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys, Point> send, Func<int, Task>? delay = null)
    {
        delay ??= ms => Task.Delay(ms);
        var attempted = false;
        try
        {
            if (guard() is string refusal) { return new(false, false, refusal); }
            attempted = true;
            send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None, start);
            await delay(50);
            if (guard() is string afterHover) { return new(false, attempted, afterHover); }
            send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None, start);
            await delay(30);
            if (guard() is string beforeDown) { return new(false, attempted, beforeDown); }
            consumeFrame(); // No await between the final guard, invalidation and the Down.
            var current = start;
            Exception? primary = null;
            try
            {
                send(CoreWebView2MouseEventKind.LeftButtonDown, CoreWebView2MouseEventVirtualKeys.LeftButton, start);
                await delay(DragPressMs);
                var path = DragPath(start, end, durationMs / DragStepMs);
                foreach (var point in path)
                {
                    send(CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.LeftButton, point);
                    current = point;
                    await delay(DragStepMs);
                }
                await delay(DragSettleMs);
            }
            catch (Exception exception) { primary = exception; throw; }
            finally
            {
                try { send(CoreWebView2MouseEventKind.LeftButtonUp, CoreWebView2MouseEventVirtualKeys.None, current); }
                catch (Exception release) when (primary is not null)
                { throw new AggregateException("拖动与释放均失败，不重试。", primary, release); }
            }
            return new(true, attempted, "native_drag_sent_postcondition_required");
        }
        catch (Exception exception) { return new(false, attempted, "native_input_failed_no_retry: " + exception.Message); }
    }

    internal static NavigationResult Wheel(Func<string?> guard, Action consumeFrame, int delta, Point point,
        Action<CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys, uint, Point> send)
    {
        var attempted = false;
        try
        {
            if (guard() is string refusal) { return new(false, false, refusal); }
            consumeFrame();
            attempted = true;
            NativeScroll.SendDelta(delta, point, send); // Move and Wheel have no asynchronous gap.
            return new(true, true, "native_wheel_sent_postcondition_required");
        }
        catch (Exception exception) { return new(false, attempted, "native_input_failed_no_retry: " + exception.Message); }
    }
}

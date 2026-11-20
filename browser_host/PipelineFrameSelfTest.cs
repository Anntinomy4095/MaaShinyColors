using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline sequence and gesture fault injection. Every sender records events; no WebView is created.</summary>
internal static class PipelineFrameSelfTest
{
    private sealed class Scenario
    {
        internal readonly byte[] Secret = Enumerable.Repeat((byte)7, 32).ToArray();
        internal readonly List<string> Events = new();
        internal readonly CancellationTokenSource Cancellation = new();
        internal readonly PipelineInputGate Gate;
        internal DateTimeOffset Now = new(2026, 10, 4, 3, 0, 0, TimeSpan.Zero);
        internal double Mono;
        internal long Capture = 1, Binding = 2;
        internal string? Sha = new string('a', 64);
        internal bool Ready = true, Pending;
        internal Action? IntervalFault, HostFault, SenderFault;
        internal Action<int>? HoverFault;
        internal bool ThrowDown;

        internal Scenario(string parent, string name)
        {
            Gate = new(Secret, Path.Combine(parent, "pipeline-frame", name, Guid.NewGuid().ToString("N")),
                () => Now, () => Mono, new PipelineLimits(IntervalJitterMaxMs: 0),
                PipelineActionTable.Default, _ => 0, (ms, _) =>
                {
                    Events.Add("interval"); Mono += ms; IntervalFault?.Invoke();
                    return Task.CompletedTask;
                });
        }

        internal PipelineClickRequest Request(bool spend = false)
        {
            var frame = new PipelineFrameRequest(spend ? "shop-exchange-confirm" : "home-mission",
                Sha!, 1280, 720, Guid.NewGuid().ToString("N"), Capture, Binding);
            var node = Gate.Table.Get(frame.Action).Nodes![0];
            if (!spend) { return new(frame, null, node); }
            var expires = Now.ToUnixTimeMilliseconds() + 1000;
            var grant = new PipelineSpendGrant(frame.Action, frame.FrameSha256, frame.Nonce, expires,
                frame.CaptureSequence, frame.BindingSequence,
                PipelineInputPolicy.Sign(Secret, frame.Action, frame.FrameSha256, frame.Nonce, expires,
                    frame.CaptureSequence, frame.BindingSequence));
            return new(frame, grant, node);
        }

        internal void SamePngCapture() { Capture++; Binding += 2; }
        internal bool Bound(PipelineFrameRequest frame) => PipelineInputPolicy.FrameMatches(frame, Sha,
            1280, 720, 10, Capture, Binding, Pending);
        internal string? Host() { HostFault?.Invoke(); return Ready ? null : "host_state_guard_failed"; }
        internal void Consume() { Events.Add("consume"); Sha = null; Binding++; }
        internal void FakeSend(CoreWebView2MouseEventKind kind, CoreWebView2MouseEventVirtualKeys keys)
        {
            Events.Add(kind.ToString());
            if (kind == CoreWebView2MouseEventKind.LeftButtonDown && ThrowDown)
            { throw new InvalidOperationException("injected Down failure"); }
        }
        internal Task Delay(int ms)
        {
            Events.Add("delay:" + ms); Mono += ms; Now = Now.AddMilliseconds(ms);
            HoverFault?.Invoke(ms); return Task.CompletedTask;
        }
        internal Task<NavigationResult> Execute(PipelineClickRequest request) =>
            Gate.Execute(request, Host, Bound, (_, _, guard) =>
            {
                SenderFault?.Invoke();
                return PipelineGesture.Click(guard, Consume, FakeSend, Delay);
            }, Cancellation.Token);
        internal int Downs => Events.Count(value => value == "LeftButtonDown");
        internal int Ups => Events.Count(value => value == "LeftButtonUp");
        internal bool IntentWritten => File.Exists(Gate.AuditPath(Gate.GameDay())) &&
            File.ReadAllLines(Gate.AuditPath(Gate.GameDay())).Any(line => line.Contains("\"intent\""));
    }

    internal static async Task Run(string parent, Action<bool> check)
    {
        // A byte-identical PNG must not hide a new capture, invalidation, or pending capture.
        foreach (var fault in new[] { "capture", "binding", "pending" })
        {
            var s = new Scenario(parent, "initial-" + fault); var request = s.Request();
            if (fault == "capture") { s.SamePngCapture(); }
            else if (fault == "binding") { s.Binding++; }
            else { s.Pending = true; }
            var result = await s.Execute(request);
            check(!result.ok && !result.attempted && result.reason == "frame_binding_guard_failed");
            check(s.Downs == 0 && s.Events.Count == 0 && s.Gate.RunCount == 0);
        }

        // The first batch consumes a nonce, but a second batch invalidated during its interval is not admitted.
        {
            var s = new Scenario(parent, "interval"); var first = s.Request();
            check(s.Gate.Admit(first) is null);
            var request = s.Request(); s.IntervalFault = s.SamePngCapture;
            var result = await s.Execute(request);
            check(!result.ok && !result.attempted && result.reason == "frame_changed_during_interval_wait");
            check(s.Events.SequenceEqual(new[] { "interval" }) && s.Gate.RunCount == 1 && s.Downs == 0);
            check(!(await s.Execute(request)).ok && s.Downs == 0 && s.Gate.RunCount == 1);
        }

        // Mutate only after a real intent line exists: checks after audit must reject the old sequence.
        {
            var s = new Scenario(parent, "after-audit"); var request = s.Request(); var injected = false;
            s.HostFault = () =>
            {
                if (!injected && s.IntentWritten) { injected = true; s.SamePngCapture(); }
            };
            var result = await s.Execute(request);
            check(injected && !result.ok && !result.attempted && result.reason == "frame_changed_after_audit_no_retry");
            check(s.Downs == 0 && s.Events.Count == 0 && s.Gate.RunCount == 1);
            s.HostFault = null; s.Capture = request.Frame.CaptureSequence; s.Binding = request.Frame.BindingSequence;
            s.Mono += 800;
            check((await s.Execute(request)).reason == "nonce_replayed" && s.Gate.RunCount == 1 && s.Downs == 0);
        }

        // The concrete sender is responsible for a final synchronous guard, even after the gate approved it.
        {
            var s = new Scenario(parent, "sender-entry"); var request = s.Request();
            s.SenderFault = s.SamePngCapture;
            var result = await s.Execute(request);
            check(!result.ok && !result.attempted && result.reason == "frame_changed_before_input_no_retry");
            check(s.Events.Count == 0 && s.Gate.RunCount == 1 && s.Downs == 0);
        }

        // Both hover windows independently reject new sequences, host loss, cancellation and expired grants.
        foreach (var window in new[] { 50, 30 })
        foreach (var fault in new[] { "capture", "binding", "host", "cancel", "expiry" })
        {
            var s = new Scenario(parent, $"hover-{window}-{fault}"); var request = s.Request(fault == "expiry");
            var injected = false;
            s.HoverFault = ms =>
            {
                if (ms != window || injected) { return; }
                injected = true;
                switch (fault)
                {
                    case "capture": s.SamePngCapture(); break;
                    case "binding": s.Binding++; break;
                    case "host": s.Ready = false; break;
                    case "cancel": s.Cancellation.Cancel(); break;
                    case "expiry": s.Now = DateTimeOffset.FromUnixTimeMilliseconds(request.Grant!.ExpiresAt); break;
                }
            };
            var result = await s.Execute(request);
            var reason = fault switch
            {
                "host" => "host_state_guard_failed", "cancel" => "cancelled_before_input",
                "expiry" => "spend_grant_expired_before_input", _ => "frame_changed_before_input_no_retry",
            };
            check(injected && !result.ok && result.attempted && result.reason == reason);
            check(s.Downs == 0 && s.Ups == 0 && !s.Events.Contains("consume") && s.Gate.RunCount == 1);
            check(s.Events.Count(value => value == "Move") == (window == 50 ? 1 : 2));
            // Restore the synthetic frame, but never restore admission: the consumed nonce/grant still rejects.
            s.Capture = request.Frame.CaptureSequence; s.Binding = request.Frame.BindingSequence; s.Ready = true;
            s.HoverFault = null; s.Mono += 800; s.Now = new(2026, 10, 4, 3, 0, 0, TimeSpan.Zero);
            var count = s.Events.Count;
            var again = await s.Execute(request);
            check(!again.ok && !again.attempted && s.Events.Count == count && s.Downs == 0 && s.Gate.RunCount == 1);
            check(s.Gate.Admit(request) == (fault == "expiry" ? "spend_grant_already_used" : "nonce_replayed"));
        }

        // Positive timing and ordering: the frame is consumed immediately before the only Down.
        {
            var s = new Scenario(parent, "positive"); var result = await s.Execute(s.Request(true));
            check(result.ok && result.attempted && s.Gate.RunCount == 1);
            check(s.Events.SequenceEqual(new[] { "Move", "delay:50", "Move", "delay:30", "consume",
                "LeftButtonDown", "delay:120", "LeftButtonUp" }));
        }
        // Cancellation or failure after Down must still release exactly once, without a second Down.
        foreach (var fault in new[] { "cancel", "down-throws", "hold-throws" })
        {
            var s = new Scenario(parent, "down-" + fault);
            s.ThrowDown = fault == "down-throws";
            s.HoverFault = ms =>
            {
                if (ms != 120) { return; }
                if (fault == "cancel") { s.Cancellation.Cancel(); }
                else if (fault == "hold-throws") { throw new OperationCanceledException("injected hold interruption"); }
            };
            var result = await s.Execute(s.Request());
            check(result.attempted && result.ok == (fault == "cancel"));
            check(s.Downs == 1 && s.Ups == 1 && s.Events.Count(value => value == "consume") == 1);
        }

        // Wheel's final guard and consume/Move/Wheel sequence have no asynchronous interval.
        {
            var events = new List<string>();
            var denied = PipelineGesture.Wheel(() => "frame_changed_before_input_no_retry", () => events.Add("consume"),
                -240, new Point(100, 100), (kind, _, _, _) => events.Add(kind.ToString()));
            check(!denied.ok && !denied.attempted && events.Count == 0);
            var accepted = PipelineGesture.Wheel(() => null, () => events.Add("consume"), -240,
                new Point(100, 100), (kind, _, data, point) =>
                {
                    events.Add(kind.ToString());
                    check(point == new Point(100, 100));
                    if (kind == CoreWebView2MouseEventKind.Wheel) { check(unchecked((int)data) == -240); }
                });
            check(accepted.ok && accepted.attempted && events.SequenceEqual(new[] { "consume", "Move", "Wheel" }));
        }

        // MAC v2 authenticates each sequence; neither grant fields nor request fields may be rebound.
        foreach (var field in new[] { "capture", "binding" })
        {
            var s = new Scenario(parent, "hmac-" + field); var request = s.Request(true);
            var tamperedGrant = field == "capture" ? request.Grant! with { CaptureSequence = 2 }
                : request.Grant! with { BindingSequence = 3 };
            check(s.Gate.Admit(request with { Grant = tamperedGrant }) == "spend_grant_hmac_invalid");
            var tamperedFrame = field == "capture" ? request.Frame with { CaptureSequence = 2 }
                : request.Frame with { BindingSequence = 3 };
            check(s.Gate.Admit(request with { Frame = tamperedFrame }) == "spend_grant_sequence_mismatch");
            check(s.Gate.RunCount == 0);
            check(s.Gate.Admit(request) is null && s.Gate.RunCount == 1);
            check(s.Gate.Admit(request) == "spend_grant_already_used" && s.Gate.RunCount == 1);
        }
    }
}

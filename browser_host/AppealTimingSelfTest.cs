using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Injected clocks and pointer event logs only: no browser, game or real input.</summary>
internal static class AppealTimingSelfTest
{
    internal static async Task RunStandalone()
    {
        var root = Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "appeal_timing_selftests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var count = 0;
        void Check(bool value) { if (!value) { throw new InvalidOperationException($"Appeal 定时离线自检失败（第 {count + 1} 项）。"); } count++; }
        await Run(root, Check);
        Console.WriteLine(JsonSerializer.Serialize(new { passed = true, checks = count, real_input_attempts = 0, evidence_directory = root }));
    }

    private static PipelineActionTable Table() => PipelineActionTable.Parse(Encoding.UTF8.GetBytes("""
        {"version":2,"canvas":[1280,720],"families":["wing-live","grade-live","navigation"],"margin_percent":20,"min_inner_px":2,"actions":[
          {"id":"wing-live-appeal-timing","family":"wing-live","consuming":false,"enabled":true,"rect":[440,300,400,160],"source":"offline","nodes":["SC.Wing.Click.Live.live_appeal_timing"]},
          {"id":"live-appeal-timing","family":"grade-live","consuming":false,"enabled":true,"rect":[385,478,70,45],"source":"offline","nodes":["SC.Grade.Live.Timing"]},
          {"id":"home-mission","family":"navigation","consuming":false,"enabled":true,"rect":[10,10,80,80],"source":"offline","nodes":["SC.Missions.Open"]}]}
        """));

    internal static async Task Run(string root, Action<bool> check)
    {
        var plan = new AppealTiming(100, 970, 4000);
        check(plan.Select(0) == 100 && plan.Select(100) == 1070 && plan.Select(1080) == 2040);
        check(new AppealTiming(100, 970, 1069).Select(100) is null);
        check(new AppealTiming(100, 970, 100).Select(100) is null);
        check(plan.Refusal(AppealTiming.Action, AppealTiming.Node) is null);
        check(plan.Refusal(AppealTiming.GradeAction, AppealTiming.GradeNode) is null);
        check(plan.Refusal(AppealTiming.GradeAction, AppealTiming.Node) is not null);
        check(plan.Refusal(AppealTiming.Action, AppealTiming.GradeNode) is not null);
        check(plan.Refusal("live-memory-appeal", "SC.Grade.Live.Memory") is not null);
        check(plan.Refusal(AppealTiming.Action, null) is not null);
        foreach (var invalid in new[] { new AppealTiming(double.NaN, 970, 1000), new AppealTiming(100, double.PositiveInfinity, 1000),
            new AppealTiming(-1, 970, 1000), new AppealTiming(100, 99, 1000), new AppealTiming(100, 970, 99), new AppealTiming(100, 970, 5101) })
        { check(invalid.Refusal(AppealTiming.Action, AppealTiming.Node) is not null); }

        var table = Table();
        const string token = "test-token";
        Dictionary<string, object?> Body() => new()
        {
            ["token"] = token, ["command"] = "pipeline-click", ["action"] = AppealTiming.Action, ["node_name"] = AppealTiming.Node,
            ["frame_sha256"] = new string('a', 64), ["source_width"] = 1280, ["source_height"] = 720,
            ["nonce"] = new string('b', 32), ["protocol_version"] = 2, ["capture_seq"] = 1, ["frame_binding_seq"] = 2,
            ["appeal_timing"] = new Dictionary<string, object?> { ["target_down_ms"] = 100, ["period_ms"] = 970, ["latest_down_ms"] = 4000 },
        };
        PipelineClickRequest Parse(Dictionary<string, object?> body)
        {
            using var document = JsonDocument.Parse(JsonSerializer.Serialize(body));
            return PipelineInputPolicy.Parse(document.RootElement, token, table);
        }
        void Reject(Dictionary<string, object?> body)
        {
            var rejected = false;
            try { Parse(body); } catch (Exception) { rejected = true; }
            check(rejected);
        }
        check(Parse(Body()).AppealTiming == plan);
        foreach (var wrong in new object?[] { null, new[] { 100, 970, 4000 }, "timing" })
        { var body = Body(); body["appeal_timing"] = wrong; Reject(body); }
        foreach (var value in new object?[] { null, "100", true, -1, 1e300 })
        { var body = Body(); ((Dictionary<string, object?>)body["appeal_timing"]!)["target_down_ms"] = value; Reject(body); }
        { var body = Body(); ((Dictionary<string, object?>)body["appeal_timing"]!)["extra"] = 1; Reject(body); }
        { var body = Body(); body["action"] = "home-mission"; body["node_name"] = "SC.Missions.Open"; Reject(body); }
        { var body = Body(); body["action"] = AppealTiming.GradeAction; body["node_name"] = AppealTiming.GradeNode; check(Parse(body).AppealTiming == plan); }
        { var body = Body(); body["action"] = AppealTiming.GradeAction; Reject(body); }
        { var body = Body(); body.Remove("node_name"); Reject(body); }
        { var body = Body(); body.Remove("appeal_timing"); check(Parse(body).AppealTiming is null); }

        foreach (var fault in new[] { "none", "cooldown", "late-before-deadline", "capture", "host", "cancel-wait", "deadline", "down-throws", "cancel-hold", "hold-throws" })
        {
            var mono = 0.0; var events = new List<string>(); var consumed = false; var bound = true; var host = true;
            using var cancellation = new CancellationTokenSource();
            var gate = new PipelineInputGate(new byte[32], Path.Combine(root, fault), () => DateTimeOffset.UtcNow,
                () => mono, new PipelineLimits(IntervalJitterMaxMs: 0), table, _ => 0,
                (ms, _) => { mono += ms; events.Add("interval"); return Task.CompletedTask; });
            PipelineClickRequest Request(string action, AppealTiming? timing = null) => new(
                new(action, new string('a', 64), 1280, 720, Guid.NewGuid().ToString("N"), 1, 2), null,
                action == AppealTiming.Action ? AppealTiming.Node : "SC.Missions.Open", AppealTiming: timing);
            if (fault == "cooldown") { check(gate.Admit(Request("home-mission")) is null); }
            var request = Request(AppealTiming.Action, new(100, 1000, 1800));
            var timingWaits = 0;
            Task Hold(int ms)
            {
                mono += ms; events.Add("delay:" + ms);
                if (ms == 120 && fault == "cancel-hold") { cancellation.Cancel(); }
                if (ms == 120 && fault == "hold-throws") { throw new OperationCanceledException(); }
                return Task.CompletedTask;
            }
            Task Wait(double ms, CancellationToken token)
            {
                mono += ms; events.Add("timing-wait");
                timingWaits++;
                if (fault == "late-before-deadline" && timingWaits == 1) { mono += 150; }
                if (fault == "capture") { bound = false; }
                if (fault == "host") { host = false; }
                if (fault == "deadline") { mono += 2000; }
                if (fault == "cancel-wait") { cancellation.Cancel(); token.ThrowIfCancellationRequested(); }
                return Task.CompletedTask;
            }
            var result = await gate.Execute(request, () => host ? null : "host_lost", _ => bound,
                (admitted, _, guard) => PipelineGesture.Click(guard, () => { consumed = true; bound = false; events.Add("consume"); },
                    (kind, _) =>
                    {
                        events.Add(kind.ToString());
                        if (kind == CoreWebView2MouseEventKind.LeftButtonDown && fault == "down-throws") { throw new InvalidOperationException("down"); }
                    }, Hold, admitted.AppealTiming, admitted.TimingObservation, gate.RecordTimedDown, cancellation.Token, () => mono, Wait), cancellation.Token);
            var timing = request.TimingObservation!;
            var didDown = events.Contains("LeftButtonDown");
            var expectedDown = fault is "none" or "cooldown" or "late-before-deadline" or "down-throws" or "cancel-hold" or "hold-throws";
            check(didDown == expectedDown && consumed == expectedDown);
            check(events.Count(e => e == "LeftButtonDown") == (expectedDown ? 1 : 0));
            check(events.Count(e => e == "LeftButtonUp") == (expectedDown ? 1 : 0));
            check(gate.RunCount == (fault == "cooldown" ? 2 : 1));
            check(timing.selected_down_ms == (fault is "cooldown" or "late-before-deadline" ? 1100 : 100));
            if (fault == "late-before-deadline") { check(timingWaits == 2 && timing.rollovers == 1 && timing.actual_down_ms == 1100); }
            if (expectedDown)
            {
                check(timing.actual_down_ms == timing.selected_down_ms && timing.lateness_ms == 0);
                check(timing.actual_up_ms == timing.actual_down_ms + (fault == "down-throws" ? 0 : 120));
                check(gate.WaitMs() == (fault == "down-throws" ? 800 : 680));
                var countBefore = gate.RunCount;
                check(gate.Admit(request) == "nonce_replayed" && gate.RunCount == countBefore);
                check(gate.Admit(Request("home-mission")) == "rate_limited_min_interval");
                mono += gate.WaitMs();
                check(gate.Admit(Request("home-mission")) is null);
            }
            else { check(timing.actual_down_ms is null && timing.actual_up_ms is null && !result.ok); }
            var audit = File.ReadAllText(gate.AuditPath(gate.GameDay()));
            check(audit.Contains("\"appeal_timing\"") && audit.Contains("unverified_down_or_up"));
            if (fault is "none" or "cooldown" or "late-before-deadline" or "cancel-hold") { check(result.ok); }
            if (fault is "down-throws" or "hold-throws") { check(!result.ok); }
        }
        // Real-clock wait used by the host: never early, cancellable during the sleep and the final spin.
        foreach (var ms in new[] { -5.0, 0.0, 3.0, 25.0, 45.0 })
        {
            var start = HostMonotonic.NowMs();
            await PipelineGesture.PreciseDelay(ms, CancellationToken.None);
            check(HostMonotonic.NowMs() - start >= Math.Max(0, ms));
        }
        foreach (var ms in new[] { 10.0, 60.0 })
        {
            using var cancelled = new CancellationTokenSource();
            cancelled.Cancel();
            var threw = false;
            try { await PipelineGesture.PreciseDelay(ms, cancelled.Token); } catch (OperationCanceledException) { threw = true; }
            check(threw);
        }
        // Existing ordinary click, Wheel, frame-binding and grant fault cases must remain unchanged.
        await PipelineFrameSelfTest.Run(root, check);
    }
}

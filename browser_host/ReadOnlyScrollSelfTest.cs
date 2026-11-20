using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline wheel/ledger/endpoint checks with fake input only.</summary>
internal static class ReadOnlyScrollSelfTest
{
    internal static async Task Run(string root, Action<bool> check)
    {
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily" }).EnableSupervisedScroll);
        var enabled = BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-scroll" });
        check(enabled.EnableSupervisedScroll && !enabled.EnableSupervisedTraining);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-training", "--enable-supervised-scroll" }).EnableSupervisedScroll);
        foreach (var args in new[] {
            new[] { "--enable-supervised-scroll" }, new[] { "--composition-game", "--enable-supervised-scroll" },
            new[] { "--composition-demo", "--enable-supervised-daily", "--enable-supervised-scroll" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-scroll", "--enable-supervised-navigation", "announcement-back" } })
        {
            var rejected = false;
            try { _ = BrowserHostOptions.Parse(args); } catch (ArgumentException) { rejected = true; }
            check(rejected);
        }
        foreach (var action in DailyNavigationPolicy.ScrollActions)
        {
            check(ReadOnlyScrollBudget.DefaultPath(action).EndsWith(System.IO.Path.Combine("readonly_scroll", action + ".json")));
            var point = action.StartsWith("mission-", StringComparison.Ordinal) ? new Point(1000, 430)
                : action.StartsWith("match-area-", StringComparison.Ordinal) ? new Point(260, 460) : new Point(1030, 410);
            check(DailyNavigationPolicy.FixedPoint(action) == point);
            var delta = action.EndsWith("-down", StringComparison.Ordinal) ? -240 : 240;
            check(DailyNavigationPolicy.FixedWheelDelta(action) == delta);
            var events = new List<(CoreWebView2MouseEventKind Kind, CoreWebView2MouseEventVirtualKeys Keys, uint Data, Point Point)>();
            NativeScroll.Send(action, point, (kind, keys, data, target) => events.Add((kind, keys, data, target)), NativeScroll.CalibratedLines);
            check(events.SequenceEqual(new[] {
                (CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventVirtualKeys.None, 0u, point),
                (CoreWebView2MouseEventKind.Wheel, CoreWebView2MouseEventVirtualKeys.None, unchecked((uint)delta), point) }));
            var clickBudgetRejected = false;
            try { _ = NavigationBudget.DefaultPath(action); } catch (InvalidOperationException) { clickBudgetRejected = true; }
            check(clickBudgetRejected);
        }
        check(NativeScroll.Normalize(-240, 3) == -240 && NativeScroll.Normalize(-240, 6) == -120 && NativeScroll.Normalize(120, 9) == 40);
        check(NativeScroll.Normalize(60, 1) == 180 && NativeScroll.Normalize(60, 500) == 1 && NativeScroll.Normalize(-60, 500) == -1);
        check(NativeScroll.Normalize(120, 0) is null && NativeScroll.Normalize(120, uint.MaxValue) is null);
        var scaled = new List<uint>();
        NativeScroll.SendDelta(-240, new(1000, 430), (kind, _, data, _) => { if (kind == CoreWebView2MouseEventKind.Wheel) { scaled.Add(data); } }, 12);
        check(scaled.SequenceEqual(new[] { unchecked((uint)-60) }));
        var pageRefused = new List<CoreWebView2MouseEventKind>();
        try { NativeScroll.SendDelta(-240, new(1000, 430), (kind, _, _, _) => pageRefused.Add(kind), uint.MaxValue); check(false); }
        catch (InvalidOperationException) { check(pageRefused.Count == 0); }
        var failedEvents = new List<CoreWebView2MouseEventKind>();
        var failedMove = false;
        try { NativeScroll.Send("mission-scroll-down", new(1000, 430), (kind, _, _, _) => { failedEvents.Add(kind); throw new InvalidOperationException("move-failed"); }, NativeScroll.CalibratedLines); }
        catch (InvalidOperationException) { failedMove = true; }
        check(failedMove && failedEvents.SequenceEqual(new[] { CoreWebView2MouseEventKind.Move }));
        failedEvents.Clear();
        var failedWheel = false;
        try { NativeScroll.Send("mission-scroll-down", new(1000, 430), (kind, _, _, _) => { failedEvents.Add(kind); if (kind == CoreWebView2MouseEventKind.Wheel) { throw new InvalidOperationException("wheel-failed"); } }, NativeScroll.CalibratedLines); }
        catch (InvalidOperationException) { failedWheel = true; }
        check(failedWheel && failedEvents.Count == 2);

        var ledgerPath = System.IO.Path.Combine(root, "scroll-budget.json");
        const string ledgerAction = "mission-scroll-down";
        var ledger = new ReadOnlyScrollBudget(ledgerPath, ledgerAction);
        check(ledger.Consume(ledgerAction, 1.ToString("x64"), 1.ToString("x32")));
        var unchanged = File.ReadAllText(ledgerPath);
        check(!new ReadOnlyScrollBudget(ledgerPath, ledgerAction).Consume(ledgerAction, 2.ToString("x64"), 1.ToString("x32")));
        check(!ledger.Consume(ledgerAction, 1.ToString("x64"), 2.ToString("x32")));
        check(File.ReadAllText(ledgerPath) == unchanged);
        for (var i = 2; i <= 12; i++)
        { check(new ReadOnlyScrollBudget(ledgerPath, ledgerAction).Consume(ledgerAction, i.ToString("x64"), i.ToString("x32"))); }
        check(!new ReadOnlyScrollBudget(ledgerPath, ledgerAction).Consume(ledgerAction, 13.ToString("x64"), 13.ToString("x32")));
        using (var completed = JsonDocument.Parse(File.ReadAllText(ledgerPath)))
        { check(completed.RootElement.GetProperty("Attempts").GetInt32() == 12 && completed.RootElement.GetProperty("History").GetArrayLength() == 12); }
        foreach (var content in new[] { "", "{", "{}", unchanged.Replace("\"MaxAttempts\":12", "\"MaxAttempts\":13"), unchanged.Replace("\"Attempts\":1", "\"Attempts\":0") })
        {
            var corrupt = System.IO.Path.Combine(root, "scroll-corrupt-" + Guid.NewGuid().ToString("N") + ".json");
            File.WriteAllText(corrupt, content);
            var rejected = false;
            try { _ = new ReadOnlyScrollBudget(corrupt, ledgerAction); } catch (Exception) { rejected = true; }
            check(rejected && File.ReadAllText(corrupt) == content);
        }
        var unchangedBudget = new NavigationBudget(System.IO.Path.Combine(root, "scroll-independent-click.json"), "home-mission");
        check(unchangedBudget.Consume("home-mission", new string('a', 64)));
        check(!unchangedBudget.Consume("home-mission", new string('b', 64)));

        foreach (var training in new[] { false, true })
        foreach (var scroll in new[] { false, true })
        {
            var sessionPath = System.IO.Path.Combine(root, $"scroll-session-{training}-{scroll}.json");
            var calls = 0;
            string? currentFrame = 1.ToString("x64");
            var age = 10.0;
            var width = 1280;
            using (var bridge = new SupervisedDailyBridge(sessionPath, (request, _) =>
            {
                calls++;
                if (!DailyNavigationPolicy.FrameMatches(request, currentFrame, width, 720, age))
                { return Task.FromResult(new NavigationResult(false, false, "offline_frame_guard_failed")); }
                var budget = new ReadOnlyScrollBudget(System.IO.Path.Combine(root, $"endpoint-{training}-{scroll}-{request.Action}.json"), request.Action);
                var accepted = budget.Consume(request.Action, request.FrameSha256, request.Nonce);
                if (accepted) { currentFrame = null; }
                return Task.FromResult(new NavigationResult(accepted, accepted, "offline_fake_wheel_only"));
            }, training, scroll))
            {
                using var session = JsonDocument.Parse(File.ReadAllText(sessionPath));
                var document = session.RootElement;
                check(document.GetProperty("actions").EnumerateArray().Select(item => item.GetString()).SequenceEqual(DailyNavigationPolicy.AllowedActions(training, scroll)));
                var paths = document.GetProperty("budget_paths");
                check(paths.EnumerateObject().Count() == DailyNavigationPolicy.AllowedActions(training, scroll).Length);
                foreach (var action in DailyNavigationPolicy.ScrollActions)
                {
                    check(paths.TryGetProperty(action, out var path) == scroll);
                    if (scroll) { check(path.GetString() == ReadOnlyScrollBudget.DefaultPath(action)); }
                }
                var payload = new Dictionary<string, object> {
                    ["token"] = document.GetProperty("token").GetString()!, ["command"] = "daily-navigate", ["action"] = ledgerAction,
                    ["frame_sha256"] = 1.ToString("x64"), ["source_width"] = 1280, ["source_height"] = 720, ["nonce"] = 1.ToString("x32") };
                async Task<NavigationResult> Send(Dictionary<string, object> data)
                {
                    using var client = new TcpClient(); await client.ConnectAsync("127.0.0.1", document.GetProperty("port").GetInt32());
                    using var stream = client.GetStream(); await stream.WriteAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(data) + "\n"));
                    using var reader = new StreamReader(stream);
                    return JsonSerializer.Deserialize<NavigationResult>((await reader.ReadLineAsync())!)!;
                }
                foreach (var change in new (string Key, object Value)[] { ("coordinates", new[] { 1000, 430 }), ("delta", -240), ("source_width", 1920), ("nonce", "bad"), ("action", "scroll-anywhere") })
                {
                    var result = await Send(new(payload) { [change.Key] = change.Value });
                    check(!result.ok && !result.attempted && calls == 0);
                }
                if (!scroll)
                {
                    var rejected = await Send(payload); check(!rejected.ok && !rejected.attempted && calls == 0);
                }
                else
                {
                    age = 5000.1;
                    var stale = await Send(payload); check(!stale.ok && !stale.attempted);
                    age = 10; width = 1282;
                    var geometry = await Send(payload); check(!geometry.ok && !geometry.attempted);
                    width = 1280;
                    var success = await Send(payload); check(success.ok && success.attempted);
                    var reusedFrame = await Send(payload); check(!reusedFrame.ok && !reusedFrame.attempted);
                    currentFrame = 2.ToString("x64"); payload["frame_sha256"] = currentFrame;
                    var replayedNonce = await Send(payload); check(!replayedNonce.ok && !replayedNonce.attempted);
                    payload["nonce"] = 2.ToString("x32");
                    var nextFrame = await Send(payload); check(nextFrame.ok && nextFrame.attempted);
                }
            }
            check(!File.Exists(sessionPath));
        }
    }
}

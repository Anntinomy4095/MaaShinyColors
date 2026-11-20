using System.Net.Sockets;
using Microsoft.Web.WebView2.Core;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline checks of the pipeline transport; never creates a browser or sends input.</summary>
internal static class PipelineInputSelfTest
{
    internal static async Task RunStandalone()
    {
        var root = System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "pipeline_input_selftests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root); var count = 0;
        await Run(root, valid => { if (!valid) { throw new InvalidOperationException($"Pipeline 输入离线自检失败（第 {count + 1} 项）。"); } count++; });
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            passed = true, checks = count, real_input_attempts = 0, evidence_directory = root,
            action_table_sha256 = PipelineActionTable.Default.Sha256, actions = PipelineActionTable.Default.Ids,
        }));
    }

    private static string Hex(int bytes) => Convert.ToHexString(RandomNumberGenerator.GetBytes(bytes)).ToLowerInvariant();

    internal static async Task RunTargetChecks(string parent, Action<bool> check)
    {
        var table = PipelineActionTable.Default;
        const string token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        Dictionary<string, object?> Body(string action, object? target) => new()
        {
            ["token"] = token, ["command"] = "pipeline-click", ["action"] = action,
            ["node_name"] = table.Get(action).Nodes![0], ["frame_sha256"] = new string('a', 64),
            ["source_width"] = 1280, ["source_height"] = 720, ["nonce"] = new string('b', 32),
            ["protocol_version"] = 2, ["capture_seq"] = 17L, ["frame_binding_seq"] = 29L, ["target"] = target,
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
        for (var slot = 1; slot <= 6; slot++)
        {
            var action = $"match-skip-plus-{slot}";
            var rect = table.Get(action).Rect;
            check(Parse(Body(action, new[] { rect.X, rect.Y })).Target == new Point(rect.X, rect.Y));
            Reject(Body(action, new[] { rect.Right, rect.Bottom - 1 }));
        }
        foreach (var target in new object?[] { null, new[] { 585 }, new[] { 585, 515, 1 }, new object[] { "585", 515 }, new[] { 585.5, 515.0 } })
        { Reject(Body("match-skip-plus-5", target)); }
        var wrongNode = Body("match-skip-plus-5", new[] { 585, 515 });
        wrongNode["node_name"] = "SC.Match.Skip.Plus3"; Reject(wrongNode);
        Reject(Body("home-mission", new[] { 585, 515 }));
        Reject(Body("match-skip-scroll-down", new[] { 585, 515 }));
        Reject(Body("match-skip-confirm", new[] { 585, 515 }));
        var request = Parse(Body("match-skip-plus-5", new[] { 585, 515 }));
        var gate = new PipelineInputGate(new byte[32], System.IO.Path.Combine(parent, "target", "run_logs"),
            () => DateTimeOffset.UtcNow, () => 0, new PipelineLimits(), table, _ => 0, (_, _) => Task.CompletedTask);
        check(gate.Admit(request with { Target = new Point(0, 0) }) == "pipeline_target_out_of_bounds");
        var sent = new List<Point>();
        var result = await gate.Execute(request, () => null, _ => true, (_, point, guard) =>
        {
            check(guard() is null); sent.Add(point);
            return Task.FromResult(new NavigationResult(true, true, "simulated"));
        }, CancellationToken.None);
        check(result.ok && result.attempted && sent.SequenceEqual(new[] { new Point(585, 515) }));
        // Hold v2: only the Match ＋ and Produce 「諦める」 (wing-giveup-hold) take hold_ms, within 300–4000 ms.
        Dictionary<string, object?> Hold(string action, int ms)
        {
            var body = Body(action, null);
            body.Remove("target");
            body["hold_ms"] = ms;
            return body;
        }
        check(PipelineInputPolicy.HoldProtocol == "SC.PipelineHold.v2" && Parse(Hold("wing-giveup-hold", 2000)).HoldMs == 2000);
        Reject(Hold("wing-giveup-hold", 299));
        Reject(Hold("wing-giveup-hold", 4001));
        Reject(Hold("home-mission", 2000));
        Reject(Hold("popup-help-close", 2000));
    }

    /// <summary>SC.PipelineDrag.v1: table field, request parsing, gate admission and the gesture order.</summary>
    internal static async Task RunDragChecks(string parent, Action<bool> check)
    {
        const string token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        var table = PipelineActionTable.Parse(Encoding.UTF8.GetBytes("""
            {"version": 2, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["t"],
             "actions": [
              {"id": "drag-a", "family": "t", "consuming": false, "rect": [1000, 90, 270, 480], "source": "test",
               "nodes": ["SC.Test.DragA"], "drag_rect": [1000, 175, 270, 70]},
              {"id": "nav-a", "family": "t", "consuming": false, "rect": [0, 0, 100, 100], "source": "test", "nodes": ["SC.Test.NavA"]}]}
            """));
        check(table.DragOf("drag-a") == new Rectangle(1000, 175, 270, 70) && table.DragOf("nav-a") is null);
        foreach (var bad in new[]
        {
            """{"id": "x", "family": "t", "consuming": true, "rect": [0, 0, 10, 10], "source": "s", "nodes": ["SC.X"], "drag_rect": [0, 0, 5, 5]}""",
            """{"id": "x", "family": "t", "consuming": false, "rect": [0, 0, 10, 10], "source": "s", "nodes": ["SC.X"], "drag_rect": [0, 0, 5, 5], "wheel": 120}""",
            """{"id": "x", "family": "t", "consuming": false, "rect": [0, 0, 10, 10], "source": "s", "nodes": ["SC.X"], "drag_rect": [1200, 0, 100, 5]}""",
            """{"id": "x", "family": "t", "consuming": false, "rect": [0, 0, 10, 10], "source": "s", "nodes": ["SC.X"], "drag_rect": [0, 0, 5]}""",
        })
        {
            var rejected = false;
            try { PipelineActionTable.Parse(Encoding.UTF8.GetBytes("""{"version": 2, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["t"], "actions": [""" + bad + "]}")); }
            catch (Exception) { rejected = true; }
            check(rejected);
        }
        Dictionary<string, object?> Body(string action, object? target, object? to, object? ms)
        {
            var body = new Dictionary<string, object?>
            {
                ["token"] = token, ["command"] = "pipeline-click", ["action"] = action,
                ["node_name"] = table.Get(action).Nodes![0], ["frame_sha256"] = new string('a', 64),
                ["source_width"] = 1280, ["source_height"] = 720, ["nonce"] = Hex(16),
                ["protocol_version"] = 2, ["capture_seq"] = 17L, ["frame_binding_seq"] = 29L,
            };
            if (target is not null) { body["target"] = target; }
            if (to is not null) { body["drag_to"] = to; }
            if (ms is not null) { body["drag_ms"] = ms; }
            return body;
        }
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
        var ok = Parse(Body("drag-a", new[] { 1100, 530 }, new[] { 1100, 210 }, 400));
        check(PipelineInputPolicy.DragProtocol == "SC.PipelineDrag.v1" && ok.Target == new Point(1100, 530) &&
              ok.DragTo == new Point(1100, 210) && ok.DragMs == 400);
        Reject(Body("drag-a", null, new[] { 1100, 210 }, 400));                 // start required
        Reject(Body("drag-a", new[] { 1100, 530 }, null, null));                  // a drag action is never a plain click
        Reject(Body("drag-a", new[] { 1100, 530 }, new[] { 1100, 210 }, null));   // duration required
        Reject(Body("drag-a", new[] { 1100, 530 }, new[] { 1100, 300 }, 400));   // end outside drag_rect
        Reject(Body("drag-a", new[] { 900, 530 }, new[] { 1100, 210 }, 400));    // start outside the action rect
        Reject(Body("drag-a", new[] { 1100, 530 }, new[] { 1100, 210 }, 149));
        Reject(Body("drag-a", new[] { 1100, 530 }, new[] { 1100, 210 }, 3001));
        Reject(Body("drag-a", new[] { 1100, 530 }, new[] { 1100.5, 210 }, 400));
        Reject(Body("nav-a", null, new[] { 50, 50 }, 400));                      // no drag_rect, no drag
        var held = Body("drag-a", new[] { 1100, 530 }, new[] { 1100, 210 }, 400); held["hold_ms"] = 500; Reject(held);
        var gate = new PipelineInputGate(new byte[32], System.IO.Path.Combine(parent, "drag", "run_logs"),
            () => DateTimeOffset.UtcNow, () => 0, new PipelineLimits(), table, _ => 0, (_, _) => Task.CompletedTask);
        check(gate.Admit(ok with { DragTo = null, DragMs = null }) == "pipeline_drag_required");
        check(gate.Admit(ok with { DragTo = new Point(1100, 300) }) == "pipeline_drag_end_out_of_bounds");
        Point? start = null;
        var result = await gate.Execute(ok, () => null, _ => true, (admitted, point, guard) =>
        {
            check(guard() is null && admitted.DragTo == new Point(1100, 210)); start = point;
            return Task.FromResult(new NavigationResult(true, true, "simulated"));
        }, CancellationToken.None);
        check(result.ok && start == new Point(1100, 530));
        // Gesture order: hover, guard, Down at the start, button-held moves ending exactly at the end, Up at the end.
        var events = new List<(CoreWebView2MouseEventKind kind, CoreWebView2MouseEventVirtualKeys keys, Point point)>();
        var consumed = 0;
        var drag = await PipelineGesture.Drag(() => null, () => consumed++, new Point(1100, 530), new Point(1100, 210), 160,
            (kind, keys, point) => events.Add((kind, keys, point)), _ => Task.CompletedTask);
        var downs = events.Where(e => e.kind == CoreWebView2MouseEventKind.LeftButtonDown).ToArray();
        var moves = events.SkipWhile(e => e.kind != CoreWebView2MouseEventKind.LeftButtonDown).Skip(1)
            .TakeWhile(e => e.kind == CoreWebView2MouseEventKind.Move).ToArray();
        check(drag.ok && consumed == 1 && downs.Length == 1 && downs[0].point == new Point(1100, 530) &&
              moves.Length == 10 && moves.All(e => e.keys == CoreWebView2MouseEventVirtualKeys.LeftButton) &&
              moves[^1].point == new Point(1100, 210) &&
              events[^1] == (CoreWebView2MouseEventKind.LeftButtonUp, CoreWebView2MouseEventVirtualKeys.None, new Point(1100, 210)));
        // A guard refusal before Down sends no button event; a failing move still releases the button.
        events.Clear();
        var guards = 0;
        var refused = await PipelineGesture.Drag(() => ++guards >= 3 ? "frame_changed" : null, () => consumed++,
            new Point(1100, 530), new Point(1100, 210), 160, (kind, keys, point) => events.Add((kind, keys, point)), _ => Task.CompletedTask);
        check(!refused.ok && refused.attempted && events.All(e => e.kind == CoreWebView2MouseEventKind.Move) && consumed == 1);
        events.Clear();
        var failing = await PipelineGesture.Drag(() => null, () => { }, new Point(1100, 530), new Point(1100, 210), 160,
            (kind, keys, point) =>
            {
                events.Add((kind, keys, point));
                if (kind == CoreWebView2MouseEventKind.Move && keys == CoreWebView2MouseEventVirtualKeys.LeftButton && events.Count > 6)
                { throw new InvalidOperationException("move failed"); }
            }, _ => Task.CompletedTask);
        check(!failing.ok && failing.attempted && events[^1].kind == CoreWebView2MouseEventKind.LeftButtonUp);
        check(PipelineGesture.DragPath(new Point(0, 0), new Point(10, -10), 0).SequenceEqual(new[] { new Point(10, -10) }));
    }

    // Offline vocabulary for the gate checks: five navigation rects and four consuming rects.
    private const string TestTable = """
        {"version": 1, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["t"],
         "actions": [
          {"id": "nav-a", "family": "t", "consuming": false, "rect": [0, 0, 100, 100], "source": "test", "nodes": []},
          {"id": "nav-b", "family": "t", "consuming": false, "rect": [100, 0, 100, 100], "source": "test", "nodes": []},
          {"id": "nav-c", "family": "t", "consuming": false, "rect": [200, 0, 100, 100], "source": "test", "nodes": []},
          {"id": "nav-d", "family": "t", "consuming": false, "rect": [300, 0, 100, 100], "source": "test", "nodes": []},
          {"id": "nav-e", "family": "t", "consuming": false, "rect": [400, 0, 6, 6], "source": "test", "nodes": []},
          {"id": "spend-a", "family": "t", "consuming": true, "rect": [0, 200, 100, 100], "source": "test", "nodes": []},
          {"id": "spend-b", "family": "t", "consuming": true, "rect": [100, 200, 100, 100], "source": "test", "nodes": []},
          {"id": "spend-c", "family": "t", "consuming": true, "rect": [200, 200, 100, 100], "source": "test", "nodes": []},
          {"id": "spend-d", "family": "t", "consuming": true, "rect": [300, 200, 100, 100], "source": "test", "nodes": []}]}
        """;

    internal static async Task Run(string parent, Action<bool> report)
    {
        void check(bool valid, [System.Runtime.CompilerServices.CallerLineNumber] int line = 0)
        {
            if (!valid) { throw new InvalidOperationException($"Pipeline 输入离线断言失败（源码行 {line}）。"); }
            report(valid);
        }
        await RunTargetChecks(parent, report);
        await RunDragChecks(parent, report);
        void Reject(System.Action action) { var rejected = false; try { action(); } catch { rejected = true; } check(rejected); }

        // Flag dependency: default off, requires the Composition game HTTPS host, independent of the legacy flags.
        check(!BrowserHostOptions.Parse(new[] { "--composition-game" }).EnablePipelineInput);
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-pipeline-input" }).EnableProduceExplore);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-pipeline-input" }).EnablePipelineInput);
        var withLegacy = BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-pipeline-input" });
        check(withLegacy.EnablePipelineInput && withLegacy.EnableSupervisedDaily);
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-pipeline-input" }).EnableSupervisedDaily);
        foreach (var args in new[] {
            new[] { "--enable-pipeline-input" },
            new[] { "--composition-demo", "--enable-pipeline-input" },
            new[] { "--browser-demo", "--enable-pipeline-input" },
            new[] { "--composition-game", "--enable-pipeline-input", "--url", "https://example.com/" },
            new[] { "--composition-game", "--enable-pipeline-input", "--enable-supervised-navigation", "announcement-back" } })
        { Reject(() => BrowserHostOptions.Parse(args)); }

        // Vocabulary read from assets/input/pipeline_actions.json at startup (no longer embedded); B4 adds the マニー shop actions and
        // B5 the training / Fes / Grade / Match actions. The consuming ids are checked explicitly below.
        var table = PipelineActionTable.Default;
        var source = System.IO.Path.Combine(SelectionLoopBudget.Root(), "assets", "input", "pipeline_actions.json");
        check(File.Exists(source) && Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source))).ToLowerInvariant() == table.Sha256);
        // KnowhowScan: fixed tiles, calibrated dynamic slots and five scroll actions; all are non-consuming.
        var knowhow = table.Actions.Where(action => action.Family == "knowhow-scan").ToArray();
        var knowhowExpected = new[] {
            (Id: "idol-list-tab-knowhow", Node: "SC.KnowhowScan.IdolList.OpenKnowhow"),
            (Id: "knowhow-sort-open", Node: "SC.KnowhowScan.List.OpenSort"),
            (Id: "knowhow-sort-key-default", Node: "SC.KnowhowScan.Sort.PickDefault"),
            (Id: "knowhow-sort-key-eval", Node: "SC.KnowhowScan.Sort.PickEval"),
            (Id: "knowhow-sort-key-date", Node: "SC.KnowhowScan.Sort.PickDate"),
            (Id: "knowhow-sort-apply", Node: "SC.KnowhowScan.Sort.Apply"),
            (Id: "knowhow-grid-scroll-down", Node: "SC.KnowhowScan.Grid.ScrollDown"),
            (Id: "knowhow-grid-scroll-up", Node: "SC.KnowhowScan.Grid.ScrollUp"),
            (Id: "knowhow-detail-scroll-down", Node: "SC.KnowhowScan.Detail.ScrollDown") }
            .Concat(from row in Enumerable.Range(0, 3) from column in Enumerable.Range(0, 5)
                select (Id: $"knowhow-tile-r{row}c{column}", Node: $"SC.KnowhowScan.Grid.OpenTile.R{row}C{column}"))
            .Concat(from row in Enumerable.Range(0, 6) from column in Enumerable.Range(0, 5)
                select (Id: $"knowhow-slot-y{row}c{column}", Node: $"SC.KnowhowScan.Grid.OpenSlot.Y{row}C{column}"))
            .Concat(new[] {
                (Id: "knowhow-filter-open", Node: "SC.KnowhowScan.Filter.Open"),
                (Id: "knowhow-filter-scroll-down", Node: "SC.KnowhowScan.Filter.ScrollDown"),
                (Id: "knowhow-filter-scroll-up", Node: "SC.KnowhowScan.Filter.ScrollUp"),
                (Id: "knowhow-filter-cancel", Node: "SC.KnowhowScan.Filter.Cancel"),
                // Jump probe (calibration mode only, 2026-10-25): -240/+240 notches and the scrollbar track taps.
                (Id: "knowhow-grid-jump-down", Node: "SC.KnowhowScan.Grid.JumpDown"),
                (Id: "knowhow-grid-jump-up", Node: "SC.KnowhowScan.Grid.JumpUp"),
                (Id: "knowhow-grid-track-upper", Node: "SC.KnowhowScan.Grid.TrackUpper"),
                (Id: "knowhow-grid-track-lower", Node: "SC.KnowhowScan.Grid.TrackLower"),
                // Keep step: the detail panel's 保護, admitted only for a run with locking on.
                (Id: "knowhow-detail-protect", Node: "SC.KnowhowScan.Keep.Protect") }).ToArray();
        check(knowhow.Length == 63 && knowhow.Select(action => action.Id).SequenceEqual(knowhowExpected.Select(action => action.Id)));
        foreach (var expected in knowhowExpected)
        {
            var action = table.Get(expected.Id);
            var wheel = action.Id.Contains("scroll", StringComparison.Ordinal) || action.Id.Contains("jump", StringComparison.Ordinal);
            check(action.Enabled && !action.Consuming && action.Nodes?.SequenceEqual(new[] { expected.Node }) == true &&
                (action.Wheel is null) == !wheel);
        }
        check(table.Actions.Where(action => !action.Id.StartsWith("wing-", StringComparison.Ordinal) && action.Family != "knowhow-scan")
            .Select(action => action.Id).SequenceEqual(new[] { "game-start", "startup-collabo-report-ok", "popup-reward-ok", "popup-event-fan-reward-ok", "popup-event-fan-receipt-ok", "popup-fes-period-reward-ok", "popup-mission-completed-ok", "popup-help-close",
            "popup-pickup-close", "popup-story-skip", "popup-new-gacha-skip", "popup-gacha-announcement-close", "popup-match-season-close", "popup-scenario-play-cancel", "nav-back", "home-mission", "home-shop", "shop-money-tab",
            "shop-offer-open-1", "shop-offer-open-2", "shop-offer-open-3", "shop-exchange-cancel", "shop-exchange-confirm",
            "shop-result-close", "home-idol", "idol-training", "training-select-card", "training-bronze-plus", "training-bronze-plus-top", "training-submit",
            "training-confirm-cancel", "training-start", "training-result-close", "home-fes", "fes-match-entry", "fes-grade-entry",
            "grade-join-open", "grade-mode-grade", "grade-mode-rehearsal", "grade-rehearsal-easy",
            "grade-rehearsal-normal", "grade-rehearsal-hard", "grade-difficulty-easy", "grade-difficulty-normal", "grade-difficulty-hard",
            "grade-tension-confirm", "grade-fes-resume", "match-latest-area-up", "match-latest-area-down", "match-latest-area-open", "match-latest-back", "match-skip-open", "match-skip-cancel", "match-skip-plus-1", "match-skip-plus-2",
            "match-skip-plus-3", "match-skip-plus-4", "match-skip-plus-5", "match-skip-plus-6", "match-skip-clear",
            "match-skip-confirm", "match-skip-review", "match-skip-review-cancel", "match-skip-result-close", "match-skip-scroll-down", "match-skip-scroll-up",
            // 2026-10-31 Match 推进 (match_play): node-bound; ライブ開始 consumes one フェス参加権.
            "match-play-area-next", "match-play-area-open", "match-play-stage-decide", "match-play-back", "match-play-unit-open",
            "match-play-unit-1", "match-play-unit-2", "match-play-unit-3", "match-play-unit-4", "match-play-unit-5",
            "match-play-unit-6", "match-play-unit-7", "match-play-unit-8", "match-play-unit-9", "match-play-unit-10",
            "match-play-omakase", "match-play-live-start", "match-play-touch", "match-play-skip", "match-play-next-stage",
            "match-play-area-select", "match-play-lose-close", "live-card-1", "live-card-2", "live-card-3",
            "live-memory-appeal", "live-memory-film", "live-judge-vo", "live-judge-da", "live-judge-vi", "live-appeal-timing", "live-speed-toggle",
            "live-report-ok", "live-record-dismiss", "home-business", "business-slot-complete-1", "business-slot-complete-2", "business-slot-complete-3",
            "business-report-next", "business-slot-open-1", "business-slot-open-2", "business-slot-open-3",
            "business-bulk-complete", "business-bulk-dispatch", "business-report-next-page", "business-reward-redispatch",
            "business-dispatch-decide" }.Concat(
                from row in Enumerable.Range(1, 3) from column in Enumerable.Range(1, 7)
                where row != 1 || column != 2 select $"training-list-slot-r{row}c{column}")
            // 2026-11-22: grids shifted +51 px (3 rows) and +103 px (2 rows) for a list resting between rows
            .Concat(from row in Enumerable.Range(1, 3) from column in Enumerable.Range(1, 7)
                select $"training-list-slot-b-r{row}c{column}")
            .Concat(from row in Enumerable.Range(1, 2) from column in Enumerable.Range(1, 7)
                select $"training-list-slot-c-r{row}c{column}")
            .Concat(new[] { "training-list-scroll-down", "training-list-scroll-up", "training-list-direction-toggle", "training-list-sort-open" })
            .Concat(from row in Enumerable.Range(1, 3) from column in Enumerable.Range(1, 3)
                select $"training-sort-key-r{row}c{column}")
            .Concat(new[] { "training-sort-apply", "idol-portal-list",
                "idol-list-tab-produce", "idol-list-tab-support", "idol-list-scroll-down", "idol-list-scroll-up" })
            // Present claim catalog: non-consuming; list scrolling remains disabled.
            .Concat(new[] { "home-present", "present-unclaimed-tab", "present-bulk-receive", "present-confirm", "present-result-close", "present-list-scroll-down" })
            .Concat(new[] { "mission-login-claim", "mission-result-close", "mission-list-top", "mission-list-bottom",
                "mission-tab-weekly", "mission-tab-daily", "mission-bulk-receive", "mission-bulk-confirm", "mission-bulk-result-close" })
            // 2026-10-22 weekly official Auto Produce: node-bound (2026-10-25: navigation enabled).
            .Concat(new[] { "weekly-auto-home-produce", "weekly-auto-home-running", "weekly-auto-end-ok", "weekly-auto-result-ok",
                "weekly-auto-running-cancel", "weekly-auto-entry", "weekly-auto-count-plus", "weekly-auto-count-minus",
                "weekly-auto-decide", "weekly-auto-dialog-cancel", "weekly-auto-skip", "weekly-auto-start" })
            // 2026-10-30: Maa sets an empty オート1 (wizard, 育成方針).
            .Concat(new[] { "weekly-auto-row-set", "weekly-auto-setup-tab-train", "weekly-auto-setup-tab-special",
                "weekly-auto-setup-scenario-next", "weekly-auto-setup-unit-1", "weekly-auto-setup-unit-2",
                "weekly-auto-setup-unit-3", "weekly-auto-setup-unit-4", "weekly-auto-setup-unit-5",
                "weekly-auto-setup-unit-6", "weekly-auto-setup-unit-7", "weekly-auto-setup-unit-8",
                "weekly-auto-setup-unit-9", "weekly-auto-setup-unit-10", "weekly-auto-setup-unit-next",
                "weekly-auto-setup-book-history", "weekly-auto-setup-history-call",
                "weekly-auto-setup-history-back", "weekly-auto-setup-book-omakase", "weekly-auto-setup-book-next",
                "weekly-auto-setup-item-decide", "weekly-auto-setup-scenario-down",
                "weekly-auto-setup-unit-page-prev", "weekly-auto-setup-unit-page-next",
                "weekly-auto-setup-book-preset", "weekly-auto-setup-preset-set-1", "weekly-auto-setup-preset-set-2",
                "weekly-auto-policy-open", "weekly-auto-policy-vocal", "weekly-auto-policy-dance",
                "weekly-auto-policy-visual", "weekly-auto-policy-set", "weekly-auto-policy-cancel", "weekly-auto-loop-off" })
            // 2026-10-30 ループ with リカバリーソーダ (ADR-013; soda-start consumes); 2026-10-31 带书「ノウハウ页指定」 picker.
            .Concat(new[] { "weekly-auto-loop-on", "weekly-auto-soda-30", "weekly-auto-soda-60", "weekly-auto-soda-100",
                "weekly-auto-recovery-cancel", "weekly-auto-soda-start", "weekly-auto-book-slot1", "weekly-auto-picker-decide" })
            .Concat(Enumerable.Range(0, 15).Select(i => $"weekly-auto-picker-tile-r{i / 5}c{i % 5}"))
            // 2026-11-18/19 Collabo Fes: テンション決定 is the one consuming action, panel-drag the one drag.
            .Concat(new[] { "collabo-banner", "collabo-banner-next", "collabo-wrong-event-back", "collabo-event-back",
                "collabo-difficulty-back", "collabo-stage-vocal", "collabo-stage-dance", "collabo-stage-visual", "collabo-stage-center",
                "collabo-difficulty-hard", "collabo-level-plus", "collabo-level-minus", "collabo-level-ok" })
            .Concat(Enumerable.Range(1, 10).Select(n => $"collabo-unit-tab-{n}"))
            .Concat(new[] { "collabo-unit-live", "collabo-tension-decide", "collabo-live-start", "collabo-skill-first",
                "collabo-panel-second", "collabo-report-ok", "collabo-panel-drag", "collabo-unit-back", "collabo-full-close" })));
        var weeklyActions = new[] {
            (Id: "weekly-auto-home-produce", Rect: new Rectangle(1005, 615, 105, 70), Node: "SC.WeeklyProduce.Home.OpenProduce", Consuming: false),
            // 2026-11-01: collect a finished Auto (オートプロデュース終了 / オートリザルト OK), leave a running one (キャンセル).
            (Id: "weekly-auto-home-running", Rect: new Rectangle(1005, 615, 105, 70), Node: "SC.WeeklyProduce.Home.OpenRunning", Consuming: false),
            (Id: "weekly-auto-end-ok", Rect: new Rectangle(575, 440, 130, 50), Node: "SC.WeeklyProduce.End.Ok", Consuming: false),
            (Id: "weekly-auto-result-ok", Rect: new Rectangle(1110, 635, 135, 50), Node: "SC.WeeklyProduce.Result.Ok", Consuming: false),
            (Id: "weekly-auto-running-cancel", Rect: new Rectangle(395, 610, 135, 50), Node: "SC.WeeklyProduce.Running.Cancel", Consuming: false),
            (Id: "weekly-auto-entry", Rect: new Rectangle(140, 632, 140, 58), Node: "SC.WeeklyProduce.Select.OpenAuto", Consuming: false),
            (Id: "weekly-auto-count-plus", Rect: new Rectangle(1174, 210, 30, 30), Node: "SC.WeeklyProduce.Schedule.Plus", Consuming: false),
            (Id: "weekly-auto-count-minus", Rect: new Rectangle(1074, 210, 30, 30), Node: "SC.WeeklyProduce.Schedule.Minus", Consuming: false),
            (Id: "weekly-auto-decide", Rect: new Rectangle(1105, 635, 140, 55), Node: "SC.WeeklyProduce.Schedule.Decide", Consuming: false),
            (Id: "weekly-auto-dialog-cancel", Rect: new Rectangle(392, 512, 140, 58), Node: "SC.WeeklyProduce.Dialog.Cancel", Consuming: false),
            (Id: "weekly-auto-skip", Rect: new Rectangle(567, 508, 145, 28), Node: "SC.WeeklyProduce.Dialog.Skip", Consuming: true),
            (Id: "weekly-auto-start", Rect: new Rectangle(747, 512, 140, 58), Node: "SC.WeeklyProduce.Dialog.Start", Consuming: true),
            // 2026-10-30: Maa sets an empty オート1 (wizard, 育成方針); all non-consuming and enabled.
            (Id: "weekly-auto-row-set", Rect: new Rectangle(1115, 127, 95, 42), Node: "SC.WeeklyProduce.Schedule.RowSet", Consuming: false),
            (Id: "weekly-auto-setup-tab-train", Rect: new Rectangle(845, 85, 150, 32), Node: "SC.WeeklyProduce.Setup.TabTrain", Consuming: false),
            (Id: "weekly-auto-setup-tab-special", Rect: new Rectangle(1015, 85, 155, 32), Node: "SC.WeeklyProduce.Setup.TabSpecial", Consuming: false),
            (Id: "weekly-auto-setup-scenario-next", Rect: new Rectangle(1110, 632, 135, 60), Node: "SC.WeeklyProduce.Setup.ScenarioNext", Consuming: false),
            (Id: "weekly-auto-setup-unit-1", Rect: new Rectangle(28, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab1", Consuming: false),
            (Id: "weekly-auto-setup-unit-2", Rect: new Rectangle(140, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab2", Consuming: false),
            (Id: "weekly-auto-setup-unit-3", Rect: new Rectangle(252, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab3", Consuming: false),
            (Id: "weekly-auto-setup-unit-4", Rect: new Rectangle(364, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab4", Consuming: false),
            (Id: "weekly-auto-setup-unit-5", Rect: new Rectangle(476, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab5", Consuming: false),
            (Id: "weekly-auto-setup-unit-6", Rect: new Rectangle(588, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab6", Consuming: false),
            (Id: "weekly-auto-setup-unit-7", Rect: new Rectangle(700, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab7", Consuming: false),
            (Id: "weekly-auto-setup-unit-8", Rect: new Rectangle(812, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab8", Consuming: false),
            (Id: "weekly-auto-setup-unit-9", Rect: new Rectangle(924, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab9", Consuming: false),
            (Id: "weekly-auto-setup-unit-10", Rect: new Rectangle(1036, 85, 95, 32), Node: "SC.WeeklyProduce.Setup.UnitTab10", Consuming: false),
            (Id: "weekly-auto-setup-unit-next", Rect: new Rectangle(1110, 632, 135, 60), Node: "SC.WeeklyProduce.Setup.UnitNext", Consuming: false),
            (Id: "weekly-auto-setup-book-history", Rect: new Rectangle(718, 636, 162, 42), Node: "SC.WeeklyProduce.Setup.BookHistory", Consuming: false),
            (Id: "weekly-auto-setup-history-call", Rect: new Rectangle(1010, 157, 116, 42), Node: "SC.WeeklyProduce.Setup.HistoryCall", Consuming: false),
            // 2026-11-21: a refused 設定履歴 call or short book slots fall back to おまかせ編成
            (Id: "weekly-auto-setup-history-back", Rect: new Rectangle(19, 625, 77, 76), Node: "SC.WeeklyProduce.Setup.HistoryBack", Consuming: false),
            (Id: "weekly-auto-setup-book-omakase", Rect: new Rectangle(540, 636, 165, 42), Node: "SC.WeeklyProduce.Setup.BookOmakase", Consuming: false),
            (Id: "weekly-auto-setup-book-next", Rect: new Rectangle(1110, 632, 135, 60), Node: "SC.WeeklyProduce.Setup.BookNext", Consuming: false),
            (Id: "weekly-auto-setup-item-decide", Rect: new Rectangle(1110, 632, 135, 60), Node: "SC.WeeklyProduce.Setup.ItemDecide", Consuming: false),
            (Id: "weekly-auto-setup-scenario-down", Rect: new Rectangle(760, 490, 300, 70), Node: "SC.WeeklyProduce.Setup.ScenarioDown", Consuming: false),
            (Id: "weekly-auto-setup-unit-page-prev", Rect: new Rectangle(1178, 85, 36, 40), Node: "SC.WeeklyProduce.Setup.UnitPagePrev", Consuming: false),
            (Id: "weekly-auto-setup-unit-page-next", Rect: new Rectangle(1218, 85, 40, 40), Node: "SC.WeeklyProduce.Setup.UnitPageNext", Consuming: false),
            (Id: "weekly-auto-setup-book-preset", Rect: new Rectangle(30, 162, 145, 30), Node: "SC.WeeklyProduce.Setup.BookPreset", Consuming: false),
            (Id: "weekly-auto-setup-preset-set-1", Rect: new Rectangle(770, 155, 95, 30), Node: "SC.WeeklyProduce.Setup.PresetSet1", Consuming: false),
            (Id: "weekly-auto-setup-preset-set-2", Rect: new Rectangle(770, 340, 95, 30), Node: "SC.WeeklyProduce.Setup.PresetSet2", Consuming: false),
            (Id: "weekly-auto-policy-open", Rect: new Rectangle(740, 208, 112, 34), Node: "SC.WeeklyProduce.Schedule.PolicyOpen", Consuming: false),
            (Id: "weekly-auto-policy-vocal", Rect: new Rectangle(420, 172, 440, 54), Node: "SC.WeeklyProduce.Policy.Vocal", Consuming: false),
            (Id: "weekly-auto-policy-dance", Rect: new Rectangle(420, 262, 440, 54), Node: "SC.WeeklyProduce.Policy.Dance", Consuming: false),
            (Id: "weekly-auto-policy-visual", Rect: new Rectangle(420, 352, 440, 54), Node: "SC.WeeklyProduce.Policy.Visual", Consuming: false),
            (Id: "weekly-auto-policy-set", Rect: new Rectangle(668, 560, 142, 60), Node: "SC.WeeklyProduce.Policy.Set", Consuming: false),
            (Id: "weekly-auto-policy-cancel", Rect: new Rectangle(467, 560, 142, 60), Node: "SC.WeeklyProduce.Policy.Cancel", Consuming: false),
            // 2026-10-30 ループ, リカバリーソーダ (soda-start consumes stamina + sodas, ADR-013); 2026-10-31 book picker.
            (Id: "weekly-auto-loop-off", Rect: new Rectangle(150, 645, 130, 36), Node: "SC.WeeklyProduce.Schedule.LoopOff", Consuming: false),
            (Id: "weekly-auto-loop-on", Rect: new Rectangle(150, 645, 130, 36), Node: "SC.WeeklyProduce.Schedule.LoopOn", Consuming: false),
            (Id: "weekly-auto-soda-30", Rect: new Rectangle(956, 198, 56, 42), Node: "SC.WeeklyProduce.Recovery.Soda30", Consuming: false),
            (Id: "weekly-auto-soda-60", Rect: new Rectangle(956, 331, 56, 42), Node: "SC.WeeklyProduce.Recovery.Soda60", Consuming: false),
            (Id: "weekly-auto-soda-100", Rect: new Rectangle(956, 464, 56, 42), Node: "SC.WeeklyProduce.Recovery.Soda100", Consuming: false),
            (Id: "weekly-auto-recovery-cancel", Rect: new Rectangle(395, 612, 135, 56), Node: "SC.WeeklyProduce.Recovery.Cancel", Consuming: false),
            (Id: "weekly-auto-soda-start", Rect: new Rectangle(750, 610, 135, 58), Node: "SC.WeeklyProduce.Recovery.Start", Consuming: true),
            (Id: "weekly-auto-book-slot1", Rect: new Rectangle(590, 300, 110, 100), Node: "SC.WeeklyProduce.Setup.BookSlot1", Consuming: false),
            (Id: "weekly-auto-picker-decide", Rect: new Rectangle(1100, 615, 120, 50), Node: "SC.WeeklyProduce.Picker.Decide", Consuming: false),
            (Id: "weekly-auto-picker-tile-r0c0", Rect: new Rectangle(550, 215, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile00", Consuming: false),
            (Id: "weekly-auto-picker-tile-r0c1", Rect: new Rectangle(688, 215, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile01", Consuming: false),
            (Id: "weekly-auto-picker-tile-r0c2", Rect: new Rectangle(826, 215, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile02", Consuming: false),
            (Id: "weekly-auto-picker-tile-r0c3", Rect: new Rectangle(964, 215, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile03", Consuming: false),
            (Id: "weekly-auto-picker-tile-r0c4", Rect: new Rectangle(1102, 215, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile04", Consuming: false),
            (Id: "weekly-auto-picker-tile-r1c0", Rect: new Rectangle(550, 353, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile10", Consuming: false),
            (Id: "weekly-auto-picker-tile-r1c1", Rect: new Rectangle(688, 353, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile11", Consuming: false),
            (Id: "weekly-auto-picker-tile-r1c2", Rect: new Rectangle(826, 353, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile12", Consuming: false),
            (Id: "weekly-auto-picker-tile-r1c3", Rect: new Rectangle(964, 353, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile13", Consuming: false),
            (Id: "weekly-auto-picker-tile-r1c4", Rect: new Rectangle(1102, 353, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile14", Consuming: false),
            (Id: "weekly-auto-picker-tile-r2c0", Rect: new Rectangle(550, 491, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile20", Consuming: false),
            (Id: "weekly-auto-picker-tile-r2c1", Rect: new Rectangle(688, 491, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile21", Consuming: false),
            (Id: "weekly-auto-picker-tile-r2c2", Rect: new Rectangle(826, 491, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile22", Consuming: false),
            (Id: "weekly-auto-picker-tile-r2c3", Rect: new Rectangle(964, 491, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile23", Consuming: false),
            (Id: "weekly-auto-picker-tile-r2c4", Rect: new Rectangle(1102, 491, 70, 50), Node: "SC.WeeklyProduce.Picker.Tile24", Consuming: false) };
        check(table.Actions.Where(action => action.Family == "weekly-auto").Select(action => action.Id)
            .SequenceEqual(weeklyActions.Select(action => action.Id)));
        foreach (var expected in weeklyActions)
        {
            var action = table.Get(expected.Id);
            // 2026-10-29: every weekly action but スキップ (cost unverified) is enabled.
            var enabled = expected.Id is not "weekly-auto-skip";
            check(action.Enabled == enabled && action.Rect == expected.Rect && action.Consuming == expected.Consuming && action.Wheel is null &&
                action.Nodes?.SequenceEqual(new[] { expected.Node }) == true &&
                table.NodeRefusal(action.Id, expected.Node) == (enabled ? null : "pipeline_action_disabled"));
        }
        check(table.Version == 2 && table.Actions.Where(action => action.Id.StartsWith("training-list-slot-", StringComparison.Ordinal))
            .All(action => action.Enabled && !action.Consuming));
        // W.I.N.G. catalog matches tools/wing_nav_build.py action_catalog(enabled=True): only verified targets enabled.
        var wing = table.Actions.Where(action => action.Family.StartsWith("wing-", StringComparison.Ordinal)).ToArray();
        // 2026-11-07 H2d: every say "Halo" in-run tap is enabled (目標ライブ cards 1/2 too).
        var wingDisabled = new[] { "wing-reward-item-2", "wing-reward-item-3", "wing-reward-item-4",
            "wing-reward-book-protect", "wing-reward-reroll", "wing-reward-reroll-confirm", "wing-item-use-cancel",
            "wing-replace-dialog-cancel", "wing-replace-ok" }
            .Concat(from row in Enumerable.Range(1, 3) from column in Enumerable.Range(1, 7)
                select $"wing-picker-tile-b-r{row}c{column}")
            // 4f87e88 (2026-10-30 live): picker tiles c (scrolled grid) and the Lv順 sort key r1c2 are verified and enabled.
            .Concat(from row in Enumerable.Range(1, 3) from column in Enumerable.Range(1, 3)
                where (row, column) is not ((1, 2) or (2, 2) or (3, 3)) select $"wing-picker-sort-key-r{row}c{column}")
            .Concat(new[] { "wing-live-memory" }).ToArray();
        check(wing.Count(action => action.Id.StartsWith("wing-skill-node-", StringComparison.Ordinal)
            && action.Family == "wing-skill" && !action.Consuming) == 97);
        // 2026-11-01: + wing-giveup-open / wing-giveup-hold (say "Halo" give-up, family wing-giveup); 2026-11-02 H2b: + 25 wing-halo.
        // 2026-11-04 H2c batch 2: +10 say "Halo" start taps (特化 tab, scenario list, book page); batch 3: +11 成長; batch 4: +1 継承一覧 -> 45.
        // 2026-11-05 H2d: +5 勧誘 candidate tiles -> 50; 2026-11-07: 目標ライブ cards 1/2 enabled + halo-unit-check -> 53, + wing-fes-skill-bulk-cancel.
        // 2026-11-15: 带书「ノウハウ页指定」 (slot 1 + 15 picker tiles + 決定) and the S2 disband (チーム編集, 4 x 離脱させる, confirm) -> 76.
        check(wing.Count(action => action.Family == "wing-halo" && action.Enabled && !action.Consuming) == 76);
        check(wing.Length == 373 && table.Get("wing-giveup-hold").Family == "wing-giveup" && wing.All(action => action.Enabled == !wingDisabled.Contains(action.Id) &&
            action.Id.StartsWith("wing-", StringComparison.Ordinal) && action.Nodes?.Count > 0 &&
            action.Nodes.All(node => node.StartsWith("SC.Wing.Click.", StringComparison.Ordinal))));
        check(wing.Where(action => !action.Enabled).Select(action => action.Id).SequenceEqual(wingDisabled));
        check(wing.Where(action => action.Wheel is not null).Select(action => (action.Id, action.Wheel))
            .SequenceEqual(new (string, int?)[] { ("wing-picker-scroll-down", -60), ("wing-picker-scroll-up", 60),
                ("wing-options-scroll-down", -60) }));
        check(wing.Where(action => action.Consuming).Select(action => action.Id)
            .SequenceEqual(new[] { "wing-reward-reroll-confirm", "wing-produce-start" }) &&
            wing.Where(action => action.Enabled && action.Consuming).Select(action => action.Id)
                .SequenceEqual(new[] { "wing-produce-start" }) &&
            table.Get("wing-produce-start").Family == "wing-start" &&
            table.Get("wing-produce-start").Rect == new Rectangle(668, 468, 144, 62));
        check(table.NodeRefusal("wing-commu-skip", "SC.Wing.Click.CommuSkip.commu_skip") is null);
        check(table.Get("present-bulk-receive").Rect == new Rectangle(1110, 650, 130, 38) &&
            table.Get("present-confirm").Rect == new Rectangle(661, 419, 158, 72) &&
            table.Get("present-result-close").Rect == new Rectangle(561, 502, 158, 72));
        check(table.Actions.Where(action => action.Family == "present").All(action => !action.Consuming && action.Nodes?.Count == 1));
        var missionActions = new[] {
            (Id: "mission-login-claim", Rect: new Rectangle(1042, 246, 168, 80), Node: "SC.Missions.Claim"),
            (Id: "mission-result-close", Rect: new Rectangle(557, 480, 166, 80), Node: "SC.Missions.ResultClose"),
            (Id: "mission-list-top", Rect: new Rectangle(1235, 114, 16, 18), Node: "SC.Missions.SeekTop"),
            (Id: "mission-list-bottom", Rect: new Rectangle(1235, 542, 16, 18), Node: "SC.Missions.SeekBottom"),
            (Id: "mission-tab-weekly", Rect: new Rectangle(30, 183, 130, 48), Node: "SC.Missions.Tab.Weekly"),
            (Id: "mission-tab-daily", Rect: new Rectangle(30, 98, 130, 48), Node: "SC.Missions.Tab.Daily"),
            (Id: "mission-bulk-receive", Rect: new Rectangle(1110, 650, 130, 38), Node: "SC.Missions.Bulk.Click"),
            (Id: "mission-bulk-confirm", Rect: new Rectangle(661, 419, 158, 72), Node: "SC.Missions.Bulk.ConfirmClick"),
            (Id: "mission-bulk-result-close", Rect: new Rectangle(561, 502, 158, 72), Node: "SC.Missions.Bulk.ResultClose") };
        check(table.Actions.Where(action => action.Family == "mission-claim").Select(action => action.Id)
            .SequenceEqual(missionActions.Select(action => action.Id)));
        foreach (var expected in missionActions)
        {
            var action = table.Get(expected.Id);
            check(action.Rect == expected.Rect && !action.Consuming && action.Wheel is null &&
                action.Nodes?.SequenceEqual(new[] { expected.Node }) == true);
            check(table.NodeRefusal(action.Id, expected.Node) == (action.Enabled ? null : "pipeline_action_disabled"));
            check(table.NodeRefusal(action.Id, "SC.Startup.TouchToStart") ==
                (action.Enabled ? "pipeline_action_node_mismatch" : "pipeline_action_disabled"));
        }
        check(table.Get("home-mission").Nodes!.Contains("SC.Missions.Open") &&
            table.Get("nav-back").Nodes!.Contains("SC.Missions.Return"));
        check(table.Get("training-list-slot-r1c1").Rect == new Rectangle(281, 198, 80, 76) &&
            table.Get("training-list-slot-r3c7").Rect == new Rectangle(1109, 506, 80, 76) &&
            table.Get("training-list-scroll-down").Wheel == -120 && table.Get("training-list-scroll-up").Wheel == 120);
        check(table.ConsumingIds.SequenceEqual(new[] { "shop-exchange-confirm", "training-start", "grade-rehearsal-easy", "grade-rehearsal-normal",
            "grade-rehearsal-hard", "grade-tension-confirm",
            "match-skip-confirm", "match-play-live-start", "weekly-auto-skip", "weekly-auto-start", "weekly-auto-soda-start", "wing-reward-reroll-confirm", "wing-produce-start",
            "collabo-tension-decide" }) &&
            table.MarginPercent >= 20 && table.MinInnerPx == 4);
        check(table.Actions.All(a => a.Family is "startup" or "popup" or "nav" or "mission" or "shop-money" or "training-bronze"
            or "fes-menu" or "grade-fes" or "match-fes" or "grade-live" or "business" or "card-scan" or "knowhow-scan" or "present" or "mission-claim"
            or "weekly-auto" or "wing-nav" or "wing-week" or "wing-live" or "wing-skill" or "wing-end" or "wing-setup" or "wing-start"
            or "wing-giveup" or "wing-halo" or "collabo-fes"));
        // B6 営業 (sampling first): one non-consuming entry inside the left padding of the home 営業 button. B6b adds one
        // non-consuming 「営業完了」 action per slot (sample_collect_one); per-slot dispatch / 差し入れ are not in the table (B6e bulk actions below).
        check(table.Get("home-business").Rect == new Rectangle(747, 658, 28, 40) && !table.IsConsuming("home-business") &&
            table.Get("home-business").Family == "business" && table.Inner("home-business") == new Rectangle(753, 666, 16, 24) &&
            table.Actions.Count(a => a.Family == "business") == 13 &&
            !table.IsAction("business-claim") && !table.IsAction("business-claim-all") && !table.IsAction("business-dispatch") &&
            !table.IsAction("business-dispatch-all") && !table.IsAction("business-gift"));
        // B6e bulk_default: 一括完了 (upper-left strip of the pink button) and 一括営業 (upper part
        // of the white button), both non-consuming; 差し入れ作成 / stamina / soda are never actions.
        check(table.Get("business-bulk-complete").Rect == new Rectangle(1096, 625, 34, 10) &&
            table.Inner("business-bulk-complete") == new Rectangle(1103, 627, 20, 6) &&
            table.Get("business-bulk-dispatch").Rect == new Rectangle(900, 626, 104, 33) &&
            table.Inner("business-bulk-dispatch") == new Rectangle(921, 633, 62, 19) &&
            new[] { "business-bulk-complete", "business-bulk-dispatch" }
                .All(id => !table.IsConsuming(id) && table.Get(id).Family == "business") &&
            !table.IsAction("business-gift-create") && !table.IsAction("business-stamina") && !table.IsAction("business-soda"));
        // B6f: 「次へ」 of the paged WORK REPORT (lower strip below the label, between training-submit and
        // grade-tension-confirm, below live-report-ok); non-consuming.
        check(table.Get("business-report-next-page").Rect == new Rectangle(1141, 690, 35, 9) &&
            table.Inner("business-report-next-page") == new Rectangle(1148, 692, 21, 5) &&
            !table.IsConsuming("business-report-next-page") && table.Get("business-report-next-page").Family == "business");
        // 2026-10-11 (unverified, single-frame derived): 再営業へ of 獲得報酬 and 決定 of 営業アイドル選択; non-consuming.
        check(table.Get("business-reward-redispatch").Rect == new Rectangle(1145, 664, 85, 34) &&
            table.Inner("business-reward-redispatch") == new Rectangle(1162, 671, 51, 20) &&
            table.Get("business-dispatch-decide").Rect == new Rectangle(1145, 650, 85, 34) &&
            table.Inner("business-dispatch-decide") == new Rectangle(1162, 657, 51, 20) &&
            !table.IsConsuming("business-reward-redispatch") && !table.IsConsuming("business-dispatch-decide") &&
            table.Get("business-reward-redispatch").Family == "business" && table.Get("business-dispatch-decide").Family == "business");
        // B6d: WORK REPORT 「Touch to Next」 strip (far below the 詳細 buttons) and one card-body action per slot for
        // sample_dispatch_open (tapped only on a slot read EMPTY on two frames); all non-consuming.
        check(table.Get("business-report-next").Rect == new Rectangle(558, 657, 99, 10) &&
            table.Inner("business-report-next") == new Rectangle(578, 659, 59, 6) &&
            table.Get("business-slot-open-1").Rect == new Rectangle(270, 180, 120, 140) &&
            table.Inner("business-slot-open-1") == new Rectangle(294, 208, 72, 84) &&
            table.Get("business-slot-open-2").Rect == new Rectangle(640, 180, 200, 140) &&
            table.Inner("business-slot-open-2") == new Rectangle(680, 208, 120, 84) &&
            table.Get("business-slot-open-3").Rect == new Rectangle(915, 180, 200, 130) &&
            table.Inner("business-slot-open-3") == new Rectangle(955, 206, 120, 78) &&
            new[] { "business-report-next", "business-slot-open-1", "business-slot-open-2", "business-slot-open-3" }
                .All(id => !table.IsConsuming(id) && table.Get(id).Family == "business"));
        check(table.Get("business-slot-complete-1").Rect == new Rectangle(245, 518, 130, 44) &&
            table.Inner("business-slot-complete-1") == new Rectangle(271, 527, 78, 26) &&
            table.Get("business-slot-complete-2").Rect == new Rectangle(726, 544, 42, 24) &&
            table.Inner("business-slot-complete-2") == new Rectangle(735, 549, 24, 14) &&
            table.Get("business-slot-complete-3").Rect == new Rectangle(1112, 512, 18, 33) &&
            table.Inner("business-slot-complete-3") == new Rectangle(1116, 519, 10, 19) &&
            new[] { 1, 2, 3 }.All(k => !table.IsConsuming($"business-slot-complete-{k}") &&
                table.Get($"business-slot-complete-{k}").Family == "business"));
        // B5d live play: every live action is non-consuming. B5j: LIVE REPORT OK has its own action well inside the pink OK
        // button (x 1097..1258, y 630..699); training-submit keeps the left part of the トレーニング button.
        check(table.Actions.Where(a => a.Family == "grade-live").All(a => !table.IsConsuming(a.Id)) &&
            table.Actions.Count(a => a.Family == "grade-live") == 12 && table.Get("live-speed-toggle").Rect == new Rectangle(1134, 12, 40, 58));
        check(table.Get("live-report-ok").Rect == new Rectangle(1141, 640, 34, 50) &&
            table.Inner("live-report-ok") == new Rectangle(1148, 650, 20, 30) && !table.IsConsuming("live-report-ok") &&
            table.Get("training-submit").Rect == new Rectangle(1097, 635, 44, 57) &&
            table.Inner("training-submit") == new Rectangle(1106, 647, 26, 33));
        check(!table.IsConsuming("grade-difficulty-normal") && !table.IsConsuming("match-skip-open") && !table.IsConsuming("training-bronze-plus"));
        // B5b wheel actions: fixed deltas from the table, never consuming; every other action is a button click.
        check(table.WheelOf("match-skip-scroll-down") == -60 && table.WheelOf("match-skip-scroll-up") == 60 &&
            table.Actions.Count(a => a.Wheel is not null && a.Enabled) == 16 &&   // + knowhow jump probe ±240
            table.Actions.Count(a => a.Wheel is not null) == 17 && table.WheelOf("match-skip-plus-6") is null);
        var scanIds = new[] { "idol-portal-list", "idol-list-tab-produce", "idol-list-tab-support",
            "idol-list-scroll-down", "idol-list-scroll-up" };
        // 2026-10-08: enabled for the one-shot supervised CardScan launcher entry only (the launcher
        // admits them by action id); still non-consuming.
        check(scanIds.All(id => table.Get(id).Enabled && !table.IsConsuming(id) && table.Get(id).Family == "card-scan"));
        check(table.Get("idol-portal-list").Rect == new Rectangle(830, 456, 170, 44) &&
            table.Get("idol-list-tab-support").Rect == new Rectangle(26, 296, 72, 36) &&
            table.WheelOf("idol-list-scroll-down") == -60 && table.WheelOf("idol-list-scroll-up") == 60);
        check(table.Get("home-idol").Nodes!.Contains("SC.CardScan.Home.OpenIdol") &&
            table.Get("nav-back").Nodes!.Contains("SC.CardScan.List.Back") &&
            table.Get("nav-back").Nodes!.Contains("SC.CardScan.Portal.Back"));
        check(table.Get("match-skip-confirm").Rect == new Rectangle(656, 583, 165, 75) && table.Inner("match-skip-plus-6") == new Rectangle(1144, 263, 12, 224));
        check(table.Get("training-start").Rect == new Rectangle(725, 475, 94, 68) && table.Inner("training-start") == new Rectangle(744, 489, 56, 40));
        check(table.Get("grade-tension-confirm").Rect == new Rectangle(1176, 636, 78, 56) &&
            table.Inner("grade-tension-confirm") == new Rectangle(1192, 648, 46, 32));
        // B5h フェス再開: only 「再開」 (non-consuming, upper-right part of the pink button); 「終了」 is not an action.
        check(table.Get("grade-fes-resume").Rect == new Rectangle(728, 440, 84, 33) && !table.IsConsuming("grade-fes-resume") &&
            table.Get("grade-fes-resume").Family == "grade-fes" && table.Inner("grade-fes-resume") == new Rectangle(745, 447, 50, 19) &&
            !table.IsAction("grade-fes-end") && !table.IsAction("grade-fes-quit"));
        check(table.Get("shop-exchange-confirm").Rect == new Rectangle(657, 640, 68, 26) &&
            table.Inner("shop-exchange-confirm") == new Rectangle(671, 646, 40, 14));
        check(table.Get("shop-result-close").Rect == new Rectangle(557, 457, 83, 24) &&
            table.Inner("shop-result-close") == new Rectangle(574, 462, 49, 14));
        var legacyEnabled = table.Actions.Where(action => action.Enabled && action.Family is not ("present" or "mission-claim")).ToArray();
        foreach (var (a, b) in legacyEnabled.SelectMany((a, i) => legacyEnabled.Skip(i + 1).Select(b => (a, b))))
        { check(!a.Rect.IntersectsWith(b.Rect) || table.Version == 2 && a.Nodes is { Count: > 0 } && b.Nodes is { Count: > 0 } && !a.Nodes.Intersect(b.Nodes).Any()); }
        check(table.Get("nav-back").Rect == new Rectangle(20, 628, 74, 66) && table.Inner("nav-back") == new Rectangle(35, 642, 44, 38));
        check(table.Get("game-start").Rect == new Rectangle(725, 604, 130, 52) && table.Inner("game-start") == new Rectangle(751, 615, 78, 30));
        check(table.Inner("popup-pickup-close") == new Rectangle(1100, 56, 24, 22) && table.Centre("home-mission") == new Point(63, 429));
        // 新ガシャ開催！ cut-in: only the lower part of the right-hand SKIP, never 全動画SKIP / Touch; non-consuming.
        check(table.Get("popup-new-gacha-skip").Rect == new Rectangle(1186, 78, 70, 15) &&
            table.Inner("popup-new-gacha-skip") == new Rectangle(1200, 81, 42, 9) && !table.IsConsuming("popup-new-gacha-skip"));
        foreach (var action in new[] { "touch-to-start", "mission-entry", "", "fes-open", "reward-ack", "announcement-back",
            "shop-exchange-confirm-1", "training-confirm-one-bronze", "grade-difficulty-expert", "mission-scroll-down" })
        { check(!table.IsAction(action)); }

        // Table validation mirrors agent/core/pipeline_actions.py.
        byte[] Mutate(string from, string to) { var text = Encoding.UTF8.GetString(File.ReadAllBytes(source)).Replace("\r\n", "\n"); check(text.Contains(from)); return Encoding.UTF8.GetBytes(text.Replace(from, to)); }
        foreach (var bad in new[] {
            Mutate("[1091, 48, 42, 38]", "[1091, 48, 42, 38], \"extra\": 1"),
            Mutate("[1091, 48, 42, 38]", "[1260, 48, 42, 38]"),                // outside the canvas
            Mutate("[1091, 48, 42, 38]", "[1091, 48, 42.0, 38]"),
            Mutate("[1091, 48, 42, 38]", "[-1, 48, 42, 38]"),
            Mutate("\"margin_percent\": 20", "\"margin_percent\": 10"),
            Mutate("\"id\": \"popup-pickup-close\"", "\"id\": \"popup-story-skip\""),
            Mutate("\"id\": \"popup-pickup-close\"", "\"id\": \"Popup_Pickup\""),
            Mutate("\"family\": \"mission\"", "\"family\": \"shop\""),
            Mutate("\"consuming\": false", "\"consuming\": 0"),
            Mutate("\"SC.MissionStatus.OpenMission\"", "\"SC.Startup.TouchToStart\""),
            Mutate("\"version\": 2", "\"version\": 3"),
            Mutate("\"wheel\": -120", "\"wheel\": -100"),                     // not a fixed wheel delta
            Mutate("\"wheel\": 120", "\"wheel\": \"120\""),
            Mutate("\"id\": \"match-skip-scroll-up\",\n            \"family\": \"match-fes\",\n            \"consuming\": false",
                   "\"id\": \"match-skip-scroll-up\",\n            \"family\": \"match-fes\",\n            \"consuming\": true") })
        { Reject(() => PipelineActionTable.Parse(bad)); }
        var testTable = PipelineActionTable.Parse(Encoding.UTF8.GetBytes(TestTable));
        check(testTable.ConsumingIds.SequenceEqual(new[] { "spend-a", "spend-b", "spend-c", "spend-d" }));
        const string scopedJson = """
            {"version": 2, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["t"],
             "actions": [
              {"id":"a","family":"t","consuming":false,"rect":[0,0,100,100],"source":"test","nodes":["SC.Test.A"]},
              {"id":"b","family":"t","consuming":false,"rect":[0,0,100,100],"source":"test","nodes":["SC.Test.B"]},
              {"id":"reserved","family":"t","consuming":false,"enabled":false,"rect":[0,0,100,100],"source":"test","nodes":["SC.Test.Reserved"]}]}
            """;
        var scoped = PipelineActionTable.Parse(Encoding.UTF8.GetBytes(scopedJson));
        check(scoped.NodeRefusal("a", "SC.Test.A") is null && scoped.NodeRefusal("a", "SC.Test.B") is not null &&
            scoped.NodeRefusal("a", null) is not null && scoped.NodeRefusal("reserved", "SC.Test.Reserved") == "pipeline_action_disabled");
        foreach (var bad in new[] { scopedJson.Replace("\"version\": 2", "\"version\": 1"),
            scopedJson.Replace("[\"SC.Test.B\"]", "[\"SC.Test.A\"]"), scopedJson.Replace("[\"SC.Test.B\"]", "[]"),
            scopedJson.Replace("\"enabled\":false", "\"enabled\":0") })
        { Reject(() => PipelineActionTable.Parse(Encoding.UTF8.GetBytes(bad))); }

        // No-rebuild table (「重新载入动作表」): Load = Parse + node binding against the Pipeline JSON; Reload swaps only a
        // fully valid file; a failing file, a missing node or an unvalidated (byte-parsed) table never becomes current.
        {
            var fakeRoot = System.IO.Path.Combine(parent, "table-reload-root");
            var pipelineDir = System.IO.Path.Combine(fakeRoot, "assets", "resource", "base", "pipeline");
            Directory.CreateDirectory(pipelineDir); Directory.CreateDirectory(System.IO.Path.Combine(fakeRoot, "assets", "input"));
            File.WriteAllText(System.IO.Path.Combine(pipelineDir, "t.json"), "{\"SC.Test.A\": {}, \"SC.Test.B\": {}, // comment" + "\n" + " \"SC.Test.Reserved\": {},}");
            var goodPath = System.IO.Path.Combine(fakeRoot, "assets", "input", "pipeline_actions.json");
            File.WriteAllText(goodPath, scopedJson);
            var loaded = PipelineActionTable.Load(goodPath, fakeRoot);
            check(loaded.SourcePath == System.IO.Path.GetFullPath(goodPath) && loaded.LoadedAt is not null &&
                loaded.Sha256 == Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(goodPath))).ToLowerInvariant());
            var danglingPath = System.IO.Path.Combine(fakeRoot, "dangling.json");
            File.WriteAllText(danglingPath, scopedJson.Replace("SC.Test.B", "SC.Test.Missing"));
            Reject(() => PipelineActionTable.Load(danglingPath, fakeRoot));
            var brokenPath = System.IO.Path.Combine(fakeRoot, "broken.json");
            File.WriteAllText(brokenPath, scopedJson.Replace("\"version\": 2", "\"version\": 3"));
            var current = PipelineActionTable.Default.Sha256;
            Reject(() => PipelineActionTable.Reload(brokenPath, fakeRoot));
            Reject(() => PipelineActionTable.Reload(danglingPath, fakeRoot));
            Reject(() => PipelineActionTable.Reload(System.IO.Path.Combine(fakeRoot, "absent.json"), fakeRoot));
            check(PipelineActionTable.Default.Sha256 == current);
            Reject(() => PipelineActionTable.Commit(PipelineActionTable.Parse(Encoding.UTF8.GetBytes(scopedJson))));
            try
            {
                check(PipelineActionTable.Reload(goodPath, fakeRoot).Sha256 == loaded.Sha256 && PipelineActionTable.Default.Sha256 == loaded.Sha256);
                var swappedGate = new PipelineInputGate(PipelineActionTable.Default, null);
                check(ReferenceEquals(swappedGate.Table, PipelineActionTable.Default) && swappedGate.Table.IsAction("a"));
            }
            finally { PipelineActionTable.Reload(); }
            check(PipelineActionTable.Default.Sha256 == current);
        }

        // Bounded click point: uniform inside the rect shrunk by ≥20% per side; centre when the inner rect is too small.
        foreach (var action in table.Actions)
        {
            var inner = table.Inner(action.Id)!.Value; var rect = action.Rect;
            check(inner.X - rect.X >= rect.Width / 5 && rect.Right - inner.Right >= rect.Width / 5 &&
                inner.Y - rect.Y >= rect.Height / 5 && rect.Bottom - inner.Bottom >= rect.Height / 5);
            check(table.Sample(action.Id, _ => 0) == new Point(inner.X, inner.Y));
            check(table.Sample(action.Id, n => n - 1) == new Point(inner.Right - 1, inner.Bottom - 1));
            Reject(() => table.Sample(action.Id, n => n));
            var samples = Enumerable.Range(0, 2000).Select(_ => table.Sample(action.Id, RandomNumberGenerator.GetInt32)).ToArray();
            check(samples.All(inner.Contains) && samples.All(rect.Contains));
            check(samples.Select(p => p.X).Distinct().Count() > inner.Width / 2 && samples.Select(p => p.Y).Distinct().Count() > inner.Height / 2);
        }
        check(testTable.Inner("nav-e") is null && testTable.Sample("nav-e", _ => throw new InvalidOperationException()) == new Point(403, 3));

        // Ordinary requests contain only an action id; observed Match Plus targets are tested above.
        const string token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        var sha = new string('a', 64);
        var nonce = new string('b', 32);
        Dictionary<string, object?> Body(string action = "home-mission")
        {
            var body = new Dictionary<string, object?>
            {
            ["token"] = token, ["command"] = "pipeline-click", ["action"] = action, ["frame_sha256"] = sha,
            ["source_width"] = 1280, ["source_height"] = 720, ["nonce"] = nonce,
            ["protocol_version"] = 2, ["capture_seq"] = 17L, ["frame_binding_seq"] = 29L,
            };
            if (table.IsAction(action)) { body["node_name"] = table.Get(action).Nodes![0]; }
            return body;
        }
        PipelineClickRequest ParseText(string text, PipelineActionTable? vocabulary = null)
        { using var document = JsonDocument.Parse(text); return PipelineInputPolicy.Parse(document.RootElement, token, vocabulary ?? table); }
        PipelineClickRequest ParseBody(Dictionary<string, object?> body, PipelineActionTable? vocabulary = null) => ParseText(JsonSerializer.Serialize(body), vocabulary);
        var parsed = ParseBody(Body());
        check(parsed.Action == "home-mission" && parsed.Frame.FrameSha256 == sha && parsed.Frame.Nonce == nonce && parsed.Grant is null);
        check(parsed.NodeName == "SC.MissionStatus.OpenMission");
        { var body = Body(); body.Remove("node_name"); Reject(() => ParseBody(body)); }
        { var body = Body(); body["node_name"] = "SC.Startup.TouchToStart"; Reject(() => ParseBody(body)); }
        check(ParseBody(Body("training-list-slot-r1c1")).NodeName == "SC.Training.List.Select.R1C1");
        // Enabled card-scan actions parse only with their own SC.CardScan.* node.
        foreach (var action in scanIds)
        {
            check(ParseBody(Body(action)).NodeName == table.Get(action).Nodes![0] && table.Get(action).Nodes![0].StartsWith("SC.CardScan.", StringComparison.Ordinal));
            var body = Body(action); body["node_name"] = "SC.Training.OpenIdol"; Reject(() => ParseBody(body));
        }
        var scopedBody = Body("a"); scopedBody["node_name"] = "SC.Test.A";
        var scopedRequest = ParseBody(scopedBody, scoped);
        check(scopedRequest.Action == "a" && scopedRequest.NodeName == "SC.Test.A");
        { var body = new Dictionary<string, object?>(scopedBody); body.Remove("node_name"); Reject(() => ParseBody(body, scoped)); }
        { var body = new Dictionary<string, object?>(scopedBody); body["node_name"] = "SC.Test.B"; Reject(() => ParseBody(body, scoped)); }
        var grantBody = Body("spend-a");
        grantBody["spend_grant"] = new Dictionary<string, object?> { ["action"] = "spend-a", ["frame_sha256"] = sha,
            ["nonce"] = nonce, ["expires_at"] = 1759374000000L, ["capture_seq"] = 17L, ["frame_binding_seq"] = 29L,
            ["hmac"] = new string('c', 64) };
        var parsedGrant = ParseBody(grantBody, testTable);
        check(parsedGrant.Grant is { Action: "spend-a", ExpiresAt: 1759374000000L } && parsedGrant.Grant.Hmac == new string('c', 64));
        Reject(() => ParseBody(grantBody));  // not in the shipped vocabulary
        foreach (var (key, value) in new (string, object?)[] { ("command", "daily-navigate"), ("token", new string('f', 64)),
            ("action", "touch-to-start"), ("action", "fes-open"), ("frame_sha256", new string('A', 64)), ("nonce", new string('b', 31)),
            ("source_width", 1300), ("source_height", "720"), ("extra", 1), ("x", 640), ("point", new[] { 640, 630 }) })
        { var body = Body(); body[key] = value; Reject(() => ParseBody(body)); }
        { var body = Body(); body.Remove("nonce"); Reject(() => ParseBody(body)); }
        foreach (var (key, value) in new (string, object?)[] { ("extra", 1), ("hmac", "short"), ("expires_at", "1"), ("nonce", "x") })
        {
            var body = Body("spend-a");
            var grant = new Dictionary<string, object?> { ["action"] = "spend-a", ["frame_sha256"] = sha, ["nonce"] = nonce,
                ["expires_at"] = 1L, ["capture_seq"] = 17L, ["frame_binding_seq"] = 29L, ["hmac"] = new string('c', 64) };
            grant[key] = value; body["spend_grant"] = grant; Reject(() => ParseBody(body, testTable));
        }
        { var body = Body(); body["spend_grant"] = "grant"; Reject(() => ParseBody(body)); }
        Reject(() => ParseText(JsonSerializer.Serialize(Body()).Replace("\"nonce\"", "\"action\":\"home-mission\",\"nonce\"")));
        Reject(() => ParseText("[]"));
        foreach (var key in new[] { "capture_seq", "frame_binding_seq" })
        {
            foreach (var value in new object?[] { null, false, true, 0L, -1L, "17", 1.5, decimal.MaxValue })
            { var body = Body(); body[key] = value; Reject(() => ParseBody(body)); }
            var missing = Body(); missing.Remove(key); Reject(() => ParseBody(missing));
            var max = Body(); max[key] = long.MaxValue; check(ParseBody(max).Action == "home-mission");
            Reject(() => ParseText(JsonSerializer.Serialize(Body()).Replace($"\"{key}\":{(key == "capture_seq" ? 17 : 29)}", $"\"{key}\":1.0")));
            Reject(() => ParseText(JsonSerializer.Serialize(Body()).Replace($"\"{key}\":{(key == "capture_seq" ? 17 : 29)}", $"\"{key}\":1e0")));
        }
        { var old = Body(); old["protocol_version"] = 1; Reject(() => ParseBody(old)); }
        { var old = Body(); old.Remove("protocol_version"); old.Remove("capture_seq"); old.Remove("frame_binding_seq"); Reject(() => ParseBody(old)); }
        foreach (var key in new[] { "capture_seq", "frame_binding_seq" })
        {
            var missing = new Dictionary<string, object?>((Dictionary<string, object?>)grantBody["spend_grant"]!);
            missing.Remove(key);
            var oldGrant = Body("spend-a"); oldGrant["spend_grant"] = missing; Reject(() => ParseBody(oldGrant, testTable));
            foreach (var value in new object?[] { false, 0L, -1L, "1", decimal.MaxValue })
            {
                var invalid = new Dictionary<string, object?>((Dictionary<string, object?>)grantBody["spend_grant"]!);
                invalid[key] = value; var body = Body("spend-a"); body["spend_grant"] = invalid;
                Reject(() => ParseBody(body, testTable));
            }
        }

        // Gate construction.
        var now = new DateTimeOffset(2026, 10, 2, 3, 0, 0, TimeSpan.Zero);
        var mono = 0.0;
        var secret = Enumerable.Repeat((byte)7, 32).ToArray();
        var gateRoot = System.IO.Path.Combine(parent, "pipeline");
        var seeded = new Random(20261002);
        var delays = new List<double>();
        Task Delay(double ms, CancellationToken _) { delays.Add(ms); mono += ms; return Task.CompletedTask; }
        PipelineInputGate NewGate(string name, PipelineLimits? limits = null, Func<int, int>? random = null,
            Func<double, CancellationToken, Task>? delay = null, PipelineActionTable? vocabulary = null) =>
            new(secret, System.IO.Path.Combine(gateRoot, name, "run_logs"), () => now, () => mono, limits ?? new PipelineLimits(),
                vocabulary ?? testTable, random ?? seeded.Next, delay ?? Delay);
        Reject(() => new PipelineInputGate(secret, System.IO.Path.GetTempPath(), () => now, () => mono, new PipelineLimits(), testTable, seeded.Next, Delay));
        foreach (var limits in new[] { new PipelineLimits(MinIntervalMs: 100), new PipelineLimits(GrantMaxTtlMs: 60000),
            new PipelineLimits(IntervalJitterMaxMs: -1), new PipelineLimits(SessionMaxMs: 43_200_001), new PipelineLimits(RunMax: -1) })
        { Reject(() => NewGate("bad-limits", limits)); }
        Reject(() => new PipelineInputGate(new byte[16], System.IO.Path.Combine(gateRoot, "short"), () => now, () => mono, new PipelineLimits(), testTable, seeded.Next, Delay));
        check(new PipelineLimits() is { MinIntervalMs: 800, IntervalJitterMaxMs: 200, PerActionPerMinute: 60, RunMax: 0, RunSpendMax: 64, SessionMaxMs: 43_200_000 });
        // Cross-language vector shared with tests/test_webview_controller.py (Python sign_spend_grant).
        check(PipelineInputPolicy.Sign(secret, "shop-exchange-confirm-1", sha, nonce, 1759374000000L, 17, 29) ==
            "d3fe989607ba19c4ce796c01f120fa2003b32f86b7a82e5e63ccf0b66288091a");
        check(PipelineInputGate.DefaultAuditRoot().EndsWith(System.IO.Path.Combine("dev_assets", "private", "run_logs")));

        // Simulated host state; the gesture delegate records instead of sending input.
        string? frameSha = null; int? width = 1280, height = 720; var age = 10.0; var hostOk = true;
        long captureSequence = 0, bindingSequence = 0;
        var gestures = new List<string>(); var points = new List<Point>(); var gestureTimes = new List<double>();
        string Capture() { frameSha = Hex(32); age = 10; captureSequence++; bindingSequence += 2; return frameSha; }
        string? Host() => hostOk ? null : "host_state_guard_failed";
        bool Bound(PipelineFrameRequest frame) => PipelineInputPolicy.FrameMatches(frame, frameSha, width, height, age, captureSequence, bindingSequence);
        Task<NavigationResult> Gesture(PipelineClickRequest request, Point point, Func<string?> guard)
        {
            if (guard() is string reason) { return Task.FromResult(new NavigationResult(false, false, reason)); }
            gestures.Add(request.Action); points.Add(point); gestureTimes.Add(mono); frameSha = null; bindingSequence++;
            return Task.FromResult(new NavigationResult(true, true, "native_gesture_sent_postcondition_required"));
        }
        PipelineClickRequest Req(string action, string? frame = null, string? requestNonce = null, PipelineSpendGrant? grant = null, int sourceWidth = 1280) =>
            new(new(action, frame ?? frameSha ?? new string('0', 64), sourceWidth, 720, requestNonce ?? Hex(16), captureSequence, bindingSequence), grant,
                table.IsAction(action) ? table.Get(action).Nodes![0] : null);
        PipelineSpendGrant Grant(PipelineClickRequest request, string? action = null, long? expires = null, byte[]? key = null)
        {
            var grantAction = action ?? request.Action; var expiresAt = expires ?? now.ToUnixTimeMilliseconds() + 4000;
            return new(grantAction, request.Frame.FrameSha256, request.Frame.Nonce, expiresAt, request.Frame.CaptureSequence, request.Frame.BindingSequence,
                PipelineInputPolicy.Sign(key ?? secret, grantAction, request.Frame.FrameSha256, request.Frame.Nonce, expiresAt,
                    request.Frame.CaptureSequence, request.Frame.BindingSequence));
        }
        PipelineClickRequest Signed(PipelineClickRequest request, PipelineSpendGrant grant) => request with { Grant = grant };
        Task<NavigationResult> Exec(PipelineInputGate gate, PipelineClickRequest request, Func<PipelineFrameRequest, bool>? bound = null,
            Func<PipelineClickRequest, Point, Func<string?>, Task<NavigationResult>>? gesture = null) => gate.Execute(request, Host, bound ?? Bound, gesture ?? Gesture, CancellationToken.None);
        string[] Lines(string path) => File.Exists(path) ? File.ReadAllLines(path) : Array.Empty<string>();

        var gate = NewGate("main", new PipelineLimits(RunSpendMax: 2));
        var scopedGate = NewGate("scoped", vocabulary: scoped);
        check(scopedGate.Admit(scopedRequest with { NodeName = null }) == "pipeline_action_node_mismatch");
        check(scopedGate.Admit(scopedRequest with { NodeName = "SC.Test.B" }) == "pipeline_action_node_mismatch");
        check(scopedGate.Admit(scopedRequest with { Frame = scopedRequest.Frame with { Action = "reserved" }, NodeName = "SC.Test.Reserved" }) ==
            "pipeline_action_disabled");
        check(scopedGate.Admit(scopedRequest) is null); // Refusals above must not consume this nonce.
        check(gate.GameDay() == "2026-10-02" && gate.AuditPath("2026-10-02").EndsWith("host-2026-10-02.jsonl") &&
            gate.SpendAuditPath("2026-10-02").EndsWith("host-spend-2026-10-02.jsonl"));
        var audit = gate.AuditPath("2026-10-02"); var spendAudit = gate.SpendAuditPath("2026-10-02");

        // Non-consuming click: no ledger, intent (with the chosen point) written before the gesture, result after.
        Capture(); var first = Req("nav-a");
        var r = await Exec(gate, first, gesture: (request, point, guard) =>
        {
            check(Lines(audit).Any(line => line.Contains("\"intent\"") && line.Contains(request.Frame.Nonce) && line.Contains($"\"point\":[{point.X},{point.Y}]")));
            return Gesture(request, point, guard);
        });
        check(r.ok && r.attempted && gestures.SequenceEqual(new[] { "nav-a" }) && delays.Count == 0);
        check(testTable.Inner("nav-a")!.Value.Contains(points[0]));
        check(Lines(audit).Length == 2 && Lines(audit)[1].Contains("\"sent\"") && !File.Exists(spendAudit));
        check(!Directory.EnumerateFiles(System.IO.Path.GetDirectoryName(audit)!).Any(path => !path.EndsWith(".jsonl")));
        // Frame binding: the binding is dropped after a gesture, so the same frame cannot be reused.
        r = await Exec(gate, Req("nav-a", first.Frame.FrameSha256));
        check(!r.ok && !r.attempted && r.reason == "frame_binding_guard_failed" && gestures.Count == 1);
        // Interval: the host waits out min 800 ms + this gesture's jitter (0..200 ms), then sends; never faster.
        var gap = gate.RequiredGapMs; check(gap >= 800 && gap <= 1000);
        Capture(); mono += 500; var replay = Req("nav-b");
        r = await Exec(gate, replay);
        check(r.ok && gestures.Count == 2 && delays.Count == 1 && Math.Abs(delays[0] - (gap - 500)) < 1e-9 &&
            Math.Abs(gestureTimes[1] - gestureTimes[0] - gap) < 1e-9);
        // A delay that does not elapse cannot bypass the gap.
        var stuck = NewGate("stuck", delay: (_, _) => Task.CompletedTask);
        Capture(); check((await Exec(stuck, Req("nav-a"))).ok);
        Capture(); r = await Exec(stuck, Req("nav-a")); check(r.reason == "rate_limited_min_interval" && gestures.Count == 3);
        // A frame that changes during the wait blocks the gesture.
        var changing = NewGate("changing", delay: (ms, _) => { mono += ms; frameSha = null; return Task.CompletedTask; });
        mono += 2000; Capture(); check((await Exec(changing, Req("nav-a"))).ok);
        Capture(); r = await Exec(changing, Req("nav-a")); check(r.reason == "frame_changed_during_interval_wait" && gestures.Count == 4);
        // Jitter bounds: always within [800, 1000] ms, both ends reachable, never below the minimum.
        var maxJitter = NewGate("max-jitter", random: n => n - 1); mono += 2000;
        Capture(); check((await Exec(maxJitter, Req("nav-a"))).ok && maxJitter.RequiredGapMs == 1000);
        check(points.Last() == new Point(79, 79));
        var minJitter = NewGate("min-jitter", random: _ => 0); mono += 2000;
        Capture(); check((await Exec(minJitter, Req("nav-a"))).ok && minJitter.RequiredGapMs == 800 && points.Last() == new Point(20, 20));
        var spread = NewGate("spread", new PipelineLimits(PerActionPerMinute: 30, RunMax: 600), random: RandomNumberGenerator.GetInt32);
        var gaps = new List<double>(); var last = -1.0;
        for (var index = 0; index < 200; index++)
        {
            mono += index % 20 == 19 ? 61000 : 0; Capture();
            check((await Exec(spread, Req(index % 2 == 0 ? "nav-c" : "nav-d"))).ok);
            if (last >= 0 && index % 20 != 19) { gaps.Add(gestureTimes.Last() - last); }
            last = gestureTimes.Last();
        }
        check(gaps.All(g => g >= 800 - 1e-9 && g <= 1000 + 1e-9) && gaps.Distinct().Count() > 50);
        // Nonce replay.
        Capture(); mono += 2000;
        r = await Exec(gate, Req("nav-b", requestNonce: replay.Frame.Nonce));
        check(r.reason == "nonce_replayed");
        // Frame binding: stale, size change, SHA mismatch, host state.
        var before = gestures.Count;
        Capture(); age = 5001; r = await Exec(gate, Req("nav-c")); check(r.reason == "frame_binding_guard_failed");
        Capture(); r = await Exec(gate, Req("nav-c", sourceWidth: 1281)); check(r.reason == "frame_binding_guard_failed");
        Capture(); r = await Exec(gate, Req("nav-c", new string('d', 64))); check(r.reason == "frame_binding_guard_failed");
        Capture(); width = null; r = await Exec(gate, Req("nav-c")); check(r.reason == "frame_binding_guard_failed"); width = 1280;
        Capture(); hostOk = false; r = await Exec(gate, Req("nav-c")); check(r.reason == "host_state_guard_failed"); hostOk = true;
        check(gestures.Count == before);
        // Re-check after the audit I/O: a frame that changes in between blocks the gesture.
        Capture(); var calls = 0;
        r = await Exec(gate, Req("nav-c"), bound: frame => ++calls == 1 && Bound(frame));
        check(!r.ok && !r.attempted && r.reason == "frame_changed_after_audit_no_retry" && calls == 2 && gestures.Count == before);

        // Consuming actions need a valid, unexpired, single-use HMAC grant bound to action, frame and nonce.
        mono += 2000; Capture(); var spend = Req("spend-a");
        r = await Exec(gate, spend); check(r.reason == "spend_grant_required");
        r = await Exec(gate, Signed(spend, Grant(spend, key: Enumerable.Repeat((byte)8, 32).ToArray()))); check(r.reason == "spend_grant_hmac_invalid");
        r = await Exec(gate, Signed(spend, Grant(spend) with { ExpiresAt = now.ToUnixTimeMilliseconds() + 4999 })); check(r.reason == "spend_grant_hmac_invalid");
        r = await Exec(gate, Signed(spend, Grant(spend, action: "spend-b"))); check(r.reason == "spend_grant_wrong_action");
        r = await Exec(gate, Signed(spend, Grant(spend with { Frame = spend.Frame with { FrameSha256 = new string('e', 64) } }))); check(r.reason == "spend_grant_frame_mismatch");
        r = await Exec(gate, Signed(spend, Grant(spend with { Frame = spend.Frame with { Nonce = new string('9', 32) } }))); check(r.reason == "spend_grant_nonce_mismatch");
        r = await Exec(gate, Signed(spend, Grant(spend, expires: now.ToUnixTimeMilliseconds()))); check(r.reason == "spend_grant_expired");
        r = await Exec(gate, Signed(spend, Grant(spend, expires: now.ToUnixTimeMilliseconds() + 5001))); check(r.reason == "spend_grant_ttl_too_long");
        var navigation = Req("nav-d");
        r = await Exec(gate, Signed(navigation, Grant(navigation))); check(r.reason == "spend_grant_not_applicable");
        var legacyMac = Convert.ToHexString(HMACSHA256.HashData(secret, Encoding.UTF8.GetBytes(
            $"SC.PipelineSpendGrant.v1\n{spend.Action}\n{spend.Frame.FrameSha256}\n{spend.Frame.Nonce}\n{Grant(spend).ExpiresAt}"))).ToLowerInvariant();
        check(gate.Admit(Signed(spend, Grant(spend) with { Hmac = legacyMac })) == "spend_grant_hmac_invalid");
        check(gestures.Count == before && !File.Exists(spendAudit));
        var valid = Signed(spend, Grant(spend));
        r = await Exec(gate, valid, gesture: (request, point, guard) =>
        {
            // Spend audit and host intent both precede the gesture.
            check(Lines(spendAudit).Length == 1 && Lines(spendAudit)[0].Contains("before_gesture") && Lines(spendAudit)[0].Contains(request.Frame.Nonce));
            check(Lines(audit).Last().Contains("\"intent\"") && Lines(audit).Last().Contains(request.Frame.Nonce));
            return Gesture(request, point, guard);
        });
        check(r.ok && r.attempted && gestures.Last() == "spend-a" && testTable.Inner("spend-a")!.Value.Contains(points.Last()));
        check(!Lines(spendAudit)[0].Contains(valid.Grant!.Hmac) && Lines(spendAudit)[0].Contains(valid.Grant.Hmac[..8]));
        mono += 2000; frameSha = valid.Frame.FrameSha256; age = 10;
        r = await Exec(gate, valid); check(r.reason == "frame_binding_guard_failed");
        check(gate.Admit(valid) == "spend_grant_already_used");
        check(Lines(spendAudit).Length == 1);
        mono += 2000; Capture(); var spendC = Req("spend-c");
        r = await Exec(gate, Signed(spendC, Grant(spendC))); check(r.ok);
        mono += 2000; Capture(); var spendD = Req("spend-d");
        r = await Exec(gate, Signed(spendD, Grant(spendD))); check(r.reason == "run_spend_cap_reached");
        check(Lines(spendAudit).Length == 2 && gestures.Count == before + 2);
        // A new run restores the run's spend budget. Used grants/nonces and the cumulative host audit stay unchanged.
        check(gate.BeginRun(Hex(16)).reason == "run_begin_refused_recent_input" && gate.RunWindow == 1);
        check(gate.Admit(Signed(spendD, Grant(spendD))) == "run_spend_cap_reached");
        mono += PipelineInputGate.RunBeginQuietMs; check(gate.BeginRun(Hex(16)) is { ok: true, attempted: false, reason: "run_window_opened" });
        check(gate.RunWindow == 2 && gate.RunCount == 0 && gate.Admit(valid) == "spend_grant_already_used");
        Capture(); var spendD2 = Req("spend-d");
        r = await Exec(gate, Signed(spendD2, Grant(spendD2))); check(r.ok && Lines(spendAudit).Length == 3 && gestures.Count == before + 3);
        mono += 2000; Capture(); var secondRunSpend = Req("spend-c");
        check((await Exec(gate, Signed(secondRunSpend, Grant(secondRunSpend)))).ok);
        mono += 2000; Capture(); var secondRunExcess = Req("spend-d");
        check((await Exec(gate, Signed(secondRunExcess, Grant(secondRunExcess)))).reason == "run_spend_cap_reached");
        check(Lines(audit).Count(line => line.Contains("\"run_begin\"") && line.Contains("\"run_window\":2")) == 1 &&
            Lines(audit).Count(line => line.Contains("\"run_begin_refused\"")) == 1);
        var firstBegin = JsonDocument.Parse(Lines(audit).Single(line => line.Contains("\"run_begin\"")));
        check(firstBegin.RootElement.GetProperty("previous_run_spends").GetInt32() == 2 &&
            firstBegin.RootElement.GetProperty("host_spends").GetInt32() == 2);
        firstBegin.Dispose();
        mono += PipelineInputGate.RunBeginQuietMs; check(gate.BeginRun(Hex(16)).ok);
        using (var nextBegin = JsonDocument.Parse(Lines(audit).Last()))
        { check(nextBegin.RootElement.GetProperty("previous_run_spends").GetInt32() == 2 && nextBegin.RootElement.GetProperty("host_spends").GetInt32() == 4); }
        check(gate.Admit(valid) == "spend_grant_already_used");

        var zeroSpend = NewGate("zero-spend", new PipelineLimits(RunSpendMax: 0));
        Capture(); var zeroRequest = Req("spend-a");
        check(zeroSpend.Admit(Signed(zeroRequest, Grant(zeroRequest))) == "run_spend_cap_reached");
        check(zeroSpend.BeginRun(Hex(16)).ok);
        check(zeroSpend.Admit(Signed(zeroRequest, Grant(zeroRequest))) == "run_spend_cap_reached");

        // Failure to audit a new window must preserve an exhausted spend budget and the current window.
        var brokenBegin = NewGate("broken-begin", new PipelineLimits(RunSpendMax: 1));
        Capture(); var lastSpend = Req("spend-a");
        check((await Exec(brokenBegin, Signed(lastSpend, Grant(lastSpend)))).ok);
        var brokenBeginAudit = brokenBegin.AuditPath("2026-10-02");
        File.Move(brokenBeginAudit, brokenBeginAudit + ".saved");
        Directory.CreateDirectory(brokenBeginAudit);
        mono += PipelineInputGate.RunBeginQuietMs;
        check(brokenBegin.BeginRun(Hex(16)).reason.StartsWith("audit_write_failed_no_reset") && brokenBegin.RunWindow == 1);
        Capture(); var afterFailedBegin = Req("spend-c");
        check(brokenBegin.Admit(Signed(afterFailedBegin, Grant(afterFailedBegin))) == "run_spend_cap_reached");
        // Shipped table: an action outside it is refused; shop-exchange-confirm needs a valid grant, other shop
        // actions refuse one, and a granted exchange lands inside the clipped exchange rect.
        var shipped = NewGate("shipped", vocabulary: table);
        mono += 2000; Capture(); var foreign = Req("spend-a");
        r = await Exec(shipped, Signed(foreign, Grant(foreign))); check(r.reason == "action_not_in_pipeline_vocabulary");
        mono += 2000; Capture(); r = await Exec(shipped, Req("popup-reward-ok"));
        check(r.ok && table.Inner("popup-reward-ok")!.Value.Contains(points.Last()));
        mono += 2000; Capture(); var exchange = Req("shop-exchange-confirm");
        r = await Exec(shipped, exchange); check(r.reason == "spend_grant_required");
        var cancel = Req("shop-exchange-cancel");
        r = await Exec(shipped, Signed(cancel, Grant(cancel))); check(r.reason == "spend_grant_not_applicable");
        r = await Exec(shipped, Signed(exchange, Grant(exchange, action: "shop-exchange-cancel"))); check(r.reason == "spend_grant_wrong_action");
        r = await Exec(shipped, Signed(exchange, Grant(exchange)));
        check(r.ok && gestures.Last() == "shop-exchange-confirm" && table.Inner("shop-exchange-confirm")!.Value.Contains(points.Last()));
        check(Lines(shipped.SpendAuditPath("2026-10-02")).Length == 1);
        // B5: training-start and grade-tension-confirm need their own grant; the difficulty tap and other B5 actions refuse one.
        foreach (var spendAction in new[] { "training-start", "grade-tension-confirm" })
        {
            mono += 2000; Capture(); var request = Req(spendAction);
            r = await Exec(shipped, request); check(r.reason == "spend_grant_required");
            r = await Exec(shipped, Signed(request, Grant(request, action: "shop-exchange-confirm"))); check(r.reason == "spend_grant_wrong_action");
            r = await Exec(shipped, Signed(request, Grant(request)));
            check(r.ok && gestures.Last() == spendAction && table.Inner(spendAction)!.Value.Contains(points.Last()));
        }
        {
            mono += 2000; Capture(); var request = Req("match-skip-confirm");
            r = await Exec(shipped, request); check(r.reason == "spend_grant_required");
            r = await Exec(shipped, Signed(request, Grant(request)));
            check(r.ok && gestures.Last() == "match-skip-confirm" && table.Inner("match-skip-confirm")!.Value.Contains(points.Last()));
        }
        foreach (var plain in new[] { "grade-difficulty-normal", "training-bronze-plus", "match-skip-open", "home-fes",
            "match-skip-plus-6", "match-skip-clear", "match-skip-scroll-down" })
        {
            mono += 2000; Capture(); var request = Req(plain);
            r = await Exec(shipped, Signed(request, Grant(request))); check(r.reason == "spend_grant_not_applicable");
            r = await Exec(shipped, request); check(r.ok && gestures.Last() == plain && table.Inner(plain)!.Value.Contains(points.Last()));
        }
        check(Lines(shipped.SpendAuditPath("2026-10-02")).Length == 4);
        // Every click, including refusals, lands in the host audit.
        check(Lines(audit).Count(line => line.Contains("\"refused\"")) >= 15 && Lines(audit).Count(line => line.Contains("\"sent\"")) == 6);

        // Per-action per-minute limit, per-run cap and session length cap.
        var limited = NewGate("per-minute", new PipelineLimits(PerActionPerMinute: 3));
        for (var index = 0; index < 3; index++) { mono += 2000; Capture(); check((await Exec(limited, Req("nav-c"))).ok); }
        mono += 2000; Capture(); check((await Exec(limited, Req("nav-c"))).reason == "rate_limited_per_action_minute");
        check((await Exec(limited, Req("nav-a"))).ok);
        mono += 60000; Capture(); check((await Exec(limited, Req("nav-c"))).ok);
        var capped = NewGate("run-cap", new PipelineLimits(RunMax: 2));
        for (var index = 0; index < 2; index++) { mono += 2000; Capture(); check((await Exec(capped, Req("nav-e"))).ok); }
        check(points.Last() == new Point(403, 3));
        mono += 2000; Capture(); check((await Exec(capped, Req("nav-e"))).reason == "run_cap_reached" && capped.RunCount == 2);
        var timed = NewGate("session-cap", new PipelineLimits(SessionMaxMs: 10_000));
        mono += 2000; Capture(); var timedEarly = Req("nav-a"); check((await Exec(timed, timedEarly)).ok);
        mono += 9_000; Capture(); check((await Exec(timed, Req("nav-b"))).ok);
        mono += 1_001; Capture(); check((await Exec(timed, Req("nav-c"))).reason == "session_time_cap_reached");
        // A new run window (launcher handshake) restarts the run count and the session clock; rate limits and nonces stay.
        check(timed.RunCount == 2 && timed.RunWindow == 1);
        var beginNonce = Hex(16);
        check(timed.BeginRun(beginNonce).reason == "run_begin_refused_recent_input" && timed.RunCount == 2 && timed.RunWindow == 1);
        check(timed.RunBeginRefusal() == "run_begin_refused_recent_input");
        mono += PipelineInputGate.RunBeginQuietMs - 1_002; check(timed.BeginRun(Hex(16)).reason == "run_begin_refused_recent_input");
        mono += 1; check(timed.RunBeginRefusal() is null);
        check(timed.BeginRun(beginNonce) is { ok: true, attempted: false } && timed.RunWindow == 2 && timed.RunCount == 0);
        check(timed.BeginRun(beginNonce).reason == "nonce_replayed" && timed.BeginRun("not-a-nonce").reason == "run_begin_nonce_invalid");
        check(timed.BeginRun(Hex(16)).ok && timed.RunWindow == 3);   // nothing sent since: a launcher retry may reopen it
        Capture(); check((await Exec(timed, Req("nav-c"))).ok && timed.RunCount == 1);
        mono += 9_000; Capture(); check((await Exec(timed, Req("nav-d"))).ok);
        mono += 1_001; Capture(); check((await Exec(timed, Req("nav-a"))).reason == "session_time_cap_reached");
        check(timed.Admit(timedEarly) == "nonce_replayed");          // a nonce from an earlier window stays used
        mono += 2000; Capture(); check((await Exec(capped, Req("nav-b"))).reason == "run_cap_reached");
        mono += PipelineInputGate.RunBeginQuietMs; check(capped.BeginRun(Hex(16)).ok);
        Capture(); check((await Exec(capped, Req("nav-e"))).ok && capped.RunCount == 1 && capped.RunWindow == 2);

        // Audit write failure: no audit, no input.
        var broken = NewGate("broken-audit");
        Directory.CreateDirectory(broken.AuditPath("2026-10-02"));
        before = gestures.Count;
        mono += 2000; Capture(); r = await Exec(broken, Req("nav-a"));
        check(!r.ok && !r.attempted && r.reason.StartsWith("audit_write_failed_no_input") && gestures.Count == before);
        var brokenSpend = NewGate("broken-spend-audit");
        Directory.CreateDirectory(brokenSpend.SpendAuditPath("2026-10-02"));
        mono += 2000; Capture(); var blocked = Req("spend-b");
        r = await Exec(brokenSpend, Signed(blocked, Grant(blocked)));
        check(!r.ok && !r.attempted && r.reason.StartsWith("audit_write_failed_no_input") && gestures.Count == before);
        check(!Lines(brokenSpend.AuditPath("2026-10-02")).Any(line => line.Contains("\"intent\"")));
        // A failed gesture is attempted and unknown; never retried.
        var failing = NewGate("gesture-failure");
        mono += 2000; Capture();
        r = await Exec(failing, Req("nav-a"), gesture: (_, _, _) => throw new InvalidOperationException("down-failure"));
        check(!r.ok && r.attempted && r.reason.StartsWith("native_input_failed_no_retry") && Lines(failing.AuditPath("2026-10-02")).Last().Contains("\"failed\""));
        // Game day rolls over at 05:00 JST.
        now = new DateTimeOffset(2026, 10, 2, 19, 59, 0, TimeSpan.Zero); check(gate.GameDay() == "2026-10-02");
        now = new DateTimeOffset(2026, 10, 2, 20, 0, 0, TimeSpan.Zero); check(gate.GameDay() == "2026-10-03");

        // Session file and loopback endpoint (stub handler, no browser), using the shipped table.
        var sessionPath = System.IO.Path.Combine(parent, "session.json" + PipelineInputPolicy.SessionSuffix);
        var handled = new List<PipelineClickRequest>();
        using (var bridge = new PipelineInputBridge(sessionPath, shipped, (request, _) =>
            { handled.Add(request); return Task.FromResult(new NavigationResult(false, false, "stub_no_input")); }))
        {
            using var session = JsonDocument.Parse(File.ReadAllBytes(sessionPath));
            var data = session.RootElement;
            check(data.GetProperty("transport").GetString() == PipelineInputPolicy.Transport &&
                data.GetProperty("spend_secret").GetString() == shipped.SecretHex && data.GetProperty("pid").GetInt32() == Environment.ProcessId);
            check(data.GetProperty("version").GetInt32() == 2 && data.EnumerateObject().Count() == 19 &&
                data.GetProperty("run_begin_command").GetString() == PipelineInputPolicy.RunBeginCommand &&
                data.GetProperty("frame_binding_protocol").GetString() == PipelineInputPolicy.FrameBindingProtocol &&
                data.GetProperty("appeal_timing_protocol").GetString() == AppealTiming.Protocol &&
                data.GetProperty("hold_protocol").GetString() == PipelineInputPolicy.HoldProtocol &&
                data.GetProperty("drag_protocol").GetString() == PipelineInputPolicy.DragProtocol);
            check(data.GetProperty("actions").EnumerateArray().Select(item => item.GetString()).SequenceEqual(table.Ids));
            check(data.GetProperty("consuming_actions").EnumerateArray().Select(item => item.GetString())
                .SequenceEqual(new[] { "shop-exchange-confirm", "training-start", "grade-rehearsal-easy", "grade-rehearsal-normal",
                    "grade-rehearsal-hard", "grade-tension-confirm", "match-skip-confirm", "match-play-live-start", "weekly-auto-skip", "weekly-auto-start", "weekly-auto-soda-start",
                    "wing-reward-reroll-confirm", "wing-produce-start", "collabo-tension-decide" }) &&
                data.GetProperty("action_table_sha256").GetString() == table.Sha256);
            var limits = data.GetProperty("limits");
            check(limits.GetProperty("min_interval_ms").GetInt32() == 800 && limits.GetProperty("interval_jitter_max_ms").GetInt32() == 200 &&
                limits.GetProperty("session_max_ms").GetInt32() == 43_200_000 && limits.EnumerateObject().Count() == 7);
            Reject(() => new PipelineInputBridge(sessionPath, shipped, (_, _) => Task.FromResult(new NavigationResult(false, false, "x"))));
            async Task<JsonElement> Send(string line)
            {
                using var client = new TcpClient();
                await client.ConnectAsync("127.0.0.1", data.GetProperty("port").GetInt32());
                using var stream = client.GetStream();
                await stream.WriteAsync(Encoding.UTF8.GetBytes(line + "\n"));
                using var reader = new StreamReader(stream, Encoding.UTF8);
                using var answer = JsonDocument.Parse(await reader.ReadLineAsync() ?? "{}");
                return answer.RootElement.Clone();
            }
            var body = Body(); body["token"] = data.GetProperty("token").GetString();
            var reply = await Send(JsonSerializer.Serialize(body));
            check(reply.GetProperty("reason").GetString() == "stub_no_input" && handled.Count == 1 && handled[0].Action == "home-mission");
            body["action"] = "mission-back";  // legacy id: not in the pipeline vocabulary
            reply = await Send(JsonSerializer.Serialize(body));
            check(!reply.GetProperty("ok").GetBoolean() && !reply.GetProperty("attempted").GetBoolean() && handled.Count == 1);
            body["action"] = "home-mission"; body["token"] = token;
            reply = await Send(JsonSerializer.Serialize(body));
            check(!reply.GetProperty("ok").GetBoolean() && !reply.GetProperty("attempted").GetBoolean() && handled.Count == 1);
            // Launcher handshake: opens a new run window on the gate, never reaches the click handler.
            var runNonce = Hex(16); var window = shipped.RunWindow;
            var begin = new Dictionary<string, object> { ["command"] = PipelineInputPolicy.RunBeginCommand, ["protocol_version"] = 2,
                ["token"] = token, ["nonce"] = runNonce };
            reply = await Send(JsonSerializer.Serialize(begin));
            check(!reply.GetProperty("ok").GetBoolean() && shipped.RunWindow == window && handled.Count == 1);   // wrong token
            begin["token"] = data.GetProperty("token").GetString()!; begin["action"] = "nav-back";
            reply = await Send(JsonSerializer.Serialize(begin));
            check(!reply.GetProperty("ok").GetBoolean() && shipped.RunWindow == window);                           // extra field
            begin.Remove("action"); begin["protocol_version"] = 1;
            reply = await Send(JsonSerializer.Serialize(begin));
            check(!reply.GetProperty("ok").GetBoolean() && shipped.RunWindow == window);                           // old protocol
            begin["protocol_version"] = 2;
            mono += PipelineInputGate.RunBeginQuietMs;
            reply = await Send(JsonSerializer.Serialize(begin));
            check(reply.GetProperty("ok").GetBoolean() && !reply.GetProperty("attempted").GetBoolean() &&
                reply.GetProperty("reason").GetString() == "run_window_opened" && shipped.RunWindow == window + 1 &&
                shipped.RunCount == 0 && handled.Count == 1);
            reply = await Send(JsonSerializer.Serialize(begin));
            check(reply.GetProperty("reason").GetString() == "nonce_replayed" && shipped.RunWindow == window + 1);
        }
        check(!File.Exists(sessionPath));
        await PipelineFrameSelfTest.Run(parent, valid => check(valid));
    }
}

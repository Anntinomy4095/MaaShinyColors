using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline checks of the default-off exploration grid; never creates a browser or sends input.</summary>
internal static class ProduceExploreSelfTest
{
    /// <summary>Pinned with tests/test_produce_explore.py (Python <c>agent/core/produce_explore.GRID_SHA256</c>).</summary>
    internal const string ExpectedGridSha256 = "6585249ad26de73ecca876b2897cbf566e5b493a3bbdbeae238461c72711d2ab";

    internal static async Task Run(string parent, Action<bool> check)
    {
        void Reject(System.Action action) { var rejected = false; try { action(); } catch { rejected = true; } check(rejected); }

        // Option truth table: default off; requires --enable-pipeline-input on the Composition game HTTPS host.
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-pipeline-input" }).EnableProduceExplore);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-pipeline-input", "--enable-produce-explore" })
            is { EnableProduceExplore: true, EnablePipelineInput: true, CompositionGame: true });
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-pipeline-input", "--enable-produce-explore", "--background-keepalive" })
            is { EnableProduceExplore: true, BackgroundKeepalive: true });
        foreach (var args in new[] {
            new[] { "--enable-produce-explore" },
            new[] { "--composition-game", "--enable-produce-explore" },
            new[] { "--composition-demo", "--enable-produce-explore" },
            new[] { "--browser-demo", "--enable-produce-explore" },
            new[] { "--offline-demo", "--enable-produce-explore" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-produce-explore" },
            new[] { "--composition-game", "--enable-pipeline-input", "--enable-produce-explore", "--url", "https://example.com/" },
            new[] { "--composition-game", "--enable-pipeline-input", "--enable-produce-explore", "--enable-supervised-navigation", "announcement-back" } })
        { Reject(() => BrowserHostOptions.Parse(args)); }

        // Grid: 18x32 cells of 40x40, unique ids, in bounds, pairwise disjoint, tiling the canvas exactly.
        var grid = ProduceExploreGrid.Default;
        var ids = grid.Ids.ToArray();
        check(ids.Length == 576 && ids.Distinct(StringComparer.Ordinal).Count() == 576 && ids[0] == "explore-r00c00" &&
            ids[^1] == "explore-r17c31" && ids.Contains("explore-r07c12"));
        var rects = ids.Select(grid.Rect).ToArray();
        check(rects.All(r => r.X >= 0 && r.Y >= 0 && r.Right <= 1280 && r.Bottom <= 720 && r.Width == 40 && r.Height == 40));
        check(rects.Sum(r => (long)r.Width * r.Height) == 1280L * 720);
        for (var i = 0; i < rects.Length; i++) { for (var j = i + 1; j < rects.Length; j++) { if (rects[i].IntersectsWith(rects[j])) { check(false); } } }
        check(grid.Rect("explore-r07c12") == new Rectangle(480, 280, 40, 40) && grid.Inner("explore-r07c12") == new Rectangle(488, 288, 24, 24));
        check(grid.Rect("explore-r17c31") == new Rectangle(1240, 680, 40, 40));
        foreach (var id in ids)
        {
            var rect = grid.Rect(id); var inner = grid.Inner(id);
            check(inner.X - rect.X >= rect.Width / 5 && rect.Right - inner.Right >= rect.Width / 5 &&
                inner.Y - rect.Y >= rect.Height / 5 && rect.Bottom - inner.Bottom >= rect.Height / 5);
            check(grid.Sample(id, _ => 0) == inner.Location && grid.Sample(id, n => n - 1) == new Point(inner.Right - 1, inner.Bottom - 1));
        }
        Reject(() => grid.Sample("explore-r00c00", n => n));
        var samples = Enumerable.Range(0, 2000).Select(_ => grid.Sample("explore-r09c16", RandomNumberGenerator.GetInt32)).ToArray();
        check(samples.All(grid.Inner("explore-r09c16").Contains) && samples.Select(p => p.X).Distinct().Count() > 12);
        foreach (var bad in new[] { "explore-r18c00", "explore-r00c32", "explore-r7c12", "explore-R07C12", "explore-r07c12 ",
            " explore-r07c12", "explore-r-1c00", "explore-r07c12x", "r07c12", "explore-r07c12\n", "explore-r０7c12", "", "explore-" })
        { check(!grid.IsCell(bad)); Reject(() => grid.Rect(bad)); }
        check(ProduceExploreGrid.Version == "produce-explore-grid.v1" && ProduceExploreGrid.Family == "produce-explore");
        check(grid.Sha256 == Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(ProduceExploreGrid.Descriptor))).ToLowerInvariant() &&
            grid.Sha256 == ExpectedGridSha256);
        // Separate from the fixed table: no shared id/family, and the shipped table is unchanged by the grid.
        var table = PipelineActionTable.Default;
        ProduceExploreGrid.RequireDisjointIds(table);
        check(ids.All(id => !table.IsAction(id)) && table.Ids.All(id => !grid.IsCell(id)));
        var source = System.IO.Path.Combine(SelectionLoopBudget.Root(), "assets", "input", "pipeline_actions.json");
        check(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source))).ToLowerInvariant() == table.Sha256);
        var clashing = PipelineActionTable.Parse(Encoding.UTF8.GetBytes("""
            {"version": 1, "canvas": [1280, 720], "margin_percent": 20, "min_inner_px": 4, "families": ["t"],
             "actions": [{"id": "explore-r00c00", "family": "t", "consuming": false, "rect": [0, 0, 100, 100], "source": "test", "nodes": []}]}
            """));
        Reject(() => ProduceExploreGrid.RequireDisjointIds(clashing));

        // Request parse: explore ids only when the grid is passed; still id-only, never coordinates.
        const string token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        var sha = new string('a', 64);
        string Body(string action, string extra = "") =>
            $"{{\"token\":\"{token}\",\"command\":\"pipeline-click\",\"action\":\"{action}\",\"frame_sha256\":\"{sha}\"," +
            $"\"source_width\":1280,\"source_height\":720,\"nonce\":\"{new string('b', 32)}\"," +
            $"\"protocol_version\":2,\"capture_seq\":1,\"frame_binding_seq\":2{extra}}}";
        PipelineClickRequest Parse(string text, ProduceExploreGrid? explore)
        { using var document = JsonDocument.Parse(text); return PipelineInputPolicy.Parse(document.RootElement, token, table, explore); }
        Reject(() => Parse(Body("explore-r07c12"), null));
        check(Parse(Body("explore-r07c12"), grid).Action == "explore-r07c12");
        Reject(() => Parse(Body("home-mission"), grid));
        check(Parse(Body("home-mission", ",\"node_name\":\"SC.MissionStatus.OpenMission\""), grid).Action == "home-mission");
        Reject(() => Parse(Body("explore-r07c12", ",\"node_name\":\"SC.MissionStatus.OpenMission\""), grid));
        foreach (var bad in new[] { "explore-r18c00", "explore-r7c12", "explore-x", "explore-r07c12-1" })
        { Reject(() => Parse(Body(bad), grid)); }
        Reject(() => Parse(Body("explore-r07c12", ",\"x\":640"), grid));
        Reject(() => Parse(Body("explore-r07c12", ",\"point\":[640,360]"), grid));

        // Gate: same limits/frame binding as the table actions; never consuming; refused without the grid.
        var now = new DateTimeOffset(2026, 10, 4, 3, 0, 0, TimeSpan.Zero);
        var mono = 0.0; var secret = Enumerable.Repeat((byte)9, 32).ToArray(); var seeded = new Random(20261004);
        var delays = new List<double>();
        Task Delay(double ms, CancellationToken _) { delays.Add(ms); mono += ms; return Task.CompletedTask; }
        var root = System.IO.Path.Combine(parent, "produce_explore");
        PipelineInputGate NewGate(string name, ProduceExploreGrid? explore, PipelineLimits? limits = null) =>
            new(secret, System.IO.Path.Combine(root, name, "run_logs"), () => now, () => mono, limits ?? new PipelineLimits(),
                table, seeded.Next, Delay, explore);
        string? frameSha = null;
        long captureSequence = 0, bindingSequence = 0;
        string Capture() { frameSha = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant(); captureSequence++; bindingSequence += 2; return frameSha; }
        var gestures = new List<(string, Point)>();
        bool Bound(PipelineFrameRequest frame) => PipelineInputPolicy.FrameMatches(frame, frameSha, 1280, 720, 10, captureSequence, bindingSequence);
        Task<NavigationResult> Gesture(PipelineClickRequest request, Point point, Func<string?> guard)
        {
            if (guard() is string reason) { return Task.FromResult(new NavigationResult(false, false, reason)); }
            gestures.Add((request.Action, point)); frameSha = null; bindingSequence++;
            return Task.FromResult(new NavigationResult(true, true, "stub_no_input"));
        }
        PipelineClickRequest Req(string action, PipelineSpendGrant? grant = null) =>
            new(new(action, frameSha ?? new string('0', 64), 1280, 720, Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant(), captureSequence, bindingSequence), grant,
                table.IsAction(action) ? table.Get(action).Nodes![0] : null);
        Task<NavigationResult> Exec(PipelineInputGate gate, PipelineClickRequest request) =>
            gate.Execute(request, () => null, Bound, Gesture, CancellationToken.None);

        var off = NewGate("off", null);
        check(off.Explore is null && !off.IsKnown("explore-r07c12"));
        Capture();
        var r = await Exec(off, Req("explore-r07c12"));
        check(!r.ok && !r.attempted && r.reason == "action_not_in_pipeline_vocabulary" && gestures.Count == 0);

        var on = NewGate("on", grid);
        check(on.IsKnown("explore-r07c12") && on.IsExplore("explore-r07c12") && !on.IsConsuming("explore-r07c12") &&
            on.FamilyOf("explore-r07c12") == "produce-explore" && on.IsKnown("home-mission") && !on.IsExplore("home-mission") &&
            on.FamilyOf("home-mission") == "mission" && !on.IsKnown("explore-r18c00"));
        Capture();
        r = await Exec(on, Req("explore-r07c12"));
        check(r.ok && r.attempted && gestures.Count == 1 && grid.Inner("explore-r07c12").Contains(gestures[0].Item2));
        var audit = File.ReadAllLines(on.AuditPath(on.GameDay()));
        check(audit.Length == 2 && audit[0].Contains("\"intent\"") && audit[0].Contains("explore-r07c12") && audit[0].Contains("\"consuming\":false") &&
            audit[1].Contains("\"sent\"") && !File.Exists(on.SpendAuditPath(on.GameDay())));
        // Frame binding is dropped after the gesture: the same frame cannot be reused.
        r = await Exec(on, Req("explore-r07c12"));
        check(!r.ok && !r.attempted && r.reason == "frame_binding_guard_failed" && gestures.Count == 1);
        // A grant on a non-consuming explore cell is refused.
        Capture();
        var request = Req("explore-r00c00");
        r = await Exec(on, request with { Grant = new("explore-r00c00", request.Frame.FrameSha256, request.Frame.Nonce, 0,
            request.Frame.CaptureSequence, request.Frame.BindingSequence, new string('c', 64)) });
        check(!r.ok && r.reason == "spend_grant_not_applicable" && gestures.Count == 1);
        // Min interval: the host waits out the gap (never faster), exactly as for table actions.
        // (The refused grant request above already waited out the gap, so use a fresh gate here.)
        var paced = NewGate("paced", grid);
        Capture(); mono += 5000; check((await Exec(paced, Req("explore-r00c01"))).ok);
        Capture(); mono += 100;
        var before = delays.Count;
        r = await Exec(paced, Req("explore-r00c02"));
        check(r.ok && gestures.Count == 3 && delays.Count == before + 1 && delays[^1] >= 700 && delays[^1] <= 1300);
        // Per-action per-minute cap applies per cell.
        var capped = NewGate("capped", grid, new PipelineLimits(PerActionPerMinute: 2));
        for (var k = 0; k < 2; k++) { Capture(); mono += 2000; check((await Exec(capped, Req("explore-r01c01"))).ok); }
        Capture(); mono += 2000;
        r = await Exec(capped, Req("explore-r01c01"));
        check(!r.ok && r.reason == "rate_limited_per_action_minute");
        // Run cap shared with table actions.
        var run = NewGate("run", grid, new PipelineLimits(RunMax: 1));
        Capture(); mono += 2000; check((await Exec(run, Req("explore-r02c02"))).ok);
        Capture(); mono += 2000; r = await Exec(run, Req("home-mission"));
        check(!r.ok && r.reason == "run_cap_reached");
        // Session cap (12 h from the first admitted gesture).
        var session = NewGate("session", grid);
        Capture(); mono += 2000; check((await Exec(session, Req("explore-r03c03"))).ok);
        Capture(); mono += 43_200_000; r = await Exec(session, Req("explore-r03c04"));
        check(!r.ok && r.reason == "session_time_cap_reached");
    }

    internal static async Task RunStandalone()
    {
        var root = System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "produce_explore_selftests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root); var count = 0;
        await Run(root, valid => { if (!valid) { throw new InvalidOperationException($"监督探索离线自检失败（第 {count + 1} 项）。"); } count++; });
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            passed = true, checks = count, real_input_attempts = 0, evidence_directory = root,
            explore_grid_version = ProduceExploreGrid.Version, explore_grid_sha256 = ProduceExploreGrid.Default.Sha256,
        }));
    }
}

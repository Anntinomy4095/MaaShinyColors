using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

internal static class DailyNavigationSelfTest
{
    internal static async Task Run(string root, Action<bool> check)
    {
        check(!BrowserHostOptions.Parse(new[] { "--composition-game" }).EnableSupervisedDaily);
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily" }).EnableSupervisedTraining);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-training" }).EnableSupervisedTraining);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily" }).EnableSupervisedDaily);
        foreach (var args in new[] {
            new[] { "--enable-supervised-daily" },
            new[] { "--composition-game", "--enable-supervised-training" },
            new[] { "--composition-demo", "--enable-supervised-daily" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-navigation", "announcement-back" },
            new[] { "--composition-game", "--enable-supervised-daily", "--url", "https://example.com/" } })
        {
            var rejected = false;
            try { _ = BrowserHostOptions.Parse(args); } catch (ArgumentException) { rejected = true; }
            check(rejected);
        }
        check(NavigationBudget.DefaultPath("announcement-back").EndsWith(System.IO.Path.Combine("composition_navigation", "one-shot.json")));
        check(NavigationBudget.DefaultPath("home-mission").EndsWith(System.IO.Path.Combine("daily_navigation", "home-mission.json")));
        check(NavigationBudget.DefaultPath("mission-complete-to-mission").EndsWith(System.IO.Path.Combine("daily_navigation", "mission-complete-to-mission.json")));
        check(NavigationBudget.DefaultPath("mission-complete-to-mission") != NavigationBudget.DefaultPath("home-mission"));
        check(DailyNavigationPolicy.Actions.Contains("mission-complete-to-mission", StringComparer.Ordinal));
        check(!DailyNavigationPolicy.IsTraining("mission-complete-to-mission"));
        check(DailyNavigationPolicy.FixedPoint("mission-complete-to-mission") == new Point(744, 580));
        var nonspendPoints = new Dictionary<string, Point> {
            ["mission-help-close"] = new(640, 603), ["home-fes"] = new(1190, 650),
            ["fes-match-entry"] = new(1045, 543), ["match-help-close"] = new(640, 603),
            ["match-skip-open"] = new(1190, 548), ["match-skip-cancel"] = new(462, 620),
            ["match-back"] = new(57, 662), ["training-back"] = new(57, 662), ["pickup-close"] = new(1111, 68), ["story-skip-open"] = new(1221, 56),
            ["training-help-close"] = new(640, 603), ["game-start"] = new(640, 630) };
        check(DailyNavigationPolicy.Actions.Distinct(StringComparer.Ordinal).Count() == DailyNavigationPolicy.Actions.Length);
        check(DailyNavigationPolicy.Actions.Select(NavigationBudget.DefaultPath).Distinct(StringComparer.Ordinal).Count() == DailyNavigationPolicy.Actions.Length);
        foreach (var (action, point) in nonspendPoints)
        {
            check(DailyNavigationPolicy.Actions.Contains(action, StringComparer.Ordinal) && !DailyNavigationPolicy.IsTraining(action));
            check(DailyNavigationPolicy.FixedPoint(action) == point);
            check(NavigationBudget.DefaultPath(action).EndsWith(System.IO.Path.Combine("daily_navigation", action + ".json")));
        }
        // Fixed non-consuming returns from the training portal stay behind the training switch.
        foreach (var action in new[] { "training-list-back", "idol-back" })
        {
            check(DailyNavigationPolicy.IsTraining(action) && !DailyNavigationPolicy.Actions.Contains(action, StringComparer.Ordinal));
            check(DailyNavigationPolicy.IsAction(action) && !DailyNavigationPolicy.IsScroll(action));
            check(DailyNavigationPolicy.FixedPoint(action) == new Point(57, 662));
            check(NavigationBudget.DefaultPath(action).EndsWith(System.IO.Path.Combine("daily_navigation", action + ".json")));
            check(!DailyNavigationPolicy.AllowedActions(false, false).Contains(action, StringComparer.Ordinal));
            check(DailyNavigationPolicy.AllowedActions(true, false).Contains(action, StringComparer.Ordinal));
        }
        check(DailyNavigationPolicy.TrainingActions.Select(NavigationBudget.DefaultPath).Distinct(StringComparer.Ordinal).Count() == DailyNavigationPolicy.TrainingActions.Length);
        // Daily shop: separate default-off switch bound to the supervised daily game host.
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily" }).EnableSupervisedShop);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-shop" }).EnableSupervisedShop);
        foreach (var args in new[] {
            new[] { "--enable-supervised-shop" }, new[] { "--composition-game", "--enable-supervised-shop" },
            new[] { "--enable-supervised-daily", "--enable-supervised-shop" },
            new[] { "--composition-demo", "--enable-supervised-daily", "--enable-supervised-shop" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-shop", "--url", "https://example.com/" },
            new[] { "--composition-game", "--enable-supervised-shop", "--enable-supervised-navigation", "announcement-back" } })
        {
            var rejected = false;
            try { _ = BrowserHostOptions.Parse(args); } catch (ArgumentException) { rejected = true; }
            check(rejected);
        }
        var shopPoints = new Dictionary<string, Point> {
            ["home-shop"] = new(62, 282), ["shop-money-tab"] = new(495, 110), ["shop-back"] = new(57, 662),
            ["shop-offer-open-1"] = new(166, 607), ["shop-offer-open-2"] = new(436, 607), ["shop-offer-open-3"] = new(706, 607),
            ["shop-exchange-cancel"] = new(540, 626), ["shop-exchange-confirm-1"] = new(740, 626),
            ["shop-exchange-confirm-2"] = new(740, 626), ["shop-exchange-confirm-3"] = new(740, 626),
            ["shop-result-close"] = new(640, 497) };
        check(DailyNavigationPolicy.ShopActions.SequenceEqual(shopPoints.Keys) && shopPoints.Count == 11);
        var today = RepeatableNavigationBudget.GameDayOf(DateTimeOffset.UtcNow);
        foreach (var (action, point) in shopPoints)
        {
            check(DailyNavigationPolicy.IsShop(action) && DailyNavigationPolicy.IsAction(action) && !DailyNavigationPolicy.IsTraining(action) &&
                !DailyNavigationPolicy.IsScroll(action) && !DailyNavigationPolicy.Actions.Contains(action, StringComparer.Ordinal));
            check(DailyNavigationPolicy.FixedPoint(action) == point);
            check(DailyNavigationPolicy.IsShopConsuming(action) == action.StartsWith("shop-exchange-confirm-", StringComparison.Ordinal));
            check(NavigationBudget.DefaultPath(action).EndsWith(System.IO.Path.Combine("dev_assets", "private", "daily_shop_native", today, action + ".json")));
            check(!DailyNavigationPolicy.AllowedActions(true, true).Contains(action, StringComparer.Ordinal));
            check(DailyNavigationPolicy.AllowedActions(false, false, true).Contains(action, StringComparer.Ordinal));
        }
        check(DailyNavigationPolicy.ShopActions.Count(DailyNavigationPolicy.IsShopConsuming) == 3);
        // Grade Fes: separate default-off switch and endpoint; never on the daily endpoint.
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily" }).EnableSupervisedGrade);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-grade" }).EnableSupervisedGrade);
        foreach (var args in new[] {
            new[] { "--enable-supervised-grade" }, new[] { "--composition-game", "--enable-supervised-grade" },
            new[] { "--enable-supervised-daily", "--enable-supervised-grade" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-supervised-grade", "--url", "https://example.com/" },
            new[] { "--composition-game", "--enable-supervised-grade", "--enable-supervised-navigation", "announcement-back" } })
        {
            var rejected = false;
            try { _ = BrowserHostOptions.Parse(args); } catch (ArgumentException) { rejected = true; }
            check(rejected);
        }
        var gradePoints = new Dictionary<string, Point> {
            ["fes-grade-entry"] = new(1188, 541), ["fes-help-close"] = new(640, 600), ["grade-join-open"] = new(1183, 620),
            ["grade-mode-grade"] = new(1167, 453), ["grade-back"] = new(57, 662), ["grade-difficulty-easy"] = new(332, 360),
            ["grade-difficulty-normal"] = new(638, 360), ["grade-difficulty-hard"] = new(942, 360) };
        check(DailyNavigationPolicy.GradeActions.SequenceEqual(gradePoints.Keys));
        foreach (var (action, point) in gradePoints)
        {
            check(DailyNavigationPolicy.IsGrade(action) && DailyNavigationPolicy.IsAction(action) && !DailyNavigationPolicy.IsShop(action) &&
                !DailyNavigationPolicy.IsTraining(action) && !DailyNavigationPolicy.IsScroll(action));
            check(DailyNavigationPolicy.FixedPoint(action) == point);
            check(DailyNavigationPolicy.IsGradeConsuming(action) == action.StartsWith("grade-difficulty-", StringComparison.Ordinal));
            check(NavigationBudget.DefaultPath(action).EndsWith(System.IO.Path.Combine("dev_assets", "private", "daily_grade_native", today, action + ".json")));
            check(!DailyNavigationPolicy.AllowedActions(true, true, true).Contains(action, StringComparer.Ordinal));
        }
        check(DailyNavigationPolicy.GradeActions.Count(DailyNavigationPolicy.IsGradeConsuming) == 3);
        foreach (var forbidden in new[] { "grade-bulk-toggle", "grade-difficulty", "rehearsal-difficulty-easy", "grade-mode-rehearsal", "grade-difficulty-expert" })
        { check(!DailyNavigationPolicy.IsAction(forbidden)); }
        var gradeSession = System.IO.Path.Combine(root, "grade-session.json");
        using (SupervisedDailyBridge.Grade(gradeSession, (_, _) => Task.FromResult(new NavigationResult(false, false, "selftest"))))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(gradeSession));
            check(document.RootElement.GetProperty("transport").GetString() == SupervisedDailyBridge.GradeTransport);
            check(document.RootElement.GetProperty("actions").EnumerateArray().Select(a => a.GetString()).SequenceEqual(DailyNavigationPolicy.GradeActions));
        }
        check(!File.Exists(gradeSession));
        check(NavigationBudget.ShopPath(root, "shop-exchange-confirm-1", "2026-10-02") != NavigationBudget.ShopPath(root, "shop-exchange-confirm-1", "2026-10-03"));
        // The quantity controls (−/+/>>) and 交換上限 row are never fixed targets.
        foreach (var forbidden in new[] { "shop-exchange-plus", "shop-exchange-max", "shop-exchange-minus", "shop-exchange-confirm", "shop-premium-buy" })
        { check(!DailyNavigationPolicy.IsAction(forbidden)); }
        var shopRoot = System.IO.Path.Combine(root, "shop-one-shot");
        var shopBudget = new NavigationBudget(NavigationBudget.ShopPath(shopRoot, "shop-exchange-confirm-1", "2026-10-02"), "shop-exchange-confirm-1");
        check(shopBudget.Consume("shop-exchange-confirm-1", new string('a', 64), new string('b', 32)));
        check(!new NavigationBudget(shopBudget.Path, "shop-exchange-confirm-1").Consume("shop-exchange-confirm-1", new string('c', 64), new string('d', 32)));
        check(new NavigationBudget(NavigationBudget.ShopPath(shopRoot, "shop-exchange-confirm-2", "2026-10-02"), "shop-exchange-confirm-2")
            .Consume("shop-exchange-confirm-2", new string('e', 64), new string('f', 32)));
        foreach (var uri in new[] { "http://shinycolors.enza.fun/", "https://example.com/", "https://shinycolors.enza.fun.attacker.test/", "https://shinycolors.enza.fun:444/", "https://user@shinycolors.enza.fun/", "about:blank" })
        { check(!DailyNavigationPolicy.IsGameUri(uri)); }
        check(DailyNavigationPolicy.IsGameUri("https://shinycolors.enza.fun/home"));
        var path = System.IO.Path.Combine(root, "daily-session.json");
        var calls = 0;
        using (var bridge = new SupervisedDailyBridge(path, (request, _) =>
        {
            calls++;
            var budget = new NavigationBudget(System.IO.Path.Combine(root, request.Action + ".json"), request.Action);
            var accepted = budget.Consume(request.Action, request.FrameSha256, request.Nonce);
            return Task.FromResult(new NavigationResult(accepted, accepted, accepted ? "offline_fake_only" : "one_shot_budget_exhausted"));
        }))
        {
            using var session = JsonDocument.Parse(File.ReadAllText(path));
            var token = session.RootElement.GetProperty("token").GetString()!;
            var port = session.RootElement.GetProperty("port").GetInt32();
            check(session.RootElement.GetProperty("actions").EnumerateArray().Select(item => item.GetString()).SequenceEqual(DailyNavigationPolicy.Actions));
            var paths = session.RootElement.GetProperty("budget_paths");
            check(paths.EnumerateObject().Select(item => item.Name).SequenceEqual(DailyNavigationPolicy.Actions));
            check(paths.GetProperty("mission-complete-to-mission").GetString() == NavigationBudget.DefaultPath("mission-complete-to-mission"));
            var payload = new Dictionary<string, object> {
                ["token"] = token, ["command"] = "daily-navigate", ["action"] = "home-mission",
                ["frame_sha256"] = new string('a', 64), ["source_width"] = 1280, ["source_height"] = 720,
                ["nonce"] = new string('b', 32) };
            async Task<NavigationResult> Send(Dictionary<string, object> data)
            {
                using var client = new TcpClient(); await client.ConnectAsync("127.0.0.1", port);
                using var stream = client.GetStream();
                await stream.WriteAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(data) + "\n"));
                using var reader = new StreamReader(stream);
                return JsonSerializer.Deserialize<NavigationResult>((await reader.ReadLineAsync())!)!;
            }
            var valid = DailyNavigationPolicy.Parse(JsonSerializer.SerializeToElement(payload), token);
            check(DailyNavigationPolicy.FrameMatches(valid, new string('a', 64), 1280, 720, 5000));
            foreach (var age in new[] { 5000.1, -1, double.NaN, double.PositiveInfinity })
            { check(!DailyNavigationPolicy.FrameMatches(valid, new string('a', 64), 1280, 720, age)); }
            check(!DailyNavigationPolicy.FrameMatches(valid, new string('c', 64), 1280, 720, 10));
            check(!DailyNavigationPolicy.FrameMatches(valid, new string('a', 64), 1282, 720, 10));
            foreach (var change in new (string Key, object Value)[] {
                ("action", "training-confirm"), ("action", "training-confirm-one-bronze"),
                ("action", "mission-complete"), ("action", "story-skip-confirm"), ("action", "pickup-details"),
                ("coordinates", new[] { 640, 520 }), ("url", "https://shinycolors.enza.fun/home"),
                ("token", "bad-token"), ("nonce", new string('b', 32) + "\n"), ("source_width", int.MinValue),
                ("source_height", true), ("command", "navigate"), ("frame_sha256", new string('A', 64)) })
            {
                var altered = new Dictionary<string, object>(payload) { [change.Key] = change.Value };
                var result = await Send(altered); check(!result.ok && !result.attempted && calls == 0);
            }
            var success = await Send(payload); check(success.ok && success.attempted && calls == 1);
            var duplicate = await Send(payload); check(!duplicate.ok && !duplicate.attempted && calls == 2);
            check(File.ReadAllText(System.IO.Path.Combine(root, "home-mission.json")).Contains(new string('b', 32)));
            var modalPayload = new Dictionary<string, object>(payload) {
                ["action"] = "mission-complete-to-mission", ["nonce"] = new string('d', 32) };
            var modalRequest = DailyNavigationPolicy.Parse(JsonSerializer.SerializeToElement(modalPayload), token);
            check(DailyNavigationPolicy.FrameMatches(modalRequest, new string('a', 64), 1280, 720, 5000));
            check(!DailyNavigationPolicy.FrameMatches(modalRequest, new string('c', 64), 1280, 720, 10));
            check(!DailyNavigationPolicy.FrameMatches(modalRequest, new string('a', 64), 1280, 720, 5000.1));
            var modalSuccess = await Send(modalPayload); check(modalSuccess.ok && modalSuccess.attempted && calls == 3);
            var modalDuplicate = await Send(modalPayload); check(!modalDuplicate.ok && !modalDuplicate.attempted && calls == 4);
            modalPayload["nonce"] = new string('e', 32);
            var modalNewNonce = await Send(modalPayload); check(!modalNewNonce.ok && !modalNewNonce.attempted && calls == 5);
            check(File.ReadAllText(System.IO.Path.Combine(root, "mission-complete-to-mission.json")).Contains(new string('d', 32)));
            foreach (var action in nonspendPoints.Keys)
            {
                check(paths.GetProperty(action).GetString() == NavigationBudget.DefaultPath(action));
                var navigationPayload = new Dictionary<string, object>(payload) {
                    ["action"] = action, ["nonce"] = new string('f', 32) };
                var navigationRequest = DailyNavigationPolicy.Parse(JsonSerializer.SerializeToElement(navigationPayload), token);
                check(DailyNavigationPolicy.FrameMatches(navigationRequest, new string('a', 64), 1280, 720, 5000));
                check(!DailyNavigationPolicy.FrameMatches(navigationRequest, new string('c', 64), 1280, 720, 10));
                check(!DailyNavigationPolicy.FrameMatches(navigationRequest, new string('a', 64), 1280, 720, 5000.1));
                var previousCalls = calls;
                var navigationSuccess = await Send(navigationPayload);
                check(navigationSuccess.ok && navigationSuccess.attempted && calls == previousCalls + 1);
                navigationPayload["nonce"] = new string('e', 32);
                var navigationDuplicate = await Send(navigationPayload);
                check(!navigationDuplicate.ok && !navigationDuplicate.attempted && calls == previousCalls + 2);
                check(File.ReadAllText(System.IO.Path.Combine(root, action + ".json")).Contains(new string('f', 32)));
            }
        }
        check(!File.Exists(path));
        // With the shop switch the session advertises the shop actions and their per-day native ledgers.
        var shopSession = System.IO.Path.Combine(root, "daily-shop-session.json");
        using (new SupervisedDailyBridge(shopSession, (_, _) => Task.FromResult(new NavigationResult(false, false, "offline")), false, false, true))
        {
            using var session = JsonDocument.Parse(File.ReadAllText(shopSession));
            check(session.RootElement.GetProperty("actions").EnumerateArray().Select(item => item.GetString())
                .SequenceEqual(DailyNavigationPolicy.Actions.Concat(DailyNavigationPolicy.ShopActions)));
            var shopPaths = session.RootElement.GetProperty("budget_paths");
            foreach (var action in DailyNavigationPolicy.ShopActions)
            { check(shopPaths.GetProperty(action).GetString() == NavigationBudget.DefaultPath(action)); }
        }
        check(!File.Exists(shopSession));
        await ReadOnlyScrollSelfTest.Run(root, check);
    }
}

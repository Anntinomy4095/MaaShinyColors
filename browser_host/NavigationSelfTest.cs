using Microsoft.Web.WebView2.Core;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline state/failure checks; never creates a browser or uses desktop input.</summary>
internal static class NavigationSelfTest
{
    internal static async Task Run()
    {
        var root = System.IO.Path.Combine(Directory.GetCurrentDirectory(), "dev_assets", "private", "navigation_selftests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var count = 0;
        void Check(bool condition) { if (!condition) { throw new InvalidOperationException("监督导航离线自检失败。"); } count++; }
        Check(BrowserNavigationPolicy.CanBrowse("https://enza.fun/login"));
        Check(BrowserNavigationPolicy.CanBrowse("https://account.example.test/authorize"));
        Check(BrowserNavigationPolicy.CanBrowse("about:blank"));
        Check(!BrowserNavigationPolicy.CanBrowse("file:///C:/Windows/test.html"));
        Check(!BrowserNavigationPolicy.CanBrowse("javascript:alert(1)"));
        Check(!DailyNavigationPolicy.IsGameUri("https://enza.fun/login"));
        Check(DailyNavigationPolicy.IsGameUri("https://shinycolors.enza.fun/"));
        var path = System.IO.Path.Combine(root, "budget.json");
        var budget = new NavigationBudget(path, "announcement-back");
        Check(budget.Consume("announcement-back", new string('a', 64)));
        Check(!new NavigationBudget(path, "announcement-back").Consume("announcement-back", new string('b', 64)));
        var startupPath = System.IO.Path.Combine(root, "start-one-shot.json");
        var startup = new NavigationBudget(startupPath, "touch-to-start");
        Check(startup.Consume("touch-to-start", new string('c', 64)));
        Check(!new NavigationBudget(startupPath, "touch-to-start").Consume("touch-to-start", new string('d', 64)));
        Check(File.ReadAllText(path).Contains("announcement-back") && File.ReadAllText(startupPath).Contains("touch-to-start"));
        Check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-navigation", "touch-to-start" }).SupervisedNavigation == "touch-to-start");
        foreach (var content in new[] { "", "{", "{}" })
        {
            var corrupt = System.IO.Path.Combine(root, $"corrupt-{count}.json");
            File.WriteAllText(corrupt, content);
            var rejected = false;
            try { _ = new NavigationBudget(corrupt, "announcement-back"); }
            catch (Exception) { rejected = true; }
            Check(rejected && File.ReadAllText(corrupt) == content);
        }
        var events = new List<CoreWebView2MouseEventKind>();
        await NativeGesture.Send((kind, _) => events.Add(kind));
        Check(events.SequenceEqual(new[] { CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventKind.Move, CoreWebView2MouseEventKind.LeftButtonDown, CoreWebView2MouseEventKind.LeftButtonUp }));
        events.Clear();
        var downFailed = false;
        try
        {
            await NativeGesture.Send((kind, _) => { events.Add(kind); if (kind == CoreWebView2MouseEventKind.LeftButtonDown) { throw new InvalidOperationException("down-failure"); } });
        }
        catch (InvalidOperationException exception) { downFailed = exception.Message == "down-failure"; }
        Check(downFailed && events.Last() == CoreWebView2MouseEventKind.LeftButtonUp && events.Count == 4);
        events.Clear();
        var bothPreserved = false;
        try
        {
            await NativeGesture.Send((kind, _) => { events.Add(kind); if (kind != CoreWebView2MouseEventKind.Move) { throw new InvalidOperationException(kind.ToString()); } });
        }
        catch (AggregateException exception) { bothPreserved = exception.InnerExceptions.Count == 2; }
        Check(bothPreserved && events.Count == 4);
        HostGuard.SelfCheck(Check);
        GameDisplay.SelfCheck(Check);
        Check(BrowserHostOptions.Parse(new[] { "--composition-demo", "--background-keepalive" }).BackgroundKeepalive);
        Check(!BrowserHostOptions.Parse(new[] { "--composition-demo" }).BackgroundKeepalive);
        var keepaliveRejected = false;
        try { _ = BrowserHostOptions.Parse(new[] { "--offline-demo", "--background-keepalive" }); }
        catch (ArgumentException) { keepaliveRejected = true; }
        Check(keepaliveRejected);
        // Default-off supervised exploration grid: option truth table, grid contract and gate.
        await ProduceExploreSelfTest.Run(root, Check);
        await DailyNavigationSelfTest.Run(root, Check);
        RepeatableNavigationSelfTest.Run(root, Check);
        Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(new { passed = true, checks = count, real_input_attempts = 0, evidence_directory = root }));
    }
}

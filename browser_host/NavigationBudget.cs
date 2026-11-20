using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>A persistent single attempt shared by all supervised navigation hosts.</summary>
internal sealed class NavigationBudget
{
    public string Path { get; }
    public NavigationBudget(string navigation) : this(DefaultPath(navigation), navigation) { }
    internal static string DefaultPath(string navigation)
    {
        if (DailyNavigationPolicy.IsScroll(navigation))
        { throw new InvalidOperationException("只读滚动必须使用独立预算；不得改变单次点击预算。"); }
        var root = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (root is not null && !File.Exists(System.IO.Path.Combine(root.FullName, "project_policy.json"))) { root = root.Parent; }
        if (root is null) { throw new InvalidOperationException("监督导航必须从本项目启动。"); }
        // Daily shop offers refresh at 05:00 JST: one native attempt per action per game day, never reset.
        if (DailyNavigationPolicy.IsGrade(navigation))
        { return GradePath(root.FullName, navigation, RepeatableNavigationBudget.GameDayOf(DateTimeOffset.UtcNow)); }
        if (DailyNavigationPolicy.IsShop(navigation))
        { return ShopPath(root.FullName, navigation, RepeatableNavigationBudget.GameDayOf(DateTimeOffset.UtcNow)); }
        if (DailyNavigationPolicy.IsAction(navigation) && navigation != "announcement-back")
        { return System.IO.Path.Combine(root.FullName, "dev_assets", "private", "daily_navigation", navigation + ".json"); }
        return System.IO.Path.Combine(root.FullName, "dev_assets", "private", "composition_navigation", navigation == "touch-to-start" ? "start-one-shot.json" : "one-shot.json");
    }
    internal static string GradePath(string root, string navigation, string gameDay) =>
        System.IO.Path.Combine(root, "dev_assets", "private", "daily_grade_native", gameDay, navigation + ".json");
    internal static string ShopPath(string root, string navigation, string gameDay) =>
        System.IO.Path.Combine(root, "dev_assets", "private", "daily_shop_native", gameDay, navigation + ".json");
    internal NavigationBudget(string path, string navigation)
    {
        Path = path;
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        try
        {
            using var file = new FileStream(Path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            Write(file, new Ledger(1, navigation, 1, 0, null, null));
        }
        catch (IOException) when (File.Exists(Path))
        {
            // An existing empty/truncated ledger may represent a crash after a
            // consumed attempt. Never recover it by silently granting a click.
            using var existing = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            _ = Read(existing, navigation);
        }
    }

    private sealed record Ledger(int Version, string Navigation, int MaxAttempts, int Attempts, int? LastPid, string? LastFrameSha256, string? LastNonce = null);
    private static Ledger Read(FileStream file, string navigation)
    {
        file.Position = 0;
        var ledger = JsonSerializer.Deserialize<Ledger>(file) ?? throw new InvalidOperationException("导航预算账本无效。");
        if (ledger.Version != 1 || ledger.Navigation != navigation || ledger.MaxAttempts != 1 || ledger.Attempts is < 0 or > 1)
        { throw new InvalidOperationException("导航预算或本批动作不匹配；禁止创建新预算或重置。"); }
        return ledger;
    }
    private static void Write(FileStream file, Ledger ledger)
    {
        file.Position = 0; file.SetLength(0);
        JsonSerializer.Serialize(file, ledger);
        file.Flush(flushToDisk: true);
    }
    public bool Consume(string navigation, string frameSha256, string? nonce = null)
    {
        using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        var ledger = Read(file, navigation);
        if (ledger.Attempts != 0) { return false; }
        // Persist before the native call: a timeout/crash cannot grant another click.
        Write(file, ledger with { Attempts = 1, LastPid = Environment.ProcessId, LastFrameSha256 = frameSha256, LastNonce = nonce });
        return true;
    }
}

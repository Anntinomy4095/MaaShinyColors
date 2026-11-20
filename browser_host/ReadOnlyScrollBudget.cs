using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost;

/// <summary>Separate bounded wheel ledger; never grants or resets a click budget.</summary>
internal sealed class ReadOnlyScrollBudget
{
    internal const int MaxAttempts = 12;
    public string Path { get; }
    private static readonly JsonSerializerOptions JsonOptions = new() { UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow };
    private sealed record Attempt(string FrameSha256, string Nonce, int Pid);
    private sealed record Ledger(int Version, string Action, int MaxAttempts, int Attempts, Attempt[] History);

    public ReadOnlyScrollBudget(string action) : this(DefaultPath(action), action) { }
    internal static string DefaultPath(string action)
    {
        if (!DailyNavigationPolicy.IsScroll(action)) { throw new InvalidOperationException("未知只读滚动动作。"); }
        var root = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (root is not null && !File.Exists(System.IO.Path.Combine(root.FullName, "project_policy.json"))) { root = root.Parent; }
        if (root is null) { throw new InvalidOperationException("只读滚动必须从本项目启动。"); }
        return System.IO.Path.Combine(root.FullName, "dev_assets", "private", "readonly_scroll", action + ".json");
    }
    internal ReadOnlyScrollBudget(string path, string action)
    {
        if (!DailyNavigationPolicy.IsScroll(action)) { throw new InvalidOperationException("未知只读滚动动作。"); }
        Path = path;
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        try
        {
            using var file = new FileStream(Path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            Write(file, new Ledger(1, action, MaxAttempts, 0, Array.Empty<Attempt>()));
        }
        catch (IOException) when (File.Exists(Path))
        {
            using var existing = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            _ = Read(existing, action);
        }
    }
    private static bool ValidSha(string? value) => value is not null && Regex.IsMatch(value, "\\A[0-9a-f]{64}\\z");
    private static bool ValidNonce(string? value) => value is not null && Regex.IsMatch(value, "\\A[0-9a-f]{32}\\z");
    private static Ledger Read(FileStream file, string action)
    {
        file.Position = 0;
        var ledger = JsonSerializer.Deserialize<Ledger>(file, JsonOptions) ?? throw new InvalidOperationException("只读滚动预算账本无效。");
        if (ledger.Version != 1 || ledger.Action != action || ledger.MaxAttempts != MaxAttempts ||
            ledger.Attempts is < 0 or > MaxAttempts || ledger.History is null || ledger.History.Length != ledger.Attempts ||
            ledger.History.Any(item => item is null || !ValidSha(item.FrameSha256) || !ValidNonce(item.Nonce) || item.Pid <= 0) ||
            ledger.History.Select(item => item.Nonce).Distinct(StringComparer.Ordinal).Count() != ledger.Attempts ||
            ledger.History.Select(item => item.FrameSha256).Distinct(StringComparer.Ordinal).Count() != ledger.Attempts)
        { throw new InvalidOperationException("只读滚动预算不匹配或损坏；禁止重置或恢复预算。"); }
        return ledger;
    }
    private static void Write(FileStream file, Ledger ledger)
    {
        file.Position = 0; file.SetLength(0);
        JsonSerializer.Serialize(file, ledger, JsonOptions);
        file.Flush(flushToDisk: true);
    }
    public bool Consume(string action, string frameSha256, string nonce)
    {
        if (!DailyNavigationPolicy.IsScroll(action) || !ValidSha(frameSha256) || !ValidNonce(nonce))
        { throw new InvalidOperationException("只读滚动动作、帧或单次编号无效。"); }
        using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        var ledger = Read(file, action);
        if (ledger.Attempts >= MaxAttempts || ledger.History.Any(item => item.Nonce == nonce || item.FrameSha256 == frameSha256))
        { return false; }
        // Persist before Move/Wheel: exceptions and timeouts cannot replay this nonce or frame.
        Write(file, ledger with { Attempts = ledger.Attempts + 1,
            History = ledger.History.Append(new Attempt(frameSha256, nonce, Environment.ProcessId)).ToArray() });
        return true;
    }
}

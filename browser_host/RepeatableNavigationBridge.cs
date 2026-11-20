using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

internal sealed record RepeatableNavigationRequest(string Command, string Action, string? Nonce = null, string? FrameSha256 = null,
    int SourceWidth = 0, int SourceHeight = 0, string? ProofPath = null, string? ProofSha256 = null);
internal sealed record RepeatableNavigationResponse(bool ok, bool attempted, string reason, string state, int action_count, int day_count);

/// <summary>Separate opt-in endpoint for bounded non-consuming navigation; fixed points only.</summary>
internal sealed class RepeatableNavigationBridge : IDisposable
{
    internal const string Transport = "webview2-composition-repeatable-navigation";
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    private readonly string _path;
    private readonly string[] _actions;
    private readonly Func<RepeatableNavigationRequest, CancellationToken, Task<RepeatableNavigationResponse>> _handle;

    /// <summary>Training-portal and shop navigation stay behind their separate switches.</summary>
    internal static string[] AllowedActions(bool training, bool shop = false, bool grade = false) => RepeatableNavigationBudget.Actions
        .Where(action => (training || !DailyNavigationPolicy.IsTraining(action)) && (shop || !DailyNavigationPolicy.IsShop(action))
            && (grade || !DailyNavigationPolicy.IsGrade(action))).ToArray();

    internal RepeatableNavigationBridge(string path, RepeatableNavigationBudget budget, bool training,
        Func<RepeatableNavigationRequest, CancellationToken, Task<RepeatableNavigationResponse>> handle, bool shop = false, bool grade = false)
    {
        _path = path; _handle = handle; _actions = AllowedActions(training, shop, grade); _listener.Start();
        try
        {
            using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            JsonSerializer.Serialize(file, new { version = 1, host = "127.0.0.1", port = ((IPEndPoint)_listener.LocalEndpoint).Port,
                token = _token, pid = Environment.ProcessId, input_enabled = true, browser_demo_only = false,
                transport = Transport, ledger_root = budget.LedgerRoot, game_day = budget.CurrentGameDay(),
                per_action_max = RepeatableNavigationBudget.PerActionMax, day_max = RepeatableNavigationBudget.DayMax,
                actions = _actions });
        }
        catch { _listener.Stop(); throw; }
        _ = Serve();
    }

    private static void Keys(JsonElement root, params string[] keys)
    {
        if (!root.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal).SequenceEqual(keys.Order(StringComparer.Ordinal)) ||
            root.EnumerateObject().Select(p => p.Name).Distinct(StringComparer.Ordinal).Count() != keys.Length)
        { throw new InvalidOperationException("可重复导航请求字段白名单变化。"); }
    }

    internal static RepeatableNavigationRequest Parse(JsonElement root, string token, IReadOnlyCollection<string> allowed)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("token", out var auth) || auth.ValueKind != JsonValueKind.String ||
            !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(auth.GetString()!), Encoding.UTF8.GetBytes(token)))
        { throw new InvalidOperationException("可重复导航认证无效。"); }
        var command = root.GetProperty("command").GetString();
        var common = new[] { "token", "command", "action", "nonce", "frame_sha256", "source_width", "source_height" };
        if (command == "repeatable-status") { Keys(root, "token", "command", "action"); }
        else if (command == "repeatable-navigate") { Keys(root, common); }
        else if (command == "repeatable-confirm") { Keys(root, common.Concat(new[] { "proof_path", "proof_sha256" }).ToArray()); }
        else { throw new InvalidOperationException("未知可重复导航命令。"); }
        var action = root.GetProperty("action").GetString();
        if (!RepeatableNavigationBudget.IsRepeatable(action) || !allowed.Contains(action!, StringComparer.Ordinal))
        { throw new InvalidOperationException("该动作不在本宿主可重复导航白名单。"); }
        if (command == "repeatable-status") { return new(command, action!); }
        var nonce = root.GetProperty("nonce").GetString(); var sha = root.GetProperty("frame_sha256").GetString();
        var width = root.GetProperty("source_width").GetInt32(); var height = root.GetProperty("source_height").GetInt32();
        if (!SelectionLoopBudget.Nonce(nonce) || !SelectionLoopBudget.Sha(sha) || Math.Abs((long)width - 1280) > 2 || Math.Abs((long)height - 720) > 2)
        { throw new InvalidOperationException("可重复导航帧、nonce或几何无效。"); }
        if (command == "repeatable-navigate") { return new(command, action!, nonce, sha, width, height); }
        var proofSha = root.GetProperty("proof_sha256").GetString();
        if (!SelectionLoopBudget.Sha(proofSha)) { throw new InvalidOperationException("可重复导航后置证明SHA无效。"); }
        return new(command, action!, nonce, sha, width, height, root.GetProperty("proof_path").GetString(), proofSha);
    }

    private async Task Serve()
    {
        while (!_shutdown.IsCancellationRequested)
        {
            try
            {
                using var client = await _listener.AcceptTcpClientAsync(_shutdown.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_shutdown.Token); timeout.CancelAfter(TimeSpan.FromSeconds(10));
                using var stream = client.GetStream(); var bytes = new List<byte>(); var single = new byte[1]; var complete = false;
                while (bytes.Count < 4096)
                {
                    if (await stream.ReadAsync(single, timeout.Token) == 0) { break; }
                    if (single[0] == (byte)'\n') { complete = true; break; } bytes.Add(single[0]);
                }
                RepeatableNavigationResponse response;
                try
                {
                    if (!complete || bytes.Count >= 4096) { throw new InvalidOperationException("可重复导航请求被截断或过长。"); }
                    using var document = JsonDocument.Parse(bytes.ToArray());
                    response = await _handle(Parse(document.RootElement, _token, _actions), timeout.Token).WaitAsync(timeout.Token);
                }
                catch (OperationCanceledException) { response = new(false, true, "request_timeout_result_unknown_no_retry", "blocked", 0, 0); }
                catch (Exception exception) { response = new(false, false, exception.Message, "blocked", 0, 0); }
                await stream.WriteAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(response) + "\n"), timeout.Token);
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested) { break; }
            catch (Exception) when (!_shutdown.IsCancellationRequested) { }
        }
    }

    public void Dispose()
    { _shutdown.Cancel(); _listener.Stop(); if (File.Exists(_path)) { File.Delete(_path); } _shutdown.Dispose(); }
}

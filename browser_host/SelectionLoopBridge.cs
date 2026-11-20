using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

internal sealed record SelectionLoopRequest(string Command, string? Nonce = null, string? FrameSha256 = null,
    int SourceWidth = 0, int SourceHeight = 0, string? ProofPath = null, string? ProofSha256 = null);
internal sealed record SelectionLoopResponse(bool ok, bool attempted, string reason, string state, int attempts, int max_attempts = SelectionLoopBudget.MaxAttempts);

/// <summary>Independent closed selection endpoint; no daily vocabulary or legacy budget changes.</summary>
internal sealed class SelectionLoopBridge : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    private readonly string _path;
    private readonly Func<SelectionLoopRequest, CancellationToken, Task<SelectionLoopResponse>> _handle;
    internal SelectionLoopBridge(string path, SelectionLoopBudget budget,
        Func<SelectionLoopRequest, CancellationToken, Task<SelectionLoopResponse>> handle)
    {
        _path = path; _handle = handle; _listener.Start();
        try
        {
            using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            JsonSerializer.Serialize(file, new { version = 1, host = "127.0.0.1", port = ((IPEndPoint)_listener.LocalEndpoint).Port,
                token = _token, pid = Environment.ProcessId, input_enabled = true, browser_demo_only = false,
                transport = "webview2-composition-training-selection-loop", action = SelectionLoopBudget.Action,
                budget_path = budget.Path, resolution_path = budget.ResolutionPath, resolution_sha256 = budget.ResolutionSha256,
                max_attempts = SelectionLoopBudget.MaxAttempts });
        }
        catch { _listener.Stop(); throw; }
        _ = Serve();
    }
    internal static SelectionLoopRequest Parse(JsonElement root, string token)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("token", out var auth) || auth.ValueKind != JsonValueKind.String ||
            !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(auth.GetString()!), Encoding.UTF8.GetBytes(token)))
        { throw new InvalidOperationException("选卡循环认证无效。"); }
        var command = root.GetProperty("command").GetString();
        if (command == "selection-loop-status")
        { SelectionLoopBudget.Keys(root, "token", "command"); return new(command); }
        var common = new[] { "token", "command", "action", "nonce", "frame_sha256", "source_width", "source_height" };
        if (command == "selection-loop-select") { SelectionLoopBudget.Keys(root, common); }
        else if (command == "selection-loop-confirm") { SelectionLoopBudget.Keys(root, common.Concat(new[] { "proof_path", "proof_sha256" }).ToArray()); }
        else { throw new InvalidOperationException("未知选卡循环命令。"); }
        var action = root.GetProperty("action").GetString(); var nonce = root.GetProperty("nonce").GetString(); var sha = root.GetProperty("frame_sha256").GetString();
        var width = root.GetProperty("source_width").GetInt32(); var height = root.GetProperty("source_height").GetInt32();
        if (action != SelectionLoopBudget.Action || !SelectionLoopBudget.Nonce(nonce) || !SelectionLoopBudget.Sha(sha) ||
            Math.Abs((long)width-1280) > 2 || Math.Abs((long)height-720) > 2)
        { throw new InvalidOperationException("选卡动作、帧、nonce或几何无效。"); }
        if (command == "selection-loop-select") { return new(command, nonce, sha, width, height); }
        var proofSha = root.GetProperty("proof_sha256").GetString();
        if (!SelectionLoopBudget.Sha(proofSha)) { throw new InvalidOperationException("选卡后置证明SHA无效。"); }
        return new(command, nonce, sha, width, height, root.GetProperty("proof_path").GetString(), proofSha);
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
                SelectionLoopResponse response;
                try
                {
                    if (!complete || bytes.Count >= 4096) { throw new InvalidOperationException("选卡请求被截断或过长。"); }
                    using var document = JsonDocument.Parse(bytes.ToArray());
                    response = await _handle(Parse(document.RootElement, _token), timeout.Token).WaitAsync(timeout.Token);
                }
                catch (OperationCanceledException) { response = new(false, true, "request_timeout_result_unknown_no_retry", "blocked", 1); }
                catch (Exception exception) { response = new(false, false, exception.Message, "blocked", 1); }
                await stream.WriteAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(response) + "\n"), timeout.Token);
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested) { break; }
            catch (Exception) when (!_shutdown.IsCancellationRequested) { }
        }
    }
    public void Dispose()
    { _shutdown.Cancel(); _listener.Stop(); if (File.Exists(_path)) { File.Delete(_path); } _shutdown.Dispose(); }
}

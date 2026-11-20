using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// Separate opt-in endpoint (<c>--enable-pipeline-input</c>) for the Maa custom controller. The session file carries
/// the per-run spend-grant secret for the Python Agent only; it is deleted when the host closes. Besides
/// <c>pipeline-click</c> it accepts the launcher's <c>pipeline-run-begin</c> handshake (named in the session file as
/// <c>run_begin_command</c>), which only asks the gate to open a new run window (<see cref="PipelineInputGate.BeginRun"/>);
/// it never sends input.
/// </summary>
internal sealed class PipelineInputBridge : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    private readonly string _path;
    private readonly Func<PipelineClickRequest, CancellationToken, Task<NavigationResult>> _handle;
    private readonly PipelineActionTable _table;
    private readonly ProduceExploreGrid? _explore;
    private readonly PipelineInputGate _gate;

    internal PipelineInputBridge(string path, PipelineInputGate gate, Func<PipelineClickRequest, CancellationToken, Task<NavigationResult>> handle)
    {
        _path = path; _handle = handle; _table = gate.Table; _explore = gate.Explore; _gate = gate;
        _listener.Start();
        try
        {
            using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            JsonSerializer.Serialize(file, new
            {
                version = PipelineInputPolicy.ProtocolVersion, frame_binding_protocol = PipelineInputPolicy.FrameBindingProtocol,
                host = "127.0.0.1", port = ((IPEndPoint)_listener.LocalEndpoint).Port, token = _token,
                pid = Environment.ProcessId, input_enabled = true, browser_demo_only = false,
                transport = PipelineInputPolicy.Transport, actions = gate.Table.Ids,
                consuming_actions = gate.Table.ConsumingIds, action_table_sha256 = gate.Table.Sha256, spend_secret = gate.SecretHex,
                audit_root = gate.AuditRoot, run_begin_command = PipelineInputPolicy.RunBeginCommand,
                appeal_timing_protocol = AppealTiming.Protocol, hold_protocol = PipelineInputPolicy.HoldProtocol,
                drag_protocol = PipelineInputPolicy.DragProtocol,
                limits = new
                {
                    min_interval_ms = gate.Limits.MinIntervalMs, interval_jitter_max_ms = gate.Limits.IntervalJitterMaxMs,
                    per_action_per_minute = gate.Limits.PerActionPerMinute, run_max = gate.Limits.RunMax,
                    run_spend_max = gate.Limits.RunSpendMax, grant_max_ttl_ms = gate.Limits.GrantMaxTtlMs,
                    session_max_ms = gate.Limits.SessionMaxMs,
                },
            });
        }
        catch { _listener.Stop(); throw; }
        _ = Serve();
    }

    private async Task Serve()
    {
        while (!_shutdown.IsCancellationRequested)
        {
            try
            {
                using var client = await _listener.AcceptTcpClientAsync(_shutdown.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_shutdown.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(10));
                using var stream = client.GetStream();
                var bytes = new List<byte>(); var single = new byte[1]; var complete = false;
                while (bytes.Count < 4096)
                {
                    if (await stream.ReadAsync(single, timeout.Token) == 0) { break; }
                    if (single[0] == (byte)'\n') { complete = true; break; }
                    bytes.Add(single[0]);
                }
                NavigationResult result;
                try
                {
                    if (!complete || bytes.Count >= 4096) { throw new InvalidOperationException("Pipeline 请求过长或被截断。"); }
                    using var document = JsonDocument.Parse(bytes.ToArray());
                    if (PipelineInputPolicy.IsRunBegin(document.RootElement))
                    {
                        // Opens a new run window only (thread-safe gate state); no frame, no input.
                        result = _gate.BeginRun(PipelineInputPolicy.ParseRunBegin(document.RootElement, _token));
                    }
                    else
                    {
                        var request = PipelineInputPolicy.Parse(document.RootElement, _token, _table, _explore);
                        result = await _handle(request, timeout.Token).WaitAsync(timeout.Token);
                    }
                }
                catch (OperationCanceledException) { result = new(false, true, "request_timeout_result_unknown_no_retry"); }
                catch (Exception exception) { result = new(false, false, exception.Message); }
                await stream.WriteAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(result) + "\n"), timeout.Token);
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested) { break; }
            catch (Exception) when (!_shutdown.IsCancellationRequested) { }
        }
    }

    public void Dispose()
    { _shutdown.Cancel(); _listener.Stop(); if (File.Exists(_path)) { File.Delete(_path); } _shutdown.Dispose(); }
}

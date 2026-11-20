using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Separate opt-in endpoint with fixed, individually bounded actions.</summary>
internal sealed class SupervisedDailyBridge : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
    private readonly string _path;
    private readonly string[] _allowedActions;
    private readonly Func<DailyNavigationRequest, CancellationToken, Task<NavigationResult>> _navigate;
    internal const string DailyTransport = "webview2-composition-daily";
    internal const string GradeTransport = "webview2-composition-grade";
    internal SupervisedDailyBridge(string path, Func<DailyNavigationRequest, CancellationToken, Task<NavigationResult>> navigate, bool training = false, bool scroll = false, bool shop = false)
        : this(path, navigate, DailyNavigationPolicy.AllowedActions(training, scroll, shop), DailyTransport) { }
    /// <summary>The Grade Fes endpoint: its own session file, transport and closed action list.</summary>
    internal static SupervisedDailyBridge Grade(string path, Func<DailyNavigationRequest, CancellationToken, Task<NavigationResult>> navigate) =>
        new(path, navigate, DailyNavigationPolicy.GradeActions, GradeTransport);
    private SupervisedDailyBridge(string path, Func<DailyNavigationRequest, CancellationToken, Task<NavigationResult>> navigate, string[] actions, string transport)
    {
        _path = path; _navigate = navigate;
        if (transport != DailyTransport && transport != GradeTransport) { throw new InvalidOperationException("未知日常端点类型。"); }
        if ((transport == GradeTransport) != actions.All(DailyNavigationPolicy.IsGrade) || actions.Length == 0)
        { throw new InvalidOperationException("Grade 端点只允许 Grade 动作，日常端点不含 Grade 动作。"); }
        _allowedActions = actions.ToArray();
        _listener.Start();
        try
        {
            using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            var advertised = _allowedActions;
            JsonSerializer.Serialize(file, new { version = 1, host = "127.0.0.1",
                port = ((IPEndPoint)_listener.LocalEndpoint).Port, token = _token,
                pid = Environment.ProcessId, input_enabled = true, browser_demo_only = false,
                transport, actions = advertised,
                budget_paths = advertised.ToDictionary(action => action, action => DailyNavigationPolicy.IsScroll(action)
                    ? ReadOnlyScrollBudget.DefaultPath(action) : NavigationBudget.DefaultPath(action)) });
        }
        catch { _listener.Stop(); throw; }
        _ = ServeAsync();
    }
    private async Task ServeAsync()
    {
        while (!_shutdown.IsCancellationRequested)
        {
            try
            {
                using var client = await _listener.AcceptTcpClientAsync(_shutdown.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_shutdown.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(10));
                using var stream = client.GetStream();
                var buffer = new List<byte>(); var one = new byte[1];
                var complete = false;
                while (buffer.Count < 1024)
                {
                    if (await stream.ReadAsync(one, timeout.Token) == 0) { break; }
                    if (one[0] == (byte)'\n') { complete = true; break; }
                    buffer.Add(one[0]);
                }
                NavigationResult result;
                try
                {
                    if (!complete || buffer.Count >= 1024) { throw new InvalidOperationException("日常请求过长或被截断。"); }
                    using var document = JsonDocument.Parse(buffer.ToArray());
                    var request = DailyNavigationPolicy.Parse(document.RootElement, _token);
                    if (!_allowedActions.Contains(request.Action, StringComparer.Ordinal))
                    { throw new InvalidOperationException("本宿主未独立启用该训练、只读滚动或商店动作。"); }
                    result = await _navigate(request, timeout.Token).WaitAsync(timeout.Token);
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
    {
        _shutdown.Cancel(); _listener.Stop();
        if (File.Exists(_path)) { File.Delete(_path); }
        _shutdown.Dispose();
    }
}

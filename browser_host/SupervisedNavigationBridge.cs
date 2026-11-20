using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

internal sealed record NavigationResult(bool ok, bool attempted, string reason);

/// <summary>One fixed supervised navigation; no arbitrary pointer, keyboard or page API.</summary>
internal sealed class SupervisedNavigationBridge : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
    private readonly string _path;
    private readonly string _navigation;
    private readonly Func<string, int, int, Task<NavigationResult>> _navigate;
    public SupervisedNavigationBridge(string path, string navigation, string budgetPath,
        Func<string, int, int, Task<NavigationResult>> navigate)
    {
        _path = path; _navigation = navigation; _navigate = navigate;
        _listener.Start();
        try
        {
            using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            JsonSerializer.Serialize(file, new { version = 1, host = "127.0.0.1",
                port = ((IPEndPoint)_listener.LocalEndpoint).Port, token = _token,
                pid = Environment.ProcessId, input_enabled = true, browser_demo_only = false,
                transport = "webview2-composition-navigation", navigation, budget_path = budgetPath });
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
                while (buffer.Count < 1024)
                {
                    if (await stream.ReadAsync(one, timeout.Token) == 0) { break; }
                    if (one[0] == (byte)'\n') { break; }
                    buffer.Add(one[0]);
                }
                NavigationResult result;
                try
                {
                    using var request = JsonDocument.Parse(buffer.ToArray());
                    var root = request.RootElement;
                    var names = root.EnumerateObject().Select(item => item.Name).Order().ToArray();
                    if (buffer.Count >= 1024 || !names.SequenceEqual(new[] { "command", "frame_sha256", "navigation", "source_height", "source_width", "token" }) ||
                        root.GetProperty("command").GetString() != "navigate" || root.GetProperty("navigation").GetString() != _navigation ||
                        !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(root.GetProperty("token").GetString() ?? ""), Encoding.UTF8.GetBytes(_token)))
                    { throw new InvalidOperationException("监督导航请求与本批固定接口不匹配。"); }
                    result = await _navigate(root.GetProperty("frame_sha256").GetString() ?? "",
                        root.GetProperty("source_width").GetInt32(), root.GetProperty("source_height").GetInt32()).WaitAsync(timeout.Token);
                }
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

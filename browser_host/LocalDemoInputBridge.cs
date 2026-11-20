using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Separate, explicitly enabled input endpoint for the owned page only.</summary>
internal sealed class LocalDemoInputBridge : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
    private readonly Func<Task<bool>> _click;
    private readonly string _path;

    public LocalDemoInputBridge(string path, Func<Task<bool>> click)
    {
        _path = path;
        _click = click;
        _listener.Start();
        try
        {
            using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            JsonSerializer.Serialize(file, new { version = 1, host = "127.0.0.1",
                port = ((IPEndPoint)_listener.LocalEndpoint).Port,
                token = _token, pid = Environment.ProcessId, input_enabled = true,
                browser_demo_only = true, transport = "webview2-composition" });
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
                var buffer = new List<byte>();
                var one = new byte[1];
                while (buffer.Count < 1024)
                {
                    if (await stream.ReadAsync(one, timeout.Token) == 0) { break; }
                    if (one[0] == (byte)'\n') { break; }
                    buffer.Add(one[0]);
                }
                object response;
                try
                {
                    using var request = JsonDocument.Parse(buffer.ToArray());
                    var root = request.RootElement;
                    if (buffer.Count >= 1024 || root.EnumerateObject().Count() != 2 ||
                        root.GetProperty("command").GetString() != "claim-demo" ||
                        !CryptographicOperations.FixedTimeEquals(
                            Encoding.UTF8.GetBytes(root.GetProperty("token").GetString() ?? ""),
                            Encoding.UTF8.GetBytes(_token)))
                    { throw new InvalidOperationException("本地输入请求不符合固定实验接口。"); }
                    response = new { ok = await _click().WaitAsync(timeout.Token) };
                }
                catch (Exception exception) { response = new { ok = false, error = exception.Message }; }
                var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(response) + "\n");
                await stream.WriteAsync(bytes, timeout.Token);
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested) { break; }
            catch (Exception) when (!_shutdown.IsCancellationRequested) { }
        }
    }

    public void Dispose()
    {
        _shutdown.Cancel();
        _listener.Stop();
        if (File.Exists(_path)) { File.Delete(_path); }
        _shutdown.Dispose();
    }
}

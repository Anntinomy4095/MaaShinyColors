using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Loopback-only, authenticated, read-only access to the WebView image.</summary>
internal sealed class CaptureBridge : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly Func<Task<byte[]>> _capture;
    private readonly Func<Task<object>> _status;
    private readonly string _sessionFile;
    private readonly string _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));

    public CaptureBridge(string sessionFile, Func<Task<byte[]>> capture, Func<Task<object>> status)
    {
        _sessionFile = sessionFile;
        _capture = capture;
        _status = status;
        // The first bridge of a session: files a killed host left behind would make every CreateNew below fail.
        StaleSession.Clear(sessionFile);
        _listener.Start();
        var pendingFile = sessionFile + "." + _token[..12] + ".tmp";
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(sessionFile)!);
            var session = new
            {
                version = 1,
                host = "127.0.0.1",
                port = ((IPEndPoint)_listener.LocalEndpoint).Port,
                token = _token,
                pid = Environment.ProcessId,
                input_enabled = false,
            };
            // A unique session file prevents one host from replacing another host's endpoint.
            using (var file = new FileStream(pendingFile, FileMode.CreateNew, FileAccess.Write))
            {
                JsonSerializer.Serialize(file, session);
            }
            File.Move(pendingFile, sessionFile);
        }
        catch
        {
            _listener.Stop();
            if (File.Exists(pendingFile)) { File.Delete(pendingFile); }
            throw;
        }
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
                await HandleAsync(client.GetStream(), timeout.Token);
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested) { break; }
            catch (Exception) when (!_shutdown.IsCancellationRequested)
            {
                // A malformed request or disconnected observer must not close the browser.
            }
        }
    }

    private async Task HandleAsync(NetworkStream stream, CancellationToken cancellation)
    {
        var line = new List<byte>();
        var one = new byte[1];
        while (line.Count < 1024)
        {
            if (await stream.ReadAsync(one, cancellation) == 0) { return; }
            if (one[0] == (byte)'\n') { break; }
            line.Add(one[0]);
        }
        if (line.Count == 1024) { return; }
        byte[] image = [];
        object response;
        try
        {
            using var request = JsonDocument.Parse(line.ToArray());
            var supplied = request.RootElement.GetProperty("token").GetString() ?? "";
            if (!CryptographicOperations.FixedTimeEquals(
                Encoding.UTF8.GetBytes(supplied), Encoding.UTF8.GetBytes(_token)))
            {
                throw new InvalidOperationException("会话验证失败。");
            }
            var command = request.RootElement.GetProperty("command").GetString();
            if (command == "status")
            {
                response = new { ok = true, status = await _status().WaitAsync(cancellation), image_length = 0 };
            }
            else if (command == "screencap")
            {
                image = await _capture().WaitAsync(cancellation);
                response = new { ok = true, image_length = image.Length };
            }
            else
            {
                throw new InvalidOperationException("截图桥只接受 status / screencap，不接受输入。");
            }
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            image = [];
            response = new { ok = false, error = exception.Message, image_length = 0 };
        }
        var header = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(response) + "\n");
        await stream.WriteAsync(header, cancellation);
        if (image.Length > 0) { await stream.WriteAsync(image, cancellation); }
    }

    public void Dispose()
    {
        _shutdown.Cancel();
        _listener.Stop();
        if (File.Exists(_sessionFile)) { File.Delete(_sessionFile); }
    }
}

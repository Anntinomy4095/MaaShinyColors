using System.Drawing;
using System.Diagnostics;
using System.Text.Json;
using System.Runtime.InteropServices;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace MaaShinyColors.BrowserHost;

internal sealed class BrowserHostForm : Form
{
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);

    protected override bool ShowWithoutActivation => true;
    private readonly BrowserHostOptions _options;
    private readonly WebView2 _webView;
    private readonly ToolStripStatusLabel _status = new("正在启动浏览器…");
    private readonly ToolStripButton _captureButton = new("保存截图");
    private readonly ToolStripButton _dailyButton = new("日常识别（本地演示）");
    private readonly ToolStripButton _produceButton = new("Produce 建议（样例）");
    private readonly string _projectRoot;
    private CaptureBridge? _bridge;
    private string? _sessionFile;
    private Process? _taskProcess;
    private bool _initialized;
    private bool _pageReady;
    private bool _capturing;
    private string _pageResult = "starting";
    private int? _sourceCaptureWidth;
    private int? _sourceCaptureHeight;

    public BrowserHostForm(BrowserHostOptions options)
    {
        _options = options;
        _projectRoot = FindProjectRoot();

        Text = options.WindowTitle;
        Name = "MaaShinyColorsBrowserHost";
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        MinimizeBox = true;
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size(
            BrowserHostOptions.ViewportWidth,
            BrowserHostOptions.ViewportHeight + 68);
        BackColor = Color.Black;

        _webView = new WebView2
        {
            Location = new Point(0, 44),
            Size = new Size(BrowserHostOptions.ViewportWidth, BrowserHostOptions.ViewportHeight),
            DefaultBackgroundColor = Color.Black,
            Margin = Padding.Empty,
        };
        Controls.Add(_webView);

        var toolbar = new ToolStrip { Dock = DockStyle.Top, AutoSize = false, Height = 44 };
        var reset = new ToolStripButton(options.BrowserDemo ? "重置本地页面" : "刷新页面");
        reset.Click += (_, _) => { if (_pageReady) { _webView.Reload(); } };
        _captureButton.Enabled = false;
        _captureButton.Click += async (_, _) => await SaveCaptureAsync();
        _dailyButton.Enabled = false;
        _dailyButton.Click += async (_, _) => await RunTaskAsync("daily-observe");
        _produceButton.Click += async (_, _) => await RunTaskAsync("produce-sample");
        var stop = new ToolStripButton("停止任务");
        stop.Click += (_, _) => StopTask();
        toolbar.Items.AddRange([reset, _captureButton, new ToolStripSeparator(),
            _dailyButton, _produceButton, stop]);
        Controls.Add(toolbar);
        var statusBar = new StatusStrip { Dock = DockStyle.Bottom, AutoSize = false, Height = 24 };
        statusBar.Items.Add(_status);
        Controls.Add(statusBar);

        Shown += OnShown;
        FormClosing += (_, _) =>
        {
            try { StopTask(); }
            finally { _bridge?.Dispose(); }
        };
    }

    private async void OnShown(object? sender, EventArgs eventArgs)
    {
        if (_initialized)
        {
            return;
        }

        _initialized = true;
        try
        {
            Directory.CreateDirectory(_options.UserDataFolder);
            var environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: _options.UserDataFolder,
                options: new CoreWebView2EnvironmentOptions { AdditionalBrowserArguments = HostGuard.RenderArguments, Language = HostGuard.GameLanguage });
            await _webView.EnsureCoreWebView2Async(environment);
            ConfigureWebView(_webView.CoreWebView2, _options.BrowserDemo);
            _webView.CoreWebView2.NavigationStarting += (_, args) =>
            {
                _pageReady = false;
                _pageResult = args.Cancel ? "navigation_blocked" : "loading";
                _captureButton.Enabled = false;
                _dailyButton.Enabled = false;
            };
            _webView.CoreWebView2.NavigationCompleted += (_, args) =>
            {
                _pageReady = args.IsSuccess;
                _pageResult = args.IsSuccess ? "loaded" : args.WebErrorStatus.ToString();
                _captureButton.Enabled = _pageReady;
                _dailyButton.Enabled = _pageReady && _options.BrowserDemo && _taskProcess is null;
                _status.Text = args.IsSuccess
                    ? "观察模式 · Maa 输入关闭 · 识别画布 1280×720"
                    : $"页面加载失败：{args.WebErrorStatus}";
            };
            _webView.ZoomFactor = 1.0;
            if (_options.BrowserDemo)
            {
                _webView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    "sc-local.test", Path.Combine(AppContext.BaseDirectory, "demo"),
                    CoreWebView2HostResourceAccessKind.Deny);
            }
            _sessionFile = _options.BridgeSession ?? Path.Combine(
                _options.UserDataFolder, $"bridge-{Environment.ProcessId}.json");
            _bridge = new CaptureBridge(_sessionFile,
                () => OnUiAsync(CaptureAsync),
                () => OnUiAsync(() => Task.FromResult<object>(new
                {
                    ready = _pageReady && WindowState != FormWindowState.Minimized,
                    page_ready = _pageReady,
                    minimized = WindowState == FormWindowState.Minimized,
                    page_result = _pageResult,
                    window_visible = Visible,
                    webview_visible = _webView.Visible,
                    source_capture_width = _sourceCaptureWidth,
                    source_capture_height = _sourceCaptureHeight,
                    browser_demo = _options.BrowserDemo,
                    input_enabled = false,
                    width = _webView.Width,
                    height = _webView.Height,
                })));
            _webView.CoreWebView2.Navigate(_options.BrowserDemo
                ? "https://sc-local.test/index.html" : _options.StartUri.AbsoluteUri);
            if (_options.BrowserDemo)
            {
                // WebView2 does not finish navigation while its host is never shown.
                // SW_SHOWNOACTIVATE keeps this local test window from taking focus.
                ShowWindow(Handle, 4);
            }
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                this,
                $"WebView2 初始化失败：{exception.Message}",
                _options.WindowTitle,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            Close();
        }
    }

    private Task<T> OnUiAsync<T>(Func<Task<T>> action)
    {
        var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        try
        {
            BeginInvoke(async () =>
            {
                try { completion.SetResult(await action()); }
                catch (Exception exception) { completion.SetException(exception); }
            });
        }
        catch (Exception exception) { completion.SetException(exception); }
        return completion.Task;
    }

    private async Task<byte[]> CaptureAsync()
    {
        if (!_pageReady || WindowState == FormWindowState.Minimized || _capturing)
        {
            throw new InvalidOperationException("页面未就绪、窗口最小化或截图正在进行。");
        }
        _capturing = true;
        try
        {
            using var stream = new MemoryStream();
            await _webView.CoreWebView2.CapturePreviewAsync(
                CoreWebView2CapturePreviewImageFormat.Png, stream);
            stream.Position = 0;
            using var image = Image.FromStream(stream);
            _sourceCaptureWidth = image.Width;
            _sourceCaptureHeight = image.Height;
            if (image.Width != 1280 || image.Height != 720)
            {
                // WebView2 rasterizes fractional DPI boundaries (e.g. 1282×721 at 150%).
                // Only tolerate rounding within two pixels; a changed layout must fail.
                if (Math.Abs(image.Width - 1280) > 2 || Math.Abs(image.Height - 720) > 2)
                {
                    throw new InvalidOperationException($"截图尺寸不符合基准：{image.Width}×{image.Height}");
                }
                using var normalized = new Bitmap(1280, 720, System.Drawing.Imaging.PixelFormat.Format24bppRgb);
                using (var graphics = Graphics.FromImage(normalized))
                {
                    graphics.DrawImage(image, new Rectangle(0, 0, 1280, 720));
                }
                using var output = new MemoryStream();
                normalized.Save(output, System.Drawing.Imaging.ImageFormat.Png);
                return output.ToArray();
            }
            return stream.ToArray();
        }
        finally { _capturing = false; }
    }

    private async Task SaveCaptureAsync()
    {
        try
        {
            var directory = Path.Combine(_projectRoot, "dev_assets", "private", "browser_captures");
            Directory.CreateDirectory(directory);
            var path = Path.Combine(directory, $"{DateTime.Now:yyyyMMdd-HHmmss-fff}.png");
            await File.WriteAllBytesAsync(path, await CaptureAsync());
            _status.Text = $"截图已保存：{path}";
        }
        catch (Exception exception) { _status.Text = $"截图失败：{exception.Message}"; }
    }

    private async Task RunTaskAsync(string task)
    {
        if (_taskProcess is not null) { return; }
        var python = Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe");
        var runner = Path.Combine(_projectRoot, "tools", "run_browser_task.py");
        if (!File.Exists(python) || !File.Exists(runner) || _sessionFile is null)
        {
            _status.Text = "开发任务环境缺失，请先运行 scripts/bootstrap.ps1。";
            return;
        }
        try
        {
            _dailyButton.Enabled = false;
            _produceButton.Enabled = false;
            var start = new ProcessStartInfo(python)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                WorkingDirectory = _projectRoot,
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding = System.Text.Encoding.UTF8,
            };
            start.Environment["PYTHONIOENCODING"] = "utf-8";
            start.Environment["PYTHONUTF8"] = "1";
            start.ArgumentList.Add(runner);
            start.ArgumentList.Add("--session"); start.ArgumentList.Add(_sessionFile);
            start.ArgumentList.Add("--task"); start.ArgumentList.Add(task);
            using var process = new Process { StartInfo = start };
            process.Start();
            _taskProcess = process;
            _status.Text = "任务运行中 · 不发送输入";
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await process.WaitForExitAsync(timeout.Token);
            var stdout = await output;
            var stderr = await error;
            if (process.ExitCode == 0)
            {
                using var result = JsonDocument.Parse(stdout);
                _status.Text = result.RootElement.GetProperty("summary").GetString() ?? "任务完成。";
            }
            else { _status.Text = "任务停止/失败：" + stderr.Trim(); }
        }
        catch (Exception exception)
        {
            StopTask();
            _status.Text = $"任务失败：{exception.Message}";
        }
        finally
        {
            _taskProcess = null;
            _produceButton.Enabled = true;
            _dailyButton.Enabled = _pageReady && _options.BrowserDemo;
        }
    }

    private void StopTask()
    {
        var process = _taskProcess;
        if (process is null) { return; }
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                _status.Text = "任务已停止。";
            }
        }
        catch (InvalidOperationException) { _status.Text = "任务已结束。"; }
        catch (System.ComponentModel.Win32Exception exception)
        {
            _status.Text = $"停止任务失败：{exception.Message}";
        }
    }

    private static string FindProjectRoot()
    {
        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            if (File.Exists(Path.Combine(current.FullName, "project_policy.json"))) { return current.FullName; }
            current = current.Parent;
        }
        return AppContext.BaseDirectory;
    }

    private static void ConfigureWebView(CoreWebView2 core, bool browserDemo)
    {
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.AreDefaultScriptDialogsEnabled = true;
        core.Settings.AreDefaultContextMenusEnabled = true;
        core.Settings.AreBrowserAcceleratorKeysEnabled = true;
        core.Settings.IsZoomControlEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsSwipeNavigationEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.Settings.IsGeneralAutofillEnabled = false;

        core.NavigationStarting += (_, eventArgs) =>
        {
            if (!Uri.TryCreate(eventArgs.Uri, UriKind.Absolute, out var uri))
            {
                eventArgs.Cancel = true;
                return;
            }

            if (uri.Scheme != Uri.UriSchemeHttps &&
                uri.Scheme != "about")
            {
                eventArgs.Cancel = true;
            }
            if (browserDemo && uri.Host != "sc-local.test") { eventArgs.Cancel = true; }
        };
        if (browserDemo) { core.NewWindowRequested += (_, args) => args.Handled = true; }
    }
}

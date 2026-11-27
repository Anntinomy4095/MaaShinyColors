using System.Globalization;
using System.IO.Compression;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// 设置 → 关于 / 问题反馈 / 更新 / 通知测试 / 紧急停止热键 (2026-10-15, the P0 items).
/// The page only names an operation; every path, URL and file list is fixed here.
/// <list type="bullet">
/// <item>问题包: recent text logs from dev_assets/private (run records, input log, W.I.N.G. decisions, settings) zipped
/// into dev_assets/private/issue-reports/ and shown in Explorer, plus the newest failure screenshots
/// (run_logs/diagnostics/failures, the frame a task stopped on). No recordings, WebView2 profile (login), other
/// captures or game assets; local paths and the Windows user name are replaced in text. Nothing is uploaded.</item>
/// <item>检查更新: GitHub releases of interface.json's <c>github</c> (owner/repo) only, on request. 下载更新 / 重启并更新 are
/// separate clicks (UiSupportUpdate.cs).</item>
/// <item>链接: repo / issues / releases of the same repo, opened in the default browser.</item>
/// </list>
/// The status snapshot (<see cref="Snapshot"/>) rides on status.update, so a background update check needs no extra event.
/// </summary>
internal sealed partial class UiSupport
{
    internal const string StopHotkeyText = "Ctrl + Alt + Shift + S";
    private const long MaxFileBytes = 4L << 20;
    private const int MaxFailureShots = 10;
    private static readonly HttpClient Http = CreateHttp();
    private static readonly string[] TextSuffixes = { ".json", ".jsonl", ".log", ".txt" };

    private readonly string _root;
    private readonly string _private;
    private readonly object _gate = new();
    private JsonObject _update = new() { ["state"] = "idle" };
    private string? _lastReport;

    /// <summary>{enabled, registered}; null when the window has no hotkey (preview / self-test).</summary>
    internal Func<JsonObject>? Hotkey { get; set; }
    /// <summary>Turns the stop hotkey on/off and persists it; returns the new hotkey state.</summary>
    internal Func<bool, JsonObject>? SetHotkey { get; set; }
    internal Action<string, string, ToolTipIcon>? Notify { get; set; }

    internal UiSupport(string root)
    {
        _root = Path.GetFullPath(root);
        _private = Path.Combine(_root, "dev_assets", "private");
        _lastApply = LastApply();
    }

    private readonly JsonObject? _lastApply;

    [GeneratedRegex("^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")] private static partial Regex RepoPattern();

    private static HttpClient CreateHttp()
    {
        var client = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("MaaShinyColors", "1.0"));
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        return client;
    }

    private JsonObject? InterfaceDocument()
    {
        try
        {
            var text = File.ReadAllText(Path.Combine(_root, "assets", "interface.json"));
            return JsonNode.Parse(text, documentOptions: new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true }) as JsonObject;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException) { return null; }
    }

    private string? Repo() => InterfaceDocument()?["github"] is JsonValue value && value.TryGetValue<string>(out var text) ? RepoName(text) : null;

    /// <summary>"owner/repo" from interface.json github: the ProjectInterface repository URL, or a bare "owner/repo".</summary>
    internal static string? RepoName(string text)
    {
        var repo = text.Trim();
        if (repo.StartsWith("https://github.com/", StringComparison.OrdinalIgnoreCase)) { repo = repo["https://github.com/".Length..].TrimEnd('/'); }
        if (repo.EndsWith(".git", StringComparison.OrdinalIgnoreCase)) { repo = repo[..^4]; }
        return RepoPattern().IsMatch(repo) ? repo : null;
    }

    internal string Version() => InterfaceDocument()?["version"] is JsonValue value && value.TryGetValue<string>(out var version) ? version : "—";

    /// <summary>关于: version, build (package manifest or source checkout), links, licences, hotkey and the last update check.</summary>
    internal JsonObject Info()
    {
        var build = new JsonObject { ["kind"] = "source" };
        try
        {
            var manifest = Path.Combine(_root, "PACKAGE_MANIFEST.json");
            if (File.Exists(manifest) && JsonNode.Parse(File.ReadAllText(manifest)) is JsonObject package)
            {
                build = new JsonObject
                {
                    ["kind"] = "package",
                    ["built_utc"] = package["built_utc"]?.DeepClone(),
                    ["commit"] = package["source"]?["commit"]?.DeepClone(),
                    ["dirty"] = package["source"]?["dirty"]?.DeepClone(),
                };
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException) { }
        var repo = Repo();
        return new JsonObject
        {
            ["version"] = Version(),
            ["build"] = build,
            ["repo"] = repo,
            ["links"] = repo is null ? new JsonArray() : new JsonArray("repo", "issues", "releases"),
            ["licenses"] = Licenses(),
            ["hotkey_keys"] = StopHotkeyText,
        };
    }

    /// <summary>Third-party components shipped with the package (README.md lists them with their sources).</summary>
    private static JsonArray Licenses() => new(
        new JsonObject { ["name"] = "MaaShinyColors（基于 MaaPracticeBoilerplate）", ["license"] = "MIT" },
        new JsonObject { ["name"] = "MaaFramework（maafw）", ["license"] = "LGPL-3.0" },
        new JsonObject { ["name"] = "MaaCommonAssets OCR 模型（PaddleOCR）", ["license"] = "Apache-2.0" },
        new JsonObject { ["name"] = "Microsoft Edge WebView2 SDK", ["license"] = "BSD-3-Clause" },
        new JsonObject { ["name"] = ".NET 运行时", ["license"] = "MIT" },
        new JsonObject { ["name"] = "CPython 3.14", ["license"] = "PSF-2.0" },
        new JsonObject { ["name"] = "NumPy / SciPy", ["license"] = "BSD-3-Clause" },
        new JsonObject { ["name"] = "Pillow", ["license"] = "MIT-CMU" },
        new JsonObject { ["name"] = "StrEnum", ["license"] = "MIT" });

    /// <summary>Status snapshot: hotkey state, last update check, last issue report file name.</summary>
    internal JsonObject Snapshot()
    {
        lock (_gate)
        {
            return new JsonObject
            {
                ["hotkey"] = Hotkey?.Invoke() ?? new JsonObject { ["enabled"] = false, ["registered"] = false, ["supported"] = false },
                ["update"] = _update.DeepClone(),
                ["last_apply"] = _lastApply?.DeepClone(),
                ["package"] = IsPackage,
                ["last_report"] = _lastReport,
            };
        }
    }

    /// <summary>One page request; returns (result, null) or (null, refusal text).</summary>
    internal (JsonObject? Result, string? Error) Handle(string op, JsonElement parameters)
    {
        switch (op)
        {
            case "info":
                return (Info(), null);
            case "open_logs":
                Directory.CreateDirectory(_private);
                var logs = Path.Combine(_private, "ui", "runs");
                ShellOpen.Folder(Directory.Exists(logs) ? logs : _private);
                return (new JsonObject { ["accepted"] = true }, null);
            case "open_guide":    // 设置 → 关于: the player guide (README.md)
                var guide = GuidePath();
                if (guide is null) { return (null, "没有找到使用说明。"); }
                ShellOpen.SelectFile(guide);
                return (new JsonObject { ["accepted"] = true, ["file"] = Path.GetFileName(guide) }, null);
            case "issue_report":
                var report = WriteIssueReport();
                ShellOpen.SelectFile(report.Path);
                lock (_gate) { _lastReport = Path.GetFileName(report.Path); }
                return (new JsonObject { ["accepted"] = true, ["file"] = Path.GetFileName(report.Path), ["files"] = report.Files, ["bytes"] = report.Bytes }, null);
            case "open_link":
                var repo = Repo();
                if (repo is null) { return (null, "还没有设置项目地址。"); }
                var link = parameters.TryGetProperty("link", out var linkElement) ? linkElement.GetString() : null;
                var url = link switch
                {
                    "repo" => $"https://github.com/{repo}",
                    "issues" => $"https://github.com/{repo}/issues",
                    "releases" => $"https://github.com/{repo}/releases",
                    _ => null,
                };
                if (url is null) { return (null, "未知链接。"); }
                ShellOpen.GitHubPage(url);
                return (new JsonObject { ["accepted"] = true }, null);
            case "update_check":
                var channel = parameters.TryGetProperty("channel", out var channelElement) && channelElement.GetString() == "beta" ? "beta" : "stable";
                return StartUpdateCheck(channel);
            case "update_download":
                return StartUpdateDownload();
            case "update_restart":
                return RestartForUpdate();
            case "notify_test":
                if (Notify is null) { return (null, "当前窗口不支持系统通知。"); }
                Notify("MaaShinyColors · 测试通知", "系统通知可以正常显示。", ToolTipIcon.Info);
                return (new JsonObject { ["accepted"] = true }, null);
            case "hotkey":
                if (SetHotkey is null) { return (null, "当前窗口不支持快捷键。"); }
                var enabled = parameters.TryGetProperty("enabled", out var enabledElement) && enabledElement.ValueKind == JsonValueKind.True;
                return (new JsonObject { ["accepted"] = true, ["hotkey"] = SetHotkey(enabled) }, null);
            default:
                return (null, "未知操作。");
        }
    }

    /// <summary>README.md, in a source checkout and in the package.</summary>
    private string? GuidePath()
    {
        foreach (var candidate in new[] { Path.Combine(_root, "README.md") })
        {
            if (File.Exists(candidate)) { return candidate; }
        }
        return null;
    }

    private (JsonObject?, string?) StartUpdateCheck(string channel)
    {
        var repo = Repo();
        if (repo is null) { return (null, "还没有设置发布地址，暂不能检查更新。"); }
        lock (_gate)
        {
            if ((string?)_update["state"] == "checking") { return (new JsonObject { ["accepted"] = true }, null); }
            _update = new JsonObject { ["state"] = "checking", ["channel"] = channel };
        }
        var current = Version();
        _ = Task.Run(async () =>
        {
            JsonObject result;
            try
            {
                using var response = await Http.GetAsync($"https://api.github.com/repos/{repo}/releases?per_page=20").ConfigureAwait(false);
                if (!response.IsSuccessStatusCode)
                {
                    result = new JsonObject { ["state"] = "failed", ["reason"] = $"http_{(int)response.StatusCode}" };
                }
                else
                {
                    var releases = JsonNode.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false)) as JsonArray ?? new JsonArray();
                    var latest = releases.OfType<JsonObject>().FirstOrDefault(r => r["draft"]?.GetValue<bool>() != true
                        && (channel == "beta" || r["prerelease"]?.GetValue<bool>() != true));
                    var tag = (string?)latest?["tag_name"];
                    result = tag is null
                        ? new JsonObject { ["state"] = "done", ["latest"] = null, ["newer"] = false }
                        : new JsonObject
                        {
                            ["state"] = "done",
                            ["latest"] = tag,
                            ["newer"] = CompareVersions(tag, current) > 0,
                            ["published_at"] = latest!["published_at"]?.DeepClone(),
                            ["prerelease"] = latest["prerelease"]?.DeepClone(),
                        };
                }
            }
            catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException or JsonException or InvalidOperationException)
            {
                result = new JsonObject { ["state"] = "failed", ["reason"] = exception is TaskCanceledException ? "timeout" : "network" };
            }
            result["channel"] = channel;
            result["current"] = current;
            result["checked_at"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture);
            lock (_gate) { _update = result; }
        });
        return (new JsonObject { ["accepted"] = true }, null);
    }

    /// <summary>Numeric dotted comparison of "v1.2.3-beta.1" style tags; a pre-release suffix sorts before its release.</summary>
    internal static int CompareVersions(string a, string b)
    {
        static (int[] Parts, string Suffix) Parse(string value)
        {
            value = value.Trim().TrimStart('v', 'V');
            value = value.Split('+', 2)[0];
            var dash = value.IndexOf('-');
            var core = dash < 0 ? value : value[..dash];
            var suffix = dash < 0 ? "" : value[(dash + 1)..];
            var parts = core.Split('.').Select(p => int.TryParse(p, NumberStyles.None, CultureInfo.InvariantCulture, out var n) ? n : 0).ToArray();
            return (parts, suffix);
        }
        var (pa, sa) = Parse(a);
        var (pb, sb) = Parse(b);
        for (var i = 0; i < Math.Max(pa.Length, pb.Length); i++)
        {
            var x = i < pa.Length ? pa[i] : 0;
            var y = i < pb.Length ? pb[i] : 0;
            if (x != y) { return x.CompareTo(y); }
        }
        if (sa == sb) { return 0; }
        if (sa.Length == 0) { return 1; }
        if (sb.Length == 0) { return -1; }
        var aIds = sa.Split('.');
        var bIds = sb.Split('.');
        for (var i = 0; i < Math.Min(aIds.Length, bIds.Length); i++)
        {
            var an = System.Numerics.BigInteger.TryParse(aIds[i], NumberStyles.None, CultureInfo.InvariantCulture, out var av);
            var bn = System.Numerics.BigInteger.TryParse(bIds[i], NumberStyles.None, CultureInfo.InvariantCulture, out var bv);
            var comparison = an && bn ? av.CompareTo(bv) : an != bn ? (an ? -1 : 1) : string.CompareOrdinal(aIds[i], bIds[i]);
            if (comparison != 0) { return comparison; }
        }
        return aIds.Length.CompareTo(bIds.Length);
    }

    // ---- 问题包 ------------------------------------------------------------------------------------------------

    /// <summary>The files a report may contain: newest text files of fixed private folders (relative to dev_assets/private).</summary>
    internal IEnumerable<string> ReportSources()
    {
        IEnumerable<string> Newest(string folder, string pattern, int count, SearchOption option = SearchOption.TopDirectoryOnly)
        {
            var path = Path.Combine(_private, folder);
            if (!Directory.Exists(path)) { return Array.Empty<string>(); }
            return new DirectoryInfo(path).EnumerateFiles(pattern, option).Where(f => (f.Attributes & FileAttributes.ReparsePoint) == 0)
                .OrderByDescending(f => f.LastWriteTimeUtc).Take(count).Select(f => f.FullName);
        }
        var runs = Path.Combine(_private, "ui", "runs");
        var runFiles = Directory.Exists(runs)
            ? new DirectoryInfo(runs).EnumerateDirectories().Where(d => (d.Attributes & FileAttributes.ReparsePoint) == 0)
                .OrderByDescending(d => d.LastWriteTimeUtc).Take(3).SelectMany(d => d.EnumerateFiles().Select(f => f.FullName))
            : Array.Empty<string>();
        var singles = new[] { Path.Combine("ui", "profile.json"), Path.Combine("ui", "host-window.json"), Path.Combine("live-status", "current.json"),
            "launcher-check.json", "decision-live.jsonl" }.Select(p => Path.Combine(_private, p)).Where(File.Exists);
        return runFiles.Concat(Newest("run_logs", "*.jsonl", 4)).Concat(Newest(Path.Combine("wing-ui", "decision-log"), "*.jsonl", 3)).Concat(singles)
            .Where(path => TextSuffixes.Contains(Path.GetExtension(path).ToLowerInvariant()))
            .Distinct(StringComparer.OrdinalIgnoreCase);
    }

    /// <summary>Replaces the data root, the user profile folder and the Windows user name in a text file.</summary>
    internal string Scrub(string text)
    {
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        foreach (var (value, mark) in new[] { (_root, "<ROOT>"), (home, "<HOME>") })
        {
            if (string.IsNullOrEmpty(value)) { continue; }
            text = text.Replace(value, mark, StringComparison.OrdinalIgnoreCase)
                .Replace(value.Replace("\\", "\\\\"), mark, StringComparison.OrdinalIgnoreCase)
                .Replace(value.Replace('\\', '/'), mark, StringComparison.OrdinalIgnoreCase);
        }
        var user = Environment.UserName;
        if (user.Length >= 3) { text = Regex.Replace(text, Regex.Escape(user), "<USER>", RegexOptions.IgnoreCase); }
        return text;
    }

    /// <summary>Newest failure screenshots (the frame each failed task stopped on), copied unchanged.</summary>
    internal IEnumerable<string> FailureShots()
    {
        var path = Path.Combine(_private, "run_logs", "diagnostics", "failures");
        if (!Directory.Exists(path)) { return Array.Empty<string>(); }
        return new DirectoryInfo(path).EnumerateFiles("*.png").Where(f => (f.Attributes & FileAttributes.ReparsePoint) == 0 && f.Length <= MaxFileBytes)
            .OrderByDescending(f => f.LastWriteTimeUtc).Take(MaxFailureShots).Select(f => f.FullName);
    }

    internal (string Path, int Files, long Bytes) WriteIssueReport()
    {
        var folder = Path.Combine(_private, "issue-reports");
        Directory.CreateDirectory(folder);
        var path = Path.Combine(folder, $"issue-{DateTime.Now:yyyyMMdd-HHmmss}.zip");
        var files = 0;
        using (var zip = ZipFile.Open(path, ZipArchiveMode.Create))
        {
            var info = new JsonObject
            {
                ["version"] = Version(),
                ["build"] = Info()["build"]?.DeepClone(),
                ["os"] = Environment.OSVersion.VersionString,
                ["dotnet"] = Environment.Version.ToString(),
                ["screens"] = new JsonArray(Screen.AllScreens.Select(s => (JsonNode)$"{s.Bounds.Width}x{s.Bounds.Height}").ToArray()),
                ["created"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture),
                ["note"] = "运行记录、输入日志、W.I.N.G. 决策、设置，以及最近的失败截图（failures/）。不含登录信息与游戏素材；文字中的本机路径与用户名已替换。",
            };
            using (var writer = new StreamWriter(zip.CreateEntry("info.json").Open(), new UTF8Encoding(false)))
            {
                writer.Write(info.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
            }
            foreach (var source in ReportSources())
            {
                try
                {
                    var bytes = File.ReadAllBytes(source);
                    if (bytes.LongLength > MaxFileBytes) { bytes = bytes[^(int)MaxFileBytes..]; }
                    var entry = Path.GetRelativePath(_private, source).Replace('\\', '/');
                    using var writer = new StreamWriter(zip.CreateEntry(entry, CompressionLevel.Optimal).Open(), new UTF8Encoding(false));
                    writer.Write(Scrub(Encoding.UTF8.GetString(bytes)));
                    files++;
                }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
            }
            foreach (var shot in FailureShots())
            {
                try
                {
                    zip.CreateEntryFromFile(shot, "failures/" + Path.GetFileName(shot), CompressionLevel.NoCompression);
                    files++;
                }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
            }
        }
        return (path, files, new FileInfo(path).Length);
    }
}

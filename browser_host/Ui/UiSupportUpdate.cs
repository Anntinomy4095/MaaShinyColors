using System.IO.Compression;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// 设置 → 更新 → 「下载更新」 / 「重启并更新」 (2026-11-27). After 检查更新 found a newer release of the same repo, the
/// player's click downloads that release's update zip for exactly this version (MaaShinyColors-update-&lt;current&gt;-to-
/// &lt;new&gt;.zip), or else its full MaaShinyColors.zip, checks GitHub's SHA-256 when given and every file's SHA-256 from
/// the zip's own list, and stages only the files that differ under dev_assets/private/update/staged/ (ready.txt last).
/// The launcher (MaaShinyColors.exe --update, or the next normal start) applies it with backup and restore. Player data
/// (dev_assets/, ui/game-assets/) is never staged. A source checkout has no launcher and is refused.
/// </summary>
internal sealed partial class UiSupport
{
    internal const string PackageRoot = "MaaShinyColors";
    private const long MaxDownloadBytes = 600L << 20;
    private static readonly string[] UserDataPrefixes = { "dev_assets/", "ui/game-assets/", "host/ui/game-assets/" };
    private static readonly HttpClient DownloadHttp = CreateDownloadHttp();

    /// <summary>Closes the host window (normal FormClosing path); set by the game window.</summary>
    internal Action? Exit { get; set; }

    private string UpdateDir => Path.Combine(_private, "update");
    private string Launcher => Path.Combine(_root, "MaaShinyColors.exe");
    private bool IsPackage => File.Exists(Path.Combine(_root, "PACKAGE_MANIFEST.json")) && File.Exists(Launcher);

    private static HttpClient CreateDownloadHttp()
    {
        var client = new HttpClient { Timeout = TimeSpan.FromMinutes(30) };
        client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("MaaShinyColors", "1.0"));
        return client;
    }

    /// <summary>Result of the launcher's apply right before this start (update/last-apply.json), shown once: the file is
    /// renamed to last-apply.seen.json after reading. Null when no update was applied.</summary>
    internal JsonObject? LastApply()
    {
        try
        {
            var path = Path.Combine(UpdateDir, "last-apply.json");
            if (!File.Exists(path)) { return null; }
            var result = JsonNode.Parse(File.ReadAllText(path)) as JsonObject;
            File.Move(path, Path.Combine(UpdateDir, "last-apply.seen.json"), overwrite: true);
            return result;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException) { return null; }
    }

    private (JsonObject?, string?) StartUpdateDownload()
    {
        var repo = Repo();
        if (repo is null) { return (null, "还没有设置发布地址。"); }
        if (!IsPackage) { return (null, "源码运行不支持自动更新。"); }
        string tag;
        lock (_gate)
        {
            if ((string?)_update["state"] is "downloading" or "staging") { return (new JsonObject { ["accepted"] = true }, null); }
            if ((string?)_update["state"] != "done" || _update["newer"]?.GetValue<bool>() != true || (string?)_update["latest"] is not { } latest)
            {
                return (null, "请先检查更新。");
            }
            tag = latest;
            _update = new JsonObject { ["state"] = "downloading", ["latest"] = tag, ["bytes"] = 0, ["channel"] = _update["channel"]?.DeepClone() };
        }
        var current = Version();
        _ = Task.Run(async () =>
        {
            JsonObject result;
            try { result = await DownloadAndStage(repo, tag, current).ConfigureAwait(false); }
            catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException or IOException
                or InvalidDataException or JsonException or UnauthorizedAccessException or InvalidOperationException)
            {
                result = new JsonObject
                {
                    ["state"] = "download_failed",
                    ["reason"] = exception is TaskCanceledException ? "timeout" : exception is HttpRequestException ? "network"
                        : exception is InvalidDataException ? exception.Message : "io",
                };
            }
            result["latest"] = tag;
            result["current"] = current;
            lock (_gate) { _update = result; }
        });
        return (new JsonObject { ["accepted"] = true }, null);
    }

    private async Task<JsonObject> DownloadAndStage(string repo, string tag, string current)
    {
        using var response = await Http.GetAsync($"https://api.github.com/repos/{repo}/releases/tags/{Uri.EscapeDataString(tag)}").ConfigureAwait(false);
        if (!response.IsSuccessStatusCode) { throw new InvalidDataException($"http_{(int)response.StatusCode}"); }
        var release = JsonNode.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false)) as JsonObject
            ?? throw new InvalidDataException("release_unreadable");
        var target = tag.TrimStart('v', 'V');
        var assets = (release["assets"] as JsonArray ?? new JsonArray()).OfType<JsonObject>().ToList();
        var asset = assets.FirstOrDefault(a => (string?)a["name"] == $"{PackageRoot}-update-{current}-to-{target}.zip")
            ?? assets.FirstOrDefault(a => (string?)a["name"] == $"{PackageRoot}.zip")
            ?? throw new InvalidDataException("no_asset");
        var url = (string?)asset["browser_download_url"] ?? throw new InvalidDataException("no_asset");
        if (!url.StartsWith($"https://github.com/{repo}/releases/download/", StringComparison.Ordinal)) { throw new InvalidDataException("asset_url"); }
        var size = asset["size"]?.GetValue<long>() ?? 0;
        if (size <= 0 || size > MaxDownloadBytes) { throw new InvalidDataException("asset_size"); }

        var downloads = Path.Combine(UpdateDir, "download");
        if (Directory.Exists(downloads)) { Directory.Delete(downloads, true); }
        Directory.CreateDirectory(downloads);
        var file = Path.Combine(downloads, "update.zip");
        using (var download = await DownloadHttp.GetAsync(url, HttpCompletionOption.ResponseHeadersRead).ConfigureAwait(false))
        {
            if (!download.IsSuccessStatusCode) { throw new InvalidDataException($"http_{(int)download.StatusCode}"); }
            await using var source = await download.Content.ReadAsStreamAsync().ConfigureAwait(false);
            await using var sink = new FileStream(file, FileMode.CreateNew, FileAccess.Write);
            var buffer = new byte[1 << 16];
            long total = 0;
            int read;
            while ((read = await source.ReadAsync(buffer).ConfigureAwait(false)) > 0)
            {
                total += read;
                if (total > size) { throw new InvalidDataException("asset_size"); }
                await sink.WriteAsync(buffer.AsMemory(0, read)).ConfigureAwait(false);
                lock (_gate) { _update["bytes"] = total; _update["total"] = size; }
            }
            if (total != size) { throw new InvalidDataException("asset_size"); }
        }
        if ((string?)asset["digest"] is { } digest && digest.StartsWith("sha256:", StringComparison.Ordinal)
            && !string.Equals(digest[7..], FileSha(file), StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("digest");
        }
        lock (_gate) { _update["state"] = "staging"; }
        var staged = Stage(_root, file, current, target);
        Directory.Delete(downloads, true);
        staged["state"] = "ready";
        staged["asset"] = (string?)asset["name"];
        return staged;
    }

    private (JsonObject?, string?) RestartForUpdate()
    {
        lock (_gate)
        {
            if ((string?)_update["state"] != "ready") { return (null, "还没有下载好的更新。"); }
        }
        if (!IsPackage || Exit is null) { return (null, "当前窗口不支持自动更新。"); }
        if (!UpdateLauncher.Start(_root)) { return (null, "启动器无法启动，请手动重新打开 MaaShinyColors。"); }
        Exit();
        return (new JsonObject { ["accepted"] = true }, null);
    }

    internal static string FileSha(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    internal static bool IsUserData(string relative) => UserDataPrefixes.Any(p => relative.StartsWith(p, StringComparison.OrdinalIgnoreCase));

    private static string CheckedRelative(string path)
    {
        var relative = path.Replace('\\', '/');
        if (relative.Length == 0 || relative.StartsWith('/') || relative.Contains(':') || relative.Split('/').Any(s => s is "" or ".." or "."))
        {
            throw new InvalidDataException("bad_path");
        }
        if (IsUserData(relative)) { throw new InvalidDataException("user_data_path"); }
        return relative;
    }

    /// <summary>
    /// Stages <paramref name="zipPath"/> (an update zip from <paramref name="current"/>, or a full zip of
    /// <paramref name="target"/>) for the launcher; returns {to, files, removed}. Every staged file's SHA-256 matches
    /// the zip's own list. A full zip stages only the files whose hash differs from the installed copy, and removes
    /// installed files of the old list that the new list no longer has.
    /// </summary>
    internal static JsonObject Stage(string root, string zipPath, string current, string target)
    {
        var staged = Path.Combine(root, "dev_assets", "private", "update", "staged");
        if (Directory.Exists(staged)) { Directory.Delete(staged, true); }
        using var zip = ZipFile.OpenRead(zipPath);
        var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
        foreach (var entry in zip.Entries)
        {
            var name = entry.FullName.Replace('\\', '/');
            if (name.EndsWith('/')) { continue; }
            if (!name.StartsWith(PackageRoot + "/", StringComparison.Ordinal)) { throw new InvalidDataException("not_update_zip"); }
            entries[name[(PackageRoot.Length + 1)..]] = entry;
        }
        JsonObject ReadJson(string name) => JsonNode.Parse(new StreamReader(entries[name].Open(), Encoding.UTF8).ReadToEnd()) as JsonObject
            ?? throw new InvalidDataException("list_unreadable");
        Dictionary<string, string> Listing(JsonObject? files) => (files ?? new JsonObject())
            .ToDictionary(p => CheckedRelative(p.Key), p => ((string?)p.Value ?? "").ToLowerInvariant(), StringComparer.Ordinal);

        Dictionary<string, string> stage;
        List<string> removed;
        if (entries.ContainsKey("UPDATE_PATCH.json"))
        {
            var patch = ReadJson("UPDATE_PATCH.json");
            if ((string?)patch["from"] != current) { throw new InvalidDataException("patch_base"); }
            if ((string?)patch["to"] != target) { throw new InvalidDataException("patch_target"); }
            stage = Listing(patch["files"] as JsonObject);
            removed = (patch["removed"] as JsonArray ?? new JsonArray()).Select(n => CheckedRelative((string?)n ?? "")).ToList();
        }
        else if (entries.ContainsKey("UPDATE_FILES.json"))
        {
            var listing = ReadJson("UPDATE_FILES.json");
            if ((string?)listing["version"] != target) { throw new InvalidDataException("full_version"); }
            var files = Listing(listing["files"] as JsonObject);
            stage = files.Where(p =>
            {
                var local = Path.Combine(root, p.Key);
                return !File.Exists(local) || FileSha(local) != p.Value;
            }).ToDictionary(p => p.Key, p => p.Value, StringComparer.Ordinal);
            stage["UPDATE_FILES.json"] = FileShaOf(entries["UPDATE_FILES.json"]);
            removed = new List<string>();
            var installed = Path.Combine(root, "UPDATE_FILES.json");
            if (File.Exists(installed) && JsonNode.Parse(File.ReadAllText(installed))?["files"] is JsonObject old)
            {
                removed = old.Select(p => p.Key.Replace('\\', '/')).Where(p => !files.ContainsKey(p) && p != "UPDATE_FILES.json"
                    && File.Exists(Path.Combine(root, p))).Select(CheckedRelative).ToList();
            }
        }
        else
        {
            throw new InvalidDataException("not_update_zip");
        }

        var lines = new List<string> { $"to\t{target}", $"from\t{current}" };
        foreach (var (relative, sha) in stage.OrderBy(p => p.Key, StringComparer.Ordinal))
        {
            if (!entries.TryGetValue(relative, out var entry)) { throw new InvalidDataException("file_missing"); }
            var path = Path.Combine(staged, "files", relative.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            entry.ExtractToFile(path, overwrite: true);
            if (FileSha(path) != sha) { throw new InvalidDataException("file_hash"); }
            lines.Add($"file\t{relative}\t{sha}");
        }
        lines.AddRange(removed.Select(r => $"remove\t{r}"));
        lines.Add("end");
        Directory.CreateDirectory(staged);
        File.WriteAllText(Path.Combine(staged, "ready.txt"), string.Join("\n", lines) + "\n", new UTF8Encoding(false));
        return new JsonObject { ["to"] = target, ["files"] = stage.Count, ["removed"] = removed.Count };
    }

    private static string FileShaOf(ZipArchiveEntry entry)
    {
        using var stream = entry.Open();
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }
}

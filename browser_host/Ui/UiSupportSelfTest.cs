using System.IO.Compression;
using System.Text;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Phase S2 of --self-test-ui: 设置 → 关于 / 问题反馈 / 更新 without Explorer, a browser or the network.
/// A throw-away root under the self-test output holds a JSONC interface.json and fake private files; the issue report
/// must keep only text logs, replace local paths and the user name, and the version compare must order pre-releases.
/// </summary>
internal static partial class UiSelfTest
{
    private static void SupportPhase(Checks checks, string output)
    {
        const string P = "S2";
        var root = Path.Combine(output, "support-root");
        var data = Path.Combine(root, "dev_assets", "private");
        Directory.CreateDirectory(Path.Combine(root, "assets"));
        File.WriteAllText(Path.Combine(root, "assets", "interface.json"),
            "{\n  // JSONC like the real file\n  \"version\": \"1.2.0\",\n  \"github\": \"example/maa-test\",\n}\n");
        var run = Path.Combine(data, "ui", "runs", "ui-1");
        Directory.CreateDirectory(run);
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var homeSlash = home.Replace(Path.DirectorySeparatorChar, '/');
        var line = new System.Text.Json.Nodes.JsonObject { ["path"] = Path.Combine(root, "x"), ["home"] = homeSlash + "/y" }.ToJsonString();
        File.WriteAllText(Path.Combine(run, "events.jsonl"), line + "\n");
        File.WriteAllBytes(Path.Combine(run, "frame.png"), new byte[] { 0x89, 0x50, 0x4E, 0x47 });
        Directory.CreateDirectory(Path.Combine(data, "webview2-profile"));
        File.WriteAllText(Path.Combine(data, "webview2-profile", "Cookies.log"), "secret");
        Directory.CreateDirectory(Path.Combine(data, "run_logs"));
        File.WriteAllText(Path.Combine(data, "run_logs", "2026-10-15.jsonl"), "{\"input\":1}\n");
        var failures = Path.Combine(data, "run_logs", "diagnostics", "failures");
        Directory.CreateDirectory(failures);
        File.WriteAllBytes(Path.Combine(failures, "20261015T000000Z.png"), new byte[] { 0x89, 0x50, 0x4E, 0x47 });

        var support = new UiSupport(root);
        var info = support.Info();
        checks.Add(P, "info_reads_jsonc_version_and_repo", (string?)info["version"] == "1.2.0" && (string?)info["repo"] == "example/maa-test"
            && info["links"] is System.Text.Json.Nodes.JsonArray { Count: 3 }, info.ToJsonString());
        var (report, files, _) = support.WriteIssueReport();
        using (var zip = ZipFile.OpenRead(report))
        {
            var names = zip.Entries.Select(e => e.FullName).ToList();
            checks.Add(P, "report_has_logs_and_failure_shots_only", names.Contains("info.json") && names.Contains("ui/runs/ui-1/events.jsonl")
                && names.Contains("run_logs/2026-10-15.jsonl") && names.Contains("failures/20261015T000000Z.png")
                && names.Count(n => n.EndsWith(".png", StringComparison.Ordinal)) == 1 && !names.Any(n => n.Contains("webview2", StringComparison.Ordinal)),
                string.Join(", ", names));
            using var reader = new StreamReader(zip.GetEntry("ui/runs/ui-1/events.jsonl")!.Open(), Encoding.UTF8);
            var text = reader.ReadToEnd();
            checks.Add(P, "report_paths_scrubbed", !text.Contains(root, StringComparison.OrdinalIgnoreCase)
                && !text.Contains(homeSlash, StringComparison.OrdinalIgnoreCase) && text.Contains("<ROOT>", StringComparison.Ordinal)
                && text.Contains("<HOME>", StringComparison.Ordinal), text);
        }
        checks.Add(P, "report_inside_private_folder", Path.GetDirectoryName(report) == Path.Combine(data, "issue-reports") && files == 3, report);
        checks.Add(P, "version_compare", UiSupport.CompareVersions("v1.3.0", "1.2.9") > 0 && UiSupport.CompareVersions("1.2.0", "v1.2.0") == 0
            && UiSupport.CompareVersions("1.2.0-beta.1", "1.2.0") < 0 && UiSupport.CompareVersions("1.2.0", "1.2.0-beta.2") > 0);
        checks.Add(P, "version_prerelease_numeric_segments", UiSupport.CompareVersions("1.2.0-beta.10", "1.2.0-beta.9") > 0
            && UiSupport.CompareVersions("1.2.0-beta.2", "1.2.0-beta.10") < 0
            && UiSupport.CompareVersions("1.2.0-beta", "1.2.0-beta.1") < 0
            && UiSupport.CompareVersions("1.2.0-1", "1.2.0-alpha") < 0);
        checks.Add(P, "repo_from_github_url", UiSupport.RepoName("https://github.com/example/maa-test") == "example/maa-test"
            && UiSupport.RepoName("https://github.com/example/maa-test.git/") == "example/maa-test"
            && UiSupport.RepoName("example/maa-test") == "example/maa-test"
            && UiSupport.RepoName("https://example.com/example/maa-test") is null && UiSupport.RepoName("https://github.com/example") is null);
        checks.Add(P, "version_build_metadata_ignored", UiSupport.CompareVersions("1.2.0-beta.10+build1", "1.2.0-beta.10+build2") == 0);
        using var empty = JsonDocument.Parse("{}");
        var (_, linkError) = new UiSupport(output).Handle("open_link", empty.RootElement.Clone());
        checks.Add(P, "no_repo_refuses_links", linkError is not null, linkError);
        var (_, opError) = support.Handle("format_disk", empty.RootElement.Clone());
        checks.Add(P, "unknown_op_refused", opError is not null, opError);
        var (_, downloadError) = support.Handle("update_download", empty.RootElement.Clone());
        checks.Add(P, "update_download_needs_package_and_check", downloadError is not null, downloadError);
        UpdateStagePhase(checks, output);
    }

    /// <summary>2026-11-27 下载更新: staging an update zip / full zip into dev_assets/private/update/staged (no network).</summary>
    private static void UpdateStagePhase(Checks checks, string output)
    {
        const string P = "S2";
        var root = Path.Combine(output, "update-root");
        if (Directory.Exists(root)) { Directory.Delete(root, true); }
        void Put(string relative, string text)
        {
            var path = Path.Combine(root, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, text);
        }
        static string Sha(string text) => Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(text))).ToLowerInvariant();
        string Zip(string name, Dictionary<string, string> entries)
        {
            var path = Path.Combine(output, name);
            if (File.Exists(path)) { File.Delete(path); }
            using var zip = ZipFile.Open(path, ZipArchiveMode.Create);
            foreach (var (entry, text) in entries)
            {
                using var writer = new StreamWriter(zip.CreateEntry("MaaShinyColors/" + entry).Open(), new UTF8Encoding(false));
                writer.Write(text);
            }
            return path;
        }
        string Ready() => File.ReadAllText(Path.Combine(root, "dev_assets", "private", "update", "staged", "ready.txt"));
        Put("agent/a.py", "old a");
        Put("agent/same.py", "same");
        Put("agent/gone.py", "gone");
        Put("UPDATE_FILES.json", "{\"version\":\"0.1.1\",\"files\":{\"agent/a.py\":\"" + Sha("old a") + "\",\"agent/same.py\":\"" + Sha("same")
            + "\",\"agent/gone.py\":\"" + Sha("gone") + "\"}}");

        var patch = Zip("update-patch.zip", new()
        {
            ["agent/a.py"] = "new a",
            ["UPDATE_PATCH.json"] = "{\"from\":\"0.1.1\",\"to\":\"0.1.2\",\"files\":{\"agent/a.py\":\"" + Sha("new a") + "\"},\"removed\":[\"agent/gone.py\"]}",
        });
        var staged = UiSupport.Stage(root, patch, "0.1.1", "0.1.2");
        var ready = Ready();
        checks.Add(P, "update_patch_staged", (int?)staged["files"] == 1 && (int?)staged["removed"] == 1
            && ready.Contains("file\tagent/a.py\t" + Sha("new a"), StringComparison.Ordinal) && ready.Contains("remove\tagent/gone.py", StringComparison.Ordinal)
            && ready.StartsWith("to\t0.1.2\nfrom\t0.1.1\n", StringComparison.Ordinal) && ready.EndsWith("end\n", StringComparison.Ordinal), ready);

        var fullListing = "{\"version\":\"0.1.2\",\"files\":{\"agent/a.py\":\"" + Sha("new a") + "\",\"agent/same.py\":\"" + Sha("same") + "\"}}";
        var full = Zip("update-full.zip", new() { ["agent/a.py"] = "new a", ["agent/same.py"] = "same", ["UPDATE_FILES.json"] = fullListing });
        staged = UiSupport.Stage(root, full, "0.1.1", "0.1.2");
        ready = Ready();
        checks.Add(P, "update_full_stages_only_changed", (int?)staged["files"] == 2 && !ready.Contains("agent/same.py", StringComparison.Ordinal)
            && ready.Contains("file\tagent/a.py", StringComparison.Ordinal) && ready.Contains("file\tUPDATE_FILES.json", StringComparison.Ordinal)
            && ready.Contains("remove\tagent/gone.py", StringComparison.Ordinal), ready);

        bool Refused(string name, Dictionary<string, string> entries, string current = "0.1.1")
        {
            try { UiSupport.Stage(root, Zip(name, entries), current, "0.1.2"); return false; }
            catch (InvalidDataException) { return true; }
        }
        var patchFor = (string files) => "{\"from\":\"0.1.1\",\"to\":\"0.1.2\",\"files\":{" + files + "},\"removed\":[]}";
        checks.Add(P, "update_refusals",
            Refused("r1.zip", new() { ["UPDATE_PATCH.json"] = patchFor("") }, current: "0.1.0")                                       // wrong base
            && Refused("r2.zip", new() { ["agent/a.py"] = "evil", ["UPDATE_PATCH.json"] = patchFor("\"agent/a.py\":\"" + Sha("new a") + "\"") })   // hash
            && Refused("r3.zip", new() { ["dev_assets/private/x.json"] = "x", ["UPDATE_PATCH.json"] = patchFor("\"dev_assets/private/x.json\":\"" + Sha("x") + "\"") })
            && Refused("r4.zip", new() { ["ui/game-assets/c.png"] = "x", ["UPDATE_PATCH.json"] = patchFor("\"ui/game-assets/c.png\":\"" + Sha("x") + "\"") })
            && Refused("r5.zip", new() { ["UPDATE_PATCH.json"] = "{\"from\":\"0.1.1\",\"to\":\"0.1.2\",\"files\":{},\"removed\":[\"../x\"]}" })
            && Refused("r6.zip", new() { ["agent/a.py"] = "x" }));                                                                     // no list
        checks.Add(P, "update_player_data_untouched", File.ReadAllText(Path.Combine(root, "agent", "a.py")) == "old a");
    }
}

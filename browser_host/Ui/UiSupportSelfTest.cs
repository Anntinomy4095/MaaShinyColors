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
    }
}

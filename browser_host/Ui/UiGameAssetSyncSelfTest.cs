using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Phase GA of --self-test-ui: <see cref="UiGameAssetSync"/> in a throw-away repo folder. A changed whitelisted module is copied
/// once and bumps its version; a file still being written, an identical copy, an oversized file and a name outside the
/// whitelist are not copied. Local files only.
/// </summary>
internal static partial class UiSelfTest
{
    private static void AssetSyncPhase(Checks checks, string output)
    {
        const string P = "GA";
        var repo = Path.Combine(output, "assetsync-repo");
        var source = Path.Combine(repo, "ui", "game-assets");
        var target = Path.Combine(output, "assetsync-output");
        Directory.CreateDirectory(source);
        Directory.CreateDirectory(target);
        var now = new DateTime(2026, 10, 24, 0, 0, 0, DateTimeKind.Utc);
        var sync = new UiGameAssetSync(repo, target, () => now);

        void Write(string folder, string name, string text, DateTime at)
        {
            var path = Path.Combine(folder, name);
            File.WriteAllText(path, text);
            File.SetLastWriteTimeUtc(path, at);
        }

        Write(source, "knowhow.js", "export default {books:[1,2]};\n", now.AddMinutes(-1));
        Write(target, "knowhow.js", "export default {};\n", now.AddDays(-3));
        Write(source, "owned-levels.js", "export default {};\n", now.AddMilliseconds(-500));    // still being written
        Write(source, "team-snapshot.js", new string('x', 70 * 1024), now.AddMinutes(-1));      // over its 64 KB limit
        Write(source, "evil.js", "export default {};\n", now.AddMinutes(-1));                  // not whitelisted
        var first = sync.SyncNow();
        checks.Add(P, "newer_module_copied", first.SequenceEqual(new[] { "knowhow.js" })
            && File.ReadAllText(Path.Combine(target, "knowhow.js")).Contains("[1,2]"), first);
        checks.Add(P, "unsettled_oversized_and_unknown_skipped", !File.Exists(Path.Combine(target, "owned-levels.js"))
            && !File.Exists(Path.Combine(target, "team-snapshot.js")) && !File.Exists(Path.Combine(target, "evil.js")));
        var status = sync.Status();
        checks.Add(P, "status_versions", status["seq"]!.GetValue<int>() == 1 && status["modules"]!["knowhow.js"]!.GetValue<int>() == 1
            && status["modules"]!.AsObject().Count == 1, status.ToJsonString());

        var again = sync.SyncNow();
        checks.Add(P, "unchanged_not_copied_again", again.Count == 0 && sync.Status()["seq"]!.GetValue<int>() == 1);

        now = now.AddSeconds(5);                            // owned-levels.js is settled now
        Write(source, "knowhow.js", "export default {books:[1,2,3]};\n", now.AddMinutes(-1).AddSeconds(30));
        var third = sync.SyncNow();
        var after = sync.Status();
        checks.Add(P, "rewrite_bumps_only_changed_modules", third.OrderBy(name => name, StringComparer.Ordinal).SequenceEqual(new[] { "knowhow.js", "owned-levels.js" })
            && after["seq"]!.GetValue<int>() == 2 && after["modules"]!["knowhow.js"]!.GetValue<int>() == 2
            && after["modules"]!["owned-levels.js"]!.GetValue<int>() == 1, after.ToJsonString());

        Write(target, "knowhow.js", "export default {stale:true};\n", now);   // any other output copy: the repo is the source
        checks.Add(P, "differing_output_copy_replaced", sync.SyncNow().SequenceEqual(new[] { "knowhow.js" })
            && File.ReadAllText(Path.Combine(target, "knowhow.js")).Contains("[1,2,3]"));

        var same = new UiGameAssetSync(Path.Combine(output, "assetsync-same"), Path.Combine(output, "assetsync-same", "ui", "game-assets"), () => now);
        checks.Add(P, "same_folder_noop", same.SyncNow().Count == 0);
    }
}

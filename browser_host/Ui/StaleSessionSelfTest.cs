namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Phase SS of --self-test-ui: <see cref="StaleSession"/> in a throw-away folder. Files of a host that is gone are removed
/// with their siblings (another session's files stay); a live host's, this process's and an unreadable session stay.
/// </summary>
internal static partial class UiSelfTest
{
    private static void StaleSessionPhase(Checks checks, string output)
    {
        const string P = "SS";
        var folder = Path.Combine(output, "stale-session");
        Directory.CreateDirectory(folder);
        string Session(string name, string json)
        {
            var path = Path.Combine(folder, name);
            File.WriteAllText(path, json);
            File.WriteAllText(path + ".pipeline-input.json", "{}");
            return path;
        }

        var dead = Session("dead.json", "{\"version\":1,\"pid\":424242}");
        var other = Session("other.json", "{\"version\":1,\"pid\":424242}");
        var removed = StaleSession.Clear(dead, (_, _) => false);
        checks.Add(P, "dead_host_files_removed", removed && !File.Exists(dead) && !File.Exists(dead + ".pipeline-input.json"));
        checks.Add(P, "other_session_untouched", File.Exists(other) && File.Exists(other + ".pipeline-input.json"));
        var live = Session("live.json", "{\"version\":1,\"pid\":424243}");
        checks.Add(P, "live_host_kept", !StaleSession.Clear(live, (_, _) => true) && File.Exists(live + ".pipeline-input.json"));
        var own = Session("own.json", $"{{\"version\":1,\"pid\":{Environment.ProcessId}}}");
        checks.Add(P, "own_session_kept", !StaleSession.Clear(own, (_, _) => false) && File.Exists(own));
        var broken = Session("broken.json", "{\"version\":1,");
        checks.Add(P, "unreadable_kept", !StaleSession.Clear(broken, (_, _) => false) && File.Exists(broken));
        checks.Add(P, "missing_is_noop", !StaleSession.Clear(Path.Combine(folder, "none.json")));
    }
}

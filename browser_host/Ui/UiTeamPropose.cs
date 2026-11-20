using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// 「重新生成推荐」 (U05, 2026-10-12): runs the offline recommender <c>.venv python tools/auto_team_propose.py --owned
/// &lt;profile&gt; --p-card &lt;P|auto&gt; --primary &lt;stat|auto&gt; --use-recognised-levels</c>, which rewrites the local
/// <c>ui/game-assets/team-proposal.js</c> for the selected P card (or, with auto, the best owned P card). Local files only: no game frame, no input, no network,
/// never a run authorisation. One process at a time, bounded by a timeout; state is polled into status.update.
/// 推荐进度条: with <c>--progress</c> the recommender prints <c>{"progress": [done, total]}</c> lines (one
/// step per compared P card plus 练级提示); the latest one is shown as <c>progress</c> while running.
/// </summary>
internal sealed partial class UiTeamPropose
{
    internal const int TimeoutMs = 300_000;   // 推荐编队 compares up to 8 P cards (~12 s each, 2026-10-14)
    private readonly string _repoRoot;
    private readonly Func<string> _profilePath;
    private readonly Func<string, string, ProcessStartInfo>? _command;
    private readonly object _gate = new();
    private Process? _process;
    private long _startedMs;
    private string _state = "idle";
    private string? _error;
    private string? _pCard;
    private string? _finishedAt;
    private int _generation;
    private int[]? _progress;
    private readonly string _outputGameAssets;

    internal UiTeamPropose(string repoRoot, Func<string> profilePath, Func<string, string, ProcessStartInfo>? command = null,
        string? outputGameAssets = null)
    {
        _repoRoot = repoRoot;
        _outputGameAssets = Path.GetFullPath(outputGameAssets ?? Path.Combine(UiPaths.OutputUiFolder(), "game-assets"));
        _profilePath = profilePath;
        _command = command;
    }

    [GeneratedRegex(@"^1\d{9}$")]
    private static partial Regex PCardId();

    [GeneratedRegex(@"^\{""progress"": \[(\d{1,3}), (\d{1,3})\]\}$")]
    private static partial Regex ProgressLine();

    /// <summary>[done, total] from one recommender stdout line, or null (anything else, or done &gt; total).</summary>
    internal static int[]? ParseProgress(string? line)
    {
        var match = ProgressLine().Match(line ?? "");
        if (!match.Success) { return null; }
        int done = int.Parse(match.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture);
        int total = int.Parse(match.Groups[2].Value, System.Globalization.CultureInfo.InvariantCulture);
        return total > 0 && done <= total ? new[] { done, total } : null;
    }

    /// <summary>pCard: a P card id, or "auto" (推荐编队, 2026-10-14: Maa picks the P card); primary: Vo/Da/Vi or "auto"
    /// (each P card's own specialty).</summary>
    internal static bool ValidRequest(string? pCard, string? primary) =>
        pCard is not null && (pCard == "auto" || PCardId().IsMatch(pCard)) && primary is "Vo" or "Da" or "Vi" or "auto";

    internal ProcessStartInfo DefaultCommand(string pCard, string primary)
    {
        var python = Path.Combine(_repoRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) { throw new InvalidOperationException("python_env_missing"); }
        var info = UiEngineSupervisor.NewStartInfo(_repoRoot, python);
        info.RedirectStandardInput = false;
        foreach (var arg in new[] { "tools/auto_team_propose.py", "--owned", _profilePath(), "--p-card", pCard, "--primary", primary,
                     "--use-recognised-levels", "--progress" })
        { info.ArgumentList.Add(arg); }
        return info;
    }

    /// <summary>Null when started; otherwise a refusal code.</summary>
    internal string? Start(string pCard, string primary)
    {
        if (!ValidRequest(pCard, primary)) { return "invalid_request"; }
        lock (_gate)
        {
            Poll();
            if (_process is not null) { return "already_running"; }
            ProcessStartInfo info;
            try { info = (_command ?? DefaultCommand)(pCard, primary); }
            catch (InvalidOperationException) { return "python_env_missing"; }
            var process = new Process { StartInfo = info };
            try { if (!process.Start()) { throw new InvalidOperationException("not started"); } }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                process.Dispose();
                _state = "failed"; _error = "start_failed";
                return "start_failed";
            }
            _process = process;
            _progress = null;
            // Drain the pipes so a chatty process never blocks; keep only this process's latest progress line.
            _process.OutputDataReceived += (_, e) =>
            {
                if (ParseProgress(e.Data) is not { } step) { return; }
                lock (_gate) { if (ReferenceEquals(_process, process)) { _progress = step; } }
            };
            _process.ErrorDataReceived += (_, _) => { };
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();
            _startedMs = Environment.TickCount64;
            _state = "running";
            _error = null;
            _pCard = pCard;
            _generation++;
            return null;
        }
    }

    /// <summary>Called from the UI timer: finishes or times out the process (never retried).</summary>
    internal void Poll()
    {
        lock (_gate)
        {
            if (_process is null) { return; }
            if (!_process.HasExited)
            {
                if (Environment.TickCount64 - _startedMs < TimeoutMs) { return; }
                try { _process.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
                Finish("failed", "timeout");
                return;
            }
            var ok = _process.ExitCode == 0;
            if (ok && !PublishProposal()) { Finish("failed", "publish_failed"); return; }
            Finish(ok ? "done" : "failed", ok ? null : "exit_" + _process.ExitCode);
        }
    }

    /// <summary>
    /// The recommender writes the repo's ui/game-assets/team-proposal.js; the panel is served from the host's output ui folder
    /// (2026-10-13 live: the panel kept showing 暂无推荐 from the stale copy), so copy it there like the other game assets.
    /// </summary>
    private bool PublishProposal()
    {
        try
        {
            var from = Path.GetFullPath(Path.Combine(_repoRoot, "ui", "game-assets", "team-proposal.js"));
            var to = Path.Combine(_outputGameAssets, "team-proposal.js");
            if (!File.Exists(from)) { return false; }
            if (string.Equals(from, Path.GetFullPath(to), StringComparison.OrdinalIgnoreCase)) { return true; }
            Directory.CreateDirectory(_outputGameAssets);
            File.Copy(from, to, overwrite: true);
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { return false; }
    }

    private void Finish(string state, string? error)
    {
        _process?.Dispose();
        _process = null;
        _state = state;
        _error = error;
        _finishedAt = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz", System.Globalization.CultureInfo.InvariantCulture);
    }

    internal JsonObject Status()
    {
        lock (_gate)
        {
            Poll();
            return new JsonObject
            {
                ["state"] = _state,
                ["error"] = _error,
                ["p_card"] = _pCard,
                ["finished_at"] = _finishedAt,
                ["generation"] = _generation,
                ["progress"] = _state == "running" && _progress is { } step
                    ? new JsonObject { ["done"] = step[0], ["total"] = step[1] } : null,
            };
        }
    }
}

using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MaaShinyColors.BrowserHost;

/// <summary>
/// Opt-in, per-game-day, per-action bounded ledger for closed non-consuming navigation.
/// Pending is persisted before the native gesture; an unconfirmed outcome blocks every
/// repeatable action for that game day. Never resets, never touches one-shot ledgers.
/// </summary>
internal sealed class RepeatableNavigationBudget
{
    internal const int PerActionMax = 3;
    internal const int DayMax = 60;
    internal const string ProofKind = "SC.RepeatableNavigation.Postcondition";
    /// <summary>A fixed Home tap that landed on the inert backdrop of a whitelisted popup covering Home.
    /// Recorded as "interrupted": counts toward the caps and unblocks the day, never a success.</summary>
    internal const string InterruptedProofKind = "SC.RepeatableNavigation.Interrupted";
    internal const int PanelMargin = 8;
    // Closed; mirrors agent/core/navigation_interrupt.py. (x, y, w, h), border included, native 1280x720.
    internal static readonly IReadOnlyDictionary<string, int[][]> InterruptPanels = new Dictionary<string, int[][]>(StringComparer.Ordinal)
    {
        ["MissionCompleted"] = new[] { new[] { 346, 69, 588, 575 } },
        ["Pickup"] = new[] { new[] { 129, 31, 1022, 611 }, new[] { 536, 646, 196, 56 } },
        ["Reward"] = new[] { new[] { 347, 128, 586, 458 } },
    };
    internal static readonly IReadOnlyDictionary<string, string> InterruptPreGroup = new Dictionary<string, string>(StringComparer.Ordinal)
    { ["home-mission"] = "Home", ["home-fes"] = "Home", ["home-idol"] = "Home", ["home-shop"] = "Home" };
    internal static bool OutsidePanels(Point point, string popup) => InterruptPanels.TryGetValue(popup, out var rects) &&
        rects.All(r => point.X < r[0] - PanelMargin || point.X >= r[0] + r[2] + PanelMargin ||
            point.Y < r[1] - PanelMargin || point.Y >= r[1] + r[3] + PanelMargin);
    // Closed and constant: no consumption, no scroll, no training-selection/confirm, no one-shot touch-to-start
    // (the daily game-start uses the same point but its own guarded daily vocabulary and ledger).
    internal static readonly string[] Actions = { "reward-ack", "login-board-skip", "announcement-back", "home-mission",
        "mission-complete-to-mission", "mission-back", "mission-help-close", "home-fes", "fes-match-entry", "match-help-close",
        "match-skip-open", "match-skip-cancel", "match-back", "training-back", "pickup-close", "story-skip-open",
        "training-help-close", "game-start", "home-idol", "idol-training", "training-list-back", "idol-back",
        // Non-consuming shop navigation only; the three exchange confirms stay one-shot per rank and day.
        // Opening an offer is repeatable because bought cards vanish and rank 2/3 reuse position 1.
        "home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel",
        "shop-offer-open-1", "shop-offer-open-2", "shop-offer-open-3", "shop-result-close",
        // Non-consuming Grade Fes navigation only; the difficulty circles stay one-shot per day.
        "fes-grade-entry", "fes-help-close", "grade-join-open", "grade-mode-grade", "grade-back" };
    private static readonly JsonSerializerOptions Options = new() { UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow };
    private sealed record Entry(string Action, string Nonce, string PreFrameSha256, int Pid, string State, bool NativeSent,
        string? PostFrameSha256 = null, string? ProofSha256 = null);
    private sealed record Ledger(int Version, string GameDay, int PerActionMax, int DayMax, Entry[] History);
    internal sealed record Snapshot(string GameDay, string State, int ActionCount, int DayCount);

    private readonly Func<DateTimeOffset> _utcNow;
    internal string LedgerRoot { get; }

    internal RepeatableNavigationBudget() : this(DefaultRoot(), () => DateTimeOffset.UtcNow) { }
    // The clock and root are injected only by the offline self-test; requests never choose them.
    internal RepeatableNavigationBudget(string ledgerRoot, Func<DateTimeOffset> utcNow)
    {
        var root = System.IO.Path.GetFullPath(ledgerRoot);
        var prefix = System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private") + System.IO.Path.DirectorySeparatorChar;
        Require(root.StartsWith(prefix, StringComparison.OrdinalIgnoreCase), "可重复导航账本必须位于private。");
        LedgerRoot = root; _utcNow = utcNow;
    }

    internal static string DefaultRoot() => System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "daily_navigation_repeatable");
    internal static string ProducerPath() => System.IO.Path.Combine(SelectionLoopBudget.Root(), "tools", "run_daily_navigation.py");
    /// <summary>Shop navigation proofs come only from the shop runner; all others from the navigation runner.</summary>
    internal static string ProducerPath(string action) => DailyNavigationPolicy.IsShop(action)
        ? System.IO.Path.Combine(SelectionLoopBudget.Root(), "tools", "run_daily_shop.py")
        : DailyNavigationPolicy.IsGrade(action) ? System.IO.Path.Combine(SelectionLoopBudget.Root(), "tools", "run_daily_grade.py")
        : ProducerPath();
    internal static bool IsRepeatable(string? action) => action is not null && Actions.Contains(action, StringComparer.Ordinal);
    /// <summary>JST (UTC+9) calendar date with the game's 05:00 rollover.</summary>
    internal static string GameDayOf(DateTimeOffset now) =>
        now.ToUniversalTime().AddHours(9 - 5).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
    internal string CurrentGameDay() => GameDayOf(_utcNow());
    internal string LedgerPath(string day) => System.IO.Path.Combine(LedgerRoot, day, "ledger.json");
    private static void Require(bool valid, string reason) { if (!valid) { throw new InvalidOperationException(reason); } }

    private static void Unique(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            var properties = value.EnumerateObject().ToArray();
            Require(properties.Select(p => p.Name).Distinct(StringComparer.Ordinal).Count() == properties.Length, "重复JSON字段。");
            foreach (var property in properties) { Unique(property.Value); }
        }
        else if (value.ValueKind == JsonValueKind.Array) { foreach (var item in value.EnumerateArray()) { Unique(item); } }
    }
    private static void Keys(JsonElement root, params string[] keys) => Require(root.ValueKind == JsonValueKind.Object &&
        root.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal).SequenceEqual(keys.Order(StringComparer.Ordinal)),
        "可重复导航字段白名单变化。");

    private static Ledger Read(FileStream file, string day)
    {
        file.Position = 0;
        using var buffer = new MemoryStream(); file.CopyTo(buffer);
        using var document = JsonDocument.Parse(buffer.ToArray()); Unique(document.RootElement);
        Keys(document.RootElement, "Version", "GameDay", "PerActionMax", "DayMax", "History");
        var ledger = document.RootElement.Deserialize<Ledger>(Options) ?? throw new InvalidOperationException("可重复导航账本损坏。");
        var history = ledger.History;
        Require(ledger.Version == 1 && ledger.GameDay == day && ledger.PerActionMax == PerActionMax && ledger.DayMax == DayMax &&
            history is not null && history.Length <= DayMax &&
            history.All(e => e is not null && IsRepeatable(e.Action) && SelectionLoopBudget.Nonce(e.Nonce) &&
                SelectionLoopBudget.Sha(e.PreFrameSha256) && e.Pid > 0 &&
                (e.State == "pending" && e.PostFrameSha256 is null && e.ProofSha256 is null ||
                 (e.State == "confirmed" || e.State == "interrupted" && InterruptPreGroup.ContainsKey(e.Action)) && e.NativeSent &&
                 SelectionLoopBudget.Sha(e.PostFrameSha256) && SelectionLoopBudget.Sha(e.ProofSha256))) &&
            history.Select(e => e.Nonce).Distinct(StringComparer.Ordinal).Count() == history.Length &&
            // Identical pixels may legitimately recur on a static screen; only consecutive reuse is a replay.
            history.Zip(history.Skip(1)).All(pair => pair.First.PreFrameSha256 != pair.Second.PreFrameSha256) &&
            history.GroupBy(e => e.Action, StringComparer.Ordinal).All(g => g.Count() <= PerActionMax) &&
            history.Take(Math.Max(0, history.Length - 1)).All(e => e.State != "pending"),
            "可重复导航账本无效或损坏；禁止重置或恢复预算。");
        return ledger;
    }
    private static void Write(FileStream file, Ledger ledger)
    { file.Position = 0; file.SetLength(0); JsonSerializer.Serialize(file, ledger, Options); file.Flush(flushToDisk: true); }
    private FileStream? OpenExisting(string day)
    {
        var path = LedgerPath(day);
        return File.Exists(path) ? new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None) : null;
    }

    internal Snapshot Inspect(string action)
    {
        Require(IsRepeatable(action), "非可重复导航动作。");
        var day = CurrentGameDay();
        using var file = OpenExisting(day);
        if (file is null) { return new(day, "ready", 0, 0); }
        var ledger = Read(file, day);
        return new(day, ledger.History.LastOrDefault()?.State ?? "ready",
            ledger.History.Count(e => e.Action == action), ledger.History.Length);
    }

    /// <summary>Persists a pending entry before any gesture. Returns null when accepted, otherwise a refusal reason.</summary>
    internal string? Begin(string action, string nonce, string frameSha256)
    {
        if (!IsRepeatable(action)) { return "repeatable_action_not_whitelisted"; }
        Require(SelectionLoopBudget.Nonce(nonce) && SelectionLoopBudget.Sha(frameSha256), "可重复导航nonce或帧摘要无效。");
        var day = CurrentGameDay();
        var path = LedgerPath(day);
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
        FileStream file;
        Ledger ledger;
        try
        {
            file = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            ledger = new(1, day, PerActionMax, DayMax, Array.Empty<Entry>());
            try { Write(file, ledger); } catch { file.Dispose(); throw; }
        }
        catch (IOException) when (File.Exists(path))
        {
            // An existing empty or truncated ledger may hide a pending gesture: refuse rather than recreate.
            file = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            try { ledger = Read(file, day); } catch { file.Dispose(); throw; }
        }
        using (file)
        {
            var history = ledger.History;
            if (history.Any(e => e.State == "pending")) { return "pending_outcome_blocks_day"; }
            if (history.Count(e => e.Action == action) >= PerActionMax) { return "per_action_cap_reached"; }
            if (history.Length >= DayMax) { return "day_cap_reached"; }
            if (history.Any(e => e.Nonce == nonce) || history.LastOrDefault()?.PreFrameSha256 == frameSha256)
            { return "nonce_or_frame_replay"; }
            Write(file, ledger with { History = history.Append(new(action, nonce, frameSha256, Environment.ProcessId, "pending", false)).ToArray() });
            return null;
        }
    }

    // A gesture started just before 05:00 JST may be completed after rollover.
    private (FileStream File, Ledger Ledger, string Day)? FindPending(string nonce)
    {
        var now = _utcNow();
        foreach (var day in new[] { GameDayOf(now), GameDayOf(now.AddDays(-1)) }.Distinct(StringComparer.Ordinal))
        {
            var file = OpenExisting(day);
            if (file is null) { continue; }
            try
            {
                var ledger = Read(file, day); var last = ledger.History.LastOrDefault();
                if (last is not null && last.Nonce == nonce && last.State == "pending") { return (file, ledger, day); }
            }
            catch { file.Dispose(); throw; }
            file.Dispose();
        }
        return null;
    }

    internal void MarkNativeSent(string nonce)
    {
        var found = FindPending(nonce);
        Require(found is not null, "可重复导航pending不匹配。");
        var (file, ledger, _) = found!.Value;
        using (file)
        {
            var last = ledger.History[^1];
            Require(last.Pid == Environment.ProcessId && !last.NativeSent, "可重复导航pending身份不匹配。");
            Write(file, ledger with { History = ledger.History[..^1].Append(last with { NativeSent = true }).ToArray() });
        }
    }

    internal bool Confirm(string action, string nonce, string postSha, string proofPath, string proofSha) =>
        ConfirmOutcome(action, nonce, postSha, proofPath, proofSha) is not null;

    /// <summary>Returns "confirmed" or "interrupted" when the pending entry was closed, null on identity mismatch;
    /// throws on an invalid proof. Either way input is never resent.</summary>
    internal string? ConfirmOutcome(string action, string nonce, string postSha, string proofPath, string proofSha)
    {
        Require(IsRepeatable(action) && SelectionLoopBudget.Nonce(nonce) && SelectionLoopBudget.Sha(postSha) && SelectionLoopBudget.Sha(proofSha),
            "可重复导航确认绑定无效。");
        proofPath = SelectionLoopBudget.PrivateFile(proofPath);
        Require(SelectionLoopBudget.Hash(proofPath) == proofSha, "可重复导航后置证明SHA变化。");
        using var document = SelectionLoopBudget.Json(proofPath); var proof = document.RootElement;
        var kind = proof.ValueKind == JsonValueKind.Object && proof.TryGetProperty("kind", out var kindValue) &&
            kindValue.ValueKind == JsonValueKind.String ? kindValue.GetString() : null;
        Require(kind == ProofKind || kind == InterruptedProofKind, "可重复导航证明类型无效。");
        var interrupted = kind == InterruptedProofKind;
        var fields = new[] { "version", "kind", "action", "passed", "resource_consumption", "input_attempts", "nonce", "session_pid",
            "pre_frame_sha256", "post_frame", "configuration_sha256", "foreground_sha256", "post_recognitions", "foreground",
            "producer_path", "producer_sha256" };
        Keys(proof, interrupted ? fields.Append("interruption").ToArray() : fields);
        var producer = System.IO.Path.GetFullPath(ProducerPath(action));
        var claimed = proof.GetProperty("producer_path");
        Require(proof.GetProperty("version").ValueKind == JsonValueKind.Number && proof.GetProperty("version").GetInt32() == 1 &&
            proof.GetProperty("action").GetString() == action &&
            proof.GetProperty("passed").ValueKind == (interrupted ? JsonValueKind.False : JsonValueKind.True) &&
            proof.GetProperty("resource_consumption").ValueKind == JsonValueKind.False &&
            proof.GetProperty("input_attempts").ValueKind == JsonValueKind.Number && proof.GetProperty("input_attempts").GetInt32() == 1 &&
            proof.GetProperty("nonce").GetString() == nonce &&
            proof.GetProperty("session_pid").ValueKind == JsonValueKind.Number && proof.GetProperty("session_pid").GetInt32() == Environment.ProcessId &&
            SelectionLoopBudget.Sha(proof.GetProperty("pre_frame_sha256").GetString()) &&
            SelectionLoopBudget.Sha(proof.GetProperty("configuration_sha256").GetString()) &&
            SelectionLoopBudget.Sha(proof.GetProperty("foreground_sha256").GetString()) &&
            proof.GetProperty("post_recognitions").ValueKind == JsonValueKind.Array &&
            proof.GetProperty("foreground").ValueKind == JsonValueKind.Object &&
            claimed.ValueKind == JsonValueKind.String &&
            string.Equals(System.IO.Path.GetFullPath(claimed.GetString()!), producer, StringComparison.OrdinalIgnoreCase) &&
            File.Exists(producer) && SelectionLoopBudget.Hash(producer) == proof.GetProperty("producer_sha256").GetString(),
            "可重复导航证明的类型、动作、PID或生产者不匹配。");
        var frame = proof.GetProperty("post_frame"); Keys(frame, "image", "sha256");
        Require(frame.GetProperty("sha256").GetString() == postSha &&
            SelectionLoopBudget.Hash(SelectionLoopBudget.PrivateFile(frame.GetProperty("image").GetString()!)) == postSha,
            "可重复导航后帧不匹配。");
        if (interrupted) { CheckInterruption(action, proof.GetProperty("interruption")); }
        var preSha = proof.GetProperty("pre_frame_sha256").GetString()!;
        var found = FindPending(nonce);
        if (found is null) { return null; }
        var (file, ledger, _) = found.Value;
        using (file)
        {
            var last = ledger.History[^1];
            if (last.Action != action || !last.NativeSent || last.Pid != Environment.ProcessId || last.PreFrameSha256 != preSha ||
                postSha == preSha) { return null; }
            var state = interrupted ? "interrupted" : "confirmed";
            Write(file, ledger with { History = ledger.History[..^1].Append(last with {
                State = state, PostFrameSha256 = postSha, ProofSha256 = proofSha }).ToArray() });
            return state;
        }
    }

    /// <summary>Closed interruption evidence: whitelisted popup, its fixed panel table, the action's own fixed
    /// point outside every panel, Home as both pre and underlying page, and the expected page not reached.</summary>
    private static void CheckInterruption(string action, JsonElement value)
    {
        Keys(value, "popup", "pre_group", "underlying_group", "click_point", "panel_rects", "panel_margin",
            "popup_complete", "underlying_present", "post_differs", "expected_post_complete");
        var popup = value.GetProperty("popup").ValueKind == JsonValueKind.String ? value.GetProperty("popup").GetString()! : "";
        var preGroup = InterruptPreGroup.TryGetValue(action, out var group) ? group : null;
        var rects = InterruptPanels.TryGetValue(popup, out var table) ? table : null;
        Require(preGroup is not null && rects is not null, "中断证明的动作或弹窗不在白名单。");
        static int[] Ints(JsonElement array) => array.ValueKind == JsonValueKind.Array &&
            array.EnumerateArray().All(i => i.ValueKind == JsonValueKind.Number && i.TryGetInt32(out _))
            ? array.EnumerateArray().Select(i => i.GetInt32()).ToArray() : Array.Empty<int>();
        var claimedRects = value.GetProperty("panel_rects");
        var point = Ints(value.GetProperty("click_point"));
        var fixedPoint = DailyNavigationPolicy.FixedPoint(action);
        Require(claimedRects.ValueKind == JsonValueKind.Array && claimedRects.GetArrayLength() == rects!.Length &&
            claimedRects.EnumerateArray().Zip(rects).All(pair => Ints(pair.First).SequenceEqual(pair.Second)) &&
            value.GetProperty("panel_margin").ValueKind == JsonValueKind.Number && value.GetProperty("panel_margin").TryGetInt32(out var margin) &&
            margin == PanelMargin &&
            value.GetProperty("pre_group").ValueKind == JsonValueKind.String && value.GetProperty("pre_group").GetString() == preGroup &&
            value.GetProperty("underlying_group").ValueKind == JsonValueKind.String && value.GetProperty("underlying_group").GetString() == preGroup &&
            point.Length == 2 && point[0] == fixedPoint.X && point[1] == fixedPoint.Y && OutsidePanels(fixedPoint, popup) &&
            value.GetProperty("popup_complete").ValueKind == JsonValueKind.True &&
            value.GetProperty("underlying_present").ValueKind == JsonValueKind.True &&
            value.GetProperty("post_differs").ValueKind == JsonValueKind.True &&
            value.GetProperty("expected_post_complete").ValueKind == JsonValueKind.False,
            "中断证明条件不成立。");
    }
}

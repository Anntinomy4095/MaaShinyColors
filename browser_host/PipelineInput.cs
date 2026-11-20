using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost;

internal sealed record PipelineFrameRequest(string Action, string FrameSha256, int SourceWidth, int SourceHeight,
    string Nonce, long CaptureSequence, long BindingSequence);
internal sealed record PipelineSpendGrant(string Action, string FrameSha256, string Nonce, long ExpiresAt,
    long CaptureSequence, long BindingSequence, string Hmac);
internal sealed record PipelineClickRequest(PipelineFrameRequest Frame, PipelineSpendGrant? Grant, string? NodeName = null, Point? Target = null,
    AppealTiming? AppealTiming = null, int? HoldMs = null, Point? DragTo = null, int? DragMs = null)
{
    internal string Action => Frame.Action;
    internal AppealTimingObservation? TimingObservation { get; set; }
}
/// <summary>
/// In-memory limits. Nothing here is persisted. RunMax, RunSpendMax and SessionMaxMs apply to one run window: it
/// starts when the host starts and again after each accepted <c>pipeline-run-begin</c> from a launcher (see
/// <see cref="PipelineInputGate.BeginRun"/>); SessionMaxMs counts from the window's first admitted gesture. The gap
/// between two gestures (MinIntervalMs plus a fresh uniform 0..IntervalJitterMaxMs, only ever longer), the per-action
/// per-minute limit and nonce/grant single-use sets span the whole host lifetime and are never reset. Each consuming
/// click still needs its own frame-bound grant; RunSpendMax is an additional hard ceiling, not spending authorization.
/// </summary>
/// 2026-10-28 (user: 「不能完全去掉点击上限吗」): RunMax 0 = no click cap (the default, as in MaaFramework / MAA); a positive
/// value still caps a window (self-tests). SessionMaxMs 12 h, PerActionPerMinute 60 (the 800 ms gap already paces input).
internal sealed record PipelineLimits(int MinIntervalMs = 800, int IntervalJitterMaxMs = 200, int PerActionPerMinute = 60, int RunMax = 0,
    int RunSpendMax = 64, int GrantMaxTtlMs = 5000, int SessionMaxMs = 43_200_000);

/// <summary>Request format of the opt-in <c>pipeline-click</c> transport; the vocabulary is <see cref="PipelineActionTable"/>.</summary>
internal static class PipelineInputPolicy
{
    internal const string Transport = "webview2-composition-pipeline";
    internal const string Command = "pipeline-click";
    /// <summary>Launcher-only handshake that opens a new run window (never sent by the Agent mid-run).</summary>
    internal const string RunBeginCommand = "pipeline-run-begin";
    internal const string SessionSuffix = ".pipeline-input.json";
    internal const int ProtocolVersion = 2;
    internal const string FrameBindingProtocol = "SC.PipelineFrameBinding.v2";
    internal const string GrantDomain = "SC.PipelineSpendGrant.v2";
    /// <summary>2026-10-14 user: long-press ＋ in the Match Skip dialog (non-consuming counter only). v2 (2026-11-01 user):
    /// also 「諦める」 in the Produce 「ホームに戻る・諦める」 dialog, which is selectable only by a long press.</summary>
    internal const string HoldProtocol = "SC.PipelineHold.v2";
    internal const string HoldActionPattern = "^(match-skip-plus-[1-6]|wing-giveup-hold)$";
    internal const int HoldMinMs = 300, HoldMaxMs = 4000;
    /// <summary>2026-11-18 user (Collabo Fes: 「以引入新功能拖动为目标」): a drag of a table action that declares
    /// <c>drag_rect</c>; the request carries the recognised start (<c>target</c>, inside the action rect), the end
    /// (<c>drag_to</c>, inside drag_rect) and <c>drag_ms</c>. Drag actions are never clicked plainly.</summary>
    internal const string DragProtocol = "SC.PipelineDrag.v1";
    internal const int DragMinMs = 150, DragMaxMs = 3000;
    private static readonly string[] RequestKeys = { "action", "capture_seq", "command", "frame_binding_seq", "frame_sha256",
        "nonce", "protocol_version", "source_height", "source_width", "token" };
    private static readonly string[] RunBeginKeys = { "command", "nonce", "protocol_version", "token" };
    private static readonly string[] GrantKeys = { "action", "capture_seq", "expires_at", "frame_binding_seq", "frame_sha256", "hmac", "nonce" };

    internal static string GrantMessage(string action, string frameSha256, string nonce, long expiresAt, long captureSequence, long bindingSequence) =>
        $"{GrantDomain}\n{action}\n{frameSha256}\n{nonce}\n{expiresAt}\n{captureSequence}\n{bindingSequence}";
    internal static string Sign(byte[] secret, string action, string frameSha256, string nonce, long expiresAt, long captureSequence, long bindingSequence) =>
        Convert.ToHexString(HMACSHA256.HashData(secret, Encoding.UTF8.GetBytes(
            GrantMessage(action, frameSha256, nonce, expiresAt, captureSequence, bindingSequence)))).ToLowerInvariant();

    internal static bool FrameMatches(PipelineFrameRequest frame, string? sha, int? width, int? height, double ageMs,
        long captureSequence, long bindingSequence, bool capturePending = false) =>
        !capturePending && frame.CaptureSequence > 0 && frame.BindingSequence > 0 &&
        frame.CaptureSequence == captureSequence && frame.BindingSequence == bindingSequence &&
        DailyNavigationPolicy.FrameMatches(new(frame.Action, frame.FrameSha256, frame.SourceWidth, frame.SourceHeight, frame.Nonce),
            sha, width, height, ageMs);

    private static string[] Names(JsonElement root)
    {
        var names = root.EnumerateObject().Select(item => item.Name).ToArray();
        if (names.Distinct(StringComparer.Ordinal).Count() != names.Length) { throw new InvalidOperationException("Pipeline 请求含重复字段。"); }
        return names.Order(StringComparer.Ordinal).ToArray();
    }
    private static string Text(JsonElement root, string name) =>
        root.GetProperty(name) is { ValueKind: JsonValueKind.String } value ? value.GetString()! : throw new InvalidOperationException("Pipeline 字段类型无效。");
    private static bool Sha(string value) => Regex.IsMatch(value, "\\A[0-9a-f]{64}\\z");
    internal static bool Nonce(string value) => Regex.IsMatch(value, "\\A[0-9a-f]{32}\\z");
    private static long Sequence(JsonElement root, string name) =>
        root.GetProperty(name) is { ValueKind: JsonValueKind.Number } value && value.TryGetInt64(out var sequence) && sequence > 0
            ? sequence : throw new InvalidOperationException("Pipeline 帧序号必须为正 int64。");

    internal static string? ActionNodeRefusal(PipelineActionTable table, ProduceExploreGrid? explore, string action, string? node)
    {
        if (table.IsAction(action)) { return table.NodeRefusal(action, node); }
        if (explore?.IsCell(action) == true) { return node is null ? null : "explore_node_not_allowed"; }
        return "action_not_in_pipeline_vocabulary";
    }

    // Match's observed row can move after scrolling; only its six Plus nodes may supply a point.
    internal static string? TargetRefusal(PipelineActionTable table, string action, string? node, Point? target)
    {
        if (target is not Point point) { return null; }
        if (table.IsAction(action) && table.DragOf(action) is not null)
        { return table.Get(action).Rect.Contains(point) ? null : "pipeline_target_out_of_bounds"; }
        var match = Regex.Match(action, "\\Amatch-skip-plus-([1-6])\\z");
        if (!match.Success || !table.IsAction(action)) { return "pipeline_target_not_allowed"; }
        if (node != $"SC.Match.Skip.Plus{match.Groups[1].Value}") { return "pipeline_target_node_mismatch"; }
        var entry = table.Get(action);
        if (entry.Consuming || entry.Wheel is not null) { return "pipeline_target_not_allowed"; }
        return entry.Rect.Contains(point) ? null : "pipeline_target_out_of_bounds";
    }

    /// <summary>A drag action needs exactly a start target and an end inside its drag_rect; anything else never drags.</summary>
    internal static string? DragRefusal(PipelineActionTable table, string action, Point? target, Point? dragTo, int? dragMs)
    {
        var drag = table.IsAction(action) ? table.DragOf(action) : null;
        if (drag is null) { return dragTo is null && dragMs is null ? null : "pipeline_drag_not_allowed"; }
        if (target is null || dragTo is not Point end || dragMs is not int ms) { return "pipeline_drag_required"; }
        if (ms < DragMinMs || ms > DragMaxMs) { return "pipeline_drag_duration_invalid"; }
        return drag.Value.Contains(end) ? null : "pipeline_drag_end_out_of_bounds";
    }

    /// <summary>True when the request names <see cref="RunBeginCommand"/> (the body is then parsed by <see cref="ParseRunBegin"/>).</summary>
    internal static bool IsRunBegin(JsonElement root) => root.ValueKind == JsonValueKind.Object &&
        root.TryGetProperty("command", out var command) && command.ValueKind == JsonValueKind.String && command.GetString() == RunBeginCommand;

    /// <summary>Exactly {command, nonce, protocol_version, token}; returns the single-use nonce.</summary>
    internal static string ParseRunBegin(JsonElement root, string token)
    {
        if (root.ValueKind != JsonValueKind.Object) { throw new InvalidOperationException("Pipeline 请求必须为对象。"); }
        if (!Names(root).SequenceEqual(RunBeginKeys) ||
            !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(Text(root, "token")), Encoding.UTF8.GetBytes(token)) ||
            Text(root, "command") != RunBeginCommand || root.GetProperty("protocol_version").ValueKind != JsonValueKind.Number ||
            !root.GetProperty("protocol_version").TryGetInt32(out var version) || version != ProtocolVersion)
        { throw new InvalidOperationException("Pipeline 运行开始请求字段或认证无效。"); }
        var nonce = Text(root, "nonce");
        if (!Nonce(nonce)) { throw new InvalidOperationException("Pipeline 运行开始单次编号无效。"); }
        return nonce;
    }

    /// <summary><paramref name="explore"/> is non-null only with <c>--enable-produce-explore</c>; its cell ids are then also accepted.</summary>
    internal static PipelineClickRequest Parse(JsonElement root, string token, PipelineActionTable table, ProduceExploreGrid? explore = null)
    {
        if (root.ValueKind != JsonValueKind.Object) { throw new InvalidOperationException("Pipeline 请求必须为对象。"); }
        var names = Names(root);
        var hasGrant = names.Contains("spend_grant", StringComparer.Ordinal);
        if (!names.Where(name => name != "spend_grant" && name != "node_name" && name != "target" && name != "appeal_timing" && name != "hold_ms" && name != "drag_to" && name != "drag_ms").SequenceEqual(RequestKeys) ||
            !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(Text(root, "token")), Encoding.UTF8.GetBytes(token)) ||
            Text(root, "command") != Command || root.GetProperty("protocol_version").GetInt32() != ProtocolVersion)
        { throw new InvalidOperationException("Pipeline 请求字段或认证无效。"); }
        var action = Text(root, "action");
        var node = names.Contains("node_name", StringComparer.Ordinal) ? Text(root, "node_name") : null;
        if (ActionNodeRefusal(table, explore, action, node) is string bindingRefusal)
        { throw new InvalidOperationException(bindingRefusal); }
        Point? target = null;
        if (root.TryGetProperty("target", out var targetValue))
        {
            if (targetValue.ValueKind != JsonValueKind.Array || targetValue.GetArrayLength() != 2 ||
                targetValue[0].ValueKind != JsonValueKind.Number || !targetValue[0].TryGetInt32(out var x) ||
                targetValue[1].ValueKind != JsonValueKind.Number || !targetValue[1].TryGetInt32(out var y))
            { throw new InvalidOperationException("Pipeline 点击目标必须为两个整数。"); }
            target = new Point(x, y);
            if (TargetRefusal(table, action, node, target) is string targetRefusal)
            { throw new InvalidOperationException(targetRefusal); }
        }
        var sha = Text(root, "frame_sha256");
        var nonce = Text(root, "nonce");
        var captureSequence = Sequence(root, "capture_seq");
        var bindingSequence = Sequence(root, "frame_binding_seq");
        var width = root.GetProperty("source_width").GetInt32();
        var height = root.GetProperty("source_height").GetInt32();
        if (!(table.IsAction(action) || explore?.IsCell(action) == true) || !Sha(sha) || !Nonce(nonce) || Math.Abs((long)width - 1280) > 2 || Math.Abs((long)height - 720) > 2)
        { throw new InvalidOperationException("Pipeline 固定动作、帧、单次编号或源尺寸无效。"); }
        PipelineSpendGrant? grant = null;
        if (hasGrant)
        {
            var value = root.GetProperty("spend_grant");
            if (value.ValueKind != JsonValueKind.Object || !Names(value).SequenceEqual(GrantKeys))
            { throw new InvalidOperationException("消费授权字段无效。"); }
            var hmac = Text(value, "hmac");
            var grantSha = Text(value, "frame_sha256");
            var grantNonce = Text(value, "nonce");
            if (!Sha(hmac) || !Sha(grantSha) || !Nonce(grantNonce) || value.GetProperty("expires_at").ValueKind != JsonValueKind.Number)
            { throw new InvalidOperationException("消费授权格式无效。"); }
            grant = new(Text(value, "action"), grantSha, grantNonce, value.GetProperty("expires_at").GetInt64(),
                Sequence(value, "capture_seq"), Sequence(value, "frame_binding_seq"), hmac);
        }
        AppealTiming? timing = null;
        if (root.TryGetProperty("appeal_timing", out var timingValue))
        {
            if (timingValue.ValueKind != JsonValueKind.Object ||
                !Names(timingValue).SequenceEqual(new[] { "latest_down_ms", "period_ms", "target_down_ms" }))
            { throw new InvalidOperationException("Appeal 定时字段无效。"); }
            double Number(string key) => timingValue.GetProperty(key) is { ValueKind: JsonValueKind.Number } value &&
                value.TryGetDouble(out var number) && double.IsFinite(number) ? number
                : throw new InvalidOperationException("Appeal 定时必须为有限数值。");
            timing = new(Number("target_down_ms"), Number("period_ms"), Number("latest_down_ms"));
            if (timing.Refusal(action, node) is string timingRefusal) { throw new InvalidOperationException(timingRefusal); }
        }
        int? hold = null;
        if (root.TryGetProperty("hold_ms", out var holdValue))
        {
            if (holdValue.ValueKind != JsonValueKind.Number || !holdValue.TryGetInt32(out var ms) || ms < HoldMinMs || ms > HoldMaxMs)
            { throw new InvalidOperationException("长按时长无效。"); }
            if (!Regex.IsMatch(action, PipelineInputPolicy.HoldActionPattern) || timing is not null || hasGrant || table.ConsumingIds.Contains(action))
            { throw new InvalidOperationException("长按只用于 Match 跳过加号与 Produce「諦める」。"); }
            hold = ms;
        }
        Point? dragTo = null; int? dragMs = null;
        if (root.TryGetProperty("drag_to", out var dragValue))
        {
            if (dragValue.ValueKind != JsonValueKind.Array || dragValue.GetArrayLength() != 2 ||
                dragValue[0].ValueKind != JsonValueKind.Number || !dragValue[0].TryGetInt32(out var dx) ||
                dragValue[1].ValueKind != JsonValueKind.Number || !dragValue[1].TryGetInt32(out var dy))
            { throw new InvalidOperationException("拖动终点必须为两个整数。"); }
            dragTo = new Point(dx, dy);
        }
        if (root.TryGetProperty("drag_ms", out var dragMsValue))
        {
            if (dragMsValue.ValueKind != JsonValueKind.Number || !dragMsValue.TryGetInt32(out var dms))
            { throw new InvalidOperationException("拖动时长无效。"); }
            dragMs = dms;
        }
        if ((dragTo is not null || dragMs is not null) && (timing is not null || hold is not null || hasGrant))
        { throw new InvalidOperationException("拖动不能与定时、长按或消费授权同用。"); }
        if (DragRefusal(table, action, target, dragTo, dragMs) is string dragRefusal) { throw new InvalidOperationException(dragRefusal); }
        return new(new(action, sha, width, height, nonce, captureSequence, bindingSequence), grant, node, target, timing, hold, dragTo, dragMs);
    }
}

/// <summary>
/// Per-run state of the pipeline transport: in-memory rate limits, run/session caps, nonce/grant single use, the click
/// point choice inside the action rect and the append-only audit. No pending state, no day lock, no persistent ledger;
/// the audit files are evidence only.
/// </summary>
internal sealed class PipelineInputGate
{
    private readonly byte[] _secret;
    private readonly Func<DateTimeOffset> _utcNow;
    private readonly Func<double> _monotonicMs;
    private readonly Func<int, int> _random;
    private readonly Func<double, CancellationToken, Task> _delay;
    private readonly object _sync = new();
    private readonly HashSet<string> _nonces = new(StringComparer.Ordinal);
    private readonly HashSet<string> _grants = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Queue<double>> _recent = new(StringComparer.Ordinal);
    private double? _last;
    private double? _first;
    private double _requiredGapMs;
    private int _runCount;
    private int _spendCount;
    private int _hostSpendCount;
    private int _runWindow = 1;
    /// <summary>A new run window is refused while the previous window sent a gesture within this many ms.</summary>
    internal const int RunBeginQuietMs = 30_000;
    internal PipelineLimits Limits { get; }
    internal PipelineActionTable Table { get; }
    /// <summary>Default-off exploration grid (<c>--enable-produce-explore</c>); null refuses every explore id.</summary>
    internal ProduceExploreGrid? Explore { get; }
    internal string AuditRoot { get; }
    internal string SecretHex => Convert.ToHexString(_secret).ToLowerInvariant();
    internal int RunCount { get { lock (_sync) { return _runCount; } } }
    /// <summary>1 for the window opened by the host start, +1 per accepted <see cref="BeginRun"/>.</summary>
    internal int RunWindow { get { lock (_sync) { return _runWindow; } } }
    internal double RequiredGapMs { get { lock (_sync) { return _requiredGapMs; } } }

    internal static string DefaultAuditRoot() => System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "run_logs");
    internal PipelineInputGate(ProduceExploreGrid? explore = null) : this(PipelineActionTable.Default, explore) { }
    /// <summary>A fresh gate (new spend secret, nonce set, limits and session clock) over <paramref name="table"/>.</summary>
    internal PipelineInputGate(PipelineActionTable table, ProduceExploreGrid? explore) : this(RandomNumberGenerator.GetBytes(32), DefaultAuditRoot(), () => DateTimeOffset.UtcNow,
        () => Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency, new PipelineLimits(), table,
        RandomNumberGenerator.GetInt32, (ms, cancellation) => Task.Delay(TimeSpan.FromMilliseconds(ms), cancellation), explore) { }
    // Secret, clocks, root, limits, table, random source and delay are injected only by the offline self-test.
    internal PipelineInputGate(byte[] secret, string auditRoot, Func<DateTimeOffset> utcNow, Func<double> monotonicMs, PipelineLimits limits,
        PipelineActionTable table, Func<int, int> random, Func<double, CancellationToken, Task> delay, ProduceExploreGrid? explore = null)
    {
        var root = System.IO.Path.GetFullPath(auditRoot);
        var prefix = System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private") + System.IO.Path.DirectorySeparatorChar;
        if (!root.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) { throw new InvalidOperationException("Pipeline 审计目录必须位于private。"); }
        if (secret.Length != 32) { throw new InvalidOperationException("消费授权密钥长度无效。"); }
        if (limits.MinIntervalMs < 800 || limits.IntervalJitterMaxMs is < 0 or > 5000 || limits.PerActionPerMinute is < 1 or > 60 ||
            limits.RunMax < 0 || limits.RunSpendMax < 0 || limits.GrantMaxTtlMs is < 1 or > 5000 ||
            limits.SessionMaxMs is < 1 or > 43_200_000)
        { throw new InvalidOperationException("Pipeline 限速参数无效。"); }
        if (explore is not null) { ProduceExploreGrid.RequireDisjointIds(table); }
        _secret = secret.ToArray(); AuditRoot = root; Explore = explore; _utcNow = utcNow; _monotonicMs = monotonicMs; Limits = limits;
        Table = table; _random = random; _delay = delay; _requiredGapMs = limits.MinIntervalMs;
    }

    /// <summary>Fixed table action, or (only when the grid is enabled) an exploration cell.</summary>
    internal bool IsKnown(string? id) => Table.IsAction(id) || IsExplore(id);
    internal bool IsExplore(string? id) => Explore?.IsCell(id) == true;
    /// <summary>Exploration cells never consume; only the fixed table marks consumption.</summary>
    internal bool IsConsuming(string? id) => Table.IsConsuming(id);
    internal string FamilyOf(string id) => IsExplore(id) ? ProduceExploreGrid.Family : Table.Get(id).Family;
    private Point SamplePoint(string id) => IsExplore(id) ? Explore!.Sample(id, _random) : Table.Sample(id, _random);

    internal string GameDay() => RepeatableNavigationBudget.GameDayOf(_utcNow());
    internal string AuditPath(string day) => System.IO.Path.Combine(AuditRoot, $"host-{day}.jsonl");
    internal string SpendAuditPath(string day) => System.IO.Path.Combine(AuditRoot, $"host-spend-{day}.jsonl");

    /// <summary>How long the next gesture must still wait (ms); never negative.</summary>
    internal double WaitMs()
    {
        lock (_sync) { return _last is double last ? Math.Max(0, last + _requiredGapMs - _monotonicMs()) : 0; }
    }

    /// <summary>Validates the vocabulary, grant and limits; commits nonce/grant/rate state only when all pass.</summary>
    internal string? Admit(PipelineClickRequest request)
    {
        lock (_sync)
        {
            var action = request.Action;
            if (PipelineInputPolicy.ActionNodeRefusal(Table, Explore, action, request.NodeName) is string nodeRefusal)
            { return nodeRefusal; }
            if (PipelineInputPolicy.TargetRefusal(Table, action, request.NodeName, request.Target) is string targetRefusal)
            { return targetRefusal; }
            if (request.AppealTiming?.Refusal(action, request.NodeName) is string timingRefusal) { return timingRefusal; }
            if (!IsExplore(action) && PipelineInputPolicy.DragRefusal(Table, action, request.Target, request.DragTo, request.DragMs) is string dragRefusal)
            { return dragRefusal; }
            var consuming = IsConsuming(action);
            var grant = request.Grant;
            if (consuming)
            {
                if (grant is null) { return "spend_grant_required"; }
                var expected = PipelineInputPolicy.Sign(_secret, grant.Action, grant.FrameSha256, grant.Nonce, grant.ExpiresAt,
                    grant.CaptureSequence, grant.BindingSequence);
                if (!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(expected), Encoding.ASCII.GetBytes(grant.Hmac)))
                { return "spend_grant_hmac_invalid"; }
                if (grant.Action != action) { return "spend_grant_wrong_action"; }
                if (grant.FrameSha256 != request.Frame.FrameSha256) { return "spend_grant_frame_mismatch"; }
                if (grant.CaptureSequence != request.Frame.CaptureSequence || grant.BindingSequence != request.Frame.BindingSequence)
                { return "spend_grant_sequence_mismatch"; }
                if (grant.Nonce != request.Frame.Nonce) { return "spend_grant_nonce_mismatch"; }
                if (_grants.Contains(grant.Hmac)) { return "spend_grant_already_used"; }
                var now = _utcNow().ToUnixTimeMilliseconds();
                if (grant.ExpiresAt <= now) { return "spend_grant_expired"; }
                if (grant.ExpiresAt > now + Limits.GrantMaxTtlMs) { return "spend_grant_ttl_too_long"; }
                if (_spendCount >= Limits.RunSpendMax) { return "run_spend_cap_reached"; }
            }
            else if (grant is not null) { return "spend_grant_not_applicable"; }
            if (_nonces.Contains(request.Frame.Nonce)) { return "nonce_replayed"; }
            if (Limits.RunMax > 0 && _runCount >= Limits.RunMax) { return "run_cap_reached"; }
            var t = _monotonicMs();
            if (_first is double first && t - first >= Limits.SessionMaxMs) { return "session_time_cap_reached"; }
            if (_last is double last && t - last < _requiredGapMs) { return "rate_limited_min_interval"; }
            if (!_recent.TryGetValue(action, out var queue)) { queue = new(); _recent[action] = queue; }
            while (queue.Count > 0 && t - queue.Peek() >= 60000) { queue.Dequeue(); }
            if (queue.Count >= Limits.PerActionPerMinute) { return "rate_limited_per_action_minute"; }
            var jitter = _random(Limits.IntervalJitterMaxMs + 1);
            if (jitter < 0 || jitter > Limits.IntervalJitterMaxMs) { return "interval_jitter_invalid"; }
            _nonces.Add(request.Frame.Nonce);
            if (consuming) { _grants.Add(grant!.Hmac); _spendCount++; _hostSpendCount++; }
            _runCount++; _last = t; _first ??= t; queue.Enqueue(t);
            _requiredGapMs = Limits.MinIntervalMs + jitter;
            return null;
        }
    }

    /// <summary>A delayed appeal advances the next ordinary gap from its actual Down without a second admission.</summary>
    internal void RecordTimedDown()
    {
        lock (_sync) { _last = Math.Max(_last ?? 0, _monotonicMs()); }
    }

    /// <summary>Why a new run window cannot open now (null = it can): a gesture within <see cref="RunBeginQuietMs"/>.</summary>
    internal string? RunBeginRefusal()
    {
        lock (_sync) { return RunBeginRefusalLocked(); }
    }

    private string? RunBeginRefusalLocked() =>
        _last is double last && _monotonicMs() - last < RunBeginQuietMs ? "run_begin_refused_recent_input" : null;

    /// <summary>
    /// Opens a new run window for a launcher's new run (the <c>pipeline-run-begin</c> handshake): resets the run click
    /// count, spend count and session clock (SessionMaxMs counts again from the next admitted gesture). The cumulative
    /// host spend count, rate state (interval gap, per-action minute queue) and nonce/grant single-use sets are kept. Refused while a
    /// gesture was admitted within the last <see cref="RunBeginQuietMs"/> (a run still in progress) or for a reused
    /// nonce. The decision is audited first (<c>run_begin</c> / <c>run_begin_refused</c>); an audit failure refuses it.
    /// </summary>
    internal NavigationResult BeginRun(string nonce)
    {
        lock (_sync)
        {
            var day = GameDay();
            var refusal = !PipelineInputPolicy.Nonce(nonce) ? "run_begin_nonce_invalid"
                : _nonces.Contains(nonce) ? "nonce_replayed" : RunBeginRefusalLocked();
            object Line(string stage, string reason) => new
            {
                time = _utcNow().ToString("O"), game_day = day, pid = Environment.ProcessId, transport = PipelineInputPolicy.Transport,
                stage, action = (string?)null, nonce, reason, protocol_version = PipelineInputPolicy.ProtocolVersion,
                run_window = refusal is null ? _runWindow + 1 : _runWindow, previous_run_clicks = _runCount,
                previous_run_spends = _spendCount, host_spends = _hostSpendCount,
            };
            if (refusal is not null)
            {
                try { Append(AuditPath(day), Line("run_begin_refused", refusal)); } catch (Exception) { }
                return new(false, false, refusal);
            }
            try { Append(AuditPath(day), Line("run_begin", "run_window_opened")); }
            catch (Exception exception) { return new(false, false, "audit_write_failed_no_reset: " + exception.Message); }
            _nonces.Add(nonce);
            _runWindow++; _runCount = 0; _spendCount = 0; _first = null;
            return new(true, false, "run_window_opened");
        }
    }

    internal static void Append(string path, object record)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
        var line = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(record) + "\n");
        using var file = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read);
        file.Write(line);
        file.Flush(flushToDisk: true);
    }

    private object Record(string day, PipelineClickRequest request, string stage, string reason, Point? point = null) => new
    {
        time = _utcNow().ToString("O"), game_day = day, pid = Environment.ProcessId, transport = PipelineInputPolicy.Transport,
        stage, action = request.Action, consuming = IsConsuming(request.Action),
        frame_sha256 = request.Frame.FrameSha256, nonce = request.Frame.Nonce, reason,
        protocol_version = PipelineInputPolicy.ProtocolVersion,
        node_name = request.NodeName,
        capture_seq = request.Frame.CaptureSequence, frame_binding_seq = request.Frame.BindingSequence,
        point = point is Point p ? new[] { p.X, p.Y } : null,
        drag_to = request.DragTo is Point to ? new[] { to.X, to.Y } : null,
        drag_ms = request.DragMs,
        appeal_timing = request.TimingObservation,
    };

    /// <summary>
    /// One bounded attempt: host guard and frame binding, a bounded wait for the interval gap (then both guards again),
    /// admission, the click point inside the action rect, audit (spend audit first) written before the gesture, a
    /// re-check after that I/O, then exactly one gesture at that canonical point. Never retries.
    /// </summary>
    internal async Task<NavigationResult> Execute(PipelineClickRequest request, Func<string?> hostGuard,
        Func<PipelineFrameRequest, bool> frameBound,
        Func<PipelineClickRequest, Point, Func<string?>, Task<NavigationResult>> gesture, CancellationToken cancellation)
    {
        var day = GameDay();
        request.TimingObservation = request.AppealTiming is AppealTiming plan ? new(plan) : null;
        NavigationResult Refuse(string reason, bool attempted = false, Point? point = null)
        {
            try { Append(AuditPath(day), Record(day, request, "refused", reason, point)); } catch (Exception) { }
            return new(false, attempted, reason);
        }
        if (cancellation.IsCancellationRequested) { return Refuse("cancelled_before_input"); }
        if (hostGuard() is string hostRefusal) { return Refuse(hostRefusal); }
        if (!frameBound(request.Frame)) { return Refuse("frame_binding_guard_failed"); }
        var wait = WaitMs();
        if (wait > 0)
        {
            // Only ever slower: wait out the remaining gap, then require the same host state and frame again.
            if (wait > Limits.MinIntervalMs + Limits.IntervalJitterMaxMs) { return Refuse("interval_wait_out_of_bounds"); }
            try { await _delay(wait, cancellation); }
            catch (OperationCanceledException) { return Refuse("cancelled_before_input"); }
            if (hostGuard() is not null || !frameBound(request.Frame)) { return Refuse("frame_changed_during_interval_wait"); }
        }
        if (Admit(request) is string refusal) { return Refuse(refusal); }
        Point point;
        try { point = request.Target ?? SamplePoint(request.Action); }
        catch (Exception exception) { return Refuse("click_point_failed_no_input: " + exception.Message); }
        try
        {
            if (IsConsuming(request.Action))
            {
                Append(SpendAuditPath(day), new
                {
                    time = _utcNow().ToString("O"), game_day = day, pid = Environment.ProcessId, stage = "before_gesture",
                    action = request.Action, frame_sha256 = request.Frame.FrameSha256, nonce = request.Frame.Nonce,
                    capture_seq = request.Frame.CaptureSequence, frame_binding_seq = request.Frame.BindingSequence,
                    grant_expires_at = request.Grant!.ExpiresAt, grant_hmac_prefix = request.Grant.Hmac[..8],
                });
            }
            Append(AuditPath(day), Record(day, request, "intent", "about_to_send", point));
        }
        catch (Exception exception) { return Refuse("audit_write_failed_no_input: " + exception.Message); }
        // Recheck after disk I/O; an intervening manual gesture or navigation may invalidate the frame.
        if (cancellation.IsCancellationRequested) { return Refuse("cancelled_before_input"); }
        if (hostGuard() is not null || !frameBound(request.Frame)) { return Refuse("frame_changed_after_audit_no_retry"); }
        // The UI sender invokes this again after each hover wait and immediately before Down/Wheel.
        // Admission is never repeated: nonce/grant/rate budgets remain consumed on refusal.
        string? BeforeInput()
        {
            if (cancellation.IsCancellationRequested) { return "cancelled_before_input"; }
            if (hostGuard() is string reason) { return reason; }
            if (!frameBound(request.Frame)) { return "frame_changed_before_input_no_retry"; }
            if (IsConsuming(request.Action) && request.Grant!.ExpiresAt <= _utcNow().ToUnixTimeMilliseconds())
            { return "spend_grant_expired_before_input"; }
            return null;
        }
        if (BeforeInput() is string finalRefusal) { return Refuse(finalRefusal); }
        NavigationResult result;
        try { result = await gesture(request, point, BeforeInput); }
        catch (Exception exception) { result = new(false, true, "native_input_failed_no_retry: " + exception.Message); }
        try { Append(AuditPath(day), Record(day, request, result.ok ? "sent" : "failed", result.reason, point)); } catch (Exception) { }
        return result;
    }
}

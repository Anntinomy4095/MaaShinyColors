using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Routes UI panel postMessage JSON to whitelisted handlers. Accepts messages only from exactly
/// https://maa-ui.test/index.html, enforces the protocol size/rate limits, validates the envelope and the per-command
/// params against the embedded ui/protocol.json, and never grants live input or spending. U2: with an engine
/// supervisor, run.start launches a read-only / offline-replay run (never live, never spend flags) and run.stop writes
/// the run's stop file; without one (U1 preview, router self-test) run.start is refused and run.stop is not accepted.
/// </summary>
internal sealed class UiMessageRouter
{
    internal const string HostKind = "ui_preview";
    internal const string GameHostKind = "browser_host";
    internal const string PreviewRunRefusal = "界面预览不能运行任务（未连接引擎）。";
    internal const string NoGameRefusal = "未连接游戏窗口：请在游戏窗口中开始运行（开发者可用离线回放）。";
    private readonly UiProtocol _protocol;
    private readonly UiTaskSchema _schema;
    private readonly UiProfileStore _profiles;
    private readonly Func<long> _clockMs;
    private readonly UiEngineSupervisor? _engine;
    private readonly Func<JsonObject>? _hostStatus;
    private readonly Func<string?>? _sessionPath;
    private readonly string _hostKind;
    private readonly string? _replayRoot;
    private readonly Queue<long> _recent = new();
    private readonly Func<JsonObject>? _policy;
    private readonly IUiDevTools? _devTools;
    private readonly IUiCardScan? _cardScan;
    private readonly IUiKnowhowScan? _knowhowScan;
    private readonly UiTeamPropose? _teamPropose;     // 「重新生成推荐」: local files only (U05)
    // reads waiting for the automatic card-library refresh; each runs once its condition holds (default: refresh ended)
    private readonly List<(Func<bool> Ready, Action Then)> _afterCardRefresh = new();
    private readonly Func<string?>? _wingBlocker;   // game host only: supervised W.I.N.G. readiness (UiWingGate)
    private readonly UiLiveStatusReader? _liveStatus;   // game host only; repository root resolved from the exe, not the working directory
    private readonly List<Decision> _decisions = new();
    private long _logSeq;
    // Keeps CJK readable while still escaping HTML-sensitive characters.
    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.Create(System.Text.Unicode.UnicodeRanges.All),
        TypeInfoResolver = new System.Text.Json.Serialization.Metadata.DefaultJsonTypeInfoResolver(),
    };

    internal sealed record Decision(string Source, string? Command, bool Accepted, string? Error);
    internal sealed record RunAudit(string Mode, IReadOnlyList<string> PageFlags, IReadOnlyList<string> DerivedFlags, IReadOnlyList<string> IgnoredPageFlags);

    internal UiMessageRouter(UiProtocol protocol, UiTaskSchema schema, UiProfileStore profiles, Func<long>? clockMs = null,
        UiEngineSupervisor? engine = null, Func<JsonObject>? hostStatus = null, Func<string?>? sessionPath = null, string? replayRoot = null,
        Func<JsonObject>? policy = null, IUiDevTools? devTools = null, IUiCardScan? cardScan = null, Func<string?>? wingBlocker = null,
        IUiKnowhowScan? knowhowScan = null, UiTeamPropose? teamPropose = null)
    {
        _teamPropose = teamPropose;
        _cardScan = cardScan;
        _knowhowScan = knowhowScan;
        _wingBlocker = wingBlocker;
        _liveStatus = hostStatus is null ? null : new UiLiveStatusReader(UiPaths.RepoRoot());
        _policy = policy;
        _devTools = devTools;
        _protocol = protocol;
        _schema = schema;
        _profiles = profiles;
        _clockMs = clockMs ?? (() => Stopwatch.GetTimestamp() * 1000 / Stopwatch.Frequency);
        _engine = engine;
        _hostStatus = hostStatus;
        _sessionPath = sessionPath;
        _hostKind = hostStatus is null ? HostKind : GameHostKind;
        _replayRoot = replayRoot is null ? null : Path.GetFullPath(replayRoot);
        if (engine is not null) { engine.Output += ForwardEngine; }
        if (cardScan is not null) { cardScan.Output += ForwardEngine; }
        if (knowhowScan is not null) { knowhowScan.Output += ForwardEngine; }
    }

    internal UiEngineSupervisor? Engine => _engine;

    /// <summary>Stop every executor owned by this panel, including scans with no engine run id.</summary>
    internal bool RequestStop(string kind)
    {
        var accepted = _engine?.RequestStop(kind) ?? false;
        accepted |= _cardScan?.RequestStop() ?? false;
        accepted |= _knowhowScan?.RequestStop() ?? false;
        accepted |= CardLibrary?.RequestStop() ?? false;
        accepted |= ExternalStop?.Invoke() ?? false;
        return accepted;
    }

    /// <summary>紧急停止 for a Maa run started outside the panel (command line): the game host refuses its further
    /// pipeline input. Returns whether such a run was active. Null on a host without pipeline input.</summary>
    internal Func<bool>? ExternalStop { get; set; }

    /// <summary>
    /// game.audio.set (静音游戏): set by the game host only; applies the game view's mute flag and returns the applied
    /// state (null = not available). Audio only: never input, never a frame. Null on the preview / without a game view.
    /// </summary>
    internal Func<bool, bool?>? GameAudio { get; set; }
    internal UiGameData? GameData { get; set; }
    internal UiCardLibrary? CardLibrary { get; set; }
    /// <summary>Copies newer repo ui/game-assets modules to the served ui folder (status.update game_assets).</summary>
    internal UiGameAssetSync? AssetSync { get; set; }

    /// <summary>
    /// window.background (设置 → 窗口): set by the game host only. op "set" saves the 后台运行 setting and applies it; op "hide"
    /// hides the window now. Returns the applied state (null = unavailable). Window handling only: never input or a frame.
    /// </summary>
    internal Func<string, bool, bool?>? BackgroundRun { get; set; }
    /// <summary>设置 → 存储 (storage.status / storage.clean / storage.auto): set by the game dock; null = not available.</summary>
    internal UiStorage? Storage { get; set; }
    internal UiSupport? Support { get; set; }
    /// <summary>设置 → 外观 面板位置 (window.layout, 2026-10-16; detached 2026-10-25): right / left / detached → applied side; null without a game window.</summary>
    internal Func<string, string?>? WindowLayout { get; set; }
    /// <summary>Current panel side (right / left / detached) for status.update.window_layout (null without a game window).</summary>
    internal Func<string>? PanelSide { get; set; }

    /// <summary>Events for the page (status.update / log.append); the panel host posts them to the UI core only.</summary>
    internal event Action<string>? EventReady;

    internal bool StatusSubscribed { get; private set; }
    internal bool LogSubscribed { get; private set; }
    internal RunAudit? LastRun { get; private set; }
    internal IReadOnlyList<Decision> Decisions => _decisions;

    /// <summary>Returns the reply JSON, or null when the message is dropped without a reply (wrong origin / unparseable).</summary>
    internal string? Handle(string? source, string? json)
    {
        if (source is null || !string.Equals(source, _protocol.SourceUri, StringComparison.Ordinal))
        { return Drop(source, null, "来源不符"); }
        if (json is null) { return Drop(source, null, "空消息"); }
        if (UiProtocol.Utf8Length(json) > _protocol.MaxMessageBytes) { return Drop(source, null, "消息过长"); }
        var now = _clockMs();
        while (_recent.Count > 0 && now - _recent.Peek() >= 1000) { _recent.Dequeue(); }
        _recent.Enqueue(now);
        var rateLimited = _recent.Count > _protocol.MaxMessagesPerSecond;

        JsonDocument document;
        try { document = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 32 }); }
        catch (JsonException) { return Drop(source, null, "不是 JSON"); }
        using (document)
        {
            var message = document.RootElement;
            string? id = message.ValueKind == JsonValueKind.Object && message.TryGetProperty("id", out var idElement)
                && idElement.ValueKind == JsonValueKind.String ? idElement.GetString() : null;
            if (id is null || id.Length > 40 || !id.All(c => char.IsAsciiLetterOrDigit(c) || c == '-'))
            { return Drop(source, null, "缺少有效 id"); }
            string? command = message.TryGetProperty("cmd", out var cmdElement) && cmdElement.ValueKind == JsonValueKind.String ? cmdElement.GetString() : null;
            if (rateLimited) { return Reply(source, id, command, null, "请求过于频繁"); }
            var envelopeError = _protocol.Validate(message, _protocol.EnvelopeRequest);
            if (envelopeError is not null) { return Reply(source, id, command, null, "消息格式无效：" + envelopeError); }
            if (command is null || !_protocol.Commands.Contains(command)) { return Reply(source, id, command, null, "命令不在白名单内"); }
            var parameters = message.GetProperty("params");
            var paramsError = _protocol.Validate(parameters, _protocol.CommandParams(command), "params");
            if (paramsError is not null) { return Reply(source, id, command, null, "参数无效：" + paramsError); }
            try { return Dispatch(source, id, command, parameters); }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or InvalidOperationException or JsonException)
            { return Reply(source, id, command, null, "处理失败：" + exception.GetType().Name); }
        }
    }

    private string Dispatch(string source, string id, string command, JsonElement parameters)
    {
        switch (command)
        {
            case "ui.ready":
                AfterCardRefresh(null);   // the panel reads the card list: refresh the library when stale (background)
                return Reply(source, id, command, new JsonObject { ["protocol_version"] = 1, ["host"] = _hostKind }, null);
            case "schema.get":
                return Reply(source, id, command, new JsonObject
                {
                    ["schema"] = _schema.Schema.DeepClone(),
                    ["annotations"] = _schema.Annotations.DeepClone(),
                }, null);
            case "policy.get":
                // Read-only display of project_policy.json; never a grant. Empty when the host has no repo root.
                return Reply(source, id, command, new JsonObject { ["policy"] = _policy?.Invoke() ?? new JsonObject() }, null);
            case "profile.get":
                return Reply(source, id, command, new JsonObject { ["profile"] = _profiles.Load() }, null);
            case "profile.save":
                var saved = _profiles.Save(JsonNode.Parse(parameters.GetProperty("profile").GetRawText()));
                ProfileSaved?.Invoke(saved);
                return Reply(source, id, command, new JsonObject { ["saved"] = true }, null);
            case "status.subscribe":
                StatusSubscribed = parameters.GetProperty("enabled").GetBoolean();
                var status = Reply(source, id, command, new JsonObject { ["subscribed"] = StatusSubscribed }, null);
                if (StatusSubscribed) { EmitStatus(); }
                return status;
            case "log.subscribe":
                var first = !LogSubscribed;
                LogSubscribed = parameters.GetProperty("enabled").GetBoolean();
                var log = Reply(source, id, command, new JsonObject { ["subscribed"] = LogSubscribed }, null);
                if (LogSubscribed && first)
                {
                    if (_engine is null)
                    {
                        EmitLog("info", "preview", "界面预览：未连接游戏，任务不会启动。");
                        EmitLog("info", "preview", "状态与日志均为预览数据。");
                    }
                    else
                    {
                        if (_hostStatus is null) { EmitLog("info", "preview", "界面预览：未连接游戏；开发者选项中可离线回放。"); }
                        Emit("run.history", new JsonObject { ["runs"] = new JsonArray(_engine.History.Select(item => (JsonNode)item.DeepClone()).ToArray()) });
                    }
                }
                return log;
            case "run.start":
                CardLibrary?.StopAuto();   // no automatic library refresh beside a live run (the next read retries)
                LastRun = AuditRun(parameters);
                return StartRun(source, id, command, parameters);
            case "run.stop":
                var accepted = RequestStop(parameters.GetProperty("kind").GetString()!);
                EmitStatus();
                return Reply(source, id, command, new JsonObject { ["accepted"] = accepted }, null);
            case "dev.action_table":
                return DevTool(source, id, command, parameters);
            case "cards.scan.start":
            case "cards.scan.stop":
            case "cards.scan.status":
                return CardScan(source, id, command);
            case "knowhow.scan.start":
            case "knowhow.scan.stop":
            case "knowhow.scan.status":
                return KnowhowScan(source, id, command, parameters);
            case "game.audio.set":
                if (GameAudio is null) { return Reply(source, id, command, null, "未连接游戏画面，无法静音。"); }
                var muted = GameAudio(parameters.GetProperty("muted").GetBoolean());
                EmitStatus();
                return muted is bool applied
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true, ["muted"] = applied }, null)
                    : Reply(source, id, command, null, "游戏画面尚未就绪。");
            case "game.data.clear":
                if (GameData is null) { return Reply(source, id, command, null, "当前宿主不支持清理游戏数据。"); }
                var clearRefusal = GameData.Start(parameters.GetProperty("kind").GetString()!,
                    parameters.TryGetProperty("confirmed", out var clearConfirmed) && clearConfirmed.ValueKind == JsonValueKind.True);
                EmitStatus();
                return clearRefusal is null
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
                    : Reply(source, id, command, null, clearRefusal);
            case "cards.library.update":
                if (CardLibrary is null) { return Reply(source, id, command, null, "请在新版应用中更新卡资料。"); }
                var libraryRefusal = CardLibrary.Start();
                EmitStatus();
                return libraryRefusal is null
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
                    : Reply(source, id, command, null, libraryRefusal switch
                    {
                        "already_running" => "卡资料正在更新。",
                        "python_env_missing" => "找不到更新程序，请重新解压完整应用。",
                        _ => "无法启动卡资料更新。",
                    });
            case "cards.library.stop":
                var libraryStopped = CardLibrary?.RequestStop() ?? false;
                EmitStatus();
                return Reply(source, id, command, new JsonObject { ["stopped"] = libraryStopped }, null);
            case "team.propose":
                if (_teamPropose is null) { return Reply(source, id, command, null, "需要更新程序后才能重新生成推荐。"); }
                var proposeP = parameters.GetProperty("p_card").GetString()!;
                var proposePrimary = parameters.GetProperty("primary").GetString()!;
                var propose = _teamPropose;
                if (AfterCardRefresh(() =>
                    {
                        var later = propose.Start(proposeP, proposePrimary);
                        if (later is not null) { EmitLog("warn", "run", "推荐未开始：" + ProposeRefusalText(later)); }
                    }))
                { return Reply(source, id, command, new JsonObject { ["accepted"] = true, ["after_card_library"] = true }, null); }
                var proposeRefusal = propose.Start(proposeP, proposePrimary);
                EmitStatus();
                return proposeRefusal is null
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
                    : Reply(source, id, command, null, ProposeRefusalText(proposeRefusal));
            case "window.background":
                if (BackgroundRun is null) { return Reply(source, id, command, null, "未连接游戏窗口。"); }
                var windowOp = parameters.GetProperty("op").GetString()!;
                var windowEnabled = parameters.TryGetProperty("enabled", out var enabledElement) && enabledElement.ValueKind == JsonValueKind.True;
                if (windowOp == "set" && !parameters.TryGetProperty("enabled", out _)) { return Reply(source, id, command, null, "缺少 enabled。"); }
                var applied2 = BackgroundRun(windowOp, windowEnabled);
                if (applied2.HasValue) { EmitStatus(); }
                return applied2 is bool done
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true, ["enabled"] = done }, null)
                    : Reply(source, id, command, null, windowOp == "hide" ? "后台运行未开启，不能隐藏。" : "后台运行不可用。");
            case "storage.status":
            case "storage.clean":
            case "storage.auto":
                return StorageCommand(source, id, command, parameters);
            case "window.layout":
                if (WindowLayout is null) { return Reply(source, id, command, null, "需要在游戏窗口中使用。"); }
                var side = parameters.GetProperty("panel_side").GetString();
                if (side is not ("left" or "right" or "detached")) { return Reply(source, id, command, null, "未知的面板位置。"); }
                return WindowLayout(side) is string appliedSide
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true, ["panel_side"] = appliedSide }, null)
                    : Reply(source, id, command, null, "面板位置不可用。");
            case "app.support":
                if (Support is null) { return Reply(source, id, command, null, "需要更新程序。"); }
                try
                {
                    var (supportResult, supportError) = Support.Handle(parameters.GetProperty("op").GetString()!, parameters);
                    EmitStatus();
                    return Reply(source, id, command, supportResult, supportError);
                }
                catch (System.ComponentModel.Win32Exception) { return Reply(source, id, command, null, "无法打开：系统拒绝了请求。"); }
            default:
                return Reply(source, id, command, null, "命令未实现");
        }
    }

    /// <summary>
    /// U2 run.start: the request's task list must survive the same sanitising as a saved profile unchanged (unknown task,
    /// option or case = refusal), then the supervisor runs it read-only (game host capture session) or as an offline
    /// replay of private frames. The page never supplies a path: frames_id is an opaque name under the private replay root.
    /// </summary>
    private string StartRun(string source, string id, string command, JsonElement parameters)
    {
        if (GameData?.Busy == true) { return Reply(source, id, command, null, "正在清理游戏数据，请稍候。"); }
        if (_engine is null) { return Reply(source, id, command, null, PreviewRunRefusal); }
        if (_engine.Active) { return Reply(source, id, command, null, "已有运行进行中。"); }
        if (_cardScan?.Active == true) { return Reply(source, id, command, null, "正在扫描持有卡，请等它结束或先停止。"); }
        if (_knowhowScan?.Active == true) { return Reply(source, id, command, null, "正在扫描ノウハウ，请等它结束或先停止。"); }
        var config = parameters.GetProperty("config");
        var requested = JsonNode.Parse(config.GetProperty("tasks").GetRawText())!.AsArray();
        var clean = _profiles.Sanitize(new JsonObject { ["schema_version"] = 1, ["tasks"] = requested.DeepClone() })["tasks"]?.AsArray() ?? new JsonArray();
        if (!SameTasks(requested, clean)) { return Reply(source, id, command, null, "运行配置含未知任务、选项或分支。"); }
        double? timeout = config.TryGetProperty("task_timeout_s", out var t) ? t.GetDouble() : null;
        string? session = null, frames = null;
        if (parameters.GetProperty("mode").GetString() == UiEngineSupervisor.WingMode) { return StartWing(source, id, command, parameters, clean); }
        if (parameters.GetProperty("mode").GetString() == UiEngineSupervisor.DailyMode) { return StartDaily(source, id, command, parameters, clean, timeout); }
        if (parameters.GetProperty("mode").GetString() == "read_only")
        {
            session = _sessionPath?.Invoke();
            if (session is null) { return Reply(source, id, command, null, _hostStatus is null ? NoGameRefusal : "游戏画面尚未就绪，请稍后再试。"); }
        }
        else
        {
            frames = parameters.TryGetProperty("frames_id", out var framesId) ? ResolveFrames(framesId.GetString()!) : null;
            if (frames is null) { return Reply(source, id, command, null, "找不到离线回放帧（dev_assets/private/ui/replay/ 下的同名目录或清单）。"); }
        }
        var runId = _engine.Start(parameters.GetProperty("mode").GetString()!, clean, timeout, session, frames);
        return Reply(source, id, command, new JsonObject { ["run_id"] = runId }, null);
    }

    internal const string WingSpendFlag = "allow_produce_spend";

    /// <summary>
    /// run.start supervised_wing: only the AutoProduce row enabled, confirmed:true (2026-10-13: the page no longer
    /// shows a start dialog; the saved setting is the consent, the host checks below are unchanged), a game host with
    /// a capture session and no blocker (UiWingGate). The produce spend flag is forwarded only when the host re-derived it
    /// from the saved profile (「Produce可开新局」, fixed to Yes since 2026-10-13: 开始 on 「Produce」 is the consent; an in-progress
    /// Produce is continued only after the Agent verified its P idol and team) AND the page requested it for this run; a
    /// page flag the profile does not back is a refusal. ui_engine --wing re-checks the same consent against the run profile before anything starts.
    /// </summary>
    private string StartWing(string source, string id, string command, JsonElement parameters, JsonArray clean)
    {
        if (_hostStatus is null) { return Reply(source, id, command, null, "W.I.N.G. 只能在游戏窗口中运行。"); }
        if (!(parameters.TryGetProperty("confirmed", out var confirmed) && confirmed.ValueKind == JsonValueKind.True))
        { return Reply(source, id, command, null, "W.I.N.G. 需要开始前确认。"); }
        var enabled = clean.Where(item => item?["enabled"]?.GetValue<bool>() == true).Select(item => (string?)item!["name"]).ToList();
        if (enabled.Count != 1 || enabled[0] != UiEngineSupervisor.WingTask)
        { return Reply(source, id, command, null, "W.I.N.G. 需要单独运行：只勾选 W.I.N.G.。"); }
        if (_wingBlocker?.Invoke() is string blocker) { return Reply(source, id, command, null, "无法开始 W.I.N.G.：" + CardScanRefusalText(blocker)); }
        var session = _sessionPath?.Invoke();
        if (session is null) { return Reply(source, id, command, null, "游戏画面尚未就绪，请稍后再试。"); }
        var audit = LastRun!;
        var pageSpend = audit.PageFlags.Contains(WingSpendFlag);
        // 2026-10-13: W.I.N.G. runs from 「Produce」, so its saved row stays unticked (daily queue); the saved spend option
        // (Produce可开新局, fixed to Yes: pressing 开始 there is the consent) still decides, re-derived here from the saved profile.
        var savedProfile = _profiles.Load();
        var derivedSpend = _schema.DeriveSpendFlags(savedProfile, new HashSet<string>(StringComparer.Ordinal) { UiEngineSupervisor.WingTask },
            savedEnabledRequired: false).Contains(WingSpendFlag);
        if (pageSpend && !derivedSpend) { return Reply(source, id, command, null, "开新局（体力 30）未在已保存的设置中允许。"); }
        var runId = _engine!.Start(UiEngineSupervisor.WingMode, clean, null, session, null, pageSpend && derivedSpend,
            ownedCards: savedProfile["prefs"]?["owned_cards"] as JsonObject);
        return Reply(source, id, command, new JsonObject { ["run_id"] = runId }, null);
    }

    /// <summary>
    /// run.start supervised_daily (ANN-17, 2026-10-11): the 任务 page's normal 开始. The page's confirmed:true (since
    /// 2026-10-26 pressing 开始 is the consent: each task shows its 「消耗：…」 line, no dialog), no W.I.N.G. row ticked, a game host with a capture session and the same
    /// readiness gate as W.I.N.G. (UiWingGate). The page may not request a spend flag the saved profile does not derive for
    /// the ticked tasks. ui_engine --daily derives the spend actions from the ticked tasks' own spend options and runs
    /// run_tasks --live --supervised-sequence (per-task families, click caps and spend action).
    /// </summary>
    private string StartDaily(string source, string id, string command, JsonElement parameters, JsonArray clean, double? timeout)
    {
        if (_hostStatus is null) { return Reply(source, id, command, null, "实机日常只能在游戏窗口中运行。"); }
        if (!(parameters.TryGetProperty("confirmed", out var confirmed) && confirmed.ValueKind == JsonValueKind.True))
        { return Reply(source, id, command, null, "实机日常需要开始前确认。"); }
        var enabled = clean.Where(item => item?["enabled"]?.GetValue<bool>() == true).Select(item => (string?)item!["name"]).ToList();
        if (enabled.Count == 0) { return Reply(source, id, command, null, "没有勾选的任务。"); }
        if (enabled.Contains(UiEngineSupervisor.WingTask)) { return Reply(source, id, command, null, "W.I.N.G. 需要单独运行：只勾选 W.I.N.G.。"); }
        if (_wingBlocker?.Invoke() is string blocker) { return Reply(source, id, command, null, "无法开始：" + CardScanRefusalText(blocker)); }
        var session = _sessionPath?.Invoke();
        if (session is null) { return Reply(source, id, command, null, "游戏画面尚未就绪，请稍后再试。"); }
        if (LastRun!.IgnoredPageFlags.Count > 0)
        { return Reply(source, id, command, null, "页面请求的消耗与已保存的设置不一致，请稍候再开始。"); }
        // 2026-10-30 Auto 自动编队: the engine picks the WeeklyProduce team from the saved ownership snapshot.
        var runId = _engine!.Start(UiEngineSupervisor.DailyMode, clean, timeout, session, null,
            ownedCards: _profiles.Load()["prefs"]?["owned_cards"] as JsonObject);
        return Reply(source, id, command, new JsonObject { ["run_id"] = runId }, null);
    }

    /// <summary>Developer tools require a game host and the developer setting.</summary>
    private string DevTool(string source, string id, string command, JsonElement parameters)
    {
        if (_devTools is null) { return Reply(source, id, command, null, "当前宿主没有开发者工具（仅游戏宿主）。"); }
        var op = parameters.GetProperty("op").GetString()!;
        var developer = _profiles.Load()["prefs"]?["developer"] is JsonValue flag && flag.TryGetValue<bool>(out var on) && on;
        if (!developer) { return Reply(source, id, command, null, "需要先在设置中开启开发者选项。"); }
        if (command == "dev.action_table" && _cardScan?.Active == true)
        { return Reply(source, id, command, null, "已拒绝：正在扫描持有卡，请等它结束或先停止。"); }
        if (command == "dev.action_table" && _knowhowScan?.Active == true)
        { return Reply(source, id, command, null, "已拒绝：正在扫描ノウハウ，请等它结束或先停止。"); }
        if (command == "dev.action_table" && _engine?.Active == true)
        { return Reply(source, id, command, null, "已拒绝：Maa 任务运行中，请先停止任务。"); }
        var refusal = _devTools.ActionTable(op);
        EmitStatus();
        return refusal is null
            ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
            : Reply(source, id, command, null, "已拒绝：" + refusal);
    }

    /// <summary>
    /// 「扫描持有卡」. start needs the page's explicit confirmed:true (protocol const); the supervisor
    /// still refuses unless the host is ready (see UiCardScanGate). stop is always accepted; status is read-only.
    /// </summary>
    private string CardScan(string source, string id, string command)
    {
        if (_cardScan is null) { return Reply(source, id, command, null, "扫描持有卡只能在游戏窗口中使用。"); }
        switch (command)
        {
            case "cards.scan.start":
                if (GameData?.Busy == true) { return Reply(source, id, command, null, "正在清理游戏数据，请稍候。"); }
                var scan = _cardScan;
                if (AfterCardRefresh(() =>
                    {
                        if (CardLibrary?.AssetsMissing == true)
                        { EmitLog("warn", "run", "扫描未开始：" + CardScanRefusalText("card_library_missing")); return; }
                        var later = scan.Start();
                        if (later is not null) { EmitLog("warn", "run", "扫描未开始：" + CardScanRefusalText(later)); }
                    }, () => CardLibrary?.IconsReady == true))   // images suffice; card details keep downloading
                {
                    EmitLog("info", "run", "先下载卡图（新装约 5 分钟），完成后自动开始扫描。");
                    return Reply(source, id, command, new JsonObject { ["accepted"] = true, ["after_card_library"] = true }, null);
                }
                if (CardLibrary?.AssetsMissing == true)
                { return Reply(source, id, command, null, "无法开始扫描：" + CardScanRefusalText("card_library_missing")); }
                var refusal = _cardScan.Start();
                EmitStatus();
                return refusal is null
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
                    : Reply(source, id, command, null, "无法开始扫描：" + CardScanRefusalText(refusal));
            case "cards.scan.stop":
                var stopped = _cardScan.RequestStop();
                EmitStatus();
                return Reply(source, id, command, new JsonObject { ["accepted"] = stopped }, null);
            default:
                return Reply(source, id, command, new JsonObject { ["card_scan"] = _cardScan.Status() }, null);
        }
    }

    /// <summary>
    /// 「扫描ノウハウ」. start needs confirmed:true and a mode enum (protocol); the supervisor
    /// refuses unless the host is ready (UiKnowhowScanGate). stop is always accepted; status is read-only.
    /// </summary>
    private string KnowhowScan(string source, string id, string command, JsonElement parameters)
    {
        if (_knowhowScan is null) { return Reply(source, id, command, null, "扫描ノウハウ只能在游戏窗口中使用。"); }
        switch (command)
        {
            case "knowhow.scan.start":
                if (GameData?.Busy == true) { return Reply(source, id, command, null, "正在清理游戏数据，请稍候。"); }
                CardLibrary?.StopAuto();
                var mode = parameters.TryGetProperty("mode", out var m) ? m.GetString() : null;
                if (_cardScan?.Active == true) { return Reply(source, id, command, null, "无法开始：" + CardScanRefusalText("card_scan_active")); }
                if (_engine?.Active == true) { return Reply(source, id, command, null, "无法开始：" + CardScanRefusalText("engine_active")); }
                // 补读新书: mode newest carries count 1..10 (protocol-checked); the other modes take none
                var count = parameters.TryGetProperty("count", out var c) && c.ValueKind == JsonValueKind.Number && c.TryGetInt32(out var n) ? n : 0;
                var refusal = UiKnowhowScanSupervisor.ValidMode(mode) && UiKnowhowScanSupervisor.ValidCount(mode!, count)
                    ? _knowhowScan.Start(mode!, count) : "invalid_mode";
                EmitStatus();
                return refusal is null
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
                    : Reply(source, id, command, null, "无法开始扫描ノウハウ：" + CardScanRefusalText(refusal));
            case "knowhow.scan.stop":
                var stopped = _knowhowScan.RequestStop();
                EmitStatus();
                return Reply(source, id, command, new JsonObject { ["accepted"] = stopped }, null);
            default:
                return Reply(source, id, command, new JsonObject { ["knowhow_scan"] = _knowhowScan.Status() }, null);
        }
    }

    internal static string CardScanRefusalText(string code) => code switch
    {
        "knowhow_scan_active" => "正在扫描ノウハウ，请等它结束或先停止。",
        "invalid_mode" => "扫描模式无效。",
        _ when code.StartsWith("knowhow_scan_", StringComparison.Ordinal) => "ノウハウ扫描动作或入口尚未开放（" + code + "）。",
        "already_running" => "已有扫描进行中。",
        "pipeline_input_disabled" => "游戏窗口尚未允许自动操作，请重新打开程序。",
        "host_not_connected" => "游戏窗口未连接，请打开游戏窗口。",
        "host_guard_active" => "当前不能操作游戏，请解锁桌面并处理游戏中的弹窗。",
        "engine_active" => "已有任务正在运行，请先停止或等它结束。",
        "other_input_activity" => "检测到另一个程序正在操作游戏，请等它结束。",
        "session_unavailable" => "游戏连接已失效，请重新打开游戏窗口。",
        "card_scan_active" => "正在扫描持有卡，请等它结束或先停止。",
        "card_library_missing" => "本机还没有卡资料，请先在设置中「下载卡资料」。",
        "python_env_missing" => "程序运行环境缺失，请更新或重新安装程序。",
        _ when code.StartsWith("card_scan_", StringComparison.Ordinal) => "扫描动作或入口尚未开放（" + code + "）。",
        _ => code,
    };

    private static bool SameTasks(JsonArray requested, JsonArray clean)
    {
        if (requested.Count == 0 || requested.Count != clean.Count) { return false; }
        for (var index = 0; index < requested.Count; index++)
        {
            var a = requested[index]!.AsObject();
            var b = clean[index]!.AsObject();
            if ((string?)a["name"] != (string?)b["name"]) { return false; }
            if ((a["enabled"]?.GetValue<bool>() ?? false) != b["enabled"]!.GetValue<bool>()) { return false; }
            if ((a["option"]?.AsObject().Count ?? 0) != (b["option"]?.AsObject().Count ?? 0)) { return false; }
        }
        return true;
    }

    /// <summary>frames_id (protocol pattern [A-Za-z0-9_-]{1,64}) -> a folder of PNGs or a steps JSON under the private replay root.</summary>
    private string? ResolveFrames(string framesId)
    {
        if (_replayRoot is null || framesId.Length is 0 or > 64 || !framesId.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or '-')) { return null; }
        foreach (var candidate in new[] { Path.Combine(_replayRoot, framesId), Path.Combine(_replayRoot, framesId + ".json") })
        {
            var full = Path.GetFullPath(candidate);
            if (!full.StartsWith(_replayRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) { continue; }
            if (Directory.Exists(full) && !new DirectoryInfo(full).Attributes.HasFlag(FileAttributes.ReparsePoint)) { return full; }
            var info = new FileInfo(full);
            if (info.Exists && !info.Attributes.HasFlag(FileAttributes.ReparsePoint)) { return full; }
        }
        return null;
    }

    /// <summary>
    /// 设置 → 存储: status returns the cached sizes (a refresh / stale cache starts a background scan); clean needs the page's
    /// confirmed:true (protocol const) and is refused while a run, scan or recording is active; auto saves the host setting.
    /// Only categorised files under dev_assets/private are ever deleted (UiStorage); the page never supplies a path.
    /// </summary>
    private string StorageCommand(string source, string id, string command, JsonElement parameters)
    {
        if (Storage is null) { return Reply(source, id, command, null, "需要更新程序后才能管理存储。"); }
        switch (command)
        {
            case "storage.status":
                var refresh = parameters.TryGetProperty("refresh", out var r) && r.ValueKind == JsonValueKind.True;
                return Reply(source, id, command, new JsonObject { ["storage"] = Storage.Status(refresh) }, null);
            case "storage.clean":
                var refusal = Storage.Clean(parameters.GetProperty("category").GetString()!);
                EmitStatus();
                return refusal is null ? Reply(source, id, command, new JsonObject { ["accepted"] = true }, null)
                    : Reply(source, id, command, null, refusal);
            default:
                var enabled = parameters.GetProperty("enabled").GetBoolean();
                var days = parameters.GetProperty("days").GetInt32();
                var autoRefusal = Storage.SetAuto(enabled, days);
                EmitStatus();
                return autoRefusal is null
                    ? Reply(source, id, command, new JsonObject { ["accepted"] = true, ["enabled"] = enabled, ["days"] = days }, null)
                    : Reply(source, id, command, null, autoRefusal);
        }
    }

    private static string ProposeRefusalText(string refusal) => refusal switch
    {
        "already_running" => "推荐正在生成，请稍候。",
        "python_env_missing" => "此功能暂不可用：找不到程序的 Python 环境。",
        "invalid_request" => "请先选择 P 偶像和主属性。",
        _ => "无法生成推荐。",
    };

    /// <summary>
    /// 2026-10-31 (user): reading the card library refreshes it first (<see cref="UiCardLibrary.StartIfStale"/>, at most
    /// every <see cref="UiCardLibrary.AutoInterval"/>; the manual update stays). True = the library is updating and
    /// <paramref name="then"/> runs from <see cref="Tick"/> once it ends (failed or not); false = run it now. Never
    /// beside a run or scan.
    /// </summary>
    private bool AfterCardRefresh(Action? then, Func<bool>? ready = null)
    {
        if (CardLibrary is null || _engine?.Active == true || _cardScan?.Active == true || _knowhowScan?.Active == true) { return false; }
        var refusal = CardLibrary.StartIfStale(DateTimeOffset.UtcNow);
        if (refusal is not null && refusal != "already_running") { return false; }
        if (then is not null) { _afterCardRefresh.Add((ready ?? (() => CardLibrary?.Running != true), then)); }
        EmitStatus();
        return true;
    }

    /// <summary>Called by the panel host timer on the UI thread.</summary>
    internal void Tick()
    {
        if (_afterCardRefresh.Count > 0)
        {
            var due = _afterCardRefresh.Where(item => CardLibrary?.Running != true || item.Ready()).ToArray();
            if (due.Length > 0)
            {
                foreach (var item in due) { _afterCardRefresh.Remove(item); }
                foreach (var item in due) { item.Then(); }
                EmitStatus();
            }
        }
        _engine?.Tick();
        _cardScan?.Tick();
        _knowhowScan?.Tick();
        Storage?.Tick();
        AssetSync?.Tick();
    }

    private void ForwardEngine(string name, JsonObject data)
    {
        if (name == "log.append")
        {
            EmitLog((string)data["level"]!, (string?)data["kind"] ?? "run", (string)data["text"]!, (string?)data["task"]);
            return;
        }
        Emit(name, data);
    }

    /// <summary>Raised after a sanitised profile was written (the shell applies theme / panel width).</summary>
    internal event Action<JsonObject>? ProfileSaved;

    /// <summary>
    /// Host-side spend derivation for run.start: the page's spend_flags are recorded but never trusted; the host
    /// re-derives them from the saved profile, restricted to tasks the request itself enables.
    /// </summary>
    private RunAudit AuditRun(JsonElement parameters)
    {
        var pageFlags = parameters.GetProperty("spend_flags").EnumerateArray().Select(item => item.GetString()!).OrderBy(item => item, StringComparer.Ordinal).ToList();
        var requested = parameters.GetProperty("config").GetProperty("tasks").EnumerateArray()
            .Where(task => task.TryGetProperty("enabled", out var enabled) && enabled.ValueKind == JsonValueKind.True)
            .Select(task => task.GetProperty("name").GetString()!).ToHashSet(StringComparer.Ordinal);
        var mode = parameters.GetProperty("mode").GetString()!;
        // A daily single-task run can select an unticked row without changing the saved plan. The saved spend option
        // still decides; the request's task set decides which rows run this time.
        var derived = _schema.DeriveSpendFlags(_profiles.Load(), requested,
            savedEnabledRequired: mode != UiEngineSupervisor.DailyMode);
        return new RunAudit(mode, pageFlags, derived,
            pageFlags.Except(derived, StringComparer.Ordinal).ToList());
    }

    private static bool? OptionalFlag(JsonElement parameters, string name) =>
        parameters.TryGetProperty(name, out var value) ? value.ValueKind == JsonValueKind.True : null;

    internal void EmitStatus()
    {
        if (!StatusSubscribed) { return; }
        if (_hostStatus is not null)
        {
            // In-process host fields only; never a screenshot.
            var status = _hostStatus();
            status["engine_running"] = _engine?.Active ?? false;
            if (_cardScan is not null) { status["card_scan"] = _cardScan.Status(); }
            if (_knowhowScan is not null) { status["knowhow_scan"] = _knowhowScan.Status(); }
            status["run_live"] = _liveStatus?.Read();
            if (_teamPropose is not null) { status["team_propose"] = _teamPropose.Status(); }
            if (CardLibrary is not null) { status["card_library"] = CardLibrary.Status(); }
            if (AssetSync is not null) { status["game_assets"] = AssetSync.Status(); }
            if (Storage is not null) { status["storage"] = Storage.Snapshot(); }
            if (Support is not null) { status["support"] = Support.Snapshot(); }
            if (PanelSide is not null) { status["window_layout"] = new JsonObject { ["panel_side"] = PanelSide(), ["supported"] = true }; }
            if (_devTools is not null)
            {
                foreach (var (key, value) in _devTools.Status()) { status[key] = value?.DeepClone(); }
            }
            Emit("status.update", status);
            return;
        }
        Emit("status.update", new JsonObject
        {
            ["connected"] = false,
            ["hide_mode"] = "none",
            ["frame_age_ms"] = null,
            ["capture_seq"] = null,
            ["desktop_locked"] = false,
            ["host_guard_reason"] = "ui_preview_no_game",
            ["pipeline_input_enabled"] = false,
            ["clicks_this_run"] = 0,
            ["team_propose"] = _teamPropose?.Status(),
            ["card_library"] = CardLibrary?.Status(),
        });
    }

    private void EmitLog(string level, string kind, string text, string? task = null)
    {
        _logSeq++;
        Emit("log.append", new JsonObject
        {
            ["seq"] = _logSeq,
            ["ts"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz", CultureInfo.InvariantCulture),
            ["level"] = level,
            ["task"] = task,
            ["kind"] = kind,
            ["text"] = text,
            ["frame_id"] = null,
        });
    }

    private void Emit(string name, JsonObject data)
    {
        if (!_protocol.Events.Contains(name)) { throw new InvalidOperationException("事件不在协议内：" + name); }
        EventReady?.Invoke(new JsonObject { ["v"] = 1, ["event"] = name, ["data"] = data }.ToJsonString(JsonOptions));
    }

    private string? Drop(string? source, string? command, string error)
    {
        Record(source, command, false, error);
        return null;
    }

    private string Reply(string source, string id, string? command, JsonObject? result, string? error)
    {
        Record(source, command, error is null, error);
        var reply = new JsonObject { ["v"] = 1, ["id"] = id, ["ok"] = error is null };
        if (error is null) { reply["result"] = result ?? new JsonObject(); }
        else { reply["error"] = error.Length > 2000 ? error[..2000] : error; }
        return reply.ToJsonString(JsonOptions);
    }

    private void Record(string? source, string? command, bool accepted, string? error)
    {
        if (_decisions.Count >= 2000) { _decisions.RemoveAt(0); }
        var safeSource = source is null ? "(null)" : source.Length > 200 ? source[..200] : source;
        var safeCommand = command is null ? null : command.Length > 64 ? command[..64] : command;
        _decisions.Add(new Decision(safeSource, safeCommand, accepted, error));
    }
}

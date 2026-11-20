using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost;

/// <summary>One fixed non-consuming action; legacy counted, pending never resets.</summary>
internal sealed class SelectionLoopBudget
{
    internal const string Action = "training-select-asahi";
    internal const int MaxAttempts = 4;
    private static readonly JsonSerializerOptions Options = new() { UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow };
    private readonly System.Action _revalidate;
    internal string Path { get; }
    internal string ResolutionPath { get; }
    internal string ResolutionSha256 { get; }
    private readonly string _legacyNonce;
    private readonly string _configurationSha256;
    private readonly string _foregroundSha256;
    private sealed record Attempt(string Nonce, string PreFrameSha256, int Pid, string State, bool NativeSent,
        string? PostFrameSha256 = null, string? ProofSha256 = null);
    private sealed record Ledger(int Version, string Action, int MaxAttempts, int ImportedAttempts,
        string ResolutionSha256, Attempt[] History);
    internal sealed record Snapshot(string State, int Attempts);

    internal static string Root()
    {
        var root = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (root is not null && !File.Exists(System.IO.Path.Combine(root.FullName, "project_policy.json"))) { root = root.Parent; }
        return root?.FullName ?? throw new InvalidOperationException("选卡循环必须从本项目启动。");
    }
    internal static string DefaultPath() => System.IO.Path.Combine(Root(), "dev_assets", "private", "daily_navigation_loop", "training-selection.json");
    internal static string DefaultResolutionPath() => System.IO.Path.Combine(Root(), "dev_assets", "private", "daily_navigation_loop", "training-selection-resolution.json");
    internal static bool Sha(string? value) => value is not null && Regex.IsMatch(value, "\\A[0-9a-f]{64}\\z");
    internal static bool Nonce(string? value) => value is not null && Regex.IsMatch(value, "\\A[0-9a-f]{32}\\z");
    internal static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    private static void Require(bool valid, string reason) { if (!valid) { throw new InvalidOperationException(reason); } }
    internal static JsonDocument Json(string path)
    {
        var document = JsonDocument.Parse(File.ReadAllBytes(path));
        try { Unique(document.RootElement); return document; }
        catch { document.Dispose(); throw; }
    }
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
    internal static void Keys(JsonElement root, params string[] keys) => Require(root.ValueKind == JsonValueKind.Object &&
        root.EnumerateObject().Select(p => p.Name).Order().SequenceEqual(keys.Order()), "选卡循环字段白名单变化。");
    internal static string PrivateFile(string raw)
    {
        var path = System.IO.Path.GetFullPath(raw);
        var prefix = System.IO.Path.Combine(Root(), "dev_assets", "private") + System.IO.Path.DirectorySeparatorChar;
        Require(path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && File.Exists(path), "选卡证据必须为现有私有文件。");
        return path;
    }
    internal static void ConsumptionUnused()
    {
        var directory = System.IO.Path.Combine(Root(), "dev_assets", "private", "daily_navigation");
        Require(!File.Exists(System.IO.Path.Combine(directory, "training-confirm-one-bronze.runner-pending.json")), "Confirm runner pending阻断选卡循环。");
        var path = System.IO.Path.Combine(directory, "training-confirm-one-bronze.json");
        if (!File.Exists(path)) { return; }
        using var document = Json(path); var ledger = document.RootElement;
        Keys(ledger, "Version", "Navigation", "MaxAttempts", "Attempts", "LastPid", "LastFrameSha256", "LastNonce");
        Require(ledger.GetProperty("Version").GetInt32() == 1 && ledger.GetProperty("Navigation").GetString() == "training-confirm-one-bronze" &&
            ledger.GetProperty("MaxAttempts").GetInt32() == 1 && ledger.GetProperty("Attempts").GetInt32() == 0 &&
            ledger.GetProperty("LastPid").ValueKind == JsonValueKind.Null && ledger.GetProperty("LastFrameSha256").ValueKind == JsonValueKind.Null &&
            ledger.GetProperty("LastNonce").ValueKind == JsonValueKind.Null, "Confirm原生消费已用或不确定，阻断选卡循环。");
    }

    internal SelectionLoopBudget(string resolutionPath, string resolutionSha256)
    {
        Path = DefaultPath(); ResolutionPath = System.IO.Path.GetFullPath(resolutionPath); ResolutionSha256 = resolutionSha256;
        Require(ResolutionPath == DefaultResolutionPath() && Sha(resolutionSha256), "必须显式绑定固定解决证明及审查SHA。");
        void Revalidate() { ValidateResolution(ResolutionPath, ResolutionSha256); ConsumptionUnused(); }
        _revalidate = Revalidate; _revalidate();
        using var document = Json(ResolutionPath); var proof = document.RootElement;
        _legacyNonce = proof.GetProperty("legacy_nonce").GetString()!;
        _configurationSha256 = proof.GetProperty("configuration_sha256").GetString()!;
        _foregroundSha256 = proof.GetProperty("foreground_sha256").GetString()!;
        Initialize();
    }
    // Injected only by the offline self-test; production never accepts ledger paths or guards from requests.
    internal SelectionLoopBudget(string path, string digest, string legacyNonce, System.Action revalidate)
    {
        Path = path; ResolutionPath = "offline"; ResolutionSha256 = digest; _legacyNonce = legacyNonce;
        _configurationSha256 = digest; _foregroundSha256 = digest; _revalidate = revalidate;
        _revalidate(); Initialize();
    }
    private void Initialize()
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        try
        {
            using var file = new FileStream(Path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            Write(file, new(1, Action, MaxAttempts, 1, ResolutionSha256, Array.Empty<Attempt>()));
        }
        catch (IOException) when (File.Exists(Path))
        { using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None); _ = Read(file); }
    }
    private Ledger Read(FileStream file)
    {
        file.Position = 0;
        using var document = JsonDocument.Parse(file); Unique(document.RootElement);
        var ledger = document.RootElement.Deserialize<Ledger>(Options) ?? throw new InvalidOperationException("选卡循环账本损坏。");
        Require(ledger.Version == 1 && ledger.Action == Action && ledger.MaxAttempts == MaxAttempts && ledger.ImportedAttempts == 1 &&
            ledger.ResolutionSha256 == ResolutionSha256 && ledger.History is not null && ledger.History.Length <= 3 &&
            ledger.History.All(a => a is not null && Nonce(a.Nonce) && a.Nonce != _legacyNonce && Sha(a.PreFrameSha256) && a.Pid > 0 &&
                (a.State == "pending" && a.PostFrameSha256 is null && a.ProofSha256 is null ||
                 a.State == "confirmed" && a.NativeSent && Sha(a.PostFrameSha256) && Sha(a.ProofSha256))) &&
            ledger.History.Select(a => a.Nonce).Distinct(StringComparer.Ordinal).Count() == ledger.History.Length &&
            ledger.History.Take(Math.Max(0, ledger.History.Length - 1)).All(a => a.State == "confirmed"), "选卡循环账本无效；禁止重置。");
        return ledger;
    }
    private static void Write(FileStream file, Ledger ledger)
    { file.Position = 0; file.SetLength(0); JsonSerializer.Serialize(file, ledger, Options); file.Flush(flushToDisk: true); }
    internal Snapshot Inspect()
    {
        _revalidate(); using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None); var ledger = Read(file);
        return new(ledger.History.LastOrDefault()?.State ?? "ready", 1 + ledger.History.Length);
    }
    internal bool Begin(string nonce, string sha)
    {
        _revalidate(); Require(Nonce(nonce) && Sha(sha), "选卡nonce或帧摘要无效。");
        using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None); var ledger = Read(file);
        if (ledger.History.Length >= 3 || nonce == _legacyNonce || ledger.History.Any(a => a.Nonce == nonce) || ledger.History.LastOrDefault()?.State == "pending") { return false; }
        Write(file, ledger with { History = ledger.History.Append(new(nonce, sha, Environment.ProcessId, "pending", false)).ToArray() });
        return true;
    }
    internal void MarkNativeSent(string nonce)
    {
        using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None); var ledger = Read(file);
        var last = ledger.History.LastOrDefault(); Require(last is not null && last.Nonce == nonce && last.State == "pending" && last.Pid == Environment.ProcessId,
            "选卡pending不匹配。");
        Write(file, ledger with { History = ledger.History[..^1].Append(last! with { NativeSent = true }).ToArray() });
    }
    internal bool Confirm(string nonce, string postSha, string proofPath, string proofSha)
    {
        _revalidate(); Require(Nonce(nonce) && Sha(postSha) && Sha(proofSha), "选卡确认绑定无效。");
        proofPath = PrivateFile(proofPath); Require(Hash(proofPath) == proofSha, "选卡确认文件SHA变化。");
        using var document = Json(proofPath); var proof = document.RootElement;
        Keys(proof, "version", "kind", "action", "passed", "resource_consumption", "input_attempts", "nonce", "session_pid",
            "pre_frame_sha256", "post_frame", "configuration_sha256", "foreground_sha256", "post_recognitions", "foreground", "producer_path", "producer_sha256");
        var producer = System.IO.Path.Combine(Root(), "tools", "run_training_selection.py");
        Require(proof.GetProperty("version").GetInt32() == 1 && proof.GetProperty("kind").GetString() == "SC.TrainingSelection.Postcondition" &&
            proof.GetProperty("action").GetString() == Action && proof.GetProperty("passed").GetBoolean() &&
            !proof.GetProperty("resource_consumption").GetBoolean() && proof.GetProperty("input_attempts").GetInt32() == 1 &&
            proof.GetProperty("nonce").GetString() == nonce && proof.GetProperty("session_pid").GetInt32() == Environment.ProcessId &&
            proof.GetProperty("configuration_sha256").GetString() == _configurationSha256 && proof.GetProperty("foreground_sha256").GetString() == _foregroundSha256 &&
            proof.GetProperty("producer_path").GetString() == producer && File.Exists(producer) && Hash(producer) == proof.GetProperty("producer_sha256").GetString(),
            "选卡确认生产者、动作、PID或模板不匹配。");
        ValidateZero(proof.GetProperty("post_recognitions")); ValidateForeground(proof.GetProperty("foreground"));
        var frame = proof.GetProperty("post_frame"); Keys(frame, "image", "sha256");
        Require(frame.GetProperty("sha256").GetString() == postSha && Hash(PrivateFile(frame.GetProperty("image").GetString()!)) == postSha,
            "选卡确认后帧不匹配。");
        return CompleteValidated(nonce, postSha, proofSha, proof.GetProperty("pre_frame_sha256").GetString()!);
    }
    internal bool CompleteValidated(string nonce, string postSha, string proofSha, string preSha)
    {
        _revalidate(); Require(Nonce(nonce) && Sha(postSha) && Sha(proofSha) && Sha(preSha), "选卡确认状态摘要无效。");
        using var file = new FileStream(Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None); var ledger = Read(file);
        var last = ledger.History.LastOrDefault();
        if (last is null || last.Nonce != nonce || last.State != "pending" || !last.NativeSent || last.Pid != Environment.ProcessId ||
            last.PreFrameSha256 != preSha) { return false; }
        Write(file, ledger with { History = ledger.History[..^1].Append(last with { State = "confirmed", PostFrameSha256 = postSha, ProofSha256 = proofSha }).ToArray() });
        return true;
    }
    internal static void ValidateForeground(JsonElement foreground)
    {
        Require(foreground.GetProperty("state").GetString() == "no_known_popup" &&
            foreground.GetProperty("complete_contracts").GetArrayLength() == 0 && foreground.GetProperty("partial_contracts").GetArrayLength() == 0,
            "选卡后置前景不明确。");
    }
    internal static void ValidateZero(JsonElement hits)
    {
        var rois = new Dictionary<string, int[]> { ["Title"] = new[] {48,24,177,31}, ["CardName"] = new[] {159,96,451,31},
            ["Portrait"] = new[] {40,102,99,75}, ["BronzeLabel"] = new[] {810,270,325,34}, ["BronzeZero"] = new[] {939,322,62,44},
            ["Lv50Zero"] = new[] {939,184,62,44}, ["SilverZero"] = new[] {939,456,62,44}, ["ClearDisabled"] = new[] {906,636,146,50}, ["Plus"] = new[] {1058,320,58,52} };
        Require(hits.ValueKind == JsonValueKind.Array && hits.GetArrayLength() == rois.Count, "选卡必须完整Zero九锚点。");
        var names = new HashSet<string>(StringComparer.Ordinal);
        foreach (var hit in hits.EnumerateArray())
        {
            var name = hit.GetProperty("scene").GetString()!; var prefix = "SC.Training.Zero.";
            Require(name.StartsWith(prefix, StringComparison.Ordinal) && names.Add(name) && rois.TryGetValue(name[prefix.Length..], out _), "Zero锚点名称不符。");
            var roi = rois[name[prefix.Length..]]; var filtered = hit.GetProperty("raw_detail").GetProperty("filtered");
            var score = hit.GetProperty("score").GetDouble();
            Require(hit.GetProperty("hit").GetBoolean() && double.IsFinite(score) && score >= .95 && score <= 1 &&
                hit.GetProperty("roi").EnumerateArray().Select(n => n.GetInt32()).SequenceEqual(roi) && filtered.GetArrayLength() == 1 &&
                filtered[0].GetProperty("box").EnumerateArray().Select(n => n.GetInt32()).SequenceEqual(roi), "Zero锚点不完整或目标不唯一。");
        }
    }
    internal static void ValidateResolution(string path, string expectedSha)
    {
        Require(Hash(path) == expectedSha, "已审查的旧选卡解决证明变化。");
        using var document = Json(path); var proof = document.RootElement;
        Keys(proof, "version", "kind", "action", "passed", "real_input_attempts", "resource_consumption", "legacy_ledger_path", "legacy_ledger_sha256",
            "legacy_report_path", "legacy_report_sha256", "legacy_pid", "legacy_nonce", "original_report_passed", "pre_frame", "post_frame",
            "configuration_sha256", "foreground_sha256", "post_recognitions", "foreground", "files", "limitations");
        Require(proof.GetProperty("version").GetInt32() == 1 && proof.GetProperty("kind").GetString() == "SC.TrainingSelection.LegacyResolution" &&
            proof.GetProperty("action").GetString() == Action && proof.GetProperty("passed").GetBoolean() &&
            proof.GetProperty("real_input_attempts").GetInt32() == 0 && !proof.GetProperty("resource_consumption").GetBoolean() &&
            !proof.GetProperty("original_report_passed").GetBoolean() && Sha(proof.GetProperty("configuration_sha256").GetString()) &&
            Sha(proof.GetProperty("foreground_sha256").GetString()), "旧选卡解决证明范围无效。");
        ValidateZero(proof.GetProperty("post_recognitions")); ValidateForeground(proof.GetProperty("foreground"));
        var files = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var privatePrefix = System.IO.Path.Combine(Root(), "dev_assets", "private") + System.IO.Path.DirectorySeparatorChar;
        var producers = new[] { "tools/resolve_training_selection.py", "tools/selection_loop_protocol.py", "agent/core/training_flow.py",
            "tools/replay_screenshots.py", "tools/replay_foreground.py", "agent/core/foreground_guard.py", "tests/fixtures/training_flow/pipeline.json" }
            .Select(p => System.IO.Path.GetFullPath(System.IO.Path.Combine(Root(), p))).ToArray();
        foreach (var item in proof.GetProperty("files").EnumerateArray())
        {
            Keys(item, "path", "sha256"); var file = System.IO.Path.GetFullPath(item.GetProperty("path").GetString()!); var hash = item.GetProperty("sha256").GetString()!;
            Require((file.StartsWith(privatePrefix, StringComparison.OrdinalIgnoreCase) || producers.Contains(file, StringComparer.OrdinalIgnoreCase)) &&
                Sha(hash) && files.TryAdd(file, hash) && Hash(file) == hash, "旧解决证据文件或代码SHA变化。");
        }
        Require(producers.All(p => files.ContainsKey(p)), "旧解决证明缺少生产者代码绑定。");
        var ledgerPath = System.IO.Path.Combine(Root(), "dev_assets", "private", "daily_navigation", Action + ".json");
        Require(proof.GetProperty("legacy_ledger_path").GetString() == ledgerPath && files.GetValueOrDefault(ledgerPath) == proof.GetProperty("legacy_ledger_sha256").GetString(), "旧账本路径或SHA变化。");
        using var oldDocument = Json(ledgerPath); var old = oldDocument.RootElement;
        Keys(old, "Version", "Navigation", "MaxAttempts", "Attempts", "LastPid", "LastFrameSha256", "LastNonce");
        var pid = proof.GetProperty("legacy_pid").GetInt32(); var nonce = proof.GetProperty("legacy_nonce").GetString();
        Require(pid > 0 && Nonce(nonce) && old.GetProperty("Version").GetInt32() == 1 && old.GetProperty("Navigation").GetString() == Action &&
            old.GetProperty("MaxAttempts").GetInt32() == 1 && old.GetProperty("Attempts").GetInt32() == 1 && old.GetProperty("LastPid").GetInt32() == pid &&
            old.GetProperty("LastNonce").GetString() == nonce && old.GetProperty("LastFrameSha256").GetString() == proof.GetProperty("pre_frame").GetProperty("sha256").GetString(), "旧尝试身份不匹配。");
        try { using var process = Process.GetProcessById(pid); Require(process.HasExited, "旧宿主仍运行，不能解除历史导航。"); }
        catch (ArgumentException) { }
        var reportPath = PrivateFile(proof.GetProperty("legacy_report_path").GetString()!);
        Require(files.GetValueOrDefault(reportPath) == proof.GetProperty("legacy_report_sha256").GetString(), "原报告不在固定证明中。");
        using var reportDocument = Json(reportPath); var report = reportDocument.RootElement;
        Require(report.GetProperty("action").GetString() == Action && !report.GetProperty("passed").GetBoolean() &&
            !report.GetProperty("resource_consumption").GetBoolean() && report.GetProperty("input_enabled").GetBoolean() &&
            !report.GetProperty("offline_only").GetBoolean() && report.GetProperty("input_attempts").GetInt32() == 1 &&
            report.GetProperty("response").GetProperty("ok").GetBoolean() && report.GetProperty("response").GetProperty("attempted").GetBoolean(), "旧报告没有真实非消费尝试。");
        var trace = report.GetProperty("node_trace");
        Require(trace.GetArrayLength() == 2 && trace[0].GetProperty("name").GetString() == "SC.DailyNavigation.training-select-asahi.Observe" &&
            trace[1].GetProperty("name").GetString() == "SC.DailyNavigation.training-select-asahi.Click" && trace.EnumerateArray().All(n =>
                n.GetProperty("completed").GetBoolean() && n.GetProperty("hit").GetBoolean() && n.GetProperty("action_success").GetBoolean()), "旧Maa节点链不匹配。");
        var frames = report.GetProperty("frames"); var pre = proof.GetProperty("pre_frame"); var post = proof.GetProperty("post_frame");
        Require(frames.GetArrayLength() >= 4 && frames[2].GetProperty("sha256").GetString() == pre.GetProperty("sha256").GetString() &&
            frames[frames.GetArrayLength()-1].GetProperty("sha256").GetString() == post.GetProperty("sha256").GetString() &&
            Hash(PrivateFile(pre.GetProperty("image").GetString()!)) == pre.GetProperty("sha256").GetString() &&
            Hash(PrivateFile(post.GetProperty("image").GetString()!)) == post.GetProperty("sha256").GetString(), "历史前后帧不匹配。");
    }
}

using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline persistence/failure checks; no host or pointer creation.</summary>
internal static class SelectionLoopSelfTest
{
    internal static void Run()
    {
        var root = System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "selection_loop_selftests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root); var count = 0;
        void Check(bool valid) { if (!valid) { throw new InvalidOperationException("选卡循环自检失败。"); } count++; }
        void Reject(System.Action action) { var rejected = false; try { action(); } catch { rejected = true; } Check(rejected); }
        var path = System.IO.Path.Combine(root, "ledger.json"); var sha = new string('a', 64); var legacy = new string('b', 32);
        SelectionLoopBudget New() => new(path, sha, legacy, () => { });
        var budget = New(); Check(budget.Inspect().Attempts == 1 && budget.Inspect().State == "ready");
        Check(!budget.Begin(legacy, sha)); Reject(() => budget.Begin("bad", sha));
        var nonce = new string('c', 32); Check(budget.Begin(nonce, sha));
        Check(New().Inspect().State == "pending" && New().Inspect().Attempts == 2);
        Check(!New().Begin(new string('d',32),sha));
        Check(!budget.CompleteValidated(nonce,sha,sha,sha)); // No native success, cannot confirm.
        budget.MarkNativeSent(nonce); Check(!budget.CompleteValidated(new string('e',32),sha,sha,sha));
        Check(!budget.CompleteValidated(nonce,sha,sha,new string('f',64)));
        Check(budget.CompleteValidated(nonce,new string('d',64),sha,sha));
        Check(New().Inspect().State == "confirmed"); Check(!New().Begin(nonce,sha));
        for (var i = 0; i < 2; i++)
        {
            var next = new string((char)('d'+i),32); budget = New();
            Check(budget.Begin(next,sha)); // An independently fresh revisit may have identical pixels.
            budget.MarkNativeSent(next); Check(budget.CompleteValidated(next,new string('d',64),sha,sha));
        }
        Check(New().Inspect().Attempts == 4 && !New().Begin(new string('a',32),sha));
        foreach (var invalid in new[] { "", "{}", "{", File.ReadAllText(path).Replace("\"MaxAttempts\":4", "\"MaxAttempts\":5"),
            File.ReadAllText(path).Replace("\"ImportedAttempts\":1", "\"ImportedAttempts\":0") })
        {
            var corrupt = System.IO.Path.Combine(root,$"corrupt-{count}.json"); File.WriteAllText(corrupt,invalid);
            Reject(() => _ = new SelectionLoopBudget(corrupt,sha,legacy,() => { })); Check(File.ReadAllText(corrupt) == invalid);
        }
        Reject(() => _ = new SelectionLoopBudget(path,new string('f',64),legacy,() => { }));
        Reject(() => _ = new SelectionLoopBudget(path,sha,legacy,() => throw new InvalidOperationException("consumption_pending")));
        var fields = new Dictionary<string,object> { ["token"] = sha, ["command"] = "selection-loop-select", ["action"] = "training-select-asahi",
            ["nonce"] = nonce, ["frame_sha256"] = sha, ["source_width"] = 1280, ["source_height"] = 720 };
        void Parse() { using var doc = JsonDocument.Parse(JsonSerializer.Serialize(fields)); _ = SelectionLoopBridge.Parse(doc.RootElement,sha); }
        Parse(); Check(true); fields["action"] = "training-confirm-one-bronze"; Reject(Parse); fields["action"] = "training-select-asahi";
        fields["coordinate"] = new[] {461,236}; Reject(Parse); fields.Remove("coordinate"); fields["source_width"] = 1920; Reject(Parse);
        Check(!BrowserHostOptions.Parse(Array.Empty<string>()).EnableTrainingSelectionLoop);
        Reject(() => BrowserHostOptions.Parse(new[] {"--composition-game","--enable-training-selection-loop"}));
        Console.WriteLine(JsonSerializer.Serialize(new {passed=true, checks=count, real_input_attempts=0, evidence_directory=root}));
    }
}

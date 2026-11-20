using System.Security.Cryptography;
using System.Text.Json;

namespace MaaShinyColors.BrowserHost;

/// <summary>Offline ledger/proof/option checks; never creates a browser or sends input.</summary>
internal static class RepeatableNavigationSelfTest
{
    internal static void RunStandalone()
    {
        var root = System.IO.Path.Combine(SelectionLoopBudget.Root(), "dev_assets", "private", "repeatable_navigation_selftests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root); var count = 0;
        Run(root, valid => { if (!valid) { throw new InvalidOperationException("可重复导航离线自检失败。"); } count++; });
        Console.WriteLine(JsonSerializer.Serialize(new { passed = true, checks = count, real_input_attempts = 0, evidence_directory = root }));
    }

    internal static void Run(string parent, Action<bool> check)
    {
        void Reject(System.Action action) { var rejected = false; try { action(); } catch { rejected = true; } check(rejected); }
        string Sha(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        var root = System.IO.Path.Combine(parent, "repeatable");
        Directory.CreateDirectory(root);

        // Options: default off and strictly dependent on the game host plus supervised daily.
        check(!BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily" }).EnableRepeatableNavigation);
        check(BrowserHostOptions.Parse(new[] { "--composition-game", "--enable-supervised-daily", "--enable-repeatable-navigation" }).EnableRepeatableNavigation);
        foreach (var args in new[] {
            new[] { "--enable-repeatable-navigation" },
            new[] { "--composition-game", "--enable-repeatable-navigation" },
            new[] { "--enable-supervised-daily", "--enable-repeatable-navigation" },
            new[] { "--composition-demo", "--enable-supervised-daily", "--enable-repeatable-navigation" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-repeatable-navigation", "--url", "https://example.com/" },
            new[] { "--composition-game", "--enable-supervised-daily", "--enable-repeatable-navigation", "--enable-supervised-navigation", "announcement-back" } })
        { Reject(() => BrowserHostOptions.Parse(args)); }

        // Closed whitelist: consumption, selection, scroll and startup are excluded.
        check(RepeatableNavigationBudget.Actions.Length == 35 &&
            RepeatableNavigationBudget.Actions.Distinct(StringComparer.Ordinal).Count() == 35);
        check(RepeatableNavigationBudget.IsRepeatable("game-start") && RepeatableNavigationBridge.AllowedActions(false).Contains("game-start") &&
            DailyNavigationPolicy.FixedPoint("game-start") == new Point(640, 630) && RepeatableNavigationBudget.ProducerPath("game-start") == RepeatableNavigationBudget.ProducerPath());
        foreach (var action in RepeatableNavigationBudget.Actions)
        { check(DailyNavigationPolicy.IsAction(action) && !DailyNavigationPolicy.IsScroll(action)); _ = DailyNavigationPolicy.FixedPoint(action); }
        foreach (var action in new[] { "training-select-asahi", "training-bronze-plus", "training-open-confirm", "training-confirm-one-bronze",
            "training-result-close", "touch-to-start", "mission-entry", "" }.Concat(DailyNavigationPolicy.ScrollActions))
        { check(!RepeatableNavigationBudget.IsRepeatable(action)); }
        check(!RepeatableNavigationBridge.AllowedActions(false).Contains("home-idol") && !RepeatableNavigationBridge.AllowedActions(false).Contains("idol-training"));
        check(!RepeatableNavigationBridge.AllowedActions(false).Contains("training-list-back") && !RepeatableNavigationBridge.AllowedActions(false).Contains("idol-back"));
        check(RepeatableNavigationBudget.IsRepeatable("training-list-back") && RepeatableNavigationBudget.IsRepeatable("idol-back") &&
            DailyNavigationPolicy.FixedPoint("training-list-back") == new Point(57, 662) && DailyNavigationPolicy.FixedPoint("idol-back") == new Point(57, 662));
        check(RepeatableNavigationBridge.AllowedActions(true, true, true).SequenceEqual(RepeatableNavigationBudget.Actions));
        // Grade Fes: non-consuming navigation only, behind the grade switch, produced by the grade runner.
        var gradeRepeatable = new[] { "fes-grade-entry", "fes-help-close", "grade-join-open", "grade-mode-grade", "grade-back" };
        foreach (var action in gradeRepeatable)
        {
            check(RepeatableNavigationBudget.IsRepeatable(action) && DailyNavigationPolicy.IsGrade(action) && !DailyNavigationPolicy.IsGradeConsuming(action));
            check(!RepeatableNavigationBridge.AllowedActions(true, true).Contains(action) && RepeatableNavigationBridge.AllowedActions(false, false, true).Contains(action));
            check(RepeatableNavigationBudget.ProducerPath(action).EndsWith(System.IO.Path.Combine("tools", "run_daily_grade.py")));
            check(!RepeatableNavigationBudget.InterruptPreGroup.ContainsKey(action));
        }
        foreach (var action in DailyNavigationPolicy.GradeActions.Except(gradeRepeatable))
        { check(DailyNavigationPolicy.IsGradeConsuming(action) && !RepeatableNavigationBudget.IsRepeatable(action) && !RepeatableNavigationBridge.AllowedActions(true, true, true).Contains(action)); }
        // Shop: only non-consuming navigation is repeatable, behind the shop switch, with the shop runner as producer.
        var shopRepeatable = new[] { "home-shop", "shop-money-tab", "shop-back", "shop-exchange-cancel",
            "shop-offer-open-1", "shop-offer-open-2", "shop-offer-open-3", "shop-result-close" };
        foreach (var action in shopRepeatable)
        {
            check(RepeatableNavigationBudget.IsRepeatable(action) && DailyNavigationPolicy.IsShop(action) && !DailyNavigationPolicy.IsShopConsuming(action));
            check(!RepeatableNavigationBridge.AllowedActions(true).Contains(action) && !RepeatableNavigationBridge.AllowedActions(true, false).Contains(action));
            check(RepeatableNavigationBridge.AllowedActions(false, true).Contains(action));
            check(RepeatableNavigationBudget.ProducerPath(action).EndsWith(System.IO.Path.Combine("tools", "run_daily_shop.py")));
        }
        foreach (var action in DailyNavigationPolicy.ShopActions.Except(shopRepeatable))
        { check(!RepeatableNavigationBudget.IsRepeatable(action) && !RepeatableNavigationBridge.AllowedActions(true, true).Contains(action)); }
        check(RepeatableNavigationBudget.ProducerPath("home-mission") == RepeatableNavigationBudget.ProducerPath());

        // Game day: JST with 05:00 rollover. 19:59 UTC = 04:59 JST next calendar day.
        check(RepeatableNavigationBudget.GameDayOf(new DateTimeOffset(2026, 10, 1, 19, 59, 59, TimeSpan.Zero)) == "2026-10-01");
        check(RepeatableNavigationBudget.GameDayOf(new DateTimeOffset(2026, 10, 1, 20, 0, 0, TimeSpan.Zero)) == "2026-10-02");
        check(RepeatableNavigationBudget.GameDayOf(new DateTimeOffset(2026, 10, 2, 5, 0, 0, TimeSpan.FromHours(9))) == "2026-10-02");
        check(RepeatableNavigationBudget.GameDayOf(new DateTimeOffset(2026, 10, 2, 4, 59, 0, TimeSpan.FromHours(9))) == "2026-10-01");
        Reject(() => _ = new RepeatableNavigationBudget(System.IO.Path.Combine(SelectionLoopBudget.Root(), "tools"), () => DateTimeOffset.UtcNow));

        var now = new DateTimeOffset(2026, 10, 1, 3, 0, 0, TimeSpan.Zero); // 12:00 JST, game day 2026-10-01
        var budget = new RepeatableNavigationBudget(root, () => now);
        var producer = RepeatableNavigationBudget.ProducerPath();
        var producerSha = SelectionLoopBudget.Hash(producer);
        var serial = 0;
        string NewNonce() => Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();
        string NewSha() => Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
        (string Path, string Sha) Frame()
        {
            var bytes = RandomNumberGenerator.GetBytes(64); var path = System.IO.Path.Combine(root, $"post-{++serial}.png");
            File.WriteAllBytes(path, bytes); return (path, Sha(bytes));
        }
        Dictionary<string, object?> Proof(string action, string nonce, string pre, (string Path, string Sha) post) => new()
        {
            ["version"] = 1, ["kind"] = RepeatableNavigationBudget.ProofKind, ["action"] = action, ["passed"] = true,
            ["resource_consumption"] = false, ["input_attempts"] = 1, ["nonce"] = nonce, ["session_pid"] = Environment.ProcessId,
            ["pre_frame_sha256"] = pre, ["post_frame"] = new Dictionary<string, string> { ["image"] = post.Path, ["sha256"] = post.Sha },
            ["configuration_sha256"] = new string('a', 64), ["foreground_sha256"] = new string('b', 64),
            ["post_recognitions"] = Array.Empty<object>(), ["foreground"] = new Dictionary<string, object> { ["state"] = "no_known_popup" },
            ["producer_path"] = producer, ["producer_sha256"] = producerSha,
        };
        (string Path, string Sha) Write(Dictionary<string, object?> fields)
        {
            var path = System.IO.Path.Combine(root, $"proof-{++serial}.json"); File.WriteAllText(path, JsonSerializer.Serialize(fields));
            return (path, SelectionLoopBudget.Hash(path));
        }
        bool Cycle(string action)
        {
            var nonce = NewNonce(); var pre = NewSha();
            if (budget.Begin(action, nonce, pre) is not null) { return false; }
            budget.MarkNativeSent(nonce); var post = Frame(); var proof = Write(Proof(action, nonce, pre, post));
            return budget.Confirm(action, nonce, post.Sha, proof.Path, proof.Sha);
        }

        bool Cycle2(RepeatableNavigationBudget target, string action)
        {
            var n = NewNonce(); var p = NewSha();
            if (target.Begin(action, n, p) is not null) { return false; }
            target.MarkNativeSent(n); var post = Frame(); var proofFile = Write(Proof(action, n, p, post));
            return target.Confirm(action, n, post.Sha, proofFile.Path, proofFile.Sha);
        }
        check(budget.Inspect("home-mission") == new RepeatableNavigationBudget.Snapshot("2026-10-01", "ready", 0, 0));
        check(!File.Exists(budget.LedgerPath("2026-10-01"))); // Status never creates a ledger.
        check(budget.Begin("training-confirm-one-bronze", NewNonce(), NewSha()) == "repeatable_action_not_whitelisted");
        check(budget.Begin("mission-scroll-down", NewNonce(), NewSha()) == "repeatable_action_not_whitelisted");
        Reject(() => budget.Begin("home-mission", "bad", NewSha()));

        // Pending is written before input and blocks every repeatable action that day.
        var nonce = NewNonce(); var pre = NewSha();
        check(budget.Begin("home-mission", nonce, pre) is null);
        check(File.ReadAllText(budget.LedgerPath("2026-10-01")).Contains(nonce));
        check(budget.Inspect("home-mission") == new RepeatableNavigationBudget.Snapshot("2026-10-01", "pending", 1, 1));
        check(budget.Begin("home-fes", NewNonce(), NewSha()) == "pending_outcome_blocks_day");
        check(new RepeatableNavigationBudget(root, () => now).Begin("mission-back", NewNonce(), NewSha()) == "pending_outcome_blocks_day");
        var post = Frame(); var good = Proof("home-mission", nonce, pre, post);
        var proof = Write(good);
        check(!budget.Confirm("home-mission", nonce, post.Sha, proof.Path, proof.Sha)); // No native success recorded yet.
        budget.MarkNativeSent(nonce);
        Reject(() => budget.MarkNativeSent(nonce));
        // Proof tampering is refused and leaves the entry pending.
        Reject(() => budget.Confirm("home-mission", nonce, post.Sha, proof.Path, NewSha()));
        foreach (var (key, value) in new (string, object?)[] {
            ("kind", "SC.TrainingSelection.Postcondition"), ("version", 2), ("passed", false), ("resource_consumption", true),
            ("session_pid", Environment.ProcessId + 1), ("nonce", NewNonce()), ("action", "home-fes"), ("input_attempts", 2),
            ("producer_sha256", NewSha()), ("producer_path", System.IO.Path.Combine(SelectionLoopBudget.Root(), "tools", "run_training_selection.py")),
            ("post_frame", new Dictionary<string, string> { ["image"] = post.Path, ["sha256"] = NewSha() }),
            ("extra", 1), ("passed", 1) })
        {
            var altered = new Dictionary<string, object?>(good) { [key] = value }; var file = Write(altered);
            Reject(() => budget.Confirm("home-mission", nonce, post.Sha, file.Path, file.Sha));
        }
        var missing = new Dictionary<string, object?>(good); missing.Remove("foreground"); var missingFile = Write(missing);
        Reject(() => budget.Confirm("home-mission", nonce, post.Sha, missingFile.Path, missingFile.Sha));
        var duplicate = System.IO.Path.Combine(root, "duplicate-proof.json");
        File.WriteAllText(duplicate, JsonSerializer.Serialize(good).TrimEnd('}') + ",\"passed\":true}");
        Reject(() => budget.Confirm("home-mission", nonce, post.Sha, duplicate, SelectionLoopBudget.Hash(duplicate)));
        var publicProof = System.IO.Path.Combine(SelectionLoopBudget.Root(), "README.md");
        Reject(() => budget.Confirm("home-mission", nonce, post.Sha, publicProof, SelectionLoopBudget.Hash(publicProof)));
        File.WriteAllBytes(post.Path + ".bak", File.ReadAllBytes(post.Path)); File.WriteAllBytes(post.Path, new byte[] { 1 });
        Reject(() => budget.Confirm("home-mission", nonce, post.Sha, proof.Path, proof.Sha)); // Post frame file changed.
        File.Copy(post.Path + ".bak", post.Path, overwrite: true);
        var wrongPre = Write(Proof("home-mission", nonce, NewSha(), post));
        check(!budget.Confirm("home-mission", nonce, post.Sha, wrongPre.Path, wrongPre.Sha));
        check(budget.Inspect("home-mission").State == "pending");
        check(budget.Confirm("home-mission", nonce, post.Sha, proof.Path, proof.Sha));
        check(!budget.Confirm("home-mission", nonce, post.Sha, proof.Path, proof.Sha)); // Never confirmed twice.
        check(budget.Inspect("home-mission") == new RepeatableNavigationBudget.Snapshot("2026-10-01", "confirmed", 1, 1));

        // Nonce and pre-frame replay within the day.
        check(budget.Begin("home-fes", nonce, NewSha()) == "nonce_or_frame_replay");
        check(budget.Begin("home-fes", NewNonce(), pre) == "nonce_or_frame_replay");
        // Per-action cap of three; other actions remain available.
        check(Cycle("home-mission") && Cycle("home-mission"));
        check(budget.Begin("home-mission", NewNonce(), NewSha()) == "per_action_cap_reached");
        check(budget.Inspect("home-mission").ActionCount == 3 && Cycle("home-fes"));
        check(budget.Inspect("home-fes") == new RepeatableNavigationBudget.Snapshot("2026-10-01", "confirmed", 1, 4));
        // A shop navigation proof claiming the navigation runner as producer is refused; the shop runner proof is accepted.
        var shopNonce = NewNonce(); var shopPre = NewSha();
        check(budget.Begin("home-shop", shopNonce, shopPre) is null); budget.MarkNativeSent(shopNonce);
        var shopPost = Frame(); var navProduced = Write(Proof("home-shop", shopNonce, shopPre, shopPost));
        Reject(() => budget.Confirm("home-shop", shopNonce, shopPost.Sha, navProduced.Path, navProduced.Sha));
        var shopProof = Proof("home-shop", shopNonce, shopPre, shopPost);
        shopProof["producer_path"] = RepeatableNavigationBudget.ProducerPath("home-shop");
        shopProof["producer_sha256"] = SelectionLoopBudget.Hash(RepeatableNavigationBudget.ProducerPath("home-shop"));
        var shopProduced = Write(shopProof);
        check(budget.Confirm("home-shop", shopNonce, shopPost.Sha, shopProduced.Path, shopProduced.Sha));
        check(budget.Inspect("home-shop") == new RepeatableNavigationBudget.Snapshot("2026-10-01", "confirmed", 1, 5));
        check(budget.Begin("shop-exchange-confirm-1", NewNonce(), NewSha()) == "repeatable_action_not_whitelisted");
        // Unconfirmed native gesture blocks the rest of the day.
        var stuck = NewNonce(); check(budget.Begin("mission-back", stuck, NewSha()) is null); budget.MarkNativeSent(stuck);
        check(budget.Begin("fes-match-entry", NewNonce(), NewSha()) == "pending_outcome_blocks_day");

        // Rollover: 04:59 JST is still the same game day; 05:00 opens a new ledger.
        now = new DateTimeOffset(2026, 10, 1, 19, 59, 0, TimeSpan.Zero);
        check(budget.Begin("fes-match-entry", NewNonce(), NewSha()) == "pending_outcome_blocks_day");
        var late = NewNonce(); var latePre = NewSha();
        now = new DateTimeOffset(2026, 10, 1, 20, 0, 0, TimeSpan.Zero);
        check(budget.Inspect("home-mission") == new RepeatableNavigationBudget.Snapshot("2026-10-02", "ready", 0, 0));
        check(Cycle("home-mission"));
        check(budget.Inspect("home-mission") == new RepeatableNavigationBudget.Snapshot("2026-10-02", "confirmed", 1, 1));
        check(File.ReadAllText(budget.LedgerPath("2026-10-01")).Contains(stuck)); // Old pending is recorded, never reset.
        // A gesture begun at 04:59:59 may be confirmed just after rollover against its own ledger.
        var rolloverRoot = System.IO.Path.Combine(root, "rollover");
        var edge = new DateTimeOffset(2026, 10, 1, 19, 59, 59, TimeSpan.Zero);
        var edgeBudget = new RepeatableNavigationBudget(rolloverRoot, () => edge);
        check(edgeBudget.Begin("reward-ack", late, latePre) is null); edgeBudget.MarkNativeSent(late);
        edge = edge.AddSeconds(2); var edgePost = Frame(); var edgeProof = Write(Proof("reward-ack", late, latePre, edgePost));
        check(edgeBudget.Confirm("reward-ack", late, edgePost.Sha, edgeProof.Path, edgeProof.Sha));
        check(File.ReadAllText(edgeBudget.LedgerPath("2026-10-01")).Contains("confirmed") && !File.Exists(edgeBudget.LedgerPath("2026-10-02")));

        // Corrupt or tampered ledgers are refused and left untouched.
        var valid = File.ReadAllText(budget.LedgerPath("2026-10-02"));
        var corruptions = new[] { "", "{", "{}", "[]", valid.Replace("\"PerActionMax\":3", "\"PerActionMax\":4"),
            valid.Replace("\"DayMax\":60", "\"DayMax\":99"), valid.Replace("\"Version\":1", "\"Version\":2"),
            valid.Replace("\"GameDay\":\"2026-10-02\"", "\"GameDay\":\"2026-10-03\""), valid.Replace("\"confirmed\"", "\"unknown\""),
            valid.Replace("\"home-mission\"", "\"training-confirm-one-bronze\""), valid.TrimEnd('}') + ",\"Extra\":1}",
            valid.TrimEnd('}') + ",\"DayMax\":60}" };
        var corruptIndex = 0;
        foreach (var content in corruptions)
        {
            var corruptRoot = System.IO.Path.Combine(root, $"corrupt-{corruptIndex++}");
            var corruptBudget = new RepeatableNavigationBudget(corruptRoot, () => now);
            var path = corruptBudget.LedgerPath("2026-10-02");
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!); File.WriteAllText(path, content);
            Reject(() => corruptBudget.Begin("home-fes", NewNonce(), NewSha()));
            Reject(() => corruptBudget.Inspect("home-fes"));
            check(File.ReadAllText(path) == content);
        }

        // Interrupted outcome: a fixed Home tap on the inert backdrop of a whitelisted popup.
        check(RepeatableNavigationBudget.InterruptPreGroup.Keys.Order(StringComparer.Ordinal)
            .SequenceEqual(new[] { "home-fes", "home-idol", "home-mission", "home-shop" }));
        check(RepeatableNavigationBudget.InterruptPanels.Keys.Order(StringComparer.Ordinal)
            .SequenceEqual(new[] { "MissionCompleted", "Pickup", "Reward" }));
        check(RepeatableNavigationBudget.OutsidePanels(new Point(62, 282), "MissionCompleted") &&
            RepeatableNavigationBudget.OutsidePanels(new Point(1190, 650), "Pickup") &&
            !RepeatableNavigationBudget.OutsidePanels(new Point(565, 665), "Pickup") && // 「もう表示しない」 checkbox
            !RepeatableNavigationBudget.OutsidePanels(new Point(640, 360), "MissionCompleted") &&
            !RepeatableNavigationBudget.OutsidePanels(new Point(342, 300), "MissionCompleted") && // inside the margin
            !RepeatableNavigationBudget.OutsidePanels(new Point(62, 282), "MissionHelp"));
        var interruptNow = new DateTimeOffset(2026, 10, 2, 3, 0, 0, TimeSpan.Zero);
        var ib = new RepeatableNavigationBudget(System.IO.Path.Combine(root, "interrupt"), () => interruptNow);
        Dictionary<string, object?> Interruption(string action, string popup) => new()
        {
            ["popup"] = popup, ["pre_group"] = "Home", ["underlying_group"] = "Home",
            ["click_point"] = new[] { DailyNavigationPolicy.FixedPoint(action).X, DailyNavigationPolicy.FixedPoint(action).Y },
            ["panel_rects"] = RepeatableNavigationBudget.InterruptPanels.TryGetValue(popup, out var rects) ? rects : new[] { new[] { 1, 2, 3, 4 } },
            ["panel_margin"] = RepeatableNavigationBudget.PanelMargin, ["popup_complete"] = true, ["underlying_present"] = true,
            ["post_differs"] = true, ["expected_post_complete"] = false,
        };
        Dictionary<string, object?> Interrupted(string action, string nonce, string pre, (string Path, string Sha) post, string popup = "MissionCompleted")
        {
            var fields = Proof(action, nonce, pre, post);
            fields["kind"] = RepeatableNavigationBudget.InterruptedProofKind; fields["passed"] = false;
            fields["producer_path"] = RepeatableNavigationBudget.ProducerPath(action);
            fields["producer_sha256"] = SelectionLoopBudget.Hash(RepeatableNavigationBudget.ProducerPath(action));
            fields["interruption"] = Interruption(action, popup);
            return fields;
        }
        var inNonce = NewNonce(); var inPre = NewSha();
        check(ib.Begin("home-shop", inNonce, inPre) is null); ib.MarkNativeSent(inNonce);
        var inPost = Frame(); var inGood = Interrupted("home-shop", inNonce, inPre, inPost);
        var tamperings = new List<Dictionary<string, object?>>();
        foreach (var (key, value) in new (string, object?)[] { ("kind", "SC.RepeatableNavigation.Other"), ("passed", true),
            ("session_pid", Environment.ProcessId + 1), ("action", "home-fes"), ("resource_consumption", true), ("input_attempts", 2),
            ("producer_path", RepeatableNavigationBudget.ProducerPath()), ("extra", 1) })
        { tamperings.Add(new Dictionary<string, object?>(inGood) { [key] = value }); }
        var noInterruption = new Dictionary<string, object?>(inGood); noInterruption.Remove("interruption"); tamperings.Add(noInterruption);
        // A postcondition proof may not smuggle an interruption block, and vice versa.
        tamperings.Add(new Dictionary<string, object?>(inGood) { ["kind"] = RepeatableNavigationBudget.ProofKind });
        tamperings.Add(new Dictionary<string, object?>(inGood) { ["kind"] = RepeatableNavigationBudget.ProofKind, ["passed"] = true });
        foreach (var (key, value) in new (string, object?)[] { ("popup", "MissionHelp"), ("popup", "Story"), ("pre_group", "Mission"),
            ("underlying_group", "Story"), ("click_point", new[] { 640, 360 }), ("click_point", new[] { 62, 283 }),
            ("panel_rects", new[] { new[] { 0, 0, 10, 10 } }), ("panel_margin", 0), ("popup_complete", false),
            ("underlying_present", false), ("post_differs", false), ("expected_post_complete", true), ("extra", 1) })
        {
            var interruption = Interruption("home-shop", "MissionCompleted"); interruption[key] = value;
            tamperings.Add(new Dictionary<string, object?>(inGood) { ["interruption"] = interruption });
        }
        var missingField = Interruption("home-shop", "MissionCompleted"); missingField.Remove("panel_margin");
        tamperings.Add(new Dictionary<string, object?>(inGood) { ["interruption"] = missingField });
        foreach (var altered in tamperings)
        { var file = Write(altered); Reject(() => ib.ConfirmOutcome("home-shop", inNonce, inPost.Sha, file.Path, file.Sha)); }
        check(ib.Inspect("home-shop").State == "pending");
        var inProof = Write(inGood);
        check(ib.ConfirmOutcome("home-shop", inNonce, inPost.Sha, inProof.Path, inProof.Sha) == "interrupted");
        check(ib.ConfirmOutcome("home-shop", inNonce, inPost.Sha, inProof.Path, inProof.Sha) is null); // Never twice.
        check(ib.Inspect("home-shop") == new RepeatableNavigationBudget.Snapshot("2026-10-02", "interrupted", 1, 1));
        check(File.ReadAllText(ib.LedgerPath("2026-10-02")).Contains("\"interrupted\""));
        // A non-interruptible action (pre page not Home) cannot record an interruption, even with valid fields.
        var nonHome = NewNonce(); var nonHomePre = NewSha();
        check(ib.Begin("mission-back", nonHome, nonHomePre) is null); ib.MarkNativeSent(nonHome);
        var nonHomePost = Frame(); var nonHomeProof = Interrupted("mission-back", nonHome, nonHomePre, nonHomePost);
        nonHomeProof["interruption"] = Interruption("home-mission", "MissionCompleted");
        var nonHomeFile = Write(nonHomeProof);
        Reject(() => ib.ConfirmOutcome("mission-back", nonHome, nonHomePost.Sha, nonHomeFile.Path, nonHomeFile.Sha));
        check(ib.Begin("home-fes", NewNonce(), NewSha()) == "pending_outcome_blocks_day");
        var nonHomeOk = Write(Proof("mission-back", nonHome, nonHomePre, nonHomePost));
        check(ib.ConfirmOutcome("mission-back", nonHome, nonHomePost.Sha, nonHomeOk.Path, nonHomeOk.Sha) == "confirmed");
        // Interrupted entries count toward the per-action cap and unblock the day; Pickup and Reward are accepted too.
        bool InterruptCycle(string action, string popup)
        {
            var n = NewNonce(); var p = NewSha();
            if (ib.Begin(action, n, p) is not null) { return false; }
            ib.MarkNativeSent(n); var post = Frame(); var proofFile = Write(Interrupted(action, n, p, post, popup));
            return ib.ConfirmOutcome(action, n, post.Sha, proofFile.Path, proofFile.Sha) == "interrupted";
        }
        check(InterruptCycle("home-fes", "MissionCompleted") && InterruptCycle("home-fes", "Pickup") && InterruptCycle("home-fes", "Reward"));
        check(ib.Begin("home-fes", NewNonce(), NewSha()) == "per_action_cap_reached");
        check(ib.Inspect("home-fes") == new RepeatableNavigationBudget.Snapshot("2026-10-02", "interrupted", 3, 5));
        check(Cycle2(ib, "home-mission"));
        check(ib.Inspect("home-mission") == new RepeatableNavigationBudget.Snapshot("2026-10-02", "confirmed", 1, 6));
        // home-idol's fixed point lies on Pickup's 「もう表示しない」 checkbox: never recorded as interrupted there.
        var idolNonce = NewNonce(); var idolPre = NewSha();
        check(ib.Begin("home-idol", idolNonce, idolPre) is null); ib.MarkNativeSent(idolNonce);
        var idolPost = Frame(); var idolFile = Write(Interrupted("home-idol", idolNonce, idolPre, idolPost, "Pickup"));
        Reject(() => ib.ConfirmOutcome("home-idol", idolNonce, idolPost.Sha, idolFile.Path, idolFile.Sha));
        var idolMission = Write(Interrupted("home-idol", idolNonce, idolPre, idolPost));
        check(ib.ConfirmOutcome("home-idol", idolNonce, idolPost.Sha, idolMission.Path, idolMission.Sha) == "interrupted");
        // A ledger claiming "interrupted" for a non-interruptible action is corrupt.
        var interruptedLedger = File.ReadAllText(ib.LedgerPath("2026-10-02"));
        check(interruptedLedger.Contains("\"State\":\"interrupted\""));
        var forgedRoot = System.IO.Path.Combine(root, "interrupt-forged");
        var forged = new RepeatableNavigationBudget(forgedRoot, () => interruptNow);
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(forged.LedgerPath("2026-10-02"))!);
        File.WriteAllText(forged.LedgerPath("2026-10-02"), interruptedLedger.Replace("\"Action\":\"home-fes\"", "\"Action\":\"match-back\""));
        Reject(() => forged.Inspect("home-fes"));

        // Endpoint parsing: closed commands, whitelist and geometry; no coordinates.
        var token = new string('c', 64);
        var allowed = RepeatableNavigationBridge.AllowedActions(false);
        var fields = new Dictionary<string, object> { ["token"] = token, ["command"] = "repeatable-navigate", ["action"] = "home-mission",
            ["nonce"] = NewNonce(), ["frame_sha256"] = NewSha(), ["source_width"] = 1280, ["source_height"] = 720 };
        RepeatableNavigationRequest Parse(Dictionary<string, object> data) =>
            RepeatableNavigationBridge.Parse(JsonSerializer.SerializeToElement(data), token, allowed);
        check(Parse(fields).Action == "home-mission");
        foreach (var (key, value) in new (string, object)[] { ("action", "training-confirm-one-bronze"), ("action", "home-idol"),
            ("action", "mission-scroll-down"), ("token", new string('d', 64)), ("command", "daily-navigate"), ("source_width", 1920),
            ("nonce", "bad"), ("coordinate", new[] { 1, 2 }) })
        { var altered = new Dictionary<string, object>(fields) { [key] = value }; Reject(() => Parse(altered)); }
        check(Parse(new Dictionary<string, object> { ["token"] = token, ["command"] = "repeatable-status", ["action"] = "home-fes" }).Command == "repeatable-status");
        Reject(() => Parse(new Dictionary<string, object> { ["token"] = token, ["command"] = "repeatable-status" }));
        var confirm = new Dictionary<string, object>(fields) { ["command"] = "repeatable-confirm", ["proof_path"] = "x", ["proof_sha256"] = NewSha() };
        check(Parse(confirm).ProofPath == "x");
        confirm["proof_sha256"] = "bad"; Reject(() => Parse(confirm));

        // Session file mirrors the selection endpoint and disappears on dispose.
        var session = System.IO.Path.Combine(root, "session.repeatable-navigation.json");
        using (new RepeatableNavigationBridge(session, budget, false, (_, _) => Task.FromResult(new RepeatableNavigationResponse(false, false, "offline", "blocked", 0, 0))))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(session)); var data = document.RootElement;
            check(data.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal).SequenceEqual(new[] { "actions", "browser_demo_only",
                "day_max", "game_day", "host", "input_enabled", "ledger_root", "per_action_max", "pid", "port", "token", "transport", "version" }));
            check(data.GetProperty("transport").GetString() == RepeatableNavigationBridge.Transport && data.GetProperty("per_action_max").GetInt32() == 3 &&
                data.GetProperty("day_max").GetInt32() == 60 && data.GetProperty("game_day").GetString() == "2026-10-02" &&
                data.GetProperty("ledger_root").GetString() == root && data.GetProperty("pid").GetInt32() == Environment.ProcessId &&
                data.GetProperty("actions").EnumerateArray().Select(a => a.GetString()).SequenceEqual(allowed));
        }
        check(!File.Exists(session));
        check(RepeatableNavigationBudget.DefaultRoot().EndsWith(System.IO.Path.Combine("dev_assets", "private", "daily_navigation_repeatable")));
    }
}

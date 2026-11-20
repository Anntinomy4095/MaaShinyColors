using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Win32.SafeHandles;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>
/// Phase S of --self-test-ui: 设置 → 存储 (UiStorage) in a throw-away private folder under the self-test output. Checks the
/// category mapping, that protected files (settings, WebView2 profiles, scan records, ledgers, memory, readings, live status)
/// are never deleted, that a junction (reparse point) is neither followed nor deleted, legacy videos stay outside cleanup, recent run logs
/// are kept, prune-by-age, refusal while busy, the router / protocol side and the host prefs. Zero game input.
/// </summary>
internal static partial class UiSelfTest
{
    private static void StoragePhase(Checks checks, string output)
    {
        const string P = "ST";
        var protocol = UiProtocol.LoadEmbedded();
        checks.Add(P, "storage_commands_whitelisted", new[] { "storage.status", "storage.clean", "storage.auto" }.All(protocol.Commands.Contains));

        // ---- classification table (relative paths; no files needed)
        var expected = new (string Path, string? Category)[]
        {
            ("videos/maa-20261012T175932Z-panel.mp4", null),
            ("recordings/wing-demo-20261005-01/frame-000001.png", UiStorage.Samples),
            ("run_logs/samples/business-top-20261003T055049Z/frame-001.png", UiStorage.Samples),
            ("run_logs/diagnostics/20261002T060501589272Z.png", UiStorage.Samples),
            ("card-scan/card-scan-20261008T130307Z/frame-001.png", UiStorage.Samples),
            ("knowhow-scan/scan-20261012T125516Z/frame-0001.png", UiStorage.Samples),
            ("captures-20261011-164549Z/frame-000001.png", UiStorage.Samples),
            ("run_logs/2026-10-02.jsonl", UiStorage.Logs),
            ("run_logs/tasks-20261013T010101Z.json", UiStorage.Logs),
            ("ui/runs/ui-20261006-010444-cc27c2/events.jsonl", UiStorage.Logs),
            ("ui/runs/ui-20261006-010444-cc27c2/profile.json", UiStorage.Logs),
            ("card-scan-ui/20261010T031847Z/summary.json", UiStorage.Logs),
            ("knowhow-scan-ui/20261012T191034Z/events.jsonl", UiStorage.Logs),
            ("wing-ui/decision-log/20261013T025236.jsonl", UiStorage.Logs),
            ("wing-cache/hand-cards.json", UiStorage.Cache),
            // protected / 其他: never categorised
            ("ui/profile.json", null), ("ui/host-window.json", null), ("ui/replay/x/frame.png", null),
            ("ui-webview2-profile/EBWebView/Default/Cookies", null), ("webview2-profile/x", null), ("browser-demo-profile/x.png", null),
            ("card-scan/card-scan-20261008T222234Z.json", null), ("knowhow-scan/knowhow-scan-20261013T150342Z-partial.json", null),
            ("wing-spend-ledger.json", null), ("wing-ui/memory.json", null), ("wing-ui/produce-settings.json", null),
            ("readings/readings.json", null), ("live-status/current.json", null), ("mission-claim/daily-login.sqlite3", null),
            ("daily_shop/2026-10-13.json", null), ("run_logs/host-spend-2026-10-02.jsonl", null),
            ("run_logs/samples", null), ("videos", null), ("ui-selftest/x/selftest-ui.json", null), ("present/ledger.json", null),
        };
        var wrong = expected.Where(item => UiStorage.Classify(item.Path)?.Category != item.Category)
            .Select(item => $"{item.Path} -> {UiStorage.Classify(item.Path)?.Category ?? "null"}").ToList();
        checks.Add(P, "category_mapping", wrong.Count == 0, wrong);

        // ---- a throw-away private root
        var root = Path.Combine(output, "storage", "dev_assets", "private");
        var outside = Path.Combine(output, "storage", "outside");
        if (Directory.Exists(Path.Combine(output, "storage"))) { Directory.Delete(Path.Combine(output, "storage"), recursive: true); }
        var old = DateTime.UtcNow - TimeSpan.FromDays(30);
        string Make(string relative, int bytes, DateTime? write = null, string? baseDir = null)
        {
            var full = Path.Combine(baseDir ?? root, relative.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(full)!);
            File.WriteAllBytes(full, new byte[bytes]);
            File.SetLastWriteTimeUtc(full, write ?? old);
            return full;
        }
        var protectedFiles = new[]
        {
            Make("ui/profile.json", 10), Make("ui/host-window.json", 10), Make("ui-webview2-profile/EBWebView/Cookies", 10),
            Make("webview2-profile/Default/Cache/data_0", 10), Make("card-scan/card-scan-20261008T222234Z.json", 10),
            Make("knowhow-scan/knowhow-scan-20261013T150342Z-partial.json", 10), Make("wing-spend-ledger.json", 10),
            Make("wing-ui/memory.json", 10), Make("wing-ui/produce-settings.json", 10), Make("readings/readings.json", 10),
            Make("live-status/current.json", 10), Make("run_logs/host-spend-2026-10-02.jsonl", 10), Make("daily_shop/2026-10-13.json", 10),
            Make("dev-only/notes.bin", 1000),
        };
        var oldVideo = Make("videos/maa-20261001T000000Z.mp4", 5000);
        var otherVideo = Make("videos/maa-20261014T000000Z-panel.mp4", 700);
        var oldFrame = Make("run_logs/samples/match-20261001T000000Z/frame-001.png", 300);
        var scanFrame = Make("card-scan/card-scan-20261008T222234Z/frame-001.png", 200);
        var oldLog = Make("run_logs/2026-10-01.jsonl", 50);
        var recentLog = Make("run_logs/2026-10-14.jsonl", 60, DateTime.UtcNow);
        var runFile = Make("ui/runs/ui-20261001-000000-aaaaaa/summary.json", 40);
        var outsideFile = Make("keep.mp4", 900, baseDir: outside);
        var junction = Path.Combine(root, "videos", "linked");
        var junctionMade = false;
        try { junctionMade = MakeJunction(junction, outside); }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }

        string? busy = null;
        bool auto = false;
        int days = UiStorage.DefaultDays;
        var storage = new UiStorage(root, () => busy, () => (auto, days), (a, d) => { auto = a; days = d; return true; });
        var scan = storage.ScanNow();
        long Bytes(JsonObject s, string key, string field) => s["categories"]!.AsArray().First(c => (string?)c!["key"] == key)![field]!.GetValue<long>();
        checks.Add(P, "scan_sizes_by_category", Bytes(scan, "samples", "bytes") == 500 && Bytes(scan, "logs", "bytes") == 150
            && Bytes(scan, "logs", "cleanable_bytes") == 90 && scan["other_bytes"]!.GetValue<long>() >= 6700, scan);
        checks.Add(P, "junction_not_followed", !junctionMade || scan["other_bytes"]!.GetValue<long>() >= 6700, new { junctionMade });

        busy = "engine_active";
        var refusal = storage.Clean(UiStorage.Samples);
        checks.Add(P, "clean_refused_while_running", refusal == UiStorage.BusyText("engine_active") && File.Exists(oldVideo), refusal);
        storage.HostBusy = () => "external_run";
        busy = null;
        checks.Add(P, "clean_refused_for_external_run", storage.Clean(UiStorage.Logs) == UiStorage.BusyText("external_run") && File.Exists(oldLog));
        storage.HostBusy = null;
        checks.Add(P, "clean_unknown_category_refused", storage.Clean("everything") is not null);

        checks.Add(P, "legacy_videos_unmanaged", File.Exists(oldVideo) && File.Exists(otherVideo));
        checks.Add(P, "junction_target_kept", File.Exists(outsideFile) && (!junctionMade || Directory.Exists(junction)), new { junctionMade });
        var samples = storage.DeleteNow(UiStorage.Samples, null);
        checks.Add(P, "clean_samples_keeps_records", !File.Exists(oldFrame) && !File.Exists(scanFrame)
            && !Directory.Exists(Path.GetDirectoryName(scanFrame)) && Directory.Exists(Path.Combine(root, "card-scan"))
            && Directory.Exists(Path.Combine(root, "run_logs", "samples")) && samples.FreedBytes == 500, new { samples.FreedBytes, samples.Files });
        var logs = storage.DeleteNow(UiStorage.Logs, null);
        checks.Add(P, "clean_logs_keeps_recent", !File.Exists(oldLog) && File.Exists(recentLog) && !File.Exists(runFile)
            && Directory.Exists(Path.Combine(root, "ui", "runs")) && logs.FreedBytes == 90, new { logs.FreedBytes, logs.Files });
        var missing = protectedFiles.Where(path => !File.Exists(path)).ToList();
        checks.Add(P, "protected_never_deleted", missing.Count == 0, missing);

        // ---- 自动清理: off by default (nothing pruned at start); on = only files older than the retention days
        var tenDays = Make("videos/maa-ten-days.mp4", 11, DateTime.UtcNow - TimeSpan.FromDays(10));
        var twentyDays = Make("run_logs/samples/s-20d/frame.png", 13, DateTime.UtcNow - TimeSpan.FromDays(20));
        var oldCache = Make("wing-cache/hand-cards.json", 7, DateTime.UtcNow - TimeSpan.FromDays(40));
        var startup = new UiStorage(root, () => null, () => (false, 14), (_, _) => true);
        startup.Tick();
        startup.Wait(TimeSpan.FromSeconds(30));
        checks.Add(P, "auto_off_by_default_keeps_files", File.Exists(tenDays) && File.Exists(twentyDays));
        var pruneBusy = "engine_active";
        var pruner = new UiStorage(root, () => pruneBusy, () => (true, 14), (_, _) => true);
        pruner.Tick();                   // host start while a run is active: waits
        checks.Add(P, "prune_waits_for_run", !pruner.Working && File.Exists(twentyDays));
        pruneBusy = null;
        pruner.Tick();                   // run ended -> prune
        pruner.Wait(TimeSpan.FromSeconds(30));
        var last = pruner.Snapshot()["last"] as JsonObject;
        checks.Add(P, "prune_keeps_hand_cache", File.Exists(oldCache));
        checks.Add(P, "prune_by_age", File.Exists(tenDays) && !File.Exists(twentyDays) && File.Exists(otherVideo)
            && (string?)last?["op"] == "prune" && last?["freed_bytes"]?.GetValue<long>() == 13, last);
        checks.Add(P, "protected_never_deleted_after_prune", protectedFiles.All(File.Exists));

        // ---- router / protocol
        var schema = UiTaskSchema.LoadFromUiFolder(UiPaths.OutputUiFolder());
        long now = 0;
        var seq = 0;
        const string Home = "https://maa-ui.test/index.html";
        string Req(string cmd, string paramsJson) => $"{{\"v\":1,\"id\":\"s-{++seq}\",\"cmd\":\"{cmd}\",\"params\":{paramsJson}}}";
        bool Ok(string? reply) => reply is not null && (bool)JsonNode.Parse(reply)!["ok"]! == true;
        var store = new UiProfileStore(Path.Combine(output, "profile-storage.json"), schema);
        var bare = new UiMessageRouter(protocol, schema, store, () => now += 1000);
        checks.Add(P, "router_without_storage_refuses", !Ok(bare.Handle(Home, Req("storage.status", "{}"))));
        var router = new UiMessageRouter(protocol, schema, store, () => now += 1000) { Storage = storage };
        var status = router.Handle(Home, Req("storage.status", "{}"));
        checks.Add(P, "router_status", Ok(status) && JsonNode.Parse(status!)!["result"]!["storage"]!["auto"]!["days"]!.GetValue<int>() == 14, status);
        storage.Wait(TimeSpan.FromSeconds(30));
        checks.Add(P, "router_clean_needs_confirmed", !Ok(router.Handle(Home, Req("storage.clean", "{\"category\":\"samples\"}")))
            && !Ok(router.Handle(Home, Req("storage.clean", "{\"category\":\"videos\",\"confirmed\":true}")))
            && !Ok(router.Handle(Home, Req("storage.clean", "{\"category\":\"profile\",\"confirmed\":true}"))));
        checks.Add(P, "router_auto_days_enum", !Ok(router.Handle(Home, Req("storage.auto", "{\"enabled\":true,\"days\":5}")))
            && Ok(router.Handle(Home, Req("storage.auto", "{\"enabled\":true,\"days\":30}"))) && auto && days == 30);
        checks.Add(P, "router_legacy_videos_unmanaged", File.Exists(tenDays) && File.Exists(otherVideo));
        checks.Add(P, "protected_never_deleted_after_router", protectedFiles.All(File.Exists) && File.Exists(outsideFile));

        // ---- host prefs: missing key = off, retention default 14, invalid days fall back
        var prefsPath = Path.Combine(output, "storage-prefs.json");
        File.WriteAllText(prefsPath, "{\"width\":1680,\"height\":788}");
        var loaded = UiHostPrefs.Load(prefsPath);
        checks.Add(P, "prefs_storage_default_off", !UiHostPrefs.Default.StorageAuto && UiHostPrefs.Default.StorageDays == 14
            && !loaded.StorageAuto && loaded.StorageDays == 14);
        var on = UiHostPrefs.Default with { StorageAuto = true, StorageDays = 7 };
        checks.Add(P, "prefs_storage_round_trip", on.Save(prefsPath) && UiHostPrefs.Load(prefsPath) == on, File.ReadAllText(prefsPath));
        File.WriteAllText(prefsPath, "{\"storage_auto\":true,\"storage_days\":9}");
        checks.Add(P, "prefs_storage_invalid_days", UiHostPrefs.Load(prefsPath).StorageDays == 14 && UiHostPrefs.Load(prefsPath).StorageAuto);

        if (junctionMade) { try { Directory.Delete(junction); } catch (IOException) { } }
    }

    // ---- a real NTFS junction (mount point) without starting a process: FSCTL_SET_REPARSE_POINT on an empty folder.
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(SafeFileHandle device, uint code, byte[] input, int inputSize, IntPtr output, int outputSize,
        out int returned, IntPtr overlapped);

    private static bool MakeJunction(string link, string target)
    {
        Directory.CreateDirectory(link);
        var substitute = Encoding.Unicode.GetBytes(@"\??\" + Path.GetFullPath(target));
        var print = Encoding.Unicode.GetBytes(Path.GetFullPath(target));
        var dataLength = 8 + substitute.Length + 2 + print.Length + 2;
        var buffer = new byte[8 + dataLength];
        BitConverter.GetBytes(0xA0000003u).CopyTo(buffer, 0);                         // IO_REPARSE_TAG_MOUNT_POINT
        BitConverter.GetBytes((ushort)dataLength).CopyTo(buffer, 4);
        BitConverter.GetBytes((ushort)0).CopyTo(buffer, 8);                            // substitute name offset
        BitConverter.GetBytes((ushort)substitute.Length).CopyTo(buffer, 10);
        BitConverter.GetBytes((ushort)(substitute.Length + 2)).CopyTo(buffer, 12);     // print name offset
        BitConverter.GetBytes((ushort)print.Length).CopyTo(buffer, 14);
        substitute.CopyTo(buffer, 16);
        print.CopyTo(buffer, 16 + substitute.Length + 2);
        using var handle = CreateFileW(link, 0x40000000, 0, IntPtr.Zero, 3, 0x02000000 | 0x00200000, IntPtr.Zero);
        if (handle.IsInvalid) { return false; }
        var ok = DeviceIoControl(handle, 0x000900A4, buffer, buffer.Length, IntPtr.Zero, 0, out _, IntPtr.Zero);
        handle.Close();
        return ok && File.GetAttributes(link).HasFlag(FileAttributes.ReparsePoint) && File.Exists(Path.Combine(link, "keep.mp4"));
    }
}

using Microsoft.Web.WebView2.Core;
using System.Text.Json.Nodes;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Targeted callable self-test with a clear delegate; no WebView, game profile, input or stored content.</summary>
internal static class UiGameDataSelfTest
{
    internal static async Task<string> Run(string output)
    {
        var checks = new JsonArray();
        void Check(string name, bool pass)
        {
            checks.Add(new JsonObject { ["check"] = name, ["pass"] = pass });
            if (!pass) { throw new InvalidOperationException("game-data self-test failed: " + name); }
        }
        var ready = false;
        string? blocker = null;
        var calls = new List<CoreWebView2BrowsingDataKinds>();
        var reloads = 0;
        var changes = 0;
        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var data = new UiGameData(() => ready, () => blocker,
            kinds => { calls.Add(kinds); entered.TrySetResult(); return gate.Task; }, () => reloads++);
        data.Changed += () => changes++;
        Check("unready_refused_without_clear", data.Start("cache", false) is not null && calls.Count == 0);
        ready = true;
        Check("login_requires_confirmation", data.Start("login", false) is not null && calls.Count == 0);
        Check("invalid_kind_refused", data.Start("all", true) is not null && calls.Count == 0);
        blocker = "正在扫描";
        Check("active_work_refused", data.Start("cache", false) == blocker && calls.Count == 0);
        blocker = null;
        Check("cache_accepted_and_busy", data.Start("cache", false) is null && data.Busy);
        await entered.Task;
        Check("cache_only_disk_cache", calls.SequenceEqual(new[] { CoreWebView2BrowsingDataKinds.DiskCache }));
        Check("busy_refuses_second_clear", data.Start("login", true) is not null && calls.Count == 1 && reloads == 0);
        Check("no_success_before_completion", data.Status(false)["last"] is null);
        gate.SetResult();
        await data.Pending;
        Check("cache_complete_refreshes_once", !data.Busy && reloads == 1 && changes == 1
            && (bool?)data.Status(false)["last"]?["ok"] == true);

        var protocol = UiProtocol.LoadEmbedded();
        var schema = UiTaskSchema.LoadFromUiFolder(UiPaths.OutputUiFolder());
        var profiles = new UiProfileStore(Path.Combine(output, "game-data-test-profile.json"), schema);
        var router = new UiMessageRouter(protocol, schema, profiles,
            hostStatus: () => new JsonObject { ["game_data"] = data.Status(false) });
        router.GameData = data;
        data.Changed += router.EmitStatus;
        var events = new List<JsonNode>();
        router.EventReady += message => events.Add(JsonNode.Parse(message)!);
        var seq = 0;
        JsonNode Request(string command, string parameters) => JsonNode.Parse(router.Handle(protocol.SourceUri,
            $"{{\"v\":1,\"id\":\"gd-{++seq}\",\"cmd\":\"{command}\",\"params\":{parameters}}}")!)!;
        Check("router_requires_login_confirmation", (bool?)Request("game.data.clear", "{\"kind\":\"login\"}")["ok"] == false);
        Request("status.subscribe", "{\"enabled\":true}");
        gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var reply = Request("game.data.clear", "{\"kind\":\"login\",\"confirmed\":true}");
        await entered.Task;
        Check("router_reports_acceptance_before_finish", (bool?)reply["result"]?["accepted"] == true && data.Busy && reloads == 1);
        Check("login_clears_site_data_only", calls.Last() == CoreWebView2BrowsingDataKinds.AllSite);
        gate.SetResult();
        await data.Pending;
        Check("completion_emits_status", events.Last()["data"]?["game_data"]?["last"]?["op"]?.GetValue<string>() == "login"
            && (bool?)events.Last()["data"]?["game_data"]?["busy"] == false && reloads == 2);

        var failed = new UiGameData(() => true, () => null, _ => Task.FromException(new InvalidOperationException("private/path")), () => reloads++);
        failed.Start("cache", false);
        await failed.Pending;
        Check("clear_failure_never_reloads_or_leaks_details", !failed.Busy && reloads == 2
            && (bool?)failed.Status(false)["last"]?["ok"] == false && !failed.Status(false).ToJsonString().Contains("private/path"));
        var reloadFailed = new UiGameData(() => true, () => null, _ => Task.CompletedTask, () => throw new InvalidOperationException());
        reloadFailed.Start("cache", false);
        await reloadFailed.Pending;
        Check("refresh_failure_reports_actual_partial_result", !reloadFailed.Busy && (bool?)reloadFailed.Status(false)["last"]?["ok"] == false
            && reloadFailed.Status(false)["last"]?["reason"]?.GetValue<string>().Contains("数据已清理") == true);
        return new JsonObject { ["passed"] = true, ["real_game_contact"] = false, ["clear_delegate_only"] = true, ["checks"] = checks }.ToJsonString();
    }
}

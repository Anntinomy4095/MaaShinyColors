using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace MaaShinyColors.BrowserHost.Ui;

/// <summary>Use the task's filtered outcome for display, without changing its status or reconciliation.</summary>
internal static class UiTaskResult
{
    internal static string? Reason(string task, string status, string? fallback, IEnumerable<JsonObject> readings)
    {
        if (status is not ("succeeded" or "failed" or "takeover")) { return fallback; }
        var kind = task switch
        {
            "Shop" => "shop", "Training" => "training", "GradeFes" => "grade",
            "MatchSkip" => "match", "Business" => "business",
            "MissionsClaim" => "missions_claim", "PresentClaim" => "present", _ => null,
        };
        if (kind is null) { return fallback; }
        foreach (var row in readings.Reverse())
        {
            if ((string?)row["task"] != task || (string?)row["kind"] != kind
                || row["readings"] is not JsonObject data || data["outcome"] is not JsonValue value
                || !value.TryGetValue<string>(out var outcome)) { continue; }
            if (!Regex.IsMatch(outcome, @"\A[a-z][a-z0-9_]{0,79}\z")) { continue; }
            if (kind == "shop" && outcome == "skipped_rules" && data["skip_reasons"] is JsonArray reasons)
            {
                var known = reasons.OfType<JsonValue>().Select(item => item.TryGetValue<string>(out var reason) ? reason : null)
                    .Where(reason => reason is "coin_reserve" or "coin_budget_exceeded" or "price_over_max" or "price_unreadable")
                    .Distinct().ToArray();
                if (known.Length > 0) { return outcome + ": " + string.Join(", ", known); }
            }
            return outcome;
        }
        return fallback;
    }

    /// <summary>
    /// A run summary task -> the first plain code in its ``fail_reasons``, or null. The entries start with the
    /// ``SC.FailTask`` reasons of the task's Pipeline stops (``maintenance``, ``gacha_announcement_unhandled``, ...; the
    /// ``fail`` reports of run_tasks), then carry diagnostics. Only an identifier is forwarded (the page maps it with
    /// outcomeText); detail or free text (``near_miss:…``, ``click_refused:…``, paths) and the ``unspecified``
    /// placeholder are skipped, so the generic failure text stays for them. The caller applies it to a failed task that
    /// has no more specific reason.
    /// </summary>
    internal static string? StopReason(JsonObject task)
    {
        if (task["fail_reasons"] is not JsonArray reasons) { return null; }
        foreach (var node in reasons)
        {
            if (node is JsonValue value && value.TryGetValue<string>(out var code)
                && Regex.IsMatch(code, @"\A[a-z][a-z0-9_]{0,63}\z") && code != "unspecified") { return code; }
        }
        return null;
    }

    /// <summary>
    /// A W.I.N.G. run summary task -> "code" or "code：text" from its ``wing`` report (outcome + stop_text), or null.
    /// The page's outcomeText maps the code and keeps the text in brackets (UX review #12, 2026-10-24). A code that
    /// already carries a detail (``click_unavailable:home_minigame_menu``) is returned alone.
    /// </summary>
    internal static string? WingReason(JsonObject task)
    {
        if (task["reports"] is not JsonArray reports) { return null; }
        foreach (var node in reports.Reverse())
        {
            if (node is not JsonObject body || (string?)body["kind"] != "wing"
                || body["outcome"] is not JsonValue value || !value.TryGetValue<string>(out var outcome)) { continue; }
            if (!Regex.IsMatch(outcome, @"\A[a-z][a-z0-9_]{0,63}(:[A-Za-z0-9_.\-]{1,64})?\z")
                || outcome is "running" or "not_started" or "finished" or "produce_finished_home") { return null; }
            var text = body["stop_text"] is JsonValue t && t.TryGetValue<string>(out var s) ? s.Trim() : null;
            if (string.IsNullOrEmpty(text) || outcome.Contains(':')) { return outcome; }
            return outcome + "：" + (text.Length > 120 ? text[..120] : text);
        }
        return null;
    }
}

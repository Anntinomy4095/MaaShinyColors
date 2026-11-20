namespace MaaShinyColors.BrowserHost;

internal sealed record BrowserHostOptions(
    Uri StartUri,
    string UserDataFolder,
    bool OfflineDemo,
    bool BrowserDemo,
    string? BridgeSession,
    bool CompositionDemo = false,
    bool EnableLocalInput = false,
    bool CompositionGame = false,
    string? SupervisedNavigation = null,
    bool EnableSupervisedDaily = false,
    bool EnableSupervisedTraining = false,
    bool EnableSupervisedScroll = false,
    bool EnableTrainingSelectionLoop = false,
    string? TrainingSelectionResolution = null,
    string? TrainingSelectionResolutionSha256 = null,
    bool EnableRepeatableNavigation = false,
    bool EnableSupervisedShop = false,
    bool EnableSupervisedGrade = false,
    bool EnablePipelineInput = false,
    bool BackgroundKeepalive = false,
    string DemoPage = "index.html",
    bool OmitBackgroundArguments = false,
    bool EnableProduceExplore = false,
    bool LegacySettingsPanel = false,
    bool UiPanel = false)
{
    public const string DefaultWindowTitle = "MaaShinyColors BrowserHost";
    public const string OfflineWindowTitle = "MaaShinyColors OfflineHarness";
    public const string BrowserDemoTitle = "MaaShinyColors BrowserDemo";
    public const string CompositionGameTitle = "MaaShinyColors";
    public const int ViewportWidth = 1280;
    public const int ViewportHeight = 720;

    private static readonly Uri DefaultStartUri =
        new("https://shinycolors.enza.fun/");

    public static BrowserHostOptions Parse(string[] args)
    {
        Uri startUri = DefaultStartUri;
        string? userDataFolder = null;
        var offlineDemo = false;
        var browserDemo = false;
        string? bridgeSession = null;
        var compositionDemo = false;
        var enableLocalInput = false;
        var compositionGame = false;
        string? supervisedNavigation = null;
        var enableSupervisedDaily = false;
        var enableSupervisedTraining = false;
        var enableSupervisedScroll = false;
        var enableTrainingSelectionLoop = false;
        string? trainingSelectionResolution = null;
        string? trainingSelectionResolutionSha256 = null;
        var enableRepeatableNavigation = false;
        var enableSupervisedShop = false;
        var enableSupervisedGrade = false;
        var enablePipelineInput = false;
        var backgroundKeepalive = false;
        var enableProduceExplore = false;
        var legacySettingsPanel = false;
        var uiPanel = false;

        for (var index = 0; index < args.Length; index++)
        {
            switch (args[index])
            {
                case "--url":
                    startUri = ParseHttpsUri(NextValue(args, ref index, "--url"));
                    break;
                case "--user-data-dir":
                    userDataFolder = Path.GetFullPath(
                        NextValue(args, ref index, "--user-data-dir"));
                    break;
                case "--offline-demo":
                    offlineDemo = true;
                    break;
                case "--browser-demo":
                    browserDemo = true;
                    break;
                case "--composition-demo":
                    compositionDemo = true;
                    browserDemo = true;
                    break;
                case "--enable-local-input":
                    enableLocalInput = true;
                    break;
                case "--composition-game":
                    compositionGame = true;
                    break;
                case "--enable-supervised-daily":
                    enableSupervisedDaily = true;
                    break;
                case "--enable-supervised-training":
                    enableSupervisedTraining = true;
                    break;
                case "--enable-supervised-scroll":
                    enableSupervisedScroll = true;
                    break;
                case "--enable-training-selection-loop":
                    enableTrainingSelectionLoop = true;
                    break;
                case "--enable-repeatable-navigation":
                    enableRepeatableNavigation = true;
                    break;
                case "--enable-supervised-shop":
                    enableSupervisedShop = true;
                    break;
                case "--enable-supervised-grade":
                    enableSupervisedGrade = true;
                    break;
                case "--enable-pipeline-input":
                    enablePipelineInput = true;
                    break;
                case "--enable-produce-explore":
                    enableProduceExplore = true;
                    break;
                case "--legacy-settings-panel":
                    // Development fallback (U4): the old WinForms daily settings panel instead of the new UI panel.
                    legacySettingsPanel = true;
                    break;
                case "--ui-panel":
                    // Offline verification only: dock the new UI panel next to the owned Composition demo page.
                    uiPanel = true;
                    break;
                case "--background-keepalive":
                    backgroundKeepalive = true;
                    break;
                case "--training-selection-resolution":
                    trainingSelectionResolution = Path.GetFullPath(NextValue(args, ref index, "--training-selection-resolution"));
                    break;
                case "--training-selection-resolution-sha256":
                    trainingSelectionResolutionSha256 = NextValue(args, ref index, "--training-selection-resolution-sha256");
                    break;
                case "--enable-supervised-navigation":
                    supervisedNavigation = NextValue(args, ref index, "--enable-supervised-navigation");
                    if (supervisedNavigation is not ("announcement-back" or "mission-entry" or "touch-to-start"))
                    { throw new ArgumentException("仅允许启动页进入、公告返回或主页任务入口固定导航。"); }
                    break;
                case "--bridge-session":
                    bridgeSession = Path.GetFullPath(
                        NextValue(args, ref index, "--bridge-session"));
                    break;
                default:
                    throw new ArgumentException($"未知参数：{args[index]}");
            }
        }

        userDataFolder ??= DefaultUserDataFolder();
        if (offlineDemo && browserDemo)
        {
            throw new ArgumentException("GDI 靶场与浏览器靶场不能同时启用。");
        }
        if (enableLocalInput && !compositionDemo)
        {
            throw new ArgumentException("原生输入仅允许显式启用的自有 Composition 靶场。");
        }
        if (compositionGame && (offlineDemo || browserDemo || compositionDemo || enableLocalInput))
        { throw new ArgumentException("Composition 游戏与自有靶场必须隔离。"); }
        if (supervisedNavigation is not null && !compositionGame)
        { throw new ArgumentException("监督单步导航只允许 Composition 游戏宿主。"); }
        if (enableSupervisedDaily && (!compositionGame || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri)))
        { throw new ArgumentException("监督日常仅允许游戏 HTTPS 宿主，并与单步导航互斥。"); }
        if (enableSupervisedTraining && !enableSupervisedDaily)
        { throw new ArgumentException("单铜券训练必须单独启用并绑定监督日常宿主。"); }
        if (enableSupervisedScroll && !enableSupervisedDaily)
        { throw new ArgumentException("只读滚动必须单独启用并绑定监督日常宿主。"); }
        if (enableTrainingSelectionLoop && (!compositionGame || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri) || trainingSelectionResolution != SelectionLoopBudget.DefaultResolutionPath() ||
            !SelectionLoopBudget.Sha(trainingSelectionResolutionSha256)))
        { throw new ArgumentException("选卡循环必须独立启用并绑定固定解决证明与审查SHA。"); }
        if (!enableTrainingSelectionLoop && (trainingSelectionResolution is not null || trainingSelectionResolutionSha256 is not null))
        { throw new ArgumentException("选卡解决证明参数必须绑定显式循环开关。"); }
        if (enableRepeatableNavigation && (!compositionGame || !enableSupervisedDaily || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri)))
        { throw new ArgumentException("可重复导航必须显式绑定 Composition 游戏宿主与监督日常。"); }
        if (enableSupervisedShop && (!compositionGame || !enableSupervisedDaily || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri)))
        { throw new ArgumentException("每日商店必须单独启用并绑定 Composition 游戏宿主与监督日常。"); }
        if (enableSupervisedGrade && (!compositionGame || !enableSupervisedDaily || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri)))
        { throw new ArgumentException("Grade Fes 必须单独启用并绑定 Composition 游戏宿主与监督日常。"); }
        // Independent endpoint for the Maa custom controller; it does not need the legacy supervised flags.
        if (enablePipelineInput && (!compositionGame || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri)))
        { throw new ArgumentException("Pipeline 输入必须显式绑定 Composition 游戏 HTTPS 宿主，并与单步导航互斥。"); }
        // Default-off supervised exploration grid: rides on the pipeline-click endpoint only.
        if (enableProduceExplore && (!enablePipelineInput || !compositionGame || supervisedNavigation is not null ||
            !DailyNavigationPolicy.IsGameUri(startUri.AbsoluteUri)))
        { throw new ArgumentException("监督探索网格必须与 --enable-pipeline-input 一起绑定 Composition 游戏 HTTPS 宿主，并与单步导航互斥。"); }
        // Experimental, default-off: Chromium flags + hide-to-tray. Composition hosts only.
        if (backgroundKeepalive && !(compositionDemo || compositionGame))
        { throw new ArgumentException("后台保活实验仅适用于 Composition 宿主。"); }
        if (legacySettingsPanel && !compositionGame)
        { throw new ArgumentException("旧设置面板只适用于 Composition 游戏宿主。"); }
        if (uiPanel && !compositionDemo)
        { throw new ArgumentException("--ui-panel 只用于自有 Composition 测试页（游戏宿主默认已使用新界面）。"); }
        return new BrowserHostOptions(
            startUri, userDataFolder, offlineDemo, browserDemo, bridgeSession,
            compositionDemo, enableLocalInput, compositionGame, supervisedNavigation, enableSupervisedDaily, enableSupervisedTraining, enableSupervisedScroll, enableTrainingSelectionLoop, trainingSelectionResolution, trainingSelectionResolutionSha256, enableRepeatableNavigation, enableSupervisedShop, enableSupervisedGrade, enablePipelineInput, backgroundKeepalive,
            EnableProduceExplore: enableProduceExplore, LegacySettingsPanel: legacySettingsPanel, UiPanel: uiPanel);
    }

    /// <summary>U4: the game host docks the new isolated UI panel unless --legacy-settings-panel is given.</summary>
    public bool DockedUiPanel => (CompositionGame && !LegacySettingsPanel) || (CompositionDemo && UiPanel);

    public string WindowTitle =>
        CompositionGame ? CompositionGameTitle :
        OfflineDemo ? OfflineWindowTitle :
        BrowserDemo ? BrowserDemoTitle : DefaultWindowTitle;

    private static string NextValue(
        IReadOnlyList<string> args,
        ref int index,
        string option)
    {
        index++;
        if (index >= args.Count || string.IsNullOrWhiteSpace(args[index]))
        {
            throw new ArgumentException($"{option} 缺少参数值。");
        }

        return args[index];
    }

    private static Uri ParseHttpsUri(string raw)
    {
        if (!Uri.TryCreate(raw, UriKind.Absolute, out var uri) ||
            !string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.Ordinal))
        {
            throw new ArgumentException("浏览器宿主只允许 HTTPS 起始地址。");
        }

        return uri;
    }

    private static string DefaultUserDataFolder()
    {
        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            if (File.Exists(Path.Combine(current.FullName, "project_policy.json")))
            {
                return Path.Combine(
                    current.FullName,
                    "dev_assets",
                    "private",
                    "webview2-profile");
            }

            current = current.Parent;
        }

        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "MaaShinyColors",
            "BrowserHost");
    }
}

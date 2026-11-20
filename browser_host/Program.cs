using System.Globalization;

namespace MaaShinyColors.BrowserHost;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        if (args.SequenceEqual(new[] { "--self-test-appeal-timing" }))
        {
            AppealTimingSelfTest.RunStandalone().GetAwaiter().GetResult();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-selection-loop" }))
        {
            SelectionLoopSelfTest.Run();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-repeatable-navigation" }))
        {
            RepeatableNavigationSelfTest.RunStandalone();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-pipeline-input" }))
        {
            PipelineInputSelfTest.RunStandalone().GetAwaiter().GetResult();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-produce-explore" }))
        {
            ProduceExploreSelfTest.RunStandalone().GetAwaiter().GetResult();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-daily-settings" }))
        {
            DailySettingsSelfTest.Run();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-navigation" }))
        {
            NavigationSelfTest.Run().GetAwaiter().GetResult();
            return;
        }
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.SequenceEqual(new[] { "--self-test-ui" }))
        {
            // Offline UI panel self-test: local maa-ui.test page only, never the game.
            Ui.UiSelfTest.Run();
            return;
        }
        if (args.SequenceEqual(new[] { "--self-test-ui-dock" }))
        {
            // U4: own Composition demo page (never the game) with the new UI panel docked.
            UiDockSelfTest.Run();
            return;
        }
        if (args.Length > 0 && args[0] == Ui.UiShellOptions.PreviewSwitch)
        {
            // Offline UI preview: no game WebView, no remote URL, no input. Only --ui-devtools may follow;
            // in particular it can never be combined with --composition-game.
            try { Application.Run(new Ui.UiShellForm(Ui.UiShellOptions.ForPreview(args.Skip(1).ToArray()))); }
            catch (Exception exception)
            {
                MessageBox.Show(exception.Message, "MaaShinyColors 界面预览", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            return;
        }
        if (args.Length > 0 && args[0] == "--self-test-background")
        {
            // Opens its own window on the local heartbeat page; never loads the game.
            BackgroundSelfTest.Run(args.Skip(1).ToArray());
            return;
        }

        try
        {
            var options = BrowserHostOptions.Parse(args);
            Form form = options.CompositionDemo || options.CompositionGame
                ? new CompositionDemoForm(options)
                : options.OfflineDemo
                ? new OfflineHarnessForm(options)
                : new BrowserHostForm(options);
            Application.Run(form);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                string.Format(
                    CultureInfo.CurrentCulture,
                    "MaaShinyColors 启动失败：{0}",
                    exception.Message),
                BrowserHostOptions.DefaultWindowTitle,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}

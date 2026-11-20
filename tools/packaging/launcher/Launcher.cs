// MaaShinyColors portable launcher. Built by tools/package.py with the .NET Framework 4.x
// csc.exe that ships with Windows, so it needs no extra runtime. C# 5 only (that compiler's language level).
//
// It only starts the packaged host with a fixed, safe argument set:
//   (no arguments)       -> game + task UI, ready to run the user's selected tasks
//   --self-test-ui       -> host --self-test-ui       (offline, local test page only; waits and returns the exit code)
//   --self-test-ui-dock  -> host --self-test-ui-dock  (offline, own Composition demo page; waits)
//   --ui-preview         -> host --ui-preview         (offline UI preview, no game WebView)
//   --check              -> environment check only, writes dev_assets\private\launcher-check.json, starts nothing
//   --supervised        -> compatibility alias for the normal launch
//   --import-resources  -> import only local recognition templates from a folder chosen by the user
// Every other argument is refused; arbitrary host switches are never forwarded.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: System.Reflection.AssemblyTitle("MaaShinyColors")]
[assembly: System.Reflection.AssemblyProduct("MaaShinyColors portable launcher")]

internal static class Launcher
{
    private const string Title = "MaaShinyColors";
    private const string WebView2ClientGuid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
    private const string WebView2Download = "https://developer.microsoft.com/microsoft-edge/webview2/";
    private static readonly string[] OfflineModes = { "--self-test-ui", "--self-test-ui-dock", "--ui-preview" };
    private static readonly uint ShowExistingMessage = RegisterWindowMessage("MaaShinyColors.ShowExisting.v1");

    private delegate bool WindowVisitor(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint RegisterWindowMessage(string name);
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(WindowVisitor visitor, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")]
    private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern bool AllowSetForegroundWindow(uint processId);

    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        bool check = args.Length == 1 && args[0] == "--check";
        bool supervised = args.Length == 0 || (args.Length == 1 && args[0] == "--supervised");
        bool import = args.Length == 1 && args[0] == "--import-resources";
        bool offline = args.Length == 1 && Array.IndexOf(OfflineModes, args[0]) >= 0;
        bool quiet = check || (offline && args[0] != "--ui-preview");
        if (!(args.Length == 0 || check || offline || supervised || import))
        {
            // Refused quietly (stderr + launcher-check.json): only scripts or shortcuts pass arguments.
            return Fail(true, root, "启动器参数：--supervised、--import-resources、--self-test-ui、--self-test-ui-dock、--ui-preview、--check。", 2);
        }

        string host = Path.Combine(root, "host\\MaaShinyColors.BrowserHost.exe");
        string python = Path.Combine(root, ".venv\\Scripts\\python.exe");
        string[] required = { host, python, Path.Combine(root, "project_policy.json"), Path.Combine(root, "assets", "interface.json"),
            Path.Combine(root, "tools\\ui_engine.py") };
        foreach (string path in required)
        {
            if (!File.Exists(path)) { return Fail(quiet, root, "包体不完整，缺少：" + path.Substring(root.Length + 1), 3); }
        }

        if (import) { return ImportResources(root, python); }
        string webView2 = WebView2Version();
        if (webView2 == null)
        {
            return Fail(quiet, root, "未检测到 Microsoft Edge WebView2 Runtime。\n请先安装 Evergreen 版：" + WebView2Download, 4);
        }

        string data = Path.Combine(root, "dev_assets\\private");
        try
        {
            Directory.CreateDirectory(data);
            string probe = Path.Combine(data, ".write-probe");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
        }
        catch (Exception exception)
        {
            return Fail(quiet, root, "数据目录不可写（请把包放在用户可写位置，不要放 Program Files）：\n" + data + "\n" + exception.Message, 5);
        }

        if (check)
        {
            WriteCheck(root, true, "ok", webView2);
            return 0;
        }

        if (supervised && HostAlreadyRunning(host, true))
        {
            return 0;
        }

        if (supervised && !HasRecognitionResources(root))
        {
            int imported = ImportResources(root, python);
            if (imported != 0 || !HasRecognitionResources(root)) { return imported; }
        }
        string hostArgs = supervised ? "--composition-game --enable-pipeline-input --enable-produce-explore"
            : args[0];
        var info = new ProcessStartInfo(host, hostArgs);
        info.WorkingDirectory = root;  // the host resolves the project root and its private data folder from here
        info.UseShellExecute = false;
        Process process;
        try { process = Process.Start(info); }
        catch (Exception exception) { return Fail(quiet, root, "宿主启动失败：" + exception.Message, 7); }
        if (process == null) { return Fail(quiet, root, "宿主启动失败。", 7); }
        if (args.Length == 0 || supervised || args[0] == "--ui-preview") { return 0; }
        process.WaitForExit();
        return process.ExitCode;
    }

    private static int ImportResources(string root, string python)
    {
        string host = Path.Combine(root, "host\\MaaShinyColors.BrowserHost.exe");
        if (HostAlreadyRunning(host)) { return Fail(false, root, "请先退出本包，再导入识别模板。", 6); }
        using (var picker = new FolderBrowserDialog())
        {
            picker.Description = "选择旧项目目录或 resource_native 目录。只导入识别模板，不导入账号或截图。";
            picker.ShowNewFolderButton = false;
            if (picker.ShowDialog() != DialogResult.OK) { return 0; }
            var info = new ProcessStartInfo(python, "-X utf8 " + Quote(Path.Combine(root, "tools\\import_local_resources.py")) +
                " --source " + Quote(picker.SelectedPath));
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            try
            {
                using (Process process = Process.Start(info))
                {
                    string output = process.StandardOutput.ReadToEnd();
                    string error = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    MessageBox.Show(output + error, Title, MessageBoxButtons.OK,
                        process.ExitCode == 0 ? MessageBoxIcon.Information : MessageBoxIcon.Error);
                    return process.ExitCode;
                }
            }
            catch (Exception exception) { return Fail(false, root, "导入失败：" + exception.Message, 7); }
        }
    }

    private static string Quote(string value)
    {
        // Windows argv quoting, including a selected drive root ending in a backslash.
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    private static string WebView2Version()
    {
        string[] keys =
        {
            @"HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\" + WebView2ClientGuid,
            @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\EdgeUpdate\Clients\" + WebView2ClientGuid,
            @"HKEY_CURRENT_USER\Software\Microsoft\EdgeUpdate\Clients\" + WebView2ClientGuid,
        };
        foreach (string key in keys)
        {
            string version = Registry.GetValue(key, "pv", null) as string;
            if (!string.IsNullOrEmpty(version) && version != "0.0.0.0") { return version; }
        }
        return null;
    }

    private static bool HasRecognitionResources(string root)
    {
        return File.Exists(Path.Combine(root, "assets\\resource\\recognition\\manifest.json")) ||
            File.Exists(Path.Combine(root, "dev_assets\\private\\resource_native\\manifest.json"));
    }

    private static bool HostAlreadyRunning(string host, bool show = false)
    {
        foreach (Process other in Process.GetProcessesByName("MaaShinyColors.BrowserHost"))
        {
            try
            {
                if (string.Equals(Path.GetFullPath(other.MainModule.FileName), Path.GetFullPath(host), StringComparison.OrdinalIgnoreCase))
                {
                    if (show)
                    {
                        uint targetId = (uint)other.Id;
                        AllowSetForegroundWindow(targetId);
                        // Enumerate even hidden/cloaked forms; MainWindowHandle can be zero in tray mode.
                        EnumWindows(delegate(IntPtr window, IntPtr parameter)
                        {
                            uint owner;
                            GetWindowThreadProcessId(window, out owner);
                            if (owner == targetId) { PostMessage(window, ShowExistingMessage, IntPtr.Zero, IntPtr.Zero); }
                            return true;
                        }, IntPtr.Zero);
                    }
                    return true;
                }
            }
            catch (Exception) { }  // processes we may not inspect belong to someone else's install
            finally { other.Dispose(); }
        }
        return false;
    }

    private static int Fail(bool quiet, string root, string message, int code)
    {
        if (quiet)
        {
            Console.Error.WriteLine(message);
            WriteCheck(root, false, message, WebView2Version());
        }
        else
        {
            MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        return code;
    }

    private static void WriteCheck(string root, bool passed, string message, string webView2)
    {
        try
        {
            string data = Path.Combine(root, "dev_assets\\private");
            Directory.CreateDirectory(data);
            string json = "{\"passed\":" + (passed ? "true" : "false") + ",\"message\":\"" + Escape(message) +
                "\",\"webview2\":\"" + Escape(webView2 ?? "") + "\",\"default_input_enabled\":true,\"recognition_ready\":" +
                (HasRecognitionResources(root) ? "true" : "false") + ",\"time_utc\":\"" + DateTime.UtcNow.ToString("o") + "\"}\n";
            File.WriteAllText(Path.Combine(data, "launcher-check.json"), json, new UTF8Encoding(false));
        }
        catch (Exception) { }
    }

    private static string Escape(string text)
    {
        var builder = new StringBuilder();
        foreach (char c in text)
        {
            if (c == '"' || c == '\\') { builder.Append('\\').Append(c); }
            else if (c < ' ') { builder.Append("\\u").Append(((int)c).ToString("x4")); }
            else { builder.Append(c); }
        }
        return builder.ToString();
    }
}

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
//   --update            -> 设置 → 「重启并更新」: wait for the host to exit, apply the staged update, then the normal launch
//   --apply-update      -> apply a staged update only (quiet; exit 0 = applied or nothing staged, 8 = failed and restored)
// Every other argument is refused; arbitrary host switches are never forwarded.
//
// Staged update (2026-11-27, written by the host's 下载更新 under dev_assets\private\update\staged\): ready.txt lists the
// target/base version, each new file with its SHA-256 (under files\) and the files to remove, ending with "end". A normal
// launch with no host running applies it first: every replaced or removed file is backed up, any failure restores them.
// Player data (dev_assets\, ui\game-assets\) is never written. The running launcher replaces itself by renaming.
using System;
using System.Collections.Generic;
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
        bool update = args.Length == 1 && args[0] == "--update";
        bool applyOnly = args.Length == 1 && args[0] == "--apply-update";
        bool quiet = check || applyOnly || (offline && args[0] != "--ui-preview");
        if (!(args.Length == 0 || check || offline || supervised || import || update || applyOnly))
        {
            // Refused quietly (stderr + launcher-check.json): only scripts or shortcuts pass arguments.
            return Fail(true, root, "启动器参数：--supervised、--update、--apply-update、--import-resources、--self-test-ui、--self-test-ui-dock、--ui-preview、--check。", 2);
        }

        string host = Path.Combine(root, "host\\MaaShinyColors.BrowserHost.exe");
        if (applyOnly)
        {
            string message;
            int applied = ApplyStagedUpdate(root, out message);
            if (applied < 0) { Console.Error.WriteLine(message); return 8; }
            return 0;
        }
        if (update)
        {
            // The host closes itself right after starting this; wait for it (at most 60 s) before touching its files.
            for (int i = 0; i < 120 && HostAlreadyRunning(host); i++) { System.Threading.Thread.Sleep(500); }
            supervised = true;
        }
        if (supervised && !HostAlreadyRunning(host))
        {
            string message;
            if (ApplyStagedUpdate(root, out message) < 0)
            {
                MessageBox.Show("更新未完成，已恢复原版本：\n" + message, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }
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

    private static readonly string[] UserDataPrefixes = { "dev_assets\\", "ui\\game-assets\\", "host\\ui\\game-assets\\" };

    /// <summary>0 = nothing staged, 1 = applied, -1 = refused or failed (installed files restored, staging removed).</summary>
    private static int ApplyStagedUpdate(string root, out string message)
    {
        message = null;
        string dir = Path.Combine(root, "dev_assets\\private\\update");
        string staged = Path.Combine(dir, "staged");
        string ready = Path.Combine(staged, "ready.txt");
        TryDelete(Path.Combine(root, "MaaShinyColors.exe.old"));   // left by the previous self-replacement
        if (!File.Exists(ready)) { return 0; }
        string to = null, from = null;
        var files = new List<string[]>();
        var removed = new List<string>();
        try
        {
            bool complete = false;
            foreach (string line in File.ReadAllLines(ready, Encoding.UTF8))
            {
                string[] part = line.Split('\t');
                if (part[0] == "to" && part.Length == 2) { to = part[1]; }
                else if (part[0] == "from" && part.Length == 2) { from = part[1]; }
                else if (part[0] == "file" && part.Length == 3) { files.Add(new[] { SafeRelative(part[1]), part[2].ToLowerInvariant() }); }
                else if (part[0] == "remove" && part.Length == 2) { removed.Add(SafeRelative(part[1])); }
                else if (part[0] == "end" && part.Length == 1) { complete = true; }
                else if (line.Length > 0) { throw new InvalidDataException("无法识别：" + line); }
            }
            string current = InstalledVersion(root);
            if (!complete || to == null || from == null) { throw new InvalidDataException("更新清单不完整"); }
            if (from != "*" && from != current) { throw new InvalidDataException("更新包适用于 " + from + "，当前为 " + current); }
            foreach (string[] file in files)
            {
                string source = Path.Combine(staged, "files\\" + file[0]);
                if (!File.Exists(source) || Sha256(source) != file[1]) { throw new InvalidDataException("文件校验失败：" + file[0]); }
            }
        }
        catch (Exception exception)
        {
            message = exception.Message;
            TryDeleteTree(staged);
            WriteUpdateResult(dir, false, to, message, 0);
            return -1;
        }

        string backup = Path.Combine(dir, "backup");
        TryDeleteTree(backup);
        var saved = new List<string>();     // replaced or removed: restored from backup on failure
        var created = new List<string>();   // did not exist before: deleted on failure
        try
        {
            foreach (string[] file in files)
            {
                string target = Path.Combine(root, file[0]);
                if (File.Exists(target)) { CopyInto(target, Path.Combine(backup, file[0])); saved.Add(file[0]); }
                else { created.Add(file[0]); }
                Replace(target, Path.Combine(staged, "files\\" + file[0]));
            }
            foreach (string relative in removed)
            {
                string target = Path.Combine(root, relative);
                if (!File.Exists(target)) { continue; }
                CopyInto(target, Path.Combine(backup, relative));
                saved.Add(relative);
                Retry(delegate { File.Delete(target); });
            }
        }
        catch (Exception exception)
        {
            message = exception.Message;
            foreach (string relative in saved)
            {
                try { Replace(Path.Combine(root, relative), Path.Combine(backup, relative)); } catch (Exception) { }
            }
            foreach (string relative in created) { TryDelete(Path.Combine(root, relative)); }
            TryDeleteTree(staged);
            WriteUpdateResult(dir, false, to, message, 0);
            return -1;
        }
        TryDeleteTree(staged);
        TryDeleteTree(backup);
        WriteUpdateResult(dir, true, to, null, files.Count + removed.Count);
        return 1;
    }

    private static string SafeRelative(string path)
    {
        string relative = path.Replace('/', '\\');
        if (relative.Length == 0 || relative.StartsWith("\\") || relative.IndexOf(':') >= 0
            || Array.IndexOf(relative.Split('\\'), "..") >= 0 || Array.IndexOf(relative.Split('\\'), "") >= 0)
        {
            throw new InvalidDataException("路径无效：" + path);
        }
        foreach (string prefix in UserDataPrefixes)
        {
            if (relative.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) { throw new InvalidDataException("不可写入用户数据：" + path); }
        }
        return relative;
    }

    private static string InstalledVersion(string root)
    {
        try
        {
            string text = File.ReadAllText(Path.Combine(root, "assets\\interface.json"), Encoding.UTF8);
            var match = System.Text.RegularExpressions.Regex.Match(text, "\"version\"\\s*:\\s*\"([^\"]+)\"");
            return match.Success ? match.Groups[1].Value : "";
        }
        catch (Exception) { return ""; }
    }

    private static string Sha256(string path)
    {
        using (var sha = System.Security.Cryptography.SHA256.Create())
        using (var stream = File.OpenRead(path))
        {
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }
    }

    private static void CopyInto(string source, string target)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(target));
        File.Copy(source, target, true);
    }

    /// <summary>Writes <paramref name="source"/> over <paramref name="target"/>; the running launcher is renamed first.</summary>
    private static void Replace(string target, string source)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(target));
        // Only while no .old exists is the target still the running image (a restore writes over the new copy instead).
        string old = target + ".old";
        if (string.Equals(Path.GetFullPath(target), Path.GetFullPath(Application.ExecutablePath), StringComparison.OrdinalIgnoreCase)
            && File.Exists(target) && !File.Exists(old))
        {
            File.Move(target, old);
        }
        Retry(delegate { File.Copy(source, target, true); });
    }

    /// <summary>A file the closing host still holds is retried for up to 10 s.</summary>
    private static void Retry(Action action)
    {
        for (int attempt = 0; ; attempt++)
        {
            try { action(); return; }
            catch (IOException) { if (attempt >= 20) { throw; } }
            catch (UnauthorizedAccessException) { if (attempt >= 20) { throw; } }
            System.Threading.Thread.Sleep(500);
        }
    }

    private static void TryDelete(string path)
    {
        try { if (File.Exists(path)) { File.Delete(path); } } catch (Exception) { }
    }

    private static void TryDeleteTree(string path)
    {
        try { if (Directory.Exists(path)) { Directory.Delete(path, true); } } catch (Exception) { }
    }

    private static void WriteUpdateResult(string dir, bool ok, string version, string message, int files)
    {
        try
        {
            Directory.CreateDirectory(dir);
            string json = "{\"ok\":" + (ok ? "true" : "false") + ",\"version\":\"" + Escape(version ?? "") + "\",\"files\":" + files
                + ",\"message\":\"" + Escape(message ?? "") + "\",\"time_utc\":\"" + DateTime.UtcNow.ToString("o") + "\"}\n";
            File.WriteAllText(Path.Combine(dir, "last-apply.json"), json, new UTF8Encoding(false));
        }
        catch (Exception) { }
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

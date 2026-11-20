param(
    [switch]$NoBuild,
    [switch]$ExecuteLocalProbe,
    [ValidateSet("maa-postmessage", "direct-postmessage", "webview2-composition")]
    [string]$Transport = "maa-postmessage"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectFile = Join-Path $projectRoot "browser_host\MaaShinyColors.BrowserHost.csproj"
# Debug output avoids rebuilding the Release executable used by a logged-in game window.
$executable = Join-Path $projectRoot "browser_host\bin\Debug\net10.0-windows\MaaShinyColors.BrowserHost.exe"
. (Join-Path $PSScriptRoot "resolve_python.ps1")
$python = (Resolve-ProjectPython -ProjectRoot $projectRoot).Path
$runner = Join-Path $projectRoot "tools\run_browser_input_probe.py"
$runId = [Guid]::NewGuid().ToString("N")
$evidence = Join-Path $projectRoot "dev_assets\private\browser_input_checks\$runId"
$session = Join-Path $evidence "session.json"
$profile = Join-Path $evidence "local-profile"

if (-not $NoBuild) {
    dotnet build $projectFile --configuration Debug
    if ($LASTEXITCODE -ne 0) { throw "BrowserHost build failed." }
}
if (-not $python) { throw "No Python interpreter found. Run scripts/bootstrap.ps1 first." }
foreach ($required in @($executable, $runner)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required file is missing: $required" }
}
New-Item -ItemType Directory -Path $evidence | Out-Null
$arguments = @("--browser-demo", "--user-data-dir", "`"$profile`"", "--bridge-session", "`"$session`"")
if ($Transport -eq "webview2-composition") {
    $arguments += "--composition-demo"
    if ($ExecuteLocalProbe) { $arguments += "--enable-local-input" }
}
$demoProcess = Start-Process -FilePath $executable -ArgumentList $arguments `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
try {
    & $python $runner --session $session --wait-seconds 20 `
        --report (Join-Path $evidence "observe.json")
    if ($LASTEXITCODE -ne 0) { throw "Local browser input probe observation failed." }
    if ($ExecuteLocalProbe) {
        & $python $runner --session $session --execute-local-probe --transport $Transport --wait-seconds 20 `
            --report (Join-Path $evidence "input.json")
        if ($LASTEXITCODE -ne 0) { throw "Local browser background input verification failed." }
    }
    Write-Output "Browser input probe checks passed. Evidence: $evidence"
}
finally {
    # Only terminate the local process created by this invocation.
    if (-not $demoProcess.HasExited) {
        $null = $demoProcess.CloseMainWindow()
        if (-not $demoProcess.WaitForExit(5000)) {
            Stop-Process -Id $demoProcess.Id
            if (-not $demoProcess.WaitForExit(3000)) { throw "Local demo process did not exit." }
        }
    }
    if (Test-Path -LiteralPath $session) {
        $sessionTarget = [System.IO.Path]::GetFullPath($session)
        $evidenceTarget = [System.IO.Path]::GetFullPath($evidence) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $sessionTarget.StartsWith($evidenceTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing session cleanup outside this run's evidence directory."
        }
        Remove-Item -LiteralPath $sessionTarget
    }
    $demoProcess.Dispose()
}

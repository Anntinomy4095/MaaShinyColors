param([switch]$NoBuild)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectFile = Join-Path $projectRoot "browser_host\MaaShinyColors.BrowserHost.csproj"
$executable = Join-Path $projectRoot "browser_host\bin\Release\net10.0-windows\MaaShinyColors.BrowserHost.exe"
. (Join-Path $PSScriptRoot "resolve_python.ps1")
$python = (Resolve-ProjectPython -ProjectRoot $projectRoot).Path
$runner = Join-Path $projectRoot "tools\run_browser_task.py"
$runId = [Guid]::NewGuid().ToString("N")
$evidence = Join-Path $projectRoot "dev_assets\private\browser_demo_checks\$runId"
$session = Join-Path $evidence "session.json"
$profile = Join-Path $projectRoot "dev_assets\private\browser-demo-profile"

if (-not $NoBuild) {
    dotnet build $projectFile --configuration Release
    if ($LASTEXITCODE -ne 0) { throw "BrowserHost build failed." }
}
if (-not (Test-Path -LiteralPath $executable) -or -not $python) {
    throw "BrowserHost or project Python is missing. Run scripts/bootstrap.ps1 first."
}
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$arguments = @("--browser-demo", "--user-data-dir", "`"$profile`"", "--bridge-session", "`"$session`"")
$demoProcess = Start-Process -FilePath $executable -ArgumentList $arguments `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
try {
    & $python $runner --session $session --task capture --frames 20 --wait-seconds 20 `
        --report (Join-Path $evidence "capture.json")
    if ($LASTEXITCODE -ne 0) { throw "CapturePreview/Maa capture verification failed." }
    & $python $runner --session $session --task daily-observe `
        --report (Join-Path $evidence "daily.json")
    if ($LASTEXITCODE -ne 0) { throw "Local daily state recognition failed." }
    & $python $runner --session $session --task produce-sample `
        --report (Join-Path $evidence "produce.json")
    if ($LASTEXITCODE -ne 0) { throw "Produce sample verification failed." }
    Write-Output "Browser demo checks passed. Evidence: $evidence"
}
finally {
    if (-not $demoProcess.HasExited) {
        $null = $demoProcess.CloseMainWindow()
        if (-not $demoProcess.WaitForExit(5000)) {
            & taskkill.exe /PID $demoProcess.Id /T /F | Out-Null
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

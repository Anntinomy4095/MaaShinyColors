$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$projectFile = Join-Path $projectRoot "browser_host\MaaShinyColors.BrowserHost.csproj"
$executable = Join-Path $projectRoot "browser_host\bin\Release\net10.0-windows\MaaShinyColors.BrowserHost.exe"
$userDataFolder = Join-Path $projectRoot "dev_assets\private\offline-webview2-profile"
. (Join-Path $PSScriptRoot "resolve_python.ps1")
$python = (Resolve-ProjectPython -ProjectRoot $projectRoot).Path
$runner = Join-Path $projectRoot "tools\run_offline_click_experiment.py"

dotnet build $projectFile --configuration Release

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Offline BrowserHost executable does not exist: $executable"
}
if (-not $python) {
    throw "No Python interpreter found. Run scripts/bootstrap.ps1 first."
}

$argumentList = @(
    "--offline-demo",
    "--user-data-dir",
    "`"$userDataFolder`""
)

$offlineProcess = Start-Process `
    -FilePath $executable `
    -ArgumentList $argumentList `
    -WorkingDirectory $projectRoot `
    -PassThru

try {
    & $python $runner --wait-seconds 20
    if ($LASTEXITCODE -ne 0) {
        throw "Offline click experiment failed with exit code $LASTEXITCODE."
    }
}
finally {
    if (-not $offlineProcess.HasExited) {
        Stop-Process -Id $offlineProcess.Id
    }
}

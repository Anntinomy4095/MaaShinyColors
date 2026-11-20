param(
    [switch]$NoBuild,
    [switch]$OfflineDemo,
    [switch]$BrowserDemo,
    [string]$BridgeSession,
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$projectFile = Join-Path $projectRoot "browser_host\MaaShinyColors.BrowserHost.csproj"
$executable = Join-Path $projectRoot "browser_host\bin\$Configuration\net10.0-windows\MaaShinyColors.BrowserHost.exe"
$profileName = if ($OfflineDemo) { "offline-webview2-profile" } elseif ($BrowserDemo) { "browser-demo-profile" } else { "webview2-profile" }
$userDataFolder = Join-Path $projectRoot "dev_assets\private\$profileName"

if (-not $NoBuild) {
    dotnet build $projectFile --configuration $Configuration
    if ($LASTEXITCODE -ne 0) { throw "BrowserHost build failed." }
}

if (-not (Test-Path -LiteralPath $executable)) {
    throw "BrowserHost executable does not exist: $executable"
}

$argumentList = @(
    "--user-data-dir",
    "`"$userDataFolder`""
)
if ($OfflineDemo) {
    $argumentList = @("--offline-demo") + $argumentList
}
if ($BrowserDemo) {
    $argumentList = @("--browser-demo") + $argumentList
}
if ($BridgeSession) {
    $sessionPath = [System.IO.Path]::GetFullPath($BridgeSession)
    $argumentList += @("--bridge-session", "`"$sessionPath`"")
}

Start-Process `
    -FilePath $executable `
    -ArgumentList $argumentList `
    -WorkingDirectory $projectRoot

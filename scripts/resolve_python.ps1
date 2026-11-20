# Resolve the Python interpreter for project scripts without hard-coding one path (
# the project uses its own .venv, but check commands must not be pinned to the .venv path).
#
# Order: 1) the active virtual environment ($env:VIRTUAL_ENV), 2) the project .venv, 3) `python` on PATH.
# Dot-source to get Resolve-ProjectPython, or run with -ResolvePrint to write "<source>|<path>" (used by the tests).
# The script parameters have distinctive names: dot-sourcing binds them in the caller's scope, and PowerShell names
# are case-insensitive (a parameter called ProjectRoot would overwrite the caller's $projectRoot).
param(
    [string]$ResolveRoot = "",
    [switch]$ResolvePrint
)

$script:ResolvePythonDefaultRoot = Split-Path -Parent $PSScriptRoot

function Resolve-ProjectPython {
    param([string]$ProjectRoot = "")
    if (-not $ProjectRoot) { $ProjectRoot = $script:ResolvePythonDefaultRoot }
    if ($env:VIRTUAL_ENV) {
        foreach ($relative in @("Scripts\python.exe", "bin\python")) {
            $candidate = Join-Path $env:VIRTUAL_ENV $relative
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return [pscustomobject]@{ Path = $candidate; Source = "active-venv" }
            }
        }
    }
    $projectVenv = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $projectVenv -PathType Leaf) {
        return [pscustomobject]@{ Path = $projectVenv; Source = "project-venv" }
    }
    $command = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return [pscustomobject]@{ Path = $command.Source; Source = "path" }
    }
    return [pscustomobject]@{ Path = $null; Source = "missing" }
}

if ($ResolvePrint) {
    $resolvedPython = Resolve-ProjectPython -ProjectRoot $ResolveRoot
    Write-Output ("{0}|{1}" -f $resolvedPython.Source, $resolvedPython.Path)
}

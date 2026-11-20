$ErrorActionPreference = "Stop"

# Interpreter: active virtualenv ($env:VIRTUAL_ENV) > project .venv > `python` on PATH (scripts/resolve_python.ps1).
# The commands below stay generic (`<python> -m unittest ...`); only the interpreter is resolved here.
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "resolve_python.ps1")
$resolved = Resolve-ProjectPython -ProjectRoot $projectRoot
if (-not $resolved.Path) { throw "No Python interpreter found. Run scripts/bootstrap.ps1 (creates .venv) first." }
$python = $resolved.Path
Write-Output "Python: $python ($($resolved.Source))"
if ($resolved.Source -eq "path") {
    Write-Warning "No active virtualenv and no project .venv; using python from PATH. Run scripts/bootstrap.ps1 first."
}

Push-Location $projectRoot
try {
    # Windows PowerShell 5.1 turns native stderr into a terminating error under "Stop"; probe with "Continue".
    $ErrorActionPreference = "Continue"
    & $python -c "import maa" 2>$null
    $hasMaa = ($LASTEXITCODE -eq 0)
    & $python -c "import jsonschema, referencing" 2>$null
    $hasSchemaDeps = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"
    if (-not $hasMaa) {
        Write-Warning ("=" * 72)
        Write-Warning "maafw (import maa) is NOT importable with $python."
        Write-Warning "The real-Maa Pipeline replay tests will be SKIPPED, not run. Activate the project .venv"
        Write-Warning "or run scripts/bootstrap.ps1 before trusting this result."
        Write-Warning ("=" * 72)
    }

    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
    & $python tools/check_project.py
    if ($LASTEXITCODE -ne 0) { throw "Project contract check failed." }

    if ($hasSchemaDeps) {
        & $python tools/validate_schema.py `
            --schema-dir deps/tools `
            --resource-dirs assets/resource `
            --interface-files assets/interface.json
        if ($LASTEXITCODE -ne 0) { throw "Schema validation failed." }
    }
    else {
        Write-Warning "jsonschema/referencing is unavailable; skipped schema validation."
    }
    if (-not $hasMaa) { Write-Warning "Reminder: maafw was missing; Maa replay tests were skipped." }
}
finally {
    Pop-Location
}

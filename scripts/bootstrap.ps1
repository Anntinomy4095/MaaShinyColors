$ErrorActionPreference = "Stop"

# Creates (once) and fills the project virtual environment .venv. Later commands do not hard-code its path:
# activate it (.venv\Scripts\Activate.ps1) or let scripts/resolve_python.ps1 find it.
$projectRoot = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $projectRoot ".venv"
if (-not (Test-Path -LiteralPath $venv)) {
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Creating .venv failed." }
}
$python = Join-Path $venv "Scripts\python.exe"
Write-Output "Python: $python (project-venv)"

Push-Location $projectRoot
try {
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
    & $python agent/main.py --self-check
}
finally {
    Pop-Location
}

Write-Output "Python Agent environment is ready (MaaFramework binaries come with the maafw wheel). Activate it with .venv\Scripts\Activate.ps1, then: python -m unittest discover -s tests -v (or scripts/check.ps1). Next: OCR model and host build, see README steps 3-5 (running from source)."

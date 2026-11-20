param([switch]$SetupModel)
$ErrorActionPreference = 'Stop'
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot "resolve_python.ps1")
$python = (Resolve-ProjectPython -ProjectRoot $repoRoot).Path
$model = Join-Path $repoRoot 'dev_assets/private/models/ja_ppocrv3_v4'
if (-not $python) { throw 'No Python interpreter found. Run scripts/bootstrap.ps1 first.' }
if (-not (Test-Path -LiteralPath $model)) {
    if ($SetupModel) { & (Join-Path $PSScriptRoot 'setup_ocr_model.ps1') }
    else { throw 'OCR model is missing; use -SetupModel or setup_ocr_model.ps1.' }
}
$evidence = Join-Path $repoRoot ('dev_assets/private/observation_checks/' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$numericManifest = Join-Path $repoRoot 'tests/fixtures/ocr_observation/manifest.json'
$japaneseManifest = Join-Path $repoRoot 'tests/fixtures/ocr_japanese/manifest.json'
$numericReport = Join-Path $evidence 'numeric.json'
$japaneseReport = Join-Path $evidence 'japanese.json'
& $python (Join-Path $repoRoot 'tools/replay_ocr.py') --manifest $numericManifest --model-dir $model --report $numericReport
if ($LASTEXITCODE -ne 0) { throw "Numeric OCR replay failed: $numericReport" }
& $python (Join-Path $repoRoot 'tools/replay_ocr.py') --manifest $japaneseManifest --model-dir $model --report $japaneseReport
if ($LASTEXITCODE -ne 0) { throw "Japanese OCR replay failed: $japaneseReport" }
foreach ($reportPath in @($numericReport, $japaneseReport)) {
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $report.passed -or $report.input_attempts -ne 0) { throw 'OCR replay did not prove read-only success.' }
}
$annotation = Join-Path $repoRoot 'tests/fixtures/ocr_observation/annotation.json'
$positiveImage = Join-Path $repoRoot 'tests/fixtures/ocr_observation/positive.png'
$adviceReport = Join-Path $evidence 'advice.json'
& $python (Join-Path $repoRoot 'tools/observe_produce.py') --ocr-report $numericReport --sample-id positive --annotation $annotation --image $positiveImage --report $adviceReport
if ($LASTEXITCODE -ne 0) { throw "OCR advice failed: $adviceReport" }
$advice = Get-Content -LiteralPath $adviceReport -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $advice.passed -or $advice.input_enabled -or $advice.decision.chosen.id -ne 'rest') { throw 'Unexpected owned-fixture advice.' }
foreach ($sample in @('blank', 'nonnumeric', 'ambiguous', 'hard_negative_arial8')) {
    $image = Join-Path $repoRoot "tests/fixtures/ocr_observation/$sample.png"
    $negativeAnnotation = Get-Content -LiteralPath $annotation -Raw -Encoding UTF8 | ConvertFrom-Json
    $negativeAnnotation.image_sha256 = (Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant()
    $negativeAnnotationPath = Join-Path $evidence "$sample-annotation.json"
    [System.IO.File]::WriteAllText($negativeAnnotationPath, ($negativeAnnotation | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
    $refusalReport = Join-Path $evidence "$sample-refusal.json"
    & $python (Join-Path $repoRoot 'tools/observe_produce.py') --ocr-report $numericReport --sample-id $sample --annotation $negativeAnnotationPath --image $image --report $refusalReport
    if ($LASTEXITCODE -ne 2) { throw "Expected refusal for $sample" }
    $refusal = Get-Content -LiteralPath $refusalReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($refusal.passed -or $null -ne $refusal.decision -or $refusal.error -notlike '*OCR field must be accepted*') { throw "Unexpected refusal path for $sample" }
}
Write-Output "Read-only observation checks passed. Evidence: $evidence"

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$fixtureRoot = $PSScriptRoot
$fieldNames = @('Turn', 'TurnsToExam', 'Stamina', 'MaxStamina', 'Vocal', 'Dance', 'Visual')
$values = @('8', '3', '42', '100', '260', '310', '220')
$points = @(@(60, 100), @(360, 100), @(660, 100), @(960, 100), @(60, 400), @(360, 400), @(660, 400))
$fields = @()
for ($index = 0; $index -lt $fieldNames.Count; $index++) {
    $fields += @{name = "SC.Produce.$($fieldNames[$index])"; roi = @($points[$index][0], $points[$index][1], 240, 150); expected = '^[0-9]+$'; threshold = 0.95}
}
$font = [System.Drawing.Font]::new('Tahoma', 48, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$arialFont = [System.Drawing.Font]::new('Arial', 48, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$labelFont = [System.Drawing.Font]::new('Arial', 18, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$samples = @()
try {
    foreach ($variant in @('positive', 'blank', 'nonnumeric', 'ambiguous', 'hard_negative_arial8')) {
        $bitmap = [System.Drawing.Bitmap]::new(1280, 720, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $expected = @{}
        try {
            $graphics.Clear([System.Drawing.Color]::White)
            $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
            for ($index = 0; $index -lt $fieldNames.Count; $index++) {
                # Calibrated synthetic baseline; not a game-font accuracy benchmark.
                $activeFont = if ($index -eq 0 -and $variant -ne 'hard_negative_arial8') { $font } else { $arialFont }
                $x = $points[$index][0]; $y = $points[$index][1]
                $graphics.DrawString($fieldNames[$index], $labelFont, [System.Drawing.Brushes]::Black, [single]$x, [single]($y - 35))
                $text = $values[$index]
                $expected["SC.Produce.$($fieldNames[$index])"] = $text
                if ($index -eq 0 -and $variant -eq 'hard_negative_arial8') { $expected['SC.Produce.Turn'] = $null }
                if ($variant -eq 'blank') { $expected["SC.Produce.$($fieldNames[$index])"] = $null; continue }
                if ($index -eq 0 -and $variant -eq 'nonnumeric') { $text = 'NO'; $expected['SC.Produce.Turn'] = $null }
                if ($index -eq 0 -and $variant -eq 'ambiguous') {
                    $graphics.DrawString('8', $activeFont, [System.Drawing.Brushes]::Black, [single]$x, [single]$y)
                    $graphics.DrawString('7', $activeFont, [System.Drawing.Brushes]::Black, [single]($x + 120), [single]($y + 85))
                    $expected['SC.Produce.Turn'] = $null
                } else {
                    $graphics.DrawString($text, $activeFont, [System.Drawing.Brushes]::Black, [single]($x + 10), [single]($y + 25))
                }
            }
            $bitmap.Save((Join-Path $fixtureRoot "$variant.png"), [System.Drawing.Imaging.ImageFormat]::Png)
        } finally { $graphics.Dispose(); $bitmap.Dispose() }
        $samples += @{id = $variant; image = "$variant.png"; expected = $expected}
    }
} finally { $font.Dispose(); $arialFont.Dispose(); $labelFont.Dispose() }
$manifest = @{version = 1; canvas = @(1280, 720); fields = $fields; samples = $samples}
$json = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText((Join-Path $fixtureRoot 'manifest.json'), $json, [System.Text.UTF8Encoding]::new($false))
$annotation = @{
    image_sha256 = (Get-FileHash -LiteralPath (Join-Path $fixtureRoot 'positive.png') -Algorithm SHA256).Hash.ToLowerInvariant()
    scene_confirmed = $true
    targets = @{vocal = 500; dance = 500; visual = 500}
    candidates = @(
        @{id = 'visual_lesson'; label = 'Visual lesson'; category = 'lesson'; available = $true; confidence = 1; gains = @{visual = 38}; stamina_cost = 18; risk = 0.08},
        @{id = 'rest'; label = 'Rest'; category = 'recovery'; available = $true; confidence = 1; stamina_recovery = 36}
    )
}
[System.IO.File]::WriteAllText((Join-Path $fixtureRoot 'annotation.json'), ($annotation | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
Write-Output 'Generated owned offline OCR observation fixtures.'

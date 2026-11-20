$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$fixtureRoot = $PSScriptRoot
$claimableText = -join ([char[]]@(0x53D7, 0x3051, 0x53D6, 0x308B))
$completedText = -join ([char[]]@(0x53D7, 0x3051, 0x53D6, 0x308A, 0x6E08, 0x307F))
$similarText = -join ([char[]]@(0x53D7, 0x3051, 0x53D6, 0x308A, 0x307E, 0x3059))
$font = [System.Drawing.Font]::new('Tahoma', 48, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$yuFont = [System.Drawing.Font]::new('Yu Gothic', 48, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$fields = @(
    @{name = 'SC.Daily.ClaimableText'; roi = @(300, 240, 680, 200); expected = ('^' + $claimableText + '$'); threshold = 0.95},
    @{name = 'SC.Daily.CompletedText'; roi = @(300, 240, 680, 200); expected = ('^' + $completedText + '$'); threshold = 0.95}
)
$samples = @()
try {
    foreach ($variant in @('claimable', 'completed', 'blank', 'similar', 'ambiguous', 'hard_negative_yugothic')) {
        $bitmap = [System.Drawing.Bitmap]::new(1280, 720, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([System.Drawing.Color]::White)
            $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
            $expected = @{'SC.Daily.ClaimableText' = $null; 'SC.Daily.CompletedText' = $null}
            switch ($variant) {
                'claimable' { $graphics.DrawString($claimableText, $font, [System.Drawing.Brushes]::Black, 320, 265); $expected['SC.Daily.ClaimableText'] = $claimableText }
                'completed' { $graphics.DrawString($completedText, $font, [System.Drawing.Brushes]::Black, 320, 265); $expected['SC.Daily.CompletedText'] = $completedText }
                'similar' { $graphics.DrawString($similarText, $font, [System.Drawing.Brushes]::Black, 320, 265) }
                'hard_negative_yugothic' { $graphics.DrawString($completedText, $yuFont, [System.Drawing.Brushes]::Black, 320, 265) }
                'ambiguous' {
                    $graphics.DrawString($claimableText, $font, [System.Drawing.Brushes]::Black, 320, 250)
                    $graphics.DrawString($completedText, $font, [System.Drawing.Brushes]::Black, 320, 345)
                }
            }
            $bitmap.Save((Join-Path $fixtureRoot "$variant.png"), [System.Drawing.Imaging.ImageFormat]::Png)
            $samples += @{id = $variant; image = "$variant.png"; expected = $expected}
        } finally { $graphics.Dispose(); $bitmap.Dispose() }
    }
} finally { $font.Dispose(); $yuFont.Dispose() }
[System.IO.File]::WriteAllText((Join-Path $fixtureRoot 'manifest.json'), (@{version = 1; canvas = @(1280, 720); fields = $fields; samples = $samples} | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
Write-Output 'Generated owned Japanese OCR samples; these are not game assets.'

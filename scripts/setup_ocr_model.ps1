$ErrorActionPreference = 'Stop'
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
# Use only Python's standard library; no dependencies, credentials or proxy overrides.
python (Join-Path $repoRoot 'tools/fetch_ocr_model.py')
if ($LASTEXITCODE -ne 0) { throw 'OCR 模型下载失败；未完成文件已清理。' }

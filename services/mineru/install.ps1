$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$backendPython = Join-Path $projectRoot 'venv/Scripts/python.exe'
$mineruVenv = Join-Path $projectRoot '.tmp/mineru-venv'
$cacheDir = Join-Path $projectRoot '.tmp/uv-cache'

if (-not (Test-Path -LiteralPath $backendPython)) {
    throw 'Create the BE venv first; its Python is used to create the MinerU environment.'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv before running this script.'
}
if (-not (Test-Path -LiteralPath (Join-Path $mineruVenv 'Scripts/python.exe'))) {
    & uv --cache-dir $cacheDir venv --python $backendPython $mineruVenv
    if ($LASTEXITCODE -ne 0) { throw 'MinerU virtual environment creation failed.' }
}
& uv --cache-dir $cacheDir pip install --python (Join-Path $mineruVenv 'Scripts/python.exe') -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'MinerU dependency installation failed.' }

Write-Host 'MinerU installed in .tmp/mineru-venv. Run services/mineru/start.ps1 next.'

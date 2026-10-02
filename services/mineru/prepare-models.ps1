param(
    [ValidateSet('basic', 'standard')]
    [string]$Tier = 'standard',
    [ValidateSet('auto', 'huggingface', 'modelscope')]
    [string]$Source = 'huggingface'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$mineruKit = Join-Path $projectRoot '.tmp/mineru-venv/Scripts/mineru-kit.exe'
$runtimeDir = Join-Path $projectRoot '.tmp/mineru-runtime'
if (-not (Test-Path -LiteralPath $mineruKit)) {
    throw 'Run services/mineru/install.ps1 first.'
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$previousMineruHome = $env:MINERU_HOME
$previousHfHome = $env:HF_HOME
$previousSmallBackend = $env:MINERU_MODEL_SMALL_BACKEND
try {
    $env:MINERU_HOME = Join-Path $runtimeDir 'state'
    $env:HF_HOME = Join-Path $runtimeDir 'huggingface'
    $env:MINERU_MODEL_SMALL_BACKEND = 'onnx'
    & $mineruKit models download --tier $Tier --small-backend onnx --source $Source
    if ($LASTEXITCODE -ne 0) { throw 'MinerU model download failed; retry the same command to reuse cached files.' }
    & $mineruKit models verify --tier $Tier --small-backend onnx
    if ($LASTEXITCODE -ne 0) { throw 'MinerU model verification failed.' }
} finally {
    $env:MINERU_HOME = $previousMineruHome
    $env:HF_HOME = $previousHfHome
    $env:MINERU_MODEL_SMALL_BACKEND = $previousSmallBackend
}

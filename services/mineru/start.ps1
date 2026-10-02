param(
    [ValidateSet('basic', 'standard')]
    [string]$Tier = 'standard',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8001
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
$previousModelSource = $env:MINERU_MODEL_SOURCE
try {
    $env:MINERU_HOME = Join-Path $runtimeDir 'state'
    $env:HF_HOME = Join-Path $runtimeDir 'huggingface'
    $env:MINERU_MODEL_SMALL_BACKEND = 'onnx'
    $env:MINERU_MODEL_SOURCE = 'local'
    & $mineruKit models verify --tier $Tier --small-backend onnx
    if ($LASTEXITCODE -ne 0) { throw 'Prepare the models with services/mineru/prepare-models.ps1 first.' }
    & $mineruKit api-server --host 127.0.0.1 --port $Port --tier $Tier --concurrency 1 --upload-dir (Join-Path $runtimeDir 'uploads')
    if ($LASTEXITCODE -ne 0) { throw 'MinerU API server exited with an error.' }
} finally {
    $env:MINERU_HOME = $previousMineruHome
    $env:HF_HOME = $previousHfHome
    $env:MINERU_MODEL_SMALL_BACKEND = $previousSmallBackend
    $env:MINERU_MODEL_SOURCE = $previousModelSource
}

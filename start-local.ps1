# Run from the SAME PowerShell terminal where you will start Uvicorn.
# Does not modify .env, credentials, Brevo, or existing Docker volumes.
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Stop,
    [switch]$Pull,
    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 180
)

$script:LocalProjectRoot = $PSScriptRoot
$script:LocalComposeProject = 'resume-screening-local'

function ConvertTo-LocalNativeArgument {
    param([AllowEmptyString()][string]$Value)
    # Windows CommandLineToArgvW quoting, including embedded quotes/backslashes.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-LocalDocker {
    param(
        [string[]]$Arguments,
        [ValidateRange(1, 3600)]
        [int]$LimitSeconds = 30
    )
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $script:LocalDockerExe
    $info.Arguments = ($Arguments | ForEach-Object { ConvertTo-LocalNativeArgument $_ }) -join ' '
    $info.WorkingDirectory = $script:LocalProjectRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    # Docker emits UTF-8 JSON, independently of the Windows console code page.
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    $started = $false
    $stdoutReader = $null
    $stderrReader = $null
    try {
        [void]$process.Start()
        $started = $true
        $stdoutReader = $process.StandardOutput
        $stderrReader = $process.StandardError
        $stdout = $stdoutReader.ReadToEndAsync()
        $stderr = $stderrReader.ReadToEndAsync()
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $nextUpdate = 15
        # A child can retain the redirected pipes after its parent exits.
        # Bound the entire operation, not just the parent process lifetime.
        while (-not ($process.HasExited -and $stdout.IsCompleted -and $stderr.IsCompleted)) {
            if ($timer.Elapsed.TotalSeconds -ge $LimitSeconds) {
                # Stop only this CLI call; never kill Docker Desktop/the engine.
                if (-not $process.HasExited) {
                    try { $process.Kill() }
                    catch [System.InvalidOperationException] { } # Exited during the check.
                }
                return [pscustomobject]@{ ExitCode = 124; Output = '' }
            }
            if ($timer.Elapsed.TotalSeconds -ge $nextUpdate) {
                Write-Host 'Docker is still working (first-time image downloads can take a while)...'
                $nextUpdate += 15
            }
            Start-Sleep -Milliseconds 50
        }
        # Never print Docker's resolved config or stderr: either can contain secrets.
        # Both tasks are complete here, so GetResult cannot wait on an open pipe.
        $output = $stdout.GetAwaiter().GetResult()
        [void]$stderr.GetAwaiter().GetResult()
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
    } finally {
        if ($started -and -not $process.HasExited) {
            try { $process.Kill() }
            catch [System.InvalidOperationException] { }
        }
        # We own the readers; close pending reads without awaiting inherited EOF.
        if ($stdoutReader) { $stdoutReader.Dispose() }
        if ($stderrReader) { $stderrReader.Dispose() }
        $process.Dispose()
    }
}

function Stop-LocalStack {
    # Do not read Compose/.env: stopping must also work after credentials or
    # configuration have been removed/broken. Resolve only our four services
    # by exact project/service labels; never stop every container on the engine.
    $containerIds = @()
    foreach ($service in @('mongo', 'redis', 'mongo-key', 'mongo-init-replica')) {
        $result = Invoke-LocalDocker -Arguments @(
            'container', 'ls', '--all', '--quiet', '--no-trunc',
            '--filter', "label=com.docker.compose.project=$script:LocalComposeProject",
            '--filter', "label=com.docker.compose.service=$service",
            '--filter', 'label=com.docker.compose.oneoff=False'
        ) -LimitSeconds 15
        if ($result.ExitCode -ne 0) {
            throw 'Could not list local containers. Check Docker Desktop; no stop command was issued.'
        }
        foreach ($line in ($result.Output -split '\r?\n')) {
            $id = $line.Trim()
            if (-not $id) { continue }
            if ($id -notmatch '^[0-9a-f]{64}$') {
                throw 'Docker returned an invalid container ID. No stop command was issued.'
            }
            $containerIds += $id
        }
    }
    $containerIds = @($containerIds | Select-Object -Unique)
    if ($containerIds.Count -eq 0) {
        Write-Host 'No local Mongo/Redis containers found. Nothing to stop.'
        return
    }
    $result = Invoke-LocalDocker -Arguments (@('container', 'stop', '--time', '30') + $containerIds) -LimitSeconds 90
    if ($result.ExitCode -ne 0) { throw 'Could not stop all local containers. Check Docker Desktop and container status.' }
    Write-Host 'Local Mongo/Redis stopped. Volumes and .env were retained.'
}

function Get-LocalStackConfiguration {
    $result = Invoke-LocalDocker -Arguments ($script:LocalComposeArgs + @('config', '--format', 'json'))
    if ($result.ExitCode -ne 0) {
        throw 'Compose configuration failed. Check .env credentials and run: docker compose -f docker-compose.local.yml config --quiet'
    }
    try { $config = $result.Output | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Docker Compose returned invalid JSON. Docker Compose v2 is required.' }

    $mongoEnv = $config.services.mongo.environment
    foreach ($key in @('MONGO_INITDB_ROOT_USERNAME', 'MONGO_INITDB_ROOT_PASSWORD', 'MONGO_APP_USERNAME', 'MONGO_APP_PASSWORD', 'MONGO_APP_DATABASE')) {
        if ([string]::IsNullOrWhiteSpace([string]$mongoEnv.$key)) {
            throw "Missing Mongo setting: $key. Check its source variable in .env."
        }
    }
    $database = [string]$mongoEnv.MONGO_APP_DATABASE
    if ($database -notmatch '^[a-zA-Z0-9_-]+$') {
        throw 'For this local launcher, MONGO_APP_DATABASE must contain only letters, numbers, underscores or hyphens.'
    }
    $redisCommand = @($config.services.redis.command)
    $passwordIndex = [array]::IndexOf($redisCommand, '--requirepass')
    if ($passwordIndex -lt 0 -or $passwordIndex + 1 -ge $redisCommand.Count -or
        [string]::IsNullOrEmpty([string]$redisCommand[$passwordIndex + 1])) {
        throw 'Redis password was not configured. Set REDIS_PASSWORD in .env.'
    }

    $ports = @{}
    foreach ($entry in @(@('mongo', 27017), @('redis', 6379))) {
        $service = $entry[0]
        $bindings = @($config.services.$service.ports | Where-Object { $_.target -eq $entry[1] })
        if ($bindings.Count -ne 1 -or $bindings[0].host_ip -ne '127.0.0.1' -or
            [int]$bindings[0].published -lt 1 -or [int]$bindings[0].published -gt 65535) {
            throw "Expected one loopback-only published port for $service in docker-compose.local.yml."
        }
        $ports[$service] = [int]$bindings[0].published
    }
    $appUser = [uri]::EscapeDataString([string]$mongoEnv.MONGO_APP_USERNAME)
    $appPassword = [uri]::EscapeDataString([string]$mongoEnv.MONGO_APP_PASSWORD)
    $redisPassword = [uri]::EscapeDataString([string]$redisCommand[$passwordIndex + 1])
    return [pscustomobject]@{
        Database = $database
        MongoPort = $ports.mongo
        RedisPort = $ports.redis
        MongoUri = "mongodb://${appUser}:${appPassword}@127.0.0.1:$($ports.mongo)/${database}?authSource=${database}&replicaSet=rs0&directConnection=true"
        RedisUri = "redis://:${redisPassword}@127.0.0.1:$($ports.redis)/0"
    }
}

function Wait-LocalDocker {
    param([int]$LimitSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($LimitSeconds)
    $desktopChecked = $false
    do {
        $result = Invoke-LocalDocker -Arguments @('info', '--format', '{{.OSType}}') -LimitSeconds 5
        if ($result.ExitCode -eq 0) {
            if ($result.Output.Trim() -ne 'linux') { throw 'Switch Docker Desktop to Linux containers first.' }
            return
        }
        if (-not $desktopChecked) {
            $desktopChecked = $true
            if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
                $binDir = Split-Path -Parent $script:LocalDockerExe
                $desktop = [IO.Path]::GetFullPath((Join-Path $binDir '../../Docker Desktop.exe'))
                if (-not (Test-Path -LiteralPath $desktop)) {
                    throw 'Docker Engine is unavailable. Open Docker Desktop manually, then run this script again.'
                }
                Write-Host 'Starting Docker Desktop...'
                Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
            } else {
                Write-Host 'Waiting for Docker Desktop / Linux Engine...'
            }
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Docker Engine did not become ready. Restart Docker Desktop and retry; do not reset/delete its data.'
}

function ConvertFrom-LocalComposeStatus {
    param([string]$Json)
    if ([string]::IsNullOrWhiteSpace($Json)) { return @() }
    try { return @($Json | ConvertFrom-Json -ErrorAction Stop) }
    catch {
        # Compose v2 may emit newline-delimited objects instead of one JSON array.
        try { return @($Json -split '\r?\n' | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop }) }
        catch { throw 'Could not read container status. Update Docker Compose v2 and retry.' }
    }
}

function Test-LocalStackReady {
    param([object[]]$Containers)
    foreach ($service in @('mongo', 'redis', 'mongo-key', 'mongo-init-replica')) {
        $matches = @($Containers | Where-Object { $_.Service -eq $service })
        if ($matches.Count -ne 1) { return $false }
        $item = $matches[0]
        if ($item.State -eq 'exited' -and $null -eq $item.ExitCode) {
            throw 'Compose did not expose container exit codes. Update Docker Compose v2.'
        }
        if ($item.State -eq 'exited' -and [int]$item.ExitCode -ne 0) {
            throw "Service $service failed. Inspect it with: docker compose -f docker-compose.local.yml logs --tail 80 $service"
        }
        if ($service -in @('mongo-key', 'mongo-init-replica')) {
            if ($item.State -ne 'exited') { return $false }
        } elseif ($item.State -ne 'running' -or $item.Health -ne 'healthy') {
            return $false
        }
    }
    return $true
}

function Wait-LocalStack {
    param([int]$LimitSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($LimitSeconds)
    do {
        $result = Invoke-LocalDocker -Arguments ($script:LocalComposeArgs + @('ps', '--all', '--format', 'json')) -LimitSeconds 10
        if ($result.ExitCode -ne 0) { throw 'Docker could not read container status. Check Docker Desktop / Linux Engine.' }
        if (Test-LocalStackReady -Containers @(ConvertFrom-LocalComposeStatus $result.Output)) { return }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Mongo/Redis did not become ready. Run docker compose -f docker-compose.local.yml ps -a and inspect service logs. Existing volumes were retained.'
}

function Set-LocalApiEnvironment {
    param($Configuration)
    # Process-level variables survive this script and are inherited by Uvicorn
    # started in this same terminal. No .env write, no credentials printed.
    $env:MONGODB_URI = $Configuration.MongoUri
    $env:MONGODB_DB_NAME = $Configuration.Database
    $env:REDIS_URL = $Configuration.RedisUri
    $env:ENVIRONMENT = 'development'
    $env:DEBUG = 'true'
    $env:REFRESH_COOKIE_SECURE = 'false'
}

function Start-LocalServices {
    param([switch]$Check, [switch]$Stop, [switch]$Pull, [int]$TimeoutSeconds = 180)
    $ErrorActionPreference = 'Stop'
    if ($Check -and $Stop) { throw 'Use either -Check or -Stop, not both.' }
    $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $docker) { throw 'Install Docker Desktop (including Compose v2) and reopen PowerShell first.' }
    $script:LocalDockerExe = $docker.Source
    if ($Stop) {
        Stop-LocalStack
        return
    }
    $envFile = Join-Path $script:LocalProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw 'Create .env and configure Mongo/Redis credentials first. This script never overwrites .env.'
    }
    $script:LocalComposeArgs = @('compose', '--project-name', $script:LocalComposeProject, '--project-directory', $script:LocalProjectRoot,
        '--env-file', $envFile, '-f', (Join-Path $script:LocalProjectRoot 'docker-compose.local.yml'))
    $configuration = Get-LocalStackConfiguration
    Write-Host "Local database: $($configuration.Database); Mongo port: $($configuration.MongoPort); Redis port: $($configuration.RedisPort)"
    if ($Check) {
        Write-Host 'Configuration OK. No containers started; no files or session settings changed.'
        return
    }
    Wait-LocalDocker -LimitSeconds $TimeoutSeconds
    if ($Pull) {
        Write-Host 'Pulling official MongoDB/Redis images...'
        $result = Invoke-LocalDocker -Arguments ($script:LocalComposeArgs + @('pull')) -LimitSeconds 900
        if ($result.ExitCode -ne 0) { throw 'Image pull failed or timed out. Check network/Docker Desktop and retry.' }
    }
    Write-Host 'Starting MongoDB/Redis (missing images will be downloaded automatically)...'
    $result = Invoke-LocalDocker -Arguments ($script:LocalComposeArgs + @('up', '-d', 'mongo', 'redis', 'mongo-init-replica')) -LimitSeconds 900
    if ($result.ExitCode -ne 0) {
        throw 'Compose startup failed or timed out. Check Docker Desktop, port conflicts, and service logs. No volumes were deleted.'
    }
    Write-Host 'Waiting for healthy Mongo/Redis and successful replica-set initialization...'
    Wait-LocalStack -LimitSeconds $TimeoutSeconds

    # Healthchecks use the root account. Also verify the application's account,
    # since editing .env does not change users in an already initialized volume.
    $probe = "const d = db.getSiblingDB(process.env.MONGO_APP_DATABASE); if (!d.auth(process.env.MONGO_APP_USERNAME, process.env.MONGO_APP_PASSWORD)) quit(1); d.getCollectionNames(); if (!db.adminCommand({hello:1}).isWritablePrimary) quit(2);"
    $result = Invoke-LocalDocker -Arguments ($script:LocalComposeArgs + @('exec', '-T', 'mongo', 'mongosh', '--quiet', '--norc', '--eval', $probe)) -LimitSeconds 15
    if ($result.ExitCode -ne 0) {
        throw 'Application Mongo authentication/readiness failed. Check MONGO_APP_* against the existing database; do not delete volumes to reset credentials.'
    }
    Set-LocalApiEnvironment -Configuration $configuration
    Write-Host 'READY: Mongo/Redis and rs0 are available. .env and all Brevo settings are unchanged.'
    Write-Host 'Run Uvicorn in THIS PowerShell terminal (with your backend venv activated):'
    Write-Host '  uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload'
    Write-Host "Run it from: $script:LocalProjectRoot"
    Write-Host 'API, MinerU and workers were NOT started. Run this script again in any new terminal before starting Uvicorn.'
}

# Dot-sourcing only loads functions, allowing isolated tests without Docker.
if ($MyInvocation.InvocationName -ne '.') {
    Start-LocalServices -Check:$Check -Stop:$Stop -Pull:$Pull -TimeoutSeconds $TimeoutSeconds
}

# Dependency-free PowerShell tests. No Docker, network, .env edits or deletions.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../start-local.ps1')

function Assert-Local {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

Assert-Local ((ConvertTo-LocalNativeArgument 'C:\a b\') -eq '"C:\a b\\"') 'Trailing slash quoting failed'
Assert-Local ((ConvertTo-LocalNativeArgument 'a"b') -eq '"a\"b"') 'Embedded quote escaping failed'
Assert-Local ((ConvertTo-LocalNativeArgument '') -eq '""') 'Empty argument escaping failed'

# Exercise the real native-process runner with a harmless PowerShell child.
$script:LocalDockerExe = (Get-Process -Id $PID).Path
$native = Invoke-LocalDocker -Arguments @('-NoProfile', '-NonInteractive', '-Command', "Write-Output 'native-ok'; exit 7") -LimitSeconds 10
Assert-Local ($native.ExitCode -eq 7 -and $native.Output.Trim() -eq 'native-ok') 'Native exit code/output capture failed'
$native = Invoke-LocalDocker -Arguments @('-NoProfile', '-NonInteractive', '-Command', 'Start-Sleep -Seconds 10') -LimitSeconds 1
Assert-Local ($native.ExitCode -eq 124) 'Native timeout was not enforced'

# Docker JSON stays UTF-8 even when Windows PowerShell uses an OEM code page.
$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
    $childCode = '[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); [Console]::Write([char]0x0111)'
    $encodedCode = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
    $native = Invoke-LocalDocker -Arguments @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encodedCode) -LimitSeconds 10
    Assert-Local ($native.ExitCode -eq 0 -and $native.Output -ceq ([string][char]0x0111)) 'UTF-8 was decoded using the Windows console code page'
} finally { [Console]::OutputEncoding = $previousEncoding }

# Parent exits immediately; a harmless child retains stdout OR stderr for 6s.
# The runner must return 124 near its 2s deadline, not wait for inherited EOF.
foreach ($heldPipe in @('stdout', 'stderr')) {
    $childCode = @'
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = (Get-Process -Id $PID).Path
$info.Arguments = '-NoProfile -NonInteractive -Command "Start-Sleep -Seconds 6"'
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardInput = $true
__REDIRECT_UNUSED_PIPE__
$child = [System.Diagnostics.Process]::Start($info)
$child.StandardInput.Close()
'@
    $redirect = if ($heldPipe -eq 'stdout') { '$info.RedirectStandardError = $true' } else { '$info.RedirectStandardOutput = $true' }
    $childCode = $childCode.Replace('__REDIRECT_UNUSED_PIPE__', $redirect)
    $encodedCode = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $native = Invoke-LocalDocker -Arguments @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encodedCode) -LimitSeconds 2
    $timer.Stop()
    Assert-Local ($native.ExitCode -eq 124) "Inherited $heldPipe did not time out"
    Assert-Local ($timer.Elapsed.TotalSeconds -lt 4) "Inherited $heldPipe exceeded the timeout budget"
}

$script:Fixture = @{
    services = @{
        mongo = @{
            environment = @{
                MONGO_INITDB_ROOT_USERNAME = 'test_root'
                MONGO_INITDB_ROOT_PASSWORD = 'test_root_password'
                MONGO_APP_USERNAME = 'app@example'
                MONGO_APP_PASSWORD = 'test:p@ss/word#%$'
                MONGO_APP_DATABASE = 'test_local'
            }
            ports = @(@{ target = 27017; published = '27019'; host_ip = '127.0.0.1' })
        }
        redis = @{
            command = @('redis-server', '--appendonly', 'yes', '--requirepass', 'test:r@dis/#%$')
            ports = @(@{ target = 6379; published = '6381'; host_ip = '127.0.0.1' })
        }
    }
}
function Invoke-LocalDocker {
    param([string[]]$Arguments, [int]$LimitSeconds)
    return [pscustomobject]@{ ExitCode = 0; Output = ($script:Fixture | ConvertTo-Json -Depth 8) }
}
$script:LocalComposeArgs = @('compose')
$configuration = Get-LocalStackConfiguration
Assert-Local ($configuration.MongoUri -eq 'mongodb://app%40example:test%3Ap%40ss%2Fword%23%25%24@127.0.0.1:27019/test_local?authSource=test_local&replicaSet=rs0&directConnection=true') 'Mongo URI encoding/configuration failed'
Assert-Local ($configuration.RedisUri -eq 'redis://:test%3Ar%40dis%2F%23%25%24@127.0.0.1:6381/0') 'Redis URI encoding failed'

$script:Fixture.services.mongo.ports[0].host_ip = '0.0.0.0'
$rejected = $false
try { $null = Get-LocalStackConfiguration } catch { $rejected = $true }
Assert-Local $rejected 'Public database binding was accepted'
$script:Fixture.services.mongo.ports[0].host_ip = '127.0.0.1'
$script:Fixture.services.mongo.environment.MONGO_APP_PASSWORD = ''
$rejected = $false
try { $null = Get-LocalStackConfiguration } catch { $rejected = $true }
Assert-Local $rejected 'Missing app password was accepted'
$script:Fixture.services.mongo.environment.MONGO_APP_PASSWORD = 'test:p@ss/word#%$'

$items = @(
    [pscustomobject]@{ Service = 'mongo'; State = 'running'; Health = 'healthy'; ExitCode = 0 },
    [pscustomobject]@{ Service = 'redis'; State = 'running'; Health = 'healthy'; ExitCode = 0 },
    [pscustomobject]@{ Service = 'mongo-key'; State = 'exited'; ExitCode = 0 },
    [pscustomobject]@{ Service = 'mongo-init-replica'; State = 'exited'; ExitCode = 0 }
)
Assert-Local (Test-LocalStackReady $items) 'Healthy stack was rejected'
$array = ConvertFrom-LocalComposeStatus ($items | ConvertTo-Json)
$ndjson = ConvertFrom-LocalComposeStatus (($items | ForEach-Object { $_ | ConvertTo-Json -Compress }) -join "`n")
Assert-Local (@($array).Count -eq 4 -and @($ndjson).Count -eq 4) 'Compose status formats not supported'
$items[3].State = 'running'
Assert-Local (-not (Test-LocalStackReady $items)) 'Running initializer was incorrectly marked ready'
$items[3].State = 'exited'
$items[3].ExitCode = 1
$rejected = $false
try { $null = Test-LocalStackReady $items } catch { $rejected = $true }
Assert-Local $rejected 'Failed initializer was accepted'
$items[3].ExitCode = 0
$items[0].Health = 'starting'
Assert-Local (-not (Test-LocalStackReady $items)) 'Unhealthy Mongo was accepted'
Assert-Local (-not (Test-LocalStackReady @())) 'Empty stack was accepted'

# Save and restore only variables explicitly exercised by this test.
$names = @('MONGODB_URI', 'MONGODB_DB_NAME', 'REDIS_URL', 'ENVIRONMENT', 'DEBUG', 'REFRESH_COOKIE_SECURE',
    'BREVO_API_KEY', 'BREVO_SENDER_EMAIL', 'BREVO_SENDER_NAME')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:BREVO_API_KEY = 'test-preserve-key'
    $env:BREVO_SENDER_EMAIL = 'preserve@example.com'
    $env:BREVO_SENDER_NAME = 'Preserve Sender'
    Set-LocalApiEnvironment $configuration
    Assert-Local ($env:MONGODB_URI -eq $configuration.MongoUri) 'Mongo session settings missing'
    Assert-Local ($env:REDIS_URL -eq $configuration.RedisUri) 'Redis session settings missing'
    Assert-Local ($env:MONGODB_DB_NAME -eq 'test_local' -and $env:DEBUG -eq 'true') 'Database/debug mismatch'
    Assert-Local ($env:BREVO_API_KEY -eq 'test-preserve-key' -and $env:BREVO_SENDER_EMAIL -eq 'preserve@example.com' -and $env:BREVO_SENDER_NAME -eq 'Preserve Sender') 'Brevo settings were modified'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}

# Test command orchestration with fakes, without Docker or a real .env.
function Get-Command {
    [CmdletBinding()]
    param([string]$Name)
    return [pscustomobject]@{ Source = 'mock-docker.exe' }
}
function Test-Path {
    param([string]$LiteralPath, [string]$PathType)
    $script:EnvCheckCount++
    return $script:EnvExists
}
function Wait-LocalDocker { param([int]$LimitSeconds); $script:Calls.Add('engine-ready') }
function Wait-LocalStack { param([int]$LimitSeconds); $script:Calls.Add('stack-ready') }
function Set-LocalApiEnvironment { param($Configuration); $script:Calls.Add('set-api-env') }
function Invoke-LocalDocker {
    param([string[]]$Arguments, [int]$LimitSeconds)
    if ($Arguments -contains 'config') {
        $script:ConfigReadCount++
        return [pscustomobject]@{ ExitCode = 0; Output = ($script:Fixture | ConvertTo-Json -Depth 8) }
    }
    if ($Arguments[0] -eq 'container' -and $Arguments[1] -eq 'ls') {
        Assert-Local ($Arguments -contains 'label=com.docker.compose.project=resume-screening-local') 'Stop query escaped the local project'
        Assert-Local ($Arguments -contains 'label=com.docker.compose.oneoff=False') 'Stop query included one-off containers'
        Assert-Local ($Arguments -contains '--no-trunc') 'Stop query did not request full container IDs'
        $serviceFilter = @($Arguments | Where-Object { $_ -like 'label=com.docker.compose.service=*' })
        Assert-Local ($serviceFilter.Count -eq 1) 'Stop query lacks an exact service label'
        $service = $serviceFilter[0].Substring('label=com.docker.compose.service='.Length)
        Assert-Local ($service -in @('mongo', 'redis', 'mongo-key', 'mongo-init-replica')) 'Stop query included an unrelated service'
        $script:Calls.Add("ls:$service")
        return [pscustomobject]@{ ExitCode = $(if ($script:FailList) { 1 } else { 0 }); Output = [string]$script:StopIds[$service] }
    }
    $operation = @('pull', 'up', 'exec', 'stop') | Where-Object { $Arguments -contains $_ }
    $script:Calls.Add([string]$operation)
    if ($operation -eq 'up') {
        Assert-Local (-not ($Arguments -contains '--remove-orphans')) 'Destructive startup option found'
        Assert-Local (($Arguments -contains 'mongo') -and ($Arguments -contains 'redis') -and -not ($Arguments -contains 'api')) 'Unexpected services started'
    }
    if ($operation -eq 'stop') {
        Assert-Local ($Arguments[0] -eq 'container' -and $Arguments[1] -eq 'stop') 'Stop still depends on Compose configuration'
        $expectedIds = @($script:StopIds.Values | Where-Object { $_ } | Select-Object -Unique)
        Assert-Local ($Arguments.Count -eq (4 + $expectedIds.Count)) 'Stop command has extra targets/options'
        foreach ($id in $Arguments[4..($Arguments.Count - 1)]) {
            Assert-Local ($id -in $expectedIds) 'Stop command targeted an unselected container'
        }
    }
    return [pscustomobject]@{ ExitCode = $(if (($operation -eq 'exec' -and $script:FailAuth) -or ($operation -eq 'stop' -and $script:FailStop)) { 1 } else { 0 }); Output = '' }
}
$script:EnvExists = $true
$script:EnvCheckCount = 0
$script:ConfigReadCount = 0
$script:FailAuth = $false
$script:FailList = $false
$script:FailStop = $false
$script:StopIds = @{ mongo = ('a' * 64); redis = ('b' * 64); 'mongo-key' = ''; 'mongo-init-replica' = '' }
$script:Calls = New-Object 'System.Collections.Generic.List[string]'
Start-LocalServices -Check
Assert-Local ($script:Calls.Count -eq 0) '-Check started services or changed the session'
Start-LocalServices -Pull
Assert-Local (($script:Calls -join ',') -eq 'engine-ready,pull,up,stack-ready,exec,set-api-env') 'Startup/readiness order is incorrect'
$script:Calls.Clear()
$configReadsBeforeStop = $script:ConfigReadCount
$envChecksBeforeStop = $script:EnvCheckCount
Start-LocalServices -Stop
Assert-Local (($script:Calls -join ',') -eq 'ls:mongo,ls:redis,ls:mongo-key,ls:mongo-init-replica,stop') '-Stop did not resolve the exact targets first'
Assert-Local ($script:ConfigReadCount -eq $configReadsBeforeStop -and $script:EnvCheckCount -eq $envChecksBeforeStop) '-Stop read .env/Compose configuration'

# A missing file or invalid current credentials must not prevent stopping.
foreach ($envExists in @($false, $true)) {
    $script:Calls.Clear()
    $script:EnvExists = $envExists
    $script:Fixture.services.mongo.environment.MONGO_APP_PASSWORD = ''
    Start-LocalServices -Stop
    Assert-Local ($script:Calls -contains 'stop') '-Stop was blocked by missing/invalid .env'
    Assert-Local ($script:ConfigReadCount -eq $configReadsBeforeStop -and $script:EnvCheckCount -eq $envChecksBeforeStop) '-Stop validated missing/invalid .env'
}
$script:EnvExists = $true
$script:Fixture.services.mongo.environment.MONGO_APP_PASSWORD = 'test:p@ss/word#%$'

$script:Calls.Clear()
$script:StopIds = @{}
Start-LocalServices -Stop
Assert-Local (-not ($script:Calls -contains 'stop')) 'Empty stack issued an unscoped stop command'

foreach ($failure in @('listing', 'invalid-id', 'stop')) {
    $script:Calls.Clear()
    $script:StopIds = @{ mongo = ('a' * 64) }
    $script:FailList = $failure -eq 'listing'
    $script:FailStop = $failure -eq 'stop'
    if ($failure -eq 'invalid-id') { $script:StopIds.mongo = '--all' }
    $rejected = $false
    try { Start-LocalServices -Stop } catch { $rejected = $true }
    Assert-Local $rejected "Stop $failure failure was ignored"
    if ($failure -ne 'stop') { Assert-Local (-not ($script:Calls -contains 'stop')) 'Stop executed before successful target validation' }
}
$script:FailList = $false
$script:FailStop = $false
$script:Calls.Clear()
$script:FailAuth = $true
$rejected = $false
try { Start-LocalServices } catch { $rejected = $true }
Assert-Local ($rejected -and -not ($script:Calls -contains 'set-api-env')) 'Authentication failure reported ready or changed the session'
Write-Host 'PASS: process/pipe timeout, UTF-8, URI encoding, private ports, credentials, readiness, config-independent scoped stop and Brevo preservation.'

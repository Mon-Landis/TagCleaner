[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
$Url = "http://127.0.0.1:{0}/api/health" -f $Port

$connections = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if (-not $connections.Count) {
    Write-Host "TagCleaner is not listening on port $Port."
    exit 0
}

try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    $payload = $response.Content | ConvertFrom-Json
    if ($response.StatusCode -ne 200 -or $payload.ok -ne $true -or $payload.service -ne "TagCleaner") {
        throw "Unexpected response"
    }
}
catch {
    throw "Port $Port is not a confirmed TagCleaner service; refusing to stop its process."
}

$processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $processIds) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if (-not $process -or $process.Name -notmatch "^python(?:w)?\.exe$" -or $process.CommandLine -notmatch "server\.py") {
        throw "PID $processId does not look like a TagCleaner Python server; refusing to stop it."
    }
    Stop-Process -Id $processId -Force
    Write-Host "Stopped TagCleaner on port $Port (PID $processId)."
}

$deadline = [DateTime]::UtcNow.AddSeconds(5)
while ([DateTime]::UtcNow -lt $deadline) {
    if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
        exit 0
    }
    Start-Sleep -Milliseconds 150
}
throw "TagCleaner process stopped, but port $Port is still listening."

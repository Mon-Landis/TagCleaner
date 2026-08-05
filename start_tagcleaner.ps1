param(
    [string]$BindHost = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Url = "http://{0}:{1}/" -f $BindHost, $Port

function Test-PortOpen {
    param([string]$Address, [int]$TargetPort)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($Address, $TargetPort)
        if (-not $task.Wait(250)) { return $false }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-TagCleanerReady {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri ($Url + "api/health") -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $false }
        $payload = $response.Content | ConvertFrom-Json
        return $payload.ok -eq $true -and $payload.service -eq "TagCleaner"
    }
    catch {
        return $false
    }
}

# Reuse an already listening instance instead of starting a second local server.
if (Test-PortOpen $BindHost $Port) {
    if (-not (Test-TagCleanerReady)) {
        throw "Port $Port is already used by another application. Choose another port with -Port."
    }
    if (-not $NoBrowser) { Start-Process $Url }
    Write-Host "TagCleaner is already listening at $Url"
    exit 0
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
$pythonPath = ""
$pythonArgs = @()
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $pythonPath = $venvPython
}
else {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $pythonPath = $python.Source
    }
    else {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $pythonPath = $launcher.Source
            $pythonArgs = @("-3")
        }
    }
}
if (-not $pythonPath) {
    throw "Python was not found. Run install_tagcleaner.bat after installing Python 3.10 or newer."
}

$preflightArgs = @($pythonArgs) + @("-c", "import sys, PIL; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)")
& $pythonPath @preflightArgs
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10+ and Pillow are required. Run install_tagcleaner.bat to repair the environment."
}

$arguments = @()
$arguments += $pythonArgs
$arguments += "server.py"
$arguments += "--host"
$arguments += $BindHost
$arguments += "--port"
$arguments += [string]$Port
$process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-TagCleanerReady) { break }
    if ($process.HasExited) {
        throw "TagCleaner failed to start; Python exit code: $($process.ExitCode)."
    }
    Start-Sleep -Milliseconds 200
}

if (-not (Test-TagCleanerReady)) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    throw "TagCleaner did not open port $Port within 20 seconds."
}

if (-not $NoBrowser) { Start-Process $Url }
Write-Host "TagCleaner started at $Url (PID $($process.Id))"

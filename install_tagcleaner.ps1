[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$NoShortcut,
    [switch]$NoStart,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Requirements = Join-Path $Root "requirements.txt"
$VenvRoot = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
Set-Location -LiteralPath $Root

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Get-PythonVersion {
    param(
        [string]$FilePath,
        [string[]]$Prefix = @()
    )

    $probeArgs = @($Prefix) + @("-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))")
    $versionText = & $FilePath @probeArgs 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $versionText) { return $null }
    try {
        return [version](($versionText | Select-Object -Last 1).Trim())
    }
    catch {
        return $null
    }
}

function Get-CompatiblePython {
    $candidates = @()
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $candidates += [pscustomobject]@{ Path = $launcher.Source; Prefix = @("-3") }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $candidates += [pscustomobject]@{ Path = $python.Source; Prefix = @() }
    }

    foreach ($candidate in $candidates) {
        $version = Get-PythonVersion $candidate.Path $candidate.Prefix
        if (-not $version) { continue }
        if ($version.Major -gt 3 -or ($version.Major -eq 3 -and $version.Minor -ge 10)) {
            return [pscustomobject]@{ Path = $candidate.Path; Prefix = @($candidate.Prefix); Version = $version }
        }
    }
    throw "Python 3.10 or newer was not found. Install 64-bit Python from python.org and run this installer again."
}

if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
    throw "requirements.txt is missing from $Root"
}

Write-Host "[1/4] Checking Python..."
if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $venvVersion = Get-PythonVersion $VenvPython
    if (-not $venvVersion -or $venvVersion.Major -lt 3 -or ($venvVersion.Major -eq 3 -and $venvVersion.Minor -lt 10)) {
        throw ".venv does not contain a working Python 3.10+ interpreter. Rename or remove $VenvRoot, then run the installer again."
    }
    Write-Host "      Python $venvVersion (.venv)"
    Write-Host "[2/4] Reusing isolated environment..."
}
else {
    if (Test-Path -LiteralPath $VenvRoot) {
        throw ".venv exists but is incomplete. Rename or remove $VenvRoot, then run the installer again."
    }
    $systemPython = Get-CompatiblePython
    Write-Host "      Python $($systemPython.Version)"
    Write-Host "[2/4] Creating isolated environment..."
    $venvArgs = @($systemPython.Prefix) + @("-m", "venv", $VenvRoot)
    Invoke-Checked $systemPython.Path $venvArgs "Could not create the .venv environment."
}

Write-Host "[3/4] Installing runtime dependencies..."
Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "-r", $Requirements) "Dependency installation failed."

Write-Host "[4/4] Verifying installation..."
Invoke-Checked $VenvPython @("-c", "import PIL, tkinter; print('Pillow ' + PIL.__version__ + ' / tkinter OK')") "Pillow or tkinter is unavailable in the selected Python installation."
if (-not $SkipTests) {
    Invoke-Checked $VenvPython @("-m", "unittest", "discover", "-s", "tests") "TagCleaner self-tests failed."
}

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if ($desktop) {
        $shortcutPath = Join-Path $desktop "TagCleaner.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $Root "start_tagcleaner.bat"
        $shortcut.WorkingDirectory = $Root
        $shortcut.Description = "Start TagCleaner local dataset workbench"
        $shortcut.Save()
        Write-Host "Desktop shortcut: $shortcutPath"
    }
}

Write-Host "TagCleaner installation completed."
if (-not $NoStart) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "start_tagcleaner.ps1") -Port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "TagCleaner was installed, but the local server did not start."
    }
}

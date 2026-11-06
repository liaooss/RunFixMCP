# Upgrade RunFix in an existing environment without touching project code.
#
# Usage:
#   .\scripts\upgrade_windows.ps1                 # upgrade the .venv install
#   .\scripts\upgrade_windows.ps1 -Conda          # upgrade the .conda_env install

param([switch]$Conda)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Invoke-Step {
    param([string]$Label, [string]$Exe, [string[]]$Arguments)
    Write-Host "> $Label"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host ('  ' + $_) }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
}

if ($Conda) {
    $PythonExe = Join-Path $ProjectRoot ".conda_env\python.exe"
    $Install = "conda"
} else {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Install = "venv"
}

if (-not (Test-Path $PythonExe)) {
    throw "No RunFix environment found for '$Install'. Run .\scripts\install_windows.ps1 (or install_conda.ps1) first."
}

Write-Host "Upgrading RunFix in $PythonExe"

$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$before = & $PythonExe -m runfix --version 2>&1 | Out-String
$ErrorActionPreference = $previous
Write-Host "Current version: $($before.Trim())"

Invoke-Step "Install the upgraded package" $PythonExe @("-m", "pip", "install", "--upgrade", "-e", ".[dev,mcp]")
Write-Host "Version after upgrade:"
Invoke-Step "RunFix version" $PythonExe @("-m", "runfix", "--version")
Invoke-Step "MCP stdio self-check" $PythonExe @("-m", "runfix.mcp.smoke")

Write-Host ""
Write-Host "Upgrade complete. Project data under <project>\.runfix was not modified."

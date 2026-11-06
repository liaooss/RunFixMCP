# One-shot environment diagnosis for RunFix itself.
#
# Prints the facts needed to debug an installation or MCP integration problem, then
# runs RunFix's own end-to-end MCP self-check. Read-only: it starts no training job
# and modifies nothing under the project.

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Show-Value {
    param([string]$Label, [string]$Value)
    "{0,-26} {1}" -f $Label, $Value
}

Write-Host "=== RunFix environment diagnosis ==="
Show-Value "Project root" $ProjectRoot
Show-Value "PowerShell" $PSVersionTable.PSVersion.ToString()

foreach ($pair in @(
    @{ Label = "venv python"; Path = (Join-Path $ProjectRoot ".venv\Scripts\python.exe") },
    @{ Label = "conda python"; Path = (Join-Path $ProjectRoot ".conda_env\python.exe") }
)) {
    if (Test-Path $pair.Path) {
        $version = & $pair.Path --version 2>&1
        Show-Value $pair.Label "$($pair.Path)  ($version)"
    } else {
        Show-Value $pair.Label "not present"
    }
}

$git = Get-Command git -ErrorAction SilentlyContinue
Show-Value "git" ($(if ($git) { (& git --version) + "  " + $git.Source } else { "NOT FOUND (patches require git)" }))

$conda = Get-Command conda -ErrorAction SilentlyContinue
Show-Value "conda" ($(if ($conda) { (& conda --version 2>$null) + "  " + $conda.Source } else { "not found" }))

if ($env:CONDA_EXE) { Show-Value "CONDA_EXE" $env:CONDA_EXE }

$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $gpu = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
    Show-Value "GPU" ($gpu -join "; ")
} else {
    Show-Value "GPU" "nvidia-smi not found (CPU-only install)"
}

Write-Host ""
Write-Host "=== RunFix version ==="
$python = $null
foreach ($candidate in @(
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".conda_env\python.exe")
)) {
    if (Test-Path $candidate) { $python = $candidate; break }
}

if (-not $python) {
    Write-Host "No RunFix environment found. Run .\scripts\install_windows.ps1 first."
    exit 1
}

& $python -m runfix --version
Write-Host ""
Write-Host "=== MCP stdio self-check (gated) ==="
& $python -m runfix.mcp.smoke
$gated = $LASTEXITCODE
Write-Host ""
Write-Host "=== MCP stdio self-check (all 20 tools, fully permitted, temp project only) ==="
& $python -m runfix.mcp.smoke --full
$full = $LASTEXITCODE

Write-Host ""
if ($gated -eq 0 -and $full -eq 0) {
    Write-Host "RESULT: OK"
    exit 0
}
Write-Host "RESULT: FAILED (gated=$gated full=$full)"
exit 1

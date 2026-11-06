# RunFix installer for a plain Windows Python (no Conda)
#
# Creates an isolated virtual environment at <project>\.venv and installs RunFix
# into it. It never installs into the interpreter that launched the script.

param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# Native tools (pip, python, conda) write ordinary progress and warnings to stderr.
# Under $ErrorActionPreference = "Stop" PowerShell 5.1 turns those into terminating
# errors, so the preference is relaxed for the duration of each native call and the
# real exit code decides success.
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

Write-Host "RunFix installer (virtual environment)"
Write-Host "Project: $ProjectRoot"

function Test-Candidate {
    param([string]$Exe, [string[]]$PrefixArgs)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    $ok = & $Exe @PrefixArgs -c "import sys; print(int(sys.version_info[:2] >= (3, 10)))" 2>&1
    return ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "1")
}

$BaseExe = $null
$BaseArgs = @()
foreach ($candidate in @(
    @{ Exe = "py"; Args = @("-3.11") },
    @{ Exe = "py"; Args = @("-3.12") },
    @{ Exe = "py"; Args = @("-3") },
    @{ Exe = "python"; Args = @() }
)) {
    if (Test-Candidate -Exe $candidate.Exe -PrefixArgs $candidate.Args) {
        $BaseExe = $candidate.Exe
        $BaseArgs = $candidate.Args
        break
    }
}

if (-not $BaseExe) {
    throw "Python 3.10+ was not found. Install Python 3.11, or run .\scripts\install_conda.ps1 if you use Anaconda."
}

Write-Host "Base interpreter: $BaseExe $($BaseArgs -join ' ')"
Invoke-Step "Base interpreter version" $BaseExe ($BaseArgs + @("--version"))

$Venv = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

if ($DryRun) {
    Write-Host "Dry run: nothing will be installed."
    Write-Host "  Virtual environment: $Venv"
    Write-Host "  Interpreter:         $VenvPython  (exists: $(Test-Path $VenvPython))"
    Write-Host "  Would run:           $BaseExe $($BaseArgs -join ' ') -m venv `"$Venv`""
    Write-Host "  Would run:           `"$VenvPython`" -m pip install -e `".[dev,mcp]`""
    Write-Host "  Would verify:        `"$VenvPython`" -m runfix --version"
    Write-Host "  Would verify:        `"$VenvPython`" -m runfix.mcp.smoke"
    exit 0
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating $Venv ..."
    Invoke-Step "Create virtual environment" $BaseExe ($BaseArgs + @("-m", "venv", $Venv))
    if (-not (Test-Path $VenvPython)) {
        throw "Virtual environment creation reported success but $VenvPython is missing."
    }
} else {
    Write-Host "Reusing the existing environment at $Venv"
}

Invoke-Step "Upgrade pip" $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Step "Install RunFix with the dev and mcp extras" $VenvPython @("-m", "pip", "install", "-e", ".[dev,mcp]")

Write-Host ""
Write-Host "Verifying the installation ..."
Invoke-Step "RunFix version" $VenvPython @("-m", "runfix", "--version")
Invoke-Step "MCP stdio self-check" $VenvPython @("-m", "runfix.mcp.smoke")

Write-Host ""
Write-Host "RunFix is installed."
Write-Host "  Web UI:      .\scripts\start_windows.ps1"
Write-Host '  MCP server:  .\.venv\Scripts\python.exe -m runfix mcp --allowed-root "<project>"'
Write-Host "  MCP check:   .\.venv\Scripts\python.exe -m runfix.mcp.smoke --full"

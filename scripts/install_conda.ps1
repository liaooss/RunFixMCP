# RunFix installer for Anaconda / Miniconda
#
# Creates an isolated prefix environment at <project>\.conda_env from
# environment.yml and installs RunFix into it. The user's base environment, other
# named environments and the system CUDA driver are never modified.

param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# conda and pip write ordinary progress and warnings to stderr. Under
# $ErrorActionPreference = "Stop" PowerShell 5.1 turns those into terminating
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

Write-Host "RunFix installer (Conda prefix environment)"
Write-Host "Project: $ProjectRoot"

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "conda was not found. Open an Anaconda Prompt (or run 'conda init powershell') and try again."
}

$EnvPath = Join-Path $ProjectRoot ".conda_env"
$PythonExe = Join-Path $EnvPath "python.exe"
$Manifest = Join-Path $ProjectRoot "environment.yml"

if ($DryRun) {
    Write-Host "Dry run: nothing will be installed."
    Write-Host "  Conda prefix:   $EnvPath"
    Write-Host "  Interpreter:    $PythonExe  (exists: $(Test-Path $PythonExe))"
    Write-Host "  Would run:      conda env create --prefix `"$EnvPath`" --file `"$Manifest`""
    Write-Host "  Would run:      `"$PythonExe`" -m pip install -e `".[dev,mcp]`""
    Write-Host "  Would verify:   `"$PythonExe`" -m runfix --version"
    Write-Host "  Would verify:   `"$PythonExe`" -m runfix.mcp.smoke"
    exit 0
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating the isolated Conda environment at $EnvPath ..."
    Invoke-Step "Create Conda environment" "conda" @("env", "create", "--prefix", $EnvPath, "--file", $Manifest)
    if (-not (Test-Path $PythonExe)) {
        throw "Conda reported success but $PythonExe is missing. See the conda output above."
    }
} else {
    Write-Host "Reusing the existing Conda environment at $EnvPath"
    Invoke-Step "Update Conda environment" "conda" @("env", "update", "--prefix", $EnvPath, "--file", $Manifest, "--prune")
}

Write-Host "Installing RunFix into the isolated environment ..."
Invoke-Step "Upgrade pip" $PythonExe @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Step "Install RunFix with the dev and mcp extras" $PythonExe @("-m", "pip", "install", "-e", ".[dev,mcp]")

Write-Host ""
Write-Host "Verifying the installation ..."
Invoke-Step "RunFix version" $PythonExe @("-m", "runfix", "--version")
Invoke-Step "MCP stdio self-check" $PythonExe @("-m", "runfix.mcp.smoke")

Write-Host ""
Write-Host "RunFix Conda environment is ready: $EnvPath"
Write-Host "  Web UI:      .\scripts\start_conda.ps1"
Write-Host '  MCP server:  .\.conda_env\python.exe -m runfix mcp --allowed-root "<project>"'
Write-Host "  MCP check:   .\.conda_env\python.exe -m runfix.mcp.smoke --full"

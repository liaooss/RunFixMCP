$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot ".conda_env\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Conda environment not found. Run .\scripts\install_conda.ps1 first."
}

& $PythonExe -m runfix web --host 127.0.0.1 --port 8765

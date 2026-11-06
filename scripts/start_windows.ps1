$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "RunFix environment not found. Run .\scripts\install_windows.ps1 first."
}

& $python -m runfix web --host 127.0.0.1 --port 8765

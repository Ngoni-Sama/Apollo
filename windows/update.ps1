# Update Apollo: download the latest code and packages. Your data, settings (.env) and models are kept.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Native([string]$exe, [string[]]$arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @arguments | Out-Host; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
}

try {
    Invoke-RestMethod "http://127.0.0.1:8765/api/status" -TimeoutSec 2 | Out-Null
    Write-Host "Apollo is running. Close the black 'Apollo' window first, then run Update Apollo again." -ForegroundColor Yellow
    exit 1
} catch {
    Write-Verbose "Apollo is not running; OK to update"
}

if (-not (Test-Path ".git")) {
    Write-Host "This copy of Apollo wasn't installed with Git, so it can't update itself." -ForegroundColor Yellow
    Write-Host "Download the latest ZIP from https://github.com/Ngoni-Sama/Apollo and run install.bat."
    exit 1
}

Write-Host "Downloading the latest Apollo..." -ForegroundColor Cyan
if ((Invoke-Native "git" @("pull", "--ff-only")) -ne 0) {
    Write-Host "git pull failed (see above). Your files were not changed." -ForegroundColor Red
    exit 1
}
Write-Host "Updating packages..." -ForegroundColor Cyan
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if ((Invoke-Native $VenvPy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt")) -ne 0) {
    Write-Host "Package update failed (see above)." -ForegroundColor Red
    exit 1
}
Invoke-Native $VenvPy @("-m", "playwright", "install", "chromium") | Out-Null
Write-Host "Apollo is up to date. Double-click 'Apollo' to start it." -ForegroundColor Green

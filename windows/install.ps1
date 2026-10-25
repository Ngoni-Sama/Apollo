# Apollo installer for Windows 10/11. Run via install.bat (double-click).
# Safe to run again: every step skips what is already done.
#
# What it does:
#   1. installs Python 3.12, Git, FFmpeg and Deno with winget (only if missing)
#   2. puts Apollo in %USERPROFILE%\Apollo (git clone, so "Update Apollo" can pull fixes)
#   3. creates the Python environment and installs Apollo's packages + the TikTok browser
#   4. adds "Apollo" and "Update Apollo" shortcuts to the Desktop and Start menu
#   5. schedules an hourly hidden check for new videos (transcribes them too)
#   6. sets up The Diary Of A CEO (full episodes only) and smoke-tests 3 episodes
#   7. opens Apollo and, if you say yes, starts transcribing

param(
    [string]$Channel = "@TheDiaryOfACEO",
    [switch]$NoSchedule,
    [switch]$NoChannel,
    [switch]$InPlace      # install into the folder that contains install.bat (used by the VS Code task)
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$RepoUrl = "https://github.com/Ngoni-Sama/Apollo.git"
$Port = 8765
$script:StepNo = 0
$TotalSteps = 8

function Step([string]$text) {
    $script:StepNo++
    Write-Host ""
    Write-Host "[$script:StepNo/$TotalSteps] $text" -ForegroundColor Cyan
}
function Ok([string]$text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Info([string]$text) { Write-Host "        $text" }
function Warn([string]$text) { Write-Host "    !!  $text" -ForegroundColor Yellow }
function Fail([string]$text) {
    Write-Host ""
    Write-Host "    XX  $text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# Run a native program; return its exit code. Never throws on stderr output (PowerShell 5.1 quirk).
function Invoke-Native([string]$exe, [string[]]$arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @arguments | Out-Host; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
}

function Test-Command([string]$name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Find-Python {
    # Apollo's speech-to-text packages support Python 3.11 - 3.13.
    foreach ($v in @("3.12", "3.13", "3.11")) {
        try {
            $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            $ErrorActionPreference = $old
            if ($LASTEXITCODE -eq 0 -and $exe) { return ($exe | Select-Object -First 1).Trim() }
        } catch { $ErrorActionPreference = $old }
    }
    foreach ($v in @("312", "313", "311")) {
        $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Install-WithWinget([string]$id, [string]$label) {
    Info "Installing $label (this can take a few minutes)..."
    $code = Invoke-Native "winget" @("install", "--id", $id, "-e", "--silent",
        "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
    Update-SessionPath
    # -1978335189 = already installed
    if ($code -ne 0 -and $code -ne -1978335189) {
        Warn "winget returned code $code for $label"
    }
}

Write-Host ""
Write-Host "  Apollo installer" -ForegroundColor Yellow
Write-Host "  Personal study only: don't republish transcripts or train commercial products on them."

# ---------------------------------------------------------------------------
Step "Checking for the tools Apollo needs"
if (-not (Test-Command "winget")) {
    Fail ("winget is missing. Open the Microsoft Store, install/update 'App Installer', " +
          "then double-click install.bat again.")
}

$Python = Find-Python
if (-not $Python) {
    Install-WithWinget "Python.Python.3.12" "Python 3.12"
    $Python = Find-Python
    if (-not $Python) { Fail "Python was installed but can't be found yet. Restart your PC, then run install.bat again." }
}
Ok "Python: $Python"

if (-not (Test-Command "git")) {
    Install-WithWinget "Git.Git" "Git"
    if (-not (Test-Command "git") -and (Test-Path "$env:ProgramFiles\Git\cmd\git.exe")) {
        $env:Path += ";$env:ProgramFiles\Git\cmd"
    }
}
if (Test-Command "git") { Ok "Git" } else { Warn "Git not found; Apollo will be installed without automatic updates" }

if (-not (Test-Command "ffmpeg")) { Install-WithWinget "Gyan.FFmpeg" "FFmpeg (audio tools)" }
if (Test-Command "ffmpeg") { Ok "FFmpeg" } else { Fail "FFmpeg could not be installed. Restart your PC and run install.bat again." }

if (-not (Test-Command "deno")) { Install-WithWinget "DenoLand.Deno" "Deno (needed for YouTube downloads)" }
if (Test-Command "deno") { Ok "Deno" } else { Warn "Deno not found; YouTube audio downloads may fail (captions still work)" }

# ---------------------------------------------------------------------------
Step "Putting Apollo in place"
# This step never deletes or overwrites a folder. Preferred home: %USERPROFILE%\Apollo as a git
# checkout (so "Update Apollo" works). Anything unexpected -> install right here instead.
$Here = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path   # folder that contains install.bat
$Target = Join-Path $env:USERPROFILE "Apollo"
if ($InPlace -or (Test-Path (Join-Path $Here ".git"))) {
    $Root = $Here
    Ok "Using this folder: $Root"
} elseif (Test-Path (Join-Path $Target ".git")) {
    $Root = $Target
    Info "Apollo is already installed at $Root; fetching the latest version"
    if ((Invoke-Native "git" @("-C", $Root, "pull", "--ff-only")) -ne 0) { Warn "Couldn't update; continuing with the installed version" }
    Ok "Apollo folder: $Root (your data is kept)"
} elseif ((Test-Command "git") -and -not (Test-Path $Target)) {
    Info "Downloading Apollo to $Target"
    Info "If a GitHub sign-in window opens, sign in. The repository is private."
    if ((Invoke-Native "git" @("clone", $RepoUrl, $Target)) -eq 0) {
        $Root = $Target
        Ok "Apollo folder: $Root"
        Info "You can delete the folder you downloaded ($Here)."
    } else {
        $Root = $Here
        Warn "Download with Git failed, so Apollo will run from this folder instead: $Root"
        Info "(It works the same, but 'Update Apollo' won't be able to update it.)"
    }
} else {
    $Root = $Here
    Ok "Apollo folder: $Root"
}
Set-Location $Root

# ---------------------------------------------------------------------------
Step "Installing Apollo's Python packages (first time: 5-10 minutes)"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$VenvPyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $VenvPy)) {
    if ((Invoke-Native $Python @("-m", "venv", ".venv")) -ne 0) { Fail "Could not create the Python environment" }
}
Invoke-Native $VenvPy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip") | Out-Null
if ((Invoke-Native $VenvPy @("-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt")) -ne 0) {
    Fail "Installing packages failed. Check your internet connection and run install.bat again."
}
Ok "Python packages"
if ((Invoke-Native $VenvPy @("-m", "playwright", "install", "chromium")) -ne 0) {
    Warn "Browser for TikTok could not be installed; YouTube still works"
} else { Ok "TikTok browser" }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Ok "Created settings file .env"
} else { Ok "Settings file .env (kept yours)" }

# ---------------------------------------------------------------------------
Step "Creating shortcuts"
$Shell = New-Object -ComObject WScript.Shell
$Icon = Join-Path $Root "windows\apollo.ico"
$places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"))
foreach ($dir in $places) {
    foreach ($s in @(@{ Name = "Apollo"; Target = "Apollo.bat"; Desc = "Open Apollo" },
                     @{ Name = "Update Apollo"; Target = "update.bat"; Desc = "Download the latest Apollo" })) {
        $lnk = $Shell.CreateShortcut((Join-Path $dir "$($s.Name).lnk"))
        $lnk.TargetPath = Join-Path $Root $s.Target
        $lnk.WorkingDirectory = $Root
        $lnk.Description = $s.Desc
        if (Test-Path $Icon) { $lnk.IconLocation = "$Icon,0" }
        $lnk.Save()
    }
}
Ok "Desktop and Start menu: Apollo, Update Apollo"

# ---------------------------------------------------------------------------
Step "Scheduling the hourly check for new videos"
if ($NoSchedule) {
    Info "Skipped (-NoSchedule)"
} else {
    try {
        $action = New-ScheduledTaskAction -Execute $VenvPyw -WorkingDirectory $Root `
            -Argument "`"$Root\src\watch.py`" --log-file `"$Root\data\watch.log`" check --transcribe"
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) `
            -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12)
        Register-ScheduledTask -TaskName "Apollo - check for new videos" -Action $action -Trigger $trigger `
            -Settings $settings -Description "Queues and transcribes new uploads from creators you watch in Apollo" `
            -Force | Out-Null
        Ok "Every hour, hidden. Log: $Root\data\watch.log"
        Info "To turn it off: Task Scheduler > 'Apollo - check for new videos' > Disable"
    } catch {
        Warn "Could not create the scheduled task: $($_.Exception.Message)"
        Info "Apollo still works; use 'Check for new videos' in the app."
    }
}

# ---------------------------------------------------------------------------
$Creator = $Channel.TrimStart("@")
$Manifest = Join-Path $Root "data\manifest\youtube\$Creator.jsonl"
$Episodes = 0
$SmokeOk = $false
Step "Setting up $Channel (full episodes only, no Shorts)"
if ($NoChannel) {
    Info "Skipped (-NoChannel)"
    $script:StepNo++
} else {
    $watchlist = Join-Path $Root "data\watchlist.json"
    $already = (Test-Path $watchlist) -and ((Get-Content $watchlist -Raw) -match [regex]::Escape("`"creator`": `"$Creator`""))
    if ($already) {
        Ok "Already watching $Channel"
    } elseif ((Invoke-Native $VenvPy @("src\watch.py", "add", "--platform", "youtube", "--channel", $Channel, "--skip-shorts")) -eq 0) {
        Ok "Watching $Channel for new episodes"
    } else {
        Warn "Could not start watching $Channel (see the message above)"
    }

    Info "Listing every full episode (about a minute)..."
    if ((Invoke-Native $VenvPy @("src\enumerate.py", "--platform", "youtube", "--channel", $Channel, "--tabs", "videos")) -eq 0) {
        $Episodes = @(Get-Content $Manifest | Where-Object { $_.Trim() }).Count
        Ok "$Episodes episodes in the list"
    } else {
        Warn "Listing the channel failed (see the message above). You can retry later with 'List all videos' in the app."
    }

    # ---------------------------------------------------------------------------
    Step "Smoke test: 3 real episodes from your internet connection"
    if (Test-Path $Manifest) {
        $urls = Get-Content $Manifest -TotalCount 3 | ForEach-Object { ($_ | ConvertFrom-Json).url }
        $smokeArgs = @("scripts\smoke_test.py")
        foreach ($u in $urls) { $smokeArgs += @("--youtube", $u) }
        $SmokeOk = (Invoke-Native $VenvPy $smokeArgs) -eq 0
        if ($SmokeOk) {
            Ok "YouTube works from your connection"
        } else {
            Warn "Some checks failed (details above)."
            Info "If you see 'Sign in to confirm you're not a bot': sign in to YouTube in Firefox,"
            Info "then open $Root\.env in Notepad and set  YTDLP_COOKIES_FROM_BROWSER=firefox"
        }
    } else {
        Info "Skipped: no episode list"
    }
}

# ---------------------------------------------------------------------------
Step "Opening Apollo"
Start-Process -FilePath (Join-Path $Root "Apollo.bat") -WorkingDirectory $Root
$up = $false
for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    Start-Sleep -Seconds 1
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 2 | Out-Null; $up = $true }
    catch { $up = $false }  # not up yet; keep polling
}
if (-not $up) { Fail "Apollo didn't start. Look at the 'Apollo' window for the error." }
Ok "Apollo is open in your browser: http://127.0.0.1:$Port"

if ($Episodes -gt 0) {
    Write-Host ""
    $prompt = "    Start transcribing all $Episodes episodes now? Keep your PC on; you can stop any time. [Y/n]"
    if (-not $SmokeOk) { $prompt = "    The smoke test had failures. Try transcribing anyway? [y/N]" }
    $answer = Read-Host $prompt
    $yes = if ($SmokeOk) { $answer -notmatch "^\s*n" } else { $answer -match "^\s*y" }
    if ($yes) {
        $body = @{ kind = "transcribe_manifest"; platform = "youtube"; creator = $Creator } | ConvertTo-Json
        try {
            Invoke-RestMethod "http://127.0.0.1:$Port/api/jobs" -Method Post -ContentType "application/json" -Body $body | Out-Null
            Ok "Transcription started: watch progress on Apollo's Dashboard"
        } catch {
            Warn "Couldn't start it: $($_.Exception.Message). Use 'Transcribe waiting' on the Creators tab."
        }
    } else {
        Info "Later: Creators tab > 'Transcribe waiting'"
    }
}

Write-Host ""
Write-Host "  All done." -ForegroundColor Green
Write-Host "  Next time, just double-click 'Apollo' on your Desktop."
Write-Host "  Keep the black 'Apollo' window open while you use it; closing it stops Apollo."
Write-Host ""

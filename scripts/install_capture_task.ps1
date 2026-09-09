<#
.SYNOPSIS
    Register a Windows Scheduled Task that keeps the opening-line capture running.

.DESCRIPTION
    The capture has to be alive before books post look-ahead numbers, which is
    Sunday 22:00 UTC onward. It does NOT have to start at that moment: the
    poller in cfb_edge/watch.py already polls hourly when nothing is expected
    and every five minutes once the window opens or once any market opens. So
    the schedule only has to get the process running early; the precision lives
    in poll_interval, which is measured and tested.

    Starting early is close to free. Sunday 16:00 UTC to 22:00 UTC is six
    hourly polls.

    This registers a weekly Sunday task that runs whether or not you are logged
    on, restarts itself if it dies, and appends output to data\capture.log.

.PARAMETER UtcHour
    Hour (UTC) to start the capture on Sunday. Default 16, six hours before the
    release window opens. Converted to your local time for you, because Task
    Scheduler works in local time and the release window is defined in UTC.

.PARAMETER TaskName
    Name of the scheduled task. Default "CFB Edge line capture".

.EXAMPLE
    # From an elevated PowerShell, in the repository root:
    powershell -ExecutionPolicy Bypass -File scripts\install_capture_task.ps1

.EXAMPLE
    # Start earlier, and check what it did:
    powershell -ExecutionPolicy Bypass -File scripts\install_capture_task.ps1 -UtcHour 12
    Get-ScheduledTask "CFB Edge line capture" | Get-ScheduledTaskInfo
#>
[CmdletBinding()]
param(
    [ValidateRange(0, 23)] [int] $UtcHour = 16,
    [string] $TaskName = "CFB Edge line capture"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $repo "scripts\capture.bat"
if (-not (Test-Path $bat)) { throw "cannot find $bat. Run this from the repository." }

if (-not $env:ODDS_API_KEY) {
    Write-Warning @"
ODDS_API_KEY is not set in this session. The task will fail until it is set
as a MACHINE or USER environment variable, not just in one terminal:
    setx ODDS_API_KEY "your-key"
A task started by the scheduler does not inherit a variable you exported into
some other window.
"@
}

# Task Scheduler triggers are local time; the release window is UTC. Convert
# rather than asking anyone to do the arithmetic, and show the working.
$nextSundayUtc = [datetime]::UtcNow.Date
while ($nextSundayUtc.DayOfWeek -ne [DayOfWeek]::Sunday) { $nextSundayUtc = $nextSundayUtc.AddDays(1) }
$targetUtc   = $nextSundayUtc.AddHours($UtcHour)
$targetLocal = [System.TimeZoneInfo]::ConvertTimeFromUtc($targetUtc, [System.TimeZoneInfo]::Local)

Write-Host ""
Write-Host "Release window opens : Sunday 22:00 UTC"
Write-Host "Capture will start   : Sunday $($UtcHour.ToString('00')):00 UTC"
Write-Host "  which is locally   : $($targetLocal.ToString('dddd HH:mm')) ($([System.TimeZoneInfo]::Local.Id))"
Write-Host ""

# cmd wraps the batch file so output can be appended to a log the task keeps.
$logDir = Join-Path $repo "data"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"`"$bat`" >> `"$logDir\capture.log`" 2>&1`"" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $targetLocal

# StartWhenAvailable covers a machine that was asleep at the trigger time, which
# is the common way a weekly task silently never runs. The restart settings
# cover the capture dying mid-window, which is worse than never starting: it
# looks like it ran.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3) `
    -MultipleInstances IgnoreNew

# S4U runs the task whether or not you are logged on without storing a
# password. If your policy refuses it, the error names the fallback.
try {
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
                                            -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                           -Settings $settings -Principal $principal -Force | Out-Null
} catch {
    throw @"
Could not register the task with S4U logon ($($_.Exception.Message)).

S4U runs a task whether or not you are logged on, without storing a password,
and some policies disallow it. Either register with stored credentials:

    Register-ScheduledTask -TaskName '$TaskName' -Action `$action -Trigger `$trigger ``
        -Settings `$settings -User '$env:USERNAME' -Password (Read-Host -AsSecureString)

or accept that the task only runs while you are logged on by passing
-LogonType Interactive. The second option is the usual reason a capture
silently misses a week.
"@
}

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "Registered '$TaskName'." -ForegroundColor Green
Write-Host "Next run : $($info.NextRunTime)"
Write-Host "Log      : $logDir\capture.log"
Write-Host ""
Write-Host "Verify it works without waiting for Sunday:"
Write-Host "    Start-ScheduledTask '$TaskName'"
Write-Host "    Get-Content '$logDir\capture.log' -Tail 20 -Wait"
Write-Host "Stop the test with:  Stop-ScheduledTask '$TaskName'"

# Makes the shop PC run everything by itself (M5):
#   - "AutoCAM service": the service in the background, no window, 30 s after the PC starts, whether or not anyone
#     is logged in (as this user, without storing a password: Task Scheduler's S4U). Task Scheduler checks every
#     5 minutes and starts it again if it isn't running. Its output is logs\service.log; the System card shows it.
#   - "AutoCAM Fusion": Fusion when this user logs in, through its Start Menu shortcut (which keeps working after
#     Fusion updates). Fusion needs the desktop, so someone has to be logged in (or automatic sign-in).
# Run it once, as the user the shop PC logs in as, from an administrator PowerShell (S4U tasks need it):
#   powershell -ExecutionPolicy Bypass -File C:\dev\frc-autocam\ops\install-autostart.ps1
# -DryRun shows what it would set up without changing anything. -NoFusion leaves Fusion out.
# Stop the service for a while: Disable-ScheduledTask "AutoCAM service"; Stop-ScheduledTask "AutoCAM service"
# (Enable-ScheduledTask and Start-ScheduledTask to bring it back). Undo with ops\uninstall-autostart.ps1.
param(
    [string]$Venv = "C:\dev\venvs\frc-autocam",
    [switch]$NoFusion,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$user = "$env:USERDOMAIN\$env:USERNAME"
$autocam = Join-Path $Venv "Scripts\autocam.exe"
if (-not (Test-Path $autocam)) { throw "Can't find $autocam. Set up the service first (docs\manual-tests.md, M3 step 1)." }

function Settings {
    New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
}
function AtLogon([string]$delay) {
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $t.Delay = $delay
    $t
}
function AtStartupAndEvery5Min {
    $t = New-ScheduledTaskTrigger -AtStartup
    $t.Delay = "PT30S"
    $every = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $every.Repetition.StopAtDurationEnd = $false
    @($t, $every)          # a run that's still going makes the 5-minute one a no-op (MultipleInstances IgnoreNew)
}
$interactive = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$background = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited

$tasks = @()
$tasks += [pscustomobject]@{
    Name = "AutoCAM service"
    Action = New-ScheduledTaskAction -Execute $autocam -Argument "run --verbose" -WorkingDirectory $repo
    Trigger = AtStartupAndEvery5Min
    Principal = $background
    When = "at startup (+30 s) and every 5 min if it isn't running, no window"
}
if (-not $NoFusion) {
    $lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Autodesk\Autodesk Fusion.lnk"
    if (Test-Path $lnk) {
        $tasks += [pscustomobject]@{
            Name = "AutoCAM Fusion"
            Action = New-ScheduledTaskAction -Execute "powershell.exe" `
                -Argument "-NoProfile -WindowStyle Hidden -Command `"Start-Process -FilePath '$lnk'`""
            Trigger = AtLogon "PT15S"
            Principal = $interactive
            When = "at logon of $user (+15 s)"
        }
    } else {
        Write-Host "Fusion's Start Menu shortcut isn't at $lnk, so Fusion won't start by itself. Is it installed for this user?"
    }
}

foreach ($t in $tasks) {
    if ($DryRun) {
        Write-Host "Would register '$($t.Name)' as $user, $($t.When): $($t.Action.Execute) $($t.Action.Arguments)"
    } else {
        Register-ScheduledTask -TaskName $t.Name -Action $t.Action -Trigger $t.Trigger -Principal $t.Principal `
            -Settings (Settings) -Description "frc-autocam ($repo)" -Force | Out-Null
        Write-Host "Registered '$($t.Name)' ($($t.When))."
    }
}

Write-Host ""
Write-Host "Still to do by hand, once:"
Write-Host "  1. In Fusion: Shift+S > Add-Ins > autocam_addin > tick 'Run on Startup'."
Write-Host "  2. Keep the PC awake: Settings > System > Power > Sleep: Never (when plugged in)."
Write-Host "  3. After a reboot someone must log in for Fusion (or set up automatic sign-in); the service doesn't need it."
Write-Host "Check it: log off and on, or reboot (docs\manual-tests.md, M5)."

# Makes the shop PC start everything when this Windows user logs in (M5):
#   - "AutoCAM service": the service, in a window titled "autocam service", 30 s after logon.
#   - "AutoCAM Fusion": Fusion, through its Start Menu shortcut (which keeps working after Fusion updates).
# Run it once, as the user the shop PC logs in as (no admin needed):
#   powershell -ExecutionPolicy Bypass -File C:\dev\frc-autocam\ops\install-autostart.ps1
# -DryRun shows what it would set up without changing anything. -NoFusion leaves Fusion out.
# Undo with ops\uninstall-autostart.ps1.
param(
    [string]$Venv = "C:\dev\venvs\frc-autocam",
    [switch]$NoFusion,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$user = "$env:USERDOMAIN\$env:USERNAME"
$start = Join-Path $PSScriptRoot "start-service.ps1"
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
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

$tasks = @()
$tasks += [pscustomobject]@{
    Name = "AutoCAM service"
    Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$start`" -Venv `"$Venv`"" -WorkingDirectory $repo
    Trigger = AtLogon "PT30S"
}
if (-not $NoFusion) {
    $lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Autodesk\Autodesk Fusion.lnk"
    if (Test-Path $lnk) {
        $tasks += [pscustomobject]@{
            Name = "AutoCAM Fusion"
            Action = New-ScheduledTaskAction -Execute "powershell.exe" `
                -Argument "-NoProfile -WindowStyle Hidden -Command `"Start-Process -FilePath '$lnk'`""
            Trigger = AtLogon "PT15S"
        }
    } else {
        Write-Host "Fusion's Start Menu shortcut isn't at $lnk, so Fusion won't start by itself. Is it installed for this user?"
    }
}

foreach ($t in $tasks) {
    if ($DryRun) {
        Write-Host "Would register '$($t.Name)': at logon of $user after $($t.Trigger.Delay): $($t.Action.Execute) $($t.Action.Arguments)"
    } else {
        Register-ScheduledTask -TaskName $t.Name -Action $t.Action -Trigger $t.Trigger -Principal $principal `
            -Settings (Settings) -Description "frc-autocam ($repo)" -Force | Out-Null
        Write-Host "Registered '$($t.Name)' (starts at logon of $user)."
    }
}

Write-Host ""
Write-Host "Still to do by hand, once:"
Write-Host "  1. In Fusion: Shift+S > Add-Ins > autocam_addin > tick 'Run on Startup'."
Write-Host "  2. Keep the PC awake: Settings > System > Power > Sleep: Never (when plugged in)."
Write-Host "  3. After a reboot someone must log in (or set up automatic sign-in for this account)."
Write-Host "Check it: log off and on, or reboot (docs\manual-tests.md, M5)."

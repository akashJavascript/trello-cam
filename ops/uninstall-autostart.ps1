# Removes the logon tasks install-autostart.ps1 made. Running copies of the service and Fusion keep running.
#   powershell -ExecutionPolicy Bypass -File C:\dev\frc-autocam\ops\uninstall-autostart.ps1
foreach ($name in "AutoCAM service", "AutoCAM Fusion") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed '$name'."
    } else {
        Write-Host "'$name' wasn't set up."
    }
}

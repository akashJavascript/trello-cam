# Starts the auto-CAM service in this window (titled "autocam service").
# The logon task runs this; you can also run it by hand:
#   powershell -ExecutionPolicy Bypass -File C:\dev\frc-autocam\ops\start-service.ps1
# Ctrl+C stops it. If the service is already running elsewhere, this one says so and stops.
param(
    [string]$Venv = "C:\dev\venvs\frc-autocam"
)
$host.UI.RawUI.WindowTitle = "autocam service"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$autocam = Join-Path $Venv "Scripts\autocam.exe"
if (-not (Test-Path $autocam)) {
    Write-Host "Can't find $autocam. Set up the service first (docs\manual-tests.md, M3 step 1)."
    exit 1
}
& $autocam run --verbose

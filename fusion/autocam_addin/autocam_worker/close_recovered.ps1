# Clicks "Close" on Fusion's "Recovered Documents" window (keeps the recovered files; File > Recover Documents has
# them). Fusion shows it over the first document after a crash, and while it's up toolpaths never generate
# (2026-11-02: four runs stuck in generate until it was closed). Started by fx_cam.generate with Fusion's process id.
# Started just before a generation (fx_cam.generate): waits WaitSeconds, then looks Tries times, WaitSeconds apart.
param([int]$FusionPid = 0, [int]$WaitSeconds = 0, [int]$Tries = 1)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$out = New-Object System.Collections.ArrayList
$f = if ($FusionPid) { Get-Process -Id $FusionPid -ErrorAction SilentlyContinue } else { Get-Process -Name Fusion360 -ErrorAction SilentlyContinue | Select-Object -First 1 }
if (-not $f) { "no Fusion"; exit }
for ($try = 0; $try -lt $Tries; $try++) {
  if ($WaitSeconds) { Start-Sleep -Seconds $WaitSeconds }
  if (-not (Get-Process -Id $f.Id -ErrorAction SilentlyContinue)) { "Fusion gone"; exit }
  $out.Clear()
  $cond = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $f.Id)
  $all = $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
  $dialog = $null
  foreach ($e in $all) { if ($e.Current.Name -eq "Recovered Documents") { $dialog = $e; break } }
  if (-not $dialog) { [void]$out.Add("no Recovered Documents window") }
  else {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $box = $dialog
    for ($i = 0; $i -lt 4 -and $box; $i++) {
      $btns = $box.FindAll([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, "Close")))
      $b = $btns | Where-Object { $_.Current.ControlType.ProgrammaticName -eq "ControlType.Button" } | Select-Object -First 1
      if ($b) { $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); [void]$out.Add("clicked Close"); break }
      $box = $walker.GetParent($box)
    }
    if ($out.Count -eq 0) { [void]$out.Add("found the window, no Close button") }
  }
  if ($out -contains "clicked Close") { break }
}
$out

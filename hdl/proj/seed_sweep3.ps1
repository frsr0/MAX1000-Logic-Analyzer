# Seed search for the c2/c4 + I/O-constraint + pump-counter design to re-close clk[2].
$ErrorActionPreference = "Continue"
$proj = "C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\hdl\proj"
$cp   = "$proj\compile.ps1"
$rpt  = "$proj\output_files\OLS_Logic_Analyzer.sta.rpt"
$log  = "$proj\seed_sweep3_results.txt"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$seeds = @(1, 3, 7, 11, 13, 17, 21, 29)
$bestSeed = 0; $bestSlack = -999.0
"=== c2/c4 design seed sweep $(Get-Date) ===" | Out-File $log
foreach ($s in $seeds) {
  $c = [System.IO.File]::ReadAllText($cp)
  $c = $c -replace "set_global_assignment -name SEED \d+", "set_global_assignment -name SEED $s"
  [System.IO.File]::WriteAllText($cp, $c, $utf8NoBom)
  Set-Location $proj
  .\compile.ps1 *>&1 | Out-Null
  $slacks = Select-String -Path $rpt -Pattern "Worst-case setup slack is\s+(-?\d+\.\d+)" |
            ForEach-Object { [double]$_.Matches[0].Groups[1].Value }
  if ($slacks) {
    $worst = ($slacks | Measure-Object -Minimum).Minimum
    "SEED=$s -> worst setup slack = $worst" | Tee-Object -FilePath $log -Append
    if ($worst -gt $bestSlack) { $bestSlack = $worst; $bestSeed = $s }
  } else { "SEED=$s -> BUILD FAILED" | Tee-Object -FilePath $log -Append }
}
$c = [System.IO.File]::ReadAllText($cp)
$c = $c -replace "set_global_assignment -name SEED \d+", "set_global_assignment -name SEED $bestSeed"
[System.IO.File]::WriteAllText($cp, $c, $utf8NoBom)
"=== done $(Get-Date); BEST SEED=$bestSeed slack=$bestSlack; compile.ps1 set to it ===" | Tee-Object -FilePath $log -Append

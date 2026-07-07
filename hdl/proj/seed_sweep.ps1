# Seed sweep for the full mixed-signal (FAST_RAW_BUILD=false) 200 MHz build.
# Compiles each seed, extracts clk[1] (200 MHz) and clk[2] (166.7 MHz) setup
# slacks from the STA summary, and stops when both close with margin.
param(
    [int[]]$Seeds = @(21, 30, 5, 12, 42, 7, 3, 17),
    # stop as soon as both domains have at least this much setup slack (ns)
    [double]$TargetSlack = 0.05,
    [switch]$RawOnly
)

$results = @()
$best = $null

foreach ($s in $Seeds) {
    Write-Host "=== seed $s ==="
    # Remove stale summaries so a failed compile can't report the previous
    # run's numbers.
    Remove-Item "output_files\OLS_Logic_Analyzer.sta.summary" -ErrorAction SilentlyContinue
    Remove-Item "output_files\OLS_Logic_Analyzer.fit.summary" -ErrorAction SilentlyContinue
    if ($RawOnly) {
        .\compile.ps1 -NoFlash -Seed $s -RawOnly | Out-Null
    } else {
        .\compile.ps1 -NoFlash -Seed $s | Out-Null
    }
    if (-not (Test-Path "output_files\OLS_Logic_Analyzer.sta.summary")) {
        Write-Host "seed $s : compile FAILED (no sta.summary)"
        continue
    }

    # Parse setup slacks per clock from the STA summary (Slow 85C model).
    $slk = @{}
    $lines = Get-Content "output_files\OLS_Logic_Analyzer.sta.summary"
    for ($i = 0; $i -lt $lines.Count - 1; $i++) {
        if ($lines[$i] -match "Slow 1200mV 85C Model Setup '.*clk\[(\d)\]'") {
            $clk = $matches[1]
            if ($lines[$i+1] -match "Slack\s*:\s*(-?[\d.]+)") {
                $slk["clk$clk"] = [double]$matches[1]
            }
        }
    }
    $le = (Select-String "Total logic elements" "output_files\OLS_Logic_Analyzer.fit.summary").Line.Trim()
    $r = [pscustomobject]@{ Seed=$s; Clk1=$slk["clk1"]; Clk2=$slk["clk2"]; Clk0=$slk["clk0"]; LE=$le }
    $results += $r
    Write-Host ("seed {0}: clk1={1}  clk2={2}  clk0={3}  {4}" -f $s, $r.Clk1, $r.Clk2, $r.Clk0, $le)

    $worst = [math]::Min($r.Clk1, $r.Clk2)
    if ($null -eq $best -or $worst -gt $best.Worst) {
        $best = [pscustomobject]@{ Seed=$s; Worst=$worst; Clk1=$r.Clk1; Clk2=$r.Clk2 }
        # archive this build's outputs
        Copy-Item "output_files\OLS_Logic_Analyzer.pof" "seed_sweep_best.pof" -Force
        Copy-Item "output_files\OLS_Logic_Analyzer.sof" "seed_sweep_best.sof" -Force
        Copy-Item "output_files\OLS_Logic_Analyzer.sta.summary" "seed_sweep_best.sta.summary" -Force
        Copy-Item "output_files\OLS_Logic_Analyzer.fit.summary" "seed_sweep_best.fit.summary" -Force
        Set-Content "seed_sweep_best_seed.txt" $s
    }

    $results | Format-Table -AutoSize | Out-String | Set-Content "seed_sweep_results.txt"
    if ($r.Clk1 -ge $TargetSlack -and $r.Clk2 -ge $TargetSlack) {
        Write-Host "seed $s closes timing with margin -- stopping sweep"
        break
    }
}

Write-Host ""
Write-Host "=== sweep done ==="
$results | Format-Table -AutoSize
if ($best) { Write-Host ("best: seed {0} (worst slack {1})" -f $best.Seed, $best.Worst) }

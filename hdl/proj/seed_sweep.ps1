# Seed sweep for the full mixed-signal (FAST_RAW_BUILD=false) 200 MHz build.
# Compiles each seed, extracts setup slacks for the four named PLL clocks
# (sys_clk/fast_clk/sdram_core_clk/adc_clk) plus the forwarded SDRAM chip
# clock (SDRAM_CHIP_CLK_OUT) from the STA summary, and stops when all close
# with margin. Clock names updated 2026-07-07 to match the SDC's explicit
# create_generated_clock names (sys_clk/fast_clk/sdram_core_clk/adc_clk) --
# the old 'clk\[(\d)\]' regex matched Quartus's auto-derived names and no
# longer matches anything, so every field silently came back blank.
param(
    [int[]]$Seeds = @(21, 30, 5, 12, 42, 7, 3, 17),
    # stop as soon as every tracked domain has at least this much setup slack (ns)
    [double]$TargetSlack = 0.05,
    [switch]$RawOnly
)

$clockNames = @('sys_clk', 'fast_clk', 'sdram_core_clk', 'adc_clk', 'SDRAM_CHIP_CLK_OUT')

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
        if ($lines[$i] -match "Slow 1200mV 85C Model Setup '([^']+)'") {
            $clk = $matches[1]
            if ($clockNames -contains $clk -and $lines[$i+1] -match "Slack\s*:\s*(-?[\d.]+)") {
                $slk[$clk] = [double]$matches[1]
            }
        }
    }
    $le = (Select-String "Total logic elements" "output_files\OLS_Logic_Analyzer.fit.summary").Line.Trim()
    $r = [pscustomobject]@{
        Seed = $s
        Sys = $slk['sys_clk']; Fast = $slk['fast_clk']; Sdram = $slk['sdram_core_clk']
        Adc = $slk['adc_clk']; ChipOut = $slk['SDRAM_CHIP_CLK_OUT']
        LE = $le
    }
    $results += $r
    Write-Host ("seed {0}: sys={1} fast={2} sdram={3} adc={4} chip_out={5}  {6}" -f `
        $s, $r.Sys, $r.Fast, $r.Sdram, $r.Adc, $r.ChipOut, $le)

    # Worst-case across every tracked clock. Missing values (parse miss) sort
    # last via a large negative sentinel, so a broken parse can't masquerade
    # as "best".
    $vals = @($r.Sys, $r.Fast, $r.Sdram, $r.Adc, $r.ChipOut) | ForEach-Object {
        if ($null -eq $_) { -999.0 } else { $_ }
    }
    $worst = ($vals | Measure-Object -Minimum).Minimum
    if ($null -eq $best -or $worst -gt $best.Worst) {
        $best = [pscustomobject]@{ Seed=$s; Worst=$worst }
        # archive this build's outputs
        Copy-Item "output_files\OLS_Logic_Analyzer.pof" "seed_sweep_best.pof" -Force
        Copy-Item "output_files\OLS_Logic_Analyzer.sof" "seed_sweep_best.sof" -Force
        Copy-Item "output_files\OLS_Logic_Analyzer.sta.summary" "seed_sweep_best.sta.summary" -Force
        Copy-Item "output_files\OLS_Logic_Analyzer.fit.summary" "seed_sweep_best.fit.summary" -Force
        Set-Content "seed_sweep_best_seed.txt" $s
    }

    $results | Format-Table -AutoSize | Out-String | Set-Content "seed_sweep_results.txt"
    if ($worst -ge $TargetSlack) {
        Write-Host "seed $s closes timing with margin on every tracked clock -- stopping sweep"
        break
    }
}

Write-Host ""
Write-Host "=== sweep done ==="
$results | Format-Table -AutoSize
if ($best) { Write-Host ("best: seed {0} (worst slack {1})" -f $best.Seed, $best.Worst) }

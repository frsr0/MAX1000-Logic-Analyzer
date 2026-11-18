# Parallel seed sweep for the full mixed-signal (FAST_RAW_BUILD=false) build.
# Usage (from the repo root or hdl/proj):
#   powershell -NoProfile -ExecutionPolicy Bypass -File hdl/proj/seed_sweep_parallel.ps1 -SeedList 16,7,30 -Jobs 3
# Runs up to -Jobs compiles at once. Quartus cannot share one project directory,
# so each seed compiles in its own copy under %TEMP% (hdl/proj, hdl/rtl, hdl/ip
# with the same relative layout the QSF uses; db/incremental_db/output_files are
# not copied). Results are archived to seed_results/seed<N>.* and the best
# result is kept in seed_sweep_best.* by the same rule as seed_sweep.ps1
# (seed_results/best.txt holds "seed worst_slack"). The temp copies are removed.
param(
    # Comma-separated seed list.
    [string]$SeedList = "21,30,5,12,42,7,3,17",
    [int]$Jobs = 3,
    # stop launching new seeds once a finished seed reaches this worst setup slack (ns)
    [double]$TargetSlack = 0.05,
    [switch]$RawOnly
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$projDir = $PSScriptRoot
$hdlDir = Split-Path $projDir -Parent
$resultsDir = Join-Path $projDir 'seed_results'
New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null

$seeds = @($SeedList -split ',' | ForEach-Object { $_.Trim() } |
    Where-Object { $_ -ne '' } | ForEach-Object { [int]$_ })
if ($seeds.Count -eq 0) { throw "No seeds given in -SeedList" }
if ($Jobs -lt 1) { throw "-Jobs must be at least 1" }

$clockNames = @('sys_clk', 'fast_clk', 'sdram_core_clk', 'SDRAM_CHIP_CLK_OUT')
$inv = [Globalization.CultureInfo]::InvariantCulture
$archiveExts = @('sof', 'pof', 'sta.summary', 'fit.summary')
$runRoot = Join-Path $env:TEMP ("seed_sweep_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

function Get-SeedSlacks {
    param([string]$OutDir)
    $slk = @{}
    $lines = Get-Content (Join-Path $OutDir 'OLS_Logic_Analyzer.sta.summary')
    for ($i = 0; $i -lt $lines.Count - 1; $i++) {
        if ($lines[$i] -match "Slow 1200mV 85C Model Setup '([^']+)'") {
            $clk = $matches[1]
            if ($clockNames -contains $clk -and $lines[$i+1] -match "Slack\s*:\s*(-?[\d.]+)") {
                $slk[$clk] = [double]$matches[1]
            }
        }
    }
    $leLine = Select-String "Total logic elements" (Join-Path $OutDir 'OLS_Logic_Analyzer.fit.summary') | Select-Object -First 1
    $le = if ($leLine) { $leLine.Line.Trim() } else { '' }
    # Missing values sort last via -999 so a parse miss can't look like a best seed.
    $vals = foreach ($c in $clockNames) { if ($slk.ContainsKey($c)) { $slk[$c] } else { -999.0 } }
    $worst = ($vals | Measure-Object -Minimum).Minimum
    [pscustomobject]@{
        Seed = $null
        Sys = $slk['sys_clk']; Fast = $slk['fast_clk']; Sdram = $slk['sdram_core_clk']
        ChipOut = $slk['SDRAM_CHIP_CLK_OUT']
        Worst = [double]$worst
        Status = ''
        LE = $le
    }
}

function Save-SeedArchive {
    param([int]$Seed, [string]$OutDir)
    foreach ($ext in $archiveExts) {
        $src = Join-Path $OutDir "OLS_Logic_Analyzer.$ext"
        if (Test-Path $src) { Copy-Item $src (Join-Path $resultsDir "seed$Seed.$ext") -Force }
    }
}

# Returns $true when this seed became the new best (and seed_sweep_best.* was replaced).
function Update-Best {
    param([int]$Seed, [double]$Worst)
    $bestFile = Join-Path $resultsDir 'best.txt'
    $prev = $null
    if (Test-Path $bestFile) {
        $parts = (Get-Content $bestFile -Raw).Trim() -split '\s+'
        if ($parts.Count -ge 2) { $prev = [double]::Parse($parts[1], $inv) }
    }
    if ($null -ne $prev -and -not ($Worst -gt $prev)) { return $false }
    foreach ($ext in $archiveExts) {
        $src = Join-Path $resultsDir "seed$Seed.$ext"
        if (Test-Path $src) { Copy-Item $src (Join-Path $projDir "seed_sweep_best.$ext") -Force }
    }
    Set-Content $bestFile ("{0} {1}" -f $Seed, $Worst.ToString('R', $inv))
    Set-Content (Join-Path $projDir 'seed_sweep_best_seed.txt') $Seed
    return $true
}

function Start-SeedRun {
    param([int]$Seed)
    $seedRoot = Join-Path $runRoot "seed$Seed"
    $dstProj = Join-Path $seedRoot 'hdl\proj'
    New-Item -ItemType Directory -Force -Path $seedRoot | Out-Null
    # Same relative layout as the repo so the QSF's ../rtl and ../ip paths resolve.
    robocopy $projDir $dstProj /E /XD db incremental_db output_files seed_results /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy of hdl/proj failed for seed $Seed (exit $LASTEXITCODE)" }
    robocopy (Join-Path $hdlDir 'rtl') (Join-Path $seedRoot 'hdl\rtl') /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy of hdl/rtl failed for seed $Seed (exit $LASTEXITCODE)" }
    robocopy (Join-Path $hdlDir 'ip') (Join-Path $seedRoot 'hdl\ip') /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy of hdl/ip failed for seed $Seed (exit $LASTEXITCODE)" }

    $log = Join-Path $runRoot "seed$Seed.log"
    $errLog = Join-Path $runRoot "seed$Seed.err.log"
    $compileArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $dstProj 'compile.ps1')`" -NoFlash -Seed $Seed"
    if ($RawOnly) { $compileArgs += ' -RawOnly' }
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $compileArgs `
        -WorkingDirectory $dstProj -RedirectStandardOutput $log -RedirectStandardError $errLog `
        -WindowStyle Hidden -PassThru
    [pscustomobject]@{ Seed = $Seed; Proc = $proc; ProjDir = $dstProj; SeedRoot = $seedRoot; Log = $log }
}

function Complete-SeedRun {
    param($Run)
    $Run.Proc.WaitForExit()
    $exit = $Run.Proc.ExitCode
    $s = $Run.Seed
    $outDir = Join-Path $Run.ProjDir 'output_files'
    $staPath = Join-Path $outDir 'OLS_Logic_Analyzer.sta.summary'
    if (-not (Test-Path $staPath)) {
        Write-Host "seed $s : compile FAILED (exit $exit, no sta.summary); see $($Run.Log)"
        Copy-Item $Run.Log (Join-Path $resultsDir "seed$s.compile.log") -Force -ErrorAction SilentlyContinue
        return [pscustomobject]@{ Seed = $s; Sys = $null; Fast = $null; Sdram = $null; ChipOut = $null; Worst = -999.0; Status = 'FAILED'; LE = '' }
    }
    $r = Get-SeedSlacks -OutDir $outDir
    $r.Seed = $s
    # Status from the slack itself: Process.ExitCode came back empty for these
    # hidden child runs, and compile.ps1 exits 1 on any negative slack anyway.
    $r.Status = if ($r.Worst -ge 0) { 'OK' } else { 'NEG-SLACK' }
    Save-SeedArchive -Seed $s -OutDir $outDir
    Write-Host ("seed {0}: sys={1} fast={2} sdram={3} chip_out={4} worst={5} [{6}]" -f `
        $s, $r.Sys, $r.Fast, $r.Sdram, $r.ChipOut, $r.Worst, $r.Status)
    if (Update-Best -Seed $s -Worst $r.Worst) {
        Write-Host ("new best: seed {0} (worst slack {1}) -> seed_sweep_best.*" -f $s, $r.Worst)
    }
    $r
}

$timer = [Diagnostics.Stopwatch]::StartNew()
$pending = New-Object System.Collections.ArrayList
$pending.AddRange($seeds)
$running = @()
$results = @()
$stop = $false

try {
    while ($pending.Count -gt 0 -or $running.Count -gt 0) {
        while (-not $stop -and $pending.Count -gt 0 -and $running.Count -lt $Jobs) {
            $s = [int]$pending[0]
            $pending.RemoveAt(0)
            Write-Host "=== launching seed $s ($($running.Count + 1)/$Jobs slots) ==="
            $running += Start-SeedRun -Seed $s
        }
        Start-Sleep -Seconds 5
        foreach ($run in @($running)) {
            if ($run.Proc.HasExited) {
                $running = @($running | Where-Object { $_.Seed -ne $run.Seed })
                $r = Complete-SeedRun -Run $run
                $results += $r
                Remove-Item -Recurse -Force $run.SeedRoot -ErrorAction SilentlyContinue
                if ($r.Worst -ge $TargetSlack) {
                    Write-Host "seed $($r.Seed) reaches target slack $TargetSlack -- no new seeds will be launched"
                    $stop = $true
                }
            }
        }
    }
} finally {
    foreach ($run in $running) {
        if (-not $run.Proc.HasExited) { Stop-Process -Id $run.Proc.Id -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -Recurse -Force $runRoot -ErrorAction SilentlyContinue
}

if ($pending.Count -gt 0) { Write-Host ("not launched (stopped at target): " + (($pending | ForEach-Object { $_ }) -join ',')) }

Write-Host ""
Write-Host ("=== parallel sweep done in {0:N1} min ===" -f $timer.Elapsed.TotalMinutes)
$table = $results | Sort-Object Worst -Descending
$table | Format-Table Seed, Worst, Sys, Fast, Sdram, ChipOut, Status, LE -AutoSize
$table | Format-Table Seed, Worst, Sys, Fast, Sdram, ChipOut, Status, LE -AutoSize | Out-String |
    Set-Content (Join-Path $projDir 'seed_sweep_results.txt')
$bestLine = Get-Content (Join-Path $resultsDir 'best.txt') -ErrorAction SilentlyContinue
if ($bestLine) { Write-Host ("best recorded in seed_results (all runs): seed {0}" -f $bestLine) }

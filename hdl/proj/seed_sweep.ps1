# Seed sweep for the full mixed-signal (FAST_RAW_BUILD=false) 200 MHz build.
# Usage (from the repo root or hdl/proj; the script changes to its own folder):
#   powershell -NoProfile -ExecutionPolicy Bypass -File hdl/proj/seed_sweep.ps1 -SeedList 1,2,3
#   (-Seeds 1,2,3 still works via -Command; -SeedList is the -File-safe form.)
# Compiles each seed one at a time, extracts setup slacks for the four named
# PLL clocks (sys_clk/fast_clk/sdram_core_clk) plus the forwarded SDRAM chip
# clock (SDRAM_CHIP_CLK_OUT) from the STA summary, and stops when all close
# with margin. Every seed's sof/pof/sta.summary/fit.summary is archived to
# seed_results/<design tag>/seed<N>.*. seed_sweep_best.* is replaced only when a seed's
# worst slack beats the best recorded in seed_results/<design tag>/best.txt ("seed worst"),
# so a later batch can never overwrite a better earlier result.
# Use seed_sweep_parallel.ps1 to run several seeds at once.
param(
    [int[]]$Seeds = @(21, 30, 5, 12, 42, 7, 3, 17),
    # Comma-separated seed list (works with powershell -File). Overrides -Seeds when given.
    [string]$SeedList = "",
    # stop as soon as every tracked domain has at least this much setup slack (ns)
    [double]$TargetSlack = 0.05,
    [switch]$RawOnly
)

Set-Location $PSScriptRoot

if ($SeedList -ne "") {
    $Seeds = @($SeedList -split ',' | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -ne '' } | ForEach-Object { [int]$_ })
}

# NB: no 'adc_clk' here -- the SDC defines no such named clock, so tracking it
# fed a permanent -999 sentinel into the worst-slack comparison.
$clockNames = @('sys_clk', 'fast_clk', 'sdram_core_clk', 'SDRAM_CHIP_CLK_OUT')
$inv = [Globalization.CultureInfo]::InvariantCulture
# Results are kept per design: a hash of the RTL, SDC and pin map names the
# folder, so sweeping changed RTL never overwrites an earlier design's images
# (seed numbers alone collide across RTL versions).
$designFiles = @(Get-ChildItem (Join-Path $PSScriptRoot '..\rtl') -Filter *.vhd | Sort-Object Name | ForEach-Object { $_.FullName }) +
               @((Join-Path $PSScriptRoot 'OLS_Logic_Analyzer.sdc'), (Join-Path $PSScriptRoot 'pin_assignments.csv'))
$designStream = New-Object System.IO.MemoryStream
foreach ($f in $designFiles) {
    if (Test-Path $f) { $b = [System.IO.File]::ReadAllBytes($f); $designStream.Write($b, 0, $b.Length) }
}
$designHash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($designStream.ToArray())
$designTag = (([BitConverter]::ToString($designHash)) -replace '-', '').Substring(0, 10).ToLower()
$resultsDir = Join-Path (Join-Path $PSScriptRoot 'seed_results') $designTag
Write-Host "design tag $designTag -> $resultsDir"
New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null
$archiveExts = @('sof', 'pof', 'sta.summary', 'fit.summary')

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
    # Missing values (parse miss) sort last via a large negative sentinel, so a
    # broken parse can't masquerade as "best".
    $vals = foreach ($c in $clockNames) { if ($slk.ContainsKey($c)) { $slk[$c] } else { -999.0 } }
    $worst = ($vals | Measure-Object -Minimum).Minimum
    [pscustomobject]@{
        Seed = $null
        Sys = $slk['sys_clk']; Fast = $slk['fast_clk']; Sdram = $slk['sdram_core_clk']
        ChipOut = $slk['SDRAM_CHIP_CLK_OUT']
        Worst = [double]$worst
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
        if (Test-Path $src) { Copy-Item $src (Join-Path $PSScriptRoot "seed_sweep_best.$ext") -Force }
    }
    Set-Content $bestFile ("{0} {1}" -f $Seed, $Worst.ToString('R', $inv))
    Set-Content (Join-Path $PSScriptRoot 'seed_sweep_best_seed.txt') $Seed
    return $true
}

$results = @()

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
    $outDir = Join-Path $PSScriptRoot 'output_files'
    if (-not (Test-Path (Join-Path $outDir 'OLS_Logic_Analyzer.sta.summary'))) {
        Write-Host "seed $s : compile FAILED (no sta.summary)"
        $results += [pscustomobject]@{ Seed = $s; Sys = $null; Fast = $null; Sdram = $null; ChipOut = $null; Worst = -999.0; LE = 'FAILED' }
        continue
    }

    $r = Get-SeedSlacks -OutDir $outDir
    $r.Seed = $s
    Save-SeedArchive -Seed $s -OutDir $outDir
    $results += $r
    Write-Host ("seed {0}: sys={1} fast={2} sdram={3} chip_out={4} worst={5}  {6}" -f `
        $s, $r.Sys, $r.Fast, $r.Sdram, $r.ChipOut, $r.Worst, $r.LE)

    if (Update-Best -Seed $s -Worst $r.Worst) {
        Write-Host ("new best: seed {0} (worst slack {1}) -> seed_sweep_best.*" -f $s, $r.Worst)
    }

    $results | Format-Table -AutoSize | Out-String | Set-Content (Join-Path $PSScriptRoot 'seed_sweep_results.txt')
    if ($r.Worst -ge $TargetSlack) {
        Write-Host "seed $s closes timing with margin on every tracked clock -- stopping sweep"
        break
    }
}

Write-Host ""
Write-Host "=== sweep done ==="
$results | Format-Table -AutoSize
$bestLine = Get-Content (Join-Path $resultsDir 'best.txt') -ErrorAction SilentlyContinue
if ($bestLine) { Write-Host ("best recorded in seed_results: seed {0}" -f $bestLine) }

param([int]$Seed = 1)

Write-Host "=== Seed ${Seed} Compile ==="

# Step 1: Generate QSF and wrapper via compile.ps1
$null = & powershell.exe -NoProfile -Command ".\compile.ps1 -NoFlash 2>&1"

# Step 2: Add SEED to QSF
$qsfPath = "OLS_Logic_Analyzer.qsf"
$qsf = Get-Content $qsfPath -Raw
$qsf = $qsf -replace '(?m)^set_global_assignment -name SEED.*\n?', ''
$seedLine = "set_global_assignment -name SEED ${Seed}`r`n"
$qsf = $seedLine + $qsf
Set-Content -Path $qsfPath -Value $qsf -Encoding ASCII
Write-Host "Injected SEED=${Seed}"

# Step 3: Compile with new seed
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_sh.exe" --flow compile OLS_Logic_Analyzer 2>&1

# Step 4: Report timing
$staPath = "output_files/OLS_Logic_Analyzer.sta.summary"
if (Test-Path $staPath) {
    $content = Get-Content $staPath
    Write-Host ""
    Write-Host "=== Seed ${Seed}: clk[2] Timing ==="
    $inClk2 = $false
    foreach ($line in $content) {
        if ($line -match "clk\[2\].*Setup") { $inClk2 = $true }
        if ($inClk2) {
            Write-Host $line
            if ($line -match "TNS") { break }
        }
    }
} else {
    Write-Host "Compilation failed or STA not generated"
}

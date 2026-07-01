$seeds = @(5, 15, 20, 25, 30, 35, 42, 50, 99)
$results = @{}

foreach ($s in $seeds) {
    Write-Host "=== Seed ${s} ==="
    Remove-Item -Recurse -Force db,incremental_db,output_files -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Name output_files | Out-Null
    $output = & powershell.exe -NoProfile -Command ".\compile.ps1 -NoFlash -Seed ${s} 2>&1"
    $r = Get-Content output_files/OLS_Logic_Analyzer.sta.summary
    $slack = ($r[5] -split ': ')[1].Trim()
    $tns = ($r[6] -split ': ')[1].Trim()
    Write-Host "  Slack: $slack   TNS: $tns"
    $results[$s] = @{Slack=$slack; TNS=$tns}
    
    # Best so far?
    $best = $results.GetEnumerator() | Sort-Object { [double]$_.Value.Slack } | Select-Object -First 1
    Write-Host "  Best so far: Seed $($best.Key) = $($best.Value.Slack)"
}

Write-Host "`n=== ALL RESULTS ==="
$results.GetEnumerator() | Sort-Object { [double]$_.Value.Slack } | ForEach-Object {
    Write-Host "Seed $($_.Key): Slack $($_.Value.Slack)  TNS $($_.Value.TNS)"
}

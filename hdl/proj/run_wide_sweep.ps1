$ErrorActionPreference = 'Continue'
& ./seed_sweep.ps1 -Seeds @(1,2,4,6,8,9,10,11,13,14,15,16,18,19,20,22,23,24,25,26,27,28,29,31,32,33,34,35,36,37,38,39,40,41,43,45,46,47,48) 2>&1 |
  Select-Object -Last 60 | Out-File -Encoding utf8 widened_sweep2.txt
Write-Output ("exit=" + $LASTEXITCODE) | Out-File -Append -Encoding utf8 widened_sweep2.txt

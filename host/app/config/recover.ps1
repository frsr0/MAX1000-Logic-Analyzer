Write-Host "=== OLS FT2232H EEPROM Recovery ===" -ForegroundColor Yellow

# Step 1: Try adding VIDPID to FTDI driver registry (may work from SYSTEM)
Write-Host "Step 1: Adding corrupted VID/PID to FTDI driver registry..." -ForegroundColor Cyan
try {
    New-Item -Path "HKLM:\SYSTEM\CurrentControlSet\Services\FTDIBUS\Parameters" -Force -ErrorAction Stop | Out-Null
    New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\FTDIBUS\Parameters" -Name "VIDPID" -Value "746E0004" -PropertyType MultiString -Force -ErrorAction Stop
    Write-Host "  OK!" -ForegroundColor Green
} catch {
    # Try via cmd.exe / reg.exe
    Write-Host "  PowerShell denied, trying reg.exe..." -ForegroundColor Yellow
    $result = reg add "HKLM\SYSTEM\CurrentControlSet\Services\FTDIBUS\Parameters" /v "VIDPID" /t REG_MULTI_SZ /d "746E0004" /f 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK!" -ForegroundColor Green
    } else {
        Write-Host "  Failed. Trying alternate approach..." -ForegroundColor Red
        
        # Try sc.exe approach: run as local system
        Write-Host "  Creating temporary SYSTEM service to write registry..." -ForegroundColor Yellow
        sc create OLSRecovery binPath= "cmd /c reg add HKLM\SYSTEM\CurrentControlSet\Services\FTDIBUS\Parameters /v VIDPID /t REG_MULTI_SZ /d 746E0004 /f" type= own start= demand 2>&1 | Out-Null
        sc start OLSRecovery 2>&1 | Out-Null
        sc delete OLSRecovery 2>&1 | Out-Null
        Start-Sleep 1
        
        # Check if it worked
        $check = reg query "HKLM\SYSTEM\CurrentControlSet\Services\FTDIBUS\Parameters" /v VIDPID 2>&1
        if ($check -match "746E0004") {
            Write-Host "  OK via SYSTEM!" -ForegroundColor Green
        } else {
            Write-Host "  All registry methods failed." -ForegroundColor Red
            Write-Host "  Continuing with alternate approach..." -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "Step 2: Trigger PnP re-scan..." -ForegroundColor Cyan
pnputil /scan-devices
Start-Sleep 3

Write-Host ""
Write-Host "Step 3: Check if FTDIBUS bound..." -ForegroundColor Cyan
$devs = Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_746E*" }
$devs | Select-Object Status, FriendlyName, InstanceId, Service | Format-Table -AutoSize

$hasFTDIBUS = $devs | Where-Object { $_.Service -eq "FTDIBUS" }
if ($hasFTDIBUS) {
    Write-Host "FTDIBUS is loaded!" -ForegroundColor Green
} else {
    Write-Host "FTDIBUS not loaded yet. Trying to install driver..." -ForegroundColor Yellow
    
    # Try to install FTDIBUS using the existing signed FTDI INF
    $repo = Get-ChildItem "${env:SYSTEMROOT}\System32\DriverStore\FileRepository\ftdibus.inf_amd64_*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $infPath = Join-Path $repo.FullName "ftdibus.inf"
    
    Write-Host "  Using INF: $infPath" -ForegroundColor Gray
    
    # Try pnputil with the signed INF
    pnputil /add-driver $infPath /install 2>&1
    
    Start-Sleep 3
    
    # Try again to install on the child device
    $mi00 = Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_746E*&MI_00*" -and $_.Service -ne "FTSER2K" }
    $mi01 = Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_746E*&MI_01*" }
    
    if ($mi00) {
        Write-Host "  Installing on MI_00..." -ForegroundColor Yellow
        pnputil /install-device $mi00.InstanceId 2>&1
    }
    if ($mi01) {
        Write-Host "  Installing on MI_01..." -ForegroundColor Yellow
        pnputil /install-device $mi01.InstanceId 2>&1
    }
    
    Start-Sleep 3
    
    $devs = Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_746E*" }
    $devs | Select-Object Status, FriendlyName, InstanceId, Service | Format-Table -AutoSize
}

Write-Host ""
Write-Host "Step 4: Check Python ftd2xx..." -ForegroundColor Cyan
python -c "import ftd2xx as ft; cnt=ft.listDevices(0); print('FTDI count:', cnt); [print('  %d: %s' % (i, ft.open(i).getDeviceInfo()['description'])) or ft.open(i).close() for i in range(cnt or [])]"

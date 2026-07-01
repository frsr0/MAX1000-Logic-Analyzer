#!/usr/bin/env pwsh
<#
.SYNOPSIS
Quick-start script: run full ACK pad optimization workflow

.DESCRIPTION
Automates testbench + hardware sweep for ACK pad optimization.
Prerequisites: GHDL installed, device connected, Python + driver working

.PARAMETER Step
Which step to run: 'all', 'testbench', 'hardware', or 'report'

.EXAMPLE
.\run_ack_pad_test.ps1 -Step all
.\run_ack_pad_test.ps1 -Step testbench
#>

param(
    [ValidateSet('all', 'testbench', 'hardware', 'report')]
    [string]$Step = 'all',
    [int]$SpiSpeed = 30000000
)

$ErrorActionPreference = 'Stop'

# Colors for output
$OK = 'Green'
$ERR = 'Red'
$WARN = 'Yellow'

function Header {
    param([string]$Text)
    Write-Host "`n$('='*60)" -ForegroundColor Cyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host $('='*60) -ForegroundColor Cyan
}

function Status {
    param([string]$Text, [switch]$Pass, [switch]$Fail, [switch]$Warn)
    if ($Pass) { Write-Host "✓ $Text" -ForegroundColor $OK }
    elseif ($Fail) { Write-Host "✗ $Text" -ForegroundColor $ERR }
    elseif ($Warn) { Write-Host "⚠ $Text" -ForegroundColor $WARN }
    else { Write-Host "→ $Text" }
}

# ─────────────────────────────────────────────────────────────
# Step 1: Testbench
# ─────────────────────────────────────────────────────────────

function Run-Testbench {
    Header "Step 1: Run ACK Pad Testbench"

    # Check GHDL
    Status "Checking GHDL installation..."
    $GhdlVer = ghdl --version 2>$null | Select-Object -First 1
    if ($GhdlVer) {
        Status $GhdlVer -Pass
    } else {
        Status "GHDL not found. Install: winget install GHDL.GHDL" -Fail
        return $false
    }

    # Run testbench
    Push-Location hdl/sim
    try {
        Status "Cleaning work directory..."
        rm work -Recurse -Force -ErrorAction SilentlyContinue | Out-Null

        Status "Analyzing packages..."
        ghdl -a --std=2008 -fsynopsys ../rtl/spi_protocol_pkg.vhd
        if ($LASTEXITCODE -ne 0) { throw "Package analysis failed" }

        Status "Analyzing testbench..."
        ghdl -a --std=2008 -fsynopsys tb_stream_protocol_timing.vhd
        if ($LASTEXITCODE -ne 0) { throw "Testbench analysis failed" }

        Status "Elaborating..."
        ghdl -e --std=2008 -fsynopsys tb_stream_protocol_timing
        if ($LASTEXITCODE -ne 0) { throw "Elaboration failed" }

        Status "Running simulation..."
        $TB_Output = @(ghdl -r --std=2008 -fsynopsys tb_stream_protocol_timing --stop-time=1us 2>&1)

        Status "Testbench completed" -Pass

        # Extract key numbers
        $Recommendations = $TB_Output | Select-String -Pattern "Recommendations|byte|safe" -A 3
        if ($Recommendations) {
            Write-Host "`nKey Results:"
            $Recommendations | ForEach-Object { Write-Host "  $_" }
        }

        return $true
    } catch {
        Status $_.Exception.Message -Fail
        return $false
    } finally {
        Pop-Location
    }
}

# ─────────────────────────────────────────────────────────────
# Step 2: Hardware Sweep
# ─────────────────────────────────────────────────────────────

function Run-HardwareSweep {
    Header "Step 2: Hardware ACK Pad Sweep"

    Status "Checking device connection..."
    try {
        python -c "
from host.driver.ols_spi_device import OLSDeviceSPI
dev = OLSDeviceSPI()
dev.open()
print('Device connected ✓')
dev.close()
" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Status "Device detected" -Pass
        } else {
            Status "Device not found or driver error" -Fail
            return $false
        }
    } catch {
        Status "Device check failed: $_" -Fail
        return $false
    }

    Status "Running sweep (this takes 2-3 minutes)..."

    $SweepArgs = @(
        "host\driver\test_ack_pad_sweep.py",
        "--spi-speed", $SpiSpeed.ToString(),
        "--start", "96",
        "--end", "32",
        "--step", "8"
    )

    python @SweepArgs
    if ($LASTEXITCODE -eq 0) {
        Status "Hardware sweep completed" -Pass
        return $true
    } else {
        Status "Hardware sweep failed" -Fail
        return $false
    }
}

# ─────────────────────────────────────────────────────────────
# Step 3: Report and Recommendations
# ─────────────────────────────────────────────────────────────

function Show-Report {
    Header "Step 3: Summary & Next Steps"

    Write-Host @"
Results Summary:
────────────────────────────────────────────────────────────

1. TESTBENCH (Theory)
   - Simulated START_STREAM timing at 30 MHz
   - Provided theoretical minimum ack_pad
   - See: hdl/sim/tb_stream_protocol_timing output

2. HARDWARE (Practice)
   - Tested real device with different ack_pad values
   - Found breaking point where corruption occurs
   - Safe value = breaking_point - 5 bytes

Next Steps:
───────────

1. Note the SAFE minimum from hardware sweep above
2. Edit: host/driver/ols_spi.py line ~376
3. Update: ack_pad value in stream_command()
4. Run: pytest host/driver/tests/test_ols_spi_device.py
5. Measure: throughput improvement

Example update:
───────────────
def stream_command(self, request, n_bytes, ack_pad=None, stop_evt=None):
    if ack_pad is None:
        if self.speed_hz <= 7_500_000:
            ack_pad = 80    # Your 7.5 MHz safe value
        elif self.speed_hz <= 15_000_000:
            ack_pad = 64    # Your 15 MHz safe value
        else:
            ack_pad = 48    # Your 30 MHz safe value (from sweep)

Expected Gain:
──────────────
Current: 96 bytes ack_pad
Optimized: ~48 bytes (example)
Reduction: 50%
Throughput gain: ~2-4%

Documentation:
───────────────
- Full workflow: ACK_PAD_OPTIMIZATION.md
- Testbench guide: hdl/sim/RUN_TESTBENCH.md
- Protocol analysis: hdl/sim/ACK_PAD_ANALYSIS.md
"@
}

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

Header "ACK Pad Optimization Workflow"
Write-Host "Step: $Step | SPI Speed: $($SpiSpeed/1e6) MHz"

switch ($Step) {
    'all' {
        $TB_OK = Run-Testbench
        if (-not $TB_OK) { exit 1 }

        $HW_OK = Run-HardwareSweep
        if (-not $HW_OK) { exit 1 }

        Show-Report
    }
    'testbench' {
        Run-Testbench | Out-Null
    }
    'hardware' {
        Run-HardwareSweep | Out-Null
    }
    'report' {
        Show-Report
    }
}

Header "Done"
Write-Host "See documentation in: ACK_PAD_OPTIMIZATION.md" -ForegroundColor Green

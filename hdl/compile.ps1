param(
    [switch]$Flash
)

$QUARTUS = "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_sh.exe"
$PROGRAMMER = "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe"
$CSV = "pin_assignments.csv"
$WRAPPER = "OLS_Logic_Analyzer_wrapper.vhd"
$QSF = "OLS_Logic_Analyzer.qsf"
$QPF = "OLS_Logic_Analyzer.qpf"
$PROJECT = "OLS_Logic_Analyzer"

function Rename-Wrapper {
    param([string]$old, [string]$new)
    Rename-Item -Path $old -NewName $new -ErrorAction SilentlyContinue
}

# Parse pin_assignments.csv
$rows = Import-Csv $CSV
$pinMap = @{}  # baseSignal -> {pins, ios}
$ioMap = @{}   # baseSignal -> iostandard

foreach ($r in $rows) {
    $sig = $r.Signal
    $pin = $r.Pin
    $io = $r.'I/O Standard'

    # Split signal into base name and index
    if ($sig -match '^(.+?)_(\d+)$') {
        $base = $matches[1]
        $idx = [int]$matches[2]
        if (-not $pinMap.ContainsKey($base)) { $pinMap[$base] = @{} }
        $pinMap[$base][$idx] = $pin
        if ($io) { $ioMap[$base] = $io }
    } else {
        # Single-pin signal (CLK, UART_RX, etc.)
        if (-not $pinMap.ContainsKey($sig)) { $pinMap[$sig] = @{} }
        $pinMap[$sig][-1] = $pin
        if ($io) { $ioMap[$sig] = $io }
    }
}

# Build chip_pin attribute strings
$attrLines = @()
$attrLines += "    -- Quartus pin assignments"
# Declare attribute types first
$attrLines += "    attribute chip_pin : string;"
foreach ($base in ($pinMap.Keys | Sort-Object)) {
    $pins = $pinMap[$base]
    if ($pins.Count -eq 1 -and $pins.ContainsKey(-1)) {
        $val = $pins[-1]
    } else {
        $ordered = $pins.GetEnumerator() | Sort-Object Name -Descending | ForEach-Object { $_.Value }
        $val = $ordered -join ","
    }
    $attrLines += "    attribute chip_pin of $base : signal is `"$val`";"
}

# Build io_standard attributes (only for LED currently, but catch any with explicit standard)
$ioLines = @()
$hasIoStandard = $false
$wpuLines = @()
foreach ($base in ($ioMap.Keys | Sort-Object)) {
    $std = $ioMap[$base]
    if ($std -and $std -ne "3.3-V LVCMOS" -and $std -ne "3.3-V LVCMOS") {
        $ioLines += "    attribute io_standard of $base : signal is `"$std`";"
        $hasIoStandard = $true
    }
}
if ($hasIoStandard) {
    $ioLines = @("    -- I/O standards", "    attribute io_standard : string;") + $ioLines
}

# Build port map connections
$portMapLines = @()
$portMapLines += "        CLK => CLK, UART_RX => UART_RX, UART_TX => UART_TX,"
$portMapLines += "        SPI_CS => SPI_CS, SPI_MISO => SPI_MISO,"
$portMapLines += "        GPIO => GPIO, LED => LED,"
$portMapLines += "        sdram_addr => sdram_addr, sdram_ba => sdram_ba,"
$portMapLines += "        sdram_cas_n => sdram_cas_n, sdram_cke => sdram_cke,"
$portMapLines += "        sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,"
$portMapLines += "        sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,"
$portMapLines += "        sdram_we_n => sdram_we_n, sdram_clk => sdram_clk,"
$portMapLines += "        SEN_SDI => SEN_SDI, SEN_SPC => SEN_SPC,"
$portMapLines += "        SEN_CS => SEN_CS, SEN_SDO => SEN_SDO"

# Generate wrapper VHDL
$wrapperContent = @"
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity OLS_Logic_Analyzer_wrapper is
port (
    CLK       : IN  STD_LOGIC;
    UART_RX   : IN  STD_LOGIC;
    UART_TX   : INOUT STD_LOGIC;
    SPI_CS    : IN  STD_LOGIC := '1';
    SPI_MISO  : OUT STD_LOGIC := 'Z';
    GPIO      : INOUT STD_LOGIC_VECTOR(7 downto 0);
    sdram_addr  : OUT STD_LOGIC_VECTOR(11 downto 0);
    sdram_ba    : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_cas_n : OUT STD_LOGIC;
    sdram_cke   : OUT STD_LOGIC;
    sdram_cs_n  : OUT STD_LOGIC;
    sdram_dq    : INOUT STD_LOGIC_VECTOR(15 downto 0);
    sdram_dqm   : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_ras_n : OUT STD_LOGIC;
    sdram_we_n  : OUT STD_LOGIC;
    sdram_clk   : OUT STD_LOGIC;
    SEN_SDI     : INOUT STD_LOGIC;
    SEN_SPC     : INOUT STD_LOGIC;
    SEN_CS      : OUT   STD_LOGIC;
    SEN_SDO     : IN    STD_LOGIC;
    LED         : OUT STD_LOGIC_VECTOR(7 downto 0)
);
end OLS_Logic_Analyzer_wrapper;

architecture rtl of OLS_Logic_Analyzer_wrapper is
$($attrLines -join "`n")
$($ioLines -join "`n")
begin
    core : entity work.OLS_SDRAM_Top
    port map (
$($portMapLines -join "`n")
    );
end rtl;
"@

# Write wrapper
Set-Content -Path $WRAPPER -Value $wrapperContent -Encoding ASCII
Write-Host "Updated $WRAPPER with pin assignments from $CSV"

# Ensure QPF exists
if (-not (Test-Path $QPF)) {
    $qpfContent = @"
# -------------------------------------------------------------------------- #
# Quartus Prime Project File
# -------------------------------------------------------------------------- #
QUARTUS_VERSION = "18.1"
DATE = "23:00:00  June 01, 2026"

# Revisions
PROJECT_REVISION = "OLS_Logic_Analyzer"
"@
    Set-Content -Path $QPF -Value $qpfContent -Encoding ASCII
}

# Generate QSF
$qsfLines = @(
    'set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files',
    'set_global_assignment -name MIN_CORE_JUNCTION_TEMP 0',
    'set_global_assignment -name MAX_CORE_JUNCTION_TEMP 85',
    'set_global_assignment -name ERROR_CHECK_FREQUENCY_DIVISOR 1',
    'set_global_assignment -name ENABLE_OCT_DONE OFF',
    'set_global_assignment -name USE_CONFIGURATION_DEVICE ON',
    'set_global_assignment -name CRC_ERROR_OPEN_DRAIN OFF',
    'set_global_assignment -name ENABLE_BOOT_SEL_PIN OFF',
    'set_global_assignment -name OUTPUT_IO_TIMING_NEAR_END_VMEAS "HALF VCCIO" -rise',
    'set_global_assignment -name OUTPUT_IO_TIMING_NEAR_END_VMEAS "HALF VCCIO" -fall',
    'set_global_assignment -name OUTPUT_IO_TIMING_FAR_END_VMEAS "HALF SIGNAL SWING" -rise',
    'set_global_assignment -name OUTPUT_IO_TIMING_FAR_END_VMEAS "HALF SIGNAL SWING" -fall',
    'set_global_assignment -name POWER_PRESET_COOLING_SOLUTION "23 MM HEAT SINK WITH 200 LFPM AIRFLOW"',
    'set_global_assignment -name POWER_BOARD_THERMAL_MODEL "NONE (CONSERVATIVE)"',
    'set_global_assignment -name LAST_QUARTUS_VERSION "18.1.0 Lite Edition"',
    'set_global_assignment -name FAMILY "MAX 10"',
    'set_global_assignment -name DEVICE 10M08SAU169C8G',
    'set_global_assignment -name TOP_LEVEL_ENTITY OLS_Logic_Analyzer_wrapper',
    'set_global_assignment -name NUM_PARALLEL_PROCESSORS 16',
    'set_global_assignment -name INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM"',
    '',
    'set_global_assignment -name VHDL_FILE ../src/OLS_SDRAM_Top.vhd',
    'set_global_assignment -name VHDL_FILE ../src/OLS_Logic_Analyzer_SDRAM_Core.vhd',
    'set_global_assignment -name VHDL_FILE ../src/Fast_Logic_Analyzer_SDRAM.vhd',
    'set_global_assignment -name VHDL_FILE ../src/OLS_Interface.vhd',
    'set_global_assignment -name VHDL_FILE ../src/UART_Interface.vhd',
    'set_global_assignment -name VHDL_FILE ../src/SDRAM_Interface.vhd',
    'set_global_assignment -name VHDL_FILE ../src/SDRAM_Controller_Custom.vhd',
    'set_global_assignment -name VHDL_FILE ../src/SPI_Slave.vhd',
    'set_global_assignment -name VHDL_FILE ../src/ADC_Controller.vhd',
    'set_global_assignment -name VHDL_FILE ../src/Protocol_Trigger.vhd',
    'set_global_assignment -name VHDL_FILE ../src/Signal_Gen.vhd',
    'set_global_assignment -name VHDL_FILE OLS_Logic_Analyzer_wrapper.vhd',
    'set_global_assignment -name QIP_FILE Libraries/Logic_Analyzer/SDRAM_PLL.qip',
    '',
    '# Weak pull-ups on all GPIO and I2C/SPI pins',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[0]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[1]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[2]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[3]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[4]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[5]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[6]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to GPIO[7]',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to SEN_SDI',
    'set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to SEN_SPC'
)
Set-Content -Path $QSF -Value $qsfLines -Encoding ASCII
Write-Host "Generated $QSF with wrapper as top-level"

# Compile
Write-Host ""
Write-Host "=== Compiling ==="
if (-not (Test-Path $QUARTUS)) {
    Write-Host "ERROR: Quartus not found at $QUARTUS"
    Write-Host "Set QUARTUS_ROOT_DIR or install Quartus 18.1"
    exit 1
}

$output = & $QUARTUS --flow compile $PROJECT 2>&1
$compileOk = $LASTEXITCODE -eq 0

if ($compileOk) {
    Write-Host "Compilation: SUCCESS"
} else {
    Write-Host "Compilation: FAILED"
    Write-Host $output | Select-String -Pattern "Error"
    exit 1
}

# Flash (optional)
if ($Flash) {
    Write-Host ""
    Write-Host "=== Flashing ==="
    $sof = "output_files\$PROJECT.sof"
    if (Test-Path $sof) {
        & $PROGRAMMER -c 1 -m JTAG -o "P;$sof" 2>&1 | Select-String "succeeded"
        Write-Host "Flash: SUCCESS"
    } else {
        Write-Host "ERROR: $sof not found"
        exit 1
    }
}

Write-Host ""
Write-Host "Done."

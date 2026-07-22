param(
    [switch]$NoFlash,
    [switch]$Flash,
    # Elide the MSO bit-pack capture pipeline (FAST_RAW_BUILD=true). Default is
    # the full mixed-signal build with mso_capture included.
    [switch]$RawOnly,
    # Keep the old direct PLL c4 forward instead of the DDIO clock forward.
    [switch]$LegacyClkForward,
    # Current validated default: full mixed-signal seed 23, timing-closed at
    # slow-85C with +0.124 ns fast_clk setup slack.
    # Re-sweep (seed_sweep.ps1) after RTL or pin changes; bitstream remains
    # seed-sensitive at this density.
    [int]$Seed = 23
)

$FastRawBuild = if ($RawOnly) { 'true' } else { 'false' }
$UseDdioClkForward = if ($LegacyClkForward) { 'false' } else { 'true' }


$QUARTUS_DIR = "C:\intelFPGA_lite\18.1\quartus\bin64"
$QUARTUS = "$QUARTUS_DIR\quartus_sh.exe"
$PROGRAMMER = "$QUARTUS_DIR\quartus_pgm.exe"
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
    if ($base -eq "GPIO" -or $base -eq "UART_RX" -or $base -eq "UART_TX") { continue }  # GPIO -> MKR_D + PMOD; UART pins are SPI aliases
    $pins = $pinMap[$base]
    if ($pins.Count -eq 1 -and $pins.ContainsKey(-1)) {
        $val = $pins[-1]
    } else {
        $ordered = $pins.GetEnumerator() | Sort-Object Name -Descending | ForEach-Object { $_.Value }
        $val = $ordered -join ","
    }
    $attrLines += "    attribute chip_pin of $base : signal is `"$val`";"
}
# FTDI Channel B shares the old UART-named pins: BDBUS0=SCK, BDBUS1=MOSI.
if ($pinMap.ContainsKey("UART_RX")) {
    $attrLines += "    attribute chip_pin of SPI_SCK : signal is `"$($pinMap["UART_RX"][-1])`";"
}
if ($pinMap.ContainsKey("UART_TX")) {
    $attrLines += "    attribute chip_pin of SPI_MOSI : signal is `"$($pinMap["UART_TX"][-1])`";"
}
# Hardcoded pin assignments for MKR_D and PMOD (match physical board)
$attrLines += "    attribute chip_pin of MKR_D : signal is `"H8,K10,H5,H4,J1,J2,L12,J12,J13,K11,K12,J10,H10,H13,G12`";"
$attrLines += "    attribute chip_pin of PMOD : signal is `"M3,L3,M2,M1,N3,N2,K2,K1`";"

# Build io_standard attributes (only for LED currently, but catch any with explicit standard)
$ioLines = @()
$hasIoStandard = $false
$wpuLines = @()
foreach ($base in ($ioMap.Keys | Sort-Object)) {
    $std = $ioMap[$base]
    if ($std -and $std -ne "3.3-V LVCMOS" -and $std -ne "3.3-V LVCMOS") {
        $ioLines += "    -- IO standard for $base"
        $hasIoStandard = $true
    }
}

# Build port map connections
$portMapLines = @()
$portMapLines += "        CLK => CLK,"
$portMapLines += "        SPI_CS => SPI_CS, SPI_SCK => SPI_SCK, SPI_MOSI => SPI_MOSI, SPI_MISO => SPI_MISO,"
$portMapLines += "        MKR_D => MKR_D, PMOD => PMOD, LED => LED,"
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
    SPI_CS    : IN  STD_LOGIC := '1';
    SPI_SCK   : IN  STD_LOGIC := '0';
    SPI_MOSI  : IN  STD_LOGIC := '0';
    SPI_MISO  : OUT STD_LOGIC := 'Z';
    MKR_D     : INOUT STD_LOGIC_VECTOR(14 downto 0) := (others => 'Z');
    PMOD      : INOUT STD_LOGIC_VECTOR(7 downto 0) := (others => 'Z');
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
    -- Fast build: 100 MHz system clock, 200 MHz SDRAM/sample clock.
    constant FAST_SPEED : boolean := true;
    -- false = full mixed-signal build (mso_capture bit-pack pipeline included)
    constant FAST_RAW_BUILD : boolean := $FastRawBuild;
    -- true = DDIO forwarded SDRAM chip clock, false = legacy PLL c4 forward
    constant USE_DDIO_CLK_FORWARD : boolean := $UseDdioClkForward;
$($attrLines -join "`n")
$($ioLines -join "`n")
begin
    core : entity work.OLS_SDRAM_Top
    generic map (FAST_SPEED => FAST_SPEED, FAST_RAW_BUILD => FAST_RAW_BUILD, USE_DDIO_CLK_FORWARD => USE_DDIO_CLK_FORWARD)
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
    "set_global_assignment -name SEED $Seed",
    'set_global_assignment -name INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM"',
    'set_global_assignment -name OPTIMIZE_MULTI_CORNER_TIMING ON',
    '',
    '# Speed-mode fitter settings (active for 200 MHz FAST_SPEED build):',
    'set_global_assignment -name FITTER_EFFORT "STANDARD FIT"',
    '# AGGRESSIVE PERFORMANCE for the fitter-side timing push (BALANCED lost',
    '# ~1 ns on clk[1]); the per-entity AREA assignments below claw back the',
    '# synthesis area bloat in the 100 MHz command/readout domain (>1.1 ns',
    '# slack there) so the full mixed-signal build still fits.',
    'set_global_assignment -name OPTIMIZATION_MODE "BALANCED"',
    '# Physical synthesis OFF: register duplication/retiming inflate area, and',
    '# historical sweeps showed physical synthesis ERODED clk[1]/clk[2] slack',
    '# at this density (placement noise dominates).',
    'set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC OFF',
    'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF',
    'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING OFF',
    '# Fast domains (200 MHz capture, 167 MHz SDRAM): keep the critical blocks',
    '# selective, but let the big FLA cone optimize for area to recover fit.',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity Fast_Logic_Analyzer_SDRAM',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity SDRAM_Interface',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity SDRAM_Controller',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity mso_capture',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity OLS_Logic_Analyzer',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity OLS_SDRAM_Top',
    '# 100 MHz command/readout domain: synthesize for area.',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity OLS_Interface',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity Bit_Engine',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity LED_Controller',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity UART_Interface',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity ADC_Controller',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity Signal_Gen',
    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA -entity Protocol_Trigger',
    'set_instance_assignment -name OPTIMIZATION_TECHNIQUE AREA -to "|OLS_SDRAM_Top:core|OLS_Logic_Analyzer:SDRAM_Analyzer|Fast_Logic_Analyzer_SDRAM:Fast_Logic_Analyzer_SDRAM1"',
    'set_instance_assignment -name OPTIMIZATION_TECHNIQUE AREA -to "|OLS_SDRAM_Top:core|Signal_Gen:GEN"',
    'set_instance_assignment -name OPTIMIZATION_TECHNIQUE AREA -to "|OLS_SDRAM_Top:core|OLS_Logic_Analyzer:SDRAM_Analyzer|OLS_Interface:OLS_Interface1|UART_Interface:UART_Interface1"',
    'set_instance_assignment -name OPTIMIZATION_TECHNIQUE AREA -to "|OLS_SDRAM_Top:core|OLS_Logic_Analyzer:SDRAM_Analyzer|OLS_Interface:OLS_Interface1|Protocol_Trigger:Proto_Trigger1"',
    '# Extra placement effort: at 99% LE the last ~20 ps of clk[2] slack is',
    '# placement noise; a 4x placement budget reliably buys it back.',
    'set_global_assignment -name PLACEMENT_EFFORT_MULTIPLIER 4',
    'set_global_assignment -name ROUTER_EFFORT_MULTIPLIER 2',
    '',
    'set_global_assignment -name VHDL_FILE ../rtl/OLS_SDRAM_Top.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/LED_Controller.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/Fast_Logic_Analyzer_SDRAM.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/spi_protocol_pkg.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/spi_packet_rx.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/spi_packet_tx.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/OLS_Interface.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/UART_Interface.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/SDRAM_Interface.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/SDRAM_Controller_Custom.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/SPI_Slave.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/rle_compressor.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/capture_compressor.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/delta_rle_compressor.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/delta_calc.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/analog_packer.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/digital_rle.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/mso_stream_mux.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/mso_capture.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/fast_capture_budget.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/fast_capture_elastic_buffer.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/ADC_Controller.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/Bit_Engine.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/Protocol_Trigger.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/Signal_Gen.vhd',
    'set_global_assignment -name VHDL_FILE ../rtl/SDRAM_PLL.vhd',
    'set_global_assignment -name VHDL_FILE OLS_Logic_Analyzer_wrapper.vhd',
    '',
    '# Clock constraints',
    'set_global_assignment -name SDC_FILE OLS_Logic_Analyzer.sdc',
    '',
    '# Altera Modular ADC II IP',
    'set_global_assignment -name QIP_FILE ../ip/MAX10_ADC/synthesis/MAX10_ADC.qip',
    'set_global_assignment -name SDC_FILE ../ip/MAX10_ADC/synthesis/submodules/altera_modular_adc_control.sdc',
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

# Compile using QSF assignments (physical synthesis enabled in QSF)
$output = & $QUARTUS --flow compile $PROJECT 2>&1
$compileOk = $LASTEXITCODE -eq 0

if ($compileOk) {
    Write-Host "Compilation: SUCCESS"
} else {
    Write-Host "Compilation: FAILED"
    Write-Host $output | Select-String -Pattern "Error"
    exit 1
}

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

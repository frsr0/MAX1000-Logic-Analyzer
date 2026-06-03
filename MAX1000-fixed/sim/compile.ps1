param(
    [string]$SrcDir = (Join-Path $PSScriptRoot "..\src"),
    [string]$SimDir = $PSScriptRoot
)

$GHDL = "ghdl"
$STD = ""
$WRK = "--work=work"

$ErrorActionPreference = "Continue"

Write-Host "=== Compiling OLS simulation sources ==="
Write-Host "  Sources: $SrcDir"
Write-Host ""

$files = @(
    (Join-Path $SimDir "pll_model.vhd"),
    (Join-Path $SimDir "SDRAM_PLL.vhd"),
    (Join-Path $SimDir "SDRAM_Controller.vhd"),
    (Join-Path $SrcDir "SPI_Slave.vhd"),
    (Join-Path $SrcDir "ADC_Controller.vhd"),
    (Join-Path $SrcDir "UART_Interface.vhd"),
    (Join-Path $SrcDir "OLS_Interface.vhd"),
    (Join-Path $SrcDir "Signal_Gen.vhd"),
    (Join-Path $SrcDir "Protocol_Trigger.vhd"),
    (Join-Path $SrcDir "SDRAM_Interface.vhd"),
    (Join-Path $SrcDir "Fast_Logic_Analyzer_SDRAM.vhd"),
    (Join-Path $SrcDir "OLS_Logic_Analyzer_SDRAM_Core.vhd"),
    (Join-Path $SrcDir "OLS_SDRAM_Top.vhd"),
    (Join-Path $SimDir "tb_double_buffer.vhd"),
    (Join-Path $SimDir "tb_interface_cont.vhd"),
    (Join-Path $SimDir "tb_pipelined_handoff.vhd"),
    (Join-Path $SimDir "tb_uart_baud.vhd"),
    (Join-Path $SimDir "tb_spi_slave.vhd"),
    (Join-Path $SimDir "tb_adc_controller.vhd")
)

$ok = 0; $fail = 0
foreach ($f in $files) {
    $name = Split-Path $f -Leaf
    Write-Host "  $name ... " -NoNewline
    $output = cmd /c "`"$GHDL`" -a --std=08 $WRK `"$f`" 2>&1" | Out-String
    $exit = $LASTEXITCODE
    if ($exit -eq 0) {
        Write-Host "OK"
        $ok++
    } else {
        Write-Host "FAILED"
        $output
        $fail++
    }
}

Write-Host ""
if ($fail -eq 0) {
    Write-Host "=== Compilation successful ($ok files) ==="
} else {
    Write-Host "=== Compilation FAILED ($fail files) ==="
    exit 1
}

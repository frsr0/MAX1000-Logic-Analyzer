param(
    [string]$SrcDir = (Join-Path $PSScriptRoot "..\src"),
    [string]$SimDir = $PSScriptRoot
)

$GHDL = "ghdl"
$STD = "--std=08"
$WRK = "--work=work"

$ErrorActionPreference = "Stop"

Write-Host "=== Compiling OLS simulation sources ==="
Write-Host "  Sources: $SrcDir"
Write-Host ""

$files = @(
    (Join-Path $SimDir "SDRAM_Controller.vhd"),
    (Join-Path $SimDir "SDRAM_PLL.vhd"),
    (Join-Path $SimDir "pll_model.vhd"),
    (Join-Path $SrcDir "UART_Interface.vhd"),
    (Join-Path $SrcDir "OLS_Interface.vhd"),
    (Join-Path $SrcDir "Signal_Gen.vhd"),
    (Join-Path $SrcDir "Protocol_Trigger.vhd"),
    (Join-Path $SrcDir "SDRAM_Interface.vhd"),
    (Join-Path $SrcDir "Fast_Logic_Analyzer_SDRAM.vhd"),
    (Join-Path $SrcDir "OLS_Logic_Analyzer_SDRAM_Core.vhd"),
    (Join-Path $SrcDir "OLS_SDRAM_Top.vhd"),
    (Join-Path $SimDir "tb_double_buffer.vhd"),
    (Join-Path $SimDir "tb_interface_cont.vhd")
)

$ok = 0; $fail = 0
foreach ($f in $files) {
    $name = Split-Path $f -Leaf
    Write-Host "  $name ... " -NoNewline
    $output = & $GHDL -a $STD $WRK "$f" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK"
        $ok++
    } else {
        Write-Host "FAILED"
        Write-Host $output
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

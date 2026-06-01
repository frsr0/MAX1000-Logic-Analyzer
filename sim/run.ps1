param(
    [string]$SimDir = $PSScriptRoot
)

$GHDL = "ghdl"
$STD = "--std=08"
$VCD = "--vcd"

$ErrorActionPreference = "Stop"

function Run-Test($tb, $test, $time, $vcdfile) {
    Write-Host "--- $tb / $test ---"
    $vcdPath = Join-Path $SimDir $vcdfile
    $output = & $GHDL -r $STD "$tb" -gTEST="$test" --stop-time="$time" --assert-level=error "$VCD=$vcdPath" 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        Write-Host "  -> PASS"
        return 1
    } else {
        Write-Host "  -> FAIL"
        if ($output) { Write-Host $output }
        return 0
    }
}

$pass = 0; $fail = 0

Write-Host "=== OLS double-buffer testbench ==="
Write-Host ""
if ((Run-Test "tb_double_buffer" "tc_single_buffer"    "500us"  "db_single_buffer.vcd")  -eq 1) { $pass++ } else { $fail++ }
if ((Run-Test "tb_double_buffer" "tc_buffer_swap"      "500us"  "db_buffer_swap.vcd")    -eq 1) { $pass++ } else { $fail++ }
if ((Run-Test "tb_double_buffer" "tc_edge_timing"      "500us"  "db_edge_timing.vcd")    -eq 1) { $pass++ } else { $fail++ }
if ((Run-Test "tb_double_buffer" "tc_read_while_write"  "1ms"    "db_read_while_write.vcd") -eq 1) { $pass++ } else { $fail++ }

Write-Host ""
Write-Host "=== OLS Interface continuous-mode testbench ==="
Write-Host ""
if ((Run-Test "tb_interface_cont" "tc_cont_cmd"    "500us"  "cont_cmd.vcd")    -eq 1) { $pass++ } else { $fail++ }
if ((Run-Test "tb_interface_cont" "tc_cont_reset"  "500us"  "cont_reset.vcd")  -eq 1) { $pass++ } else { $fail++ }

Write-Host ""
Write-Host "=== Results: $pass passed, $fail failed ==="
exit $fail

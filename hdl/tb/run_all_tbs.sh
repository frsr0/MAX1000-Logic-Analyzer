#!/usr/bin/env bash
#
# run_all_tbs.sh - run the HDL testbench regression suite with GHDL.
#
# Scope: every regression TB in hdl/tb/ (tb_*.vhd). The hdl/sim/ testbenches
# (tb_stream_protocol_timing, tb_stream_timing_real, tb_stream_prefetch_latency,
# tb_ring_buffer_streaming, tb_sdram_read_pipeline, tb_sdram_streaming_pipeline,
# tb_stream_latency_profile, tb_generic_pattern_trigger) are deliberately NOT
# part of this suite: they are measurement/analysis dumps for the ACK-pad timing
# study (driven by hdl/sim/Makefile), not regression testbenches, and their pass
# criterion is human inspection of printed numbers, not assertions.
#
# Each TB is compiled against the full RTL set plus the simulation support
# models, analyzed once up front:
#   - library altera_mf : altera_mf.vhd (package altera_mf) +
#                         altera_mf_stub.vhd (package altera_mf_components)
#   - library lpm       : lpm_components_sim.vhd (package lpm_components) +
#                         lpm_divide_sim.vhd (entity lpm_divide)
#   - library work      : sim_pkg, pll_model, sdram_model, sdram_pin_model,
#                         adxl345_model, dcfifo_sim, altera_modular_adc_control_model,
#                         tb/SDRAM_PLL.vhd (behavioral Sim PLL model)
#   NOTE: lpm_divide_sim.vhd is analyzed ONLY into library lpm. It must NOT be
#   re-analyzed into work: GHDL names the object file lpm_divide_sim.o in both
#   libraries, so the work analysis would overwrite the lpm object and the
#   elaboration link fails with "undefined reference to lpm__lpm_divide__...".
#   Fast_Logic_Analyzer_SDRAM binds the lpm_divide component from lpm.lpm_components.
#   - library work      : all hdl/rtl/*.vhd EXCEPT rtl/SDRAM_PLL.vhd (the
#                         Quartus altpll wrapper; tb/SDRAM_PLL.vhd replaces it
#                         in simulation). RTL files are analyzed in dependency
#                         order (packages first, then leaves, then dependents).
#
# A TB PASSes only when it analyzes, elaborates and self-terminates cleanly.
# GHDL stops on severity error/failure, and reaching --stop-time is a failure:
# every gating TB must call std.env.finish/stop after its final assertion.
#
# Every run is bounded by --stop-time so a TB with a free-running clock (or a
# stalled DUT) can never hang the suite; reaching that bound does not count as
# completion. tb_tiny.vhd is included as a print-only
# toolchain smoke TB: it has no assertions but verifies analyze/elaborate/run
# end to end and always exits 0.
#
# Idempotent, offline, no root required. Run from anywhere:
#   bash hdl/tb/run_all_tbs.sh
#   STOP_TIME=20ms bash hdl/tb/run_all_tbs.sh   # override the 10 ms bound
#   GHDL=/opt/ghdl/bin/ghdl bash hdl/tb/run_all_tbs.sh
#   FILTER=sdram bash hdl/tb/run_all_tbs.sh     # run only TBs whose name contains "sdram"
#   SUITE=known-failures bash hdl/tb/run_all_tbs.sh  # audit the quarantine (currently empty)

set -u   # no -e: per-TB failures are collected and reported

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TB_DIR="$SCRIPT_DIR"
SUPPORT_DIR="$TB_DIR/support"
RTL_DIR="$(cd "$TB_DIR/../rtl" && pwd)"
WORK_DIR="$TB_DIR/.ghdl_work"
LOG_DIR="$TB_DIR/.ghdl_logs"

GHDL_BIN="${GHDL:-ghdl}"
FLAGS="--std=08 -fsynopsys"

# Generous simulation-time bound (10 ms at ~100-200 MHz is ~1-2M clock edges).
# The longest explicit waits in the suite are <= ~4 ms; tb_top needs more
# (16-pin UART loopback, ~1.3 ms/pin) and gets a per-TB override below.
STOP_TIME="${STOP_TIME:-10ms}"
# Wall-clock seconds per run, an anti-hang backstop for pathological delta
# loops that never advance simulation time (timeout(1) from coreutils).
RUN_TIMEOUT="${RUN_TIMEOUT:-300}"

if ! command -v "$GHDL_BIN" >/dev/null 2>&1; then
  echo "ERROR: GHDL not found on PATH (set GHDL=/path/to/ghdl)" >&2
  exit 1
fi

rm -rf "$WORK_DIR" "$LOG_DIR"
mkdir -p "$WORK_DIR" "$LOG_DIR" || exit 1
cd "$WORK_DIR" || exit 1

# ---------------------------------------------------------------------------
# Shared support/RTL analysis (once). Any failure aborts the suite.
# ---------------------------------------------------------------------------
analyze() {
  if ! "$GHDL_BIN" -a $FLAGS "$@"; then
    echo "ERROR: analysis failed: $*" >&2
    exit 1
  fi
}

# Device libraries (Altera megafunction stubs).
analyze --work=altera_mf "$SUPPORT_DIR/altera_mf.vhd"
analyze --work=altera_mf "$SUPPORT_DIR/altera_mf_stub.vhd"
analyze --work=lpm "$SUPPORT_DIR/lpm_components_sim.vhd"
analyze --work=lpm "$SUPPORT_DIR/lpm_divide_sim.vhd"

# Support models into work.
analyze "$SUPPORT_DIR/sim_pkg.vhd"
analyze "$SUPPORT_DIR/pll_model.vhd"
analyze "$SUPPORT_DIR/sdram_model.vhd"
analyze "$SUPPORT_DIR/sdram_pin_model.vhd"
analyze "$SUPPORT_DIR/adxl345_model.vhd"
analyze "$SUPPORT_DIR/dcfifo_sim.vhd"
analyze "$SUPPORT_DIR/altera_modular_adc_control_model.vhd"
analyze "$TB_DIR/SDRAM_PLL.vhd"             # behavioral Sim PLL model

# RTL, dependency order: packages -> leaves -> dependents.
RTL_ORDER=(
  spi_protocol_pkg.vhd
  LED_Controller.vhd                        # led_controller_pkg + entity
  Generic_Pattern_Trigger.vhd
  Protocol_Trigger.vhd
  Signal_Gen.vhd
  UART_Interface.vhd
  SPI_Slave.vhd
  spi_packet_rx.vhd
  spi_packet_tx.vhd
  digital_rle.vhd
  rle_compressor.vhd
  capture_compressor.vhd
  delta_calc.vhd
  analog_packer.vhd
  mso_stream_mux.vhd
  fast_capture_budget.vhd
  fast_capture_elastic_buffer.vhd
  Bit_Engine.vhd
  SDRAM_Controller_Custom.vhd
  SDRAM_Interface.vhd                       # component: SDRAM_Controller
  mso_capture.vhd                           # entity-work: delta_calc, analog_packer, digital_rle, mso_stream_mux
  delta_rle_compressor.vhd                  # entity-work: capture_compressor, rle_compressor
  OLS_Interface.vhd                         # entity-work: Generic_Pattern_Trigger, delta_rle_compressor
  Fast_Logic_Analyzer_SDRAM.vhd             # entity-work: fast_capture_elastic_buffer; components: SDRAM_Interface, dcfifo, lpm_divide
  OLS_Logic_Analyzer_SDRAM_Core.vhd         # components: OLS_Interface, Fast_Logic_Analyzer_SDRAM
  ADC_Controller.vhd                        # component: altera_modular_adc_control
  OLS_SDRAM_Top.vhd                         # entity-work: SDRAM_PLL, mso_capture, LED_Controller
)
for f in "${RTL_ORDER[@]}"; do
  analyze "$RTL_DIR/$f"
done

# ---------------------------------------------------------------------------
# Per-TB compile/elaborate/run.
# ---------------------------------------------------------------------------
# Optional name filter (iteration aid): FILTER=substring keeps only TBs whose
# name contains the substring. The shared support/RTL set is still analyzed
# once, so per-TB iteration stays fast without recompiling the DUT.
FILTER="${FILTER:-}"
SUITE="${SUITE:-gate}"

# Every tb_*.vhd file is maintained and runs in the normal gate. The exclusion
# variables remain explicit and empty so the summary proves that no bench is
# silently quarantined and an accidental reintroduction is visible in review.
ORPHANED_TBS=""
TOOLCHAIN_BLOCKED_TBS=""
KNOWN_FAILING_TBS=""
EXCLUDED="$ORPHANED_TBS $TOOLCHAIN_BLOCKED_TBS $KNOWN_FAILING_TBS"

TBS=()
case "$SUITE" in
  gate)
    for f in "$TB_DIR"/tb_*.vhd; do
      [ -e "$f" ] || continue
      tb="$(basename "$f" .vhd)"
      if [ -n "$FILTER" ] && [[ "$tb" != *"$FILTER"* ]]; then
        continue
      fi
      case " $EXCLUDED " in
        *" $tb "*) continue ;;
      esac
      TBS+=("$tb")
    done
    ;;
  known-failures)
    for tb in $KNOWN_FAILING_TBS; do
      if [ -n "$FILTER" ] && [[ "$tb" != *"$FILTER"* ]]; then
        continue
      fi
      TBS+=("$tb")
    done
    ;;
  *)
    echo "ERROR: unknown SUITE=$SUITE (expected gate or known-failures)" >&2
    exit 1
    ;;
esac

if [ "${#TBS[@]}" -eq 0 ]; then
  if [ "$SUITE" = "known-failures" ] && [ -z "$FILTER" ]; then
    echo "No documented HDL failures: 0 XFAIL, 0 XPASS."
    exit 0
  fi
  echo "ERROR: no tb_*.vhd testbenches found in $TB_DIR" >&2
  exit 1
fi

passed=0
failed=0
xfailed=0
failed_list=()

for tb in "${TBS[@]}"; do
  log="$LOG_DIR/$tb.log"

  # Per-TB stop-time overrides (default STOP_TIME covers the rest).
  case "$tb" in
    tb_continuous_wedge)  stop_time="30ms" ;;  # four complete full-block stream scenarios
    tb_gen_loopback)      stop_time="30ms" ;;  # real-pin SDRAM model, two capture scenarios
    tb_ols_rle_raw_stream) stop_time="30ms" ;; # complete 16K-sample compressed stream
    tb_top)               stop_time="30ms" ;;  # 16-pin UART loopback, ~1.3 ms per pin
    *)                    stop_time="$STOP_TIME" ;;
  esac

  reason=""
  if ! "$GHDL_BIN" -a $FLAGS "$TB_DIR/$tb.vhd" >"$log" 2>&1; then
    reason="analysis"
  elif ! "$GHDL_BIN" -e $FLAGS "$tb" >>"$log" 2>&1; then
    reason="elaboration"
  else
    if command -v timeout >/dev/null 2>&1; then
      run_cmd=(timeout "$RUN_TIMEOUT" "$GHDL_BIN" -r $FLAGS "$tb" --assert-level=error --stop-time="$stop_time")
    else
      run_cmd=("$GHDL_BIN" -r $FLAGS "$tb" --assert-level=error --stop-time="$stop_time")
    fi
    if ! "${run_cmd[@]}" >>"$log" 2>&1; then
      reason="simulation error"
    elif grep -qi "simulation stopped by --stop-time" "$log"; then
      reason="reached stop-time without explicit completion"
    elif grep -Eqi "assertion (error|failure)" "$log"; then
      reason="error/failure assertion in log"
    fi
  fi

  if [ "$SUITE" = "known-failures" ]; then
    if [ -n "$reason" ]; then
      echo "XFAIL $tb ($reason)"
      xfailed=$((xfailed + 1))
    else
      echo "XPASS $tb (documented failure unexpectedly passed; move it into the gate)"
      failed=$((failed + 1)); failed_list+=("$tb")
    fi
  elif [ -n "$reason" ]; then
    echo "FAIL  $tb ($reason)"
    failed=$((failed + 1)); failed_list+=("$tb")
  else
    echo "PASS  $tb"
    passed=$((passed + 1))
  fi
done

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  HDL TB SUMMARY: $passed passed, $xfailed expected failures, $failed failed"
echo "=============================================="
if [ "$SUITE" = "gate" ]; then
  echo "Excluded orphaned TBs: $ORPHANED_TBS"
  echo "Excluded toolchain-blocked TBs: $TOOLCHAIN_BLOCKED_TBS"
  echo "Tracked by SUITE=known-failures: $KNOWN_FAILING_TBS"
fi
if [ "$failed" -gt 0 ]; then
  echo "Failed TBs: ${failed_list[*]}"
  echo "Logs: $LOG_DIR"
  exit 1
fi
exit 0

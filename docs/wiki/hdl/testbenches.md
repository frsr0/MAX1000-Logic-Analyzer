# HDL Testbenches

**Directory:** `hdl/tb/`

The repository contains 57 `tb_*.vhd` files. The executable GHDL 6.0 gate is
defined by `run_all_tbs.sh`, not by file presence alone: 34 benches are in the
passing gate, while orphaned, toolchain-blocked, and known-failing benches are
classified explicitly.

## Run the suites

```bash
# Maintained passing gate (default)
bash hdl/tb/run_all_tbs.sh

# One maintained bench
FILTER=tb_bit_engine_div24 bash hdl/tb/run_all_tbs.sh

# Expected failures; XPASS is an error until the bench is reclassified
SUITE=known-failures bash hdl/tb/run_all_tbs.sh
```

The runner analyzes shared RTL/support once, then each bench. It requires an
explicit finish: reaching `--stop-time`, hanging until the wall timeout, or
logging an error/failure assertion is a failure. Per-bench stop-time overrides
cover deliberately long scenarios.

CI pins `ghdl/ghdl:6.0.0-llvm-ubuntu-22.04` so local and hosted semantics do
not drift with distribution packages.

## Maintained gate — 34 benches

| Area | Benches |
|---|---|
| SPI/CRC | `tb_crc`, `tb_crc2`, `tb_spi_slave`, `tb_spi_protocol`, `tb_spi_packet_tx`, `tb_spi_packet_link` |
| Capture/readout | `tb_minimal_capture`, `tb_capture_path`, `tb_capture_compressor`, `tb_batched_reads`, `tb_core_batched_reads`, `tb_repeated_blockreads`, `tb_stream_readout`, `tb_stream_tput` |
| FAST/continuous | `tb_fast_analyzer`, `tb_fast_capture_budget`, `tb_fast_capture_elastic_buffer`, `tb_continuous`, `tb_continuous_rate1` |
| Packed/MSO/compression | `tb_analog_packer`, `tb_mso_capture_probe`, `tb_mso_full_roundtrip`, `tb_rle_compressor`, `tb_delta_rle_compressor`, `tb_digital_rle_exactly_once` |
| Generator/trigger | `tb_bit_engine_repeat`, `tb_bit_engine_div24`, `tb_protocol_trigger` |
| Peripheral/control | `tb_adc_controller`, `tb_uart_interface`, `tb_sdram_interface` |
| Diagnostic/minimal | `tb_probe_core`, `tb_probe_run`, `tb_tiny` |

`tb_bit_engine_div24` is the current low-rate regression. It uses divider
values 1,000, 83,499, and 65,536 and checks exact output periods; the latter
two prove the value is not truncated to 16 bits.

## Orphaned benches — not compiled by the active RTL set

These benches instantiate legacy entities or ports that are no longer part of
the active build:

```text
tb_signal_gen
tb_gen_spi_decode
tb_gen_uart_decode
tb_gen_uart_repeat_decode
tb_gen_start
tb_gen_full
tb_gen_start_sim
```

They are retained as migration/reference material and are not counted as
passes.

## GHDL external-name blocked

These benches depend on external hierarchical names that trigger a GHDL 6.0
NULL-access/elaboration failure in this harness:

```text
tb_ols_interface
tb_ols_capture_contract
tb_fla_drop
tb_flush_path
tb_fifo_bridge
tb_packed_continuous_renew
tb_top
tb_pump_tput
```

Their underlying contracts are covered where possible by maintained seam
benches and software/board validation, but they are not represented as green
GHDL results.

## Tracked expected failures

`SUITE=known-failures` requires these eight benches to fail in their documented
way. If one passes, CI reports XPASS so it can be reviewed and moved into the
gate.

| Bench | Current issue |
|---|---|
| `tb_sdram_controller` | Legacy single-write command/data skew |
| `tb_core_stream` | Readback data-integrity assertion after elaboration |
| `tb_raw_stream_teardown` | Readback/teardown data-integrity assertion |
| `tb_analog_preamble` | Mixed ADC metavalue path never completes |
| `tb_continuous_wedge` | First SPI command does not explicitly finish |
| `tb_gen_loopback` | Cold-capture generator output remains flat |
| `tb_ols_rle_raw_stream` | Decoder runs beyond requested sample count |
| `tb_led_controller` | DONE-to-IDLE transition assertion |

## Support models

`hdl/tb/support/` provides simulation packages/models for clocks, SDRAM pins
and storage, Altera megafunctions, `dcfifo`, `lpm_divide`, MAX 10 ADC control,
and common test utilities. The independent exploratory benches under
`hdl/sim/` are intentionally outside this gate and retain their own Makefile
and documentation.

The connected-board suite remains the authority for exact-image electrical
behavior; simulation alone does not prove fitted timing or pin routing. See
[Verification Traceability](../verification-traceability.md).

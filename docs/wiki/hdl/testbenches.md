# HDL Testbenches

**Directory:** `hdl/tb/`

All 57 `tb_*.vhd` files are part of the required GHDL 6.0 gate. There are no
orphan, toolchain-blocked, expected-failure, or ignored testbench lists.

## Run the gate

```bash
# Complete required gate
bash hdl/tb/run_all_tbs.sh

# One bench, using the same analysis and pass criteria
FILTER=tb_bit_engine_div24 bash hdl/tb/run_all_tbs.sh
```

The runner analyzes shared RTL/support once, then runs every bench. A bench
passes only when it explicitly calls `finish` with no error/failure assertion.
Reaching `--stop-time`, hanging until the wall timeout, or merely producing no
failure is not a pass. Per-bench stop-time overrides cover deliberately long
scenarios without weakening those rules.

CI pins `ghdl/ghdl:6.0.0-llvm-ubuntu-22.04`, so local and hosted runs use the
same compiler and VHDL-2008 semantics.

## Current result

The 2026-09-04 signoff run reports:

```text
HDL TB SUMMARY: 57 passed, 0 expected failures, 0 failed
Excluded orphaned TBs:
Excluded toolchain-blocked TBs:
Tracked by SUITE=known-failures:
```

The formerly excluded generator benches were migrated to the active public
interfaces. Benches that used fragile external hierarchical names now assert
public ready/valid, status, and data contracts instead. All benches have
bounded scenarios and explicit completion.

## Repaired expected failures

The previous eight XFAILs are now ordinary required tests:

| Bench | Repair and strict contract |
|---|---|
| `tb_sdram_controller` | Corrected command/data skew; checks exact write/read ordering and DQ ownership |
| `tb_core_stream` | Corrected stream pipeline behavior; checks exact public stream data and teardown |
| `tb_raw_stream_teardown` | Repaired readback unwind; verifies bounded data integrity and clean return to command mode |
| `tb_analog_preamble` | Removed the ADC metavalue stall; checks complete, balanced packed preamble output |
| `tb_continuous_wedge` | Completed the command handshake and continuous capture renewal path |
| `tb_gen_loopback` | Repaired generator start/read-valid behavior; checks generated transitions through capture |
| `tb_ols_rle_raw_stream` | Bounded the RLE run counter and requested-sample budget; checks exact decoded length |
| `tb_led_controller` | Corrected DONE-to-IDLE sequencing; checks the visible LED state transitions |

## Coverage by subsystem

| Area | Required benches |
|---|---|
| SPI/CRC | `tb_crc`, `tb_crc2`, `tb_spi_slave`, `tb_spi_protocol`, `tb_spi_packet_tx`, `tb_spi_packet_link`, `tb_ols_interface`, `tb_ols_capture_contract` |
| Capture/readout | `tb_minimal_capture`, `tb_capture_path`, `tb_capture_compressor`, `tb_batched_reads`, `tb_core_batched_reads`, `tb_core_stream`, `tb_repeated_blockreads`, `tb_stream_readout`, `tb_stream_tput`, `tb_raw_stream_teardown`, `tb_ols_rle_raw_stream`, `tb_top` |
| FAST/continuous | `tb_fast_analyzer`, `tb_fast_capture_budget`, `tb_fast_capture_elastic_buffer`, `tb_continuous`, `tb_continuous_rate1`, `tb_continuous_wedge`, `tb_fifo_bridge`, `tb_fla_drop`, `tb_flush_path`, `tb_pump_tput` |
| Packed/MSO/compression | `tb_analog_packer`, `tb_analog_preamble`, `tb_mso_capture_probe`, `tb_mso_full_roundtrip`, `tb_rle_compressor`, `tb_delta_rle_compressor`, `tb_digital_rle_exactly_once`, `tb_packed_continuous_renew` |
| Generator/trigger | `tb_signal_gen`, `tb_gen_full`, `tb_gen_loopback`, `tb_gen_start`, `tb_gen_start_sim`, `tb_gen_spi_decode`, `tb_gen_uart_decode`, `tb_gen_uart_repeat_decode`, `tb_bit_engine_repeat`, `tb_bit_engine_div24`, `tb_protocol_trigger` |
| Peripheral/control | `tb_adc_controller`, `tb_uart_interface`, `tb_sdram_controller`, `tb_sdram_interface`, `tb_led_controller` |
| Diagnostic/seams | `tb_probe_core`, `tb_probe_run`, `tb_tiny` |

`tb_bit_engine_div24` uses divider values 1,000, 83,499, and 65,536 and
checks exact output periods; the latter two prove the value is not truncated
to 16 bits.

## Support models

`hdl/tb/support/` provides simulation packages/models for clocks, SDRAM pins
and storage, Altera megafunctions, `dcfifo`, `lpm_divide`, MAX 10 ADC control,
and common test utilities. Independent exploratory benches under `hdl/sim/`
remain outside this `tb_*.vhd` inventory and retain their own Makefile and
documentation.

The connected-board suite remains the authority for fitted-image electrical
behavior; simulation alone does not prove timing closure or pin routing. See
[Verification Traceability](../verification-traceability.md).

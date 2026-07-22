# HDL Testbenches

**Directory:** `hdl/tb/`

The HDL simulation suite covers every major subsystem. Testbenches use GHDL (VHDL-2008) and produce VCD traces where indicated. Support models for the SDRAM, PLL, ADC, LIS3DH accelerometer, and Altera primitives live in `hdl/tb/support/`.

## SPI Protocol

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_spi_protocol.vhd` | Full packet protocol | Packetised commands at various lengths | CRC match, correct status, sync recovery |
| `tb_spi_slave.vhd` | SPI slave byte interface | SPI byte sequences with CS toggles | Correct byte reception, CS mid-packet abort |
| `tb_spi_packet_tx.vhd` | TX packet builder | TX requests of various payloads | Correct sync, header, CRC, streaming |
| `tb_spi_packet_link.vhd` | RX → TX loopback | Send packet → verify response | Round-trip integrity |
| `tb_crc.vhd`, `tb_crc2.vhd` | CRC-16 function | Test vectors with known CRC values | Matched CRC-16-IBM/MODBUS |

## Capture Path

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_minimal_capture.vhd` | FLA + Interface | Arm, wait, read back 16 samples | Data integrity, Full flag |
| `tb_capture_path.vhd` | Full capture datapath | Varying Rate_Div and Samples | Sample count matches, no drop |
| `tb_capture_compressor.vhd` | Capture compressor | Capture with compression enabled | Correct compressed readback |
| `tb_fla_drop.vhd` | Fast_Logic_Analyzer | Marginal-rate captures | Zero dropped samples verified |
| `tb_core_stream.vhd` | Core streaming | CMD_START_STREAM readback | Contiguous samples, no gap |
| `tb_core_batched_reads.vhd` | Core batched reads | CMD_READ_CAPTURE in series | Address continuity across blocks |
| `tb_batched_reads.vhd` | FLA batched reads | Multiple read blocks | Correct block boundaries |
| `tb_repeated_blockreads.vhd` | Repeated read blocks | Continuous block reads | No stale data |
| `tb_fast_capture_budget.vhd` | FAST budget seam | Single-shot and continuous consume events | Exhaustion, done pulse, reload |
| `tb_fast_capture_elastic_buffer.vhd` | FAST stream seam | Fill, stall, simultaneous pop/push | Stable output and ordered words |
| `tb_fast_analyzer.vhd` (`FAST_SPEED=true`, `CHANNELS=16`) | Narrow packed FAST path | Select channel 3, capture 32 samples, read packed words | Full flag and words 1–3 equal `0xFFFF` |

## SDRAM

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_sdram_controller.vhd` | SDRAM controller | Command sequences, refresh, bursts | Correct RAS/CAS timing |
| `tb_sdram_interface.vhd` | SDRAM interface | Signal timing with pin model | Setup/hold margins |
| `tb_pump_tput.vhd` | Write pump | Streaming writes at max rate | Throughput meets timing |
| `tb_stream_tput.vhd` | Streaming throughput | Readback at max block rate | No underrun |

## Continuous Mode

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_continuous.vhd` | Triple-buffer continuous | Ring capture with Buffer_Ack | Buffer rotation, no overwrite |
| `tb_continuous_rate1.vhd` | Continuous at Rate_Div=1 | Max-rate capture into ring | Sample integrity at max rate |
| `tb_packed_continuous_renew.vhd` | Packed continuous renewal | Repeated packed buffer renewal | Ordered samples and buffer turnover |
| `tb_continuous_wedge.vhd` | Continuous wedge recovery | Host delays ACK → ring wraparound | Recovery without hang |

## Analog / MSO

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_analog_preamble.vhd` | Analog frame preamble | Analog capture with ADC model | Correct frame header, sample count |
| `tb_analog_packer.vhd` | Analog packer | Backpressure and framing transitions | Bit-exact ordered output |
| `tb_mso_capture_probe.vhd` | Full MSO pipeline | ADC + digital inputs through mso_capture | Output word format, bit15 routing |
| `tb_mso_full_roundtrip.vhd` | Full MSO round trip | Packed capture and readback | Digital/analog payload integrity |

## Generator

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_signal_gen.vhd` | Signal_Gen | Load FIFO, start, verify output | Correct protocol waveform |
| `tb_gen_start.vhd` | Gen start FSM | Start with full/empty FIFO | Start_Ack, Start_Reject timing |
| `tb_gen_full.vhd` | Full FIFO operation | Load to full, start, drain | FIFO full/empty flags |
| `tb_gen_loopback.vhd` | Gen loopback capture | CMD_GEN_CAPTURE | Capture matches generator output |
| `tb_gen_uart_decode.vhd` | UART gen + decode | Generate UART bytes, capture, decode | Matched bytes (VCD output) |
| `tb_gen_spi_decode.vhd` | SPI gen + decode | Generate SPI frame, capture, decode | Matched frame |
| `tb_gen_uart_repeat_decode.vhd` | Repeat mode UART | Gen UART in repeat, capture multiple frames | Each frame decodes correctly |
| `tb_bit_engine_repeat.vhd` | Bit Engine repeat mode | Load one byte, start repeat, assert clear | Remains busy, no `Done` pulse before clear |
| `tb_gen_start_sim.vhd` | Gen start timing | Start/load/handshake cycles | Timing diagrams in VCD |

## Interface / Control

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_ols_interface.vhd` | OLS_Interface | All commands + register access | Correct responses, error codes |
| `tb_fast_analyzer.vhd` | Fast path | Fast_Mode captures | BRAM readout, timing |
| `tb_ols_capture_contract.vhd` | Arm/abort contract | Repeated arm/abort cycles | Sticky DONE, no phantom captures |

## Trigger

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_protocol_trigger.vhd` | Protocol_Trigger | UART bytes, match/no-match | Correct trigger firing |

## UART

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_uart_interface.vhd` | UART_Interface | Async serial at various bauds | Data byte, framing error flag |

## Misc / Support

| Testbench | UUT | Stimulus | Asserts |
|---|---|---|---|
| `tb_led_controller.vhd` | LED_Controller | Brightness register changes | PWM output, fade transitions |
| `tb_adc_controller.vhd` | ADC_Controller | ADC profile changes | Correct channel mux, valid flags |
| `tb_fifo_bridge.vhd` | Async FIFO bridge | FAST_CLK → pclk writes | Correct data, no metastability |
| `tb_rle_compressor.vhd` | rle_compressor | Various data patterns | RLE ratio, flush correctness |
| `tb_flush_path.vhd` | Compressor flush | Partial runs at end of capture | Final run emitted |
| `tb_ols_rle_raw_stream.vhd` | RLE raw stream | Streaming with compression | Decompressed data == original |
| `tb_raw_stream_teardown.vhd` | Stream termination | Abrupt stream end | Clean state, no hang |
| `tb_stream_readout.vhd` | Stream readout | Continuous stream read | Sample order preserved |
| `tb_probe_core.vhd` | Core probe | Internal signal visibility | Waveform-level correctness |
| `tb_probe_run.vhd` | Run probe | Run start/stop edge timing | Run edge detection |
| `tb_top.vhd` | Full top-level | End-to-end capture + readback | Complete system integration |
| `tb_tiny.vhd` | Minimal smoke test | Quick SDRAM access | PLL lock, basic read/write |

## Support Models

| File | What it simulates |
|---|---|
| `support/sdram_model.vhd` | 64 Mbit SDRAM timing model |
| `support/sdram_pin_model.vhd` | SDRAM pin-level I/O model |
| `support/pll_model.vhd` | PLL (provides gated clocks without lock delay) |
| `support/altera_mf.vhd` / `altera_mf_stub.vhd` | Altera megafunction simulation models |
| `support/dcfifo_sim.vhd` | Dual-clock FIFO simulation model |
| `support/lpm_components_sim.vhd` / `lpm_divide_sim.vhd` | LPM component simulation models |
| `support/altera_modular_adc_control_model.vhd` | MAX10 ADC simulation model |
| `support/adxl345_model.vhd` | LIS3DH accelerometer simulation |
| `support/sim_pkg.vhd` | Shared simulation utilities and clock generation |

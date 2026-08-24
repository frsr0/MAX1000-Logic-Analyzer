# Graph Report - OLS_Logic_Analyzer_Clean  (2026-08-09)

## Corpus Check
- 475 files · ~1,288,124 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 4537 nodes · 9716 edges · 518 communities (214 shown, 304 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 683 edges (avg confidence: 0.68)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `5ef8a875`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- OLSDeviceSPI
- hw_validation.py
- ols_spi_device.py
- registry.py
- ExistingHostAdapter
- hardware/base.py
- CaptureResult
- client.ts
- OLS
- get_session_or_404
- DecodeContext
- ModbusDecoder
- encode
- _make_scope
- analog_frame_stride
- generator.py
- spi_protocol.py
- OLScope
- main.py
- TestCompressionHelpers
- CaptureManager
- server.py
- lod.py
- MSO Capture Pipeline: `mso_capture`
- mil/service.py
- SpiPort
- WaveformData
- measurements/base.py
- TriggerConfig
- TestOLSDeviceSPI
- test_generator_controller.py
- useApp
- measurements/digital.py
- test_api.py
- mockApp.ts
- greybox/MAX10_ADC.v
- test_core.py
- CaptureControls.tsx
- exports.py
- HardwareError
- properties
- WaveformView
- WaveformDisplay
- SPIDevice
- _wf
- frontend/package.json
- MockDevice
- .capture
- test_hw_validation_helpers.py
- renderer.ts
- list_ftdi_devices
- ols_spi.py
- measurements/analogue.py
- MAX1000 Logic Analyzer
- bit_bang.py
- swd_sequence_symbols
- TestOLSXfer
- serial.py
- waveform.worker.ts
- test_mso_packed.py
- .start_raw_stream_read
- ChannelRole
- compilerOptions
- analog_uart_decode.py
- TestI2CReadSymbols
- _FakeCanvas
- desktop/package.json
- SDRAM PLL: `SDRAM_PLL`
- _mk_scope_for_capture
- decoders.py
- TestOLSPublicAPI
- hw_api_sweep.py
- measurements.py
- _dig_pkt
- capture.py
- ._process_decoders
- diag_raw_stream_probe.py
- find_spi_device
- jumper_compression_probe.py
- find_edges
- mock_signals.py
- decode_uart
- _make_mock_dev
- test_requested_features.py
- test_roadmap_robustness.py
- DecoderService
- decode_spi
- OLS_SPI_MPSSE
- ConnectionManager
- test_serialization_and_connection_manager_edges
- _ana_header
- gui_decoders.py
- decode_i2c
- _compression_tradeoff_probe.py
- _d2xx_write_pacing_probe.py
- test_analog_decode.py
- TestOLScopeRateLimits
- decode
- virtual_bridge.py
- TestParseI2CReadPayload
- uart_substitution_probe.py
- TestOLSDeviceSPIGenerator
- App.tsx
- test_ols_spi_device.py
- TestOLSDeviceSPICapture
- Fast_Logic_Analyzer_SDRAM1
- can.py
- glitch_filter
- ._build_side_panel
- live_readback_sweep.py
- test_processing_edges.py
- test_new_features.py
- test_mcp_and_serial.py
- decode_modbus
- expand_symbols
- TestRoundTrip
- TestDecodeIntegration
- .transaction
- TestOLSLowLevel
- demux
- core
- host/conftest.py
- parse_response
- test_ack_pad_sweep.py
- test_hardware_helper_and_fallback_branches
- diag_arm_methods.py
- ._auto_connect
- _burst_characterize.py
- debug_basic.py
- debug_i2c_direct.py
- _drop_period_precise.py
- .accel_read_i2c
- ._transaction_raw
- TestOLScopeUpdateBufEstimate
- run_ack_pad_test.ps1
- altera_modular_adc_sequencer
- _burst_vs_transition.py
- diag_capture_cont.py
- preflight_ftdi.py
- _stream_bulk_readback.py
- SPI Packet Protocol
- TestModbusCRC16
- Delta Codec
- OLS Logic Analyzer Hardware Validation Suite
- Test 30: Jumper-pair discovery
- altera_modular_adc_control
- altera_modular_adc_control_fsm
- fiftyfivenm_adcblock_top_wrapper
- altera_modular_adc_control
- altera_modular_adc_control_fsm
- fiftyfivenm_adcblock_top_wrapper
- Internal Architecture
- debug_i2c3.py
- debug_status.py
- diag_arm_fast0.py
- diag_arm_formats.py
- diag_arm_positions.py
- diag_arm_sequence.py
- diag_cont_arm.py
- diag_minimal_prefix.py
- _drop_mechanism.py
- _drop_period_scan.py
- .protocol_trigger_match_pos
- test_ack_pad_direct
- MAX10_ADC_modular_adc_0
- altera_modular_adc_sample_store
- altera_modular_adc_sample_store_ram
- MeasurementContext
- OLS Logic Analyzer for MAX1000
- Software Feature Capability Matrix
- hardware-features.spec.ts
- Test 31: Generator matrix over jumper
- Test 12c2: High-speed analog-only mode
- altera_modular_adc_control_avrg_fifo
- MAX10_ADC
- altera_modular_adc_control_avrg_fifo
- Seed Sweep Results Table
- program_eeprom.py
- diag_arm_minimal.py
- diag_postflash.py
- recover_eeprom.py
- ACK Pad Optimization Workflow
- strategies/__init__.py
- mcp/__init__.py
- mil/__init__.py
- Internal Architecture
- Streaming Latency Root Cause Found
- CMD_GEN_CAPTURE
- CMD_GET_METADATA
- Fast Logic Analyzer SDRAM
- Accelerometer Session Waveform UI
- Capture Controls UI
- Compression Sweep Delta UI
- UI Screenshot: Digital Deep Live 1 MHz
- Test 8: Generator UART functional
- Test 14b: Falling edge trigger on CH0
- Test 34: Codec readback matrix
- Test 34: Codec readback matrix
- Test 13: Rolling capture with UART generator
- Test 36: LIS3DH WHO_AM_I via I2C and SPI
- ncsim_setup.sh
- simulation/submodules/chsel_code_converter_sw_to_hw.v
- simulation/submodules/fiftyfivenm_adcblock_primitive_wrapper.v
- vcsmx_setup.sh
- altera_modular_adc_sequencer_csr.v
- altera_modular_adc_sequencer_ctrl.v
- synthesis/submodules/chsel_code_converter_sw_to_hw.v
- synthesis/submodules/fiftyfivenm_adcblock_primitive_wrapper.v
- ._export_marker_range
- debug_enum.py
- debug_ftdi.py
- debug_info.py
- debug_mode.py
- parse_vcd.py
- .read_stream
- SDRAM Ring Buffer
- Test 3: All packet protocol commands
- Test 30: Jumper-pair discovery + UART loopback
- Timing Optimisation Experiment Log
- ACK Pad Guard Period
- ACK Pad Sweep Results
- Generator Startup Failure RCA
- Protocol Fixture Format
- Backend Requirements
- Compressed Streaming Blocker Analysis
- ADR-001: Capture Mode Strategy Pattern
- ADR-002: Extract Wire Format Module
- MAX1000 Analog Mode Plan
- Hardware Smoke Test 2026-07-20
- Hardware Validation 2026-07-22
- Hardware Validation 2026-07-26
- MAX1000 User Guide
- On-board LIS3DH Accelerometer
- API Layer Wiki
- Capture Manager Wiki
- Capture Strategies Wiki
- Decoder Framework
- Decoder Implementations
- Existing Host Adapter
- Export Formats
- Generator Controller
- Hardware Abstraction
- Machine-In-Loop (MIL)
- Measurements
- Mock Device
- Backend Wiki
- Session Model
- Session Stores
- Triggers
- Waveform Service
- WebSocket & Diagnostics
- Current Implementation Status
- Feature and Coverage Matrix
- API Client Documentation
- App Shell Documentation
- Build & Test Documentation
- Capture Controls Documentation
- Decoder UI Documentation
- Pages Documentation
- Side Panels Documentation
- Frontend Wiki
- WebSocket Integration Documentation
- Signal Generator: `Signal_Gen` + `Bit_Engine`
- Hardware Screenshot Matrix
- Pattern Trigger: `Generic_Pattern_Trigger`
- HDL Build Flow Documentation
- Capture Compressor Documentation
- Capture Engine Documentation
- Core Wrapper Documentation
- Delta Calculator Documentation
- Delta-RLE Compressor Documentation
- Digital RLE Documentation
- FAST Capture Stream Documentation
- HDL Wiki
- Project Wiki Index
- Recent Software Features
- Verification and Change Traceability
- Frontend Entry
- Analog Session Waveform UI
- Analog Spectrum Analysis UI
- Bit Banger Loopback Capture UI
- Bit Banger Loopback Latest UI
- Bit Banger Preview Sweep UI
- CAN/LIN Health Dashboard UI
- Capture Analog Fast UI
- Capture Compression Delta RLE UI
- Capture Compression RLE UI
- Capture Live 50MHz UI
- Capture Live 50MHz Latest UI
- Channel Layout Configuration UI
- Command Palette UI
- UI Screenshot: Compression Sweep 10M Raw
- UI Screenshot: Compression Sweep 50M Raw
- Compression Sweep Results
- UI Screenshot: Compression Sweep Summary
- Capture Page UI Screenshot
- UI Screenshot: Diagnostics Page Latest
- UI Screenshot: Diagnostics Page
- UI Screenshot: Exports Tab
- UI Screenshot: Generator Loopback Capture
- UI Screenshot: Generator Page
- UI Screenshot: Hardware Capture Job
- UI Screenshot: Analog Fast Single 100 kHz
- UI Screenshot: Analog Fast Single 500 kHz
- UI Screenshot: Digital Deep Live 100 kHz
- UI Screenshot: Digital Deep Live 10 kHz
- UI Screenshot: Digital Deep Live 12.5 MHz
- UI Screenshot: Digital Deep Live 14 MHz
- UI Screenshot: Digital Deep Live 20 MHz
- UI Screenshot: Digital Deep Live 2 MHz
- UI Screenshot: Digital Deep Live 500 kHz
- UI Screenshot: Digital Deep Live 50 MHz
- UI Screenshot: Digital Deep Live 5 MHz
- Digital Deep 12.5MHz Screenshot
- Hardware Matrix Digital Deep 14MHz
- UI Screenshot: Digital Deep Single 1 MHz
- Hardware Matrix Digital Deep 200MHz Screenshot
- Hardware Pretrigger Controls Screenshot
- Hardware Validated Analog Fast Live 100kHz
- Screenshot: Hardware Validated Digital Deep Single (20M)
- Screenshot: Hardware Validated Digital Deep Single (200M)
- Screenshot: Hardware Validated Digital Deep Single (500k)
- Screenshot: Hardware Validated Digital Deep Single (5M)
- Screenshot: Hardware Validated Digital Deep Single (50M)
- Screenshot: Hardware Validated Maximum Analog Live (125k)
- Screenshot: Hardware Validated Maximum Analog Single (125k)
- Screenshot: Hardware Validated Mixed Scan Live (125k)
- Screenshot: Hardware Validated Mixed Scan Single (125k)
- Screenshot: Hardware Validated Packed Narrow Live (200M)
- Screenshot: Live Accelerometer Session Waveform
- Screenshot: Live Analog Fast Waveform
- Screenshot: Live Generator Loopback Capture Waveform
- Screenshot: Live Generator Session Waveform
- Screenshot: Live HW Smoke Session Waveform
- Screenshot: Live Maximum Analog Waveform
- Screenshot: Live MIL Session Waveform
- Screenshot: Live Mixed Analog Waveform
- Markers Panel Screenshot
- Measurements Screenshot
- MIL Transaction Screenshot
- MSO Analog UART Live Screenshot
- Raw Inspector Screenshot
- Session Comparison Screenshot
- Session Dashboard Screenshot
- Settings Page Screenshot
- SWD Generator Capture Screenshot
- Trigger Builder Screenshot
- Trigger Decoder Auto Scope Screenshot
- Test 4: Single capture
- Test 5: Fast mode (BRAM) capture
- Test 5b: 200 MHz max-speed capture
- Test 5c: Max-rate continuous ring overrun
- Test 5d: 200 MHz narrow packed digital mode
- Test 6: Continuous capture
- Test 7: Rising edge trigger on CH0
- Hardware Validation Suite Run 3
- Test 10: SPI generator loopback decode
- Test 9: I2C generator loopback decode
- Hardware Validation Suite Run 4
- Test 14: Protocol trigger (UART byte match)
- Test 14c: Abort capture while running
- Test 14d: Digital glitch filter (Schmitt)
- Test 14e: Generator output routing verify
- Test 15: Noise floor
- Test 15b: Crosstalk characterisation
- Test 16: Long-duration stress
- Test 26: Pre-trigger capture
- Test 27: Full-depth SDRAM capture
- Test 28: Back-to-back captures
- Test 29: SPI readout stress during active capture
- Test 33: Repeating UART decodes in SDRAM ring
- Hardware Validation Suite Run 5
- Test 12b: full-width digital capture decode
- Test 12c: Mixed digital + analog mode
- Test 12c2: High-speed analog-only mode
- Test 12c3: Dual analog channel mode
- Test 14: Protocol trigger (UART byte match)
- Test 14b: Falling edge trigger on CH0
- Test 14c: Abort capture while running
- Test 14d: digital glitch filter
- Test 14e: Generator output routing verify
- Test 15: Noise floor
- Test 15b: Crosstalk characterisation
- Test 16: Long-duration stress
- Test 26: Pre-trigger capture
- Test 27: Full-depth SDRAM capture
- Test 28: Back-to-back captures
- Test 29: SPI readout stress
- Test 10: SPI generator loopback decode
- Test 11: Divider accuracy
- Test 12b: Full-width digital capture decode
- Test 12d: Mixed-frame de-interleave integrity
- Test 12e: Mixed -> digital -> mixed back-to-back
- Test 12f: Mixed capture with lossless codec roundtrip
- Test 12g: Analog profile -> digital recovery
- Test 2: SPI handoff and CMD_GET_METADATA
- Test 3: All packet protocol commands
- Test 4: Single capture
- Test 5: Fast mode (BRAM) capture
- Test 5b: 200 MHz max-speed capture
- Test 5c: Max-rate continuous ring overrun
- Test 5d: 200 MHz narrow packed digital mode
- Test 6: Continuous capture (triple buffer)
- Test 7: Rising edge trigger on CH0
- Test 9: I2C generator loopback decode
- CI Workflow
- Clock Transfer Report
- Compilation Run 2 Log
- HDL Compilation Log Run 4
- HDL Compilation Log Run 5
- Compile V2 Output
- Compile V3 Output
- Current Worst Paths Report
- Timing Violation Report (Seed 42)
- HDL - OLS Logic Analyzer FPGA Design
- ACK Pad Testbench Guide
- tb_stream_protocol_timing
- ACK Pad Sweep Error Trace
- FTDI EEPROM Backup
- Host Debug Trace
- Host Software README
- Python Requirements
- Hardware Validation Run 10
- Hardware Validation Run 3
- Hardware Validation Run 4
- Hardware Validation Run 5
- Hardware Validation Run 6
- Hardware Validation Run 7
- Hardware Validation Run 8
- Hardware Validation Run 9
- Hardware Validation Run 1
- Hardware Validation Seed 30
- Data-Rate Status & Optimization Analysis
- ols-maxscope
- Protocol Tuning Plan - ACK Pad Optimization
- Streaming Latency Analysis - 60+ Byte Gap
- Streaming RLE Protocol Plan
- Test 10: SPI generator loopback decode
- Test 11: Divider accuracy
- Test 12c: Mixed digital + analog mode
- Test 13: Rolling capture with UART generator
- Test 14: Protocol trigger (UART byte match)
- Test 14b: Falling edge trigger on CH0
- Test 14c: Abort capture while running
- Test 15: Noise floor
- Test 16: Long-duration stress
- Test 26: Pre-trigger capture
- Test 29: SPI readout stress during active capture
- Test 4: Single capture
- Test 5: Fast mode (BRAM) capture
- Test 5b: 200 MHz max-speed capture
- Test 6: Continuous capture (triple buffer)
- Test 7: Rising edge trigger on CH0
- Test 9: I2C generator loopback decode
- Streaming Latency Investigation - Testbench Results
- MAX1000 Mixed-Signal Analyser — Web Host App (v2)
- OneWireDecoder
- MachineInLoopPage.tsx
- SwdDecoder
- DecoderResult
- main.cjs
- validate_capture_result
- OLS Logic Analyzer — User Guide
- HDL Testbenches
- sweep.py
- ADC Controller: `ADC_Controller`
- LED Controller: `LED_Controller`
- status_ws.py
- Waveform Viewer
- UART Interface: `UART_Interface`
- Hardware and Capture Seam
- Workers
- analog_interface_probe.py
- ManchesterDecoder
- SpiDecoder
- State Management
- RLE Compressor: `rle_compressor`
- Q: Are there any changes we should make based off the graphs?
- measurement_results
- main
- analog_probe_sweep.py
- _rolling_tput_probe.py
- ._sync_ch_vis_ui

## God Nodes (most connected - your core abstractions)
1. `OLSDeviceSPI` - 163 edges
2. `DecodeContext` - 127 edges
3. `WaveformData` - 103 edges
4. `ExistingHostAdapter` - 97 edges
5. `CaptureSettings` - 92 edges
6. `OLScope` - 85 edges
7. `SPIDevice` - 83 edges
8. `DecoderResult` - 73 edges
9. `SettingField` - 65 edges
10. `HardwareError` - 64 edges

## Surprising Connections (you probably didn't know these)
- `Test 13: Rolling capture with UART generator` --semantically_similar_to--> `Test 8: Generator UART functional`  [INFERRED] [semantically similar]
  fullsuite_run.txt → fullsuite_run2.txt
- `Compression Baseline and Jumper-Driven Validation` --semantically_similar_to--> `OLS Logic Analyzer for MAX1000`  [INFERRED] [semantically similar]
  COMPRESSION_BASELINE_NEW.md → README.md
- `Timing Report Summary` --semantically_similar_to--> `Timing Optimisation Experiment Log`  [INFERRED] [semantically similar]
  TIMING_REPORT_SUMMARY.md → TIMING_OPTIMIZATION_EXPERIMENT_LOG.md
- `test_adapter_analog_and_mixed_strategies_decode_wire_frames()` --calls--> `payload_to_wire()`  [INFERRED]
  backend/app/tests/test_existing_host_adapter.py → host/driver/wire_format.py
- `fresh_spi()` --calls--> `OLS`  [INFERRED]
  hdl/hw_test/diag_clean.py → host/driver/ols_spi.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Hardware Validation Suite** — newsuite_run_txt, newsuite_run3_txt, newsuite_run4_txt, newsuite_run5_txt, newsuite_run6_txt, newsuite_run7_txt, newsuite_run8_txt, newsuite_run9_txt, newsuite_seed30_txt [EXTRACTED 1.00]
- **OLS System Architecture** — readme, webapp, software_feature_roadmap, feature_capability_matrix [EXTRACTED 0.90]
- **FPGA Timing Closure Effort** — timing_optimization_experiment_log, timing_report_summary, changelog [EXTRACTED 0.85]
- **Compression Validation Flow** — compression_baseline_new, readme, changelog [EXTRACTED 0.80]
- **Compression Algorithm Comparison** — frontend_test_results_screenshots_compression_sweep_1000000_delta, frontend_test_results_screenshots_compression_sweep_1000000_raw [EXTRACTED 0.95]
- **Compression Sweep Test Results** — frontend_test_results_screenshots_compression_sweep_1000000_rle_png, frontend_test_results_screenshots_compression_sweep_10000000_raw_png, frontend_test_results_screenshots_compression_sweep_50000000_raw_png, frontend_test_results_screenshots_compression_sweep_summary_png [EXTRACTED 1.00]
- **Hardware Matrix Test Screenshots** — frontend_test_results_screenshots_hardware_matrix_analog_fast_single_1_mhz, frontend_test_results_screenshots_hardware_matrix_digital_deep_live_1_mhz [INFERRED 0.90]
- **MSO Capture Data Flow** — hdl_proj_current_worst_paths_seed42_fast_logic_analyzer_sdram1, hdl_proj_current_worst_paths_seed42_u_pack, hdl_proj_current_worst_paths_seed42_ram_block1a0 [EXTRACTED 0.95]
- **Timing Closure & Seed Sweeping** — hdl_proj_current_worst_paths_seed42, hdl_proj_seed_sweep3_results, hdl_proj_seed_sweep_results, hdl_proj_seed_sweep_best_seed [EXTRACTED 1.00]
- **Streaming Latency Investigation Flow** — streaming_latency_analysis_md, testbench_latency_findings_md, bottleneck_found_and_solution_md [EXTRACTED 0.95]
- **Analog and Mixed Mode Tests** — fullsuite_run_txt_test_12c, fullsuite_run_txt_test_12c2, fullsuite_run_txt_test_12c3 [EXTRACTED 1.00]
- **High-Speed Digital Capture Tests** — fullsuite_run2_txt_test_5b, fullsuite_run2_txt_test_5c, fullsuite_run2_txt_test_5d [EXTRACTED 1.00]
- **Generator Loopback and Jumper Tests** — fullsuite_run_txt_test_30, fullsuite_run_txt_test_31, fullsuite_run3_txt_test_9, fullsuite_run3_txt_test_10 [INFERRED 0.90]
- **Logic Analyzer Capture Modes** — test_4, test_5, test_6, test_12c, test_26 [EXTRACTED 0.90]
- **Signal Generator Subsystem** — test_8, test_9, test_10, test_13, test_31 [EXTRACTED 0.90]
- **Data Compression Codecs** — test_34, test_35, codec_rle, codec_delta [EXTRACTED 0.90]
- **Analog Capture Modes** — fullsuite_seed30_test_12c, fullsuite_seed30_test_12c2, fullsuite_seed30_test_12c3 [EXTRACTED 0.95]
- **Codec Performance Matrix** — fullsuite_run5_test_34, fullsuite_run5_test_35 [EXTRACTED 0.90]
- **Hardware Validation Matrix Screenshots** — frontend_test_results_screenshots_hardware_validated_matrix_analog_fast_single_500000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_10000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_100000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_1000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_10000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_12500000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_14000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_2000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_20000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_500000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_5000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_live_50000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_single_100000000 [EXTRACTED 1.00]
- **Hardware Validation Matrix Screenshots** — frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_single_20000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_single_200000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_single_500000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_single_5000000, frontend_test_results_screenshots_hardware_validated_matrix_digital_deep_single_50000000, frontend_test_results_screenshots_hardware_validated_matrix_maximum_analog_live_125000, frontend_test_results_screenshots_hardware_validated_matrix_maximum_analog_single_125000, frontend_test_results_screenshots_hardware_validated_matrix_mixed_scan_live_125000, frontend_test_results_screenshots_hardware_validated_matrix_mixed_scan_single_125000, frontend_test_results_screenshots_hardware_validated_matrix_packed_narrow_live_200000000 [EXTRACTED 1.00]
- **Live Session Waveform Visualizations** — frontend_test_results_screenshots_live_accelerometer_session_waveform, frontend_test_results_screenshots_live_analog_fast_waveform, frontend_test_results_screenshots_live_generator_loopback_capture, frontend_test_results_screenshots_live_generator_session_waveform, frontend_test_results_screenshots_live_hw_smoke_session_waveform, frontend_test_results_screenshots_live_maximum_analog_waveform, frontend_test_results_screenshots_live_mil_session_waveform, frontend_test_results_screenshots_live_mixed_analog_waveform [EXTRACTED 1.00]
- **Worst Case Timing Path #1** — fast_logic_analyzer_sdram_1, analog_packer_u_pack, ols_sdram_top_core [EXTRACTED 1.00]
- **Timing Violation Analysis** — hdl_proj_timing_rpt, hdl_proj_worst_paths, fast_logic_analyzer_sdram1 [EXTRACTED 0.90]

## Communities (518 total, 304 thin omitted)

### Community 0 - "OLSDeviceSPI"
Cohesion: 0.04
Nodes (38): OLSDeviceSPI, Emit optional trace lines for continuous ring debugging., Read a dense 16-bit sample range by absolute sample index. The FPGA block…, Repair the known single-sample readout inversion at 256-sample boundaries., Read nsamples from a completed single-shot SDRAM buffer. Uses batched CS-held…, Yield chunks from the FPGA continuous SDRAM ring by absolute index. This arms…, Yield raw data chunks from the continuous SDRAM ring (caller must configure…, Run continuous SDRAM ring capture while UART generator replays data. (+30 more)

### Community 1 - "hw_validation.py"
Cohesion: 0.09
Nodes (97): samples_to_channels(), _capture_physical_analog_activity(), _channel_transitions(), check(), check_channels_clean(), decode_uart_safe(), _discover_jumper_pair(), _floating_except() (+89 more)

### Community 2 - "ols_spi_device.py"
Cohesion: 0.02
Nodes (51): ch0_runs(), main(), Robust SDRAM page-boundary readback check. Unlike verify_block_boundary.py…, Find where single-shot capture stops filling SDRAM. Small/slow captures return…, Where does capture() fail? Print arm result + status transitions.…, Test GEN_BAUD via _xfer_cmd., Test capture after ARM fix., Directly check if gen starts by reading status after GEN_STRT. (+43 more)

### Community 3 - "registry.py"
Cohesion: 0.11
Nodes (16): Decoder, ABC, Any, Plugin-style protocol decoder framework. Decoders consume immutable…, I2C decoder — structured-annotation port of the proven mid-plateau sampling…, I²S and related three-wire audio serial decoder., Lightweight JTAG TAP shift decoder for TMS/TDI/TDO/TCK captures., Manchester and differential-Manchester decoder. This is intentionally a host-… (+8 more)

### Community 4 - "ExistingHostAdapter"
Cohesion: 0.09
Nodes (62): CaptureSettings, GeneratorConfig, ExistingHostAdapter, HardwareDevice implementation backed by host/driver/ols_spi_device.py., test_mock_rs485_generator_outputs_complementary_a_b(), FakeHostDevice, FakePkt, FakeSpi (+54 more)

### Community 5 - "hardware/base.py"
Cohesion: 0.09
Nodes (28): HardwareDevice, ABC, DeviceMetadata, Hardware abstraction. Every backend (real FPGA via the existing host driver,…, Interface owned by the backend server; browsers never touch hardware., CaptureProgress, DebugInfo, DeviceCapabilities (+20 more)

### Community 6 - "CaptureResult"
Cohesion: 0.06
Nodes (44): adc_to_volts(), CaptureResult, Canonical result returned by every hardware adapter. The result is the seam…, Adapter wrapping the existing, known-working OLSDeviceSPI host driver,…, AnalogAllCaptureStrategy, CaptureSettings, Event, ProgressCb (+36 more)

### Community 7 - "client.ts"
Cohesion: 0.06
Nodes (59): ApiError, clientId(), del(), downloadDebugBundle(), downloadExport(), get(), patch(), post() (+51 more)

### Community 8 - "OLS"
Cohesion: 0.06
Nodes (24): fresh_spi(), OLS, Open FTDI Channel B, enter MPSSE mode, configure SPI., Import ftd2xx on first hardware use; raise a clear error if missing., Full-duplex SPI: batched write + 0x87, return read bytes. Sends GPIO CS-low +…, 6-byte command xfer. Returns [preamble, b0, b1, b2, b3]. Format: [0x11, cmd,…, Bulk write: send bytes via 0x11 + 0x87. Returns response bytes., Read nbytes using 0x31 + 0x11 (NOP) so MOSI stays driven high, avoiding 0x00… (+16 more)

### Community 9 - "get_session_or_404"
Cohesion: 0.07
Nodes (70): all_decoder_events(), Merged events from all enabled decoders for the visible window., get_session_or_404(), get_waveform_or_404(), Session, delete_measurement(), patch_measurement(), delete (+62 more)

### Community 10 - "DecodeContext"
Cohesion: 0.13
Nodes (15): DecodeContext, Event, ProgressCb, Runtime services handed to a decoder: sample window, cancellation, progress…, Channel bits (uint8 0/1) for the role, sliced to the region., Build an event. start/end are region-relative sample offsets and get translated…, _bit_at(), _differential_bits() (+7 more)

### Community 11 - "ModbusDecoder"
Cohesion: 0.14
Nodes (9): ModbusDecoder, Any, _fmt_freq(), PwmDecoder, Any, PWM / frequency decoder: per-cycle frequency, duty, pulse widths., test_modbus_decoder_empty_runt_exception_and_bad_crc_frames(), test_pwm_decoder_warning_falling_reference_and_truncation() (+1 more)

### Community 12 - "encode"
Cohesion: 0.14
Nodes (30): encode(), _hold(), i2c_symbols(), lin_symbols(), manchester_symbols(), nrz_symbols(), onewire_symbols(), ps2_symbols() (+22 more)

### Community 13 - "_make_scope"
Cohesion: 0.06
Nodes (10): _make_scope(), 16 Dig + 2 Ana' maps to MODE_MIXED., TestChannelVisibility, TestOLScopeExports, TestOLScopeGenShowProtoFields, TestOLScopeGetters, TestOLScopeProcessDecoders, TestOLScopeTimeChanged (+2 more)

### Community 14 - "analog_frame_stride"
Cohesion: 0.04
Nodes (33): compress_mixed_group(), compress_mixed_stream(), decompress_mixed_group(), decompress_mixed_stream(), Compress a stream of mixed payload frames in 16-frame groups., Read raw (uncompressed) capture blocks while readback compression is globally…, Decompress one mixed compression group. Returns ``(payload_bytes,…, Read a mixed-mode SDRAM word range via the lossless mixed codec. (+25 more)

### Community 15 - "generator.py"
Cohesion: 0.16
Nodes (22): bitbang_presets(), generator_capabilities(), generator_configure(), generator_preview(), generator_self_test(), generator_send(), generator_start(), generator_status() (+14 more)

### Community 16 - "spi_protocol.py"
Cohesion: 0.05
Nodes (28): chunked_transaction_raw(), read_block_chunked(), main(), Find the REAL max deep (SDRAM-streamed) sample rate. Deep capture writes one…, Capture-based UART gen diagnostic., Quick gen_busy diagnostic via packet protocol., I2C generator capture diagnostic., main() (+20 more)

### Community 17 - "OLScope"
Cohesion: 0.08
Nodes (10): analog_wire_stride(), OLScope, Abort the running capture and return partial data., Parse preset rate string, clamp to max. Returns rate in Hz., Main application: combines device control, waveform view, and protocol tools., Return the maximum allowed rate based on mode and capture type., Regenerate buffer combobox values with MB sizes based on current rate+mode., Show/hide the analog channel note based on current mode. (+2 more)

### Community 18 - "main.py"
Cohesion: 0.04
Nodes (80): api_route, client_id_header(), hw_error(), Shared API dependencies: error mapping, session lookup, control lock., Hardware-mutating endpoints require the control lock (auto-acquired when free)., require_control(), connect(), ConnectRequest (+72 more)

### Community 19 - "TestCompressionHelpers"
Cohesion: 0.10
Nodes (16): measure_case(), Hardware compression-ratio matrix for the digital readback RLE codec., TestCompressionHelpers, decompress_block_readback_stream(), decompress_delta_block(), _decompress_delta_blocks_fast(), decompress_delta_stream(), decompress_rle_stream() (+8 more)

### Community 20 - "CaptureManager"
Cohesion: 0.07
Nodes (18): CaptureManager, ControlLock, CaptureSettings, DecoderInstance, Session, Queue a capture for unattended clients and return immediately., One client controls the hardware at a time; others are read-only viewers until…, Small-ish chunks make rolling mode feel alive while the configured num_samples… (+10 more)

### Community 21 - "server.py"
Cohesion: 0.06
Nodes (58): acquire_analyser_control(), add_decoder(), analyser_status(), capture(), _claim_control(), connect_analyser(), create_virtual_com_pair(), disconnect_analyser() (+50 more)

### Community 22 - "lod.py"
Cohesion: 0.42
Nodes (7): AnalogLodLevel, build_analog_levels(), build_digital_levels(), DigitalLodLevel, _pad_to(), ndarray, Level-of-detail pyramid for fast zoomed-out waveform rendering. For each level…

### Community 23 - "MSO Capture Pipeline: `mso_capture`"
Cohesion: 0.06
Nodes (31): Analog Packer: `analog_packer`, bit15 = 0 (Analog word), Dependencies, Frame Structure, Host-side Decoder, Known Limitations, Output Word Format, Purpose (+23 more)

### Community 24 - "mil/service.py"
Cohesion: 0.11
Nodes (35): MilCaptureConfig, MilConfig, MilLoadRequest, MilNode, MilPresetSummary, MilRegister, MilRuntimeStatus, MilTiming (+27 more)

### Community 25 - "SpiPort"
Cohesion: 0.10
Nodes (13): pyftdi-compatible OLS SPI driver using ftd2xx bitbang. Provides the same…, Open and configure the FTDI device for SPI bitbang., Return a SpiPort for the given CS line., Single SPI port, mirrors pyftdi.spi.SpiPort API., SPI write (MOSI), discard MISO., SPI read (MISO), send 0x00 on MOSI., Full-duplex exchange., pyftdi-compatible SPI controller for Arrow USB Programmer2. Uses ftd2xx bitbang… (+5 more)

### Community 26 - "WaveformData"
Cohesion: 0.06
Nodes (63): live_accel_session(), Create a live LIS3DH session on the attached board. This is used by the…, import_session(), Import a JSON, CSV, or VCD session., Capture orchestration: owns the hardware device, the single-control lock,…, Canonical in-memory waveform representation. Raw data is immutable once…, Immutable capture payload for one session., WaveformData (+55 more)

### Community 27 - "measurements/base.py"
Cohesion: 0.21
Nodes (14): MeasurementType, Measurement framework: typed measurement functions over a sample region., register(), _events_in_region(), m_bus_utilisation(), m_byte_rate(), m_error_count(), m_inter_packet() (+6 more)

### Community 28 - "TriggerConfig"
Cohesion: 0.19
Nodes (21): TriggerConfig, test_software_trigger_search(), test_generic_pattern_refinement_handles_partial_final_lane_group(), test_generic_pattern_refines_multi_channel_capture(), test_protocol_and_sequence_trigger_search_on_decoder_events(), test_raw_trigger_occurrence_selects_nth_match(), test_software_trigger_no_match_paths(), test_software_trigger_search_covers_patterns_buses_pulses_and_unknowns() (+13 more)

### Community 30 - "test_generator_controller.py"
Cohesion: 0.18
Nodes (18): _compare_uart_loopback(), loopback_self_test(), normalized_loopback_samples(), CaptureSettings, Send a pattern through the generator while capturing, decode the capture, and…, validate_generator_payload(), parametrize, test_generator_compare_exact_and_length_mismatch_and_non_uart_sizing() (+10 more)

### Community 31 - "useApp"
Cohesion: 0.14
Nodes (26): api, DecoderEvent, DecoderTable(), formatField(), CapturePage(), Tab, TABS, AnalogPanel() (+18 more)

### Community 32 - "measurements/digital.py"
Cohesion: 0.13
Nodes (27): _bits(), m_bus_value_at(), m_channel_skew(), m_duty(), m_edges(), m_frequency(), m_glitch_count(), m_high_time() (+19 more)

### Community 33 - "test_api.py"
Cohesion: 0.07
Nodes (14): client(), parse_binary(), fixture, API smoke + end-to-end flow tests against the mock device., test_analog_capture_and_measurements(), test_analog_only_capture_has_no_digital_channels(), test_api_management_error_and_filter_paths(), test_capture_flow_uart() (+6 more)

### Community 34 - "mockApp.ts"
Cohesion: 0.10
Nodes (30): listLiveSessions(), openLiveSession(), shots, analogPinMap(), buildAccelBuffer(), buildAnalogSeries(), buildDigitalSeries(), buildMixedAnalogBuffer() (+22 more)

### Community 35 - "greybox/MAX10_ADC.v"
Cohesion: 0.08
Nodes (35): dffeas, fiftyfivenm_lcell_comb, fiftyfivenm_ram_block, MAX10_ADC, MAX10_ADC_a_dpfifo_3o41, MAX10_ADC_a_fefifo_c6e, MAX10_ADC_altera_modular_adc_control, MAX10_ADC_altera_modular_adc_control_avrg_fifo (+27 more)

### Community 36 - "test_core.py"
Cohesion: 0.13
Nodes (32): modbus_crc16(), get(), Idle-high UART TX line containing `data` starting at start_sample., uart_signal(), make_wf(), Core unit tests: sample format, LOD, decoders, measurements, exports., SWCLK toggles low->high->low once per bit; SWDIO holds its value across the…, _session_with_wf() (+24 more)

### Community 37 - "CaptureControls.tsx"
Cohesion: 0.11
Nodes (33): Acquisition, acquisitionForMode(), ALL_DIGITAL, ANALOG_ALL_RATES, ANALOG_DEPTHS, ANALOG_FAST_RATES, ANALOG_MODES, ANALOG_ONLY_MODES (+25 more)

### Community 38 - "exports.py"
Cohesion: 0.08
Nodes (52): CsvOptions, export_csv(), export_json(), export_npz(), export_pdf(), export_pulseview(), export_report(), export_vcd() (+44 more)

### Community 39 - "HardwareError"
Cohesion: 0.08
Nodes (19): HardwareError, CaptureSettings, DeviceCapabilities, Event, Exception, GeneratorConfig, ProgressCb, Return a list of {'level','message'} validation findings. (+11 more)

### Community 40 - "properties"
Cohesion: 0.06
Nodes (31): type, type, additionalProperties, type, items, type, items, type (+23 more)

### Community 42 - "WaveformDisplay"
Cohesion: 0.13
Nodes (3): Scrollable/zoomable digital waveform viewer with markers and measurement., WaveformDisplay, TestWaveformDisplay

### Community 43 - "SPIDevice"
Cohesion: 0.12
Nodes (6): Low-level SPI device wrapper using packet protocol., SPIDevice, TestSPIDeviceStatusMetadata, FakeChunkSPI, Fake transport implementing the chunked RLE stream primitive. Mirrors the…, TestSPIPacketProtocol

### Community 44 - "_wf"
Cohesion: 0.08
Nodes (39): clamp_window(), ndarray, Raw sample window slicing. Kept separate from waveform_store so the wire…, Per-channel value at one sample index., raw_analog_window(), raw_derived_window(), raw_digital_window(), value_at() (+31 more)

### Community 45 - "frontend/package.json"
Cohesion: 0.06
Nodes (30): dependencies, react, react-dom, zustand, devDependencies, @playwright/test, @types/react, @types/react-dom (+22 more)

### Community 46 - "MockDevice"
Cohesion: 0.09
Nodes (17): MockDevice, CaptureSettings, DeviceMetadata, Event, GeneratorConfig, ProgressCb, Loopback: render the configured generator output into the capture., test_mock_device_capture() (+9 more)

### Community 47 - ".capture"
Cohesion: 0.09
Nodes (21): _adapt_driver_progress(), CaptureSettings, Event, ProgressCb, Return the strategy handling *settings.mode*, or None if no match., Single-shot deep digital must not use the continuous ring path. Hardware…, Translate the host driver's raw-buffer callback to the backend contract. The…, Atomic generator+capture via the proven CMD_GEN_CAPTURE path. (+13 more)

### Community 48 - "test_hw_validation_helpers.py"
Cohesion: 0.07
Nodes (8): TestCheck, TestDecodeI2CBest, TestDecodeUARTSafe, TestFloatingChannelActivity, TestLog, TestPrintHeader, TestPrintProgress, TestSaveResult

### Community 49 - "renderer.ts"
Cohesion: 0.16
Nodes (23): ChannelInfo, Minimap(), baseRowHeight(), buildLayout(), COLORS, digitalBit(), drawAnalogRow(), drawAnnotations() (+15 more)

### Community 50 - "list_ftdi_devices"
Cohesion: 0.13
Nodes (19): debugger_status(), ftdi_devices(), List OS-visible COM/tty ports without opening or changing them., Inspect FTDI D2XX endpoints, COM assignment, and channel hints., Combine analyser status with COM-port, FTDI, and active-device debug info., serial_ports(), Serial-port and FTDI debugger utilities., _ftdi_channel_hint() (+11 more)

### Community 51 - "ols_spi.py"
Cohesion: 0.08
Nodes (6): Check raw sample alignment., Debug I2C capture with stride=1 to match FPGA 1-byte-per-sample output., Test UART gen + capture with stride=1 to verify gen+mux+readback work., preamble(), raw_xfer(), OLS Logic Analyzer - SPI Host Library Fixed MPSSE driver: batched writes, 0x87,…

### Community 52 - "measurements/analogue.py"
Cohesion: 0.16
Nodes (24): _levels(), m_crest(), m_duty(), m_fall_time(), m_frequency(), m_max(), m_mean(), m_min() (+16 more)

### Community 53 - "MAX1000 Logic Analyzer"
Cohesion: 0.08
Nodes (25): Compression Algorithms, UI Screenshot: Compression Sweep 1M RLE, UI Screenshot: Decoder Builder, UI Screenshot: Device Page, UI Screenshot: Eye Diagram, UI Screenshot: Generator Page Latest, UI Screenshot: Analog Fast Single 1 MHz, UI Screenshot: Analog Fast Single 200 kHz (+17 more)

### Community 54 - "bit_bang.py"
Cohesion: 0.11
Nodes (18): pack_symbols(), Protocol encoders for the FPGA Bit_Engine (generic 2-bit symbol shifter). The…, One SWCLK cycle carrying SWDIO=d: clock-low then clock-high symbol., 8-bit SWD request: start, APnDP, RnW, A[2:3], parity, stop, park. addr is the…, Request byte + turnaround + 3 released ACK clocks (target drives)., Pack a list of 2-bit symbols into generator FIFO bytes (4 per byte). A final…, One SWD write packet (host drives data phase regardless of ACK)., One SWD read packet; data+parity clocks are released for the target. (+10 more)

### Community 55 - "swd_sequence_symbols"
Cohesion: 0.15
Nodes (17): decode_swd(), Decode ARM SWD from captured SWCLK/SWDIO channels. Returns a list of event…, max_swd_ops(), Line reset: >=50 clocks with SWDIO high, then idle clocks low., JTAG-to-SWD switch: reset, 0xE79E LSB-first, reset, idle., How many read/write packets fit in one generator burst., Compose a full SWD burst from a list of operations. ops -- iterable of ('w',…, swd_jtag_to_swd_symbols() (+9 more)

### Community 57 - "serial.py"
Cohesion: 0.13
Nodes (18): ftdi_devices(), BaseModel, get, post, Serial-port and debugger utility endpoints., serial_layout(), serial_ports(), virtual_com_pair() (+10 more)

### Community 58 - "waveform.worker.ts"
Cohesion: 0.15
Nodes (11): parseWaveformPayload(), WaveformHeader, WaveformPayload, FetchOverviewRequest, FetchWindowRequest, WorkerError, WorkerRequest, WorkerResult (+3 more)

### Community 59 - "test_mso_packed.py"
Cohesion: 0.11
Nodes (21): decode_analog_words(), decode_digital_words(), decode_packed_stream(), Host-side decoder for the parallel bit-packing capture mode. The FPGA…, Reconstruct the digital timeline from the bit15=1 sub-stream. Returns (words,…, Split a packed capture into its analog and digital sub-streams and decode.…, Yield 16-bit little-endian words from a byte stream., Interpret the low `bits` of v as a two's-complement signed value. (+13 more)

### Community 60 - ".start_raw_stream_read"
Cohesion: 0.10
Nodes (11): Safe ack_pad in SPI bytes for the current SPI clock rate. FPGA pipeline from…, Decode little-endian (count, value) uint16 pairs to raw samples, skipping…, Start streaming from absolute sample index. Legacy two-step helper. Prefer…, Compatibility wrapper for raw sample streaming., Start a true raw sample stream and read it under one CS-held transaction.…, Clock only the bytes needed for a raw stream: enough to parse the ack, then…, Locate a ST_STREAM_ACTIVE ack in ``buf``; return (producer, oldest, end_offset)…, Start an RLE-compressed stream and return decoded raw sample bytes. The FPGA… (+3 more)

### Community 61 - "ChannelRole"
Cohesion: 0.09
Nodes (13): ChannelRole, I2sDecoder, Any, Ps2Decoder, Any, Any, QuadratureDecoder, Any (+5 more)

### Community 62 - "compilerOptions"
Cohesion: 0.09
Nodes (21): compilerOptions, allowImportingTsExtensions, isolatedModules, jsx, lib, module, moduleResolution, noEmit (+13 more)

### Community 63 - "analog_uart_decode.py"
Cohesion: 0.21
Nodes (21): analog_bits_from_raw(), banner(), baud_sweep(), best_uart_decode(), capture_uart_analog(), clear_generator_state(), correlation_at_spi_rate(), digitise() (+13 more)

### Community 64 - "TestI2CReadSymbols"
Cohesion: 0.13
Nodes (13): i2c_read_symbols(), i2c_symbols(), max_i2c_read_bytes(), Max read_len that fits the FIFO given a write frame of write_len bytes., I2C master-write-then-read symbols. write_frame -- bytes to send before the…, Tests for bit_bang.i2c_read_symbols and max_i2c_read_bytes., A read transaction should produce two START events (initial + repeated) when…, The last byte's ACK slot should have SDA released (high). (+5 more)

### Community 66 - "desktop/package.json"
Cohesion: 0.07
Nodes (26): build, appId, asar, extraResources, files, portable, productName, win (+18 more)

### Community 67 - "SDRAM PLL: `SDRAM_PLL`"
Cohesion: 0.07
Nodes (25): Burst Handling, Controller Architecture, Dependencies, Key Features, Key Implementation Details, Known Limitations, Open-Page Policy, Producer-Done Completion (+17 more)

### Community 68 - "_mk_scope_for_capture"
Cohesion: 0.11
Nodes (11): _mk_scope_for_capture(), _MockVar, Simple get/set variable mock (replaces tk.BooleanVar/StringVar)., Build a minimal scope state for testing _capture paths., Rolling capture should disable fast mode., Single digital capture reaches thread creation., Rolling digital capture reaches thread creation., Single 2-analog capture reaches thread creation. (+3 more)

### Community 69 - "decoders.py"
Cohesion: 0.16
Nodes (21): add_decoder(), cancel_decoder(), decoder_annotations(), decoder_table(), DecoderCreate, DecoderPatch, DecoderRunRequest, delete_decoder() (+13 more)

### Community 71 - "hw_api_sweep.py"
Cohesion: 0.25
Nodes (17): analog_capture(), channel_counts(), digital_after_mixed_capture(), digital_capture(), i2c_send_capture(), markers(), maximum_analog_capture(), measurements() (+9 more)

### Community 72 - "measurements.py"
Cohesion: 0.19
Nodes (17): add_measurement(), _compute(), _cursor_samples(), MeasurementCreate, MeasurementPatch, BaseModel, post, Measurement endpoints: types, per-session instances, results. (+9 more)

### Community 73 - "_dig_pkt"
Cohesion: 0.14
Nodes (11): _dig_pkt(), Two packets with same value extend the run (511-dwell saturation)., Overflow drops a segment; decoder should not crash and later packets still…, Last in-progress run not emitted; decoder pads., Slice with zero packets gets padded with zeros., Build a digital RLE packet word (bit15=1)., Digital RLE packet decoding., No packets → all channels idle (zeros) for full sample count. (+3 more)

### Community 74 - "capture.py"
Cohesion: 0.21
Nodes (16): arm_capture(), capture_state(), CaptureRequest, disarm_capture(), get_capture_job(), mock_scenarios(), BaseModel, CaptureSettings (+8 more)

### Community 75 - "._process_decoders"
Cohesion: 0.11
Nodes (8): Grey out Send+Capture during rolling; update Send label., Render partial waveform â€” throttled to ~5fps, with visual loading bar., Load captured data into the waveform view., Build filtered + decoded channels, arranged under source channels. Order: CH0,…, Atomic generator capture via CMD_GEN_CAPTURE hardware FSM., Read LIS3DH register(s) via I2C and display result., Update the export tab's captured data size estimate., Read UI state into self.decoder_slots + filter config, rebuild channels.

### Community 76 - "diag_raw_stream_probe.py"
Cohesion: 0.18
Nodes (16): check(), CheckFailed, main(), ProbeError, Exception, Diagnostic probe for CMD_START_RAW_STREAM — validates the raw-stream path end…, 100 sequential raw-stream reads; verify monotonic producer index., Verify data offset aligns to 2-byte sample boundary in raw buffer. (+8 more)

### Community 77 - "find_spi_device"
Cohesion: 0.17
Nodes (10): configure_signal(), main(), Compare raw/delta_rle digital live-readback throughput on hardware. Examples:…, run_case(), main(), patched(), Live hardware probe for separating readback transport and decode cost. This…, run_case() (+2 more)

### Community 78 - "jumper_compression_probe.py"
Cohesion: 0.20
Nodes (14): BenchmarkRow, _capture_stimulus(), _capture_words(), _delta_payload_bytes(), main(), _make_alternating_symbols(), _make_i2c_frame(), _make_idle_symbols() (+6 more)

### Community 79 - "find_edges"
Cohesion: 0.18
Nodes (12): find_edges(), Sample indices where a transition lands (index of the first new-value sample).…, Any, autobaud_estimate(), _bit_at(), Any, ndarray, UART decoder — structured-annotation rewrite of the proven algorithm in… (+4 more)

### Community 80 - "mock_signals.py"
Cohesion: 0.17
Nodes (17): analog_square(), glitchy_signal(), i2c_signal(), ndarray, ramp_wave(), Synthetic signal builders for the mock device and decoder tests. All builders…, Returns (sclk, mosi, miso, cs)., Bit sequence (idle-high) for UART bytes: start(0), LSB-first data, optional… (+9 more)

### Community 81 - "decode_uart"
Cohesion: 0.26
Nodes (4): decode_uart(), make_fractional_uart_signal(), make_uart_signal(), TestDecodeUART

### Community 82 - "_make_mock_dev"
Cohesion: 0.32
Nodes (3): _make_mock_dev(), patch, TestOLS_SPI_MPSSE

### Community 83 - "test_requested_features.py"
Cohesion: 0.12
Nodes (10): DigitalCaptureStrategy, CaptureSettings, Event, ProgressCb, Single-shot and rolling general-purpose digital capture., _PreTriggerDevice, Focused coverage for the regression, automation, and bus-health additions., test_capture_job_is_queued_and_polled_on_mock_device() (+2 more)

### Community 84 - "test_roadmap_robustness.py"
Cohesion: 0.12
Nodes (18): measurement_types(), get, list_decoders(), preset_symbols(), preview(), Any, Validation and expansion helpers for the generic two-output Bit Banger., Return deterministic two-output symbols for a named exerciser preset. (+10 more)

### Community 85 - "DecoderService"
Cohesion: 0.15
Nodes (12): DecodeCancelled, Exception, DecoderService, DecoderInstance, Session, Events overlapping *[start, end)* for annotation rendering., Return decoders in dependency order: consumers after their source. Uses the…, Manages decoder run lifecycle for sessions. Handles dependency ordering… (+4 more)

### Community 86 - "decode_spi"
Cohesion: 0.33
Nodes (4): decode_spi(), Decode SPI (CPOL=0/CPHA=0) from a data line and SCLK. Each bit is sampled at…, make_spi_signal(), TestDecodeSPI

### Community 87 - "OLS_SPI_MPSSE"
Cohesion: 0.25
Nodes (3): OLS_SPI_MPSSE, Full-duplex SPI. Returns read_len bytes (default len(data))., CMD_METADATA returns 18 bytes.

### Community 88 - "ConnectionManager"
Cohesion: 0.18
Nodes (6): AbstractEventLoop, test_websocket_manager_connect_broadcast_dead_clients_and_publish(), ConnectionManager, WebSocket, Publish from any thread; no-op if the loop isn't running yet., Publish from async context (fire-and-forget).

### Community 89 - "test_serialization_and_connection_manager_edges"
Cohesion: 0.25
Nodes (13): test_digital_filters_cover_short_empty_and_unknown_inputs(), test_serialization_and_connection_manager_edges(), apply_filter(), debounce(), glitch_suppress(), majority3(), min_pulse_filter(), ndarray (+5 more)

### Community 90 - "_ana_header"
Cohesion: 0.18
Nodes (9): _ana_header(), Analog packed block decoding., W=0 block: header + 4 anchors, no payload. 16 interleaved samples → 4 per…, W=1 block: alternating 0/-1 deltas. Verify reconstruction., Partial analog block with <4 anchors emits received anchors., Two consecutive flat analog blocks produce 8 samples per channel., No analog words → empty dict., Build an analog block header word (bit15=0, bit10=1). (+1 more)

### Community 91 - "gui_decoders.py"
Cohesion: 0.12
Nodes (14): parse_spi_read_payload(), Protocol decoders for OLS MaxScope â€” pure functions, no tkinter dependency., Sample SWDIO at the middle of every SWCLK-high plateau. Returns (bits,…, Extract the payload bytes from a decoded SPI read transaction. The sensor…, _swd_sample_bits(), HW test: sweep gen_tx_pin across all LA channels via capture_with_gen()., channel_transitions(), Probe external pin routing by sweeping generator output across pin indexes.… (+6 more)

### Community 92 - "decode_i2c"
Cohesion: 0.31
Nodes (5): decode_i2c(), Decode I2C from SCL/SDA logic channels. Robust against sub-bit SDA glitches…, decode_i2c_best(), make_i2c_signal(), TestDecodeI2C

### Community 93 - "_compression_tradeoff_probe.py"
Cohesion: 0.24
Nodes (13): delta_payload_bytes(), emit_row(), main(), pat_all_idle(), pat_alternating(), pat_four_runs(), pat_incompressible(), pat_one_then_idle() (+5 more)

### Community 94 - "_d2xx_write_pacing_probe.py"
Cohesion: 0.25
Nodes (13): main(), _mbps(), D2XX write-pacing diagnosis probe. Goal: confirm WHERE the ~0.7 MB/s (~310…, Real deep-capture readout: read_capture_block() in a tight loop. Arms and…, One big CS-held NOP read (streaming path), minimal per-block cost., Raw USB bulk-OUT throughput: GPIO-set commands drain ~instantly., Clock n bytes out MOSI at the SPI clock; FIFO drains at the WIRE rate. 0x11 is…, Decompose a CS-held read into write-half vs read-drain-half. Mirrors… (+5 more)

### Community 95 - "test_analog_decode.py"
Cohesion: 0.20
Nodes (9): _pack_pair(), test_decode_full_mixed_frame(), test_decode_maximum_analog_frame_from_dense_wire(), test_decode_mixed_frame_from_dense_wire(), test_stride_analog_only(), test_stride_digital(), test_stride_mixed(), test_wire_to_payload_is_identity_for_even_maximum_analog_frames() (+1 more)

### Community 96 - "TestOLScopeRateLimits"
Cohesion: 0.15
Nodes (8): parametrize, Every rate preset parses to the correct Hz value., 200 MHz accepted when device sys_clk supports it., _fmt_rate followed by _apply_rate returns same value., Rolling 16 Digital clamps to the live-view ceiling., Rolling 2-ana clamps using the 6-byte padded wire stride., Rolling 2-ana compressed clamps using the 6-byte wire stride., TestOLScopeRateLimits

### Community 97 - "decode"
Cohesion: 0.26
Nodes (11): decode(), decode_analog(), decode_digital(), ndarray, Decode the MSO capture-side packed stream (mso_stream_mux output). Words are…, Full packed decode: demux, then decode digital and analog. Args: words: 1-D…, Sign-extend a `bits`-wide value to a signed int., Decode digital RLE packets into a 16xN uint8 array. Each packet reports a… (+3 more)

### Community 98 - "virtual_bridge.py"
Cohesion: 0.20
Nodes (9): _parse_setupc_list(), Any, Software virtual-COM and SWD bridge support. The MAX1000's two FTDI interfaces…, Own one optional COM/TCP bridge for this backend process., _run_setupc(), _setupc_path(), _valid_com_name(), VirtualComManager (+1 more)

### Community 99 - "TestParseI2CReadPayload"
Cohesion: 0.21
Nodes (8): parse_i2c_read_payload(), Extract the read-phase payload bytes from a decoded I2C transaction. A full I2C…, Tests for gui_decoders.parse_i2c_read_payload., Write-only transaction (no repeated START) returns all DATA bytes., Full read: skip dev_r byte, return remaining data., 2-byte read returns both bytes after dev_r., Slave-ACK case: STOP appears before repeated START., TestParseI2CReadPayload

### Community 100 - "uart_substitution_probe.py"
Cohesion: 0.31
Nodes (12): best_alignment(), channel_bits(), lfsr_bytes(), main(), mismatch_runs(), nearest_source_delta(), ndarray, Probe UART generator captures for sample substitution signatures. This drives a… (+4 more)

### Community 102 - "App.tsx"
Cohesion: 0.26
Nodes (4): WsMessage, Handler, ReconnectingSocket, App()

### Community 103 - "test_ols_spi_device.py"
Cohesion: 0.10
Nodes (4): TestOLSDeviceSPICaptureWithGen, TestOLSDeviceSPII2C, TestOLSDeviceSPII2CCapture, TestOLSDeviceSPIModbus

### Community 105 - "Fast_Logic_Analyzer_SDRAM1"
Cohesion: 0.18
Nodes (11): analog_packer:u_pack, Fast_Logic_Analyzer_SDRAM1, Fast_Logic_Analyzer_SDRAM_1, Timing Report, Worst Paths Report, ACK Pad Analysis, mso_capture:MSO_CAP, OLS_Interface1 (+3 more)

### Community 106 - "can.py"
Cohesion: 0.24
Nodes (5): can_crc15(), CanDecoder, Any, Host-side classical CAN decoder for a captured CAN-RX logic level., test_can_decoder_standard_data_frame()

### Community 109 - "live_readback_sweep.py"
Cohesion: 0.31
Nodes (9): main(), Run one live readback throughput case and print JSON., configure_signal(), main(), parse_int_list(), print_case(), Sweep live digital readback throughput across modes, rates, chunks, and…, run_case() (+1 more)

### Community 110 - "test_processing_edges.py"
Cohesion: 0.06
Nodes (49): payload_to_digital(), ndarray, Channel bits as uint8 0/1 (a view-derived copy; raw stays packed)., Resolve a digital, derived, or thresholded analog channel to bits., Collapse the existing host 32-bit wire words (payload in low 16 bits) to a…, Dense 2-byte digital frames -> packed uint16 array., wire_words_to_digital(), _glitch_filter() (+41 more)

### Community 111 - "test_new_features.py"
Cohesion: 0.10
Nodes (20): SettingField, hdlc_crc16(), HdlcDecoder, Any, Host-side HDLC/PPP decoder with flag detection, bit unstuffing and CRC., JtagDecoder, lin_checksum(), lin_pid() (+12 more)

### Community 112 - "test_mcp_and_serial.py"
Cohesion: 0.22
Nodes (6): Explain fixed FTDI roles and whether extra COM interfaces are possible., serial_interface_layout(), MCP mounting and fixed FTDI/JTAG serial-layout checks., test_serial_layout_preserves_jtag_and_mpsse_roles(), test_virtual_com_setup_output_is_parsed_and_pair_creation_is_host_only(), test_virtual_tcp_bridge_answers_ping_and_status()

### Community 113 - "decode_modbus"
Cohesion: 0.31
Nodes (5): decode_modbus(), modbus_crc16(), cli_mode(), Command-line interface for automated capture and testing (SPI only)., TestDecodeModbus

### Community 114 - "expand_symbols"
Cohesion: 0.18
Nodes (4): expand_symbols(), Expand either ``symbols`` or a list of scripted symbol steps. Script steps are…, DeviceMetadata, Leave a newly opened FPGA connection in a known idle state.

### Community 115 - "TestRoundTrip"
Cohesion: 0.25
Nodes (6): ndarray, End-to-end encode→decode round-trip for known patterns., Encode per-channel data into digital RLE packets. Args: channel_data: (16, N)…, Encode known digital pattern, decode, compare., Flat analog block (W=0) round-trips correctly., TestRoundTrip

### Community 116 - "TestDecodeIntegration"
Cohesion: 0.22
Nodes (5): Full decode(path) integration tests., Only digital RLE packets in the stream., Only analog block words in the stream., Interleaved digital and analog in the same word stream., TestDecodeIntegration

### Community 118 - "TestOLSLowLevel"
Cohesion: 0.10
Nodes (4): TestOLSChainedRead, TestOLSConvenience, TestOLSInit, TestOLSLowLevel

### Community 119 - "demux"
Cohesion: 0.36
Nodes (4): demux(), Split word array into digital RLE packets and analog block words. Returns…, Demux splits words by bit15 into digital/analog lists., TestDemux

### Community 120 - "core"
Cohesion: 0.29
Nodes (8): core, Fast_Logic_Analyzer_SDRAM1, MSO_CAP, OLS_SDRAM_Top, pll_inst, ram_block1a0, SDRAM_Analyzer, u_pack

### Community 121 - "host/conftest.py"
Cohesion: 0.43
Nodes (7): device_spi(), _make_smart_dev(), mock_dev(), mock_ftd2xx(), ols(), ols_no_dev(), fixture

### Community 122 - "parse_response"
Cohesion: 0.32
Nodes (4): crc16(), parse_response(), CRC-16-IBM, reflected poly 0xA001 (init 0xFFFF = CRC-16/MODBUS)., Parse a response packet from raw SPI bytes. Returns (status, seq, payload) or…

### Community 123 - "test_ack_pad_sweep.py"
Cohesion: 0.36
Nodes (7): check_data_integrity(), main(), measure_throughput(), Single streaming capture, return (throughput_mb, success)., Quick check: no long runs of 0xFF (sign of stale/corrupted samples)., Test a specific ack_pad value. Returns: (avg_throughput, pass_fail, details), test_ack_pad()

### Community 124 - "test_hardware_helper_and_fallback_branches"
Cohesion: 0.14
Nodes (16): analog_channel_info(), default_digital_channel_pin_info(), digital_pin_info(), exposed_analog_count_for_current_rtl(), Any, MAX1000 board pin maps derived from the bundled user guide. The FPGA capture…, Number of physical MAX1000 analogue inputs exposed by the current RTL., import_host_decoders() (+8 more)

### Community 125 - "diag_arm_methods.py"
Cohesion: 0.33
Nodes (4): Send payload via SPI, return all MISO bytes., Read SPI status via 1-byte xfer., raw_xfer(), read_status()

### Community 127 - "_burst_characterize.py"
Cohesion: 0.47
Nodes (5): main(), Characterize the rare multi-sample BURST (whole-capture corruption). Captures…, Contiguous runs where CH0 != dom: list of (start, length)., runs_of_bad(), to_words()

### Community 128 - "debug_basic.py"
Cohesion: 0.47
Nodes (5): cmd5(), drain(), Direct MPSSE test: ARM capture, wait, read sample data., Send NOPs via 0x31, read MISO., read_n()

### Community 129 - "debug_i2c_direct.py"
Cohesion: 0.47
Nodes (4): cmd6(), load_block(), Minimal direct I2C gen test — builds buffer manually, no method calls., _xfer()

### Community 130 - "_drop_period_precise.py"
Cohesion: 0.47
Nodes (5): approx_gcd(), devs_of(), main(), Pin the EXACT drop period and its modular structure. Long static-CH0 captures;…, GCD of values allowing +-tol jitter (snap each to nearest multiple).

### Community 131 - ".accel_read_i2c"
Cohesion: 0.33
Nodes (3): Extract I2C frame bits from RX samples using the known generated SCL pattern:…, Read one LIS3DH register over I2C. Returns the byte or None. Requires the RX-…, LIS3DH WHO_AM_I (expect 0x33) via I2C.

### Community 132 - "._transaction_raw"
Cohesion: 0.25
Nodes (4): Return matching response from buffered SPI bytes, if complete., Read one 1024-byte capture block at given address., Read one streaming block (1024 bytes uncompressed, 384 compressed)., Like transaction() but for large read responses.

### Community 134 - "run_ack_pad_test.ps1"
Cohesion: 0.67
Nodes (5): Header(), Run-HardwareSweep(), Run-Testbench(), Show-Report(), Status()

### Community 135 - "altera_modular_adc_sequencer"
Cohesion: 0.40
Nodes (3): altera_modular_adc_sequencer_csr, altera_modular_adc_sequencer_ctrl, altera_modular_adc_sequencer

### Community 136 - "_burst_vs_transition.py"
Cohesion: 0.60
Nodes (4): analyze(), main(), Decide whether the multi-sample 'bursts' are real corruption or just a real…, words()

### Community 137 - "diag_capture_cont.py"
Cohesion: 0.60
Nodes (4): mb_cmd(), preamble(), Multi-byte command: [0x11, cmd, d0, d1, d2, d3], raw_xfer()

### Community 138 - "preflight_ftdi.py"
Cohesion: 0.60
Nodes (4): _decode(), inspect_device(), main(), Quick FTDI preflight for OLS hardware selection. Prints each enumerated FTDI…

### Community 139 - "_stream_bulk_readback.py"
Cohesion: 0.50
Nodes (4): Lever #1: stream a completed single-shot capture instead of block reads. A…, Drain nsamples from a completed single-shot buffer via streaming., run(), stream_buffer()

### Community 140 - "SPI Packet Protocol"
Cohesion: 0.11
Nodes (17): Block Size, Command Set, Constants, CRC-16, Dependencies, Host-side Compat Opcodes, Host-side Implementation, Host SPI Transport (+9 more)

### Community 142 - "Delta Codec"
Cohesion: 0.67
Nodes (4): Delta Codec, RLE Codec, Test 34: Codec readback matrix, Test 35: Live ring rate ceiling per codec

### Community 143 - "OLS Logic Analyzer Hardware Validation Suite"
Cohesion: 0.50
Nodes (4): Test 2: SPI handoff and CMD_GET_METADATA, Test 3: All packet protocol commands, Test 11: Divider accuracy, OLS Logic Analyzer Hardware Validation Suite

### Community 144 - "Test 30: Jumper-pair discovery"
Cohesion: 0.50
Nodes (4): Test 30: Jumper-pair discovery, Test 31: Generator matrix over jumper, Test 32: Generator decodable in live operation, Test 33: Repeating UART decodes in SDRAM ring

### Community 148 - "altera_modular_adc_control"
Cohesion: 0.50
Nodes (3): altera_modular_adc_control, altera_modular_adc_control_fsm, fiftyfivenm_adcblock_top_wrapper

### Community 149 - "altera_modular_adc_control_fsm"
Cohesion: 0.50
Nodes (3): altera_modular_adc_control_fsm, altera_modular_adc_control_avrg_fifo, altera_std_synchronizer

### Community 150 - "fiftyfivenm_adcblock_top_wrapper"
Cohesion: 0.50
Nodes (3): fiftyfivenm_adcblock_top_wrapper, chsel_code_converter_sw_to_hw, fiftyfivenm_adcblock_primitive_wrapper

### Community 151 - "altera_modular_adc_control"
Cohesion: 0.50
Nodes (3): altera_modular_adc_control, altera_modular_adc_control_fsm, fiftyfivenm_adcblock_top_wrapper

### Community 152 - "altera_modular_adc_control_fsm"
Cohesion: 0.50
Nodes (3): altera_modular_adc_control_fsm, altera_modular_adc_control_avrg_fifo, altera_std_synchronizer

### Community 153 - "fiftyfivenm_adcblock_top_wrapper"
Cohesion: 0.50
Nodes (3): fiftyfivenm_adcblock_top_wrapper, chsel_code_converter_sw_to_hw, fiftyfivenm_adcblock_primitive_wrapper

### Community 154 - "Internal Architecture"
Cohesion: 0.11
Nodes (17): ADC Controller, Capture Mux (FAST_CLK domain), Clock Domain Crossings, Clock Generation, Dependencies, Entity Signature, Generator Wiring, Generics (+9 more)

### Community 157 - "diag_arm_fast0.py"
Cohesion: 0.83
Nodes (3): mb_cmd(), preamble(), raw_xfer()

### Community 162 - "diag_minimal_prefix.py"
Cohesion: 1.00
Nodes (3): check_arm(), preamble(), raw_xfer()

### Community 163 - "_drop_mechanism.py"
Cohesion: 0.67
Nodes (3): main(), measure(), Probe the mechanism behind the toggling-modulated ~1958 write drops. (1) DUTY…

### Community 164 - "_drop_period_scan.py"
Cohesion: 0.67
Nodes (3): devs_of(), main(), Classify the periodic write-drop: sample-counted vs time-counted. Captures a…

### Community 166 - "test_ack_pad_direct"
Cohesion: 0.67
Nodes (3): main(), Test a specific ack_pad by monkeypatching at driver level, test_ack_pad_direct()

### Community 170 - "MeasurementContext"
Cohesion: 0.19
Nodes (13): MeasurementContext, ndarray, Region-scoped access to waveform data for measurement functions., run_measurement(), test_analog_measurements(), test_digital_measurements(), test_glitch_measurement_and_filters(), test_extended_timing_and_analog_statistics_are_registered() (+5 more)

### Community 171 - "OLS Logic Analyzer for MAX1000"
Cohesion: 0.67
Nodes (3): Changelog, Compression Baseline and Jumper-Driven Validation, OLS Logic Analyzer for MAX1000

### Community 172 - "Software Feature Capability Matrix"
Cohesion: 0.67
Nodes (3): Software Feature Capability Matrix, Software Roadmap Release Notes, Software-Only Feature Roadmap

### Community 174 - "Test 31: Generator matrix over jumper"
Cohesion: 0.67
Nodes (3): Test 30: Jumper-pair discovery + UART loopback, Test 31: Generator matrix over jumper, Test 32: Generator decodable in live operation

### Community 175 - "Test 12c2: High-speed analog-only mode"
Cohesion: 0.67
Nodes (3): Test 12c: Mixed digital + analog mode, Test 12c2: High-speed analog-only mode, Test 12c3: Dual analog channel mode

### Community 179 - "Seed Sweep Results Table"
Cohesion: 0.67
Nodes (3): Seed Sweep 3 Results, Best Seed Value, Seed Sweep Results Table

### Community 189 - "Internal Architecture"
Cohesion: 0.12
Nodes (16): 1. SPI Packet Reception, 2. Command Dispatch, 3. Register Read/Write, 4. Block Readout (Response FIFO), 5. Readback Compression and Raw Streaming, 6. Generator Capture FSM, 7. Sticky DONE + ACK, Dependencies (+8 more)

### Community 275 - "Signal Generator: `Signal_Gen` + `Bit_Engine`"
Cohesion: 0.12
Nodes (16): Generator Routing and Bit Banger Contract, Architecture, Auxiliary routing and fast capture, Bit_Engine, Bit Engine FIFO, Dependencies, Entity Ports, Generator Capture Loopback (+8 more)

### Community 277 - "Pattern Trigger: `Generic_Pattern_Trigger`"
Cohesion: 0.17
Nodes (11): Hardware Validation Wiki, Bit ordering, Board validation, Integration, Interface, Known Limitations, Operation, Pattern Trigger: `Generic_Pattern_Trigger` (+3 more)

### Community 490 - "OneWireDecoder"
Cohesion: 0.13
Nodes (9): OneWireDecoder, Any, format_value(), ParallelDecoder, Any, Parallel bus decoder: N digital channels as a bus, clocked or unclocked., test_decoder_short_and_protocol_edge_paths(), test_onewire_decoder_reports_reset_and_lsb_first_byte() (+1 more)

### Community 491 - "MachineInLoopPage.tsx"
Cohesion: 0.27
Nodes (15): MilConfig, bytesFromHex(), cleanHex(), commandHex(), commandOptions(), defaultRequest(), fmtAddress(), MachineInLoopPage() (+7 more)

### Community 492 - "SwdDecoder"
Cohesion: 0.14
Nodes (8): I2cDecoder, Any, _glitch_filter(), Any, ARM SWD decoder (SWCLK/SWDIO), ported from the proven bit-level parser in…, SwdDecoder, test_decoder_control_and_error_edges(), test_swd_decoder_can_mark_open_loop_no_target_as_expected()

### Community 493 - "DecoderResult"
Cohesion: 0.25
Nodes (7): DecoderResult, InfraredDecoder, Any, ndarray, Common consumer-infrared decoder for NEC, RC5, and RC6 pulse trains., Any, Any

### Community 494 - "main.cjs"
Cohesion: 0.20
Nodes (13): { app, BrowserWindow, dialog }, backendCommand(), createMainWindow(), findFreePort(), fs, hasSingleInstanceLock, http, net (+5 more)

### Community 495 - "validate_capture_result"
Cohesion: 0.19
Nodes (10): Validate the capture contract and return the sample count. Hardware adapters…, validate_capture_result(), hardware_available(), Create the real-device adapter. ``driver_loader`` is the external-driver seam:…, parametrize, test_capture_result_contract_accepts_aligned_digital_and_analog_channels(), test_capture_result_contract_rejects_malformed_results(), test_existing_host_adapter_accepts_injected_driver_loader() (+2 more)

### Community 496 - "OLS Logic Analyzer — User Guide"
Cohesion: 0.15
Nodes (11): Build, Hardware note, Windows desktop package, Capture and inspect a waveform, First run: connect a device, Generate protocol traffic, Hardware versus mock mode, OLS Logic Analyzer — User Guide (+3 more)

### Community 497 - "HDL Testbenches"
Cohesion: 0.15
Nodes (12): Analog / MSO, Capture Path, Continuous Mode, Generator, HDL Testbenches, Interface / Control, Misc / Support, SDRAM (+4 more)

### Community 498 - "sweep.py"
Cohesion: 0.30
Nodes (11): expand_variants(), preview_variant(), Any, GeneratorConfig, Deterministic generator variant expansion and preview sweeps., Create a bounded Cartesian product of top-level or ``extra`` fields., Validate one variant and return stable metrics instead of raising., Run bounded generator variants through a capture-backed runner. (+3 more)

### Community 499 - "ADC Controller: `ADC_Controller`"
Cohesion: 0.20
Nodes (9): ADC Controller: `ADC_Controller`, ADC Mux Channel Map, Analog Frame Output, Analog Profile Flags (from REG_FLAGS), Dependencies, Entity Ports, Purpose, Scan Profiles (+1 more)

### Community 500 - "LED Controller: `LED_Controller`"
Cohesion: 0.20
Nodes (9): Dependencies, Entity Ports, Fade Controller, Internal Architecture, LED Controller: `LED_Controller`, Package, Purpose, PWM Engine (+1 more)

### Community 501 - "status_ws.py"
Cohesion: 0.50
Nodes (8): WebSocket, WebSocket endpoints. Topics map 1:1 to the manager's broadcast topics:…, _serve(), ws_capture(), ws_decoder(), ws_logs(), ws_session(), ws_status()

### Community 502 - "Waveform Viewer"
Cohesion: 0.22
Nodes (8): Channel Colours, Dependencies, Interaction, Performance, Playwright captures, Purpose, Rendering Architecture, Waveform Viewer

### Community 503 - "UART Interface: `UART_Interface`"
Cohesion: 0.25
Nodes (7): Baud Rates, Dependencies, Entity Ports, Operation, Purpose, Testing, UART Interface: `UART_Interface`

### Community 504 - "Hardware and Capture Seam"
Cohesion: 0.29
Nodes (6): Adapters and testing, Change checklist, Flow, Hardware and Capture Seam, Interface contract, Shared data contracts

### Community 505 - "Workers"
Cohesion: 0.29
Nodes (6): Dependencies, Message Protocol, Purpose, waveform.worker.ts, waveformClient.ts, Workers

### Community 506 - "analog_interface_probe.py"
Cohesion: 0.48
Nodes (5): adc_to_volts(), capture_adc(), count_edges(), Hit, main()

### Community 507 - "ManchesterDecoder"
Cohesion: 0.33
Nodes (3): ManchesterDecoder, Any, test_manchester_decoder_decodes_msb_word()

### Community 508 - "SpiDecoder"
Cohesion: 0.33
Nodes (3): Any, SpiDecoder, test_spi_decoder_requires_data_channel()

### Community 509 - "State Management"
Cohesion: 0.33
Nodes (5): Split State Architecture, State Management, `useApp` (Zustand), WaveformView (Plain Class), Why Split

### Community 510 - "RLE Compressor: `rle_compressor`"
Cohesion: 0.33
Nodes (5): Interface, Known Limitations, Purpose, RLE Compressor: `rle_compressor`, Testing

### Community 511 - "Q: Are there any changes we should make based off the graphs?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Are there any changes we should make based off the graphs?, Source Nodes

### Community 512 - "measurement_results"
Cohesion: 0.50
Nodes (4): measurement_results(), Recompute all measurements (live recalculation when cursors move: pass…, get_measurements(), Recompute and return all configured measurements for a session.

### Community 513 - "main"
Cohesion: 0.50
Nodes (3): main(), Auto-detect SPI device. Returns 'SPI' or None., splash_choose()

### Community 514 - "analog_probe_sweep.py"
Cohesion: 0.83
Nodes (3): count_edges(), main(), sweep_pin()

### Community 515 - "_rolling_tput_probe.py"
Cohesion: 0.67
Nodes (3): main(), Rolling-capture throughput probe for FT2232H transport tuning. Compares the…, run_case()

## Knowledge Gaps
- **660 isolated node(s):** `$schema`, `title`, `type`, `name`, `sample_rate` (+655 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **304 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OLSDeviceSPI` connect `OLSDeviceSPI` to `hw_validation.py`, `main`, `analog_probe_sweep.py`, `ols_spi_device.py`, `_drop_period_precise.py`, `_rolling_tput_probe.py`, `.accel_read_i2c`, `_burst_vs_transition.py`, `OLS`, `_stream_bulk_readback.py`, `analog_frame_stride`, `spi_protocol.py`, `OLScope`, `TestCompressionHelpers`, `TestOLSDeviceSPI`, `_drop_mechanism.py`, `_drop_period_scan.py`, `.protocol_trigger_match_pos`, `test_ack_pad_direct`, `SPIDevice`, `.capture`, `swd_sequence_symbols`, `test_mso_packed.py`, `analog_uart_decode.py`, `TestI2CReadSymbols`, `diag_raw_stream_probe.py`, `find_spi_device`, `jumper_compression_probe.py`, `gui_decoders.py`, `_d2xx_write_pacing_probe.py`, `TestParseI2CReadPayload`, `uart_substitution_probe.py`, `TestOLSDeviceSPIGenerator`, `test_ols_spi_device.py`, `TestOLSDeviceSPICapture`, `live_readback_sweep.py`, `decode_modbus`, `host/conftest.py`, `analog_interface_probe.py`, `test_ack_pad_sweep.py`, `._auto_connect`, `_burst_characterize.py`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Why does `CaptureSettings` connect `ExistingHostAdapter` to `test_core.py`, `hardware/base.py`, `CaptureResult`, `HardwareError`, `measurements.py`, `capture.py`, `MockDevice`, `test_processing_edges.py`, `main.py`, `test_requested_features.py`, `CaptureManager`, `server.py`, `test_roadmap_robustness.py`, `mil/service.py`, `WaveformData`, `test_hardware_helper_and_fallback_branches`, `test_generator_controller.py`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `test_adapter_stream_capture_unpacks_narrow_ring_and_restores_flags()` connect `ExistingHostAdapter` to `OLSDeviceSPI`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Are the 65 inferred relationships involving `OLSDeviceSPI` (e.g. with `main()` and `main_analog_only()`) actually correct?**
  _`OLSDeviceSPI` has 65 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `DecodeContext` (e.g. with `CanDecoder` and `HdlcDecoder`) actually correct?**
  _`DecodeContext` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `WaveformData` (e.g. with `CaptureManager` and `ControlLock`) actually correct?**
  _`WaveformData` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `ExistingHostAdapter` (e.g. with `CaptureResult` and `HardwareDevice`) actually correct?**
  _`ExistingHostAdapter` has 13 INFERRED edges - model-reasoned connections that need verification._
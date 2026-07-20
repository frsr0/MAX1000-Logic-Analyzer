# Software-Only Feature Roadmap

This roadmap covers features that can be added to the frontend, backend, and
host driver without changing the FPGA image or board hardware. It assumes the
current MAX1000 limits remain in place: 16 digital inputs, the existing ADC
profiles, the two-output Bit Banger, the 256-byte generator FIFO, and the
current SPI readback bandwidth.

## Working rules

- [x] Keep raw capture data immutable; all filters and thresholds create derived data.
- [x] Label hardware triggers, post-capture triggers, and unavailable features accurately.
- [x] Every new decoder or processing feature gets deterministic backend tests.
- [ ] Every user-facing feature gets a mock-mode E2E test before hardware testing.
- [ ] Add hardware tests only where the existing pins and routing genuinely support them.
- [x] Update `README.md` and `WEBAPP.md` whenever the advertised capability changes.

## Phase 0 — Baseline and project scaffolding

- [x] Record the current branch baseline and test commands.
- [x] Add a feature/capability matrix covering mock, real hardware, and post-capture-only behavior.
- [x] Reconcile documentation with the implementation: SWD is already present, and the analog spectrum/XY UI already exists.
- [x] Add shared protocol concepts: bit order, parity, stop bits, clock phase, line idle state, differential polarity, and error severity.
- [x] Add common event fields for all decoders: sample position, duration, raw value, decoded value, error flags, and human-readable label.
- [x] Add a standard fixture format for protocol captures and expected decoder events.

## Phase 1 — Bit Banger protocol exerciser

### Raw waveform and scripting

- [x] Expose the generic two-bit symbol format through a backend API.
- [x] Add validation for symbol count, FIFO capacity, idle state, pin selection, and timing divisor.
- [x] Add a raw symbol editor in the Generator page.
- [x] Add waveform presets: idle, pulse, square wave, alternating bits, counter, walking bit, and PRBS.
- [x] Add repeat count, inter-transaction gap, continuous mode, and stop behavior for Bit Banger scripts.
- [x] Add a transaction script format with symbol steps, gaps, delays, and repeats.
- [x] Add script import/export as JSON.
- [x] Add a preview waveform before sending.
- [x] Add a send-and-capture operation that creates a normal session with generator metadata.
- [x] Add clear errors when a requested waveform cannot fit the current FIFO.

### Existing protocol generators

- [x] UART: parity selection, 1/1.5/2 stop bits, break generation, configurable idle bits, and framing-error injection.
- [x] RS-485 Bit Banger exerciser: transmit-enable timing, turnaround delay, direction-change markers, and half-duplex transaction scripts.
- [ ] Hardware RS-485 generator: expose physical DE timing if a future firmware route provides it.
- [x] SPI Bit Banger template: CPOL/CPHA 0–3, MSB/LSB first, word sizes 4–32 bits, and inter-word gaps.
- [ ] Hardware SPI generator: configurable CS/MISO only where firmware routing permits.
- [x] I²C Bit Banger templates: 7-bit address/register read-write forms, repeated starts, ACK/NACK control, clock stretching visualization, and bus recovery clocks.
- [x] PWM Bit Banger templates: frequency/duty sweeps, bursts, finite pulse counts, and configurable start phase.
- [x] SWD Bit Banger exerciser: line reset, JTAG-to-SWD transition, DP/AP read/write forms, ACK/data parity, and transaction scripts.
- [ ] SWD transaction capture/logging against a physical route when firmware exposes one.
- [x] SPI mode 3: promote the existing host helper into the public generator workflow.

### New generators using the existing two outputs

- [x] 1-Wire reset/presence/read/write transactions through the software Bit Banger exerciser.
- [x] Manchester and differential-Manchester waveform generation.
- [x] NRZ/custom framed serial generation.
- [x] PS/2 clock/data generation.
- [x] MIDI serial generation.
- [x] LIN break, sync, identifier, payload, and checksum generation.
- [x] I²S remains decode-only on the current two-output hardware; the existing decoder supports three captured lines and format variants.
- [x] Add Bit Banger protocol fault injection: wrong parity, invalid stop bit, malformed checksum, missing ACK, shortened pulse, and illegal bus transition.

## Phase 2 — Decoder expansion

- [x] Manchester and differential Manchester.
- [x] Add an initial clocked NRZ decoder; extend it later with custom framing, sync, length, endian order, escape, and checksum rules.
- [x] I²S: basic standard/left/right-justified-compatible extraction, word length, sample width, and channel extraction.
- [x] CAN: classical CAN-RX logic-level decoding with bit stuffing, arbitration identifiers, CRC, ACK, and extended identifiers.
- [x] LIN: sync/identifier/data/checksum and classic/enhanced checksum modes from UART events.
- [x] MIDI: channel messages, system messages, running status, and realtime timing messages.
- [x] PS/2: scan codes/bytes and parity/start/stop validation.
- [x] Quadrature encoder: direction, count, and illegal-transition detection.
- [x] I²S: basic stereo word extraction with format/edge/sample-width settings.
- [x] JTAG: TMS/TDI/TDO/TCK sampling, TAP state transitions, IR/DR scans, and extracted data words.
- [x] SMBus/PMBus: address, command, PEC, alert response, and common transaction forms.
- [x] HDLC/PPP-style framed serial with bit/byte stuffing and CRC.
- [x] NEC/RC5/RC6 infrared protocol decoders.
- [x] Quadrature encoder decoder with direction, count, and illegal-transition detection.
- [x] Add decoder confidence/quality scores for ambiguous or undersampled captures.
- [x] Add decoder presets and reusable channel mappings.
- [x] Add stacked decoders for UART → Modbus/LIN/custom framing and SPI/I²C → device-register views.

## Phase 3 — Trigger and search engine

### Software trigger implementation

- [x] Implement `any_edge` consistently across channels and derived channels.
- [x] Implement `sequence`: event A followed by event B within a configurable time/sample window.
- [x] Implement `i2c_address`, including read/write value matching.
- [x] Implement `i2c_nack` using decoder ACK fields.
- [x] Implement `spi_byte` using decoded SPI word values.
- [x] Implement `decoder_error` using decoder severity.
- [x] Add trigger-on-nth-event and trigger-on-first-error modes.
- [x] Add minimum/maximum duration and persistence qualifiers.
- [x] Add “N consecutive samples/events match” qualification.
- [x] Add trigger holdoff and re-arm search behavior for repeated captures.
- [x] Add trigger search over an existing session and its decoded event store.

### Trigger UI

- [x] Show execution class beside every trigger: FPGA hardware, post-capture software, or unavailable.
- [x] Add a trigger builder for event sequences and timing windows.
- [x] Add visual trigger previews in the trigger builder.
- [x] Add “jump to first match”, “next match”, and “previous match”.
- [x] Add trigger result metadata to session exports and reports.
- [x] Add tests proving that post-capture triggers never alter raw samples.

## Phase 4 — Signal processing and measurements

### Digital timing

- [x] Add min/maximum/mean/median period and pulse-width statistics.
- [x] Add standard deviation, peak-to-peak jitter, RMS jitter, and period histogram.
- [x] Add setup/hold measurement between a data edge and a clock edge.
- [x] Add clock-to-data skew and propagation-delay measurement between channels.
- [x] Add inter-event and response/event latency measurements; extend to typed protocol transactions later.
- [x] Add bus throughput, utilization, and event-rate measurements.
- [x] Add pulse-width and period histograms.
- [x] Add glitch density and glitch duration statistics.

### Analog and mixed signal

- [x] Add RMS, mean, peak-to-peak, min/max, crest factor, and noise-floor measurements.
- [x] Add configurable digital threshold sweeps over analog channels.
- [x] Add hysteresis threshold controls for derived analog digital channels.
- [x] Add spectrogram/time-frequency view using a dedicated endpoint.
- [x] Add spectrum peak finding and frequency labeling.
- [x] Add waveform persistence and min/max envelope display.
- [x] Add cross-correlation and estimated time delay between channels.
- [x] Add analog/digital event correlation and aligned protocol annotations.
- [x] Add optional software filters: moving average, median, low-pass, high-pass, and baseline removal.
- [x] Keep all filtered signals as named derived channels with reproducible settings.

### Analysis views

- [x] Add configurable digital eye diagrams for UART/SPI/I²C lines.
- [x] Add protocol activity heatmaps.
- [x] Add bus transaction timeline view.
- [x] Add waveform/session diff view with first divergence and alignment controls.
- [x] Add automatic “suspect timing” annotations for out-of-family pulses or gaps.

## Phase 5 — Frontend workflow improvements

- [x] Add decoder table filters for errors, addresses, values, and time ranges.
- [x] Make every decoder event clickable from table to waveform and vice versa.
- [x] Add bookmarkable event markers and named regions.
- [x] Add keyboard navigation for next/previous edge, decoder event, trigger match, and error.
- [x] Add drag-and-drop channel reordering.
- [x] Add per-channel visibility groups and saved layouts.
- [x] Add command palette for navigation.
- [x] Extend the command palette with capture, decode, trigger search, and export actions.
- [x] Add session tags, notes, and searchable metadata.
- [x] Add reusable capture/decoder/measurement presets.
- [x] Add a protocol dashboard summarizing packets, errors, timing, and throughput.
- [x] Add a generator-to-capture comparison view showing expected versus observed bytes.
- [x] Add clear capability badges for hardware versus mock-only generator features.

## Phase 6 — Import, export, and automation

- [x] Add CSV import with channel names and digital/analog inference.
- [x] Add VCD import and map `$var` signals to analyzer channels.
- [x] Add optional PulseView-compatible VCD export where the format can be supported reliably.
- [x] Add decoded protocol JSON export with stable schema versioning.
- [x] Add richer HTML reports with plots, error summaries, trigger details, generator provenance, and measurements.
- [x] Add a dependency-free PDF report export path alongside the rich HTML report.
- [x] Add batch decode of multiple sessions.
- [x] Add command-line capture/decode/export workflows for CI and regression testing.
- [x] Add automated generator parameter/preview sweeps for CI and regression runs.
- [x] Add opt-in capture-backed sweeps for a connected device and route.
- [x] Add pass/fail assertions for expected packets, timing bounds, and error counts.
- [x] Add machine-readable JUnit/JSON results for hardware validation runs.

## Phase 7 — Verification and release hardening

- [x] Add unit smoke/property coverage for every registered encoder, decoder, trigger, and measurement.
- [x] Add malformed-input tests: truncation, undersampling, noise, missing edges, illegal transitions, and FIFO overflow.
- [x] Add deterministic property-style tests for round-tripping generator output through decoder input.
- [x] Add deterministic mock scenarios for supported generator protocols and representative error conditions.
- [x] Add frontend E2E coverage for generator, decoder, trigger builder, measurements, reports, and session comparison.
- [x] Run the existing backend test suite.
- [x] Run the existing host/driver test suite.
- [x] Run frontend typecheck and production build.
- [ ] Run hardware smoke tests for unchanged capture paths after host-driver changes.
- [x] Validate no new feature falsely advertises unavailable physical capabilities.
- [x] Update screenshots and user documentation.
- [x] Add release notes and migration notes for session/decoder schema changes.

## Suggested first milestones

- [x] Milestone 1: raw Bit Banger API, waveform preview, repeat/gap controls, and transaction metadata.
- [x] Milestone 2: sequence triggers, protocol error search, and next/previous match UI.
- [x] Milestone 3: Manchester, NRZ/custom serial, LIN, and MIDI decoders.
- [x] Milestone 4: jitter/statistics/eye-diagram processing.
- [x] Milestone 5: SWD and I²C register-explorer workflows through software exercisers.
- [x] Milestone 6: scripted generator fault injection and automated preview sweeps.
- [x] Milestone 7: session diff, import, enhanced reports, and CI automation.

## Hardware-boundary notes

- [x] Do not advertise CAN electrical connectivity without an external CAN transceiver.
- [x] Do not advertise true analog bandwidth beyond the existing MAX10 ADC profiles.
- [x] Do not assume the hardware SPI generator has CS/MISO on real hardware; current loopback is MOSI/SCLK only.
- [x] Treat Bit Banger readback/open-drain behavior as host-emulated unless the target can safely override released-high lines.
- [x] Keep large arbitrary waveforms chunked or rejected because the generator FIFO is finite.
- [x] Keep high-rate rolling capture limitations and overrun reporting visible in the UI.

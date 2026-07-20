# Software-Only Feature Roadmap

This roadmap covers features that can be added to the frontend, backend, and
host driver without changing the FPGA image or board hardware. It assumes the
current MAX1000 limits remain in place: 16 digital inputs, the existing ADC
profiles, the two-output Bit Banger, the 256-byte generator FIFO, and the
current SPI readback bandwidth.

## Working rules

- [x] Keep raw capture data immutable; all filters and thresholds create derived data.
- [ ] Label hardware triggers, post-capture triggers, and unavailable features accurately.
- [x] Every new decoder or processing feature gets deterministic backend tests.
- [ ] Every user-facing feature gets a mock-mode E2E test before hardware testing.
- [ ] Add hardware tests only where the existing pins and routing genuinely support them.
- [x] Update `README.md` and `WEBAPP.md` whenever the advertised capability changes.

## Phase 0 — Baseline and project scaffolding

- [ ] Record the current branch baseline and test commands.
- [ ] Add a feature/capability matrix covering mock, real hardware, and post-capture-only behavior.
- [ ] Reconcile documentation with the implementation: SWD is already present, and the analog spectrum/XY UI already exists.
- [ ] Add shared protocol concepts: bit order, parity, stop bits, clock phase, line idle state, differential polarity, and error severity.
- [x] Add common event fields for all decoders: sample position, duration, raw value, decoded value, error flags, and human-readable label.
- [ ] Add a standard fixture format for protocol captures and expected decoder events.

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
- [ ] Add a send-and-capture operation that creates a normal session with generator metadata.
- [x] Add clear errors when a requested waveform cannot fit the current FIFO.

### Existing protocol generators

- [ ] UART: parity selection, 1/1.5/2 stop bits, break generation, configurable idle bits, and framing-error injection.
- [ ] RS-485: transmit-enable timing, turnaround delay, direction-change markers, and half-duplex transaction scripts.
- [ ] SPI: CPOL/CPHA 0–3, MSB/LSB first, word sizes 4–32 bits, configurable CS where routing permits, and inter-word gaps.
- [ ] I²C: 7-bit address templates, register read/write templates, repeated starts, ACK/NACK control, clock stretching visualization, and bus recovery clocks.
- [ ] PWM: frequency/duty sweeps, bursts, finite pulse counts, and configurable start phase.
- [ ] SWD: line reset, JTAG-to-SWD transition, DP/AP read/write forms, IDCODE discovery, ACK decoding, and transaction logs.
- [ ] SPI mode 3: promote the existing host helper into the public generator workflow.

### New generators using the existing two outputs

- [ ] 1-Wire reset/presence/read/write transactions.
- [ ] Manchester and differential-Manchester waveform generation.
- [ ] NRZ/custom framed serial generation.
- [ ] PS/2 clock/data generation.
- [ ] MIDI serial generation.
- [ ] LIN break, sync, identifier, payload, and checksum generation.
- [ ] Optional I²S clock/word-select/data generation where three captured/generated lines can be represented by available routing; otherwise support decode-only.
- [ ] Add protocol fault injection: wrong parity, invalid stop bit, malformed checksum, missing ACK, shortened pulse, and illegal bus transition.

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
- [ ] Add decoder presets and reusable channel mappings.
- [x] Add stacked decoders for UART → Modbus/LIN/custom framing and SPI/I²C → device-register views.

## Phase 3 — Trigger and search engine

### Software trigger implementation

- [ ] Implement `any_edge` consistently across channels and derived channels.
- [x] Implement `sequence`: event A followed by event B within a configurable time/sample window.
- [x] Implement `i2c_address`, including read/write value matching.
- [x] Implement `i2c_nack` using decoder ACK fields.
- [x] Implement `spi_byte` using decoded SPI word values.
- [x] Implement `decoder_error` using decoder severity.
- [x] Add trigger-on-nth-event and trigger-on-first-error modes.
- [x] Add minimum/maximum duration and persistence qualifiers.
- [x] Add “N consecutive samples/events match” qualification.
- [ ] Add trigger holdoff and re-arm search behavior for repeated captures.
- [x] Add trigger search over an existing session and its decoded event store.

### Trigger UI

- [x] Show execution class beside every trigger: FPGA hardware, post-capture software, or unavailable.
- [x] Add a trigger builder for event sequences and timing windows.
- [ ] Add visual trigger previews on the waveform.
- [x] Add “jump to first match”, “next match”, and “previous match”.
- [ ] Add trigger result metadata to session exports and reports.
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
- [ ] Add configurable digital threshold sweeps over analog channels.
- [ ] Add hysteresis threshold controls for derived analog digital channels.
- [x] Add spectrogram/time-frequency view using a dedicated endpoint.
- [x] Add spectrum peak finding and frequency labeling.
- [ ] Add waveform persistence and min/max envelope display.
- [x] Add cross-correlation and estimated time delay between channels.
- [ ] Add analog/digital event correlation and aligned protocol annotations.
- [ ] Add optional software filters: moving average, median, low-pass, high-pass, and baseline removal.
- [x] Keep all filtered signals as named derived channels with reproducible settings.

### Analysis views

- [ ] Add UART/SPI/I²C eye diagrams.
- [ ] Add protocol activity heatmaps.
- [ ] Add bus transaction timeline view.
- [ ] Add waveform/session diff view with first divergence and alignment controls.
- [ ] Add automatic “suspect timing” annotations for out-of-family pulses or gaps.

## Phase 5 — Frontend workflow improvements

- [ ] Add decoder table filters for errors, addresses, values, and time ranges.
- [ ] Make every decoder event clickable from table to waveform and vice versa.
- [ ] Add bookmarkable event markers and named regions.
- [ ] Add keyboard navigation for next/previous edge, decoder event, trigger match, and error.
- [ ] Add drag-and-drop channel reordering.
- [ ] Add per-channel visibility groups and saved layouts.
- [ ] Add command palette for capture, decode, trigger search, export, and navigation.
- [ ] Add session tags, notes, and searchable metadata.
- [ ] Add reusable capture/decoder/measurement presets.
- [ ] Add a protocol dashboard summarizing packets, errors, timing, and throughput.
- [ ] Add a generator-to-capture comparison view showing expected versus observed bytes.
- [ ] Add clear capability badges for hardware versus mock-only generator features.

## Phase 6 — Import, export, and automation

- [x] Add CSV import with channel names and digital/analog inference.
- [x] Add VCD import and map `$var` signals to analyzer channels.
- [ ] Add optional Sigrok/ PulseView-compatible export where the format can be supported reliably.
- [ ] Add decoded protocol JSON export with stable schema versioning.
- [ ] Add richer HTML/PDF reports with plots, error summaries, trigger details, and measurements.
- [ ] Add batch decode of multiple sessions.
- [ ] Add command-line capture/decode/export workflows for CI and regression testing.
- [ ] Add automated generator/capture protocol sweeps.
- [ ] Add pass/fail assertions for expected packets, timing bounds, and error counts.
- [ ] Add machine-readable JUnit/JSON results for hardware validation runs.

## Phase 7 — Verification and release hardening

- [ ] Add unit tests for every encoder, decoder, trigger, and measurement.
- [ ] Add malformed-input tests: truncation, undersampling, noise, missing edges, illegal transitions, and FIFO overflow.
- [ ] Add property tests for round-tripping generator output through decoder input.
- [ ] Add mock scenarios for every supported protocol and error condition.
- [ ] Add frontend E2E coverage for generator, decoder, trigger builder, measurements, reports, and session comparison.
- [ ] Run the existing backend test suite.
- [ ] Run the existing host/driver test suite.
- [ ] Run frontend typecheck and production build.
- [ ] Run hardware smoke tests for unchanged capture paths after host-driver changes.
- [ ] Validate no new feature falsely advertises unavailable physical capabilities.
- [ ] Update screenshots and user documentation.
- [ ] Add release notes and migration notes for session/decoder schema changes.

## Suggested first milestones

- [ ] Milestone 1: raw Bit Banger API, waveform preview, repeat/gap controls, and transaction metadata.
- [ ] Milestone 2: sequence triggers, protocol error search, and next/previous match UI.
- [ ] Milestone 3: Manchester, NRZ/custom serial, LIN, and MIDI decoders.
- [ ] Milestone 4: jitter/statistics/eye-diagram processing.
- [ ] Milestone 5: SWD and I²C register-explorer workflows.
- [ ] Milestone 6: scripted generator fault injection and automated pass/fail sweeps.
- [ ] Milestone 7: session diff, import, enhanced reports, and CI automation.

## Hardware-boundary notes

- [ ] Do not advertise CAN electrical connectivity without an external CAN transceiver.
- [ ] Do not advertise true analog bandwidth beyond the existing MAX10 ADC profiles.
- [ ] Do not assume the hardware SPI generator has CS/MISO on real hardware; current loopback is MOSI/SCLK only.
- [ ] Treat Bit Banger readback/open-drain behavior as host-emulated unless the target can safely override released-high lines.
- [ ] Keep large arbitrary waveforms chunked or rejected because the generator FIFO is finite.
- [ ] Keep high-rate rolling capture limitations and overrun reporting visible in the UI.

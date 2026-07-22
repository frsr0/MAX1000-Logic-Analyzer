# Recent Software Features

This page records the software features added after the original subsystem
wiki pages were written. It complements the [Feature and Coverage Matrix](feature-matrix.md)
with user-facing behavior, API names, and implementation boundaries.

## Expanded decoder catalog

The decoder registry is exposed by `GET /api/decoders` and feeds the frontend
Decoder Builder. Every decoder consumes immutable captured channels and emits
timestamped events with fields, labels, and severity. Decoders can be stacked
when one decoder consumes another decoder's events.

| Decoder ID | Purpose | Important behavior |
|---|---|---|
| `manchester` | Manchester and differential Manchester | Bit rate, polarity, bit order, and differential mode |
| `nrz` | Generic NRZ data | Bit rate, bit order, and polarity |
| `i2s` | I²S audio serial bus | BCLK, word-select, data, word width, alignment, and polarity |
| `can` | CAN/CAN-FD-style frames | Bit timing, identifiers, data bytes, CRC and frame fields |
| `lin` | LIN serial frames | Break, sync, protected identifier, data, and checksum |
| `midi` | MIDI 31.25 kbaud messages | Status/data bytes, running status, and channel messages |
| `ps2` | PS/2 keyboard/mouse bus | Clocked scan-code bytes and parity framing |
| `quadrature` | Rotary encoder A/B signals | Direction, position/count, and illegal transitions |
| `hdlc` | HDLC/bit-stuffed frames | Flag detection, bit unstuffing, payload, and CRC-16 |
| `jtag` | JTAG TAP transactions | TMS state transitions, IR/DR scans, and TDO/TDI bits |
| `infrared` | Common remote-control protocols | NEC, RC5, and RC6 pulse/Manchester decoding |
| `smbus` | SMBus transactions | I²C-compatible frames plus PEC validation and SMBus fields |
| `modbus_uart` | Modbus RTU over UART | CRC-16, function codes, exception frames, stacked on UART |

Existing UART, I²C, SPI, parallel, 1-Wire, PWM, RS-485, SWD, and decoder
framework documentation remains in [Decoder Implementations](backend/decoder-implementations.md).

These added decoders are software analysis features. They can decode imported,
mock, or physically captured waveforms; they do not imply that the MAX1000 has
a native CAN, I²S, LIN, MIDI, PS/2, or infrared electrical generator route.

## Bit Banger scripts, presets, and previews

The Generator page supports `bitbang`/`pattern` workflows in addition to the
framed UART, I²C, SPI, RS-485, and SWD generators. A symbol is two bits:

| Symbol bit | Physical output |
|---:|---|
| bit 0 | data/TX/MOSI/SDA |
| bit 1 | clock/SCL/SCLK |

Built-in deterministic presets are:

`idle`, `pulse`, `square`, `alternating`, `counter`, `walking`, and `prbs`.
The preset request uses `extra.preset` and optional `extra.count`.

JSON script steps provide repeatable multi-segment waveforms:

```json
{
  "symbol_rate": 1000000,
  "extra": {
    "script": [
      {"symbols": [3, 1, 0, 2], "gap_symbols": 8, "repeat": 4},
      {"symbols": [0, 3], "delay_s": 0.00001}
    ]
  }
}
```

Each step accepts `symbols` (values 0–3), `repeat`, and either
`gap_symbols` or `delay_s`; the entire expanded sequence is bounded by the
FPGA's 1,024-symbol FIFO. The frontend can preview TX/clock levels and
duration before sending. JSON scripts can be imported and exported from the
Generator page. `GET /api/generator/bitbang/presets` returns the preset list.

The software encoder library also provides reusable templates for UART, NRZ,
Manchester, SPI, I²C, RS-485, and SWD. Protocols that require more than two
physical wires are preview/decode capable unless the board route supplies the
required auxiliary pins.

## Generator sweeps and self-test

The generator workflow is configure → generate → optionally capture → decode →
compare → pass/fail. A sweep expands configured variants and records one row
per case, including protocol, status, expected/decoded data, mismatch indices,
and error details. This powers the frontend generator matrix and backend
hardware smoke checks.

The route capability descriptor controls whether optional RS-485 DE and SPI
CS/MISO controls are shown. The backend validates routes before writing FPGA
registers. See [Generator Routing](generator-routing.md).

## Waveform analysis and derived views

The Analog panel and waveform API now expose:

| Operation | API endpoint | Result |
|---|---|---|
| Spectrum / peaks | `/api/sessions/{id}/spectrum` | Frequency bins and detected peaks |
| Spectrogram | `/api/sessions/{id}/spectrogram` | Time-frequency magnitude slices |
| XY plot | `/api/sessions/{id}/xy` | Paired-channel scatter/trajectory data |
| Cross-correlation | `/api/sessions/{id}/correlation` | Delay estimate and correlation data |
| Event correlation | `/api/sessions/{id}/event-correlation` | Analog/digital edge relationship |
| Envelope | `/api/sessions/{id}/envelope` | Min/max values in configurable bins |
| Threshold sweep | `/api/sessions/{id}/threshold-sweep` | Edge/event counts across voltage levels |

Analog processing also includes rise/fall time, overshoot, undershoot, noise,
crest factor, RMS, peak-to-peak, and thresholded frequency/duty measurements.
Digital processing includes period statistics, pulse statistics/histograms,
glitch count, transition rate, jitter, setup/hold, channel skew, and bus value
at cursor. Protocol measurements include packet/error/NACK counts, UART error
counts, byte rate, bus utilization, inter-packet gap, and response latency.

Derived threshold channels are separate from raw data: the raw waveform is
never modified. A threshold or filter creates a named derived channel that
can be used by decoders, triggers, measurements, and the viewer.

## Session import and comparison

Sessions can be imported from JSON, CSV, or VCD. CSV/VCD import preserves
signal names and creates a normal session/waveform representation; imported
data can then use the same decoders, measurements, triggers, exports, and
visual analysis as a hardware capture.

The Sessions page compares two sessions through
`POST /api/sessions/{id}/compare/{other}`. It reports:

- settings differences and sample-count delta;
- optional sample alignment offset;
- whether digital data is identical;
- first divergence in each session;
- per-channel differences and summary counts.

This is intended for regression, generator loopback, and before/after signal
investigation rather than analog calibration certification.

## Trigger search and navigation

Hardware triggering remains limited to the FPGA UART byte protocol trigger.
Software search adds bus-value, byte, pulse-width, edge, and protocol-event
matching after capture. Trigger configuration supports threshold, polarity,
baud, value/mask, pulse-width, holdoff, and occurrence number.

The Trigger panel can search the first or nth occurrence and navigate previous
or next matches. The selected sample is sent to the waveform viewer so the
cursor and viewport jump to the match. The search API returns the match sample
or an explicit no-match result.

## Decoder quality and activity dashboard

After decoder execution, the backend stores a host-side quality score in the
range 0–1 on the decoder instance. It is a confidence indicator derived from
event count and warnings/errors; it is not a physical signal-integrity grade.
The Decoder panel displays it as a percentage when available.

The Capture page protocol dashboard aggregates decoder activity over capture
time and shows event density, error activity, and protocol summaries. It is a
navigation/triage aid; the underlying decoder event list remains authoritative.

## Command palette and keyboard controls

The AppShell command palette opens with `Ctrl+K` (or `Cmd+K`) and supports:

- navigation to all seven pages;
- start/stop capture;
- run the first available decoder on the active session;
- search the current trigger;
- export the active session as JSON;
- export an HTML report.

`Escape` closes the palette. The global space shortcut starts/stops capture
when the control lock permits it, and `Ctrl+S`/`Cmd+S` exports the active
session JSON. Text inputs and selectors do not consume these global shortcuts.

## Validation boundary

The new software features have backend unit/edge coverage and frontend
Playwright/mock coverage. The final physical-board regression validates the
FPGA acquisition/generator contract and the protocols with available physical
partners, but it does not turn every software decoder into a real electrical
hardware test. External protocol fixtures are still needed for meaningful CAN,
I²S, LIN, MIDI, PS/2, infrared, JTAG, or SWD response validation.

# On-board LIS3DH Accelerometer

The validation hardware targets the 8kLE MAX1000 build (Intel MAX 10
`10M08SAU169C8G`, 8 MB SDRAM, 8 MB flash), matching the currently offered
Trenz Electronic **TEI0001-04-DBC87A** variant. It carries an on-board LIS3DH
accelerometer. It is
an actual protocol partner for the analyzer, not a simulated peripheral: the
Bit Banger can talk to it over I²C or SPI, and the capture path can mirror the
bus dialogue into normal logic-analyzer channels.

## Board orientation

The live-pose drawing is a top-side view of the physical MAX1000: the Pmod is
at left and the Micro USB connector at right. Its axis key is anchored to U4,
the LIS3DH location in the upper-left section of the board:

| LIS3DH axis | Direction in the top-side drawing |
|---|---|
| `+X` | toward the lower board edge |
| `+Y` | toward the Micro USB connector (right) |
| `+Z` | out of the component side of the PCB |

This is derived from U4's pin-1 position in Trenz's top-side assembly drawing
and the LIS3DH package-axis drawing. The renderer uses `X` for rotation about
the USB axis and `Y` for rotation about the header axis, matching the physical
sensor-to-board frame rather than the browser's screen axes.

## Physical wiring

| Board signal | Pin-pool index | Function |
|---|---:|---|
| `SEN_SDO` | 23 | SPI MISO / I²C address strap (`SA0`) |
| `SEN_SDI` | 24 | SPI MOSI / I²C SDA |
| `SEN_SPC` | 25 | SPI SCLK / I²C SCL |
| `SEN_CS` | dedicated output | Chip select; held high for I²C and asserted for SPI |

The host adapter maps capture channels 13, 14, and 15 to the sensor data,
clock, and response lines when an attached accelerometer dialogue is captured.
The normal programmable pin pool exposes the three sensor signals as indices
23–25; the physical route and auxiliary capture selectors are documented in
[Generator Routing](generator-routing.md).

## Supported protocols

### I²C

The LIS3DH responds at address `0x18` or `0x19`, depending on the `SA0` strap.
The host probes both addresses. Register reads use a write phase containing
the register address, a repeated START, the read address, the requested number
of bytes, and STOP. SDA is open-drain: the generator releases the line during
ACK and read-bit slots.

The standard identity check reads register `WHO_AM_I` (`0x0F`) and expects
`0x33`. The validation also reads `CTRL_REG1` (`0x20`) to prove that a second
register address is handled correctly. I²C is tested at 50 kHz and 100 kHz.

### SPI

The LIS3DH uses SPI mode 3 (`CPOL=1`, `CPHA=1`): SCLK idles high, data changes
on the falling edge, and data is sampled on the rising edge. Register reads
set the read bit in the register command and clock the response on `SEN_SDO`.

The same `WHO_AM_I` check expects `0x33` at a stable response offset. The
validation repeats the read to ensure the result is stable rather than a
floating-line artifact.

## Data-rate and word-layout contract

The LIS3DH high-rate `CTRL_REG1` codes are mode-dependent. The application
therefore validates the mode/rate pair before writing registers and reports
both the sensor ODR and the measured host decoded rate:

| Operating mode | Highest ODR choices | Output word | Unused low bits |
|---|---:|---:|---:|
| Normal | 1.344 kHz | 10-bit | 6 |
| High resolution | 1.344 kHz | 12-bit | 4 |
| Low power | 1.620 or 5.376 kHz | 8-bit | 8 |

The host rate is a transport measurement, not a claim about the sensor’s
configured ODR. It includes only complete, structurally validated I²C XYZ
transactions and exposes the USB/ring transport ceiling when it is lower than
the sensor rate. The implementation follows ST’s [LIS3DH datasheet](https://www.st.com/resource/en/datasheet/CD00274221.pdf),
including the mode-dependent ODR table and full-scale sensitivities.

## Host and application surfaces

| Layer | Implementation |
|---|---|
| Bit-level encoding | `host/driver/bit_bang.py` — `i2c_read_symbols()` and `spi3_read_symbols()` |
| Device operations | `host/driver/ols_spi_device.py` — accelerometer register and capture helpers |
| Classic UI | `host/app/OLS_Console.py` — Accelerometer tab and register-read controls |
| Backend diagnostic | `backend/app/api/diagnostics.py` — `/api/diagnostics/live-accel-session` |
| Backend adapter | `backend/app/hardware/existing_host_adapter.py` — physical LIS3DH route handling |
| Web UI | Live session is shown in the normal waveform viewer with an I²C decoder |
| HDL | `Signal_Gen`, `Bit_Engine`, auxiliary capture routing, and `GEN_ACCEL_ATTACH` |

The live diagnostic creates a normal persisted session named `LIS3DH WHO_AM_I
live`, captures the dialogue at 2 MHz, maps SDA/SCL to the sensor channels,
and attaches the standard I²C decoder. Direct-live-capable FPGA images feed
that waveform through the ordered capture-tap FIFO (the three sensor bits are
transport-packed to one byte/sample); older images retain the SDRAM-ring
fallback. This makes the peripheral useful both as a device-level self-test
and as a realistic protocol waveform source.

## Live pose timing and recovery

The live path now publishes the ordered packed bus waveform separately from
the backend's legacy decoded-pose messages. `frontend/src/workers/liveI2cDecoder.ts`
keeps the I²C transaction state across WebSocket chunk boundaries, validates
the address/register/ACK layout and LIS3DH word padding, and emits a complete
XYZ point as soon as its final byte is observed. The display target consumes
those frontend-decoded points; its `requestAnimationFrame` smoothing is
presentation only and does not delay acquisition or publication.

The raw decoded point is retained on the FIFO history stream. The pose mailbox
also applies a causal physical-plausibility check, so a complete but
protocol-shaped/misaligned frame cannot flip the board orientation; the check
drops that one pose update without waiting for a later confirmation point.
The normal/high-resolution path uses a 2 MHz capture ring, standard 400 kHz
I²C, a 1,792-sample tail window, and no host prefetch. For the low-power
5.376 kHz setting, the generator reads 24 bytes from the LIS3DH FIFO per
dialogue and uses a larger ring window to amortise fixed USB block-read setup.
`source_backlog_samples` is the unread source queue after the returned window;
it must remain zero or near zero. `source_age_samples` is a separate point-age
measurement and may be non-zero even when the queue is empty.

The sensor ODR and host decoded rate are intentionally reported separately.
The flashed FPGA image currently sustains the 1.344 kHz normal/high-resolution
limit, but its block-read endpoint does not yet sustain the 5.376 kHz
low-power maximum. Reaching that absolute setting at full rate requires an
FPGA-side bulk FIFO/stream endpoint; enlarging browser buffers would only add
latency.

The application keeps separate WebSocket contracts so presentation cannot hold
up history or raw decoding:

| Endpoint | Contract |
|---|---|
| `/ws/accelerometer` | FIFO stream of every validated point for archival/history consumers |
| `/ws/accelerometer/pose` | Replaceable latest-value stream for the visualization target; stale pending poses are dropped |
| `/ws/accelerometer/waveform` | Ordered packed CH13/CH14/CH15 waveform chunks for frontend protocol decoding; at most two chunks may wait in the event-loop handoff |

The waveform WebSocket is the board's live source of truth. If a chunk is lost,
its sequence gap resets the incremental decoder instead of allowing unrelated
waveform sections to complete a plausible-looking fake frame. The current
hardware producer still obtains those chunks from the SDRAM ring; replacing
that final ring readback with a direct FPGA bulk FIFO is the remaining hardware
transport step needed to remove the ring from the live path entirely.

Each sample trace carries the decoder sequence, ring source index and
producer/oldest pointers, source backlog, `decoded_at`, `gate_accepted_at`,
`published_at`, WebSocket enqueue/send timestamps, and browser receive,
target-selection, state-update, and render timestamps. The Hardware lab shows
the resulting backlog and capture→publish, decode→publish, and
WebSocket→render latencies.

If the board is unplugged, the failed owner can be reclaimed from the visible
`Retry / reconnect` action. Reconnect stops the live worker and ring capture,
closes the old transport, reacquires control, reopens the device, restores the
pending LIS3DH configuration, and waits for a fresh decoded point before
reporting success. A failed attempt leaves the transport and control ownership
clean for another retry after the board is replugged.

## Hardware validation status

The 2026-09-09 persistent seed-10 image (SOF `0x0050492F`) passed the on-board
accelerometer checks as part of the **436/436** connected-board regression.
The persistent 2026-08-27 image retains its historical **383/383** evidence.
The test covers:

- I²C `WHO_AM_I` at 50 kHz and 100 kHz, probing both possible addresses;
- I²C `CTRL_REG1` read;
- repeated SPI mode-3 `WHO_AM_I` reads;
- stable SPI response-offset detection;
- capture-visible I²C and SPI dialogue decoding.

The frontend’s live accelerometer session and waveform screenshot were also
produced against the real backend and physical board. The test is included in
`host/app/hw_validation.py` as `test_accelerometer_whoami` and can be run
directly with:

```powershell
cd host
python -m app.hw_validation accel
```

This validates the analyzer-to-LIS3DH connection and protocol handling. It is
not a full accelerometer functional-characterization suite: it does not claim
calibration accuracy, motion performance, interrupt behavior, or every LIS3DH
register mode.

## Related documentation

- [Signal Generator and Bit Engine](hdl/signal-generator.md)
- [Top-Level Architecture](hdl/top-level-architecture.md)
- [Hardware Validation](hardware-validation.md)
- [Decoder Implementations](backend/decoder-implementations.md)
- [Frontend build and test evidence](frontend/build-and-test.md)

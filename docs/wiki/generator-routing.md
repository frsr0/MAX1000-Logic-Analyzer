# Generator Routing and Bit Banger Contract

The generator is a host-encoded symbol engine. The host converts protocol
frames into 2-bit symbols, the FPGA's `Bit_Engine` shifts them at the selected
divider, and the top-level pin mux routes the two symbol bits to physical pool
entries.

## Signal path

```text
frontend GeneratorConfig
        ↓ REST
backend capability validation/controller
        ↓ host driver
bit_bang.py → 256-byte generator FIFO → Bit_Engine
                                      ├─ Out_0: data/MOSI/SDA
                                      └─ Out_1: clock/SCLK/SCL
                                          ↓ OLS_SDRAM_Top pin mux
                                      26-entry physical pin pool
```

The base two outputs are selected by `REG_GEN_PINS`:

| Register | Bits | Meaning |
|---|---:|---|
| `0x32` | 4:0 | TX/data/MOSI/SDA pool pin |
| `0x32` | 12:8 | SCL/clock/SCLK pool pin |

## Auxiliary routes

`REG_GEN_AUX_PINS` (`0x35`) adds optional routes without changing the two
legacy generator outputs:

| Bits | Field | Meaning |
|---:|---|---|
| 4:0 | DE pin | RS-485 direction-control pool pin |
| 5 | DE enable | Drive DE high while the generator is busy |
| 12:8 | CS pin | SPI chip-select pool pin |
| 13 | CS enable | Drive CS low while an SPI burst is active |
| 20:16 | MISO pin | SPI input pool pin |
| 21 | MISO enable | Sample this pool pin as SPI MISO |

The host writes zero to the unused fields. SPI configuration rewrites the
whole register on every capture so a previous CS route cannot leak into a
later operation.

`REG_GEN_CAPTURE_AUX` (`0x45`) selects direct fast-path capture channels:

| Bits | Field | Meaning |
|---:|---|---|
| 3:0 | CS capture channel | Logical capture channel for CS |
| 4 | CS capture enable | Enable direct CS insertion |
| 11:8 | MISO capture channel | Logical capture channel for MISO |
| 12 | MISO capture enable | Enable direct MISO insertion |

This separate register is important in the FAST build: the runtime general
pin-map write path is intentionally frozen for timing/safety, so SPI auxiliary
signals must enter the capture mux directly. The route is synchronized into
the fast clock domain before sampling.

## Physical pool

| Pool indices | Board connection |
|---:|---|
| `0..14` | MKR_D0..MKR_D14 |
| `15..22` | PMOD[0]..PMOD[7] |
| `23` | LIS3DH `SEN_SDO` |
| `24` | LIS3DH `SEN_SDI` |
| `25` | LIS3DH `SEN_SPC` |

Pins are validated by the backend as `0..25`. Auxiliary pins must not overlap
the protocol's two primary outputs. CS and MISO capture channels must be
distinct logical channels `0..15`.

## Route behavior by protocol

### UART

One output is generated on `tx_pin`. Capture loopback is available when the
physical output is connected to a capture input.

### RS-485

`tx_pin` and `scl_pin` represent the A/B generator outputs. `extra.de_pin` is
optional. When enabled, the FPGA drives DE high for the active Bit_Engine
burst and releases it after completion. The route descriptor advertises
`internal_de_timing` and `de_pin`.

### SPI

`tx_pin` is MOSI and `scl_pin` is SCLK. `extra.cs_pin` optionally selects a
GPIO CS output. If omitted, the board-specific sensor CS behavior remains
available. `extra.miso_pin` selects a GPIO input; the board's sensor SDO is
pool pin `23` and is the default hardware input route.

`extra.cs_capture_channel` and `extra.miso_capture_channel` select where the
auxiliary signals appear in the captured 16-bit sample word. The backend
rejects duplicate capture channels or pins that overlap MOSI/SCLK.

### I²C, SWD, and Bit Banger

I²C and SWD use the two base outputs and their protocol-specific host symbol
encoders. SWD capture is useful with an electrically connected target; a
disconnected target cannot provide response bits. Bit Banger exposes the raw
bounded symbol list and does not add open-drain hardware—open-drain behavior
must be represented by host symbols and external wiring.

## API example

```json
{
  "protocol": "spi",
  "data_hex": "9F",
  "baud": 1000000,
  "tx_pin": 3,
  "scl_pin": 1,
  "extra": {
    "cs_pin": 7,
    "miso_pin": 23,
    "cs_capture_channel": 14,
    "miso_capture_channel": 15
  }
}
```

The frontend exposes these fields only when the connected device advertises
the corresponding route features. The backend remains authoritative and
validates the request before any register writes occur.

## Debugging checklist

1. Call `GET /api/generator/capabilities` and confirm the route features.
2. Check pool-pin conflicts and capture-channel conflicts.
3. Confirm the FPGA image contains the auxiliary registers `0x35` and `0x45`.
4. Capture enough samples to include the complete generator burst.
5. Decode with SCLK, MOSI, MISO, and CS mapped to the configured channels.
6. If SPI produces no decoder events, inspect CS polarity and verify the CS
   capture channel goes active during the burst.
7. Run `backend/hw_smoke_test.py` after any RTL reprogramming.

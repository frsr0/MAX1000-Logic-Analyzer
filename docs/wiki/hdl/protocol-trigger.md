# Pattern Trigger: `Generic_Pattern_Trigger`

**File:** `hdl/rtl/Generic_Pattern_Trigger.vhd` (3.5 KB)

## Purpose

Protocol-independent sampled pattern trigger.  It samples a selected data
channel at a configurable baud rate, shifts the bits into a 32-bit frame,
and asserts `Trigger` when the frame matches a configured value and mask.

The trigger supports both free-running mode (samples continuously, fires
every `width` bits) and start-qualified mode (waits for a start edge on a
selectable channel, then samples `width` bits centred on each bit period —
UART-compatible).

Enabled by `pattern_ctrl(0) = '1'` in `OLS_Interface.vhd`.  When enabled,
`Run` is gated on `pattern_trigger` — the capture starts only when the
trigger fires.

## Interface

| Port | Width | Direction | Domain | Description |
|---|---|---|---|---|
| `CLK` | 1 | IN | sys_clk | System clock (100 MHz) |
| `Inputs` | 16 | IN | async | Digital capture inputs |
| `Enable` | 1 | IN | — | Module enable |
| `Data_Channel_0` | — | IN | — | Selected data input index (0..15) |
| `Clock_Channel` | — | IN | — | External clock channel index (for clock_source=external) |
| `Clock_Source` | 1 | IN | — | 0=internal baud, 1=external edge |
| `Clock_Edge` | 1 | IN | — | 0=rising, 1=falling (external clock) |
| `Start_Mode` | 1 | IN | — | 0=free_run, 1=edge_on_channel |
| `Start_Polarity` | 1 | IN | — | 0=falling edge, 1=rising edge |
| `Start_Channel` | — | IN | — | Channel for start edge detection |
| `Bit_Order` | 1 | IN | — | 0=LSB-first (host reverses value/mask), 1=MSB-first |
| `Baud_Div` | 16 | IN | — | Internal baud rate divider (samples every `Baud_Div` CLK cycles) |
| `Frame_Width` | 5 | IN | — | Number of bits per frame (1..32) |
| `Match_Value` | 32 | IN | — | Pattern to match (wire order, host-reversed for LSB-first) |
| `Match_Mask` | 32 | IN | — | Bits to compare (1=compare, 0=don't-care) |
| `Trigger` | 1 | OUT | sys_clk | Trigger pulse on match, one CLK cycle wide |

## Operation

### Start mode = free_run

Sampling begins immediately after reset.  Every `Baud_Div` CLK cycles, the
input on `Data_Channel_0` is shifted into the frame register (LSB first).
When `bit_count >= Frame_Width`, the frame is compared:

    (frame XOR Match_Value) AND Match_Mask = 0  →  Trigger <= '1'

`Match_Mask = 0` matches any pattern (used for FSM-only validation in
Test 14f).  After firing, the frame resets and sampling continues.

### Start mode = edge_on_channel

Waits for a qualified edge on `Start_Channel` (polarity controlled by
`Start_Polarity`).  After the edge, waits half a bit period (`Baud_Div / 2`
CLK cycles), then samples the data channel every `Baud_Div` cycles.  This
places each sample at the centre of the bit period — the standard UART
sampling strategy.

After `width` samples the frame is compared.  On match, `Trigger` asserts
and the capture begins.  On mismatch, the trigger re-arms and waits for
the next start edge.

### Bit ordering

The frame register is always wire-order (first received bit in bit 0, last
in bit `width-1`).  For LSB-first protocols (UART), the host driver bit-
reverses the match value and mask before writing the register so the
comparison is correct.

## Integration

The `Generic_Pattern_Trigger` is instantiated in `OLS_Interface.vhd` and
wired to the capture `Inputs` bus.  Its control registers are exposed at
addresses `0x12`–`0x16`:

| Register | Address | Content |
|---|---|---|
| `REG_PATTERN_CTRL` | 0x12 | Enable, clock source/edge, start mode/polarity, bit order, start/clock channel, frame width |
| `REG_PATTERN_CHANNELS` | 0x13 | Data channel selector |
| `REG_PATTERN_VALUE` | 0x14 | Match value |
| `REG_PATTERN_MASK` | 0x15 | Match mask |
| `REG_PATTERN_BAUD` | 0x16 | Baud rate divider |

The OLS_Interface control logic uses `pattern_ctrl(0)` to select between
the register-based trigger modes (level/edge via `REG_TRIGGER_MASK`) and the
pattern trigger.  When `pattern_ctrl(0) = '1'`, `Run` is gated on
`pattern_trigger`; the capture only starts when the pattern fires.

## Board validation

The Generic_Pattern_Trigger is validated at **HW** level on SOF `0x00515DB0`
(seed 44, 2026-07-23):

| Test | What it proves |
|---|---|
| **14f** — `test_generic_pattern_trigger_hw` | Free-run mode, match_mask=0: baud counter, shift register, comparator all function (internal FSM, no external signal) |
| **14g** — `test_generic_pattern_trigger_jumper` | Start-qualified UART byte match: Bit_Engine sends 0x55 through physical jumper, trigger samples 8 bits at baud_div, matches with mask=0xFF, capture fires |

See [hardware-validation.md](../hardware-validation.md) for the full
validation report.

## Known Limitations

- Single data channel (coarse match; multi-channel patterns are refined in
  software after capture)
- External clock source (clock_source=1) not tested in HW validation
- No I2C/SPI pattern matching in hardware (post-capture software search
  available)
- Former `Protocol_Trigger` (UART byte match on decoded RX data) is retired;
  the software `trigger_decode()` handles UART byte matching post-capture

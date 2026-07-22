# Protocol Trigger: `Protocol_Trigger`

**File:** `hdl/rtl/Protocol_Trigger.vhd` (2.7 KB)

## Purpose

Implements a hardware trigger on UART byte patterns. The trigger fires when a specific byte value is received on a selected UART RX channel, enabling trigger-while-capture for serial protocol analysis.

## Interface

| Port | Width | Direction | Description |
|---|---|---|---|
| `CLK` | 1 | IN | System clock |
| `rx_data` | 8 | IN | UART RX data byte |
| `rx_valid` | 1 | IN | RX data valid |
| `trigger_byte` | 8 | IN | Target byte to match |
| `trigger_mask` | 8 | IN | Bit mask for don't-care bits |
| `armed` | 1 | IN | Trigger armed |
| `trigger_fired` | 1 | OUT | Trigger match detected |

## Operation

- When `armed`, monitors `rx_data` for a byte matching `(rx_data & trigger_mask) == (trigger_byte & trigger_mask)`
- On match, asserts `trigger_fired` and de-asserts `armed`
- The capture engine uses `trigger_fired` to start post-trigger capture

## Dependencies

| Component | File |
|---|---|
| `UART_Interface` | `UART_Interface.vhd` |
| `OLS_Interface` | `OLS_Interface.vhd` |

## Known Limitations

- Only UART byte trigger implemented
- No I2C/SPI pattern matching in hardware (post-capture software search available)

## Testing

| Testbench | What it covers |
|---|---|
| `tb_protocol_trigger.vhd` | UART byte trigger match/no-match scenarios |

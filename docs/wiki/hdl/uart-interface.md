# UART Interface: `UART_Interface`

**File:** `hdl/rtl/UART_Interface.vhd` (6.4 KB)

## Purpose

Asynchronous UART receiver for the protocol trigger subsystem. Detects start bits, samples data at the configured baud rate, and presents received bytes to the protocol trigger module.

## Entity Ports

| Port | Width | Direction | Description |
|---|---|---|---|
| `CLK` | 1 | IN | System clock (100 MHz) |
| `rxd` | 1 | IN | UART RX line |
| `baud_div` | 16 | IN | Baud rate divider = sys_clk / baud |
| `data` | 8 | OUT | Received byte |
| `valid` | 1 | OUT | Byte received strobe |
| `framing_error` | 1 | OUT | Stop bit not detected |

## Operation

- Samples `rxd` at sys_clk rate
- Detects start bit (falling edge on `rxd`, sampled mid-bit)
- Samples 8 data bits at the baud rate (oversampling at sys_clk/baud_div intervals)
- Checks for valid stop bit (logic 1) — asserts `framing_error` if missing
- Outputs `data` and `valid` strobe on complete byte

## Baud Rates

The baud rate divider is configured via `REG_GEN_BAUD` (0x31) and written through `CMD_WRITE_REG`. At 100.2 MHz sys_clk:

| Baud | Divider Value |
|---|---|
| 9600 | 10437 |
| 115200 | 870 |
| 230400 | 435 |
| 460800 | 217 |
| 921600 | 109 |

## Dependencies

| Component | File |
|---|---|
| `Protocol_Trigger` | `Protocol_Trigger.vhd` |
| `Signal_Gen` (for TX) | `Signal_Gen.vhd` |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_uart_interface.vhd` | UART RX: start bit, data, stop bit, framing error |

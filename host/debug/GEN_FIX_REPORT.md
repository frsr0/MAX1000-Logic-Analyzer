# Generator Startup Failure — Root Cause Analysis and Fix

## 1. Root Cause

**`send_uart()` (ols_spi_device.py:143-152) loaded FIFO data, configured protocol/baud/pins — but NEVER called `start_gen()`.**

No `0xA1` (CMD_GEN_STRT) byte was transmitted to the FPGA. Consequently:
- `Gen_Start` never pulsed
- `Signal_Gen` never asserted `tx_active`
- `gen_busy` remained `0`
- The generator never ran

### Affected callers

| Caller | Status |
|---|---|
| `send_uart()` | **Never started gen** |
| `capture_with_gen()` | **ARM burst had no GEN_STRT** (despite comment claiming it did) |
| `rolling_capture()` | **No `start_gen()` after FIFO load** |

## 2. Exact Byte Sequences

### Before fix — `send_uart()` (NO gen start)
```
[0xA4, 0x00, 0x00, 0x00, 0x00]  — CMD_GEN_PROTO = 0 (UART)
[0xA2, 0xD0, 0x00, 0x00, 0x00]  — CMD_GEN_BAUD = 208 (115200 @ 24 MHz)
[0xA3, n, 0, 0, 0] + n bytes    — CMD_GEN_BLK + FIFO data
[0xA6, 0x03, 0x01, 0x00, 0x00]  — CMD_GEN_PINS tx=3, scl=1
*** 0xA1 NEVER SENT ***
```

### Before fix — `start_gen()` (harmful padding)
```
[0xA1, 0x00, 0x00, 0x00, 0x00]  — 0x00 = CMD_RESET
```
Trailing `0x00` decodes as CMD_RESET (OLS_Interface.vhd:528-545), clearing `Run_OLS`, `Run`, `Gen_Baud_Div`, `Gen_Proto`, `blk_mode`. While the Gen_Start pulse fires before the RESET (4 CLK cycles = 167 ns vs 8 µs SPI byte gap), and Signal_Gen survives (latches Baud_Div on Start), this is fragile and resets OLS_Interface config.

### After fix — `send_uart()` + `start_gen()`
```
[0xA4, 0x00, 0x00, 0x00, 0x00]  — CMD_GEN_PROTO = 0 (UART)
[0xA2, 0xD0, 0x00, 0x00, 0x00]  — CMD_GEN_BAUD = 208
[0xA3, n, 0, 0, 0] + n bytes    — CMD_GEN_BLK + FIFO data
[0xA6, 0x03, 0x01, 0x00, 0x00]  — CMD_GEN_PINS tx=3, scl=1
[0xA1, 0x11, 0x11, 0x11, 0x11]  — CMD_GEN_STRT with safe 0x11 padding
```

## 3. Code Changes

### ols_spi_device.py — `send_uart()` (line 143-155)
Added `self.start_gen()` after configuration commands complete.

### ols_spi_device.py — `start_gen()` (line 157-165)
```python
# OLD: self._long(CMD_GEN_STRT, 0)  → packs 0 as b'\x00\x00\x00\x00'
# NEW: self.spi.tx(CMD_GEN_STRT, b'\x11\x11\x11\x11')  → safe NOP padding
```

### ols_spi_device.py — `fast_start_gen()` (line 167-173)
Same fix — explicit `b'\x11\x11\x11\x11'` instead of default `b'\x00\x00\x00\x00'`.

### ols_spi_device.py — `capture_with_gen()` (line 272-290)
Added GEN_STRT with 0x11 padding in the CS-low burst before ARM:
```python
# OLD: [CMD_ARM, 0x11, 0x11, 0x11, 0x11] only
# NEW: [CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11] + [CMD_ARM, 0x11, 0x11, 0x11, 0x11]
```

### ols_spi_device.py — `rolling_capture()` (line 421)
Added `self.start_gen()` after FIFO load and `self.spi.flush()`.

## 4. Simulation Evidence

### `tb_send_uart.vhd` — Full `send_uart()` sequence (PASS)
```
CMD_RESET...
CMD_GEN_PROTO = 0 (UART)...
CMD_GEN_BAUD = 208...
CMD_GEN_BLK + 4 bytes...
CMD_GEN_PINS tx=3 scl=1...
CMD_GEN_STRT with 0x11 padding...
PASS: Gen_Start pulsed
PASS: Gen_Busy went high (gen running)
PASS: Gen_TX toggled (UART data transmitted)
PASS: Gen_Busy went low after transmission complete
*** send_uart TEST COMPLETE ***
```

### `tb_old_behavior.vhd` — OLD vs NEW padding comparison
```
=== OLD: GEN_STRT with 0x00 padding ===
PASS: Gen_Start pulsed
PASS: Gen_Busy went high
OBSERVED: Gen_Baud_Div RESET from 208 to 833  ← CMD_RESET confirmed

=== NEW: GEN_STRT with 0x11 padding ===
PASS: Gen_Baud_Div still 208 (NOT reset - 0x11 is NOP)
PASS: Gen_Start pulsed
PASS: Gen_Busy went high
```

### Existing tests — unchanged, all pass
- `tb_direct_path.vhd`: Gen_Start pulsed, Gen_Busy high, Gen_TX toggled — PASS
- `tb_gen_strt.vhd`: Gen_Start pulsed after 0xA1 — PASS

## 5. Software Regression Tests

```
host/test_gen_seq.py — all 4 tests pass:
  test_fast_start_gen_padding: transmits [a1 11 11 11 11]
  test_old_padding_was_reset: confirms 0x00 = CMD_RESET
  test_send_uart_start_gen_called: PROTO -> BAUD -> PINS -> STR with 0x11
  test_start_gen_padding: transmits [a1 11 11 11 11]
```

## 6. Remaining Issues

None identified. The generator now starts correctly via all paths:
- `send_uart()` → auto-starts after config
- `start_gen()` → safe padding
- `fast_start_gen()` → safe padding
- `capture_with_gen()` → GEN_STRT in ARM burst
- `rolling_capture()` → auto-starts after FIFO load

All simulation testbenches pass. Hardware validation confirms gen_busy toggles and UART data is transmitted.

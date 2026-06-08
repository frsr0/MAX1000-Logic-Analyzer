#!/usr/bin/env python3
"""Check if CSR_RUN_CMD (0x05) works for ARM instead."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

def raw_xfer(spi, payload):
    spi._drain()
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload)-1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    spi.dev.write(buf)
    time.sleep(0.005)
    return spi._read_all(timeout=0.050)

def preamble(spi):
    r = raw_xfer(spi, bytes([0x11]))
    return r[0] if r else None

def status(p):
    return {"RO":(p>>6)&1,"Dbg":(p>>1)&1}

spi = OLS(speed_hz=12_000_000)
spi.open()

# Reset to known state
raw_xfer(spi, bytes([0x00])); time.sleep(0.02)
print(f"Reset: 0x{preamble(spi):02x}")

# Send 0x0C (debug on) to confirm commands work
raw_xfer(spi, bytes([0x0C]))
p = preamble(spi)
print(f"After 0x0C: 0x{p:02x} Dbg={status(p)['Dbg']}")

# Now send 0x06 (CMD_ARM2 alternative? Actually let me check)
# 0x06 sends Thread44 to Thread44+8 = 8 in single-byte mode
# 0x05 sends Thread44 to Thread44+7 = 7 in single-byte mode

# 0x05: Thread44=7 -> saved_command = 0x05, which doesn't match any multi-byte case
# Thread44+10 = 17 (others case at line 978) -> Thread44=17
# Thread44=17: just null/reset

# 0x06: Thread44=8 -> Gen_Proto <= data(0) (from state 5, Thread44=8)
# That's for proto config...

# Let me try what happens if I set Run_OLS via continuous mode (0xAA,1)
# CMD_CONTINUOUS with data=1 sets Run_OLS <= '1'  
# But as multi-byte: [0x11, 0xAA, 0x01, 0x00, 0x00, 0x00]

# Actually let me try: what if the issue is that when we're in SPI single-byte mode,
# 0x01 is being caught by Thread38=4 as a single-byte command with cmd_was_multibyte?
# No, the flow analysis says it should go to Thread38=5, Thread44=0, command=x"01"

# Let me try setting Run_OLS via the "back door" at Thread44=2
# Thread44=2 in state 5: UART_TX_Data <= x"BB", spi_tx_fifo_clear, Run_OLS <= '1'
# How to get Thread44=2? command=0x01 with Thread44=0 goes to Thread44 stays 0
# Actually command=0x00 with Thread44=0 goes to Thread44=1
# Thread44=1 does reset.
# Then... Thread44=2 is the "alternate ARM". But no command goes to Thread44=2 directly.

# Thread44=2 at Thread38=5: Run_OLS <= '1' (line 618)
# So how does a byte reach Thread44=2? 
# If Thread44 is 2 at state 4, and a byte arrives at Thread38=3...
# Actually, Thread44 is only changed in state 5 and 6.

# Hmm, but I notice something at state 4, when command=x"11" (0x11):
# Thread44 := Thread44 + 4 at state 5 (line 584)
# After: Thread38=5, Thread44=4 -> cleared back to 0

# What if I do a multi-byte format that goes through Thread44=7 (the accumulate path)?
# That requires cmd_was_multibyte='1' with saved_command=x"11" and data(7:0)=x"01"

# Wait, for the 6-byte ARM format: [0x11, 0x01, 0x11, 0x11, 0x11, 0x11]
# Byte 1 (0x11): goes state 3->4->5, Thread44=4, cleared. cmd_was_multibyte='0'
# Byte 2 (0x01): command=0x01, cmd_was_multibyte='0', Thread38=5, Thread44=0
#   → handles ATM via x"01" case!
# Byte 3-6: NOPs, cleared

# So the SINGLE-byte handler (Thread38=5, Thread44=0, command=x"01") runs!
# But Run_OLS stays 0.

# WAIT — maybe the problem is that UART_TX_Data <= x"AA" and spi_tx_fifo_clear
# cause a COMBINATIONAL LOOP through the spi_tx_fifo_proc or the SPI_TX_Data mux?

# Let me check: spi_tx_fifo_clear <= '1' in the main process.
# The spi_tx_fifo_proc checks: if spi_tx_fifo_clear = '1' then...
# This also runs on rising_edge(CLK). So the FIFO clear happens on the NEXT cycle.

# The SPI_TX_Data mux: uses spi_tx_fifo_count (from the same clock cycle)
# When spi_tx_fifo_count becomes 0 (next cycle after clear):
#   SPI_TX_Data <= UART_TX_Data (which is now 0xAA)
# This is correct.

# BUT: what if UART_TX_Data <= x"AA" triggers a UART transmission?
# No, UART_TX_Enable is not set.

# What about TX_Ready? It goes high then low (pulse) after each byte.
# If TX_Ready goes high, the fifo's next_count logic decrements.

# Actually, there might be a subtle issue with the fifo logic:
# When spi_tx_fifo_clear = '1', some TX data is discarded. But this shouldn't
# affect Run_OLS.

# Let me try the most important test: 
# Try setting CMD_CONTINUOUS (0xAA) with data=1 to see if THAT sets Run_OLS.
# Because Thread44=28 handler says IF data(0)='1' then Run_OLS <= '1'

print("\n=== Try via continuous mode ===")
# Multi-byte: [0x11, 0xAA, 0x01, 0x00, 0x00, 0x00]
r = raw_xfer(spi, bytes([0x11, 0xAA, 0x01, 0x00, 0x00, 0x00]))
print(f"Set Cont=1: {r.hex()}")
time.sleep(0.01)
p = preamble(spi)
print(f"After: 0x{p:02x} RO={status(p)['RO']} Cont={(p>>3)&1}")
# Check if Cont is now 1
# Then check Run_OLS

# Reset
raw_xfer(spi, bytes([0x00])); time.sleep(0.02)

# Try set both fast mode (0xA8,1) AND cont (0xAA,1) then check if ARM works
r = raw_xfer(spi, bytes([0x11, 0xA8, 0x01, 0x00, 0x00, 0x00]))
print(f"Set Fast=1: {r.hex()}")
r = raw_xfer(spi, bytes([0x11, 0xAA, 0x01, 0x00, 0x00, 0x00]))
print(f"Set Cont=1: {r.hex()}")
time.sleep(0.01)
p = preamble(spi)
print(f"After fast+cont: 0x{p:02x} Fast={(p>>2)&1} Cont={(p>>3)&1} RO={(p>>6)&1}")

# Now try ARM
raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi)
print(f"After ARM(fc): 0x{p:02x} RO={status(p)['RO']}")

# Well - does continuous_mode SET Run_OLS?
# Line 828: IF data(0)='1' THEN ... Run_OLS <= '1'

spi.close()

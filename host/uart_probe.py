#!/usr/bin/env python3
"""Probe UART protocol-trigger capture timing on CH3."""

import time

from app.OLS_Console import decode_uart, samples_to_channels
from driver.ols_spi_device import OLSDeviceSPI

RATE = 2_000_000
BAUD = 115200
TX_PIN = 3
PAYLOAD = b"Hello"


def main():
    dev = OLSDeviceSPI()
    dev.open()
    try:
        dev.reset()
        time.sleep(0.5)
        dev.trigger_decode(match_byte=PAYLOAD[0], channel=TX_PIN,
                           baud=BAUD, enable=True)
        dev._gen_data = PAYLOAD
        dev._gen_baud = BAUD
        dev._gen_tx_pin = TX_PIN
        data = dev.capture_with_gen(rate_hz=RATE, nsamples=5000, timeout=10)
        ch, ns = samples_to_channels(data, stride=2)
        c3 = ch[TX_PIN]

        spb = RATE / BAUD
        runs = []
        cur = c3[0] if c3 else 1
        cnt = 1
        for value in c3[1:]:
            if value == cur:
                cnt += 1
            else:
                runs.append((cur, cnt))
                cur = value
                cnt = 1
        if c3:
            runs.append((cur, cnt))

        print("RATE %d, samp/bit %.1f, %d samples, %d runs"
              % (RATE, spb, ns, len(runs)))
        print("CH3 runs (val:bits):",
              " ".join("%d:%.1f" % (v, c / spb) for v, c in runs[:40]))
        decoded = decode_uart(ch, RATE, ch_idx=TX_PIN, baud=BAUD)
        print("decoded %d bytes:" % len(decoded),
              [hex(b.value) for b in decoded[:8]])
    finally:
        try:
            dev.trigger_decode(enable=False)
        finally:
            dev.close()


if __name__ == "__main__":
    main()

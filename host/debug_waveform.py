"""Analyze CH3 waveform to determine if gen_busy is active."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
if data:
    ch, ns = samples_to_channels(data)
    ch3 = ch[3]
    ch0 = ch[0]
    
    # Count CH0 transitions to verify sample rate
    ch0_tr = sum(1 for i in range(1, ns) if ch0[i] != ch0[i-1])
    print(f'CH0 transitions: {ch0_tr} (expect ~234 at 1 MHz)')
    print(f'CH0 ones: {sum(ch0)}/{ns}')
    
    # CH3 analysis
    ch3_tr = sum(1 for i in range(1, ns) if ch3[i] != ch3[i-1])
    ch3_zeros = sum(1 for v in ch3 if v == 0)
    print(f'CH3 transitions: {ch3_tr}')
    print(f'CH3 zeros: {ch3_zeros}/{ns}')
    
    # Find first activity region
    for i in range(ns):
        if ch3[i] == 0:
            zero_start = i
            break
    
    # Count consecutive zeros from start
    zero_run = 0
    for i in range(min(200, ns)):
        if ch3[i] == 0:
            zero_run += 1
        else:
            break
    
    print(f'Leading zeros: {zero_run}')
    
    # If gen is running, the first transition (start bit) should occur
    # within ~87 samples at 115200 baud (1 MHz sample rate)
    first_one = None
    for i in range(min(500, ns)):
        if ch3[i] == 1:
            first_one = i
            break
    if first_one is not None:
        print(f'First 1 at sample {first_one}')
    
    first_zero_to_one = None
    for i in range(1, min(500, ns)):
        if ch3[i-1] == 0 and ch3[i] == 1:
            first_zero_to_one = i
            break
    if first_zero_to_one is not None:
        print(f'First 0->1 at sample {first_zero_to_one}')
    
    # If gen_busy is high, CH3 = gen_tx. If gen_busy is low, CH3 = GPIO(3) = 0
    # Check if there's ANY 1 in CH3 (gen_tx must output 1 for stop bits and idle)
    has_any_one = any(ch3)
    print(f'Any 1 on CH3: {has_any_one}')
    
    # Count regions of 1s and 0s
    ones_runs = []
    zeros_runs = []
    prev = ch3[0]
    run_start = 0
    for i in range(1, ns):
        if ch3[i] != prev:
            if prev == 0:
                zeros_runs.append((run_start, i - run_start))
            else:
                ones_runs.append((run_start, i - run_start))
            run_start = i
            prev = ch3[i]
    if run_start < ns:
        if prev == 0:
            zeros_runs.append((run_start, ns - run_start))
        else:
            ones_runs.append((run_start, ns - run_start))
    
    print(f'Number of 0-runs: {len(zeros_runs)}')
    print(f'Number of 1-runs: {len(ones_runs)}')
    if zeros_runs:
        avg_z = sum(r[1] for r in zeros_runs) / len(zeros_runs)
        print(f'Avg 0-run length: {avg_z:.1f}')
    if ones_runs:
        avg_o = sum(r[1] for r in ones_runs) / len(ones_runs)
        print(f'Avg 1-run length: {avg_o:.1f}')
    
    # Expected at 115200 baud, 1 MHz sample rate: ~8.7 samples per bit
    # A UART start bit = 0 for ~8.7 samples
    # Data + stop = 1 for ~8.7 samples per bit
    print(f'\nFirst 20 run lengths:')
    prev = ch3[0]
    run_start = 0
    run_count = 0
    for i in range(1, min(500, ns)):
        if ch3[i] != prev:
            length = i - run_start
            print(f'  {prev} for {length} ({length/8.68:.1f} bits)')
            run_count += 1
            if run_count >= 20:
                break
            run_start = i
            prev = ch3[i]

dev.close()

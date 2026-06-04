import sys, time, threading
sys.path.insert(0, '.')
from OLS_Console import OLSDevice, find_port, samples_to_channels, CMD_RESET

SYS_CLK = 48000000
rates = [375000, 500000, 750000, 1000000, 1250000, 1500000, 1750000, 2000000,
         2250000, 2500000, 2750000, 3000000, 3500000, 4000000, 5000000, 6000000]

print(f"{'MHz':>8} {'Hz':>9} {'Edges':>7} {'Gaps':>5} {'Std':>8} {'Consist':>10}")
print("-" * 50)

port = find_port()
for r in rates:
    r_mhz = r / 1e6
    stop = threading.Event()
    dev = OLSDevice(port)
    dev.raw_mode(True)
    cap = bytearray()

    # Hard reset: send many resets to clear FPGA state
    for _ in range(10):
        dev.ser.write(bytes([CMD_RESET]))
        time.sleep(0.01)
    time.sleep(0.2)
    dev.ser.reset_input_buffer()

    try:
        gen = dev.rolling_capture(rate_hz=r, chunk_nsamp=1024, buffer_nsamp=50000,
                                  stop_evt=stop, full_out=cap, use_continuous=True)
        for i in range(10):
            next(gen)
    except StopIteration:
        pass
    except:
        pass
    finally:
        stop.set()
        for _ in range(5):
            dev.ser.write(bytes([CMD_RESET]))
            time.sleep(0.01)

    ns = len(cap)
    if ns < 100:
        print(f"{r_mhz:>8.4f} {r:>9} {'--':>7}")
        continue

    ch_data, _ = samples_to_channels(bytes(cap), stride=1)
    ch0 = [ch_data[0][i] for i in range(0, len(ch_data[0]), 2)]
    edges = [i for i in range(1, len(ch0)) if ch0[i] != ch0[i-1]]

    if len(edges) < 3:
        # dev closed by rolling_capture finally
        print(f"{r_mhz:>8.4f} {r:>9} {'--':>7}")
        continue

    sp = [edges[i+1] - edges[i] for i in range(len(edges)-1)]
    exp = round(256 * r / SYS_CLK)
    margin = max(2, exp // 4)
    gaps = len([s for s in sp if s > exp + margin])
    avg_s = sum(sp) / len(sp)
    std_s = (sum((s - avg_s)**2 for s in sp) / len(sp)) ** 0.5
    max_dev = max(abs(s - exp) for s in sp)
    consist = "YES" if max_dev <= 1 else f"NO({max_dev})"
    # dev closed by rolling_capture finally

    print(f"{r_mhz:>8.4f} {r:>9} {len(edges):>7} {gaps:>5} {std_s:>8.4f} {consist:>10}")

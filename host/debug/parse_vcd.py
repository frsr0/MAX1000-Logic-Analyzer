"""Parse VCD and print signals of interest."""
import sys

vcd_path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\hdl\tb_spi_protocol.vcd"

with open(vcd_path, 'r') as f:
    content = f.read()

# Parse variable definitions
vars = {}
in_defs = True
for line in content.splitlines():
    if in_defs:
        if line.startswith("$var"):
            parts = line.split()
            if len(parts) >= 4:
                width = int(parts[1])
                code = parts[2]
                name = parts[3]
                vars[code] = (width, name)
        elif line.strip() == "$enddefinitions":
            in_defs = False

print(f"Found {len(vars)} signals")
for code, (w, name) in list(vars.items())[:30]:
    print(f"  {code:4s}: {w} {name}")

# Find signals of interest
interesting = ['rx_byte', 'rx_valid', 'state', 'packet_ok', 'packet_err', 'sync_reg', 'crc_acc', 'crc_rx', 'rx_cmd', 'rx_seq', 'rx_len']
sig_codes = {}
for code, (w, name) in vars.items():
    if name in interesting:
        sig_codes[name] = code
        
print(f"\nMatch codes: {sig_codes}")

# Parse timeline
times = {}
current_time = 0
for line in content.splitlines():
    if line.startswith('#'):
        current_time = int(line[1:])
    elif line.startswith('b'):
        parts = line.split()
        val_bits = parts[0][1:]
        code = parts[1]
        if code in vars:
            w, name = vars[code]
            if current_time not in times:
                times[current_time] = {}
            # Convert binary string to hex for display
            if w <= 8:
                val = int(val_bits, 2) if val_bits else 0
                times[current_time][name] = hex(val)
            elif w <= 16:
                val = int(val_bits, 2) if val_bits else 0
                times[current_time][name] = f"0x{val:04x}"
            else:
                times[current_time][name] = val_bits[:40] + "..." if len(val_bits) > 40 else val_bits
    elif len(line) == 1 and not line.startswith('#'):
        code = line.strip()
        if code in vars:
            w, name = vars[code]
            if current_time not in times:
                times[current_time] = {}
            times[current_time][name] = '1'

# Print at key times
print(f"\nTimeline ({len(times)} entries):")
prev = {n: None for n in interesting}
for t in sorted(times.keys()):
    sigs = times[t]
    changes = []
    for n in interesting:
        v = sigs.get(n)
        if v is not None and v != prev.get(n):
            changes.append(f"{n}={v}")
            prev[n] = v
    if changes:
        print(f"@{t:6d}ps: {'  '.join(changes)}")

# Wide-Divider Bitstream Rebuild (24-bit REG_GEN_BAUD)

The Bit_Engine's `REG_GEN_BAUD` divider was widened from 16 to 24 bits so the
generator can emit any symbol rate down to ~6 Hz at 100 MHz (the 16-bit image
floors at ~1.5 kHz; 1200 baud silently ran ~5.6 kHz). This documents the
Quartus rebuild, the timing-margin gate, and the post-flash validation.

## What changed (already in the tree)

- `hdl/rtl/Bit_Engine.vhd` — `Bit_Div` port and `baud_acc` are now 24 bits.
- `hdl/rtl/OLS_Interface.vhd` — `REG_GEN_BAUD` stores/reads back 24 bits;
  `CMD_GET_METADATA` now returns a 10th byte: feature flags, bit0 = 24-bit
  divider.
- `hdl/rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd`, `OLS_SDRAM_Top.vhd` — port
  widths follow.
- `hdl/tb/tb_bit_engine_div24.vhd` — cycle-exact testbench: measures the
  out_0 period for `Bit_Div` 1000, 83499 (1200 baud at 100.2 MHz) and 65536,
  asserting the 24-bit divider holds the full value (truncation to 16 bits
  would produce ~35.9k cycles instead of 167,003).

Verified with GHDL 6.0 (mcode): `Bit_Engine.vhd`, `OLS_Interface.vhd` and
`OLS_Logic_Analyzer_SDRAM_Core.vhd` analyze clean; the divider testbench
measured out_0 periods of 2005 / 167003 / 131077 cycles for Bit_Div
1000 / 83499 / 65536 — exactly the 24-bit model `2*div + 5` per two symbols
(4 ticks of div+1 plus the byte-boundary LOAD and M9K FIFO-read latency).
The 83499 case is the regression: a 16-bit register would truncate it to
17963 and produce 35931 cycles (~5.6 kHz instead of 1.2 kHz).

## Rebuild (on the Quartus machine)

```
cd hdl\proj
.\compile.ps1 -Flash -Seed 44
```

Compile only (no flash) if you want to inspect timing first:

```
.\compile.ps1 -Seed 44        # then check timing, then re-run with -Flash
```

The script regenerates `OLS_Logic_Analyzer_wrapper.vhd` from
`pin_assignments.csv` and compiles `OLS_Logic_Analyzer` in `hdl/proj`.

## Timing gate — BEFORE flashing

The Bit_Engine runs in the `sys_clk` domain; the widening adds 8
flip-flops to `baud_acc` and 8 bits to the accumulator comparator — the
lowest-margin change possible in that domain. Baseline (seed 29, Slow
1200 mV 85 C setup):

| Domain          | Baseline slack |
|-----------------|----------------|
| `fast_clk`      | +0.084 ns      |
| `sdram_core_clk`| +0.087 ns      |
| `sys_clk`       | +0.403 ns      |
| `SPI_SCK_EXT`   | +11.290 ns     |

All hold checks positive (fast_clk +0.342, sys_clk +0.332, sdram_core
+0.286, SPI_SCK_EXT +0.394).

Gate: extract the four setup slacks from the compile report
(`OLS_Logic_Analyzer.fit.rpt`) or `quartus_sta`. **Do not flash if any
setup or hold slack is negative.** The Bit_Engine change is expected to cost
a few ps in `sys_clk`; if `sys_clk` closes below ~+0.1 ns, re-sweep fitter
seeds (the repo's `seed_sweep.ps1`) before flashing.

## Post-flash hardware validation (this machine)

1. `python backend/hw_smoke_test.py` — 10/10 must pass.
2. `python host/debug/rate_sweep_probe.py` — measures the on-wire symbol
   rate for 1200 / 2400 / 9600 / 19200 / 57600 / 115200 baud against the
   requested rate; every case must be within ~2%.
3. Full regression: `host/app/hw_validation.py`, then backend/host/frontend
   suites, then the 37-capture hardware e2e matrix.

After the wide-divider image is flashed, `CMD_GET_METADATA` returns byte 9
with bit0 set, and the host driver (`_detect_gen_div_width`) switches from
the 16-bit mask (0xFFFF) to 24-bit (0xFFFFFF) automatically — no host
rebuild or config needed. The 16-bit bitstream keeps working unchanged with
the same host code.

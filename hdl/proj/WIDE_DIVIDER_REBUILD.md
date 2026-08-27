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

Quartus Prime Lite 25.1 is installed at `C:\altera_lite\25.1std` on this PC
(`compile.ps1` finds it; override with `$env:QUARTUS_DIR`).

```
cd hdl\proj
.\compile.ps1 -Flash -Seed 33
```

The script regenerates `OLS_Logic_Analyzer_wrapper.vhd` from
`pin_assignments.csv` and compiles `OLS_Logic_Analyzer` in `hdl/proj`.

## Timing gate — result (PASSED, seed 10, after init-FSM fix)

Measured under Quartus 25.1 (Slow 1200 mV 85 C setup), comparing the
widened RTL against the pre-widening baseline at the same seed, then a seed
sweep (the design is fitter-seed sensitive at 95-97% LE density):

| Config | best seed | fast_clk | sys_clk | sdram_core_clk | chip_out |
|--------|-----------|----------|---------|----------------|----------|
| Baseline (16-bit) | 5  | +0.023* | -0.084* | +0.206 | +1.098 |
| Widened (24-bit)  | 33 | +0.210  | +0.310 | +0.060 | +1.098 |
| + init_cnt fix    | 10 | +0.083  | +0.410 | +0.111 | +1.098 |

(*baseline best was -0.043 ns overall.)

The tightest cone was the SDRAM controller init FSM: the live 15-bit
`init_cnt < 19999` compare decoded straight into `state`. Registered it
(`init_cnt_done`, same pattern as the existing `timer_ge_ref`/`timer_eq_zero`
comparators); the state transition is now a 1-FF path. Remaining worst paths
are `pretrig_en_r -> narrow_shift_r` (fast_clk) and
`buf_rem_single -> prefetch_valid_r` (sdram_core), both >= +0.083.

**Flashed to the MAX1000 CFM on 2026-08-27 (seed 10, init_cnt fix) — the best
margin this design has shipped with.**

Post-flash validation (all green):
- `python backend/hw_smoke_test.py` — 10/10.
- `python host/debug/rate_sweep_probe.py` — 1200..115200 baud all within
  +0.79% of request.
- Deep 1M-sample @ 200 MHz SDRAM capture, live generator streaming, clean
  stops.

Re-sweep after any RTL/pin change (`.\seed_sweep.ps1 -Seeds @(...)`), and
check the `.sta.summary` numbers before flashing again.

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

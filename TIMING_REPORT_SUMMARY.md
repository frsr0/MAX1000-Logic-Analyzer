# Timing Report Summary
## Status: ✅ **PASSING - No Violations** (seed 30 + rdfifo almost-full fix)


**Report Date:** July 5, 2026 (seed 30 build, STA summary from latest compilation)

> Updated July 8, 2026: FAST_SPEED seed 3 now uses the DDIO-forwarded SDRAM chip clock and the
> `SDRAM_CHIP_CLK_OUT` write cone closes in STA.

> **Updated July 9, 2026 (current):** the July-9 RTL changes pushed the seed-3
> build fast_clk-negative (-0.206 ns @ Slow 85C; a fresh compile caught it —
> the July-8 numbers below predate those commits). Root cause of the worst
> sdram_core_clk cone: the rdfifo dcfifo's combinational `wrfull` (4-level
> gray-code compare) fanned out to the whole 22-bit `s_addr`/`rd_wd_cnt`
> enable cone (65% interconnect). Fixed by a registered almost-full gate
> (`rdfifo_afull_r`, threshold DEPTH-8, safe with the single-outstanding-read
> design) in `Fast_Logic_Analyzer_SDRAM.vhd`; regression-tested via
> tb_fifo_bridge / tb_stream_readout / tb_repeated_blockreads. A re-sweep
> (with the seed_sweep.ps1 best-picker bug fixed — it tracked a nonexistent
> 'adc_clk' and always kept the first seed) found **seed 30** closes every
> domain: Slow-85C setup fast_clk **+0.068**, sdram_core_clk **+0.375**,
> sys_clk **+1.000**, SDRAM_CHIP_CLK_OUT **+1.108**; all holds positive; TNS
> 0.000 everywhere; 6,691/8,064 LE (83%). compile.ps1 default seed is now 30.
> The remaining tightest cone is analog_packer `held[]→out_data[]` on
> fast_clk — the structural candidate if a future RTL change reopens timing.

---

## Key Metrics

### Setup Timing
| Metric | Value | Status |
|--------|-------|--------|
| **Paths Analyzed** | 20 | ✅ |
| **Violated Paths** | 0 | ✅ PASS |
| **Worst Case Slack** | 0.182 ns (clk[1]) | ✅ PASSING (positive) |
| **Filler:** | 0.343 ns (clk[2]), 0.994 ns (clk[0]) | ✅ All positive |

### Timing by Clock Domain

**SDRAM Clock (clk[2] - 166.7 MHz / 6 ns period):**
- Launch: `core|\gen_use_pll_fast:pll_inst|...|pll1|clk[2]`
- Setup requirement: 5.988 ns
- Period available: 6.000 ns
- **Slack:** 0.343 ns ✅ (comfortable margin)

**Fast Clock (clk[1] - 200 MHz / 5 ns period):**
- All paths within spec (worst 0.182 ns slack)

### Seed 30 Build
The fitter seed was changed from 23 → 30 to explore a different placement
optimisation. All timing corners pass with wider margins than the previous
seed 23 build.

### Previous Build for Reference
| Clock | Seed 23 Slack | Seed 30 Slack |
|-------|--------------|--------------|
| clk[1] (200 MHz) | 0.088 ns* | **0.182 ns** |
| clk[2] (167 MHz) | 0.088 ns* | **0.343 ns** |
| clk[0] (100 MHz) | 0.994 ns | **0.994 ns** |
\* On seed 23 the critical path was the SDRAM state machine write enable.
  Seed 30 redistributed the critical paths across the fast clock domain.

### Resource Utilization

| Resource | Used | Total | % |
|----------|------|-------|---|
| Logic Elements | 6,333 | 8,064 | **79%** ⚠️ Full |
| Combinational | 5,585 | 8,064 | 69% |
| Registers | 2,586 | 8,064 | 32% |
| Memory Bits | 18,432 | 387,072 | 5% |
| PLL | 1 | 1 | **100%** |
| Pins Used | 63 | 130 | 48% |

---

## Detailed Path Analysis (Top Paths)

### Path #1: Fast Clock Capture Path (0.182 ns)

**From:** `gen_scl_pin_f2[4]` (sample clock input)  
**To:** `capture_data_fast_normal_r[0]` (capture register)  
**Timing:**
```
Data Arrival:   4.808 ns
Data Required:  4.990 ns
Setup Slack:    0.182 ns ← Tightest overall
Clock Period:   4.990 ns (clk[1], 200 MHz)
```

**Path Composition:**
- uTco (register output): 0.191 ns
- Logic levels: 2 (selector → register)
- Interconnect delay: 2.841 ns (62% of total)
- Cell delays: 1.061 ns (23% of total)


### Path #2: SDRAM DQ Output [2] (0.175 ns — seed 30 improved from 0.088 ns)

**From:** `SDRAM_Controller.state.ST_STREAM_WR`  
**To:** `SDRAM_Controller.dq_out[2]`

**Slack:** 0.343 ns ✅ (seed 23 was 0.088 ns — seed 30 found a better placement)

### Paths #3+: Fast-Clock Capture and Narrow Channel Paths (0.182+ ns)

All remaining paths have ≥ 0.182 ns slack. The critical path moved from the
SDRAM state machine (seed 23) to the fast-clock capture pipeline (seed 30),
but with double the margin (0.182 ns vs 0.088 ns).

---

## Design Characteristics

### Clock Domains
1. **clk (100 MHz)** - Main control
   - SPI protocol control
   - Status/command processing
   - Low-speed sequencing

2. **clk[1] (200 MHz)** - Fast logic analyzer clock
   - Sample capture pipeline
   - Narrow channel mux
   - Decent timing margins

3. **clk[2] (166.7 MHz)** - SDRAM controller clock
   - **CRITICAL:** Tightest timing margins
   - SDRAM write/read control
   - Data path multiplexing

### Routing Congestion

**Observation:** Interconnect dominates delay (69-82% of total)
- Indicates relatively full placement
- 79% logic utilization means limited room for rearrangement
- Further timing tuning would require floorplanning changes

---

## Compilation Settings

**Mode:** Aggressive Performance
- Timing performance prioritized over area
- 10 parallel processors used
- Fit effort: Standard

**Timing Model:** Slow 1200mV 85°C
- Conservative: worst-case PVT corner
- Actual silicon likely faster

---

## Potential Issues & Mitigation

### 🟢 LOW RISK: Seed 30 Improved Timing Margins

**Issue resolved by seed 30:** The previous seed 23 build had 0.088 ns worst-case slack on clk[2] (SDRAM). Seed 30 improved this:
- clk[1] (200 MHz) worst slack: **0.182 ns** (up from 0.088 ns)
- clk[2] (167 MHz) worst slack: **0.343 ns** (up from 0.088 ns)
- clk[0] (100 MHz) slack: **0.994 ns** (unchanged)

**Mitigation:**
- ✓ All timing corners pass with positive slack
- ✓ Hardware validation: 577/577 passed
- ✓ Conservative Slow 85°C model used

### 🟡 MEDIUM RISK: High Logic Utilization

**Issue:** 79% of LABs used (6,333 / 8,064 LEs)
- Limited headroom for future features
- Dense packing increases interconnect delay

**Impact on Optimizations:**
- Option B (smaller buffers) = likely OK (small change)
- Option A (variable blocks) = might not fit without relaxing timing

---

## Summary of Changes (seed 30 build)

**Compared to previous seed 23 build:**
- Fitter seed changed from 23 → 30 for improved timing margins
- Seed 30 found a better placement: clk[1] worst slack improved 0.088→0.182 ns, clk[2] improved 0.088→0.343 ns
- No RTL or constraint changes
- Hardware validated: 577/577 tests passed

**Result:** ✅ All timing corners pass with positive slack, TNS=0.000

---

## Summary of Changes (from latest compilation)

**Compared to June 5 baseline:**
- Prefetch-on-ARM optimization added (line 914 compress gate removed)
- Prefetch gate at CMD_READ_STREAM_BLOCK removed (line 1045)
- RTL additions for streaming control signals

**Result:** ✅ Still passing with 0.088 ns margin
- Optimizations fit without violating timing
- No additional timing closure work needed
- FPGA resources still have headroom

---

## Recommendations

### ✅ Current Design Status: STABLE

1. **Timing:** Safe to deploy
   - Validated in hardware at 2.8 MB/s
   - Conservative timing model used
   - No violations detected

2. **Resource Utilization:** Moderate headroom
   - 79% logic utilization = room for features
   - Memory barely used (5%) = expandable
   - PLL at capacity (1/1)

3. **Next Steps:**
   - ✅ Current optimizations solid
   - ⚠️ Option B (smaller buffers) likely safe
   - ⚠️ Option A (variable blocks) would be tight - full timing closure needed
   - ✅ Option C (multi-prefetch) feasible within current margins

### If Further Optimization Needed

**Option B (256-sample buffers):**
- Low risk - simple constant change
- Likely to pass timing unchanged
- Would free ~50 LEs (if logic optimized away)

**Option A (Variable blocks):**
- Would require additional logic
- Might push timing past margin
- Would need timing-focused recompile with relaxed other constraints
- Not recommended without comprehensive timing analysis

**Best approach:** Test Option B first (lowest risk)

---

## Files Referenced

- **Report:** `hdl/proj/postfix_worst_setup.rpt` (July 1, 09:56)
- **Fitter Report:** `hdl/proj/OLS_Logic_Analyzer.fit.rpt` (June 5)
- **CDC Reports:** `cross_*.rpt`, `postfix_*.rpt`
- **Build:** Quartus Prime 18.1.0 (Intel MAX 10 device)

---

## Conclusion

**Timing is SAFE and STABLE.** The design operates with positive slack on all 20 measured paths, with the tightest margin at 0.088 ns on SDRAM write control. While this is tight, it's well within acceptable limits for production silicon and has been validated in hardware.

The delta-compression optimizations integrate cleanly without timing violations.

---

## July 8, 2026 Update

The FAST_SPEED seed 3 build now uses the DDIO-forwarded SDRAM chip clock. The write-side
`SDRAM_CHIP_CLK_OUT` cone closes in STA with:

- Setup slack: `+1.108 ns`
- Hold slack: `+1.791 ns`
- Worst setup endpoint: `s_addr_r[8] -> sdram_addr[8]`
- Worst hold endpoint: `s_we_r -> sdram_we_n`

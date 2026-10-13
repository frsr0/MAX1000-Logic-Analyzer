# Build Flow: Quartus Project

**Directory:** `hdl/proj/`

## Current target

| Property | Value |
|---|---|
| FPGA | Intel MAX 10 `10M08SAU169C8G` |
| Package | 169-ball UFBGA |
| Speed grade | C8 |
| Toolchain | Quartus Prime Lite 25.1 |
| Default profile | `FAST_SPEED=true`, `FAST_RAW_BUILD=false` |
| Default fitter seed | 1 |
| Logic use | 8,027/8,064 LEs (100%) |
| Registers / memory | 5,048 registers; 54,404/387,072 memory bits |

## Project inputs

| File | Purpose |
|---|---|
| `OLS_Logic_Analyzer.qpf` | Quartus project |
| `OLS_Logic_Analyzer.qsf` | Generated device, source, fitter, and assignment settings |
| `OLS_Logic_Analyzer.sdc` | Clock, I/O, and CDC constraints |
| `pin_assignments.csv` | Board pin source used to generate the wrapper |
| `OLS_Logic_Analyzer_wrapper.vhd` | Generated top-level wrapper; overwritten by the build |
| `compile.ps1` | Wrapper/QSF generation, compile, optional persistent programming |
| `seed_sweep.ps1` | Multi-seed timing sweep |
| `WIDE_DIVIDER_REBUILD.md` | 24-bit generator-divider build and validation record |

## Compile and program

`compile.ps1` locates Quartus through `$env:QUARTUS_DIR`, defaulting to
`C:\altera_lite\25.1std\quartus\bin64`:

```powershell
cd hdl\proj
.\compile.ps1 -NoFlash -Seed 1
.\compile.ps1 -Flash -Seed 1
```

Parameters:

| Parameter | Meaning |
|---|---|
| `-Seed` | Fitter seed; default 1, the first seed that closes timing on the current RTL |
| `-NoFlash` | Compile without programming |
| `-Flash` | Program `output_files\OLS_Logic_Analyzer.pof` into CFM |
| `-RawOnly` | Set `FAST_RAW_BUILD=true` and omit the packed MSO pipeline |
| `-LegacyClkForward` | Use the old direct PLL c4 SDRAM clock instead of DDIO forwarding |

The script regenerates the wrapper and QSF, then runs the normal Quartus flow
through assembly and STA. `-Flash` deliberately uses the POF rather than an
SOF so the image survives power cycling. On the current Quartus 25.1 bench,
the MAX1000 JTAG interface also requires the Arrow USB-Blaster plugin from
Arrow USB Programmer2.

## Timing gate

The current RTL (fitter seed 1) passes every post-fit timing check at the slow
1200 mV, 85 C corner:

| Domain | Setup slack | Hold slack |
|---|---:|---:|
| `fast_clk` | +0.024 ns | +0.289 ns |
| `sdram_core_clk` | +0.226 ns | +0.268 ns |
| `sys_clk` | +0.194 ns | +0.338 ns |
| `SDRAM_CHIP_CLK_OUT` | +1.098 ns | +1.808 ns |
| `SPI_SCK_EXT` | +10.769 ns | +0.394 ns |

`fast_clk` is the minimum-slack domain; its critical path is the
`analog_burst_active` → `fifo_wdata` priority mux inside
`Fast_Logic_Analyzer_SDRAM`. The design is at 100% logic-element utilization,
so the margin is thin and the fit is highly placement-sensitive.

For comparison, the 2026-09-09 reference fit (seed 10, 7,713 LEs) closed at
`fast_clk` +0.253/+0.291 ns, `sdram_core_clk` +0.178/+0.340 ns, `sys_clk`
+0.278/+0.223 ns, `SDRAM_CHIP_CLK_OUT` +1.098/+1.808 ns, and `SPI_SCK_EXT`
+12.025/+0.394 ns.

The latest timing fix represents the fast capture sample budget as a modular
carry-chain accumulator, keeps packed ready registered, and separates the
continuous-budget reload pulse from the wide seed mux. This removes the live
22-bit equality/control cone from the 200.4 MHz capture path.

The build is seed-sensitive. On the current RTL, only seed 1 closes timing;
every other placement tested failed by −0.082 ns or worse and seed 3 cannot
fit at all (506 LABs required vs. 504 available). Re-run a seed sweep after
any RTL, pin, SDC, QSF, tool-version, or fitter-option change:

```powershell
.\seed_sweep.ps1 -Seeds @(1,2,3,4,5,6,7,8,9,10)
```

Do not inherit timing or hardware-validation claims from an older image.
Review all setup, hold, recovery/removal, minimum-pulse-width, I/O, and CDC
reports before programming.

## Generated evidence

The signoff artifacts are under `hdl/proj/output_files/`:

| Artifact | Use |
|---|---|
| `OLS_Logic_Analyzer.fit.summary` | Device, utilisation, and fitter status |
| `OLS_Logic_Analyzer.sta.summary` | Per-domain timing gate |
| `OLS_Logic_Analyzer.asm.rpt` | Assembler status and SOF checksum |
| `OLS_Logic_Analyzer.sof` | Volatile SRAM image |
| `OLS_Logic_Analyzer.pof` | Persistent CFM image |

The current build's assembler checksum is `0x0057C698` (the 2026-09-09
reference SOF was `0x0050492F`). A checksum identifies a build for bench
records; it is not a cryptographic integrity proof. The current SOF and POF
SHA-256 hashes are recorded in [Current Status](../current-status.md).

## Verification sequence

```mermaid
flowchart LR
    RTL[RTL + QSF + SDC] --> BUILD[Quartus compile]
    BUILD --> STA{All timing checks clean?}
    STA -->|no| REJECT[Reject or re-sweep]
    STA -->|yes| SIM[Maintained GHDL gate]
    SIM --> PROGRAM[Program POF]
    PROGRAM --> SMOKE[10-check hardware smoke]
    SMOKE --> FULL[Full host validation]
    FULL --> UI[37-case browser hardware matrix]
    UI --> SIGNOFF[Update status and traceability]
```

## Build profiles

| Profile | `FAST_SPEED` | `FAST_RAW_BUILD` | Purpose |
|---|---:|---:|---|
| Full (default) | true | false | Shipping mixed-signal image with packed MSO and 24-bit generator divider |
| Raw-only | true | true | Diagnostic image without the packed MSO pipeline |
| Slow/debug | false | profile-specific | Low-speed simulation or hardware investigation |

The current timing and connected-board claims apply only to the full seed-10
profile. See [Wide-Divider Rebuild](../../../hdl/proj/WIDE_DIVIDER_REBUILD.md)
and [Verification Traceability](../verification-traceability.md).

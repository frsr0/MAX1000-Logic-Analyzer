# Build Flow: Quartus Project

**Directory:** `hdl/proj/`

## Target Device

| Property | Value |
|---|---|
| FPGA | Intel MAX 10 10M08DAF484C8G |
| Family | MAX 10 |
| Package | 484-pin FBGA |
| Speed grade | C8 |
| Logic elements | 8,000 (∼87% used in current build) |

## Project Files

| File | Purpose |
|---|---|
| `OLS_Logic_Analyzer.qsf` | Quartus Settings File — device, pin assignments, SDC, VHDL files |
| `OLS_Logic_Analyzer.qpf` | Quartus Project File |
| `OLS_Logic_Analyzer.sdc` | Synopsys Design Constraints — clock, I/O timing |
| `OLS_Logic_Analyzer.vhd` | Generated wrapper (overwritten on compile) |
| `postfix_reports.tcl` | Post-fit timing report generation |
| `report_after_buf_logic.tcl` | Report after buffer logic synthesis stage |
| `report_after_analog_stage.tcl` | Report after analog synthesis stage |
| `report_clk1_paths.tcl` | Report all paths on clock domain 1 |

## Build Scripts

### compile.ps1

PowerShell build script:
```
.\compile.ps1 -Seed 33
```

Parameters:
- `-Seed` (default: 1): Quartus fitter seed for placement/routing
- `-Fast` (switch): enable FAST_SPEED build profile
- `-NoClean` (switch): skip clean before compile (incremental)

Steps:
1. Generate top-level wrapper VHDL with generic parameters
2. Update QSF with current seed and options
3. Run `quartus_map` (analysis & elaboration)
4. Run `quartus_fit` (fitter with seed)
5. Run `quartus_asm` (assembler)
6. Run `quartus_sta` (timing analysis)
7. Run `quartus_eda` (netlist export)

### seed_sweep.ps1

Automated fitting with multiple seeds:
```
.\seed_sweep.ps1 -Seeds @(1,10,20,30,33,40,50)
```

Reports results to `seed_sweep_results.txt` and identifies the best-fit seed.

## Timing Constraints (SDC)

| Constraint | Value | Domain |
|---|---|---|
| `create_clock` sys_clk | 100.2 MHz (period 9.98 ns) | c0 |
| `create_clock` fast_clk | 200.4 MHz (period 4.99 ns) | c1 |
| `create_clock` sdram_core_clk | 167 MHz (period 5.99 ns) | c2 |
| `create_clock` sdram_chip_clk | 167 MHz (period 5.99 ns) | c4 |
| `create_clock` adc_conv_clk | 12 MHz (period 83.33 ns) | c3 |
| SDRAM output delay | 2.0 ns | sdram_dq/dqm/addr/ba |
| SDRAM input delay | 1.5 ns | sdram_dq input |
| SPI input delay | 3.0 ns | SPI_SCK → MOSI |

## Timing Reports

| Report | Description |
|---|---|
| `TIMING_REPORT_SUMMARY.md` | Signoff-oriented timing summary |
| `compile_run*.txt` | Full compilation logs from each run |
| `current_worst_paths.txt` | Worst timing paths for the current seed |
| `seed_sweep_results.txt` | Multi-seed sweep results |
| `cr_ie_info.json` | Compilation resource info (LE, register, memory usage) |

## Build Profiles

| Profile | FAST_SPEED | FAST_RAW_BUILD | Fmax (fast_clk) | Use Case |
|---|---|---|---|---|
| Speed | true | true | 200.4 MHz | Production — excludes compression for timing |
| Development | true | false | 200.4 MHz | Testing — includes compression |
| Slow | false | true | 12-50 MHz | Low-speed debug |

## Known Issues

- The generated wrapper in `proj/` is overwritten by `compile.ps1`
- Seed 33 is the currently validated speed-build placement
- At ∼87% LE utilisation, fitter struggles — changing one parameter often requires a seed sweep to find a new valid placement
- The `FAST_RAW_BUILD` option that excludes compression modules exists purely for timing closure at 200 MHz

## Testbenches

See [Testbenches](testbenches.md) for the simulation suite.

# Historical MAX10 flash image verification — 2026-09-28

This records an earlier timing-failing image and is superseded for current
status by the [final seed-7 build and board evidence](mcp4725-fast-timing-clean/README.md).

From `hdl/proj`, Quartus Programmer was run in JTAG verify-only mode:

```powershell
quartus_pgm -c 1 -m JTAG -o 'V;output_files/OLS_Logic_Analyzer.pof'
```

It exited `0`, confirming that the POF's contents matched the image read from
MAX10 configuration flash **at the time of verification**. The separate verify-only attempt against
`seed_sweep_best.pof` exited `1`; its initial “configuration succeeded” line
was not the final result. Neither command programmed or erased the device.
The verified POF SHA-256 is
`1154FB70FD914D6AE9C6D2EC6CF8013684FA723D2D8F4DC8AD5F88DC229928E0`; its
Quartus Programmer checksum is `0x01DBF859`.

The verified POF was last modified at 15:28. The substantive
`OLS_SDRAM_Top.vhd` edits predate it (15:00), as does the QSF update (15:26).
The wrapper file's 15:35 change was formatting only, and the timing-gate script
was added at 16:33. Physical MCP4725 evidence was captured at 15:52 and 16:12,
after the POF was built but before verify-only was run (at approximately 17:01,
17:18, and 17:19). The later verification establishes what was in flash at
verification time; it cannot prove that the same image was in flash during the
earlier physical tests. The voltage results and wrong-address NACK support the
behavior of the tested route, but are not byte-for-byte image provenance.

That earlier image's post-fit timing report failed setup timing: `fast_clk`
slack was −0.565 ns and `sys_clk` slack was −0.027 ns at slow 85 °C. Flash verification
proves image identity only; it is not timing signoff. See the
[MCP4725 physical test record](mcp4725-fast-2026-09-28/README.md) for test
results.

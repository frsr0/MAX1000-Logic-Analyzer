# MAX1000 Hardware Validation — 2026-07-22

## Exact image

- Target: Arrow MAX1000, Intel MAX 10 `10M08SAU169C8G`
- Build: full mixed-signal FAST build, fitter seed 23
- Quartus build: successful
- Post-fit setup slack: `fast_clk +0.124 ns`, `sdram_core_clk +0.426 ns`
- SOF checksum: `0x004FDDF3`
- POF checksum: `0x01D65FD0`
- Programming: JTAG configuration succeeded on Arrow-USB-Blaster
- Nonvolatile programming: POF write to the MAX 10 configuration flash succeeded
- Power-cycle verification: board reconnected and passed metadata/status/capture validation from persisted configuration

## Connected-board regression

The full `python -m app.hw_validation` regression recorded **369/369 passed,
0 failed, 0 skipped** against the exact programmed SOF. Coverage included digital,
continuous/ring, 200 MHz narrow packed, MSO packed analog/mixed capture,
readback codecs, generator routes and repeat mode, triggers, full-depth and
back-to-back capture, physical analog jumpers, accelerometer I²C/SPI access,
readout stress, lifecycle, and long-duration rolling capture.

The digital loopback jumper was discovered as pool pin 22 to capture channel 13;
UART, SPI, I²C, live-generator, and repeating-ring loopback checks all ran. The
physical analog jumper paths were connected and passed full-scale/cross-talk
checks.

## Reproduction commands

```powershell
cd hdl\proj
powershell -NoProfile -ExecutionPolicy Bypass -File .\compile.ps1 -NoFlash -Seed 23
& 'C:\intelFPGA_lite\18.1\quartus\bin64\quartus_sta.exe' -t .\sta_report.tcl
& 'C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe' -c 1 -m JTAG -o "P;output_files\OLS_Logic_Analyzer.sof"

cd ..\..\host
python -m app.hw_validation
```

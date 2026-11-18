# FPGA Image History

This page keeps the history of each FPGA image, oldest first. The README only carries the current image. Each section gives the build date, the Quartus seed, and the timing, resource, hash and validation results for that image. Each image is rebuilt with `.\compile.ps1 -Seed <n>` from `hdl\proj`.

## 2026-09-28 final build (seed 7)

The 2026-09-28 final build used Quartus seed `7`. The compile, timing gate, and
flash programming completed successfully; verify-only at 17:46:54 confirmed
that the image matched MAX 10 configuration flash before the physical retests.
The POF SHA-256 is
`C65DDA0E8042156024F1170EF967316D072CBF9BADFA67A735EC51BE7FFEC18C` and its
Programmer checksum is `0x01DE1DE1`. Slow 85 °C post-fit setup slack is
`fast_clk` +0.046 ns, `sys_clk` +0.183 ns, and SDRAM +0.396 ns. See the
[current-status wiki](wiki/current-status.md) and [final image evidence](evidence/mcp4725-fast-timing-clean/README.md).

```powershell
cd hdl\proj
.\compile.ps1 -Seed 7
```

## 2026-11-16 build (seed 17)

The image uses 8,022 of 8,064 logic elements and 19 of 42 M9K blocks. Its POF
SHA-256 is
`59DBC40464DD95A4AD1869EBBD952D2B89ECC1CD0D4F2FF3E20326831FC168C0`, and slow
85 °C post-fit setup slack is `sdram_core_clk` +0.038 ns, `fast_clk` +0.073 ns,
and `sys_clk` +0.109 ns (the `sdram_dq[*]` to `sdram_s_readdata[*]` path keeps
+2.417 ns setup and +7.595 ns hold at slow 85 °C, and +6.200 ns setup and
+4.713 ns hold at fast 0 °C). Timing is seed-sensitive: seeds 1, 2, 3, 5, 7, 9,
11 and 13 failed the timing gate for this RTL (seed 3 by 4 ps) and seed 17 is
the first that passed, so re-sweep seeds after any RTL change. On the attached
board (2026-11-16) the image passed the hardware validation suite (433/433,
with the bench jumper not decoding so three checks did not run), the
74-testbench GHDL regression, a 20 s live 16-channel stream at 100.2 MS/s
(producer slope within 0.001 % of the request, dropped-sample counter at zero),
and ten full-depth 4,194,304-sample captures at 100.2 MS/s with uniform run
lengths and no dropped samples.

## 2026-11-17 build (seed 7, generation 4)

This image is generation 4, rebuilt on 2026-11-17 with seed `7` (what
the project file selects) for timing margin; host-visible behaviour is the
same as the 2026-11-16 seed-17 build described above. On top of the 2026-11-13 read-timing fix (the
controller latches DQ inside the chip's CL3 data window,
`RD_WAIT_EXTRA_CYCLES = 3` in `hdl/rtl/SDRAM_Controller_Custom.vhd`) it keeps
the SDRAM write pump running between bursts of host reads in continuous mode,
fixes the 1-in-17 word loss in narrow 200 MHz capture, adds a dropped-sample
counter (register `0x60`, bits 15:0), makes the RLE stream always send its
last run, and removes the fixed 50 ms limit on a generator-synchronised
capture: a burst now ends when `Gen_Busy` falls or the capture fills, with a
2 s watchdog instead of a 50 ms cut, so a legitimate burst of any length is
mirrored and captured whole. `CMD_GET_METADATA` returns 12 bytes ending
`3F 04`: byte 10 holds feature flags (bit 5 = no fixed generator-capture
limit) and byte 11 the image generation, and the host driver selects its read
compensations from them, so it also works with older images.

The 2026-11-17 rebuild takes three narrow compares out of the worst timing
paths (the read loop's "one sample left" check, the analog packer's DRAIN
step and the compressed-stream sample count) and runs the dispatcher timeouts
off one shared tick. It uses 8,019 of 8,064 logic elements; slow 85 °C setup
slack is `fast_clk` +0.161 ns, `sdram_core_clk` +0.163 ns and `sys_clk`
+0.215 ns, and all 66 timing checks pass. Its POF SHA-256 is
`1CCF7E14DBECF4258C4FEEB33D9B7338BA26A711DD7A3FC1F83A9275ACB2619C`
(Programmer checksum `0x01DDB7C1`). On the attached board it passed the smoke
test (10/10), the hardware validation suite (415/415, with 8 jumper checks
skipped because the jumper fixture was not selected), the codec suite (28/28)
and the 75-testbench GHDL regression; `CMD_GET_METADATA` still ends `3F 04`.

## 2026-11-18 build (seed 7, MISO on SCK rising edge)

The 2026-11-18 rebuild (still seed 7, same core slack) launches MISO on the
SCK rising edge, the edge the FT2232H samples on, and the SDC now constrains
the SPI link at the 30 MHz the host actually runs instead of 15 MHz. At
30 MHz the FT2232H needs 11 ns MISO setup; the old falling-edge launch left
about 4.7 ns for an 8 ns path and failed STA by 3.2 ns (it worked only because
the FTDI samples better than its worst case). MISO now has +13.4 ns setup at
slow 85 °C and at least +1.06 ns hold at fast 0 °C, and all 66 timing checks
pass. Its POF SHA-256 is
`63CD7FC007DC171EFB837B8051E102D4DF938982C1B28D26F7C82DA111EA1690`
(Programmer checksum `0x01DDB91A`). On the attached board it passed the smoke
test (10/10), the hardware validation suite (415/415, 8 jumper checks
skipped), the codec suite (28/28), the 75-testbench GHDL regression and
bit-exact live and readback checks.

## 2026-11-18 build (seed 16, current)

This image (2026-11-18, seed 16) also shortens the idle gap after each
16-read burst of continuous-capture SDRAM reads from 80 to 16 cycles. The
capture FIFO stays protected by the existing gate that only starts a burst
while it holds under 512 of 4096 words. Live `rle` with 16,384-sample windows
rises from about 3.7 to about 4.5 MS/s. Timing is tighter: of 21 seeds swept,
seed 16 is the best at slow 85 °C setup slack `sys_clk` +0.043 ns, `fast_clk`
+0.047 ns and `sdram_core_clk` +0.311 ns, with all 66 checks passing and
8,014 of 8,064 logic elements used. Its POF SHA-256 is
`DCA287B6528EEBE2FBB10055E8DDD9EA363E5EAB3DFAA0C5AF3DF9FCD6B7FE72`
(Programmer checksum `0x01DE8153`). On the attached board it passed the smoke
test (10/10), the hardware validation suite (415/415, 8 jumper checks
skipped; one earlier run had a single LIS3DH I2C NACK at 50 kHz that did not
recur in 7 later runs), the codec suite (28/28), the 75-testbench GHDL
regression, bit-exact live checks, and continuous capture at 100.2, 50.1 and
25.1 MS/s under maximum live readback with the dropped-sample counter at 0.

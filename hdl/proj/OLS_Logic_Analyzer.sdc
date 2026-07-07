  # OLS Logic Analyzer clock constraints
  # PLL: 12 MHz in, c0=100.2 MHz (sys_clk), c1=200.4 MHz (fast_clk),
  # c2=167.0 MHz core SDRAM clock, c4=167.0 MHz SDRAM chip clock delayed ~1.1 ns
  # Speed mode (FAST_SPEED=true):
  #   FAST_CLK (~200.4 MHz, c1): 3-stage pipeline: sample -> control -> BRAM/FIFO write
  #     -> registered skid buffer -> DCFIFO (sync depth 4)
  #   sys_clk  (~100.2 MHz, c0): protocol/sys/host control
  #   sdram_core_clk (~167.0 MHz, c2): DCFIFO read -> prefetch register -> SDRAM write pump
  #   sdram_chip_clk_out (~167.0 MHz, c4): forwarded clock on the SDRAM pin
  # Normal mode (FAST_SPEED=false):
  #   FAST_CLK (120 MHz, c1): capture mux, sample divider, input packer, BRAM, async FIFO push
  #   sys_clk  (96 MHz, c0):  async FIFO read, SDRAM write pump, buffer mgmt, readout, OLS interface
  # All cross-clock paths go through proper 2FF synchronizers, toggle synchronizers, or
  # async FIFO (dcfifo with internal gray-code CDC, rdsync_delaypipe=wrsync_delaypipe=4).
  #   - FAST_CLK write path: producer -> registered skid -> dcfifo data/wrreq (registered)
  #   - pclk read path: dcfifo q -> prefetch register -> SDRAM write pump (timed at pclk)
  #   - Drain-based run start replaces async FIFO clear
  # No multicycle path constraints are needed for the DCFIFO bridge — the
  # show-ahead read path through the M9K is now properly registered (prefetch_data_r).
 
 # 12 MHz input clock
 create_clock -name CLK -period 83.333 [get_ports CLK]
 
 # Explicit named PLL output clocks. Must come before derive_pll_clocks;
 # derive_pll_clocks respects pre-existing clocks on PLL outputs and will
 # not overwrite these names. The names are stable across fitter runs even
 # when the PLL instance name changes.
 # FAST_SPEED: c0~100.2MHz sys, c1~200.4MHz fast, c2~167.0MHz sdram core
 # Normal:     c0~96MHz   sys, c1~120MHz   fast, c2~167.0MHz sdram core
 # c3 = 12 MHz ADC conversion clock (present in mixed/analog builds).
 # The actual synthesized pin path is
 # core|\gen_use_pll_fast:pll_inst|\gen_fast_speed:altpll_component|auto_generated|pll1|clk[N]
 # -- "pll_inst" (the VHDL instantiation label) is NOT immediately followed by
 # "|clk[N]"; the altpll_component|auto_generated hierarchy sits in between,
 # and a leading-only wildcard (*pll_inst|clk[N]) can never bridge that gap.
 # Match the inner "pll1" leaf name instead, exactly like the working
 # SDRAM_CHIP_CLK_OUT constraint below (*pll1|clk[4]). Getting this wrong
 # silently no-ops create_generated_clock (empty -source/<targets>), so
 # sys_clk/fast_clk/sdram_core_clk/adc_clk were never actually created, the
 # set_clock_groups -asynchronous block resolved to four EMPTY groups, and
 # every real CDC crossing between the PLL's genuinely-async outputs was
 # analyzed as synchronous — the cause of large, seed-independent setup
 # violations on every clock (2026-07-07).
 create_generated_clock -name sys_clk \
   -source [get_pins -compatibility_mode {*pll1|inclk[0]}] \
   [get_pins -compatibility_mode {*pll1|clk[0]}]
 create_generated_clock -name fast_clk \
   -source [get_pins -compatibility_mode {*pll1|inclk[0]}] \
   [get_pins -compatibility_mode {*pll1|clk[1]}]
 create_generated_clock -name sdram_core_clk \
   -source [get_pins -compatibility_mode {*pll1|inclk[0]}] \
   [get_pins -compatibility_mode {*pll1|clk[2]}]
 create_generated_clock -name adc_clk \
   -source [get_pins -compatibility_mode {*pll1|inclk[0]}] \
   [get_pins -compatibility_mode {*pll1|clk[3]}]
 
 # Derive remaining PLL output clocks and PLL-internal dividers
 derive_pll_clocks
 
 # Realistic clock uncertainty for timing signoff
 derive_clock_uncertainty

 # External SPI timing:
 # - SPI_SCK is the FTDI-generated clock that times the slave interface.
 # - MOSI is captured relative to that clock.
 # - MISO is launched relative to that same clock.
 # The hardware validation suite treats 15 MHz as the validated ceiling.
 # Restored 2026-07-07: dropped by the SDC-hardening pass (commit b378e212)
 # when the async clock-group block was narrowed to only the four named PLL
 # clocks. Without these, SPI_SCK/SPI_CS/SPI_MOSI/SPI_MISO are unconstrained
 # against every internal clock instead of explicitly excluded from them.
 create_clock -name SPI_SCK_EXT -period 66.667 [get_ports SPI_SCK]
 create_clock -name SPI_CS_QUAL -period 1000.000 [get_ports SPI_CS]
 set_input_delay -clock [get_clocks SPI_SCK_EXT] -max 12.0 [get_ports SPI_MOSI]
 set_input_delay -clock [get_clocks SPI_SCK_EXT] -min 0.0 [get_ports SPI_MOSI]
 set_output_delay -clock [get_clocks SPI_SCK_EXT] -max 12.0 [get_ports SPI_MISO]
 set_output_delay -clock [get_clocks SPI_SCK_EXT] -min -2.0 [get_ports SPI_MISO]

 # The SPI chip-select is used as an asynchronous qualifier/reset, not a clock.
 # Keep it out of the timed datapaths.
 set_false_path -from [get_ports {SPI_SCK SPI_CS}]
 set_false_path -to   [get_ports {SPI_SCK SPI_CS}]
 set_false_path -from [get_ports {SPI_MOSI}]  -to [all_registers]

 # Asynchronous clock groups: all cross-domain CDC paths properly synchronized.
 # Note: sdram_chip_clk_out (c4) is intentionally NOT in these async groups —
 # the core↔chip relationship is synchronous-by-design (same PLL, phase-shifted),
 # and the I/O delays & multicycle constraints at lines 84–103 already cover it.
 set_clock_groups -asynchronous \
   -group [get_clocks sys_clk] \
   -group [get_clocks fast_clk] \
   -group [get_clocks sdram_core_clk] \
   -group [get_clocks adc_clk] \
   -group [get_clocks SPI_SCK_EXT] \
   -group [get_clocks SPI_CS_QUAL]
 
 # Async FIFO internal gray-code synchronizer paths
 # The dcfifo megafunction generates these internally; they are intentional
 # CDC synchronization paths and cannot be timed at the fastest edge rate.
 set_false_path -from [get_registers *auto_generated|delayed_wrptr_g*] \
                -to   [get_registers *auto_generated|rdemp_eq_comp*]
 set_false_path -from [get_registers *auto_generated|rdptr_g*] \
                -to   [get_registers *auto_generated|wrfull_eq_comp*]
 
  # Make the forwarded SDRAM chip clock explicit so I/O delays are referenced to
 # the same delayed edge the external SDRAM sees, not the internal controller
 # clock that launches the commands/data.
 create_generated_clock -name SDRAM_CHIP_CLK_OUT \
   -source [get_pins -compatibility_mode {*pll1|clk[4]}] \
   [get_ports {sdram_clk}]
 
 # SDRAM write-side timing relative to the forwarded chip clock. The board now
 # drives a dedicated delayed clock to the memory, so constrain address/control
 # and DQ outputs against that forwarded edge instead of the undelayed core
 # clock.
 set_output_delay -clock [get_clocks SDRAM_CHIP_CLK_OUT] -max 1.5 \
   [get_ports {sdram_addr[*] sdram_ba[*] sdram_cas_n sdram_cke sdram_cs_n sdram_dqm[*] sdram_ras_n sdram_we_n sdram_dq[*]}]
 set_output_delay -clock [get_clocks SDRAM_CHIP_CLK_OUT] -min -0.8 \
   [get_ports {sdram_addr[*] sdram_ba[*] sdram_cas_n sdram_cke sdram_cs_n sdram_dqm[*] sdram_ras_n sdram_we_n sdram_dq[*]}]

 # DQ write-side multicycle: dq_oe/dq_out (SDRAM_Controller_Custom.vhd) are
 # deliberately NOT pipelined through the extra s_*_r register stage that
 # every command signal (s_cas/s_we/s_ras/s_addr/s_ba) goes through before
 # reaching its pin -- see the "keeping row/page-hit decisions out of the IO
 # OE timing path" comment at sdram_dq's driver. dq_oe/dq_out and the command
 # signals are all written in the SAME ST_WR/ST_STREAM_WR case branch on the
 # same edge, but the command's extra _r stage means sdram_cas_n/sdram_we_n
 # (the edge the SDRAM actually latches DQ on) become valid to the chip one
 # full sdram_core_clk cycle AFTER sdram_dq does. DQ is safely stable well
 # before the command that consumes it; the default single-cycle relationship
 # checks an edge the memory never samples against. Model the real one-cycle
 # head start, mirroring the read-side CL3 multicycle below (2026-07-07).
 set_multicycle_path -setup 2 \
   -from [get_registers {*dq_oe* *dq_out*}] \
   -to   [get_ports {sdram_dq[*]}]
 set_multicycle_path -hold 1 \
   -from [get_registers {*dq_oe* *dq_out*}] \
   -to   [get_ports {sdram_dq[*]}]

 # SDRAM read-side timing: use the same forwarded clock and model a 5.4 ns
 # access window for the 166 MHz SDRAM grade, with a conservative 0 ns minimum
 # arrival for hold analysis.
 set_input_delay -clock [get_clocks SDRAM_CHIP_CLK_OUT] -max 5.4 [get_ports {sdram_dq[*]}]
 set_input_delay -clock [get_clocks SDRAM_CHIP_CLK_OUT] -min 0.0 [get_ports {sdram_dq[*]}]
 
 # The SDRAM controller only samples read data in ST_RD_DATA, after the explicit
 # CAS-latency wait. Do not time DQ as if the controller captured the first
 # internal core-clock edge after every forwarded SDRAM clock edge.
 set_multicycle_path -setup 3 \
   -from [get_ports {sdram_dq[*]}] \
   -to   [get_registers {*sdram_s_readdata[*]}]
 set_multicycle_path -hold 2 \
   -from [get_ports {sdram_dq[*]}] \
   -to   [get_registers {*sdram_s_readdata[*]}]
 
 # DELETED: old multicycle for afifo portb -> cap_stream_data. The prefetch
 # register (prefetch_data_r) now captures fifo_rdata one cycle before the
 # write pump uses it, so the long M9K address-reg -> q path terminates at
 # a pclk register and is properly timed at single-cycle.
 #
 # DELETED: old multicycle for stream_rem. The write pump now uses registered
 # drain logic; stream_rem decrements only on an SDRAM accept cycle, which
 # is at least one bubble apart. The timing path is naturally met.



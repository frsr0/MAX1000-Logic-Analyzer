# OLS Logic Analyzer clock constraints
# PLL: 12 MHz in, c0=100.2 MHz (sys_clk), c1=200.4 MHz (fast_clk),
# c2=167.0 MHz core SDRAM clock, c4=167.0 MHz SDRAM chip clock delayed ~1.5 ns
# Speed mode (FAST_SPEED=true):
#   FAST_CLK (~200.4 MHz, c1): 3-stage pipeline: sample -> control -> BRAM/FIFO write
#   sys_clk  (~100.2 MHz, c0): protocol/sys/host control
#   sdram_core_clk (~167.0 MHz, c2): async FIFO read, SDRAM write pump, buffer mgmt, SDRAM readback path
#   sdram_chip_clk_out (~167.0 MHz, c4): forwarded clock on the SDRAM pin
# Normal mode (FAST_SPEED=false):
#   FAST_CLK (120 MHz, c1): capture mux, sample divider, input packer, BRAM, async FIFO push
#   sys_clk  (96 MHz, c0):  async FIFO read, SDRAM write pump, buffer mgmt, readout, OLS interface
# All cross-clock paths go through proper 2FF synchronizers, toggle synchronizers, or
# async FIFO (dcfifo with internal gray-code CDC).  No multicycle path constraints needed.

# 12 MHz input clock
create_clock -name CLK -period 83.333 [get_ports CLK]

# Derive PLL output clocks (FAST_SPEED: c0~100.2MHz, c1~200.4MHz, c2~167.0MHz core, c4~167.0MHz delayed)
derive_pll_clocks

# Realistic clock uncertainty for timing signoff
derive_clock_uncertainty

# Asynchronous clock groups: all cross-domain CDC paths properly synchronized.
# FAST_SPEED build uses only clk[0]=sys, clk[1]=fast, clk[2]=sdram.
# The ADC clock domain is compiled out of this profile.
set_clock_groups -asynchronous \
  -group [get_clocks {*pll_inst|*clk[0]}] \
  -group [get_clocks {*pll_inst|*clk[1]}] \
  -group [get_clocks {*pll_inst|*clk[2]}]

# Async FIFO internal gray-code synchronizer paths
# The dcfifo megafunction generates these internally; they are intentional
# CDC synchronization paths and cannot be timed at the fastest edge rate.
set_false_path -from [get_registers *auto_generated|delayed_wrptr_g*] \
               -to   [get_registers *auto_generated|rdemp_eq_comp*]
set_false_path -from [get_registers *auto_generated|rdptr_g*] \
               -to   [get_registers *auto_generated|wrfull_eq_comp*]

# Runtime pin-map registers are configuration selects, written before capture
# and held stable while sampling. Do not time the rare pin-map register update
# through the high-speed input mux as if it were per-sample data.
set_false_path -from [get_registers *pin_map_fast*] \
               -to   [get_registers *capture_data_fast_normal_r*]

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

# SDRAM read-side timing: use the same forwarded clock and model a 5.4 ns
# access window for the 166 MHz SDRAM grade, with a conservative 0 ns minimum
# arrival for hold analysis.
set_input_delay -clock [get_clocks SDRAM_CHIP_CLK_OUT] -max 5.4 [get_ports {sdram_dq[*]}]
set_input_delay -clock [get_clocks SDRAM_CHIP_CLK_OUT] -min 0.0 [get_ports {sdram_dq[*]}]

# OLS Logic Analyzer clock constraints
# PLL: 12 MHz in, c0=100 MHz (sys_clk), c1=200 MHz (fast_clk), c2=100 MHz shifted (sdram_clk)
# Speed mode (FAST_SPEED=true):
#   FAST_CLK (200 MHz, c1): 3-stage pipeline: sample -> control -> BRAM/FIFO write
#   sys_clk  (100 MHz, c0): async FIFO read, SDRAM write pump, buffer mgmt, readout, OLS interface
# Normal mode (FAST_SPEED=false):
#   FAST_CLK (120 MHz, c1): capture mux, sample divider, input packer, BRAM, async FIFO push
#   sys_clk  (96 MHz, c0):  async FIFO read, SDRAM write pump, buffer mgmt, readout, OLS interface
# All cross-clock paths go through proper 2FF synchronizers, toggle synchronizers, or
# async FIFO (dcfifo with internal gray-code CDC).  No multicycle path constraints needed.

# 12 MHz input clock
create_clock -name CLK -period 83.333 [get_ports CLK]

# Derive PLL output clocks (c0=96MHz, c1=120MHz, c2=96MHz shifted)
derive_pll_clocks

# Realistic clock uncertainty for timing signoff
derive_clock_uncertainty

# Asynchronous clock groups: all cross-domain CDC paths properly synchronized.
set_clock_groups -asynchronous \
  -group [get_clocks {*|clk[0]}] \
  -group [get_clocks {*|clk[1]}] \
  -group [get_clocks {*|clk[2]}]

# Async FIFO internal gray-code synchronizer paths
# The dcfifo megafunction generates these internally; they are intentional
# CDC synchronization paths and cannot be timed at the fastest edge rate.
set_false_path -from [get_registers *auto_generated|delayed_wrptr_g*] \
               -to   [get_registers *auto_generated|rdemp_eq_comp*]
set_false_path -from [get_registers *auto_generated|rdptr_g*] \
               -to   [get_registers *auto_generated|wrfull_eq_comp*]

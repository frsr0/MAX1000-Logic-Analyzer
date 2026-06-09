# OLS Logic Analyzer clock constraints
# PLL: 12 MHz in, c0=96 MHz (sys_clk), c1=120 MHz (fast_clk), c2=96 MHz shifted (sdram_clk)
# Two-clock domain split:
#   FAST_CLK (120 MHz, c1): sample divider, input packer, async FIFO push
#   sys_clk  (96 MHz, c0):  async FIFO read, SDRAM write pump, buffer mgmt, readout

# 12 MHz input clock
create_clock -name CLK -period 83.333 [get_ports CLK]

derive_pll_clocks
derive_clock_uncertainty

# Asynchronous CDC paths between sys_clk (96 MHz) and fast_clk (120 MHz).
# Use explicit clock name globs that match the generated clock names.
set_false_path -from [get_clocks {*|pll1|clk[0]}] -to [get_clocks {*|pll1|clk[1]}]
set_false_path -from [get_clocks {*|pll1|clk[1]}] -to [get_clocks {*|pll1|clk[0]}]

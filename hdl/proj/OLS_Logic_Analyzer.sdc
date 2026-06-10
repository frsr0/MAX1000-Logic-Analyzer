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
set_false_path -from [get_clocks {*|pll1|clk[0]}] -to [get_clocks {*|pll1|clk[1]}]
set_false_path -from [get_clocks {*|pll1|clk[1]}] -to [get_clocks {*|pll1|clk[0]}]

# Multicycle paths for capture counters (take multiple FAST_CLK cycles)
set_multicycle_path -setup 2 -from [get_registers *fast_sample_cnt*] -to [get_registers *fifo_overflow_f*]
set_multicycle_path -setup 2 -from [get_registers *fast_sample_cnt*] -to [get_registers *cfg_samples_f*]
set_multicycle_path -setup 2 -from [get_registers *waddr_*] -to [get_registers *buf_rem_*]
set_multicycle_path -setup 2 -from [get_registers *cnt*] -to [get_registers *cnt*]

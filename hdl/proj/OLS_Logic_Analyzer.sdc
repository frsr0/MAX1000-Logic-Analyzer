# OLS Logic Analyzer clock constraints
# PLL: 12 MHz in, c0=96 MHz (sys_clk), c1=120 MHz (fast_clk), c2=96 MHz shifted (sdram_clk)

# 12 MHz input clock
create_clock -name CLK -period 83.333 [get_ports CLK]

derive_pll_clocks
derive_clock_uncertainty

# Asynchronous CDC paths between sys_clk (96 MHz) and fast_clk (120 MHz).
# All cross-domain transfers use 2FF or 3FF synchronizers explicitly.
set_false_path -from [get_clocks {*|pll1|clk[0]}] -to [get_clocks {*|pll1|clk[1]}]
set_false_path -from [get_clocks {*|pll1|clk[1]}] -to [get_clocks {*|pll1|clk[0]}]

# LED controller: multi-cycle path (fade_tick enables updates at ~100 Hz).
# Relax all paths through LED_CTRL to ~10 ms (1,000,000 cycles at 96 MHz).
set_multicycle_path -setup 1000000 -to [get_registers {*LED_CTRL|*}]
set_multicycle_path -hold 999999 -to [get_registers {*LED_CTRL|*}]

# full_i & rd_mode: multi-cycle paths. The combinatorial path from run_sync2/run_r
# through the 33-bit waddr adder + 36-bit comparator exceeds 1 cycle (~16.7 ns).
# full_i fires once per capture session; rd_mode controls SDRAM direction at
# capture start/stop. 1-cycle latency shifts first sample by 1 — functionally
# harmless for OLS use cases.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|full_i}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|full_i}]
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|rd_mode}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|rd_mode}]

# Status[*]: LED bar-graph status outputs depend on fifo_count_v which flows
# through the waddr adder/comparator chain. Status is read by OLS interface at
# a much slower rate (not pclk rate), so 2-cycle path is safe.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|Status[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|Status[*]}]

# bram_raddr: BRAM read address depends on the readout address computation
# (Add4 + LessThan4 carry chains) which shares logic with the waddr chain.
# Only active during readout (rd_mode=true); BRAM data feeds SDRAM which is slow.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|bram_raddr[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|bram_raddr[*]}]

# Outputs[*]: Readout output register depends on the same Add4 + LessThan4
# chain (bram_cnt + bram_post_cnt comparison) that feeds bram_raddr.
# bram_rdata is 1 cycle behind bram_raddr (BRAM pipeline), so Outputs captures
# stale BRAM data anyway. Only active during readout; fifo_cnt_r is stable
# because the FIFO is not being written during readout.
set_multicycle_path -setup 2 -from [get_registers {*Fast_Logic_Analyzer_SDRAM1|fifo_cnt_r[*]}] -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|Outputs[*]}]
set_multicycle_path -hold 1 -from [get_registers {*Fast_Logic_Analyzer_SDRAM1|fifo_cnt_r[*]}] -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|Outputs[*]}]

# full_pending: Status signal depends on the waddr adder/comparator chain
# (fast_mode_i → flush_rem → LessThan10 → fifo_head_v → Add18 → LessThan15 → full_pending).
# Only checked when FIFO is empty (slow readout), so 2-cycle path is safe.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|full_pending}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|full_pending}]

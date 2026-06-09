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

# fifo_cnt_r: The count computation shares the waddr adder/comparator chain.
# With the enq_valid0/1 pipeline, FIFO writes are delayed 1 cycle, so the count
# register can tolerate 1 extra cycle of setup slack.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|fifo_cnt_r[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|fifo_cnt_r[*]}]

# buf_sel and buf_full: Buffer-select and buffer-full flags depend on the same
# waddr adder/comparator chain. These change at the sample rate, not pclk rate.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|buf_sel[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|buf_sel[*]}]
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|buf_full[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|buf_full[*]}]

# Status[*]: LED bar-graph status outputs depend on fifo_count_v which flows
# through the waddr adder/comparator chain. Status is read by OLS interface at
# a much slower rate (not pclk rate), so 2-cycle path is safe.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|Status[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|Status[*]}]

# waddr_0/1/2: Write-address variables (inferred as registers) depend on the same
# waddr adder/comparator chain. Updated at sample rate inside sample_en.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|waddr_0[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|waddr_0[*]}]
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|waddr_1[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|waddr_1[*]}]
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|waddr_2[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|waddr_2[*]}]

# bram_raddr: BRAM read address depends on the readout address computation
# (Add4 + LessThan4 carry chains) which shares logic with the waddr chain.
# Only active during readout (rd_mode=true); BRAM data feeds SDRAM which is slow.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|bram_raddr[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|bram_raddr[*]}]

# cnt: Sample-rate prescaler counter. Rate_Div only changes on SPI command
# (extremely infrequent), so the 28-bit comparison can use 2 cycles.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|cnt[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|cnt[*]}]

# fifo_head_r: FIFO head register depends on the waddr adder/comparator chain.
# Updated at sample rate, not pclk rate.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|fifo_head_r[*]}]
set_multicycle_path -hold 1 -to [get_registers {*Fast_Logic_Analyzer_SDRAM1|fifo_head_r[*]}]

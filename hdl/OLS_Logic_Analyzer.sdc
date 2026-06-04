# OLS Logic Analyzer timing constraints - minimal
# MAX1000 board: 12 MHz input, PLL x4 = 48 MHz core

create_clock -name clk_12m -period 83.333 [get_ports {CLK}]
derive_pll_clocks -create_base_clocks

set_clock_groups -asynchronous \
  -group [get_clocks {clk_12m}] \
  -group [get_clocks {*|pll1|*clk[0]*}]

# SPI signals are slow
set_false_path -from [get_ports {UART_RX UART_TX SPI_CS}]
set_false_path -to [get_ports {SPI_MISO}]
set_false_path -from [get_ports {GPIO* SEN_* LED*}]
set_false_path -to [get_ports {GPIO* SEN_* LED*}]

# Samples divide path: static config value (set once via SPI), multi-cycle
# LPM_DIVIDE takes 44 ns, so at 96 MHz (10.4 ns period) need 5 cycles (52 ns)
set_multicycle_path -setup 5 -from [get_registers *samples_d1*] -to [get_registers *samples_div*]
set_multicycle_path -hold 4 -from [get_registers *samples_d1*] -to [get_registers *samples_div*]

# Buffer/status control + readout paths: non-critical (buffer boundaries, address calc), allow 2 cycles
set_multicycle_path -setup 2 -from [get_registers *OLS_Interface1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|*]
set_multicycle_path -hold 1 -from [get_registers *OLS_Interface1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|*]
set_multicycle_path -setup 2 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|full_i*]
set_multicycle_path -hold 1 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|full_i*]
set_multicycle_path -setup 2 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|rd_mode*]
set_multicycle_path -hold 1 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|rd_mode*]
set_multicycle_path -setup 2 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|full_clr_pending*]
set_multicycle_path -hold 1 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|full_clr_pending*]
set_multicycle_path -setup 2 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|bram_raddr*]
set_multicycle_path -hold 1 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|bram_raddr*]
set_multicycle_path -setup 2 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|buf_full*]
set_multicycle_path -hold 1 -from [get_registers *Fast_Logic_Analyzer_SDRAM1|*] -to [get_registers *Fast_Logic_Analyzer_SDRAM1|buf_full*]

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

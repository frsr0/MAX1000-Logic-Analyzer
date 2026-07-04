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
# FAST_SPEED build: clk[0]=sys, clk[1]=fast, clk[2]=sdram, clk[3]=12 MHz ADC
# conversion clock (the modular ADC control core handles the clk<->adcblock
# crossing internally; result handoff to sys_clk is via rsp_valid handshake).
set_clock_groups -asynchronous \
  -group [get_clocks {*pll_inst|*clk[0]}] \
  -group [get_clocks {*pll_inst|*clk[1]}] \
  -group [get_clocks {*pll_inst|*clk[2]}] \
  -group [get_clocks {*pll_inst|*clk[3]}]

# External SPI timing:
# - SPI_SCK is the FTDI-generated clock that times the slave interface.
# - MOSI is captured relative to that clock.
# - MISO is launched relative to that same clock.
# The hardware validation suite treats 15 MHz as the validated ceiling.
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
set_clock_groups -asynchronous \
  -group [get_clocks SPI_SCK_EXT] \
  -group [get_clocks SPI_CS_QUAL] \
  -group [get_clocks {*pll_inst|*clk[0]}] \
  -group [get_clocks {*pll_inst|*clk[1]}] \
  -group [get_clocks {*pll_inst|*clk[2]}] \
  -group [get_clocks {*pll_inst|*clk[3]}]

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

# Capture write-pump: cap_stream_data latches the afifo show-ahead head, but the
# pump inserts a mandatory pop-bubble (one dead cycle after every registered
# fifo_rd) before it can present the next word to the SDRAM controller. So a NEW
# head produced by a pop is never captured into cap_stream_data until two clk[2]
# cycles after the afifo read address advanced — the M9K read has two cycles, not
# one. Timing it as single-cycle was a false critical path (the read-address ->
# cap_stream_data cone through the show-ahead mux). Verified on HW by the
# odd-interval debug-wave capture test, which catches any sample duplication/loss
# a wrong multicycle would introduce.
set_multicycle_path -setup 2 \
  -from [get_registers {*afifo*portb_address_reg*}] \
  -to   [get_registers {*cap_stream_data[*]}]
set_multicycle_path -hold 1 \
  -from [get_registers {*afifo*portb_address_reg*}] \
  -to   [get_registers {*cap_stream_data[*]}]

# stream_rem is the readout remaining-count. It is loaded once at block-read
# start (blk_req_edge / rd_mode) and then decremented only on s_rvalid — one
# SDRAM read completion. Completions are spaced by CAS latency plus the read
# cadence (>= 2 clk[2] cycles; cf. the sdram_dq -> sdram_s_readdata multicycle
# above), and the load precedes the first completion by several cycles, so every
# path into stream_rem has at least two clk[2] cycles. Timing them single-cycle
# is a false critical path.
set_multicycle_path -setup 2 -to [get_registers {*Fast_Logic_Analyzer_SDRAM*stream_rem[*]}]
set_multicycle_path -hold 1  -to [get_registers {*Fast_Logic_Analyzer_SDRAM*stream_rem[*]}]

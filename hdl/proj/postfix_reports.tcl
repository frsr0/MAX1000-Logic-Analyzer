project_open OLS_Logic_Analyzer -current_revision
create_timing_netlist -model slow -voltage 1200 -temperature 85
read_sdc
report_timing -setup -npaths 20 -detail full_path -file postfix_worst_setup.rpt
report_timing -setup -from_clock [get_clocks SDRAM_CHIP_CLK_OUT] -to_clock [get_clocks {*|clk[2]}] -npaths 10 -detail full_path -file postfix_sdram_to_clk2.rpt
report_timing -setup -from_clock [get_clocks {*|clk[2]}] -to_clock [get_clocks SDRAM_CHIP_CLK_OUT] -npaths 10 -detail full_path -file postfix_clk2_to_sdram.rpt
delete_timing_netlist
project_close

# STA Tcl script for timing analysis
load_package report
project_open OLS_Logic_Analyzer

# Get worst 10 paths for the SDRAM clock setup at the slow corner
create_timing_netlist -model slow -voltage 1200 -temperature 85
read_sdc

# Report worst 10 setup paths for the named generated clocks
report_timing -setup -npaths 10 -from_clock [get_clocks sdram_core_clk] -to_clock [get_clocks sdram_core_clk] -panel_name "Worst 10 Paths" -file sta_worst_clk2.txt
report_timing -setup -npaths 10 -from_clock [get_clocks fast_clk] -to_clock [get_clocks fast_clk] -panel_name "Worst 10 Paths clk1" -file sta_worst_clk1.txt

delete_timing_netlist
project_close

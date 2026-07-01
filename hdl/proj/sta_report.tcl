# STA Tcl script for timing analysis
load_package report
project_open OLS_Logic_Analyzer

# Get worst 10 paths for clk[2] setup at Slow 85C
create_timing_netlist -slow 85
read_sdc

# Report worst 10 setup paths for clk[2]
report_timing -setup -npaths 10 -clock_filter {*clk[2]} -panel_name "Worst 10 Paths" -file sta_worst_clk2.txt
report_timing -setup -npaths 10 -clock_filter {*clk[1]} -panel_name "Worst 10 Paths clk1" -file sta_worst_clk1.txt

delete_timing_netlist
project_close

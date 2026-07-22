load_package report
project_open OLS_Logic_Analyzer
create_timing_netlist -model slow -speed 8
read_sdc
update_timing_netlist

puts "=== clk[1] worst 10 ==="
report_timing -setup -npaths 10 -detail full_path -from_clock [get_clocks fast_clk] -to_clock [get_clocks fast_clk] -file clk1_worst10.rpt -stdout

puts "=== clk[2] worst 10 ==="
report_timing -setup -npaths 10 -detail full_path -from_clock [get_clocks sdram_core_clk] -to_clock [get_clocks sdram_core_clk] -file clk2_worst10.rpt -stdout

project_close
exit

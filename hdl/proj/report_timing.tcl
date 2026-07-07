# Report worst paths
load_package report
project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc

# Slow corner
set_operating_conditions -model slow -temperature 85 -voltage 1200
update_timing_netlist

puts "=== TOP 10 FAST_CLK ==="
report_timing -npaths 10 -setup -detail full_path -clock_filter {*clk[1]}

puts "=== TOP 10 pclk ==="
report_timing -npaths 10 -setup -detail full_path -clock_filter {*clk[2]}

project_close

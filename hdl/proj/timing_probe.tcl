load_package report
project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc
set_operating_conditions -model slow -temperature 85 -voltage 1200
update_timing_netlist
puts "=== FAST_CLK TOP PATHS ==="
report_timing -npaths 10 -setup -detail full_path -from_clock [get_clocks fast_clk] -to_clock [get_clocks fast_clk]
puts "=== SYS_CLK TOP PATHS ==="
report_timing -npaths 10 -setup -detail full_path -from_clock [get_clocks sys_clk] -to_clock [get_clocks sys_clk]
puts "=== ALL TOP PATHS ==="
report_timing -npaths 20 -setup -detail full_path
puts "=== VIOLATED PATHS ==="
report_timing -npaths 50 -setup -detail full_path -slack_lesser_than 0
project_close

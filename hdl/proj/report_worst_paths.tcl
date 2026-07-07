project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc
update_timing_netlist

set slow_clk "core|\\gen_use_pll_fast:pll_inst|\\gen_fast_speed:altpll_component|auto_generated|pll1|clk[1]"

puts "=== Worst clk[1] setup paths ==="
report_timing -from_clock $slow_clk -to_clock $slow_clk -setup -npaths 5 -detail full_path

puts "=== Worst clk[2] setup paths ==="
report_timing -from_clock "core|\\gen_use_pll_fast:pll_inst|\\gen_fast_speed:altpll_component|auto_generated|pll1|clk[2]" -to_clock "core|\\gen_use_pll_fast:pll_inst|\\gen_fast_speed:altpll_component|auto_generated|pll1|clk[2]" -setup -npaths 3 -detail full_path

delete_timing_netlist
project_close

project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc
update_timing_netlist
set paths [get_timing_paths -npaths 10 -setup -from_clock {core|gen_use_pll_fast:pll_inst|gen_fast_speed:altpll_component|auto_generated|pll1|clk[1]} -to_clock {core|gen_use_pll_fast:pll_inst|gen_fast_speed:altpll_component|auto_generated|pll1|clk[1]}]
foreach_in_collection p $paths {
  puts "----"
  puts "Slack: [get_path_info $p -slack]"
  puts "From: [get_path_info $p -from]"
  puts "To: [get_path_info $p -to]"
  puts "Launch: [get_path_info $p -launch_clock]"
  puts "Latch: [get_path_info $p -latch_clock]"
  puts "Elements: [get_path_info $p -num_logic_levels]"
}
delete_timing_netlist
project_close

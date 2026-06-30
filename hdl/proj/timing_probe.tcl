project_open OLS_Logic_Analyzer
create_timing_netlist
read_sdc
update_timing_netlist
report_timing -setup -npaths 10 -detail full_path -file worst_setup.rpt
report_timing -setup -to_clock {core|\gen_use_pll_fast:pll_inst|\gen_fast_speed:altpll_component|auto_generated|pll1|clk[2]} -npaths 20 -detail full_path -file worst_clk2_setup.rpt
project_close

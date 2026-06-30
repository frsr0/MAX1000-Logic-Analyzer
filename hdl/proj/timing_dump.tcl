project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc
update_timing_netlist
report_timing -setup -npaths 3 -detail full_path -from_clock {core|\gen_use_pll_fast:pll_inst|\gen_fast_speed:altpll_component|auto_generated|pll1|clk[2]} -to_clock {core|\gen_use_pll_fast:pll_inst|\gen_fast_speed:altpll_component|auto_generated|pll1|clk[2]}

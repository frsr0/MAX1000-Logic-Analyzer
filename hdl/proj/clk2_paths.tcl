project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist -model slow
read_sdc
update_timing_netlist
set c2 {core|\gen_use_pll_fast:pll_inst|\gen_fast_speed:altpll_component|auto_generated|pll1|clk[2]}
report_timing -setup -npaths 20 -detail summary -from_clock $c2 -to_clock $c2 -file clk2_worst.rpt
project_close

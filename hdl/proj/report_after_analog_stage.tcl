project_open OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc
update_timing_netlist
report_timing -setup -to_clock [get_clocks {*clk[1]}] -npaths 10 -detail path_only -file clk1_after_analog_stage.rpt
report_timing -setup -to_clock [get_clocks {*clk[2]}] -npaths 10 -detail path_only -file clk2_after_analog_stage.rpt
delete_timing_netlist
project_close

load_package report
project_open OLS_Logic_Analyzer
create_timing_netlist -post_fit
read_sdc
update_timing_netlist
report_timing -setup -npaths 3 -file worst3.rpt
project_close
exit

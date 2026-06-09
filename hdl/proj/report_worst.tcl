load_package report
project_open OLS_Logic_Analyzer
create_timing_netlist 8_slow_1200mv_85c
read_sdc
update_timing_netlist
report_timing -setup -npaths 3 -file worst3.rpt
project_close
exit

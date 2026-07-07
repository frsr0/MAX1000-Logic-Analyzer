project_open OLS_Logic_Analyzer
create_timing_netlist -model slow
read_sdc
update_timing_netlist
report_timing -setup -npaths 4 -detail full_path -file worst_paths.txt
project_close
exit

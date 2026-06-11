project_open OLS_Logic_Analyzer -current_revision
create_timing_netlist
read_sdc
update_timing_netlist
report_clock_fmax -npaths 3 -file fmax6.rpt
puts "Done"
project_close

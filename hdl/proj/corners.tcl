load_package report
project_open OLS_Logic_Analyzer -current_revision
create_timing_netlist
read_sdc
update_timing_netlist

puts "Available operating conditions:"
foreach_in_collection op [get_available_operating_conditions] {
    puts "  [get_operating_condition_info -name $op]: [get_operating_condition_info -voltage $op]V [get_operating_condition_info -temperature $op]C"
}
project_close

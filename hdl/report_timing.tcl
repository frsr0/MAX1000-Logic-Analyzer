# Report worst timing paths
load_package report
project_open C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/vhdplus/OLS_Logic_Analyzer -revision OLS_Logic_Analyzer
create_timing_netlist
read_sdc
update_timing_netlist
# Report worst 10 setup paths
report_timing -setup -npaths 10 -panel_name "Worst Setup Paths" -file "C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/vhdplus/timing_rpt.txt"
# Report clock summary
report_clock_transfers -panel_name "Clock Transfers" -file "C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/vhdplus/clock_xfer.txt"
project_close

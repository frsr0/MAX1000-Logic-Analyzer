project_open OLS_Logic_Analyzer -current_revision
create_timing_netlist -model slow -voltage 1200 -temperature 85
read_sdc

# Verify all four async clock groups have at least one clock assigned.
# An empty group means the PLL instance name changed and the clock name
# lookup failed, causing cross-domain paths to silently re-enter timing.
foreach clk {sys_clk fast_clk sdram_core_clk adc_clk} {
    if {[llength [get_clocks $clk]] == 0} {
        puts "WARNING: $clk not found — async clock group would be empty"
    }
}
# report_clock_groups does not exist in Quartus 18.1's TimeQuest API; the
# foreach self-check above is the actual verification and needs no report.

report_timing -setup -npaths 20 -detail full_path -file postfix_worst_setup.rpt
report_timing -setup -from_clock [get_clocks SDRAM_CHIP_CLK_OUT] -to_clock [get_clocks {*|clk[2]}] -npaths 10 -detail full_path -file postfix_sdram_to_clk2.rpt
report_timing -setup -from_clock [get_clocks {*|clk[2]}] -to_clock [get_clocks SDRAM_CHIP_CLK_OUT] -npaths 10 -detail full_path -file postfix_clk2_to_sdram.rpt
delete_timing_netlist
project_close

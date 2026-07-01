package require ::quartus::report

project_open OLS_Logic_Analyzer -current_revision

create_timing_netlist -model slow -voltage 1200 -temperature 85
read_sdc

puts "=== All clocks ==="
foreach clock [get_clocks] {
    puts "  Clock: [get_clock_info $clock -name]  Freq: [get_clock_info $clock -frequency]"
}

puts ""
puts "=== Worst 10 setup paths (all clocks, Slow 85C) ==="
set paths [get_timing_paths -setup -npaths 10 -nworst 3]
set i 1
foreach path $paths {
    set slack [get_path_info $path -slack]
    set from [get_node_info [get_path_info $path -from_node] -name]
    set to   [get_node_info [get_path_info $path -to_node] -name]
    set from_clk [get_clock_info [get_path_info $path -from_clock] -name]
    set to_clk   [get_clock_info [get_path_info $path -to_clock] -name]
    puts "[format %2d $i]: Slack [format %5.3f $slack] ns"
    puts "      From: $from_clk -> $to_clk"
    puts "      $from -> $to"
    incr i
}

delete_timing_netlist
project_close

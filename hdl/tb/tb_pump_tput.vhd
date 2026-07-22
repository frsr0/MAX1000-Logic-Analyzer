-- Measure the real FLA write-pump throughput through the actual SDRAM
-- controller path. The bench drives the front-end fast enough to keep the
-- async FIFO fed, then samples the hardware-readable pump counters over a
-- fixed pclk window.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_pump_tput is
  generic (RATE_DIV : natural := 4);   -- 200/4 = 50 MHz front-end (fills afifo)
end tb_pump_tput;

architecture bench of tb_pump_tput is
  constant SYS_CLK_HALF    : time := 4990 ps;  -- 100.2 MHz
  constant FAST_CLK_HALF   : time := 2495 ps;  -- 200.4 MHz period / 2
  constant SDRAM_CLK_HALF  : time := 2994 ps;  -- ~167 MHz
  signal clk, fastclk, sdram_core_clk, sdram_clk_model : std_logic := '0';
  signal rdiv : natural range 1 to 500000000 := RATE_DIV;
  signal samples_in : natural range 1 to 3000000 := 2000000;
  signal run, full, armed, fast_mode : std_logic := '0';
  signal inputs  : std_logic_vector(15 downto 0) := (others => '0');
  signal address : natural range 0 to 3000000 := 0;
  signal outputs : std_logic_vector(15 downto 0);
  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n, sdram_ras_n, sdram_we_n, sdram_clk : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal status     : std_logic_vector(7 downto 0);
  signal pump_valid_cycles   : std_logic_vector(31 downto 0);
  signal pump_ready_cycles   : std_logic_vector(31 downto 0);
  signal pump_accept_cycles  : std_logic_vector(31 downto 0);
  signal pump_stall_cycles   : std_logic_vector(31 downto 0);
  signal pump_nodata_cycles  : std_logic_vector(31 downto 0);
  signal pump_overflow_count : std_logic_vector(31 downto 0);
begin
  clk            <= not clk            after SYS_CLK_HALF;
  fastclk        <= not fastclk        after FAST_CLK_HALF;
  sdram_core_clk <= not sdram_core_clk after SDRAM_CLK_HALF;
  sdram_clk_model <= transport sdram_core_clk after 1.5 ns;
  process(fastclk) begin
    if rising_edge(fastclk) then inputs <= std_logic_vector(unsigned(inputs)+1); end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples=>3000000, Channels=>16, Sim=>false, FAST_SPEED=>true,
      CLK_Frequency=>166666667, SDRAM_CLK_HZ=>166666667, SAMPLE_CLK_HZ=>200000000)
    port map (CLK=>clk, SDRAM_CLK_IN=>sdram_core_clk, CLK_150=>open, Rate_Div=>rdiv,
      Samples=>samples_in, Start_Offset=>0, Run=>run, Full=>full, Inputs=>inputs,
      Address=>address, Outputs=>outputs, sdram_addr=>sdram_addr, sdram_ba=>sdram_ba,
      sdram_cas_n=>sdram_cas_n, sdram_dq=>sdram_dq, sdram_dqm=>sdram_dqm,
      sdram_ras_n=>sdram_ras_n, sdram_we_n=>sdram_we_n, sdram_cke=>sdram_cke,
      sdram_cs_n=>sdram_cs_n, sdram_clk=>sdram_clk, Status=>status,
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>fastclk, Continuous_Mode=>'0',
      Pump_Valid_Cycles=>pump_valid_cycles, Pump_Ready_Cycles=>pump_ready_cycles,
      Pump_Accept_Cycles=>pump_accept_cycles, Pump_Stall_Cycles=>pump_stall_cycles,
      Pump_NoData_Cycles=>pump_nodata_cycles, Pump_Overflow_Count=>pump_overflow_count);

  SDRAM : entity work.sdram_pin_model
    generic map (CL=>3, STRICT=>true)
    port map (clk=>sdram_clk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  main : process
    variable acc0, stall0, nopres0, valid0, ready0, overflow0 : natural := 0;
    variable acc1, stall1, nopres1, valid1, ready1, overflow1 : natural := 0;
    variable acc, stall, nopres, valid, ready, overflow, cyc : integer := 0;
    constant INIT_CYCLES : natural := 900000;
    constant WIN : integer := 8000;
  begin
    wait_cycles(sdram_core_clk, INIT_CYCLES);
    fast_mode<='1'; armed<='1'; wait_cycles(clk,40); run<='1';
    -- Let the SDRAM controller exit reset/init and the capture FIFO fill.
    wait_cycles(sdram_core_clk, 400);
    valid0 := to_integer(unsigned(pump_valid_cycles));
    ready0 := to_integer(unsigned(pump_ready_cycles));
    acc0 := to_integer(unsigned(pump_accept_cycles));
    stall0 := to_integer(unsigned(pump_stall_cycles));
    nopres0 := to_integer(unsigned(pump_nodata_cycles));
    overflow0 := to_integer(unsigned(pump_overflow_count));
    for i in 0 to WIN-1 loop
      wait until rising_edge(sdram_core_clk);
    end loop;
    valid1 := to_integer(unsigned(pump_valid_cycles));
    ready1 := to_integer(unsigned(pump_ready_cycles));
    acc1 := to_integer(unsigned(pump_accept_cycles));
    stall1 := to_integer(unsigned(pump_stall_cycles));
    nopres1 := to_integer(unsigned(pump_nodata_cycles));
    overflow1 := to_integer(unsigned(pump_overflow_count));
    cyc := WIN;
    valid := integer(valid1 - valid0);
    ready := integer(ready1 - ready0);
    acc := integer(acc1 - acc0);
    stall := integer(stall1 - stall0);
    nopres := integer(nopres1 - nopres0);
    overflow := integer(overflow1 - overflow0);
    report "REAL pump over " & integer'image(cyc) & " pclk cycles: valid=" & integer'image(valid) &
           " ready=" & integer'image(ready) &
           " accept=" & integer'image(acc) &
           " stall(v&!r)=" & integer'image(stall) &
           " no_data=" & integer'image(nopres) &
           " overflow=" & integer'image(overflow);
    report "  cycles/accept = " & real'image(real(cyc)/real(maximum(1,acc))) &
           "  => pump rate @167 = " & real'image(166.667/(real(cyc)/real(maximum(1,acc)))) & " MHz";
    report "  SDRAM cmds: ACT=" & integer'image(<< signal .tb_pump_tput.sdram.n_act : natural >>) &
           " WRITE=" & integer'image(<< signal .tb_pump_tput.sdram.n_wr : natural >>) &
           " PRE=" & integer'image(<< signal .tb_pump_tput.sdram.n_pre : natural >>) &
           "  (ACT/WRITE ~1 => row NOT held open / page-mode broken)";
    std.env.finish;
    wait;
  end process;
end bench;

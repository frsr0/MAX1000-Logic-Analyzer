-- Measure the real FLA write-pump throughput through the actual SDRAM
-- controller path. The bench drives the front-end fast enough to keep the
-- async FIFO fed, then samples the hardware-readable pump counters over a
-- fixed pclk window.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_pump_tput_max is
  generic (RATE_DIV : natural := 1);  -- 200.4 MS/s: producer faster than the drain
end tb_pump_tput_max;

architecture bench of tb_pump_tput_max is
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
      CLK_Frequency=>166666667, SDRAM_CLK_HZ=>166666667, SAMPLE_CLK_HZ=>200000000,
      Enable_Pump_Metrics=>true)
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
    generic map (CL=>3, STRICT=>true, FAIL_ON_VIOLATION=>true)
    port map (clk=>sdram_clk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  main : process
    variable acc0, stall0, nopres0, valid0, ready0, overflow0 : natural := 0;
    variable acc1, stall1, nopres1, valid1, ready1, overflow1 : natural := 0;
    variable acc, stall, nopres, valid, ready, overflow, cyc : integer := 0;
    variable first : boolean := true;
    variable c32, cend, a32, aend : integer := 0;
    constant INIT_CYCLES : natural := 900000;
    constant WIN : integer := 8000;
  begin
    wait_cycles(sdram_core_clk, INIT_CYCLES);
    fast_mode<='1'; armed<='1'; wait_cycles(clk,40); run<='1';
    -- Let the SDRAM controller exit reset/init and the capture FIFO fill.
    -- The producer outruns the drain, so the capture ends in an overflow after
    -- ~192 FIFO words. Measure the steady drain rate between the 32nd accepted
    -- word and the overflow: words per pclk cycle.
    first := true;
    c32 := 0; cend := 0; a32 := 0; aend := 0;
    for i in 0 to 20000 loop
      wait until rising_edge(sdram_core_clk);
      acc := to_integer(unsigned(pump_accept_cycles));
      if first and acc >= 32 then
        first := false; c32 := i; a32 := acc;
      end if;
      if (not first) and to_integer(unsigned(pump_overflow_count)) = 0 then
        cend := i; aend := acc;
      end if;
      exit when to_integer(unsigned(pump_overflow_count)) /= 0 and i > c32 + 10;
    end loop;
    report "MAX DRAIN (producer 200.4 MS/s): " & integer'image(aend - a32) & " words in " &
           integer'image(cend - c32) & " pclk cycles = " &
           real'image(real(aend - a32) / real(maximum(1, cend - c32))) & " words per pclk cycle";
    assert aend - a32 >= 100 report "FAIL: too few words to measure" severity failure;
    assert real(aend - a32) / real(cend - c32) > 0.80
      report "FAIL: max drain rate below 0.80 words per pclk" severity failure;
    std.env.finish;
    wait;
  end process;
end bench;

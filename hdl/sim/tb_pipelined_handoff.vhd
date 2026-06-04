library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_pipelined_handoff is
  generic (TEST : string := "tc_prefetch");
end tb_pipelined_handoff;

architecture sim of tb_pipelined_handoff is
  constant CLK_PERIOD : time := 20.833 ns;

  signal clk     : std_logic := '0';
  signal running : boolean := true;

  signal rate_div     : natural range 1 to 12000000 := 12;
  signal samples      : natural range 1 to 1048576 := 192;
  signal start_offset : natural range 0 to 1048576 := 0;
  signal run_f        : std_logic := '0';
  signal full         : std_logic;
  signal inputs       : std_logic_vector(15 downto 0) := (others => '0');
  signal address      : natural range 0 to 1048576 := 0;
  signal outputs      : std_logic_vector(15 downto 0);
  signal armed        : std_logic := '0';
  signal fast_mode    : std_logic := '0';
  signal fast_clk     : std_logic := '0';
  signal status       : std_logic_vector(7 downto 0);
  signal cont_mode    : std_logic := '0';
  signal buf_full     : std_logic_vector(2 downto 0) := (others => '0');
  signal buf_ack      : std_logic_vector(2 downto 0) := (others => '0');

  signal sample_en   : std_logic;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  FLA : entity work.Fast_Logic_Analyzer_SDRAM(rtl)
    generic map (Max_Samples => 1048576, Channels => 16, Sim => true)
    port map (
      CLK => clk, CLK_150 => open,
      Rate_Div => rate_div, Samples => samples,
      Start_Offset => start_offset, Run => run_f,
      Full => full, Inputs => inputs(15 downto 0),
      Address => address, Outputs => outputs,
      sdram_addr => open, sdram_ba => open,
      sdram_cas_n => open, sdram_dq => open,
      sdram_dqm => open, sdram_ras_n => open,
      sdram_we_n => open, sdram_cke => open,
      sdram_cs_n => open, sdram_clk => open,
      Status => status, s_burst => open,
      Armed => armed, Fast_Mode => fast_mode,
      FAST_CLK => fast_clk,
      Continuous_Mode => cont_mode,
      Buffer_Full => buf_full,
      Buffer_Ack => buf_ack
    );

  sample_en <= <<signal .tb_pipelined_handoff.FLA.sample_en : std_logic>>;

  process
  begin
    wait for 10 us;

    if TEST = "all" or TEST = "tc_prefetch" then
      report "--- tc_prefetch: Prefetch primes next buffer read ---" severity note;
      cont_mode <= '1';
      samples <= 192;
      run_f <= '1';
      wait until buf_full(0) = '1' for 200 us;
      assert buf_full(0) = '1' report "tc_prefetch: Buffer A not full" severity failure;
      wait until buf_full(1) = '1' for 200 us;
      assert buf_full(1) = '1' report "tc_prefetch: Buffer B not full" severity failure;
      wait until buf_full(2) = '1' for 200 us;
      assert buf_full(2) = '1' report "tc_prefetch: Buffer C not full" severity failure;
      wait until full = '1' for 200 us;
      assert full = '1' report "tc_prefetch: Full not asserted" severity failure;
      report "tc_prefetch: All 3 buffers full, PASS" severity note;
      run_f <= '0';
      wait for 500 ns;
      cont_mode <= '0';
    end if;

    if TEST = "all" or TEST = "tc_triple_fill" then
      report "--- tc_triple_fill: A-B-C fill order, backpressure ---" severity note;
      cont_mode <= '1';
      samples <= 192;
      run_f <= '1';
      wait until buf_full(0) = '1' for 200 us;
      assert buf_full(0) = '1' and full = '0' report "tc_triple_fill: A full, no backpressure yet" severity failure;
      report "tc_triple_fill: A full, still capturing (OK)" severity note;
      wait until buf_full(1) = '1' for 200 us;
      assert buf_full(1) = '1' and full = '0' report "tc_triple_fill: B full, no backpressure yet" severity failure;
      report "tc_triple_fill: B full, still capturing (OK)" severity note;
      wait until buf_full(2) = '1' for 200 us;
      assert buf_full(2) = '1' report "tc_triple_fill: C not full" severity failure;
      report "tc_triple_fill: C full" severity note;
      wait until full = '1' for 200 us;
      assert full = '1' report "tc_triple_fill: Full not asserted at 3 full" severity failure;
      report "tc_triple_fill: Backpressure Full asserted only when all 3 full (PASS)" severity note;
      run_f <= '0';
      wait for 500 ns;
      cont_mode <= '0';
    end if;

    if TEST = "all" then
      report "ALL TESTS: PASS" severity note;
    end if;

    running <= false;
    wait;
  end process;

end sim;

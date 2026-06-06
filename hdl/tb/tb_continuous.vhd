library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_continuous is
  generic (CLK_HALF : time := 5 ns);
end tb_continuous;

architecture bench of tb_continuous is
  constant CHANNELS   : natural := 8;
  constant TEST_WORDS : natural := 8;

  signal clk       : std_logic := '0';
  signal rate_div  : natural range 1 to 150000000 := 4;
  signal samples_in : natural range 1 to 3000000 := TEST_WORDS;
  signal start_offset : natural range 0 to 3000000 := 0;
  signal run       : std_logic := '0';
  signal full      : std_logic;
  signal inputs    : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
  signal address   : natural range 0 to 3000000 := 0;
  signal outputs   : std_logic_vector(15 downto 0);
  signal armed     : std_logic := '0';
  signal fast_mode : std_logic := '1';
  signal continuous_mode : std_logic := '1';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack  : std_logic_vector(2 downto 0) := (others => '0');
  signal sdram_dq  : std_logic_vector(15 downto 0);
  signal status    : std_logic_vector(7 downto 0);
  signal fast_clk  : std_logic := '0';
  signal sample_en : std_logic;
  signal bram_waddr : natural range 0 to 1023;
  signal bram_wren  : std_logic;

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;
  inputs <= x"A0";

  sample_en  <= << signal .tb_continuous.dut.sample_en : std_logic >>;
  bram_wren  <= << signal .tb_continuous.dut.bram_wren : std_logic >>;
  bram_waddr <= << signal .tb_continuous.dut.bram_waddr : natural range 0 to 1023 >>;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples => 3000000, Channels => CHANNELS, Sim => true)
    port map (
      CLK => clk, CLK_150 => open, Rate_Div => rate_div,
      Samples => samples_in, Start_Offset => start_offset,
      Run => run, Full => full, Inputs => inputs,
      Address => address, Outputs => outputs,
      sdram_dq => sdram_dq, Status => status, s_burst => open,
      Armed => armed, Fast_Mode => fast_mode,
      FAST_CLK => fast_clk, Continuous_Mode => continuous_mode,
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack,
      sdram_addr => open, sdram_ba => open, sdram_cas_n => open,
      sdram_cke => open, sdram_cs_n => open, sdram_dqm => open,
      sdram_ras_n => open, sdram_we_n => open, sdram_clk => open
    );

  process
  begin
    wait_cycles(clk, 30);

    report "Test 1: Continuous fast mode fill + Full assertion";
    armed <= '1'; run <= '1';
    -- Wait for Full
    wait until rising_edge(full);
    report "Full asserted";
    check(buffer_full(0) = '1', "Buffer_full(0) should be '1' in continuous mode");
    report "Test 1: PASS";

    report "Test 2: Ack buffer, Full clears, new cycle starts";
    -- Ack the buffer — fast mode continuous clears bram_post_cnt via full_clr_pending
    buffer_ack(0) <= '1';
    wait_cycles(clk, 3);
    buffer_ack(0) <= '0';
    wait_cycles(clk, 2);
    check(full = '0', "Full should clear after ack");
    report "Test 2: PASS";

    report "Test 3: Second full cycle after ack";
    wait until rising_edge(full);
    report "Full reasserted - continuous cycle working";
    check(full = '1', "Full should reassert for second capture cycle");
    report "Test 3: PASS";

    run <= '0';
    wait_cycles(clk, 10);
    report "=== ALL CONTINUOUS CAPTURE TESTS PASSED ===";
    wait;
  end process;
end bench;

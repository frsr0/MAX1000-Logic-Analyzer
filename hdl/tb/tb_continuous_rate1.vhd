library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_continuous_rate1 is
  generic (CLK_HALF : time := 5 ns);
end tb_continuous_rate1;

architecture bench of tb_continuous_rate1 is
  constant CHANNELS    : natural := 8;
  -- Samples=96 -> buf_limit_r = 96/6 = 16 words per buffer
  constant TEST_WORDS  : natural := 96;
  constant BUF_WORDS   : natural := TEST_WORDS / 6;

  signal clk       : std_logic := '0';
  signal rate_div  : natural range 1 to 150000000 := 1;
  signal samples_in : natural range 1 to 3000000 := TEST_WORDS;
  signal start_offset : natural range 0 to 3000000 := 0;
  signal run       : std_logic := '0';
  signal full      : std_logic;
  signal inputs    : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
  signal address   : natural range 0 to 3000000 := 0;
  signal outputs   : std_logic_vector(15 downto 0);
  signal armed     : std_logic := '0';
  signal fast_mode : std_logic := '0';
  signal continuous_mode : std_logic := '1';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack  : std_logic_vector(2 downto 0) := (others => '0');
  signal sdram_dq  : std_logic_vector(15 downto 0);
  signal status    : std_logic_vector(7 downto 0);
  signal fast_clk  : std_logic := '0';
  signal sample_en : std_logic;
  signal fifo_cnt  : natural range 0 to 16;
  signal buf_sel   : std_logic_vector(1 downto 0);

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;

  inputs <= x"A0";

  -- Probe internal signals
  sample_en     <= << signal .tb_continuous_rate1.dut.sample_en : std_logic >>;
  fifo_cnt      <= << signal .tb_continuous_rate1.dut.fifo_cnt : natural range 0 to 16 >>;
  buf_sel       <= << signal .tb_continuous_rate1.dut.buf_sel : std_logic_vector(1 downto 0) >>;

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

    ------------------------------------------------------------------
    -- Test 1: Max-rate continuous fill — buffer order A → B → C
    ------------------------------------------------------------------
    report "Test 1: Max-rate continuous fill at Rate_Div=1";
    armed <= '1'; run <= '1';

    -- Wait for buffer A full
    wait_until(clk, buffer_full(0), '1', 5 ms, "Buffer A should fill at Rate_Div=1");
    report "Buffer A full";

    -- Wait for buffer B full
    wait_until(clk, buffer_full(1), '1', 10 ms, "Buffer B should fill after A");
    report "Buffer B full";
    check(buffer_full(0) = '1', "Buffer A should still be full when B fills");

    -- Wait for buffer C full
    wait_until(clk, buffer_full(2), '1', 15 ms, "Buffer C should fill after B");
    report "Buffer C full";

    -- Verify Full asserts after all 3 full
    wait_until(clk, full, '1', 1 ms, "Full should assert after all 3 buffers full");
    check(full = '1', "Full asserted");
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: Ack + resume — capture continues with acked buffer
    ------------------------------------------------------------------
    report "Test 2: Ack buffer A, verify capture resumes";
    buffer_ack(0) <= '1';
    wait_cycles(clk, 2);
    buffer_ack(0) <= '0';
    wait_cycles(clk, 2);

    -- Full should clear (via full_clr_pending in continuous mode)
    check(full = '0', "Full should clear after ack");

    -- Buffer A should fill again
    wait_until(clk, buffer_full(0), '1', 5 ms, "Buffer A should fill again after ack");
    report "Buffer A full again after ack";
    check(buffer_full(0) = '1', "Buffer A full after resume");

    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: Ack all, stop cleanly
    ------------------------------------------------------------------
    report "Test 3: Clean stop";
    buffer_ack(1) <= '1'; buffer_ack(2) <= '1';
    wait_cycles(clk, 2);
    buffer_ack(1) <= '0'; buffer_ack(2) <= '0';
    run <= '0';
    wait_cycles(clk, 20);
    report "Test 4: PASS";

    report "=== ALL CONTINUOUS RATE=1 TESTS PASSED ===";
    wait;
  end process;
end bench;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_continuous_rate1 is
  generic (CLK_HALF : time := 5 ns);
end tb_continuous_rate1;

architecture bench of tb_continuous_rate1 is
  constant CHANNELS    : natural := 8;
  -- Continuous buffers are fixed 512 samples each; budget all three (3*512).
  constant TEST_WORDS  : natural := 1536;
  constant BUF_WORDS   : natural := 512;

  signal clk       : std_logic := '0';
  signal rate_div  : natural range 1 to 500000000 := 1;
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
  signal fifo_cnt  : natural range 0 to 64;
  signal buf_sel   : std_logic_vector(1 downto 0);

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;

  inputs <= x"A0";

  -- Probe internal signals
  fifo_cnt      <= << signal .tb_continuous_rate1.dut.fifo_cnt : natural range 0 to 64 >>;
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
    -- Test 1: Continuous triple-buffer auto-rotation, no intermediate acks.
    -- Verifies the write pump rotates A -> B -> C on its own (each earlier
    -- buffer stays full as the next fills). Rate_Div=1 is the max-rate path
    -- that used to trip a documented start-up race. The ack/resume +
    -- Full-after-budget behaviour is covered by tb_continuous; this TB focuses
    -- on unforced rotation at the fastest divider.
    ------------------------------------------------------------------
    report "Test 1: Continuous triple-buffer auto-rotation at Rate_Div=1";
    armed <= '1'; run <= '1';

    wait_until(clk, buffer_full(0), '1', 5 ms, "Buffer A should fill");
    report "Buffer A full";

    wait_until(clk, buffer_full(1), '1', 10 ms, "Buffer B should fill after A");
    report "Buffer B full";
    check(buffer_full(0) = '1', "Buffer A should still be full when B fills");

    wait_until(clk, buffer_full(2), '1', 15 ms, "Buffer C should fill after B");
    report "Buffer C full";
    check(buffer_full(0) = '1', "Buffer A still full when C fills");
    check(buffer_full(1) = '1', "Buffer B still full when C fills");
    report "Test 1: PASS (A->B->C auto-rotation, no acks)";

    run <= '0';
    wait_cycles(clk, 20);

    report "=== ALL CONTINUOUS RATE TESTS PASSED ===";
    wait;
  end process;
end bench;

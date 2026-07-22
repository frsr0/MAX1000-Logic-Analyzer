library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_continuous_rate1 is
  generic (CLK_HALF : time := 5 ns);
end tb_continuous_rate1;

architecture bench of tb_continuous_rate1 is
  constant CHANNELS    : natural := 8;
  -- Exercise more than the old 3x512 buffer bookkeeping. Continuous mode
  -- should now be governed by the full SDRAM ring metadata, not A/B/C flags.
  constant TEST_WORDS  : natural := 1536;

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
  signal producer_index : std_logic_vector(31 downto 0);
  signal oldest_index   : std_logic_vector(31 downto 0);
  signal newest_index   : std_logic_vector(31 downto 0);
  signal overrun_count  : std_logic_vector(31 downto 0);
  signal fast_clk  : std_logic := '0';
  signal buf_sel   : std_logic_vector(1 downto 0);

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;

  inputs <= x"A0";

  -- Probe internal signals
  buf_sel       <= << signal .tb_continuous_rate1.dut.buf_sel : std_logic_vector(1 downto 0) >>;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples => 3000000, Channels => CHANNELS, Sim => true)
    port map (
      CLK => clk, CLK_150 => open, Rate_Div => rate_div,
      Samples => samples_in, Start_Offset => start_offset,
      Run => run, Full => full, Inputs => inputs,
      Address => address, Outputs => outputs,
      sdram_dq => sdram_dq, Status => status,
      Armed => armed, Fast_Mode => fast_mode,
      FAST_CLK => fast_clk, Continuous_Mode => continuous_mode,
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack,
      Producer_Index => producer_index,
      Oldest_Index => oldest_index,
      Newest_Index => newest_index,
      Overrun_Count => overrun_count,
      sdram_addr => open, sdram_ba => open, sdram_cas_n => open,
      sdram_cke => open, sdram_cs_n => open, sdram_dqm => open,
      sdram_ras_n => open, sdram_we_n => open, sdram_clk => open
    );

  process
  begin
    wait_cycles(clk, 30);

    ------------------------------------------------------------------
    -- Test 1: Continuous ring progress, no intermediate acks.
    -- Rate_Div=1 is the max-rate path. The ring should keep committing samples
    -- without depending on legacy 3x512 buffer-full flags, and should not
    -- report overrun before the full SDRAM ring wraps.
    ------------------------------------------------------------------
    report "Test 1: Continuous SDRAM ring progress at Rate_Div=1";
    armed <= '1'; run <= '1';

    wait until to_integer(unsigned(producer_index)) >= TEST_WORDS for 500 us;
    check(to_integer(unsigned(producer_index)) >= TEST_WORDS,
          "Producer index should reach requested continuous capture length");
    check(to_integer(unsigned(oldest_index)) = 0,
          "Oldest index should stay at zero before ring wrap");
    check(to_integer(unsigned(overrun_count)) = 0,
          "Overrun counter should stay zero before ring wrap");
    check(to_integer(unsigned(newest_index)) + 1 = to_integer(unsigned(producer_index)),
          "Newest index should track the last committed sample");
    check(buffer_full = "000",
          "Legacy A/B/C buffer flags should not gate continuous ring writes");
    report "Test 1: PASS (continuous ring progress, no false overrun)";

    run <= '0';
    wait_cycles(clk, 20);

    report "=== ALL CONTINUOUS RATE TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;
end bench;

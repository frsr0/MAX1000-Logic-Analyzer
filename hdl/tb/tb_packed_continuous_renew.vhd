-- Regression for the packed-mode continuous/live auto-renew fix
-- (Fast_Logic_Analyzer_SDRAM.vhd Stage 2c/2d): verifies that a packed
-- capture with Continuous_Mode='1' keeps accepting words past its
-- configured Samples budget instead of permanently deasserting
-- Packed_Ready once sample_remaining hits zero.
--
-- Deliberately configures a TINY Samples budget (64) so several renewal
-- cycles happen within a short simulation. A producer that never stops
-- offering packed_valid proves the fix: total accepted words must exceed
-- several multiples of the budget, not plateau at/near it.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_packed_continuous_renew is
end tb_packed_continuous_renew;

architecture bench of tb_packed_continuous_renew is
  constant FAST_PERIOD : time := 5.0 ns;    -- 200 MHz
  constant PCLK_PERIOD : time := 6.0 ns;     -- 166.67 MHz

  signal fast_clk : std_logic := '0';
  signal pclk     : std_logic := '0';

  signal run       : std_logic := '0';
  signal full      : std_logic;
  signal inputs    : std_logic_vector(15 downto 0) := (others => '0');
  signal address   : natural range 0 to 3000000 := 0;
  signal outputs   : std_logic_vector(15 downto 0);
  signal status    : std_logic_vector(7 downto 0);
  signal fast_mode : std_logic := '1';
  signal armed     : std_logic := '0';
  signal rate_div    : natural range 1 to 500000000 := 1;
  -- Tiny budget: 64 fast_clk cycles. At a throttled 1-word-per-4-cycle
  -- producer that's only ~16 accepted words per budget window if renewal
  -- did NOT happen -- the assertion below requires far more than that.
  signal samples_cfg : natural range 1 to 3000000 := 64;

  signal packed_mode  : std_logic := '1';
  signal packed_data  : std_logic_vector(15 downto 0) := (others => '0');
  signal packed_valid : std_logic := '0';
  signal packed_ready : std_logic;
  signal packed_accepted : integer := 0;

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n : std_logic;
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal sdram_ras_n, sdram_we_n : std_logic;
  signal sdram_clk  : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
begin

  fast_clk <= not fast_clk after FAST_PERIOD / 2;
  pclk     <= not pclk     after PCLK_PERIOD / 2;

  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      inputs <= std_logic_vector(unsigned(inputs) + 1);
    end if;
  end process;

  dut : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples   => 3000000,
      Channels      => 16,
      Sim           => true,
      FAST_SPEED    => true,
      FAST_RAW_BUILD => true,
      CLK_Frequency => 166666667,
      SDRAM_CLK_HZ  => 166666667,
      SAMPLE_CLK_HZ => 200000000
    )
    port map (
      CLK          => pclk,
      SDRAM_CLK_IN => pclk,
      CLK_150      => open,
      Rate_Div     => rate_div,
      Samples      => samples_cfg,
      Start_Offset => 0,
      Run          => run,
      Full         => full,
      Inputs       => inputs,
      Address      => address,
      Outputs      => outputs,
      sdram_addr   => sdram_addr,
      sdram_ba     => sdram_ba,
      sdram_cas_n  => sdram_cas_n,
      sdram_cke    => sdram_cke,
      sdram_cs_n   => sdram_cs_n,
      sdram_dq     => sdram_dq,
      sdram_dqm    => sdram_dqm,
      sdram_ras_n  => sdram_ras_n,
      sdram_we_n   => sdram_we_n,
      sdram_clk    => sdram_clk,
      Status       => status,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      FAST_CLK     => fast_clk,
      Continuous_Mode => '1',
      Packed_Mode  => packed_mode,
      Packed_Data  => packed_data,
      Packed_Valid => packed_valid,
      Packed_Ready => packed_ready
    );

  -- Producer never stops offering a word (worst case for the budget: it
  -- would exhaust the tiny 64-cycle window almost immediately if the fix
  -- did not renew it).
  process(fast_clk)
    variable next_word : unsigned(15 downto 0) := x"0001";
  begin
    if rising_edge(fast_clk) then
      if packed_valid = '1' and packed_ready = '1' then
        packed_accepted <= packed_accepted + 1;
        next_word := next_word + 1;
      end if;
      packed_valid <= '1';
      packed_data  <= std_logic_vector(next_word);
    end if;
  end process;

  sdram_model : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => false)
    port map (
      clk   => sdram_clk,
      cke   => sdram_cke,
      cs_n  => sdram_cs_n,
      ras_n => sdram_ras_n,
      cas_n => sdram_cas_n,
      we_n  => sdram_we_n,
      ba    => sdram_ba,
      addr  => sdram_addr,
      dqm   => sdram_dqm,
      dq    => sdram_dq
    );

  main : process
  begin
    report "=== Starting packed continuous-renew test ===";
    armed <= '1';
    wait for 100 ns;
    run <= '1';
    -- Run long enough to span MANY multiples of the 64-cycle budget
    -- (64 cycles = 320 ns at 200 MHz; 200 us spans ~625 budget windows).
    wait for 200 us;
    run <= '0';
    report "packed_accepted after 200us with Continuous_Mode='1', " &
           "Samples=64: " & integer'image(packed_accepted);
    -- Without the fix, Packed_Ready would permanently deassert after the
    -- FIRST budget window (~64 cycles = a handful of accepted words) and
    -- packed_accepted would plateau there. With the fix, a producer that
    -- never stops offering should be accepted essentially every cycle
    -- (200 us / 5 ns = 40000 cycles), so demand a count far beyond one
    -- budget window as proof the renewal kept firing.
    assert packed_accepted > 1000
      report "FAIL: packed_accepted=" & integer'image(packed_accepted) &
             " -- budget did not renew (continuous packed capture would " &
             "permanently stall after the first ~64-cycle window)"
      severity failure;
    report "PASS: packed continuous capture renewed its budget repeatedly " &
           "(" & integer'image(packed_accepted) & " words accepted over " &
           "~625 budget windows)";
    std.env.finish;
    wait;
  end process;

end bench;

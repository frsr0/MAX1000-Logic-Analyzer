library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_fast_analyzer is
  generic (
    CLK_FREQ     : natural := 96000000;
    SAMPLE_RATE  : natural := 12;
    CHANNELS     : natural := 8;
    MAX_SAMPLES  : natural := 1048576
  );
end tb_fast_analyzer;

architecture bench of tb_fast_analyzer is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);

  signal clk : std_logic := '0';
  signal clk_150 : std_logic;
  signal rate_div : natural range 1 to 150000000 := SAMPLE_RATE;
  signal samples_s : natural range 1 to MAX_SAMPLES := 1024;
  signal start_offset : natural range 0 to MAX_SAMPLES := 0;
  signal run : std_logic := '0';
  signal full : std_logic;
  signal inputs : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
  signal address : natural range 0 to MAX_SAMPLES := 0;
  signal outputs : std_logic_vector(15 downto 0);

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_dq : std_logic_vector(15 downto 0);
  signal sdram_dqm : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n : std_logic;
  signal sdram_cke : std_logic := '1';
  signal sdram_cs_n : std_logic := '0';
  signal sdram_clk : std_logic;
  signal status : std_logic_vector(7 downto 0);
  signal s_burst : std_logic;
  signal armed : std_logic := '0';
  signal fast_mode : std_logic := '0';
  signal continuous_mode : std_logic := '0';
  signal buffer_full_s : std_logic_vector(2 downto 0) := (others => '0');
  signal buffer_ack_s : std_logic_vector(2 downto 0) := (others => '0');

  signal pat_toggle : std_logic := '0';
  signal pat_enable : boolean := false;
begin

  gen_clk(clk, CLK_PERIOD / 2);

  -- Pattern generator for CH0 (concurrent process)
  process(clk)
  begin
    if rising_edge(clk) then
      if pat_enable then
        pat_toggle <= not pat_toggle;
        inputs(0) <= pat_toggle;
      end if;
    end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples   => MAX_SAMPLES,
      Channels      => CHANNELS,
      Sim           => true,
      Write_Latency => 1,
      Read_Latency  => 1,
      Page_Latency  => 1
    )
    port map (
      CLK          => clk,
      CLK_150      => clk_150,
      Rate_Div     => rate_div,
      Samples      => samples_s,
      Start_Offset => start_offset,
      Run          => run,
      Full         => full,
      Inputs       => inputs,
      Address      => address,
      Outputs      => outputs,
      sdram_addr   => sdram_addr,
      sdram_ba     => sdram_ba,
      sdram_cas_n  => sdram_cas_n,
      sdram_dq     => sdram_dq,
      sdram_dqm    => sdram_dqm,
      sdram_ras_n  => sdram_ras_n,
      sdram_we_n   => sdram_we_n,
      sdram_cke    => sdram_cke,
      sdram_cs_n   => sdram_cs_n,
      sdram_clk    => sdram_clk,
      Status       => status,
      s_burst      => s_burst,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      FAST_CLK     => '0',
      Continuous_Mode => continuous_mode,
      Buffer_Full     => buffer_full_s,
      Buffer_Ack      => buffer_ack_s
    );

  process
  begin
    report "=== Fast Logic Analyzer tests ===";

    ------------------------------------------------------------------
    -- Test 1: Single-buffer capture, 64 samples, CH0 toggling
    ------------------------------------------------------------------
    report "Test 1: Single-buffer capture 64 samples";
    samples_s <= 64;
    wait_cycles(clk, 10);

    armed <= '1';
    wait_cycles(clk, 10);

    -- Start pattern generator
    pat_enable <= true;
    run <= '1';

    wait_until(clk, full, '1', 5 ms, "Single-buffer capture should complete");
    check(full = '1', "Full asserted");
    pat_enable <= false;

    -- Read back first few samples
    for addr in 0 to 15 loop
      address <= addr;
      wait_cycles(clk, 3);
      report "Addr " & integer'image(addr) & " = " & to_hstring(outputs);
    end loop;

    run <= '0';
    armed <= '0';
    wait_cycles(clk, 50);
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: Continuous triple-buffer mode
    ------------------------------------------------------------------
    report "Test 2: Continuous triple-buffer mode";
    samples_s <= 96;
    continuous_mode <= '1';
    fast_mode <= '0';

    armed <= '1';
    wait_cycles(clk, 10);
    pat_enable <= true;
    run <= '1';

    wait_until(clk, buffer_full_s(0), '1', 10 ms, "Buffer 0 should fill");
    report "Buffer 0 full";
    buffer_ack_s(0) <= '1';
    wait_cycles(clk, 2);
    buffer_ack_s(0) <= '0';

    wait_until(clk, buffer_full_s(1), '1', 10 ms, "Buffer 1 should fill");
    report "Buffer 1 full";
    buffer_ack_s(1) <= '1';
    wait_cycles(clk, 2);
    buffer_ack_s(1) <= '0';

    wait_until(clk, buffer_full_s(2), '1', 10 ms, "Buffer 2 should fill");
    report "Buffer 2 full";
    buffer_ack_s(2) <= '1';
    wait_cycles(clk, 2);
    buffer_ack_s(2) <= '0';

    run <= '0';
    armed <= '0';
    continuous_mode <= '0';
    pat_enable <= false;
    wait_cycles(clk, 50);
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: Fast mode (BRAM only)
    ------------------------------------------------------------------
    report "Test 3: Fast mode (BRAM)";
    samples_s <= 64;
    fast_mode <= '1';
    armed <= '1';
    wait_cycles(clk, 10);

    inputs(0) <= '1';
    run <= '1';
    wait_until(clk, full, '1', 500 us, "Fast mode capture should complete");
    check(full = '1', "Full asserted in fast mode");

    for addr in 0 to 7 loop
      address <= addr;
      wait_cycles(clk, 3);
      report "Fast Addr " & integer'image(addr) & " = " & to_hstring(outputs);
    end loop;

    run <= '0';
    armed <= '0';
    fast_mode <= '0';
    wait_cycles(clk, 50);
    report "Test 3: PASS";

    report "=== ALL FAST ANALYZER TESTS PASSED ===";
    wait;
  end process;

end bench;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_capture_path is
  generic (
    CLK_HALF : time := 5 ns  -- 100 MHz
  );
end tb_capture_path;

architecture bench of tb_capture_path is
  constant CLK_PERIOD : time := CLK_HALF * 2;
  constant CHANNELS   : natural := 8;
  constant TEST_SAMPLES : natural := 16;

  signal clk      : std_logic := '0';
  signal rate_div : natural range 1 to 500000000 := 4;
  signal samples_in : natural range 1 to 3000000 := TEST_SAMPLES;
  signal start_offset : natural range 0 to 3000000 := 0;
  signal run      : std_logic := '0';
  signal full     : std_logic;
  signal inputs   : std_logic_vector(CHANNELS-1 downto 0) := x"A5";
  signal address  : natural range 0 to 3000000 := 0;
  signal outputs  : std_logic_vector(15 downto 0);
  signal armed    : std_logic := '0';
  signal fast_mode : std_logic := '0';
  signal continuous_mode : std_logic := '0';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack  : std_logic_vector(2 downto 0) := (others => '0');

  signal sdram_addr  : std_logic_vector(11 downto 0);
  signal sdram_ba    : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_clk   : std_logic;
  signal status      : std_logic_vector(7 downto 0);
  signal fast_clk    : std_logic := '0';
begin

  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples   => 3000000,
      Channels      => CHANNELS,
      Sim           => true
    )
    port map (
      CLK          => clk,
      CLK_150      => open,
      Rate_Div     => rate_div,
      Samples      => samples_in,
      Start_Offset => start_offset,
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
      Continuous_Mode => continuous_mode,
      Buffer_Full     => buffer_full,
      Buffer_Ack      => buffer_ack
    );

  process
    variable rdata : std_logic_vector(15 downto 0);
  begin
    wait_cycles(clk, 50);

    ------------------------------------------------------------------
    -- Test 1: Capture completion through the public interface
    ------------------------------------------------------------------
    -- Internal-name probes crash the pinned GHDL LLVM backend and duplicate
    -- the divider's focused unit coverage.  This integration bench instead
    -- verifies the observable contract: configured capture completes and all
    -- returned words are known and correctly ordered.
    report "Test 1: capture reaches Full";
    rate_div <= 4;
    samples_in <= TEST_SAMPLES;
    fast_mode <= '1';
    armed <= '1';
    run <= '1';
    wait_cycles(clk, 20);

    -- Run should now be sampled as '1', capture should start
    -- sample_en fires every 4 cycles
    -- Need 2 sample_en events per BRAM write (sub_steps=2)
    -- Need SAMPLES BRAM writes for Full to fire
    -- Total: SAMPLES * sub_steps * rate_div = 16 * 2 * 4 = 128 cycles

    wait until rising_edge(full);
    report "Full asserted at " & integer'image(now / 1 ns) & " ns";
    check(full = '1', "Full should be '1' after capture");
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: Every channel and both packed samples match the input pattern
    ------------------------------------------------------------------
    report "Test 2: Readback every packed sample exactly";
    -- Address starts at zero, so the Sim-only legacy reader may already have
    -- issued its documented cold/prime read for address zero as Full rose.
    -- Move away first; the loop's return to zero then issues a real read.
    address <= 1;
    wait_cycles(clk, 50);
    for addr in 0 to (TEST_SAMPLES / 2) - 1 loop
      address <= addr;
      -- The legacy Sim-only Address/Outputs port performs a real SDRAM read;
      -- allow its controller/refresh pipeline to return the requested word.
      wait_cycles(clk, 50);
      rdata := outputs;
      check(not is_x(rdata), "Outputs must be known at addr " & integer'image(addr));
      check(rdata = x"A5A5", "capture mismatch at addr " & integer'image(addr) &
            ": expected A5A5, got " & to_hstring(rdata));
    end loop;
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: Verify Full goes low when Run falls, then readout at rd_mode
    ------------------------------------------------------------------
    report "Test 3: Run edge handling";
    run <= '0';
    wait_cycles(clk, 10);
    check(full = '0', "Full should clear when Run falls (reset in FLA)");
    report "Test 3: PASS";

    ------------------------------------------------------------------
    -- Test 4: Second capture — data integrity
    ------------------------------------------------------------------
    report "Test 4: Second capture, verify data integrity";
    inputs <= x"3C";
    rate_div <= 4;
    samples_in <= TEST_SAMPLES;
    armed <= '1';
    wait_cycles(clk, 4);
    run <= '1';

    -- Wait for Full
    wait until rising_edge(full);

    for addr in 0 to (TEST_SAMPLES / 2) - 1 loop
      address <= addr;
      wait_cycles(clk, 50);
      rdata := outputs;
      check(rdata = x"3C3C", "second-capture mismatch at addr " & integer'image(addr) &
            ": expected 3C3C, got " & to_hstring(rdata));
    end loop;
    report "Test 4: PASS";

    ------------------------------------------------------------------
    run <= '0';
    wait_cycles(clk, 20);

    report "=== ALL CAPTURE PATH TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;

end bench;

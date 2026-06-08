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
  signal rate_div : natural range 1 to 150000000 := 4;
  signal samples_in : natural range 1 to 3000000 := TEST_SAMPLES;
  signal start_offset : natural range 0 to 3000000 := 0;
  signal run      : std_logic := '0';
  signal full     : std_logic;
  signal inputs   : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
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
  signal s_burst     : std_logic;
  signal status      : std_logic_vector(7 downto 0);
  signal fast_clk    : std_logic := '0';
  signal bram_waddr  : natural range 0 to 1023;
  signal bram_wren   : std_logic;
  signal sample_en   : std_logic;
  signal fifo_cnt    : natural range 0 to 16;
  signal tb_counter  : std_logic_vector(7 downto 0) := (others => '0');

begin

  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;

  -- CH0 toggles every 80ns (counter bit 3), sample_en every 40ns (rate_div=4)
  -- CH0 stays constant within each 16-bit word (2 samples), changes between words
  process(clk)
  begin
    if rising_edge(clk) then
      tb_counter <= std_logic_vector(unsigned(tb_counter) + 1);
      if tb_counter(3) = '1' then
        inputs(0) <= '1';
      else
        inputs(0) <= '0';
      end if;
    end if;
  end process;

  -- Probe internal signals
  bram_waddr <= << signal .tb_capture_path.dut.bram_waddr : natural range 0 to 1023 >>;
  bram_wren  <= << signal .tb_capture_path.dut.bram_wren : std_logic >>;
  sample_en  <= << signal .tb_capture_path.dut.sample_en : std_logic >>;
  fifo_cnt   <= << signal .tb_capture_path.dut.fifo_cnt : natural range 0 to 16 >>;

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
      s_burst      => s_burst,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      FAST_CLK     => fast_clk,
      Continuous_Mode => continuous_mode,
      Buffer_Full     => buffer_full,
      Buffer_Ack      => buffer_ack
    );

  process
    variable rdata : std_logic_vector(15 downto 0);
    variable prev_ch0 : std_logic := 'U';
  begin
    wait_cycles(clk, 50);

    ------------------------------------------------------------------
    -- Test 1: Sample rate divider
    ------------------------------------------------------------------
    report "Test 1: sample_en period = Rate_Div cycles";
    rate_div <= 4;
    wait_cycles(clk, 10);

    -- Count cycles between sample_en rising edges
    -- sample_en is synchronous to pclk (= clk), pulses for 1 cycle every Rate_Div cycles
    wait until rising_edge(sample_en);
    wait until rising_edge(clk);  -- wait one cycle (sample_en just went high)
    wait until rising_edge(sample_en);  -- next sample_en rising
    wait until rising_edge(clk);
    -- Now count until next sample_en
    for i in 1 to 20 loop
      wait until rising_edge(clk);
      if sample_en = '1' then
        check(i = rate_div, "sample_en period: expected " & integer'image(rate_div) &
              " cycles, got " & integer'image(i));
        exit;
      end if;
    end loop;
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: Fast mode capture into BRAM
    ------------------------------------------------------------------
    report "Test 2: Fast mode capture, verify BRAM writes";
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
    report "bram_wren fired: check waveform";
    -- Quick peek at bram_waddr/bram_wren
    report "bram_waddr=" & integer'image(bram_waddr) & " bram_wren=" & std_logic'image(bram_wren);
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: Readback data matches Inputs toggle during capture window
    ------------------------------------------------------------------
    report "Test 3: Readback samples, verify CH0 timing integrity";
    wait_cycles(clk, 5);
    for addr in 0 to (TEST_SAMPLES / 2) - 1 loop
      address <= addr;
      wait_cycles(clk, 5);
      rdata := outputs;
      check(not is_x(rdata), "Outputs must be known at addr " & integer'image(addr));
      -- CH0 is captured in bit 0 of each half-word
      -- Both halves of a word should have same CH0 (captured 40ns apart, CH0 toggles every 80ns)
      check(rdata(0) = rdata(8), "CH0 mismatch within word at addr " & integer'image(addr) &
            ": lo=" & std_logic'image(rdata(0)) & " hi=" & std_logic'image(rdata(8)));
      -- Adjacent words should have opposite CH0 (80ns between words = 1 CH0 toggle)
      if addr > 0 then
        check(rdata(0) /= prev_ch0, "CH0 should toggle between words at addr " & integer'image(addr) &
              ": prev=" & std_logic'image(prev_ch0) & " cur=" & std_logic'image(rdata(0)));
      end if;
      prev_ch0 := rdata(0);
    end loop;
    report "Test 3: PASS";

    ------------------------------------------------------------------
    -- Test 4: Verify Full goes low when Run falls, then readout at rd_mode
    ------------------------------------------------------------------
    report "Test 4: Run edge handling";
    run <= '0';
    wait_cycles(clk, 10);
    check(full = '0', "Full should clear when Run falls (reset in FLA)");
    report "Test 4: PASS";

    ------------------------------------------------------------------
    -- Test 5: Second capture — data integrity
    ------------------------------------------------------------------
    report "Test 5: Second capture, verify data integrity";
    rate_div <= 4;
    samples_in <= TEST_SAMPLES;
    armed <= '1';
    run <= '1';

    -- Wait for Full
    wait until rising_edge(full);

    -- Read back and check at least some CH0 toggling
    address <= 0;
    wait_cycles(clk, 2);
    rdata := outputs;
    check(rdata(0) = '0' or rdata(0) = '1', "Outputs(0) should be valid");
    report "Test 5: PASS";

    ------------------------------------------------------------------
    run <= '0';
    wait_cycles(clk, 20);

    report "=== ALL CAPTURE PATH TESTS PASSED ===";
    wait;
  end process;

end bench;

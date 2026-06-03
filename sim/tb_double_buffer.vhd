library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_double_buffer is
  generic (TEST : string := "tc_single_buffer");
end tb_double_buffer;

architecture sim of tb_double_buffer is
  constant CLK_PERIOD : time := 20.833 ns;  -- 48 MHz
  constant RATE_VAL   : natural := 12;

  signal clk     : std_logic := '0';
  signal running : boolean := true;

  -- FLA signals
  signal rate_div     : natural range 1 to 12000000 := RATE_VAL;
  signal samples      : natural range 1 to 1048576 := 128;
  signal start_offset : natural range 0 to 1048576 := 0;
  signal run_f        : std_logic := '0';
  signal full         : std_logic;
  signal inputs       : std_logic_vector(7 downto 0) := (others => '0');
  signal address      : natural range 0 to 1048576 := 0;
  signal outputs      : std_logic_vector(15 downto 0);
  signal armed        : std_logic := '0';
  signal fast_mode    : std_logic := '0';
  signal fast_clk     : std_logic := '0';
  signal status       : std_logic_vector(7 downto 0);
  signal cont_mode    : std_logic := '0';
  signal buf_full     : std_logic_vector(2 downto 0) := (others => '0');
  signal buf_ack      : std_logic_vector(2 downto 0) := (others => '0');

  -- Debug signal (probed via VHDL-2008 external name)
  signal sample_en   : std_logic;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  FLA : entity work.Fast_Logic_Analyzer_SDRAM(rtl)
    generic map (Max_Samples => 1048576, Channels => 8, Sim => true)
    port map (
      CLK => clk, CLK_150 => open,
      Rate_Div => rate_div, Samples => samples,
      Start_Offset => start_offset, Run => run_f,
      Full => full, Inputs => inputs(7 downto 0),
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

  -- Probe internal sample_en using VHDL-2008 external name
  sample_en <= <<signal .tb_double_buffer.FLA.sample_en : std_logic>>;

  -----------------------------------------------------------
  -- TEST SEQUENCER
  -----------------------------------------------------------
  process
    type slv_array is array (natural range <>) of std_logic;
    variable ch0_vals : slv_array(0 to 8191) := (others => '0');
    variable edges    : natural := 0;
    variable prev     : std_logic := '0';
    variable spacing  : natural := 0;
    variable max_spc  : natural := 0;
    variable min_spc  : natural := 1000;
    variable gap_cnt  : natural := 0;
    variable expected_spc : natural := 0;
  begin
    wait for 1 us;

    -- ============================================================
    -- tc_single_buffer: Baseline single buffer capture
    -- ============================================================
    if TEST = "all" or TEST = "tc_single_buffer" then
      report "--- tc_single_buffer: Baseline single buffer ---" severity note;

      samples <= 64;  -- 64 sub-samples / sub_steps(2) = 32 words
      run_f <= '1';
      wait until full = '1' for 100 us;
      assert full = '1' report "tc_single_buffer: Full not asserted" severity failure;
      report "tc_single_buffer: Full asserted" severity note;
      run_f <= '0';
      wait for 500 ns;

      -- Warmup (prime SDRAM read pipeline with address 127), then read back
      address <= 127; wait for CLK_PERIOD * 20;
      for i in 0 to 31 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
      end loop;

      edges := 0;
      for i in 1 to 31 loop
        if ch0_vals(i) /= ch0_vals(i-1) then
          edges := edges + 1;
        end if;
      end loop;
      report "tc_single_buffer: Read " & integer'image(32) & " words, " &
             integer'image(edges) & " edges (expected 0)" severity note;
      assert edges <= 1 report "tc_single_buffer: Unexpected transitions (" & integer'image(edges) & ")" severity failure;
      report "tc_single_buffer: PASS (" & integer'image(edges) & " edges, <=1 allowed)" severity note;
    end if;

    -- ============================================================
    -- tc_data_integrity: Verify captured data matches input pattern
    -- ============================================================
    if TEST = "all" or TEST = "tc_data_integrity" then
      report "--- tc_data_integrity: Verify data integrity ---" severity note;

      cont_mode <= '0';  -- single-buffer
      samples <= 64;     -- 32 words (sub_steps=2)
      rate_div <= 1;     -- sample every 2 cycles
      run_f <= '1';

      -- Drive known pattern: sample i has CH0 = i mod 2 (toggle every sample)
      -- With Channels=8, sub_steps=2: each word = 16 bits (2 × 8-bit samples)
      for i in 0 to 63 loop
        inputs <= (others => '0');
        if i mod 2 = 0 then
          inputs(0) <= '1';  -- first sample of pair
        else
          inputs(0) <= '0';  -- second sample of pair
        end if;
        wait for CLK_PERIOD * 2;
      end loop;

      wait until full = '1' for 200 us;
      assert full = '1' report "tc_data_integrity: Full not asserted" severity failure;

      run_f <= '0';
      wait for 500 ns;

      -- Read back all 32 words (address walks, sub-step alternates)
      address <= 31; wait for CLK_PERIOD * 20;
      for i in 0 to 31 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
      end loop;

      -- Verify: even addresses read step 0 (CH0='1'), odd read step 1 (CH0='0')
      report "tc_data_integrity: Read " & integer'image(32) & " words" severity note;
      for i in 0 to 31 loop
        report "  word " & integer'image(i) & " CH0=" & std_logic'image(ch0_vals(i)) severity note;
        if i mod 2 = 0 then
          assert ch0_vals(i) = '1'
            report "tc_data_integrity: Word " & integer'image(i) & " CH0='0' expected '1' - DATA CORRUPTED!" severity failure;
        else
          assert ch0_vals(i) = '0'
            report "tc_data_integrity: Word " & integer'image(i) & " CH0='1' expected '0' - DATA CORRUPTED!" severity failure;
        end if;
      end loop;

      report "tc_data_integrity: PASS (32 words verified)" severity note;
    end if;

    -- ============================================================
    -- tc_signal_path: Verify capture sees toggling signals (like test_out)
    -- Uses SDRAM path (proven working) with known input pattern.
    -- Drives CH0 with alternating 1/0 pattern, captures, reads back,
    -- and verifies every word matches expectations.
    -- ============================================================
    if TEST = "all" or TEST = "tc_signal_path" then
      report "--- tc_signal_path: Verify signal capture integrity ---" severity note;

      cont_mode <= '0';  -- single-buffer (SDRAM path)
      samples <= 128;    -- 64 words (sub_steps=2)
      rate_div <= 1;     -- sample every 2 cycles
      run_f <= '1';

      -- Drive known pattern: CH0 = 1,0,1,0,... CH1-CH7 = steady 1 (pull-ups)
      for i in 0 to 127 loop
        inputs <= (others => '1');
        if i mod 2 = 0 then
          inputs(0) <= '1';
        else
          inputs(0) <= '0';
        end if;
        wait for CLK_PERIOD * 2;  -- rate_div+1 = 2
      end loop;

      wait until full = '1' for 200 us;
      assert full = '1' report "tc_signal_path: Full not asserted" severity failure;

      run_f <= '0';
      wait for 500 ns;

      -- Read back all 64 words
      address <= 63; wait for CLK_PERIOD * 20;
      for i in 0 to 63 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
      end loop;

      -- Count CH0 transitions
      edges := 0;
      for i in 1 to 63 loop
        if ch0_vals(i) /= ch0_vals(i-1) then
          edges := edges + 1;
        end if;
      end loop;
      report "tc_signal_path: " & integer'image(64) & " words read, " &
             integer'image(edges) & " CH0 transitions" severity note;

      -- With alternating input: even words (step0) see CH0='1', odd (step1) see CH0='0'
      -- So words should alternate 1,0,1,0,... giving 63 transitions
      -- Unless the address bit 0 selects the sub-step, then even addr = step0 = '1',
      -- odd addr = step1 = '0'
      for i in 0 to 63 loop
        if i mod 2 = 0 then
          assert ch0_vals(i) = '1'
            report "tc_signal_path: Word " & integer'image(i) & " CH0='0' expected '1'" severity failure;
        else
          assert ch0_vals(i) = '0'
            report "tc_signal_path: Word " & integer'image(i) & " CH0='1' expected '0'" severity failure;
        end if;
      end loop;

      -- Verify non-CH0 bits are always 1 (pull-up)
      for i in 0 to 7 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        for c in 1 to 7 loop
          assert outputs(c) = '1'
            report "tc_signal_path: CH" & integer'image(c) & " word " & integer'image(i) &
                   " = " & std_logic'image(outputs(c)) & " expected '1'" severity failure;
        end loop;
      end loop;

      report "tc_signal_path: PASS (" & integer'image(edges) & " transitions)" severity note;
    end if;

    if TEST = "all" then
      report "ALL TESTS: PASS" severity note;
    end if;

    running <= false;
    wait;
  end process;

end sim;

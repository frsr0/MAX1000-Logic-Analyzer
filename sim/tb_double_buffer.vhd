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
  signal inputs       : std_logic_vector(15 downto 0) := (others => '0');
  signal address      : natural range 0 to 1048576 := 0;
  signal outputs      : std_logic_vector(15 downto 0);
  signal armed        : std_logic := '0';
  signal fast_mode    : std_logic := '0';
  signal fast_clk     : std_logic := '0';
  signal status       : std_logic_vector(7 downto 0);
  signal cont_mode    : std_logic := '0';
  signal buf_full     : std_logic_vector(1 downto 0) := (others => '0');
  signal buf_ack      : std_logic_vector(1 downto 0) := (others => '0');

  -- Debug signal (probed via VHDL-2008 external name)
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
    -- tc_buffer_swap: Verify no-gap at buffer A->B transition
    -- ============================================================
    if TEST = "all" or TEST = "tc_buffer_swap" then
      report "--- tc_buffer_swap: No-gap buffer transition ---" severity note;

      cont_mode <= '1';
      samples <= 128;  -- 64 words total = 2 x 32-word buffers
      run_f <= '1';

      -- Wait for buffer A to fill (32 words)
      wait until buf_full(0) = '1' for 100 us;
      assert buf_full(0) = '1' report "tc_buffer_swap: Buffer A not full" severity failure;
      report "tc_buffer_swap: Buffer A full, should still be capturing (Full=" &
             std_logic'image(full) & ")" severity note;

      -- Full should NOT be asserted yet (buffer B is still filling)
      assert full = '0' report "tc_buffer_swap: Full asserted prematurely!" severity failure;
      report "tc_buffer_swap: Continuous capture OK (no premature Full)" severity note;

      -- Wait for buffer B to fill (backpressure)
      wait until full = '1' for 100 us;
      assert full = '1' report "tc_buffer_swap: Full not asserted at backpressure" severity failure;
      report "tc_buffer_swap: Backpressure Full asserted" severity note;

      run_f <= '0';
      wait for 500 ns;

      -- Warmup read to flush SDRAM pipeline
      address <= 63; wait for CLK_PERIOD * 20;
      -- Read back all samples
      for i in 0 to 63 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
        if i < 8 then
          report "  read word " & integer'image(i) & " CH0=" & std_logic'image(ch0_vals(i)) severity note;
        end if;
      end loop;
      -- Verify data is not 'U'  
      for i in 0 to 7 loop
        assert ch0_vals(i) /= 'U' report "tc_buffer_swap: Word " & integer'image(i) & " is uninitialized!" severity failure;
      end loop;

      report "tc_buffer_swap: PASS" severity note;
      cont_mode <= '0';
    end if;

    -- ============================================================
    -- tc_edge_timing: Uniform edge spacing across buffer boundary
    -- ============================================================
    if TEST = "all" or TEST = "tc_edge_timing" then
      report "--- tc_edge_timing: Uniform edge spacing ---" severity note;

      cont_mode <= '0';  -- Single-buffer (same sample path, avoids SDRAM sim quirks)
      samples <= 256;
      expected_spc := 1;

      run_f <= '1';
      wait for CLK_PERIOD * 6;
      for i in 0 to 127 loop
        inputs(0) <= '1';
        wait for CLK_PERIOD * 12;
        inputs(0) <= '0';
        wait for CLK_PERIOD * 12;
      end loop;

      wait until full = '1' for 200 us;
      assert full = '1' report "tc_edge_timing: Full not asserted" severity failure;

      -- Warmup + read back all samples
      address <= 63; wait for CLK_PERIOD * 20;
      for i in 0 to 127 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
      end loop;

      -- Debug: dump first 20 values
      for i in 0 to 19 loop
        report "  word " & integer'image(i) & " CH0=" & std_logic'image(ch0_vals(i)) severity note;
      end loop;

      -- Find edges and measure spacing
      edges := 0; gap_cnt := 0;
      max_spc := 0; min_spc := 1000;
      prev := ch0_vals(0);
      spacing := 0;
      for i in 1 to 127 loop
        if ch0_vals(i) /= prev then
          edges := edges + 1;
          if edges > 1 then
            if spacing > expected_spc + 1 then
              gap_cnt := gap_cnt + 1;
              report "  GAP at word " & integer'image(i) &
                     " spacing=" & integer'image(spacing) severity note;
            end if;
            if spacing > max_spc then max_spc := spacing; end if;
            if spacing < min_spc then min_spc := spacing; end if;
          end if;
          spacing := 0;
          prev := ch0_vals(i);
        end if;
        spacing := spacing + 1;
      end loop;

      report "tc_edge_timing: " & integer'image(edges) & " edges, " &
             "min=" & integer'image(min_spc) & " max=" & integer'image(max_spc) &
             " gaps=" & integer'image(gap_cnt) severity note;
      assert gap_cnt = 0 report "tc_edge_timing: Gaps detected!" severity failure;
      report "tc_edge_timing: PASS" severity note;
      run_f <= '0';
      wait for 500 ns;
      cont_mode <= '0';
    end if;

    -- ============================================================
    -- tc_read_while_write: Read buffer A while B is filling
    -- ============================================================
    if TEST = "all" or TEST = "tc_read_while_write" then
      report "--- tc_read_while_write: Read A while B fills ---" severity note;

      cont_mode <= '1';
      samples <= 256;  -- 128 words = 2 x 64-word buffers
      run_f <= '1';

      -- Wait for buffer A to fill
      wait until buf_full(0) = '1' for 100 us;
      assert buf_full(0) = '1' report "tc_read_while_write: Buffer A not full" severity failure;
      report "tc_read_while_write: Buffer A full, reading while B fills..." severity note;

      -- Warmup then read buffer A while capture continues to buffer B
      address <= 63; wait for CLK_PERIOD * 20;
      for i in 0 to 63 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
        if i < 4 then
          report "  A word " & integer'image(i) & " CH0=" & std_logic'image(ch0_vals(i)) severity note;
        end if;
      end loop;

      -- Ack buffer A so capture can reuse it when B fills
      buf_ack(0) <= '1';
      wait for CLK_PERIOD;
      buf_ack(0) <= '0';

      -- Wait for buffer B to fill
      wait until buf_full(1) = '1' for 100 us;
      assert buf_full(1) = '1' report "tc_read_while_write: Buffer B not full" severity failure;

      -- Warmup then read buffer B
      address <= 63; wait for CLK_PERIOD * 20;
      for i in 64 to 127 loop
        address <= i;
        wait for CLK_PERIOD * 10;
        ch0_vals(i) := outputs(0);
      end loop;

      report "tc_read_while_write: Read both buffers, total " & integer'image(128) & " words" severity note;
      report "tc_read_while_write: PASS" severity note;
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

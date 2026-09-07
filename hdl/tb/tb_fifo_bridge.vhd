-- End-to-end FIFO bridge regression. Separate 200 MHz producer and 166.67 MHz
-- pump clocks exercise CDC ordering, overflow recovery, and packed backpressure.
-- Every check uses public completion/counter/readback ports; no private aliases.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_fifo_bridge is
end tb_fifo_bridge;

architecture bench of tb_fifo_bridge is
  constant FAST_PERIOD : time := 5 ns;
  constant PCLK_PERIOD : time := 6 ns;

  signal fast_clk, pclk : std_logic := '0';
  signal run             : std_logic := '0';
  signal full            : std_logic;
  signal inputs          : std_logic_vector(15 downto 0) := (others => '0');
  signal address         : natural range 0 to 3000000 := 0;
  signal outputs         : std_logic_vector(15 downto 0);
  signal status          : std_logic_vector(7 downto 0);
  signal rate_div        : natural range 1 to 500000000 := 20;
  signal samples_cfg     : natural range 1 to 3000000 := 256;

  signal packed_mode     : std_logic := '0';
  signal packed_data     : std_logic_vector(15 downto 0) := (others => '0');
  signal packed_valid    : std_logic := '0';
  signal packed_ready    : std_logic;
  signal packed_accepted : integer := 0;
  signal producer_index  : std_logic_vector(31 downto 0);
  signal overflow_count  : std_logic_vector(31 downto 0);
  signal pump_valid_count, pump_accept_count, pump_nodata_count : std_logic_vector(31 downto 0);

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n : std_logic;
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal sdram_ras_n, sdram_we_n, sdram_clk : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
begin
  fast_clk <= not fast_clk after FAST_PERIOD / 2;
  pclk     <= not pclk after PCLK_PERIOD / 2;

  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      inputs <= std_logic_vector(unsigned(inputs) + 1);
    end if;
  end process;

  dut : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples => 3000000, Channels => 16, Sim => true,
      FAST_SPEED => true, FAST_RAW_BUILD => false,
      CLK_Frequency => 166666667, SDRAM_CLK_HZ => 166666667,
      SAMPLE_CLK_HZ => 200000000, Enable_Pump_Metrics => true
    )
    port map (
      CLK => pclk, SDRAM_CLK_IN => pclk, CLK_150 => open,
      Rate_Div => rate_div, Samples => samples_cfg, Start_Offset => 0,
      Run => run, Full => full, Inputs => inputs,
      Address => address, Outputs => outputs,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba,
      sdram_cas_n => sdram_cas_n, sdram_cke => sdram_cke,
      sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
      sdram_we_n => sdram_we_n, sdram_clk => sdram_clk,
      Status => status, Armed => '1', Fast_Mode => '1', FAST_CLK => fast_clk,
      Continuous_Mode => '0', Packed_Mode => packed_mode,
      Packed_Data => packed_data, Packed_Valid => packed_valid,
      Packed_Ready => packed_ready, Producer_Index => producer_index,
      Pump_Overflow_Count => overflow_count,
      Pump_Valid_Cycles => pump_valid_count,
      Pump_Accept_Cycles => pump_accept_count,
      Pump_NoData_Cycles => pump_nodata_count
    );

  -- Increment only on a real packed valid/ready transfer. Throttling keeps the
  -- producer below pump capacity and makes any duplicate/loss unambiguous.
  process(fast_clk)
    variable next_word : unsigned(15 downto 0) := x"0001";
    variable throttle  : natural range 0 to 3 := 0;
  begin
    if rising_edge(fast_clk) then
      if packed_mode = '1' then
        if packed_valid = '1' and packed_ready = '1' then
          packed_accepted <= packed_accepted + 1;
          next_word := next_word + 1;
          packed_valid <= '0';
        elsif throttle = 0 then
          packed_valid <= '1';
        end if;
        throttle := (throttle + 1) mod 4;
        packed_data <= std_logic_vector(next_word);
      else
        packed_valid <= '0';
        packed_accepted <= 0;
        next_word := x"0001";
        throttle := 0;
      end if;
    end if;
  end process;

  main : process
    procedure wait_pclk(constant n : natural) is
    begin
      for i in 1 to n loop wait until rising_edge(pclk); end loop;
    end procedure;

    procedure verify_readback(
      constant count : natural; constant expected_step : natural;
      constant require_first : boolean := false;
      constant first_expected : natural := 0) is
      variable prev_v, cur_v, step_v : integer := 0;
    begin
      assert count > 0 report "readback count must be non-zero" severity failure;
      -- Address powers up at zero; force an edge so address zero is requested.
      address <= count;
      wait_pclk(24);
      for i in 0 to count - 1 loop
        address <= i;
        wait_pclk(24);
        wait for 1 ps;
        assert not is_x(outputs)
          report "unknown public readback at address " & integer'image(i)
          severity failure;
        cur_v := to_integer(unsigned(outputs));
        if i = 0 then
          if require_first then
            assert cur_v = first_expected
              report "first readback word=" & integer'image(cur_v) &
                     ", expected " & integer'image(first_expected)
              severity failure;
          end if;
        else
          step_v := (cur_v - prev_v) mod 65536;
          assert step_v = expected_step
            report "readback step at address " & integer'image(i) & " is " &
                   integer'image(step_v) & ", expected " & integer'image(expected_step)
            severity failure;
        end if;
        prev_v := cur_v;
      end loop;
    end procedure;

    procedure divided_capture(
      constant div : natural; constant count : natural;
      constant label_s : string) is
      variable committed : natural;
    begin
      rate_div <= div;
      samples_cfg <= count;
      wait for 100 ns;
      run <= '1';
      wait until rising_edge(full) for 1 ms;
      assert full = '1'
        report label_s & ": capture did not complete" severity failure;
      run <= '0';
      wait for 500 ns;
      committed := to_integer(unsigned(producer_index));
      assert committed = count
        report label_s & ": committed " & integer'image(committed) &
               ", expected " & integer'image(count)
        severity failure;
      assert unsigned(overflow_count) = 0
        report label_s & ": sustainable capture overflowed" severity failure;
      verify_readback(count, div);
      report label_s & " PASS: exact count and ordered public readback";
    end procedure;

    variable committed : natural;
  begin
    wait for 200 ns;

    report "TEST 1: divided-rate FIFO fall-through ordering";
    divided_capture(20, 256, "TEST 1");

    report "TEST 2: full-rate overflow preserves a contiguous prefix";
    rate_div <= 1;
    samples_cfg <= 8192;
    wait for 100 ns;
    run <= '1';
    wait until rising_edge(full) for 1 ms;
    assert full = '1' report "TEST 2: overflow did not terminate capture" severity failure;
    run <= '0';
    wait for 500 ns;
    committed := to_integer(unsigned(producer_index));
    report "TEST 2 committed=" & integer'image(committed) &
           " overflow_count=" & integer'image(to_integer(unsigned(overflow_count))) &
           " pump_valid=" & integer'image(to_integer(unsigned(pump_valid_count))) &
           " pump_accept=" & integer'image(to_integer(unsigned(pump_accept_count))) &
           " pump_nodata=" & integer'image(to_integer(unsigned(pump_nodata_count)));
    assert committed >= 10 report "TEST 2: too few words committed" severity failure;
    assert unsigned(overflow_count) > 0
      report "TEST 2: full-rate run did not report overflow" severity failure;
    verify_readback(committed, 1);
    report "TEST 2 PASS: overflowed prefix is contiguous";

    report "TEST 3: next capture drains all stale post-overflow data";
    divided_capture(20, 256, "TEST 3");

    report "TEST 4: packed valid/ready stream commits exactly once";
    packed_mode <= '1';
    rate_div <= 8;
    samples_cfg <= 4096;
    wait for 100 ns;
    run <= '1';
    wait until rising_edge(full) for 1 ms;
    report "TEST 4 timeout state: full=" & std_logic'image(full) &
           " accepted=" & integer'image(packed_accepted) &
           " producer_index=" & integer'image(to_integer(unsigned(producer_index))) &
           " status=" & to_hstring(status);
    assert full = '1' report "TEST 4: packed capture did not complete" severity failure;
    run <= '0';
    wait for 500 ns;
    committed := to_integer(unsigned(producer_index));
    assert packed_accepted > 100 report "TEST 4: Packed_Ready accepted too few words" severity failure;
    assert committed > 100 report "TEST 4: pump committed too few packed words" severity failure;
    assert committed <= packed_accepted
      report "TEST 4: committed more words than producer accepted" severity failure;
    assert unsigned(overflow_count) = 0 report "TEST 4: packed capture overflowed" severity failure;
    verify_readback(committed, 1, true, 1);
    packed_mode <= '0';
    report "TEST 4 PASS: packed stream starts at 1 with no gaps or duplicates";

    report "=== FIFO bridge tests complete: ALL PASS ===";
    std.env.finish;
    wait;
  end process;
end bench;

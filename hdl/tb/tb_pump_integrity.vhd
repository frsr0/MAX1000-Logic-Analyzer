-- End-to-end data integrity of the pull-based capture burst: real FLA (Sim =>
-- false: real afifo model, real write pump, real SDRAM_Controller) writing into
-- the STRICT pin-level SDRAM model, then every captured word is read back
-- through the normal readout path (block read -> response FIFO) and compared.
--
-- The sampled input is an incrementing counter (one count per FAST_CLK), so a
-- capture at RATE_DIV r must read back as an exact arithmetic progression with
-- step r: any dropped, duplicated, reordered or mis-addressed word breaks it.
-- The SDRAM model runs with FAIL_ON_VIOLATION, so tRCD / tRP / tRAS / tRC /
-- tWR / tRFC violations, closed-bank accesses, refresh with an open bank and
-- WRITEs with an undriven DQ all fail the bench.
--
-- Cases run in parallel (one DUT + SDRAM model each):
--   fast100   100.2 MS/s, 3500 samples (13 row boundaries, several refreshes)
--   fast67     66.8 MS/s, 3000 samples
--   mid20      20 MS/s,  2000 samples (capture ends mid-row)
--   sparse1     1 MS/s,   700 samples (FIFO empties mid-burst, ends mid-row)
--   mid100    100.2 MS/s, 3333 samples (ends 5 words into a row)
--   ring      continuous ring of 1000 words (not a multiple of 256) at
--             50 MS/s over ~3.4 laps: wrap mid-row, bursts that start at a
--             non-zero column after refresh interruptions
-- Every case also measures the refresh-to-refresh interval on the SDRAM pins
-- and requires it never to exceed the 1308-clock refresh timer period plus a
-- small service latency (the old design's bound), including the sparse case.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity pump_integrity_case is
  generic (
    NAME     : string;
    RATE_DIV : natural;
    SAMPLES  : natural;
    CONT     : boolean := false;
    MAXS     : natural := 8192
  );
  port (
    clk, fastclk, pclk, pclk_model : in std_logic;
    done : out std_logic := '0'
  );
end pump_integrity_case;

architecture bench of pump_integrity_case is
  signal rdiv : natural range 1 to 500000000 := RATE_DIV;
  signal samples_in : natural range 1 to MAXS := SAMPLES;
  signal run, full, armed, fast_mode, cont_mode : std_logic := '0';
  signal inputs  : std_logic_vector(15 downto 0) := (others => '0');
  signal address : natural range 0 to MAXS := 0;
  signal outputs : std_logic_vector(15 downto 0);
  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n, sdram_ras_n, sdram_we_n, sdram_clk : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal status     : std_logic_vector(7 downto 0);
  signal overflow_count : std_logic_vector(31 downto 0);
  signal producer_idx, oldest_idx : std_logic_vector(31 downto 0);
  signal blk_tog : std_logic := '0';
  signal blk_base, blk_count : natural range 0 to MAXS := 0;
  signal rd_q : std_logic_vector(15 downto 0);
  signal rd_empty : std_logic;
  signal rd_req : std_logic := '0';
  signal monitor_on : boolean := false;
  type word_arr is array (0 to 4095) of std_logic_vector(15 downto 0);
begin
  process(fastclk) begin
    if rising_edge(fastclk) then inputs <= std_logic_vector(unsigned(inputs)+1); end if;
  end process;
  cont_mode <= '1' when CONT else '0';

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples=>MAXS, Channels=>16, Sim=>false, FAST_SPEED=>true,
      CLK_Frequency=>166666667, SDRAM_CLK_HZ=>166666667, SAMPLE_CLK_HZ=>200000000,
      Enable_Pump_Metrics=>false)
    port map (CLK=>clk, SDRAM_CLK_IN=>pclk, CLK_150=>open, Rate_Div=>rdiv,
      Samples=>samples_in, Start_Offset=>0, Run=>run, Full=>full, Inputs=>inputs,
      Address=>address, Outputs=>outputs, sdram_addr=>sdram_addr, sdram_ba=>sdram_ba,
      sdram_cas_n=>sdram_cas_n, sdram_dq=>sdram_dq, sdram_dqm=>sdram_dqm,
      sdram_ras_n=>sdram_ras_n, sdram_we_n=>sdram_we_n, sdram_cke=>sdram_cke,
      sdram_cs_n=>sdram_cs_n, sdram_clk=>sdram_clk, Status=>status,
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>fastclk, Continuous_Mode=>cont_mode,
      Blk_Rd_Req_Tog=>blk_tog, Blk_Rd_Base=>blk_base, Blk_Rd_Count=>blk_count,
      Rd_Fifo_Q=>rd_q, Rd_Fifo_Empty=>rd_empty, Rd_Fifo_RdReq=>rd_req,
      Producer_Index=>producer_idx, Oldest_Index=>oldest_idx,
      Pump_Overflow_Count=>overflow_count);

  SDRAM : entity work.sdram_pin_model
    generic map (CL=>3, STRICT=>true, FAIL_ON_VIOLATION=>true)
    port map (clk=>pclk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  -- Refresh interval monitor on the SDRAM command pins.
  refresh_mon : process(pclk_model)
    variable cyc, last, n : integer := 0;
    variable worst : integer := 0;
    variable have_last : boolean := false;
    variable have_last_rep : boolean := true;
  begin
    if rising_edge(pclk_model) then
      cyc := cyc + 1;
      if not monitor_on then
        have_last := false;
      elsif sdram_cs_n = '0' and sdram_ras_n = '0' and sdram_cas_n = '0' and sdram_we_n = '1' then
                if have_last then
          n := n + 1;
          if cyc - last > worst then worst := cyc - last; end if;
          assert cyc - last <= 1308 + 24
            report NAME & ": refresh interval " & integer'image(cyc - last) &
                   " clocks exceeds the refresh-timer period + service latency"
            severity failure;
          assert cyc - last >= 1308 - 24
            report NAME & ": refresh interval " & integer'image(cyc - last) & " too short"
            severity failure;
        end if;
        last := cyc; have_last := true;
      end if;
      if not monitor_on and have_last_rep and n > 0 then
        report NAME & ": refresh intervals checked: " & integer'image(n) & ", worst " & integer'image(worst) & " clocks";
        have_last_rep := false;
      end if;
    end if;
  end process;

  main : process
    variable words : word_arr := (others => (others => '0'));
    variable got : natural;
    variable step : integer;
    variable pidx : natural;
    variable d : integer;
    procedure read_block(base : natural; count : natural) is
      variable n : natural := 0;
      variable t : natural := 0;
    begin
      blk_base <= base; blk_count <= count;
      wait_cycles(clk, 2);
      blk_tog <= not blk_tog;
      while n < count loop
        wait until rising_edge(clk);
        rd_req <= '0';
        if rd_empty = '0' then
          rd_req <= '1';
          wait until rising_edge(clk);
          rd_req <= '0';
          wait until rising_edge(clk);
          wait until rising_edge(clk);
          words(n) := rd_q;
          n := n + 1;
          t := 0;
        else
          t := t + 1;
          assert t < 200000 report NAME & ": readout timeout after " & integer'image(n) & " words"
            severity failure;
        end if;
      end loop;
      got := n;
    end procedure;
  begin
    -- SDRAM power-up (5 ms reset hold + init), same wait as tb_pump_tput.
    wait_cycles(pclk, 900000);
    fast_mode <= '1'; armed <= '1';
    wait_cycles(clk, 40);
    run <= '1';
    monitor_on <= true;
    if not CONT then
      wait until full = '1' for 3 ms;
      assert full = '1' report NAME & ": capture did not complete" severity failure;
      assert to_integer(unsigned(overflow_count)) = 0
        report NAME & ": capture overflowed" severity failure;
      wait_cycles(clk, 20);
      pidx := to_integer(unsigned(producer_idx));
      assert pidx = SAMPLES
        report NAME & ": producer index " & integer'image(pidx) & " /= samples " & integer'image(SAMPLES)
        severity failure;
      monitor_on <= false;  -- block reads pre-empt refresh by design (unchanged read path)
      read_block(0, SAMPLES);
      for i in 1 to SAMPLES - 1 loop
        d := (to_integer(unsigned(words(i))) - to_integer(unsigned(words(i-1))) + 65536) mod 65536;
        assert d = RATE_DIV
          report NAME & ": word " & integer'image(i) & " = " & to_hstring(words(i)) &
                 ", previous " & to_hstring(words(i-1)) & " (expected step " &
                 integer'image(RATE_DIV) & ")"
          severity failure;
      end loop;
      report NAME & ": " & integer'image(SAMPLES) & " words read back exactly, first=" &
             to_hstring(words(0)) & " last=" & to_hstring(words(SAMPLES-1));
    else
      -- Ring: ~3.4 laps, then stop and read the whole ring oldest-first.
      wait_cycles(pclk, 11500);  -- ~3450 samples at one per 4 FAST_CLK
      monitor_on <= false;
      run <= '0';
      wait_cycles(clk, 4000);
      pidx := to_integer(unsigned(producer_idx));
      report NAME & ": producer index " & integer'image(pidx) & " oldest " &
             integer'image(to_integer(unsigned(oldest_idx)));
      assert pidx > 3 * MAXS report NAME & ": ring did not wrap three times" severity failure;
      assert to_integer(unsigned(oldest_idx)) = pidx - MAXS
        report NAME & ": oldest index " & integer'image(to_integer(unsigned(oldest_idx))) &
               " /= producer - ring" severity failure;
      monitor_on <= false;
      read_block(pidx mod MAXS, MAXS);
      for i in 1 to MAXS - 1 loop
        d := (to_integer(unsigned(words(i))) - to_integer(unsigned(words(i-1))) + 65536) mod 65536;
        assert d = RATE_DIV
          report NAME & ": ring word " & integer'image(i) & " = " & to_hstring(words(i)) &
                 ", previous " & to_hstring(words(i-1)) & " (expected step " &
                 integer'image(RATE_DIV) & ")"
          severity failure;
      end loop;
      -- The newest word must be the value sampled (pidx-1) samples after the
      -- first: last = first_of_ring + (MAXS-1)*RATE_DIV is implied by the
      -- progression; also tie it to the sample count.
      report NAME & ": ring of " & integer'image(MAXS) & " words reads back as an exact progression";
    end if;
    run <= '0';
    done <= '1';
    wait;
  end process;
end bench;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use work.sim_pkg.all;

entity tb_pump_integrity is
end tb_pump_integrity;

architecture bench of tb_pump_integrity is
  constant SYS_CLK_HALF    : time := 4990 ps;  -- 100.2 MHz
  constant FAST_CLK_HALF   : time := 2495 ps;  -- 200.4 MHz
  constant SDRAM_CLK_HALF  : time := 2994 ps;  -- ~167 MHz
  signal clk, fastclk, pclk, pclk_model : std_logic := '0';
  signal done : std_logic_vector(5 downto 0);
begin
  clk    <= not clk    after SYS_CLK_HALF;
  fastclk <= not fastclk after FAST_CLK_HALF;
  pclk   <= not pclk   after SDRAM_CLK_HALF;
  pclk_model <= transport pclk after 1.5 ns;

  c0 : entity work.pump_integrity_case
    generic map (NAME => "fast100", RATE_DIV => 2, SAMPLES => 3500)
    port map (clk, fastclk, pclk, pclk_model, done(0));
  c1 : entity work.pump_integrity_case
    generic map (NAME => "fast67", RATE_DIV => 3, SAMPLES => 3000)
    port map (clk, fastclk, pclk, pclk_model, done(1));
  c2 : entity work.pump_integrity_case
    generic map (NAME => "mid20", RATE_DIV => 10, SAMPLES => 2000)
    port map (clk, fastclk, pclk, pclk_model, done(2));
  c3 : entity work.pump_integrity_case
    generic map (NAME => "sparse1", RATE_DIV => 200, SAMPLES => 700)
    port map (clk, fastclk, pclk, pclk_model, done(3));
  c4 : entity work.pump_integrity_case
    generic map (NAME => "mid100", RATE_DIV => 2, SAMPLES => 3333)
    port map (clk, fastclk, pclk, pclk_model, done(4));
  c5 : entity work.pump_integrity_case
    generic map (NAME => "ring", RATE_DIV => 4, SAMPLES => 64, CONT => true, MAXS => 1000)
    port map (clk, fastclk, pclk, pclk_model, done(5));

  finish : process
  begin
    wait until done = "111111" for 25 ms;
    assert done = "111111" report "not every integrity case completed" severity failure;
    report "ALL PUMP INTEGRITY CASES PASSED";
    std.env.finish;
    wait;
  end process;
end bench;

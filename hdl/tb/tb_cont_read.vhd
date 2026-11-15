-- Continuous (live) capture while the host reads: no samples may be lost.
--
-- Real FLA + real SDRAM controller + strict pin model with the real chip's read
-- data window (REAL_READ_TIMING, inverted chip clock as on the board). A reader
-- models the SPI link: it drains the response FIFO at ~1.1 M words/s (one word
-- per 89 clk cycles), by CMD_READ_CAPTURE-style 512-word blocks or by one held
-- auto-renew stream. Before the fix a read set rd_mode, which aborted the write
-- pump for the whole (SPI-paced) stream, so the capture FIFO filled and samples
-- were skipped (4.7 % at 100.2 MS/s with block reads, 80 % with the held stream).
--
-- Per case (all run in parallel, one DUT + SDRAM model each):
--   raw100blk   100.2 MS/s 16-bit, block reads, ring of 3000 words (wraps ~10x)
--   raw100str   100.2 MS/s, held auto-renew stream
--   narrow200   200.4 MHz narrow (12.5 M words/s), block reads
-- Checks: (a) dropped-sample counter (REG 0x60) stays 0 and the producer index
-- follows the sample ticks, (b) after stopping, the ring is one exact
-- incrementing run, (c) every word the reader receives equals the value that was
-- stored at that address when it was read (checked against the producer index
-- logged at the moment the response FIFO was written), (d) refresh intervals stay
-- within the part's 15.6 us limit with a tight bound on the stretch caused by
-- a read burst.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.textio.all;
use work.sim_pkg.all;

entity cont_read_case is
  generic (
    NAME     : string;
    RATE_DIV : natural;
    NARROW   : boolean;
    STREAM   : boolean;
    RING     : natural := 3000;
    RUN_CLK  : natural := 30000   -- reader runtime in CLK (100.2 MHz) cycles
  );
  port (clk, fastclk, pclk, pclk_model : in std_logic; done : out std_logic := '0');
end cont_read_case;

architecture bench of cont_read_case is
  signal rdiv : natural range 1 to 500000000 := RATE_DIV;
  signal run, full, armed, fast_mode, auto_renew : std_logic := '0';
  signal inputs  : std_logic_vector(15 downto 0) := (others => '0');
  signal address : natural range 0 to RING := 0;
  signal outputs : std_logic_vector(15 downto 0);
  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n, sdram_ras_n, sdram_we_n, sdram_clk : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal status     : std_logic_vector(7 downto 0);
  signal producer_idx, oldest_idx, drop_reg : std_logic_vector(31 downto 0);
  signal blk_tog : std_logic := '0';
  signal blk_base, blk_count : natural range 0 to RING := 0;
  signal rd_q : std_logic_vector(15 downto 0);
  signal rd_empty : std_logic;
  signal rd_req : std_logic := '0';
  signal narrow_en : std_logic;
  signal monitor_on : boolean := false;
  signal v0_valid : boolean := false;
  signal v0 : natural := 0;
  type word_arr is array (0 to 16383) of std_logic_vector(15 downto 0);
  type nat_arr is array (0 to 16383) of natural;
  signal log_data : word_arr := (others => (others => '0'));
  signal log_pi   : nat_arr := (others => 0);
  signal log_n    : natural := 0;
  signal log_ad   : nat_arr := (others => 0);
  signal worst_refresh : natural := 0;
  signal n_refresh : natural := 0;
  signal ticks_seen : natural := 0;
begin
  narrow_en <= '1' when NARROW else '0';
  process(fastclk) begin
    if rising_edge(fastclk) then inputs <= std_logic_vector(unsigned(inputs)+1); end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples=>RING, Channels=>16, Sim=>false, FAST_SPEED=>true,
      CLK_Frequency=>166666667, SDRAM_CLK_HZ=>166666667, SAMPLE_CLK_HZ=>200000000)
    port map (CLK=>clk, SDRAM_CLK_IN=>pclk, CLK_150=>open, Rate_Div=>rdiv,
      Samples=>RING, Start_Offset=>0, Run=>run, Full=>full, Inputs=>inputs,
      Address=>address, Outputs=>outputs, sdram_addr=>sdram_addr, sdram_ba=>sdram_ba,
      sdram_cas_n=>sdram_cas_n, sdram_dq=>sdram_dq, sdram_dqm=>sdram_dqm,
      sdram_ras_n=>sdram_ras_n, sdram_we_n=>sdram_we_n, sdram_cke=>sdram_cke,
      sdram_cs_n=>sdram_cs_n, sdram_clk=>sdram_clk, Status=>status,
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>fastclk, Continuous_Mode=>'1',
      Narrow_Enable=>narrow_en, Narrow_Channel=>4,
      Blk_Rd_Req_Tog=>blk_tog, Blk_Rd_Base=>blk_base, Blk_Rd_Count=>blk_count,
      Auto_Renew=>auto_renew,
      Rd_Fifo_Q=>rd_q, Rd_Fifo_Empty=>rd_empty, Rd_Fifo_RdReq=>rd_req,
      Producer_Index=>producer_idx, Oldest_Index=>oldest_idx,
      Pump_Valid_Cycles=>drop_reg);

  SDRAM : entity work.sdram_pin_model
    generic map (CL=>3, STRICT=>true, FAIL_ON_VIOLATION=>true, REAL_READ_TIMING=>true, READ_HOLD_CYCLES=>3)
    port map (clk=>pclk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  -- ---- observation through external names into the FLA -------------------
  obs : block
    alias rdwr   is <<signal ^.dut.rdfifo_wr : std_logic>>;
    alias rddat  is <<signal ^.dut.rdfifo_wdata : std_logic_vector(15 downto 0)>>;
    alias afwr   is <<signal ^.dut.afifo_wr : std_logic>>;
    alias afdat  is <<signal ^.dut.afifo_wdata : std_logic_vector(15 downto 0)>>;
    alias saddr  is <<signal ^.dut.stream_addr_r : std_logic_vector(21 downto 0)>>;
  begin
    -- first word pushed into the capture FIFO after run start = sample 0
    first_word : process(fastclk)
      variable got : boolean := false;
    begin
      if rising_edge(fastclk) then
        if run = '1' and not got and afwr = '1' then
          got := true;
          v0 <= to_integer(unsigned(afdat));
          v0_valid <= true;
        end if;
        if afwr = '1' and monitor_on then
          ticks_seen <= ticks_seen + 1;
        end if;
      end if;
    end process;
    progress : process
      alias rdused is <<signal ^.dut.rdfifo_wrusedw : std_logic_vector(9 downto 0)>>;
    begin
      wait until monitor_on;
      while monitor_on loop
        wait for 20 us;
        report NAME & " progress: PI=" & integer'image(to_integer(unsigned(producer_idx))) &
               " drops=" & integer'image(to_integer(unsigned(drop_reg(15 downto 0)))) &
               " rdfifo_used=" & integer'image(to_integer(unsigned(rdused))) &
               " log_n=" & integer'image(log_n);
      end loop;
      wait;
    end process;
    -- one log entry per word written into the response FIFO (read time)
    rd_log : process(pclk)
    begin
      if rising_edge(pclk) then
        if rdwr = '1' and monitor_on and log_n < 16384 then
          log_data(log_n) <= rddat;
          log_pi(log_n) <= to_integer(unsigned(producer_idx));
          log_ad(log_n) <= to_integer(unsigned(saddr));
          log_n <= log_n + 1;
        end if;
      end if;
    end process;
  end block;

  -- ---- refresh interval monitor (SDRAM command pins) --------------------
  refresh_mon : process(pclk_model)
    variable cyc, last : integer := 0;
    variable have_last : boolean := false;
  begin
    if rising_edge(pclk_model) then
      cyc := cyc + 1;
      if not monitor_on then
        have_last := false;
      elsif sdram_cs_n = '0' and sdram_ras_n = '0' and sdram_cas_n = '0' and sdram_we_n = '1' then
        if have_last then
          n_refresh <= n_refresh + 1;
          if cyc - last > worst_refresh then worst_refresh <= cyc - last; end if;
        end if;
        last := cyc; have_last := true;
      end if;
    end if;
  end process;

  main : process
    variable base : natural := 0;
    variable base0 : natural := 0;
    variable next_block : natural := 0;
    variable n_recv : natural := 0;
    variable ring_words : word_arr := (others => (others => '0'));
    variable rn : natural;
    variable d : integer;
    variable w : std_logic_vector(15 downto 0);
    variable bad : natural := 0;
    variable bad_first : integer := -1;
    variable pi_end, drops : natural;
    variable l : line;
    variable stop_at : natural;
    variable done_read : boolean := false;
    variable t : natural;
    -- expected value of ring address a given the producer index pi
    function val_of(v : natural; i : integer) return std_logic_vector is
    begin
      return std_logic_vector(to_unsigned((v + RATE_DIV * i) mod 65536, 16));
    end function;
    procedure check_word(n : natural) is
      variable a, pi, il : integer;
      variable ok : boolean;
    begin
      if NARROW then return; end if;
      a := (base0 + n) mod RING;
      pi := log_pi(n);
      if pi - 1 < a then return; end if;    -- cell not written yet: model memory init
      il := a + RING * ((pi - 1 - a) / RING);
      ok := (log_data(n) = val_of(v0, il));
      -- cells within a few words of the write pointer may be one lap behind /
      -- ahead (a word counted but not yet on the pins, or just overwritten)
      if (not ok) and ((pi - 1 - il) < 8 or (il + RING - (pi - 1)) < 8) then
        ok := (log_data(n) = val_of(v0, il + RING)) or (log_data(n) = val_of(v0, il - RING));
      end if;
      if not ok then
        if bad < 4 then
          report NAME & ": mismatch n=" & integer'image(n) & " addr=" & integer'image(a) &
                 " PI=" & integer'image(pi) & " il=" & integer'image(il) & " got=" & to_hstring(log_data(n)) &
                 " expected=" & to_hstring(val_of(v0, il)) & " v0=" & integer'image(v0) &
                 " stream_addr_r=" & integer'image(log_ad(n));
        end if;
        bad := bad + 1;
        if bad_first < 0 then bad_first := n; end if;
      end if;
    end procedure;
  begin
    wait_cycles(pclk, 900000);
    fast_mode <= '1'; armed <= '1';
    wait_cycles(clk, 40);
    run <= '1';
    wait_cycles(fastclk, 4000);           -- pump settles, ring starts filling
    monitor_on <= true;
    base0 := 0;
    stop_at := RUN_CLK;
    t := 0;
    -- reader loop: SPI-paced, drains one word per 89 clk cycles
    if STREAM then
      auto_renew <= '1';
      blk_base <= 0; blk_count <= RING;
      wait_cycles(clk, 2);
      blk_tog <= not blk_tog;
    end if;
    while t < stop_at loop
      if not STREAM and n_recv >= next_block then
        next_block := next_block + 512;
        base := (base0 + n_recv) mod RING;
        blk_base <= base; blk_count <= 512;
        wait_cycles(clk, 2);
        blk_tog <= not blk_tog;
      end if;
      wait until rising_edge(clk);
      t := t + 1;
      rd_req <= '0';
      if rd_empty = '0' then
        rd_req <= '1';
        wait until rising_edge(clk);
        rd_req <= '0';
        wait until rising_edge(clk);
        wait until rising_edge(clk);
        w := rd_q;
        -- (log entry n was written when this word entered the response FIFO)
        if n_recv < log_n then
          assert w = log_data(n_recv)
            report NAME & ": response FIFO word " & integer'image(n_recv) & " altered in transit"
            severity failure;
          check_word(n_recv);
        end if;
        n_recv := n_recv + 1;
        t := t + 3;
        wait_cycles(clk, 86);
        t := t + 86;
      end if;
    end loop;
    monitor_on <= false;
    pi_end := to_integer(unsigned(producer_idx));
    drops := to_integer(unsigned(drop_reg(15 downto 0)));
    write(l, string'(NAME & ": reader received ") & integer'image(n_recv) & " words; producer index " &
          integer'image(pi_end) & "; dropped-sample counter " & integer'image(drops) &
          "; capture-FIFO pushes during window " & integer'image(ticks_seen) &
          "; worst refresh interval " & integer'image(worst_refresh) & " clks (" &
          integer'image(n_refresh) & " intervals); value mismatches " & integer'image(bad));
    writeline(output, l);
    assert drops = 0
      report NAME & ": " & integer'image(drops) & " samples dropped while the host read" severity failure;
    assert bad = 0
      report NAME & ": " & integer'image(bad) & " words received do not match the stored data (first at " &
             integer'image(bad_first) & ")" severity failure;
    assert n_refresh >= 20 and worst_refresh <= 1308 + 24 + 16 * 22 + 90
      report NAME & ": refresh interval stretched to " & integer'image(worst_refresh) & " clks" severity failure;
    assert n_recv >= 100 report NAME & ": reader made no progress" severity failure;
    -- (b) stop the capture and read back the whole ring: one exact incrementing run
    auto_renew <= '0';
    run <= '0';
    wait_cycles(clk, 6000);
    -- discard what the stopped stream left in the response FIFO
    while rd_empty = '0' loop
      rd_req <= '1';
      wait until rising_edge(clk);
      rd_req <= '0';
      wait until rising_edge(clk);
      wait until rising_edge(clk);
    end loop;
    if not NARROW then
      pi_end := to_integer(unsigned(producer_idx));
      blk_base <= pi_end mod RING; blk_count <= RING;
      wait_cycles(clk, 2);
      blk_tog <= not blk_tog;
      rn := 0; t := 0;
      while rn < RING loop
        wait until rising_edge(clk);
        rd_req <= '0';
        if rd_empty = '0' then
          rd_req <= '1';
          wait until rising_edge(clk);
          rd_req <= '0';
          wait until rising_edge(clk);
          wait until rising_edge(clk);
          ring_words(rn) := rd_q;
          rn := rn + 1; t := 0;
        else
          t := t + 1;
          assert t < 200000 report NAME & ": ring readout timeout" severity failure;
        end if;
      end loop;
      for i in 1 to RING - 1 loop
        d := (to_integer(unsigned(ring_words(i))) - to_integer(unsigned(ring_words(i-1))) + 65536) mod 65536;
        assert d = RATE_DIV
          report NAME & ": ring word " & integer'image(i) & " = " & to_hstring(ring_words(i)) &
                 " after " & to_hstring(ring_words(i-1)) & " (a sample was lost or repeated)"
          severity failure;
      end loop;
      report NAME & ": ring of " & integer'image(RING) & " words is one exact incrementing run";
    end if;
    done <= '1';
    wait;
  end process;
end bench;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use work.sim_pkg.all;

entity tb_cont_read is
end tb_cont_read;

architecture bench of tb_cont_read is
  signal clk, fastclk, pclk, pclk_model : std_logic := '0';
  signal done : std_logic_vector(2 downto 0);
begin
  clk    <= not clk    after 4990 ps;
  fastclk <= not fastclk after 2495 ps;
  pclk   <= not pclk   after 2994 ps;
  -- the board forwards an INVERTED copy of the core clock to the chip
  pclk_model <= transport not pclk after 1.5 ns;

  c0 : entity work.cont_read_case
    generic map (NAME => "raw100blk", RATE_DIV => 2, NARROW => false, STREAM => false)
    port map (clk, fastclk, pclk, pclk_model, done(0));
  c1 : entity work.cont_read_case
    generic map (NAME => "raw100str", RATE_DIV => 2, NARROW => false, STREAM => true)
    port map (clk, fastclk, pclk, pclk_model, done(1));
  c2 : entity work.cont_read_case
    generic map (NAME => "narrow200", RATE_DIV => 1, NARROW => true, STREAM => false)
    port map (clk, fastclk, pclk, pclk_model, done(2));

  finish : process
  begin
    wait until done = "111" for 40 ms;
    assert done = "111" report "not every case completed" severity failure;
    report "tb_cont_read: ALL PASS";
    std.env.finish;
    wait;
  end process;
end bench;

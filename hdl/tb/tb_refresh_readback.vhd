-- SDRAM refresh service during every long bus-holding path, measured on the
-- command pins of the strict pin model (REAL_READ_TIMING, inverted chip clock).
--
-- Prints, per phase, the number of AUTO REFRESH commands, the minimum / average /
-- maximum interval between them (166.7 MHz clocks and ns), and the age of the
-- last refresh when the phase ends (so a phase that never refreshes shows a
-- large age), then asserts a bound (MAX_ALLOWED_NS) on the worst gap.
--
-- What the part needs is every row refreshed within 64 ms: 4,096 AUTO REFRESH
-- commands per 64 ms, 15.6 us on average. The controller issues one every
-- 7.8 us, but in rd_mode a pending refresh is only served when the reader
-- pauses (response FIFO almost full, or between blocks), so single-shot
-- readback has holes: 66 us per 512-word block and about 134 us at the start
-- of a stream with a host at SPI speed, 351 us for a host that drains faster
-- than SPI can. The average over a readback stays at or below 8.8 us, so the
-- requirement is met with about 1.8x margin. The default bound (400 us) passes
-- on that behaviour and fails if a change starves refresh for longer; run with
-- -gMAX_ALLOWED_NS=15600 to list every hole.
--
-- Phases (one device, one sequence):
--   1 single-shot capture at 100 MS/s (65536 samples): pump bursts
--   2 idle after Full (no host reads)
--   3 block readback, host draining the response FIFO at 1.1 M words/s
--   4 host stalled with a 4096-word block request outstanding (FIFO fills,
--     reader pauses), then drained
--   5 held raw stream: one RD_WORDS request, host draining at 1.1 M words/s
--   6 second run: run start (stale-word drain) and continuous capture
--   7 readback after the continuous capture was stopped (rd_mode after stop)
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.textio.all;
use work.sim_pkg.all;

entity tb_refresh_readback is
  generic (MAX_ALLOWED_NS : natural := 400000;
           RD_WORDS : natural := 8192);
end tb_refresh_readback;

architecture bench of tb_refresh_readback is
  constant MAXS : natural := 65536;
  constant NPH  : natural := 8;
  signal clk, fastclk, pclk, pclk_model : std_logic := '0';
  signal rdiv : natural range 1 to 500000000 := 2;
  signal samples_in : natural range 1 to MAXS := 65536;
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
  signal producer_idx, oldest_idx : std_logic_vector(31 downto 0);
  signal blk_tog : std_logic := '0';
  signal blk_base, blk_count : natural range 0 to MAXS := 0;
  signal rd_q : std_logic_vector(15 downto 0);
  signal rd_empty : std_logic;
  signal rd_req : std_logic := '0';
  signal phase : natural range 0 to NPH := 0;
  type stat_t is array (0 to NPH) of integer;
  signal st_n, st_min, st_max, st_sum, st_age, st_clks, st_rdcmds : stat_t := (others => 0);
begin
  clk     <= not clk     after 4990 ps;
  fastclk <= not fastclk after 2495 ps;
  pclk    <= not pclk    after 2994 ps;
  pclk_model <= transport not pclk after 1.5 ns;

  process(fastclk) begin
    if rising_edge(fastclk) then inputs <= std_logic_vector(unsigned(inputs)+1); end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples=>MAXS, Channels=>16, Sim=>false, FAST_SPEED=>true,
      CLK_Frequency=>166666667, SDRAM_CLK_HZ=>166666667, SAMPLE_CLK_HZ=>200000000)
    port map (CLK=>clk, SDRAM_CLK_IN=>pclk, CLK_150=>open, Rate_Div=>rdiv,
      Samples=>samples_in, Start_Offset=>0, Run=>run, Full=>full, Inputs=>inputs,
      Address=>address, Outputs=>outputs, sdram_addr=>sdram_addr, sdram_ba=>sdram_ba,
      sdram_cas_n=>sdram_cas_n, sdram_dq=>sdram_dq, sdram_dqm=>sdram_dqm,
      sdram_ras_n=>sdram_ras_n, sdram_we_n=>sdram_we_n, sdram_cke=>sdram_cke,
      sdram_cs_n=>sdram_cs_n, sdram_clk=>sdram_clk, Status=>status,
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>fastclk, Continuous_Mode=>cont_mode,
      Blk_Rd_Req_Tog=>blk_tog, Blk_Rd_Base=>blk_base, Blk_Rd_Count=>blk_count,
      Rd_Fifo_Q=>rd_q, Rd_Fifo_Empty=>rd_empty, Rd_Fifo_RdReq=>rd_req,
      Producer_Index=>producer_idx, Oldest_Index=>oldest_idx);

  SDRAM : entity work.sdram_pin_model
    generic map (CL=>3, STRICT=>true, FAIL_ON_VIOLATION=>true, REAL_READ_TIMING=>true, READ_HOLD_CYCLES=>3)
    port map (clk=>pclk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  -- Debug view into the controller while the host is stalled (phase 4).
  dbg : block
    alias refq  is <<signal ^.dut.SDRAM_Interface1.u187.u0.ref_req : std_logic>>;
    alias prn   is <<signal ^.dut.SDRAM_Interface1.u187.u0.pend_rn : std_logic>>;
    alias pwn   is <<signal ^.dut.SDRAM_Interface1.u187.u0.pend_wn : std_logic>>;
    alias rdn   is <<signal ^.dut.SDRAM_Interface1.u187.u0.sdram_s_read_n : std_logic>>;
    alias wrq   is <<signal ^.dut.SDRAM_Interface1.u187.u0.sdram_s_waitrequest : std_logic>>;
    alias rdused is <<signal ^.dut.rdfifo_wrusedw : std_logic_vector(9 downto 0)>>;
  begin
    process
    begin
      wait until phase = 4;
      for i in 1 to 14 loop
        wait for 25 us;
        report "stall dbg: ref_req=" & std_logic'image(refq) & " pend_rn=" & std_logic'image(prn) &
               " pend_wn=" & std_logic'image(pwn) & " read_n=" & std_logic'image(rdn) &
               " waitreq=" & std_logic'image(wrq) & " rdfifo_used=" & integer'image(to_integer(unsigned(rdused))) ;
      end loop;
      wait;
    end process;
  end block;

  -- Per-phase refresh statistics. An interval is credited to the phase in which
  -- it ENDS; the running age of the last refresh is credited to the current
  -- phase, so a phase that never refreshes shows a large age.
  refresh_mon : process(pclk_model)
    variable cyc, last : integer := 0;
    variable have_last : boolean := false;
    variable iv : integer;
    variable n_long : natural := 0;
  begin
    if rising_edge(pclk_model) then
      cyc := cyc + 1;
      st_clks(phase) <= st_clks(phase) + 1;
      if sdram_cs_n = '0' and sdram_ras_n = '0' and sdram_cas_n = '0' and sdram_we_n = '1' then
        if have_last and phase /= 0 then
          iv := cyc - last;
          if iv > 2600 and n_long < 40 then
            n_long := n_long + 1;
            report "long gap: phase " & integer'image(phase) & " interval " & integer'image(iv) &
                   " clks = " & integer'image((iv * 5994) / 1000) & " ns";
          end if;
          st_n(phase) <= st_n(phase) + 1;
          st_sum(phase) <= st_sum(phase) + iv;
          if iv > st_max(phase) then st_max(phase) <= iv; end if;
          if st_min(phase) = 0 or iv < st_min(phase) then st_min(phase) <= iv; end if;
        end if;
        last := cyc; have_last := true;
      elsif sdram_cs_n = '0' and sdram_ras_n = '1' and sdram_cas_n = '0' and sdram_we_n = '1' then
        st_rdcmds(phase) <= st_rdcmds(phase) + 1;
      end if;
      if have_last and phase /= 0 and cyc - last > st_age(phase) then
        st_age(phase) <= cyc - last;
      end if;
    end if;
  end process;

  main : process
    variable t : natural;
    variable l : line;
    variable worst_clks : integer := 0;
    procedure drain_paced(constant count : in natural; constant gap : in natural) is
      variable n : natural := 0;
    begin
      t := 0;
      while n < count loop
        wait until rising_edge(clk);
        rd_req <= '0';
        if rd_empty = '0' then
          rd_req <= '1';
          wait until rising_edge(clk);
          rd_req <= '0';
          wait until rising_edge(clk);
          wait until rising_edge(clk);
          n := n + 1; t := 0;
          if gap > 0 then wait_cycles(clk, gap); end if;
        else
          t := t + 1;
          assert t < 200000 report "readout timeout after " & integer'image(n) & " words" severity failure;
        end if;
      end loop;
    end procedure;
    procedure drain_all is
    begin
      t := 0;
      while t < 3000 loop
        wait until rising_edge(clk);
        rd_req <= '0';
        if rd_empty = '0' then
          rd_req <= '1';
          wait until rising_edge(clk);
          rd_req <= '0';
          wait until rising_edge(clk);
          wait until rising_edge(clk);
          t := 0;
        else
          t := t + 1;
        end if;
      end loop;
    end procedure;
    procedure request(constant base : in natural; constant count : in natural) is
    begin
      blk_base <= base; blk_count <= count;
      wait_cycles(clk, 2);
      blk_tog <= not blk_tog;
    end procedure;
    procedure report_phase(constant p : in natural; constant label_s : in string) is
      variable avg_x10 : integer;
    begin
      if st_n(p) > 0 then avg_x10 := (st_sum(p) * 10) / st_n(p); else avg_x10 := 0; end if;
      write(l, string'("PHASE " & integer'image(p) & " " & label_s & ": span " &
            integer'image((st_clks(p) / 1000) * 5994 / 1000) & " us, refreshes " & integer'image(st_n(p)) &
            ", interval min/avg/max " & integer'image(st_min(p)) & "/" &
            integer'image(avg_x10 / 10) & "/" & integer'image(st_max(p)) & " clks = " &
            integer'image((st_min(p) * 5994) / 1000) & "/" & integer'image((avg_x10 * 5994) / 10000) &
            "/" & integer'image((st_max(p) * 5994) / 1000) & " ns, max age " &
            integer'image(st_age(p)) & " clks = " & integer'image((st_age(p) * 5994) / 1000) &
            " ns, READ cmds " & integer'image(st_rdcmds(p))));
      writeline(output, l);
      if st_max(p) > worst_clks then worst_clks := st_max(p); end if;
      if st_age(p) > worst_clks then worst_clks := st_age(p); end if;
    end procedure;
  begin
    wait_cycles(pclk, 900000);               -- SDRAM power-up
    fast_mode <= '1'; armed <= '1';
    wait_cycles(clk, 40);
    -- Phase 1: single-shot capture
    phase <= 1;
    run <= '1';
    wait until full = '1' for 3 ms;
    assert full = '1' report "capture did not complete" severity failure;
    wait_cycles(clk, 20);
    -- Phase 2: idle after Full
    phase <= 2;
    wait_cycles(clk, 20000);                 -- 200 us
    -- Phase 3: paced block readback, 512-word blocks, 1.1 M words/s (gap 86)
    phase <= 3;
    for b in 0 to RD_WORDS / 512 - 1 loop
      request(b * 512, 512);
      drain_paced(512, 86);
    end loop;
    -- Phase 4: host stalled with a request outstanding
    phase <= 4;
    request(0, 4096);
    wait_cycles(clk, 30000);                 -- 300 us, nothing drained
    drain_paced(4096, 0);
    -- Phase 5: held raw stream
    phase <= 5;
    request(0, RD_WORDS);
    drain_paced(RD_WORDS, 86);
    drain_all;
    -- Phase 6: second run: run start (stale-word drain) + continuous capture
    phase <= 6;
    cont_mode <= '1';
    run <= '0';
    wait_cycles(clk, 100);
    run <= '1';
    wait_cycles(clk, 70000);                 -- 700 us of continuous capture
    -- Phase 7: stop, then readback (rd_mode after stop)
    run <= '0';
    wait_cycles(clk, 6000);
    phase <= 7;
    request(0, RD_WORDS);
    drain_paced(RD_WORDS, 86);
    phase <= 8;
    report_phase(1, "single-shot capture 100 MS/s (pump bursts)");
    report_phase(2, "idle after Full");
    report_phase(3, "paced block readback 1.1 Mw/s");
    report_phase(4, "stalled host, request outstanding");
    report_phase(5, "held raw stream, paced");
    report_phase(6, "2nd run start + continuous capture");
    report_phase(7, "readback after stopped continuous capture");
    write(l, string'("worst interval or age over all phases: " & integer'image(worst_clks) & " clks = " &
          integer'image((worst_clks * 5994) / 1000) & " ns"));
    writeline(output, l);
    assert (worst_clks * 5994) / 1000 <= MAX_ALLOWED_NS
      report "a refresh gap of " & integer'image((worst_clks * 5994) / 1000) & " ns exceeds the bound"
      severity failure;
    report "tb_refresh_readback: done";
    std.env.finish;
    wait;
  end process;
end bench;

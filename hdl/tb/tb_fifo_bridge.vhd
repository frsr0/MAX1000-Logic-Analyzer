-- FIFO bridge testbench: verifies the registered CDC boundary between FAST_CLK
-- and pclk domains. Tests ordering, show-ahead prefetch correctness, no
-- duplicated/lost words under backpressure, drain-based run start logic, and
-- that the FAST_RAW_BUILD profile still captures correctly.
--
-- Uses the real Fast_Logic_Analyzer_SDRAM with separate clocks and the
-- pipelined FIFO bridge. The SDRAM is replaced by a behavioral model that
-- captures the cap_stream handshake.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_fifo_bridge is
end tb_fifo_bridge;

architecture bench of tb_fifo_bridge is
  constant FAST_PERIOD : time := 5.0 ns;    -- 200 MHz
  constant PCLK_PERIOD : time := 6.0 ns;     -- 166.67 MHz

  signal fast_clk : std_logic := '0';
  signal pclk     : std_logic := '0';

  -- DUT signals
  signal run       : std_logic := '0';
  signal full      : std_logic;
  signal inputs    : std_logic_vector(15 downto 0) := (others => '0');
  signal address   : natural range 0 to 3000000 := 0;
  signal outputs   : std_logic_vector(15 downto 0);
  signal status    : std_logic_vector(7 downto 0);
  signal fast_mode : std_logic := '1';
  signal armed     : std_logic := '0';
  signal rate_div    : natural range 1 to 500000000 := 1;
  signal samples_cfg : natural range 1 to 3000000 := 8192;

  -- Packed-mode (mso_capture) producer port
  signal packed_mode  : std_logic := '0';
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

  -- SDRAM model signals
  signal sdram_dq   : std_logic_vector(15 downto 0);

  -- Capture-probe signals
  signal cap_valid  : std_logic;
  signal cap_ready  : std_logic;
  signal cap_data   : std_logic_vector(15 downto 0);
  signal cap_addr   : std_logic_vector(21 downto 0);

  type word_array is array (natural range <>) of std_logic_vector(15 downto 0);
  type addr_array is array (natural range <>) of std_logic_vector(21 downto 0);
  shared variable captured : word_array(0 to 16383) := (others => (others => '0'));
  shared variable cap_addrs : addr_array(0 to 16383) := (others => (others => '0'));
  shared variable cap_cnt  : integer := 0;

begin

  -- Clock generation: asynchronous domains
  fast_clk <= not fast_clk after FAST_PERIOD / 2;
  pclk     <= not pclk     after PCLK_PERIOD / 2;

  -- Probe the write-pump handshake
  cap_valid <= << signal .tb_fifo_bridge.dut.cap_stream_valid : std_logic >>;
  cap_ready <= << signal .tb_fifo_bridge.dut.cap_stream_ready : std_logic >>;
  cap_data  <= << signal .tb_fifo_bridge.dut.cap_stream_data  : std_logic_vector(15 downto 0) >>;
  cap_addr  <= << signal .tb_fifo_bridge.dut.cap_stream_addr  : std_logic_vector(21 downto 0) >>;

  -- Free-running input counter in FAST_CLK domain
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      inputs <= std_logic_vector(unsigned(inputs) + 1);
    end if;
  end process;

  -- DUT: Fast_Logic_Analyzer_SDRAM with the new registered FIFO bridge
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
      Continuous_Mode => '0',
      Packed_Mode  => packed_mode,
      Packed_Data  => packed_data,
      Packed_Valid => packed_valid,
      Packed_Ready => packed_ready
    );

  -- Packed-mode producer: offers an incrementing word stream (throttled to one
  -- word per 4 cycles, well under the pump drain rate, like the real
  -- mso_capture trickle) and advances only on the valid/ready handshake.
  process(fast_clk)
    variable next_word : unsigned(15 downto 0) := x"0001";
    variable throttle  : integer range 0 to 3 := 0;
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
        next_word := x"0001";
        throttle := 0;
        packed_accepted <= 0;
      end if;
    end if;
  end process;

  -- SDRAM pin model
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

  -- Record every accepted capture-stream handshake to verify ordering
  process(pclk)
  begin
    if rising_edge(pclk) then
      if cap_valid = '1' and cap_ready = '1' then
        if cap_cnt < captured'length then
          captured(cap_cnt) := cap_data;
          cap_addrs(cap_cnt) := cap_addr;
        end if;
        cap_cnt := cap_cnt + 1;
      end if;
    end if;
  end process;

  -- Test sequence
  process
    variable prev, curr : integer;
    variable step       : integer;
    variable anomalies  : integer := 0;
    variable cap_base   : integer := 0;
    variable got        : integer := 0;

    -- Run one divided-rate capture and require an EXACT sample count with
    -- EXACTLY rate_div counter steps between consecutive words. This is the
    -- regression reproducer for both 2026 sample-duplication bugs (a
    -- duplicate or junk word shows as step 0 / garbage; an eaten or stale
    -- word shows as a wrong count or wrong step).
    procedure divided_capture(constant div : natural;
                              constant nsamp : natural;
                              constant tname : string) is
    begin
      rate_div <= div;
      samples_cfg <= nsamp;
      wait for 100 ns;
      cap_base := cap_cnt;
      run <= '1';
      wait until rising_edge(full) for 1 ms;
      assert full = '1'
        report "FAIL(" & tname & "): capture did not complete (full never rose)"
        severity failure;
      run <= '0';
      wait for 500 ns;
      got := cap_cnt - cap_base;
      report tname & ": captured " & integer'image(got) & " words (expect "
             & integer'image(nsamp) & ")";
      for i in cap_base to cap_base + 9 loop
        if i < cap_cnt then
          report "  captured[" & integer'image(i - cap_base) & "] = "
                 & integer'image(to_integer(unsigned(captured(i))))
                 & " @addr " & integer'image(to_integer(unsigned(cap_addrs(i))));
        end if;
      end loop;
      assert got = nsamp
        report "FAIL(" & tname & "): expected exactly " & integer'image(nsamp)
               & " committed words, got " & integer'image(got)
               & " (duplication or drain eating samples)"
        severity failure;
      for i in cap_base + 1 to cap_cnt - 1 loop
        prev := to_integer(unsigned(captured(i - 1)));
        curr := to_integer(unsigned(captured(i)));
        step := (curr - prev) mod 65536;
        assert step = div
          report "FAIL(" & tname & "): step at idx " & integer'image(i - cap_base)
                 & " is " & integer'image(step) & ", expected "
                 & integer'image(div) & " (prev=" & integer'image(prev)
                 & " curr=" & integer'image(curr) & ")"
          severity failure;
      end loop;
      report tname & " PASS: exact count, uniform step " & integer'image(div);
    end procedure;
  begin
    report "=== Starting FIFO bridge test ===";

    armed <= '1';

    -- Wait for clocks to stabilize
    wait for 100 ns;

    -- ======== TEST 1: Divided-rate capture (duplication regression) ========
    -- Tick every 20 FAST_CLK cycles: the FIFO goes empty between ticks, which
    -- is the first-word fall-through case that reintroduced duplication on
    -- hardware (junk word committed before every real sample).
    report "TEST 1: Divided-rate capture (Rate_Div=20)";
    divided_capture(20, 256, "TEST 1");

    -- ======== TEST 2: Full-rate capture ordering ========
    -- Rate_Div=1 = 200 MS/s: faster than the pump can commit, so the FIFO
    -- overflows and the capture ends early with a contiguous prefix. Every
    -- committed word must still be exactly +1 from its predecessor.
    report "TEST 2: Full-rate capture ordering (Rate_Div=1, overflow expected)";
    rate_div <= 1;
    samples_cfg <= 8192;
    wait for 100 ns;
    cap_base := cap_cnt;
    run <= '1';
    wait until rising_edge(full) for 1 ms;
    assert full = '1'
      report "FAIL: full-rate capture never completed/overflowed" severity failure;
    run <= '0';
    wait for 500 ns;
    got := cap_cnt - cap_base;
    report "Full-rate: captured " & integer'image(got) & " words";
    assert got >= 10
      report "FAIL: too few committed words at full rate ("
             & integer'image(got) & ")" severity failure;
    anomalies := 0;
    for i in cap_base + 1 to cap_cnt - 1 loop
      prev := to_integer(unsigned(captured(i - 1)));
      curr := to_integer(unsigned(captured(i)));
      step := (curr - prev) mod 65536;
      if step /= 1 then
        anomalies := anomalies + 1;
        if anomalies <= 5 then
          report "  Anomaly at idx " & integer'image(i - cap_base)
                 & ": expected step 1, got " & integer'image(step)
                 & " (prev=" & integer'image(prev)
                 & " curr=" & integer'image(curr) & ")" severity warning;
        end if;
      end if;
    end loop;
    assert anomalies = 0
      report "FAIL: " & integer'image(anomalies)
             & " ordering anomalies at full rate" severity failure;
    report "TEST 2 PASS: contiguous prefix at full rate";

    -- ======== TEST 3: Post-overflow drain correctness ========
    -- The overflow above left the FIFO full of uncommitted garbage. The next
    -- divided-rate capture must still return an exact, clean sequence: the
    -- run-start drain has to discard all of it (snapshot drain), without
    -- deadlocking (bounded target) and without eating the new capture.
    report "TEST 3: Divided-rate capture after overflow (drain regression)";
    divided_capture(20, 256, "TEST 3");

    -- ======== TEST 4: Packed-mode write path (Packed_Ready regression) ========
    -- The packed producer only emits on the valid/ready handshake. This test
    -- fails (zero accepted words) if Packed_Ready is not driven from the
    -- stage-accept condition — the bug that made hardware packed captures
    -- read back an untouched SDRAM.
    report "TEST 4: Packed-mode capture (Packed_Ready handshake)";
    -- Window must exceed the worst-case run-start stale drain (a full
    -- 2048-word FIFO drains in ~75 us; the packed producer is held off until
    -- it completes so the analog stream starts on a block header).
    packed_mode <= '1';
    rate_div <= 8;
    samples_cfg <= 4096;
    wait for 100 ns;
    cap_base := cap_cnt;
    run <= '1';
    wait until rising_edge(full) for 1 ms;
    got := cap_cnt - cap_base;
    report "TEST 4 debug: full=" & std_logic'image(full)
           & " accepted=" & integer'image(packed_accepted)
           & " committed=" & integer'image(got);
    assert full = '1'
      report "FAIL(TEST 4): packed capture never completed" severity failure;
    run <= '0';
    wait for 500 ns;
    got := cap_cnt - cap_base;
    report "Packed: producer accepted " & integer'image(packed_accepted)
           & " words, committed " & integer'image(got);
    assert packed_accepted > 100
      report "FAIL(TEST 4): Packed_Ready never accepted producer words ("
             & integer'image(packed_accepted) & ")" severity failure;
    assert got > 100
      report "FAIL(TEST 4): too few packed words committed ("
             & integer'image(got) & ")" severity failure;
    -- Committed packed words must be the exact ascending producer sequence
    -- (contiguous, no duplicates or losses). The stream starts at 1.
    for i in cap_base + 1 to cap_cnt - 1 loop
      prev := to_integer(unsigned(captured(i - 1)));
      curr := to_integer(unsigned(captured(i)));
      step := (curr - prev) mod 65536;
      assert step = 1
        report "FAIL(TEST 4): packed step at idx " & integer'image(i - cap_base)
               & " is " & integer'image(step) & " (prev=" & integer'image(prev)
               & " curr=" & integer'image(curr) & ")" severity failure;
    end loop;
    assert to_integer(unsigned(captured(cap_base))) = 1
      report "FAIL(TEST 4): packed stream does not start at word 1 (got "
             & integer'image(to_integer(unsigned(captured(cap_base)))) & ")"
      severity failure;
    packed_mode <= '0';
    report "TEST 4 PASS: packed handshake, contiguous committed stream";

    report "=== FIFO bridge tests complete: ALL PASS ===";
    std.env.finish;
    wait;
  end process;

end bench;


-- Stage 2a: a raw single-shot capture whose producer (200.4 MS/s, one sample per
-- FAST_CLK) outruns the SDRAM write pump must KEEP what it captured.
--
-- Real FLA + real SDRAM_Controller + STRICT pin-level SDRAM model
-- (FAIL_ON_VIOLATION). Expected behaviour:
--   * the sampler stops when the afifo is within RAW_FULL_MARGIN (16) words of
--     genuinely full -- not at DEPTH-320 as before;
--   * everything already pushed is written to SDRAM through the normal burst
--     path, then the capture completes (Full) with the overflow flagged
--     (Pump_Overflow_Count >= 1);
--   * Producer_Index equals the number of words actually committed, and those
--     words read back as one exact, gap-free incrementing run from sample 0.
--
-- Expected committed count: the FIFO fills at (1.2 - drain) words per pclk
-- cycle, with drain ~0.9 words/pclk (tb_pump_tput_max), so
--   committed ~ (DEPTH-16) * 1.2 / (1.2 - 0.9) ~ 4 * (DEPTH-16)
-- (about 2,000 for DEPTH 512, 16,000 for DEPTH 4096). The bound asserted is +-30 % of that.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_pump_overflow_keep is
  generic (
    FIFO_DEPTH : natural := 4096;     -- AFIFO_DEPTH of the DUT
    RATE_DIV   : natural := 1
  );
end tb_pump_overflow_keep;

architecture bench of tb_pump_overflow_keep is
  constant SYS_CLK_HALF    : time := 4990 ps;
  constant FAST_CLK_HALF   : time := 2495 ps;
  constant SDRAM_CLK_HALF  : time := 2994 ps;
  constant MAXS            : natural := 3000000;
  constant SAMPLES         : natural := 2000000;
  signal clk, fastclk, pclk, pclk_model : std_logic := '0';
  signal rdiv : natural range 1 to 500000000 := RATE_DIV;
  signal samples_in : natural range 1 to MAXS := SAMPLES;
  signal run, full, armed, fast_mode : std_logic := '0';
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
  signal producer_idx : std_logic_vector(31 downto 0);
  signal blk_tog : std_logic := '0';
  signal blk_base, blk_count : natural range 0 to MAXS := 0;
  signal rd_q : std_logic_vector(15 downto 0);
  signal rd_empty : std_logic;
  signal rd_req : std_logic := '0';
  type word_arr is array (0 to 65535) of std_logic_vector(15 downto 0);
  -- Pin-level WRITE accounting: every WRITE command on the SDRAM pins, and the
  -- highest linear address written (bank/row/column tracked from ACTIVATE).
  signal pin_writes : natural := 0;
  signal pin_max_addr : integer := -1;
begin
  write_monitor : process(pclk_model)
    variable act_row : integer_vector(0 to 3) := (others => 0);
    variable cmd : std_logic_vector(2 downto 0);
    variable b, lin : integer;
  begin
    if rising_edge(pclk_model) and sdram_cs_n = '0' then
      cmd := sdram_ras_n & sdram_cas_n & sdram_we_n;
      b := to_integer(unsigned(sdram_ba));
      if cmd = "011" then
        act_row(b) := to_integer(unsigned(sdram_addr));
      elsif cmd = "100" and run = '1' then
        lin := b * 1048576 + act_row(b) * 256 + to_integer(unsigned(sdram_addr(7 downto 0)));
        pin_writes <= pin_writes + 1;
        if lin > pin_max_addr then pin_max_addr <= lin; end if;
      end if;
    end if;
  end process;

  clk    <= not clk    after SYS_CLK_HALF;
  fastclk <= not fastclk after FAST_CLK_HALF;
  pclk   <= not pclk   after SDRAM_CLK_HALF;
  pclk_model <= transport pclk after 1.5 ns;
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
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>fastclk, Continuous_Mode=>'0',
      Blk_Rd_Req_Tog=>blk_tog, Blk_Rd_Base=>blk_base, Blk_Rd_Count=>blk_count,
      Rd_Fifo_Q=>rd_q, Rd_Fifo_Empty=>rd_empty, Rd_Fifo_RdReq=>rd_req,
      Producer_Index=>producer_idx, Pump_Overflow_Count=>overflow_count);

  SDRAM : entity work.sdram_pin_model
    generic map (CL=>3, STRICT=>true, FAIL_ON_VIOLATION=>true)
    port map (clk=>pclk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  main : process
    variable words : word_arr := (others => (others => '0'));
    variable n : natural := 0;
    variable t : natural := 0;
    variable pidx, expected : natural;
    variable d : integer;
  begin
    wait_cycles(pclk, 900000);
    fast_mode <= '1'; armed <= '1';
    wait_cycles(clk, 40);
    run <= '1';
    wait until full = '1' for 6 ms;
    assert full = '1' report "FAIL: capture never completed after the overflow" severity failure;
    wait_cycles(clk, 20);
    pidx := to_integer(unsigned(producer_idx));
    expected := (FIFO_DEPTH - 16) * 4;
    report "OVERFLOW KEEP: committed " & integer'image(pidx) & " words, overflow count " &
           integer'image(to_integer(unsigned(overflow_count))) & ", expected ~" &
           integer'image(expected) & " for FIFO depth " & integer'image(FIFO_DEPTH);
    assert to_integer(unsigned(overflow_count)) >= 1
      report "FAIL: overflow not flagged" severity failure;
    assert status(4) = '1'
      report "FAIL: overflow status bit not set" severity failure;
    assert pidx > FIFO_DEPTH
      report "FAIL: committed no more than the FIFO capacity" severity failure;
    assert pidx >= expected * 70 / 100 and pidx <= expected * 130 / 100
      report "FAIL: committed count outside the expected bound" severity failure;
    assert pidx <= 65536 report "TB word array too small" severity failure;
    -- Read every committed word back through the normal readout path.
    blk_base <= 0; blk_count <= pidx;
    wait_cycles(clk, 2);
    blk_tog <= not blk_tog;
    while n < pidx loop
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
        assert t < 200000 report "readout timeout" severity failure;
      end if;
    end loop;
    for i in 1 to pidx - 1 loop
      d := (to_integer(unsigned(words(i))) - to_integer(unsigned(words(i-1))) + 65536) mod 65536;
      assert d = RATE_DIV
        report "FAIL: gap or repeat at word " & integer'image(i) & ": " & to_hstring(words(i)) &
               " after " & to_hstring(words(i-1))
        severity failure;
    end loop;
    -- No SDRAM WRITE may exist that Producer_Index does not account for: one
    -- WRITE command per committed word, none at or beyond address Producer_Index.
    assert pin_writes = pidx
      report "FAIL: " & integer'image(pin_writes) & " WRITE commands reached the SDRAM pins for " &
             integer'image(pidx) & " committed words" severity failure;
    assert pin_max_addr = pidx - 1
      report "FAIL: highest address written is " & integer'image(pin_max_addr) &
             ", expected Producer_Index-1 = " & integer'image(pidx - 1) severity failure;
    report "OVERFLOW KEEP: " & integer'image(pidx) & " committed words are one exact run from the first sample";
    std.env.finish;
    wait;
  end process;
end bench;

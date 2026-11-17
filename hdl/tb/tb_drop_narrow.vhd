-- Continuous capture accounting through the real FLA, real SDRAM controller and
-- the strict pin model:
--   * narrow 200 MHz (RATE_DIV 1, one selected channel packed 16 per word) must
--     push exactly one word per 16 sample ticks: the cycle that writes a finished
--     word used to consume a sample tick (5.9 % loss);
--   * raw 100.2 MS/s must lose nothing (drop counter 0, producer index follows
--     the tick count);
--   * raw 200.4 MS/s outruns the SDRAM pump, so samples are dropped, and the
--     dropped-sample counter (REG 0x60 / Pump_Valid_Cycles bits 15:0) must see
--     them: produced words + dropped samples + FIFO content = sample ticks.
-- Every case runs in parallel (one DUT + SDRAM model each).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity drop_case is
  generic (
    NAME     : string;
    RATE_DIV : natural;
    NARROW   : boolean;
    WIN_FAST : natural     -- measurement window in FAST_CLK cycles
  );
  port (clk, fastclk, pclk, pclk_model : in std_logic; done : out std_logic := '0');
end drop_case;

architecture bench of drop_case is
  constant MAXS : natural := 4194304;
  signal rdiv : natural range 1 to 500000000 := RATE_DIV;
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
  signal producer_idx : std_logic_vector(31 downto 0);
  signal drop_reg : std_logic_vector(31 downto 0);
  signal narrow_en : std_logic;
begin
  narrow_en <= '1' when NARROW else '0';
  process(fastclk) begin
    if rising_edge(fastclk) then inputs <= std_logic_vector(unsigned(inputs)+1); end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples=>MAXS, Channels=>16, Sim=>false, FAST_SPEED=>true,
      CLK_Frequency=>166666667, SDRAM_CLK_HZ=>166666667, SAMPLE_CLK_HZ=>200000000)
    port map (CLK=>clk, SDRAM_CLK_IN=>pclk, CLK_150=>open, Rate_Div=>rdiv,
      Samples=>MAXS, Start_Offset=>0, Run=>run, Full=>full, Inputs=>inputs,
      Address=>address, Outputs=>outputs, sdram_addr=>sdram_addr, sdram_ba=>sdram_ba,
      sdram_cas_n=>sdram_cas_n, sdram_dq=>sdram_dq, sdram_dqm=>sdram_dqm,
      sdram_ras_n=>sdram_ras_n, sdram_we_n=>sdram_we_n, sdram_cke=>sdram_cke,
      sdram_cs_n=>sdram_cs_n, sdram_clk=>sdram_clk, Status=>status,
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>fastclk, Continuous_Mode=>'1',
      Narrow_Enable=>narrow_en, Narrow_Channel=>0,
      Producer_Index=>producer_idx, Pump_Valid_Cycles=>drop_reg);

  SDRAM : entity work.sdram_pin_model
    generic map (CL=>3, STRICT=>true, FAIL_ON_VIOLATION=>true)
    port map (clk=>pclk_model, cke=>sdram_cke, cs_n=>sdram_cs_n, ras_n=>sdram_ras_n,
      cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba, addr=>sdram_addr,
      dqm=>sdram_dqm, dq=>sdram_dq);

  main : process
    variable pi0, pi1, d0, d1 : natural;
    variable ticks, words, drops : integer;
    variable expect_w : integer;
  begin
    wait_cycles(pclk, 900000);
    fast_mode <= '1'; armed <= '1';
    wait_cycles(clk, 40);
    run <= '1';
    wait_cycles(fastclk, 5000);          -- let the pump settle
    pi0 := to_integer(unsigned(producer_idx));
    d0  := to_integer(unsigned(drop_reg(15 downto 0)));
    wait_cycles(fastclk, WIN_FAST);
    pi1 := to_integer(unsigned(producer_idx));
    d1  := to_integer(unsigned(drop_reg(15 downto 0)));
    ticks := WIN_FAST / RATE_DIV;
    words := pi1 - pi0;
    drops := d1 - d0;
    report NAME & ": window " & integer'image(WIN_FAST) & " FAST_CLK -> " & integer'image(ticks) &
           " sample ticks; producer index +" & integer'image(words) & ", dropped " &
           integer'image(drops) & " (counter " & integer'image(d1) & ")";
    if NARROW then
      expect_w := ticks / 16;
      assert abs(words - expect_w) <= 3 and d1 = 0
        report NAME & ": narrow capture must push " & integer'image(expect_w) & " words (got " &
               integer'image(words) & ", dropped " & integer'image(d1) & ")" severity failure;
    elsif RATE_DIV >= 2 then
      assert d1 = 0 and abs(words - ticks) <= 8
        report NAME & ": raw capture must lose nothing: words " & integer'image(words) &
               " of " & integer'image(ticks) & ", dropped " & integer'image(d1) severity failure;
    else
      -- Producer outruns the pump: the missing samples must show up in the counter.
      assert drops > 0 and d1 > 0
        report NAME & ": overrun produced no dropped-sample count" severity failure;
      assert abs(words + drops - ticks) <= 4200
        report NAME & ": words + dropped (" & integer'image(words + drops) &
               ") must account for the sample ticks (" & integer'image(ticks) & ") within the FIFO" severity failure;
    end if;
    done <= '1';
    wait;
  end process;
end bench;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use work.sim_pkg.all;

entity tb_drop_narrow is
end tb_drop_narrow;

architecture bench of tb_drop_narrow is
  signal clk, fastclk, pclk, pclk_model : std_logic := '0';
  signal done : std_logic_vector(2 downto 0);
begin
  clk    <= not clk    after 4990 ps;
  fastclk <= not fastclk after 2495 ps;
  pclk   <= not pclk   after 2994 ps;
  pclk_model <= transport pclk after 1.5 ns;

  c0 : entity work.drop_case generic map (NAME => "narrow200", RATE_DIV => 1, NARROW => true, WIN_FAST => 160000)
    port map (clk, fastclk, pclk, pclk_model, done(0));
  c1 : entity work.drop_case generic map (NAME => "raw100", RATE_DIV => 2, NARROW => false, WIN_FAST => 200000)
    port map (clk, fastclk, pclk, pclk_model, done(1));
  c2 : entity work.drop_case generic map (NAME => "raw200", RATE_DIV => 1, NARROW => false, WIN_FAST => 200000)
    port map (clk, fastclk, pclk, pclk_model, done(2));

  finish : process
  begin
    wait until done = "111" for 20 ms;
    assert done = "111" report "not every case completed" severity failure;
    report "tb_drop_narrow: ALL PASS";
    std.env.finish;
    wait;
  end process;
end bench;

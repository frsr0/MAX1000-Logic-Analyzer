-- Reproduce the during-capture repeated block-read stall (2026-07-02 HW):
-- with continuous capture running, host-style SEQUENTIAL single-block reads
-- (Blk_Rd_Req_Tog toggle -> drain 512 -> toggle -> drain ...) succeed for the
-- first block or two, then every later read never completes (Rd_Fifo stays
-- empty until the OLS-side watchdog would fire). Real FLA + real SDRAM
-- controller + pin model, Continuous_Mode=1, Auto_Renew=0 (single blocks).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_repeated_blockreads is
  generic (
    RATE_DIV  : natural := 20;    -- 200 MHz / 20 = 10 MHz sample rate
    FILL      : natural := 4000;  -- samples captured before reading starts
    N_BLOCKS  : natural := 6;     -- sequential 512-sample block reads
    GAP_CYC   : natural := 3000;  -- pclk cycles between block reads
    DRAIN_TO  : natural := 90000  -- per-block drain timeout (pclk cycles)
  );
end tb_repeated_blockreads;

architecture bench of tb_repeated_blockreads is
  signal clk      : std_logic := '0';   -- pclk / SDRAM core 166.67 MHz
  signal fastclk  : std_logic := '0';   -- sample clock 200 MHz
  signal sdram_clk_model : std_logic := '0';

  signal rdiv       : natural range 1 to 500000000 := RATE_DIV;
  signal samples_in : natural range 1 to 3000000 := 2;
  signal run, full, armed, fast_mode : std_logic := '0';
  signal inputs     : std_logic_vector(15 downto 0) := (others => '0');
  signal address    : natural range 0 to 3000000 := 0;
  signal outputs    : std_logic_vector(15 downto 0);

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
  signal status      : std_logic_vector(8 downto 0);
  signal s_burst     : std_logic;

  signal blk_req_tog : std_logic := '0';
  signal blk_base    : natural range 0 to 3000000 := 0;
  signal blk_count   : natural range 0 to 3000000 := 0;
  signal auto_renew  : std_logic := '0';
  signal rd_fifo_q   : std_logic_vector(15 downto 0);
  signal rd_fifo_empty : std_logic;
  signal rd_fifo_rdreq : std_logic := '0';
  signal producer_index : std_logic_vector(31 downto 0);
begin

  clk <= not clk after 3.0 ns;
  fastclk_gen : process
  begin
    fastclk <= '1'; wait for 2.5 ns;
    fastclk <= '0'; wait for 2.5 ns;
  end process;
  sdram_clk_model <= transport sdram_clk after 1.5 ns;

  -- one unique 16-bit value per FAST_CLK tick (ramp; step RATE_DIV per sample)
  process(fastclk)
  begin
    if rising_edge(fastclk) then
      inputs <= std_logic_vector(unsigned(inputs) + 1);
    end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples => 3000000, Channels => 16, Sim => true, FAST_SPEED => true,
      CLK_Frequency => 166666667, SDRAM_CLK_HZ => 166666667,
      SAMPLE_CLK_HZ => 200000000)
    port map (
      CLK => clk, SDRAM_CLK_IN => '0', CLK_150 => open,
      Rate_Div => rdiv, Samples => samples_in, Start_Offset => 0,
      Run => run, Full => full, Inputs => inputs, Address => address,
      Outputs => outputs,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba, sdram_cas_n => sdram_cas_n,
      sdram_dq => sdram_dq, sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
      sdram_we_n => sdram_we_n, sdram_cke => sdram_cke, sdram_cs_n => sdram_cs_n,
      sdram_clk => sdram_clk, Status => open, s_burst => s_burst,
      Armed => armed, Fast_Mode => fast_mode, FAST_CLK => fastclk,
      Continuous_Mode => '1',
      Blk_Rd_Req_Tog => blk_req_tog, Blk_Rd_Base => blk_base,
      Blk_Rd_Count => blk_count, Auto_Renew => auto_renew,
      Rd_Fifo_Q => rd_fifo_q, Rd_Fifo_Empty => rd_fifo_empty,
      Rd_Fifo_RdReq => rd_fifo_rdreq,
      Producer_Index => producer_index);

  SDRAM : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => false)
    port map (clk => sdram_clk_model, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  main : process
    variable drained  : integer := 0;
    variable timeout  : integer := 0;
    variable blocks_ok : integer := 0;
    variable step_err  : integer := 0;
    variable prev_w    : integer := -1;
    variable cur_w     : integer := 0;
    variable first_w   : integer := -1;
  begin
    rdiv <= RATE_DIV; samples_in <= 3000000; fast_mode <= '1'; armed <= '1';
    auto_renew <= '0';
    wait_cycles(clk, 40);
    run <= '1';

    report "filling ring...";
    wait until unsigned(producer_index) > FILL for 4 ms;
    report "producer=" & integer'image(to_integer(unsigned(producer_index)));

    for b in 0 to N_BLOCKS - 1 loop
      blk_base  <= b * 512;
      blk_count <= 512;
      wait_cycles(clk, 2);
      blk_req_tog <= not blk_req_tog;

      drained := 0;
      timeout := 0;
      prev_w := -1;
      first_w := -1;
      step_err := 0;
      while drained < 512 and timeout < DRAIN_TO loop
        if rd_fifo_empty = '0' then
          rd_fifo_rdreq <= '1';
          wait_cycles(clk, 1);
          rd_fifo_rdreq <= '0';
          wait_cycles(clk, 1);
          cur_w := to_integer(unsigned(rd_fifo_q));
          if first_w < 0 then first_w := cur_w; end if;
          if prev_w >= 0 and ((prev_w + RATE_DIV) mod 65536) /= cur_w then
            step_err := step_err + 1;
          end if;
          prev_w := cur_w;
          drained := drained + 1;
        else
          wait_cycles(clk, 1);
        end if;
        timeout := timeout + 1;
      end loop;

      report "block " & integer'image(b) & " (base " & integer'image(b*512)
             & "): drained " & integer'image(drained) & "/512, first="
             & integer'image(first_w) & ", ramp errs=" & integer'image(step_err)
             & ", producer=" & integer'image(to_integer(unsigned(producer_index)));
      if drained = 512 and step_err <= 1 then
        blocks_ok := blocks_ok + 1;
      end if;

      wait_cycles(clk, GAP_CYC);
    end loop;

    run <= '0';
    wait_cycles(clk, 4);
    report "RESULT: " & integer'image(blocks_ok) & "/"
           & integer'image(N_BLOCKS) & " blocks complete and clean";
    check(blocks_ok = N_BLOCKS,
          "all sequential during-capture block reads complete with ramp data");
    report "=== TB PASSED ===";
    std.env.finish;
    wait;
  end process;
end bench;

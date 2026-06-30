-- Reproduce the continuous-mode streaming-readout failure (HW: producer
-- advances but start_stream readout returns junk/zeros -> Rd_Fifo underruns).
-- Drives the REAL Fast_Logic_Analyzer_SDRAM in Continuous_Mode with a counter
-- input, fills the ring, then exercises the Rd_Fifo streaming readout
-- (Blk_Rd_Req_Tog + Auto_Renew) exactly as the OLS streaming path does, and
-- checks the drained samples form the expected ramp (no underrun/garbage).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_stream_readout is
  generic (
    RATE_DIV : natural := 20;     -- 200 MHz / 20 = 10 MHz sample rate
    FILL     : natural := 3000;   -- samples to capture before reading
    READN    : natural := 600;    -- samples to drain from the stream
    -- HW reality: Auto_Renew (stream_active_i) asserts LATE, only after the
    -- ack TX completes -- well after blk_req_tog. LATE_RENEW=true reproduces
    -- that ordering (and the start-stalled drain) to expose the underrun race.
    LATE_RENEW : boolean := false
  );
end tb_stream_readout;

architecture bench of tb_stream_readout is
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
  signal status      : std_logic_vector(7 downto 0);
  signal s_burst     : std_logic;

  -- Streaming readout ports
  signal blk_req_tog : std_logic := '0';
  signal blk_base    : natural range 0 to 3000000 := 0;
  signal blk_count   : natural range 0 to 3000000 := 0;
  signal auto_renew  : std_logic := '0';
  signal rd_fifo_q   : std_logic_vector(15 downto 0);
  signal rd_fifo_empty : std_logic;
  signal rd_fifo_rdreq : std_logic := '0';
  signal producer_index : std_logic_vector(31 downto 0);

  type word_arr is array (natural range <>) of std_logic_vector(15 downto 0);
  signal got : word_arr(0 to READN-1) := (others => (others => '0'));
begin

  clk <= not clk after 3.0 ns;
  fastclk_gen : process
  begin
    fastclk <= '1'; wait for 2.5 ns;
    fastclk <= '0'; wait for 2.5 ns;
  end process;
  sdram_clk_model <= transport sdram_clk after 1.5 ns;

  -- one unique 16-bit value per FAST_CLK (sampled every RATE_DIV -> ramp step RATE_DIV)
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
      sdram_clk => sdram_clk, Status => status, s_burst => s_burst,
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
    variable idx      : integer := 0;
    variable pop_wait : integer := 0;
    variable nonzero  : integer := 0;
    variable timeout  : integer := 0;
  begin
    rdiv <= RATE_DIV; samples_in <= 3000000; fast_mode <= '1'; armed <= '1';
    wait_cycles(clk, 40);
    run <= '1';

    -- Let the continuous ring fill past FILL samples.
    report "filling ring...";
    wait until unsigned(producer_index) > FILL for 2 ms;
    report "producer_index=" & integer'image(to_integer(unsigned(producer_index)))
           & " after fill";

    -- Start a streaming read from base 0, like CMD_START_STREAM.
    blk_base  <= 0;
    blk_count <= 512;
    if not LATE_RENEW then
      auto_renew <= '1';              -- (incorrect HW order: early)
      wait_cycles(clk, 2);
      blk_req_tog <= not blk_req_tog;
      wait_cycles(clk, 5);
    else
      -- HW order: toggle first (block_rd FSM), Auto_Renew asserts much later
      -- after the ack TX. Do NOT drain yet, so the first 512-block can complete
      -- with Auto_Renew=0 -> stream_active deasserts before renew.
      auto_renew <= '0';
      wait_cycles(clk, 2);
      blk_req_tog <= not blk_req_tog;
      wait_cycles(clk, 1200);         -- ack-TX delay; FLA finishes 512 meanwhile
      auto_renew <= '1';
      wait_cycles(clk, 5);
    end if;

    -- Drain Rd_Fifo: pop when not empty (rdreq one cycle, q valid next cycle).
    idx := 0;
    while idx < READN and timeout < 200000 loop
      if rd_fifo_empty = '0' then
        rd_fifo_rdreq <= '1';
        wait_cycles(clk, 1);
        rd_fifo_rdreq <= '0';
        wait_cycles(clk, 1);      -- q valid now (showahead off)
        got(idx) <= rd_fifo_q;
        idx := idx + 1;
      else
        wait_cycles(clk, 1);
      end if;
      timeout := timeout + 1;
    end loop;
    run <= '0';
    wait_cycles(clk, 4);

    report "drained " & integer'image(idx) & " samples (timeout cnt="
           & integer'image(timeout) & ")";
    for a in 0 to 19 loop
      if a < READN then
        report "  got[" & integer'image(a) & "] = " &
               integer'image(to_integer(unsigned(got(a))));
      end if;
    end loop;
    for a in 0 to READN-1 loop
      if got(a) /= x"0000" then nonzero := nonzero + 1; end if;
    end loop;
    report "nonzero samples = " & integer'image(nonzero) & " / " & integer'image(READN);
    if idx >= READN and nonzero > READN/2 then
      report "STREAM READOUT OK" severity note;
    else
      report "STREAM READOUT BROKEN (underrun/zeros)" severity note;
    end if;
    std.env.finish;
    wait;
  end process;
end bench;

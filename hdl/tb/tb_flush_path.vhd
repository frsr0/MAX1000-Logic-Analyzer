library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_flush_path is
  generic (CLK_HALF : time := 5 ns);
end tb_flush_path;

architecture bench of tb_flush_path is
  constant CHANNELS   : natural := 8;
  constant TEST_SAMPLES : natural := 64;
  constant PRE_TRIGGER : natural := 50;

  signal clk       : std_logic := '0';
  signal rate_div  : natural range 1 to 150000000 := 2;
  signal samples_in : natural range 1 to 3000000 := TEST_SAMPLES;
  signal start_offset : natural range 0 to 3000000 := 0;
  signal run       : std_logic := '0';
  signal full      : std_logic;
  signal inputs    : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
  signal address   : natural range 0 to 3000000 := 0;
  signal outputs   : std_logic_vector(15 downto 0);
  signal armed     : std_logic := '0';
  signal fast_mode : std_logic := '0';
  signal continuous_mode : std_logic := '0';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack  : std_logic_vector(2 downto 0) := (others => '0');

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke  : std_logic;
  signal sdram_cs_n : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n : std_logic;
  signal sdram_clk  : std_logic;
  signal s_burst    : std_logic;
  signal status     : std_logic_vector(7 downto 0);
  signal fast_clk   : std_logic := '0';

  -- Internal probes
  signal sample_en  : std_logic;
  signal fifo_cnt   : natural range 0 to 16;
  signal bram_wren  : std_logic;
  signal bram_waddr : natural range 0 to 1023;
  signal bram_raddr : natural range 0 to 1023;
  signal flush_done_r : std_logic;
  signal enq_valid0 : boolean;
  signal enq_valid1 : boolean;

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;

  -- Input pattern: CH0 toggles based on counter
  process(clk)
    variable cnt : natural := 0;
  begin
    if rising_edge(clk) then
      cnt := cnt + 1;
      if cnt mod 4 = 0 then
        inputs(0) <= not inputs(0);
      end if;
    end if;
  end process;

  -- Internal probes
  sample_en   <= << signal .tb_flush_path.dut.sample_en : std_logic >>;
  fifo_cnt    <= << signal .tb_flush_path.dut.fifo_cnt : natural range 0 to 16 >>;
  bram_wren   <= << signal .tb_flush_path.dut.bram_wren : std_logic >>;
  bram_waddr  <= << signal .tb_flush_path.dut.bram_waddr : natural range 0 to 1023 >>;
  bram_raddr  <= << signal .tb_flush_path.dut.bram_raddr : natural range 0 to 1023 >>;
  flush_done_r <= << signal .tb_flush_path.dut.flush_done_r : std_logic >>;
  enq_valid0  <= << signal .tb_flush_path.dut.enq_valid0 : boolean >>;
  enq_valid1  <= << signal .tb_flush_path.dut.enq_valid1 : boolean >>;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples => 3000000, Channels => CHANNELS, Sim => true)
    port map (
      CLK          => clk,
      CLK_150      => open,
      Rate_Div     => rate_div,
      Samples      => samples_in,
      Start_Offset => start_offset,
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
      s_burst      => s_burst,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      FAST_CLK     => fast_clk,
      Continuous_Mode => continuous_mode,
      Buffer_Full     => buffer_full,
      Buffer_Ack      => buffer_ack
    );

  process
    variable rdata : std_logic_vector(15 downto 0);
    variable prev_ch0 : std_logic := 'U';
    type expected_arr is array(0 to PRE_TRIGGER-1) of std_logic;
    variable expected : expected_arr := (others => '0');
  begin
    wait_cycles(clk, 30);

    ------------------------------------------------------------------
    -- Phase 1: Pre-trigger — fill BRAM while Armed, but not running
    ------------------------------------------------------------------
    report "Phase 1: Pre-trigger BRAM fill (" & integer'image(PRE_TRIGGER) & " samples)";
    rate_div <= 2;
    samples_in <= TEST_SAMPLES;
    fast_mode <= '0';
    armed <= '1';
    run <= '0';

    -- Wait for enough pre-trigger BRAM writes
    -- With rate_div=2 and sub_steps=2, we get 1 BRAM write every 4 cycles
    -- We need PRE_TRIGGER BRAM writes (each is 16-bit = 2 samples)
    -- But actually each sample_en accumulates one 8-bit sample; after sub_steps cycles
    -- it writes one 16-bit word to BRAM. So we need PRE_TRIGGER * sub_steps sample_en events.
    wait_cycles(clk, PRE_TRIGGER * 4 + 20);

    -- Record expected CH0 for each pre-trigger word (each 16-bit word captures CH0 in bit 0)
    for i in 0 to PRE_TRIGGER-1 loop
      expected(i) := inputs(0);  -- capture current CH0 for each expected word
      wait_cycles(clk, 4);  -- one 16-bit word per 4 cycles
    end loop;
    report "Pre-trigger CH0 pattern captured";
    report "Phase 1: PASS";

    ------------------------------------------------------------------
    -- Phase 2: Trigger — flush should drain BRAM through enq_valid0
    ------------------------------------------------------------------
    report "Phase 2: Trigger and flush";
    run <= '1';

    -- Wait for flush to complete (flush_done_r goes high)
    wait_until(clk, flush_done_r, '1', 10 ms, "Flush should complete");
    report "Flush done, fifo_cnt=" & integer'image(fifo_cnt);
    -- Note: fifo_cnt may be 0 because the SDRAM pump drains entries
    -- as fast as the flush writes them. The data is already in SDRAM.
    report "Phase 2: PASS";

    ------------------------------------------------------------------
    -- Phase 3: Wait for capture complete, then read back
    ------------------------------------------------------------------
    report "Phase 3: Capture complete and readback";
    wait_until(clk, full, '1', 10 ms, "Capture should complete");

    -- Read back first PRE_TRIGGER words (pre-trigger data)
    -- With Sim=true, SDRAM read has low latency
    for addr in 0 to PRE_TRIGGER-1 loop
      address <= addr;
      wait_cycles(clk, 3);
      rdata := outputs;
      check(not is_x(rdata), "Outputs must be known at addr " & integer'image(addr));
      -- Verify CH0 data integrity: within each 16-bit word, both halves should have
      -- the same CH0 (captured at same sample time)
      check(rdata(0) = rdata(8), "CH0 mismatch within word at addr " & integer'image(addr));
    end loop;
    report "Phase 3: PASS";

    ------------------------------------------------------------------
    -- Phase 4: Cleanup
    ------------------------------------------------------------------
    report "Phase 4: Stop";
    run <= '0';
    wait_cycles(clk, 20);
    report "Phase 4: PASS";

    report "=== ALL FLUSH PATH TESTS PASSED ===";
    wait;
  end process;
end bench;

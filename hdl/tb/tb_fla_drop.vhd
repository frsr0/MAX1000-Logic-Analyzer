-- Reproduce the sample-periodic write-side drop in deep capture.
-- Drives the REAL Fast_Logic_Analyzer_SDRAM with SEPARATE FAST_CLK (200 MHz)
-- and CLK/pclk (166.67 MHz) -- the domain interaction the existing testbenches
-- never exercise (tb_capture_path ties fast_clk = clk). Inputs is a free-running
-- counter so every captured sample is unique; after capture, the Sim-mode
-- Address/Outputs readout walks the SDRAM and we flag any sample whose value
-- breaks the smooth arithmetic progression -> a dropped/duplicated write.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_fla_drop is
  generic (
    NSAMP    : natural := 4096;   -- spans ~2x the ~1958-sample period
    RATE_DIV : natural := 20      -- 200 MHz / 20 = 10 MHz sample rate
  );
end tb_fla_drop;

architecture bench of tb_fla_drop is
  signal clk      : std_logic := '0';   -- pclk / SDRAM core: 166.67 MHz (6 ns)
  signal fastclk  : std_logic := '0';   -- sample clock: 200 MHz (5 ns)
  signal sdram_clk_model : std_logic := '0';

  signal rdiv       : natural range 1 to 500000000 := RATE_DIV;
  signal samples_in : natural range 1 to 3000000 := NSAMP;
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

  type word_arr is array (natural range <>) of std_logic_vector(15 downto 0);
  signal store : word_arr(0 to NSAMP-1) := (others => (others => '0'));

  signal p_valid : std_logic;
  signal p_ready : std_logic;
  signal p_addr  : std_logic_vector(21 downto 0);
  signal p_data  : std_logic_vector(15 downto 0);
begin

  -- Probe the write-pump handshake to see exactly which address gets which data.
  p_valid <= << signal .tb_fla_drop.dut.cap_stream_valid : std_logic >>;
  p_ready <= << signal .tb_fla_drop.dut.cap_stream_ready : std_logic >>;
  p_addr  <= << signal .tb_fla_drop.dut.cap_stream_addr  : std_logic_vector(21 downto 0) >>;
  p_data  <= << signal .tb_fla_drop.dut.cap_stream_data  : std_logic_vector(15 downto 0) >>;

  wlog : process(clk)
    variable c : natural := 0;
  begin
    if rising_edge(clk) then
      if p_valid = '1' and p_ready = '1' and c < 30 then
        report "WRITE addr=" & integer'image(to_integer(unsigned(p_addr))) &
               " data=" & integer'image(to_integer(unsigned(p_data)));
        c := c + 1;
      end if;
    end if;
  end process;

  -- 200 MHz and 166.67 MHz from the same time base (beat repeats every 30 ns).
  clk     <= not clk     after 3.0 ns;
  fastclk <= not fastclk after 2.5 ns;
  sdram_clk_model <= transport sdram_clk after 1.5 ns;

  -- Free-running input counter (one unique 16-bit value per FAST_CLK).
  process(fastclk)
  begin
    if rising_edge(fastclk) then
      inputs <= std_logic_vector(unsigned(inputs) + 1);
    end if;
  end process;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples   => 3000000,
      Channels      => 16,
      Sim           => true,
      FAST_SPEED    => true,
      CLK_Frequency => 166666667,
      SDRAM_CLK_HZ  => 166666667,
      SAMPLE_CLK_HZ => 200000000
    )
    port map (
      CLK          => clk,
      SDRAM_CLK_IN => '0',
      CLK_150      => open,
      Rate_Div     => rdiv,
      Samples      => samples_in,
      Start_Offset => 0,
      Run          => run,
      Full         => full,
      Inputs       => inputs,
      Address      => address,
      Outputs      => outputs,
      sdram_addr   => sdram_addr,
      sdram_ba     => sdram_ba,
      sdram_cas_n  => sdram_cas_n,
      sdram_dq     => sdram_dq,
      sdram_dqm    => sdram_dqm,
      sdram_ras_n  => sdram_ras_n,
      sdram_we_n   => sdram_we_n,
      sdram_cke    => sdram_cke,
      sdram_cs_n   => sdram_cs_n,
      sdram_clk    => sdram_clk,
      Status       => status,
      s_burst      => s_burst,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      FAST_CLK     => fastclk,
      Continuous_Mode => '0'
    );

  SDRAM : entity work.sdram_pin_model
    generic map (CL => 3)
    port map (
      clk   => sdram_clk_model,
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

  main : process
    variable rd     : std_logic_vector(15 downto 0);
    variable prevv  : integer;
    variable curv   : integer;
    variable d      : integer;
    variable mode_d : integer := 0;
    variable anom   : integer := 0;
    variable first_anom : integer := -1;
    variable last_anom  : integer := -1;
    variable gap_prev   : integer := -1;
  begin
    rdiv       <= RATE_DIV;
    samples_in <= NSAMP;
    fast_mode  <= '1';
    armed      <= '1';
    wait_cycles(clk, 40);
    run <= '1';

    wait until rising_edge(full);
    report "capture Full at " & integer'image(now / 1 ns) & " ns; reading back";
    run <= '0';
    wait_cycles(clk, 20);

    -- Sim-mode address-driven readout into Outputs (fixed latency latch).
    for a in 0 to NSAMP-1 loop
      address <= a;
      wait_cycles(clk, 22);
      store(a) <= outputs;
    end loop;
    wait_cycles(clk, 4);

    -- The per-sample increment equals RATE_DIV (counter steps once per FAST_CLK,
    -- sampled every RATE_DIV ticks). A clean stream is a constant-step ramp; a
    -- dropped/duplicated write breaks the step. Find the dominant step, flag
    -- breaks, and report their spacing (the period we are hunting). Skip the
    -- first few samples (capture start-up / the addr-0 read-latch warm-up are
    -- TB-readout artifacts, not capture drops).
    mode_d := RATE_DIV;
    for a in 4 to NSAMP-1 loop
      prevv := to_integer(unsigned(store(a-1)));
      curv  := to_integer(unsigned(store(a)));
      d := (curv - prevv) mod 65536;
      if d /= mode_d then
        anom := anom + 1;
        if first_anom < 0 then first_anom := a; end if;
        if gap_prev >= 0 then
          report "  anomaly at sample " & integer'image(a) &
                 " (step=" & integer'image(d) & ") spacing=" &
                 integer'image(a - gap_prev);
        else
          report "  anomaly at sample " & integer'image(a) &
                 " (step=" & integer'image(d) & ")";
        end if;
        gap_prev := a;
        last_anom := a;
      end if;
    end loop;

    report "TOTAL anomalies = " & integer'image(anom) &
           "  (NSAMP=" & integer'image(NSAMP) & ", step=" & integer'image(mode_d) & ")";
    if anom = 0 then
      report "CLEAN: no dropped writes" severity note;
    else
      report "DROPS PRESENT" severity note;
    end if;
    std.env.finish;
    wait;
  end process;

end bench;

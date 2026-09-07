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
    RATE_DIV : natural := 20;     -- 200 MHz / 20 = 10 MHz sample rate
    PHASE_PS : natural := 0;      -- initial phase offset of fastclk vs clk (ps)
    DO_READBACK : boolean := true -- run the (slow, unreliable) legacy readback
  );
end tb_fla_drop;

architecture bench of tb_fla_drop is
  signal clk      : std_logic := '0';   -- pclk / SDRAM core: 166.67 MHz (6 ns)
  signal fastclk  : std_logic := '0';   -- sample clock: 200 MHz (5 ns)
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

  type word_arr is array (natural range <>) of std_logic_vector(15 downto 0);
  signal store : word_arr(0 to NSAMP-1) := (others => (others => '0'));

begin
  -- 200 MHz and 166.67 MHz from the same time base (beat repeats every 30 ns).
  -- fastclk is offset by PHASE_PS to emulate the per-capture FAST_CLK/pclk phase
  -- relationship the PLL/Run-reset establishes on real hardware. Sweeping it
  -- explores whether an unlucky phase reproduces the rare HW write drop.
  clk <= not clk after 3.0 ns;
  fastclk_gen : process
  begin
    wait for PHASE_PS * 1 ps;
    loop
      fastclk <= '1'; wait for 2.5 ns;
      fastclk <= '0'; wait for 2.5 ns;
    end loop;
  end process;

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
      Armed        => armed,
      Fast_Mode    => fast_mode,
      FAST_CLK     => fastclk,
      Continuous_Mode => '0'
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
    assert DO_READBACK
      report "tb_fla_drop requires end-to-end Address/Outputs verification"
      severity failure;
    rdiv       <= RATE_DIV;
    samples_in <= NSAMP;
    fast_mode  <= '1';
    armed      <= '1';
    wait_cycles(clk, 40);
    run <= '1';

    wait_until(clk, full, '1', 10 ms, "Capture should complete (Full asserted)");
    report "capture Full at " & integer'image(now / 1 ns) & " ns; reading back";
    run <= '0';
    wait_cycles(clk, 20);

    -- Sim-mode address-driven readout into Outputs (fixed latency latch).
    if DO_READBACK then
      for a in 0 to NSAMP-1 loop
        address <= a;
        wait_cycles(clk, 22);
        store(a) <= outputs;
      end loop;
      wait_cycles(clk, 4);
    end if;

    -- The per-sample increment equals RATE_DIV (counter steps once per FAST_CLK,
    -- sampled every RATE_DIV ticks). A clean stream is a constant-step ramp; a
    -- dropped/duplicated write breaks the step. Find the dominant step, flag
    -- breaks, and report their spacing (the period we are hunting). Skip the
    -- first few samples (capture start-up / the addr-0 read-latch warm-up are
    -- TB-readout artifacts, not capture drops).
    mode_d := RATE_DIV;
    for a in 4 to NSAMP-1 loop
      next when not DO_READBACK;
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

    -- Classify the first anomaly: dump stored values around it, then RE-READ
    -- the same SDRAM address via the readout path to see if it is stable
    -- (write-side: SDRAM content wrong) or varies (readout artifact).
    if first_anom >= 2 then
      for a in first_anom - 2 to first_anom + 2 loop
        if a >= 0 and a < NSAMP then
          report "  near[" & integer'image(a) & "] = " &
                 integer'image(to_integer(unsigned(store(a))));
        end if;
      end loop;
      address <= first_anom;
      wait_cycles(clk, 22);
      report "  RE-READ[" & integer'image(first_anom) & "] = " &
             integer'image(to_integer(unsigned(outputs)));
      address <= first_anom;
      wait_cycles(clk, 22);
      report "  RE-READ#2[" & integer'image(first_anom) & "] = " &
             integer'image(to_integer(unsigned(outputs)));
    end if;

    report "TOTAL anomalies = " & integer'image(anom) &
           "  (NSAMP=" & integer'image(NSAMP) & ", step=" & integer'image(mode_d) & ")";
    if anom = 0 then
      report "CLEAN: no dropped writes" severity note;
    else
      report "DROPS PRESENT" severity failure;
    end if;
    std.env.finish;
    wait;
  end process;

end bench;

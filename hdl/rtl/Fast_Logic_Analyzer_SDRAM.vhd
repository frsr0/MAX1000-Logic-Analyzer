library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
library lpm;
use lpm.lpm_components.all;

entity Fast_Logic_Analyzer_SDRAM is
  generic (
    Max_Samples : natural := 3000000;
    Channels    : natural range 1 to 16 := 16;
    Sim         : boolean := false;
    FAST_SPEED  : boolean := false;
    CLK_Frequency : natural := 100_000_000;
    SAMPLE_CLK_HZ : natural := 200_000_000;
    Write_Latency : natural := 10;
    Read_Latency  : natural := 3;
    Page_Latency  : natural := 3
  );
port (
  CLK          : in  std_logic;
  CLK_150      : out std_logic;
  Rate_Div     : in  natural range 1 to 500000000 := 12;
  Samples      : in  natural range 1 to Max_Samples := Max_Samples;
  Start_Offset : in  natural range 0 to Max_Samples := 0;
  Run          : in  std_logic := '0';
  Full         : out std_logic := '0';
  Inputs       : in  std_logic_vector(Channels-1 downto 0) := (others => '0');
  Address      : in  natural range 0 to Max_Samples := 0;
  Outputs      : out std_logic_vector(15 downto 0);
  sdram_addr   : out std_logic_vector(11 downto 0);
  sdram_ba     : out std_logic_vector(1 downto 0);
  sdram_cas_n  : out std_logic;
  sdram_dq     : inout std_logic_vector(15 downto 0) := (others => '0');
  sdram_dqm    : out std_logic_vector(1 downto 0);
  sdram_ras_n  : out std_logic;
  sdram_we_n   : out std_logic;
   sdram_cke    : out std_logic := '1';
   sdram_cs_n   : out std_logic := '0';
   sdram_clk    : out std_logic;
    Status       : out std_logic_vector(7 downto 0) := (others => '0');
    s_burst      : out std_logic := '0';
    Armed        : in  std_logic := '0';
    Fast_Mode    : in  std_logic := '0';
    FAST_CLK     : in  std_logic := '0';
    -- Double-buffer control
    Continuous_Mode : in std_logic := '0';
    Buffer_Full     : out std_logic_vector(2 downto 0) := (others => '0');
    Buffer_Ack      : in std_logic_vector(2 downto 0) := (others => '0');
    Analog_Frame_Data : in std_logic_vector(127 downto 0) := (others => '0');
    Analog_Frame_Len  : in natural range 1 to 14 := 1;
    Analog_Stream_Mode : in std_logic := '0'
  );
end Fast_Logic_Analyzer_SDRAM;

architecture rtl of Fast_Logic_Analyzer_SDRAM is

  constant sub_steps : natural := 16 / Channels;

  signal pclk : std_logic;

  signal s_addr  : std_logic_vector(21 downto 0) := (others => '0');
  signal s_wr    : std_logic := '0';
  signal s_wdata : std_logic_vector(15 downto 0) := (others => '0');
  signal s_rd    : std_logic := '0';
  signal s_rdata : std_logic_vector(15 downto 0) := (others => '0');
   signal s_rvalid: std_logic := '0';
   signal s_burst_i : std_logic := '0';
  signal full_i      : std_logic := '0';
  signal run_sync1   : std_logic := '0';
  signal run_sync2   : std_logic := '0';
  signal samples_div_p  : natural range 0 to Max_Samples := 0;
  signal samples_div6_p : natural range 0 to Max_Samples := 0;
  signal samples_d1   : natural range 0 to Max_Samples := 0;
  signal samples_div  : natural range 0 to Max_Samples := 0;
  signal samples_div6 : natural range 0 to Max_Samples := 0;

  -- Pipelined divide-by-3 (LPM_DIVIDE with 4-stage pipeline)
  signal lpm_numer : std_logic_vector(21 downto 0) := (others => '0');
  signal lpm_quot  : std_logic_vector(21 downto 0) := (others => '0');

  -- Old FIFO replaced by dcfifo. Keep fifo_cnt for external visibility.
  signal fifo_cnt      : natural range 0 to 64 := 0;
  signal buf_limit_r   : natural range 0 to Max_Samples := 0;
  signal buf_last_r    : natural range 0 to Max_Samples := 0;
  signal buf_base0_r   : natural range 0 to Max_Samples := 0;
  signal buf_base1_r   : natural range 0 to Max_Samples := 0;
  signal buf_base2_r   : natural range 0 to Max_Samples := 0;

  -- Triple-buffer state
  signal buf_sel    : std_logic_vector(1 downto 0) := "00";
  signal buf_full   : std_logic_vector(2 downto 0) := (others => '0');
  signal full_pending : std_logic := '0';
  signal full_clr_pending : std_logic := '0';

  -- Per-buffer remaining-word count
  signal buf_rem_0      : natural range 0 to Max_Samples := 0;
  signal buf_rem_1      : natural range 0 to Max_Samples := 0;
  signal buf_rem_2      : natural range 0 to Max_Samples := 0;
  signal buf_rem_single : natural range 0 to Max_Samples := 0;

  -- Pipeline registers: pre-compute buf_rem decrements
  signal brem0_dec : natural range 0 to Max_Samples := 0;
  signal brem1_dec : natural range 0 to Max_Samples := 0;
  signal brem2_dec : natural range 0 to Max_Samples := 0;
  signal brem_single_dec : natural range 0 to Max_Samples := 0;

  -- Registered run-edge event detection: breaks run_r → process_5~0 → burst_rem →
  -- fifo_tail → fifo_head → Add18 → LessThan18 → fifo_head_r critical path.
  -- run_level_r replaces the run_r process variable.
  signal run_level_r : std_logic := '0';
  signal run_edge_r  : std_logic := '0';
  signal run_start_r : std_logic := '0';
  signal run_stop_r  : std_logic := '0';

  -- Registered sample-rate divider: pre-computed rate_div_r - 1 breaks the
  -- Rate_Div → Add0 (28-bit subtractor) → LessThan0 → cnt carry chain path.
  -- Down-counter uses cnt = 0 (fast NOR gate) instead of cnt >= threshold
  -- (slow 28-bit comparator). Rate_Div changes only when the user sets
  -- the sample rate (before capture starts), so 1-cycle latency is harmless.
  signal rate_div_r    : natural range 1 to 500000000 := 12;
  signal rate_div_m1_r : natural range 0 to 500000000 := 11;

  -- FAST_CLK domain signals (2FF CDC + async FIFO)
  signal sdram_busy : std_logic := '0';

  constant MAX_RATE_DIV : natural := 500_000_000;

  -- Config handshake: CLK -> FAST_CLK
  signal cfg_rate_div  : natural range 1 to MAX_RATE_DIV := 12;
  signal cfg_samples   : natural range 1 to 3000000 := 3000000;
  signal cfg_valid_toggle : std_logic := '0';
  signal cfg_ack_s1    : std_logic := '0';
  signal cfg_ack_s2    : std_logic := '0';
  signal cfg_ack_edge  : std_logic := '0';

  -- Config handshake: FAST_CLK domain
  signal cfg_rate_div_f  : natural range 1 to MAX_RATE_DIV := 12;
  signal cfg_rate_reload_f : natural range 0 to MAX_RATE_DIV := 11;
  signal cfg_samples_f   : natural range 1 to 3000000 := 3000000;
  signal cfg_valid_s1    : std_logic := '0';
  signal cfg_valid_s2    : std_logic := '0';
  signal cfg_valid_edge  : std_logic := '0';
  signal cfg_ack_toggle  : std_logic := '0';

  signal rate_div_m1_f : natural range 0 to MAX_RATE_DIV := 11;

  -- Registered sample counter and tick (replaces variable cnt).
  -- cnt_s is a registered signal; sample_tick_r is asserted for one cycle
  -- when the counter reaches zero, breaking the cnt=0 -> packing -> BRAM/FIFO
  -- path into (cnt -> zero -> tick) on cycle N and (tick -> pack) on cycle N+1.
  signal cnt_s         : natural range 0 to MAX_RATE_DIV := 0;
  signal sample_tick_r : std_logic := '0';

  signal run_f_s1  : std_logic := '0';
  signal run_f_s2   : std_logic := '0';
  signal Inputs_r   : std_logic_vector(Channels-1 downto 0) := (others => '0');
  signal Armed_s1   : std_logic := '0';
  signal Armed_f    : std_logic := '0';
  signal run_f_level : std_logic := '0';
  signal fifo_overflow_f  : std_logic := '0';
  signal overflow_toggle  : std_logic := '0';
  signal overflow_t_s1    : std_logic := '0';
  signal overflow_t_s2    : std_logic := '0';
  signal overflow_t_s3    : std_logic := '0';
  signal overflow_clk     : std_logic := '0';
  signal sample_remaining : natural range 0 to 3000000 := 0;
  signal run_stop_overflow : std_logic := '0';
  signal status_overflow   : std_logic := '0';

  constant AFIFO_DEPTH : natural := 4096;
  constant AFIFO_WIDTH : natural := 16;
  constant AFIFO_WIDTHU : natural := 12;
  signal fifo_wdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_wr    : std_logic := '0';
  signal fifo_wrfull : std_logic := '0';
  signal fifo_rdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_rd    : std_logic := '0';
  signal fifo_rdempty : std_logic := '0';

  -- Pre-trigger BRAM (dual-port M9K, FAST_CLK write / CLK read)
  constant BRAM_SIZE : natural := 1024;
  type bram_array is array(0 to BRAM_SIZE-1) of std_logic_vector(15 downto 0);
  signal bram : bram_array := (others => (others => '0'));
  attribute ramstyle : string;
  attribute ramstyle of bram : signal is "M9K, no_rw_check";
  signal bram_wren   : std_logic := '0';
  signal bram_waddr  : natural range 0 to BRAM_SIZE-1 := 0;
  signal bram_wdata  : std_logic_vector(15 downto 0) := (others => '0');
  -- BRAM read port (FAST_CLK domain): used during flush-to-FIFO
  signal bram_raddr_f  : natural range 0 to BRAM_SIZE-1 := 0;
  signal bram_rdata_f  : std_logic_vector(15 downto 0) := (others => '0');

  component SDRAM_Interface is
  generic (
    Sim : boolean := false;
    CLK_Frequency : natural := 96000000;
    Write_Latency : natural := 10;
    Read_Latency  : natural := 3;
    Page_Latency  : natural := 3
  );
  port (
    CLK          : in  std_logic;
    Reset        : in  std_logic := '0';
    CLK_150_Out  : out std_logic;
    Address      : in  std_logic_vector(21 downto 0) := (others => '0');
    Write_Enable : in  std_logic := '0';
    Write_Data   : in  std_logic_vector(15 downto 0) := (others => '0');
    Burst        : in  std_logic := '0';
    Read_Enable  : in  std_logic := '0';
    Read_Data    : out std_logic_vector(15 downto 0) := (others => '0');
    Read_Valid   : out std_logic := '0';
    Busy         : out std_logic := '0';
    Idle         : out std_logic := '0';
    sdram_addr   : out std_logic_vector(11 downto 0);
    sdram_ba     : out std_logic_vector(1 downto 0);
    sdram_cas_n  : out std_logic;
    sdram_cke    : out std_logic := '1';
    sdram_cs_n   : out std_logic := '0';
    sdram_dq     : inout std_logic_vector(15 downto 0) := (others => '0');
    sdram_dqm    : out std_logic_vector(1 downto 0);
    sdram_ras_n  : out std_logic;
    sdram_we_n   : out std_logic;
    sdram_clk    : out std_logic
  );
  end component;

  component dcfifo
  generic (
    lpm_width       : natural;
    lpm_widthu      : natural;
    lpm_numwords    : natural;
    lpm_showahead   : string;
    lpm_type        : string;
    rdsync_delaypipe : natural;
    wrsync_delaypipe : natural;
    intended_device_family : string
  );
  port (
    data     : in  std_logic_vector(lpm_width-1 downto 0);
    wrreq    : in  std_logic;
    wrclk    : in  std_logic;
    rdreq    : in  std_logic;
    rdclk    : in  std_logic;
    q        : out std_logic_vector(lpm_width-1 downto 0);
    rdempty  : out std_logic;
    wrfull   : out std_logic;
    wrusedw  : out std_logic_vector(lpm_widthu-1 downto 0);
    rdusedw  : out std_logic_vector(lpm_widthu-1 downto 0)
  );
  end component;

begin

  -- 4-stage pipelined divide-by-3 (replaces combinatorial /3 with 38 LUT levels)
  u_div6 : lpm_divide
    generic map (
      LPM_WIDTHN => 22,
      LPM_WIDTHD => 2,
      LPM_NREPRESENTATION => "UNSIGNED",
      LPM_DREPRESENTATION => "UNSIGNED",
      LPM_PIPELINE => 4
    )
    port map (
      clock    => CLK,
      numer    => lpm_numer,
      denom    => "11",
      quotient => lpm_quot,
      remain   => open
    );

  -- Pipeline: register input, LPM divides over 4 cycles, register output
  process(CLK) begin
    if rising_edge(CLK) then
      samples_d1   <= Samples;
      lpm_numer    <= std_logic_vector(to_unsigned(samples_d1, 22));
      samples_div  <= samples_d1;
      samples_div6 <= to_integer(unsigned(lpm_quot));
    end if;
  end process;

  CLK_150 <= pclk;

  -- 2FF synchronizer: Run from CLK domain into pclk domain
  process(pclk)
  begin
    if rising_edge(pclk) then
      run_sync1 <= Run;
      run_sync2 <= run_sync1;
    end if;
  end process;

  -- Registered run-edge event detection: produces single-cycle pulses for
  -- run start, run stop, and any edge. run_level_r replaces the run_r variable.
  -- This breaks the run_r → process_5~0 → burst_rem → fifo_tail → fifo_head →
  -- Add18 → LessThan18 → fifo_head_r timing path by registering the edge decode
  -- in a separate process with minimal fanout.
  process(pclk)
  begin
    if rising_edge(pclk) then
      run_edge_r  <= run_sync2 xor run_level_r;
      run_start_r <= run_sync2 and not run_level_r;
      run_stop_r  <= (not run_sync2) and run_level_r;
      run_level_r <= run_sync2;
    end if;
  end process;

  -- Config latch: on run start, sample Rate_Div and Samples into cfg_*.
  -- Toggle cfg_valid_toggle so the FAST_CLK domain knows config is stable.
  -- The FAST_CLK domain acks by toggling cfg_ack_toggle (detected via 2FF).
  process(pclk)
  begin
    if rising_edge(pclk) then
      cfg_ack_s1 <= cfg_ack_toggle;
      cfg_ack_s2 <= cfg_ack_s1;
      cfg_ack_edge <= cfg_ack_s1 xor cfg_ack_s2;
      if run_edge_r = '1' and run_start_r = '1' then
        cfg_rate_div  <= Rate_Div;
        cfg_samples   <= Samples;
        cfg_valid_toggle <= not cfg_valid_toggle;
      end if;
    end if;
  end process;

  -- Register Rate_Div into pclk domain and pre-compute rate_div_r - 1 to
  -- break the 28-bit carry chain comparator path (rate_div_r → Add0 →
  -- LessThan0 → cnt). Down-counter uses cnt = 0 (fast NOR) instead of
  -- cnt >= rate_div_r (slow 28-bit comparator).
  process(pclk)
  begin
    if rising_edge(pclk) then
      rate_div_r    <= Rate_Div;
      if Rate_Div > 1 then
        rate_div_m1_r <= Rate_Div - 1;
      else
        rate_div_m1_r <= 0;
      end if;
    end if;
  end process;

  -- Re-register CLK-domain divide results into pclk domain; pre-compute buffer limits
  process(pclk)
  begin
    if rising_edge(pclk) then
      samples_div_p  <= samples_div;
      samples_div6_p <= samples_div6;
      buf_limit_r    <= samples_div6;
      if samples_div6 > 0 then
        buf_last_r <= samples_div6 - 1;
      else
        buf_last_r <= 0;
      end if;
      buf_base0_r    <= 0;
      buf_base1_r    <= samples_div6;
      buf_base2_r    <= samples_div6 + samples_div6;
    end if;
  end process;

  -- Pipeline registers: pre-compute buf_rem - 1 (break 21-bit subtractor chain)
  -- Registered in a separate process so the main process only drives a MUX.
  -- The subtractor output is available at the START of the next cycle, before
  -- the main process evaluates its combinatorial logic.
  process(pclk)
  begin
    if rising_edge(pclk) then
      brem0_dec <= buf_rem_0 - 1;
      brem1_dec <= buf_rem_1 - 1;
      brem2_dec <= buf_rem_2 - 1;
      brem_single_dec <= buf_rem_single - 1;
    end if;
  end process;

  -- ============================================================
  -- FAST_CLK domain (200 MHz speed / 120 MHz normal)
  -- ============================================================

  -- Shared processes (both speed and normal mode):

  -- Config handshake: FAST_CLK domain detects cfg_valid_toggle edge,
  -- latches config, acks back via cfg_ack_toggle.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      cfg_valid_s1 <= cfg_valid_toggle;
      cfg_valid_s2 <= cfg_valid_s1;
      cfg_valid_edge <= cfg_valid_s1 xor cfg_valid_s2;
      if cfg_valid_edge = '1' then
        cfg_rate_div_f  <= cfg_rate_div;
        cfg_samples_f   <= cfg_samples;
        if cfg_rate_div > 1 then
          cfg_rate_reload_f <= cfg_rate_div - 1;
        else
          cfg_rate_reload_f <= 0;
        end if;
        cfg_ack_toggle <= not cfg_ack_toggle;
      end if;
    end if;
  end process;

  -- Run signal CDC: run_sync2 (CLK domain) -> FAST_CLK domain
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      run_f_s1 <= run_sync2;
      run_f_s2 <= run_f_s1;
      run_f_level <= run_f_s2;
    end if;
  end process;

  -- Armed CDC: CLK domain -> FAST_CLK domain (2FF)
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      Armed_s1 <= Armed;
      Armed_f  <= Armed_s1;
    end if;
  end process;

  -- BRAM write port (shared)
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if bram_wren = '1' then
        bram(bram_waddr) <= bram_wdata;
      end if;
    end if;
  end process;

  -- Overflow flag CDC: FAST_CLK domain -> CLK domain (toggle synchronizer)
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if fifo_overflow_f = '1' then
        overflow_toggle <= not overflow_toggle;
      end if;
    end if;
  end process;

  -- ============================================================
  -- Speed mode (200 MHz): 3-stage pipeline, no divider, no flush
  -- ============================================================
  gen_fast_speed : if FAST_SPEED generate
    signal sample_word_r  : std_logic_vector(Channels-1 downto 0) := (others => '0');
    signal capture_en_r   : std_logic := '0';
    signal pretrig_en_r   : std_logic := '0';
    signal bram_wp_r      : natural range 0 to BRAM_SIZE-1 := 0;
    signal bram_cnt_r     : natural range 0 to BRAM_SIZE := 0;
    signal sample_div_cnt_r : natural range 0 to MAX_RATE_DIV := 0;
    signal sample_tick_r  : std_logic := '0';
    signal sample_rem_nonzero_r : std_logic := '0';
  begin
    -- Stage 0: sample pins
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        sample_word_r <= Inputs;
      end if;
    end process;

    -- Stage 1: control decode
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        capture_en_r <= run_f_level;
        pretrig_en_r <= Armed_f and not run_f_level;
      end if;
    end process;

    -- Stage 2a: rate divider counter (free-running when capture active)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_valid_edge = '1' then
          sample_div_cnt_r <= 0;
        elsif capture_en_r = '1' and sample_rem_nonzero_r = '1' then
          if sample_div_cnt_r = 0 then
            sample_div_cnt_r <= cfg_rate_reload_f;
          else
            sample_div_cnt_r <= sample_div_cnt_r - 1;
          end if;
        end if;
      end if;
    end process;

    -- Stage 2b: sample tick (pipelined one cycle after divider reaches zero)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        sample_tick_r <= '0';
        if capture_en_r = '1' and sample_rem_nonzero_r = '1' and sample_div_cnt_r = 0 then
          sample_tick_r <= '1';
        end if;
      end if;
    end process;

    -- Stage 2c: sample-remaining non-zero flag (pipelined, avoids 22-bit >0 in write path)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_valid_edge = '1' then
          sample_rem_nonzero_r <= '1';
        elsif fifo_wr = '1' and sample_remaining <= 2 then
          sample_rem_nonzero_r <= '0';
        end if;
      end if;
    end process;

    -- Stage 2d: BRAM/FIFO write (uses pipelined flags, only 1-bit compares)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        fifo_wr <= '0';
        bram_wren <= '0';

        if cfg_valid_edge = '1' then
          sample_remaining <= cfg_samples_f;
          fifo_overflow_f <= '0';
          bram_wp_r <= 0;
          bram_cnt_r <= 0;
        end if;

        if fifo_overflow_f = '0' then
          if pretrig_en_r = '1' then
            bram_waddr <= bram_wp_r;
            bram_wdata <= sample_word_r;
            bram_wren  <= '1';
            if bram_wp_r = BRAM_SIZE-1 then
              bram_wp_r <= 0;
            else
              bram_wp_r <= bram_wp_r + 1;
            end if;
            if bram_cnt_r < BRAM_SIZE then
              bram_cnt_r <= bram_cnt_r + 1;
            end if;

          elsif capture_en_r = '1' and sample_tick_r = '1' then
            if fifo_wrfull = '0' then
              fifo_wdata <= sample_word_r;
              fifo_wr <= '1';
              sample_remaining <= sample_remaining - 1;
            end if;
            if fifo_wrfull = '1' then
              fifo_overflow_f <= '1';
            end if;
          end if;
        end if;
      end if;
    end process;
  end generate;

  -- ============================================================
  -- Normal mode (120 MHz): sample divider + input packer + flush FSM
  -- ============================================================
  gen_fast_normal : if not FAST_SPEED generate
  begin
    -- Pre-compute rate_div - 1 for the fast down-counter
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_rate_div_f > 1 then
          rate_div_m1_f <= cfg_rate_div_f - 1;
        else
          rate_div_m1_f <= 0;
        end if;
      end if;
    end process;

    -- Fast capture process: runs at 120 MHz on FAST_CLK
    -- Samples Inputs, packs into 16-bit words.
    -- When Armed and pre-trigger: writes to circular BRAM.
    -- On cfg_valid_edge (trigger/starts): flushes BRAM to async FIFO,
    --   then pushes live samples until cfg_samples_f reached.
    process(FAST_CLK)
      variable step_r    : natural range 0 to sub_steps := 0;
      variable wbuf      : std_logic_vector(31 downto 0) := (others => '0');
      variable bram_wp   : natural range 0 to BRAM_SIZE-1 := 0;
      variable bram_cnt  : natural range 0 to BRAM_SIZE := 0;
      -- State: 0=pre-trigger, 1=flush BRAM to FIFO, 2=live capture
      variable state     : natural range 0 to 2 := 0;
      variable flush_raddr : natural range 0 to BRAM_SIZE-1 := 0;
      variable flush_rem   : natural range 0 to BRAM_SIZE := 0;
      variable sample_en_v : boolean := false;
    begin
      if rising_edge(FAST_CLK) then
        Inputs_r <= Inputs;
        fifo_wr <= '0';
        bram_wren <= '0';
        sample_tick_r <= '0';

        -- Registered sample tick generator (replaces variable cnt).
        -- Only advances in states that actually sample: pre-trigger (state 0
        -- when Armed) or live capture (state 2). Holds count during flush.
        sample_en_v := false;
        if fifo_overflow_f = '0' then
          if (state = 0 and Armed_f = '1' and run_f_level = '0') or state = 2 then
            sample_en_v := true;
          end if;
        end if;
        if cfg_valid_edge = '1' then
          cnt_s <= 0;
        elsif sample_en_v then
          if cnt_s = 0 then
            cnt_s <= rate_div_m1_f;
            sample_tick_r <= '1';
          else
            cnt_s <= cnt_s - 1;
          end if;
        end if;

        -- Config handshake edge: transition from pre-trigger to flush/capture
        if cfg_valid_edge = '1' then
          step_r := 0;
          wbuf := (others => '0');
          sample_remaining <= cfg_samples_f;
          fifo_overflow_f <= '0';
          if bram_cnt > 0 then
            if bram_wp >= bram_cnt then
              flush_raddr := bram_wp - bram_cnt;
            else
              flush_raddr := BRAM_SIZE - bram_cnt + bram_wp;
            end if;
            flush_rem := bram_cnt;
            state := 1;
          else
            state := 2;
          end if;

        -- State machine (only runs when not in overflow)
        elsif fifo_overflow_f = '0' then

          -- State 0: Pre-trigger — circular BRAM write
          if state = 0 then
            if Armed_f = '1' and run_f_level = '0' then
              if sample_tick_r = '1' then
                wbuf(((step_r + 1) * Channels) - 1 downto step_r * Channels) := Inputs_r;
                if step_r = sub_steps - 1 then
                  bram_waddr <= bram_wp;
                  bram_wdata <= wbuf(15 downto 0);
                  bram_wren <= '1';
                  if bram_wp = BRAM_SIZE-1 then bram_wp := 0;
                  else bram_wp := bram_wp + 1; end if;
                  if bram_cnt < BRAM_SIZE then bram_cnt := bram_cnt + 1; end if;
                  step_r := 0;
                else
                  step_r := step_r + 1;
                end if;
              end if;
            end if;

          -- State 1: Flush BRAM to async FIFO (pre-trigger samples first)
          elsif state = 1 then
            if flush_rem > 0 then
              if fifo_wrfull = '0' then
                bram_raddr_f <= flush_raddr;
                -- Skip write on first cycle (BRAM read is registered)
                if flush_rem < bram_cnt then
                  fifo_wdata <= bram_rdata_f;
                  fifo_wr <= '1';
                  sample_remaining <= sample_remaining - 1;
                end if;
                if flush_raddr = BRAM_SIZE-1 then flush_raddr := 0;
                else flush_raddr := flush_raddr + 1; end if;
                flush_rem := flush_rem - 1;
              end if;
            else
              state := 2;
            end if;

          -- State 2: Live capture — push samples to async FIFO
          else
            if sample_tick_r = '1' then
              wbuf(((step_r + 1) * Channels) - 1 downto step_r * Channels) := Inputs_r;
              if step_r = sub_steps - 1 then
                if fifo_wrfull = '0' and sample_remaining /= 0 then
                  fifo_wdata <= wbuf(15 downto 0);
                  fifo_wr <= '1';
                  sample_remaining <= sample_remaining - 1;
                end if;
                if fifo_wrfull = '1' or sample_remaining <= 1 then
                  fifo_overflow_f <= '1';
                end if;
                step_r := 0;
              else
                step_r := step_r + 1;
              end if;
            end if;
          end if;
        end if;
      end if;
    end process;

    -- BRAM read port (FAST_CLK domain): used during flush-to-FIFO
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        bram_rdata_f <= bram(bram_raddr_f);
      end if;
    end process;
  end generate;

  process(pclk)
  begin
    if rising_edge(pclk) then
      overflow_t_s1 <= overflow_toggle;
      overflow_t_s2 <= overflow_t_s1;
      overflow_t_s3 <= overflow_t_s2;
      overflow_clk <= overflow_t_s2 xor overflow_t_s3;
    end if;
  end process;

  -- Async FIFO: dcfifo bridges FAST_CLK (write) and pclk (read)
  afifo : dcfifo
    generic map (
      lpm_width       => AFIFO_WIDTH,
      lpm_widthu      => AFIFO_WIDTHU,
      lpm_numwords    => AFIFO_DEPTH,
      lpm_showahead   => "OFF",
      lpm_type        => "dcfifo",
      rdsync_delaypipe => 3,
      wrsync_delaypipe => 3,
      intended_device_family => "MAX 10"
    )
    port map (
      data     => fifo_wdata,
      wrreq    => fifo_wr,
      wrclk    => FAST_CLK,
      rdreq    => fifo_rd,
      rdclk    => pclk,
      q        => fifo_rdata,
      rdempty  => fifo_rdempty,
      wrfull   => fifo_wrfull
    );

  -- Main: SDRAM write pump + buffer management + readout
  -- Runs on pclk (96 MHz). Reads 16-bit sample words from async FIFO,
  -- assigns SDRAM addresses, manages triple-buffer continuous mode.
  process (pclk)
    variable rd_mode : boolean := true;
    variable read_addr : natural := 0;
    variable waddr_0   : natural range 0 to Max_Samples := 0;
    variable waddr_1   : natural range 0 to Max_Samples := 0;
    variable waddr_2   : natural range 0 to Max_Samples := 0;
    variable a_reg   : natural range 0 to Max_Samples := Max_Samples;
    variable wip        : boolean := false;
    variable wr_cnt   : natural range 0 to 3 := 0;
    variable wr_pend    : boolean := false;
    variable wr_pend_addr : std_logic_vector(21 downto 0) := (others => '0');
    variable wr_pend_data : std_logic_vector(15 downto 0) := (others => '0');
    variable rd_pend : std_logic := '0';
    variable write_addr : std_logic_vector(21 downto 0) := (others => '0');
  begin
    if rising_edge(pclk) then
      fifo_rd <= '0';
      s_wr <= '0';
      s_burst_i <= '0';

      -- Overflow from fast domain
      if overflow_clk = '1' then
        run_stop_overflow <= '1';
        status_overflow <= '1';
      end if;

      -- Buffer ack handling (evaluated every cycle)
      if Buffer_Ack(0) = '1' then
        buf_full(0) <= '0';
        buf_rem_0   <= buf_limit_r;
        if buf_sel = "00" and buf_full(1) = '1' then
          -- A was waiting to be written (B is full), reset pointer now
          waddr_0 := 0;
        end if;
      end if;
      if Buffer_Ack(1) = '1' then
        buf_full(1) <= '0';
        buf_rem_1   <= buf_limit_r;
        if buf_sel = "01" and buf_full(0) = '1' then
          -- B was waiting to be written (A is full), reset pointer now
          waddr_1 := 0;
        end if;
      end if;
      if Buffer_Ack(2) = '1' then
        buf_full(2) <= '0';
        buf_rem_2   <= buf_limit_r;
        if buf_sel = "10" and buf_full(1) = '1' then
          -- C was waiting to be written (B is full), reset pointer now
          waddr_2 := 0;
        end if;
      end if;
      -- Continuous mode backpressure handling
      if Continuous_Mode = '1' then
        if full_i = '1' and (Buffer_Ack(0) = '1' or Buffer_Ack(1) = '1' or Buffer_Ack(2) = '1') then
          full_clr_pending <= '1';
        end if;
        if full_clr_pending = '1' then
          full_i <= '0';
          full_pending <= '0';
          rd_mode := false;
          full_clr_pending <= '0';
        end if;
        if full_pending = '1' and fifo_rdempty = '1'
           and not wip and not wr_pend then
          full_i <= '1';
          full_pending <= '0';
          rd_mode := true;  -- enter readout so OLS can read completed buffer
        end if;
      end if;

      if run_edge_r = '1' or run_stop_overflow = '1' then
        waddr_0 := 0; waddr_1 := 0; waddr_2 := 0;
        buf_rem_0 <= buf_limit_r;
        buf_rem_1 <= buf_limit_r;
        buf_rem_2 <= buf_limit_r;
        buf_rem_single <= samples_div_p;
        rd_pend := '0';
        wr_pend := false; wip := false; wr_cnt := 0;
        buf_sel <= "00";
        buf_full(0) <= '0'; buf_full(1) <= '0'; buf_full(2) <= '0';
        full_i <= '0';
        full_pending <= '0'; full_clr_pending <= '0';
        run_stop_overflow <= '0';
        status_overflow <= '0';
        if run_stop_r = '1' then
          rd_mode := true;
        else
          rd_mode := false;
        end if;
        s_wr <= '0'; s_rd <= '0';

      else
      -- Normal capture/readout/write-pump logic (skipped on run-edge cycle)

      if rd_mode then
        -- READOUT (SDRAM only)
        read_addr := Address + Start_Offset;
        if read_addr /= a_reg then
          a_reg := read_addr;
          if read_addr < samples_div_p then
            s_addr <= std_logic_vector(to_unsigned(read_addr, 22));
            s_rd <= '1';
            rd_pend := '1';
          else
            s_rd <= '0';
            rd_pend := '0';
          end if;
        end if;
        if s_rvalid = '1' and rd_pend = '1' then
          Outputs <= s_rdata;
          s_rd <= '0';
          rd_pend := '0';
        elsif read_addr >= samples_div_p then
          Outputs <= (others => '0');
        end if;

      else
        -- CAPTURE: SDRAM write pump — drains async FIFO (live + flushed
        -- pre-trigger samples arrive via a single FIFO stream).

        if wr_pend then
          s_addr  <= wr_pend_addr;
          s_wdata <= wr_pend_data;
          s_wr    <= '1';
          wip     := true;
          wr_pend := false;

        elsif fifo_rdempty = '0' and not wip and sdram_busy = '0' then
          -- async FIFO source: live post-trigger data
          fifo_rd <= '1';

          -- Compute SDRAM address
          if Continuous_Mode = '1' then
            if buf_sel = "00" then
              write_addr := std_logic_vector(to_unsigned(waddr_0, 22));
            elsif buf_sel = "01" then
              write_addr := std_logic_vector(to_unsigned(buf_base1_r + waddr_1, 22));
            else
              write_addr := std_logic_vector(to_unsigned(buf_base2_r + waddr_2, 22));
            end if;
          else
            write_addr := std_logic_vector(to_unsigned(waddr_0, 22));
          end if;

          wr_pend      := true;
          wr_pend_addr := write_addr;
          wr_pend_data := fifo_rdata;

          -- Update buffer counters
          if Continuous_Mode = '1' then
            if buf_sel = "00" then
              if buf_rem_0 = 1 then
                buf_full(0) <= '1';  buf_rem_0 <= 0;
                if buf_full(1) = '1' and buf_full(2) = '1' then
                  full_pending <= '1';
                else
                  if buf_full(1) = '0' then buf_sel <= "01"; waddr_1 := 0; buf_rem_1 <= buf_limit_r;
                  else                     buf_sel <= "10"; waddr_2 := 0; buf_rem_2 <= buf_limit_r; end if;
                end if;
              else
                buf_rem_0 <= brem0_dec;
              end if;
              waddr_0 := waddr_0 + 1;
            elsif buf_sel = "01" then
              if buf_rem_1 = 1 then
                buf_full(1) <= '1';  buf_rem_1 <= 0;
                if buf_full(0) = '1' and buf_full(2) = '1' then
                  full_pending <= '1';
                else
                  if buf_full(2) = '0' then buf_sel <= "10"; waddr_2 := 0; buf_rem_2 <= buf_limit_r;
                  else                     buf_sel <= "00"; waddr_0 := 0; buf_rem_0 <= buf_limit_r; end if;
                end if;
              else
                buf_rem_1 <= brem1_dec;
              end if;
              waddr_1 := waddr_1 + 1;
            else
              if buf_rem_2 = 1 then
                buf_full(2) <= '1';  buf_rem_2 <= 0;
                if buf_full(0) = '1' and buf_full(1) = '1' then
                  full_pending <= '1';
                else
                  if buf_full(0) = '0' then buf_sel <= "00"; waddr_0 := 0; buf_rem_0 <= buf_limit_r;
                  else                     buf_sel <= "01"; waddr_1 := 0; buf_rem_1 <= buf_limit_r; end if;
                end if;
              else
                buf_rem_2 <= brem2_dec;
              end if;
              waddr_2 := waddr_2 + 1;
            end if;
          else
            -- Single-buffer mode: buf_rem_single accounts for ALL samples
            if buf_rem_single > 0 then
              buf_rem_single <= brem_single_dec;
              waddr_0 := waddr_0 + 1;
            end if;
          end if;
        end if;

        -- Track SDRAM write completion
        if wip then
          if wr_cnt < 2 then
            wr_cnt := wr_cnt + 1;
          else
            wip := false; wr_cnt := 0;
          end if;
        end if;

        -- Assert Full (single-buffer mode)
        if not rd_mode and full_i = '0' and Continuous_Mode = '0' then
          if buf_rem_single = 0
             and fifo_rdempty = '1'
             and not wip
             and not wr_pend
          then
            full_i <= '1';
            rd_mode := true;
          end if;
        end if;
      end if; -- end rd_mode
      end if; -- end run_edge_r else

      -- Status
      Status(0) <= run_level_r;
      if wip then Status(1) <= '1'; else Status(1) <= '0'; end if;
      Status(2) <= s_rd;
      Status(3) <= full_i;
      Status(4) <= status_overflow;
      Status(5) <= run_stop_overflow;
      Status(7 downto 6) <= (others => '0');
    end if;
  end process;

  Full <= full_i;
  s_burst <= s_burst_i;
  Buffer_Full(0) <= buf_full(0);
  Buffer_Full(2) <= buf_full(2);
  Buffer_Full(1) <= buf_full(1);

  SDRAM_Interface1 : SDRAM_Interface
  generic map (Sim => Sim, CLK_Frequency => CLK_Frequency, Write_Latency => Write_Latency, Read_Latency => Read_Latency, Page_Latency => Page_Latency)
  port map (
    CLK          => CLK,
    Reset        => '0',
    CLK_150_Out  => pclk,
    Address      => s_addr,
    Write_Enable => s_wr,
    Write_Data   => s_wdata,
    Burst        => s_burst_i,
    Read_Enable  => s_rd,
    Read_Data    => s_rdata,
    Read_Valid   => s_rvalid,
    Busy         => sdram_busy,
    Idle         => open,
    sdram_addr   => sdram_addr,
    sdram_ba     => sdram_ba,
    sdram_cas_n  => sdram_cas_n,
    sdram_cke    => sdram_cke,
    sdram_cs_n   => sdram_cs_n,
    sdram_dq     => sdram_dq,
    sdram_dqm    => sdram_dqm,
    sdram_ras_n  => sdram_ras_n,
    sdram_we_n   => sdram_we_n,
    sdram_clk    => sdram_clk
  );

end rtl;

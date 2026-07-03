library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
library lpm;
use lpm.lpm_components.all;

entity Fast_Logic_Analyzer_SDRAM is
  generic (
    Max_Samples : natural := 4194304;
    Channels    : natural range 1 to 16 := 16;
    Sim         : boolean := false;
    FAST_SPEED  : boolean := false;
    CLK_Frequency : natural := 100_000_000;
    SDRAM_CLK_HZ : natural := 166_666_667;
    SAMPLE_CLK_HZ : natural := 200_000_000;
    Write_Latency : natural := 10;
    Read_Latency  : natural := 3;
    Page_Latency  : natural := 3
  );
port (
  CLK          : in  std_logic;
  SDRAM_CLK_IN : in  std_logic := '0';
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
    Narrow_Enable : in std_logic := '0';
    Narrow_Channel : in natural range 0 to 15 := 0;
    -- Double-buffer control
    Continuous_Mode : in std_logic := '0';
    Buffer_Full     : out std_logic_vector(2 downto 0) := (others => '0');
    Buffer_Ack      : in std_logic_vector(2 downto 0) := (others => '0');
    Analog_Frame_Data : in std_logic_vector(127 downto 0) := (others => '0');
    Analog_Frame_Len  : in natural range 1 to 14 := 1;
    Analog_Stream_Mode : in std_logic := '0';
    Analog_Frame_Toggle : in std_logic := '0';
    -- Parallel bit-packing capture path. When Packed_Mode is set, the packed
    -- 16-bit valid/ready stream (from mso_capture in the top, already in the
    -- FAST_CLK domain) drives the write FIFO instead of the Analog_Frame/sample
    -- writer. Packed_Ready backpressures the producer (FIFO not full).
    Packed_Mode  : in  std_logic := '0';
    Packed_Data  : in  std_logic_vector(15 downto 0) := (others => '0');
    Packed_Valid : in  std_logic := '0';
    Packed_Ready : out std_logic := '0';
    -- Single-shot block readout via a response FIFO. The OLS side (CLK domain)
    -- requests a stream and drains the FIFO; the FLA walks the addresses on pclk
    -- and pushes each valid sample in. The dcfifo is a proper CDC between the
    -- pclk readout domain and the CLK domain, replacing the old fixed-latency
    -- Address/Outputs latch that corrupted the first sample(s) of each block.
    Blk_Rd_Req_Tog : in  std_logic := '0';   -- toggle edge starts a stream
    Blk_Rd_Base    : in  natural range 0 to Max_Samples := 0;  -- base sample idx
    Blk_Rd_Count   : in  natural range 0 to Max_Samples := 0;  -- samples to stream
    Auto_Renew     : in  std_logic := '0';  -- auto-renew stream (no deassert on block end)
    -- (Compression moved to the OLS_Interface CLK-domain drain; the FLA
    -- always streams raw samples into the response FIFO.)
    Rd_Fifo_Q      : out std_logic_vector(15 downto 0) := (others => '0');
    Rd_Fifo_Empty  : out std_logic := '1';
    Rd_Fifo_RdReq  : in  std_logic := '0';
    Producer_Index : out std_logic_vector(31 downto 0) := (others => '0');
    Oldest_Index   : out std_logic_vector(31 downto 0) := (others => '0');
    Newest_Index   : out std_logic_vector(31 downto 0) := (others => '0');
    Overrun_Count  : out std_logic_vector(31 downto 0) := (others => '0');
    Pump_Valid_Cycles   : out std_logic_vector(31 downto 0) := (others => '0');
    Pump_Ready_Cycles   : out std_logic_vector(31 downto 0) := (others => '0');
    Pump_Accept_Cycles  : out std_logic_vector(31 downto 0) := (others => '0');
    Pump_Stall_Cycles   : out std_logic_vector(31 downto 0) := (others => '0');
    Pump_NoData_Cycles  : out std_logic_vector(31 downto 0) := (others => '0');
    Pump_Overflow_Count : out std_logic_vector(31 downto 0) := (others => '0')
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
  -- In continuous mode each triple buffer is exactly one host read block
  -- (512 samples / 1024 bytes) so the standard CMD_READ_CAPTURE block protocol
  -- streams a completed buffer verbatim. Single-shot still uses samples/6.
  constant CONT_BUF    : natural := 512;
  constant CONT_RING_WORDS : natural := Max_Samples;

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
  signal single_count_load_q : std_logic := '0';
  -- Single-shot drain-complete counter: number of consecutive pclk cycles the pump
  -- FIFO has been empty AFTER the producer-done bit. Local pclk counter (no wide
  -- cross-domain compare), so it stays out of the hot 167 MHz accept path.
  signal single_drain_cnt : natural range 0 to 2047 := 0;

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
  signal cfg_samples   : natural range 1 to Max_Samples := Max_Samples;
  signal cfg_valid_toggle : std_logic := '0';
  signal cfg_ack_s1    : std_logic := '0';
  signal cfg_ack_s2    : std_logic := '0';
  signal cfg_ack_edge  : std_logic := '0';

  -- Config handshake: FAST_CLK domain
  signal cfg_rate_div_f  : natural range 1 to MAX_RATE_DIV := 12;
  signal cfg_rate_reload_f : natural range 0 to MAX_RATE_DIV := 11;
  signal cfg_samples_f   : natural range 1 to Max_Samples := Max_Samples;
  signal cfg_valid_s1    : std_logic := '0';
  signal cfg_valid_s2    : std_logic := '0';
  signal cfg_valid_edge  : std_logic := '0';
  signal cfg_ack_toggle  : std_logic := '0';
  signal cap_done_toggle_f : std_logic := '0';
  signal cap_done_s1       : std_logic := '0';
  signal cap_done_s2       : std_logic := '0';
  signal cap_done_last     : std_logic := '0';
  signal producer_done_q   : std_logic := '0';

  signal rate_div_m1_f : natural range 0 to MAX_RATE_DIV := 11;

  -- Registered sample counter and tick (replaces variable cnt).
  -- cnt_s is a registered signal; sample_tick_r is asserted for one cycle
  -- when the counter reaches zero, breaking the cnt=0 -> packing -> BRAM/FIFO
  -- path into (cnt -> zero -> tick) on cycle N and (tick -> pack) on cycle N+1.
  signal cnt_s         : natural range 0 to MAX_RATE_DIV := 0;
  signal sample_tick_r : std_logic := '0';

  signal cnt_eq_zero   : std_logic := '0';
  signal run_f_s1  : std_logic := '0';
  signal run_f_s2   : std_logic := '0';
  signal continuous_f_s1 : std_logic := '0';
  signal continuous_f    : std_logic := '0';
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
  signal overflow_count_en_q : std_logic := '0';
  signal sample_remaining : natural range 0 to Max_Samples := 0;
  signal run_stop_overflow : std_logic := '0';
  signal status_overflow   : std_logic := '0';
  signal overflow_readout_q : std_logic := '0';
  signal producer_index_u  : unsigned(31 downto 0) := (others => '0');
  signal oldest_index_u    : unsigned(31 downto 0) := (others => '0');
  signal newest_index_u    : unsigned(31 downto 0) := (others => '0');
  signal overrun_count_u   : unsigned(31 downto 0) := (others => '0');
  -- Write-pump utilisation counters (host regs 0x60..0x64). Diagnostic only
  -- (read by host/debug/_deep_rate_sweep.py); compiled out by default to
  -- reclaim five 32-bit counters (~200 LEs) from the congested 167 MHz cone
  -- — at 99% device utilisation they cost timing closure and can push the
  -- fitter past device capacity. The register map is unchanged: reads
  -- return zero. Pump_Overflow_Count (0x65) stays — it counts real capture
  -- overflow aborts.
  constant PUMP_METRICS : boolean := false;
  signal pump_valid_cycles_u   : unsigned(31 downto 0) := (others => '0');
  signal pump_ready_cycles_u   : unsigned(31 downto 0) := (others => '0');
  signal pump_accept_cycles_u  : unsigned(31 downto 0) := (others => '0');
  signal pump_stall_cycles_u   : unsigned(31 downto 0) := (others => '0');
  signal pump_nodata_cycles_u  : unsigned(31 downto 0) := (others => '0');
  signal pump_overflow_count_u : unsigned(31 downto 0) := (others => '0');
  -- Registered (1-cycle) copies of the per-cycle pump condition flags. The wide
  -- counters increment from these short registered enables instead of directly
  -- from the combinational pump_*_v (which carry the buf_rem/cur_full producer
  -- gating and, in series with the 32-bit counter carry, violated clk[2] setup).
  signal pump_valid_q  : boolean := false;
  signal pump_ready_q  : boolean := false;
  signal pump_accept_q : boolean := false;
  signal pump_stall_q  : boolean := false;
  signal pump_nodata_q : boolean := false;
  signal cont_accept_q : boolean := false;
  signal cont_meta_reset_q : std_logic := '0';
  signal ring_used         : natural range 0 to CONT_RING_WORDS := 0;

  constant AFIFO_DEPTH : natural := 16384;
  constant AFIFO_WIDTH : natural := 16;
  constant AFIFO_WIDTHU : natural := 14;
  signal fifo_wdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_wr    : std_logic := '0';
  signal fifo_wrfull : std_logic := '0';
  -- Parallel packed-mode write mux: the afifo is fed from afifo_wdata/afifo_wr,
  -- which select the packed stream when packed_mode_f is set, else the native
  -- sample/analog writer's fifo_wdata/fifo_wr (so packed_mode='0' is bit-for-bit
  -- the original behaviour). packed_mode_f is Packed_Mode 2FF-synced to FAST_CLK.
  signal afifo_wdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal afifo_wr    : std_logic := '0';
  signal packed_mode_meta : std_logic := '0';
  signal packed_mode_f    : std_logic := '0';
  signal fifo_wrusedw : std_logic_vector(AFIFO_WIDTHU-1 downto 0) := (others => '0');
  signal fifo_wralmost_full : std_logic := '0';
  signal fifo_aclr  : std_logic := '0';
  signal fifo_rdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_rd    : std_logic := '0';
  signal fifo_rdempty : std_logic := '0';

  -- Readout response FIFO: pclk write (FLA streams samples) / CLK read (OLS
  -- drains). Depth >= one block so a whole block streams without backpressure.
  constant RDFIFO_DEPTH  : natural := 1024;
  constant RDFIFO_WIDTHU : natural := 10;
  signal rdfifo_wdata  : std_logic_vector(15 downto 0) := (others => '0');
  signal rdfifo_wr     : std_logic := '0';
  signal rdfifo_wrfull : std_logic := '0';
  signal rdfifo_aclr   : std_logic := '0';
  -- Pipeline registers for readout address (breaks 22-bit comparator + conversion path)
  signal addr_is_wrap  : std_logic := '0';
  signal stream_addr_r : std_logic_vector(21 downto 0) := (others => '0');
  -- Pre-computed increment + ring-wrap compare for the stream address. Both
  -- used to sit combinationally inside the address-update mux and made
  -- stream_addr_u's 22-bit carry chain the worst clk[2] setup path. They are
  -- computed off stream_addr_r (the REGISTERED mirror — not the process
  -- variable, whose reads chain the current cycle's update logic into the
  -- path), so each is a shallow reg->logic->reg path with a full cycle
  -- budget, valid two cycles after any stream_addr_u write. Consumers only
  -- fire on read COMPLETIONS, which are spaced by at least CAS latency plus
  -- the rd_gap bubble (and a stream start loads the address several cycles
  -- before the first completion), so the lag can never be consumed stale.
  signal stream_at_ring_end : std_logic := '0';
  signal stream_addr_nxt_r  : unsigned(21 downto 0) := (others => '0');

  -- 2FF synchroniser for the block-read request toggle (CLK -> pclk)
  signal blk_req_s1    : std_logic := '0';
  signal blk_req_s2    : std_logic := '0';
  signal blk_req_edge_r : std_logic := '0';

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
  signal cap_stream_valid : std_logic := '0';
  signal cap_stream_ready : std_logic := '0';
  signal cap_stream_addr  : std_logic_vector(21 downto 0) := (others => '0');
  signal cap_stream_data  : std_logic_vector(15 downto 0) := (others => '0');

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
    Capture_Stream_Valid : in  std_logic := '0';
    Capture_Stream_Ready : out std_logic := '0';
    Capture_Stream_Address : in std_logic_vector(21 downto 0) := (others => '0');
    Capture_Stream_Data  : in  std_logic_vector(15 downto 0) := (others => '0');
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
    aclr     : in  std_logic;
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

  -- Clear both CDC FIFOs whenever a capture run starts/stops or the writer
  -- aborts on overflow. Without this, stale capture words can survive between
  -- runs and get written into SDRAM starting at address 0 on the next capture,
  -- which matches the rare large bad-prefix burst seen on hardware.
  fifo_aclr <= run_edge_r or overflow_clk;
  rdfifo_aclr <= run_edge_r;

  pclk <= CLK when Sim else SDRAM_CLK_IN;

  gen_fast_div : if FAST_SPEED generate
  begin
    -- FAST_SPEED captures store one 16-bit word per requested sample. The
    -- /3 divider only exists for the non-fast packer and must not be
    -- elaborated here, otherwise Quartus rejects the now-unused LPM output.
    process(CLK) begin
      if rising_edge(CLK) then
        samples_d1   <= Samples;
        samples_div  <= Samples;
        samples_div6 <= 0;
      end if;
    end process;
  end generate;

  gen_normal_div : if not FAST_SPEED generate
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
  end generate;

  CLK_150 <= pclk;
  -- wrfull can arrive too late for the 200 MHz write clock. Stop early using
  -- write-domain fill level so Rate_Div=1 captures backpressure cleanly
  -- instead of silently losing FIFO writes and wedging producer progress.
  fifo_wralmost_full <= '1'
    when unsigned(fifo_wrusedw) >= to_unsigned(AFIFO_DEPTH - 256, AFIFO_WIDTHU)
    else '0';

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
      cap_done_s1 <= cap_done_toggle_f;
      cap_done_s2 <= cap_done_s1;
      if cap_done_s2 /= cap_done_last then
        cap_done_last <= cap_done_s2;
        producer_done_q <= '1';
      end if;
      single_count_load_q <= '0';
      if run_edge_r = '1' and run_start_r = '1' then
        cfg_rate_div  <= Rate_Div;
        cfg_samples   <= Samples;
        cfg_valid_toggle <= not cfg_valid_toggle;
        single_count_load_q <= '1';
        producer_done_q <= '0';
      end if;
    end if;
  end process;

  -- Reset the continuous-mode metadata counters one cycle after the run edge.
  -- Keeping this out of the write-pump decision cone avoids routing run_edge_r
  -- through the 32-bit producer/oldest/newest counter enables at 167 MHz.
  process(pclk)
  begin
    if rising_edge(pclk) then
      cont_meta_reset_q <= (run_edge_r and run_start_r) or run_stop_overflow;
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
      if Continuous_Mode = '1' then
        -- One 512-sample block per buffer, laid out 0 / 512 / 1024.
        buf_limit_r <= CONT_BUF;
        buf_last_r  <= CONT_BUF - 1;
        buf_base0_r <= 0;
        buf_base1_r <= CONT_BUF;
        buf_base2_r <= CONT_BUF + CONT_BUF;
      else
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
    end if;
  end process;

  -- Pipeline registers: pre-compute buf_rem - 1 (break 21-bit subtractor chain)
  -- Registered in a separate process so the main process only drives a MUX.
  -- The subtractor output is available at the START of the next cycle, before
  -- the main process evaluates its combinatorial logic.
  process(pclk)
  begin
    if rising_edge(pclk) then
      -- The decremented value is only consumed while the counter is > 0;
      -- guard the natural subtraction so simulation doesn't trap at 0.
      if buf_rem_0 > 0 then brem0_dec <= buf_rem_0 - 1; else brem0_dec <= 0; end if;
      if buf_rem_1 > 0 then brem1_dec <= buf_rem_1 - 1; else brem1_dec <= 0; end if;
      if buf_rem_2 > 0 then brem2_dec <= buf_rem_2 - 1; else brem2_dec <= 0; end if;
      if buf_rem_single > 0 then brem_single_dec <= buf_rem_single - 1; else brem_single_dec <= 0; end if;
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
      continuous_f_s1 <= Continuous_Mode;
      continuous_f <= continuous_f_s1;
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
    constant FAST_MAX_RATE_DIV : natural := 65535;
    signal sample_word_r  : std_logic_vector(Channels-1 downto 0) := (others => '0');
    signal capture_en_r   : std_logic := '0';
    signal pretrig_en_r   : std_logic := '0';
    signal bram_wp_r      : natural range 0 to BRAM_SIZE-1 := 0;
    signal bram_cnt_r     : natural range 0 to BRAM_SIZE := 0;
    signal sample_div_cnt_r : natural range 0 to FAST_MAX_RATE_DIV := 0;
    signal fast_rate_reload_r : natural range 0 to FAST_MAX_RATE_DIV := 0;
    signal sample_tick_r  : std_logic := '0';
    signal sample_rem_nonzero_r : std_logic := '0';
    -- Pipeline register: pre-compute sample_remaining - 1 to break 22-bit carry chain
    signal sample_rem_dec_r    : natural range 0 to Max_Samples := 0;
    -- Pre-trigger counter: limits BRAM pre-trigger to 8 ticks, then switches to FIFO.
    -- Without this, the CDC settling window for run_f_level (Armed→Run) can cause up to
    -- 1024 pre-trigger BRAM writes before switching to FIFO, delaying gen capture data.
    signal pretrig_tick_cnt : natural range 0 to 15 := 0;
    -- Analog stream capture: when Analog_Stream_Mode is set, the ADC controller
    -- owns frame cadence. Each completed 8-channel ADC scan toggles
    -- Analog_Frame_Toggle; this writer snapshots that coherent frame and bursts
    -- its 6 or 7 16-bit words into SDRAM.
    signal aframe_pending : std_logic_vector(127 downto 0) := (others => '0');
    signal astream_s    : std_logic := '0';
    signal astream_f    : std_logic := '0';
    signal aframe_shift : std_logic_vector(127 downto 0) := (others => '0');
    signal aframe_toggle_s1 : std_logic := '0';
    signal aframe_toggle_s2 : std_logic := '0';
    signal aframe_toggle_last : std_logic := '0';
    signal aframe_ready_r : std_logic := '0';
    signal aword_count_pending : natural range 1 to 7 := 7;
    signal aword_count_f : natural range 1 to 7 := 7;
    signal aword_idx    : natural range 0 to 6 := 0;
    signal analog_burst_active : std_logic := '0';
    -- Registered copy of fifo_wralmost_full for the analog burst gate. The flag
    -- is a wide wrusedw>=(DEPTH-256) compare off the afifo status reg; feeding it
    -- straight into the burst next-state logic is the 200 MHz critical path.
    -- The 256-word cushion plus the fact that analog bursts are 7 words every
    -- ~10 us (FIFO drains continuously) mean this flag never asserts in analog
    -- mode, so reacting a cycle late is harmless and keeps the compare off path.
    signal afull_r       : std_logic := '0';
    signal start_gate_r  : natural range 0 to 3 := 0;
    signal narrow_enable_s1 : std_logic := '0';
    signal narrow_enable_f  : std_logic := '0';
    signal narrow_channel_s1 : natural range 0 to 15 := 0;
    signal narrow_channel_f  : natural range 0 to 15 := 0;
    signal narrow_shift_r : std_logic_vector(15 downto 0) := (others => '0');
    signal narrow_bit_count_r : natural range 0 to 15 := 0;
  begin
    -- Stage 0: sample pins
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        sample_word_r <= Inputs;
      end if;
    end process;

    -- CDC: detect the slow ADC-frame toggle in FAST_CLK and snapshot the frame
    -- after it has settled. sys_clk toggles only after all 8 ADC result
    -- registers have been updated.
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        aframe_toggle_s1 <= Analog_Frame_Toggle;
        aframe_toggle_s2 <= aframe_toggle_s1;
        aframe_ready_r <= '0';
        if aframe_toggle_s2 /= aframe_toggle_last then
          aframe_toggle_last <= aframe_toggle_s2;
          aframe_pending <= Analog_Frame_Data;
          if Analog_Frame_Len <= 2 then
            aword_count_pending <= 1;
          else
            aword_count_pending <= (Analog_Frame_Len + 1) / 2;
          end if;
          aframe_ready_r <= '1';
        end if;
        astream_s <= Analog_Stream_Mode;
        astream_f <= astream_s;
        narrow_enable_s1 <= Narrow_Enable;
        narrow_enable_f <= narrow_enable_s1;
        narrow_channel_s1 <= Narrow_Channel;
        narrow_channel_f <= narrow_channel_s1;
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
          if cfg_rate_div > FAST_MAX_RATE_DIV then
            fast_rate_reload_r <= FAST_MAX_RATE_DIV;
          elsif cfg_rate_div > 1 then
            fast_rate_reload_r <= cfg_rate_div - 1;
          else
            fast_rate_reload_r <= 0;
          end if;
        elsif capture_en_r = '1' and sample_rem_nonzero_r = '1' then
          if sample_div_cnt_r = 0 then
            sample_div_cnt_r <= fast_rate_reload_r;
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
        if cfg_valid_edge = '1' then
          start_gate_r <= 2;
        elsif capture_en_r = '1' and start_gate_r > 0 then
          start_gate_r <= start_gate_r - 1;
        end if;
        if capture_en_r = '1' and sample_rem_nonzero_r = '1'
           and sample_div_cnt_r = 0 and start_gate_r = 0 then
          sample_tick_r <= '1';
        end if;
      end if;
    end process;

    -- Stage 2c: sample-remaining non-zero flag (pipelined, avoids 22-bit >0 in write path)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if sample_remaining > 0 then
          sample_rem_dec_r <= sample_remaining - 1;
        else
          sample_rem_dec_r <= 0;
        end if;

        if cfg_valid_edge = '1' then
          sample_rem_nonzero_r <= '1';
        elsif fifo_wr = '1' and sample_remaining = 0 then
          -- fifo_wr and sample_remaining are both registered, so this process
          -- observes the POST-decrement count: remaining=0 here means the
          -- last word was just pushed. The previous <=2 threshold stopped two
          -- words short, so the write pump (which counts the full sample
          -- count) never saw Full and the capture never reported DONE.
          sample_rem_nonzero_r <= '0';
          -- Producer just emitted the LAST requested sample to the async FIFO
          -- (covers all three emit paths: digital / narrow / analog). Toggle the
          -- producer-done bit so the pclk side completes the single-shot capture
          -- once the pump FIFO has drained -- no exact SDRAM write-count match
          -- required (the packed producer can fall a few words short at some
          -- dividers) and no wide compare in the hot 167 MHz accept branch.
          cap_done_toggle_f <= not cap_done_toggle_f;
        end if;
      end if;
    end process;

    -- Pre-trigger tick counter: limits BRAM pre-trigger to 8 ticks, then switches to FIFO.
    -- Prevents CDC settling window from causing 1024 pre-trigger writes that delay gen data.
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_valid_edge = '1' then
          pretrig_tick_cnt <= 0;
        elsif pretrig_en_r = '1' and sample_tick_r = '1' and pretrig_tick_cnt < 15 then
          pretrig_tick_cnt <= pretrig_tick_cnt + 1;
        end if;
      end if;
    end process;

    -- Stage 2d: BRAM/FIFO write (uses pipelined flags, only 1-bit compares)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        fifo_wr <= '0';
        bram_wren <= '0';
        afull_r <= fifo_wralmost_full;

        if cfg_valid_edge = '1' then
          -- Load from cfg_samples (CLK-domain value, quasi-static while the
          -- toggle handshake is in flight), NOT cfg_samples_f: that register
          -- is updated by another process on this same edge, so reading it
          -- here returned the PREVIOUS capture's sample count — captures ran
          -- with stale lengths (e.g. the host reset()'s SAMPLE_COUNT=2),
          -- completed instantly and read back as full-length flat data.
          sample_remaining <= cfg_samples;
          fifo_overflow_f <= '0';
          bram_wp_r <= 0;
          bram_cnt_r <= 0;
          aframe_shift <= (others => '0');
          aword_idx <= 0;
          analog_burst_active <= '0';
          narrow_shift_r <= (others => '0');
          narrow_bit_count_r <= 0;
        end if;

        if fifo_overflow_f = '0' then
          -- Pre-trigger BRAM is digital-only; skip it entirely in analog stream
          -- mode so only ADC frame words enter the FIFO.
          if pretrig_en_r = '1' and pretrig_tick_cnt < 8 and astream_f = '0' then
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

          elsif capture_en_r = '1' and astream_f = '1' then
            -- Only start a new frame burst while the word budget is unspent.
            -- Without this the writer keeps bursting a frame every ADC scan
            -- after sample_remaining hits 0, so the FIFO never settles empty
            -- and single-shot Full only asserts by luck in an inter-frame gap.
            -- sample_rem_nonzero_r is the 1-bit budget flag the digital path
            -- already gates on (a wide >0 compare would not close at 200 MHz);
            -- a frame in flight always finishes (the burst loop below is not
            -- gated), and cfg_samples is a whole number of frames, so capture
            -- stops cleanly on a frame boundary.
            if aframe_ready_r = '1' and analog_burst_active = '0'
               and sample_rem_nonzero_r = '1' then
              -- Snapshot the coherent frame once; it is NOT shifted (see below).
              aframe_shift <= aframe_pending;
              aword_count_f <= aword_count_pending;
              aword_idx <= 0;
              analog_burst_active <= '1';
            end if;

            -- Emit word[aword_idx] via a 16-bit 8:1 mux instead of shifting the
            -- whole 128-bit frame each cycle. The old `x"0000" & shift(127..16)`
            -- put a 128-bit-wide mux on the FAST_CLK (200 MHz) path, gated by the
            -- late-arriving afifo almost-full flag, and would not close timing
            -- (every seed -0.64..-1.12 ns). fifo_wdata is selected purely from
            -- aword_idx (NOT gated by almost_full — it only matters when fifo_wr
            -- is asserted), so the wide datapath and the almost-full fanout are
            -- off the critical path; only the 1-bit write strobe and the 3-bit
            -- index counter remain gated.
            if analog_burst_active = '1' then
              case aword_idx is
                when 0      => fifo_wdata <= aframe_shift(15 downto 0);
                when 1      => fifo_wdata <= aframe_shift(31 downto 16);
                when 2      => fifo_wdata <= aframe_shift(47 downto 32);
                when 3      => fifo_wdata <= aframe_shift(63 downto 48);
                when 4      => fifo_wdata <= aframe_shift(79 downto 64);
                when 5      => fifo_wdata <= aframe_shift(95 downto 80);
                when others => fifo_wdata <= aframe_shift(111 downto 96);
              end case;
            end if;

            if analog_burst_active = '1' and afull_r = '0' then
              fifo_wr <= '1';
              if aword_idx + 1 >= aword_count_f then
                analog_burst_active <= '0';
                aword_idx <= 0;
              else
              aword_idx <= aword_idx + 1;
              end if;
              if sample_rem_nonzero_r = '1' then
                sample_remaining <= sample_rem_dec_r;
              end if;
            end if;
            if afull_r = '1' and continuous_f = '0' then
              fifo_overflow_f <= '1';
            end if;

          elsif capture_en_r = '1' and sample_tick_r = '1' and narrow_enable_f = '1' then
            -- Narrow high-speed rolling packs 16 consecutive samples of one
            -- selected digital channel into one SDRAM word. Bit 0 is earliest.
            if narrow_channel_f < Channels then
              narrow_shift_r(narrow_bit_count_r) <= sample_word_r(narrow_channel_f);
            else
              narrow_shift_r(narrow_bit_count_r) <= '0';
            end if;

            if narrow_bit_count_r = 15 then
              if afull_r = '0' then
                fifo_wdata(14 downto 0) <= narrow_shift_r(14 downto 0);
                if narrow_channel_f < Channels then
                  fifo_wdata(15) <= sample_word_r(narrow_channel_f);
                else
                  fifo_wdata(15) <= '0';
                end if;
                fifo_wr <= '1';
                if sample_rem_nonzero_r = '1' then
                  sample_remaining <= sample_rem_dec_r;
                end if;
              end if;
              narrow_shift_r <= (others => '0');
              narrow_bit_count_r <= 0;
            else
              narrow_bit_count_r <= narrow_bit_count_r + 1;
            end if;

            if afull_r = '1' and continuous_f = '0' then
              fifo_overflow_f <= '1';
            end if;

          elsif capture_en_r = '1' and sample_tick_r = '1' then
            -- Data is registered unconditionally; it is only consumed when
            -- fifo_wr is asserted, so keeping the almost-full compare off the
            -- 16-bit fifo_wdata mux (it now only gates the 1-bit write strobe)
            -- shortens the 200 MHz path that the analog frame mux shares.
            fifo_wdata <= sample_word_r;
            if afull_r = '0' then
              fifo_wr <= '1';
              -- guard: at full rate one in-flight tick can push a word after
              -- the nonzero flag clears; don't underflow the natural
              if sample_rem_nonzero_r = '1' then
                sample_remaining <= sample_rem_dec_r;
              end if;
            end if;
            if afull_r = '1' and continuous_f = '0' then
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
    signal start_gate_r : natural range 0 to 3 := 0;
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
          start_gate_r <= 2;
        elsif sample_en_v then
          if start_gate_r > 0 then
            start_gate_r <= start_gate_r - 1;
          elsif cnt_s = 0 then
            cnt_s <= rate_div_m1_f;
            -- sample_tick_r now set from cnt_eq_zero below
          else
            cnt_s <= cnt_s - 1;
          end if;
        if cnt_s = 0 then cnt_eq_zero <= '1'; else cnt_eq_zero <= '0'; end if;
        sample_tick_r <= cnt_eq_zero;

        end if;

        -- Config handshake edge: transition from pre-trigger to flush/capture
        if cfg_valid_edge = '1' then
          step_r := 0;
          wbuf := (others => '0');
          -- cfg_samples, not cfg_samples_f: the registered copy is written on
          -- this same edge by the handshake process and reads stale here
          -- (previous capture's count). Same fix as the FAST_SPEED branch.
          sample_remaining <= cfg_samples;
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
              if fifo_wralmost_full = '0' then
                bram_raddr_f <= flush_raddr;
                -- Skip write on first cycle (BRAM read is registered)
                if flush_rem < bram_cnt then
                  fifo_wdata <= bram_rdata_f;
                  fifo_wr <= '1';
                  sample_remaining <= sample_remaining - 1;
                  if sample_remaining = 1 then
                    -- Last requested sample came from the pre-trigger flush
                    -- (tiny-capture edge case); signal producer-done here too.
                    cap_done_toggle_f <= not cap_done_toggle_f;
                  end if;
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
                if fifo_wralmost_full = '0' and sample_remaining /= 0 then
                  fifo_wdata <= wbuf(15 downto 0);
                  fifo_wr <= '1';
                  sample_remaining <= sample_remaining - 1;
                  if sample_remaining = 1 then
                    -- Last requested sample just emitted to the async FIFO. Toggle
                    -- the producer-done bit; the pclk side completes the single-shot
                    -- capture once the pump FIFO has drained (no wide write-count
                    -- compare in the hot pump branch).
                    cap_done_toggle_f <= not cap_done_toggle_f;
                  end if;
                end if;
                if continuous_f = '0' and (fifo_wralmost_full = '1' or sample_remaining <= 1) then
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

  -- Async FIFO: dcfifo bridges FAST_CLK (write) and pclk (read).
  -- show-ahead ("ON"): q always presents the head word while not empty, so the
  -- write pump's read-at-pop (wr_pend_data := fifo_rdata) latches the CORRECT
  -- word. With "OFF" q only updated one read later, so the first pop returned
  -- the stale initial q and every write stored the previous pop's word — that
  -- left a junk word at SDRAM[0] and shifted the stream by one, which the host
  -- saw as the mixed-frame 2-sample preamble. read-at-pop timing is unchanged,
  -- so this does not perturb the (phase-sensitive) SDRAM readout the way an
  -- extra pump pipeline stage did.
  -- 2-FF synchronise the (CLK-domain) mode select into FAST_CLK.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      packed_mode_meta <= Packed_Mode;
      packed_mode_f    <= packed_mode_meta;
    end if;
  end process;

  -- Write-port source mux. In packed mode the packed producer writes only while
  -- capture is running and the FIFO can accept; Packed_Ready mirrors that so the
  -- producer never drops a word. packed_mode_f='0' passes the native writer
  -- through unchanged.
  afifo_wdata  <= Packed_Data when packed_mode_f = '1' else fifo_wdata;
  afifo_wr     <= (Packed_Valid and run_f_level and not fifo_wrfull)
                  when packed_mode_f = '1' else fifo_wr;
  Packed_Ready <= (not fifo_wrfull) and packed_mode_f and run_f_level;

  afifo : dcfifo
    generic map (
      lpm_width       => AFIFO_WIDTH,
      lpm_widthu      => AFIFO_WIDTHU,
      lpm_numwords    => AFIFO_DEPTH,
      lpm_showahead   => "ON",
      lpm_type        => "dcfifo",
      rdsync_delaypipe => 3,
      wrsync_delaypipe => 3,
      intended_device_family => "MAX 10"
    )
    port map (
      aclr     => fifo_aclr,
      data     => afifo_wdata,
      wrreq    => afifo_wr,
      wrclk    => FAST_CLK,
      rdreq    => fifo_rd,
      rdclk    => pclk,
      q        => fifo_rdata,
      rdempty  => fifo_rdempty,
      wrfull   => fifo_wrfull,
      wrusedw  => fifo_wrusedw,
      rdusedw  => open
    );

  -- Readout response FIFO: bridges the pclk readout domain to the CLK (OLS)
  -- domain for single-shot block reads. This is the CDC that removes the old
  -- fixed-3-cycle latch and its prime/drain workaround.
  rdfifo : dcfifo
    generic map (
      lpm_width       => 16,
      lpm_widthu      => RDFIFO_WIDTHU,
      lpm_numwords    => RDFIFO_DEPTH,
      lpm_showahead   => "OFF",
      lpm_type        => "dcfifo",
      rdsync_delaypipe => 3,
      wrsync_delaypipe => 3,
      intended_device_family => "MAX 10"
    )
    port map (
      aclr     => rdfifo_aclr,
      data     => rdfifo_wdata,
      wrreq    => rdfifo_wr,
      wrclk    => pclk,
      rdreq    => Rd_Fifo_RdReq,
      rdclk    => CLK,
      q        => Rd_Fifo_Q,
      rdempty  => Rd_Fifo_Empty,
      wrfull   => rdfifo_wrfull,
      wrusedw  => open,
      rdusedw  => open
    );

  -- (The readback delta compressor used to sit here in the pclk domain. It
  -- was moved to OLS_Interface's CLK-domain block drain: 322 LCs of packing
  -- logic in the congested 167 MHz SDRAM cone cost timing closure, and the
  -- readout drain at 100 MHz has both headroom and the natural place to
  -- produce the variable-length response.)

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
    variable rd_pend : std_logic := '0';
    variable write_addr : std_logic_vector(21 downto 0) := (others => '0');
    -- Streaming block-readout state (single-shot CMD_READ_CAPTURE path)
    variable stream_active : boolean := false;
    variable stream_addr_u : unsigned(21 downto 0) := (others => '0');
    variable stream_addr_inc_pending : boolean := false;
    variable stream_rem    : natural range 0 to Max_Samples := 0;
    variable rd_pend2      : std_logic := '0';
    -- Prime read: the FIRST SDRAM read of each block stream comes back garbage
    -- (un-driven DQ reads as 0xFFFF) because the controller is coming out of an
    -- idle/refresh window and the first read's data is not yet valid when it is
    -- latched. The legacy readout absorbed this with prime/drain padding that the
    -- streaming refactor removed, reintroducing a stale first sample at the start
    -- of every CMD_READ_CAPTURE block. Re-add throwaway reads of the base address
    -- whose results are discarded; the real stream then starts clean. Two prime
    -- reads (not one) are needed because the cold/refresh transition occasionally
    -- leaves the SECOND read unsettled too.
    constant STREAM_PRIME_N : natural := 2;
    variable stream_prime  : natural range 0 to STREAM_PRIME_N := 0;
    -- Read watchdog: an SDRAM read issued concurrently with capture-stream
    -- writes occasionally goes unanswered on hardware (stochastic arbitration
    -- loss; s_rvalid never comes). Without a timeout the readout waits
    -- forever, holding rd_mode and starving the write pump — the host sees a
    -- wedged block read. Time out and reissue the SAME address (the address
    -- now only advances on completion, so a retry is transparent; a late
    -- s_rvalid from the dropped attempt just completes the identical retry).
    -- Generous vs any legitimate latency (pump burst + refresh < ~1 us) so a
    -- merely-slow read cannot be retried into a duplicate sample; still ~40x
    -- faster than the OLS-side 500 us block watchdog.
    constant STREAM_RD_WD : natural := 2047;  -- ~12 us at 167 MHz
    variable rd_wd_cnt     : natural range 0 to STREAM_RD_WD := 0;
    -- One idle cycle after each completed read: stream_addr_r (the registered
    -- copy of stream_addr_u that feeds s_addr) lags the completion-time
    -- address advance by one pclk, so an immediate reissue would re-read the
    -- same address and duplicate a sample.
    variable rd_gap        : std_logic := '0';
    -- Write-pump pop bubble: fifo_rd is a registered pop against a show-ahead
    -- FIFO, so the head word only advances two cycles after an SDRAM accept.
    -- Without this gap, sustained cap_stream_ready wrote EVERY capture word
    -- to SDRAM twice (producer counted 2x, every sample appeared as an
    -- identical word pair on the wire, and all displayed frequencies were
    -- halved — masked historically by the 32-bit wire format that dropped
    -- every other word). The bubble condition is fifo_rd itself (its
    -- registered value = "popped last cycle") to keep new logic out of the
    -- timing-critical 167 MHz accept cone.
    -- Continuous-mode readout: a host block read temporarily enters rd_mode to
    -- stream the rolling SDRAM window, then returns to capture.
    -- cur_full backpressures the write pump off a full buffer.
    variable cur_full      : boolean := false;
    variable cont_base_v   : natural range 0 to Max_Samples - 1 := 0;
    variable ring_waddr    : natural range 0 to Max_Samples - 1 := 0;
    variable pump_valid_v  : boolean := false;
    variable pump_ready_v  : boolean := false;
    variable pump_accept_v : boolean := false;
    variable pump_stall_v  : boolean := false;
    variable pump_nodata_v : boolean := false;
    variable cont_accept_v : boolean := false;
  begin
    if rising_edge(pclk) then
      pump_valid_v := false;
      pump_ready_v := false;
      pump_accept_v := false;
      pump_stall_v := false;
      pump_nodata_v := false;
      cont_accept_v := false;
      fifo_rd <= '0';
      s_wr <= '0';
      s_burst_i <= '0';
      cap_stream_valid <= '0';
      rdfifo_wr <= '0';
      overflow_count_en_q <= overflow_clk;
      if overflow_count_en_q = '1' then
        pump_overflow_count_u <= pump_overflow_count_u + 1;
      end if;
      if stream_addr_inc_pending then
        if Continuous_Mode = '1' and stream_at_ring_end = '1' then
          stream_addr_u := (others => '0');
        else
          -- Registered pre-increment: no adder in this mux (see declaration)
          stream_addr_u := stream_addr_nxt_r;
        end if;
        stream_addr_inc_pending := false;
      end if;
      if single_count_load_q = '1' then
        buf_rem_single <= cfg_samples;
      end if;
      -- Synchronise the block-read request toggle into pclk (runs every cycle so
      -- no edge is missed). Blk_Rd_Base/Count are quasi-static: they are set on
      -- the OLS side before the toggle flips and held for the whole stream, so
      -- by the time this 2FF sees the edge they have long settled.
      blk_req_s1 <= Blk_Rd_Req_Tog;
      blk_req_s2 <= blk_req_s1;
      blk_req_edge_r <= blk_req_s1 xor blk_req_s2;
      -- Overflow from fast domain
      if overflow_clk = '1' then
        run_stop_overflow <= '1';
        status_overflow <= '1';
        if Continuous_Mode = '0' and full_i = '0' then
          -- End the single-shot capture on producer overflow. Re-entering the
          -- run-edge reset path here clears the write/read bookkeeping and
          -- leaves the host polling BUSY until timeout; latching Full preserves
          -- readout access to whatever was captured and makes the failure visible
          -- through the overflow/status counters.
          full_i <= '1';
          overflow_readout_q <= '1';
        end if;
      end if;
      if overflow_readout_q = '1' then
        rd_mode := true;
        overflow_readout_q <= '0';
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
      -- Continuous mode status/backpressure flag.
      -- full_i just reports "all three buffers full" (host can read). rd_mode is
      -- owned by the host-read path below, NOT here: the old code flipped it on
      -- a quiet-pipeline window the async host read never coincided with, so a
      -- completed buffer was never streamed and continuous readout returned 0.
      if Continuous_Mode = '1' then
        if full_i = '1' and (Buffer_Ack(0) = '1' or Buffer_Ack(1) = '1' or Buffer_Ack(2) = '1') then
          full_clr_pending <= '1';
        end if;
        if full_clr_pending = '1' then
          full_i <= '0';
          full_pending <= '0';
          full_clr_pending <= '0';
        end if;
        if full_pending = '1' and fifo_rdempty = '1'
           and cap_stream_valid = '0' then
          full_i <= '1';
          full_pending <= '0';
        end if;
      end if;

      if run_edge_r = '1' then
        waddr_0 := 0; waddr_1 := 0; waddr_2 := 0;
        buf_rem_0 <= buf_limit_r;
        buf_rem_1 <= buf_limit_r;
        buf_rem_2 <= buf_limit_r;
        -- Loaded from cfg_samples one pclk later, after the run-edge config
        -- latch has updated. Keep the pump count at zero for that holdoff cycle.
        buf_rem_single <= 0;
        single_drain_cnt <= 0;
        rd_pend := '0';
        buf_sel <= "00";
        buf_full(0) <= '0'; buf_full(1) <= '0'; buf_full(2) <= '0';
        full_i <= '0';
        full_pending <= '0'; full_clr_pending <= '0';
        run_stop_overflow <= '0';
        status_overflow <= '0';
        overflow_readout_q <= '0';
        if run_start_r = '1' then
          pump_valid_cycles_u <= (others => '0');
          pump_ready_cycles_u <= (others => '0');
          pump_accept_cycles_u <= (others => '0');
          pump_stall_cycles_u <= (others => '0');
          pump_nodata_cycles_u <= (others => '0');
          pump_overflow_count_u <= (others => '0');
          overflow_count_en_q <= '0';
          ring_waddr := 0;
        end if;
        if run_stop_r = '1' then
          rd_mode := true;
        else
          rd_mode := false;
        end if;
        s_wr <= '0'; s_rd <= '0';
        cap_stream_valid <= '0';
        stream_active := false; rd_pend2 := '0';
        stream_addr_inc_pending := false;
        cur_full := false;

      else
      -- Normal capture/readout/write-pump logic (skipped on run-edge cycle)

      -- Block-read toggle edge -> start a stream. Base/Count are stable by now
      -- (set on the OLS side before the toggle flipped and held for the stream).
      if blk_req_edge_r = '1' then
        if rd_mode then
          -- Single-shot: read exactly the host-requested block.
          stream_addr_u := to_unsigned(Blk_Rd_Base + Start_Offset, 22);
          stream_rem    := Blk_Rd_Count;
          stream_addr_inc_pending := false;
          rd_pend2      := '0';
          stream_prime  := STREAM_PRIME_N;
          stream_active := (Blk_Rd_Count /= 0);
          s_rd          <= '0';
        elsif Continuous_Mode = '1' then
          -- Continuous indexed read: host byte address names an absolute sample
          -- index; physical storage is the full SDRAM rolling window.
          cont_base_v := Blk_Rd_Base mod CONT_RING_WORDS;
          stream_addr_u := to_unsigned(cont_base_v, 22);
          stream_rem    := Blk_Rd_Count;
          stream_addr_inc_pending := false;
          rd_pend2      := '0';
          stream_prime  := STREAM_PRIME_N;
          stream_active := (Blk_Rd_Count /= 0);
          rd_mode       := true;
          s_rd          <= '0';
        end if;
      end if;

      stream_addr_r <= std_logic_vector(stream_addr_u);
      if rd_mode then

        if stream_active then
          -- STREAMING READOUT: walk the block addresses, latch on the SDRAM
          -- valid strobe, push each sample into the response FIFO. Self-timed,
          -- so it is immune to the CLK/pclk phase relationship that broke the
          -- old fixed-latency latch at block boundaries.
          if rd_pend2 = '0' then
            if rd_gap = '1' then
              rd_gap := '0';   -- let stream_addr_r catch up post-completion
            elsif rdfifo_wrfull = '0' then
              s_addr <= stream_addr_r;
              s_rd     <= '1';
              rd_pend2 := '1';
              rd_wd_cnt := 0;
            end if;
          elsif s_rvalid = '1' then
            s_rd         <= '0';
            rd_pend2     := '0';
            rd_gap       := '1';
            if stream_prime /= 0 then
              -- Discard the throwaway prime read(s); the real stream starts next.
              stream_prime := stream_prime - 1;
            else
              rdfifo_wdata <= s_rdata;
              rdfifo_wr    <= '1';
              -- Advance the address on COMPLETION (not issue) so a timed-out
              -- read can be reissued at the same address without skipping.
              stream_addr_inc_pending := true;
              if stream_rem <= 1 then
                if Auto_Renew = '1' then
                  -- Auto-renew: reload and keep streaming
                  stream_rem := Blk_Rd_Count;
                else
                  stream_active := false;
                  stream_rem    := 0;
                  -- Continuous mode: hand the SDRAM bus back to the write pump
                  -- after the host has streamed its requested block.
                  if Continuous_Mode = '1' then
                    rd_mode      := false;
                  end if;
                end if;
              else
                stream_rem := stream_rem - 1;
              end if;
            end if;  -- closes stream_prime if/else
          elsif rd_wd_cnt = STREAM_RD_WD then
            -- Unanswered read: drop the request; the next cycle reissues the
            -- same address (see the watchdog comment at the declarations).
            s_rd     <= '0';
            rd_pend2 := '0';
          else
            rd_wd_cnt := rd_wd_cnt + 1;
          end if;    -- closes if rd_pend2 / elsif s_rvalid / watchdog

        elsif Sim then
          -- LEGACY Address-driven readout -> Outputs. Used ONLY by the FLA-direct
          -- testbenches (which read Outputs directly). On real hardware the host
          -- reads exclusively via the response-FIFO block-read path and nothing
          -- consumes Outputs (OLS_Interface's Outputs input is unconnected), so
          -- this walk is dead silicon -- gate it out behind Sim to free LABs (the
          -- device is 100% full). NB the OLS-side Thread23 address walk that feeds
          -- this can NOT also be removed: it still does Run<='0' to terminate a
          -- single-shot capture (OLS_Interface.vhd), so it stays.
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
        end if;

      else
        -- CAPTURE: SDRAM write pump — drains async FIFO (live + flushed
        -- pre-trigger samples arrive via a single FIFO stream).

        -- Single-shot uses the old buffer fullness bookkeeping. Continuous
        -- mode is a true SDRAM ring: never backpressure on the legacy 3x512
        -- buffer flags, because indexed reads use producer/oldest/newest
        -- metadata and the whole SDRAM window is the retention boundary.
        cur_full := false;
        if Continuous_Mode = '0' then
          if (buf_sel = "00" and buf_full(0) = '1')
             or (buf_sel = "01" and buf_full(1) = '1')
             or (buf_sel = "10" and buf_full(2) = '1') then
            cur_full := true;
            if buf_full(0) = '0' then
              buf_sel <= "00"; waddr_0 := 0; buf_rem_0 <= buf_limit_r;
            elsif buf_full(1) = '0' then
              buf_sel <= "01"; waddr_1 := 0; buf_rem_1 <= buf_limit_r;
            elsif buf_full(2) = '0' then
              buf_sel <= "10"; waddr_2 := 0; buf_rem_2 <= buf_limit_r;
            end if;
          end if;
        end if;

        cap_stream_valid <= '0';
        if fifo_rd = '1' then
          -- Pop bubble: the show-ahead FIFO head advances one cycle after the
          -- registered fifo_rd pop, so keep valid low for one cycle after an
          -- accept or the same word is written to SDRAM twice.
          null;
        elsif fifo_rdempty = '0' and not cur_full then
          single_drain_cnt <= 0;
          if Continuous_Mode = '1' then
            write_addr := std_logic_vector(to_unsigned(ring_waddr, 22));
          else
            write_addr := std_logic_vector(to_unsigned(waddr_0, 22));
          end if;

          cap_stream_addr <= write_addr;
          cap_stream_data <= fifo_rdata;

          if Continuous_Mode = '1' or buf_rem_single > 0 then
            cap_stream_valid <= '1';
            pump_valid_v := true;
            if cap_stream_ready = '1' then
              pump_ready_v := true;
              pump_accept_v := true;
            else
              pump_stall_v := true;
            end if;
          end if;

          if cap_stream_ready = '1' then
            -- Normal SDRAM-write path: pop FIFO and update bookkeeping
            fifo_rd <= '1';
            if Continuous_Mode = '1' then
              cont_accept_v := true;
              if ring_waddr = Max_Samples - 1 then
                ring_waddr := 0;
              else
                ring_waddr := ring_waddr + 1;
              end if;
            else
              buf_rem_single <= brem_single_dec;
              waddr_0 := waddr_0 + 1;
            end if;
          elsif producer_done_q = '1' and buf_rem_single <= 0 then
            -- Drain mode: producer finished and all samples written to SDRAM.
            -- Pop remaining FIFO entries (discard) so the drain-completion
            -- logic (elsif below) can count the empty-FIFO window and assert
            -- full_i. Without this, cur_full blocks SDRAM writes and cap_
            -- stream_ready never fires, so the pump stops draining.
            fifo_rd <= '1';
          end if;

        elsif producer_done_q = '1' and fifo_rdempty = '0' then
          -- Fallback flush: when the main path was blocked by cur_full but the
          -- FIFO still has items. Pop and discard.
          fifo_rd <= '1';
          single_drain_cnt <= 0;
        elsif Continuous_Mode = '0' and run_level_r = '1' and not rd_mode
              and producer_done_q = '1' then
          if fifo_rdempty = '1' then
            if single_drain_cnt = 2047 then
              full_i <= '1';
              rd_mode := true;
            else
              single_drain_cnt <= single_drain_cnt + 1;
            end if;
          else
            single_drain_cnt <= 0;
          end if;
        elsif fifo_rdempty = '1' then
          single_drain_cnt <= 0;
          pump_nodata_v := true;
        end if;

        -- Single-shot Full is asserted in the accept branch above when the
        -- final requested SDRAM word is accepted.
      end if; -- end rd_mode
      end if; -- end run_edge_r else

      -- Status
      Status(0) <= run_level_r;
      Status(1) <= cap_stream_valid and not cap_stream_ready;
      Status(2) <= s_rd;
      Status(3) <= full_i;
      Status(4) <= status_overflow;
      Status(5) <= run_stop_overflow;
      Status(7 downto 6) <= (others => '0');

      -- Register the gated condition flags (short path off buf_rem/cur_full),
      -- then advance the wide counters from those 1-bit registered enables on the
      -- next cycle. All five lag by one cycle uniformly, so the throughput ratios
      -- and window deltas the bench reads are unaffected.
      if run_level_r = '1' then
        pump_valid_q  <= pump_valid_v;
        pump_ready_q  <= pump_ready_v;
        pump_accept_q <= pump_accept_v;
        pump_stall_q  <= pump_stall_v;
        pump_nodata_q <= pump_nodata_v;
      else
        pump_valid_q  <= false;
        pump_ready_q  <= false;
        pump_accept_q <= false;
        pump_stall_q  <= false;
        pump_nodata_q <= false;
      end if;
      if PUMP_METRICS then
        if pump_valid_q  then pump_valid_cycles_u  <= pump_valid_cycles_u  + 1; end if;
        if pump_ready_q  then pump_ready_cycles_u  <= pump_ready_cycles_u  + 1; end if;
        if pump_accept_q then pump_accept_cycles_u <= pump_accept_cycles_u + 1; end if;
        if pump_stall_q  then pump_stall_cycles_u  <= pump_stall_cycles_u  + 1; end if;
        if pump_nodata_q then pump_nodata_cycles_u <= pump_nodata_cycles_u + 1; end if;
      end if;

      if cont_meta_reset_q = '1' then
        producer_index_u <= (others => '0');
        oldest_index_u <= (others => '0');
        newest_index_u <= (others => '0');
        overrun_count_u <= (others => '0');
        ring_used <= 0;
      elsif cont_accept_q then
        newest_index_u <= producer_index_u;
        producer_index_u <= producer_index_u + 1;
        if ring_used < CONT_RING_WORDS then
          ring_used <= ring_used + 1;
        else
          oldest_index_u <= oldest_index_u + 1;
          overrun_count_u <= overrun_count_u + 1;
        end if;
      end if;
      cont_accept_q <= cont_accept_v;
      -- Off the REGISTERED address mirror on purpose — see the declaration
      -- comment for the two-cycle-lag validity argument.
      stream_addr_nxt_r <= unsigned(stream_addr_r) + 1;
      if unsigned(stream_addr_r) = to_unsigned(CONT_RING_WORDS - 1, 22) then
        stream_at_ring_end <= '1';
      else
        stream_at_ring_end <= '0';
      end if;
    end if;
  end process;

  Full <= full_i;
  s_burst <= s_burst_i;
  Buffer_Full(0) <= buf_full(0);
  Buffer_Full(2) <= buf_full(2);
  Buffer_Full(1) <= buf_full(1);
  Producer_Index <= std_logic_vector(producer_index_u);
  Oldest_Index <= std_logic_vector(oldest_index_u);
  Newest_Index <= std_logic_vector(newest_index_u);
  Overrun_Count <= std_logic_vector(overrun_count_u);
  Pump_Valid_Cycles <= std_logic_vector(pump_valid_cycles_u);
  Pump_Ready_Cycles <= std_logic_vector(pump_ready_cycles_u);
  Pump_Accept_Cycles <= std_logic_vector(pump_accept_cycles_u);
  Pump_Stall_Cycles <= std_logic_vector(pump_stall_cycles_u);
  Pump_NoData_Cycles <= std_logic_vector(pump_nodata_cycles_u);
  Pump_Overflow_Count <= std_logic_vector(pump_overflow_count_u);

  SDRAM_Interface1 : SDRAM_Interface
  generic map (Sim => Sim, CLK_Frequency => SDRAM_CLK_HZ, Write_Latency => Write_Latency, Read_Latency => Read_Latency, Page_Latency => Page_Latency)
  port map (
    CLK          => pclk,
    Reset        => '0',
    CLK_150_Out  => open,
    Address      => s_addr,
    Write_Enable => s_wr,
    Write_Data   => s_wdata,
    Burst        => s_burst_i,
    Capture_Stream_Valid => cap_stream_valid,
    Capture_Stream_Ready => cap_stream_ready,
    Capture_Stream_Address => cap_stream_addr,
    Capture_Stream_Data => cap_stream_data,
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

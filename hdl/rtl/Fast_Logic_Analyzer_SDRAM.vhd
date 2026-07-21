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
    -- FAST_RAW_BUILD: when true, exclude compression modules at elaboration
    -- time for maximum timing closure. Only fast input sampling, minimal
    -- packing, registered FIFO bridge, SDRAM write pump, and OLS readout
    -- are included. When false, compression modules are available for MSO builds.
    FAST_RAW_BUILD : boolean := true;
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
  attribute altera_attribute : string;

  constant sub_steps : natural := 16 / Channels;

  signal pclk : std_logic;

  signal s_addr  : std_logic_vector(21 downto 0) := (others => '0');
  signal s_wr    : std_logic := '0';
  signal s_wdata : std_logic_vector(15 downto 0) := (others => '0');
  signal s_rd    : std_logic := '0';
  signal s_rdata : std_logic_vector(15 downto 0) := (others => '0');
   signal s_rvalid: std_logic := '0';
  signal full_i      : std_logic := '0';
  signal run_sync1   : std_logic := '0';
  signal run_sync2   : std_logic := '0';
  signal samples_div_p  : natural range 0 to Max_Samples := 0;
  signal samples_div  : natural range 0 to Max_Samples := 0;
  signal samples_div6 : natural range 0 to Max_Samples := 0;

  -- Pipelined divide-by-3 (LPM_DIVIDE with 4-stage pipeline)
  signal lpm_numer : std_logic_vector(21 downto 0) := (others => '0');
  signal lpm_quot  : std_logic_vector(21 downto 0) := (others => '0');

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

  signal buf_rem_single : natural range 0 to Max_Samples := 0;
  signal single_count_load_q : std_logic := '0';
  -- Single-shot drain-complete counter: number of consecutive pclk cycles the pump
  -- FIFO has been empty AFTER the producer-done bit. Local pclk counter (no wide
  -- cross-domain compare), so it stays out of the hot 167 MHz accept path.
  signal single_drain_cnt : natural range 0 to 2047 := 0;

  -- Pipeline registers: pre-compute buf_rem decrements
  signal brem_single_dec : natural range 0 to Max_Samples := 0;

  -- Registered run-edge event detection: breaks run_r → process_5~0 → burst_rem →
  -- fifo_tail → fifo_head → Add18 → LessThan18 → fifo_head_r critical path.
  -- run_level_r replaces the run_r process variable.
  signal run_level_r : std_logic := '0';
  signal run_edge_r  : std_logic := '0';
  signal run_start_r : std_logic := '0';
  signal run_stop_r  : std_logic := '0';

  constant MAX_RATE_DIV : natural := 500_000_000;

  -- Config handshake: CLK -> FAST_CLK
  signal cfg_rate_div  : natural range 1 to MAX_RATE_DIV := 12;
  signal cfg_samples   : natural range 1 to Max_Samples := Max_Samples;
  signal cfg_valid_toggle : std_logic := '0';
  -- Config handshake: FAST_CLK domain
  signal cfg_rate_div_f  : natural range 1 to MAX_RATE_DIV := 12;
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
  signal fifo_overflow_f_q : std_logic := '0';
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
  attribute altera_attribute of run_f_s1 : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of run_f_s2 : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of run_f_level : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";

  -- 1024 (was 2048, originally 4096): trims one afifo pointer bit and the
  -- associated empty/used-word compare logic from the 167 MHz read-side cone.
  -- Functionally the FIFO only has to ride out SDRAM drain stalls (refresh +
  -- page turnaround, ~120 words at 200 MW/s); the DEPTH-320 almost-full
  -- cushion still leaves ~700 words of normal fill headroom.
  constant AFIFO_DEPTH : natural := 1024;
  constant AFIFO_WIDTH : natural := 16;
  constant AFIFO_WIDTHU : natural := 10;
  signal fifo_wdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_wr    : std_logic := '0';
  -- Parallel packed-mode write mux: the afifo is fed from afifo_wdata/afifo_wr,
  -- which select the packed stream when packed_mode_f is set, else the native
  -- sample/analog writer's fifo_wdata/fifo_wr (so packed_mode='0' is bit-for-bit
  -- the original behaviour). packed_mode_f is Packed_Mode 2FF-synced to FAST_CLK.
  signal afifo_wdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal afifo_wr    : std_logic := '0';
  signal packed_buf_in_valid : std_logic := '0';
  signal packed_buf_in_ready : std_logic := '0';
  signal packed_buf_out_data : std_logic_vector(15 downto 0) := (others => '0');
  signal packed_buf_out_valid : std_logic := '0';
  signal packed_buf_out_ready : std_logic := '0';
  signal packed_buf_rst : std_logic := '0';
  signal packed_mode_meta : std_logic := '0';
  signal packed_mode_f    : std_logic := '0';
  signal packed_mode_path_f : std_logic := '0';
  signal fifo_wrusedw : std_logic_vector(AFIFO_WIDTHU-1 downto 0) := (others => '0');
  -- Pipelined almost-full compare (two-stage): fifo_afull_cmp_r is the
  -- wrusedw >= threshold comparison; fifo_afull_r is one more register stage
  -- so the compare result has a full cycle to settle before gating producers.
  signal fifo_afull_cmp_r : std_logic := '0';
  signal fifo_afull_r     : std_logic := '0';
  -- Legacy (deprecated, kept for external compatibility — use fifo_afull_r):
  signal fifo_wralmost_full : std_logic := '0';
  -- Registered skid buffer for the non-packed FAST_CLK write path.
  -- fifo_wr_skid_data_r drives afifo_wdata; fifo_wr_skid_req_r drives afifo_wr.
  signal fifo_wr_skid_data_r : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_wr_skid_req_r  : std_logic := '0';
  -- Registered almost-full for packed-mode backpressure. Same 256-word cushion
  -- as afull_r in the digital writer; keeps the wrusedw compare off the path
  -- from producer -> Packed_Ready -> mso_capture -> analog_packer.
  signal packed_afull_r : std_logic := '0';
  -- FAST_CLK level: '1' once the capture's tick budget is exhausted (driven
  -- from sample_rem_nonzero_r inside gen_fast_speed). Stops the packed
  -- producer at capture end; otherwise its continuous trickle (digital RLE
  -- saturation markers arrive every <= 2.56 us) keeps the FIFO non-empty and
  -- the single-shot drain-completion window (2047 empty pclk cycles) never
  -- elapses, so Full never rises. Left at '0' in non-FAST_SPEED builds
  -- (packed capture is a FAST_SPEED feature).
  signal packed_stop_f : std_logic := '0';
  signal packed_budget_last_r : std_logic := '0';
  -- Run-start gate for the packed producer. The pclk side discards a snapshot
  -- of stale FIFO words on the run edge; the FAST side sees Run a few cycles
  -- earlier than pclk, so packed words pushed immediately at run start can
  -- land inside that snapshot and be discarded — losing the leading analog
  -- block header and mis-framing the whole analog sub-stream. Hold the
  -- producer off until the pclk drain has completed (2FF-synced level).
  signal pump_live_p  : std_logic := '0';
  signal pump_live_s1 : std_logic := '0';
  signal pump_live_f  : std_logic := '0';
  signal fifo_aclr  : std_logic := '0';
  -- FAST_CLK synchronous reset for packed-mode registers (derived from
  -- cfg_valid_edge, already 2FF-synchronized to FAST_CLK). Replaces the old
  -- pclk-domain fifo_aclr that was unsynchronized in FAST_CLK.
  signal packed_rst_f : std_logic := '0';
  -- FIFO read-side prefetch: 1-deep skid buffer between the dcfifo show-ahead
  -- output and the SDRAM write pump. Loaded (and popped) only by the pump
  -- process loader; consumed by the pump accept/drain paths. Keeps the long
  -- M9K address-reg -> q path off the cap_stream_data mux.
  signal prefetch_data_r : std_logic_vector(15 downto 0) := (others => '0');
  signal prefetch_valid_r : std_logic := '0';
  -- Loader settling trackers: rdempty must be low for two consecutive cycles
  -- before q is trusted (real show-ahead q can lag empty by one cycle), and a
  -- pop must be fully settled (fifo_rd, fifo_rd_q both low) before reloading.
  signal rdempty_q  : std_logic := '1';
  signal fifo_rd_q  : std_logic := '0';
  signal fifo_rdata : std_logic_vector(AFIFO_WIDTH-1 downto 0) := (others => '0');
  signal fifo_rd    : std_logic := '0';
  signal fifo_rdempty : std_logic := '0';
  signal fifo_rdempty_r : std_logic := '0';
  -- Drain mode: instead of asynchronously clearing the DCFIFO on every run
  -- start (which created unsafe CDC paths), we drain stale data on the pclk
  -- side. drain_pending_r is set on run_edge_r or overflow_clk; the write
  -- pump discards exactly drain_rem skid-buffer words, where drain_rem is a
  -- SNAPSHOT of rdusedw taken when the drain was requested. A fixed target
  -- (rather than drain-until-empty) matters: at max sample rate the FAST_CLK
  -- producer refills faster than the pclk side pops, so waiting for empty
  -- never terminates (hw-verified deadlock at div=0: producer_index stuck at
  -- 0). The snapshot covers every stale word (they were written a whole run
  -- ago, so the read-side usedw view includes them all) and completes
  -- instantly in the common empty-at-arm case.
  signal drain_pending_r : std_logic := '0';
  signal drain_rem       : natural range 0 to AFIFO_DEPTH := 0;
  signal fifo_rdusedw    : std_logic_vector(AFIFO_WIDTHU-1 downto 0) := (others => '0');
  -- Registered fill snapshot for drain start. The async FIFO's rdusedw / rdptr
  -- compare chain is too deep to drive the drain counter load directly on the
  -- same pclk edge, so we snapshot it here and let the drain state machine pick
  -- it up on the next cycle.
  signal drain_snapshot_pending_r : std_logic := '0';
  signal fifo_rdusedw_r           : natural range 0 to AFIFO_DEPTH := 0;

  -- Readout response FIFO: pclk write (FLA streams samples) / CLK read (OLS
  -- drains). Depth >= one block so a whole block streams without backpressure.
  constant RDFIFO_DEPTH  : natural := 1024;
  constant RDFIFO_WIDTHU : natural := 10;
  signal rdfifo_wdata  : std_logic_vector(15 downto 0) := (others => '0');
  signal rdfifo_wr     : std_logic := '0';
  signal rdfifo_wrfull : std_logic := '0';
  signal rdfifo_wrusedw : std_logic_vector(RDFIFO_WIDTHU - 1 downto 0) := (others => '0');
  -- Registered almost-full gate for the streaming-read issue decision. The
  -- dcfifo's combinational wrfull (wrptr vs synced rdptr compare) fans out to
  -- the whole s_addr/rd_wd_cnt enable cone and was the worst sdram_core_clk
  -- setup path (-0.106 ns, 65% interconnect). Registering the compare one
  -- cycle early cuts the cone at a FF. Safe: only one read is ever
  -- outstanding (rd_pend2), so a 1-cycle-stale gate admits at most one extra
  -- in-flight write against 8 slots of reserved headroom.
  signal rdfifo_afull_r : std_logic := '0';
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
  -- Registered pre-decrement of stream_rem. Read completions are spaced by at
  -- least CAS latency plus the rd_gap bubble, so this 1-cycle look-ahead is
  -- always settled before any consumer needs it.
  signal stream_rem_dec_r   : natural range 0 to Max_Samples := 0;

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

  -- The raw-only profile deliberately has no packed producer. Keeping its
  -- path select at a constant lets Quartus remove the unused MSO branch and
  -- its mode fanout from the 200 MHz write path.
  gen_packed_path_enabled : if not FAST_RAW_BUILD generate
  begin
    packed_mode_path_f <= packed_mode_f;
  end generate;
  gen_packed_path_disabled : if FAST_RAW_BUILD generate
  begin
    packed_mode_path_f <= '0';
  end generate;

  -- The DCFIFO aclr port is tied to '0' permanently. Stale data between capture
  -- runs is handled by draining the FIFO on the pclk side (see drain_pending_r
  -- in the write pump). Async clear on every run start created unsafe CDC timing
  -- paths across both clock domains. For overflow recovery the FAST_CLK writer
  -- stops on fifo_overflow_f and the pclk side drains before resuming.
  fifo_aclr <= '0';
  -- rdfifo_aclr likewise: the readout response FIFO is naturally drained by the
  -- host read logic after each block transfer, so no async clear is needed.
  rdfifo_aclr <= '0';

  pclk <= CLK when Sim else SDRAM_CLK_IN;

  gen_fast_div : if FAST_SPEED generate
  begin
    -- FAST_SPEED captures store one 16-bit word per requested sample. The
    -- /3 divider only exists for the non-fast packer and must not be
    -- elaborated here, otherwise Quartus rejects the now-unused LPM output.
    process(CLK) begin
      if rising_edge(CLK) then
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
        lpm_numer    <= std_logic_vector(to_unsigned(Samples, 22));
        samples_div  <= Samples;
        samples_div6 <= to_integer(unsigned(lpm_quot));
      end if;
    end process;
  end generate;

  CLK_150 <= pclk;
  -- Pipelined almost-full: wrusedw compare is registered, then registered again
  -- so producers see a clean synchronous backpressure signal. The compare output
  -- changes asynchronously when wrusedw (itself a registered dcfifo output)
  -- updates; adding two register stages ensures the producer always sees a stable
  -- value. The threshold is earlier (DEPTH - 320 instead of -256) to account for
  -- the extra pipeline latency, maintaining the same effective headroom.
  -- Pipeline is on FAST_CLK because fifo_wrusedw is synchronous to wrclk=FAST_CLK.
  -- The pclk side never reads wrusedw directly (it uses rdempty for drain detection).
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if unsigned(fifo_wrusedw) >= to_unsigned(AFIFO_DEPTH - 320, AFIFO_WIDTHU) then
        fifo_afull_cmp_r <= '1';
      else
        fifo_afull_cmp_r <= '0';
      end if;
      fifo_afull_r     <= fifo_afull_cmp_r;
      fifo_wralmost_full <= fifo_afull_r;  -- legacy alias, used by afull_r in FAST_CLK
    end if;
  end process;

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

  -- Re-register CLK-domain divide results into pclk domain; pre-compute buffer limits
  process(pclk)
  begin
    if rising_edge(pclk) then
      samples_div_p  <= samples_div;
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

  -- Overflow flag CDC: FAST_CLK domain -> CLK domain (toggle synchronizer).
  -- Toggle once per overflow EVENT (rising edge of the latched flag), not on
  -- every cycle the flag is high: a free-running toggle floods the pclk edge
  -- detector with pulses that keep arriving after the next run has started
  -- and instantly re-abort it (sim-verified: post-overflow capture returned
  -- 0 words).
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      fifo_overflow_f_q <= fifo_overflow_f;
      if fifo_overflow_f = '1' and fifo_overflow_f_q = '0' then
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
    signal cfg_valid_edge_d1 : std_logic := '0';
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
    signal aframe_toggle_last_r : std_logic := '0';
    signal aframe_ready_r : std_logic := '0';
    signal aword_count_pending : natural range 1 to 7 := 7;
    signal aword_count_f : natural range 1 to 7 := 7;
    signal aword_idx    : natural range 0 to 6 := 0;
    signal analog_burst_active : std_logic := '0';
    signal aframe_load_pending_r : std_logic := '0';
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
     signal narrow_word_pending_r : std_logic := '0';
     signal narrow_word_data_r : std_logic_vector(15 downto 0) := (others => '0');
     signal narrow_sample_bit_r : std_logic := '0';
     signal narrow_sample_valid_r : std_logic := '0';
     signal narrow_sample_last_r : std_logic := '0';
  begin
    -- Stage 0: sample pins
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        sample_word_r <= Inputs;
      end if;
    end process;

    -- Keep the wide analog-frame register out of the native digital/narrow
    -- writer process.  Sharing that process caused Quartus to build a common
    -- enable cone from narrow_word_pending_r into aframe_shift, which became
    -- the post-route fast_clk critical path.  The load handshake is already
    -- registered, so this separate process preserves the same cycle while
    -- giving the frame register an independent enable path.
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_valid_edge = '1' then
          aframe_shift <= (others => '0');
        elsif aframe_load_pending_r = '1' and analog_burst_active = '0' then
          aframe_shift <= aframe_pending;
        end if;
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
        if aframe_toggle_s2 /= aframe_toggle_last_r then
          aframe_toggle_last_r <= aframe_toggle_s2;
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
        -- sample_rem_dec_r is a 1-cycle-ahead pipeline of "sample_remaining - 1"
        -- (see comment on the signal decl) that Stage 2d writes back into
        -- sample_remaining the FOLLOWING cycle. That round trip means
        -- sample_remaining(k+1) actually depends on sample_remaining(k-1),
        -- not sample_remaining(k) -- two independent interleaved countdown
        -- chains, one per cycle parity. Reloading sample_remaining alone on
        -- cfg_valid_edge (in Stage 2d below) only resyncs ONE of those two
        -- chains; the other silently keeps counting down from the PREVIOUS
        -- capture's stale value forever, since nothing ever touches it again.
        -- Found 2026-07-10 by tracing a continuous->single-shot packed-mode
        -- transition in tb_packed_continuous_renew Phase 2: sample_remaining
        -- visibly alternated between a freshly-reloaded 20000-scale sequence
        -- and a leftover ~63-scale sequence from the prior capture every
        -- other cycle. When the stale chain's turn to be visible landed on
        -- exactly 0, Stage 2c's clear-check below (mis)fired on that stale
        -- zero and permanently latched sample_rem_nonzero_r low, halting
        -- Packed_Ready with ~19937 genuine samples still remaining. Fix:
        -- resync dec_r to the new budget on the SAME cfg_valid_edge cycle
        -- that resyncs sample_remaining, so both pipeline parities restart
        -- from the new capture together.
        -- Reload with a straight copy of cfg_samples (no "-1" subtraction):
        -- an extra wide subtractor here, in parallel with the one below,
        -- cost enough fast_clk timing margin to push this domain negative
        -- (measured: fast_clk slack went from +0.094ns to -0.497ns with a
        -- "cfg_samples - 1" version). Being one cycle "long" on the very
        -- first reload of a multi-thousand-to-million-sample budget is
        -- functionally negligible and still fixes the real bug (both
        -- pipeline parities restart from the new capture together instead
        -- of one silently continuing the PREVIOUS capture's countdown).
        if cfg_valid_edge = '1' then
          sample_rem_dec_r <= cfg_samples;
        elsif sample_remaining > 0 then
          sample_rem_dec_r <= sample_remaining - 1;
        else
          sample_rem_dec_r <= 0;
        end if;

        -- One-cycle-delayed copy of cfg_valid_edge: extra defense-in-depth
        -- for the elsif branch below (kept from the earlier fix attempt;
        -- harmless now that the dec_r resync above addresses the actual
        -- root cause).
        cfg_valid_edge_d1 <= cfg_valid_edge;

        if cfg_valid_edge = '1' then
          sample_rem_nonzero_r <= '1';
        elsif cfg_valid_edge_d1 = '0'
              and (fifo_wr = '1' or packed_budget_last_r = '1')
              and sample_remaining = 0 then
          -- Use continuous_f (the FAST_CLK-synchronized copy, driven near
          -- line 636), NOT the raw Continuous_Mode port -- an earlier version
          -- of this fix used the raw port directly and it caused a real,
          -- reproducible bug: at the exact boundary between a continuous
          -- capture ending and a new single-shot packed capture starting,
          -- sampling the async port let sample_rem_nonzero_r latch low
          -- (falling into the single-shot "done" branch) on stale/transitional
          -- data, so the new capture reported instant completion with
          -- producer_index=0 and zero real words. Every other FAST_CLK-domain
          -- use of continuous mode in this generate block goes through
          -- continuous_f for the same reason (e.g. the afull_r/continuous_f
          -- checks below) -- this fix now matches that convention.
          if packed_mode_path_f = '1' and continuous_f = '1' then
            -- Packed continuous/live capture: auto-renew the budget instead
            -- of halting Packed_Ready forever. The plain digital/narrow/
            -- analog-frame producers' fifo_wr pulse is NOT gated by
            -- sample_rem_nonzero_r (only this counter and the one-shot
            -- cap_done_toggle_f below are), so continuous captures on those
            -- paths already run indefinitely without needing a reload here.
            -- Packed mode is different: Packed_Ready is hard-gated by
            -- packed_stop_f <= not sample_rem_nonzero_r, so without this
            -- renew every packed continuous/live capture would permanently
            -- stop producing words after exactly cfg_samples fast_clk cycles
            -- (~20.9 ms at the 4,194,304-sample budget stream_ring_capture
            -- always requests) and never resume -- Stage 2d (below) reloads
            -- sample_remaining <= cfg_samples on this same edge.
            sample_rem_nonzero_r <= '1';
          else
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
      end if;
    end process;

    -- Packed producer halt: budget exhausted (registered level, same domain).
    packed_stop_f <= not sample_rem_nonzero_r;

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
      variable narrow_count_v : natural range 0 to 15;
      variable narrow_valid_v : std_logic;
      variable narrow_bit_v   : std_logic;
      variable narrow_last_v  : std_logic;
    begin
      if rising_edge(FAST_CLK) then
        narrow_count_v := narrow_bit_count_r;
        narrow_valid_v := narrow_sample_valid_r;
        narrow_bit_v := narrow_sample_bit_r;
        narrow_last_v := narrow_sample_last_r;
        fifo_wr <= '0';
        packed_budget_last_r <= '0';
        bram_wren <= '0';
        afull_r <= fifo_wralmost_full;

        -- fifo_wdata datapath mux (DECOUPLED from capture_en_r).
        -- The 16-bit data source is chosen purely from the quasi-static mode
        -- flags (narrow_word_pending_r / astream_f) and the burst index
        -- aword_idx; capture_en_r / sample_tick_r / afull_r only gate the 1-bit
        -- write STROBE (fifo_wr) in the control cascade below. Previously the
        -- data mux sat at the bottom of a 5-deep capture_en_r priority cascade,
        -- so the 200 MHz hot path was capture_en_r -> cascade -> fifo_wdata[15].
        -- Loading fifo_wdata every cycle is harmless: the async FIFO only
        -- consumes it when fifo_wr is asserted, which the control logic still
        -- gates exactly as before. narrow/analog/digital modes are mutually
        -- exclusive, so priority order here only resolves impossible overlaps.
        if narrow_word_pending_r = '1' then
          fifo_wdata <= narrow_word_data_r;
        elsif astream_f = '1' then
          case aword_idx is
            when 0      => fifo_wdata <= aframe_shift(15 downto 0);
            when 1      => fifo_wdata <= aframe_shift(31 downto 16);
            when 2      => fifo_wdata <= aframe_shift(47 downto 32);
            when 3      => fifo_wdata <= aframe_shift(63 downto 48);
            when 4      => fifo_wdata <= aframe_shift(79 downto 64);
            when 5      => fifo_wdata <= aframe_shift(95 downto 80);
            when others => fifo_wdata <= aframe_shift(111 downto 96);
          end case;
        else
          fifo_wdata <= sample_word_r;
        end if;

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
        aword_idx <= 0;
        analog_burst_active <= '0';
        narrow_shift_r <= (others => '0');
        narrow_bit_count_r <= 0;
          narrow_word_pending_r <= '0';
        narrow_sample_bit_r <= '0';
        narrow_sample_valid_r <= '0';
        narrow_sample_last_r <= '0';
        aframe_load_pending_r <= '0';
        narrow_count_v := 0;
        narrow_valid_v := '0';
        narrow_bit_v := '0';
        narrow_last_v := '0';
        end if;

        if fifo_overflow_f = '0' then
          if capture_en_r = '1' and packed_mode_path_f = '1' then
            -- Packed mode (mso_capture) samples digital_in unconditionally
            -- every fast_clk cycle -- no Rate_Div/sample_tick_r gating (see
            -- mso_capture.vhd) -- and its own afifo write-port mux
            -- (afifo_wdata/afifo_wr above) routes the elastic packed stream
            -- into the afifo. The packed branch deliberately does not assert
            -- fifo_wr: that request is reserved for ordinary producers, while
            -- packed_budget_last_r supplies the single-shot completion event.
            -- Deplete the Samples budget at that SAME full-rate
            -- cadence instead of falling through to the Rate_Div-gated
            -- sample_tick_r branch below, which only ever applied to the
            -- plain digital/narrow/analog-frame producers.
            --
            -- BEFORE THIS FIX: the shared sample_remaining/packed_stop_f
            -- budget (packed_stop_f <= not sample_rem_nonzero_r, gating
            -- Packed_Ready) was decremented by the stale sample_tick_r
            -- branch below instead, so a HIGHER requested "rate_hz"
            -- (irrelevant to packed mode) burned through the budget FASTER
            -- in wall-clock time, halting the packed producer sooner the
            -- more aggressively a caller asked for speed -- backwards from
            -- every other capture mode. Measured: real decoded sample
            -- throughput scaled INVERSELY with requested rate_hz (e.g.
            -- ~96 MS/s effective at a 2 MS/s request vs ~4 MS/s at 100
            -- MS/s), capping packed-mode throughput far below what the
            -- inline compressor can actually sustain.
            if sample_rem_nonzero_r = '1' then
              -- continuous_f (synced), not the raw Continuous_Mode port --
              -- see the Stage 2c comment above for the bug this caused.
              if sample_remaining = 0 and continuous_f = '1' then
                -- Auto-renew (mirrors Stage 2c's reload on the same edge):
                -- reload the budget instead of freezing at 0, so packed
                -- continuous/live capture never permanently halts.
                sample_remaining <= cfg_samples;
              else
                sample_remaining <= sample_rem_dec_r;
                if sample_remaining = 1 and continuous_f = '0' then
                  packed_budget_last_r <= '1';
                end if;
              end if;
            end if;
          elsif narrow_word_pending_r = '1' and afull_r = '0' then
            -- fifo_wdata (= narrow_word_data_r) is driven by the decoupled data
            -- mux above; here we only assert the write strobe and clear pending.
            fifo_wr <= '1';
            narrow_word_pending_r <= '0';
            if sample_rem_nonzero_r = '1' then
              sample_remaining <= sample_rem_dec_r;
            end if;
          -- Pre-trigger BRAM is digital-only; skip it entirely in analog stream
          -- mode so only ADC frame words enter the FIFO.
          elsif pretrig_en_r = '1' and pretrig_tick_cnt < 8 and astream_f = '0' then
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
            if aframe_load_pending_r = '1' and analog_burst_active = '0' then
              -- Snapshot the coherent frame once; it is NOT shifted (see below).
              aword_count_f <= aword_count_pending;
              aword_idx <= 0;
              analog_burst_active <= '1';
              aframe_load_pending_r <= '0';
            elsif aframe_ready_r = '1' and analog_burst_active = '0'
               and sample_rem_nonzero_r = '1' then
              aframe_load_pending_r <= '1';
            end if;

            -- Emit word[aword_idx] via the 16-bit 8:1 mux in the decoupled data
            -- block above (aframe_shift selected purely from aword_idx). Shifting
            -- the whole 128-bit frame each cycle, or gating that wide mux with the
            -- late-arriving afifo almost-full flag, would not close timing (every
            -- seed -0.64..-1.12 ns). Here only the 1-bit write strobe and the
            -- 3-bit burst index remain gated by capture/almost-full.
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
            -- Queue the selected bit first, then consume the previously queued
            -- bit on this edge. That keeps the muxed input off the same cone as
            -- the shift-register update.
            if narrow_valid_v = '1' then
              if narrow_last_v = '1' then
                narrow_word_data_r(14 downto 0) <= narrow_shift_r(14 downto 0);
                narrow_word_data_r(15) <= narrow_bit_v;
                narrow_word_pending_r <= '1';
                narrow_shift_r <= (others => '0');
                narrow_count_v := 0;
              else
                narrow_shift_r(narrow_count_v) <= narrow_bit_v;
                narrow_count_v := narrow_count_v + 1;
              end if;
            end if;

            if narrow_channel_f < Channels then
              narrow_bit_v := sample_word_r(narrow_channel_f);
            else
              narrow_bit_v := '0';
            end if;
            if narrow_count_v = 15 then
              narrow_last_v := '1';
            else
              narrow_last_v := '0';
            end if;
            narrow_valid_v := '1';

            if afull_r = '1' and continuous_f = '0' then
              fifo_overflow_f <= '1';
            end if;

          elsif capture_en_r = '1' and sample_tick_r = '1' then
            -- fifo_wdata (= sample_word_r) is driven by the decoupled data mux
            -- above; this branch only asserts the write strobe and decrements
            -- the sample budget, keeping capture_en_r off the fifo_wdata cone.
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
        narrow_bit_count_r <= narrow_count_v;
        narrow_sample_bit_r <= narrow_bit_v;
        narrow_sample_valid_r <= narrow_valid_v;
        narrow_sample_last_r <= narrow_last_v;
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
  -- pclk-domain level: run active and the run-start stale drain finished.
  pump_live_p <= run_level_r and not drain_pending_r;

  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      packed_mode_meta <= Packed_Mode;
      packed_mode_f    <= packed_mode_meta;
      packed_afull_r   <= fifo_wralmost_full;
      pump_live_s1     <= pump_live_p;
      pump_live_f      <= pump_live_s1;
    end if;
  end process;

  -- FAST_CLK-domain synchronous reset for packed-mode registers, derived from
  -- cfg_valid_edge (already 2FF-synchronized to FAST_CLK) and fifo_overflow_f.
  -- Replaces the old pclk-domain fifo_aclr which was unsynchronized in FAST_CLK.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if cfg_valid_edge = '1' or fifo_overflow_f = '1' then
        packed_rst_f <= '1';
      else
        packed_rst_f <= '0';
      end if;
    end if;
  end process;
  -- Write-port source mux. The registered-ready elastic buffer breaks the
  -- producer-ready -> producer-data timing loop identified by STA. It is
  -- conservative when full, but preserves ordering and never drops a word.
  packed_buf_rst <= '1' when packed_rst_f = '1' or run_f_level = '0'
                              else '0';
  packed_buf_in_valid <= '1' when Packed_Valid = '1' and packed_mode_path_f = '1'
                              and run_f_level = '1' and packed_stop_f = '0'
                              and pump_live_f = '1' else '0';
  packed_buf_out_ready <= not packed_afull_r;

  packed_stream_buf : entity work.fast_capture_elastic_buffer
    generic map (DATA_WIDTH => 16)
    port map (
      clk       => FAST_CLK,
      rst       => packed_buf_rst,
      in_data   => Packed_Data,
      in_valid  => packed_buf_in_valid,
      in_ready  => packed_buf_in_ready,
      out_data  => packed_buf_out_data,
      out_valid => packed_buf_out_valid,
      out_ready => packed_buf_out_ready
    );

  Packed_Ready <= '1' when packed_mode_path_f = '1' and run_f_level = '1'
                       and packed_stop_f = '0' and pump_live_f = '1'
                       and packed_buf_in_ready = '1' else '0';

  -- Non-packed FAST_CLK skid buffer: registers fifo_wdata and fifo_wr from the
  -- producer processes so the dcfifo write port sees clean registered signals.
  -- This breaks the producer combinational logic -> dcfifo data/wrreq timing path
  -- at 200 MHz. A single register stage adds one cycle of latency, which the
  -- 320-word almost-full cushion absorbs.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      -- No async clear: data follows the producer stream synchronously.
      fifo_wr_skid_data_r <= fifo_wdata;
      fifo_wr_skid_req_r  <= fifo_wr;
    end if;
  end process;

  -- Packed and ordinary producers are mutually exclusive. Select data from
  -- the registered packed-valid bit and OR the registered write requests;
  -- packed_mode_f is no longer on the async FIFO write-port control path.
  afifo_wdata  <= packed_buf_out_data when packed_buf_out_valid = '1'
                  else fifo_wr_skid_data_r;
  afifo_wr     <= (packed_buf_out_valid and packed_buf_out_ready)
                  or fifo_wr_skid_req_r;

  -- Async FIFO (dcfifo) bridging FAST_CLK (write) to pclk (read).
  -- Sync depths increased to 4 for safer CDC across the ~200 MHz / ~167 MHz
  -- boundary. The skid buffers upstream ensure data/wrreq are registered.
  afifo : dcfifo
    generic map (
      lpm_width       => AFIFO_WIDTH,
      lpm_widthu      => AFIFO_WIDTHU,
      lpm_numwords    => AFIFO_DEPTH,
      lpm_showahead   => "ON",
      lpm_type        => "dcfifo",
      rdsync_delaypipe => 4,
      wrsync_delaypipe => 4,
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
      wrfull   => open,
      wrusedw  => fifo_wrusedw,
      rdusedw  => fifo_rdusedw
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
      rdsync_delaypipe => 4,
      wrsync_delaypipe => 4,
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
      wrusedw  => rdfifo_wrusedw,
      rdusedw  => open
    );

  -- (The readback delta compressor used to sit here in the pclk domain. It
  -- was moved to OLS_Interface's CLK-domain block drain: 322 LCs of packing
  -- logic in the congested 167 MHz SDRAM cone cost timing closure, and the
  -- readout drain at 100 MHz has both headroom and the natural place to
  -- produce the variable-length response.)


  -- ============================================================
  -- pclk-side FIFO read prefetch (1-deep skid buffer)
  -- ============================================================
  -- prefetch_data_r/prefetch_valid_r form a proper 1-deep skid buffer loaded
  -- by the write-pump process below, which is the SOLE owner of fifo_rd.
  -- The registers break the long M9K read-address-reg -> q -> cap_stream_data
  -- and empty-comparator -> pump combinational paths, same as the original
  -- free-running prefetch, but with explicit load/consume handshakes.
  --
  -- Why not a free-running "prefetch_data_r <= fifo_rdata every cycle" copy:
  -- on real silicon a show-ahead dcfifo with registered M9K output can present
  -- q one cycle AFTER rdempty deasserts, so a valid flag derived only from
  -- rdempty pairs garbage data with valid='1' on every first-word fall-through
  -- (on hardware this wrote a junk word before every real sample - the
  -- sample-duplication bug reintroduced, see git history 2026-07-04). The
  -- loader below therefore requires empty to have been low for TWO consecutive
  -- cycles before trusting q, and waits out its own pop settling window.
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
    -- FIFO pops are owned exclusively by the prefetch loader at the top of
    -- this process; the accept/drain paths only consume the skid buffer
    -- (prefetch_valid_r <= '0'). This makes duplicate SDRAM writes impossible
    -- by construction: a word is committed at most once per load, and the
    -- loader's settling guards make a load grab the head word at most once.
    -- (Historic context: both the original 2026-07-02 duplication bug and its
    -- 2026-07-04 reintroduction came from pop/valid timing races between a
    -- registered pop and the show-ahead q/empty outputs.)
    -- Continuous-mode readout: a host block read temporarily enters rd_mode to
    -- stream the rolling SDRAM window, then returns to capture.
    -- cur_full backpressures the write pump off a full buffer.
    variable cur_full      : boolean := false;
    variable cont_base_v   : natural range 0 to Max_Samples - 1 := 0;
    variable ring_waddr    : natural range 0 to Max_Samples - 1 := 0;
    -- Snapshot the persistent readout state at the start of each pclk tick so
    -- current-cycle updates do not feed back into the same cycle's mux tree.
    variable rd_mode_cur       : boolean := false;
    variable stream_rem_cur    : natural range 0 to Max_Samples := 0;
    variable stream_active_cur : boolean := false;
    variable rd_pend2_cur      : std_logic := '0';
    variable rd_gap_cur        : std_logic := '0';
    variable pump_valid_v  : boolean := false;
    variable pump_ready_v  : boolean := false;
    variable pump_accept_v : boolean := false;
    variable pump_stall_v  : boolean := false;
    variable pump_nodata_v : boolean := false;
    variable cont_accept_v : boolean := false;
  begin
    if rising_edge(pclk) then
      rd_mode_cur := rd_mode;
      stream_rem_cur := stream_rem;
      stream_active_cur := stream_active;
      rd_pend2_cur := rd_pend2;
      rd_gap_cur := rd_gap;
      pump_valid_v := false;
      pump_ready_v := false;
      pump_accept_v := false;
      pump_stall_v := false;
      pump_nodata_v := false;
      cont_accept_v := false;
      fifo_rd <= '0';
      s_wr <= '0';
      cap_stream_valid <= '0';
      rdfifo_wr <= '0';
      fifo_rdempty_r <= fifo_rdempty;
      -- wrusedw wraps to 0 at full on dcfifo, so OR in wrfull; both cones
      -- terminate in this FF instead of fanning out to the issue logic.
      if rdfifo_wrfull = '1'
         or unsigned(rdfifo_wrusedw) >= RDFIFO_DEPTH - 8 then
        rdfifo_afull_r <= '1';
      else
        rdfifo_afull_r <= '0';
      end if;
      -- Loader settling trackers (see skid-buffer comment above the process).
      fifo_rd_q <= fifo_rd;
      -- Chain off fifo_rdempty_r (not the live fifo_rdempty) so rdempty_q and
      -- fifo_rdempty_r hold DISTINCT cycles: fifo_rdempty_r = empty one cycle
      -- ago, rdempty_q = empty two cycles ago. Before this fix both loaded
      -- from the same source on the same edge and were always equal, so the
      -- "empty low for two consecutive cycles" guard below (intended: real
      -- show-ahead q can lag the empty flag by one cycle on silicon) had
      -- silently degenerated to a one-cycle check since 852572f4, which moved
      -- the loader condition onto fifo_rdempty_r for timing but left this
      -- assignment on the live signal.
      rdempty_q <= fifo_rdempty_r;
      if fifo_rdempty = '0' and unsigned(fifo_rdusedw) = 0 then
        fifo_rdusedw_r <= AFIFO_DEPTH;
      else
        fifo_rdusedw_r <= to_integer(unsigned(fifo_rdusedw));
      end if;
      -- Prefetch loader: capture the show-ahead head word into the skid buffer
      -- and pop it, in the same cycle. Guards: skid empty; rdempty low for two
      -- consecutive cycles (q can lag empty by one cycle on silicon); previous
      -- pop fully settled (fifo_rd and fifo_rd_q low). run_edge_r reset below
      -- overrides prefetch_valid_r for the run-start cycle.
      if prefetch_valid_r = '0' and fifo_rdempty_r = '0' and rdempty_q = '0'
         and fifo_rd = '0' and fifo_rd_q = '0' then
        prefetch_data_r  <= fifo_rdata;
        prefetch_valid_r <= '1';
        fifo_rd <= '1';
      end if;
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
      if stream_rem_cur /= 0 then
        stream_rem_dec_r <= stream_rem_cur - 1;
      else
        stream_rem_dec_r <= 0;
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
        drain_pending_r <= '1';
        drain_snapshot_pending_r <= '1';
        -- Snapshot as at run start. Overflow only latches in single-shot
        -- mode, where the producer has already stopped, so the fill can only
        -- shrink and a re-snapshot on repeated overflow pulses stays correct.
        if Continuous_Mode = '0' and full_i = '0' then
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
        if buf_sel = "00" and buf_full(1) = '1' then
          -- A was waiting to be written (B is full), reset pointer now
          waddr_0 := 0;
        end if;
      end if;
      if Buffer_Ack(1) = '1' then
        buf_full(1) <= '0';
        if buf_sel = "01" and buf_full(0) = '1' then
          -- B was waiting to be written (A is full), reset pointer now
          waddr_1 := 0;
        end if;
      end if;
      if Buffer_Ack(2) = '1' then
        buf_full(2) <= '0';
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
        if full_pending = '1' and prefetch_valid_r = '0'
           and fifo_rdempty_r = '1' and rdempty_q = '1'
           and cap_stream_valid = '0' then
          full_i <= '1';
          full_pending <= '0';
        end if;
      end if;

      if run_edge_r = '1' then
        waddr_0 := 0; waddr_1 := 0; waddr_2 := 0;
        drain_pending_r <= '1';
        drain_snapshot_pending_r <= '1';
        -- Snapshot the stale-word count. usedw is fill mod DEPTH: a full FIFO
        -- reads 0, so substitute the full depth when non-empty (the empty-
        -- pipeline drain exit absorbs any overestimate).
        -- Invalidate any word still sitting in the skid buffer from the
        -- previous run (overrides a same-cycle loader load).
        prefetch_valid_r <= '0';
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

      -- Drain mode: on run start or overflow recovery, discard the snapshot
      -- count of stale FIFO words before allowing normal operation. This
      -- replaces the old async fifo_aclr which created unsafe CDC timing
      -- paths. The loader keeps refilling the skid buffer; this block
      -- discards each loaded word. Terminates on drain_rem = 0 (target met;
      -- immune to a producer that refills faster than we pop) or on a
      -- genuinely empty pipeline (covers the usedw-snapshot overestimate when
      -- the FIFO was exactly full, where usedw wraps to 0 and the snapshot
      -- logic substitutes AFIFO_DEPTH).
      if drain_pending_r = '1' then
        if drain_snapshot_pending_r = '1' then
          drain_rem <= fifo_rdusedw_r;
          drain_snapshot_pending_r <= '0';
        elsif drain_rem = 0 then
          drain_pending_r <= '0';
        elsif prefetch_valid_r = '1' then
          prefetch_valid_r <= '0';  -- discard one stale word
          drain_rem <= drain_rem - 1;
        elsif fifo_rdempty_r = '1' and rdempty_q = '1'
              and fifo_rd = '0' and fifo_rd_q = '0' then
          drain_pending_r <= '0';  -- FIFO ran dry before target: stale gone
          drain_rem <= 0;
        end if;
      end if;
      -- Block-read toggle edge -> start a stream. Base/Count are stable by now
      -- (set on the OLS side before the toggle flipped and held for the stream).
      if blk_req_edge_r = '1' then
        if rd_mode_cur then
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
      if rd_mode_cur then

        if stream_active_cur then
          -- STREAMING READOUT: walk the block addresses, latch on the SDRAM
          -- valid strobe, push each sample into the response FIFO. Self-timed,
          -- so it is immune to the CLK/pclk phase relationship that broke the
          -- old fixed-latency latch at block boundaries.
          if rd_pend2_cur = '0' then
            if rd_gap_cur = '1' then
              rd_gap := '0';   -- let stream_addr_r catch up post-completion
            elsif rdfifo_afull_r = '0' then
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
              if stream_rem_cur <= 1 then
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
                stream_rem := stream_rem_dec_r;
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
              buf_sel <= "00"; waddr_0 := 0;
            elsif buf_full(1) = '0' then
              buf_sel <= "01"; waddr_1 := 0;
            elsif buf_full(2) = '0' then
              buf_sel <= "10"; waddr_2 := 0;
            end if;
          end if;
        end if;

        cap_stream_valid <= '0';
        if drain_pending_r = '1' then
          -- Drain owns the skid buffer this cycle (block above); committing
          -- here would leak stale words from the previous run into SDRAM.
          null;
        elsif prefetch_valid_r = '1' and not cur_full then
          single_drain_cnt <= 0;
          if Continuous_Mode = '1' then
            write_addr := std_logic_vector(to_unsigned(ring_waddr, 22));
          else
            write_addr := std_logic_vector(to_unsigned(waddr_0, 22));
          end if;

          cap_stream_addr <= write_addr;
          cap_stream_data <= prefetch_data_r;

          if Continuous_Mode = '1' or buf_rem_single > 0 then
            -- Registered valid/ack handshake. cap_stream_ready is a 2-stage
            -- pipelined ack OF cap_stream_valid (ready_now needs valid; see
            -- SDRAM_Controller_Custom r_pipe_stream_ready), so it arrives
            -- late and can stay high for several cycles. Accepting on ready
            -- alone therefore transfers the same word more than once (the
            -- 2026-07-04 duplication regression). Transfer exactly once:
            -- hold valid+addr+data until ready is seen WHILE valid is high,
            -- then drop valid (top-of-process default) and consume the word.
            if cap_stream_valid = '1' and cap_stream_ready = '1' then
              pump_valid_v := true;
              pump_ready_v := true;
              pump_accept_v := true;
              prefetch_valid_r <= '0';
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
            else
              cap_stream_valid <= '1';
              pump_valid_v := true;
              if cap_stream_ready = '0' then
                pump_stall_v := true;
              end if;
            end if;
          elsif producer_done_q = '1' then
            -- Budget exhausted and producer finished: discard remaining words
            -- so the drain-completion logic (elsif below) can count the empty
            -- window and assert full_i. Without this, cur_full blocks SDRAM
            -- writes and cap_stream_ready never fires, so the pump stops
            -- draining.
            prefetch_valid_r <= '0';
          end if;

        elsif producer_done_q = '1' and prefetch_valid_r = '1' then
          -- Fallback flush: when the main path was blocked by cur_full but the
          -- FIFO still has items. Discard.
          prefetch_valid_r <= '0';
          single_drain_cnt <= 0;
        elsif Continuous_Mode = '0' and run_level_r = '1' and not rd_mode
              and producer_done_q = '1' then
          if prefetch_valid_r = '0' then
            if single_drain_cnt = 2047 then
              full_i <= '1';
              rd_mode := true;
            else
              single_drain_cnt <= single_drain_cnt + 1;
            end if;
          else
            single_drain_cnt <= 0;
          end if;
        elsif prefetch_valid_r = '0' then
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
    Capture_Stream_Valid => cap_stream_valid,
    Capture_Stream_Ready => cap_stream_ready,
    Capture_Stream_Address => cap_stream_addr,
    Capture_Stream_Data => cap_stream_data,
    Read_Enable  => s_rd,
    Read_Data    => s_rdata,
    Read_Valid   => s_rvalid,
    Busy         => open,
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

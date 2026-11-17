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
    -- Full feature build by default; use the explicit RawOnly profile to
    -- elide the MSO compression pipeline for diagnostic timing experiments.
    FAST_RAW_BUILD : boolean := false;
    CLK_Frequency : natural := 100_000_000;
    SDRAM_CLK_HZ : natural := 166_666_667;
    SAMPLE_CLK_HZ : natural := 200_000_000;
    Write_Latency : natural := 10;
    Read_Latency  : natural := 3;
    Page_Latency  : natural := 3;
    -- Compile-time diagnostic instrumentation. Kept off in production builds
    -- because the five wide counters cost timing/area; benches may enable it.
    Enable_Pump_Metrics : boolean := false
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
    -- CLK-domain level: freeze the Producer/Oldest/Newest/Overrun/Overflow output
    -- snapshot while high (set by OLS_Interface across a packet's execution).
    Index_Hold      : in std_logic := '0';
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
    -- Registered tap of the accepted capture words. OLS_Interface uses this
    -- for CMD_START_LIVE_STREAM; the SDRAM writer remains an independent
    -- consumer of the same capture stream.
    Live_Stream_Data  : out std_logic_vector(15 downto 0) := (others => '0');
    Live_Stream_Valid : out std_logic := '0';
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

  signal buf_rem_single : natural range 0 to Max_Samples := 0;
  signal single_count_load_q : std_logic := '0';
  -- Single-shot drain-complete counter: number of consecutive pclk cycles the pump
  -- FIFO has been empty AFTER the producer-done bit. Local pclk counter (no wide
  -- cross-domain compare), so it stays out of the hot 167 MHz accept path.
  signal single_drain_cnt : natural range 0 to 2047 := 0;


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
  signal pump_budget_done_q : std_logic := '0';

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
  signal Armed_f_d  : std_logic := '0';
  signal Armed_f_d2 : std_logic := '0';
  signal run_f_level : std_logic := '0';
  signal fifo_overflow_f  : std_logic := '0';
  signal fifo_overflow_f_q : std_logic := '0';
  signal overflow_toggle  : std_logic := '0';
  signal overflow_t_s1    : std_logic := '0';
  signal overflow_t_s2    : std_logic := '0';
  signal overflow_t_s3    : std_logic := '0';
  signal overflow_clk     : std_logic := '0';
  -- Raw single-shot "keep" overflow (stage 2a): the sampler stopped because the
  -- afifo is genuinely about to fill; the words already in the FIFO are KEPT
  -- and written to SDRAM. It crosses to pclk on its own toggle synchroniser.
  signal ovf_keep_f        : std_logic := '0';
  signal ovf_keep_toggle   : std_logic := '0';
  signal ovf_k_s1          : std_logic := '0';
  signal ovf_k_s2          : std_logic := '0';
  signal ovf_k_s3          : std_logic := '0';
  signal keep_clk          : std_logic := '0';
  signal overflow_keep_r   : std_logic := '0';
  signal fifo_rawfull_cmp_r : std_logic := '0';
  -- Dropped-sample counter (continuous mode). drop_ev_r is a one-cycle FAST_CLK
  -- event for every sample / narrow word the sampler could not push because the
  -- capture FIFO was not accepting. drop_cnt_f saturates at 0xFFFF; it is
  -- crossed to pclk with a request/acknowledge snapshot (drop_snap_f is held
  -- stable between request and acknowledge) and held by Index_Hold like the
  -- ring indices. Host: REG_PUMP_VALID_CYCLES (0x60), bits 15:0.
  signal drop_ev_r      : std_logic := '0';
  signal drop_cnt_f     : unsigned(15 downto 0) := (others => '0');
  signal drop_snap_f    : unsigned(15 downto 0) := (others => '0');
  signal drop_ack_f     : std_logic := '0';
  signal drop_req_s1    : std_logic := '0';
  signal drop_req_s2    : std_logic := '0';
  signal drop_req_p     : std_logic := '0';
  signal drop_ack_s1    : std_logic := '0';
  signal drop_ack_s2    : std_logic := '0';
  signal drop_val_p     : std_logic_vector(15 downto 0) := (others => '0');
  -- wrusedw register stage: the dcfifo's usedw subtractor (write pointer minus
  -- the synchronised read pointer) is a long path at 13 bits, so the almost-
  -- full and raw-full compares start from this register, not from the megafunction
  -- output.
  signal raw_full_r        : std_logic := '0';
  signal overflow_count_en_q : std_logic := '0';
  signal sample_remaining : natural range 0 to Max_Samples := 0;
  signal run_stop_overflow : std_logic := '0';
  signal status_overflow   : std_logic := '0';
  signal overflow_readout_q : std_logic := '0';
  signal producer_index_u  : unsigned(31 downto 0) := (others => '0');
  -- oldest_index_u also drives Overrun_Count: the two were always reset and
  -- incremented together, so they held identical values.
  signal oldest_index_u    : unsigned(31 downto 0) := (others => '0');
  signal newest_index_u    : unsigned(31 downto 0) := (others => '0');
  -- Write-pump utilisation counters (host regs 0x60..0x64). Diagnostic only
  -- (read by host/debug/_deep_rate_sweep.py); compiled out by default to
  -- reclaim five 32-bit counters (~200 LEs) from the congested 167 MHz cone
  -- — at 99% device utilisation they cost timing closure and can push the
  -- fitter past device capacity. The register map is unchanged: reads
  -- return zero. Pump_Overflow_Count (0x65) stays — it counts real capture
  -- overflow aborts.
  signal pump_valid_cycles_u   : unsigned(31 downto 0) := (others => '0');
  signal pump_ready_cycles_u   : unsigned(31 downto 0) := (others => '0');
  signal pump_accept_cycles_u  : unsigned(31 downto 0) := (others => '0');
  signal pump_stall_cycles_u   : unsigned(31 downto 0) := (others => '0');
  signal pump_nodata_cycles_u  : unsigned(31 downto 0) := (others => '0');
  -- 8-bit saturating: counts capture-overflow events within one run.
  signal pump_overflow_count_u : unsigned(7 downto 0) := (others => '0');
  -- pclk-domain snapshot of the ring counters that the CLK-domain dispatcher
  -- reads. They follow the live counters every pclk cycle unless the
  -- synchronised Index_Hold is high.
  signal index_hold_s1    : std_logic := '0';
  signal index_hold_p     : std_logic := '0';
  signal producer_snap_r  : unsigned(31 downto 0) := (others => '0');
  signal oldest_snap_r    : unsigned(31 downto 0) := (others => '0');
  signal newest_snap_r    : unsigned(31 downto 0) := (others => '0');
  signal overflow_snap_r  : unsigned(7 downto 0) := (others => '0');
  -- pclk-domain 2FF copy of the CLK-domain Continuous_Mode level.
  signal continuous_p_s1  : std_logic := '0';
  signal continuous_p     : std_logic := '0';
  attribute altera_attribute of index_hold_s1   : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of index_hold_p    : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of continuous_p_s1 : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of continuous_p    : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  -- Registered (1-cycle) copies of the per-cycle pump condition flags. The wide
  -- counters increment from these short registered enables instead of directly
  -- from the combinational pump_*_v (which carry the buf_rem producer
  -- gating and, in series with the 32-bit counter carry, violated clk[2] setup).
  signal pump_valid_q  : boolean := false;
  signal pump_ready_q  : boolean := false;
  signal pump_accept_q : boolean := false;
  signal pump_stall_q  : boolean := false;
  signal pump_nodata_q : boolean := false;
  signal producer_accept_q : boolean := false;
  signal cont_accept_q : boolean := false;
  signal cont_meta_reset_q : std_logic := '0';
  -- Sticky "ring has filled once since run start" flag, set by ring_wrap_q
  -- (a continuous burst ending exactly at the ring end was committed).
  signal ring_filled       : std_logic := '0';
  attribute altera_attribute of run_f_s1 : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of run_f_s2 : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  attribute altera_attribute of run_f_level : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";

  -- Stage 2b: capture FIFO depth. History: 512 (was 1024, originally
  -- 2048/4096) to trim pointer bits from the 167 MHz cone. A raw single-shot
  -- capture that outruns the pump now keeps everything pushed before the
  -- FIFO fills, so a deeper FIFO directly lengthens the prefix kept at
  -- 200.4 MS/s (~4 x (DEPTH - 16) samples). Packed/MSO/narrow/analog still
  -- use the DEPTH-320 almost-full cushion.
  constant AFIFO_DEPTH : natural := 4096;
  -- Raw single-shot capture stops the sampler when wrusedw >= DEPTH - 16.
  -- Words that can still arrive after the threshold is crossed: sampler
  -- decision -> fifo_wr (1) -> skid register (1) -> dcfifo write edge (1),
  -- wrusedw register (1..2), wrusedw_q (1), compare register (1), raw_full_r (1),
  -- decision (1) is ~8 cycles at one word per clock (RATE_DIV 1, the worst case). wrusedw
  -- never under-reports occupancy (reads are seen late, writes promptly), so
  -- a margin of 16 leaves ~8 words of slack: the FIFO never reaches full, so
  -- no word is ever dropped inside the stored run.
  constant RAW_FULL_MARGIN : natural := 16;
  constant AFIFO_WIDTH : natural := 16;
  constant AFIFO_WIDTHU : natural := 12;
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
  -- Registered pipeline stage for the packed-mode data/valid pair.  Breaks the
  -- combinational path from the producer registers (packed_mode_f, Packed_Ready_r,
  -- sample_rem_nonzero_r) through packed_buf_in_valid and the elastic buffer's
  -- push/enable logic to data1_r[*] - the #1 fast_clk critical path at -0.501 ns.
  signal packed_buf_in_valid_r : std_logic := '0';
  signal Packed_Data_r : std_logic_vector(15 downto 0) := (others => '0');
  signal packed_mode_meta : std_logic := '0';
  signal packed_mode_f    : std_logic := '0';
  signal packed_mode_path_f : std_logic := '0';
  signal fifo_wrusedw : std_logic_vector(AFIFO_WIDTHU-1 downto 0) := (others => '0');
  signal fifo_wrusedw_q : std_logic_vector(AFIFO_WIDTHU-1 downto 0) := (others => '0');
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
  -- Independent live-tap sample seam.  This is deliberately driven from the
  -- capture tick, not from fifo_wr: fifo_wr is allowed to stop when the
  -- SDRAM write FIFO is almost full, but a direct live consumer must not
  -- inherit that backpressure or silently turn into stale/idle waveform data.
  -- The direct accelerometer transport only consumes CH13..CH15. Keep the
  -- internal tap narrow; OLS_Interface expands it back to the normal 16-bit
  -- waveform word at the protocol boundary.
  signal live_tap_data_r : std_logic_vector(2 downto 0) := (others => '0');
  signal live_tap_valid_r : std_logic := '0';
  -- Registered almost-full for packed-mode backpressure. Same 256-word cushion
  -- as afull_r in the digital writer; keeps the wrusedw compare off the path
  -- from producer -> Packed_Ready -> mso_capture -> analog_packer.
  signal packed_afull_r : std_logic := '0';
  -- Registered Packed_Ready for timing closure. The combinational 5-term AND
  -- feeding the output port created a cross-hierarchy path from the FAST_CLK
  -- domain registers (sample_rem_nonzero_r -> packed_stop_f, run_f_level,
  -- pump_live_f) through three levels of hierarchy into analog_packer's BRAM
  -- write-address register. One register stage breaks this path; the extra
  -- cycle of backpressure latency is absorbed by the elastic buffer (2 entries)
  -- and the 320-word async-FIFO almost-full cushion downstream.
  signal Packed_Ready_r : std_logic := '0';
  -- FAST_CLK level: '1' once the capture's tick budget is exhausted (driven
  -- from sample_rem_nonzero_r inside gen_fast_speed). Stops the packed
  -- producer at capture end; otherwise its continuous trickle (digital RLE
  -- saturation markers arrive every <= 2.56 us) keeps the FIFO non-empty and
  -- the single-shot drain-completion window (2047 empty pclk cycles) never
  -- elapses, so Full never rises. Left at '0' in non-FAST_SPEED builds
  -- (packed capture is a FAST_SPEED feature).
  signal packed_stop_f : std_logic := '0';
  signal packed_budget_last_r : std_logic := '0';
  signal packed_budget_reload_r : std_logic := '0';
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
  -- FIFO read side (normal mode: q is the popped word one cycle after rdreq).
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
  -- Continuous-mode reads run in bounded bursts of RD_BURST_N words so the write
  -- pump keeps the bus in between: a burst starts only when the response FIFO
  -- has room for the whole burst (rdfifo_room_r), and RD_COOL_N idle issue cycles
  -- follow it (refresh and the pump get the bus; a burst of 16 reads is ~320
  -- pclk cycles, far inside the capture FIFO's fill time at 100.2 MS/s).
  constant RD_BURST_N : natural := 16;
  constant RD_COOL_N  : natural := 80;
  signal rdfifo_room_r  : std_logic := '0';
  -- Capture FIFO read-side fill below 512 words (of AFIFO_DEPTH): a continuous
  -- read burst only starts while the capture FIFO has plenty of room, so the
  -- reader yields to the write pump whenever the pump is behind.
  signal cap_low_r      : std_logic := '0';
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
  -- Registered "at most one sample left" predicate for the completion branch.
  -- It is computed from the same pre-decrement snapshot (stream_rem_cur) as
  -- stream_rem_dec_r, for the same spacing argument: read completions are
  -- spaced by at least CAS latency plus the rd_gap bubble, and stream_rem only
  -- changes on a completion or a stream start, so the flag is settled at least
  -- two pclk cycles before a completion can consume it (the first completion
  -- after a start/load is >= 3 cycles later). Replacing the inline 23-bit
  -- compare (stream_rem spans 0..Max_Samples) with this one-bit register takes
  -- that reduce out of the s_rvalid completion cone and gives it a full cycle
  -- from register to register.
  signal stream_rem_le1_r   : std_logic := '0';

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
  -- Capture burst interface to the SDRAM controller (see the interface comment
  -- in SDRAM_Controller_Custom.vhd). cap_req/cap_addr/cap_len/cap_stop/
  -- pop_ok_r are registers here; cap_pop/cap_wr/cap_busy are registers in the
  -- controller.
  signal cap_req   : std_logic := '0';
  signal cap_stop  : std_logic := '0';
  signal cap_busy  : std_logic := '0';
  signal cap_pop   : std_logic := '0';
  signal cap_wr    : std_logic := '0';
  signal cap_addr  : std_logic_vector(21 downto 0);
  -- Write address of the next burst. A signal (not a process variable) so the
  -- controller's burst address port can be wired straight from it: it only
  -- changes at the burst commit (P_M3) and at run start, never while a burst
  -- request/burst is outstanding.
  signal waddr : natural range 0 to Max_Samples - 1 := 0;
  signal cap_len   : std_logic_vector(8 downto 0) := (others => '0');
  -- Pump burst sequencer. P_C1..P_C4 compute the burst (row remainder,
  -- budget / ring remainder, length), P_REQ presents it, P_RUN waits for the
  -- controller to finish, P_M1..P_M3 commit the words written (address and
  -- budget) once per burst. All wide arithmetic is registered between states.
  type pump_state_t is (P_IDLE, P_C1, P_C2, P_C3, P_C4, P_REQ, P_RUN,
                        P_M1, P_M2, P_M3);
  signal pst        : pump_state_t := P_IDLE;
  signal wcnt       : natural range 0 to 256 := 0;  -- words written this burst
  signal row_rem_r  : natural range 0 to 256 := 0;
  -- ra_r / rb_r are time-shared between the burst-compute states (P_C1..P_C4:
  -- ring remainder, limit) and the commit states (P_M1..P_M3: new write address,
  -- new budget); the two groups of states never overlap.
  signal ra_r       : natural range 0 to Max_Samples := 0;
  signal rb_r       : natural range 0 to Max_Samples := 0;
  signal len_r      : natural range 0 to 256 := 0;
  signal wrap_r     : std_logic := '0';
  signal remz_r     : std_logic := '0';
  signal bnz_r      : std_logic := '0';
  signal run_hold   : natural range 0 to 3 := 0;
  signal ring_wrap_q : boolean := false;
  -- FIFO pop permission and history (see the pop-grant comment in the pump).
  signal pop_ok_r    : std_logic := '0';
  signal g_h1        : std_logic := '0';
  signal g_h2        : std_logic := '0';
  signal g_h3        : std_logic := '0';
  signal rd_h1       : std_logic := '0';
  signal rd_h2       : std_logic := '0';
  signal rd_h3       : std_logic := '0';
  signal usedw_ge8_r : std_logic := '0';
  signal drain_rd_r  : std_logic := '0';

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
    Capture_Burst_Req    : in  std_logic := '0';
    Capture_Burst_Addr   : in  std_logic_vector(21 downto 0) := (others => '0');
    Capture_Burst_Len    : in  std_logic_vector(8 downto 0) := (others => '0');
    Capture_Burst_Stop   : in  std_logic := '0';
    Capture_Pop_Ok       : in  std_logic := '0';
    Capture_Data         : in  std_logic_vector(15 downto 0) := (others => '0');
    Capture_Pop          : out std_logic := '0';
    Capture_Wr           : out std_logic := '0';
    Capture_Busy         : out std_logic := '0';
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
    intended_device_family : string;
    overflow_checking : string
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

  cap_addr <= std_logic_vector(to_unsigned(waddr, 22));

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
      fifo_wrusedw_q <= fifo_wrusedw;
      if unsigned(fifo_wrusedw_q) >= to_unsigned(AFIFO_DEPTH - 320, AFIFO_WIDTHU) then
        fifo_afull_cmp_r <= '1';
      else
        fifo_afull_cmp_r <= '0';
      end if;
      fifo_afull_r     <= fifo_afull_cmp_r;
      fifo_wralmost_full <= fifo_afull_r;  -- legacy alias, used by afull_r in FAST_CLK
      -- Raw digital producer stop flag. Single-shot: only RAW_FULL_MARGIN words
      -- below the real depth (see the margin derivation at the constant).
      -- Continuous mode keeps the original almost-full flag (and its drop-
      -- the-sample behaviour); that path never flags overflow.
      if unsigned(fifo_wrusedw_q) >= to_unsigned(AFIFO_DEPTH - RAW_FULL_MARGIN, AFIFO_WIDTHU) then
        fifo_rawfull_cmp_r <= '1';
      else
        fifo_rawfull_cmp_r <= '0';
      end if;
      if continuous_f = '1' then
        raw_full_r <= fifo_wralmost_full;
      else
        raw_full_r <= fifo_rawfull_cmp_r;
      end if;
    end if;
  end process;

  -- 2FF synchronisers: Continuous_Mode and Index_Hold (CLK domain levels) into pclk.
  -- Continuous_Mode is written by the host well before Run; even if both change
  -- in the same CLK cycle, continuous_p (2 stages) is valid before run_start_r
  -- (run_sync1, run_sync2, then the run_start_r register = 3 stages).
  process(pclk)
  begin
    if rising_edge(pclk) then
      continuous_p_s1 <= Continuous_Mode;
      continuous_p    <= continuous_p_s1;
      index_hold_s1   <= Index_Hold;
      index_hold_p    <= index_hold_s1;
    end if;
  end process;

  -- Ring-index snapshot: all four registers load in the same pclk cycle, so
  -- Producer/Oldest/Newest/Overflow are mutually consistent and are never
  -- sampled mid-increment while the CLK-domain dispatcher holds Index_Hold.
  process(pclk)
  begin
    if rising_edge(pclk) then
      if index_hold_p = '0' then
        producer_snap_r <= producer_index_u;
        oldest_snap_r   <= oldest_index_u;
        newest_snap_r   <= newest_index_u;
        overflow_snap_r <= pump_overflow_count_u;
      end if;
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
      -- Preserve the committed prefix metadata after a single-shot overflow so
      -- the host can inspect/read it. The next run edge performs the reset;
      -- using the latched run_stop_overflow level here erased Producer_Index
      -- back to zero on every cycle after overflow.
      cont_meta_reset_q <= run_edge_r and run_start_r;
    end if;
  end process;

  -- Re-register CLK-domain divide results into pclk domain; pre-compute buffer limits
  process(pclk)
  begin
    if rising_edge(pclk) then
      samples_div_p  <= samples_div;
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
      Armed_f_d <= Armed_f;
      Armed_f_d2 <= Armed_f_d;
      cfg_valid_s1 <= cfg_valid_toggle;
      cfg_valid_s2 <= cfg_valid_s1;
      cfg_valid_edge <= cfg_valid_s1 xor cfg_valid_s2;
      if cfg_valid_edge = '1' then
        cfg_rate_div_f  <= cfg_rate_div;
        cfg_ack_toggle <= not cfg_ack_toggle;
      elsif Armed_f = '1' and Armed_f_d = '0' then
        -- Pre-trigger sampling starts before the run-edge config handshake.
        -- Rate_Div is quasi-static (written before arming), so latch it when
        -- synchronized Armed rises instead of silently using divisor 12.
        cfg_rate_div_f <= Rate_Div;
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
        if ovf_keep_f = '1' then
          ovf_keep_toggle <= not ovf_keep_toggle;
        else
          overflow_toggle <= not overflow_toggle;
        end if;
      end if;
    end if;
  end process;

  -- ============================================================
  -- Speed mode (200 MHz): 3-stage pipeline, no divider, no flush
  -- ============================================================
  gen_fast_speed : if FAST_SPEED generate
    -- Direct waveform transport runs at 2 MS/s, so its divider fits in the
    -- timing-clean 16-bit fast counter. The normal/ring path retains the
    -- wider divider implementation in its non-fast clock domain.
    constant FAST_MAX_RATE_DIV : natural := 65535;
    signal sample_word_r  : std_logic_vector(Channels-1 downto 0) := (others => '0');
    signal capture_en_r   : std_logic := '0';
    signal pretrig_en_r   : std_logic := '0';
    signal bram_wp_r      : natural range 0 to BRAM_SIZE-1 := 0;
    signal bram_cnt_r     : natural range 0 to BRAM_SIZE := 0;
    signal sample_div_cnt_r : natural range 0 to FAST_MAX_RATE_DIV := 0;
    signal fast_rate_reload_r : natural range 0 to FAST_MAX_RATE_DIV := 0;
    signal sample_due_r : std_logic := '0';
    signal fast_sample_tick_r  : std_logic := '0';
    signal sample_rem_nonzero_r : std_logic := '0';
    -- Modular sample-budget accumulator. It starts at -cfg_samples and adds
    -- one for each consumed budget unit; the carry-out is the exact terminal
    -- event. This uses the MAX 10 carry chain and avoids a 22-bit equality
    -- comparator in the 200 MHz control cone.
    constant BUDGET_WIDTH : positive := 22;
    subtype budget_t is unsigned(BUDGET_WIDTH-1 downto 0);
    signal budget_count_r : budget_t := (others => '0');
    function budget_seed(n : natural) return budget_t is
      variable magnitude : budget_t;
    begin
      magnitude := to_unsigned(n mod (2**BUDGET_WIDTH), BUDGET_WIDTH);
      return (not magnitude) + 1;
    end function;
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
        -- Do not let a new producer write into the async FIFO while the pclk
        -- side is still snapshot-draining stale words from the previous run.
        -- Packed mode already used pump_live_f at its ready boundary; applying
        -- the same gate to ordinary digital/analog producers prevents the new
        -- run from being discarded and overflowing with zero commits.
        capture_en_r <= run_f_level and pump_live_f;
        pretrig_en_r <= Armed_f and not run_f_level;
      end if;
    end process;

    -- Stage 2a: rate divider counter (free-running while Run is active).
    -- This clock/tap cadence must not inherit pump_live_f: that signal is the
    -- SDRAM writer's backpressure/flush state, not the live capture clock.
    -- Direct live streaming has its own FIFO and must keep sampling through an
    -- SDRAM pump pause; ordinary SDRAM writes remain gated below by
    -- capture_en_r.
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_valid_edge = '1' then
          sample_div_cnt_r <= 0;
          sample_due_r <= '0';
          if cfg_rate_div > FAST_MAX_RATE_DIV then
            fast_rate_reload_r <= FAST_MAX_RATE_DIV;
          elsif cfg_rate_div > 1 then
            fast_rate_reload_r <= cfg_rate_div - 1;
          else
            fast_rate_reload_r <= 0;
          end if;
        elsif run_f_level = '1' and sample_rem_nonzero_r = '1' then
          if sample_div_cnt_r = 0 then
            sample_div_cnt_r <= fast_rate_reload_r;
            sample_due_r <= '1';
          else
            sample_div_cnt_r <= sample_div_cnt_r - 1;
            sample_due_r <= '0';
          end if;
        else
          sample_due_r <= '0';
        end if;
      end if;
    end process;

    -- Stage 2b: sample tick (pipelined one cycle after divider reaches zero)
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        fast_sample_tick_r <= '0';
        live_tap_valid_r <= '0';
        if cfg_valid_edge = '1' then
          start_gate_r <= 2;
        elsif run_f_level = '1' and start_gate_r > 0 then
          start_gate_r <= start_gate_r - 1;
        end if;
        if run_f_level = '1' and sample_rem_nonzero_r = '1'
           and sample_due_r = '1' and start_gate_r = 0 then
          fast_sample_tick_r <= '1';
          -- Hold the sampled tap value beside its one-cycle valid pulse. The
          -- live FIFO writer consumes both on the following FAST_CLK edge,
          -- so a divider tick cannot be paired with a later pin sample.
          live_tap_data_r <= sample_word_r(15 downto 13);
          live_tap_valid_r <= '1';
        end if;
      end if;
    end process;


    -- Stage 2c: registered budget-active flag. The write process emits a
    -- one-cycle terminal carry pulse, so no wide terminal comparison feeds
    -- this 200 MHz control path.
    process(FAST_CLK)
    begin
      if rising_edge(FAST_CLK) then
        if cfg_valid_edge = '1' then
          sample_rem_nonzero_r <= '1';
        elsif packed_budget_last_r = '1' then
          sample_rem_nonzero_r <= '0';
          -- The terminal carry means the final requested budget unit was
          -- consumed. The pclk side waits for the async FIFO to drain before
          -- reporting completion.
          cap_done_toggle_f <= not cap_done_toggle_f;
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
        elsif pretrig_en_r = '1' and fast_sample_tick_r = '1' and pretrig_tick_cnt < 15 then
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
      variable budget_consume_v : boolean;
      variable budget_sum_v : unsigned(BUDGET_WIDTH downto 0);
    begin
      if rising_edge(FAST_CLK) then
        narrow_count_v := narrow_bit_count_r;
        narrow_valid_v := narrow_sample_valid_r;
        narrow_bit_v := narrow_sample_bit_r;
        narrow_last_v := narrow_sample_last_r;
        budget_consume_v := false;
        fifo_wr <= '0';
        packed_budget_last_r <= '0';
        packed_budget_reload_r <= '0';
        bram_wren <= '0';
        drop_ev_r <= '0';
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
        elsif analog_burst_active = '1' then
          -- The data mux only needs to select the frame while a burst is
          -- actually being emitted.  Using the mode level (astream_f) here
          -- put the slow CDC/control fanout on every fifo_wdata bit and was
          -- the final 200 MHz setup path.  analog_burst_active is registered
          -- in this same FAST_CLK process and is exactly the qualifier used
          -- by the write strobe below, so this preserves the consumed data
          -- sequence while removing the mode-level path from fifo_wdata.
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
          budget_count_r <= budget_seed(cfg_samples);
        fifo_overflow_f <= '0';
        ovf_keep_f <= '0';
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
          -- Narrow word write. Independent of the sample branches below: at
          -- RATE_DIV 1 a sample tick arrives every FAST_CLK, and when this was a
          -- branch of the priority chain the cycle that wrote a finished word
          -- (1 in 17) consumed a tick without shifting its sample in, losing
          -- 5.9 % of the samples at 200 MHz.
          if narrow_word_pending_r = '1' and afull_r = '0'
             and not (capture_en_r = '1' and packed_mode_path_f = '1') then
            fifo_wr <= '1';
            narrow_word_pending_r <= '0';
            budget_consume_v := (sample_rem_nonzero_r = '1');
          end if;
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
            -- continuous_f (synced), not the raw Continuous_Mode port --
            -- see the Stage 2c comment above for the bug this caused.
            budget_consume_v := (sample_rem_nonzero_r = '1');
          -- (narrow word write: see the independent block above)
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
              budget_consume_v := (sample_rem_nonzero_r = '1');
            end if;
            if afull_r = '1' and continuous_f = '0' then
              fifo_overflow_f <= '1';
            end if;

          elsif capture_en_r = '1' and fast_sample_tick_r = '1' and narrow_enable_f = '1' then
            -- Narrow high-speed rolling packs 16 consecutive samples of one
            -- selected digital channel into one SDRAM word. Bit 0 is earliest.
            -- Queue the selected bit first, then consume the previously queued
            -- bit on this edge. That keeps the muxed input off the same cone as
            -- the shift-register update.
            if narrow_valid_v = '1' then
              if narrow_last_v = '1' then
                -- A finished word still pending (FIFO not accepting) is lost.
                if narrow_word_pending_r = '1' and afull_r = '1' and continuous_f = '1' then
                  drop_ev_r <= '1';
                end if;
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

          elsif capture_en_r = '1' and fast_sample_tick_r = '1' then
            -- fifo_wdata (= sample_word_r) is driven by the decoupled data mux
            -- above; this branch only asserts the write strobe and decrements
            -- the sample budget, keeping capture_en_r off the fifo_wdata cone.
            if raw_full_r = '0' then
              fifo_wr <= '1';
              budget_consume_v := (sample_rem_nonzero_r = '1');
            end if;
            if raw_full_r = '1' and continuous_f = '1' then
              drop_ev_r <= '1';
            end if;
            if raw_full_r = '1' and continuous_f = '0' then
              -- Stop the sampler here: every earlier sample was pushed, this
              -- one and all later ones are not, so the stored words are one
              -- contiguous run from the first sample.
              fifo_overflow_f <= '1';
              ovf_keep_f <= '1';
            end if;
          end if;
        end if;
        -- Configuration has priority over a same-cycle producer event. For a
        -- running capture, one carry-chain increment represents one consumed
        -- budget unit; overflow is the exact terminal event. Continuous mode
        -- reloads atomically, while single-shot emits one completion pulse.
        if cfg_valid_edge = '0' and packed_budget_reload_r = '1' then
          budget_count_r <= budget_seed(cfg_samples);
        elsif cfg_valid_edge = '0' and budget_consume_v then
          budget_sum_v := ('0' & budget_count_r) + 1;
          -- Keep the carry off the wide register's synchronous-load mux: the
          -- modular sum always writes back directly, while carry only sets a
          -- one-bit registered completion/reload event.
          budget_count_r <= budget_sum_v(BUDGET_WIDTH-1 downto 0);
          if budget_sum_v(BUDGET_WIDTH) = '1' then
            if continuous_f = '1' then
              packed_budget_reload_r <= '1';
            else
              packed_budget_last_r <= '1';
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
      variable flush_wait  : natural range 0 to 2 := 0;
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
          if (state = 0 and Armed_f_d2 = '1' and run_f_level = '0') or state = 2 then
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
            flush_wait := 0;
            state := 1;
          else
            state := 2;
          end if;

        -- State machine (only runs when not in overflow)
        elsif fifo_overflow_f = '0' then

          -- State 0: Pre-trigger — circular BRAM write
          if state = 0 then
            if Armed_f_d2 = '1' and run_f_level = '0' then
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
              -- bram_rdata_f is a registered synchronous read port. Allow two
              -- edges after presenting each address before consuming its data;
              -- the former one-cycle "skip first" pipeline duplicated address
              -- zero and dropped the tail of every pre-trigger history.
              if flush_wait = 0 then
                bram_raddr_f <= flush_raddr;
                flush_wait := 1;
              elsif flush_wait = 1 then
                flush_wait := 2;
              elsif fifo_wralmost_full = '0' then
                fifo_wdata <= bram_rdata_f;
                fifo_wr <= '1';
                if sample_remaining = 1 then
                  sample_remaining <= 0;
                  -- Last requested sample came from the pre-trigger flush
                  -- (tiny-capture edge case); signal producer-done here too.
                  cap_done_toggle_f <= not cap_done_toggle_f;
                  flush_rem := 0;
                else
                  sample_remaining <= sample_remaining - 1;
                  flush_rem := flush_rem - 1;
                end if;
                if flush_raddr = BRAM_SIZE-1 then flush_raddr := 0;
                else flush_raddr := flush_raddr + 1; end if;
                flush_wait := 0;
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
                -- Reaching the requested budget is normal completion, not an
                -- overflow. cap_done_toggle_f above tells the pclk pump to
                -- wait until every queued word is committed before asserting
                -- Full. Treating sample_remaining=1 as overflow made Full rise
                -- immediately and exposed unwritten SDRAM words to readout.
                if continuous_f = '0' and fifo_wralmost_full = '1' then
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

  -- Dropped-sample counter (FAST_CLK) with request/acknowledge snapshot.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if cfg_valid_edge = '1' then
        drop_cnt_f <= (others => '0');
      elsif drop_ev_r = '1' and drop_cnt_f /= x"FFFF" then
        drop_cnt_f <= drop_cnt_f + 1;
      end if;
      drop_req_s1 <= drop_req_p;
      drop_req_s2 <= drop_req_s1;
      if drop_req_s2 /= drop_ack_f then
        drop_snap_f <= drop_cnt_f;
        drop_ack_f  <= drop_req_s2;
      end if;
    end if;
  end process;

  process(pclk)
  begin
    if rising_edge(pclk) then
      drop_ack_s1 <= drop_ack_f;
      drop_ack_s2 <= drop_ack_s1;
      -- Request answered (ack follows req): take the snapshot unless the host
      -- is mid-packet (Index_Hold), then ask for the next one.
      if drop_ack_s2 = drop_req_p and index_hold_p = '0' then
        drop_val_p <= std_logic_vector(drop_snap_f);
        drop_req_p <= not drop_req_p;
      end if;
    end if;
  end process;

  process(pclk)
  begin
    if rising_edge(pclk) then
      overflow_t_s1 <= overflow_toggle;
      overflow_t_s2 <= overflow_t_s1;
      overflow_t_s3 <= overflow_t_s2;
      overflow_clk <= overflow_t_s2 xor overflow_t_s3;
      ovf_k_s1 <= ovf_keep_toggle;
      ovf_k_s2 <= ovf_k_s1;
      ovf_k_s3 <= ovf_k_s2;
      keep_clk <= ovf_k_s2 xor ovf_k_s3;
    end if;
  end process;

  -- Async FIFO: dcfifo bridges FAST_CLK (write) and pclk (read).
  -- Read mode is normal (show-ahead "OFF"): the word popped by rdreq in cycle c
  -- is on q in cycle c+1 (see the read-timing comment above the pump process).
  -- 2-FF synchronise the (CLK-domain) mode select into FAST_CLK.
  -- pclk-domain level: run active and the run-start stale drain finished.
  pump_live_p <= run_level_r and not drain_pending_r and not pump_budget_done_q;

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
  packed_buf_in_valid <= packed_buf_in_valid_r;
  packed_buf_out_ready <= not packed_afull_r;
  -- One-entry ready/valid pipeline in front of the elastic buffer. The former
  -- unconditional register copied Packed_Valid every cycle, even while Ready
  -- was low, so a producer correctly holding one word under backpressure could
  -- be enqueued repeatedly. Hold the stage until the elastic buffer consumes it
  -- and only replace it when the stage is empty or drains on this edge.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if packed_buf_rst = '1' then
        packed_buf_in_valid_r <= '0';
      elsif packed_buf_in_valid_r = '0' or packed_buf_in_ready = '1' then
        if Packed_Valid = '1' and Packed_Ready_r = '1' then
          Packed_Data_r <= Packed_Data;
          packed_buf_in_valid_r <= '1';
        else
          packed_buf_in_valid_r <= '0';
        end if;
      end if;
    end if;
  end process;

  -- Register producer backpressure so mso_capture/analog_packer never sees a
  -- combinational path through the FLA pipeline and elastic-buffer state.
  process(FAST_CLK)
  begin
    if rising_edge(FAST_CLK) then
      if packed_buf_rst = '1' then
        Packed_Ready_r <= '0';
      elsif packed_mode_path_f = '1' and run_f_level = '1'
         and packed_stop_f = '0' and pump_live_f = '1'
         and (packed_buf_in_valid_r = '0' or packed_buf_in_ready = '1') then
        Packed_Ready_r <= '1';
      else
        Packed_Ready_r <= '0';
      end if;
    end if;
  end process;

  packed_stream_buf : entity work.fast_capture_elastic_buffer
    generic map (DATA_WIDTH => 16)
    port map (
      clk       => FAST_CLK,
      rst       => packed_buf_rst,
      in_data   => Packed_Data_r,
      in_valid  => packed_buf_in_valid_r,
      in_ready  => packed_buf_in_ready,
      out_data  => packed_buf_out_data,
      out_valid => packed_buf_out_valid,
      out_ready => packed_buf_out_ready
    );

  Packed_Ready <= Packed_Ready_r;

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
  -- fifo_wr_skid_* remain the SDRAM writer's input. FAST_SPEED builds expose
  -- the independent capture-tick seam to the direct live FIFO instead of
  -- reusing the SDRAM writer's ready/valid strobe.
  gen_live_tap_fast : if FAST_SPEED generate
  begin
    -- Keep the public tap layout unchanged: CH13..CH15 occupy bits 15..13.
    Live_Stream_Data  <= live_tap_data_r & "0000000000000";
    Live_Stream_Valid <= live_tap_valid_r;
  end generate;

  gen_live_tap_normal : if not FAST_SPEED generate
  begin
    Live_Stream_Data  <= fifo_wr_skid_data_r;
    Live_Stream_Valid <= fifo_wr_skid_req_r;
  end generate;

  -- Async FIFO (dcfifo) bridging FAST_CLK (write) to pclk (read).
  -- Sync depths increased to 4 for safer CDC across the ~200 MHz / ~167 MHz
  -- boundary. The skid buffers upstream ensure data/wrreq are registered.
  -- Normal (show-ahead OFF) read mode: data one cycle after rdreq, so the
  -- SDRAM controller can pull one word per clock with a registered pop.
  fifo_rd <= cap_pop or drain_rd_r;
  afifo : dcfifo
    generic map (
      lpm_width       => AFIFO_WIDTH,
      lpm_widthu      => AFIFO_WIDTHU,
      lpm_numwords    => AFIFO_DEPTH,
      lpm_showahead   => "OFF",
      lpm_type        => "dcfifo",
      rdsync_delaypipe => 4,
      wrsync_delaypipe => 4,
      intended_device_family => "MAX 10",
      -- overflow_checking OFF: wrreq no longer passes through the wrfull
      -- comparator (write pointer vs the synchronised read pointer) before the
      -- write-pointer counter and RAM address. That comparator is the 200 MHz
      -- critical path once the FIFO is deep. Safe because no producer ever
      -- writes into a full FIFO: raw single-shot stops RAW_FULL_MARGIN words
      -- below full, every other producer is gated by the DEPTH-320
      -- almost-full flag (the simulation model asserts if a write ever
      -- reaches a full FIFO).
      overflow_checking => "OFF"
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
      intended_device_family => "MAX 10",
      overflow_checking => "ON"
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
  -- pclk-side FIFO read: pull-based burst
  -- ============================================================
  -- FIFO read timing relied on (afifo is lpm_showahead => "OFF", normal mode):
  --   * rdreq high in cycle c (sampled at the edge ending c) puts the POPPED
  --     word on q in cycle c+1 and q holds it until the next rdreq edge. This
  --     is Intel's dcfifo_async model (altera_mf.vhd, rden: i_q_tmp <=
  --     mem_data(rdptr)) and the same behaviour the rdfifo on the OLS side
  --     already relies on.
  --   * rdreq on an empty FIFO is ignored (underflow_checking default "ON");
  --     the pump never issues one anyway (pop_ok_r below).
  --   * rdempty and rdusedw are registered and reflect pops with a lag of
  --     one to three cycles, and never show data that is not yet readable.
  -- Words are popped only by the SDRAM controller (capture_pop) during a burst
  -- or by drain_rd_r; the controller writes every word it pops exactly once.
  -- Main: SDRAM write pump + buffer management + readout
  -- Runs on pclk (96 MHz). Reads 16-bit sample words from async FIFO,
  -- assigns SDRAM addresses, manages triple-buffer continuous mode.
  -- Main: SDRAM write pump + buffer management + readout
  -- Runs on pclk (96 MHz). Reads 16-bit sample words from async FIFO,
  -- assigns SDRAM addresses, manages triple-buffer continuous mode.
  process (pclk)
    variable rd_mode : boolean := true;
    variable read_addr : natural := 0;
    -- Single write-address counter shared by single-shot and continuous mode
    -- (only one mode advances it per run; reset on run start only).
    variable a_reg   : natural range 0 to Max_Samples := Max_Samples;
    variable rd_pend : std_logic := '0';
    -- Streaming block-readout state (single-shot CMD_READ_CAPTURE path)
    variable stream_active : boolean := false;
    variable stream_addr_u : unsigned(21 downto 0) := (others => '0');
    variable stream_addr_inc_pending : boolean := false;
    variable stream_rem    : natural range 0 to Max_Samples := 0;
    variable rd_pend2      : std_logic := '0';
    -- The read latch samples inside the chip's CL3 data window now, so the
    -- throwaway prime reads that used to absorb the stale first read of a block
    -- are gone; a block stream starts with real data. (Verified on the board
    -- with 10k block reads across idle/refresh gaps.)
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
    variable rd_burst_left : natural range 0 to RD_BURST_N := 0;
    variable rd_cool       : natural range 0 to RD_COOL_N := 0;
    -- FIFO pops come from exactly two registers, never at the same time: the
    -- SDRAM controller's capture_pop (during a burst) and drain_rd_r (run-start
    -- stale-word drain / post-budget discard, only while no burst is
    -- outstanding). Every popped word is written exactly once because the
    -- controller issues one WRITE for every pop it made and the pump counts
    -- exactly those writes (cap_wr).
    -- Continuous-mode readout: a host block read temporarily enters rd_mode to
    -- stream the rolling SDRAM window, then returns to capture.
    variable cont_base_v   : natural range 0 to Max_Samples - 1 := 0;
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
    variable ring_wrap_v   : boolean := false;
    variable abort_v       : boolean := false;
    variable quiet_v       : boolean := false;
    variable drain_go_v    : std_logic := '0';
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
      ring_wrap_v := false;
      s_wr <= '0';
      rdfifo_wr <= '0';
      -- Response-FIFO data is loaded every cycle (it is only consumed when
      -- rdfifo_wr is set, in the same cycle the old code loaded it), which keeps
      -- rd_mode / s_rvalid out of the 16-bit data enable cone (167 MHz path).
      rdfifo_wdata <= s_rdata;
      fifo_rdempty_r <= fifo_rdempty;
      -- wrusedw wraps to 0 at full on dcfifo, so OR in wrfull; both cones
      -- terminate in this FF instead of fanning out to the issue logic.
      if rdfifo_wrfull = '1'
         or unsigned(rdfifo_wrusedw) >= RDFIFO_DEPTH - 8 then
        rdfifo_afull_r <= '1';
      else
        rdfifo_afull_r <= '0';
      end if;
      if rdfifo_wrfull = '0'
         and unsigned(rdfifo_wrusedw) < RDFIFO_DEPTH - 8 - RD_BURST_N then
        rdfifo_room_r <= '1';
      else
        rdfifo_room_r <= '0';
      end if;
      if unsigned(fifo_rdusedw(AFIFO_WIDTHU-1 downto 9)) = 0 then
        cap_low_r <= '1';
      else
        cap_low_r <= '0';
      end if;
      -- FIFO pop permission (see the read-timing block comment above the
      -- process). pop_ok_r is a grant: "one more word may be popped one cycle
      -- from now". It is registered, derived only from registers, and is the
      -- only FIFO-flag logic the SDRAM controller ever sees.
      --   fast grant: rdusedw (read-side view, never ahead of the real count)
      --     >= 8. The count may lag the real pointer by up to ~3 pops (pops
      --     issued since the flag was sampled are not yet subtracted), so
      --     >= 8 keeps a margin of 3 words for continuous one-per-clock pops.
      --   slow grant: rdempty low and no grant in the last four cycles, so
      --     every earlier pop has long been reflected in rdempty; exactly one
      --     word is granted, then the grant history blocks the next one.
      rd_h1 <= fifo_rd; rd_h2 <= rd_h1; rd_h3 <= rd_h2;
      g_h1 <= pop_ok_r; g_h2 <= g_h1; g_h3 <= g_h2;
      if unsigned(fifo_rdusedw(AFIFO_WIDTHU-1 downto 3)) /= 0 then
        usedw_ge8_r <= '1';
      else
        usedw_ge8_r <= '0';
      end if;
      if usedw_ge8_r = '1' then
        pop_ok_r <= '1';
      elsif fifo_rdempty_r = '0' and pop_ok_r = '0' and g_h1 = '0'
            and g_h2 = '0' and g_h3 = '0' then
        pop_ok_r <= '1';
      else
        pop_ok_r <= '0';
      end if;
      if fifo_rdempty = '0' and unsigned(fifo_rdusedw) = 0 then
        fifo_rdusedw_r <= AFIFO_DEPTH;
      else
        fifo_rdusedw_r <= to_integer(unsigned(fifo_rdusedw));
      end if;
      overflow_count_en_q <= overflow_clk or keep_clk;
      if overflow_count_en_q = '1' then
        if pump_overflow_count_u /= 255 then
          pump_overflow_count_u <= pump_overflow_count_u + 1;
        end if;
      end if;
      if stream_addr_inc_pending then
        if continuous_p = '1' and stream_at_ring_end = '1' then
          stream_addr_u := (others => '0');
        else
          -- Registered pre-increment: no adder in this mux (see declaration)
          stream_addr_u := stream_addr_nxt_r;
        end if;
        stream_addr_inc_pending := false;
      end if;
      if stream_rem_cur <= 1 then
        stream_rem_le1_r <= '1';
      else
        stream_rem_le1_r <= '0';
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
      -- ----------------------------------------------------------------
      -- SDRAM write pump (pull-based burst). Runs every cycle, including
      -- while the readout owns the bus (it then aborts and drains).
      --   * Between bursts it computes, from registers, a burst of
      --     len = min(words left in the 256-word row, samples left in the
      --     single-shot budget | words left in the ring) and presents
      --     cap_req / cap_addr / cap_len as stable registered values.
      --   * The controller pulls: it latches the burst (capture_busy), opens
      --     the row, pops the FIFO and writes one word per clock. Every write
      --     is reported on cap_wr, which is the single accept event.
      --   * When the burst ends (length used up, refresh, pending read or an
      --     abort) busy falls and the written words (wcnt) are committed to the
      --     write address and the budget once, through registered stages.
      -- ----------------------------------------------------------------
      abort_v := rd_mode_cur or drain_pending_r = '1' or run_level_r = '0'
                 or run_edge_r = '1';
      quiet_v := fifo_rd = '0' and rd_h1 = '0' and rd_h2 = '0' and rd_h3 = '0';
      -- Abort request to the controller: sticky while a burst is outstanding
      -- so a one-cycle abort pulse (run edge) cannot be missed.
      if cap_busy = '0' then
        if abort_v then
          cap_stop <= '1';
        else
          cap_stop <= '0';
        end if;
      elsif abort_v then
        cap_stop <= '1';
      end if;
      -- Words written in the current burst (dropped while idle).
      if pst = P_IDLE then
        wcnt <= 0;
      elsif cap_wr = '1' then
        wcnt <= wcnt + 1;
      end if;
      if buf_rem_single /= 0 then
        bnz_r <= '1';
      else
        bnz_r <= '0';
      end if;
      -- Metrics: one event per cycle, accept = a word written this cycle.
      if cap_busy = '1' then
        pump_valid_v := true;
        if cap_wr = '1' then
          pump_ready_v := true;
          pump_accept_v := true;
        else
          pump_stall_v := true;
        end if;
      elsif (not rd_mode_cur) and fifo_rdempty_r = '1' then
        pump_nodata_v := true;
      end if;

      case pst is
        when P_IDLE =>
          cap_req <= '0';
          if (not abort_v) and cap_busy = '0' and cap_wr = '0'
             and fifo_rdempty_r = '0' and pump_budget_done_q = '0'
             and (continuous_p = '1' or bnz_r = '1') then
            pst <= P_C1;
          end if;
        when P_C1 =>
          -- Operands (waddr, budget) are stable while idle.
          if abort_v then
            pst <= P_IDLE;
          else
            row_rem_r  <= 256 - (waddr mod 256);
            ra_r <= Max_Samples - waddr;
            pst <= P_C2;
          end if;
        when P_C2 =>
          if abort_v then
            pst <= P_IDLE;
          else
            if continuous_p = '1' then
              rb_r <= ra_r;
            else
              rb_r <= buf_rem_single;
            end if;
            pst <= P_C3;
          end if;
        when P_C3 =>
          if abort_v then
            pst <= P_IDLE;
          else
            if rb_r >= row_rem_r then
              len_r <= row_rem_r;
            else
              len_r <= rb_r;
            end if;
            pst <= P_C4;
          end if;
        when P_C4 =>
          if abort_v or len_r = 0 then
            pst <= P_IDLE;
          else
            cap_len <= std_logic_vector(to_unsigned(len_r, 9));
            cap_req <= '1';
            pst <= P_REQ;
          end if;
        when P_REQ =>
          -- Hold the request until the controller has latched it. On an
          -- abort drop it; the hold in P_RUN covers a latch that was in
          -- flight when the request fell.
          if cap_busy = '1' or abort_v then
            cap_req <= '0';
            run_hold <= 3;
            pst <= P_RUN;
          end if;
        when P_RUN =>
          if run_hold /= 0 then
            run_hold <= run_hold - 1;
          elsif cap_busy = '0' then
            pst <= P_M1;
          end if;
        when P_M1 =>
          ra_r <= waddr + wcnt;
          if continuous_p = '1' or wcnt >= buf_rem_single then
            rb_r <= 0;
          else
            rb_r <= buf_rem_single - wcnt;
          end if;
          pst <= P_M2;
        when P_M2 =>
          if ra_r = Max_Samples then
            wrap_r <= '1';
          else
            wrap_r <= '0';
          end if;
          if rb_r = 0 then
            remz_r <= '1';
          else
            remz_r <= '0';
          end if;
          pst <= P_M3;
        when P_M3 =>
          if wrap_r = '1' then
            waddr <= 0;
          else
            waddr <= ra_r;
          end if;
          if continuous_p = '1' then
            if wrap_r = '1' then
              ring_wrap_v := true;
            end if;
          else
            buf_rem_single <= rb_r;
            if remz_r = '1' then
              pump_budget_done_q <= '1';
            end if;
          end if;
          pst <= P_IDLE;
      end case;

      -- Run-start stale-word drain and post-budget discard. FIFO words are
      -- popped one per grant (pop_ok_r) and only while no burst is
      -- outstanding, so the controller and the drain never pop together.
      -- The stale count is snapshotted once the read side is quiet.
      drain_go_v := '0';
      if drain_pending_r = '1' then
        if drain_snapshot_pending_r = '1' then
          if cap_busy = '0' and quiet_v then
            drain_rem <= fifo_rdusedw_r;
            drain_snapshot_pending_r <= '0';
          end if;
        elsif drain_rem = 0 then
          drain_pending_r <= '0';
        elsif cap_busy = '0' then
          if pop_ok_r = '1' then
            drain_go_v := '1';
            drain_rem <= drain_rem - 1;
          elsif fifo_rdempty_r = '1' and quiet_v then
            drain_pending_r <= '0';  -- FIFO ran dry before target: stale gone
            drain_rem <= 0;
          end if;
        end if;
      elsif pump_budget_done_q = '1' and continuous_p = '0' and cap_busy = '0'
            and pop_ok_r = '1' then
        -- Budget exhausted: discard whatever else the producer pushed so the
        -- empty window below can be observed.
        drain_go_v := '1';
      end if;
      drain_rd_r <= drain_go_v;

      -- Single-shot completion: producer finished (or budget used up) and the
      -- FIFO has been empty and idle for 2047 consecutive pclk cycles.
      if drain_pending_r = '0' and continuous_p = '0' and run_level_r = '1'
         and (not rd_mode_cur)
         and (producer_done_q = '1' or pump_budget_done_q = '1'
              or overflow_keep_r = '1') then
        if fifo_rdempty_r = '1' and quiet_v and cap_wr = '0' then
          if single_drain_cnt = 2047 then
            full_i <= '1';
            rd_mode := true;
          else
            single_drain_cnt <= single_drain_cnt + 1;
          end if;
        else
          single_drain_cnt <= 0;
        end if;
      else
        single_drain_cnt <= 0;
      end if;

      -- Overflow from fast domain
      if overflow_clk = '1' then
        run_stop_overflow <= '1';
        status_overflow <= '1';
        drain_pending_r <= '1';
        drain_snapshot_pending_r <= '1';
        -- Snapshot as at run start. Overflow only latches in single-shot
        -- mode, where the producer has already stopped, so the fill can only
        -- shrink and a re-snapshot on repeated overflow pulses stays correct.
        if continuous_p = '0' and full_i = '0' then
          full_i <= '1';
          overflow_readout_q <= '1';
        end if;
      end if;
      -- Keep-overflow (raw single-shot, stage 2a): the sampler has stopped and
      -- everything already pushed is still in the FIFO. Flag the overflow but do
      -- NOT drain or end the capture: the pump keeps writing the FIFO contents
      -- and the normal end-of-capture window (below) completes the capture when
      -- the FIFO has stayed empty.
      if keep_clk = '1' then
        run_stop_overflow <= '1';
        status_overflow <= '1';
        overflow_keep_r <= '1';
      end if;
      if overflow_readout_q = '1' then
        rd_mode := true;
        overflow_readout_q <= '0';
      end if;

      if run_edge_r = '1' then
        drain_pending_r <= '1';
        drain_snapshot_pending_r <= '1';
        -- Snapshot the stale-word count. usedw is fill mod DEPTH: a full FIFO
        -- reads 0, so substitute the full depth when non-empty (the empty-
        -- pipeline drain exit absorbs any overestimate).
        -- Loaded from cfg_samples one pclk later, after the run-edge config
        -- latch has updated. Keep the pump count at zero for that holdoff cycle.
        buf_rem_single <= 0;
        single_drain_cnt <= 0;
        rd_pend := '0';
        full_i <= '0';
        run_stop_overflow <= '0';
        status_overflow <= '0';
        overflow_keep_r <= '0';
        overflow_readout_q <= '0';
        pump_budget_done_q <= '0';
        if run_start_r = '1' then
          pump_valid_cycles_u <= (others => '0');
          pump_ready_cycles_u <= (others => '0');
          pump_accept_cycles_u <= (others => '0');
          pump_stall_cycles_u <= (others => '0');
          pump_nodata_cycles_u <= (others => '0');
          pump_overflow_count_u <= (others => '0');
          overflow_count_en_q <= '0';
          waddr <= 0;
          -- Any burst bookkeeping from before the run start is void.
          pst <= P_IDLE;
          cap_req <= '0';
          wcnt <= 0;
        end if;
        if run_stop_r = '1' then
          rd_mode := true;
        else
          rd_mode := false;
        end if;
        s_wr <= '0'; s_rd <= '0';
        stream_active := false; rd_pend2 := '0';
        stream_addr_inc_pending := false;

      else
      -- Normal capture/readout/write-pump logic (skipped on run-edge cycle)

      -- Block-read toggle edge -> start a stream. Base/Count are stable by now
      -- (set on the OLS side before the toggle flipped and held for the stream).
      if blk_req_edge_r = '1' then
        if rd_mode_cur then
          -- Single-shot: read exactly the host-requested block.
          stream_addr_u := to_unsigned(Blk_Rd_Base + Start_Offset, 22);
          stream_rem    := Blk_Rd_Count;
          stream_addr_inc_pending := false;
          rd_pend2      := '0';
          stream_active := (Blk_Rd_Count /= 0);
          s_rd          <= '0';
        elsif continuous_p = '1' then
          -- Continuous indexed read: host byte address names an absolute sample
          -- index; physical storage is the full SDRAM rolling window.
          cont_base_v := Blk_Rd_Base mod CONT_RING_WORDS;
          stream_addr_u := to_unsigned(cont_base_v, 22);
          stream_rem    := Blk_Rd_Count;
          stream_addr_inc_pending := false;
          rd_pend2      := '0';
          stream_active := (Blk_Rd_Count /= 0);
          rd_burst_left := 0;
          rd_cool       := 0;
          -- rd_mode is NOT set: the write pump keeps running between the
          -- bounded read bursts below, so capture does not stall while the
          -- host drains the response FIFO at SPI speed.
          s_rd          <= '0';
        end if;
      end if;

      stream_addr_r <= std_logic_vector(stream_addr_u);
      if rd_mode_cur or stream_active_cur then

        if stream_active_cur then
          -- STREAMING READOUT: walk the block addresses, latch on the SDRAM
          -- valid strobe, push each sample into the response FIFO. Self-timed,
          -- so it is immune to the CLK/pclk phase relationship that broke the
          -- old fixed-latency latch at block boundaries.
          if rd_pend2_cur = '0' then
            if rd_gap_cur = '1' then
              rd_gap := '0';   -- let stream_addr_r catch up post-completion
            elsif continuous_p = '1' and not rd_mode_cur then
              -- Live (continuous) capture: bounded read bursts, then a cool-down
              -- with no read pending so refresh and the capture pump get the bus.
              if rd_burst_left /= 0 then
                rd_burst_left := rd_burst_left - 1;
                if rd_burst_left = 0 then
                  rd_cool := RD_COOL_N;
                end if;
                s_addr <= stream_addr_r;
                s_rd     <= '1';
                rd_pend2 := '1';
                rd_wd_cnt := 0;
              elsif rd_cool /= 0 then
                rd_cool := rd_cool - 1;
              elsif rdfifo_room_r = '1' and cap_low_r = '1' then
                rd_burst_left := RD_BURST_N - 1;
                s_addr <= stream_addr_r;
                s_rd     <= '1';
                rd_pend2 := '1';
                rd_wd_cnt := 0;
              end if;
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
            rdfifo_wr    <= '1';
            -- Advance the address on COMPLETION (not issue) so a timed-out
            -- read can be reissued at the same address without skipping.
            stream_addr_inc_pending := true;
            if stream_rem_le1_r = '1' then
              if Auto_Renew = '1' then
                -- Auto-renew: reload and keep streaming
                stream_rem := Blk_Rd_Count;
              else
                stream_active := false;
                stream_rem    := 0;
                -- Continuous mode: hand the SDRAM bus back to the write pump
                -- after the host has streamed its requested block.
                if continuous_p = '1' then
                  rd_mode      := false;
                end if;
              end if;
            else
              stream_rem := stream_rem_dec_r;
            end if;
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

      end if; -- end rd_mode
      end if; -- end run_edge_r else

      -- Status
      Status(0) <= run_level_r;
      Status(1) <= cap_busy and not cap_wr;
      Status(2) <= s_rd;
      Status(3) <= full_i;
      Status(4) <= status_overflow;
      Status(5) <= run_stop_overflow;
      Status(7 downto 6) <= (others => '0');

      -- Register the gated condition flags (short path off buf_rem),
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
      if Enable_Pump_Metrics then
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
        ring_filled <= '0';
      elsif producer_accept_q then
        newest_index_u <= producer_index_u;
        producer_index_u <= producer_index_u + 1;
        if cont_accept_q then
          if ring_filled = '1' then
            oldest_index_u <= oldest_index_u + 1;
          end if;
        end if;
      end if;
      -- ring_filled is set when a continuous burst that ends exactly at the
      -- ring end is committed (ring_wrap_q pulse). Every accept of that burst,
      -- including the one that wrapped the ring, was processed with
      -- ring_filled = '0'; every accept of later bursts sees '1' -- the same
      -- accept-by-accept result as setting it on the wrapping accept.
      if ring_wrap_q then
        ring_filled <= '1';
      end if;
      -- One accept event per written word (cap_wr is the controller's
      -- registered write strobe), possibly on every consecutive clock.
      producer_accept_q <= (cap_wr = '1');
      cont_accept_q <= (cap_wr = '1') and (continuous_p = '1');
      ring_wrap_q <= ring_wrap_v;
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
  Buffer_Full <= (others => '0');
  Producer_Index <= std_logic_vector(producer_snap_r);
  Oldest_Index <= std_logic_vector(oldest_snap_r);
  Newest_Index <= std_logic_vector(newest_snap_r);
  Overrun_Count <= std_logic_vector(oldest_snap_r);
  -- NOTE: the Pump_*_Cycles diagnostics below are NOT snapshot-protected (compiled out in production).
  -- Production images compile the pump metric counters out; register 0x60 then
  -- returns the dropped-sample counter (bits 15:0, saturating).
  Pump_Valid_Cycles <= std_logic_vector(pump_valid_cycles_u) when Enable_Pump_Metrics
                       else x"0000" & drop_val_p;
  Pump_Ready_Cycles <= std_logic_vector(pump_ready_cycles_u);
  Pump_Accept_Cycles <= std_logic_vector(pump_accept_cycles_u);
  Pump_Stall_Cycles <= std_logic_vector(pump_stall_cycles_u);
  Pump_NoData_Cycles <= std_logic_vector(pump_nodata_cycles_u);
  Pump_Overflow_Count <= std_logic_vector(resize(overflow_snap_r, 32));

  SDRAM_Interface1 : SDRAM_Interface
  generic map (Sim => Sim, CLK_Frequency => SDRAM_CLK_HZ, Write_Latency => Write_Latency, Read_Latency => Read_Latency, Page_Latency => Page_Latency)
  port map (
    CLK          => pclk,
    Reset        => '0',
    CLK_150_Out  => open,
    Address      => s_addr,
    Write_Enable => s_wr,
    Write_Data   => s_wdata,
    Capture_Burst_Req => cap_req,
    Capture_Burst_Addr => cap_addr,
    Capture_Burst_Len => cap_len,
    Capture_Burst_Stop => cap_stop,
    Capture_Pop_Ok => pop_ok_r,
    Capture_Data => fifo_rdata,
    Capture_Pop => cap_pop,
    Capture_Wr => cap_wr,
    Capture_Busy => cap_busy,
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

  
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all; 


ENTITY OLS_Interface IS
  GENERIC (
      CLK_Frequency   :   INTEGER     := 12000000;    
      SAMPLE_CLK_HZ  :   INTEGER     := 200_000_000;
    Max_Samples     :   NATURAL     := 25000       

  );
PORT (
  CLK : IN STD_LOGIC;
  FAST_CLK : IN STD_LOGIC := '0';
  SPI_CS       : IN  STD_LOGIC := '1';
  SPI_SCK      : IN  STD_LOGIC := '0';
  SPI_MOSI     : IN  STD_LOGIC := '0';
  SPI_MISO     : OUT STD_LOGIC := 'Z';
  Interface_Mode : OUT STD_LOGIC := '1';
  Inputs       : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');  
  Rate_Div     : BUFFER NATURAL range 1 to 500000000 := 12; 
  Samples      : BUFFER NATURAL range 1 to Max_Samples   := Max_Samples;  
  Start_Offset : BUFFER NATURAL range 0 to Max_Samples   := 0;  
  Run          : BUFFER STD_LOGIC := '0'; 
  Full         : IN  STD_LOGIC := '0'; 
  Address      : BUFFER NATURAL range 0 to Max_Samples-1 := 0;
  Outputs      : IN STD_LOGIC_VECTOR(31 downto 0);
  Gen_Load_Byte : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  Gen_Load_We   : OUT STD_LOGIC := '0';
  Gen_Start     : OUT STD_LOGIC := '0';
  Gen_Baud_Div  : OUT STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  Gen_Busy      : IN  STD_LOGIC := '0';
  Gen_Fifo_Count : IN STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  Gen_Proto     : OUT STD_LOGIC;
    Gen_TX_Pin    : OUT NATURAL range 0 to 31 := 0;
    Gen_SCL_Pin   : OUT NATURAL range 0 to 31 := 0;
    Gen_DE_Pin    : OUT NATURAL range 0 to 31 := 0;
    Gen_DE_Enable : OUT STD_LOGIC := '0';
    Gen_CS_Pin    : OUT NATURAL range 0 to 31 := 0;
    Gen_CS_Enable : OUT STD_LOGIC := '0';
    Gen_MISO_Pin  : OUT NATURAL range 0 to 31 := 0;
    Gen_MISO_Enable : OUT STD_LOGIC := '0';
   Gen_Clear      : OUT STD_LOGIC := '0';
   Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
   Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_I2C_Test   : OUT STD_LOGIC := '0';
    Gen_SPI_Test   : OUT STD_LOGIC := '0';
    Gen_Repeat     : OUT STD_LOGIC := '0';
    Gen_RS485_Pair : OUT STD_LOGIC := '0';
    Gen_Accel_Attach : OUT STD_LOGIC := '0';
     Armed          : OUT STD_LOGIC := '0';
      Fast_Mode      : OUT STD_LOGIC := '0';
      Continuous_Mode : OUT STD_LOGIC := '0';
      Narrow_Enable   : OUT STD_LOGIC := '0';
      Narrow_Channel  : OUT NATURAL range 0 to 15 := 0;
      Analog_Enable   : OUT STD_LOGIC := '0';
      Analog_Only     : OUT STD_LOGIC := '0';
      Analog_Profile  : OUT STD_LOGIC_VECTOR(1 downto 0) := (others => '0');
      Analog_Channel  : OUT NATURAL range 0 to 31 := 1;
      -- Parallel bit-packing capture mode select (REG_FLAGS bit 20). When set,
      -- the mso_capture front end drives the SDRAM write FIFO instead of the
      -- 128-bit Analog_Frame path.
      Packed_Mode     : OUT STD_LOGIC := '0';
       Buffer_Full     : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
       Buffer_Ack      : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
        Pin_Map_Write   : OUT STD_LOGIC := '0';
        Pin_Map_Channel : OUT NATURAL range 0 to 15 := 0;
        Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0;
         Gen_Capture_Tx_Channel  : OUT NATURAL range 0 to 15 := 0;
         Gen_Capture_Scl_Channel : OUT NATURAL range 0 to 15 := 1;
         Gen_Capture_CS_Channel  : OUT NATURAL range 0 to 15 := 0;
         Gen_Capture_CS_Enable   : OUT STD_LOGIC := '0';
         Gen_Capture_MISO_Channel : OUT NATURAL range 0 to 15 := 1;
         Gen_Capture_MISO_Enable  : OUT STD_LOGIC := '0';
         Gen_Capture_Active : OUT STD_LOGIC := '0';
         Debug_Ch0_Enable : OUT STD_LOGIC := '0';
         Debug_Ch0_Period : OUT STD_LOGIC_VECTOR(31 downto 0) := x"00000400";
         Debug_Ch0_Duty   : OUT STD_LOGIC_VECTOR(31 downto 0) := x"00000200";
         Gen_Start_Ack      : IN  STD_LOGIC := '0';
         Gen_Start_Reject   : IN  STD_LOGIC := '0';
         Gen_Done_Pulse     : IN  STD_LOGIC := '0';
         Gen_RX_Data      : IN  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
         Gen_RX_Used      : IN  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
         Gen_RX_Re        : OUT STD_LOGIC := '0';
         -- Block readout (CMD_READ_CAPTURE) via the FLA response FIFO. Replaces
         -- the fixed-latency Address/Outputs latch and its prime/drain hack.
         Blk_Rd_Req_Tog : OUT STD_LOGIC := '0';
         Blk_Rd_Base    : OUT NATURAL range 0 to Max_Samples := 0;
         Blk_Rd_Count   : OUT NATURAL range 0 to Max_Samples := 0;
         -- Auto-renew block read (pass-through to FLA, controlled by dispatch)
         Auto_Renew     : OUT STD_LOGIC := '0';
         Rd_Fifo_Q      : IN  STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
         Rd_Fifo_Empty  : IN  STD_LOGIC := '1';
         Rd_Fifo_RdReq  : OUT STD_LOGIC := '0';
         Producer_Index : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Oldest_Index   : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Newest_Index   : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Overrun_Count  : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Pump_Valid_Cycles   : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Pump_Ready_Cycles   : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Pump_Accept_Cycles  : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Pump_Stall_Cycles   : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Pump_NoData_Cycles  : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
         Pump_Overflow_Count : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0')

);
END OLS_Interface;

ARCHITECTURE BEHAVIORAL OF OLS_Interface IS

  SIGNAL Run_OLS  : STD_LOGIC := '0';
  SIGNAL Trigger_Mask   : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Trigger_Values : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL inputs_prev    : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Divider : NATURAL range 0 to 16777215 := 0;
  SIGNAL Read_Count  : NATURAL := 0;
  SIGNAL Delay_Count : NATURAL := 0;
  SIGNAL analog_enable_i  : STD_LOGIC := '0';
  SIGNAL analog_only_i    : STD_LOGIC := '0';
  SIGNAL analog_profile_i : STD_LOGIC_VECTOR(1 downto 0) := (others => '0');
  SIGNAL analog_channel_i : NATURAL range 0 to 31 := 1;
  SIGNAL packed_mode_i    : STD_LOGIC := '0';
  SIGNAL SPI_RX_Valid     : STD_LOGIC := '0';
  SIGNAL SPI_RX_Data      : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');
  -- SPI mode only: directly use SPI signals (no UART muxing)
  -- Generator FIFO depth (matches Signal_Gen.vhd generic)
  constant GEN_FIFO_DEPTH : natural := 256;


  SIGNAL gen_start_cnt : NATURAL range 0 to 63 := 0;
  SIGNAL gen_start_req : STD_LOGIC := '0';
  SIGNAL gen_busy_d    : STD_LOGIC := '0';
  SIGNAL gen_load_cnt  : NATURAL range 0 to 63 := 0;
  SIGNAL gen_load_events : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL gen_reg_load_req  : STD_LOGIC := '0';
  SIGNAL gen_reg_load_byte : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL disp_gen_load_d   : STD_LOGIC := '0';
  SIGNAL gen_reg_load_req_d : STD_LOGIC := '0';
   SIGNAL gen_tx_pin_int  : NATURAL range 0 to 31 := 3;
   SIGNAL gen_scl_pin_int : NATURAL range 0 to 31 := 1;  -- default=1 (CH0 is test counter, can't use 0)
  SIGNAL gen_de_pin_int   : NATURAL range 0 to 31 := 0;
  SIGNAL gen_de_enable_int : STD_LOGIC := '0';
  SIGNAL gen_cs_pin_int   : NATURAL range 0 to 31 := 0;
  SIGNAL gen_cs_enable_int : STD_LOGIC := '0';
  SIGNAL gen_miso_pin_int : NATURAL range 0 to 31 := 0;
  SIGNAL gen_miso_enable_int : STD_LOGIC := '0';
  SIGNAL gen_i2c_rd_len_int : NATURAL range 0 to 255 := 0;
  SIGNAL gen_i2c_dev_r_int  : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
   SIGNAL gen_i2c_test_int   : STD_LOGIC := '0';
   SIGNAL gen_spi_test_int   : STD_LOGIC := '0';
   SIGNAL gen_repeat_int     : STD_LOGIC := '0';
   SIGNAL gen_rs485_pair_int : STD_LOGIC := '0';
   SIGNAL gen_accel_attach_int : STD_LOGIC := '0';
  SIGNAL compress_mode_i    : STD_LOGIC_VECTOR(1 downto 0) := "00";
   SIGNAL gen_proto_int      : STD_LOGIC := '0';
   SIGNAL gen_baud_div_int   : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL fast_mode_i        : STD_LOGIC := '0';
  SIGNAL continuous_mode_i   : STD_LOGIC := '0';
  SIGNAL narrow_enable_i     : STD_LOGIC := '0';
  SIGNAL narrow_channel_i    : NATURAL range 0 to 15 := 0;
  SIGNAL spi_preamble        : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL spi_preamble_r      : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL spi_tx_ready_i      : STD_LOGIC := '0';

  SIGNAL ch_mode             : STD_LOGIC := '0';  -- 0=8ch/500k, 1=4ch/4M
  SIGNAL gen_capture_tx_channel_i  : NATURAL range 0 to 15 := 0;
  SIGNAL gen_capture_scl_channel_i : NATURAL range 0 to 15 := 1;
  SIGNAL gen_capture_cs_channel_i  : NATURAL range 0 to 15 := 0;
  SIGNAL gen_capture_cs_enable_i   : STD_LOGIC := '0';
  SIGNAL gen_capture_miso_channel_i : NATURAL range 0 to 15 := 1;
  SIGNAL gen_capture_miso_enable_i  : STD_LOGIC := '0';
  SIGNAL gen_capture_active_i  : STD_LOGIC := '0';
  SIGNAL debug_ch0_enable_i : STD_LOGIC := '0';
  SIGNAL debug_ch0_period_i : STD_LOGIC_VECTOR(31 downto 0) := x"00000400";
  SIGNAL debug_ch0_duty_i   : STD_LOGIC_VECTOR(31 downto 0) := x"00000200";
  SIGNAL gen_capture_done_i    : STD_LOGIC := '0';
  SIGNAL gen_capture_error_i   : STD_LOGIC := '0';
  SIGNAL gen_start_pulse     : STD_LOGIC := '0';
  SIGNAL gen_capture_guard   : NATURAL range 0 to 255 := 0;
  SIGNAL gen_capture_start   : STD_LOGIC := '0';
  type gen_cap_state_t is (GENCAP_IDLE, GENCAP_LOOPBACK_ON, GENCAP_ARM, GENCAP_GUARD, GENCAP_WAIT_BUSY, GENCAP_RUNNING, GENCAP_WAIT_FULL, GENCAP_DONE, GENCAP_ERROR);
  SIGNAL gen_cap_state : gen_cap_state_t := GENCAP_IDLE;

  -- Synthesis preserve: prevent Quartus from optimizing away gen start chain
  attribute preserve : boolean;
  attribute preserve of gen_start_cnt : signal is true;
  attribute preserve of gen_load_cnt : signal is true;
  attribute preserve of gen_start_req : signal is true;

  -- SPI packet protocol signals (streaming architecture — no wide payload buses)
  SIGNAL spi_cs_rise      : STD_LOGIC := '0';
  SIGNAL pkt_cmd_active       : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL pkt_seq              : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL pkt_payload_len      : NATURAL range 0 to MAX_RX_PAYLOAD_BYTES := 0;
  SIGNAL pkt_payload_byte     : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL pkt_payload_valid    : STD_LOGIC := '0';
  SIGNAL pkt_ok               : STD_LOGIC := '0';
  SIGNAL pkt_err              : STD_LOGIC := '0';
  -- First 8 payload bytes captured for quick dispatch access
  TYPE payload_header_t IS ARRAY(0 TO 7) OF STD_LOGIC_VECTOR(7 DOWNTO 0);
  SIGNAL rx_payload_header    : payload_header_t := (others => (others => '0'));
  SIGNAL rx_header_idx        : NATURAL range 0 TO 8 := 0;
  SIGNAL rx_header_len        : NATURAL range 0 TO MAX_RX_PAYLOAD_BYTES := 0;
  -- TX streaming interface
  SIGNAL pkt_tx_byte          : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL pkt_tx_done          : STD_LOGIC := '0';
  SIGNAL pkt_tx_payload_ready : STD_LOGIC := '0';
  SIGNAL raw_stream_tx_byte   : STD_LOGIC_VECTOR(7 downto 0) := x"FF";
  SIGNAL raw_stream_tx_sel    : STD_LOGIC := '0';
  SIGNAL disp_tx_build        : STD_LOGIC := '0';
  SIGNAL disp_tx_status       : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL disp_tx_len          : NATURAL range 0 to MAX_TX_PAYLOAD_BYTES := 0;
  SIGNAL disp_tx_seq          : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL disp_tx_payload_in   : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL disp_tx_payload_vld  : STD_LOGIC := '0';
  SIGNAL disp_arm             : STD_LOGIC := '0';
  SIGNAL disp_gen_arm         : STD_LOGIC := '0';
  SIGNAL disp_abort           : STD_LOGIC := '0';
  SIGNAL disp_reg_write       : STD_LOGIC := '0';
  SIGNAL disp_reg_addr        : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL disp_reg_wdata       : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL disp_gen_start       : STD_LOGIC := '0';
  SIGNAL disp_gen_load    : STD_LOGIC := '0';
  SIGNAL disp_gen_data    : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL block_rd_pending     : STD_LOGIC := '0';
  SIGNAL block_rd_ack         : STD_LOGIC := '0';
  SIGNAL block_rd_addr        : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL block_rd_issue_req   : STD_LOGIC := '0';
  SIGNAL block_rd_issue_addr  : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL block_rd_release     : STD_LOGIC := '0';
  SIGNAL block_rd_state       : NATURAL range 0 to 8 := 0;
  -- Watchdog kill: forces the block-read FSM back to idle when the dispatch
  -- gives up on a stalled block read (e.g. a read issued during continuous
  -- capture, where rd_mode is false so the FLA never streams and the response
  -- FIFO never fills). Without this the dispatch hangs in WAIT_BLOCK forever
  -- and every later command is ignored (the unrecoverable continuous wedge).
  SIGNAL block_rd_kill        : STD_LOGIC := '0';
  -- Each 1024-byte read block packs 2 samples per 32-bit block_buf entry
  -- (even sample in bits 15:0, odd in 31:16), so one block carries 512 samples
  -- on the wire as contiguous 16-bit little-endian words (no wasted high half).
  CONSTANT BLOCK_SAMPLES      : INTEGER := 512;
  -- Block readout now streams the 512 samples through the FLA response FIFO
  -- (a true CLK<->pclk CDC), so there is no fixed-latency latch to warm and the
  -- old prime/drain read-ahead/read-behind padding has been removed.
  SIGNAL block_rd_j           : INTEGER range 0 to BLOCK_SAMPLES := 0;
  -- Holds the even sample so the odd cycle can write the full 32-bit block_buf
  -- entry in one go (partial-width writes don't infer correctly on the RAM).
  SIGNAL block_pack_lo        : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL capture_seq          : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL done_latched         : STD_LOGIC := '0';
  SIGNAL done_suppressed      : STD_LOGIC := '0';
  SIGNAL disp_ack_done        : STD_LOGIC := '0';
  SIGNAL disp_ack_seq         : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL raw_blk_rd_base_cfg  : NATURAL range 0 to Max_Samples := 0;
  SIGNAL raw_blk_rd_count_cfg : NATURAL range 0 to 16384 := 0;
  SIGNAL raw_stream_req_active : STD_LOGIC := '0';
  SIGNAL raw_stream_comp_mode : STD_LOGIC := '0';
  SIGNAL raw_blk_req_fire     : STD_LOGIC := '0';
  SIGNAL block_fifo_rdreq     : STD_LOGIC := '0';
  SIGNAL raw_fifo_rdreq       : STD_LOGIC := '0';
  SIGNAL raw_fifo_drain_active : STD_LOGIC := '0';
  SIGNAL raw_comp_fifo_rdreq  : STD_LOGIC := '0';
  SIGNAL raw_comp_state       : NATURAL range 0 to 6 := 0;
  SIGNAL raw_comp_samples_read : NATURAL range 0 to 16384 := 0;
  SIGNAL raw_comp_samples_fed : NATURAL range 0 to 16384 := 0;
  SIGNAL raw_comp_flush_issued : STD_LOGIC := '0';
  SIGNAL raw_comp_done        : STD_LOGIC := '0';
  SIGNAL comp_sample_in        : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  -- Output FIFO between the streaming RLE compressor and the SPI shifter. The
  -- compressor runs far ahead of the slow SPI drain (it feeds a sample every
  -- few 100 MHz cycles; SPI clocks one byte every ~160), emitting runs into
  -- this FIFO in bursts which SPI then drains contiguously. Without buffering,
  -- the wire would go idle between runs during a long run's accumulation and
  -- the host would decode the idle bytes as bogus pairs. A WORD FIFO (not a
  -- byte FIFO) is used: the compressor emits one 16-bit word per valid, so a
  -- single write per emit -- half the entries and no per-entry dual-byte write
  -- mux, which matters on this ~full device. Bytes are split out at the SPI
  -- side (low byte first), like the raw-stream path. raw_comp_pop is the
  -- SPI-side "byte consumed" handshake. Depth 8 words with the DEPTH-4 feed
  -- gate below keeps the (up to two feeds x two words) in flight from
  -- overflowing.
  CONSTANT RAW_COMP_FIFO_DEPTH : NATURAL := 8;  -- words
  CONSTANT RAW_COMP_FIFO_LAST  : NATURAL := RAW_COMP_FIFO_DEPTH - 1;
  TYPE raw_comp_fifo_t IS ARRAY(0 TO RAW_COMP_FIFO_LAST) OF STD_LOGIC_VECTOR(15 downto 0);
  SIGNAL raw_comp_fifo        : raw_comp_fifo_t := (others => (others => '0'));
  SIGNAL raw_comp_fifo_wr_ptr : NATURAL range 0 to RAW_COMP_FIFO_LAST := 0;
  SIGNAL raw_comp_fifo_rd_ptr : NATURAL range 0 to RAW_COMP_FIFO_LAST := 0;
  SIGNAL raw_comp_fifo_count  : NATURAL range 0 to RAW_COMP_FIFO_DEPTH := 0;
  -- Byte serialization + idle framing live in the SPI dispatch process; process
  -- 1 only does whole-word pops via raw_comp_pop. When the FIFO is empty while
  -- streaming, the dispatch process shifts out 0x0000 filler WORDS (a count word
  -- is never 0x0000, so the host skips them) so the continuously-clocked wire is
  -- never starved into carrying ambiguous data between runs.
  SIGNAL raw_comp_pop         : STD_LOGIC := '0';
  CONSTANT SAMPLE_CLK_KHZ_SLV : STD_LOGIC_VECTOR(31 downto 0) :=
    STD_LOGIC_VECTOR(TO_UNSIGNED(SAMPLE_CLK_HZ / 1000, 32));
  SIGNAL sig_rd_pend_d1       : STD_LOGIC := '0';
  -- Drives the OUT request toggle so the FSM can read/flip it (port is OUT).
  SIGNAL blk_req_tog_i        : STD_LOGIC := '0';
-- Max entries per block read: 512 uncompressed, 192 when compression is active in streaming mode
SIGNAL blk_rd_samples : INTEGER range 0 to 512 := BLOCK_SAMPLES;
SIGNAL blk_rsp_words : INTEGER range 0 to 512 := BLOCK_SAMPLES;
  -- Local readback compressor (CLK domain — deliberately NOT in the FLA's
  -- 167 MHz cone). Passthrough when compression is disabled, so all block
  -- reads share one drain/store path.
  SIGNAL drain_in_cnt  : INTEGER range 0 to 512 := 0;
  SIGNAL comp_wait_cnt : INTEGER range 0 to 63 := 0;
  SIGNAL pad_req       : STD_LOGIC := '0';
  SIGNAL comp_rst_i    : STD_LOGIC := '0';
  SIGNAL comp_feed_i   : STD_LOGIC := '0';
  SIGNAL comp_flush_i  : STD_LOGIC := '0';
  SIGNAL comp_sample_hold : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL comp_out_data   : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL comp_out_valid  : STD_LOGIC := '0';
  SIGNAL comp_busy_i     : STD_LOGIC := '0';
  SIGNAL comp_in_ready_i : STD_LOGIC := '1';
  SIGNAL comp_enable_i : STD_LOGIC := '0';
  SIGNAL delta_mode_i : STD_LOGIC := '0';
  SIGNAL codec_out_data  : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL codec_out_valid : STD_LOGIC := '0';
  SIGNAL codec_busy      : STD_LOGIC := '0';
  SIGNAL codec_in_ready  : STD_LOGIC := '1';
  TYPE block_buf_t IS ARRAY(0 TO 255) OF STD_LOGIC_VECTOR(31 DOWNTO 0);
  SIGNAL block_buf            : block_buf_t := (others => (others => '0'));
  -- 21-cycle bit-serial divider for /3 (replaces 58-level lpm_divide)
  SIGNAL div3_shift   : STD_LOGIC_VECTOR(20 downto 0) := (others => '0');
  SIGNAL div3_acc     : NATURAL range 0 to 7 := 0;
  SIGNAL div3_result  : NATURAL range 0 to Max_Samples := 0;
  SIGNAL div3_count   : NATURAL range 0 to 31 := 0;
  SIGNAL div3_busy    : STD_LOGIC := '0';
  SIGNAL div3_pending : STD_LOGIC := '0';

  COMPONENT spi_packet_rx IS
  PORT (
    clk         : IN  STD_LOGIC;
    rst         : IN  STD_LOGIC := '0';
    rx_byte     : IN  STD_LOGIC_VECTOR(7 downto 0);
    rx_valid    : IN  STD_LOGIC;
    cs_rise     : IN  STD_LOGIC := '0';
    cmd_active  : OUT STD_LOGIC_VECTOR(7 downto 0);
    seq         : OUT STD_LOGIC_VECTOR(7 downto 0);
    payload_len : OUT NATURAL range 0 to MAX_RX_PAYLOAD_BYTES;
    payload_byte   : OUT STD_LOGIC_VECTOR(7 downto 0);
    payload_valid  : OUT STD_LOGIC;
    packet_ok   : OUT STD_LOGIC;
    packet_err  : OUT STD_LOGIC;
    err_bad_crc  : OUT STD_LOGIC;
    err_bad_sync : OUT STD_LOGIC;
    err_oversize : OUT STD_LOGIC
  );
  END COMPONENT;

  COMPONENT spi_packet_tx IS
  PORT (
    clk         : IN  STD_LOGIC;
    rst         : IN  STD_LOGIC := '0';
    req_seq     : IN  STD_LOGIC_VECTOR(7 downto 0);
    build       : IN  STD_LOGIC;
    rsp_status  : IN  STD_LOGIC_VECTOR(7 downto 0);
    rsp_len     : IN  NATURAL range 0 to MAX_TX_PAYLOAD_BYTES;
    payload_byte_in  : IN  STD_LOGIC_VECTOR(7 downto 0);
    payload_valid_in : IN  STD_LOGIC;
    payload_ready    : OUT STD_LOGIC;
    tx_ready    : IN  STD_LOGIC := '1';
    tx_byte     : OUT STD_LOGIC_VECTOR(7 downto 0);
    tx_done     : OUT STD_LOGIC
  );
  END COMPONENT;



  COMPONENT SPI_Slave2 IS
  PORT (
    sys_clk    : IN  STD_LOGIC;
    fast_clk   : IN  STD_LOGIC := '0';
    reset      : IN  STD_LOGIC := '0';
    SCK        : IN  STD_LOGIC := '0';
    MOSI       : IN  STD_LOGIC := '0';
    MISO       : OUT STD_LOGIC := 'Z';
    CS_n       : IN  STD_LOGIC := '1';
    TX_Data    : IN  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    SPI_Preamble   : IN  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    TX_Ready   : OUT STD_LOGIC := '0';
    RX_Data    : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    RX_Valid   : OUT STD_LOGIC := '0';
    CS_Rise    : OUT STD_LOGIC := '0'
  );
  END COMPONENT;

BEGIN
  PROCESS (CLK)
    variable fifo_v : raw_comp_fifo_t;
    variable fifo_wr_v : natural range 0 to RAW_COMP_FIFO_LAST;
    variable fifo_rd_v : natural range 0 to RAW_COMP_FIFO_LAST;
    variable fifo_count_v : natural range 0 to RAW_COMP_FIFO_DEPTH;
  BEGIN
  IF RISING_EDGE(CLK) THEN
    fifo_v := raw_comp_fifo;
    fifo_wr_v := raw_comp_fifo_wr_ptr;
    fifo_rd_v := raw_comp_fifo_rd_ptr;
    fifo_count_v := raw_comp_fifo_count;
    div3_pending <= '0';
    Pin_Map_Write <= '0';
    gen_reg_load_req <= '0';
    if raw_blk_req_fire = '1' then
      blk_req_tog_i <= NOT blk_req_tog_i;
    end if;
      IF disp_arm = '1' THEN
        Run_OLS <= '1';
      Run <= '0';  -- force a clean 0->1 Run edge so the FLA starts a new capture
      done_latched <= '0';
      done_suppressed <= '0';
      capture_seq <= std_logic_vector(unsigned(capture_seq) + 1);
    ELSIF disp_abort = '1' THEN
      Run_OLS <= '0';
      Run <= '0';
      done_latched <= '0';
      done_suppressed <= '1';
    ELSIF disp_ack_done = '1' THEN
      IF disp_ack_seq = x"00000000" OR disp_ack_seq = capture_seq THEN
        done_latched <= '0';
      END IF;
    ELSIF Full = '1' AND done_suppressed = '0' THEN
      done_latched <= '1';
    END IF;
    IF disp_reg_write = '1' THEN
      CASE disp_reg_addr IS
        WHEN REG_DIVIDER =>
          Divider <= TO_INTEGER(UNSIGNED(disp_reg_wdata(23 downto 0)));
        WHEN REG_SAMPLE_COUNT =>
          Read_Count <= TO_INTEGER(UNSIGNED(disp_reg_wdata(29 downto 0)));
          div3_pending <= '1';
        WHEN REG_DELAY_COUNT =>
          Delay_Count <= TO_INTEGER(UNSIGNED(disp_reg_wdata(29 downto 0)));
        WHEN REG_TRIGGER_MASK =>
          Trigger_Mask <= disp_reg_wdata;
        WHEN REG_TRIGGER_VALUE =>
          Trigger_Values <= disp_reg_wdata;
        WHEN REG_FLAGS =>
          fast_mode_i <= disp_reg_wdata(0);
          continuous_mode_i <= disp_reg_wdata(1);
          ch_mode <= disp_reg_wdata(2);
          analog_enable_i <= disp_reg_wdata(3);
          analog_only_i <= disp_reg_wdata(4);
          analog_profile_i <= disp_reg_wdata(6 downto 5);
          analog_channel_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(12 downto 8)));
          narrow_enable_i <= disp_reg_wdata(13);
          narrow_channel_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(17 downto 14)));
          compress_mode_i <= disp_reg_wdata(19 downto 18);
          packed_mode_i <= disp_reg_wdata(20);
        WHEN REG_FAST_MODE =>
          fast_mode_i <= disp_reg_wdata(0);
        WHEN REG_CONT_MODE =>
          continuous_mode_i <= disp_reg_wdata(0);
          IF disp_reg_wdata(0) = '1' THEN
            Run_OLS <= '1';
          ELSE
            -- Stop continuous. Only clear Run_OLS here; the capture-engine Run is
            -- owned by the arm/abort logic (disp_arm forces a clean 0->1 edge and
            -- abort clears it). Driving Run from this register handler too breaks
            -- ARM (it would also fire on stray reg writes and stick Run at 0).
            Run_OLS <= '0';
          END IF;
        WHEN REG_GEN_PROTO =>
          gen_proto_int <= disp_reg_wdata(0);
        WHEN REG_GEN_BAUD =>
          gen_baud_div_int <= disp_reg_wdata(15 downto 0);
        WHEN REG_GEN_PINS =>
          IF disp_reg_wdata(31) = '1' THEN
            Pin_Map_Channel <= TO_INTEGER(UNSIGNED(disp_reg_wdata(3 downto 0)));
            Pin_Map_Pin <= TO_INTEGER(UNSIGNED(disp_reg_wdata(12 downto 8)));
            Pin_Map_Write <= '1';
          ELSE
            gen_tx_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(4 downto 0)));
            gen_scl_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(12 downto 8)));
          END IF;
        WHEN REG_GEN_DATA =>
          -- Legacy CMD_I2C_TEST (0xA7) layout when upper bytes are set.
          -- Low-byte-only writes load the gen FIFO without touching mode flags.
          IF disp_reg_wdata(31 downto 8) = x"000000" THEN
            gen_reg_load_byte <= disp_reg_wdata(7 downto 0);
            gen_reg_load_req <= '1';
          ELSE
            gen_i2c_test_int <= disp_reg_wdata(0);
            gen_spi_test_int <= disp_reg_wdata(1);
            gen_repeat_int <= disp_reg_wdata(2);
            gen_rs485_pair_int <= disp_reg_wdata(3);
            -- bit 4: mirror the accelerometer bus (SDI/SPC/SDO) onto
            -- capture channels 13/14/15 so a normal capture shows the
            -- Bit_Engine <-> LIS3DH dialogue.
            gen_accel_attach_int <= disp_reg_wdata(4);
            gen_i2c_rd_len_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(15 downto 8)));
            gen_i2c_dev_r_int <= disp_reg_wdata(23 downto 16);
          END IF;

        WHEN REG_GEN_AUX_PINS =>
          gen_de_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(4 downto 0)));
          gen_de_enable_int <= disp_reg_wdata(5);
          gen_cs_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(12 downto 8)));
          gen_cs_enable_int <= disp_reg_wdata(13);
          gen_miso_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(20 downto 16)));
          gen_miso_enable_int <= disp_reg_wdata(21);

        WHEN REG_GEN_CAPTURE_TX_CHAN =>
          gen_capture_tx_channel_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(3 downto 0)));
        WHEN REG_GEN_CAPTURE_SCL_CHAN =>
          gen_capture_scl_channel_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(3 downto 0)));
        WHEN REG_GEN_CAPTURE_AUX =>
          gen_capture_cs_channel_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(3 downto 0)));
          gen_capture_cs_enable_i <= disp_reg_wdata(4);
          gen_capture_miso_channel_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(11 downto 8)));
          gen_capture_miso_enable_i <= disp_reg_wdata(12);
        WHEN REG_DEBUG_CH0_ENABLE =>
          debug_ch0_enable_i <= disp_reg_wdata(0);
        WHEN REG_DEBUG_CH0_PERIOD =>
          debug_ch0_period_i <= disp_reg_wdata;
        WHEN REG_DEBUG_CH0_DUTY =>
          debug_ch0_duty_i <= disp_reg_wdata;
        WHEN others => null;
      END CASE;
    END IF;

    IF (Divider < SAMPLE_CLK_HZ) THEN
      Rate_Div <= Divider + 1;
    ELSE
      Rate_Div <= SAMPLE_CLK_HZ;
    END IF;
    IF (Read_Count < Max_Samples) THEN
      IF (Read_Count > 1) THEN
        Samples <= Read_Count;
      ELSE
        Samples <= 2;
      END IF;
    ELSE
      Samples <= Max_Samples;
    END IF;
    -- Delay_Count=0 means read from sample 0 (legacy CMD_DELAY 0xC2 was a no-op).
    -- For triggered captures, set 0 < Delay_Count < Read_Count for pre-trigger depth.
    IF Delay_Count = 0 THEN
      Start_Offset <= 0;
    ELSIF (Read_Count > Delay_Count) THEN
      IF (Read_Count-Delay_Count < Max_Samples) THEN
        Start_Offset <= Read_Count-Delay_Count;
      ELSE
        Start_Offset <= Max_Samples - 1;
      END IF;
    ELSE
      IF (Read_Count > Max_Samples) THEN
        Start_Offset <= 10;
      ELSE
        Start_Offset <= 0;
      END IF;
    END IF;
    IF (Run = '0') THEN
      IF (Run_OLS = '1') THEN
        IF (UNSIGNED(Trigger_Mask(29 downto 0)) = 0) THEN
          Run <= '1';
        ELSIF (Trigger_Mask(31 downto 30) = "00") THEN
          -- Level trigger: fire when inputs match Trigger_Values on masked bits
          IF (UNSIGNED((Inputs XOR Trigger_Values) AND Trigger_Mask(29 downto 0)) = 0) THEN
            Run <= '1';
          END IF;
        ELSIF (Trigger_Mask(31 downto 30) = "01") THEN
          -- Rising edge: 0→1 transition on any masked channel
          IF (UNSIGNED(Inputs AND NOT inputs_prev AND Trigger_Mask(29 downto 0)) /= 0) THEN
            Run <= '1';
          END IF;
          ELSIF (Trigger_Mask(31 downto 30) = "10") THEN
            -- Falling edge: 1→0 transition on any masked channel
            IF (UNSIGNED(inputs_prev AND NOT Inputs AND Trigger_Mask(29 downto 0)) /= 0) THEN
              Run <= '1';
            END IF;
          END IF;
        END IF;
        IF Run_OLS = '1' THEN
          inputs_prev <= Inputs;
        END IF;
    END IF;
    -- ── Block read state machine (for CMD_READ_CAPTURE) ──────────────
    -- Request a BLOCK_SAMPLES-long raw stream from the FLA and drain it
    -- through the response FIFO (a true pclk->CLK CDC), feeding every popped
    -- word through the merged delta->RLE compressor. With compression
    -- disabled the wrapper is a 1:1 passthrough, so one unified store path
    -- packs the (raw or compressed) output words into block_buf and
    -- blk_rsp_words carries the exact response word count.
    sig_rd_pend_d1 <= block_rd_pending;
    block_fifo_rdreq <= '0';
    comp_feed_i <= '0';
    comp_rst_i <= '0';
    comp_flush_i <= '0';
    raw_comp_fifo_rdreq <= '0';
    -- The dispatch process consumed a whole output word: pop it off the FIFO.
    if raw_comp_pop = '1' and fifo_count_v > 0 then
      if fifo_rd_v = RAW_COMP_FIFO_LAST then
        fifo_rd_v := 0;
      else
        fifo_rd_v := fifo_rd_v + 1;
      end if;
      fifo_count_v := fifo_count_v - 1;
    end if;
    if raw_stream_comp_mode = '0' then
      fifo_wr_v := 0;
      fifo_rd_v := 0;
      fifo_count_v := 0;
      raw_comp_state <= 0;
      raw_comp_samples_read <= 0;
      raw_comp_samples_fed <= 0;
      raw_comp_flush_issued <= '0';
      raw_comp_done <= '0';
    end if;

    -- Store path: pack compressor output words (raw passthrough or delta
    -- stream) 2-per-32-bit block_buf entry. Clamped at BLOCK_SAMPLES words:
    -- incompressible content can emit MORE words than went in (overflow
    -- keyframes); a truncated payload decompresses short and the host
    -- retries that block uncompressed.
    -- NB: this if/elsif is the ONLY block_buf write site — a second write
    -- elsewhere in the process makes the writes non-exclusive, block_buf
    -- stops inferring as an M9K altsyncram, and 8 kbit of RAM explodes into
    -- ~15k logic cells of registers and muxes.
    IF comp_out_valid = '1' THEN
      IF raw_stream_comp_mode = '1' THEN
        -- Push one emitted word into the word FIFO. The FETCH feed gate leaves
        -- room for the (at most two feeds x two words) that can be in flight.
        if fifo_count_v < RAW_COMP_FIFO_DEPTH then
          fifo_v(fifo_wr_v) := comp_out_data;
          if fifo_wr_v = RAW_COMP_FIFO_LAST then
            fifo_wr_v := 0;
          else
            fifo_wr_v := fifo_wr_v + 1;
          end if;
          fifo_count_v := fifo_count_v + 1;
        else
          report "raw RLE output FIFO overflow" severity failure;
        end if;
      ELSIF block_rd_j < BLOCK_SAMPLES THEN
        IF (block_rd_j MOD 2) = 0 THEN
          block_pack_lo <= comp_out_data;
        ELSE
          block_buf(block_rd_j / 2) <= comp_out_data & block_pack_lo;
        END IF;
        block_rd_j <= block_rd_j + 1;
      END IF;
    ELSIF pad_req = '1' THEN
      -- Odd word count: zero-pad the high half of the final entry.
      IF (block_rd_j MOD 2) = 1 THEN
        block_buf(block_rd_j / 2) <= x"0000" & block_pack_lo;
      END IF;
      pad_req <= '0';
    END IF;

    IF block_rd_release = '1' THEN
      block_rd_pending <= '0';
    END IF;
    IF block_rd_kill = '1' THEN
      -- Dispatch watchdog gave up on a stalled stream; unwind the FSM so the
      -- next block read starts clean instead of resuming a half-finished one.
      block_rd_state <= 0;
      block_rd_ack   <= '0';
      block_rd_pending <= '0';
    ELSIF block_rd_issue_req = '1' AND block_rd_pending = '0' THEN
      block_rd_addr <= block_rd_issue_addr;
      block_rd_pending <= '1';
    ELSIF block_rd_pending = '1' AND sig_rd_pend_d1 = '0' THEN
      -- block_rd_addr is a BYTE address; the wire is 2 bytes/sample, so the
      -- base sample index = byte_addr / 2 (one 1024-byte block = 512 samples).
      block_rd_j <= 0;
      drain_in_cnt <= 0;
      blk_rsp_words <= 0;
      pad_req <= '0';
      comp_rst_i <= '1';   -- fresh anchor per block: each response payload
                           -- must be independently decodable by the host
      block_rd_state <= 1;
    END IF;
    CASE block_rd_state IS
      WHEN 1 =>
        -- Base/Count have been stable since the pending edge; fire the request
        -- toggle (single-bit CDC) to start the stream on the FLA side.
        blk_req_tog_i <= NOT blk_req_tog_i;
        block_rd_state <= 2;
      WHEN 2 =>
        -- Drain one sample: pop when the FIFO has data (rdreq asserted next
        -- cycle, q valid the cycle after that -- showahead OFF). The pop is
        -- also gated on comp_in_ready: the compressor drops inputs while it
        -- flushes a packed group.
        IF drain_in_cnt = blk_rd_samples THEN
          comp_wait_cnt <= 0;
          block_rd_state <= 6;
        ELSIF Rd_Fifo_Empty = '0' AND comp_in_ready_i = '1' THEN
          block_fifo_rdreq <= '1';
          block_rd_state <= 3;
        END IF;
      WHEN 3 =>
        block_rd_state <= 4;
      WHEN 4 =>
        block_rd_state <= 7;
      WHEN 7 =>
        -- q now holds the popped sample. Latch it first so the compressor sees
        -- a stable word for the full cycle before sample_valid is pulsed.
        comp_sample_hold <= Rd_Fifo_Q;
        block_rd_state <= 8;
      WHEN 8 =>
        comp_feed_i <= '1';
        drain_in_cnt <= drain_in_cnt + 1;
        block_rd_state <= 2;
      WHEN 6 =>
        comp_flush_i <= '1';
        -- All input words fed; wait for the compressor to finish flushing
        -- the final packed group. If overflow keyframes left a partial tail
        -- group (busy never clears without more input), time out — the
        -- short payload is caught by the host's uncompressed retry.
        IF (comp_busy_i = '0' AND comp_out_valid = '0')
           OR comp_wait_cnt = 63 THEN
          pad_req <= '1';   -- executed by the single block_buf write site
          block_rd_state <= 5;
        ELSE
          comp_wait_cnt <= comp_wait_cnt + 1;
        END IF;
      WHEN 5 =>
        IF pad_req = '0' THEN
          blk_rsp_words <= block_rd_j;
          block_rd_ack <= '1';
          IF block_rd_pending = '0' THEN
            block_rd_ack <= '0';
            block_rd_state <= 0;
          END IF;
        END IF;
      WHEN OTHERS =>
        null;
    END CASE;

    if raw_blk_req_fire = '1' and raw_stream_comp_mode = '1' then
      comp_rst_i <= '1';
      raw_comp_state <= 1;
      raw_comp_samples_read <= 0;
      raw_comp_samples_fed <= 0;
      raw_comp_flush_issued <= '0';
      raw_comp_done <= '0';
      fifo_wr_v := 0;
      fifo_rd_v := 0;
      fifo_count_v := 0;
    elsif raw_stream_comp_mode = '1' then
      case raw_comp_state is
        -- ── STATE 1: FETCH ───────────────────────────────────────────────
        -- Issue one FIFO read, or transition to flush once every requested
        -- source sample has been fed. Gated only on the compressor being ready
        -- and the output FIFO having room (so the compressor may run ahead of
        -- the slow SPI drain and buffer whole runs). Rd_Fifo latency is two
        -- cycles (mirror the block-read drain): rdreq here, WAIT (state 2), q
        -- valid in state 3, latched there, then fed in state 5.
        when 1 =>
          if raw_comp_samples_read >= raw_blk_rd_count_cfg then
            raw_comp_state <= 4;  -- all samples fed; go flush
          elsif Rd_Fifo_Empty = '0'
                and comp_in_ready_i = '1'
                and fifo_count_v <= RAW_COMP_FIFO_DEPTH - 4 then
            raw_comp_fifo_rdreq <= '1';
            raw_comp_samples_read <= raw_comp_samples_read + 1;
            raw_comp_state <= 2;
          end if;

        -- ── STATE 2: WAIT ────────────────────────────────────────────────
        -- Rd_Fifo latency cycle: rdreq was asserted in state 1, but Rd_Fifo_Q
        -- does not present the popped sample until the following cycle.
        when 2 =>
          raw_comp_state <= 3;

        -- ── STATE 3: FEED ────────────────────────────────────────────────
        -- Rd_Fifo_Q now holds the requested sample. Latch it before pulsing
        -- sample_valid so the compressor does not sample a moving FIFO output.
        when 3 =>
          raw_comp_state <= 5;

        -- Feed the previously latched sample into the compressor.
        when 5 =>
          comp_sample_hold <= Rd_Fifo_Q;
          raw_comp_state <= 6;

        when 6 =>
          comp_feed_i <= '1';
          raw_comp_samples_fed <= raw_comp_samples_fed + 1;
          raw_comp_state <= 1;

        -- ── STATE 4: FLUSH ───────────────────────────────────────────────
        -- All samples fed: drain the FIFO, emit the compressor's final held
        -- run, then finish.
        when 4 =>
          if raw_comp_flush_issued = '0'
             and fifo_count_v = 0 then
            comp_flush_i <= '1';
            raw_comp_flush_issued <= '1';
          elsif raw_comp_flush_issued = '1'
                and comp_busy_i = '0' and comp_out_valid = '0' then
            raw_comp_done <= '1';
            raw_comp_state <= 0;
          end if;

        when others =>
          raw_comp_state <= 0;
      end case;
    end if;

    raw_comp_fifo       <= fifo_v;
    raw_comp_fifo_wr_ptr <= fifo_wr_v;
    raw_comp_fifo_rd_ptr <= fifo_rd_v;
    raw_comp_fifo_count <= fifo_count_v;
  END IF;
  END PROCESS;

  -- Generator load/start: dedicated process so FIFO writes and Start are not
  -- lost across the dispatch/streaming handshake timing.
  gen_ctl: PROCESS (CLK)
  BEGIN
    IF RISING_EDGE(CLK) THEN
      Gen_Load_We <= '0';
      Gen_Start <= '0';
      Gen_Clear <= disp_abort;  -- abort stops the generator and flushes its FIFO

      IF (disp_gen_load = '1' AND disp_gen_load_d = '0')
         OR (gen_reg_load_req = '1' AND gen_reg_load_req_d = '0') THEN
        IF disp_gen_load = '1' AND disp_gen_load_d = '0' THEN
          Gen_Load_Byte <= disp_gen_data;
        ELSE
          Gen_Load_Byte <= gen_reg_load_byte;
        END IF;
        Gen_Load_We <= '1';
        IF unsigned(gen_load_events) < 255 THEN
          gen_load_events <= std_logic_vector(unsigned(gen_load_events) + 1);
        END IF;
      END IF;
      disp_gen_load_d <= disp_gen_load;
      gen_reg_load_req_d <= gen_reg_load_req;

      -- Hold start through the full transmission; clear after Gen_Busy falls.
      IF disp_abort = '1' THEN
        gen_start_req <= '0';
        gen_load_events <= (others => '0');
      ELSIF disp_gen_start = '1' OR gen_start_pulse = '1' OR (pkt_ok = '1' AND pkt_cmd_active = CMD_GEN_START) THEN
        gen_start_req <= '1';
      ELSIF Gen_Busy = '0' AND gen_busy_d = '1' THEN
        gen_start_req <= '0';
      END IF;
      gen_busy_d <= Gen_Busy;

      IF gen_start_req = '1' THEN
        Gen_Start <= '1';
      END IF;
    END IF;
  END PROCESS;

  -- Generated-capture FSM: guard period + GEN_START after ARM.
  -- gen_capture_active is set when Gen_Busy goes high and held until
  -- Full (capture buffer full), ensuring the loopback mux stays active
  -- until the capture completes — not tied to gen_busy duration alone.
  gen_capture_fsm: PROCESS (CLK)
    VARIABLE guard_var : NATURAL range 0 to 1023 := 0;
    VARIABLE timeout_var : NATURAL range 0 to 8388607 := 0;
    VARIABLE disp_gen_arm_d : STD_LOGIC := '0';
  BEGIN
    IF RISING_EDGE(CLK) THEN
      gen_start_pulse <= '0';
      IF disp_abort = '1' THEN
        gen_cap_state <= GENCAP_IDLE;
        gen_capture_active_i <= '0';
        gen_capture_done_i <= '0';
        gen_capture_error_i <= '0';
      ELSE
        CASE gen_cap_state IS
          WHEN GENCAP_IDLE =>
            IF disp_gen_arm = '1' AND disp_gen_arm_d = '0' THEN
              gen_capture_active_i <= '1';
              gen_capture_done_i <= '0';
              gen_capture_error_i <= '0';
              guard_var := 512;
              gen_cap_state <= GENCAP_GUARD;
            END IF;
          WHEN GENCAP_GUARD =>
            IF guard_var > 0 THEN
              guard_var := guard_var - 1;
            ELSE
              gen_start_pulse <= '1';
              timeout_var := 2000;
              gen_cap_state <= GENCAP_WAIT_BUSY;
            END IF;
          WHEN GENCAP_WAIT_BUSY =>
            gen_start_pulse <= '1';
            IF Gen_Busy = '1' OR Gen_Start_Ack = '1' THEN
              timeout_var := 5000000;
              gen_cap_state <= GENCAP_RUNNING;
            ELSIF Gen_Start_Reject = '1' OR timeout_var = 0 THEN
              gen_cap_state <= GENCAP_ERROR;
            ELSE
              timeout_var := timeout_var - 1;
            END IF;
          WHEN GENCAP_RUNNING =>
            gen_start_pulse <= '0';
            IF Gen_Busy = '0' AND gen_busy_d = '1' THEN
              gen_cap_state <= GENCAP_WAIT_FULL;
            ELSIF timeout_var = 0 THEN
              gen_cap_state <= GENCAP_ERROR;
            ELSE
              timeout_var := timeout_var - 1;
            END IF;
          WHEN GENCAP_WAIT_FULL =>
            gen_start_pulse <= '0';
            IF Full = '1' THEN
              gen_capture_active_i <= '0';
              gen_capture_done_i <= '1';
              gen_cap_state <= GENCAP_DONE;
            END IF;
          WHEN GENCAP_DONE =>
            gen_start_pulse <= '0';
            NULL;
          WHEN GENCAP_ERROR =>
            gen_start_pulse <= '0';
            gen_capture_error_i <= '1';
            gen_capture_active_i <= '0';
            gen_cap_state <= GENCAP_IDLE;
          WHEN OTHERS =>
            NULL;
        END CASE;
      END IF;
      disp_gen_arm_d := disp_gen_arm;
    END IF;
  END PROCESS;

  -- ─── 21-cycle bit-serial divider solves lpm_divide timing hole ─────
  -- N/3 computed one bit at a time (MSB first). Takes 21 cycles = 0.44us.
  -- Triggered by div3_pending pulse from main process when Read_Count changes.
  divider_proc: process(CLK)
    variable acc : natural range 0 to 6 := 0;
  begin
    if rising_edge(CLK) then
      if div3_pending = '1' then
        div3_shift <= std_logic_vector(to_unsigned(Read_Count, 21));
        div3_acc   <= 0;
        div3_result <= 0;
        div3_count  <= 21;
        div3_busy  <= '1';
      elsif div3_busy = '1' and div3_count > 0 then
        acc := div3_acc * 2;
        if div3_shift(20) = '1' then acc := acc + 1; end if;
        div3_shift <= div3_shift(19 downto 0) & '0';
        div3_acc <= acc;
        if acc >= 3 then
          div3_result <= div3_result + 1;
          div3_acc <= acc - 3;
        end if;
        div3_count <= div3_count - 1;
        if div3_count = 1 then
          div3_busy <= '0';
        end if;
      end if;
    end if;
  end process;

  -- Gen-FIFO depth invariant (simulation-only check)
  -- pragma translate_off
  assert GEN_FIFO_DEPTH > 0 report "GEN_FIFO_DEPTH must be > 0" severity failure;
  -- pragma translate_on

  -- Bring-up/status preamble: run flags plus sticky SPI packet diagnostics.
  -- Registered on sys_clk before crossing to fast_clk SPI slave domain.
  spi_preamble <= Run & Run_OLS & Full & '1' & "0000";
  process(CLK)
  begin
    if rising_edge(CLK) then
      spi_preamble_r <= spi_preamble;
    end if;
  end process;

  Gen_Proto    <= gen_proto_int;
  Gen_Baud_Div <= gen_baud_div_int;
  Gen_TX_Pin   <= gen_tx_pin_int;
  Gen_SCL_Pin <= gen_scl_pin_int;
  Gen_DE_Pin <= gen_de_pin_int;
  Gen_DE_Enable <= gen_de_enable_int;
  Gen_CS_Pin <= gen_cs_pin_int;
  Gen_CS_Enable <= gen_cs_enable_int;
  Gen_MISO_Pin <= gen_miso_pin_int;
  Gen_MISO_Enable <= gen_miso_enable_int;
  Gen_I2C_Rd_Len <= gen_i2c_rd_len_int;
  Gen_I2C_Dev_R  <= gen_i2c_dev_r_int;
  Gen_I2C_Test   <= gen_i2c_test_int;
  Gen_SPI_Test   <= gen_spi_test_int;
  Gen_Repeat     <= gen_repeat_int;
  Gen_RS485_Pair <= gen_rs485_pair_int;
  Gen_Accel_Attach <= gen_accel_attach_int;
  Fast_Mode      <= fast_mode_i;
  Blk_Rd_Req_Tog <= blk_req_tog_i;
  Blk_Rd_Base    <= raw_blk_rd_base_cfg when raw_stream_req_active = '1'
                    else TO_INTEGER(UNSIGNED(block_rd_addr(15 downto 1)));
  Blk_Rd_Count   <= raw_blk_rd_count_cfg when raw_stream_req_active = '1'
                    else blk_rd_samples;
  Continuous_Mode <= continuous_mode_i;
  Narrow_Enable <= narrow_enable_i;
  Narrow_Channel <= narrow_channel_i;
  Analog_Enable <= analog_enable_i;
  Analog_Only <= analog_only_i;
  Analog_Profile <= analog_profile_i;
  Analog_Channel <= analog_channel_i;
  Packed_Mode <= packed_mode_i;
  Buffer_Ack      <= (others => '0');  -- FLA frees its own continuous buffers
  Armed          <= Run_OLS;
  Gen_Capture_Tx_Channel <= gen_capture_tx_channel_i;
  Gen_Capture_Scl_Channel <= gen_capture_scl_channel_i;
  Gen_Capture_CS_Channel <= gen_capture_cs_channel_i;
  Gen_Capture_CS_Enable <= gen_capture_cs_enable_i;
  Gen_Capture_MISO_Channel <= gen_capture_miso_channel_i;
  Gen_Capture_MISO_Enable <= gen_capture_miso_enable_i;
  Debug_Ch0_Enable <= debug_ch0_enable_i;
  Debug_Ch0_Period <= debug_ch0_period_i;
  Debug_Ch0_Duty <= debug_ch0_duty_i;
  Gen_Capture_Active <= gen_capture_active_i;
  -- Pin_Map_Write is driven from the main process (default low, pulsed in CMD_PIN_MAP handler)

  Interface_Mode <= '1';

  -- Mux TX_Data between UART path (UART mode) and packet protocol (SPI mode)
  Rd_Fifo_RdReq <= '1' when block_fifo_rdreq = '1'
                           or raw_fifo_rdreq = '1'
                           or raw_comp_fifo_rdreq = '1' else '0';

  spi_tx_mux : block
    signal spi_tx_tdata : std_logic_vector(7 downto 0) := x"FF";
  begin
    spi_tx_tdata <= raw_stream_tx_byte when raw_stream_tx_sel = '1' else pkt_tx_byte;
      SPI_Slave1 : SPI_Slave2
      PORT MAP (
        sys_clk    => CLK,
        fast_clk   => FAST_CLK,
      reset      => '0',
      SCK        => SPI_SCK,
      MOSI       => SPI_MOSI,
      MISO       => SPI_MISO,
      CS_n       => SPI_CS,
      TX_Data    => spi_tx_tdata,
      SPI_Preamble   => spi_preamble_r,
        TX_Ready   => spi_tx_ready_i,
        RX_Data    => SPI_RX_Data,
        RX_Valid   => SPI_RX_Valid,
        CS_Rise    => spi_cs_rise
      );
  end block;

  -- ── SPI Packet Protocol (parallel path, SPI mode only) ───────────
  -- Decode SPI byte stream into framed packets
  pkt_rx_inst : spi_packet_rx
  PORT MAP (
    clk         => CLK,
    rst         => '0',
    rx_byte     => SPI_RX_Data,
    rx_valid    => SPI_RX_Valid,
    cs_rise     => spi_cs_rise,
    seq         => pkt_seq,
    payload_len => pkt_payload_len,
    payload_byte   => pkt_payload_byte,
    payload_valid  => pkt_payload_valid,
    cmd_active     => pkt_cmd_active,
    packet_ok   => pkt_ok,
    packet_err  => pkt_err,
    err_bad_crc  => open,
    err_bad_sync => open,
    err_oversize => open
  );

  -- ── RX payload header capture & GEN_LOAD streaming ───────────────
  -- Captures first 8 payload bytes for quick dispatch access.
  -- Routes GEN_LOAD payload bytes to disp_gen_data (caught by main process).
  rx_stream_handler: process(CLK)
  begin
    if rising_edge(CLK) then
      disp_gen_load <= '0';
      if pkt_payload_valid = '1' then
        if rx_header_idx < 8 then
          rx_payload_header(rx_header_idx) <= pkt_payload_byte;
          rx_header_idx <= rx_header_idx + 1;
        end if;
        if pkt_cmd_active = CMD_GEN_LOAD then
          disp_gen_data <= pkt_payload_byte;
          disp_gen_load <= '1';
        end if;
      end if;
      if pkt_ok = '1' or pkt_err = '1' then
        rx_header_idx <= 0;
      end if;
    end if;
  end process;
  rx_header_len <= pkt_payload_len;

  -- ── SPI Packet Protocol: Dispatch & Response Builder (streaming) ─
  -- All control registers are small (no wide payload buses).
  -- Block read data is streamed directly from block_buf to the TX.
  spi_pkt_dispatch: process(CLK)
    type state_t is (IDLE, EXEC, WAIT_BLOCK, BUILD_RSP, FEED_TX, WAIT_TX, RAW_STREAM);
    variable st : state_t := IDLE;
    variable rsp_seq_v : std_logic_vector(7 downto 0) := (others => '0');
    variable rsp_stat_v : std_logic_vector(7 downto 0) := ST_OK;
    variable rsp_len_v : natural range 0 to MAX_TX_PAYLOAD_BYTES := 0;
    -- Small response buffer (24 bytes covers status metadata responses)
    type rspbuf_t is array(0 to 23) of std_logic_vector(7 downto 0);
    variable rsp_buf : rspbuf_t;
    variable rsp_buf_len : natural range 0 to 24 := 0;
    variable rsp_buf_idx : natural range 0 to 24 := 0;
    variable reg_val : std_logic_vector(31 downto 0) := (others => '0');
    -- Block-read streaming state
    variable blk_wc : natural range 0 to 255 := 0;  -- word counter
    variable blk_bc : natural range 0 to 3 := 0;    -- byte-within-word counter
    variable blk_bytes_sent : natural range 0 to MAX_TX_PAYLOAD_BYTES := 0;
    -- Flag: true when payload comes from block_buf, not rsp_buf
    variable feeding_block : boolean := false;
    variable feed_wait_ready_low : boolean := false;
    variable block_last_v : boolean := false;
    -- A normal 512-sample block read completes in ~5000 CLK cycles (~50 us at
    -- 100 MHz); 50000 gives ~10x margin before the watchdog declares a stall.
    constant BLOCK_WD_MAX : natural := 50000;
    -- Watchdog for WAIT_BLOCK. A healthy block read completes in a few thousand
    -- CLK cycles; if block_rd_ack has not arrived well past that the stream has
    -- stalled (read issued during continuous capture), so give up and recover
    -- rather than hang the whole command dispatcher forever.
    variable block_wd : natural range 0 to BLOCK_WD_MAX := 0;
    variable raw_start_pending : boolean := false;
    variable raw_words_rem : natural range 0 to 16384 := 0;
    variable raw_fetch_state : natural range 0 to 2 := 0;
    variable raw_word : std_logic_vector(15 downto 0) := (others => '0');
    variable raw_have_word : boolean := false;
    variable raw_byte_hi_next : boolean := false;
    -- Compressed-stream byte serializer state: which byte of the current
    -- output word is next, and whether that word is a 0x0000 idle filler
    -- (FIFO was empty at the word boundary) rather than a real FIFO word.
    variable raw_comp_bhi : std_logic := '0';
    variable raw_comp_word_idle : boolean := false;
  begin
    if rising_edge(CLK) then
      -- Defaults
      disp_tx_build <= '0';
      disp_arm <= '0';
      disp_gen_arm <= '0';
      disp_abort <= '0';
      disp_reg_write <= '0';
      disp_gen_start <= '0';
      disp_tx_payload_vld <= '0';
      block_rd_kill <= '0';
      Gen_RX_Re <= '0';
      block_rd_issue_req <= '0';
      block_rd_release <= '0';
      disp_ack_done <= '0';
      raw_blk_req_fire <= '0';
      raw_fifo_rdreq <= '0';
      raw_comp_pop <= '0';

      -- (The idle-loop SDRAM prefetch that used to live here was removed:
      -- a prefetch block read was never released by anyone — its ack/pending
      -- stuck high until the next host WAIT_BLOCK consumed the stale ack and
      -- served the WRONG address's data while the host request's own issue
      -- was silently swallowed. During continuous capture it also reissued
      -- endlessly as Oldest advanced, stealing the SDRAM bus from the write
      -- pump. It only existed to feed the abandoned compressed-streaming
      -- path. See tb_batched_reads.)
      if disp_arm = '1' then
        raw_stream_tx_sel <= '0';
        raw_stream_req_active <= '0';
        raw_stream_comp_mode <= '0';
        raw_start_pending := false;
        raw_words_rem := 0;
      elsif disp_abort = '1' then
        raw_stream_tx_sel <= '0';
        raw_stream_req_active <= '0';
        raw_stream_comp_mode <= '0';
        raw_start_pending := false;
        raw_words_rem := 0;
      end if;
        -- Clear streaming mode on CS rise (host drops SPI chip select).
        -- Also flag the residual Rd_Fifo entries so the drain logic below
        -- flushes them before the next command reuses the FIFO read path.
        if spi_cs_rise = '1' then
          raw_stream_tx_sel <= '0';
          raw_stream_req_active <= '0';
          raw_stream_comp_mode <= '0';
        raw_start_pending := false;
        raw_words_rem := 0;
        if st = RAW_STREAM then
          st := IDLE;
        end if;
        raw_fifo_drain_active <= '1';
      end if;

      if (raw_start_pending or st = RAW_STREAM) and raw_stream_comp_mode = '0' then
        if (not raw_have_word) and raw_words_rem > 0 then
          case raw_fetch_state is
            when 0 =>
              if Rd_Fifo_Empty = '0' then
                raw_fifo_rdreq <= '1';
                raw_fetch_state := 1;
              end if;
            when 1 =>
              raw_fetch_state := 2;
            when others =>
              raw_word := Rd_Fifo_Q;
              raw_have_word := true;
              raw_fetch_state := 0;
          end case;
        end if;
      else
        raw_fetch_state := 0;
        raw_have_word := false;
        if raw_stream_comp_mode = '0' then
          raw_byte_hi_next := false;
        end if;
      end if;

      -- Drain residual Rd_Fifo entries left from a raw-stream session.
      -- The raw stream ends on CS rise (handled above); by that time the FLA
      -- has finished streaming every requested sample into the dcfifo, but
      -- straggler words that were enqueued but never read by the raw-stream
      -- byte shifter remain.  Without this drain, the next CMD_READ_CAPTURE
      -- reads stale FIFO data first, corrupting the block payload.
      if raw_fifo_drain_active = '1' then
        if Rd_Fifo_Empty = '0' then
          raw_fifo_rdreq <= '1';
        else
          raw_fifo_drain_active <= '0';
        end if;
      end if;

      case st is
        when IDLE =>
          if pkt_ok = '1' then
            rsp_seq_v := pkt_seq;
            rsp_stat_v := ST_OK;
            rsp_len_v := 0;
            rsp_buf_len := 0;
            feeding_block := false;
            feed_wait_ready_low := false;
            st := EXEC;
          end if;

        when EXEC =>
          case pkt_cmd_active is
            when CMD_PING =>
              rsp_buf(0) := x"01";
              rsp_buf(1) := x"01";
              rsp_buf(2) := x"00";
              rsp_buf_len := 3;
              rsp_len_v := 3;
              st := BUILD_RSP;

            when CMD_GET_STATUS =>
              if Run_OLS = '1' and Run = '0' then
                rsp_stat_v := ST_CAPTURE_ARMED;
              elsif Run = '1' and Full = '0' then
                rsp_stat_v := ST_CAPTURE_BUSY;
              elsif done_latched = '1' then
                rsp_stat_v := ST_CAPTURE_DONE;
              else
                rsp_stat_v := ST_CAPTURE_IDLE;
              end if;
              rsp_buf(0) := Gen_Fifo_Count;
              rsp_buf(2) := gen_load_events;
              rsp_buf(1)(0) := Gen_Busy;
              rsp_buf(1)(1) := gen_start_req;
              rsp_buf(1)(7 downto 2) := (others => '0');
              rsp_buf(3) := capture_seq(7 downto 0);
              rsp_buf(4) := capture_seq(15 downto 8);
              rsp_buf(5) := capture_seq(23 downto 16);
              rsp_buf(6) := capture_seq(31 downto 24);
              rsp_buf(7) := Producer_Index(7 downto 0);
              rsp_buf(8) := Producer_Index(15 downto 8);
              rsp_buf(9) := Producer_Index(23 downto 16);
              rsp_buf(10) := Producer_Index(31 downto 24);
              rsp_buf(11) := Oldest_Index(7 downto 0);
              rsp_buf(12) := Oldest_Index(15 downto 8);
              rsp_buf(13) := Oldest_Index(23 downto 16);
              rsp_buf(14) := Oldest_Index(31 downto 24);
              rsp_buf(15) := Newest_Index(7 downto 0);
              rsp_buf(16) := Newest_Index(15 downto 8);
              rsp_buf(17) := Newest_Index(23 downto 16);
              rsp_buf(18) := Newest_Index(31 downto 24);
              rsp_buf(19) := Overrun_Count(7 downto 0);
              rsp_buf(20) := Overrun_Count(15 downto 8);
              rsp_buf(21) := Overrun_Count(23 downto 16);
              rsp_buf(22) := Overrun_Count(31 downto 24);
              rsp_buf(23)(0) := done_latched;
              rsp_buf(23)(7 downto 1) := (others => '0');
              rsp_buf_len := 24;
              rsp_len_v := 24;
              st := BUILD_RSP;

            when CMD_GET_METADATA =>
              rsp_buf(0) := x"10";
              rsp_buf(1) := x"10";  -- 16 channels
              rsp_buf(2) := x"00";
              rsp_buf(3) := x"F0";
              rsp_buf(4) := x"01";
              -- bytes 5-8: SAMPLE_CLK_HZ in kHz, little-endian uint32
              -- (constant declared near the top; sliced type conversions are
              -- not portable to GHDL)
              rsp_buf(5) := SAMPLE_CLK_KHZ_SLV(7 downto 0);
              rsp_buf(6) := SAMPLE_CLK_KHZ_SLV(15 downto 8);
              rsp_buf(7) := SAMPLE_CLK_KHZ_SLV(23 downto 16);
              rsp_buf(8) := SAMPLE_CLK_KHZ_SLV(31 downto 24);
              rsp_buf_len := 9;
              rsp_len_v := 9;
              st := BUILD_RSP;

            when CMD_ARM_CAPTURE =>
              disp_arm <= '1';
              rsp_stat_v := ST_CAPTURE_ARMED;
              st := BUILD_RSP;

            when CMD_ABORT_CAPTURE =>
              disp_abort <= '1';
              rsp_stat_v := ST_CAPTURE_IDLE;
              st := BUILD_RSP;

            when CMD_START_RAW_STREAM =>
              if rx_header_len >= 8 then
                reg_val(7 downto 0)   := rx_payload_header(0);
                reg_val(15 downto 8)  := rx_payload_header(1);
                block_rd_issue_addr   <= reg_val(15 downto 0);
                raw_blk_rd_base_cfg   <= TO_INTEGER(UNSIGNED(reg_val(15 downto 1)));
                reg_val(7 downto 0) := rx_payload_header(4);
                reg_val(15 downto 8) := rx_payload_header(5);
                reg_val(23 downto 16) := rx_payload_header(6);
                reg_val(31 downto 24) := rx_payload_header(7);
                raw_blk_rd_count_cfg <= TO_INTEGER(UNSIGNED(reg_val(14 downto 0)));
                raw_stream_req_active <= '1';
                if comp_enable_i = '1' then
                  raw_stream_comp_mode <= '1';
                else
                  raw_stream_comp_mode <= '0';
                end if;
                raw_blk_req_fire <= '1';
                raw_words_rem := TO_INTEGER(UNSIGNED(reg_val(14 downto 0)));
                raw_start_pending := (raw_words_rem /= 0);
                raw_have_word := false;
                raw_fetch_state := 0;
                raw_byte_hi_next := false;
                raw_comp_bhi := '0';
                raw_comp_word_idle := false;
                rsp_buf(0) := Producer_Index(7 downto 0);
                rsp_buf(1) := Producer_Index(15 downto 8);
                rsp_buf(2) := Producer_Index(23 downto 16);
                rsp_buf(3) := Producer_Index(31 downto 24);
                rsp_buf(4) := Oldest_Index(7 downto 0);
                rsp_buf(5) := Oldest_Index(15 downto 8);
                rsp_buf(6) := Oldest_Index(23 downto 16);
                rsp_buf(7) := Oldest_Index(31 downto 24);
                rsp_buf_len := 8;
                rsp_len_v := 8;
                rsp_stat_v := ST_STREAM_ACTIVE;
              else
                rsp_stat_v := ST_BAD_LEN;
                raw_start_pending := false;
                raw_words_rem := 0;
              end if;
              st := BUILD_RSP;

            when CMD_ACK_CAPTURE_DONE =>
              disp_ack_seq <= (others => '0');
              if rx_header_len >= 4 then
                disp_ack_seq(7 downto 0)   <= rx_payload_header(0);
                disp_ack_seq(15 downto 8)  <= rx_payload_header(1);
                disp_ack_seq(23 downto 16) <= rx_payload_header(2);
                disp_ack_seq(31 downto 24) <= rx_payload_header(3);
              end if;
              disp_ack_done <= '1';
              st := BUILD_RSP;

            when CMD_READ_CAPTURE =>
              if rx_header_len >= 4 then
                block_rd_issue_addr(7 downto 0)   <= rx_payload_header(0);
                block_rd_issue_addr(15 downto 8)  <= rx_payload_header(1);
                block_rd_issue_req <= '1';
                block_wd := 0;
                st := WAIT_BLOCK;
              else
                rsp_stat_v := ST_BAD_LEN;
                st := BUILD_RSP;
              end if;

            when CMD_WRITE_REG =>
              if rx_header_len >= 5 then
                disp_reg_addr <= rx_payload_header(0);
                disp_reg_wdata(7 downto 0)   <= rx_payload_header(1);
                disp_reg_wdata(15 downto 8)  <= rx_payload_header(2);
                disp_reg_wdata(23 downto 16) <= rx_payload_header(3);
                disp_reg_wdata(31 downto 24) <= rx_payload_header(4);
                disp_reg_write <= '1';
              else
                rsp_stat_v := ST_BAD_LEN;
              end if;
              st := BUILD_RSP;

            when CMD_READ_REG =>
              if rx_header_len >= 1 then
                reg_val := (others => '0');
                case rx_payload_header(0) is
                  when REG_DIVIDER =>
                    reg_val(23 downto 0) := std_logic_vector(to_unsigned(Divider, 24));
                  when REG_SAMPLE_COUNT =>
                    reg_val(29 downto 0) := std_logic_vector(to_unsigned(Read_Count, 30));
                  when REG_DELAY_COUNT =>
                    reg_val(29 downto 0) := std_logic_vector(to_unsigned(Delay_Count, 30));
                  when REG_TRIGGER_MASK =>
                    reg_val := Trigger_Mask;
                  when REG_TRIGGER_VALUE =>
                    reg_val := Trigger_Values;
                  when REG_FLAGS | REG_FAST_MODE =>
                    reg_val(0) := fast_mode_i;
                    reg_val(1) := continuous_mode_i;
                    reg_val(2) := ch_mode;
                    reg_val(3) := analog_enable_i;
                    reg_val(4) := analog_only_i;
                    reg_val(6 downto 5) := analog_profile_i;
                    reg_val(12 downto 8) := std_logic_vector(to_unsigned(analog_channel_i, 5));
                    reg_val(13) := narrow_enable_i;
                    reg_val(17 downto 14) := std_logic_vector(to_unsigned(narrow_channel_i, 4));
                    reg_val(19 downto 18) := compress_mode_i;
                  when REG_CONT_MODE =>
                    reg_val(0) := continuous_mode_i;
                  when REG_GEN_PROTO =>
                    reg_val(0) := gen_proto_int;
                  when REG_GEN_BAUD =>
                    reg_val(15 downto 0) := gen_baud_div_int;
                  when REG_GEN_PINS =>
                    reg_val(4 downto 0) := std_logic_vector(to_unsigned(gen_tx_pin_int, 5));
                    reg_val(12 downto 8) := std_logic_vector(to_unsigned(gen_scl_pin_int, 5));
                  when REG_GEN_AUX_PINS =>
                    reg_val(4 downto 0) := std_logic_vector(to_unsigned(gen_de_pin_int, 5));
                    reg_val(5) := gen_de_enable_int;
                    reg_val(12 downto 8) := std_logic_vector(to_unsigned(gen_cs_pin_int, 5));
                    reg_val(13) := gen_cs_enable_int;
                    reg_val(20 downto 16) := std_logic_vector(to_unsigned(gen_miso_pin_int, 5));
                    reg_val(21) := gen_miso_enable_int;
                  when REG_GEN_DATA =>
                    reg_val(0) := gen_i2c_test_int;
                    reg_val(1) := gen_spi_test_int;
                    reg_val(2) := gen_repeat_int;
                    reg_val(3) := gen_rs485_pair_int;
                    reg_val(4) := gen_accel_attach_int;
                    reg_val(15 downto 8) := std_logic_vector(to_unsigned(gen_i2c_rd_len_int, 8));
                    reg_val(23 downto 16) := gen_i2c_dev_r_int;
                  when REG_GEN_RX_DATA =>
                    reg_val(7 downto 0)  := Gen_RX_Data;
                    reg_val(15 downto 8) := Gen_RX_Used;
                  when REG_GEN_CAPTURE_TX_CHAN =>
                    reg_val(3 downto 0) := std_logic_vector(to_unsigned(gen_capture_tx_channel_i, 4));
                  when REG_GEN_CAPTURE_SCL_CHAN =>
                    reg_val(3 downto 0) := std_logic_vector(to_unsigned(gen_capture_scl_channel_i, 4));
                  when REG_GEN_CAPTURE_AUX =>
                    reg_val(3 downto 0) := std_logic_vector(to_unsigned(gen_capture_cs_channel_i, 4));
                    reg_val(4) := gen_capture_cs_enable_i;
                    reg_val(11 downto 8) := std_logic_vector(to_unsigned(gen_capture_miso_channel_i, 4));
                    reg_val(12) := gen_capture_miso_enable_i;
                  when REG_DEBUG_CH0_ENABLE =>
                    reg_val(0) := debug_ch0_enable_i;
                  when REG_DEBUG_CH0_PERIOD =>
                    reg_val := debug_ch0_period_i;
                  when REG_DEBUG_CH0_DUTY =>
                    reg_val := debug_ch0_duty_i;
                  when REG_CAPTURE_SEQ =>
                    reg_val := capture_seq;
                  when REG_PRODUCER_INDEX =>
                    reg_val := Producer_Index;
                  when REG_OLDEST_INDEX =>
                    reg_val := Oldest_Index;
                  when REG_NEWEST_INDEX =>
                    reg_val := Newest_Index;
                  when REG_OVERRUN_COUNT =>
                    reg_val := Overrun_Count;
                  when REG_DONE_LATCHED =>
                    reg_val(0) := done_latched;
                  when REG_PUMP_VALID_CYCLES =>
                    reg_val := Pump_Valid_Cycles;
                  when REG_PUMP_READY_CYCLES =>
                    reg_val := Pump_Ready_Cycles;
                  when REG_PUMP_ACCEPT_CYCLES =>
                    reg_val := Pump_Accept_Cycles;
                  when REG_PUMP_STALL_CYCLES =>
                    reg_val := Pump_Stall_Cycles;
                  when REG_PUMP_NODATA_CYCLES =>
                    reg_val := Pump_NoData_Cycles;
                  when REG_PUMP_OVERFLOW_COUNT =>
                    reg_val := Pump_Overflow_Count;
                  when REG_STREAM_DEBUG0 | REG_STREAM_DEBUG1 =>
                    reg_val := (others => '0');
                  when others => null;
                end case;
                rsp_buf(0) := reg_val(7 downto 0);
                rsp_buf(1) := reg_val(15 downto 8);
                rsp_buf(2) := reg_val(23 downto 16);
                rsp_buf(3) := reg_val(31 downto 24);
                rsp_buf_len := 4;
                -- Pop the RX FIFO on the address being READ (NOT
                -- disp_reg_addr, which only tracks register WRITES and
                -- left the FIFO stuck at its head byte forever).
                if rx_payload_header(0) = REG_GEN_RX_DATA then
                  Gen_RX_Re <= '1';
                end if;
                rsp_len_v := 4;
              else
                rsp_stat_v := ST_BAD_LEN;
              end if;
              st := BUILD_RSP;

            when CMD_GEN_START =>
              disp_gen_start <= '1';
              st := BUILD_RSP;

            when CMD_GEN_STOP =>
              st := BUILD_RSP;

            when CMD_GEN_LOAD =>
              -- GEN_LOAD payload bytes were already written to Gen_Load_Byte
              -- by rx_stream_handler during RX.  Nothing more to do.
              st := BUILD_RSP;

            when CMD_GEN_CAPTURE =>
              if gen_cap_state = GENCAP_IDLE then
                disp_gen_arm <= '1';
                disp_arm <= '1';
                rsp_stat_v := ST_CAPTURE_ARMED;
              else
                rsp_stat_v := ST_BUSY;
              end if;
              st := BUILD_RSP;

            when CMD_GEN_STATUS =>
              rsp_buf(0)(0) := Gen_Busy;
              rsp_buf(0)(1) := Gen_Start_Ack;
              rsp_buf(0)(2) := gen_capture_error_i;
              rsp_buf(0)(3) := gen_capture_active_i;
              rsp_buf(0)(4) := gen_capture_done_i;
              rsp_buf(0)(5) := Gen_Start_Reject;
              IF unsigned(Gen_Fifo_Count) > 0 THEN rsp_buf(0)(6) := '1'; ELSE rsp_buf(0)(6) := '0'; END IF;
              rsp_buf(0)(7) := Gen_Done_Pulse;
              rsp_buf_len := 1;
              rsp_len_v := 1;
              st := BUILD_RSP;

            when others =>
              rsp_stat_v := ST_BAD_CMD;
              st := BUILD_RSP;
          end case;

        when WAIT_BLOCK =>
          if block_rd_ack = '1' then
            rsp_len_v := blk_rsp_words * 2;
            block_rd_release <= '1';
            blk_wc := 0;
            blk_bc := 0;
            blk_bytes_sent := 0;
            feeding_block := true;
            st := BUILD_RSP;
          elsif block_wd >= BLOCK_WD_MAX then
            -- Stream stalled (e.g. block read during continuous capture). Kill
            -- the block-read FSM, drop the pending request, and return an empty
            -- error response so the dispatcher frees up for the next command
            -- instead of wedging the device until it is reconfigured.
            block_rd_kill <= '1';
            rsp_stat_v := ST_CAPTURE_IDLE;
            rsp_len_v := 0;
            st := BUILD_RSP;
          else
            block_wd := block_wd + 1;
          end if;

        when BUILD_RSP =>
          disp_tx_seq <= rsp_seq_v;
          disp_tx_status <= rsp_stat_v;
          disp_tx_len <= rsp_len_v;
          disp_tx_build <= '1';
          if rsp_len_v = 0 then
            st := WAIT_TX;
          else
            rsp_buf_idx := 0;
            feed_wait_ready_low := false;
            st := FEED_TX;
          end if;

        when FEED_TX =>
          if feed_wait_ready_low then
            if pkt_tx_payload_ready = '0' then
              feed_wait_ready_low := false;
            end if;
          elsif pkt_tx_payload_ready = '1' then
            if feeding_block then
              -- Stream from block_buf; rsp_len_v carries the exact byte count.
              disp_tx_payload_in <= block_buf(blk_wc)(blk_bc * 8 + 7 downto blk_bc * 8);
              disp_tx_payload_vld <= '1';
              feed_wait_ready_low := true;
              block_last_v := (blk_bytes_sent + 1 >= rsp_len_v);
              if block_last_v then
                st := WAIT_TX;
              else
                blk_bytes_sent := blk_bytes_sent + 1;
                if blk_bc < 3 then
                  blk_bc := blk_bc + 1;
                else
                  blk_bc := 0;
                  if blk_wc < 255 then
                    blk_wc := blk_wc + 1;
                  end if;
                end if;
              end if;
            else
              -- Stream from rsp_buf
              if rsp_buf_idx < rsp_buf_len then
                disp_tx_payload_in <= rsp_buf(rsp_buf_idx);
                disp_tx_payload_vld <= '1';
                feed_wait_ready_low := true;
                rsp_buf_idx := rsp_buf_idx + 1;
              end if;
              if rsp_buf_idx >= rsp_buf_len then
                st := WAIT_TX;
              end if;
            end if;
          end if;

        when WAIT_TX =>
          if pkt_tx_done = '1' then
            if raw_start_pending then
              if raw_stream_comp_mode = '1' then
                -- Seed an explicit 0x0000 filler word at the handoff. Without
                -- this, one stale packet-side byte can leak ahead of the RAW
                -- stream and misalign every later filler/real word boundary.
                raw_stream_tx_sel <= '1';
                raw_stream_tx_byte <= x"00";
                raw_comp_bhi := '1';
                raw_comp_word_idle := true;
              else
                if raw_have_word then
                  raw_stream_tx_sel <= '1';
                  raw_stream_tx_byte <= raw_word(7 downto 0);
                  raw_byte_hi_next := true;
                else
                  raw_stream_tx_sel <= '0';
                  raw_byte_hi_next := false;
                end if;
              end if;
              st := RAW_STREAM;
            else
              st := IDLE;
            end if;
          end if;

        when RAW_STREAM =>
          if raw_stream_comp_mode = '1' then
            -- Serialize output words low-byte-first. At each word boundary
            -- (raw_comp_bhi = '0') pick a real FIFO word if one is available,
            -- else -- if not finished -- a 0x0000 idle filler word so the
            -- continuously-clocked wire is never starved into ambiguous bytes.
            -- raw_comp_word_idle latches that choice so the two bytes of a word
            -- are never mixed (idle low + real high or vice-versa), which keeps
            -- the host word-aligned and lets it skip whole idle words.
            if spi_tx_ready_i = '1' then
              if raw_comp_bhi = '0' then
                if raw_comp_fifo_count > 0 then
                  raw_comp_word_idle := false;
                  raw_stream_tx_sel <= '1';
                  raw_stream_tx_byte <= raw_comp_fifo(raw_comp_fifo_rd_ptr)(7 downto 0);
                  raw_comp_bhi := '1';
                elsif raw_comp_done = '1' then
                  raw_start_pending := false;
                  st := IDLE;
                else
                  raw_comp_word_idle := true;
                  raw_stream_tx_sel <= '1';
                  raw_stream_tx_byte <= x"00";
                  raw_comp_bhi := '1';
                end if;
              else
                raw_stream_tx_sel <= '1';
                if raw_comp_word_idle then
                  raw_stream_tx_byte <= x"00";
                else
                  raw_stream_tx_byte <= raw_comp_fifo(raw_comp_fifo_rd_ptr)(15 downto 8);
                  raw_comp_pop <= '1';  -- whole real word consumed
                end if;
                raw_comp_bhi := '0';
              end if;
            end if;
          elsif spi_tx_ready_i = '1' and raw_have_word then
            if raw_stream_tx_sel = '0' then
              raw_stream_tx_sel <= '1';
              raw_stream_tx_byte <= raw_word(7 downto 0);
              raw_byte_hi_next := true;
            elsif raw_byte_hi_next then
              raw_stream_tx_byte <= raw_word(15 downto 8);
              raw_byte_hi_next := false;
              raw_have_word := false;
              if raw_words_rem > 0 then
                raw_words_rem := raw_words_rem - 1;
              end if;
              -- raw_words_rem is a variable, so this reads the POST-decrement
              -- count: 0 means the high byte just queued completes the LAST
              -- requested word. The old "<= 1" exit here left one word unsent
              -- on every stream segment (host saw 0xFFFF idle in the final
              -- word slot; hw-verified 2026-07-04).
              if raw_words_rem = 0 then
                raw_start_pending := false;
                st := IDLE;
              end if;
            else
              raw_stream_tx_byte <= raw_word(7 downto 0);
              raw_byte_hi_next := true;
            end if;
          end if;
      end case;
    end if;
  end process;

  -- Build response packets from dispatch output (streaming payload)
  pkt_tx_inst : spi_packet_tx
  PORT MAP (
    clk         => CLK,
    rst         => '0',
    req_seq     => disp_tx_seq,
    build       => disp_tx_build,
    rsp_status  => disp_tx_status,
    rsp_len     => disp_tx_len,
    payload_byte_in  => disp_tx_payload_in,
        payload_valid_in => disp_tx_payload_vld,
        payload_ready    => pkt_tx_payload_ready,
        tx_ready    => spi_tx_ready_i,
        tx_byte     => pkt_tx_byte,
        tx_done     => pkt_tx_done
      );



  -- Debug: rising-edge detect on SPI_RX_Valid (sys_clk domain)
  -- Auto_Renew: drives FLA block-read auto-renew.  Default '0' (single-shot).
  Auto_Renew <= '0';

  blk_rd_samples <= BLOCK_SAMPLES;
  comp_enable_i <= '1' when compress_mode_i /= "00" and analog_enable_i = '0' else '0';

  -- One shared RLE stage keeps both compressed modes timing-friendly:
  -- mode "01" selects delta-packing before RLE, mode "10" feeds full words
  -- directly into the same RLE stage, and mode "00" remains raw.
  rd_readback_compressor : entity work.delta_rle_compressor
    PORT MAP (
      clk                => CLK,
      rst                => comp_rst_i,
      sample_in          => comp_sample_in,
      sample_valid       => comp_feed_i,
      compression_enable => comp_enable_i,
      delta_mode         => delta_mode_i,
      flush              => comp_flush_i,
      comp_data          => codec_out_data,
      comp_valid         => codec_out_valid,
      busy               => codec_busy,
      in_ready           => codec_in_ready
    );

  -- The codec select is configuration state held for the duration of a
  -- readback. Keep the common drain-facing interface registered/controlled by
  -- that stable mode bit; the inactive codec is reset and cannot contribute
  -- output words.
  comp_out_data   <= codec_out_data;
  comp_out_valid  <= codec_out_valid;
  comp_busy_i     <= codec_busy;
  comp_in_ready_i <= codec_in_ready;

  -- Streaming RLE feeds the compressor directly from the SDRAM read FIFO
  -- output; the FETCH/WAIT/FEED sequencer above only asserts comp_feed_i in the
  -- cycle Rd_Fifo_Q is valid, so no skid/holding register is needed.
  comp_sample_in <= comp_sample_hold;
  delta_mode_i <= '1' when compress_mode_i = "01" else '0';

END BEHAVIORAL;
